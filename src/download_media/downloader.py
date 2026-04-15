"""Thin wrapper around :class:`yt_dlp.YoutubeDL`.

We translate :class:`~download_media.config.DownloadOptions` into the dict of
flags yt-dlp expects, then drive the download. No shelling out — yt-dlp is
imported as a library so we get exceptions, typed entries, and direct access
to extracted metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from .config import DownloadOptions


def build_ydl_opts(opts: DownloadOptions, output_dir: Path) -> dict[str, Any]:
    """Translate our high-level options into a yt-dlp options dict."""
    ydl: dict[str, Any] = {
        "no_warnings": True,
        "noprogress": False,
    }

    is_playlist = opts.playlist_mode == "yes"
    if is_playlist:
        # Index + ID prefix prevents filename collisions across carousel items.
        ydl["outtmpl"] = str(
            output_dir / "%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
        )
        ydl["noplaylist"] = False
        if opts.playlist_items:
            ydl["playlist_items"] = opts.playlist_items
        # Carousels often mix media types — one un-extractable item shouldn't
        # abort the rest of the batch.
        ydl["ignoreerrors"] = True
    else:
        ydl["outtmpl"] = str(output_dir / "%(title)s.%(ext)s")
        ydl["noplaylist"] = True

    if opts.cookies_browser:
        ydl["cookiesfrombrowser"] = (opts.cookies_browser,)

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
            fmt = f"bv*[height<={q}]+ba/b[height<={q}]/b/best"
        ydl.update(
            {
                "format": fmt,
                "merge_output_format": opts.video_format,
                "postprocessors": [{"key": "FFmpegMetadata"}],
            }
        )

    return ydl


def download(url: str, opts: DownloadOptions) -> int:
    """Download a single URL. Returns 0 on success, non-zero otherwise."""
    output_dir = opts.resolve_output()
    output_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = build_ydl_opts(opts, output_dir)
    with YoutubeDL(ydl_opts) as ydl:
        return int(ydl.download([url]))


def list_formats(url: str, opts: DownloadOptions) -> int:
    """Print available formats for a URL (equivalent to ``yt-dlp -F``)."""
    ydl_opts: dict[str, Any] = {"listformats": True}
    if opts.cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (opts.cookies_browser,)
    with YoutubeDL(ydl_opts) as ydl:
        return int(ydl.download([url]))
