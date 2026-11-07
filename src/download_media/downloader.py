"""Thin wrapper around :class:`yt_dlp.YoutubeDL`.

We translate :class:`~download_media.config.DownloadOptions` into the dict of
flags yt-dlp expects, then drive the download. No shelling out — yt-dlp is
imported as a library so we get exceptions, typed entries, and direct access
to extracted metadata.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

from .config import DownloadOptions
from .cookies import ydl_cookies
from .i18n import t


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """What one URL produced; multi-entry URLs can succeed only in part."""

    saved: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.saved + self.skipped + self.failed


class _Ydl(YoutubeDL):  # type: ignore[misc]
    """Keeps the errors that ``ignoreerrors`` would otherwise only print."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.errors: list[str] = []
        super().__init__(*args, **kwargs)

    def report_error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(message))
        super().report_error(message, *args, **kwargs)


def build_ydl_opts(opts: DownloadOptions, output_dir: Path) -> dict[str, Any]:
    """Translate our high-level options into a yt-dlp options dict."""
    ydl: dict[str, Any] = {
        "no_warnings": True,
        "noprogress": False,
    }

    is_playlist = opts.playlist_mode == "yes"
    if is_playlist:
        # Carousel items get an index prefix and an ID suffix (_AvoidNameClash) so
        # they can't collide; a lone video has no index and keeps its plain name.
        ydl["outtmpl"] = str(
            output_dir / "%(playlist_index&{:02d} - |)s%(title)s%(_name_suffix|)s.%(ext)s"
        )
        ydl["noplaylist"] = False
        if opts.playlist_items:
            ydl["playlist_items"] = opts.playlist_items
        # Carousels often mix media types — one un-extractable item shouldn't
        # abort the rest of the batch.
        ydl["ignoreerrors"] = True
    else:
        # The suffix is only filled in by _AvoidNameClash when needed.
        ydl["outtmpl"] = str(output_dir / "%(title)s%(_name_suffix|)s.%(ext)s")
        ydl["noplaylist"] = True

    if opts.cookies_browser:
        ydl["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)

    if opts.subs:
        ydl.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [s.strip() for s in opts.sub_langs.split(",") if s.strip()],
                "embedsubtitles": True,
                "postprocessors": [{"key": "FFmpegEmbedSubtitle"}],
            }
        )

    if opts.audio_only:
        ydl.update(
            {
                "format": "bestaudio/best",
                "writethumbnail": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": opts.audio_format,
                        "preferredquality": "0",
                    },
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata"},
                ],
            }
        )
    else:
        # Format selector falls back to "best" so image entries (e.g. items in
        # an Instagram carousel) still download — they have no video stream.
        if opts.quality == "best":
            fmt = "bv*+ba/b/best"
        else:
            q = opts.quality
            # `<=?` keeps formats with unknown height — Instagram's progressive
            # H.264 files don't report one, and the filter would drop them.
            fmt = f"bv*[height<=?{q}]+ba/b[height<=?{q}]/b/best"
        if opts.video_format == "mp4":
            # mp4 means "plays everywhere": WhatsApp, iOS and most TVs reject
            # VP9/AV1 even inside an mp4 container.
            ydl["format_sort"] = ["vcodec:h264", "acodec:aac"]
        ydl.update(
            {
                "format": fmt,
                "merge_output_format": opts.video_format,
                "postprocessors": [{"key": "FFmpegMetadata"}],
            }
        )

    return ydl


def _source_url(path: Path) -> str | None:
    """URL that FFmpegMetadata stored in a file we downloaded earlier, if any."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=purl,comment"]
        cmd += ["-of", "default=nw=1:nk=1", str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line for line in out.splitlines() if "://" in line), None)


class _AvoidNameClash(PostProcessor):  # type: ignore[misc]
    """Append ``[id]`` to the filename when another video already took the name.

    Titles aren't unique (every Instagram post by a user is "Video by <user>"),
    and yt-dlp treats an existing file as "already downloaded" — silently
    skipping the new video and re-tagging the old file with its metadata.
    """

    def __init__(self, final_ext: str | None) -> None:
        super().__init__()
        # Audio extraction changes the extension after download.
        self._final_ext = final_ext

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        # Carousel items share both title and webpage_url, so the check below
        # can't tell them apart.
        if info.get("playlist_index"):
            info["_name_suffix"] = f" [{info.get('id')}]"
            return [], info
        path = Path(self._downloader.prepare_filename(info))
        if self._final_ext:
            path = path.with_suffix(f".{self._final_ext}")
        if path.exists() and _source_url(path) != info.get("webpage_url"):
            info["_name_suffix"] = f" [{info.get('id')}]"
        return [], info


def _skip_items_without_media(skipped: list[str]) -> Callable[..., str | None]:
    """``match_filter`` dropping carousel items yt-dlp has no formats for.

    Instagram images come through as such items, and yt-dlp downloads carousels
    even with ``noplaylist``. Without this the first image would abort a
    single-mode run. yt-dlp also calls this with the playlist's own metadata
    (no ``id``) and for lone URLs (no ``playlist_index``); those must pass, so a
    lone URL with no formats still fails as an error.
    """

    def match_filter(info: Any, *, incomplete: Any = False) -> str | None:
        item = info.get("id")
        if not (incomplete and item and info.get("playlist_index")):
            return None
        if info.get("_type", "video") != "video" or info.get("formats") or info.get("url"):
            return None
        skipped.append(str(item))
        return t("item_no_media", item)

    return match_filter


def download(url: str, opts: DownloadOptions) -> DownloadResult:
    """Download a single URL. Raises ``DownloadError`` if nothing could be saved."""
    output_dir = opts.resolve_output()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped: list[str] = []
    ydl_opts = build_ydl_opts(opts, output_dir)
    ydl_opts["post_hooks"] = [saved.append]
    ydl_opts["match_filter"] = _skip_items_without_media(skipped)
    with _Ydl(ydl_opts) as ydl:
        final_ext = opts.audio_format if opts.audio_only else None
        ydl.add_post_processor(_AvoidNameClash(final_ext), when="video")
        returncode = int(ydl.download([url]))
    # ignoreerrors turns a total failure into a bare return code.
    if returncode != 0 and not saved:
        raise DownloadError("\n".join(ydl.errors) or t("failed", url))
    return DownloadResult(saved=len(saved), skipped=len(skipped), failed=len(ydl.errors))


def list_formats(url: str, opts: DownloadOptions) -> int:
    """Print available formats for a URL (equivalent to ``yt-dlp -F``)."""
    ydl_opts: dict[str, Any] = {
        "listformats": True,
        "match_filter": _skip_items_without_media([]),
    }
    if opts.cookies_browser:
        ydl_opts["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)
    with YoutubeDL(ydl_opts) as ydl:
        return int(ydl.download([url]))
