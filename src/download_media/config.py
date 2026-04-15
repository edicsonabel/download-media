"""Defaults, type aliases, and resolved options for a download invocation."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_QUALITY = "1080"
DEFAULT_VIDEO_FORMAT = "mp4"
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_SUB_LANGS = "es,en"

Quality = Literal["360", "480", "720", "1080", "1440", "2160", "best"]
AudioFormat = Literal["mp3", "m4a", "opus", "wav"]
VideoFormat = Literal["mp4", "mkv", "webm"]
Browser = Literal["firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari"]
PlaylistMode = Literal["auto", "yes", "no"]

QUALITIES: tuple[str, ...] = ("360", "480", "720", "1080", "1440", "2160", "best")
AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "wav")
VIDEO_FORMATS: tuple[str, ...] = ("mp4", "mkv", "webm")
BROWSERS: tuple[str, ...] = (
    "firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari",
)


def xdg_dir(kind: str, fallback: str) -> Path:
    """Resolve a localized XDG user dir (Videos/Music/Downloads/etc.).

    Uses ``xdg-user-dir`` when available so the user's localized folder name
    (e.g. ``Vídeos``, ``Música``, ``Movies``) and any custom path on another
    disk is respected. Falls back to ``~/<fallback>`` otherwise.
    """
    home = Path.home()
    if shutil.which("xdg-user-dir"):
        try:
            result = subprocess.run(
                ["xdg-user-dir", kind],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            path = result.stdout.strip()
            if path and path != str(home):
                return Path(path)
        except (subprocess.SubprocessError, OSError):
            pass
    return home / fallback


def state_dir() -> Path:
    """Persistent state directory (XDG Base Directory spec)."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "download-media"


def history_file() -> Path:
    return state_dir() / "history.log"


@dataclass(slots=True)
class DownloadOptions:
    """Resolved options for one or more download invocations."""

    urls: list[str]
    audio_only: bool = False
    quality: str = DEFAULT_QUALITY
    audio_format: str = DEFAULT_AUDIO_FORMAT
    video_format: str = DEFAULT_VIDEO_FORMAT
    output: Path | None = None
    cookies_browser: str | None = None
    subs: bool = False
    sub_langs: str = DEFAULT_SUB_LANGS
    playlist_mode: PlaylistMode = "auto"
    playlist_items: str | None = None
    no_confirm: bool = False
    list_formats: bool = False

    def resolve_output(self) -> Path:
        if self.output is not None:
            return self.output
        return xdg_dir("MUSIC", "Music") if self.audio_only else xdg_dir("VIDEOS", "Videos")
