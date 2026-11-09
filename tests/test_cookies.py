"""Tests for the --cookies spec and keyring detection."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from yt_dlp.cookies import (
    SUPPORTED_BROWSERS,
    SUPPORTED_KEYRINGS,
    CookieLoadError,
    _parse_browser_specification,
)
from yt_dlp.utils import DownloadError

from download_media.config import BROWSERS
from download_media.cookies import (
    _FLAG_FILES,
    _PASSWORD_STORES,
    KEYRINGS,
    CookieFailure,
    browser_of,
    cookie_failure,
    detect_keyring,
    parse_spec,
    report_failure,
    uses_keyring,
    ydl_cookies,
)
from download_media.i18n import t


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


def load_error(cause: Exception) -> CookieLoadError:
    """What yt-dlp's load_cookies raises: the real cause is the context."""
    try:
        try:
            raise cause
        except Exception:
            raise CookieLoadError("failed to load cookies")  # noqa: B904
    except CookieLoadError as error:
        return error


NOT_INSTALLED = 'could not find chrome cookies database in "x"'


def test_a_browser_that_is_not_installed_is_missing() -> None:
    error = load_error(FileNotFoundError(NOT_INSTALLED))
    assert cookie_failure(error) == CookieFailure(missing=True, reason=NOT_INSTALLED)


def test_without_ignoreerrors_the_failure_comes_inside_a_download_error() -> None:
    error = load_error(FileNotFoundError(NOT_INSTALLED))
    wrapped = DownloadError("ERROR: " + NOT_INSTALLED, (CookieLoadError, error, None))
    assert cookie_failure(wrapped) == CookieFailure(missing=True, reason=NOT_INSTALLED)


def test_any_other_cause_is_unreadable_with_its_reason() -> None:
    error = load_error(ValueError("unsupported platform: linux"))
    assert cookie_failure(error) == CookieFailure(
        missing=False, reason="unsupported platform: linux"
    )


def test_an_unreachable_keyring_is_unreadable() -> None:
    error = load_error(FileNotFoundError(2, "No such file or directory"))
    assert cookie_failure(error) == CookieFailure(
        missing=False, reason="[Errno 2] No such file or directory"
    )


def test_the_reason_is_one_short_line() -> None:
    error = load_error(RuntimeError("first line " + "x" * 300 + "\nsecond line"))
    failure = cookie_failure(error)
    assert failure is not None
    assert "\n" not in failure.reason
    assert "second line" not in failure.reason
    assert len(failure.reason) <= 160


def test_a_short_cause_on_two_lines_keeps_only_the_first() -> None:
    error = load_error(RuntimeError("cannot open keyring\nsee the man page"))
    assert cookie_failure(error) == CookieFailure(missing=False, reason="cannot open keyring")


def test_a_load_error_without_a_cause_still_counts() -> None:
    failure = cookie_failure(CookieLoadError("failed to load cookies"))
    assert failure == CookieFailure(missing=False, reason="failed to load cookies")


@pytest.mark.parametrize(
    "exc",
    [
        DownloadError("ERROR: HTTP Error 404: Not Found"),
        DownloadError("ERROR: boom", (ValueError, ValueError("boom"), None)),
        FileNotFoundError(NOT_INSTALLED),
        ValueError("unsupported platform: linux"),
    ],
)
def test_anything_else_is_not_a_cookie_failure(exc: Exception) -> None:
    assert cookie_failure(exc) is None


SECRETSTORAGE = (
    "secretstorage not available as the `secretstorage` module is not installed."
    " Please install by running `python3 -m pip install secretstorage`"
)


@pytest.mark.parametrize(
    ("said", "reason"),
    [
        # How report_error hands the cause to load_cookies: labelled, coloured on a terminal.
        (f"ERROR: {SECRETSTORAGE}", SECRETSTORAGE),
        ("\x1b[0;31mERROR:\x1b[0m failed to read from keyring", "failed to read from keyring"),
        ("ERROR: ERROR: invalid key", "invalid key"),
    ],
    ids=["plain", "coloured", "twice"],
)
def test_a_labelled_cause_loses_its_label(said: str, reason: str) -> None:
    assert cookie_failure(load_error(DownloadError(said))) == CookieFailure(False, reason)


def test_a_labelled_secretstorage_cause_still_gets_its_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = cookie_failure(load_error(DownloadError(f"ERROR: {SECRETSTORAGE}")))
    assert failure is not None
    report_failure("chrome", failure)
    said = capsys.readouterr().out
    assert t("cookies_unreadable", "chrome", SECRETSTORAGE) in said
    assert "sudo pacman -S python-secretstorage" in said


def test_a_label_in_the_middle_of_the_reason_stays() -> None:
    error = load_error(RuntimeError("cannot open keyring: ERROR: denied"))
    assert cookie_failure(error) == CookieFailure(False, "cannot open keyring: ERROR: denied")


SECRETSTORAGE_MISSING = "secretstorage not available No module named 'secretstorage'"


@pytest.mark.parametrize(
    ("spec", "failure", "lines"),
    [
        # Nothing to decrypt: never a keyring hint, even for a browser that uses one.
        ("chrome", CookieFailure(True, NOT_INSTALLED), ["cookies_no_browser"]),
        ("chrome", CookieFailure(False, "boom"), ["cookies_unreadable", "cookies_keyring_hint"]),
        ("chrome+gnomekeyring", CookieFailure(False, "boom"), ["cookies_unreadable"]),
        ("firefox", CookieFailure(False, "boom"), ["cookies_unreadable"]),
        ("chrome", CookieFailure(False, SECRETSTORAGE_MISSING), ["cookies_unreadable", "pacman"]),
    ],
)
def test_the_report_adds_the_hint_that_fits_the_cause(
    capsys: pytest.CaptureFixture[str], spec: str, failure: CookieFailure, lines: list[str]
) -> None:
    browser = browser_of(spec)
    report_failure(spec, failure)
    said = capsys.readouterr().out
    expected = {
        "cookies_no_browser": t("cookies_no_browser", browser),
        "cookies_unreadable": t("cookies_unreadable", browser, failure.reason),
        "cookies_keyring_hint": t("cookies_keyring_hint", browser),
        "pacman": "sudo pacman -S python-secretstorage",
    }
    for key, text in expected.items():
        assert (text in said) == (key in lines), key
