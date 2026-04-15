"""Item-selection parser for playlists/carousels.

Accepts user input like ``1-3,5,7-9`` and returns sorted unique indices,
or ``[]`` for "all", or ``None`` on parse error. Pure function — no I/O.
"""

from __future__ import annotations

import re

_PLAYLIST_PATTERNS = [
    re.compile(r"[?&]list="),
    re.compile(r"/playlist", re.IGNORECASE),
    re.compile(r"/sets/", re.IGNORECASE),
    re.compile(r"instagram\.com/(p|reel|tv)/", re.IGNORECASE),
    re.compile(r"tiktok\.com/.*/photo/", re.IGNORECASE),
    re.compile(r"tiktok\.com/@[^/]+/?$", re.IGNORECASE),
    re.compile(r"(twitter|x)\.com/.*/status/", re.IGNORECASE),
]


def is_playlist_url(url: str) -> bool:
    """URL pattern heuristic for "this might be a multi-entry resource"."""
    return any(p.search(url) for p in _PLAYLIST_PATTERNS)


def parse_selection(input_str: str, max_n: int) -> list[int] | None:
    """Parse ``"1-3,5,7-9"`` into sorted unique indices.

    Returns ``[]`` for empty/whitespace input (caller treats as "all"),
    or ``None`` on any parse error.
    """
    cleaned = input_str.replace(" ", "")
    if not cleaned:
        return []
    out: set[int] = set()
    for part in cleaned.split(","):
        if "-" in part:
            try:
                a_str, b_str = part.split("-", 1)
                a, b = int(a_str), int(b_str)
            except ValueError:
                return None
            if a < 1 or b < 1 or a > max_n or b > max_n or a > b:
                return None
            out.update(range(a, b + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                return None
            if n < 1 or n > max_n:
                return None
            out.add(n)
    return sorted(out)


def to_yt_dlp_items(indices: list[int]) -> str:
    """Convert sorted indices to the comma-separated string yt-dlp expects."""
    return ",".join(str(i) for i in indices)
