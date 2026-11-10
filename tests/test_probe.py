"""Tests for the probe module — site detection, classification, auth-error sniff."""

from __future__ import annotations

import os
import sqlite3
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import MEDIA_EXTENSIONS

from download_media.downloader import _skip_items_without_media
from download_media.i18n import _MESSAGES
from download_media.probe import (
    ContentKind,
    _classify_entry,
    _classify_single,
    _media_kind,
    _ProbeYdl,
    _read_result,
    _short_reason,
    detect_site,
    is_auth_error,
    probe,
    site_likely_needs_cookies,
)

from .conftest import ANONYMOUS_LIMIT, GEO_BLOCK, SUBSCRIBERS_ONLY


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/p/abc/", "instagram"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.tiktok.com/@user/video/123", "tiktok"),
        ("https://twitter.com/user/status/123", "x"),
        ("https://x.com/user/status/123", "x"),
        ("https://mobile.twitter.com/user/status/123", "x"),
        # Hosts that merely end in "x.com" aren't X.
        ("https://www.dropbox.com/s/abc/clip.mp4", "generic"),
        ("https://app.box.com/s/abc", "generic"),
        ("https://www.fox.com/watch/x", "generic"),
        ("https://www.netflix.com/title/1", "generic"),
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


@pytest.mark.parametrize(
    "url",
    [
        "https://mobile.x.com/u/status/1",
        "https://www.twitter.com/u/status/1",
        "HTTPS://X.COM/U/STATUS/1",
        "https://x.com:443/u/status/1",
        "https://user@x.com/u/status/1",
        "https://x.com",
        # The whole URL is read, so a query naming X counts (the e2e tests rely on it).
        "https://example.com/?u=x.com",
    ],
)
def test_detect_site_finds_x_wherever_its_whole_name_appears(url: str) -> None:
    assert detect_site(url) == "x"


@pytest.mark.parametrize(
    "url",
    [
        "https://xx.com/a",
        "https://spacex.com/a",
        "https://my_x.com/a",
        "https://x.company.com/a",
        "https://x.comics.org/a",
        "https://twitter.community/a",
        "https://x.co/a",
        "https://fixupx.com/u/status/1",
        "https://vxtwitter.com/u/status/1",
    ],
)
def test_detect_site_ignores_names_that_only_contain_x_com(url: str) -> None:
    assert detect_site(url) == "generic"


@pytest.mark.parametrize(
    "url",
    [
        "https://m.facebook.com/watch/?v=1",
        "https://web.facebook.com/u/videos/1",
        # Used to read "x", since "fox.com" holds "x.com".
        "https://www.facebook.com/u/videos/1?ref=fox.com",
    ],
)
def test_detect_site_still_finds_facebook(url: str) -> None:
    assert detect_site(url) == "facebook"


def test_cookie_hungry_sites() -> None:
    # A login wall on Instagram reaches the retry after the probe: public posts need no cookies.
    assert site_likely_needs_cookies("instagram") is False
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
        ("Rate-limit reached", False),
        ("HTTP 404 Not Found", False),
        ("Connection refused", False),
        ("Unable to extract initial data", False),
    ],
)
def test_is_auth_error(msg: str, expected: bool) -> None:
    assert is_auth_error(msg) is expected


def test_classify_entry_none_is_not_called_an_image() -> None:
    item = _classify_entry(None, 5)
    assert item.kind == ContentKind.UNKNOWN
    assert item.supported is False
    assert "image" not in item.title.lower()


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


# A Content-Type or an <audio> tag gives vcodec 'none' and names no audio codec.
EXT_MAY_HOLD_VIDEO = ["ogg", "wma", "m4b", "asf", "ogx", "mp4", "webm"]
# D5: aiff, alac, ape and wav can't hold a video unless renamed, like the rest here.
EXT_SAYS_AUDIO = ["m4a", "wav", "aiff", "alac", "ape", "mp3", "aac", "flac", "opus", "oga", "mka"]
HLS_VARIANT = {"vcodec": "avc1.64001f", "acodec": "mp4a.40.2", "ext": "mp4"}


def may_hold_video(*formats: dict[str, Any]) -> bool:
    data = {"id": "i", "title": "t", "formats": list(formats)}
    return _read_result("http://x/f", "generic", data, []).may_hold_video


