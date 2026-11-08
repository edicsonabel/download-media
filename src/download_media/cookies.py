"""``--cookies BROWSER[+KEYRING]`` spec and Linux keyring detection.

yt-dlp picks the keyring from the desktop environment, but Chromium browsers
can be pointed elsewhere with ``--password-store``. On a mismatch yt-dlp reads
the wrong key and drops every cookie it can't decrypt, and sites answer the
half-empty session with errors that look nothing like an auth problem.
"""

from __future__ import annotations

import argparse
import os
import shlex
import textwrap
from dataclasses import dataclass
from pathlib import Path

from yt_dlp.cookies import CookieLoadError
from yt_dlp.utils import DownloadError

from .config import BROWSERS
from .i18n import t
from .ui import warn

KEYRINGS: tuple[str, ...] = ("basictext", "gnomekeyring", "kwallet", "kwallet5", "kwallet6")

_PASSWORD_STORES: dict[str, str] = {
    "basic": "basictext",
    "gnome": "gnomekeyring",
    "gnome-keyring": "gnomekeyring",
    "gnome-libsecret": "gnomekeyring",
    "kwallet": "kwallet",
    "kwallet5": "kwallet5",
    "kwallet6": "kwallet6",
}

# Flag files read by the Arch launcher scripts of each browser.
_FLAG_FILES: dict[str, tuple[str, ...]] = {
    "brave": ("brave-flags.conf", "brave-browser-flags.conf"),
    "chrome": ("chrome-flags.conf", "google-chrome-flags.conf"),
    "chromium": ("chromium-flags.conf",),
    "edge": ("microsoft-edge-stable-flags.conf",),
    "opera": ("opera-flags.conf",),
    "vivaldi": ("vivaldi-stable.conf",),
}


def parse_spec(value: str) -> str:
    """Validate and normalize a ``BROWSER[+KEYRING]`` value (argparse ``type``)."""
    spec = value.lower()
    browser, sep, keyring = spec.partition("+")
    if browser not in BROWSERS or (sep and keyring not in KEYRINGS):
        raise argparse.ArgumentTypeError(
            t("cookies_bad_spec", value, ", ".join(BROWSERS), ", ".join(KEYRINGS))
        )
    return spec


def browser_of(spec: str) -> str:
    return spec.partition("+")[0]


def uses_keyring(spec: str) -> bool:
    """Whether this browser encrypts cookies and no keyring was chosen explicitly."""
    return "+" not in spec and spec in _FLAG_FILES


def _password_store(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    store = None
    for line in text.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        for token in tokens:
            if token.startswith("--password-store="):
                store = token.partition("=")[2]  # Chromium keeps the last one.
    return store


def detect_keyring(browser: str) -> str | None:
    """Keyring forced by the browser's flags file, if any."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for name in _FLAG_FILES.get(browser, ()):
        keyring = _PASSWORD_STORES.get(_password_store(config / name) or "")
        if keyring:
            return keyring
    return None


def ydl_cookies(spec: str) -> tuple[str | None, ...]:
    """Translate a spec into yt-dlp's ``cookiesfrombrowser`` tuple."""
    browser, _, keyring = spec.partition("+")
    keyring = keyring or detect_keyring(browser) or ""
    return (browser, None, keyring.upper(), None) if keyring else (browser,)


@dataclass(frozen=True)
class CookieFailure:
    """Why yt-dlp couldn't load a browser's cookies."""

    missing: bool  # No cookie database: the browser isn't installed, or was never opened.
    reason: str  # yt-dlp's cause, first line.


_REASON_LENGTH = 160


def cookie_failure(exc: BaseException) -> CookieFailure | None:
    """The cookie-load failure behind ``exc``, if that's what it is."""
    # Without ignoreerrors, yt-dlp reports the CookieLoadError, which raises a DownloadError.
    if isinstance(exc, DownloadError) and exc.exc_info:
        exc = exc.exc_info[1]
    if not isinstance(exc, CookieLoadError):
        return None
    # load_cookies turns any exception into CookieLoadError, raised inside the except block.
    cause = exc.__context__ or exc
    text = str(cause)
    lines = text.strip().splitlines()
    reason = textwrap.shorten(lines[0] if lines else text, _REASON_LENGTH, placeholder="...")
    missing = (
        isinstance(cause, FileNotFoundError)
        and text.startswith("could not find ")
        and " cookies database" in text
    )
    return CookieFailure(missing=missing, reason=reason)


def secretstorage_hint() -> None:
    warn("  → install python-secretstorage so cookies from Brave/Chrome can be decrypted:")
    warn("      sudo pacman -S python-secretstorage")


def report_failure(spec: str, failure: CookieFailure) -> None:
    """Say why the cookies of ``spec`` couldn't be read; the run goes on without them."""
    browser = browser_of(spec)
    if failure.missing:
        # Not a keyring problem: there's nothing to decrypt.
        warn(t("cookies_no_browser", browser))
        return
    warn(t("cookies_unreadable", browser, failure.reason))
    if "secretstorage" in failure.reason.lower():
        secretstorage_hint()
    elif uses_keyring(spec):
        warn(t("cookies_keyring_hint", browser))
