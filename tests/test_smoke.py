"""Smoke tests covering the most basic invariants of the CLI."""

from __future__ import annotations

import subprocess
import sys

from download_media import __version__


def test_version_constant_format() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3, f"expected SemVer, got {__version__!r}"
    assert all(p.isdigit() for p in parts), f"non-numeric component in {__version__!r}"


def test_version_flag_prints_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "download_media", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout


def test_help_does_not_crash() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "download_media", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "download-media" in result.stdout
    assert "URLs" in result.stdout