@pytest.mark.parametrize(
    "formats,flagged",
    [
        *(([{"vcodec": "none", "acodec": None, "ext": e}], True) for e in EXT_MAY_HOLD_VIDEO),
        *(([{"vcodec": "none", "acodec": None, "ext": e}], False) for e in EXT_SAYS_AUDIO),
        # An <audio src> with no extension.
        ([{"vcodec": "none", "acodec": None, "ext": "unknown_video"}], True),
        *(
            ([{"vcodec": None, "acodec": None, "ext": e}], False)
            for e in EXT_MAY_HOLD_VIDEO + EXT_SAYS_AUDIO
        ),
        ([{"vcodec": "none", "acodec": "opus", "ext": "webm"}], False),
        ([{"vcodec": "none", "acodec": "none", "ext": "mp4"}], False),
        # An HLS audio rendition next to its video variants: the result is a video.
        ([HLS_VARIANT, {"vcodec": "none", "acodec": None, "ext": "mp4"}], False),
        # D6: any labelled format of an audio result raises the flag.
        (
            [
                {"vcodec": "none", "acodec": "mp3", "ext": "mp3"},
                {"vcodec": "none", "acodec": None, "ext": "webm"},
            ],
            True,
        ),
    ],
)
def test_audio_known_only_by_a_label_may_hold_a_video(
    formats: list[dict[str, Any]], flagged: bool
) -> None:
    assert may_hold_video(*formats) is flagged


@pytest.mark.parametrize("ext", EXT_MAY_HOLD_VIDEO)
def test_a_url_only_result_under_a_label_may_hold_a_video(ext: str) -> None:
    data = {"id": "i", "title": "t", "url": "http://x/f", "vcodec": "none", "ext": ext}
    assert _read_result("http://x/f", "generic", data, []).may_hold_video is True


def test_a_playlist_of_labelled_audio_is_not_flagged() -> None:
    entry = {"id": "e", "formats": [{"vcodec": "none", "acodec": None, "ext": "ogg"}]}
    result = _read_result("http://x/f", "generic", {"id": "p", "entries": [entry]}, [])
    assert result.is_playlist
    assert result.may_hold_video is False


@pytest.mark.parametrize(
    "formats,flagged",
    [
        # yt-dlp always sets ext after processing, but a missing one says nothing either.
        ([{"vcodec": "none"}], True),
        ([{"vcodec": "none", "ext": "OGG"}], True),
        (
            [
                {"vcodec": "none", "ext": "ogg"},
                {"vcodec": "none", "acodec": "vorbis", "ext": "ogg"},
            ],
            True,
        ),
        ([{"vcodec": "none", "acodec": "vorbis", "ext": "ogg"}], False),
        ([{"vcodec": "none", "acodec": "mp4a.40.2", "ext": "mp4"}], False),
        (
            [{"vcodec": "none", "acodec": "aac", "ext": "m4a"}, {"vcodec": "none", "ext": "mp3"}],
            False,
        ),
    ],
)
def test_the_flag_reads_every_format_of_an_audio_result(
    formats: list[dict[str, Any]], flagged: bool
) -> None:
    assert may_hold_video(*formats) is flagged


@pytest.mark.parametrize("ext", EXT_SAYS_AUDIO)
def test_a_url_only_result_whose_extension_says_audio_is_not_flagged(ext: str) -> None:
    data = {"id": "i", "title": "t", "url": "http://x/f", "vcodec": "none", "ext": ext}
    result = _read_result("http://x/f", "generic", data, [])
    assert result.content_kind == AUDIO
    assert result.may_hold_video is False


@pytest.mark.parametrize("ext", ["MP3", "M4A", "WAV", "FLAC", "Opus"])
def test_an_uppercase_extension_that_says_audio_is_not_flagged(ext: str) -> None:
    assert may_hold_video({"vcodec": "none", "acodec": None, "ext": ext}) is False


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
        {"formats": [{"url": "http://x/f.mp4"}]},
        {"formats": [{"url": "http://x/f.mp4"}], "url": None},
        {"formats": [STORYBOARD]},
    ],
)
def test_probe_and_match_filter_agree_on_nothing_to_fetch(extra: dict[str, Any]) -> None:
    # In -a and -l, where images aren't saved.
    entry = {"id": "i", "title": "t", **extra}
    skipped: list[str] = []
    match_filter = _skip_items_without_media(skipped, keep_images=False)
    verdict = match_filter({**entry, "playlist_index": 1}, incomplete=True)
    assert (verdict is not None) is (_media_kind(entry) is None)
    assert bool(skipped) is (_media_kind(entry) is None)


