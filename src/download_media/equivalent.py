"""Build the direct-mode command equivalent to a finished run.

The user can copy-paste this command to repeat the download without the wizard
or any prompt, so it always carries ``-y``. Flags that match defaults are
omitted to keep the output short and readable.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from .config import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_QUALITY,
    DEFAULT_SUB_LANGS,
    DEFAULT_VIDEO_FORMAT,
    DownloadOptions,
    xdg_dir,
)
from .selection import is_playlist_url


def _free_text(flag: str, value: str) -> list[str]:
    quoted = shlex.quote(value)
    # As its own token, argparse can take a value starting with - for a flag.
    return [f"{flag}={quoted}"] if value.startswith("-") else [flag, quoted]


def build(opts: DownloadOptions) -> str:
    parts: list[str] = ["download-media", "-y"]

    if opts.audio_only:
        parts.append("-a")
        if opts.audio_format != DEFAULT_AUDIO_FORMAT:
            parts.extend(["-f", opts.audio_format])
    else:
        if opts.quality != DEFAULT_QUALITY:
            parts.extend(["-q", opts.quality])
        if opts.video_format != DEFAULT_VIDEO_FORMAT:
            parts.extend(["--video-format", opts.video_format])

    if opts.output is not None:
        default_out = xdg_dir("MUSIC", "Music") if opts.audio_only else xdg_dir("VIDEOS", "Videos")
        if Path(opts.output) != default_out:
            parts.extend(["-o", shlex.quote(str(opts.output))])

    if opts.cookies_browser:
        parts.extend(["--cookies", opts.cookies_browser])

    if opts.subs:
        parts.append("-s")  # Older versions need it: there --sub-langs alone did nothing.
        if opts.sub_langs != DEFAULT_SUB_LANGS:
            parts.extend(_free_text("--sub-langs", opts.sub_langs))

    if opts.playlist_mode == "yes":
        if opts.playlist_items:
            parts.extend(_free_text("--playlist-items", opts.playlist_items))
        else:
            parts.append("-p")
    elif opts.playlist_mode == "no" and any(is_playlist_url(u) for u in opts.urls):
        # Records the decision on URLs that look like playlists/carousels;
        # anywhere else -P is just noise.
        parts.append("-P")

    for url in opts.urls:
        parts.append(shlex.quote(url))

    return " ".join(parts)
