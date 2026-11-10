"""Allow running the package as `python -m download_media`."""

from __future__ import annotations

from .cli import entry

if __name__ == "__main__":
    entry()