def test_probe_and_match_filter_differ_on_an_empty_formats_list_with_a_url() -> None:
    # yt-dlp reads an empty list as no formats, so nothing can be fetched, and the probe says
    # so. The filter lets it through, for yt-dlp to report as a failed item: aligning the two
    # would change what -y -p counts.
    entry = {"id": "i", "title": "t", "url": "http://x/f.mp4", "formats": []}
    skipped: list[str] = []
    match_filter = _skip_items_without_media(skipped, keep_images=True)
    assert match_filter({**entry, "playlist_index": 1}, incomplete=True) is None
    assert skipped == []
    assert _media_kind(entry) is None
    assert _classify_single(entry) != VIDEO


def test_a_thumbnail_key_alone_makes_a_result_without_formats_an_image() -> None:
    # yt-dlp fills ``thumbnails`` in later, so a raw result may carry just this.
    assert _classify_single({"formats": [], "thumbnail": "http://x/i.jpg"}) == IMAGE


def test_a_duration_does_not_make_audio_a_video() -> None:
    entry = {"id": "i", "duration": 60, "formats": [{"vcodec": "none", "acodec": "mp3"}]}
    item = _classify_entry(entry, 1)
    assert item.kind == AUDIO
    assert item.duration == 60


@pytest.mark.parametrize(
    "extra",
    [{}, {"duration": 3}, {"url": "http://x/f.mp4"}],
    ids=["plain", "duration", "url"],
)
def test_entry_with_nothing_to_fetch_is_an_unsupported_image(extra: dict[str, Any]) -> None:
    item = _classify_entry({"id": "i", "title": "t", "formats": [], **extra}, 1)
    assert item.kind == IMAGE
    assert item.supported is False


@pytest.mark.parametrize(
    "extra",
    [
        {"formats": [], "thumbnails": [{"url": "http://x/img.jpg"}]},
        {"thumbnails": [{"url": "http://x/img.jpg"}]},
        {"formats": [], "thumbnail": "http://x/img.jpg"},
    ],
    ids=["thumbnails", "no-formats-key", "thumbnail"],
)
def test_entry_with_no_formats_and_a_thumbnail_is_a_supported_image(
    extra: dict[str, Any],
) -> None:
    item = _classify_entry({"id": "i", "title": "t", **extra}, 1)
    assert item.kind == IMAGE
    assert item.supported is True


def test_probe_never_raises(monkeypatch) -> None:
    from download_media import probe as probe_module

    def explode(opts):
        raise RuntimeError("Login required")

    monkeypatch.setattr(probe_module, "_ProbeYdl", explode)
    result = probe_module.probe("http://x")
    assert result.failed is True
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


def test_probe_handles_empty_extraction(fake_site, monkeypatch) -> None:
    from download_media import probe as probe_module

    # ignoreerrors turns a failed extraction into None.
    result = probe_module.probe("https://fake.test/private")
    assert result.failed is True


HINT = "Use --cookies-from-browser or --cookies for the authentication. See  https://x.test/faq"
RATE_LIMIT_REASON = (
    "This content isn't available, try again later. The current session has been rate-limited "
    "by YouTube for up to an hour."
)


