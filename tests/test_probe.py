"""Tests for the probe module — site detection, classification, auth-error sniff."""

from __future__ import annotations

from itertools import combinations
from typing import Any

import pytest
from yt_dlp.utils import MEDIA_EXTENSIONS

from download_media.downloader import _skip_items_without_media
from download_media.probe import (
    ContentKind,
    _classify_entry,
    _classify_single,
    _is_auth_error,
    _media_kind,
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


STORYBOARD = {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"}
IMAGE, AUDIO, VIDEO, UNKNOWN = (
    ContentKind.SINGLE_IMAGE,
    ContentKind.SINGLE_AUDIO,
    ContentKind.SINGLE_VIDEO,
    ContentKind.UNKNOWN,
)


@pytest.mark.parametrize(
    "fmt,kind",
    [
        ({"vcodec": "none", "acodec": "none", "ext": "mp4"}, IMAGE),
        ({"vcodec": "none", "acodec": "none", "ext": "jpg"}, IMAGE),
        ({"vcodec": "none", "acodec": "aac", "ext": "m4a"}, AUDIO),
        ({"vcodec": "none", "acodec": None, "ext": "m4a"}, AUDIO),
        ({"vcodec": "none", "acodec": None, "ext": "mp4"}, AUDIO),
        ({"vcodec": "h264", "acodec": "aac", "ext": "mp4"}, VIDEO),
        ({"vcodec": "h264", "acodec": "none", "ext": "mp4"}, VIDEO),
        ({"vcodec": "h264", "acodec": None, "ext": "jpg"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": "m4a"}, AUDIO),
        ({"vcodec": None, "acodec": "mp3", "ext": "mp3"}, AUDIO),
        ({"vcodec": None, "acodec": "none", "ext": "ogg"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": "jpg"}, UNKNOWN),
        ({"vcodec": None, "acodec": None, "ext": "png"}, UNKNOWN),
        ({"vcodec": None, "acodec": None, "ext": "webp"}, UNKNOWN),
        ({"vcodec": None, "acodec": None, "ext": "mp4"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": "webm"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": "gif"}, VIDEO),
        ({"vcodec": None, "acodec": "mp4a.40.2", "ext": "mp4"}, VIDEO),
        ({"ext": "mp4"}, VIDEO),
        ({}, VIDEO),
    ],
)
@pytest.mark.parametrize(
    "shape",
    [
        lambda fmt: {"id": "i", "title": "t", "formats": [fmt]},
        lambda fmt: {"id": "i", "title": "t", "url": "http://x/f", **fmt},
    ],
    ids=["formats", "url-only"],
)
def test_classification_rule(shape: Any, fmt: dict[str, Any], kind: ContentKind) -> None:
    info = shape(fmt)
    item = _classify_entry(info, 1)
    assert item.kind == kind
    assert item.supported is (kind != IMAGE)
    assert _classify_single(info) == kind


@pytest.mark.parametrize(
    "formats,kind",
    [
        ([STORYBOARD, {"vcodec": None, "acodec": None, "ext": "mp4"}], VIDEO),
        ([STORYBOARD, {"vcodec": "none", "acodec": "opus", "ext": "webm"}], AUDIO),
        ([STORYBOARD, {"vcodec": None, "acodec": None, "ext": "jpg"}], UNKNOWN),
        ([{"vcodec": "none", "acodec": "aac", "ext": "m4a"}, {"vcodec": "h264"}], VIDEO),
        ([{"vcodec": None, "ext": "jpg"}, {"vcodec": "none", "acodec": "aac"}], AUDIO),
    ],
)
def test_strongest_format_decides(formats: list[dict[str, Any]], kind: ContentKind) -> None:
    item = _classify_entry({"id": "i", "title": "t", "formats": formats}, 1)
    assert item.kind == kind
    assert item.supported is True
    assert _classify_single({"formats": formats}) == kind


@pytest.mark.parametrize(
    "fmt,kind",
    [
        ({"vcodec": "none"}, AUDIO),
        ({"vcodec": "none", "ext": "jpg"}, AUDIO),
        ({"acodec": "none"}, VIDEO),
        ({"acodec": "none", "ext": "jpg"}, UNKNOWN),
        ({"acodec": "aac", "ext": "png"}, UNKNOWN),
        ({"vcodec": "", "acodec": "", "ext": "mp4"}, VIDEO),
        ({"vcodec": "", "acodec": "", "ext": "m4a"}, AUDIO),
        ({"vcodec": None, "acodec": None, "ext": "wav"}, AUDIO),
        ({"vcodec": None, "acodec": None, "ext": "opus"}, AUDIO),
        ({"vcodec": None, "acodec": None, "ext": "oga"}, AUDIO),
        ({"vcodec": None, "acodec": None, "ext": "aac"}, AUDIO),
        ({"vcodec": None, "acodec": None, "ext": "ogv"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": "unknown_video"}, VIDEO),
        ({"vcodec": None, "acodec": None, "ext": None}, VIDEO),
        ({"vcodec": "vp9", "acodec": "none", "ext": "m4a"}, VIDEO),
        ({"vcodec": "none", "acodec": "none", "ext": "mhtml", "format_note": "storyboard"}, IMAGE),
        ({"vcodec": "none", "acodec": "none", "ext": "jpg", "format_note": "thumbnail"}, IMAGE),
        ({"vcodec": None, "acodec": None, "ext": "jpg", "format_note": "thumbnail"}, UNKNOWN),
    ],
)
def test_format_with_one_codec_set_or_neither(fmt: dict[str, Any], kind: ContentKind) -> None:
    assert _classify_single({"formats": [fmt]}) == kind


MAY_HOLD_VIDEO = ["asf", "m4b", "ogg", "ogx", "wma"]


@pytest.mark.parametrize("ext", MAY_HOLD_VIDEO)
@pytest.mark.parametrize(
    "fmt,kind",
    [
        ({"vcodec": None, "acodec": None}, VIDEO),
        ({"vcodec": None, "acodec": "vorbis"}, VIDEO),
        ({"vcodec": None, "acodec": "none"}, VIDEO),
        ({"acodec": None}, VIDEO),
        ({"vcodec": "theora", "acodec": "vorbis"}, VIDEO),
        ({"vcodec": "none", "acodec": "vorbis"}, AUDIO),
        ({"vcodec": "none", "acodec": None}, AUDIO),
        ({"vcodec": "none", "acodec": "none"}, IMAGE),
    ],
)
@pytest.mark.parametrize(
    "shape",
    [
        lambda fmt: {"id": "i", "title": "t", "formats": [fmt]},
        lambda fmt: {"id": "i", "title": "t", "url": "http://x/f", **fmt},
    ],
    ids=["formats", "url-only"],
)
def test_container_that_can_hold_video_needs_vcodec_none_to_be_audio(
    shape: Any, ext: str, fmt: dict[str, Any], kind: ContentKind
) -> None:
    info = shape({**fmt, "ext": ext})
    assert _classify_entry(info, 1).kind == kind
    assert _classify_single(info) == kind


@pytest.mark.parametrize("ext", sorted(set(MEDIA_EXTENSIONS.audio) - set(MAY_HOLD_VIDEO)))
@pytest.mark.parametrize("acodec", [None, "aac"])
def test_every_other_extension_yt_dlp_lists_as_audio_stays_audio(
    ext: str, acodec: str | None
) -> None:
    assert _classify_single({"formats": [{"vcodec": None, "acodec": acodec, "ext": ext}]}) == AUDIO


KINDS_WEAKEST_FIRST = [
    (IMAGE, STORYBOARD),
    (UNKNOWN, {"vcodec": None, "acodec": None, "ext": "jpg"}),
    (AUDIO, {"vcodec": "none", "acodec": None, "ext": "m4a"}),
    (VIDEO, {"vcodec": None, "acodec": None, "ext": "mp4"}),
]


@pytest.mark.parametrize("weak,strong", list(combinations(KINDS_WEAKEST_FIRST, 2)))
def test_strongest_format_decides_in_any_order(weak: Any, strong: Any) -> None:
    kind, strongest = strong
    other = weak[1]
    assert _classify_single({"formats": [other, strongest]}) == kind
    assert _classify_single({"formats": [strongest, other]}) == kind


def test_a_url_only_result_with_a_thumbnail_is_not_an_image() -> None:
    thumbnails = [{"url": "http://x/img.jpg"}]
    url_only = {"url": "http://x/f.mp4", "ext": "mp4", "thumbnails": thumbnails}
    assert _classify_single(url_only) == VIDEO
    assert _classify_single({"url": "", "thumbnails": thumbnails}) == IMAGE
    assert _classify_single({"url": None, "formats": None, "thumbnails": thumbnails}) == IMAGE


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"formats": []},
        {"formats": None},
        {"url": ""},
        {"url": None},
        {"formats": [], "url": None},
        {"thumbnails": [{"url": "http://x/img.jpg"}], "formats": []},
        {"duration": 3},
        {"url": "http://x/f.mp4"},
        {"url": "http://x/f.mp4", "formats": None},
        {"url": "http://x/f.mp4", "formats": []},
        {"formats": [{"url": "http://x/f.mp4"}]},
        {"formats": [{"url": "http://x/f.mp4"}], "url": None},
        {"formats": [STORYBOARD]},
    ],
)
def test_probe_and_match_filter_agree_on_nothing_to_fetch(extra: dict[str, Any]) -> None:
    entry = {"id": "i", "title": "t", **extra}
    skipped: list[str] = []
    verdict = _skip_items_without_media(skipped)({**entry, "playlist_index": 1}, incomplete=True)
    assert (verdict is not None) is (_media_kind(entry) is None)
    assert bool(skipped) is (_media_kind(entry) is None)


def test_a_duration_does_not_make_audio_a_video() -> None:
    entry = {"id": "i", "duration": 60, "formats": [{"vcodec": "none", "acodec": "mp3"}]}
    item = _classify_entry(entry, 1)
    assert item.kind == AUDIO
    assert item.duration == 60


@pytest.mark.parametrize(
    "extra",
    [{}, {"thumbnails": [{"url": "http://x/img.jpg"}]}, {"duration": 3}],
    ids=["plain", "thumbnails", "duration"],
)
def test_entry_with_nothing_to_fetch_is_an_unsupported_image(extra: dict[str, Any]) -> None:
    item = _classify_entry({"id": "i", "title": "t", "formats": [], **extra}, 1)
    assert item.kind == IMAGE
    assert item.supported is False


def test_probe_never_raises(monkeypatch) -> None:
    from download_media import probe as probe_module

    def explode(opts):
        raise RuntimeError("Login required")

    monkeypatch.setattr(probe_module, "YoutubeDL", explode)
    result = probe_module.probe("http://x")
    assert result.failed is True
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


def test_probe_handles_empty_extraction(fake_site, monkeypatch) -> None:
    from download_media import probe as probe_module

    # ignoreerrors turns a failed extraction into None.
    result = probe_module.probe("https://fake.test/private")
    assert result.failed is True
