"""Best-effort clipboard URL detection (Wayland + X11)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

_URL_RE = re.compile(r"^https?://[^\s]+$")


def read_clipboard() -> str | None:
    """Return the clipboard contents if it looks like an HTTP(S) URL.

    Tries ``wl-paste`` on Wayland, then ``xclip`` and ``xsel`` on X11.
    Returns ``None`` if no tool is available, the clipboard is empty, the
    contents don't look like a URL, or any subprocess errors.
    """
    candidates: list[tuple[str, list[str]]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(("wl-paste", []))
    candidates.extend([
        ("xclip", ["-selection", "clipboard", "-o"]),
        ("xsel", ["-b"]),
    ])

    for cmd, args in candidates:
        if not shutil.which(cmd):
            continue
        try:
            result = subprocess.run(
                [cmd, *args],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        text = result.stdout.strip()
        if _URL_RE.match(text):
            return text
    return None