@pytest.mark.parametrize(
    "message",
    [
        f"[site] x: This video is only available for registered users. {HINT}",
        f"[Instagram] x: This content is only available for registered users who follow this account. {HINT}",
        "[Instagram] x: Instagram sent an empty media response. Check if this post is accessible "
        f"in your browser without being logged-in. If it is not, then use {HINT[4:]}",
        f"[Instagram] x: The webpage request was redirected to the login page. You have exceeded "
        f"the rate-limit for accessing posts anonymously. {HINT}",
        f"[Instagram] x: Requested content is not available, rate-limit reached or login required. {HINT}",
        f"[youtube] x: Sign in to confirm your age. {HINT}",
        f"[youtube] x: Private video. Sign in if you've been granted access to this video. {HINT}",
        "[youtube] x: Join this channel to get access to members-only content like this video.",
        "[TikTok] 1: Fresh cookies (not necessarily logged in) are needed",
        "[generic] s401: Unable to download webpage: HTTP Error 401: Unauthorized "
        "(caused by <HTTPError 401: Unauthorized>)",
    ],
)
def test_cookies_can_help(message: str) -> None:
    assert is_auth_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "[site] x: Account needed. Use --username and --password",
        "[site] x: You need to log in to watch this",
        "[site] x: This video is only available for registered users",
        "[site] x: This video is for premium subscribers",
    ],
    ids=["password-hint", "log-in", "registered", "premium"],
)
def test_one_signal_alone_is_enough_to_offer_cookies(message: str) -> None:
    assert is_auth_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "[Instagram] x: There is no video in this post",
        "[youtube] x: This content isn't available, try again later. The current session has "
        "been rate-limited by YouTube for up to an hour.",
        "[bilibili] 1: You have exceeded the rate limit. Try again later",
        "[generic] s429: Unable to download webpage: HTTP Error 429: Too Many Requests",
        "This video is not available from your location due to geo restriction\n"
        "You might want to use a VPN or a proxy server (with --proxy) to workaround.",
        "Private to your region\nYou might want to use a VPN or a proxy server.",
        "[generic] s403: Unable to download webpage: HTTP Error 403: Forbidden",
        "[generic] s404: Unable to download webpage: HTTP Error 404: Not Found",
        "Unsupported URL: https://example.com/page",
        "This video is DRM protected",
        'could not find chrome cookies database in "/tmp/home/.config/google-chrome"',
        # The site's words only: an id or a path says nothing about the content.
        "Unsupported URL: https://example.com/private/page",
        "[generic] login: Unable to download webpage: HTTP Error 404: Not Found",
    ],
)
def test_cookies_cannot_help(message: str) -> None:
    assert is_auth_error(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "[site] x: The login rate-limit was exceeded",
        "[site] x: The login rate limit was exceeded",
        "[site] x: Too many requests to the login service",
        "[site] x: The login service is down, try again later",
    ],
    ids=["hyphen", "space", "requests", "later"],
)
def test_a_limit_outranks_the_word_login(message: str) -> None:
    assert is_auth_error(message) is False


@pytest.mark.parametrize(
    "message,shown",
    [
        (
            "[Instagram] x: Instagram sent an empty media response. Check if this post is "
            "accessible in your browser without being logged-in. If it is not, then use "
            "--cookies-from-browser or --cookies for the authentication.",
            "Instagram sent an empty media response. Check if this post is accessible in your "
            "browser without being logged-in.",
        ),
        (
            "[generic] s401: Unable to download webpage: HTTP Error 401: Unauthorized "
            "(caused by <HTTPError 401: Unauthorized>)",
            "Unable to download webpage: HTTP Error 401: Unauthorized",
        ),
        (
            "[fakesite] bug: Unable to extract video data; please report this issue on  "
            "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the template",
            "Unable to extract video data",
        ),
        (
            "[fakesite] bug: Unable to extract video data, please report this issue on  x",
            "Unable to extract video data",
        ),
        (
            "[fakesite] i1: Requested format is not available. Use --list-formats for a list",
            "Requested format is not available.",
        ),
        (
            "The session is limited. It is recommended to use `-t sleep` to add a delay.",
            "The session is limited.",
        ),
        (
            "This video is not available from your location due to geo restriction\n"
            "This video is available in United States.\n"
            "You might want to use a VPN or a proxy server (with --proxy) to workaround.",
            "This video is not available from your location due to geo restriction",
        ),
    ],
)
def test_short_reason_keeps_the_sites_words(message: str, shown: str) -> None:
    assert _short_reason(message) == shown


@pytest.mark.parametrize(
    "message,shown",
    [
        ("", ""),
        ("[x] y: ", ""),
        ("[x] y: first\nsecond\nthird", "first"),
        ("[x] y: \nLogin required\nsecond", "Login required"),
        ("\nLogin required", "Login required"),
        ("[x] y: \tspread\t\tout   text  ", "spread out text"),
        ("[x] y: Gone.\nUse --cookies for it", "Gone."),
        ("[x] y: Failed; Please report nothing", "Failed; Please report nothing"),
        ("[x] y: Bad range. Offset -1 is too low", "Bad range. Offset -1 is too low"),
        ("[x] y: {} {0} {name} 100%", "{} {0} {name} 100%"),
    ],
    ids=[
        "empty",
        "only-a-prefix",
        "lines",
        "blank-first-line",
        "leading-newline",
        "spaces",
        "flag-line",
        "case",
        "minus",
        "braces",
    ],
)
def test_short_reason_on_what_yt_dlp_can_hand_over(message: str, shown: str) -> None:
    assert _short_reason(message) == shown


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("key", ["auth_needed_retry", "probe_failed", "probe_partial"])
def test_both_languages_frame_the_reason_as_it_comes(language: str, key: str) -> None:
    reason = _short_reason("[x] y: {} {0} braces and a 100% sign; please report this")
    assert reason == "{} {0} braces and a 100% sign"
    assert _MESSAGES[language][key].format(reason).endswith(f": {reason}")


