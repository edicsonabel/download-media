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


@pytest.fixture(scope="session")
def media_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Base URL of a loopback server hosting one tiny real video."""
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
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
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
        if page == "video":
            return self._video("v1", page="video")
        if page == "video-b":
            return self._video("v2", page="video-b")
        if page == "image":
            return self._image("i1")
        if page == "carousel":
            entries = [
                self._image("i1"),
                self._video("v1"),
                self._image("i2"),
                self._video("v2"),
            ]
            return self.playlist_result(entries, "post", "Post")
        if page == "only-images":
            return self.playlist_result([self._image("i1"), self._image("i2")], "post", "Post")
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
