"""Tests for the --cookies spec and keyring detection."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS, _parse_browser_specification

from download_media.config import BROWSERS
from download_media.cookies import (
    _FLAG_FILES,
    _PASSWORD_STORES,
    KEYRINGS,
    browser_of,
    detect_keyring,
    parse_spec,
    uses_keyring,
    ydl_cookies,
)


def write_flags(tmp_path: Path, content: str, name: str = "brave-flags.conf") -> None:
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / name).write_text(content, encoding="utf-8")


@pytest.mark.parametrize("browser", BROWSERS)
def test_every_browser_is_accepted(browser: str) -> None:
    assert parse_spec(browser) == browser


@pytest.mark.parametrize("keyring", KEYRINGS)
def test_every_keyring_is_accepted(keyring: str) -> None:
    assert parse_spec(f"brave+{keyring}") == f"brave+{keyring}"


def test_spec_is_normalized_to_lower_case() -> None:
    assert parse_spec("Brave+GnomeKeyring") == "brave+gnomekeyring"


@pytest.mark.parametrize("bad", ["", "netscape", "brave+", "brave+nope", "+gnomekeyring"])
def test_invalid_specs_are_rejected(bad: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_spec(bad)


def test_our_lists_match_what_yt_dlp_supports() -> None:
    assert set(BROWSERS) <= set(SUPPORTED_BROWSERS)
    assert {k.upper() for k in KEYRINGS} == set(SUPPORTED_KEYRINGS)
    assert set(_PASSWORD_STORES.values()) <= set(KEYRINGS)
    assert set(_FLAG_FILES) <= set(BROWSERS)


def test_browser_of_strips_keyring() -> None:
    assert browser_of("brave+kwallet6") == "brave"
    assert browser_of("firefox") == "firefox"


def test_uses_keyring_only_for_chromium_without_explicit_choice() -> None:
    assert uses_keyring("brave")
    assert not uses_keyring("brave+kwallet6")
    assert not uses_keyring("firefox")
    assert not uses_keyring("safari")


@pytest.mark.parametrize(
    ("store", "keyring"),
    [
        ("gnome-libsecret", "gnomekeyring"),
        ("gnome-keyring", "gnomekeyring"),
        ("gnome", "gnomekeyring"),
        ("kwallet", "kwallet"),
        ("kwallet5", "kwallet5"),
        ("kwallet6", "kwallet6"),
        ("basic", "basictext"),
    ],
)
def test_password_store_maps_to_keyring(tmp_path: Path, store: str, keyring: str) -> None:
    write_flags(tmp_path, f"--password-store={store}\n")
    assert detect_keyring("brave") == keyring


def test_detection_without_flags_file_defers_to_yt_dlp(tmp_path: Path) -> None:
    assert detect_keyring("brave") is None
    assert ydl_cookies("brave") == ("brave",)


@pytest.mark.parametrize(
    "content",
    [
        "--enable-features=Foo\n",
        "# --password-store=gnome-libsecret\n",
        "--password-store=detect\n",
        "--password-store=\n",
        "--flag='unterminated\n",
    ],
)
def test_flags_without_a_usable_store_are_ignored(tmp_path: Path, content: str) -> None:
    write_flags(tmp_path, content)
    assert detect_keyring("brave") is None


def test_flag_found_among_others_on_the_same_line(tmp_path: Path) -> None:
    write_flags(tmp_path, "--foo --password-store=kwallet6 --bar\n")
    assert detect_keyring("brave") == "kwallet6"


def test_quoted_flag_value(tmp_path: Path) -> None:
    write_flags(tmp_path, '--password-store="gnome-libsecret"\n')
    assert detect_keyring("brave") == "gnomekeyring"


def test_last_flag_wins_like_chromium(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=basic\n--password-store=kwallet6\n")
    assert detect_keyring("brave") == "kwallet6"


def test_second_flags_file_is_tried(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=basic\n", "brave-browser-flags.conf")
    assert detect_keyring("brave") == "basictext"


def test_flags_file_of_another_browser_is_not_used(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=basic\n", "chromium-flags.conf")
    assert detect_keyring("brave") is None
    assert detect_keyring("chromium") == "basictext"


def test_browsers_without_keyring_are_never_detected(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=basic\n", "firefox-flags.conf")
    assert detect_keyring("firefox") is None


def test_unreadable_flags_file_is_ignored(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "brave-flags.conf").write_bytes(b"\xff\xfe\xff")
    assert detect_keyring("brave") is None


def test_falls_back_to_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config" / "brave-flags.conf").write_text("--password-store=basic\n")
    assert detect_keyring("brave") == "basictext"


def test_detected_keyring_reaches_yt_dlp(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=gnome-libsecret\n")
    assert ydl_cookies("brave") == ("brave", None, "GNOMEKEYRING", None)


def test_explicit_keyring_beats_detection(tmp_path: Path) -> None:
    write_flags(tmp_path, "--password-store=gnome-libsecret\n")
    assert ydl_cookies("brave+kwallet6") == ("brave", None, "KWALLET6", None)


@pytest.mark.parametrize("keyring", KEYRINGS)
def test_yt_dlp_accepts_the_tuple(keyring: str) -> None:
    parsed = _parse_browser_specification(*ydl_cookies(f"brave+{keyring}"))
    assert parsed == ("brave", None, keyring.upper(), None)