@pytest.mark.parametrize(
    "message",
    [
        "[generic] private clip: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] login page: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] tag:private: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] 'private-video' is not a valid URL. Set --default-search to search YouTube",
        "[site] x: Unable to download webpage: Failed to resolve 'login.example.com'",
    ],
    ids=["space", "login", "colon", "not-a-url", "host"],
)
def test_words_from_the_id_or_the_address_ask_for_nothing(message: str) -> None:
    assert is_auth_error(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "[generic] --cookies: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] my clip: Failed to download MPD manifest: HTTP Error 404: Not Found",
        "[generic] private clip: Failed to parse JSON",
        "[site] tag:private: This post is gone",
        "[generic] 'private video' is not a valid URL",
        "[generic] '--cookies' is not a valid URL",
        "[generic] 'private-video' is not a valid URL. Set --default-search \"ytsearch\" (or run  "
        'yt-dlp "ytsearch:private-video" ) to search YouTube',
        "Unsupported URL: ftp://media-server/private/page",
        "[generic] x: Unable to download webpage: "
        "HTTPSConnectionPool(host='login.example.com', port=443): Read timed out",
        "[generic] x: Failed to perform, curl: (6) Could not resolve host: private.example.com",
    ],
    ids=[
        "flag-in-the-id",
        "manifest",
        "failed-to",
        "id-with-a-colon",
        "not-a-url-with-a-space",
        "flag-as-a-url",
        "not-a-url-at-the-floor",
        "other-scheme",
        "quoted-host",
        "bare-host",
    ],
)
def test_only_the_sites_own_words_can_ask_for_cookies(message: str) -> None:
    assert is_auth_error(message) is False


@pytest.mark.parametrize("status", [404, 410, 500, 503])
@pytest.mark.parametrize(
    "shape",
    [
        "[generic] tag: private: Unable to download webpage: HTTP Error {}: Not Found",
        "[site] x: Unable to download webpage: HTTP Error {}: Login Required",
        # No extractor words this today: those that read a 404 as a login raise without the status.
        "[site] x: HTTP Error {}: Not Found. " + HINT,
    ],
    ids=["id-the-prefix-cannot-read", "server-reason-phrase", "login-hint"],
)
def test_a_status_that_is_no_login_outranks_the_rest_of_its_line(shape: str, status: int) -> None:
    assert is_auth_error(shape.format(status)) is False


@pytest.mark.parametrize(
    "message",
    [
        "[generic] login page: Unable to download webpage: HTTP Error 401: Unauthorized",
        "[generic] tag:x: Unable to download webpage: HTTP Error 401: Unauthorized",
        "[generic] Unable to download webpage: HTTP Error 401: Unauthorized",
        f"[Instagram] my clip: {ANONYMOUS_LIMIT}. {HINT}",
        f"[site] x: HTTP Error 403: Forbidden. {HINT}",
        "[site] tag:x: Private video",
        "[site] a: HTTP Error 404: Not Found\n[site] b: Login required",
        "[site] x: Login required (caused by <HTTPError 404: Not Found>)",
    ],
    ids=[
        "id-with-space",
        "id-with-colon",
        "no-id",
        "d4",
        "hint-on-403",
        "private",
        "one-of-two",
        "404-only-as-the-cause",
    ],
)
def test_what_the_site_said_still_asks_past_the_id(message: str) -> None:
    assert is_auth_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "[generic] tag:x: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] x: Unable to download webpage: HTTP Error 404: Not Found",
        "[generic] Unable to download webpage: HTTP Error 404: Not Found",
    ],
    ids=["colon", "plain", "floor-without-an-id"],
)
def test_short_reason_drops_an_id_and_nothing_else(message: str) -> None:
    assert _short_reason(message) == "Unable to download webpage: HTTP Error 404: Not Found"


@pytest.mark.parametrize(
    "ending",
    [
        "HTTP Error 400: Bad Request",
        "HTTP Error 403: Forbidden",
        "HTTP Error 451: Unavailable For Legal Reasons",
        "<urlopen error [Errno 111] Connection refused>",
    ],
    ids=["400", "403", "451", "refused"],
)
def test_the_end_of_an_id_with_a_colon_asks_for_nothing(ending: str) -> None:
    assert is_auth_error(f"[generic] tag: private: Unable to download webpage: {ending}") is False


