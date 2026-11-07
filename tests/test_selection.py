"""Tests for carousel selection parsing and URL detection."""

from __future__ import annotations

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import PlaylistEntries

from download_media.selection import is_playlist_url, missing_items, parse_selection


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc&list=PL123", True),
        ("https://www.youtube.com/playlist?list=PL123", True),
        ("https://soundcloud.com/user/sets/my-set", True),
        ("https://www.instagram.com/p/DNlpJ_gxcDL/", True),
        ("https://www.instagram.com/reel/abc/", True),
        ("https://www.tiktok.com/@user/photo/123", True),
        ("https://www.tiktok.com/@user", True),
        ("https://x.com/user/status/123", True),
        ("https://twitter.com/user/status/123", True),
        ("https://youtu.be/abc", False),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://example.com/page", False),
    ],
)
def test_is_playlist_url(url: str, expected: bool) -> None:
    assert is_playlist_url(url) is expected


@pytest.mark.parametrize(
    "input_str,max_n,expected",
    [
        ("", 10, []),
        ("   ", 10, []),
        ("1", 10, [1]),
        ("1,2,4", 10, [1, 2, 4]),
        ("1-3", 10, [1, 2, 3]),
        ("1-3,5,7-9", 10, [1, 2, 3, 5, 7, 8, 9]),
        ("3,1,2", 10, [1, 2, 3]),  # sorted
        ("1,1,2", 10, [1, 2]),  # deduped
        ("1-17", 17, list(range(1, 18))),
        ("  1 , 2 ", 10, [1, 2]),  # whitespace tolerated
    ],
)
def test_parse_selection_valid(input_str: str, max_n: int, expected: list[int]) -> None:
    assert parse_selection(input_str, max_n) == expected


@pytest.mark.parametrize(
    "input_str,max_n",
    [
        ("abc", 10),
        ("0", 10),  # below 1
        ("11", 10),  # above max
        ("1-11", 10),
        ("3-1", 10),  # reversed
        ("1,abc,3", 10),
        ("1--3", 10),
    ],
)
def test_parse_selection_invalid(input_str: str, max_n: int) -> None:
    assert parse_selection(input_str, max_n) is None


def test_non_numeric_range_is_rejected() -> None:
    assert parse_selection("a-b", 5) is None


def test_missing_items_names_segments_that_select_nothing() -> None:
    assert missing_items("2,9,-7,5-9,3-9,-1,0", 4) == ["9", "-7", "5-9", "0"]


def test_missing_items_follows_yt_dlp_slices() -> None:
    assert missing_items("1:", 4) == []
    assert missing_items("-2:,::2,4:1:-1", 4) == []


@pytest.mark.parametrize("spec", ["x", "1,,2", "::0"])
def test_missing_items_never_raises_on_a_bad_spec(spec: str) -> None:
    assert missing_items(spec, 4) == []


SEGMENTS = [
    "1", "4", "5", "0", "-0", "+2", "-1", "-4", "-5", "99999999999",
    "1-2", "3-9", "5-9", "9-5", "4-1", "4:1:-1", "1:4:-1", "10:5:-1", "10:1:-1",
    "::2", "::-1", "::-2", "-5::2", "-2:", "5:", ":", "-", ":0", "0:0", "0-1",
    "-9:-7", "-9:-3", "1-inf", "5-inf", "3:3", "2:-1:-1",
]  # fmt: skip


@pytest.mark.parametrize("count", [1, 4])
@pytest.mark.parametrize("segment", SEGMENTS)
def test_missing_items_agrees_with_what_yt_dlp_downloads(segment: str, count: int) -> None:
    # What yt-dlp's own download path selects, on real entries, against the helper.
    ydl = YoutubeDL({"playlist_items": segment, "quiet": True})
    entries = PlaylistEntries(ydl, {"entries": [{"id": str(i)} for i in range(count)]})
    selects = next(entries.get_requested_items(), None) is not None
    assert missing_items(segment, count) == ([] if selects else [segment])


def test_missing_items_names_each_part_as_typed() -> None:
    assert missing_items("9,2,9", 4) == ["9", "9"]
    assert missing_items("+9,-0", 4) == ["+9", "-0"]
    assert missing_items("1-3,2-4", 4) == []


@pytest.mark.parametrize("spec", ["2, 9", " 2", "inf", "1-2-3", ""])
def test_missing_items_leaves_specs_yt_dlp_rejects_alone(spec: str) -> None:
    assert missing_items(spec, 4) == []
