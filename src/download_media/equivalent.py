"""Build the direct-mode command equivalent to a wizard run.

The user can copy-paste this command to repeat the download without going
through the wizard again. Flags that match defaults are omitted to keep the
output short and readable.
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


def build(opts: DownloadOptions) -> str:
    parts: list[str] = ["download-media"]

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
        if opts.sub_langs != DEFAULT_SUB_LANGS:
            parts.extend(["--sub-langs", opts.sub_langs])
        else:
            parts.append("-s")

    if opts.playlist_mode == "yes":
        parts.append("-p")
    elif opts.playlist_mode == "no":
        parts.append("-P")

    for url in opts.urls:
        parts.append(shlex.quote(url))

    return " ".join(parts)