def test_short_reason_drops_an_id_with_a_space() -> None:
    message = "[generic] my clip: Unable to download webpage: HTTP Error 404: Not Found"
    assert _short_reason(message) == "Unable to download webpage: HTTP Error 404: Not Found"


def test_short_reason_drops_the_label_of_a_raised_error() -> None:
    assert _short_reason("ERROR: [x] y: This post is gone") == "This post is gone"


def test_short_reason_keeps_a_message_with_a_colon_and_no_id() -> None:
    assert _short_reason("[site] Video gone: removed by the owner") == (
        "Video gone: removed by the owner"
    )


def test_short_reason_keeps_something_when_a_flag_opens_the_message() -> None:
    assert _short_reason("[x] y: Use --cookies to log in") == "Use --cookies to log in"


def test_short_reason_is_bounded() -> None:
    reason = _short_reason("word " * 80)
    assert len(reason) <= 200
    assert reason.endswith("...")


def test_probe_reports_the_real_reason(fake_site: Any) -> None:
    result = probe("https://fake.test/needs-cookies")
    assert result.failed is True
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


@pytest.mark.parametrize(
    "page,reason",
    [
        ("age-gate", "Sign in to confirm your age."),
        ("paid-post", f"{SUBSCRIBERS_ONLY}."),
        ("rate-limited", RATE_LIMIT_REASON),
        ("geo-metadata", GEO_BLOCK),
        ("drm", "This video is DRM protected"),
        ("no-video-post", "There is no video in this post"),
    ],
)
def test_a_failure_with_metadata_never_reads_as_an_image(
    fake_site: Any, page: str, reason: str
) -> None:
    # These only stay failures if the probe doesn't set ignore_no_formats_error.
    result = probe(f"https://fake.test/{page}")
    assert result.failed is True
    assert result.content_kind != IMAGE
    assert result.error_message == reason


@pytest.mark.parametrize(
    "page,reason",
    [
        ("age-gate", "Sign in to confirm your age."),
        ("paid-post", f"{SUBSCRIBERS_ONLY}."),
        ("login-hint", "This video is only available for registered users."),
        ("anonymous-limit", f"{ANONYMOUS_LIMIT}."),
    ],
)
def test_probe_asks_for_cookies_on_a_login_error(fake_site: Any, page: str, reason: str) -> None:
    # The reason proves the page's own raiser fired: "This post is private" would ask too.
    result = probe(f"https://fake.test/{page}")
    assert result.needs_cookies is True
    assert result.error_message == reason


@pytest.mark.parametrize(
    "page", ["rate-limited", "geo", "geo-metadata", "drm", "no-video-post", "extractor-bug"]
)
def test_probe_does_not_ask_for_cookies_when_they_cannot_help(fake_site: Any, page: str) -> None:
    result = probe(f"https://fake.test/{page}")
    assert result.failed is True
    assert result.error_message
    assert "private" not in result.error_message
    assert result.needs_cookies is False


def test_probe_drops_the_bug_report_request(fake_site: Any) -> None:
    result = probe("https://fake.test/extractor-bug")
    assert result.error_message == "Unable to extract video data"


@pytest.mark.parametrize(
    "page,kinds,supported",
    [
        ("carousel", [IMAGE, VIDEO, IMAGE, VIDEO], [False, True, False, True]),
        ("ig-carousel", [IMAGE, VIDEO, VIDEO, IMAGE], [True, True, True, True]),
        ("only-images", [IMAGE, IMAGE], [False, False]),
        ("url-only-carousel", [VIDEO, VIDEO], [True, True]),
    ],
)
def test_a_healthy_carousel_keeps_its_items(
    fake_site: Any, page: str, kinds: list[Any], supported: list[bool]
) -> None:
    # Images with a thumbnail are saved through it; the carousel page's have none.
    result = probe(f"https://fake.test/{page}")
    assert result.content_kind == ContentKind.PLAYLIST
    assert [item.kind for item in result.items] == kinds
    assert [item.supported for item in result.items] == supported
    assert not result.failed
    assert not result.error_message
    assert result.needs_cookies is False


def test_an_empty_playlist_has_no_error_to_report(fake_site: Any) -> None:
    result = probe("https://fake.test/empty")
    assert result.content_kind == ContentKind.UNKNOWN
    assert result.items == []
    assert not result.failed
    assert not result.error_message
    assert result.needs_cookies is False


