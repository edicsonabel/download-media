"""Tests for the probe module — site detection, classification, auth-error sniff."""

from __future__ import annotations

import pytest

from download_media.probe import (
    ContentKind,
    _classify_entry,
    _classify_single,
    _is_auth_error,
    detect_site,
    site_likely_needs_cookies,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/p/abc/", "instagram"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.tiktok.com/@user/video/123", "tiktok"),
        ("https://twitter.com/user/status/123", "x"),
        ("https://x.com/user/status/123", "x"),
        ("https://www.facebook.com/watch?v=123", "facebook"),
        ("https://fb.watch/abc", "facebook"),
        ("https://vimeo.com/123", "vimeo"),
        ("https://www.twitch.tv/user", "twitch"),
        ("https://soundcloud.com/user/track", "soundcloud"),
        ("https://example.com/random", "generic"),
    ],
)
def test_detect_site(url: str, expected: str) -> None:
    assert detect_site(url) == expected


def test_cookie_hungry_sites() -> None:
    assert site_likely_needs_cookies("instagram") is True
    assert site_likely_needs_cookies("x") is True
    assert site_likely_needs_cookies("facebook") is True
    assert site_likely_needs_cookies("youtube") is False
    assert site_likely_needs_cookies("vimeo") is False
    assert site_likely_needs_cookies("generic") is False


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Sign in to confirm you're not a bot", True),
        ("ERROR: Login required to access this video", True),
        ("This is a private account", True),
        ("Rate-limit reached", True),
        ("HTTP 404 Not Found", False),
        ("Connection refused", False),
        ("Unable to extract initial data", False),
    ],
)
def test_is_auth_error(msg: str, expected: bool) -> None:
    assert _is_auth_error(msg) is expected


def test_classify_entry_none_is_image() -> None:
    item = _classify_entry(None, 5)
    assert item.kind == ContentKind.SINGLE_IMAGE
    assert item.supported is False
    assert "image" in item.title.lower()


def test_classify_entry_video() -> None:
    entry = {
        "id": "abc",
        "title": "Test Video",
        "duration": 60,
        "formats": [{"vcodec": "h264", "acodec": "aac", "ext": "mp4"}],
    }
    item = _classify_entry(entry, 1)
    assert item.kind == ContentKind.SINGLE_VIDEO
    assert item.supported is True
    assert item.title == "Test Video"


def test_classify_entry_audio() -> None:
    entry = {
        "id": "abc",
        "title": "Track",
        "duration": 0,
        "formats": [{"vcodec": "none", "acodec": "mp3", "ext": "mp3"}],
    }
    item = _classify_entry(entry, 1)
    assert item.kind == ContentKind.SINGLE_AUDIO
    assert item.supported is True


def test_classify_single_video() -> None:
    data = {"formats": [{"vcodec": "h264", "acodec": "aac"}]}
    assert _classify_single(data) == ContentKind.SINGLE_VIDEO


def test_classify_single_audio() -> None:
    data = {"formats": [{"vcodec": "none", "acodec": "mp3"}]}
    assert _classify_single(data) == ContentKind.SINGLE_AUDIO


def test_classify_single_image() -> None:
    data = {"formats": [], "thumbnails": [{"url": "http://x/img.jpg"}]}
    assert _classify_single(data) == ContentKind.SINGLE_IMAGE


def test_classify_single_unknown_when_empty() -> None:
    assert _classify_single({}) == ContentKind.UNKNOWN
