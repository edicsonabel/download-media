"""URL probe: detect site, content kind, items, and cookie requirements.

This is the single source of truth for "what is at this URL?". The wizard
calls :func:`probe` once, looks at the structured result, and tailors its
questions accordingly — instead of asking quality/format up-front and
discovering at download time that the user picked an image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yt_dlp import YoutubeDL


class ContentKind(str, Enum):
    SINGLE_VIDEO = "video"
    SINGLE_AUDIO = "audio"
    SINGLE_IMAGE = "image"
    PLAYLIST = "playlist"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProbeItem:
    """One entry inside a playlist or carousel."""

    index: int
    kind: ContentKind
    supported: bool  # whether yt-dlp can actually download this item
    title: str
    duration: float | None = None


@dataclass(slots=True)
class Probe:
    """Result of inspecting a URL.

    Always returned (never raises). Check :attr:`failed` and
    :attr:`needs_cookies` to decide what to do next.
    """

    url: str
    site: str  # "instagram", "youtube", "x", "tiktok", ... or "generic"
    content_kind: ContentKind
    title: str | None = None
    uploader: str | None = None
    duration: float | None = None
    filesize: int | None = None
    items: list[ProbeItem] = field(default_factory=list)
    needs_cookies: bool = False
    failed: bool = False
    error_message: str | None = None

    @property
    def is_playlist(self) -> bool:
        return self.content_kind == ContentKind.PLAYLIST

    @property
    def supported_video_count(self) -> int:
        return sum(1 for i in self.items if i.kind == ContentKind.SINGLE_VIDEO and i.supported)

    @property
    def supported_audio_count(self) -> int:
        return sum(1 for i in self.items if i.kind == ContentKind.SINGLE_AUDIO and i.supported)

    @property
    def unsupported_count(self) -> int:
        return sum(1 for i in self.items if not i.supported)


# ---------------------------------------------------------------------------
# Site detection
# ---------------------------------------------------------------------------
_SITE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(r"instagram\.com", re.IGNORECASE)),
    ("youtube",   re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)),
    ("tiktok",    re.compile(r"tiktok\.com", re.IGNORECASE)),
    ("x",         re.compile(r"(twitter\.com|x\.com)", re.IGNORECASE)),
    ("facebook",  re.compile(r"facebook\.com|fb\.watch", re.IGNORECASE)),
    ("vimeo",     re.compile(r"vimeo\.com", re.IGNORECASE)),
    ("twitch",    re.compile(r"twitch\.tv", re.IGNORECASE)),
    ("soundcloud", re.compile(r"soundcloud\.com", re.IGNORECASE)),
    ("reddit",    re.compile(r"reddit\.com", re.IGNORECASE)),
]

# Sites where authenticated cookies are usually required even to inspect
# public content. We pre-emptively ask the user for cookies before probing
# these to avoid the round-trip "probe → fail → ask cookies → probe again".
_COOKIE_HUNGRY_SITES: frozenset[str] = frozenset({"instagram", "x", "facebook"})


def detect_site(url: str) -> str:
    for name, pattern in _SITE_PATTERNS:
        if pattern.search(url):
            return name
    return "generic"


def site_likely_needs_cookies(site: str) -> bool:
    return site in _COOKIE_HUNGRY_SITES


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
class _SilentLogger:
    """Sink for yt-dlp logging during probe."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


_AUTH_ERROR_HINTS = (
    "sign in to confirm",
    "login required",
    "private",
    "rate-limit",
    "requested content is not available",
    "login or signup",
    "this video is unavailable",
    "members-only",
    "premium content",
)


def _is_auth_error(message: str) -> bool:
    msg = message.lower()
    return any(hint in msg for hint in _AUTH_ERROR_HINTS)


def _classify_entry(entry: dict[str, Any] | None, idx: int) -> ProbeItem:
    """Classify one playlist entry as video / audio / image."""
    if entry is None:
        # yt-dlp returns None when it can't extract an entry. For Instagram
        # carousels this happens for image items — yt-dlp doesn't support
        # downloading IG images at all.
        return ProbeItem(
            index=idx,
            kind=ContentKind.SINGLE_IMAGE,
            supported=False,
            title=f"(image #{idx})",
        )
    duration = entry.get("duration") or 0
    formats = entry.get("formats") or []
    has_video = any(
        (f.get("vcodec") or "none") not in ("none", "") for f in formats
    ) or duration > 0
    has_audio = any((f.get("acodec") or "none") not in ("none", "") for f in formats)

    if has_video:
        kind = ContentKind.SINGLE_VIDEO
    elif has_audio:
        kind = ContentKind.SINGLE_AUDIO
    else:
        kind = ContentKind.SINGLE_IMAGE

    title = str(entry.get("title") or entry.get("id") or f"Item {idx}")
    title = title.replace("\n", " ").replace("|", "/")[:80]
    return ProbeItem(
        index=idx,
        kind=kind,
        supported=bool(formats),
        title=title,
        duration=duration if duration > 0 else None,
    )


def _classify_single(data: dict[str, Any]) -> ContentKind:
    formats = data.get("formats") or []
    has_video = any((f.get("vcodec") or "none") not in ("none", "") for f in formats)
    has_audio = any((f.get("acodec") or "none") not in ("none", "") for f in formats)
    if has_video:
        return ContentKind.SINGLE_VIDEO
    if has_audio:
        return ContentKind.SINGLE_AUDIO
    if data.get("thumbnails"):
        return ContentKind.SINGLE_IMAGE
    return ContentKind.UNKNOWN


def probe(url: str, cookies_browser: str | None = None) -> Probe:
    """Inspect ``url`` and return a structured :class:`Probe`. Never raises."""
    site = detect_site(url)

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "logger": _SilentLogger(),
    }
    if cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False, process=True)
    except Exception as exc:
        msg = str(exc)
        return Probe(
            url=url,
            site=site,
            content_kind=ContentKind.UNKNOWN,
            failed=True,
            error_message=msg,
            needs_cookies=_is_auth_error(msg),
        )

    if not isinstance(data, dict):
        return Probe(
            url=url, site=site, content_kind=ContentKind.UNKNOWN, failed=True
        )

    entries = data.get("entries")
    if entries:
        items = [_classify_entry(e, i) for i, e in enumerate(entries, 1)]
        return Probe(
            url=url,
            site=site,
            content_kind=ContentKind.PLAYLIST,
            title=data.get("title") or data.get("id"),
            uploader=data.get("uploader") or data.get("channel"),
            items=items,
        )

    kind = _classify_single(data)
    return Probe(
        url=url,
        site=site,
        content_kind=kind,
        title=data.get("title") or data.get("id"),
        uploader=data.get("uploader") or data.get("channel"),
        duration=data.get("duration"),
        filesize=data.get("filesize_approx") or data.get("filesize"),
    )