def test_a_gated_entry_keeps_the_rest_of_the_carousel(fake_site: Any) -> None:
    result = probe("https://fake.test/partial-gated")
    assert result.failed is False
    assert result.needs_cookies is True
    assert result.error_message == "Login required"
    assert [item.kind for item in result.items] == [VIDEO, IMAGE, ContentKind.UNKNOWN]


def test_the_login_error_outranks_an_earlier_failure(fake_site: Any) -> None:
    # A rate limit on one entry mustn't hide the reason cookies are offered for.
    result = probe("https://fake.test/partial-both")
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


def test_a_carousel_with_every_entry_gated_is_a_failure(fake_site: Any) -> None:
    result = probe("https://fake.test/all-gated")
    assert result.failed is True
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


def test_a_text_duration_on_an_image_entry_cannot_break_the_probe(fake_site: Any) -> None:
    result = probe("https://fake.test/text-duration-carousel")
    assert [item.kind for item in result.items] == [IMAGE, VIDEO]
    assert result.items[0].duration == 12


def test_odd_types_on_an_image_entry_cannot_break_the_probe(fake_site: Any) -> None:
    result = probe("https://fake.test/odd-types-carousel")
    assert [item.kind for item in result.items] == [IMAGE, VIDEO]
    assert result.items[0].title == "123"
    assert result.items[0].duration is None


def test_what_the_classifier_cannot_read_is_a_failure_and_not_a_crash(
    fake_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from download_media import probe as probe_module

    def unreadable(entry: Any, idx: int) -> None:
        raise TypeError("a value nobody expected")

    monkeypatch.setattr(probe_module, "_classify_entry", unreadable)
    result = probe("https://fake.test/carousel")
    assert result.failed is True
    assert result.needs_cookies is False
    assert result.error_message == "a value nobody expected"


@pytest.mark.parametrize(
    "raw,seconds",
    [
        ("12", 12),
        (12.5, 12.5),
        (0, None),
        (-1, None),
        ("soon", None),
        ("inf", None),
        ("nan", None),
        ([], None),
        ({"s": 1}, None),
    ],
    ids=["text", "float", "zero", "negative", "words", "inf", "nan", "list", "dict"],
)
def test_a_raw_duration_reads_as_seconds_or_nothing(raw: Any, seconds: float | None) -> None:
    assert _classify_entry({"url": "http://x/f.mp4", "duration": raw}, 1).duration == seconds


def test_a_text_duration_on_a_lone_image_is_still_an_image(fake_site: Any) -> None:
    result = probe("https://fake.test/text-duration-image")
    assert result.content_kind == IMAGE
    assert result.duration == 12


@pytest.mark.parametrize("page", ["live-video", "url-video"])
def test_a_result_with_something_to_fetch_is_never_taken_for_an_image(
    fake_site: Any, page: str
) -> None:
    result = probe(f"https://fake.test/{page}")
    assert result.content_kind == VIDEO
    assert result.failed is False


def test_a_stream_that_has_not_started_says_so(fake_site: Any) -> None:
    result = probe("https://fake.test/upcoming")
    assert result.failed is True
    assert result.needs_cookies is False
    assert result.error_message == "This live event will begin in 5 minutes"


def test_a_format_without_a_url_stays_a_failure(fake_site: Any) -> None:
    result = probe("https://fake.test/no-url-format")
    assert result.failed is True
    assert result.error_message == "No video formats found!"


@pytest.mark.parametrize(
    "page,reason",
    [
        ("partial-drm", "This video is DRM protected"),
        ("partial-no-url", "No video formats found!"),
    ],
)
def test_an_entry_that_really_failed_is_not_handed_back(
    fake_site: Any, page: str, reason: str
) -> None:
    result = probe(f"https://fake.test/{page}")
    assert result.failed is False
    assert result.needs_cookies is False
    assert result.error_message == reason
    assert [item.kind for item in result.items] == [VIDEO, ContentKind.UNKNOWN]
    assert [item.supported for item in result.items] == [True, False]


def test_failures_are_read_at_their_own_position(fake_site: Any) -> None:
    result = probe("https://fake.test/partial-first")
    assert [item.kind for item in result.items] == [ContentKind.UNKNOWN, VIDEO, VIDEO]
    assert result.items[0].title == "(item #1)"


def test_a_redirect_to_a_gated_page_asks_for_cookies(fake_site: Any) -> None:
    result = probe("https://fake.test/transparent-gated")
    assert result.failed is True
    assert result.needs_cookies is True


def test_a_list_that_fails_while_it_is_read_is_a_failure(fake_site: Any) -> None:
    # YouTube tabs and profiles build their entries lazily, so nothing fails at extraction.
    result = probe("https://fake.test/lazy-gated")
    assert result.failed is True
    assert result.needs_cookies is True
    assert "registered users" in (result.error_message or "")


def test_a_list_that_fails_halfway_keeps_what_it_read(fake_site: Any) -> None:
    result = probe("https://fake.test/lazy-half")
    assert result.failed is False
    assert result.needs_cookies is True
    assert [item.kind for item in result.items] == [VIDEO, ContentKind.UNKNOWN]


def test_a_gated_entry_inside_a_nested_list_counts(fake_site: Any) -> None:
    result = probe("https://fake.test/nested-gated")
    assert result.needs_cookies is True
    assert result.error_message == "Login required"


def test_an_error_with_no_words_reports_no_reason(fake_site: Any) -> None:
    result = probe("https://fake.test/blank-error")
    assert result.failed is True
    assert result.needs_cookies is False
    assert not result.error_message


def test_only_the_first_line_of_an_error_is_the_reason(fake_site: Any) -> None:
    assert probe("https://fake.test/two-line-error").error_message == "This post is gone"


def test_a_mistyped_url_with_a_login_word_asks_for_nothing() -> None:
    result = probe("private-video")  # The generic extractor refuses it before any request.
    assert result.failed is True
    assert result.needs_cookies is False


def test_a_browser_that_is_not_installed_is_a_cookie_failure(media_url: str) -> None:
    # isolated_env's home holds no browser at all.
    result = probe(f"{media_url}/poster.html", "chrome")
    assert result.failed is True
    assert result.cookie_failure is not None
    assert result.cookie_failure.missing is True


def test_a_keyring_error_is_a_cookie_failure(
    media_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "bin" / "kwallet-query"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent))
    profile = Path(os.environ["XDG_CONFIG_HOME"]) / "google-chrome" / "Default"
    profile.mkdir(parents=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '20')")
    con.execute(
        "CREATE TABLE cookies (host_key BLOB, name BLOB, value BLOB, encrypted_value BLOB,"
        " path BLOB, expires_utc INTEGER, is_secure INTEGER)"
    )
    con.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, 0, 0)",
        (b"127.0.0.1", b"s", b"", b"v11" + b"\0" * 16, b"/"),
    )
    con.commit()
    con.close()
    result = probe(f"{media_url}/poster.html", "chrome+kwallet6")
    assert result.failed is True
    assert result.cookie_failure is not None
    assert result.cookie_failure.missing is False
    assert "kwallet-query failed" in result.cookie_failure.reason
    assert result.error_message is not None
    assert "kwallet-query failed" in result.error_message


