"""The autouse ``isolated_env`` fixture keeps tests out of the real home folder."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from download_media.config import xdg_dir


def test_default_folders_resolve_under_tmp_path(tmp_path: Path) -> None:
    assert Path.home() == tmp_path / "isolated-home"
    assert xdg_dir("VIDEOS", "Videos").is_relative_to(tmp_path)
    assert xdg_dir("MUSIC", "Music").is_relative_to(tmp_path)


def test_relative_paths_resolve_under_tmp_path(tmp_path: Path) -> None:
    assert Path.cwd().is_relative_to(tmp_path)
    assert Path("2").resolve().is_relative_to(tmp_path)


def test_exported_user_dirs_cannot_send_tests_to_the_real_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Desktops such as KDE export XDG_VIDEOS_DIR, and the shell build of
    # xdg-user-dir reads the environment before falling back to $HOME.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "xdg-user-dir"
    script.write_text('#!/bin/sh\neval echo "\\${XDG_${1}_DIR:-$HOME}"\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    assert xdg_dir("VIDEOS", "Videos").is_relative_to(tmp_path)
    assert xdg_dir("MUSIC", "Music").is_relative_to(tmp_path)


def test_no_test_can_reach_the_real_session_bus(tmp_path: Path) -> None:
    bus = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    assert bus.startswith("unix:path=")
    socket = Path(bus.removeprefix("unix:path="))
    assert socket.is_relative_to(tmp_path)
    assert not socket.exists()


def test_no_desktop_picks_a_keyring() -> None:
    desktop = (
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "KDE_FULL_SESSION",
        "GNOME_DESKTOP_SESSION_ID",
    )
    assert not [name for name in desktop if name in os.environ]


def test_live_tests_keep_the_real_home(tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    conftest = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    (inner / "conftest.py").write_text(conftest, encoding="utf-8")
    (inner / "pytest.ini").write_text("[pytest]\nmarkers =\n    live: real\n", encoding="utf-8")
    probe = """
        import os

        import pytest

        HOME = os.environ["HOME"]
        VIDEOS = os.environ["XDG_VIDEOS_DIR"]
        BUS = os.environ["DBUS_SESSION_BUS_ADDRESS"]
        DESKTOP = os.environ["XDG_CURRENT_DESKTOP"]


        @pytest.mark.live
        def test_live_sees_the_real_home():
            assert os.environ["HOME"] == HOME
            assert os.environ["XDG_VIDEOS_DIR"] == VIDEOS
            assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == BUS
            assert os.environ["XDG_CURRENT_DESKTOP"] == DESKTOP


        def test_others_do_not():
            assert os.environ["HOME"] != HOME
            assert "XDG_VIDEOS_DIR" not in os.environ
            assert os.environ["DBUS_SESSION_BUS_ADDRESS"] != BUS
            assert "XDG_CURRENT_DESKTOP" not in os.environ
    """
    (inner / "test_inner.py").write_text(textwrap.dedent(probe), encoding="utf-8")
    real = tmp_path / "real-home"
    env = {
        **os.environ,
        "HOME": str(real),
        "XDG_VIDEOS_DIR": str(real / "Videos"),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/real-bus",
        "XDG_CURRENT_DESKTOP": "KDE",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini"],
        cwd=inner,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
