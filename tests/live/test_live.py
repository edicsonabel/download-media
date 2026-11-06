"""Real-network release checks. Deselected by default; see scripts/pre-release.sh.

These catch what no local test can: site changes, the real keyring, and browser
cookies. Everything goes through the CLI, as a user would run it.

    DM_LIVE_IG_CAROUSEL  public Instagram post mixing images and videos
    DM_LIVE_BROWSER      logged-in browser, e.g. brave
    DM_LIVE_VIDEO        optional, public video URL
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

VIDEO = os.environ.get("DM_LIVE_VIDEO", "https://www.youtube.com/watch?v=jNQXAC9IVRw")
CAROUSEL = os.environ.get("DM_LIVE_IG_CAROUSEL")
BROWSER = os.environ.get("DM_LIVE_BROWSER")

needs_carousel = pytest.mark.skipif(not CAROUSEL, reason="DM_LIVE_IG_CAROUSEL not set")
needs_browser = pytest.mark.skipif(not BROWSER, reason="DM_LIVE_BROWSER not set")


@pytest.fixture
def out(tmp_path: Path) -> Path:
    """Own folder: ``tmp_path`` also holds the isolated state directory."""
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "download_media", *argv],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "LANG": "en_US.UTF-8"},
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def assert_every_saved_item_is_a_file(result: subprocess.CompletedProcess[str], out: Path) -> None:
    files = list(out.iterdir())
    assert files, output_of(result)
    count = re.search(r"(\d+) of \d+ items downloaded", result.stdout)
    if count:
        assert len(files) == int(count.group(1))


def test_public_video(out: Path) -> None:
    result = run("-y", "-o", str(out), VIDEO)
    assert result.returncode == 0, output_of(result)
    assert len(list(out.iterdir())) == 1


def test_public_video_as_audio(out: Path) -> None:
    result = run("-y", "-a", "-o", str(out), VIDEO)
    assert result.returncode == 0, output_of(result)
    assert [p.suffix for p in out.iterdir()] == [".mp3"]


def test_unsupported_url_fails_cleanly(out: Path) -> None:
    result = run("-y", "-o", str(out), "https://example.com/")
    assert result.returncode == 1
    assert "Traceback" not in output_of(result)
    assert list(out.iterdir()) == []


@needs_carousel
@pytest.mark.parametrize("flags", [(), ("-p",)], ids=["yes", "playlist"])
def test_instagram_carousel_without_cookies(out: Path, flags: tuple[str, ...]) -> None:
    result = run("-y", *flags, "-o", str(out), str(CAROUSEL))
    assert result.returncode == 0, output_of(result)
    assert_every_saved_item_is_a_file(result, out)


@needs_carousel
@needs_browser
@pytest.mark.parametrize("flags", [(), ("-p",)], ids=["yes", "playlist"])
def test_instagram_carousel_with_browser_cookies(out: Path, flags: tuple[str, ...]) -> None:
    result = run("-y", *flags, "--cookies", str(BROWSER), "-o", str(out), str(CAROUSEL))
    output = output_of(result)
    assert result.returncode == 0, output
    assert "could not be decrypted" not in output
    assert "Retrying without cookies" not in output
    assert_every_saved_item_is_a_file(result, out)


@needs_carousel
def test_listing_carousel_formats(out: Path) -> None:
    result = run("-y", "-l", str(CAROUSEL))
    assert result.returncode == 0, output_of(result)
    assert "Traceback" not in output_of(result)
    assert "Available formats" in result.stdout


@needs_carousel
def test_repeating_a_carousel_adds_no_files(out: Path) -> None:
    run("-y", "-o", str(out), str(CAROUSEL))
    before = sorted(p.name for p in out.iterdir())
    result = run("-y", "-o", str(out), str(CAROUSEL))
    assert result.returncode == 0, output_of(result)
    assert sorted(p.name for p in out.iterdir()) == before
