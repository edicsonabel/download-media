"""Tests for carousel selection parsing and URL detection."""

from __future__ import annotations

import pytest

from download_media.selection import is_playlist_url, parse_selection


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
