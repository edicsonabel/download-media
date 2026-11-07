"""Shared fixtures: environment isolation, scripted input, and a fake site.

The fake site drives the real yt-dlp and ffmpeg against a loopback HTTP server.
Mocks can't catch what these tests are for: how yt-dlp reacts to carousels,
``ignoreerrors``, filters and filenames.
"""

from __future__ import annotations

import builtins
import functools
import http.server
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yt_dlp
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

from download_media import downloader
from download_media import probe as probe_module


@pytest.fixture(autouse=True)
def isolated_env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep tests away from the real history log and browser flag files."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Live tests exist to exercise the real browser setup.
    if "live" not in request.keywords:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


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
    def log_message(self, format: str, *args: Any) -> None:
        pass


def _write_generic_pages(root: Path, base: str) -> None:
    """Pages for yt-dlp's real generic extractor, which leaves codecs unknown."""
    og_image = f'<meta property="og:image" content="{base}/tiny.jpg">'
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
        "audio-relative-og": '<audio src="tiny.m4a"></audio><meta property="og:image" content="tiny.jpg">',
        "video-ogg": '<video src="theora.ogg"></video>',
        "video-ogg-codecs": (
            """<video><source src="theora.ogg" type='video/ogg; codecs="theora, vorbis"'></video>"""
        ),
        "video-ogx": '<video src="theora.ogx"></video>',
        "video-m4b": '<video src="clip.m4b"></video>',
        "video-wma": '<video src="clip.wma"></video>',
        "video-m4a": '<video src="clip.m4a"></video>',
        "og-video-ogg": f'<meta property="og:video" content="{base}/theora.ogg">',
        "video-tag-audio-ogg": '<video src="tiny.ogg"></video>',
        "video-tag-audio-m4b": '<video src="tiny.m4b"></video>',
        "audio-theora-ogg": '<audio src="theora.ogg"></audio>',
        "ogg-video-and-audio": '<video src="theora.ogg"></video><audio src="tiny.ogg"></audio>',
        "everything": (
            '<video src="v1.mp4"></video><video src="tiny.jpg"></video><audio src="a1.m4a"></audio>'
        ),
        "no-video": '<video src="tiny.jpg"></video><audio src="a1.m4a"></audio>',
    }
    for name, body in pages.items():
        (root / f"{name}.html").write_text(f"<!doctype html><html>{body}</html>", encoding="utf-8")


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
    subprocess.run([*source, "-frames:v", "1", str(root / "tiny.png")], check=True)
    subprocess.run([*source, "-frames:v", "1", str(root / "tiny.gif")], check=True)
    subprocess.run([*source, "-vn", "-c:a", "copy", str(root / "tiny.m4a")], check=True)
    subprocess.run([*source, "-vn", "-c:a", "libvorbis", str(root / "tiny.ogg")], check=True)
    subprocess.run([*source, "-vn", str(root / "tiny.wav")], check=True)
    subprocess.run(
        [*source, "-c:v", "libvpx", "-c:a", "libvorbis", str(root / "tiny.webm")], check=True
    )
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
    hls = ["-c", "copy", "-f", "hls", "-hls_playlist_type", "vod", str(root / "tiny.m3u8")]
    subprocess.run([*source, *hls], check=True)
    for copy in ("v1", "v2", "v3", "v4"):
        shutil.copy(root / "tiny.mp4", root / f"{copy}.mp4")
    for copy in ("a1", "a2"):
        shutil.copy(root / "tiny.m4a", root / f"{copy}.m4a")
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    # After the server starts: og:image and og:video need its port.
    _write_generic_pages(root, base)
    yield base
    server.shutdown()


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
        if page == "storyboard-video":
            return self._with_formats("v1", self._storyboard(), plain_video)
        if page == "storyboard-audio":
            return self._with_formats("a1", self._storyboard(), audio)
        if page == "storyboard-only":
            return self._with_formats("s1", self._storyboard())
        if page == "thumbnail-video":
            return self._with_formats("v1", self._thumbnail_format(), plain_video)
        if page == "thumbnail-audio":
            return self._with_formats("a1", self._thumbnail_format(), audio)
        if page == "image-format":
            return self._with_formats("i1", image_format)
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
        shape = self._codec_shape(page)
        if shape is not None:
            return shape
        if page == "video":
            return self._video("v1", page="video")
        if page == "video-b":
            return self._video("v2", page="video-b")
        if page == "image":
            return self._image("i1")
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
    monkeypatch.setattr(probe_module, "YoutubeDL", FakeYdl)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYdl)
    return seen
