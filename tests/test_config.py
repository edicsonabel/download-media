"""Tests for config module — defaults, XDG resolution, options dataclass."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_media.config import (
    AUDIO_FORMATS,
    BROWSERS,
    QUALITIES,
    VIDEO_FORMATS,
    DownloadOptions,
    history_file,
    state_dir,
    xdg_dir,
)


def test_constants_are_non_empty() -> None:
    assert len(QUALITIES) > 0
    assert len(AUDIO_FORMATS) > 0
    assert len(VIDEO_FORMATS) > 0
    assert len(BROWSERS) > 0
    assert "best" in QUALITIES
    assert "mp3" in AUDIO_FORMATS
    assert "mp4" in VIDEO_FORMATS


def test_state_dir_uses_xdg_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_dir() == tmp_path / "download-media"
    assert history_file() == tmp_path / "download-media" / "history.log"


def test_xdg_dir_falls_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("download_media.config.shutil.which", lambda _name: None)
    assert xdg_dir("VIDEOS", "Videos") == tmp_path / "Videos"


def test_options_resolve_output_defaults_per_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("download_media.config.xdg_dir",
                        lambda kind, fallback: Path(f"/fake/{kind.lower()}"))
    video_opts = DownloadOptions(urls=["x"], audio_only=False)
    audio_opts = DownloadOptions(urls=["x"], audio_only=True)
    assert video_opts.resolve_output() == Path("/fake/videos")
    assert audio_opts.resolve_output() == Path("/fake/music")


def test_options_resolve_output_respects_override() -> None:
    opts = DownloadOptions(urls=["x"], output=Path("/tmp/custom"))
    assert opts.resolve_output() == Path("/tmp/custom")
