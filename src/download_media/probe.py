"""URL probe: detect site, content kind, items, and cookie requirements.

This is the single source of truth for "what is at this URL?". The wizard
calls :func:`probe` once, looks at the structured result, and tailors its
questions accordingly — instead of asking quality/format up-front and
discovering at download time that the user picked an image.
"""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yt_dlp.utils import MEDIA_EXTENSIONS, float_or_none, remove_terminal_sequences

from .cookies import ydl_cookies
from .downloader import _Ydl


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
    may_hold_video: bool = False  # Audio by a label only: the file may hold a video.
    error_message: str | None = None

    @property
    def is_playlist(self) -> bool:
        return self.content_kind == ContentKind.PLAYLIST

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


_LOGIN_HINTS = ("--cookies", "--username")  # yt-dlp's own hint, from raise_login_required.
_NOT_AUTH_ERRORS = (
    "rate-limit",
    "rate limit",
    "too many requests",
    "try again later",
    "use a vpn",  # yt-dlp adds this to every geo error.
)
_AUTH_ERROR_HINTS = (
    "sign in",
    "log in",
    "login",
    "logged in",
    "private",
    "registered users",
    "members-only",
    "premium",
    "http error 401",
)
# The generic extractor's id is the URL's last segment, unquoted, so it can hold spaces or
# ": ", which is why yt-dlp's own opening is looked for first. At the floor it has no id at
# all, and "Unable to download webpage: " is the site's sentence.
# A DownloadError adds "ERROR: ", and under -p it holds one error per line.
_SITE_PREFIX = re.compile(
    r"^(?:ERROR: )?(?:\[[^\]]+\] (?:.*?: (?=Unable to |Failed to )|\S+: )?)?", re.MULTILINE
)
# These speak of the address, so anything else in them came from it, even a flag-like id.
_NEVER_A_LOGIN = re.compile(r"HTTP Error (?:404|410|5\d\d)\b|is not a valid URL", re.IGNORECASE)
_ADDRESS = re.compile(r"\S+://\S+|[\w-]+(?:\.[\w-]+)+\S*")
_NAMES_A_FLAG = re.compile(r"(?:^|[\s`])--?[a-z]")
_REASON_TAILS = ("; please report", ", please report", " (caused by")
_REASON_LENGTH = 200


def _site_words(message: str) -> str:
    """What the site said: an id or an address can say "private" and mean nothing by it."""
    lines = _SITE_PREFIX.sub("", remove_terminal_sequences(message)).splitlines()
    kept = [line for line in lines if not _NEVER_A_LOGIN.search(line)]
    return _ADDRESS.sub("", "\n".join(kept)).lower()


def is_auth_error(message: str) -> bool:
    """Whether browser cookies are the user's next move after this yt-dlp error."""
    words = _site_words(message)
    if any(hint in words for hint in _LOGIN_HINTS):
        return True
    if any(hint in words for hint in _NOT_AUTH_ERRORS):
        return False
    return any(hint in words for hint in _AUTH_ERROR_HINTS)


def _short_reason(message: str) -> str:
    """The site's words, without what only means something to a yt-dlp user.

    Its hints name its own flags: ``--cookies`` is a file there and a browser here.
    """
    text = _SITE_PREFIX.sub("", message).strip().split("\n")[0]
    for tail in _REASON_TAILS:
        text = text.partition(tail)[0]
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _NAMES_A_FLAG.search(sentence):
            break
        kept.append(sentence)
    return textwrap.shorten(" ".join(kept) or text, _REASON_LENGTH, placeholder="...")


# yt-dlp lists these as audio, yet they can hold a video it would keep. A wrong
# "audio" converts and deletes that video; a wrong "video" only keeps the raw file.
# .m4a isn't one: yt-dlp's metadata step already strips video from it.
_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO = frozenset({"asf", "m4b", "ogg", "ogx", "wma"})


def _ext_says_audio(ext: Any) -> bool:
    return ext in MEDIA_EXTENSIONS.audio and ext not in _AUDIO_LISTED_BUT_MAY_HOLD_VIDEO


def _audio_by_label_only(fmt: dict[str, Any]) -> bool:
    # A Content-Type or an <audio> tag says 'none' and names no audio codec:
    # nothing looked at the stream, and the container can hold a video.
    ext = fmt.get("ext")
    ext = ext.lower() if isinstance(ext, str) else ext  # yt-dlp keeps the URL's case.
    return fmt.get("vcodec") == "none" and fmt.get("acodec") is None and not _ext_says_audio(ext)


def _format_kind(fmt: dict[str, Any]) -> ContentKind:
    """Read a format as yt-dlp does: ``'none'`` is absent, ``None`` is unknown."""
    vcodec = fmt.get("vcodec")
    if vcodec == "none":
        no_audio = fmt.get("acodec") == "none"
        return ContentKind.SINGLE_IMAGE if no_audio else ContentKind.SINGLE_AUDIO
    if vcodec:
        return ContentKind.SINGLE_VIDEO
    # acodec can't decide here: yt-dlp may name it and still have video (or
    # fill it in from the extension), while vcodec stays unknown.
    ext = fmt.get("ext")
    if _ext_says_audio(ext):
        return ContentKind.SINGLE_AUDIO
    if ext in MEDIA_EXTENSIONS.thumbnails:
        return ContentKind.UNKNOWN  # Offered, never refused: a direct .jpg downloads.
    return ContentKind.SINGLE_VIDEO


