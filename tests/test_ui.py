"""Tests for UI helpers — formatting only (color output covered manually)."""

from __future__ import annotations

import pytest

from download_media.ui import fmt_bytes, fmt_duration


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, "N/A"),
        (0, "N/A"),
        (-5, "N/A"),
        (45, "0:45"),
        (90, "1:30"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (90061, "25:01:01"),
        (45.7, "0:45"),
    ],
)
def test_fmt_duration(seconds: float | None, expected: str) -> None:
    assert fmt_duration(seconds) == expected


@pytest.mark.parametrize(
    "b,expected",
    [
        (None, "N/A"),
        (0, "N/A"),
        (-1, "N/A"),
        (500, "500 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (int(1.5 * 1024 * 1024), "1.5 MB"),
        (1024**3, "1.00 GB"),
        (int(2.5 * 1024**3), "2.50 GB"),
    ],
)
def test_fmt_bytes(b: int | None, expected: str) -> None:
    assert fmt_bytes(b) == expected
