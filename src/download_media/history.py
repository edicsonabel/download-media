"""Append-only history log of successful downloads.

One TSV line per download, written to ``$XDG_STATE_HOME/download-media/history.log``
(falls back to ``~/.local/state/download-media/``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import history_file


def append(url: str, kind: str, output_dir: Path) -> None:
    """Append one download record. Failure to write is silently ignored."""
    log = history_file()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{kind}\t{url}\t{output_dir}\n")
    except OSError:
        pass
