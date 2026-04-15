"""Terminal UI helpers: colors, prompts, formatters.

Colors are emitted only when stdout is a TTY (so piping to a file or another
process produces clean output).
"""

from __future__ import annotations

import sys
from typing import Final, NoReturn

_TTY: Final[bool] = sys.stdout.isatty()


class C:
    """ANSI color constants. Empty strings when stdout is not a TTY."""

    GREEN: Final[str] = "\033[0;32m" if _TTY else ""
    CYAN: Final[str] = "\033[0;36m" if _TTY else ""
    YELLOW: Final[str] = "\033[0;33m" if _TTY else ""
    RED: Final[str] = "\033[0;31m" if _TTY else ""
    BOLD: Final[str] = "\033[1m" if _TTY else ""
    DIM: Final[str] = "\033[2m" if _TTY else ""
    RESET: Final[str] = "\033[0m" if _TTY else ""


def info(msg: str) -> None:
    print(f"{C.CYAN}{msg}{C.RESET}")


def ok(msg: str) -> None:
    print(f"{C.GREEN}{msg}{C.RESET}")


def warn(msg: str) -> None:
    print(f"{C.YELLOW}{msg}{C.RESET}")


def err(msg: str) -> None:
    print(f"{C.RED}{msg}{C.RESET}", file=sys.stderr)


def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    raise SystemExit(code)


def fmt_duration(seconds: float | int | None) -> str:
    """Render a duration in human form (``1:23`` or ``1:23:45``)."""
    if not seconds or seconds <= 0:
        return "N/A"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fmt_bytes(b: float | int | None) -> str:
    """Render a byte count in human form (``1.4 MB`` etc.)."""
    if not b or b <= 0:
        return "N/A"
    if b >= 1024**3:
        return f"{b / 1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b / 1024**2:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{int(b)} B"
