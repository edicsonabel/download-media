"""Tests for the history log writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_media import history


def test_append_writes_tsv_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history.append("https://x", "video", Path("/home/user/Videos"))

    log = tmp_path / "download-media" / "history.log"
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip()
    fields = line.split("\t")
    assert len(fields) == 4
    _ts, kind, url, out = fields
    assert kind == "video"
    assert url == "https://x"
    assert out == "/home/user/Videos"


def test_append_creates_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "deep" / "nested"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    history.append("https://x", "audio", Path("/m"))
    assert (state / "download-media" / "history.log").exists()


def test_append_swallows_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure to write must not crash the caller — history is best-effort."""
    monkeypatch.setattr(
        "download_media.history.history_file",
        lambda: Path("/proc/this-cannot-be-written/log"),
    )
    history.append("https://x", "video", Path("/m"))  # no exception
