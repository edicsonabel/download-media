"""Shared fixtures: environment isolation, scripted input, and a fake site.

The fake site drives the real yt-dlp and ffmpeg against a loopback HTTP server.
Mocks can't catch what these tests are for: how yt-dlp reacts to carousels,
``ignoreerrors``, filters and filenames.
"""

from __future__ import annotations

import base64
import builtins
import functools
import gzip
import http.server
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yt_dlp
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

from download_media import downloader
from download_media import probe as probe_module

# What yt-dlp reads to pick a Linux keyring for a Chromium browser.
_DESKTOP_VARS = (
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
    "KDE_FULL_SESSION",
    "GNOME_DESKTOP_SESSION_ID",
)


@pytest.fixture(autouse=True)
def isolated_env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Keep tests away from the real history log, browser flag files, home and working folder."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Live tests exist to exercise the real browser setup.
    if "live" in request.keywords:
        yield
        return
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # xdg_dir() falls back to $HOME/Videos and $HOME/Music.
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    # Desktops export these, and some xdg-user-dir builds read them first.
    for kind in ("VIDEOS", "MUSIC"):
        monkeypatch.delenv(f"XDG_{kind}_DIR", raising=False)
    # A fake browser profile with an encrypted cookie would ask the real session keyring for
    # its key. A bus that isn't there fails at once, and without a desktop yt-dlp picks none.
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={tmp_path / 'no-bus' / 'socket'}")
    for name in _DESKTOP_VARS:
        monkeypatch.delenv(name, raising=False)
    # A scripted wizard answer that drifts by one step can become a relative folder:
    # it lands here instead of the checkout, and the test fails instead of passing.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    yield
    stray = sorted(p.name for p in cwd.iterdir())
    assert not stray, f"a test wrote to the working folder: {stray}"


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Script ``input()``. Asking more than scripted fails the test."""

    def script(*replies: str | type[BaseException]) -> list[str]:
        pending = list(replies)
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            assert pending, f"unexpected prompt: {prompt!r}"
            reply = pending.pop(0)
            if isinstance(reply, str):
                return reply
            raise reply

        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return script


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    # yt-dlp's generic extractor branches on Content-Type, and anything missing here
    # comes from the host's MIME database: without /etc/mime.types, Python below 3.14
    # doesn't send .ogg as audio/*.
    extensions_map: ClassVar[dict[str, str]] = {
        ".asf": "video/x-ms-asf",
        ".flv": "video/x-flv",
        ".gif": "image/gif",
        ".html": "text/html",
        ".jpg": "image/jpeg",
        ".m3u8": "application/vnd.apple.mpegurl",
        ".m4a": "audio/mp4",
        ".m4b": "audio/mp4",
        ".mp4": "video/mp4",
        ".ogg": "audio/ogg",
        ".ogx": "application/ogg",
        ".png": "image/png",
        ".ts": "video/mp2t",
        ".wav": "audio/x-wav",
        ".webm": "video/webm",
        ".wma": "audio/x-ms-wma",
    }

    # /as-audio/<file> serves <file> under an audio/* label, as some servers do for video.
    _AS_AUDIO: ClassVar[dict[str, str]] = {
        ".asf": "audio/x-ms-asf",
        ".mp4": "audio/mp4",
        ".webm": "audio/webm",
    }

    def log_message(self, format: str, *args: Any) -> None:
        pass

    # /typed/<type>/<subtype>/<file>/<name> serves <file> as <type>/<subtype> under any last
    # segment: an uppercase extension, or none at all.
    _TYPED = re.compile(r"/typed/(?P<mime>[\w.+-]+/[\w.+-]+)/(?P<file>[^/?]+)/[^/?]+(?:\?.*)?")

    def translate_path(self, path: str) -> str:
        typed = self._TYPED.fullmatch(path)
        if typed:
            path = f"/{typed['file']}"
        return super().translate_path(path.removeprefix("/as-audio"))

    def guess_type(self, path: Any) -> str:
        typed = self._TYPED.fullmatch(self.path)
        if typed:
            return typed["mime"]
        if self.path.startswith("/as-audio/"):
            return self._AS_AUDIO[Path(path).suffix]
        return super().guess_type(path)

    def send_head(self) -> Any:
        if self.path.startswith("/loop/"):  # Redirects to itself.
            self.send_response(302)
            self.send_header("Location", self.path)
            self.end_headers()
            return None
        if self.path.startswith("/hang/"):  # Answers after any sane client gave up.
            time.sleep(3)
            self.send_error(504)
            return None
        if self.path.startswith("/short/"):  # A JPEG cut off long before its stated length.
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"\xff\xd8\xff\xe0" + b"\0" * 60)
            self.close_connection = True
            return None
        flaky = re.fullmatch(r"/flaky/(\w+)(/[^?]+)", self.path)
        if flaky and flaky[1] not in FLAKY_SEEN:  # Cut off the first time, whole after that.
            FLAKY_SEEN.add(flaky[1])
            self.path = "/short/" + flaky[1]
            return self.send_head()
        if flaky:
            self.path = flaky[2]
        body = re.fullmatch(r"/(nolength|gzip0)(/[^?]+)", self.path)
        if body:  # No Content-Length, or a gzip body that is longer than the file it holds.
            data = Path(self.translate_path(body[2])).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(body[2]))
            if body[1] == "gzip0":
                data = gzip.compress(data, compresslevel=0)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True
            return None
        status = re.fullmatch(r"/status/(\d{3})(?:/.*)?", self.path)  # A sub-path names the id.
        if status:
            self.send_error(int(status[1]))
            return None
        gate = self._GATE.fullmatch(self.path)
        if gate:
            cookie = self.headers.get("Cookie") or ""
            GATE_REQUESTS.setdefault(gate["token"], []).append((gate["rest"], cookie))
            if gate["kind"] == "cookie" and "dm=ok" not in cookie:
                self.send_error(401)
                return None
            self.path = gate["rest"]
        return super().send_head()

    # /cookie/<token>/<file> answers 401 unless the request carries the cookie dm=ok, and
    # /seen/<token>/<file> serves <file> to anyone. Both record what each request sent.
    _GATE = re.compile(r"/(?P<kind>cookie|seen)/(?P<token>\w+)(?P<rest>/[^?]+)(?:\?.*)?")


# Tokens /flaky/ already cut off once.
FLAKY_SEEN: set[str] = set()

# Per token: the path and the Cookie header of every request to /cookie/ or /seen/.
GATE_REQUESTS: dict[str, list[tuple[str, str]]] = {}


# A text file served as a video: ffmpeg finds no stream in it.
JUNK = "typed/video/mp4/en.vtt/junk.mp4"
JUNK_2 = "typed/video/mp4/es.vtt/junk-2.mp4"
# yt-dlp names a link with no extension and no media type .unknown_video.
NO_EXT_VIDEO = "typed/application/octet-stream/tiny.mp4/stream"
# A video, and an audio, under extensions yt-dlp lists as audio: ffmpeg can't tag either, and
# the container step leaves both alone.
OGA_VIDEO = "typed/audio/ogg/tiny.mp4/clip.oga"
WEBA = "typed/audio/webm/audio.webm/clip.weba"
# Thumbnails ffmpeg can't embed: a text body, and a WebP signature with nothing after it,
# which yt-dlp renames to .webp and then fails to convert.
JUNK_IMAGE = "typed/image/jpeg/en.vtt/t.jpg"
WEBP_IMAGE = "typed/image/jpeg/webp-signature.bin/t.jpg"
# A real JPEG under link names yt-dlp turns into the thumbnail's extension.
JPEG_AS = "typed/image/jpeg/tiny.jpg/cover.{}"


def _write_generic_pages(root: Path, base: str) -> None:
    """Pages for yt-dlp's real generic extractor, which leaves codecs unknown."""
    og_image = f'<meta property="og:image" content="{base}/tiny.jpg">'
    relative_og = '<meta property="og:image" content="tiny.jpg">'

    def og(path: str) -> str:
        return f'<meta property="og:image" content="{base}/{path}">'

    two_audios = '<audio src="a1.m4a"></audio><audio src="a2.m4a"></audio>'
    videos = "".join(f'<video src="v{i}.mp4"></video>' for i in range(1, 5))
    codecs = """<source src="tiny.mp4" type='video/mp4; codecs="avc1.42E01E, mp4a.40.2"'>"""
    pages = {
        "multi": videos,
        "poster": '<video src="tiny.mp4" poster="tiny.jpg"></video>',
        "og-video": f'<meta property="og:video" content="{base}/tiny.mp4">{og_image}',
        "og-videos": (
            f'<meta property="og:video" content="{base}/v1.mp4">'
            f'<meta property="og:video" content="{base}/v2.mp4">'
        ),
        "partial-codec": f"<video>{codecs}</video>",
        "audio": f'<audio src="tiny.m4a"></audio>{og_image}',
        "audio-video-m4a": f'<audio src="clip.m4a"></audio>{og_image}',
        "audio-png-thumb": f'<audio src="tiny.m4a"></audio>{og("tiny.png")}',
        "audios": '<audio src="a1.m4a"></audio><audio src="a2.m4a"></audio>',
        "video-audio": '<video src="v1.mp4"></video><audio src="a1.m4a"></audio>',
        "jpg-video": '<video src="tiny.jpg"></video><video src="v1.mp4"></video>',
        "gone": '<video src="missing.mp4" poster="tiny.jpg"></video>',
        "half-gone": '<video src="v1.mp4"></video><video src="missing.mp4"></video>',
        "webm": '<video src="tiny.webm" poster="tiny.jpg"></video>',
        "hls": '<video src="tiny.m3u8" poster="tiny.jpg"></video>',
        "jpg-alone": '<video src="tiny.jpg"></video>',
        "audio-ogg": '<audio src="tiny.ogg"></audio>',
        "audio-wav": '<audio src="tiny.wav"></audio>',
        "audio-relative-og": f'<audio src="tiny.m4a"></audio>{relative_og}',
        "audio-gone": f'<audio src="missing.m4a"></audio>{og_image}',
        "video-junk-og": f'<video src="{JUNK}"></video>{og_image}',
        "audio-junk-thumb": f'<audio src="tiny.m4a"></audio>{og(JUNK_IMAGE)}',
        "audio-gif-thumb": f'<audio src="tiny.m4a"></audio>{og("tiny.gif")}',
        "audio-webp-thumb": f'<audio src="tiny.m4a"></audio>{og(WEBP_IMAGE)}',
        "audios-og": f"{two_audios}{og_image}",
        "audios-relative-og": f"{two_audios}{relative_og}",
        # Thumbnail links of other shapes.
        "audio-php-thumb": f'<audio src="tiny.m4a"></audio>{og(JPEG_AS.format("php"))}',
        "audio-html-thumb": f'<audio src="tiny.m4a"></audio>{og("audio.html")}',
        "audio-mp3-thumb": f'<audio src="tiny.m4a"></audio>{og(JPEG_AS.format("mp3"))}',
        # A text body under a link ending in .mp3: a cover that fails, named like the audio.
        "audio-mp3-junk-thumb": f'<audio src="tiny.m4a"></audio>{og("typed/image/jpeg/en.vtt/t.mp3")}',
        # Named like the media itself, so yt-dlp finds the cover where the download goes.
        "audio-m4a-thumb": f'<audio src="tiny.m4a"></audio>{og(JPEG_AS.format("m4a"))}',
        "audio-file-thumb": (
            f'<audio src="tiny.m4a"></audio><meta property="og:image" content="file://{root}/tiny.jpg">'
        ),
        "audio-data-thumb": (
            '<audio src="tiny.m4a"></audio><meta property="og:image" content="data:image/jpeg;base64,'
            f'{base64.b64encode((root / "tiny.jpg").read_bytes()).decode()}">'
        ),
        "audio-loop-thumb": f'<audio src="tiny.m4a"></audio>{og("loop/t.jpg")}',
        "audio-short-thumb": f'<audio src="tiny.m4a"></audio>{og("short/t.jpg")}',
        "video-ogg": '<video src="theora.ogg"></video>',
        "video-ogg-codecs": (
            """<video><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'></video>"""
        ),
        "video-ogx": '<video src="theora.ogx"></video>',
        "video-m4b": '<video src="clip.m4b"></video>',
        "video-wma": '<video src="clip.wma"></video>',
        "video-m4a": '<video src="clip.m4a"></video>',
        # yt-dlp keeps the URL's case in ext, and fills no acodec in from an uppercase one.
        "video-upper-mp3": '<video src="typed/audio/mpeg/tiny.mp3/TINY.MP3"></video>',
        "video-upper-flac": '<video src="typed/audio/flac/tiny.flac/TINY.FLAC"></video>',
        "video-upper-wav": '<video src="typed/audio/x-wav/tiny.wav/TINY.WAV"></video>',
        "video-upper-opus": '<video src="typed/audio/ogg/tiny.opus/TINY.OPUS"></video>',
        "video-upper-jpg": '<video src="typed/image/jpeg/tiny.jpg/TINY.JPG"></video>',
        "video-mixed-png": '<video src="typed/image/png/tiny.png/Tiny.Png"></video>',
        "video-upper-m4a-video": '<video src="typed/video/mp4/clip.m4a/CLIP.M4A"></video>',
        "og-video-ogg": f'<meta property="og:video" content="{base}/theora.ogg">',
        "video-tag-audio-ogg": '<video src="tiny.ogg"></video>',
        "video-tag-audio-m4b": '<video src="tiny.m4b"></video>',
        "audio-theora-ogg": '<audio src="theora.ogg"></audio>',
        "audio-mp4": '<audio src="tiny.mp4"></audio>',
        "audio-webm": '<audio src="tiny.webm"></audio>',
        "audio-mp3": '<audio src="tiny.mp3"></audio>',
        "audio-no-ext": '<audio src="typed/audio/mpeg/tiny.mp3/stream"></audio>',
        "audio-source-vorbis": """<audio><source src="tiny.ogg" type='audio/ogg; codecs="vorbis"'></audio>""",
        "audio-source-ogg": """<audio><source src="theora.ogg" type="audio/ogg"></audio>""",
        # The <audio> tag sets vcodec 'none' over the codecs its type names; the type sets ext.
        "audio-source-theora-videotype": (
            """<audio><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'></audio>"""
        ),
        "audio-source-mp4-videotype": f"<audio>{codecs}</audio>",
        "audio-source-webm-videotype": (
            """<audio><source src="tiny.webm" type='video/webm; codecs="vp8, vorbis"'></audio>"""
        ),
        # Real audio first, then a declared video: either source can be the one saved.
        "audio-sources-audio-then-video": (
            """<audio><source src="tiny.ogg" type='audio/ogg; codecs="vorbis"'>"""
            f"{codecs}</audio>"
        ),
        # An audio type naming a video codec: the same formats as real Vorbis.
        "audio-source-theora-audiotype": (
            """<audio><source src="theora.ogg" type='audio/ogg; codecs="theora, vorbis"'></audio>"""
        ),
        "audio-hls": '<audio src="tiny.m3u8"></audio>',
        "audios-theora-and-vorbis": '<audio src="theora.ogg"></audio><audio src="tiny.ogg"></audio>',
        "ogg-video-and-audio": '<video src="theora.ogg"></video><audio src="tiny.ogg"></audio>',
        "everything": (
            '<video src="v1.mp4"></video><video src="tiny.jpg"></video><audio src="a1.m4a"></audio>'
        ),
        "no-video": '<video src="tiny.jpg"></video><audio src="a1.m4a"></audio>',
        # ffmpeg can't write tags to an .oga holding a video, and can't read a text body at all.
        # The container step skips the .oga: yt-dlp lists it as audio.
        "oga-and-mp4": f'<video src="{OGA_VIDEO}"></video><video src="tiny.mp4"></video>',
        # A video the container step converts, and one ffmpeg can't put in an mp4.
        "webm-and-attached": '<video src="tiny.webm"></video><video src="attached.mkv"></video>',
        "junk-and-mp4": f'<video src="{JUNK}"></video><video src="tiny.mp4"></video>',
        "all-junk": f'<video src="{JUNK}"></video><video src="{JUNK_2}"></video>',
        "two-junk-and-mp4": (
            f'<video src="{JUNK}"></video><video src="{JUNK_2}"></video>'
            '<video src="tiny.mp4"></video>'
        ),
        "audio-no-ext-video": f'<audio src="{NO_EXT_VIDEO}"></audio>',
        # Under -a, a video without sound fails audio extraction, and ffmpeg can't tag the .oga.
        # Not rotated: ffprobe's "video," for a rotated one can fail the item on another path.
        "silent-oga-and-m4a": (
            '<audio src="typed/audio/ogg/silent.mov/clip.oga"></audio><audio src="a1.m4a"></audio>'
        ),
    }
    for name, body in pages.items():
        (root / f"{name}.html").write_text(f"<!doctype html><html>{body}</html>", encoding="utf-8")


_VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:00.200\n{}\n"
# TTML is a real subtitle format, but ffmpeg can't read it: embedding it fails.
_TTML = (
    '<?xml version="1.0" encoding="utf-8"?><tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
    '<p begin="00:00:00.000" end="00:00:00.200">Hello</p></div></body></tt>'
)
_SUBTITLES = {
    "en.vtt": _VTT.format("Hello"),
    "es.vtt": _VTT.format("Hola"),
    "fr.vtt": _VTT.format("Bonjour"),
    "en.srt": "1\n00:00:00,000 --> 00:00:00,200\nHello\n",
    "en.ttml": _TTML,
    "en.ass": (
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,"
        "0,0,1,1,0,2,10,10,10,1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:00.20,Default,,0,0,0,,Hello\n"
    ),
    # Broken files under a subtitle extension; missing.vtt is never written (a 404).
    "empty.vtt": "",
    "garbage.vtt": "\x00\x01 not a subtitle \xff\xfe\n" * 8,
    "header.vtt": "WEBVTT\n\n",  # A valid file with no cue: no subtitle text.
    "note.vtt": "WEBVTT\n\nNOTE nothing to show here\n\n",  # Comments only: no cue.
}


@pytest.fixture(scope="session")
def media_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Base URL of a loopback server hosting tiny real media and generic pages."""
    if not shutil.which("ffmpeg"):
        pytest.fail("ffmpeg is required: it is a runtime dependency")
    root = tmp_path_factory.mktemp("media")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=0.2:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(root / "tiny.mp4"),
        ],
        check=True,
    )
    source = ["ffmpeg", "-v", "error", "-i", str(root / "tiny.mp4")]
    subprocess.run([*source, "-frames:v", "1", str(root / "tiny.jpg")], check=True)
    # A smaller JPEG than tiny.jpg, like the square crops Instagram lists before the original.
    small = ["-frames:v", "1", "-vf", "scale=16:16", str(root / "small.jpg")]
    subprocess.run([*source, *small], check=True)
    subprocess.run([*source, "-frames:v", "1", str(root / "tiny.png")], check=True)
    subprocess.run([*source, "-frames:v", "1", str(root / "tiny.gif")], check=True)
    subprocess.run([*source, "-vn", "-c:a", "copy", str(root / "tiny.m4a")], check=True)
    subprocess.run([*source, "-vn", "-c:a", "libvorbis", str(root / "tiny.ogg")], check=True)
    subprocess.run([*source, "-vn", str(root / "tiny.wav")], check=True)
    # Real audio under the extensions that say audio (spec 27 must not flag them).
    for ext, codec in (("mp3", "libmp3lame"), ("flac", "flac"), ("opus", "libopus")):
        subprocess.run([*source, "-vn", "-c:a", codec, str(root / f"tiny.{ext}")], check=True)
    subprocess.run(
        [*source, "-vn", "-c:a", "copy", "-f", "adts", str(root / "tiny.aac")], check=True
    )
    subprocess.run(
        [*source, "-c:v", "libvpx", "-c:a", "libvorbis", str(root / "tiny.webm")], check=True
    )
    audio_webm = ["ffmpeg", "-v", "error", "-i", str(root / "tiny.webm"), "-vn", "-c:a", "copy"]
    subprocess.run([*audio_webm, str(root / "audio.webm")], check=True)
    # Containers yt-dlp lists as audio, holding a video.
    subprocess.run(
        [*source, "-c:v", "libtheora", "-c:a", "libvorbis", str(root / "theora.ogg")], check=True
    )
    subprocess.run([*source, "-c:v", "wmv2", "-c:a", "wmav2", str(root / "clip.asf")], check=True)
    shutil.copy(root / "theora.ogg", root / "theora.ogx")
    shutil.copy(root / "clip.asf", root / "clip.wma")
    shutil.copy(root / "tiny.mp4", root / "clip.m4b")
    shutil.copy(root / "tiny.mp4", root / "clip.m4a")
    shutil.copy(root / "tiny.m4a", root / "tiny.m4b")
    # A container yt-dlp can't embed subtitles in, by stream copy: no new encoder.
    subprocess.run([*source, "-c", "copy", str(root / "tiny.flv")], check=True)
    subprocess.run([*source, "-an", "-c", "copy", str(root / "silent.mov")], check=True)
    hls = ["-c", "copy", "-f", "hls", "-hls_playlist_type", "vod", str(root / "tiny.m3u8")]
    subprocess.run([*source, *hls], check=True)
    for copy in ("v1", "v2", "v3", "v4"):
        shutil.copy(root / "tiny.mp4", root / f"{copy}.mp4")
    for copy in ("a1", "a2"):
        shutil.copy(root / "tiny.m4a", root / f"{copy}.m4a")
    for name, text in _SUBTITLES.items():
        (root / name).write_text(text, encoding="utf-8")
    # An attachment stream, which ffmpeg can't put in an mp4: by stream copy, no new encoder.
    attach = ["-attach", str(root / "en.vtt"), "-metadata:s:t", "mimetype=text/plain"]
    subprocess.run([*source, *attach, "-c", "copy", str(root / "attached.mkv")], check=True)
    # A phone video: ffprobe prints "video," for a stream carrying a display matrix.
    rotate = ["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(root / "tiny.mp4")]
    for ext in ("mov", "mp4"):
        subprocess.run([*rotate, "-c", "copy", str(root / f"rotated.{ext}")], check=True)
    subprocess.run([*rotate, "-an", "-c", "copy", str(root / "rotated-silent.mov")], check=True)
    # Stereo 3D side data, from a Matroska tag: "video," as well.
    stereo = ["-c", "copy", "-metadata:s:v", "stereo_mode=left_right", str(root / "stereo.mkv")]
    subprocess.run([*source, *stereo], check=True)
    # Enough for imghdr to say webp, and for ffmpeg to fail on it.
    (root / "webp-signature.bin").write_bytes(b"RIFF\0\0\0\0WEBP")
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    # After the server starts: og:image and og:video need its port.
    _write_generic_pages(root, base)
    yield base
    server.shutdown()


# instagram.py and youtube/_video.py, word for word.
ANONYMOUS_LIMIT = (
    "The webpage request was redirected to the login page. "
    "You have exceeded the rate-limit for accessing posts anonymously"
)
FOLLOWERS_ONLY = "This content is only available for registered users who follow this account"
GEO_BLOCK = "The uploader has not made this video available in your country"
HTTP_404 = "Unable to download webpage: HTTP Error 404: Not Found"
SUBSCRIBERS_ONLY = "This post requires a subscription"
SESSION_RATE_LIMIT = (
    "This content isn't available, try again later. The current session has been rate-limited "
    "by YouTube for up to an hour. It is recommended to use `-t sleep` to add a delay between "
    "video requests to avoid exceeding the rate limit. For more information, refer to  "
    "https://github.com/yt-dlp/yt-dlp/wiki/Extractors#this-content-isnt-available-try-again-later"
)


class FakeSiteIE(InfoExtractor):
    IE_NAME = "fakesite"
    _VALID_URL = r"https://fake\.test/(?P<id>[\w-]+)"
    media_url = ""

    def _video(self, item: str, *, path: str = "tiny.mp4", page: str = "post") -> dict[str, Any]:
        return {
            "id": item,
            "title": "Clip",  # Same title and page everywhere, like Instagram.
            "webpage_url": f"https://fake.test/{page}",
            "formats": [
                {
                    "format_id": "main",
                    "url": f"{self.media_url}/{path}",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "height": 64,
                }
            ],
        }

    def _merged_video(self, item: str, page: str) -> dict[str, Any]:
        """Separate video and audio streams, like most of YouTube: ffmpeg merges them."""
        url = f"{self.media_url}/tiny.mp4"
        return {
            "id": item,
            "title": "Clip",
            "webpage_url": f"https://fake.test/{page}",
            "formats": [
                {"format_id": "v", "url": url, "ext": "mp4", "vcodec": "h264", "acodec": "none"},
                {"format_id": "a", "url": url, "ext": "mp4", "vcodec": "none", "acodec": "aac"},
            ],
        }

    def _bare_video(self, item: str) -> dict[str, Any]:
        """A format with no codec info, like most of what extractors build."""
        video = self._video(item)
        video["formats"] = [{"url": f"{self.media_url}/tiny.mp4", "ext": "mp4"}]
        return video

    def _url_only(self, item: str, path: str, ext: str) -> dict[str, Any]:
        """No formats at all: yt-dlp fetches the entry's own ``url``."""
        return {"id": item, "title": "Clip", "url": f"{self.media_url}/{path}", "ext": ext}

    def _audio(self, item: str) -> dict[str, Any]:
        video = self._video(item)
        video["duration"] = 1
        video["formats"] = [
            {"url": f"{self.media_url}/tiny.m4a", "ext": "m4a", "vcodec": "none", "acodec": "aac"}
        ]
        return video

    def _instagram_video(self, item: str, **codecs: str) -> dict[str, Any]:
        """``_extract_product_media`` when the post has no ``video_codec`` or DASH manifest."""
        video = self._video(item)
        video["formats"] = [
            {"format_id": "101", "url": f"{self.media_url}/tiny.mp4", "height": 64, **codecs}
        ]
        return video

    def _instagram_image(self, item: str) -> dict[str, Any]:
        """Same function on an image: no formats, but thumbnails."""
        thumbnail = {"url": f"{self.media_url}/tiny.jpg", "width": 64, "height": 64}
        return {**self._image(item), "duration": None, "thumbnails": [thumbnail]}

    def _storyboard(self) -> dict[str, Any]:
        url = f"{self.media_url}/tiny.jpg"
        return {
            "format_id": "sb0",
            "format_note": "storyboard",
            "url": url,
            "ext": "mhtml",
            "protocol": "mhtml",
            "vcodec": "none",
            "acodec": "none",
            "width": 48,
            "height": 27,
            "rows": 1,
            "columns": 1,
            "fragments": [{"url": url, "duration": 1}],
        }

    def _thumbnail_format(self, **codecs: str) -> dict[str, Any]:
        url = f"{self.media_url}/tiny.jpg"
        return {"format_id": "t", "format_note": "thumbnail", "url": url, "ext": "jpg", **codecs}

    def _with_formats(self, item: str, *formats: dict[str, Any]) -> dict[str, Any]:
        video = self._video(item)
        video["formats"] = list(formats)
        return video

    def _codec_shape(self, page: str) -> dict[str, Any] | None:
        """Shapes real extractors produce, one page each."""
        plain_video = {"format_id": "v", "url": f"{self.media_url}/tiny.mp4", "ext": "mp4"}
        plain_audio = {"format_id": "a", "url": f"{self.media_url}/tiny.m4a", "ext": "m4a"}
        audio = {**plain_audio, "vcodec": "none"}
        image_format = self._thumbnail_format(vcodec="none", acodec="none")
        if page == "jpg":
            return self._url_only("p1", "tiny.jpg", "jpg")
        if page == "ig-image":
            return self._instagram_image("i1")
        if page == "ig-lone-image":
            # instagram.py's two paths for a post holding one image: logged out, it raises
            # unless ignore_no_formats_error is set; logged in, the image comes back.
            if not self._downloader.cookies_used:
                self.raise_no_formats("There is no video in this post", expected=True)
            return self._instagram_image("i1")
        if page == "ig-lone-image-b":
            return self._instagram_image("i9")
        if page == "ig-lone-link":
            # A page that only points at the post: yt-dlp follows the link to a lone result.
            return self.url_result("https://fake.test/ig-lone-image")
        if page == "ig-lone-broken":
            broken = {"url": f"{self.media_url}/status/404/i1.jpg", "width": 64, "height": 64}
            return {**self._instagram_image("i1"), "thumbnails": [broken]}
        if page == "ig-video":
            return self._instagram_video("v1")
        if page == "ig-carousel":
            entries = [
                self._instagram_image("i1"),
                self._instagram_video("v1"),
                self._instagram_video("v2", acodec="none"),
                self._instagram_image("i2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "ig-repeated-video":
            # A list can hold the same item twice (a YouTube playlist can); single mode
            # names both the same.
            entries = [
                self._instagram_image("i1"),
                self._instagram_video("v1"),
                self._instagram_video("v1"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "ig-broken-image":
            broken = {"url": f"{self.media_url}/status/404/i1.jpg", "width": 64, "height": 64}
            image = {**self._instagram_image("i1"), "thumbnails": [broken]}
            return self.playlist_result([image, self._video("v1")], "post", "Post")
        if page == "ig-two-thumbs":
            # Without login, Instagram's candidates carry no size: yt-dlp sorts them by URL.
            candidates = [{"url": f"{self.media_url}/{name}"} for name in ("small.jpg", "tiny.jpg")]
            image = {**self._instagram_image("i1"), "thumbnails": candidates}
            return self.playlist_result([image], "post", "Post")
        # Two candidates, the cut-off one tried first (preference), then a whole one; the
        # next pair has a whole first try and a second one that is never reached, the last
        # one two cut-off tries.
        fallbacks = {
            "ig-short-then-jpg": ("short/i1.jpg", "tiny.jpg"),
            "ig-short-then-png": ("short/i1.jpg", "tiny.png"),
            "ig-short-png-then-jpg": ("short/i1.png", "tiny.jpg"),
            "ig-jpg-then-png": ("tiny.jpg", "tiny.png"),
            "ig-short-then-short": ("short/i1.jpg", "short/i2.jpg"),
        }
        if page in fallbacks:
            first, second = fallbacks[page]
            candidates = [
                {"url": f"{self.media_url}/{second}", "preference": 0},
                {"url": f"{self.media_url}/{first}", "preference": 1},
            ]
            image = {**self._instagram_image("i1"), "thumbnails": candidates}
            return self.playlist_result([image, self._video("v1")], "post", "Post")
        odd_images = {
            # Cut off long before its stated length: yt-dlp writes part of it, then fails.
            "ig-short-image": f"{self.media_url}/short/i1.jpg",
            # Cut off on the first request only, like a dropped connection.
            "ig-flaky-image": f"{self.media_url}/flaky/i1/tiny.jpg",
            # Whole, but with no Content-Length, or a Content-Length of the encoded body.
            "ig-no-length-image": f"{self.media_url}/nolength/tiny.jpg",
            "ig-gzip-image": f"{self.media_url}/gzip0/tiny.jpg",
            # No scheme, and an extension yt-dlp refuses to write: both raise before the fetch.
            "ig-relative-image": "tiny.jpg",
            "ig-php-image": f"{self.media_url}/{JPEG_AS.format('php')}",
        }
        if page in odd_images:
            image = {**self._instagram_image("i1"), "thumbnails": [{"url": odd_images[page]}]}
            return self.playlist_result([image, self._video("v1")], "post", "Post")
        if page == "ig-png-image":
            image = {
                **self._instagram_image("i1"),
                "thumbnails": [{"url": f"{self.media_url}/tiny.png"}],
            }
            return self.playlist_result([image, self._video("v1")], "post", "Post")
        if page == "storyboard-video":
            return self._with_formats("v1", self._storyboard(), plain_video)
        if page == "storyboard-audio":
            return self._with_formats("a1", self._storyboard(), audio)
        if page == "storyboard-only":
            return self._with_formats("s1", self._storyboard())
        if page == "storyboard-carousel":
            # YouTube's shape when it skipped every stream: only storyboards, and thumbnails.
            thumbnail = {"url": f"{self.media_url}/tiny.jpg", "width": 64, "height": 64}
            storyboard = {**self._with_formats("s1", self._storyboard()), "thumbnails": [thumbnail]}
            return self.playlist_result([storyboard, self._video("v1")], "post", "Post")
        if page == "thumbnail-video":
            return self._with_formats("v1", self._thumbnail_format(), plain_video)
        if page == "thumbnail-audio":
            return self._with_formats("a1", self._thumbnail_format(), audio)
        if page == "image-format":
            return self._with_formats("i1", image_format)
        if page == "site-audio-label":
            # A site extractor's audio with no codec named: 71 modules build this shape.
            label = {"format_id": "a", "url": f"{self.media_url}/theora.ogg", "ext": "ogg"}
            return self._with_formats("a1", {**label, "vcodec": "none"})
        if page in ("weba", "weba-chapters", "weba-subs"):
            # A format ffmpeg can read but has no muxer for, so it can't write tags to it. The
            # container step skips it: yt-dlp lists .weba as audio.
            weba = {"format_id": "o", "url": f"{self.media_url}/audio.webm", "ext": "weba"}
            video = self._with_formats("x1", weba)
            if page == "weba-chapters":
                video["chapters"] = [{"start_time": 0, "end_time": 0.1, "title": "Start"}]
            if page == "weba-subs":
                video["subtitles"] = self._subtitles("en.vtt")
            return video
        if page == "junk-carousel":
            # Text bodies served as videos: every item fails its metadata step.
            junk = [
                f"{self.media_url}/typed/video/mp4/{name}/junk.mp4" for name in ("en.vtt", "es.vtt")
            ]
            entries = [
                self._with_formats(f"j{n}", {"format_id": "j", "url": url, "ext": "mp4"})
                for n, url in enumerate(junk, 1)
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "junk-thumb-carousel":
            # A text body as the first item's thumbnail: -a can't embed it.
            thumb = f"{self.media_url}/typed/image/jpeg/en.vtt/t.jpg"
            entries = [{**self._video("t1"), "thumbnail": thumb}, self._video("t2")]
            return self.playlist_result(entries, "post", "Post")
        if page == "cover-carousel":
            # Mixed audio sources with working covers: an audio, a video with a PNG, an .m4a
            # holding a video, and a video with no thumbnail at all.
            m4a = {"format_id": "m", "url": f"{self.media_url}/clip.m4a", "ext": "m4a"}
            entries = [
                {**self._audio("a1"), "thumbnail": f"{self.media_url}/tiny.jpg"},
                {**self._video("v1"), "thumbnail": f"{self.media_url}/tiny.png"},
                {**self._with_formats("m1", m4a), "thumbnail": f"{self.media_url}/tiny.jpg"},
                self._video("v2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page in ("junk-media-carousel", "junk-media-relative-carousel"):
            # Both items have a thumbnail; the first one's audio extraction fails.
            thumb = "tiny.jpg" if "relative" in page else f"{self.media_url}/tiny.jpg"
            broken = self._video("t1", path=JUNK)
            entries = [{**item, "thumbnail": thumb} for item in (broken, self._video("t2"))]
            return self.playlist_result(entries, "post", "Post")
        if page == "unprobeable-carousel":
            # An HTML page served as the video, which ffprobe can't open, with a thumbnail:
            # yt-dlp's cover step then raises a KeyError after audio extraction reported its
            # own error.
            broken = self._video("t1", path="typed/video/mp4/poster.html/junk.mp4")
            thumb = f"{self.media_url}/tiny.jpg"
            entries = [{**item, "thumbnail": thumb} for item in (broken, self._video("t2"))]
            return self.playlist_result(entries, "post", "Post")
        if page == "junk-thumb":
            thumb = f"{self.media_url}/{JUNK_IMAGE}"
            return {**self._video("t1", page="junk-thumb"), "thumbnail": thumb}
        if page == "thumb-404-carousel":
            # The first item's thumbnail is written before its media turns out to be gone.
            broken = self._video("t1", path="status/404/t1")
            entries = [{**broken, "thumbnail": f"{self.media_url}/tiny.jpg"}, self._video("t2")]
            return self.playlist_result(entries, "post", "Post")
        if page == "site-audio-named":
            named = {"format_id": "a", "url": f"{self.media_url}/tiny.ogg", "ext": "ogg"}
            return self._with_formats("a1", {**named, "vcodec": "none", "acodec": "vorbis"})
        if page == "site-audio-video-ext":
            # A DASH audio rendition: the shape an <audio> declaring a video leaves on a page.
            rendition = {"format_id": "a", "url": f"{self.media_url}/audio.webm", "ext": "webm"}
            return self._with_formats("a1", {**rendition, "vcodec": "none", "acodec": "vorbis"})
        if page == "split-streams":
            video_only = {**plain_video, "acodec": "none"}
            return self._with_formats("v1", video_only, audio)
        if page == "query-carousel":
            entries = [
                {"id": "a1", "title": "Clip", "url": f"{self.media_url}/tiny.m4a?token=1"},
                {"id": "p1", "title": "Clip", "url": f"{self.media_url}/tiny.jpg?w=100"},
                {"id": "v1", "title": "Clip", "url": f"{self.media_url}/tiny.mp4?token=1"},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "undownloadable-carousel":
            entries = [
                self._with_formats("i1", image_format),
                self._with_formats("s1", self._storyboard()),
                {**self._url_only("e1", "tiny.mp4", "mp4"), "formats": []},
                self._bare_video("v1"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "mixed-carousel":
            entries = [
                self._instagram_image("i1"),
                self._url_only("p1", "tiny.jpg", "jpg"),
                self._bare_video("v1"),
                self._audio("a1"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "no-video-carousel":
            entries = [
                self._instagram_image("i1"),
                self._url_only("p1", "tiny.jpg", "jpg"),
                self._audio("a1"),
            ]
            return self.playlist_result(entries, "post", "Post")
        return None

    def _failure_shape(self, page: str) -> dict[str, Any] | None:
        """Failures built with yt-dlp's own raisers, so the wording is yt-dlp's.

        Some raisers only warn under ``ignore_no_formats_error``, and then real extractors
        go on to return their metadata. Those pages must too: falling through would land
        on "This post is private", and a test could no longer catch that option.
        """
        metadata = self._instagram_image("m1")
        if page == "login-hint":
            self.raise_login_required()
        if page == "anonymous-limit":
            self.raise_login_required(ANONYMOUS_LIMIT)
        if page == "age-gate":
            hint = self._login_hint(method="cookies")
            self.raise_no_formats(f"Sign in to confirm your age. {hint}", expected=True)
            return metadata
        if page == "rate-limited":
            self.raise_no_formats(SESSION_RATE_LIMIT, expected=True)
            return metadata
        if page == "geo":
            self.raise_geo_restricted(countries=["US"])
        if page == "geo-metadata":
            self.raise_geo_restricted(GEO_BLOCK, ["US"], metadata_available=True)
            return metadata
        if page == "paid-post":
            self.raise_login_required(SUBSCRIBERS_ONLY, metadata_available=True)
            return metadata
        if page == "no-video-post":
            self.raise_no_formats("There is no video in this post", expected=True)
            return metadata
        if page == "drm":
            drm_format = {"url": f"{self.media_url}/tiny.mp4", "ext": "mp4", "has_drm": True}
            return {**metadata, "formats": [drm_format]}
        if page == "extractor-bug":
            raise ExtractorError("Unable to extract video data")
        if page == "crash":
            # A bug in extractor code, the kind a site change causes: yt-dlp doesn't wrap it.
            raise TypeError("'NoneType' object is not subscriptable")
        return None

    def _partial_shape(self, page: str) -> dict[str, Any] | None:
        """Carousels where some entries fail, or are left empty."""
        follow = "https://fake.test/{}".format
        video, image = self._video("v2"), self._image("i1")
        if page == "partial-gated":
            entries = [video, image, self.url_result(follow("needs-cookies"))]
        elif page == "partial-rate":
            entries = [video, self.url_result(follow("rate-limited"))]
        elif page == "partial-broken":
            entries = [video, image, self.url_result(follow("extractor-bug"))]
        elif page == "all-gated":
            entries = [self.url_result(follow(p)) for p in ("needs-cookies", "login-hint")]
        elif page == "partial-both":
            entries = [
                self.url_result(follow("rate-limited")),
                self.url_result(follow("needs-cookies")),
                video,
            ]
        elif page == "hole":
            entries = [video, None]
        elif page == "ig-linked-carousel":
            # Entries that are links, like a profile's posts: the item is only known once followed.
            entries = [self.url_result(follow(p)) for p in ("ig-image", "ig-video")]
        else:
            return None
        return self.playlist_result(entries, "post", "Post")

    def _edge_shape(self, page: str) -> dict[str, Any] | None:
        """What an override of ``process_video_result`` gets before yt-dlp repairs or rejects it."""
        follow = "https://fake.test/{}".format
        gated, broken = self.url_result(follow("needs-cookies")), self.url_result(follow("drm"))
        video, thumbnail = self._video("v2"), f"{self.media_url}/tiny.jpg"
        # Extractors pass an API's text through: yt-dlp only makes it numeric later.
        text_duration = {**self._instagram_image("i1"), "duration": "12"}
        odd_types = {"duration": ["12"], "title": 123, "id": 456, "height": "tall", "width": {}}
        no_url = {**self._instagram_image("n1"), "formats": [{"format_id": "x", "ext": "mp4"}]}
        # The generic extractor's id is the URL's last segment, unquoted. It only reaches this
        # message on recent yt-dlp, so a loopback 404 can't show it at the floor.
        missing = {"404-login": "login", "404-spaced": "private clip", "404-colon": "tag:private"}

        def fails_at_once() -> Iterator[dict[str, Any]]:
            self.raise_login_required()
            yield video

        def fails_midway() -> Iterator[dict[str, Any]]:
            yield video
            self.raise_login_required()

        def two_lazily() -> Iterator[dict[str, Any]]:
            # Most paged extractors: yt-dlp only learns the size by reading to the end.
            yield video
            yield self._video("v3")

        if page in missing:
            raise ExtractorError(
                "Unable to download webpage: HTTP Error 404: Not Found",
                expected=True,
                video_id=missing[page],
                ie="generic",
            )
        if page == "text-duration-image":
            return text_duration
        if page == "text-duration-carousel":
            return self.playlist_result([text_duration, video], "post", "Post")
        if page == "odd-types-carousel":
            return self.playlist_result(
                [{**self._instagram_image("i1"), **odd_types}, video], "post", "Post"
            )
        if page == "live-video":
            return {**self._video("v1"), "is_live": True, "thumbnail": thumbnail}
        if page == "upcoming":
            self.raise_no_formats("This live event will begin in 5 minutes", expected=True)
            return {**self._instagram_image("m1"), "live_status": "is_upcoming"}
        if page == "url-video":
            return {**self._url_only("v1", "tiny.mp4", "mp4"), "thumbnail": thumbnail}
        if page == "no-url-format":
            return no_url
        if page == "blank-error":
            raise ExtractorError("", expected=True)
        if page == "two-line-error":
            raise ExtractorError("This post is gone\nIt was removed by the uploader", expected=True)
        if page == "transparent-gated":
            return {"_type": "url_transparent", "url": follow("needs-cookies"), "title": "Clip"}
        if page == "lazy-gated":
            return self.playlist_result(fails_at_once(), "post", "Post")
        if page == "lazy-half":
            return self.playlist_result(fails_midway(), "post", "Post")
        if page == "nested-gated":
            return self.playlist_result([self.url_result(follow("partial-gated"))], "post", "Post")
        if page == "lazy-carousel":
            return self.playlist_result(two_lazily(), "post", "Post")
        if page in ("hidden-items", "stale-count"):
            # YouTube's tab extractor sets playlist_count from the header's "N videos", which
            # counts the unavailable videos it hides from the list, and can lag behind it.
            entries = [self._video("v1"), video]
            if page == "stale-count":
                entries.append(self._video("v3"))
            declared = 3 if page == "hidden-items" else 2
            return {**self.playlist_result(entries, "post", "Post"), "playlist_count": declared}
        if page == "lazy-hidden":
            # The same header count on a paged list, YouTube's real shape: 3 declared, 2 read.
            return {**self.playlist_result(two_lazily(), "post", "Post"), "playlist_count": 3}
        if page == "multi-hidden":
            # A multi_video result (one video in parts) carries the same declared count.
            parts = [self._video("v1"), video]
            return {
                **self.playlist_result(parts, "post", "Post", multi_video=True),
                "playlist_count": 3,
            }
        if page == "partial-drm":
            return self.playlist_result([video, broken], "post", "Post")
        if page == "partial-no-url":
            return self.playlist_result([video, no_url], "post", "Post")
        if page == "partial-first":
            return self.playlist_result([gated, video, self._video("v3")], "post", "Post")
        if page == "partial-all-but-one":
            rest = [self.url_result(follow(p)) for p in ("extractor-bug", "rate-limited")]
            return self.playlist_result([gated, rest[0], video, rest[1]], "post", "Post")
        if page == "partial-private":
            return self.playlist_result([video, self.url_result(follow("private"))], "post", "Post")
        if page == "images-and-gated":
            return self.playlist_result([self._image("i1"), gated], "post", "Post")
        if page == "unscoped-cookies":
            # yt-dlp reports these with is_error=False, and saves the video all the same.
            clip = self._video("v1")
            clip["formats"][0]["cookies"] = "a=b"
            return clip
        if page == "unscoped-and-broken":
            clip = self._video("v1")
            clip["formats"][0]["cookies"] = "a=b"
            return self.playlist_result([clip, self._video("j1", path=JUNK)], "post", "Post")
        if page == "partial-rejected" and self._downloader.cookies_used:
            raise ExtractorError("Failed to parse JSON", expected=True)
        if page == "partial-rejected":
            return self.playlist_result([video, gated], "post", "Post")
        return None

    def _subtitles(self, *files: str) -> dict[str, list[dict[str, str]]]:
        """``lang.ext`` files on the media server, keyed by language."""
        tracks: dict[str, list[dict[str, str]]] = {}
        for name in files:
            lang, ext = name.split(".")
            tracks[lang] = [{"url": f"{self.media_url}/{name}", "ext": ext}]
        return tracks

    def _subtitled(
        self, video: dict[str, Any], *files: str, auto: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        return {
            **video,
            "subtitles": self._subtitles(*files),
            "automatic_captions": self._subtitles(*auto),
        }

    def _subtitle_shape(self, page: str) -> dict[str, Any] | None:
        """Pages whose videos carry subtitles and auto-captions, as YouTube's do."""
        webm = {"format_id": "w", "url": f"{self.media_url}/tiny.webm", "ext": "webm"}
        webm_video = self._with_formats("w1", {**webm, "vcodec": "vp8", "acodec": "vorbis"})
        if page == "subs":
            return self._subtitled(self._video("v1", page="subs"), "en.vtt", auto=("es.vtt",))
        if page == "subs-en":
            return self._subtitled(self._video("v1", page="subs-en"), "en.vtt")
        if page == "subs-merged":
            return self._subtitled(self._merged_video("v1", "subs-merged"), "en.vtt", "es.vtt")
        if page == "subs-fr":
            return self._subtitled(self._video("v1", page="subs-fr"), "fr.vtt")
        if page == "subs-ttml":
            return self._subtitled(self._video("v1", page="subs-ttml"), "en.ttml")
        if page == "subs-webm":
            return self._subtitled(webm_video, "en.vtt")
        if page == "subs-webm-srt":
            return self._subtitled(webm_video, "en.srt")
        if page == "subs-webm-two":
            return self._subtitled(webm_video, "en.vtt", "es.vtt")
        if page == "subs-carousel":
            entries = [self._subtitled(self._video("s1"), "en.vtt"), self._video("s2")]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-ttml-carousel":
            entries = [
                self._subtitled(self._video("s1"), "en.vtt"),
                self._subtitled(self._video("s2"), "en.ttml"),
            ]
            return self.playlist_result(entries, "post", "Post")
        return None

    def _tracks(self, **files: str) -> dict[str, Any]:
        """Subtitles keyed by language, each pointing at any file on the media server."""
        return {
            lang: [{"url": f"{self.media_url}/{name}", "ext": name.rsplit(".", 1)[1]}]
            for lang, name in files.items()
        }

    def _subtitle_attack_shape(self, page: str) -> dict[str, Any] | None:
        """Subtitle tracks that break the embed step, one page each."""
        webm = {"format_id": "w", "url": f"{self.media_url}/tiny.webm", "ext": "webm"}
        webm_video = self._with_formats("w1", {**webm, "vcodec": "vp8", "acodec": "vorbis"})
        flv = {"format_id": "f", "url": f"{self.media_url}/tiny.flv", "ext": "flv"}
        flv_video = self._with_formats("f1", {**flv, "vcodec": "h264", "acodec": "aac"})
        tracks: dict[str, dict[str, str]] = {
            "subs-empty": {"en": "empty.vtt"},
            "subs-garbage": {"en": "garbage.vtt"},
            "subs-garbage-two": {"en": "en.vtt", "es": "garbage.vtt"},
            "subs-header": {"en": "header.vtt"},
            "subs-one-broken": {"en": "en.vtt", "es": "en.ttml"},
            "subs-404": {"en": "missing.vtt"},
            "subs-404-two": {"en": "en.vtt", "es": "missing.vtt"},
            "subs-short": {"en": "short/en.vtt"},  # Cut off long before its stated length.
            "subs-note": {"en": "note.vtt"},
            "subs-404-both": {"en": "missing.vtt", "es": "missing.vtt"},
            "subs-503": {"en": "status/503/en.vtt"},
            "subs-429": {"en": "status/429/en.vtt"},
            "subs-hang": {"en": "hang/en.vtt"},
            "subs-ttml-404": {"en": "en.ttml", "es": "missing.vtt"},
            "subs-garbage-404": {"en": "garbage.vtt", "es": "missing.vtt"},
            "subs-srt-404": {"en": "en.srt", "es": "missing.vtt"},
            "subs-srt": {"en": "en.srt"},
            "subs-ass": {"en": "en.ass"},
        }
        if page in tracks:
            return {**self._video("v1", page=page), "subtitles": self._tracks(**tracks[page])}
        if page in ("subs-merged-srt", "subs-merged-ass"):
            name = "en.srt" if page.endswith("srt") else "en.ass"
            return {**self._merged_video("v1", page), "subtitles": self._tracks(en=name)}
        if page == "subs-webm-ass":
            return {**webm_video, "subtitles": self._tracks(en="en.ass")}
        if page == "subs-webm-mixed":
            return {**webm_video, "subtitles": self._tracks(en="en.vtt", es="en.srt")}
        if page == "subs-webm-wrong-media":
            # A video served as a vtt: ffmpeg opens the temp file, then can't write it.
            wrong = [{"url": f"{self.media_url}/tiny.mp4", "ext": "vtt"}]
            return {**webm_video, "subtitles": {"en": wrong}}
        if page == "subs-flv":
            return {**flv_video, "subtitles": self._tracks(en="en.vtt")}
        if page in ("subs-novideo", "subs-junk"):
            path = "missing.mp4" if page == "subs-novideo" else JUNK
            return {
                **self._video("v1", path=path, page=page),
                "subtitles": self._tracks(en="en.vtt"),
            }
        if page == "subs-novideo-carousel":
            entries = [
                {**self._video("s1", path="missing.mp4"), "subtitles": self._tracks(en="en.vtt")},
                self._video("s2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-auto-404":  # A subtitle, and auto-captions that 404.
            return {
                **self._video("v1", page=page),
                "subtitles": self._tracks(en="en.vtt"),
                "automatic_captions": self._tracks(es="missing.vtt"),
            }
        if page == "subs-inline-empty":  # Served inline, as some extractors do, with no text.
            inline = {"en": [{"data": "", "ext": "vtt"}]}
            return {**self._video("v1", page=page), "subtitles": inline}
        if page == "subs-webm-404":
            return {**webm_video, "subtitles": self._tracks(en="en.vtt", es="missing.vtt")}
        if page == "subs-carousel-404-last":  # Item 1 fine, item 2's subtitle 404s.
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="en.vtt")},
                {**self._video("s2"), "subtitles": self._tracks(en="missing.vtt")},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-carousel-short":  # Every item's subtitle is cut off or empty.
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="short/en.vtt")},
                {**self._video("s2"), "subtitles": self._tracks(en="empty.vtt")},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-junk-404":  # A junk body, and a subtitle that 404s.
            return {
                **self._video("v1", path=JUNK, page=page),
                "subtitles": self._tracks(en="missing.vtt"),
            }
        if page == "subs-junk-404-carousel":  # Item 1 as subs-junk-404, item 2 plain.
            entries = [
                {**self._video("s1", path=JUNK), "subtitles": self._tracks(en="missing.vtt")},
                self._video("s2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-garbage-carousel":  # Item 1's subtitle holds no text, item 2's does.
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="garbage.vtt")},
                {**self._video("s2"), "subtitles": self._tracks(en="en.vtt")},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-404-novideo":  # The subtitle 404s, then so does the video.
            return {
                **self._video("v1", path="missing.mp4", page=page),
                "subtitles": self._tracks(en="missing.vtt"),
            }
        if page == "subs-404-then-novideo":  # Item 1's subtitle 404s, item 2's video does.
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="missing.vtt")},
                {**self._video("s2", path="missing.mp4"), "subtitles": self._tracks(en="en.vtt")},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-novideo-two":  # The video 404s after two subtitles were written.
            return {
                **self._video("v1", path="missing.mp4", page=page),
                "subtitles": self._tracks(en="en.vtt", es="es.vtt"),
            }
        if page == "subs-auto-novideo":  # Only an auto-caption, then the video 404s.
            return {
                **self._video("v1", path="missing.mp4", page=page),
                "automatic_captions": self._tracks(en="en.vtt"),
            }
        if page in ("subs-carousel-both", "subs-carousel-novideo-last"):
            # Same items, so the same names; in the second, item 2's video 404s.
            last = "missing.mp4" if page.endswith("last") else "tiny.mp4"
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="en.vtt")},
                {**self._video("s2", path=last), "subtitles": self._tracks(en="en.vtt")},
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-404-carousel":
            entries = [
                {**self._video("s1"), "subtitles": self._tracks(en="missing.vtt")},
                self._video("s2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "subs-image-carousel":
            entries = [
                self._image("i1"),
                {**self._video("s1"), "subtitles": self._tracks(en="en.vtt")},
                self._image("i2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "nosubs-carousel":
            return self.playlist_result([self._video("s1"), self._video("s2")], "post", "Post")
        return None

    def _container_shape(self, page: str) -> dict[str, Any] | None:
        """Sources in a container other than the one asked for."""
        webm = {"format_id": "w", "url": f"{self.media_url}/tiny.webm", "ext": "webm"}
        attached = {"format_id": "x", "url": f"{self.media_url}/attached.mkv", "ext": "mkv"}
        if page in ("single-webm", "single-webm-b"):
            # Same title, different pages: two videos that only share a name.
            video = self._video(page, page=page)
            video["formats"] = [{**webm, "vcodec": "vp8", "acodec": "vorbis"}]
            return video
        attached_video = self._with_formats("x1", {**attached, "vcodec": "h264", "acodec": "aac"})
        if page == "attached":
            return attached_video
        if page == "attached-carousel":
            return self.playlist_result([attached_video, self._video("v1")], "post", "Post")
        if page == "mp4-or-webm":
            mp4 = {"format_id": "alt", "url": f"{self.media_url}/tiny.mp4", "ext": "mp4"}
            return self._with_formats("v1", mp4, webm)
        return None

    @staticmethod
    def _image(item: str) -> dict[str, Any]:
        return {
            "id": item,
            "title": "Clip",
            "webpage_url": "https://fake.test/post",
            "formats": [],
        }

    def _real_extract(self, url: str) -> dict[str, Any]:
        page = self._match_id(url)
        cookies = self._downloader.cookies_used
        shape = (
            self._codec_shape(page)
            or self._failure_shape(page)
            or self._partial_shape(page)
            or self._edge_shape(page)
            or self._subtitle_shape(page)
            or self._subtitle_attack_shape(page)
            or self._container_shape(page)
        )
        if shape is not None:
            return shape
        if page == "video":
            return self._video("v1", page="video")
        if page == "video-b":
            return self._video("v2", page="video-b")
        if page == "image":
            return self._image("i1")
        if page == "thumbnail-image":
            return {**self._image("i1"), "thumbnail": f"{self.media_url}/tiny.jpg"}
        if page == "merged":
            return self._merged_video("v1", "merged")
        if page == "empty":
            return self.playlist_result([], "post", "Post")
        if page == "rejects-cookies-carousel" and cookies:
            raise ExtractorError("Failed to parse JSON", expected=True)
        if page in ("carousel", "rejects-cookies-carousel"):
            entries = [
                self._image("i1"),
                self._video("v1"),
                self._image("i2"),
                self._video("v2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "only-images":
            return self.playlist_result([self._image("i1"), self._image("i2")], "post", "Post")
        if page == "bare-carousel":
            entries = [self._image("i1"), self._bare_video("v1")]
            return self.playlist_result(entries, "post", "Post")
        if page == "url-only-carousel":
            entries = [self._url_only(i, "tiny.mp4", "mp4") for i in ("v1", "v2")]
            return self.playlist_result(entries, "post", "Post")
        if page == "jpg-carousel":
            entries = [self._url_only(i, "tiny.jpg", "jpg") for i in ("p1", "p2")]
            return self.playlist_result(entries, "post", "Post")
        if page == "audio-carousel":
            return self.playlist_result([self._audio("a1"), self._audio("a2")], "post", "Post")
        if page == "broken-item":
            entries = [self._video("v1"), self._video("v2", path="missing.mp4")]
            return self.playlist_result(entries, "post", "Post")
        if page == "all-broken":
            entries = [self._video("v1", path="missing.mp4")]
            return self.playlist_result(entries, "post", "Post")
        if page == "rejects-cookies" and cookies:
            raise ExtractorError("Failed to parse JSON", expected=True)
        if page == "rejects-cookies":
            return self._video("v1", page="rejects-cookies")
        if page == "needs-cookies" and not cookies:
            raise ExtractorError("Login required", expected=True)
        if page == "needs-cookies":
            return self._video("v1", page="needs-cookies")
        if page == "hidden-404" and not cookies:
            # Sites hide private content this way, and yt-dlp can't tell it from a dead link.
            raise ExtractorError(HTTP_404, expected=True)
        if page == "hidden-404":
            return self._video("v1", page="hidden-404")
        if page == "gated-carousel" and not cookies:
            self.raise_login_required(FOLLOWERS_ONLY)
        if page == "gated-carousel":
            entries = [self._image("i1"), self._video("v1"), self._video("v2")]
            return self.playlist_result(entries, "post", "Post")
        raise ExtractorError("This post is private", expected=True)


@pytest.fixture
def fake_site(monkeypatch: pytest.MonkeyPatch, media_url: str) -> list[Any]:
    """Point every yt-dlp entry point at the fake site.

    Returns the ``cookiesfrombrowser`` value each instance was created with.
    Browser cookies are never read.
    """
    monkeypatch.setattr(FakeSiteIE, "media_url", media_url)
    seen: list[Any] = []

    class FakeYdl(downloader._Ydl):  # type: ignore[misc, name-defined]
        def __init__(self, params: dict[str, Any] | None = None) -> None:
            params = dict(params or {})
            self.cookies_used = params.pop("cookiesfrombrowser", None)
            seen.append(self.cookies_used)
            super().__init__(params, auto_init=False)
            self.add_info_extractor(FakeSiteIE())

    monkeypatch.setattr(downloader, "_Ydl", FakeYdl)
    monkeypatch.setattr(downloader, "YoutubeDL", FakeYdl)

    class FakeProbeYdl(FakeYdl, probe_module._ProbeYdl):  # type: ignore[misc, name-defined]
        pass

    monkeypatch.setattr(probe_module, "_ProbeYdl", FakeProbeYdl)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYdl)
    return seen
