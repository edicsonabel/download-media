"""Release invariants: versions in sync, declared deps, working impersonation."""

from __future__ import annotations

import re
import string
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget

from download_media import __version__
from download_media.i18n import _MESSAGES

ROOT = Path(__file__).resolve().parent.parent
PKGBUILD = (ROOT / "PKGBUILD").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
LANDING = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def test_version_is_the_same_in_all_four_places() -> None:
    pkgver = re.search(r"^pkgver=(.+)$", PKGBUILD, re.MULTILINE)
    assert pkgver is not None
    assert f'version = "{__version__}"' in PYPROJECT
    assert pkgver.group(1) == __version__
    assert f">v{__version__} ·" in LANDING


def test_pkgbuild_declares_runtime_dependencies() -> None:
    depends = re.search(r"^depends=\((.*?)^\)", PKGBUILD, re.MULTILINE | re.DOTALL)
    assert depends is not None
    for package in ("yt-dlp", "ffmpeg", "python-secretstorage", "python-curl_cffi"):
        assert f"'{package}'" in depends.group(1)


def test_pyproject_requests_impersonation_support() -> None:
    assert '"yt-dlp[curl-cffi]' in PYPROJECT


def test_yt_dlp_can_impersonate() -> None:
    """Without this, yt-dlp silently skips Instagram's logged-out path."""
    with YoutubeDL({"quiet": True}) as ydl:
        assert ydl._impersonate_target_available(ImpersonateTarget())


def test_translations_take_the_same_arguments() -> None:
    def fields(message: str) -> list[str | None]:
        return sorted(f or "" for _, f, _, _ in string.Formatter().parse(message) if f is not None)

    for key, message in _MESSAGES["en"].items():
        assert fields(message) == fields(_MESSAGES["es"][key]), key