_KINDS_BY_STRENGTH = (
    ContentKind.SINGLE_VIDEO,
    ContentKind.SINGLE_AUDIO,
    ContentKind.UNKNOWN,
    ContentKind.SINGLE_IMAGE,
)


def _formats(info: dict[str, Any]) -> list[dict[str, Any]]:
    # yt-dlp keeps no ``formats`` on a result that only has a ``url``.
    formats = info.get("formats")
    if formats is None:
        return [info] if info.get("url") else []
    return list(formats)


def _media_kind(info: dict[str, Any]) -> ContentKind | None:
    """What ``info`` holds, or None if there is nothing to fetch."""
    kinds = {_format_kind(f) for f in _formats(info)}
    return next((kind for kind in _KINDS_BY_STRENGTH if kind in kinds), None)


def _seconds(info: dict[str, Any]) -> float | None:
    # What the override hands back hasn't been through yt-dlp's numeric sanitizing.
    seconds = float_or_none(info.get("duration")) or 0
    return seconds if 0 < seconds < math.inf else None


def _classify_entry(entry: dict[str, Any] | None, idx: int) -> ProbeItem:
    """Classify one playlist entry as video / audio / image."""
    if entry is None:
        # A failure, or a hole the extractor left. Images arrive as dicts.
        return ProbeItem(
            index=idx,
            kind=ContentKind.UNKNOWN,
            supported=False,
            title=f"(item #{idx})",
        )
    kind = _media_kind(entry) or ContentKind.SINGLE_IMAGE

    title = str(entry.get("title") or entry.get("id") or f"Item {idx}")
    title = title.replace("\n", " ").replace("|", "/")[:80]
    return ProbeItem(
        index=idx,
        kind=kind,
        supported=kind != ContentKind.SINGLE_IMAGE,
        title=title,
        duration=_seconds(entry),
    )


def _has_thumbnail(info: dict[str, Any]) -> bool:
    # A raw result may carry just ``thumbnail``: yt-dlp hasn't normalized it yet.
    return bool(info.get("thumbnails") or info.get("thumbnail"))


def _classify_single(data: dict[str, Any]) -> ContentKind:
    kind = _media_kind(data)
    if kind is not None:
        return kind
    if _has_thumbnail(data):
        return ContentKind.SINGLE_IMAGE
    return ContentKind.UNKNOWN


class _ProbeYdl(_Ydl):
    """Hands back what has nothing to fetch, so ``errors`` holds real failures only.

    ``ignore_no_formats_error`` can't do this: extractors read it too, and turn a
    site's own "sign in" or "private" into a warning.
    """

    def process_video_result(self, info_dict: dict[str, Any], download: bool = True) -> Any:
        kind = _media_kind(info_dict)
        known = info_dict.get("playlist_index") or _has_thumbnail(info_dict)
        if kind == ContentKind.SINGLE_IMAGE or (kind is None and known):
            return info_dict
        return super().process_video_result(info_dict, download=download)


def _read_result(url: str, site: str, data: Any, errors: list[str]) -> Probe:
    auth_errors = [error for error in errors if is_auth_error(error)]
    worst = next(iter(auth_errors or errors), None)
    reason = _short_reason(worst) if worst else None
    entries = data.get("entries") if isinstance(data, dict) else None

    if not isinstance(data, dict) or (entries and not any(entries)):
        return Probe(
            url=url,
            site=site,
            content_kind=ContentKind.UNKNOWN,
            failed=True,
            error_message=reason,
            needs_cookies=bool(auth_errors),
        )

    if entries:
        items = [_classify_entry(e, i) for i, e in enumerate(entries, 1)]
        return Probe(
            url=url,
            site=site,
            content_kind=ContentKind.PLAYLIST,
            title=data.get("title") or data.get("id"),
            uploader=data.get("uploader") or data.get("channel"),
            items=items,
            needs_cookies=bool(auth_errors),
            error_message=reason,
        )

    kind = _classify_single(data)
    return Probe(
        url=url,
        site=site,
        content_kind=kind,
        title=data.get("title") or data.get("id"),
        uploader=data.get("uploader") or data.get("channel"),
        duration=_seconds(data),
        filesize=data.get("filesize_approx") or data.get("filesize"),
        may_hold_video=kind == ContentKind.SINGLE_AUDIO
        and any(_audio_by_label_only(f) for f in _formats(data)),
    )


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
        ydl_opts["cookiesfrombrowser"] = ydl_cookies(cookies_browser)

    errors: list[str] = []
    try:
        with _ProbeYdl(ydl_opts) as ydl:
            errors = ydl.errors
            data = ydl.extract_info(url, download=False, process=True)
        return _read_result(url, site, data, errors)
    except Exception as exc:
        # A missing browser reports its real cause first, then raises a generic error.
        return _read_result(url, site, None, errors or [str(exc)])
