"""Tests for clipboard URL detection."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from download_media import clipboard


def setup_tools(
    monkeypatch: pytest.MonkeyPatch, available: set[str], output: Any, *, wayland: bool = False
) -> list[str]:
    calls: list[str] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd[0])
        if isinstance(output, Exception):
            raise output
        return subprocess.CompletedProcess(cmd, 0, stdout=output)

    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    else:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda c: c if c in available else None)
    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    return calls


def test_wayland_tool_is_tried_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = setup_tools(monkeypatch, {"wl-paste", "xclip"}, "https://a.test/x\n", wayland=True)
    assert clipboard.read_clipboard() == "https://a.test/x"
    assert calls == ["wl-paste"]


def test_wl_paste_is_ignored_outside_wayland(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = setup_tools(monkeypatch, {"wl-paste", "xsel"}, "https://a.test/x")
    assert clipboard.read_clipboard() == "https://a.test/x"
    assert calls == ["xsel"]


@pytest.mark.parametrize("text", ["", "not a url", "ftp://a.test/x", "https://a b", "a\nb"])
def test_non_urls_are_rejected(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    setup_tools(monkeypatch, {"xclip"}, text)
    assert clipboard.read_clipboard() is None


def test_no_tools_available(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_tools(monkeypatch, set(), "https://a.test/x")
    assert clipboard.read_clipboard() is None


@pytest.mark.parametrize("error", [OSError("nope"), subprocess.TimeoutExpired("xclip", 2)])
def test_tool_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    setup_tools(monkeypatch, {"xclip", "xsel"}, error)
    assert clipboard.read_clipboard() is None