def test_cookies_that_decrypt_to_nothing_are_a_cookie_failure(media_url: str) -> None:
    # One v11 cookie, and basictext asks no keyring for its key: yt-dlp logs no error.
    profile = Path(os.environ["XDG_CONFIG_HOME"]) / "google-chrome" / "Default"
    profile.mkdir(parents=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '20')")
    con.execute(
        "CREATE TABLE cookies (host_key BLOB, name BLOB, value BLOB, encrypted_value BLOB,"
        " path BLOB, expires_utc INTEGER, is_secure INTEGER)"
    )
    con.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, 0, 0)",
        (b"127.0.0.1", b"s", b"", b"v11" + b"\0" * 16, b"/"),
    )
    con.commit()
    con.close()
    result = probe(f"{media_url}/poster.html", "chrome+basictext")
    assert result.failed is True
    assert result.cookie_failure is not None
    assert result.cookie_failure.undecrypted is True
    assert result.cookie_failure.missing is False


def test_what_yt_dlp_reported_outranks_the_exception_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def extract(self: Any, _url: str, **_kwargs: Any) -> Any:
        self.report_error("the real cause")
        raise RuntimeError("a generic error")

    monkeypatch.setattr(_ProbeYdl, "extract_info", extract)
    result = probe("http://127.0.0.1:9/page")
    assert result.failed is True
    assert result.error_message == "the real cause"
    assert result.cookie_failure is None


def test_a_probe_without_a_browser_has_no_cookie_failure(media_url: str) -> None:
    result = probe(f"{media_url}/poster.html")
    assert result.failed is False
    assert result.cookie_failure is None
