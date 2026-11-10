"""End-to-end runs through ``cli.main`` with real yt-dlp and ffmpeg, loopback only."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import types
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.aes import aes_cbc_encrypt_bytes
from yt_dlp.cookies import pbkdf2_sha1
from yt_dlp.utils import ReExtractInfo

from download_media import cli, i18n, wizard
from download_media.config import AUDIO_FORMATS, BROWSERS, history_file
from download_media.cookies import CookieFailure, ydl_cookies
from download_media.downloader import (
    CookiesUnavailable,
    _source_url,
    _TagSource,
    _Ydl,
    load_browser_cookies,
)
from download_media.i18n import t
from download_media.probe import ContentKind, Probe

from .conftest import (
    FLAKY_SEEN,
    GATE_REQUESTS,
    GEO_BLOCK,
    HTTP_404,
    JUNK,
    NO_EXT_VIDEO,
    OGA_VIDEO,
    WEBA,
    FakeSiteIE,
)

CAROUSEL = "https://fake.test/carousel"
LIST_URL = "https://fake.test/carousel?list=1"  # Looks multi-entry, so direct mode asks.
EMPTY = "https://fake.test/empty"
PRIVATE = "https://fake.test/private"
VIDEO = "https://fake.test/video"
VIDEO_B = "https://fake.test/video-b"
BOTH_ITEMS = ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]
# URL, type, quality, container, folder, cookies.
SINGLE_WALK = ("",) * 6
# URL, item selection, type, quality, container, folder, cookies.
PICKER_WALK = ("",) * 7


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def history_rows() -> list[str]:
    log = history_file()
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def commands(output: str) -> list[str]:
    """The equivalent commands a run printed."""
    lines = (line.strip() for line in output.splitlines())
    return [line for line in lines if line.startswith("download-media ")]


def streams(path: Path) -> set[str]:
    """The stream types ffprobe finds in ``path``."""
    probe = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]
    return set(subprocess.run([*probe, str(path)], capture_output=True, text=True).stdout.split())


def detected(kind: str) -> str:
    """The detection banner, since a bare "Video" also matches the output folder."""
    return f"generic / {t(kind)}"


def tag(kind: str) -> str:
    """How the item picker labels an item."""
    return f"[{t(kind).lower()}]"


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "out"


@pytest.fixture
def run(
    out: Path, fake_site: list[Any], capfd: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, str]]:
    def _run(*argv: str) -> tuple[int, str]:
        code = cli.main(["-o", str(out), *argv])
        captured = capfd.readouterr()
        return code, captured.out + captured.err

    return _run


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default output folders under ``tmp_path``, for runs that can't use ``-o``.

    ``-o`` is an intent flag, so the ``run`` fixture hides whether a command
    stays out of the wizard on its own.
    """
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture
def pasted(capfd: pytest.CaptureFixture[str], answers: Any) -> Callable[..., tuple[int, str]]:
    """Run a printed command as pasted, optionally into another folder.

    Nothing is scripted, so any prompt fails the test.
    """

    def _replay(line: str, folder: Path | None = None) -> tuple[int, str]:
        argv = shlex.split(line)[1:]
        assert cli.should_run_wizard(cli.build_parser().parse_args(argv), argv) is False
        if folder is not None:
            argv[argv.index("-o") + 1] = str(folder)
        answers()
        code = cli.main(argv)
        captured = capfd.readouterr()
        return code, captured.out + captured.err

    return _replay


@pytest.fixture
def replay(fake_site: list[Any], pasted: Any) -> Callable[..., tuple[int, str]]:
    return pasted


@pytest.fixture
def generic(
    media_url: str, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, str]]:
    """Run a loopback page through yt-dlp's real generic extractor.

    ``fake_site`` loads only ``FakeSiteIE``, and ``-o`` would leave the wizard.
    """

    def _generic(page: str, *replies: str, flags: tuple[str, ...] = ()) -> tuple[int, str]:
        prompts = answers(*replies)
        code = cli.main([*flags, f"{media_url}/{page}"])
        captured = capfd.readouterr()
        assert len(prompts) == len(replies)  # A skipped step would go unnoticed.
        return code, captured.out + captured.err

    return _generic


@pytest.fixture
def again(tmp_path: Path) -> Path:
    return tmp_path / "again"


def test_single_video(run: Any, out: Path) -> None:
    code, output = run("-y", "https://fake.test/video")
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert t("done") in output
    assert t("partial_result", 1, 1, 0, 0) not in output
    assert len(history_rows()) == 1


def test_repeating_a_video_keeps_one_file(run: Any, out: Path) -> None:
    run("-y", "https://fake.test/video")
    code, _ = run("-y", "https://fake.test/video")
    assert code == 0
    assert names(out) == ["Clip.mp4"]


def test_different_videos_with_same_title_both_survive(run: Any, out: Path) -> None:
    code, _ = run("-y", "https://fake.test/video", "https://fake.test/video-b")
    assert code == 0
    assert names(out) == ["Clip [v2].mp4", "Clip.mp4"]


@pytest.mark.parametrize("audio_format", AUDIO_FORMATS)
def test_audio_extraction(run: Any, out: Path, audio_format: str) -> None:
    code, _ = run("-y", "-a", "-f", audio_format, "https://fake.test/video")
    assert code == 0
    assert names(out) == [f"Clip.{audio_format}"]


def test_carousel_with_yes_skips_images_and_keeps_every_video(run: Any, out: Path) -> None:
    code, output = run("-y", CAROUSEL)
    assert code == 0
    assert names(out) == ["Clip [v1].mp4", "Clip [v2].mp4"]
    assert t("partial_result", 2, 4, 2, 0) in output
    assert t("item_no_media", "i1") in output
    assert "ERROR" not in output


def test_carousel_with_playlist_flag_numbers_items(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]
    assert t("partial_result", 2, 4, 2, 0) in output


def test_carousel_with_no_playlist_flag_behaves_like_yes(run: Any, out: Path) -> None:
    code, _ = run("-y", "-P", CAROUSEL)
    assert code == 0
    assert names(out) == ["Clip [v1].mp4", "Clip [v2].mp4"]


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_repeating_a_carousel_adds_nothing_and_still_counts(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    run("-y", *flags, CAROUSEL)
    before = names(out)
    code, output = run("-y", *flags, CAROUSEL)
    assert code == 0
    assert names(out) == before
    assert t("partial_result", 2, 4, 2, 0) in output


def test_carousel_audio_only_skips_images(run: Any, out: Path) -> None:
    code, _ = run("-y", "-a", CAROUSEL)
    assert code == 0
    assert names(out) == ["Clip [v1].mp3", "Clip [v2].mp3"]


def test_playlist_item_selection_is_respected(out: Path, fake_site: list[Any]) -> None:
    from download_media.config import DownloadOptions
    from download_media.downloader import download

    opts = DownloadOptions(
        urls=[CAROUSEL], output=out, playlist_mode="yes", playlist_items="4", no_confirm=True
    )
    result = download(CAROUSEL, opts)
    assert (result.saved, result.skipped, result.failed) == (1, 0, 0)
    assert names(out) == ["04 - Clip [v2].mp4"]


def test_playlist_items_flag_skips_the_wizard(
    home: Path, fake_site: list[Any], answers: Any
) -> None:
    answers()
    assert cli.main(["--playlist-items", "2", CAROUSEL]) == 0
    assert names(home / "Videos") == ["02 - Clip [v1].mp4"]


def test_playlist_items_flag_can_pick_several(run: Any, out: Path) -> None:
    code, _ = run("-y", "--playlist-items", "2,4", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]


@pytest.mark.parametrize("flags", [("-p",), ("--playlist-items", "2")])
def test_a_plain_video_in_playlist_mode_keeps_its_single_mode_name(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    code, output = run("-y", *flags, VIDEO)
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert t("done") in output


def test_plain_videos_in_playlist_mode_still_avoid_a_name_clash(run: Any, out: Path) -> None:
    assert run("-y", "-p", VIDEO)[0] == 0
    assert run("-y", "-p", VIDEO_B)[0] == 0
    assert names(out) == ["Clip [v2].mp4", "Clip.mp4"]


def test_whole_playlist_answer_on_a_plain_video_keeps_its_name(
    run: Any, out: Path, answers: Any
) -> None:
    answers("2")
    code, _ = run(f"{VIDEO}?list=1")
    assert code == 0
    assert names(out) == ["Clip.mp4"]


def test_playlist_items_out_of_range_is_a_failure(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "9", CAROUSEL)
    assert code == 1
    assert names(out) == []
    assert t("items_not_found", 4, "9") in output
    assert t("failed", CAROUSEL) in output
    assert t("no_supported_items") not in output
    assert t("done") not in output
    assert history_rows() == []


def test_playlist_items_names_the_missing_item_before_done(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "2,9", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    note = t("items_not_found", 4, "9")
    assert note in output
    assert output.index(note) < output.index(t("done"))
    assert len(history_rows()) == 1
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == "2,9"


def test_playlist_items_names_each_segment_that_selects_nothing(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items=2,-7,5-9,3-9,-1", CAROUSEL)
    assert code == 0
    assert names(out) == BOTH_ITEMS
    assert t("items_not_found", 4, "-7,5-9") in output


def test_playlist_items_counts_the_outermost_list(run: Any, out: Path) -> None:
    # Inner lists are filtered too, silently (roadmap row 32); only the outer one is checked.
    code, output = run("-y", "--playlist-items", "1,3", "https://fake.test/nested-gated")
    assert code == 0
    assert t("items_not_found", 1, "3") in output
    assert names(out) == ["01 - Clip [v2].mp4"]


def test_playlist_items_on_an_empty_playlist_has_no_note(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "1", EMPTY)
    assert code == 1
    assert t("no_supported_items") in output
    assert t("items_not_found", 0, "1").split("(")[0] not in output


def not_found_notes(output: str) -> list[str]:
    """Every items_not_found note the run printed, whatever its numbers."""
    prefix = t("items_not_found", 0, "").split("(")[0]
    return [line.strip() for line in output.splitlines() if line.strip().startswith(prefix)]


def test_playlist_items_zero_is_named_as_missing(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "0", CAROUSEL)
    assert code == 1
    assert not_found_notes(output) == [t("items_not_found", 4, "0")]
    assert t("failed", CAROUSEL) in output
    assert t("no_supported_items") not in output
    assert names(out) == []


def test_missing_item_next_to_a_skipped_image_keeps_the_image_reason(run: Any, out: Path) -> None:
    # Item 1 is an image: something was skipped, so "failed" would hide the real reason.
    code, output = run("-y", "--playlist-items", "1,9", CAROUSEL)
    assert code == 1
    assert not_found_notes(output) == [t("items_not_found", 4, "9")]
    assert t("no_supported_items") in output
    assert t("failed", CAROUSEL) not in output
    assert t("done") not in output
    assert names(out) == []
    assert history_rows() == []


def test_missing_items_of_one_url_are_not_reported_for_the_next(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "2,9", CAROUSEL, VIDEO)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4", "Clip.mp4"]
    assert not_found_notes(output) == [t("items_not_found", 4, "9")]
    assert output.count(t("done")) == 2
    assert len(history_rows()) == 2


def test_each_url_reports_its_own_missing_items(run: Any, out: Path) -> None:
    partial = "https://fake.test/partial-gated"  # video, image, gated: 3 items.
    code, output = run("-y", "--playlist-items", "1,4", partial, CAROUSEL)
    assert code == 0
    assert names(out) == ["01 - Clip [v2].mp4", "04 - Clip [v2].mp4"]
    assert not_found_notes(output) == [t("items_not_found", 3, "4")]


def test_rerun_with_missing_items_keeps_the_note_and_the_files(
    run: Any, replay: Any, out: Path, again: Path
) -> None:
    note = t("items_not_found", 4, "9")
    assert run("-y", "--playlist-items", "2,9", CAROUSEL)[0] == 0
    code, output = run("-y", "--playlist-items", "2,9", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    assert not_found_notes(output) == [note]
    assert output.index(note) < output.index(t("done"))
    assert len(history_rows()) == 2
    [line] = commands(output)
    code, output = replay(line, again)
    assert code == 0
    assert names(again) == names(out)
    assert not_found_notes(output) == [note]


def test_missing_items_of_a_lazy_list_are_named_once_it_is_read_to_the_end(
    run: Any, out: Path
) -> None:
    code, output = run("-y", "--playlist-items", "2,9", "https://fake.test/lazy-carousel")
    assert code == 0
    assert names(out) == ["02 - Clip [v3].mp4"]
    assert not_found_notes(output) == [t("items_not_found", 2, "9")]


def test_lazy_list_not_read_to_the_end_has_no_note(run: Any, out: Path) -> None:
    # yt-dlp never learns the size here: nothing can be named, and nothing is missing.
    code, output = run("-y", "--playlist-items", "1", "https://fake.test/lazy-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [v2].mp4"]
    assert not_found_notes(output) == []
    assert t("done") in output


@pytest.mark.parametrize(
    ("page", "spec", "saved", "notes"),
    [
        # Header says 3, item 3 is hidden: yt-dlp drops it, and nothing names it.
        ("hidden-items", "2,3", ["02 - Clip [v2].mp4"], ["3"]),
        # Header says 2, the list holds 3: item 3 is saved, and the note says it was skipped.
        ("stale-count", "3", ["03 - Clip [v3].mp4"], []),
        # The same header on a paged list: asking for item 3 reads it to the end.
        ("lazy-hidden", "2,3", ["02 - Clip [v3].mp4"], ["3"]),
        # Not read to the end: the size is unknown, and nothing requested is missing.
        ("lazy-hidden", "1", ["01 - Clip [v2].mp4"], []),
        # A multi_video result declares its count the same way.
        ("multi-hidden", "2,3", ["02 - Clip [v2].mp4"], ["3"]),
    ],
)
def test_missing_items_follow_the_entries_not_the_declared_count(
    run: Any, out: Path, page: str, spec: str, saved: list[str], notes: list[str]
) -> None:
    code, output = run("-y", "--playlist-items", spec, f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == saved
    assert [note.rsplit(" ", 1)[-1] for note in not_found_notes(output)] == notes
    assert t("done") in output


@pytest.mark.parametrize("page", ["hidden-items", "lazy-hidden", "multi-hidden"])
def test_missing_items_note_counts_the_entries_read(run: Any, out: Path, page: str) -> None:
    code, output = run("-y", "--playlist-items", "2,3", f"https://fake.test/{page}")
    assert code == 0
    assert not_found_notes(output) == [t("items_not_found", 2, "3")]


def test_rerun_of_a_plain_video_in_playlist_mode_keeps_one_file(run: Any, out: Path) -> None:
    assert run("-y", "-p", VIDEO)[0] == 0
    code, output = run("-y", "-p", VIDEO)
    assert code == 0
    assert t("done") in output
    assert names(out) == ["Clip.mp4"]
    # The same file as without -p: no second copy of the same video.
    assert run("-y", VIDEO)[0] == 0
    assert names(out) == ["Clip.mp4"]


def test_plain_audio_in_playlist_mode_keeps_its_name_and_avoids_a_clash(
    run: Any, out: Path
) -> None:
    assert run("-y", "-p", "-a", VIDEO)[0] == 0
    assert names(out) == ["Clip.mp3"]
    assert run("-y", "-p", "-a", VIDEO_B)[0] == 0
    assert names(out) == ["Clip [v2].mp3", "Clip.mp3"]


def test_missing_items_with_audio_are_reported_the_same(run: Any, out: Path) -> None:
    code, output = run("-y", "-a", "--playlist-items", "2,9", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp3"]
    assert not_found_notes(output) == [t("items_not_found", 4, "9")]


def test_playlist_items_with_a_bad_spec_is_a_clean_failure(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "x", CAROUSEL)
    assert code == 1
    assert t("failed", CAROUSEL) in output
    assert "Traceback" not in output
    assert t("done") not in output
    assert names(out) == []


def test_playlist_items_on_an_image_saves_nothing_and_says_so(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "1", CAROUSEL)
    assert code == 1
    assert names(out) == []
    assert t("no_supported_items") in output


@pytest.mark.parametrize(
    ("flags", "url"),
    [
        ((), "https://fake.test/video"),
        (("-a",), "https://fake.test/video"),
        (("--video-format", "mkv"), "https://fake.test/video"),
        (("-a",), CAROUSEL),
        (("-a", "-p"), CAROUSEL),
        (("--playlist-items", "2"), CAROUSEL),
        (("-a", "--playlist-items", "2"), CAROUSEL),
    ],
)
def test_repeating_a_run_is_still_a_success(
    run: Any, out: Path, flags: tuple[str, ...], url: str
) -> None:
    # Files already on disk must count as saved: "nothing saved" means failure.
    run("-y", *flags, url)
    before = names(out)
    code, output = run("-y", *flags, url)
    assert code == 0
    assert names(out) == before
    assert t("no_supported_items") not in output
    assert len(history_rows()) == 2


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_carousel_of_only_images_is_a_failure(run: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = run("-y", *flags, "https://fake.test/only-images")
    assert code == 1
    assert names(out) == []
    assert t("no_supported_items") in output
    assert t("done") not in output
    assert history_rows() == []


def test_lone_image_is_an_error_not_a_skip(run: Any, out: Path) -> None:
    code, output = run("-y", "https://fake.test/image")
    assert code == 1
    assert "No video formats found" in output
    assert t("done") not in output


def test_playlist_keeps_going_past_a_broken_item(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "https://fake.test/broken-item")
    assert code == 0
    assert names(out) == ["01 - Clip [v1].mp4"]
    assert t("partial_result", 1, 2, 0, 1) in output
    assert len(history_rows()) == 1


def test_single_mode_stops_at_a_broken_item(run: Any) -> None:
    code, output = run("-y", "https://fake.test/broken-item")
    assert code == 1
    assert t("done") not in output


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_total_failure_is_reported(run: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = run("-y", *flags, "https://fake.test/private")
    assert code == 1
    assert "This post is private" in output
    assert t("failed", "https://fake.test/private") in output
    assert t("done") not in output
    assert names(out) == []
    assert history_rows() == []
    assert len(commands(output)) == 1


def test_playlist_where_every_item_breaks_is_a_failure(run: Any) -> None:
    code, output = run("-y", "-p", "https://fake.test/all-broken")
    assert code == 1
    assert t("done") not in output
    assert history_rows() == []


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_cookie_fallback_recovers(
    run: Any, out: Path, fake_site: list[Any], flags: tuple[str, ...]
) -> None:
    code, output = run("-y", *flags, "--cookies", "brave", "https://fake.test/rejects-cookies")
    assert code == 0
    assert len(names(out)) == 1
    assert t("cookies_retry", "brave") in output
    assert fake_site[-1] is None
    assert "--cookies" not in output.strip().splitlines()[-1]


def test_cookies_unlock_a_login_walled_post(run: Any, out: Path) -> None:
    code, output = run("-y", "--cookies", "firefox", "https://fake.test/needs-cookies")
    assert code == 0
    assert t("cookies_retry", "firefox") not in output
    assert "--cookies firefox" in output.strip().splitlines()[-1]


def test_failure_with_cookies_reports_original_error_and_keyring_hint(run: Any) -> None:
    code, output = run("-y", "--cookies", "brave", "https://fake.test/private")
    assert code == 1
    assert t("cookies_retry", "brave") in output
    assert t("cookies_keyring_hint", "brave") in output


def test_one_failed_url_fails_the_run_but_keeps_the_rest(run: Any, out: Path) -> None:
    code, _ = run("-y", "https://fake.test/video", "https://fake.test/private")
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert len(history_rows()) == 1


def test_list_formats_downloads_nothing(run: Any, out: Path) -> None:
    code, output = run("-y", "-l", "https://fake.test/video")
    assert code == 0
    assert "main" in output
    assert names(out) == []
    assert t("equiv_cmd") not in output


def test_list_formats_on_a_carousel_skips_images(run: Any, out: Path) -> None:
    code, output = run("-y", "-l", CAROUSEL)
    assert code == 0
    assert "Available formats for v1" in output
    assert "Available formats for v2" in output
    assert t("item_no_media", "i1") in output


def test_list_formats_failure_exits_cleanly(run: Any) -> None:
    code, output = run("-y", "-l", "https://fake.test/private")
    assert code == 1
    assert t("failed", "https://fake.test/private") in output


def _flags_file(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "brave-flags.conf").write_text("--password-store=gnome-libsecret\n")


def test_detected_keyring_reaches_download(run: Any, fake_site: list[Any], tmp_path: Path) -> None:
    _flags_file(tmp_path)
    run("-y", "--cookies", "brave", "https://fake.test/needs-cookies")
    assert fake_site == [("brave", None, "GNOMEKEYRING", None)]


def test_detected_keyring_reaches_list_formats(
    run: Any, fake_site: list[Any], tmp_path: Path
) -> None:
    _flags_file(tmp_path)
    run("-y", "-l", "--cookies", "brave", "https://fake.test/needs-cookies")
    assert fake_site == [("brave", None, "GNOMEKEYRING", None)]


def test_detected_keyring_reaches_preview(
    run: Any, fake_site: list[Any], tmp_path: Path, answers: Any
) -> None:
    _flags_file(tmp_path)
    answers("")
    run("--cookies", "brave", "https://fake.test/needs-cookies")
    assert fake_site == [("brave", None, "GNOMEKEYRING", None)] * 2


def test_explicit_keyring_survives_into_the_equivalent_command(run: Any) -> None:
    _, output = run("-y", "--cookies", "brave+kwallet6", "https://fake.test/needs-cookies")
    assert "--cookies brave+kwallet6" in output.strip().splitlines()[-1]


def test_direct_mode_preview_shows_metadata(run: Any, out: Path, answers: Any) -> None:
    answers("")
    code, output = run("https://fake.test/video")
    assert code == 0
    assert "Clip" in output
    assert names(out) == ["Clip.mp4"]


def test_direct_mode_preview_can_be_declined(run: Any, out: Path, answers: Any) -> None:
    answers("n")
    code, output = run("https://fake.test/video")
    assert code == 1
    assert t("cancelled") in output
    assert names(out) == []
    assert t("equiv_cmd") not in output


def test_direct_mode_preview_survives_unreachable_url(run: Any, answers: Any) -> None:
    answers("n")
    code, _ = run("https://fake.test/private")
    assert code == 1


def test_direct_mode_preview_treats_eof_as_cancel(run: Any, answers: Any, out: Path) -> None:
    answers(EOFError)
    code, _ = run("https://fake.test/video")
    assert code == 1
    assert names(out) == []


def test_wizard_on_a_carousel_downloads_the_videos(run: Any, out: Path, answers: Any) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "", "")
    code, output = run("-i", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]
    assert t("partial_result", 2, 2, 0, 0) not in output


def test_wizard_on_a_single_video(run: Any, out: Path, answers: Any) -> None:
    # URL, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "")
    code, _ = run("-i", "https://fake.test/video")
    assert code == 0
    assert names(out) == ["Clip.mp4"]


def test_wizard_cancelled_downloads_nothing(run: Any, out: Path, answers: Any) -> None:
    answers("", "c")
    code, output = run("-i", "https://fake.test/video")
    assert code == 0
    assert t("cancelled") in output
    assert names(out) == []
    assert t("equiv_cmd") not in output


def test_wizard_selection_replaces_the_playlist_items_flag(
    run: Any, out: Path, answers: Any
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "4", "", "", "", "", "")
    code, _ = run("-i", "--playlist-items", "2", CAROUSEL)
    assert code == 0
    assert names(out) == ["04 - Clip [v2].mp4"]


def test_wizard_picker_preselects_the_playlist_items_flag(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    prompts = answers(*PICKER_WALK)
    code, output = run("-i", "--playlist-items", "2", CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    assert t("ask_selection_default_preset", "2") in prompts
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == "2"
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_wizard_picker_preset_out_of_range_is_reported(run: Any, out: Path, answers: Any) -> None:
    answers(*PICKER_WALK)
    code, output = run("-i", "--playlist-items", "9", CAROUSEL)
    assert code == 1
    assert t("items_not_found", 4, "9") in output
    assert names(out) == []


def test_wizard_picker_preset_reaches_every_url(run: Any, out: Path, answers: Any) -> None:
    prompts = answers(*PICKER_WALK, *PICKER_WALK)
    other = "https://fake.test/rejects-cookies-carousel"
    code, _ = run("-i", "--playlist-items", "4", CAROUSEL, other)
    assert code == 0
    assert names(out) == ["04 - Clip [v2].mp4"]
    preset = t("ask_selection_default_preset", "4")
    assert prompts.count(preset) == 2


@pytest.mark.parametrize(
    ("replies", "items", "saved"),
    [
        # Back at the picker, the first step after the probe: it asks again, Enter keeps 2.
        (("", "b", *PICKER_WALK[1:]), "2", "02 - Clip [v1].mp4"),
        # Back from the type step to the picker, then a typed answer replaces the preset.
        (("", "", "b", "4", *PICKER_WALK[2:]), "4", "04 - Clip [v2].mp4"),
        # Back from the type step, then Enter: still the command line's value.
        (("", "", "b", *PICKER_WALK[1:]), "2", "02 - Clip [v1].mp4"),
    ],
    ids=["back-at-picker", "back-then-typed", "back-then-enter"],
)
def test_wizard_picker_preset_survives_going_back(
    run: Any, out: Path, answers: Any, replies: tuple[str, ...], items: str, saved: str
) -> None:
    prompts = answers(*replies)
    code, output = run("-i", "--playlist-items", "2", CAROUSEL)
    assert code == 0
    assert names(out) == [saved]
    assert prompts.count(t("ask_selection_default_preset", "2")) == 2
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == items


def test_wizard_typed_selection_does_not_become_the_next_urls_preset(
    run: Any, out: Path, answers: Any
) -> None:
    other = "https://fake.test/rejects-cookies-carousel"
    prompts = answers("", "4", *PICKER_WALK[2:], *PICKER_WALK)
    code, output = run("-i", "--playlist-items", "2", CAROUSEL, other)
    assert code == 0
    assert names(out) == BOTH_ITEMS
    assert prompts.count(t("ask_selection_default_preset", "2")) == 2
    assert t("ask_selection_default_preset", "4") not in prompts
    items = [shlex.split(line) for line in commands(output)]
    assert sorted(argv[argv.index("--playlist-items") + 1] for argv in items) == ["2", "4"]


@pytest.mark.parametrize(
    ("lang", "prompt"),
    [
        ("es", "Selecciona items (Enter = 2, 'all' = todo intentable, c = cancelar): "),
        ("en", "Pick items (Enter = 2, 'all' = try everything, c = cancel): "),
    ],
)
def test_wizard_picker_preset_prompt_in_both_languages(
    run: Any, answers: Any, monkeypatch: pytest.MonkeyPatch, lang: str, prompt: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    prompts = answers(*PICKER_WALK)
    assert run("-i", "--playlist-items", "2", CAROUSEL)[0] == 0
    assert prompt in prompts


def test_detected_keyring_reaches_wizard_probe(
    run: Any, fake_site: list[Any], tmp_path: Path, answers: Any
) -> None:
    _flags_file(tmp_path)
    # URL, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "")
    run("-i", "--cookies", "brave", "https://fake.test/needs-cookies")
    assert set(fake_site) == {("brave", None, "GNOMEKEYRING", None)}


def test_wizard_with_nothing_supported_prints_no_command(run: Any, answers: Any) -> None:
    answers("")
    code, output = run("-i", "https://fake.test/only-images")
    assert code == 0
    assert t("equiv_cmd") not in output


def test_wizard_on_all_defaults_repeats_without_a_prompt(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_site: list[Any],
    capfd: pytest.CaptureFixture[str],
    answers: Any,
    replay: Any,
) -> None:
    # URL, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "")
    assert cli.main(["https://fake.test/video"]) == 0
    [line] = commands(capfd.readouterr().out)
    assert line == "download-media -y https://fake.test/video"
    assert names(home / "Videos") == ["Clip.mp4"]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = replay(line)
    assert code == 0
    assert names(elsewhere / "Videos") == ["Clip.mp4"]


@pytest.mark.parametrize(
    "argv",
    [
        ("--cookies", "brave", "-P", "https://fake.test/rejects-cookies"),
        ("-q", "1080", "-P", "https://fake.test/video"),
    ],
)
def test_run_whose_flags_are_all_omitted_stays_direct(
    home: Path,
    fake_site: list[Any],
    capfd: pytest.CaptureFixture[str],
    replay: Any,
    argv: tuple[str, ...],
) -> None:
    assert cli.main(list(argv)) == 0
    [line] = commands(capfd.readouterr().out)
    assert line == f"download-media -y {argv[-1]}"
    (home / "Videos" / "Clip.mp4").unlink()
    code, _ = replay(line)
    assert code == 0
    assert names(home / "Videos") == ["Clip.mp4"]


@pytest.mark.parametrize(
    ("flags", "kept"),
    [
        ((), ()),
        (("-q", "720"), ("-q", "720")),
        (("-a",), ("-a",)),
        (("-a", "-f", "m4a"), ("-a", "-f", "m4a")),
        (("--video-format", "mkv"), ("--video-format", "mkv")),
    ],
)
def test_yes_run_repeats_with_its_flags(
    run: Any, replay: Any, out: Path, again: Path, flags: tuple[str, ...], kept: tuple[str, ...]
) -> None:
    _, output = run("-y", *flags, "https://fake.test/video")
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-y" in argv
    assert all(flag in argv for flag in kept)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_preview_accepted_repeats_without_asking_again(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers("")
    _, output = run("https://fake.test/video")
    [line] = commands(output)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out) == ["Clip.mp4"]


def test_direct_answer_all_repeats_the_whole_playlist(
    run: Any, replay: Any, again: Path, answers: Any
) -> None:
    answers("2")
    code, output = run(LIST_URL)
    assert code == 0
    [line] = commands(output)
    assert "-p" in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]


def test_direct_answer_single_repeats_in_single_mode(
    run: Any, replay: Any, again: Path, answers: Any
) -> None:
    answers("1", "")
    code, output = run(LIST_URL)
    assert code == 0
    [line] = commands(output)
    assert "-P" in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip [v1].mp4", "Clip [v2].mp4"]


def test_yes_on_a_playlist_url_prints_the_mode_that_ran(
    run: Any, replay: Any, out: Path, again: Path
) -> None:
    _, output = run("-y", LIST_URL)
    [line] = commands(output)
    assert "-P" in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize(("flag", "kept"), [("-p", "-p"), ("-P", "-P")])
def test_forced_playlist_flags_are_kept(run: Any, flag: str, kept: str) -> None:
    _, output = run(flag, LIST_URL)
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-y" in argv
    assert kept in argv


def test_urls_in_different_modes_get_one_command_each(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers("2", "")
    code, output = run(LIST_URL, "https://fake.test/video")
    assert code == 0
    assert output.count(t("equiv_cmd")) == 1
    lines = commands(output)
    assert len(lines) == 2
    assert LIST_URL in shlex.split(lines[0])
    assert "-p" in shlex.split(lines[0])
    assert "https://fake.test/video" in shlex.split(lines[1])
    assert "-p" not in shlex.split(lines[1])
    for line in lines:
        code, _ = replay(line, again)
        assert code == 0
    assert names(again) == names(out)


def test_urls_in_the_same_mode_share_one_command(run: Any) -> None:
    urls = ("https://fake.test/video", "https://fake.test/video-b")
    _, output = run("-y", *urls)
    [line] = commands(output)
    assert shlex.split(line)[-2:] == list(urls)


@pytest.mark.parametrize(
    ("reply", "flags", "saved"),
    [
        ("2", ("--playlist-items", "2"), ["02 - Clip [v1].mp4"]),
        ("", ("--playlist-items", "2,4"), ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]),
        ("all", ("-p",), ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]),
    ],
)
def test_wizard_selection_repeats(
    run: Any,
    replay: Any,
    out: Path,
    again: Path,
    answers: Any,
    reply: str,
    flags: tuple[str, ...],
    saved: list[str],
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", reply, "", "", "", "", "")
    code, output = run("-i", CAROUSEL)
    assert code == 0
    assert names(out) == saved
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-i" not in argv
    assert " ".join(flags) in " ".join(argv)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


def test_wizard_after_a_failed_probe_prints_the_resolved_mode(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    # URL, type, quality, container, folder, cookies, then single or all.
    answers("", "", "", "", "", "", "2")
    code, output = run("-i", "--cookies", "brave", "https://fake.test/rejects-cookies?list=1")
    assert code == 0
    assert t("probe_falling_back") in output
    [line] = commands(output)
    assert "-p" in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_declined_url_is_left_out_of_the_command(run: Any, out: Path, answers: Any) -> None:
    answers("n", "")
    code, output = run("https://fake.test/video-b", "https://fake.test/video")
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    argv = shlex.split(line)
    assert "https://fake.test/video" in argv
    assert "https://fake.test/video-b" not in argv


def test_cancelled_playlist_question_prints_no_command(run: Any, answers: Any) -> None:
    answers(EOFError)
    code, output = run(LIST_URL)
    assert code == 1
    assert t("equiv_cmd") not in output


def test_failed_urls_stay_in_the_command(run: Any) -> None:
    urls = ("https://fake.test/video", "https://fake.test/private")
    code, output = run("-y", *urls)
    assert code == 1
    [line] = commands(output)
    assert shlex.split(line)[-2:] == list(urls)


def test_partial_failure_prints_the_command(run: Any) -> None:
    code, output = run("-y", "-p", "https://fake.test/broken-item")
    assert code == 0
    assert len(commands(output)) == 1


def test_shell_metacharacters_survive_a_replay(
    tmp_path: Path,
    fake_site: list[Any],
    capfd: pytest.CaptureFixture[str],
    replay: Any,
) -> None:
    url = "https://fake.test/video?a=b c&d='e'$f"
    folder = tmp_path / "it's a $dir & more"
    assert cli.main(["-y", "-o", str(folder), url]) == 0
    [line] = commands(capfd.readouterr().out)
    argv = shlex.split(line)
    assert argv[-1] == url
    assert argv[argv.index("-o") + 1] == str(folder)
    (folder / "Clip.mp4").unlink()
    code, _ = replay(line)
    assert code == 0
    assert names(folder) == ["Clip.mp4"]


@pytest.mark.parametrize(
    "flags", [(), ("-p",), ("--playlist-items", "1")], ids=["auto", "playlist", "items"]
)
def test_empty_playlist_is_a_failure(run: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = run("-y", *flags, EMPTY)
    assert code == 1
    assert names(out) == []
    assert t("no_supported_items") in output
    assert t("done") not in output
    assert history_rows() == []
    assert len(commands(output)) == 1


def test_empty_playlist_after_the_preview_is_a_failure(run: Any, out: Path, answers: Any) -> None:
    answers("")
    code, output = run(EMPTY)
    assert code == 1
    assert names(out) == []
    assert t("done") not in output
    assert history_rows() == []


def test_empty_playlist_in_the_wizard_is_a_failure(run: Any, out: Path, answers: Any) -> None:
    # URL, type, quality, container, folder, cookies, then the preview: an empty
    # playlist probes as unknown content, so the mode stays open until cli.py.
    answers("", "", "", "", "", "", "")
    code, output = run("-i", EMPTY)
    assert code == 1
    assert names(out) == []
    assert t("no_supported_items") in output
    assert t("done") not in output
    assert history_rows() == []


def test_empty_playlist_fails_the_run_but_keeps_the_rest(run: Any, out: Path) -> None:
    code, output = run("-y", VIDEO, EMPTY)
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("done")) == 1
    assert len(history_rows()) == 1
    [line] = commands(output)
    assert shlex.split(line)[-2:] == [VIDEO, EMPTY]


@pytest.mark.parametrize(
    ("flags", "urls"),
    [
        ((), ("https://fake.test/merged",)),
        (("--video-format", "mkv"), ("https://fake.test/merged",)),
        (("-a",), ("https://fake.test/merged",)),
        (("-s",), ("https://fake.test/subs",)),
        (("-P",), (LIST_URL,)),
        ((), (VIDEO, VIDEO_B)),
        (("-a",), (VIDEO, VIDEO_B)),
        (("-p",), ("https://fake.test/broken-item",)),
        (("--playlist-items", "2-4"), (CAROUSEL,)),
        (("--cookies", "brave"), ("https://fake.test/rejects-cookies",)),
        (("--cookies", "brave", "-p"), ("https://fake.test/rejects-cookies-carousel",)),
        (("--cookies", "firefox"), ("https://fake.test/needs-cookies",)),
    ],
)
def test_rerun_over_existing_files_is_not_a_false_failure(
    run: Any, out: Path, flags: tuple[str, ...], urls: tuple[str, ...]
) -> None:
    first, _ = run("-y", *flags, *urls)
    assert first == 0
    before = names(out)
    assert before
    code, output = run("-y", *flags, *urls)
    assert code == 0
    assert names(out) == before
    assert t("no_supported_items") not in output
    assert output.count(t("done")) == len(urls)
    assert len(history_rows()) == 2 * len(urls)


@pytest.mark.parametrize(
    "audio_format",
    [
        pytest.param(
            audio_format,
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="_source_url reads ffprobe's format tags, but ogg keeps them on the stream: "
                "the rerun takes the old file for another video and saves 'Clip [v1].opus'.",
            ),
        )
        if audio_format == "opus"
        else audio_format
        for audio_format in AUDIO_FORMATS
    ],
)
def test_audio_rerun_is_not_a_false_failure(run: Any, out: Path, audio_format: str) -> None:
    run("-y", "-a", "-f", audio_format, "https://fake.test/video")
    code, output = run("-y", "-a", "-f", audio_format, "https://fake.test/video")
    assert code == 0
    assert names(out) == [f"Clip.{audio_format}"]
    assert t("no_supported_items") not in output


def test_wizard_rerun_is_not_a_false_failure(
    run: Any, replay: Any, out: Path, answers: Any
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "", "")
    _, output = run("-i", CAROUSEL)
    [line] = commands(output)
    code, again_output = replay(line)
    assert code == 0
    assert names(out) == BOTH_ITEMS
    assert t("no_supported_items") not in again_output
    assert len(history_rows()) == 2


def test_cookie_retry_that_finds_nothing_is_a_failure(run: Any, out: Path) -> None:
    url = "https://fake.test/rejects-cookies-carousel"
    code, output = run("-y", "--cookies", "brave", "--playlist-items", "9", url)
    assert code == 1
    assert t("cookies_retry", "brave") in output
    assert t("items_not_found", 4, "9") in output
    assert t("failed", url) in output
    assert t("done") not in output
    assert names(out) == []
    assert history_rows() == []
    # Nothing shows the cookies were the problem, so the command keeps them.
    [line] = commands(output)
    assert "--cookies" in shlex.split(line)


def test_empty_playlist_with_cookies_is_a_failure(run: Any, out: Path) -> None:
    code, output = run("-y", "--cookies", "firefox", EMPTY)
    assert code == 1
    assert t("done") not in output
    assert names(out) == []


def test_selection_survives_the_cookie_retry(run: Any, replay: Any, out: Path, again: Path) -> None:
    url = "https://fake.test/rejects-cookies-carousel"
    code, output = run("-y", "--cookies", "brave", "--playlist-items", "2", url)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    [line] = commands(output)
    argv = shlex.split(line)
    assert "--cookies" not in argv
    assert argv[argv.index("--playlist-items") + 1] == "2"
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize("spec", ["0", "1,,2", "::0", "2,x", " "])
def test_bad_item_specs_fail_cleanly(run: Any, out: Path, spec: str) -> None:
    code, output = run("-y", "--playlist-items", spec, CAROUSEL)
    assert code == 1
    assert "Traceback" not in output
    assert t("done") not in output
    assert names(out) == []
    assert history_rows() == []
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == spec


@pytest.mark.parametrize(
    ("spec", "saved"),
    [
        ("2", ["02 - Clip [v1].mp4"]),
        ("2,4", BOTH_ITEMS),
        ("4,2", BOTH_ITEMS),
        ("2-4", BOTH_ITEMS),
        ("2:4", BOTH_ITEMS),
        ("2::2", BOTH_ITEMS),
        ("1-2,4", BOTH_ITEMS),
    ],
)
def test_item_specs_repeat_elsewhere_and_in_the_same_folder(
    run: Any, replay: Any, out: Path, again: Path, spec: str, saved: list[str]
) -> None:
    code, output = run("-y", "--playlist-items", spec, CAROUSEL)
    assert code == 0
    assert names(out) == saved
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == spec

    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved

    code, _ = replay(line)
    assert code == 0
    assert names(out) == saved


@pytest.mark.parametrize(
    ("spec", "saved"),
    [
        ("-1", ["04 - Clip [v2].mp4"]),
        ("-2:", ["04 - Clip [v2].mp4"]),
        ("-3::2", BOTH_ITEMS),
    ],
)
def test_item_specs_starting_with_a_dash_repeat(
    run: Any, replay: Any, out: Path, again: Path, spec: str, saved: list[str]
) -> None:
    # The = form on the way in too: before 3.14 argparse rejects a separate -2:.
    code, output = run("-y", f"--playlist-items={spec}", CAROUSEL)
    assert code == 0
    assert names(out) == saved
    [line] = commands(output)
    assert f"--playlist-items={spec}" in shlex.split(line)

    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


def test_sub_langs_starting_with_a_dash_repeat(
    run: Any, replay: Any, out: Path, again: Path
) -> None:
    code, output = run("-y", "-s", "--sub-langs=-live_chat", VIDEO)
    assert code == 0
    saved = names(out)
    assert saved
    [line] = commands(output)
    assert "--sub-langs=-live_chat" in shlex.split(line)

    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


@pytest.mark.parametrize(
    ("reply", "printed", "saved"),
    [
        ("2-4", "2,4", BOTH_ITEMS),
        ("1-2", "2", ["02 - Clip [v1].mp4"]),
        ("4, 2", "2,4", BOTH_ITEMS),
        ("1-4", "2,4", BOTH_ITEMS),
    ],
)
def test_wizard_ranges_repeat_without_their_unsupported_items(
    run: Any,
    replay: Any,
    out: Path,
    again: Path,
    answers: Any,
    reply: str,
    printed: str,
    saved: list[str],
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", reply, "", "", "", "", "")
    code, output = run("-i", CAROUSEL)
    assert code == 0
    assert names(out) == saved
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--playlist-items") + 1] == printed
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved
    code, _ = replay(line)
    assert code == 0
    assert names(out) == saved


def test_wizard_carousel_on_defaults_prints_only_the_selection(
    home: Path,
    fake_site: list[Any],
    capfd: pytest.CaptureFixture[str],
    answers: Any,
    replay: Any,
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "", "")
    assert cli.main([CAROUSEL]) == 0
    [line] = commands(capfd.readouterr().out)
    assert line == f"download-media -y --playlist-items 2,4 {CAROUSEL}"
    code, _ = replay(line)
    assert code == 0
    assert names(home / "Videos") == BOTH_ITEMS


def test_wizard_audio_selection_repeats(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    # URL, item selection, type, audio format, folder, cookies.
    answers("", "4", "2", "", "", "")
    code, output = run("-i", CAROUSEL)
    assert code == 0
    assert names(out) == ["04 - Clip [v2].mp3"]
    [line] = commands(output)
    assert "-a" in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_wizard_browser_choice_repeats(
    run: Any, replay: Any, again: Path, fake_site: list[Any], answers: Any
) -> None:
    # URL, cookies, type, quality, container, folder. The probe asks first: no preview.
    answers("", "2", "", "", "", "")
    code, output = run("-i", "https://fake.test/needs-cookies")
    assert code == 0
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("--cookies") + 1] == BROWSERS[0]
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip.mp4"]


def test_wizard_partial_failure_repeats_the_same_partial_result(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "", "")
    code, output = run("-i", "https://fake.test/broken-item")
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    [line] = commands(output)
    code, replayed = replay(line, again)
    assert code == 0
    assert names(again) == names(out) == ["01 - Clip [v1].mp4"]
    assert t("partial_result", 1, 2, 0, 1) in replayed


def test_wizard_total_failure_prints_the_command(run: Any, out: Path, answers: Any) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "", "")
    code, output = run("-i", "https://fake.test/all-broken")
    assert code == 1
    assert names(out) == []
    assert t("done") not in output
    [line] = commands(output)
    assert "https://fake.test/all-broken" in shlex.split(line)


def test_wizard_all_replaces_the_playlist_items_flag(run: Any, out: Path, answers: Any) -> None:
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "all", "", "", "", "", "")
    code, output = run("-i", "--playlist-items", "2", CAROUSEL)
    assert code == 0
    assert names(out) == BOTH_ITEMS
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-p" in argv
    assert "--playlist-items" not in argv


def test_items_flag_is_dropped_when_the_wizard_finds_a_single_video(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    # URL, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "")
    code, output = run("-i", "--playlist-items", "2", VIDEO)
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    assert "--playlist-items" not in shlex.split(line)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip.mp4"]


def test_items_flag_survives_a_failed_wizard_probe(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    url = "https://fake.test/rejects-cookies-carousel"
    # URL, type, quality, container, folder, cookies: no item step without a probe.
    answers("", "", "", "", "", "")
    code, output = run("-i", "--cookies", "brave", "--playlist-items", "2", url)
    assert code == 0
    assert t("probe_falling_back") in output
    assert names(out) == ["02 - Clip [v1].mp4"]
    [line] = commands(output)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_cancelled_wizard_with_the_items_flag_prints_nothing(run: Any, answers: Any) -> None:
    answers("", "c")
    code, output = run("-i", "--playlist-items", "2", CAROUSEL)
    assert code == 0
    assert t("equiv_cmd") not in output


def test_interactive_with_yes_prints_like_yes(run: Any, answers: Any) -> None:
    answers()
    code, output = run("-i", "-y", VIDEO)
    assert code == 0
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-y" in argv
    assert "-i" not in argv


@pytest.mark.parametrize(
    ("replies", "flag"),
    [(("2",), "-p"), (("1", ""), "-P")],
    ids=["all", "single"],
)
def test_failed_url_keeps_the_mode_it_resolved_to(
    run: Any, answers: Any, replies: tuple[str, ...], flag: str
) -> None:
    url = f"{PRIVATE}?list=1"
    answers(*replies)
    code, output = run(url)
    assert code == 1
    assert t("failed", url) in output
    [line] = commands(output)
    assert flag in shlex.split(line)


def test_failed_url_under_yes_keeps_the_single_mode(run: Any) -> None:
    code, output = run("-y", f"{PRIVATE}?list=1")
    assert code == 1
    [line] = commands(output)
    assert "-P" in shlex.split(line)


def test_url_with_nothing_saved_keeps_the_mode_it_resolved_to(run: Any, answers: Any) -> None:
    answers("2")
    code, output = run("https://fake.test/only-images?list=1")
    assert code == 1
    assert t("no_supported_items") in output
    [line] = commands(output)
    assert "-p" in shlex.split(line)


def test_failed_declined_and_saved_urls_in_one_run(run: Any, out: Path, answers: Any) -> None:
    answers("", "n", "")
    code, output = run(PRIVATE, VIDEO_B, VIDEO)
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert t("failed", PRIVATE) in output
    assert t("cancelled") in output
    assert output.count(t("done")) == 1
    assert len(history_rows()) == 1
    assert output.count(t("equiv_cmd")) == 1
    [line] = commands(output)
    assert shlex.split(line)[-2:] == [PRIVATE, VIDEO]


def test_failed_url_in_another_mode_gets_its_own_command(run: Any, out: Path, answers: Any) -> None:
    url = f"{PRIVATE}?list=1"
    answers("2", "")
    code, output = run(url, VIDEO)
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("equiv_cmd")) == 1
    first, second = (shlex.split(line) for line in commands(output))
    assert "-p" in first
    assert first[-1] == url
    assert "-p" not in second
    assert second[-1] == VIDEO


def test_every_url_declined_prints_nothing(run: Any, out: Path, answers: Any) -> None:
    answers("n", EOFError)
    code, output = run(VIDEO, LIST_URL)
    assert code == 1
    assert names(out) == []
    assert t("equiv_cmd") not in output
    assert commands(output) == []


def test_question_cancelled_on_one_url_keeps_the_next(run: Any, out: Path, answers: Any) -> None:
    answers(EOFError, "")
    code, output = run(LIST_URL, VIDEO)
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    assert shlex.split(line)[-1] == VIDEO
    assert LIST_URL not in shlex.split(line)


def test_cookie_drop_ignores_a_declined_url(
    run: Any, replay: Any, again: Path, answers: Any
) -> None:
    url = "https://fake.test/rejects-cookies"
    answers("n", "")
    code, output = run("--cookies", "brave", VIDEO, url)
    assert code == 1
    assert t("cookies_retry", "brave") in output
    [line] = commands(output)
    argv = shlex.split(line)
    assert "--cookies" not in argv
    assert VIDEO not in argv
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip.mp4"]


def test_cookies_stay_when_a_reached_url_failed(run: Any) -> None:
    code, output = run("-y", "--cookies", "brave", "https://fake.test/rejects-cookies", PRIVATE)
    assert code == 1
    [line] = commands(output)
    assert "--cookies" in shlex.split(line)


def test_cookies_stay_when_a_reached_url_needed_them(run: Any) -> None:
    urls = ("https://fake.test/rejects-cookies", "https://fake.test/needs-cookies")
    code, output = run("-y", "--cookies", "brave", *urls)
    assert code == 0
    [line] = commands(output)
    assert "--cookies" in shlex.split(line)


def test_cookie_drop_applies_to_every_mode(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    urls = (
        "https://fake.test/rejects-cookies-carousel?list=1",
        "https://fake.test/rejects-cookies",
    )
    answers("2", "")
    code, output = run("--cookies", "brave", *urls)
    assert code == 0
    lines = commands(output)
    assert len(lines) == 2
    for line in lines:
        assert "--cookies" not in shlex.split(line)
        code, _ = replay(line, again)
        assert code == 0
    assert names(again) == names(out)


def test_cookies_stay_in_every_mode_when_one_url_needed_them(run: Any, answers: Any) -> None:
    answers("2", "")
    urls = ("https://fake.test/rejects-cookies?list=1", "https://fake.test/needs-cookies")
    code, output = run("--cookies", "brave", *urls)
    assert code == 0
    lines = commands(output)
    assert len(lines) == 2
    assert all("--cookies" in shlex.split(line) for line in lines)


def test_four_urls_in_two_modes_keep_their_order(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    other = "https://fake.test/broken-item?list=1"
    answers("2", "", "2", "")
    code, output = run(LIST_URL, VIDEO, other, VIDEO_B)
    assert code == 0
    assert output.count(t("equiv_cmd")) == 1
    first, second = commands(output)
    assert "-p" in shlex.split(first)
    assert shlex.split(first)[-2:] == [LIST_URL, other]
    assert shlex.split(second)[-2:] == [VIDEO, VIDEO_B]
    assert "-p" not in shlex.split(second)
    for line in (first, second):
        code, _ = replay(line, again)
        assert code == 0
    assert names(again) == names(out)


def test_first_mode_seen_prints_first_whichever_it_is(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers("", "2", "")
    code, output = run(VIDEO, LIST_URL, VIDEO_B)
    assert code == 0
    assert output.count(t("equiv_cmd")) == 1
    first, second = (shlex.split(line) for line in commands(output))
    assert first[-2:] == [VIDEO, VIDEO_B]
    assert "-p" not in first
    assert second[-1] == LIST_URL
    assert "-p" in second


def test_single_answer_next_to_a_plain_url_shares_one_flagged_command(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    other = "https://fake.test/broken-item?list=1"
    answers("1", "", "", "2")
    code, output = run(LIST_URL, VIDEO_B, other)
    assert code == 0
    first, second = commands(output)
    assert "-P" in shlex.split(first)
    assert shlex.split(first)[-2:] == [LIST_URL, VIDEO_B]
    assert "-p" in shlex.split(second)
    for line in (first, second):
        code, _ = replay(line, again)
        assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize(
    ("lang", "header", "nothing", "done"),
    [
        (
            "es",
            "Comando equivalente para repetir esta descarga:",
            "No están en esta playlist (4 items), se omiten: 9",
            "Listo.",
        ),
        (
            "en",
            "Equivalent command to repeat this download:",
            "Not in this playlist (4 items), skipped: 9",
            "Done.",
        ),
    ],
)
def test_what_the_user_reads_in_both_languages(
    run: Any,
    answers: Any,
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
    header: str,
    nothing: str,
    done: str,
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    code, output = run("-y", "--playlist-items", "9", CAROUSEL)
    assert code == 1
    assert nothing in output
    assert done not in output
    assert output.count(header) == 1

    answers("2", "")
    code, output = run(LIST_URL, VIDEO)
    assert code == 0
    assert output.count(header) == 1
    assert len(commands(output)) == 2


@pytest.mark.parametrize(
    ("lang", "header"),
    [
        ("es_ES.UTF-8", "Comando equivalente para repetir esta descarga:"),
        ("en_US.UTF-8", "Equivalent command to repeat this download:"),
    ],
)
def test_real_process_repeats_a_wizard_run_with_stdin_closed(
    media_url: str, tmp_path: Path, lang: str, header: str
) -> None:
    # The reproduction in the spec: no fake extractor, no fake input().
    def process(argv: list[str], home: Path, stdin: Any) -> subprocess.CompletedProcess[str]:
        home.mkdir()
        env = {**os.environ, "HOME": str(home), "LANG": lang}
        program = "import sys; from download_media.cli import main; sys.exit(main())"
        return subprocess.run(
            [sys.executable, "-c", program, *argv],
            env=env,
            input=stdin,
            stdin=None if stdin else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    url = f"{media_url}/tiny.mp4"
    first = process([url], tmp_path / "first", "\n" * 8)
    assert first.returncode == 0, first.stderr
    assert header in first.stdout
    [line] = commands(first.stdout)
    assert line == f"download-media -y {url}"
    assert names(tmp_path / "first" / "Videos") == ["tiny.mp4"]

    second = process(shlex.split(line)[1:], tmp_path / "second", None)
    assert second.returncode == 0, second.stderr
    assert names(tmp_path / "second" / "Videos") == ["tiny.mp4"]


MULTI = [f"0{i} - multi ({i}) [multi-{i}].mp4" for i in range(1, 5)]


def test_wizard_saves_every_video_without_codec_info(generic: Any, home: Path) -> None:
    code, output = generic("multi.html", *PICKER_WALK)
    assert code == 0
    assert names(home / "Videos") == MULTI
    assert output.count(tag("kind_video")) == 4
    assert t("done") in output
    assert t("no_supported_items") not in output
    assert len(history_rows()) == 1


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        ("poster.html", "poster (1).mp4"),
        ("og-video.html", "video.mp4"),
        ("partial-codec.html", "partial-codec (1).mp4"),
    ],
)
def test_wizard_reads_a_lone_video_without_codec_info_as_video(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert names(home / "Videos") == [saved]
    assert names(home / "Music") == []
    assert detected("kind_video") in output
    assert t("image_only_skipped") not in output


def test_wizard_offers_entries_that_only_have_a_url(generic: Any, home: Path) -> None:
    code, output = generic("og-videos.html", *PICKER_WALK)
    assert code == 0
    assert names(home / "Videos") == ["01 - video (1) [v1].mp4", "02 - video (2) [v2].mp4"]
    assert output.count(tag("kind_video")) == 2


def test_wizard_reads_audio_without_codec_info_as_audio(generic: Any, home: Path) -> None:
    # URL, format, folder, cookies: no type step.
    code, output = generic("audio.html", "", "", "", "")
    assert code == 0
    assert names(home / "Music") == ["audio (1).mp3"]
    assert names(home / "Videos") == []
    assert detected("kind_audio") in output


def test_wizard_tags_audio_items_without_codec_info(generic: Any, home: Path) -> None:
    code, output = generic("audios.html", *PICKER_WALK)
    assert code == 0
    assert names(home / "Videos") == [
        "01 - audios (1) [audios-1].m4a",
        "02 - audios (2) [audios-2].m4a",
    ]
    assert output.count(tag("kind_audio")) == 2


@pytest.mark.parametrize("reply", ["", "all"])
def test_wizard_enter_on_video_and_audio_keeps_both(generic: Any, home: Path, reply: str) -> None:
    code, output = generic("video-audio.html", "", reply, "", "", "", "", "")
    assert code == 0
    assert names(home / "Videos") == [
        "01 - video-audio (1) [video-audio-1].mp4",
        "02 - video-audio (2) [video-audio-2].m4a",
    ]
    assert tag("kind_video") in output
    assert tag("kind_audio") in output


@pytest.mark.parametrize("reply", ["", "all"])
def test_wizard_saves_a_jpg_next_to_the_video(generic: Any, home: Path, reply: str) -> None:
    code, output = generic("jpg-video.html", "", reply, "", "", "", "", "")
    assert code == 0
    assert names(home / "Videos") == [
        "01 - jpg-video (1) [jpg-video-1].jpg",
        "02 - jpg-video (2) [jpg-video-2].mp4",
    ]
    assert "[?]" in output
    assert tag("kind_video") in output


def test_wizard_on_a_missing_video_is_a_failure_not_an_image(
    generic: Any, media_url: str, home: Path
) -> None:
    code, output = generic("gone.html", *SINGLE_WALK)
    assert code == 1
    assert t("failed", f"{media_url}/gone.html") in output
    assert t("done") not in output
    assert t("image_only_skipped") not in output
    assert names(home / "Videos") == []


def test_wizard_on_one_missing_video_of_two_saves_the_other(generic: Any, home: Path) -> None:
    code, output = generic("half-gone.html", *PICKER_WALK)
    assert code == 0
    assert names(home / "Videos") == ["01 - half-gone (1) [half-gone-1].mp4"]
    assert t("partial_result", 1, 2, 0, 1) in output


def test_interactive_audio_flag_on_a_video_without_codec_info(generic: Any, home: Path) -> None:
    # URL, type, format, folder, cookies.
    code, _ = generic("poster.html", "", "", "", "", "", flags=("-i", "-a"))
    assert code == 0
    assert names(home / "Music") == ["poster (1).mp3"]


@pytest.mark.parametrize(
    ("page", "replies", "flags", "saved"),
    [
        ("poster.html", SINGLE_WALK, "-y", ["poster (1).mp4"]),
        ("multi.html", PICKER_WALK, "-y --playlist-items 1,2,3,4", MULTI),
    ],
)
def test_wizard_on_media_without_codec_info_repeats_without_a_prompt(
    generic: Any,
    pasted: Any,
    media_url: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    page: str,
    replies: tuple[str, ...],
    flags: str,
    saved: list[str],
) -> None:
    _, output = generic(page, *replies)
    [line] = commands(output)
    assert line == f"download-media {flags} {media_url}/{page}"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == saved


def test_wizard_twice_on_a_video_without_codec_info(generic: Any, home: Path) -> None:
    first, _ = generic("poster.html", *SINGLE_WALK)
    second, output = generic("poster.html", *SINGLE_WALK)
    assert (first, second) == (0, 0)
    assert t("done") in output
    assert names(home / "Videos") == ["poster (1).mp4"]
    assert len(history_rows()) == 2


def test_wizard_keeps_saving_a_direct_image(generic: Any, home: Path) -> None:
    # An unknown kind leaves the playlist mode open, so the preview asks too.
    code, output = generic("tiny.jpg", *SINGLE_WALK, "")
    assert code == 0
    assert names(home / "Videos") == ["tiny.jpg"]
    assert detected("kind_unknown") in output


@pytest.mark.parametrize(
    ("page", "flags", "replies", "saved"),
    [
        ("poster", (), ("",), ["poster (1).mp4"]),
        ("poster", ("-y",), (), ["poster (1).mp4"]),
        ("poster", ("-y", "-p"), (), ["poster (1).mp4"]),
        ("poster", ("-y", "-P"), (), ["poster (1).mp4"]),
        ("poster", ("-y", "-a"), (), ["poster (1).mp3"]),
        ("multi", (), ("",), [f"multi ({i}) [multi-{i}].mp4" for i in range(1, 5)]),
        ("multi", ("-y",), (), [f"multi ({i}) [multi-{i}].mp4" for i in range(1, 5)]),
        ("multi", ("-y", "-p"), (), MULTI),
        ("multi", ("-y", "-P"), (), [f"multi ({i}) [multi-{i}].mp4" for i in range(1, 5)]),
        ("multi", ("-y", "-a"), (), [f"multi ({i}) [multi-{i}].mp3" for i in range(1, 5)]),
        ("og-video", (), ("",), ["video.mp4"]),
        ("og-video", ("-y",), (), ["video.mp4"]),
        ("og-video", ("-y", "-p"), (), ["video.mp4"]),
        ("og-video", ("-y", "-P"), (), ["video.mp4"]),
        ("og-video", ("-y", "-a"), (), ["video.mp3"]),
        ("audio", (), ("",), ["audio (1).m4a"]),
        ("audio", ("-y",), (), ["audio (1).m4a"]),
        ("audio", ("-y", "-p"), (), ["audio (1).m4a"]),
        ("audio", ("-y", "-P"), (), ["audio (1).m4a"]),
        ("audio", ("-y", "-a"), (), ["audio (1).mp3"]),
    ],
)
def test_direct_runs_on_media_without_codec_info_are_unchanged(
    generic: Any,
    out: Path,
    page: str,
    flags: tuple[str, ...],
    replies: tuple[str, ...],
    saved: list[str],
) -> None:
    code, _ = generic(f"{page}.html", *replies, flags=("-o", str(out), *flags))
    assert code == 0
    assert names(out) == saved


def test_wizard_on_images_and_a_video_without_codec_info(
    run: Any, out: Path, again: Path, answers: Any
) -> None:
    url = "https://fake.test/bare-carousel"
    answers(*PICKER_WALK)
    code, output = run("-i", url)
    assert code == 0
    assert tag("kind_image") in output
    assert tag("kind_video") in output
    assert names(out) == ["02 - Clip [v1].mp4"]

    code, _ = run("-y", "-p", "-o", str(again), url)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        ("url-only-carousel", ["01 - Clip [v1].mp4", "02 - Clip [v2].mp4"]),
        ("jpg-carousel", ["01 - Clip [p1].jpg", "02 - Clip [p2].jpg"]),
    ],
)
def test_wizard_saves_carousels_of_url_only_entries(
    run: Any, out: Path, answers: Any, page: str, saved: list[str]
) -> None:
    answers(*PICKER_WALK)
    code, _ = run("-i", f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == saved


def test_wizard_tags_known_audio_with_a_duration_as_audio(
    run: Any, out: Path, answers: Any
) -> None:
    answers(*PICKER_WALK)
    code, output = run("-i", "https://fake.test/audio-carousel")
    assert code == 0
    assert output.count(tag("kind_audio")) == 2
    assert tag("kind_video") not in output


def test_wizard_keeps_refusing_a_carousel_of_only_images(run: Any, out: Path, answers: Any) -> None:
    answers("")
    code, output = run("-i", "https://fake.test/only-images")
    assert code == 0
    assert t("no_supported_items") in output
    assert t("equiv_cmd") not in output
    assert names(out) == []


def test_wizard_on_a_lone_image_still_fails_the_probe(run: Any, out: Path, answers: Any) -> None:
    # The failed probe falls back to the manual steps, and the preview asks too.
    answers(*SINGLE_WALK, "")
    code, output = run("-i", "https://fake.test/image")
    assert code == 1
    assert t("probe_failed", "No video formats found!") in output
    assert names(out) == []


def test_interactive_no_playlist_flag_still_shows_the_picker(generic: Any, home: Path) -> None:
    code, output = generic("multi.html", *PICKER_WALK, flags=("-i", "-P"))
    assert code == 0
    assert output.count(tag("kind_video")) == 4
    assert names(home / "Videos") == MULTI


@pytest.mark.parametrize("page", ["poster", "multi", "og-video", "og-videos", "audio", "audios"])
def test_listing_formats_directly_on_media_without_codec_info(
    generic: Any, home: Path, page: str
) -> None:
    code, output = generic(f"{page}.html", flags=("-y", "-l"))
    assert code == 0
    assert "Available formats" in output
    assert names(home) == []
    assert history_rows() == []


def test_listing_formats_alone_walks_the_wizard_instead_of_refusing(
    generic: Any, home: Path
) -> None:
    code, output = generic("multi.html", *PICKER_WALK, flags=("-l",))
    assert code == 0
    assert t("no_supported_items") not in output
    assert "Available formats" in output
    assert names(home / "Videos") == []


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        # The default container is mp4 (spec 30).
        ("webm.html", "webm (1).mp4"),
        ("hls.html", "hls (1).mp4"),
        ("tiny.webm", "tiny.mp4"),
    ],
)
def test_wizard_reads_webm_and_hls_without_codec_info_as_video(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert names(home / "Videos") == [saved]
    assert names(home / "Music") == []


def test_wizard_saves_a_lone_jpg_in_a_video_tag(generic: Any, home: Path) -> None:
    code, output = generic("jpg-alone.html", *SINGLE_WALK, "")
    assert code == 0
    assert detected("kind_unknown") in output
    assert t("image_only_skipped") not in output
    assert names(home / "Videos") == ["jpg-alone (1).jpg"]


@pytest.mark.parametrize("page", ["tiny.jpg?w=100", "tiny.png"])
def test_wizard_keeps_direct_images_unknown(generic: Any, home: Path, page: str) -> None:
    code, output = generic(page, *SINGLE_WALK, "")
    assert code == 0
    assert detected("kind_unknown") in output
    assert names(home / "Videos") == [page.partition("?")[0]]


def test_wizard_labels_an_image_yt_dlp_does_not_list_as_video_and_saves_it(
    generic: Any, home: Path
) -> None:
    code, output = generic("tiny.gif", *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert names(home / "Videos") == ["tiny.gif"]


def test_wizard_audio_type_on_a_direct_image_fails_honestly(
    generic: Any, media_url: str, home: Path
) -> None:
    # URL, type, format, folder, cookies, and the unknown kind's confirmation.
    code, output = generic("tiny.jpg", "", "2", "", "", "", "")
    assert code == 1
    assert t("failed", f"{media_url}/tiny.jpg") in output
    assert t("done") not in output
    assert history_rows() == []


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        ("multi.html", MULTI),
        ("og-videos.html", ["01 - video (1) [v1].mp4", "02 - video (2) [v2].mp4"]),
    ],
)
def test_wizard_twice_on_a_picker_without_codec_info(
    generic: Any, home: Path, page: str, saved: list[str]
) -> None:
    for _ in range(2):
        code, output = generic(page, *PICKER_WALK)
        assert code == 0
        assert t("done") in output
        assert names(home / "Videos") == saved
    assert len(history_rows()) == 2


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        ("tiny.m4a", "tiny.mp3"),
        ("tiny.m4a?token=1", "tiny.mp3"),
        ("tiny.wav", "tiny.mp3"),
        ("audio-wav.html", "audio-wav (1).mp3"),
        ("audio.html", "audio (1).mp3"),
    ],
)
def test_wizard_converts_audio_without_codec_info_and_reruns(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    # URL, format, folder, cookies: no type step.
    for _ in range(2):
        code, output = generic(page, "", "", "", "")
        assert code == 0
        assert detected("kind_audio") in output
        assert t("done") in output
        assert names(home / "Music") == [saved]
        assert names(home / "Videos") == []
    assert len(history_rows()) == 2


@pytest.mark.parametrize(
    ("page", "choices", "saved"),
    [
        ("tiny.m4a", ("2",), "tiny.m4a"),
        ("tiny.wav", ("4",), "tiny.wav"),
        # Type, then format: an .ogg under an audio label may hold a video.
        ("tiny.ogg", ("2", "2"), "tiny.m4a"),
        ("audio.html", ("2",), "audio (1).m4a"),
    ],
)
def test_wizard_on_audio_without_codec_info_honours_the_format(
    generic: Any, home: Path, page: str, choices: tuple[str, ...], saved: str
) -> None:
    for _ in range(2):
        code, output = generic(page, "", *choices, "", "")
        assert code == 0
        assert t("done") in output
        assert names(home / "Music") == [saved]


def test_wizard_on_audio_with_a_relative_og_image_saves_it_without_a_cover(
    generic: Any, home: Path
) -> None:
    # yt-dlp can't fetch a thumbnail URL with no scheme, and raises what it only catches
    # for network errors: the cover is dropped, not the download.
    code, output = generic("audio-relative-og.html", "", "", "", "")
    assert code == 0
    [saved] = names(home / "Music")
    assert saved.endswith(".mp3")
    assert t("no_cover", saved) in output
    assert t("done") in output


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        # In the default container, mp4 (spec 30).
        ("video-ogg.html", "video-ogg (1).mp4"),
        ("video-ogg-codecs.html", "video-ogg-codecs (1).mp4"),
        ("og-video-ogg.html", "video.mp4"),
        ("clip.asf", "clip.mp4"),
        ("video-m4b.html", "video-m4b (1).mp4"),
        ("video-wma.html", "video-wma (1).mp4"),
    ],
)
def test_wizard_keeps_the_video_in_a_container_yt_dlp_lists_as_audio(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert t("done") in output
    assert names(home / "Music") == []
    assert names(home / "Videos") == [saved]
    assert "video" in streams(home / "Videos" / saved)


@pytest.mark.parametrize(
    ("page", "saved"), [("video-ogx.html", "video-ogx (1).mp4"), ("theora.ogx", "theora.mp4")]
)
def test_wizard_converts_an_ogx_video_and_tags_it(
    generic: Any, media_url: str, home: Path, page: str, saved: str
) -> None:
    # ffmpeg has no muxer for .ogx; the container step turns it into an mp4 it can tag.
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert untagged_warnings(output) == 0
    assert _source_url(home / "Videos" / saved) == f"{media_url}/{page}"
    assert t("done") in output
    assert t("failed", f"{media_url}/{page}") not in output
    assert names(home / "Music") == []
    assert names(home / "Videos") == [saved]
    assert "video" in streams(home / "Videos" / saved)


def step_leftovers(folder: Path) -> list[str]:
    """What a failed ffmpeg step or an unfinished download leaves behind."""
    return [n for n in names(folder) if ".temp." in n or n.endswith((".meta", ".part"))]


def untagged_warnings(output: str) -> int:
    return output.count(t("untagged", ""))


def said_partial_result(output: str) -> bool:
    pattern = re.escape(t("partial_result", *["{}"] * 4)).replace(r"\{\}", r"\d+")
    return re.search(pattern, output) is not None


@pytest.mark.parametrize(
    ("page", "kinds"),
    [
        (WEBA, {"audio"}),
        ("typed/audio/mp4/tiny.m4a/clip.m4r", {"audio"}),
        ("typed/audio/mp4/tiny.m4a/clip.f4a", {"audio"}),
        # ffmpeg's muxer can't take the streams, and leaves an empty temp file.
        ("typed/audio/x-wav/tiny.mp4/clip.wav", {"audio", "video"}),
        (OGA_VIDEO, {"audio", "video"}),
    ],
)
def test_a_saved_file_ffmpeg_cannot_tag_is_a_success(
    generic: Any, media_url: str, out: Path, page: str, kinds: set[str]
) -> None:
    code, output = generic(page, flags=("-y", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert streams(out / saved) == kinds
    assert t("untagged", saved) in output
    assert untagged_warnings(output) == 1
    assert t("done") in output
    assert t("failed", f"{media_url}/{page}") not in output
    assert step_leftovers(out) == []
    assert len(history_rows()) == 1


@pytest.mark.parametrize("page", ["theora.ogx", NO_EXT_VIDEO])
def test_a_video_ffmpeg_cannot_tag_is_converted_and_tagged(
    generic: Any, media_url: str, out: Path, page: str
) -> None:
    # Spec 29's untagged files, until spec 30 put them in an mp4.
    code, output = generic(page, flags=("-y", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp4")
    assert streams(out / saved) == {"audio", "video"}
    assert _source_url(out / saved) == f"{media_url}/{page}"
    assert untagged_warnings(output) == 0
    assert t("done") in output
    assert step_leftovers(out) == []
    assert len(history_rows()) == 1


def test_a_body_that_is_not_media_is_still_a_failure(
    generic: Any, media_url: str, out: Path
) -> None:
    code, output = generic(JUNK, flags=("-y", "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/{JUNK}") in output
    assert untagged_warnings(output) == 0
    assert history_rows() == []


def test_playlist_with_an_untaggable_item_saves_both_and_counts_each_once(
    generic: Any, out: Path
) -> None:
    code, output = generic("oga-and-mp4.html", flags=("-y", "-p", "-o", str(out)))
    assert code == 0
    saved = names(out)
    assert len(saved) == 2
    assert all("video" in streams(out / name) for name in saved)
    [oga] = [name for name in saved if name.endswith(".oga")]
    assert t("untagged", oga) in output
    assert untagged_warnings(output) == 1
    assert not said_partial_result(output)
    assert step_leftovers(out) == []
    assert len(history_rows()) == 1


def test_playlist_counts_an_item_that_is_not_media_as_failed_only(generic: Any, out: Path) -> None:
    # yt-dlp reports a failed post-processing step under ignoreerrors, then still runs the
    # post hooks: the item was counted as saved and as failed.
    code, output = generic("junk-and-mp4.html", flags=("-y", "-p", "-o", str(out)))
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    assert t("done") in output
    assert untagged_warnings(output) == 0


@pytest.mark.parametrize(
    "flags",
    [("-p", "-a"), ("-p", "-a", "-f", "wav"), ("--playlist-items", "1,2", "-a")],
    ids=["-p -a", "-p -a -f wav", "--playlist-items -a"],
)
def test_an_item_failing_in_two_steps_counts_once(
    generic: Any, out: Path, flags: tuple[str, ...]
) -> None:
    # Under ignoreerrors each failed post-processing step reports an error of its own.
    code, output = generic("junk-and-mp4.html", flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    assert t("done") in output


@pytest.mark.parametrize("flags", [(), ("-p",)], ids=["single", "-p"])
def test_a_message_yt_dlp_does_not_call_an_error_is_not_a_failure(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    code, output = run("-y", *flags, "https://fake.test/unscoped-cookies")
    assert code == 0
    assert len(names(out)) == 1
    assert not said_partial_result(output)
    assert t("done") in output


def test_playlist_where_no_item_is_media_is_a_failure(
    generic: Any, media_url: str, out: Path
) -> None:
    code, output = generic("all-junk.html", flags=("-y", "-p", "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/all-junk.html") in output
    assert t("done") not in output
    assert history_rows() == []


def test_single_mode_goes_on_past_an_untaggable_item(generic: Any, out: Path) -> None:
    code, output = generic("oga-and-mp4.html", flags=("-y", "-o", str(out)))
    assert code == 0
    assert len(names(out)) == 2
    assert untagged_warnings(output) == 1


def test_rerunning_an_untaggable_file_adds_one_copy_at_most(generic: Any, out: Path) -> None:
    # No source-URL tag, so the second run can't tell the file is its own (D5).
    page = "typed/audio/webm/audio.webm/clip.weba"
    for count in (1, 2, 2):
        code, output = generic(page, flags=("-y", "-o", str(out)))
        assert code == 0
        assert t("done") in output
        assert len(names(out)) == count
    assert len(history_rows()) == 3


def test_audio_from_an_untaggable_file_is_tagged_as_usual(generic: Any, out: Path) -> None:
    code, output = generic("theora.ogx", flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    assert names(out) == ["theora.mp3"]
    assert untagged_warnings(output) == 0


def test_a_taggable_file_still_gets_its_source_url(generic: Any, media_url: str, out: Path) -> None:
    code, output = generic("tiny.mp4", flags=("-y", "-o", str(out)))
    assert code == 0
    assert names(out) == ["tiny.mp4"]
    assert _source_url(out / "tiny.mp4") == f"{media_url}/tiny.mp4"
    assert untagged_warnings(output) == 0


def test_an_untaggable_file_with_cookies_is_not_downloaded_again(
    run: Any, out: Path, fake_site: list[Any]
) -> None:
    code, output = run("-y", "--cookies", "brave", "https://fake.test/weba")
    assert code == 0
    assert names(out) == ["Clip.weba"]
    assert t("cookies_retry", "brave") not in output
    assert t("untagged", "Clip.weba") in output
    assert fake_site == [("brave",)]


def test_an_untaggable_file_with_chapters_leaves_no_chapters_file(run: Any, out: Path) -> None:
    code, output = run("-y", "https://fake.test/weba-chapters")
    assert code == 0
    assert step_leftovers(out) == []
    assert names(out) == ["Clip.weba"]
    assert t("untagged", "Clip.weba") in output


@pytest.mark.parametrize("own", ["Clip.meta", "Clip.temp.weba"])
def test_an_untaggable_file_leaves_the_users_own_files_alone(run: Any, out: Path, own: str) -> None:
    # No chapters, so yt-dlp writes no .meta; ffmpeg has no .weba muxer, so it opens no temp file.
    out.mkdir(parents=True)
    (out / own).write_text("mine", encoding="utf-8")
    code, output = run("-y", "https://fake.test/weba")
    assert code == 0
    assert t("untagged", "Clip.weba") in output
    assert (out / own).read_text(encoding="utf-8") == "mine"


def test_an_untaggable_file_in_a_relative_folder_starting_with_a_dash(
    generic: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    code, output = generic(WEBA, flags=("-y", "--output=-clips"))
    assert names(work / "-clips") == ["clip.weba"]
    assert code == 0
    assert t("untagged", "clip.weba") in output


def test_untaggable_subtitled_file_warns_twice_and_keeps_both_files(run: Any, out: Path) -> None:
    # yt-dlp only embeds subtitles in mp4, webm, mkv and mov: spec 21's warning still follows.
    code, output = run("-y", "-s", "https://fake.test/weba-subs")
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.weba"]
    assert t("untagged", "Clip.weba") in output
    assert t("subs_not_embedded", "Clip.weba") in output
    assert t("done") in output
    assert step_leftovers(out) == []


def test_playlist_items_on_the_untaggable_item_alone(generic: Any, out: Path) -> None:
    flags = ("-y", "--playlist-items", "1", "-o", str(out))
    code, output = generic("oga-and-mp4.html", flags=flags)
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".oga")
    assert t("untagged", saved) in output
    assert untagged_warnings(output) == 1
    assert not said_partial_result(output)


def several(media_url: str, out: Path, capfd: pytest.CaptureFixture[str], *pages: str) -> Any:
    code = cli.main(["-y", "-o", str(out), *(f"{media_url}/{page}" for page in pages)])
    captured = capfd.readouterr()
    return code, captured.out + captured.err


def test_several_urls_with_one_untaggable_all_count_as_done(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code, output = several(media_url, out, capfd, WEBA, "tiny.mp4")
    assert code == 0
    assert names(out) == ["clip.weba", "tiny.mp4"]
    # The whole line: the warning names the file, not its path.
    assert t("untagged", "clip.weba") in output.splitlines()
    assert untagged_warnings(output) == 1
    # Both files go through yt-dlp's step, under its own tag.
    assert output.count("[Metadata] Adding metadata") == 2
    assert output.count(t("done")) == 2
    assert len(history_rows()) == 2


def test_several_urls_an_untaggable_one_and_a_broken_one(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code, output = several(media_url, out, capfd, WEBA, JUNK)
    assert code == 1
    assert t("untagged", "clip.weba") in output
    assert untagged_warnings(output) == 1
    assert output.count(t("done")) == 1
    assert t("failed", f"{media_url}/{JUNK}") in output
    assert t("failed", f"{media_url}/{WEBA}") not in output
    assert len(history_rows()) == 1


@pytest.mark.parametrize("lang", ["es", "en"])
def test_untagged_and_broken_in_both_languages(
    generic: Any, media_url: str, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    said = i18n._MESSAGES[lang]
    code, output = generic(WEBA, flags=("-y", "-o", str(out)))
    assert code == 0
    assert said["untagged"].format("clip.weba") in output
    other = i18n._MESSAGES["en" if lang == "es" else "es"]["untagged"]
    assert other.split("{}")[1][:20] not in output
    assert said["done"] in output
    code, output = generic(JUNK, flags=("-y", "-o", str(out)))
    assert code == 1
    assert said["failed"].format(f"{media_url}/{JUNK}") in output
    assert said["untagged"].split("{}")[1] not in output


def test_playlist_where_no_item_is_media_with_cookies_still_fails(
    run: Any, out: Path, fake_site: list[Any]
) -> None:
    # A total -p failure now raises, so the cookie fallback retries it once, and fails again.
    code, output = run("-y", "-p", "--cookies", "brave", "https://fake.test/junk-carousel")
    assert code == 1
    assert output.count(t("cookies_retry", "brave")) == 1
    assert t("failed", "https://fake.test/junk-carousel") in output
    assert t("done") not in output
    assert fake_site == [("brave",), None]
    assert history_rows() == []


@pytest.mark.parametrize("flags", [("-p",), ()])
def test_audio_item_whose_thumbnail_cannot_be_embedded(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    # Spec 28 reverses spec 29's D6 edge for the cover step: the audio is what was asked for.
    url = "https://fake.test/junk-thumb-carousel"
    code, output = run("-y", "-a", *flags, url)
    assert code == 0
    assert len(names(out)) == 2
    assert not said_partial_result(output)
    assert cover_warnings(output) == 1
    assert t("done") in output
    assert t("failed", url) not in output
    assert untagged_warnings(output) == 0


def cover_warnings(output: str) -> int:
    return output.count(t("no_cover", ""))


def images(folder: Path) -> list[str]:
    return [n for n in names(folder) if n.endswith((".jpg", ".webp", ".png", ".gif"))]


def cover_leftovers(folder: Path) -> list[str]:
    """Thumbnails, their conversions, and what a failed embed or download leaves behind."""
    return images(folder) + step_leftovers(folder)


@pytest.fixture
def no_mutagen(monkeypatch: pytest.MonkeyPatch) -> None:
    """yt-dlp needs mutagen to embed a cover in opus: fail the same on every machine."""
    from yt_dlp.postprocessor import embedthumbnail

    monkeypatch.setattr(embedthumbnail, "mutagen", None)


@pytest.mark.parametrize("flags", [(), ("-p",), ("-P",)])
def test_audio_with_a_relative_og_image_is_saved_without_a_cover(
    generic: Any, media_url: str, out: Path, flags: tuple[str, ...]
) -> None:
    page = "audio-relative-og.html"
    code, output = generic(page, flags=("-y", "-a", *flags, "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp3")
    assert t("no_cover", saved) in output
    assert cover_warnings(output) == 1
    assert t("failed", f"{media_url}/{page}") not in output
    assert len(history_rows()) == 1


def test_audio_whose_thumbnail_is_not_an_image_is_saved_and_tagged(
    generic: Any, media_url: str, out: Path
) -> None:
    page = "audio-junk-thumb.html"
    code, output = generic(page, flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp3")
    assert t("no_cover", saved) in output
    assert cover_warnings(output) == 1
    assert t("done") in output
    # The metadata step runs after a failed cover, so a rerun recognises the file.
    assert _source_url(out / saved) == f"{media_url}/{page}"


def test_rerunning_audio_whose_cover_failed_keeps_one_file(generic: Any, out: Path) -> None:
    for _ in range(2):
        code, _ = generic("audio-junk-thumb.html", flags=("-y", "-a", "-o", str(out)))
        assert code == 0
        assert len(names(out)) == 1


def test_wav_asks_for_no_cover(generic: Any, out: Path) -> None:
    # yt-dlp can't embed a cover in wav, so asking for one could only fail.
    code, output = generic("audio.html", flags=("-y", "-a", "-f", "wav", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".wav")
    assert cover_warnings(output) == 0


@pytest.mark.parametrize("page", ["audio.html", "audio-gif-thumb.html", "audio-webp-thumb.html"])
def test_opus_without_mutagen_is_saved_without_a_cover(
    generic: Any, out: Path, no_mutagen: None, page: str
) -> None:
    code, output = generic(page, flags=("-y", "-a", "-f", "opus", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".opus")
    assert t("no_cover", saved) in output
    assert cover_warnings(output) == 1


def test_mp3_whose_thumbnail_is_renamed_to_webp_leaves_no_image(generic: Any, out: Path) -> None:
    # yt-dlp renames a WebP thumbnail to .webp, converts it to .png, and the conversion fails.
    code, output = generic("audio-webp-thumb.html", flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp3")
    assert t("no_cover", saved) in output


def test_failed_cover_leaves_the_users_own_png_alone(
    generic: Any, out: Path, no_mutagen: None
) -> None:
    # A .temp.opus can't be checked here: yt-dlp's metadata step writes to it, then moves it.
    # test_downloader drives the cover step alone for that.
    out.mkdir(parents=True)
    own = out / "audio (1).png"
    own.write_bytes(b"mine")
    code, output = generic("audio.html", flags=("-y", "-a", "-f", "opus", "-o", str(out)))
    assert code == 0
    assert names(out) == ["audio (1).opus", "audio (1).png"]
    assert t("no_cover", "audio (1).opus") in output
    assert own.read_bytes() == b"mine"


def test_cover_failure_with_cookies_is_not_downloaded_again(
    run: Any, out: Path, fake_site: list[Any]
) -> None:
    code, output = run("-y", "-a", "--cookies", "brave", "https://fake.test/junk-thumb")
    assert code == 0
    assert names(out) == ["Clip.mp3"]
    assert t("no_cover", "Clip.mp3") in output
    assert t("cookies_retry", "brave") not in output
    assert fake_site == [("brave",)]


def test_audio_whose_media_is_gone_leaves_no_thumbnail(
    generic: Any, media_url: str, out: Path
) -> None:
    page = "audio-gone.html"
    code, output = generic(page, flags=("-y", "-a", "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/{page}") in output
    assert names(out) == []


def test_failed_audio_extraction_leaves_no_thumbnail(
    generic: Any, media_url: str, out: Path
) -> None:
    page = "video-junk-og.html"
    code, output = generic(page, flags=("-y", "-a", "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/{page}") in output
    # The downloaded body that couldn't be read may stay: it isn't a thumbnail.
    assert cover_leftovers(out) == []


def test_playlist_item_that_fails_after_its_thumbnail_leaves_none(run: Any, out: Path) -> None:
    code, output = run("-y", "-a", "-p", "https://fake.test/thumb-404-carousel")
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    [saved] = names(out)
    assert saved.endswith(".mp3")


@pytest.mark.parametrize("page", ["audios-og.html", "audios-relative-og.html"])
@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_a_lists_own_thumbnail_is_never_written(
    generic: Any, out: Path, page: str, flags: tuple[str, ...]
) -> None:
    code, output = generic(page, flags=("-y", "-a", *flags, "-o", str(out)))
    assert code == 0
    saved = names(out)
    assert len(saved) == 2
    assert all(name.endswith(".mp3") for name in saved)
    assert cover_warnings(output) == 0


def test_several_urls_go_on_past_a_relative_og_image(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    urls = [f"{media_url}/{page}" for page in ("audio-relative-og.html", "audio.html")]
    code = cli.main(["-y", "-a", "-o", str(out), *urls])
    output = capfd.readouterr().out
    assert code == 0
    assert names(out) == ["audio (1).mp3", "audio-relative-og (1).mp3"]
    assert output.count(t("done")) == 2
    assert len(history_rows()) == 2


@pytest.mark.parametrize("audio_format", ["mp3", "m4a"])
def test_a_working_cover_is_still_embedded(generic: Any, out: Path, audio_format: str) -> None:
    code, output = generic("audio.html", flags=("-y", "-a", "-f", audio_format, "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(f".{audio_format}")
    assert cover_warnings(output) == 0
    # yt-dlp's metadata step, which runs after the cover's, keeps no video stream in an m4a.
    if audio_format == "mp3":
        assert "video" in streams(out / saved)


def test_video_with_an_og_image_leaves_no_image(generic: Any, out: Path) -> None:
    code, _ = generic("og-video.html", flags=("-y", "-o", str(out)))
    assert code == 0
    assert len(names(out)) == 1
    assert images(out) == []


@pytest.mark.parametrize("lang", ["es", "en"])
def test_no_cover_in_both_languages(
    generic: Any, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    said = i18n._MESSAGES[lang]
    code, output = generic("audio-relative-og.html", flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert said["no_cover"].format(saved) in output
    other = i18n._MESSAGES["en" if lang == "es" else "es"]["no_cover"]
    assert other.format(saved) not in output


@pytest.mark.parametrize(
    ("page", "covered"),
    [
        ("audio-file-thumb.html", False),  # file:// is a RequestError, not a network one
        ("audio-short-thumb.html", False),  # cut off: fetched, then the embed fails
        ("audio-loop-thumb.html", False),  # a redirect loop: yt-dlp drops it itself
        ("audio-data-thumb.html", True),  # a data: URL holding a real JPEG
    ],
)
def test_other_thumbnail_links_never_fail_the_audio(
    generic: Any, media_url: str, out: Path, page: str, covered: bool
) -> None:
    code, output = generic(page, flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp3")
    assert ("video" in streams(out / saved)) is covered
    assert t("failed", f"{media_url}/{page}") not in output
    assert len(history_rows()) == 1
    if covered:
        assert cover_warnings(output) == 0


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_a_thumbnail_named_like_the_audio_never_deletes_it(
    generic: Any, out: Path, flags: tuple[str, ...]
) -> None:
    code, output = generic("audio-mp3-thumb.html", flags=("-y", "-a", *flags, "-o", str(out)))
    assert names(out) == ["audio-mp3-thumb (1).mp3"]
    # The link holds a real JPEG; named apart from the audio, it is embedded like any cover.
    assert {"audio", "video"} <= streams(out / "audio-mp3-thumb (1).mp3")
    assert code == 0
    assert cover_warnings(output) == 0


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_a_broken_thumbnail_named_like_the_audio_is_dropped_alone(
    generic: Any, out: Path, flags: tuple[str, ...]
) -> None:
    page = "audio-mp3-junk-thumb.html"
    code, output = generic(page, flags=("-y", "-a", *flags, "-o", str(out)))
    assert names(out) == ["audio-mp3-junk-thumb (1).mp3"]
    assert "audio" in streams(out / "audio-mp3-junk-thumb (1).mp3")
    assert code == 0
    assert t("no_cover", "audio-mp3-junk-thumb (1).mp3") in output


def test_a_rerun_never_writes_a_thumbnail_over_the_last_audio(generic: Any, out: Path) -> None:
    flags = ("-y", "-a", "-o", str(out))
    generic("audio-mp3-thumb.html", flags=flags)
    code, output = generic("audio-mp3-thumb.html", flags=flags)
    assert code == 0
    assert names(out) == ["audio-mp3-thumb (1).mp3"]
    # yt-dlp deletes whatever sits at the thumbnail's path before fetching it.
    assert f"Deleting existing file {out / 'audio-mp3-thumb (1).mp3'}" not in output


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_a_thumbnail_named_like_the_downloaded_media_never_fails_the_audio(
    generic: Any, media_url: str, out: Path, flags: tuple[str, ...]
) -> None:
    page = "audio-m4a-thumb.html"
    code, output = generic(page, flags=("-y", "-a", *flags, "-o", str(out)))
    assert names(out) == ["audio-m4a-thumb (1).mp3"]
    assert {"audio", "video"} <= streams(out / "audio-m4a-thumb (1).mp3")
    assert code == 0
    assert t("failed", f"{media_url}/{page}") not in output
    assert cover_warnings(output) == 0
    assert len(history_rows()) == 1


@pytest.mark.parametrize(
    ("page", "flags"),
    [
        ("audio-php-thumb.html", ()),
        ("audio-php-thumb.html", ("-p",)),
        ("audio-html-thumb.html", ()),
    ],
)
def test_a_thumbnail_link_with_an_unusual_extension_never_fails_the_audio(
    generic: Any, out: Path, page: str, flags: tuple[str, ...]
) -> None:
    code, output = generic(page, flags=("-y", "-a", *flags, "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp3")
    assert t("no_cover", saved) in output
    assert len(history_rows()) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes to a read-only folder")
@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_a_folder_the_thumbnail_cannot_be_written_to_still_fails(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str], flags: tuple[str, ...]
) -> None:
    # Writing the thumbnail is the first write: an OSError there is the audio's failure too,
    # never a missing cover. Single mode reports it itself (spec 42), -p through yt-dlp.
    out.mkdir()
    out.chmod(0o555)
    url = f"{media_url}/audio.html"
    try:
        code = cli.main(["-y", "-a", *flags, "-o", str(out), url])
    finally:
        out.chmod(0o755)
    output = capfd.readouterr().out
    assert code == 1
    assert names(out) == []
    assert cover_warnings(output) == 0
    assert t("done") not in output
    assert history_rows() == []
    assert t("failed", url) in output


def test_cancelling_after_the_thumbnail_leaves_none(
    media_url: str, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_dlp

    def interrupted(*args: Any, **kwargs: Any) -> bool:
        raise KeyboardInterrupt

    # Ctrl-C while the media downloads, after the thumbnail was written.
    monkeypatch.setattr(yt_dlp.YoutubeDL, "dl", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["-y", "-a", "-o", str(out), f"{media_url}/audio.html"])
    assert names(out) == []


@pytest.mark.parametrize(
    ("page", "warned"), [("junk-media-carousel", 0), ("junk-media-relative-carousel", 1)]
)
def test_playlist_with_a_failed_item_warns_and_cleans_for_the_saved_one_only(
    run: Any, out: Path, page: str, warned: int
) -> None:
    code, output = run("-y", "-a", "-p", f"https://fake.test/{page}")
    assert code == 0
    assert said_partial_result(output)
    # The failed item's body, which audio extraction couldn't read, may stay (spec 28, Out).
    assert [name for name in names(out) if name.endswith(".mp3")] == ["02 - Clip [t2].mp3"]
    assert cover_leftovers(out) == []
    # The failed item had no cover either, but only the saved one is named.
    assert cover_warnings(output) == warned
    if warned:
        assert t("no_cover", "02 - Clip [t2].mp3") in output
    else:
        assert "video" in streams(out / "02 - Clip [t2].mp3")


def test_cookie_retry_of_a_failed_item_leaves_no_thumbnail(
    run: Any, out: Path, fake_site: list[Any]
) -> None:
    url = "https://fake.test/thumb-404-carousel"
    code, output = run("-y", "-a", "--cookies", "brave", url)
    assert code == 1
    assert t("cookies_retry", "brave") in output
    assert fake_site == [("brave",), None]
    assert t("failed", url) in output
    assert names(out) == []


def test_several_urls_clean_up_per_url(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    pages = ("audio-gone.html", "audio.html", "audio-junk-thumb.html")
    urls = [f"{media_url}/{page}" for page in pages]
    code = cli.main(["-y", "-a", "-o", str(out), *urls])
    output = capfd.readouterr().out
    assert code == 1
    assert t("failed", urls[0]) in output
    assert names(out) == ["audio (1).mp3", "audio-junk-thumb (1).mp3"]
    assert "video" in streams(out / "audio (1).mp3")
    assert cover_warnings(output) == 1
    assert t("no_cover", "audio-junk-thumb (1).mp3") in output
    assert len(history_rows()) == 2


def test_failed_webp_conversion_leaves_the_users_own_png_alone(generic: Any, out: Path) -> None:
    # yt-dlp renames the thumbnail to .webp and converts it to .png, which fails: only a .png
    # this step wrote may go.
    out.mkdir(parents=True)
    own = out / "audio-webp-thumb (1).png"
    own.write_bytes(b"mine")
    code, output = generic("audio-webp-thumb.html", flags=("-y", "-a", "-o", str(out)))
    assert code == 0
    assert names(out) == ["audio-webp-thumb (1).mp3", "audio-webp-thumb (1).png"]
    assert own.read_bytes() == b"mine"
    assert t("no_cover", "audio-webp-thumb (1).mp3") in output


@pytest.mark.parametrize("audio_format", ["opus", "wav"])
def test_a_run_without_a_cover_still_prints_its_command(
    generic: Any, media_url: str, out: Path, no_mutagen: None, audio_format: str
) -> None:
    url = f"{media_url}/audio-relative-og.html"
    code, output = generic(
        "audio-relative-og.html", flags=("-y", "-a", "-f", audio_format, "-o", str(out))
    )
    assert code == 0
    assert names(out) == [f"audio-relative-og (1).{audio_format}"]
    assert cover_warnings(output) == (audio_format != "wav")
    # The cover step keeps yt-dlp's own tag; wav has no cover step at all.
    assert ("[EmbedThumbnail]" in output) is (audio_format != "wav")
    command = ["download-media", "-y", "-a", "-f", audio_format, "-o", str(out), url]
    assert commands(output) == [shlex.join(command)]


@pytest.mark.parametrize("flags", [(), ("-p",)])
def test_video_mode_has_no_cover_step(generic: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = generic("og-video.html", flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert len(names(out)) == 1
    assert images(out) == []
    assert "[EmbedThumbnail]" not in output
    assert cover_warnings(output) == 0


@pytest.mark.parametrize(
    "page", ["typed/audio/webm/audio.webm/clip.weba", "typed/audio/ogg/tiny.mp4/clip.oga"]
)
def test_rerun_of_an_untaggable_file_under_p_counts_each_run_once(
    generic: Any, out: Path, page: str
) -> None:
    for count in (1, 2, 2):
        code, output = generic(page, flags=("-y", "-p", "-o", str(out)))
        assert code == 0
        assert untagged_warnings(output) == 1
        assert not said_partial_result(output)
        assert len(names(out)) == count
        assert step_leftovers(out) == []


@pytest.mark.parametrize(
    ("page", "saved"),
    [
        ("video-tag-audio-ogg.html", "video-tag-audio-ogg (1).mp4"),
        ("video-tag-audio-m4b.html", "video-tag-audio-m4b (1).mp4"),
    ],
)
def test_wizard_keeps_audio_in_such_a_container_when_nothing_says_it_is_audio(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    # Read as video, so it goes into the chosen container (spec 30): an audio-only mp4.
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert names(home / "Music") == []
    assert names(home / "Videos") == [saved]
    assert streams(home / "Videos" / saved) == {"audio"}


def test_wizard_can_still_choose_audio_for_such_a_container(generic: Any, home: Path) -> None:
    # URL, type, format, folder, cookies: reading as video brings the type step back.
    code, _ = generic("video-tag-audio-ogg.html", "", "2", "", "", "")
    assert code == 0
    assert names(home / "Music") == ["video-tag-audio-ogg (1).mp3"]
    assert names(home / "Videos") == []


def test_yt_dlp_strips_video_from_m4a_so_reading_it_as_audio_loses_nothing(
    generic: Any, home: Path
) -> None:
    # The reason .m4a isn't with the containers above. If yt-dlp stops doing this,
    # an .m4a holding video would start losing it to the audio path.
    code, _ = generic("video-m4a.html", flags=("-y",))
    assert code == 0
    assert streams(home / "Videos" / "video-m4a (1).m4a") == {"audio"}


def test_wizard_tells_ogg_video_from_ogg_audio_on_one_page_and_repeats_it(
    generic: Any, pasted: Any, media_url: str, home: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    # Same extension on both items: only the vcodec yt-dlp sets for <audio> separates them.
    # URL, the video at the picker, type, quality, container, folder, cookies.
    code, output = generic("ogg-video-and-audio.html", "", "1", "", "", "", "", "")
    assert code == 0
    assert tag("kind_video") in output
    assert tag("kind_audio") in output
    saved = "01 - ogg-video-and-audio (1) [ogg-video-and-audio-1].mp4"
    assert names(home / "Videos") == [saved]
    assert names(home / "Music") == []
    assert "video" in streams(home / "Videos" / saved)

    [line] = commands(output)
    assert line == f"download-media -y --playlist-items 1 {media_url}/ogg-video-and-audio.html"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == [saved]
    assert "video" in streams(elsewhere / "Videos" / saved)


# Videos that a Content-Type or an <audio> tag calls audio (spec 27). Nothing in what
# yt-dlp hands back tells them from real audio under the same label.
LABELLED_VIDEOS = [
    ("theora.ogg", "theora.ogg"),
    ("clip.wma", "clip.wma"),
    ("clip.m4b", "clip.m4b"),
    ("as-audio/tiny.mp4", "tiny.mp4"),
    ("as-audio/tiny.webm", "tiny.webm"),
    ("as-audio/clip.asf", "clip.asf"),
    ("audio-theora-ogg.html", "audio-theora-ogg (1).ogg"),
    ("audio-mp4.html", "audio-mp4 (1).mp4"),
    ("audio-webm.html", "audio-webm (1).webm"),
]


@pytest.mark.parametrize(("page", "saved"), LABELLED_VIDEOS)
def test_wizard_enter_keeps_the_video_of_a_file_labelled_as_audio(
    generic: Any,
    pasted: Any,
    media_url: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: str,
    saved: str,
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert detected("kind_audio") in output
    assert t("audio_may_hold_video") in output
    assert t("done") in output
    assert names(home / "Music") == []
    assert names(home / "Videos") == [saved]
    assert "video" in streams(home / "Videos" / saved)

    [line] = commands(output)
    assert line == f"download-media -y {media_url}/{page}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == [saved]
    assert "video" in streams(elsewhere / "Videos" / saved)


@pytest.mark.parametrize(("page", "saved"), LABELLED_VIDEOS)
def test_wizard_can_still_take_the_audio_of_a_file_labelled_as_audio(
    generic: Any, media_url: str, home: Path, page: str, saved: str
) -> None:
    # URL, type, format, folder, cookies.
    code, output = generic(page, "", "2", "", "", "")
    assert code == 0
    assert names(home / "Music") == [f"{Path(saved).stem}.mp3"]
    assert names(home / "Videos") == []
    assert commands(output) == [f"download-media -y -a {media_url}/{page}"]


def test_interactive_audio_flag_opens_the_type_step_of_a_file_labelled_as_audio_at_audio(
    generic: Any, home: Path
) -> None:
    code, output = generic("theora.ogg", *[""] * 5, flags=("-i", "-a"))
    assert code == 0
    assert t("ask_type") in output
    assert names(home / "Music") == ["theora.mp3"]
    assert names(home / "Videos") == []


def test_interactive_quality_flag_on_a_file_labelled_as_audio_keeps_the_video(
    generic: Any, media_url: str, home: Path
) -> None:
    code, output = generic("theora.ogg", *SINGLE_WALK, flags=("-i", "-q", "720"))
    assert code == 0
    assert names(home / "Videos") == ["theora.ogg"]
    assert "video" in streams(home / "Videos" / "theora.ogg")
    assert commands(output) == [f"download-media -y -q 720 {media_url}/theora.ogg"]


REAL_AUDIO_UNDER_A_LABEL = [("tiny.ogg", "tiny.ogg"), ("audio-ogg.html", "audio-ogg (1).ogg")]


@pytest.mark.parametrize(("page", "saved"), REAL_AUDIO_UNDER_A_LABEL)
def test_wizard_enter_keeps_real_audio_under_a_label_raw(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert t("audio_may_hold_video") in output
    assert names(home / "Music") == []
    assert names(home / "Videos") == [saved]
    assert streams(home / "Videos" / saved) == {"audio"}


@pytest.mark.parametrize(("page", "saved"), REAL_AUDIO_UNDER_A_LABEL)
def test_wizard_converts_real_audio_under_a_label_when_asked(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    code, _ = generic(page, "", "2", "", "", "")
    assert code == 0
    assert names(home / "Music") == [f"{Path(saved).stem}.mp3"]
    assert names(home / "Videos") == []


@pytest.mark.parametrize(
    ("page", "saved"),
    [("clip.wma", "clip.wma"), ("clip.m4b", "clip.m4b"), ("as-audio/tiny.mp4", "tiny.mp4")],
)
def test_wizard_twice_on_a_file_labelled_as_audio_keeps_one_file(
    generic: Any, home: Path, page: str, saved: str
) -> None:
    for _ in range(2):
        code, output = generic(page, *SINGLE_WALK)
        assert code == 0
        assert t("done") in output
        assert names(home / "Videos") == [saved]
    assert len(history_rows()) == 2


@pytest.mark.xfail(
    strict=True,
    reason="Known, roadmap row 25 (opus-rerun): ogg keeps purl in stream tags while "
    "_source_url reads format tags, so the second run writes 'theora [theora].ogg', as -y does.",
)
def test_wizard_twice_on_an_ogg_labelled_as_audio_keeps_one_file(generic: Any, home: Path) -> None:
    for _ in range(2):
        code, _ = generic("theora.ogg", *SINGLE_WALK)
        assert code == 0
    assert names(home / "Videos") == ["theora.ogg"]


@pytest.mark.parametrize("page", ["theora.ogg", "clip.wma", "as-audio/tiny.mp4"])
@pytest.mark.parametrize(
    ("flags", "replies", "audio"),
    [(("-q", "1080"), ("",), False), (("-y",), (), False), (("-y", "-a"), (), True)],
    ids=["preview", "yes", "yes-audio"],
)
def test_direct_runs_on_a_file_labelled_as_audio_are_unchanged(
    generic: Any,
    home: Path,
    page: str,
    flags: tuple[str, ...],
    replies: tuple[str, ...],
    audio: bool,
) -> None:
    code, output = generic(page, *replies, flags=flags)
    assert code == 0
    assert t("audio_may_hold_video") not in output
    name = Path(page).name
    if audio:
        assert names(home / "Music") == [f"{Path(name).stem}.mp3"]
        assert names(home / "Videos") == []
    else:
        assert names(home / "Music") == []
        assert names(home / "Videos") == [name]
        assert "video" in streams(home / "Videos" / name)


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_note_on_a_file_labelled_as_audio_in_both_languages(
    generic: Any, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    _, output = generic("theora.ogg", *SINGLE_WALK)
    assert i18n._MESSAGES[lang]["audio_may_hold_video"] in output


# Spec 27, from the outside: what must not get the type step, what must, and the
# answers around it.
# URL, format, folder, cookies: real audio keeps today's flow.
AUDIO_WALK = ("",) * 4

REAL_AUDIO_THAT_SAYS_SO = [
    "tiny.mp3",
    "tiny.opus",
    "tiny.flac",
    "tiny.aac",
    "audio-mp3.html",
    # An <audio> whose <source> names its codec: something looked at the stream.
    "audio-source-vorbis.html",
    # No extension in the URL: yt-dlp takes it from the Content-Type.
    "typed/audio/mpeg/tiny.mp3/stream",
    "typed/audio/mp4/tiny.m4a/stream",
    "typed/audio/x-wav/tiny.wav/stream",
    "typed/audio/aac/tiny.aac/stream",
    "typed/audio/mpeg/tiny.mp3/get?file=a.mp3",
]


@pytest.mark.parametrize("page", REAL_AUDIO_THAT_SAYS_SO)
def test_wizard_keeps_the_audio_flow_for_audio_that_says_so(
    generic: Any, media_url: str, home: Path, page: str
) -> None:
    code, output = generic(page, *AUDIO_WALK)
    assert code == 0
    assert detected("kind_audio") in output
    assert t("audio_may_hold_video") not in output
    assert t("ask_type") not in output
    assert names(home / "Videos") == []
    [saved] = names(home / "Music")
    assert saved.endswith(".mp3")
    assert commands(output) == [f"download-media -y -a {shlex.quote(f'{media_url}/{page}')}"]


@pytest.mark.parametrize(
    "page",
    [
        "typed/audio/mpeg/tiny.mp3/TINY.MP3",
        "typed/audio/mp4/tiny.m4a/TINY.M4A",
        "typed/audio/x-wav/tiny.wav/TINY.WAV",
        "typed/audio/flac/tiny.flac/TINY.FLAC",
    ],
)
def test_wizard_keeps_the_audio_flow_for_an_uppercase_audio_extension(
    generic: Any, home: Path, page: str
) -> None:
    code, output = generic(page, *AUDIO_WALK)
    assert code == 0
    assert t("audio_may_hold_video") not in output
    assert names(home / "Videos") == []
    [saved] = names(home / "Music")
    assert saved.endswith(".mp3")


@pytest.mark.parametrize(
    "page",
    [
        "theora.ogg?token=1",
        "typed/audio/ogg/theora.ogg/THEORA.OGG",
        # No extension: the Content-Type names a container that can hold a video.
        "typed/audio/ogg/theora.ogg/stream",
        "typed/audio/webm/tiny.webm/stream",
        # <source> with a type but no codecs is still only a label.
        "audio-source-ogg.html",
    ],
)
def test_wizard_offers_the_video_of_other_labelled_shapes(
    generic: Any,
    pasted: Any,
    media_url: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: str,
) -> None:
    code, output = generic(page, *SINGLE_WALK)
    assert code == 0
    assert t("audio_may_hold_video") in output
    assert t("done") in output
    assert names(home / "Music") == []
    [saved] = names(home / "Videos")
    assert "video" in streams(home / "Videos" / saved)
    [line] = commands(output)
    assert line == f"download-media -y {shlex.quote(f'{media_url}/{page}')}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == [saved]
    assert "video" in streams(elsewhere / "Videos" / saved)


def test_hls_in_an_audio_tag_reads_as_video_and_keeps_it(generic: Any, home: Path) -> None:
    # An <audio> tag on a playlist doesn't reach its formats: no label, no flag.
    code, output = generic("audio-hls.html", *SINGLE_WALK)
    assert code == 0
    assert detected("kind_video") in output
    assert t("audio_may_hold_video") not in output
    [saved] = names(home / "Videos")
    assert "video" in streams(home / "Videos" / saved)


def test_wizard_offers_the_video_of_real_audio_with_no_extension_in_an_audio_tag(
    generic: Any, home: Path
) -> None:
    # Enter keeps the file as served; ffmpeg can't tag an .unknown_video.
    code, output = generic("audio-no-ext.html", *SINGLE_WALK)
    assert code == 0
    assert t("audio_may_hold_video") in output
    assert t("done") in output
    assert names(home / "Music") == []
    [saved] = names(home / "Videos")
    assert saved.endswith(".unknown_video")
    assert t("untagged", saved) in output


def test_wizard_can_still_save_such_audio_as_mp3(generic: Any, home: Path) -> None:
    # URL, type, format, folder, cookies.
    code, output = generic("audio-no-ext.html", "", "2", "", "", "")
    assert code == 0
    assert t("audio_may_hold_video") in output
    assert names(home / "Videos") == []
    [saved] = names(home / "Music")
    assert saved.endswith(".mp3")


def test_wizard_keeps_the_video_behind_an_audio_tag_with_no_extension(
    generic: Any, home: Path
) -> None:
    code, output = generic("audio-no-ext-video.html", *SINGLE_WALK)
    assert code == 0
    assert t("audio_may_hold_video") in output
    assert names(home / "Music") == []
    [saved] = names(home / "Videos")
    assert saved.endswith(".unknown_video")
    assert "video" in streams(home / "Videos" / saved)


@pytest.mark.parametrize(
    ("page", "replies", "music", "videos"),
    [
        # Type, quality, container, folder, cookies after the URL.
        ("site-audio-label", SINGLE_WALK, [], ["Clip.ogg"]),
        # The extractor named the codec: no type step, as before.
        ("site-audio-named", AUDIO_WALK, ["Clip.mp3"], []),
    ],
)
def test_wizard_on_a_site_extractor_s_audio_follows_what_it_named(
    wizard_urls: Any,
    home: Path,
    page: str,
    replies: tuple[str, ...],
    music: list[str],
    videos: list[str],
) -> None:
    code, output, asked = wizard_urls([f"https://fake.test/{page}"], *replies)
    assert code == 0
    assert asked == len(replies)
    assert (t("audio_may_hold_video") in output) is bool(videos)
    assert names(home / "Music") == music
    assert names(home / "Videos") == videos
    for name in videos:
        assert "video" in streams(home / "Videos" / name)


def test_audio_answer_on_a_labelled_file_replays_the_same_mp3(
    generic: Any, pasted: Any, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = generic("theora.ogg", "", "2", "", "", "")
    assert code == 0
    assert names(home / "Music") == ["theora.mp3"]
    assert streams(home / "Music" / "theora.mp3") == {"audio"}
    [line] = commands(output)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Music") == ["theora.mp3"]
    assert names(elsewhere / "Videos") == []
    assert streams(elsewhere / "Music" / "theora.mp3") == {"audio"}


@pytest.mark.parametrize(
    ("flags", "replies", "music", "videos", "command"),
    [
        # -f is an audio choice, not a type: the step still opens at video, and the
        # command drops what the video path doesn't use.
        (("-i", "-f", "m4a"), SINGLE_WALK, [], ["theora.ogg"], "-y"),
        (("-i", "-f", "m4a"), ("", "2", "", "", ""), ["theora.m4a"], [], "-y -a -f m4a"),
        (("-i", "--video-format", "mkv"), SINGLE_WALK, [], ["theora.ogg"], "-y --video-format mkv"),
        (("-i", "-a", "-f", "m4a"), (*AUDIO_WALK, ""), ["theora.m4a"], [], "-y -a -f m4a"),
        # -i -a, then 1 at the type step: the video is still there to keep.
        (("-i", "-a"), ("", "1", "", "", "", ""), [], ["theora.ogg"], "-y"),
    ],
    ids=["format", "format-then-audio", "container", "audio-and-format", "audio-then-video"],
)
def test_interactive_flags_preset_the_steps_around_the_type_step(
    generic: Any,
    media_url: str,
    home: Path,
    flags: tuple[str, ...],
    replies: tuple[str, ...],
    music: list[str],
    videos: list[str],
    command: str,
) -> None:
    code, output = generic("theora.ogg", *replies, flags=flags)
    assert code == 0
    assert t("ask_type") in output
    assert names(home / "Music") == music
    assert names(home / "Videos") == videos
    for name in videos:
        assert "video" in streams(home / "Videos" / name)
    assert commands(output) == [f"download-media {command} {media_url}/theora.ogg"]


def test_back_at_the_type_step_of_a_labelled_file_asks_it_again(generic: Any, home: Path) -> None:
    # URL, b, then type, quality, container, folder, cookies.
    code, output = generic("theora.ogg", "", "b", *[""] * 5)
    assert code == 0
    assert output.count(t("ask_type")) == 2
    assert names(home / "Videos") == ["theora.ogg"]
    assert "video" in streams(home / "Videos" / "theora.ogg")


def test_cancel_at_the_type_step_of_a_labelled_file_saves_nothing(generic: Any, home: Path) -> None:
    code, output = generic("theora.ogg", "", "c")
    assert code == 0
    assert t("ask_type") in output
    assert t("cancelled") in output
    assert names(home / "Videos") == []
    assert names(home / "Music") == []
    assert commands(output) == []
    assert history_rows() == []


def test_a_page_of_labelled_audio_items_keeps_the_carousel_flow(generic: Any, home: Path) -> None:
    # Two <audio>: Theora and Vorbis. The flag is for a single result: the picker's
    # type step already defaults to video, and nothing converts.
    code, output = generic("audios-theora-and-vorbis.html", *PICKER_WALK)
    assert code == 0
    assert t("audio_may_hold_video") not in output
    assert output.count(tag("kind_audio")) == 2
    assert names(home / "Music") == []
    saved = names(home / "Videos")
    assert len(saved) == 2
    assert all(name.endswith(".ogg") for name in saved)
    assert sorted("video" in streams(home / "Videos" / name) for name in saved) == [False, True]


def test_wizard_on_a_generic_page_with_every_kind(
    generic: Any, pasted: Any, media_url: str, home: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    code, output = generic("everything.html", *PICKER_WALK)
    assert code == 0
    assert tag("kind_video") in output
    assert tag("kind_audio") in output
    assert "[?]" in output
    # Enter takes everything downloadable.
    assert names(home / "Videos") == [
        "01 - everything (1) [everything-1].mp4",
        "02 - everything (2) [everything-2].jpg",
        "03 - everything (3) [everything-3].m4a",
    ]

    [line] = commands(output)
    assert line == f"download-media -y --playlist-items 1,2,3 {media_url}/everything.html"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == names(home / "Videos")


def test_wizard_all_on_a_generic_page_with_every_kind_repeats_with_all_of_them(
    generic: Any, pasted: Any, media_url: str, home: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    code, output = generic("everything.html", "", "all", "", "", "", "", "")
    assert code == 0
    assert names(home / "Videos") == [
        "01 - everything (1) [everything-1].mp4",
        "02 - everything (2) [everything-2].jpg",
        "03 - everything (3) [everything-3].m4a",
    ]
    [line] = commands(output)
    assert line == f"download-media -y -p {media_url}/everything.html"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    code, _ = pasted(line)
    assert code == 0
    assert names(elsewhere / "Videos") == names(home / "Videos")


def test_wizard_enter_without_videos_takes_everything_offered(
    generic: Any, media_url: str, home: Path
) -> None:
    code, output = generic("no-video.html", *PICKER_WALK)
    assert code == 0
    assert names(home / "Videos") == [
        "01 - no-video (1) [no-video-1].jpg",
        "02 - no-video (2) [no-video-2].m4a",
    ]
    assert commands(output) == [f"download-media -y --playlist-items 1,2 {media_url}/no-video.html"]


def test_wizard_audio_type_on_every_audio_item_goes_to_music(generic: Any, home: Path) -> None:
    # URL, item selection, type, format, folder, cookies.
    code, output = generic("audios.html", "", "", "2", "", "", "")
    assert code == 0
    assert names(home / "Music") == [
        "01 - audios (1) [audios-1].mp3",
        "02 - audios (2) [audios-2].mp3",
    ]
    assert names(home / "Videos") == []
    assert "-a" in shlex.split(commands(output)[0])


def test_interactive_audio_flag_preselects_audio_on_a_picker(generic: Any, home: Path) -> None:
    # URL, item selection, type, format, folder, cookies.
    code, _ = generic("multi.html", "", "", "", "", "", "", flags=("-i", "-a"))
    assert code == 0
    assert names(home / "Music") == [f"0{i} - multi ({i}) [multi-{i}].mp3" for i in range(1, 5)]
    assert names(home / "Videos") == []


def test_interactive_video_flags_on_audio_without_codec_info_stay_audio(
    generic: Any, media_url: str, home: Path
) -> None:
    code, output = generic("tiny.m4a", "", "", "", "", flags=("-i", "-q", "720"))
    assert code == 0
    assert names(home / "Music") == ["tiny.mp3"]
    assert commands(output) == [f"download-media -y -a {media_url}/tiny.m4a"]


@pytest.mark.parametrize(
    ("page", "kind", "walk", "saved"),
    [
        ("ig-video", "kind_video", SINGLE_WALK, "Clip.mp4"),
        ("storyboard-video", "kind_video", SINGLE_WALK, "Clip.mp4"),
        ("thumbnail-video", "kind_video", SINGLE_WALK, "Clip.mp4"),
        ("split-streams", "kind_video", SINGLE_WALK, "Clip.mp4"),
        ("storyboard-audio", "kind_audio", ("",) * 4, "Clip.mp3"),
        ("thumbnail-audio", "kind_audio", ("",) * 4, "Clip.mp3"),
    ],
)
def test_wizard_on_real_shapes_with_partial_codec_info(
    run: Any, out: Path, answers: Any, page: str, kind: str, walk: tuple[str, ...], saved: str
) -> None:
    prompts = answers(*walk)
    code, output = run("-i", f"https://fake.test/{page}")
    assert code == 0
    assert f"generic / {t(kind)}" in output
    assert t("image_only_skipped") not in output
    assert names(out) == [saved]
    assert len(prompts) == len(walk)


def test_split_streams_with_unknown_codecs_are_merged(run: Any, out: Path, answers: Any) -> None:
    answers(*SINGLE_WALK)
    code, _ = run("-i", "https://fake.test/split-streams")
    assert code == 0
    assert streams(out / "Clip.mp4") == {"audio", "video"}


@pytest.mark.parametrize("page", ["storyboard-only", "image-format", "ig-image"])
def test_wizard_on_a_lone_format_without_streams_is_refused_as_an_image(
    run: Any, out: Path, answers: Any, page: str
) -> None:
    prompts = answers("")
    code, output = run("-i", f"https://fake.test/{page}")
    assert code == 0
    assert len(prompts) == 1
    assert t("image_only_skipped") in output
    assert t("done") not in output
    assert names(out) == []


IG_CAROUSEL = "https://fake.test/ig-carousel"
LINKED_CAROUSEL = "https://fake.test/ig-linked-carousel"
IG_NUMBERED = [
    "01 - Clip [i1].jpg",
    "02 - Clip [v1].mp4",
    "03 - Clip [v2].mp4",
    "04 - Clip [i2].jpg",
]
IG_SINGLE = ["Clip [i1].jpg", "Clip [i2].jpg", "Clip [v1].mp4", "Clip [v2].mp4"]
WITH_AND_WITHOUT_COOKIES = pytest.mark.parametrize(
    "cookies", [(), ("--cookies", "firefox")], ids=["without", "with"]
)


def jpgs(folder: Path) -> list[str]:
    return [name for name in names(folder) if name.endswith(".jpg")]


@pytest.fixture
def tiny_jpg(media_url: str) -> bytes:
    with urllib.request.urlopen(f"{media_url}/tiny.jpg") as response:
        return bytes(response.read())


@WITH_AND_WITHOUT_COOKIES
def test_playlist_mode_saves_the_images_of_a_carousel(
    run: Any, out: Path, tiny_jpg: bytes, cookies: tuple[str, ...]
) -> None:
    code, output = run("-y", "-p", *cookies, IG_CAROUSEL)
    assert code == 0
    assert names(out) == IG_NUMBERED
    assert all((out / name).read_bytes() == tiny_jpg for name in jpgs(out))
    assert t("done") in output
    assert not said_partial_result(output)
    assert len(history_rows()) == 1
    assert history_rows()[0].split("\t")[1] == "video"


@WITH_AND_WITHOUT_COOKIES
@pytest.mark.parametrize("flag", ["-y", "-P"])
def test_single_mode_saves_the_images_of_a_carousel_and_no_thumbnail_next_to_a_video(
    run: Any, out: Path, flag: str, cookies: tuple[str, ...]
) -> None:
    flags = (flag,) if flag == "-y" else ("-y", flag)
    code, output = run(*flags, *cookies, IG_CAROUSEL)
    assert code == 0
    assert names(out) == IG_SINGLE
    assert jpgs(out) == ["Clip [i1].jpg", "Clip [i2].jpg"]
    assert t("done") in output
    assert not said_partial_result(output)


def test_direct_mode_with_a_preview_saves_the_images_after_the_confirmation(
    run: Any, out: Path, answers: Any
) -> None:
    prompts = answers("")
    code, output = run(IG_CAROUSEL)
    assert code == 0
    assert prompts == [t("ask_confirm")]
    assert names(out) == IG_SINGLE
    assert t("done") in output


@pytest.mark.parametrize("flags", [("-p",), ()], ids=["playlist", "single"])
def test_repeating_a_carousel_with_images_adds_nothing_and_rewrites_no_image(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    code, _ = run("-y", *flags, IG_CAROUSEL)
    assert code == 0
    before = {name: (out / name).stat().st_mtime_ns for name in names(out)}
    code, output = run("-y", *flags, IG_CAROUSEL)
    assert code == 0
    assert names(out) == sorted(before)
    assert {name: (out / name).stat().st_mtime_ns for name in jpgs(out)} == {
        name: mtime for name, mtime in before.items() if name.endswith(".jpg")
    }
    assert "thumbnail is already present" in output
    assert t("done") in output
    assert not said_partial_result(output)


def test_playlist_items_picks_an_image_and_a_video(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "1,3", IG_CAROUSEL)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "03 - Clip [v2].mp4"]
    assert not said_partial_result(output)


@pytest.mark.parametrize("flags", [("-a", "-p"), ("-a",)], ids=["playlist", "single"])
def test_audio_mode_skips_the_images_as_images(run: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = run("-y", *flags, IG_CAROUSEL)
    assert code == 0
    assert len(names(out)) == 2
    assert all(name.endswith(".mp3") for name in names(out))
    assert t("item_image_skipped", "i1") in output
    assert t("item_image_skipped", "i2") in output
    assert t("partial_result", 2, 4, 2, 0) in output


def test_list_formats_skips_the_images_as_images(run: Any, out: Path) -> None:
    code, output = run("-y", "-l", IG_CAROUSEL)
    assert code == 0
    assert "Available formats for v1" in output
    assert "Available formats for v2" in output
    assert t("item_image_skipped", "i1") in output
    assert names(out) == []


def test_an_image_that_cannot_be_fetched_is_a_failed_item(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "https://fake.test/ig-broken-image")
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    assert t("image_not_saved", "i1") in output
    assert t("partial_result", 1, 2, 0, 1) in output


def test_single_mode_stops_at_an_image_that_cannot_be_fetched(run: Any, out: Path) -> None:
    code, output = run("-y", "https://fake.test/ig-broken-image")
    assert code == 1
    assert t("image_not_saved", "i1") in output
    assert t("done") not in output
    assert jpgs(out) == []


def test_the_image_saved_is_the_candidate_yt_dlp_sorts_last(
    run: Any, out: Path, tiny_jpg: bytes
) -> None:
    code, _ = run("-y", "-p", "https://fake.test/ig-two-thumbs")
    assert code == 0
    [image] = names(out)
    assert image.endswith(".jpg")
    assert (out / image).read_bytes() == tiny_jpg


def test_subtitles_asked_for_still_save_the_images(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", IG_CAROUSEL)
    assert code == 0
    assert names(out) == IG_NUMBERED
    assert "ERROR" not in output


def test_wizard_on_an_instagram_shaped_carousel(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers(*PICKER_WALK)
    code, output = run("-i", IG_CAROUSEL)
    assert code == 0
    assert output.count(tag("kind_image")) == 2
    assert output.count(tag("kind_video")) == 2
    assert "[no soportado]" not in output
    assert names(out) == IG_NUMBERED
    assert t("done") in output

    [line] = commands(output)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == IG_NUMBERED


@pytest.mark.parametrize("flags", [(), ("-a",)], ids=["video", "audio"])
def test_wizard_on_only_images_asks_no_type_quality_or_container(
    run: Any, replay: Any, out: Path, again: Path, answers: Any, flags: tuple[str, ...]
) -> None:
    # URL, picker, folder, cookies: a type, quality or container step would fail the test.
    answers("", "1,4", "", "")
    code, output = run("-i", *flags, IG_CAROUSEL)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "04 - Clip [i2].jpg"]

    [line] = commands(output)
    assert " -a " not in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_wizard_in_audio_mode_on_a_mixed_pick_skips_the_image(
    run: Any, out: Path, answers: Any
) -> None:
    # URL, picker, type (audio, from -a), audio format, folder, cookies.
    answers("", "1,2", "", "", "", "")
    code, output = run("-i", "-a", IG_CAROUSEL)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp3"]
    assert t("item_image_skipped", "i1") in output
    assert t("partial_result", 1, 2, 1, 0) in output


def test_a_pick_of_only_images_is_done_and_logged(run: Any, out: Path) -> None:
    # Post hooks never see an image: only the tool's own count says something was saved.
    code, output = run("-y", "--playlist-items", "1,4", IG_CAROUSEL)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "04 - Clip [i2].jpg"]
    assert t("done") in output
    assert t("no_supported_items") not in output
    assert not said_partial_result(output)
    [row] = history_rows()
    assert row.split("\t")[1] == "video"


@WITH_AND_WITHOUT_COOKIES
def test_a_run_whose_only_item_is_a_broken_image_fails(
    run: Any, out: Path, cookies: tuple[str, ...]
) -> None:
    url = "https://fake.test/ig-broken-image"
    code, output = run("-y", "--playlist-items", "1", *cookies, url)
    assert code == 1
    assert t("image_not_saved", "i1") in output
    assert t("done") not in output
    assert names(out) == []
    assert history_rows() == []


@WITH_AND_WITHOUT_COOKIES
def test_a_broken_image_fails_alike_with_and_without_cookies(
    run: Any, out: Path, cookies: tuple[str, ...]
) -> None:
    code, output = run("-y", "-p", *cookies, "https://fake.test/ig-broken-image")
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    assert t("partial_result", 1, 2, 0, 1) in output
    assert t("cookies_retry", "firefox") not in output


@pytest.mark.parametrize("page", ["ig-relative-image", "ig-php-image"])
def test_an_image_link_yt_dlp_refuses_is_a_failed_item(run: Any, out: Path, page: str) -> None:
    # A link with no scheme, or an extension yt-dlp won't write: both raise before the fetch.
    code, output = run("-y", "-p", f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4"]
    assert t("image_not_saved", "i1") in output
    assert t("partial_result", 1, 2, 0, 1) in output
    assert "Traceback" not in output


@pytest.mark.parametrize("page", ["ig-relative-image", "ig-php-image"])
def test_single_mode_stops_at_an_image_link_yt_dlp_refuses(run: Any, out: Path, page: str) -> None:
    url = f"https://fake.test/{page}"
    code, output = run("-y", url)
    assert code == 1
    assert t("image_not_saved", "i1") in output
    assert t("failed", url) in output
    assert t("done") not in output
    assert "Traceback" not in output


def test_a_cut_off_image_is_not_reported_as_saved(run: Any, out: Path) -> None:
    url = "https://fake.test/ig-short-image"
    for _ in range(2):  # The rerun must not take the leftover for a saved image.
        code, output = run("-y", "-p", url)
        assert code == 0
        assert t("image_not_saved", "i1") in output
        assert t("partial_result", 1, 2, 0, 1) in output
        assert names(out) == ["02 - Clip [v1].mp4"]


def png_bytes(media_url: str) -> bytes:
    with urllib.request.urlopen(f"{media_url}/tiny.png") as response:
        return bytes(response.read())


@pytest.mark.parametrize(
    ("page", "image", "ext"),
    [
        ("ig-short-then-jpg", "01 - Clip [i1].jpg", "jpg"),
        ("ig-short-then-png", "01 - Clip [i1].png", "png"),
        ("ig-short-png-then-jpg", "01 - Clip [i1].jpg", "jpg"),
    ],
    ids=["same-extension", "other-extension", "cut-off-png"],
)
def test_a_cut_off_candidate_falls_back_to_the_next_one(
    run: Any, out: Path, media_url: str, tiny_jpg: bytes, page: str, image: str, ext: str
) -> None:
    # yt-dlp moves on to the next candidate; what the cut-off one left must not stay.
    code, output = run("-y", "-p", f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == [image, "02 - Clip [v1].mp4"]
    assert (out / image).read_bytes() == (tiny_jpg if ext == "jpg" else png_bytes(media_url))
    assert t("image_not_saved", "i1") not in output
    assert not said_partial_result(output)
    assert t("done") in output


def test_an_image_whose_every_candidate_is_cut_off_is_a_failed_item(run: Any, out: Path) -> None:
    # Both write to the same .jpg: the second must not take the first one's leftover.
    url = "https://fake.test/ig-short-then-short"
    for _ in range(2):
        code, output = run("-y", "-p", url)
        assert code == 0
        assert t("image_not_saved", "i1") in output
        assert t("partial_result", 1, 2, 0, 1) in output
        assert names(out) == ["02 - Clip [v1].mp4"]


def test_a_rerun_after_a_fallback_keeps_the_whole_image_it_saved(
    run: Any, out: Path, tiny_jpg: bytes
) -> None:
    url = "https://fake.test/ig-short-then-jpg"
    code, _ = run("-y", "-p", url)
    assert code == 0
    image = out / "01 - Clip [i1].jpg"
    before = image.stat().st_mtime_ns
    code, output = run("-y", "-p", url)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [v1].mp4"]
    assert image.read_bytes() == tiny_jpg
    assert image.stat().st_mtime_ns == before
    assert "thumbnail is already present" in output
    assert t("done") in output
    assert not said_partial_result(output)


def test_a_video_listed_twice_next_to_an_image_is_downloaded_once(run: Any, out: Path) -> None:
    # Single mode gives both the same name; the image hook must not touch a video's check.
    code, output = run("-y", "https://fake.test/ig-repeated-video")
    assert code == 0
    assert names(out) == ["Clip [i1].jpg", "Clip [v1].mp4"]
    assert output.count("has already been downloaded") == 1
    assert t("done") in output


def test_a_file_at_a_candidate_path_never_reached_is_left_alone(
    run: Any, out: Path, tiny_jpg: bytes
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    mine = out / "01 - Clip [i1].png"
    mine.write_bytes(b"the user's own file")
    code, output = run("-y", "-p", "https://fake.test/ig-jpg-then-png")
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "01 - Clip [i1].png", "02 - Clip [v1].mp4"]
    assert (out / "01 - Clip [i1].jpg").read_bytes() == tiny_jpg
    assert mine.read_bytes() == b"the user's own file"
    assert not said_partial_result(output)


def test_an_image_saved_before_survives_a_rerun_whose_link_is_cut_off(run: Any, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    earlier = out / "01 - Clip [i1].jpg"
    earlier.write_bytes(b"saved by an earlier run")
    code, output = run("-y", "-p", "https://fake.test/ig-short-image")
    assert code == 0
    assert earlier.read_bytes() == b"saved by an earlier run"
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [v1].mp4"]
    assert t("image_not_saved", "i1") not in output
    assert not said_partial_result(output)


def test_a_rerun_after_a_cut_off_image_saves_it_whole(run: Any, out: Path, tiny_jpg: bytes) -> None:
    FLAKY_SEEN.discard("i1")
    url = "https://fake.test/ig-flaky-image"
    code, output = run("-y", "-p", url)
    assert code == 0
    assert t("image_not_saved", "i1") in output
    assert names(out) == ["02 - Clip [v1].mp4"]

    code, output = run("-y", "-p", url)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [v1].mp4"]
    assert (out / "01 - Clip [i1].jpg").read_bytes() == tiny_jpg
    assert t("image_not_saved", "i1") not in output
    assert not said_partial_result(output)


@pytest.mark.parametrize("page", ["ig-no-length-image", "ig-gzip-image"])
def test_a_whole_image_without_a_usable_length_is_saved(
    run: Any, out: Path, tiny_jpg: bytes, page: str
) -> None:
    # No Content-Length to check, or one that counts the gzip body, not the image in it.
    code, output = run("-y", "-p", f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [v1].mp4"]
    assert (out / "01 - Clip [i1].jpg").read_bytes() == tiny_jpg
    assert t("image_not_saved", "i1") not in output
    assert not said_partial_result(output)


@pytest.mark.parametrize("flags", [("-a",), ("-a", "-p")], ids=["single", "playlist"])
@pytest.mark.parametrize("url", [IG_CAROUSEL, LINKED_CAROUSEL], ids=["carousel", "linked"])
def test_audio_mode_never_leaves_an_image_file(
    run: Any, out: Path, flags: tuple[str, ...], url: str
) -> None:
    code, output = run("-y", *flags, url)
    assert code == 0
    assert names(out)
    assert all(name.endswith(".mp3") for name in names(out))
    assert t("item_image_skipped", "i1") in output


def test_an_image_keeps_the_extension_and_bytes_of_its_link(
    run: Any, out: Path, media_url: str
) -> None:
    code, output = run("-y", "-p", "https://fake.test/ig-png-image")
    assert code == 0
    assert names(out) == ["01 - Clip [i1].png", "02 - Clip [v1].mp4"]
    with urllib.request.urlopen(f"{media_url}/tiny.png") as response:
        assert (out / "01 - Clip [i1].png").read_bytes() == response.read()
    assert not said_partial_result(output)


def test_quality_and_container_leave_the_images_as_they_are(
    run: Any, out: Path, tiny_jpg: bytes
) -> None:
    code, output = run("-y", "-p", "-q", "480", "--video-format", "mkv", IG_CAROUSEL)
    assert code == 0
    assert jpgs(out) == ["01 - Clip [i1].jpg", "04 - Clip [i2].jpg"]
    assert len(names(out)) == 4
    assert all((out / name).read_bytes() == tiny_jpg for name in jpgs(out))
    assert not said_partial_result(output)


def test_list_formats_names_every_image_it_skips(run: Any, out: Path) -> None:
    code, output = run("-y", "-l", IG_CAROUSEL)
    assert code == 0
    assert t("item_image_skipped", "i2") in output
    assert t("item_no_media", "i1") not in output


LINKED = "https://fake.test/ig-linked-carousel"


@pytest.mark.parametrize(
    ("flags", "saved"),
    [
        (("-p",), ["01 - Clip [i1].jpg", "02 - Clip [v1].mp4"]),
        ((), ["Clip [i1].jpg", "Clip [v1].mp4"]),
    ],
    ids=["playlist", "single"],
)
def test_an_image_known_only_once_its_link_is_followed_is_saved(
    run: Any, out: Path, tiny_jpg: bytes, flags: tuple[str, ...], saved: list[str]
) -> None:
    # The match_filter only sees a link here: the image is found inside process_video_result.
    code, output = run("-y", *flags, LINKED)
    assert code == 0
    assert names(out) == saved
    assert (out / saved[0]).read_bytes() == tiny_jpg
    assert t("done") in output
    assert not said_partial_result(output)


def test_audio_mode_never_saves_an_image_known_only_once_its_link_is_followed(
    run: Any, out: Path
) -> None:
    code, _ = run("-y", "-a", "-p", LINKED)
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp3"]


@pytest.mark.parametrize(
    ("flags", "result"),
    [(("-a", "-p"), ("partial_result", 1, 2, 1, 0)), (("-l",), None)],
    ids=["audio", "list"],
)
def test_audio_and_list_modes_skip_a_linked_image_as_an_image(
    run: Any, out: Path, flags: tuple[str, ...], result: tuple[Any, ...] | None
) -> None:
    code, output = run("-y", *flags, LINKED)
    assert code == 0
    assert t("item_image_skipped", "i1") in output
    assert "No video formats found" not in output
    if result:
        assert t(*result) in output


def test_wizard_in_audio_mode_saves_a_pick_of_only_images_in_the_video_folder(
    fake_site: Any, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    # URL, picker, folder, cookies. Images go next to the videos, never to the Music folder.
    answers("", "1,4", "", "")
    code = cli.main(["-i", "-a", IG_CAROUSEL])
    output = capfd.readouterr().out
    assert code == 0
    assert names(home / "Videos") == ["01 - Clip [i1].jpg", "04 - Clip [i2].jpg"]
    assert names(home / "Music") == []
    assert str(home / "Videos") in output


def test_wizard_all_on_an_instagram_shaped_carousel_saves_everything(
    run: Any, out: Path, answers: Any
) -> None:
    answers("", "all", "", "", "", "", "")
    code, output = run("-i", IG_CAROUSEL)
    assert code == 0
    assert names(out) == IG_NUMBERED
    assert not said_partial_result(output)


@pytest.mark.parametrize("flags", [(), ("-a",)], ids=["video", "audio"])
def test_wizard_with_a_preset_of_only_images_asks_no_media_step(
    run: Any, replay: Any, out: Path, again: Path, answers: Any, flags: tuple[str, ...]
) -> None:
    # URL, picker (Enter keeps the preset), folder, cookies.
    prompts = answers("", "", "", "")
    code, output = run("-i", *flags, "--playlist-items", "1,4", IG_CAROUSEL)
    assert code == 0
    assert len(prompts) == 4
    assert names(out) == ["01 - Clip [i1].jpg", "04 - Clip [i2].jpg"]

    [line] = commands(output)
    assert "--playlist-items 1,4" in line
    assert " -a " not in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize(
    ("lang", "prompt", "image", "skipped"),
    [
        (
            "es",
            "Selecciona items (Enter = todo lo descargable, 'all' = todo intentable, c = cancelar): ",
            "[imagen]",
            "i1: es una imagen, se omite",
        ),
        (
            "en",
            "Pick items (Enter = everything downloadable, 'all' = try everything, c = cancel): ",
            "[image]",
            "i1: an image, skipping",
        ),
    ],
)
def test_carousel_images_in_both_languages(
    run: Any,
    out: Path,
    answers: Any,
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
    prompt: str,
    image: str,
    skipped: str,
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    prompts = answers(*PICKER_WALK)
    code, output = run("-i", IG_CAROUSEL)
    assert code == 0
    assert prompt in prompts
    assert output.count(image) == 2

    code, output = run("-y", "-a", "-p", IG_CAROUSEL)
    assert code == 0
    assert skipped in output


def test_wizard_reads_the_extension_of_a_url_with_a_query_string(
    run: Any, out: Path, answers: Any
) -> None:
    answers("", "all", "", "", "", "", "")
    code, output = run("-i", "https://fake.test/query-carousel")
    assert code == 0
    assert tag("kind_audio") in output
    assert "[?]" in output
    assert tag("kind_video") in output
    assert names(out) == ["01 - Clip [a1].m4a", "02 - Clip [p1].jpg", "03 - Clip [v1].mp4"]


def test_items_yt_dlp_cannot_fetch_are_never_offered_and_never_saved(
    run: Any, out: Path, again: Path, answers: Any
) -> None:
    url = "https://fake.test/undownloadable-carousel"
    answers(*PICKER_WALK)
    code, output = run("-i", url)
    assert code == 0
    assert output.count(tag("kind_image")) == 3
    assert output.count(tag("kind_video")) == 1
    assert names(out) == ["04 - Clip [v1].mp4"]

    code, output = run("-y", "-p", "-o", str(again), url)
    assert code == 0
    assert names(again) == names(out)
    assert t("partial_result", 1, 4, 0, 3) in output


@pytest.mark.parametrize("cookies", [(), ("--cookies", "firefox")], ids=["without", "with"])
def test_wizard_on_a_mixed_carousel_enter_and_replay(
    run: Any, replay: Any, out: Path, again: Path, answers: Any, cookies: tuple[str, ...]
) -> None:
    url = "https://fake.test/mixed-carousel"
    answers(*PICKER_WALK)
    code, output = run("-i", *cookies, url)
    assert code == 0
    assert tag("kind_image") in output
    assert "[?]" in output
    assert tag("kind_video") in output
    assert tag("kind_audio") in output
    assert names(out) == MIXED_SAVED
    assert not said_partial_result(output)

    [line] = commands(output)
    assert "--playlist-items 1,2,3,4" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


MIXED_SAVED = [
    "01 - Clip [i1].jpg",
    "02 - Clip [p1].jpg",
    "03 - Clip [v1].mp4",
    "04 - Clip [a1].m4a",
]


def test_wizard_all_on_a_mixed_carousel_saves_the_image_too(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers("", "all", "", "", "", "", "")
    code, output = run("-i", "https://fake.test/mixed-carousel")
    assert code == 0
    saved = MIXED_SAVED
    assert names(out) == saved
    assert not said_partial_result(output)

    [line] = commands(output)
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


def test_wizard_enter_on_a_carousel_without_videos_keeps_what_is_offered(
    run: Any, replay: Any, out: Path, again: Path, answers: Any
) -> None:
    answers(*PICKER_WALK)
    code, output = run("-i", "https://fake.test/no-video-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [p1].jpg", "03 - Clip [a1].m4a"]

    [line] = commands(output)
    assert "--playlist-items 1,2,3" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize(
    ("lang", "banner", "image", "unknown"),
    [
        ("es", "Carrusel/Playlist", "[imagen]", "Desconocido"),
        ("en", "Carousel/Playlist", "[image]", "Unknown"),
    ],
)
def test_what_the_user_reads_about_each_kind_in_both_languages(
    run: Any,
    answers: Any,
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
    banner: str,
    image: str,
    unknown: str,
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    answers(*PICKER_WALK)
    _, output = run("-i", "https://fake.test/mixed-carousel")
    assert f"generic / {banner}" in output
    assert image in output
    assert "[?]" in output
    assert "[video]" in output
    assert "[audio]" in output

    answers(*SINGLE_WALK, "")
    _, output = run("-i", "https://fake.test/jpg")
    assert f"generic / {unknown}" in output


GATED = "https://fake.test/needs-cookies"
# detect_site reads the whole URL, and FakeSiteIE ignores the query.
COOKIE_HUNGRY = f"{GATED}?from=x.com"
X_CAROUSEL = f"{CAROUSEL}?from=x.com"
NO_COOKIES = "1"
FIRST_BROWSER = "2"
# Type, quality, container, folder.
VIDEO_STEPS = ("",) * 4
LOGIN_REQUIRED = "Login required"
RATE_LIMIT_REASON = (
    "This content isn't available, try again later. The current session has been rate-limited "
    "by YouTube for up to an hour."
)
GEO_REASON = "This video is not available from your location due to geo restriction"


def asked_for_login(reason: str = "") -> str:
    return t("auth_needed_retry", reason)


def partly_inspected(reason: str = "") -> str:
    return t("probe_partial", reason)


@pytest.fixture
def wizard_on(run: Any, answers: Any) -> Callable[..., tuple[int, str]]:
    """Walk the wizard on ``url``. Every reply must be asked for, and nothing else."""

    def _wizard_on(url: str, *replies: Any, flags: tuple[str, ...] = ()) -> tuple[int, str]:
        prompts = answers(*replies)
        code, output = run("-i", *flags, url)
        assert len(prompts) == len(replies)
        return code, output

    return _wizard_on


def test_wizard_retries_a_gated_video_with_the_browser_it_is_given(
    wizard_on: Any, replay: Any, out: Path, again: Path
) -> None:
    code, output = wizard_on(GATED, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert names(out) == ["Clip.mp4"]
    assert len(history_rows()) == 1
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip.mp4"]


@pytest.mark.parametrize("reply", [NO_COOKIES, "b"], ids=["declines", "back"])
def test_wizard_says_why_a_gated_video_failed_when_cookies_are_declined(
    wizard_on: Any, out: Path, reply: str
) -> None:
    # Then the manual steps and the preview. Not asked for cookies again.
    code, output = wizard_on(GATED, "", reply, *VIDEO_STEPS, "")
    assert code == 1
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert t("probe_failed", LOGIN_REQUIRED) in output
    assert "unknown" not in output
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == []
    assert history_rows() == []


@pytest.mark.parametrize("reply", ["c", EOFError], ids=["cancel", "eof"])
def test_cancelling_at_the_cookie_retry_ends_the_run(wizard_on: Any, reply: Any) -> None:
    code, output = wizard_on(GATED, "", reply)
    assert code == 0
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert t("equiv_cmd") not in output


def test_wizard_keeps_the_picker_on_a_gated_carousel(
    wizard_on: Any, replay: Any, out: Path, again: Path
) -> None:
    code, output = wizard_on(
        "https://fake.test/gated-carousel", "", FIRST_BROWSER, "", *VIDEO_STEPS
    )
    assert code == 0
    assert output.count(tag("kind_video")) == 2
    saved = ["02 - Clip [v1].mp4", "03 - Clip [v2].mp4"]
    assert names(out) == saved
    [line] = commands(output)
    assert "--cookies" in line
    assert "--playlist-items" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


def test_no_playlist_flag_keeps_the_picker_on_a_gated_carousel(wizard_on: Any, out: Path) -> None:
    url = "https://fake.test/gated-carousel"
    code, output = wizard_on(url, "", FIRST_BROWSER, "", *VIDEO_STEPS, flags=("-P",))
    assert code == 0
    assert output.count(tag("kind_video")) == 2
    assert len(names(out)) == 2


def test_declining_a_gated_carousel_goes_manual(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/gated-carousel", "", NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    assert asked_for_login() in output
    assert t("probe_falling_back") in output
    assert names(out) == []


def test_a_host_that_only_ends_in_x_com_is_not_asked_for_cookies_up_front(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on("https://fake.test/carousel?from=dropbox.com", *PICKER_WALK)
    assert code == 0
    assert t("auth_needed_pre") not in output
    assert f"generic / {t('kind_playlist')}" in output
    assert len(names(out)) == 2


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_public_instagram_carousel_is_probed_before_any_cookies_question(
    wizard_on: Any, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    # URL, picker, type, quality, container, folder, and the late cookies step.
    code, output = wizard_on("https://fake.test/carousel?from=instagram.com", *PICKER_WALK)
    assert code == 0
    assert t("auth_needed_pre") not in output
    assert output.count(t("ask_cookies")) == 1
    assert f"instagram / {t('kind_playlist')}" in output
    assert len(names(out)) == 2


def test_a_gated_instagram_post_asks_for_cookies_once_after_the_probe(
    wizard_on: Any, out: Path
) -> None:
    url = f"{GATED}?from=instagram.com"
    code, output = wizard_on(url, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert t("auth_needed_pre") not in output
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line


def test_declining_cookies_on_a_gated_instagram_post_asks_once(wizard_on: Any, out: Path) -> None:
    # Then the manual steps and the preview.
    code, output = wizard_on(f"{GATED}?from=instagram.com", "", NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == []


def test_declining_twice_on_a_cookie_hungry_site_asks_no_third_time(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on(COOKIE_HUNGRY, "", NO_COOKIES, NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert output.count(t("ask_cookies")) == 2
    assert names(out) == []


def test_a_decline_up_front_can_be_taken_back_at_the_retry(wizard_on: Any, out: Path) -> None:
    code, _ = wizard_on(COOKIE_HUNGRY, "", NO_COOKIES, FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert names(out) == ["Clip.mp4"]


def test_a_browser_given_up_front_needs_no_retry(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on(COOKIE_HUNGRY, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert asked_for_login() not in output
    assert names(out) == ["Clip.mp4"]


def test_a_public_carousel_on_a_cookie_hungry_site_asks_once(wizard_on: Any) -> None:
    code, output = wizard_on(X_CAROUSEL, "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1


BACK_TWICE = ("", "b", "", "b")


def test_back_at_the_prompt_before_the_probe_can_be_pressed_again_and_again(
    wizard_on: Any, out: Path
) -> None:
    # Each back reaches the URL prompt again, and the URL asks again.
    code, output = wizard_on(X_CAROUSEL, *BACK_TWICE, "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 3
    assert output.count(t("ask_cookies")) == 3
    assert len(names(out)) == 2


@pytest.mark.parametrize("reply", ["c", EOFError], ids=["cancel", "eof"])
def test_cancelling_at_the_url_prompt_after_backing_twice_ends_the_run(
    wizard_on: Any, out: Path, reply: Any
) -> None:
    code, output = wizard_on(X_CAROUSEL, *BACK_TWICE, reply)
    assert code == 0
    assert t("cancelled") in output
    assert t("equiv_cmd") not in output
    assert names(out) == []


def test_a_url_typed_after_back_that_needs_no_cookies_is_not_asked_up_front(
    wizard_on: Any, out: Path
) -> None:
    # The late cookies step stays, since nothing asked before the walk.
    code, output = wizard_on(X_CAROUSEL, "", "b", CAROUSEL, *PICKER_WALK[1:])
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 1
    assert output.count(t("ask_cookies")) == 2
    assert output.rindex(t("ask_cookies")) > output.index(t("probing"))
    assert len(names(out)) == 2


def test_a_browser_from_the_command_line_skips_the_prompt_before_the_probe(
    wizard_on: Any, out: Path
) -> None:
    # The late cookies step stays, with the browser preselected.
    flags = ("--cookies", "firefox")
    code, output = wizard_on(X_CAROUSEL, *PICKER_WALK, flags=flags)
    assert code == 0
    assert t("auth_needed_pre") not in output
    assert output.count(t("ask_cookies")) == 1
    assert len(names(out)) == 2
    [line] = commands(output)
    assert "--cookies firefox" in line


def cookies_line(value: str) -> str:
    """The running summary's cookies line."""
    return f"✓ {wizard._summary_label('cookies')}: {value}"


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_summary_lists_a_browser_given_up_front(
    wizard_on: Any, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    code, output = wizard_on(COOKIE_HUNGRY, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    line = cookies_line(BROWSERS[0])
    assert line in output
    assert output.index(line) < output.index(t("ask_type"))


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_summary_lists_a_decline_up_front(
    wizard_on: Any, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    code, output = wizard_on(X_CAROUSEL, "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1
    assert cookies_line(t("no_cookies")) in output


def test_the_summary_lists_a_browser_given_after_a_login_wall(wizard_on: Any) -> None:
    code, output = wizard_on(GATED, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert cookies_line(BROWSERS[0]) in output


def test_the_summary_of_the_manual_flow_lists_a_decline_up_front(wizard_on: Any) -> None:
    # The probe fails without a login wall: no retry, no late step.
    url = "https://fake.test/rate-limited?from=x.com"
    code, output = wizard_on(url, "", NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    assert t("probe_falling_back") in output
    assert output.count(t("ask_cookies")) == 1
    assert cookies_line(t("no_cookies")) in output


def test_the_summary_has_no_cookies_line_before_the_late_step(wizard_on: Any) -> None:
    code, output = wizard_on(CAROUSEL, *PICKER_WALK)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1
    assert cookies_line("") not in output[: output.index(t("ask_cookies"))]


@pytest.mark.parametrize("backs", [1, 2])
@pytest.mark.parametrize("reply", ["c", EOFError], ids=["cancel", "eof"])
def test_cancelling_the_prompt_before_the_probe_after_going_back_ends_the_run(
    wizard_on: Any, out: Path, backs: int, reply: Any
) -> None:
    code, output = wizard_on(X_CAROUSEL, *("", "b") * backs, "", reply)
    assert code == 0
    assert output.count(t("auth_needed_pre")) == backs + 1
    assert t("cancelled") in output
    assert t("probing") not in output
    assert t("equiv_cmd") not in output
    assert names(out) == []


def test_the_browser_picked_after_going_back_twice_reaches_the_probe_and_the_command(
    wizard_on: Any, fake_site: list[Any], replay: Any, out: Path, again: Path
) -> None:
    code, output = wizard_on(COOKIE_HUNGRY, *BACK_TWICE, "", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert asked_for_login() not in output
    assert fake_site[0][0] == BROWSERS[0]  # The probe, not just the download.
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == ["Clip.mp4"]


def test_a_decline_after_going_back_leaves_cookies_out_of_the_command(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on(X_CAROUSEL, *BACK_TWICE, "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert len(names(out)) == 2
    [line] = commands(output)
    assert "--cookies" not in line


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_summary_lists_the_retry_s_browser_after_a_decline_up_front(
    wizard_on: Any, monkeypatch: pytest.MonkeyPatch, out: Path, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    code, output = wizard_on(COOKIE_HUNGRY, "", NO_COOKIES, FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 2
    # The summary shows the answer the download uses, not the one the retry replaced.
    assert cookies_line(BROWSERS[0]) in output
    assert cookies_line(t("no_cookies")) not in output
    assert output.index(cookies_line(BROWSERS[0])) < output.index(t("ask_type"))
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_summary_keeps_cookies_first_after_back_at_the_first_step(
    wizard_on: Any, monkeypatch: pytest.MonkeyPatch, out: Path, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(wizard, "LANG", lang)
    # Back at the type step asks the type again, never the cookies answered before the probe.
    code, output = wizard_on(COOKIE_HUNGRY, "", FIRST_BROWSER, "b", *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1
    assert output.count(t("ask_type")) == 2
    # Type, type again, quality, container, folder: each shows the summary.
    assert output.count(cookies_line(BROWSERS[0])) == 5
    last = output[output.rindex(t("summary")) :]
    assert last.index(cookies_line(BROWSERS[0])) < last.index(
        f"✓ {wizard._summary_label('type')}: "
    )
    assert names(out) == ["Clip.mp4"]


def test_the_summary_says_no_cookies_when_the_browser_picked_up_front_cannot_be_read(
    generic: Any, home: Path
) -> None:
    code, output = generic("poster.html?from=x.com", "", CHROME, *VIDEO_STEPS)
    assert code == 0
    assert output.count(no_browser()) == 1
    # What the download will do: the unreadable browser was dropped before the walk.
    assert cookies_line(t("no_cookies")) in output
    assert cookies_line("chrome") not in output
    [line] = commands(output)
    assert "--cookies" not in line
    assert len(names(home / "Videos")) == 1


def test_the_manual_flow_lists_a_browser_given_up_front_and_keeps_it_in_the_command(
    wizard_on: Any,
) -> None:
    # The probe fails without a login wall: no retry, no late step, the browser stays.
    url = "https://fake.test/rate-limited?from=x.com"
    code, output = wizard_on(url, "", FIRST_BROWSER, *VIDEO_STEPS, "")
    assert code == 1
    assert t("probe_falling_back") in output
    assert output.count(t("ask_cookies")) == 1
    assert cookies_line(BROWSERS[0]) in output
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line


def test_the_manual_flow_without_a_prompt_before_it_keeps_the_late_step(wizard_on: Any) -> None:
    # Type, quality, container, folder, cookies, then the preview.
    code, output = wizard_on("https://fake.test/rate-limited", "", *VIDEO_STEPS, FIRST_BROWSER, "")
    assert code == 1
    assert t("auth_needed_pre") not in output
    assert output.count(t("ask_cookies")) == 1
    assert output.index(t("probe_falling_back")) < output.index(t("ask_cookies"))
    assert cookies_line("") not in output
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line


def test_a_keyring_from_the_command_line_skips_the_prompt_before_the_probe(
    wizard_on: Any, fake_site: list[Any], out: Path
) -> None:
    # The late step preselects the browser, and Enter keeps its keyring.
    code, output = wizard_on(X_CAROUSEL, *PICKER_WALK, flags=("--cookies", "brave+kwallet6"))
    assert code == 0
    assert t("auth_needed_pre") not in output
    assert output.count(t("ask_cookies")) == 1
    assert fake_site[0] == ("brave", None, "KWALLET6", None)
    assert len(names(out)) == 2
    [line] = commands(output)
    assert "--cookies brave+kwallet6" in line


def test_an_instagram_url_typed_after_back_is_probed_without_a_second_prompt(
    wizard_on: Any, out: Path
) -> None:
    ig = f"{CAROUSEL}?from=instagram.com"
    code, output = wizard_on(X_CAROUSEL, "", "b", ig, *PICKER_WALK[1:])
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 1
    assert output.count(t("ask_cookies")) == 2
    assert f"instagram / {t('kind_playlist')}" in output
    # The second question is the late step, after the probe.
    assert output.rindex(t("ask_cookies")) > output.index(t("probing"))
    assert len(names(out)) == 2


def test_list_formats_in_the_wizard_survives_back_twice_before_the_probe(
    wizard_urls: Any, fake_site: list[Any], out: Path
) -> None:
    # URL, back, URL, back, URL, cookies, picker, type, quality, container, folder.
    replies = (*BACK_TWICE, "", FIRST_BROWSER, "", "", "", "", str(out))
    code, output, asked = wizard_urls(["-l", X_CAROUSEL], *replies)
    assert asked == 11
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 3
    assert "Available formats for v1" in output
    assert fake_site and {used[0] for used in fake_site} == {BROWSERS[0]}
    assert names(out) == []


def test_cookies_that_do_not_help_end_the_retry_without_a_loop(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on(PRIVATE, "", FIRST_BROWSER, *VIDEO_STEPS, "")
    assert code == 1
    assert t("auth_still_needed", BROWSERS[0]) in output
    assert t("probe_failed", "This post is private") in output
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == []


def test_cookies_from_the_command_line_that_do_not_help_say_so(wizard_on: Any) -> None:
    # The late cookies step stays: nothing has asked yet.
    code, output = wizard_on(PRIVATE, *SINGLE_WALK, "", flags=("--cookies", "firefox"))
    assert code == 1
    assert t("auth_still_needed", "firefox") in output
    assert asked_for_login() not in output


@pytest.mark.parametrize("page,reason", [("rate-limited", RATE_LIMIT_REASON), ("geo", GEO_REASON)])
def test_a_failure_cookies_cannot_fix_is_explained_without_a_prompt(
    wizard_on: Any, page: str, reason: str
) -> None:
    code, output = wizard_on(f"https://fake.test/{page}", *SINGLE_WALK, "")
    assert code == 1
    assert t("probe_failed", reason) in output
    assert asked_for_login() not in output
    assert output.count(t("ask_cookies")) == 1
    assert output.index(t("probe_falling_back")) < output.index(t("ask_cookies"))


def test_a_geo_block_with_metadata_is_not_an_image(wizard_on: Any) -> None:
    code, output = wizard_on("https://fake.test/geo-metadata", *SINGLE_WALK, "")
    assert code == 1
    assert t("probe_failed", GEO_BLOCK) in output
    assert t("image_only_skipped") not in output


def test_an_extractor_bug_is_reported_without_yt_dlps_tail(wizard_on: Any) -> None:
    code, output = wizard_on("https://fake.test/extractor-bug", *SINGLE_WALK, "")
    assert code == 1
    assert t("probe_failed", "Unable to extract video data") in output


def test_declining_a_partly_gated_carousel_keeps_the_picker(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/partial-gated", "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert partly_inspected(LOGIN_REQUIRED) in output
    assert "[?]" in output
    assert tag("kind_image") in output
    assert names(out) == ["01 - Clip [v2].mp4"]
    [line] = commands(output)
    assert "--playlist-items 1" in line


def test_cookies_recover_the_gated_entry_of_a_carousel(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/partial-gated", "", FIRST_BROWSER, "", *VIDEO_STEPS)
    assert code == 0
    assert "[?]" not in output
    assert partly_inspected() not in output
    assert output.count(tag("kind_video")) == 2
    assert names(out) == ["01 - Clip [v2].mp4", "03 - Clip [v1].mp4"]


def test_a_rate_limited_entry_keeps_the_picker_with_no_early_prompt(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on("https://fake.test/partial-rate", *PICKER_WALK)
    assert code == 0
    assert partly_inspected(RATE_LIMIT_REASON) in output
    assert asked_for_login() not in output
    assert output.count(t("ask_cookies")) == 1
    assert output.index("[?]") < output.index(t("ask_cookies"))
    assert names(out) == ["01 - Clip [v2].mp4"]


def test_a_broken_entry_counts_the_same_in_the_wizard_and_with_y_p(
    wizard_on: Any, run: Any, out: Path, again: Path
) -> None:
    url = "https://fake.test/partial-broken"
    code, output = wizard_on(url, "", "all", *VIDEO_STEPS, "")
    assert code == 0
    assert partly_inspected("Unable to extract video data") in output
    assert "[?]" in output
    assert t("partial_result", 1, 3, 1, 1) in output
    [line] = commands(output)
    assert " -p " in f"{line} "
    saved = names(out)
    assert saved == ["01 - Clip [v2].mp4"]
    code, direct = run("-y", "-p", "-o", str(again), url)
    assert code == 0
    assert t("partial_result", 1, 3, 1, 1) in direct
    assert names(again) == saved


def test_a_carousel_with_every_entry_gated_is_a_failure_not_an_empty_one(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on("https://fake.test/all-gated", "", NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert t("no_supported_items") not in output
    assert names(out) == []


def test_an_empty_entry_is_not_called_an_image(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/hole", *PICKER_WALK)
    assert code == 0
    assert "[?]" in output
    assert partly_inspected() not in output
    assert tag("kind_image") not in output
    assert names(out) == ["01 - Clip [v2].mp4"]


@pytest.mark.parametrize("page", ["ig-image", "image-format", "storyboard-only", "thumbnail-image"])
def test_a_lone_image_is_refused_at_once(wizard_on: Any, out: Path, page: str) -> None:
    code, output = wizard_on(f"https://fake.test/{page}", "")
    assert code == 0
    assert t("image_only_skipped") in output
    assert names(out) == []
    assert history_rows() == []


def test_entries_yt_dlp_cannot_fetch_stay_images_with_no_warning_of_failure(wizard_on: Any) -> None:
    code, output = wizard_on("https://fake.test/undownloadable-carousel", *PICKER_WALK)
    assert code == 0
    assert output.count(tag("kind_image")) == 3
    assert output.count(tag("kind_video")) == 1
    assert partly_inspected() not in output


def test_cookies_the_site_rejects_are_still_dropped_by_the_retry(wizard_on: Any, out: Path) -> None:
    url = "https://fake.test/rejects-cookies"
    code, output = wizard_on(url, *SINGLE_WALK, "", flags=("--cookies", "brave"))
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    assert "--cookies" not in line


def test_audio_intent_survives_the_cookie_retry(wizard_on: Any, out: Path) -> None:
    # URL, cookies, type, audio format, folder.
    code, _ = wizard_on(GATED, "", FIRST_BROWSER, "", "", "", flags=("-a",))
    assert code == 0
    assert names(out) == ["Clip.mp3"]


def test_listing_formats_goes_through_the_cookie_retry(
    fake_site: list[Any], answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    # -l alone opens the wizard, so it gets the same retry before listing.
    prompts = answers("", FIRST_BROWSER, *VIDEO_STEPS)
    code = cli.main(["-l", GATED])
    captured = capfd.readouterr()
    assert code == 0
    assert len(prompts) == 6
    assert asked_for_login(LOGIN_REQUIRED) in captured.out + captured.err
    assert "main" in captured.out


TWO_THIRDS = {"-p": (0, ["01 - Clip [v2].mp4"])}
ONE_LEFT_BEHIND = {
    "": (1, ["Clip [v2].mp4"]),
    "-P": (1, ["Clip [v2].mp4"]),
    "-a": (1, ["Clip [v2].mp3"]),
    **TWO_THIRDS,
}
# What main does with these pages: a page and a mode missing here exit 1 and save nothing.
DIRECT_OUTCOMES: dict[str, dict[str, tuple[int, list[str]]]] = {
    "partial-gated": ONE_LEFT_BEHIND,
    "partial-rate": ONE_LEFT_BEHIND,
    "partial-broken": ONE_LEFT_BEHIND,
    "partial-both": {"-p": (0, ["03 - Clip [v2].mp4"])},
    "hole": {
        "": (0, ["Clip [v2].mp4"]),
        "-p": (0, ["01 - Clip [v2].mp4"]),
        "-P": (0, ["Clip [v2].mp4"]),
        "-a": (0, ["Clip [v2].mp3"]),
        "-l": (0, []),
    },
}


@pytest.mark.parametrize("mode", ["", "-p", "-P", "-a", "-l"])
@pytest.mark.parametrize(
    "page",
    [
        "needs-cookies",
        "login-hint",
        "gated-carousel",
        "anonymous-limit",
        "age-gate",
        "rate-limited",
        "geo",
        "geo-metadata",
        "paid-post",
        "no-video-post",
        "drm",
        "extractor-bug",
        "partial-gated",
        "partial-rate",
        "partial-broken",
        "partial-both",
        "all-gated",
        "hole",
        "thumbnail-image",
    ],
)
def test_direct_modes_keep_their_exit_codes_and_files(
    run: Any, out: Path, page: str, mode: str
) -> None:
    code, _ = run("-y", *mode.split(), f"https://fake.test/{page}")
    assert (code, names(out)) == DIRECT_OUTCOMES.get(page, {}).get(mode, (1, []))


def test_wizard_asks_for_cookies_on_a_real_401(generic: Any) -> None:
    code, output = generic("status/401", "", NO_COOKIES, *VIDEO_STEPS, "")
    assert code == 1
    reason = "Unable to download webpage: HTTP Error 401: Unauthorized"
    assert asked_for_login(reason) in output
    assert t("probe_failed", reason) in output


@pytest.mark.parametrize(
    "status,reason",
    [
        (403, "Forbidden"),
        (404, "Not Found"),
        (410, "Gone"),
        (429, "Too Many Requests"),
        (503, "Service Unavailable"),
    ],
)
def test_wizard_explains_a_real_http_error_without_a_prompt(
    generic: Any, status: int, reason: str
) -> None:
    code, output = generic(f"status/{status}", *SINGLE_WALK, "")
    assert code == 1
    message = f"Unable to download webpage: HTTP Error {status}: {reason}"
    assert t("probe_failed", message) in output
    assert asked_for_login() not in output


def test_a_browser_picked_at_the_retry_that_is_not_installed_ends_the_retry(
    generic: Any, home: Path
) -> None:
    code, output = generic("status/401", "", CHROME, *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(no_browser()) == 1
    reason = "Unable to download webpage: HTTP Error 401: Unauthorized"
    assert t("probe_failed", reason) in output
    assert t("probe_falling_back") in output
    assert t("auth_still_needed", "chrome") not in output
    assert "could not find chrome cookies database" not in output
    assert output.count(t("ask_cookies")) == 1
    assert names(home / "Videos") == []


def test_a_browser_that_is_not_installed_is_named_and_the_page_still_detected(
    generic: Any, home: Path
) -> None:
    # No preview: the cookieless probe found the video.
    code, output = generic("poster.html", *SINGLE_WALK, flags=("-i", "--cookies", "chrome"))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert t("probe_failed", "") not in output
    assert detected("kind_video") in output
    assert len(names(home / "Videos")) == 1
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


def test_a_browser_that_is_not_installed_picked_up_front_still_detects_the_page(
    generic: Any, home: Path
) -> None:
    code, output = generic("poster.html?from=x.com", "", CHROME, *VIDEO_STEPS)
    assert code == 0
    assert output.count(no_browser()) == 1
    # detect_site reads the whole URL.
    assert f"x / {t('kind_video')}" in output
    assert output.count(t("ask_cookies")) == 1
    assert len(names(home / "Videos")) == 1


GIVE_UP = "try again with --cookies"


@pytest.mark.parametrize("reply", [NO_COOKIES, "b"], ids=["declines", "back"])
def test_declining_part_of_a_carousel_keeps_the_rest_and_no_cookies_in_the_command(
    wizard_on: Any, replay: Any, out: Path, again: Path, reply: str
) -> None:
    url = "https://fake.test/partial-first"
    code, output = wizard_on(url, "", reply, "", *VIDEO_STEPS)
    assert code == 0
    assert output.count("[?]") == 1
    saved = ["02 - Clip [v2].mp4", "03 - Clip [v3].mp4"]
    assert names(out) == saved
    assert len(history_rows()) == 1
    [line] = commands(output)
    assert "--playlist-items 2,3" in line
    assert "--cookies" not in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == saved


@pytest.mark.parametrize("page", ["partial-first", "gated-carousel"])
@pytest.mark.parametrize("reply", ["c", EOFError], ids=["cancel", "eof"])
def test_cancelling_the_retry_of_a_carousel_saves_nothing(
    wizard_on: Any, out: Path, page: str, reply: Any
) -> None:
    code, output = wizard_on(f"https://fake.test/{page}", "", reply)
    assert code == 0
    assert t("equiv_cmd") not in output
    assert names(out) == []
    assert history_rows() == []


def test_back_at_the_retry_of_a_gated_carousel_is_a_decline(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/gated-carousel", "", "b", *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(t("ask_cookies")) == 1
    assert t("probe_falling_back") in output
    assert names(out) == []


def test_back_at_the_second_prompt_of_a_cookie_hungry_site_asks_no_third_time(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on(COOKIE_HUNGRY, "", NO_COOKIES, "b", *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(t("ask_cookies")) == 2
    assert names(out) == []


@pytest.mark.parametrize("reply", ["c", EOFError], ids=["cancel", "eof"])
def test_cancelling_the_second_prompt_of_a_cookie_hungry_site_ends_the_run(
    wizard_on: Any, reply: Any
) -> None:
    code, output = wizard_on(COOKIE_HUNGRY, "", NO_COOKIES, reply)
    assert code == 0
    assert t("equiv_cmd") not in output


def test_a_wrong_answer_at_the_retry_is_asked_again_in_place(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on(GATED, "", "nope", FIRST_BROWSER, *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == ["Clip.mp4"]


def test_a_declined_login_ends_with_the_cookies_hint(wizard_on: Any) -> None:
    _, output = wizard_on(GATED, "", NO_COOKIES, *VIDEO_STEPS, "")
    assert GIVE_UP in output


def test_cookies_that_did_not_help_get_no_cookies_hint(wizard_on: Any) -> None:
    _, output = wizard_on(PRIVATE, "", FIRST_BROWSER, *VIDEO_STEPS, "")
    assert GIVE_UP not in output


def test_a_wrong_keyring_reads_as_cookies_that_did_not_help(wizard_on: Any) -> None:
    flags = ("--cookies", "brave+basictext")
    code, output = wizard_on(PRIVATE, *SINGLE_WALK, "", flags=flags)
    assert code == 1
    assert t("auth_still_needed", "brave") in output
    assert asked_for_login() not in output
    [line] = commands(output)
    assert "--cookies brave+basictext" in line


def test_one_entry_left_among_failures_is_the_one_saved(
    wizard_on: Any, replay: Any, out: Path, again: Path
) -> None:
    url = "https://fake.test/partial-all-but-one"
    code, output = wizard_on(url, "", NO_COOKIES, "", *VIDEO_STEPS)
    assert code == 0
    assert partly_inspected(LOGIN_REQUIRED) in output
    assert output.count("[?]") == 3
    assert names(out) == ["03 - Clip [v2].mp4"]
    [line] = commands(output)
    assert "--playlist-items 3" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_all_counts_the_failed_entry_and_replays_the_same_files(
    wizard_on: Any, replay: Any, out: Path, again: Path
) -> None:
    url = "https://fake.test/partial-first"
    code, output = wizard_on(url, "", NO_COOKIES, "all", *VIDEO_STEPS)
    assert code == 0
    assert t("partial_result", 2, 3, 0, 1) in output
    [line] = commands(output)
    assert " -p " in f"{line} "
    assert "--cookies" not in line
    code, again_output = replay(line, again)
    assert code == 0
    assert t("partial_result", 2, 3, 0, 1) in again_output
    assert names(again) == names(out)


def test_picking_a_failed_entry_leaves_only_what_can_be_saved(wizard_on: Any, out: Path) -> None:
    url = "https://fake.test/partial-first"
    code, output = wizard_on(url, "", NO_COOKIES, "1", "1,3", *VIDEO_STEPS)
    assert code == 0
    assert t("selection_warning_unsupported", "1") in output
    assert names(out) == ["03 - Clip [v3].mp4"]
    [line] = commands(output)
    assert "--playlist-items 3" in line


def test_cookies_that_leave_an_entry_gated_keep_the_picker_without_a_loop(
    wizard_on: Any, replay: Any, out: Path, again: Path
) -> None:
    url = "https://fake.test/partial-private"
    code, output = wizard_on(url, "", FIRST_BROWSER, "", *VIDEO_STEPS)
    assert code == 0
    assert output.count(t("ask_cookies")) == 1
    assert partly_inspected("This post is private") in output
    assert t("auth_still_needed", BROWSERS[0]) not in output
    assert names(out) == ["01 - Clip [v2].mp4"]
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line
    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_cookies_that_break_a_partial_carousel_are_explained_without_a_loop(
    wizard_on: Any,
) -> None:
    url = "https://fake.test/partial-rejected"
    _, output = wizard_on(url, "", FIRST_BROWSER, *VIDEO_STEPS, "")
    assert output.count(t("ask_cookies")) == 1
    assert t("probe_failed", "Failed to parse JSON") in output
    assert t("auth_still_needed", BROWSERS[0]) not in output
    assert t("cookies_retry", BROWSERS[0]) in output


def test_a_refusal_after_a_decline_says_why_first(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/images-and-gated", "", NO_COOKIES)
    assert code == 0
    assert output.index(partly_inspected(LOGIN_REQUIRED)) < output.index(t("no_supported_items"))
    assert names(out) == []
    assert history_rows() == []


def test_an_error_with_no_words_still_reads_unknown(wizard_on: Any) -> None:
    code, output = wizard_on("https://fake.test/blank-error", *SINGLE_WALK, "")
    assert code == 1
    assert t("probe_failed", "unknown") in output
    assert asked_for_login() not in output


def test_a_text_duration_on_an_image_entry_keeps_the_picker(wizard_on: Any, out: Path) -> None:
    code, output = wizard_on("https://fake.test/text-duration-carousel", *PICKER_WALK)
    assert code == 0
    assert names(out) == ["01 - Clip [i1].jpg", "02 - Clip [v2].mp4"]
    assert tag("kind_image") in output


@pytest.mark.parametrize("page", ["login-hint", "anonymous-limit", "age-gate", "paid-post"])
def test_direct_mode_points_at_cookies_on_a_login_error(run: Any, page: str) -> None:
    code, output = run("-y", f"https://fake.test/{page}")
    assert code == 1
    assert GIVE_UP in output


def test_direct_mode_does_not_repeat_the_hint_to_someone_using_cookies(run: Any) -> None:
    code, output = run("-y", "--cookies", "firefox", "https://fake.test/private")
    assert code == 1
    assert GIVE_UP not in output


@pytest.mark.parametrize("page", ["rate-limited", "geo", "geo-metadata", "drm", "extractor-bug"])
def test_direct_mode_keeps_quiet_when_cookies_cannot_help(run: Any, page: str) -> None:
    code, output = run("-y", f"https://fake.test/{page}")
    assert code == 1
    assert GIVE_UP not in output


@pytest.mark.parametrize(
    "page,hint",
    [
        ("status/401", True),
        ("status/403", False),
        ("status/404", False),
        ("status/410", False),
        ("status/429", False),
        ("status/503", False),
    ],
)
def test_direct_mode_hint_on_real_http_errors(generic: Any, page: str, hint: bool) -> None:
    code, output = generic(page, flags=("-y",))
    assert code == 1
    assert (GIVE_UP in output) is hint


@pytest.mark.parametrize("page", ["login", "private%20clip", "tag:private"])
def test_a_real_404_named_after_a_login_asks_for_nothing(generic: Any, page: str) -> None:
    code, output = generic(page, flags=("-y",))
    assert code == 1
    assert GIVE_UP not in output
    code, output = generic(page, *SINGLE_WALK, "")
    assert code == 1
    assert asked_for_login() not in output
    assert t("probe_failed", "Unable to download webpage: HTTP Error 404: File not found") in output


def test_a_real_404_whose_id_the_prefix_cannot_read_asks_for_nothing(generic: Any) -> None:
    page = "tag:%20private"
    code, output = generic(page, flags=("-y",))
    assert code == 1
    assert GIVE_UP not in output
    code, output = generic(page, *SINGLE_WALK, "")
    assert code == 1
    assert asked_for_login() not in output


def test_direct_mode_does_not_blame_a_refused_connection_on_login(
    home: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-y", "http://127.0.0.1:1/login"]) == 1
    assert GIVE_UP not in capfd.readouterr().out


def test_a_404_hiding_a_private_video_is_rescued_by_the_late_cookies_step(
    wizard_on: Any, out: Path
) -> None:
    # A 404 never asks up front, so this step is all such a site has left.
    *steps, _ = SINGLE_WALK
    code, output = wizard_on("https://fake.test/hidden-404", *steps, FIRST_BROWSER, "")
    assert code == 0
    assert asked_for_login() not in output
    assert t("probe_failed", HTTP_404) in output
    assert output.count(t("ask_cookies")) == 1
    assert names(out) == ["Clip.mp4"]
    assert len(history_rows()) == 1
    [line] = commands(output)
    assert f"--cookies {BROWSERS[0]}" in line


def test_direct_mode_ignores_an_id_with_a_colon_on_a_403(generic: Any) -> None:
    code, output = generic("status/403/tag:%20private", flags=("-y",))
    assert code == 1
    assert GIVE_UP not in output


def test_direct_mode_ignores_an_id_with_a_colon_on_a_refused_connection(
    home: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-y", "http://127.0.0.1:1/tag:%20private"]) == 1
    assert GIVE_UP not in capfd.readouterr().out


def test_wizard_ignores_an_id_with_a_colon_on_a_403(generic: Any) -> None:
    code, output = generic("status/403/tag:%20private", *SINGLE_WALK, "")
    assert code == 1
    assert asked_for_login() not in output
    assert t("probe_failed", "Unable to download webpage: HTTP Error 403: Forbidden") in output


def test_the_reason_of_a_404_drops_all_of_an_id_with_a_colon(generic: Any) -> None:
    code, output = generic("tag:%20private", *SINGLE_WALK, "")
    assert code == 1
    assert t("probe_failed", "Unable to download webpage: HTTP Error 404: File not found") in output


@pytest.mark.parametrize("page", ["404-login", "404-spaced"])
def test_direct_mode_does_not_blame_a_404_on_login(run: Any, page: str) -> None:
    code, output = run("-y", f"https://fake.test/{page}")
    assert code == 1
    assert GIVE_UP not in output


@pytest.mark.parametrize("page", ["404-spaced", "404-colon"])
def test_wizard_does_not_ask_for_cookies_on_a_404_named_after_a_login(
    wizard_on: Any, page: str
) -> None:
    code, output = wizard_on(f"https://fake.test/{page}", *SINGLE_WALK, "")
    assert code == 1
    assert asked_for_login() not in output


def test_wizard_does_not_ask_for_cookies_on_a_404_whose_id_is_a_login_word(
    wizard_on: Any,
) -> None:
    code, output = wizard_on("https://fake.test/404-login", *SINGLE_WALK, "")
    assert code == 1
    assert asked_for_login() not in output
    assert t("probe_failed", "Unable to download webpage: HTTP Error 404: Not Found") in output


# Several URLs in the wizard: one walk each, then every download (spec 19).
IMAGE = "https://fake.test/ig-image"
ONLY_IMAGES = "https://fake.test/only-images"
BRAVE = str(BROWSERS.index("brave") + 2)


def kinds_and_urls() -> list[list[str]]:
    return [row.split("\t")[1:3] for row in history_rows()]


def walk_into(folder: Path, steps: int) -> tuple[str, ...]:
    """All defaults, the folder answered: it's the second-to-last step of a walk."""
    replies = [""] * steps
    replies[-2] = str(folder)
    return tuple(replies)


@pytest.fixture
def wizard_urls(
    fake_site: list[Any],
    answers: Any,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[int, str, int]]:
    """Run ``cli.main(argv)`` as typed: no ``-o``, so no intent flag unless given."""
    monkeypatch.setattr(wizard, "read_clipboard", lambda: None)

    def _go(argv: list[str], *replies: str) -> tuple[int, str, int]:
        prompts = answers(*replies)
        code = cli.main(argv)
        captured = capfd.readouterr()
        return code, captured.out + captured.err, len(prompts)

    return _go


def test_wizard_walks_every_url_then_downloads_them_all(wizard_urls: Any, out: Path) -> None:
    code, output, asked = wizard_urls([VIDEO, CAROUSEL], *walk_into(out, 6), *walk_into(out, 7))
    assert asked == 13
    assert code == 0
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])
    assert kinds_and_urls() == [["video", VIDEO], ["video", CAROUSEL]]
    assert commands(output) == [
        f"download-media -y -o {out} {VIDEO}",
        f"download-media -y -o {out} --playlist-items 2,4 {CAROUSEL}",
    ]
    assert output.count(t("equiv_cmd")) == 1
    assert f"{t('wizard_title')} (2/2)" in output


def test_each_command_of_a_multi_url_walk_replays_without_a_prompt(
    wizard_urls: Any, pasted: Any, out: Path, again: Path
) -> None:
    _, output, _ = wizard_urls([VIDEO, CAROUSEL], *walk_into(out, 6), *walk_into(out, 7))
    for line in commands(output):
        code, _ = pasted(line, again)
        assert code == 0
    assert names(again) == sorted(["Clip.mp4", *BOTH_ITEMS])


def test_wizard_gives_each_url_the_steps_of_its_own_kind(
    media_url: str, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    audio, video = f"{media_url}/audio.html", f"{media_url}/tiny.mp4"
    # Audio: URL, format, folder, cookies. Video: URL, type, quality, container, folder, cookies.
    prompts = answers(*[""] * 4, *[""] * 6)
    code = cli.main([audio, video])
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert len(prompts) == 10
    assert code == 0
    assert names(home / "Music") == ["audio (1).mp3"]
    assert names(home / "Videos") == ["tiny.mp4"]
    assert kinds_and_urls() == [["audio", audio], ["video", video]]
    assert commands(output) == [f"download-media -y -a {audio}", f"download-media -y {video}"]


def test_wizard_gives_a_file_labelled_as_audio_its_type_step_among_several_urls(
    media_url: str, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    ogg, audio = f"{media_url}/theora.ogg", f"{media_url}/audio.html"
    # Labelled video: URL, type, quality, container, folder, cookies. Audio: URL, format,
    # folder, cookies.
    prompts = answers(*[""] * 6, *[""] * 4)
    code = cli.main([ogg, audio])
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert len(prompts) == 10
    assert code == 0
    assert names(home / "Videos") == ["theora.ogg"]
    assert "video" in streams(home / "Videos" / "theora.ogg")
    assert names(home / "Music") == ["audio (1).mp3"]
    assert kinds_and_urls() == [["video", ogg], ["audio", audio]]
    assert commands(output) == [f"download-media -y {ogg}", f"download-media -y -a {audio}"]


@pytest.mark.parametrize(
    ("first", "replies", "music", "videos"),
    [
        # Audio first: its walk sets audio, and the next walk must start at video again.
        ("audio.html", (*AUDIO_WALK, *SINGLE_WALK), ["audio (1).mp3"], ["theora.ogg"]),
        # The labelled file answered 2: the next URL keeps its own default.
        ("theora.ogg", ("", "2", "", "", "", *SINGLE_WALK), ["theora.mp3"], ["tiny.mp4"]),
    ],
    ids=["audio-then-labelled", "labelled-as-audio-then-video"],
)
def test_a_file_labelled_as_audio_among_several_urls_shares_no_answer(
    media_url: str,
    home: Path,
    answers: Any,
    capfd: pytest.CaptureFixture[str],
    first: str,
    replies: tuple[str, ...],
    music: list[str],
    videos: list[str],
) -> None:
    second = "theora.ogg" if first == "audio.html" else "tiny.mp4"
    urls = [f"{media_url}/{first}", f"{media_url}/{second}"]
    prompts = answers(*replies)
    code = cli.main(urls)
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert len(prompts) == len(replies)
    assert code == 0
    assert output.count(t("audio_may_hold_video")) == 1
    assert names(home / "Music") == music
    assert names(home / "Videos") == videos
    assert "video" in streams(home / "Videos" / videos[0])
    assert kinds_and_urls() == [["audio", urls[0]], ["video", urls[1]]]
    assert commands(output) == [f"download-media -y -a {urls[0]}", f"download-media -y {urls[1]}"]


def test_wizard_asks_for_cookies_on_the_second_url_s_login_wall(
    wizard_urls: Any, out: Path
) -> None:
    # URL, cookies at the login wall, type, quality, container, folder.
    gated = ("", BRAVE, "", "", "", str(out))
    code, output, asked = wizard_urls([VIDEO, GATED], *walk_into(out, 6), *gated)
    assert asked == 12
    assert code == 0
    assert asked_for_login(LOGIN_REQUIRED) in output
    assert len(names(out)) == 2
    assert commands(output) == [
        f"download-media -y -o {out} {VIDEO}",
        f"download-media -y -o {out} --cookies brave {GATED}",
    ]


def test_wizard_skips_a_refused_url_and_says_so_at_the_end(wizard_urls: Any, out: Path) -> None:
    code, output, asked = wizard_urls([VIDEO, IMAGE], *walk_into(out, 6), "")
    assert asked == 7
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert t("image_only_skipped") in output
    assert output.rindex(t("wizard_skipped", IMAGE)) > output.index(t("done"))
    assert commands(output) == [f"download-media -y -o {out} {VIDEO}"]


def test_wizard_with_every_url_refused_is_cancelled(wizard_urls: Any, out: Path) -> None:
    code, output, asked = wizard_urls([IMAGE, ONLY_IMAGES], "", "")
    assert asked == 2
    assert code == 0
    assert t("image_only_skipped") in output
    assert t("no_supported_items") in output
    assert t("cancelled") in output
    assert commands(output) == []
    assert names(out) == []


def test_cancel_in_the_second_walk_cancels_the_whole_run(wizard_urls: Any, out: Path) -> None:
    # The second walk: URL, then c at the picker.
    code, output, asked = wizard_urls([VIDEO, CAROUSEL], *walk_into(out, 6), "", "c")
    assert asked == 8
    assert code == 0
    assert names(out) == []
    assert history_rows() == []
    assert t("cancelled") in output
    assert commands(output) == []


def test_urls_typed_together_are_walked_one_by_one(wizard_urls: Any, out: Path) -> None:
    # URL, type, quality, container, folder, cookies.
    first = (f"{VIDEO} {CAROUSEL}", "", "", "", str(out), "")
    # Straight to the picker: picker, type, quality, container, folder, cookies.
    second = ("", "", "", "", str(out), "")
    code, output, asked = wizard_urls([], *first, *second)
    assert asked == 12
    assert code == 0
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])
    assert kinds_and_urls() == [["video", VIDEO], ["video", CAROUSEL]]
    assert f"{t('wizard_title')} (2/2)" in output


def test_wizard_failure_on_one_url_keeps_the_other(wizard_urls: Any, out: Path) -> None:
    # Private reads as a login wall: URL, cookies declined, type, quality, container,
    # folder. Its probe failed, so its preview comes at download time.
    private = ("", "", "", "", "", str(out))
    code, output, asked = wizard_urls([VIDEO, PRIVATE], *walk_into(out, 6), *private, "")
    assert asked == 13
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert kinds_and_urls() == [["video", VIDEO]]
    assert t("failed", PRIVATE) in output
    assert commands(output) == [f"download-media -y -o {out} {VIDEO} {PRIVATE}"]


def test_interactive_flags_preset_every_walk(wizard_urls: Any, out: Path) -> None:
    code, _, asked = wizard_urls(["-i", "-o", str(out), VIDEO, CAROUSEL], *[""] * 13)
    assert asked == 13
    assert code == 0
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])


def test_list_formats_still_walks_and_lists_only_the_first_url(
    wizard_urls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin, not a fix: row 22 takes -l out of the wizard and lists every URL.
    extracted: list[str] = []
    real = FakeSiteIE._real_extract

    def recording(self: FakeSiteIE, url: str) -> dict[str, Any]:
        extracted.append(url)
        return real(self, url)

    monkeypatch.setattr(FakeSiteIE, "_real_extract", recording)
    code, _, asked = wizard_urls(["-l", VIDEO, VIDEO_B], *SINGLE_WALK)
    assert asked == 6
    assert code == 0
    assert VIDEO in extracted
    assert VIDEO_B not in extracted


# Several URLs, attacked: nothing a walk chose reaches the next one (spec 19, D3).
def test_cookies_chosen_in_one_walk_are_not_the_next_walk_s_default(
    wizard_urls: Any, out: Path
) -> None:
    # URL, cookies at the login wall, type, quality, container, folder.
    gated = ("", BRAVE, "", "", "", str(out))
    code, output, asked = wizard_urls([GATED, VIDEO], *gated, *walk_into(out, 6))
    assert asked == 12
    assert code == 0
    assert commands(output) == [
        f"download-media -y -o {out} --cookies brave {GATED}",
        f"download-media -y -o {out} {VIDEO}",
    ]


def test_a_later_walk_probes_without_the_cookies_of_an_earlier_one(
    wizard_urls: Any, tmp_path: Path
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    again = f"{GATED}?again=1"
    code, output, asked = wizard_urls(
        [GATED, again], "", BRAVE, "", "", "", str(first), "", BRAVE, "", "", "", str(second)
    )
    assert asked == 12
    assert code == 0
    assert output.count(asked_for_login(LOGIN_REQUIRED)) == 2
    assert names(first) == names(second) == ["Clip.mp4"]


def test_a_folder_answered_in_one_walk_is_not_the_next_walk_s_default(
    wizard_urls: Any, home: Path, out: Path
) -> None:
    code, output, asked = wizard_urls([VIDEO, VIDEO_B], *walk_into(out, 6), *SINGLE_WALK)
    assert asked == 12
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert names(home / "Videos") == ["Clip.mp4"]
    assert commands(output) == [
        f"download-media -y -o {out} {VIDEO}",
        f"download-media -y {VIDEO_B}",
    ]


def test_items_picked_in_one_walk_do_not_reach_the_next(wizard_urls: Any, out: Path) -> None:
    # The carousel: URL, picker, type, quality, container, folder, cookies.
    picked = ("", "2", "", "", "", str(out), "")
    code, output, asked = wizard_urls(
        [VIDEO, CAROUSEL, VIDEO_B], *walk_into(out, 6), *picked, *walk_into(out, 6)
    )
    assert asked == 19
    assert code == 0
    assert names(out) == ["02 - Clip [v1].mp4", "Clip [v2].mp4", "Clip.mp4"]
    # Both videos had the same choices, so they share a command.
    assert commands(output) == [
        f"download-media -y -o {out} {VIDEO} {VIDEO_B}",
        f"download-media -y -o {out} --playlist-items 2 {CAROUSEL}",
    ]


def test_a_carousel_s_playlist_mode_does_not_reach_a_later_failed_probe(
    wizard_urls: Any, out: Path
) -> None:
    # Private: URL, cookies declined, type, quality, container, folder; then its preview.
    private = ("", "", "", "", "", str(out))
    code, output, asked = wizard_urls([CAROUSEL, PRIVATE], *walk_into(out, 7), *private, "")
    assert asked == 14
    assert code == 1
    assert names(out) == BOTH_ITEMS
    assert t("failed", PRIVATE) in output
    assert commands(output) == [
        f"download-media -y -o {out} --playlist-items 2,4 {CAROUSEL}",
        f"download-media -y -o {out} {PRIVATE}",
    ]


@pytest.mark.parametrize("flags", [("-p",), ("-P",)])
def test_interactive_playlist_flags_yield_to_each_walk_s_own_decision(
    wizard_urls: Any, replay: Any, out: Path, again: Path, flags: tuple[str, ...]
) -> None:
    code, output, asked = wizard_urls(["-i", *flags, "-o", str(out), VIDEO, CAROUSEL], *[""] * 13)
    assert asked == 13
    assert code == 0
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])
    lines = commands(output)
    assert len(lines) == 2
    for line in lines:
        code, _ = replay(line, again)
        assert code == 0
    assert names(again) == names(out)


def test_interactive_audio_flag_presets_every_walk(wizard_urls: Any, out: Path) -> None:
    # Each walk: URL, type (audio preselected), audio format, folder, cookies.
    code, output, asked = wizard_urls(["-i", "-a", "-o", str(out), VIDEO, VIDEO_B], *[""] * 10)
    assert asked == 10
    assert code == 0
    assert names(out) == ["Clip [v2].mp3", "Clip.mp3"]
    assert commands(output) == [f"download-media -y -a -o {out} {VIDEO} {VIDEO_B}"]


# `c` and EOF anywhere in a later walk end the whole run before any download (D4).
@pytest.mark.parametrize("stop", ["c", EOFError])
@pytest.mark.parametrize("at", range(6))
def test_cancel_at_any_prompt_of_a_later_walk_downloads_nothing(
    wizard_urls: Any, out: Path, stop: Any, at: int
) -> None:
    # The second walk: URL, type, quality, container, folder, cookies.
    second = [*[""] * at, stop]
    code, output, asked = wizard_urls([VIDEO, VIDEO_B], *walk_into(out, 6), *second)
    assert asked == 6 + at + 1
    assert code == 0
    assert t("cancelled") in output
    assert t("downloading") not in output
    assert names(out) == []
    assert history_rows() == []
    assert t("equiv_cmd") not in output


@pytest.mark.parametrize(
    ("url", "second"),
    [
        (GATED, ("", "c")),  # At the login wall's cookie prompt.
        (COOKIE_HUNGRY, ("", "c")),  # At the cookie prompt before the probe.
        (COOKIE_HUNGRY, ("", "b", "c")),  # Back from it, then at the URL prompt.
        (COOKIE_HUNGRY, ("", "b", EOFError)),
    ],
)
def test_cancel_at_a_later_walk_s_cookie_prompts_downloads_nothing(
    wizard_urls: Any, out: Path, url: str, second: tuple[Any, ...]
) -> None:
    code, output, asked = wizard_urls([VIDEO, url], *walk_into(out, 6), *second)
    assert asked == 6 + len(second)
    assert code == 0
    assert t("cancelled") in output
    assert names(out) == []
    assert history_rows() == []
    assert t("equiv_cmd") not in output


def test_back_at_a_later_walk_s_prompt_before_the_probe_can_be_pressed_again(
    wizard_urls: Any, out: Path
) -> None:
    second = (*BACK_TWICE, "", NO_COOKIES, "", "", "", "", str(out))
    code, output, asked = wizard_urls([VIDEO, X_CAROUSEL], *walk_into(out, 6), *second)
    assert asked == 17
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 3
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])


def test_each_url_s_cookies_answer_before_the_probe_gets_its_own_command(
    wizard_urls: Any, out: Path
) -> None:
    # A browser up front on the first, nothing asked before the probe on the second.
    first = ("", FIRST_BROWSER, "", "", "", str(out))
    code, output, asked = wizard_urls([COOKIE_HUNGRY, VIDEO], *first, *walk_into(out, 6))
    assert asked == 12
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 1
    # Both pages hold a "Clip", so one of the names carries its id.
    assert names(out) == ["Clip [v1].mp4", "Clip.mp4"]
    assert commands(output) == [
        f"download-media -y -o {out} --cookies {BROWSERS[0]} {shlex.quote(COOKIE_HUNGRY)}",
        f"download-media -y -o {out} {VIDEO}",
    ]


def test_urls_typed_after_back_ask_again_only_for_the_one_that_needs_it(
    wizard_urls: Any, out: Path
) -> None:
    # Back at the first URL's prompt, then a URL off the list followed by an X one: the first
    # is walked with the late step, the X one gets its own prompt before its probe.
    retyped = f"{VIDEO} {X_CAROUSEL}"
    first = ("", "b", retyped, *walk_into(out, 5))
    second = ("", NO_COOKIES, "", "", "", "", str(out))
    code, output, asked = wizard_urls([X_CAROUSEL], *first, *second)
    assert asked == 15
    assert code == 0
    assert output.count(t("auth_needed_pre")) == 2
    assert output.count(t("ask_cookies")) == 3
    assert names(out) == sorted(["Clip.mp4", *BOTH_ITEMS])
    assert f"{t('wizard_title')} (2/2)" in output


def test_cancel_after_a_refusal_skips_nothing_and_downloads_nothing(
    wizard_urls: Any, out: Path
) -> None:
    code, output, asked = wizard_urls([IMAGE, VIDEO], "", "", "c")
    assert asked == 3
    assert code == 0
    assert t("image_only_skipped") in output
    assert t("cancelled") in output
    assert t("wizard_skipped", IMAGE) not in output
    assert names(out) == []
    assert history_rows() == []
    assert t("equiv_cmd") not in output


# Partial outcomes in other orders (D5, D6).
def test_a_refusal_before_a_saved_url_still_exits_clean(wizard_urls: Any, out: Path) -> None:
    code, output, asked = wizard_urls([IMAGE, VIDEO], "", *walk_into(out, 6))
    assert asked == 7
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert f"{t('wizard_title')} (2/2)" in output
    assert output.rindex(t("wizard_skipped", IMAGE)) > output.index(t("done"))
    assert commands(output) == [f"download-media -y -o {out} {VIDEO}"]


def test_a_refusal_and_a_failure_exit_1_and_name_both(wizard_urls: Any, out: Path) -> None:
    private = ("", "", "", "", "", str(out))
    code, output, asked = wizard_urls([IMAGE, PRIVATE], "", *private, "")
    assert asked == 8
    assert code == 1
    assert names(out) == []
    assert history_rows() == []
    assert t("failed", PRIVATE) in output
    assert t("wizard_skipped", IMAGE) in output
    assert commands(output) == [f"download-media -y -o {out} {PRIVATE}"]


def test_a_declined_preview_a_refusal_and_a_save_in_one_run(wizard_urls: Any, out: Path) -> None:
    # Private's preview comes at download time, and is declined.
    private = ("", "", "", "", "", str(out))
    code, output, asked = wizard_urls(
        [PRIVATE, IMAGE, VIDEO], *private, "", *walk_into(out, 6), "n"
    )
    assert asked == 14
    # A URL that reached the download and saved nothing counts, as in direct mode.
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert kinds_and_urls() == [["video", VIDEO]]
    assert output.count(t("done")) == 1
    assert output.rindex(t("wizard_skipped", IMAGE)) > output.index(t("done"))
    assert commands(output) == [f"download-media -y -o {out} {VIDEO}"]


def test_a_typed_url_followed_by_junk_says_the_junk_failed(wizard_urls: Any, out: Path) -> None:
    # Video: URL, type, quality, container, folder, cookies. Junk fails its probe:
    # type, quality, container, folder, cookies; then its preview at download time.
    first = (f"{VIDEO} junk", "", "", "", str(out), "")
    code, output, asked = wizard_urls([], *first, "", "", "", str(out), "", "")
    assert asked == 12
    assert code == 1
    assert names(out) == ["Clip.mp4"]
    assert kinds_and_urls() == [["video", VIDEO]]
    assert t("failed", "junk") in output
    assert commands(output) == [f"download-media -y -o {out} {VIDEO} junk"]


def test_back_at_the_first_step_of_a_later_walk_stays_in_that_walk(
    fake_site: list[Any],
    answers: Any,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    out: Path,
) -> None:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: None)
    # The second walk starts at its type step: b asks it again.
    first = (f"{VIDEO} {VIDEO_B}", "", "", "", str(out), "")
    prompts = answers(*first, "b", "", "", "", str(out), "")
    code = cli.main([])
    output = capfd.readouterr().out
    assert len(prompts) == 12
    assert code == 0
    assert sum(t("ask_url") in prompt for prompt in prompts) == 1
    assert output.count(f"{t('wizard_title')} (2/2)") == 1
    assert names(out) == ["Clip [v2].mp4", "Clip.mp4"]
    assert kinds_and_urls() == [["video", VIDEO], ["video", VIDEO_B]]


def test_a_single_url_keeps_the_title_without_a_counter(wizard_urls: Any, out: Path) -> None:
    code, output, _ = wizard_urls([VIDEO], *walk_into(out, 6))
    assert code == 0
    assert t("wizard_title") in output
    assert f"{t('wizard_title')} (" not in output


@pytest.mark.parametrize(
    ("lang", "second_title", "skipped"),
    [
        ("es", "Wizard de descarga (2/2)", "Omitida, no hay nada que descargar: "),
        ("en", "Download wizard (2/2)", "Skipped, nothing to download: "),
    ],
)
def test_the_counter_and_the_skipped_url_in_both_languages(
    wizard_urls: Any,
    out: Path,
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
    second_title: str,
    skipped: str,
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    code, output, _ = wizard_urls([VIDEO, IMAGE], *walk_into(out, 6), "")
    assert code == 0
    assert second_title in output
    assert f"{skipped}{IMAGE}" in output


# Subtitles (spec 21): -s embeds them in the video and keeps the files.
SUBS = "https://fake.test/subs"  # en subtitles, es auto-captions.
SUBS_FILES = ["Clip.en.vtt", "Clip.es.vtt", "Clip.mp4"]


def subtitle_streams(path: Path) -> list[tuple[str, str]]:
    """Codec and language of each subtitle stream ffprobe finds in ``path``, in order."""
    probe = ["ffprobe", "-v", "error", "-select_streams", "s"]
    probe += ["-show_entries", "stream=codec_name:stream_tags=language", "-of", "csv=p=0"]
    out = subprocess.run([*probe, str(path)], capture_output=True, text=True, timeout=15).stdout
    return [tuple(line.split(",")) for line in out.splitlines()]  # type: ignore[misc]


def never_said(key: str, output: str) -> bool:
    """Whether no part of message ``key`` shows up, whatever its arguments."""
    chunks = t(key, *("\0",) * t(key).count("{}")).split("\0")
    return max(chunks, key=len) not in output


def test_subs_are_embedded_and_kept_as_files(run: Any, out: Path) -> None:
    code, output = run("-y", "-s", SUBS)
    assert code == 0
    assert names(out) == SUBS_FILES
    # --sub-langs order (es,en), not the extractor's.
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "spa"), ("mov_text", "eng")]
    assert output.count(t("done")) == 1
    assert never_said("subs_none", output)
    assert never_said("subs_not_embedded", output)
    assert len(history_rows()) == 1


def test_subs_are_embedded_in_a_merged_mkv(run: Any, out: Path) -> None:
    code, _ = run("-y", "-s", "--video-format", "mkv", "https://fake.test/subs-merged")
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.es.vtt", "Clip.mkv"]
    assert len(subtitle_streams(out / "Clip.mkv")) == 2


def test_subs_are_embedded_in_a_webm(run: Any, out: Path) -> None:
    code, output = run("-y", "-s", "--video-format", "webm", "https://fake.test/subs-webm")
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.webm"]
    assert subtitle_streams(out / "Clip.webm") == [("webvtt", "eng")]
    assert never_said("subs_not_embedded", output)


def default_flags(path: Path) -> list[str]:
    """The ``default`` disposition of each subtitle stream in ``path``, in order."""
    probe = ["ffprobe", "-v", "error", "-select_streams", "s"]
    probe += ["-show_entries", "stream_disposition=default", "-of", "csv=p=0"]
    out = subprocess.run([*probe, str(path)], capture_output=True, text=True, timeout=15).stdout
    return out.split()


@pytest.mark.parametrize(
    ("flags", "page", "video", "expected"),
    [
        # With the default es,en, a default track shows Spanish on English content unasked.
        (("--video-format", "mkv"), "subs-merged", "Clip.mkv", ["0", "0"]),
        (("--video-format", "webm"), "subs-webm-two", "Clip.webm", ["0", "0"]),
        # The mp4 muxer always enables the first track; no ffmpeg option stops it.
        ((), "subs", "Clip.mp4", ["1", "0"]),
    ],
    ids=["mkv", "webm", "mp4"],
)
def test_no_subtitle_track_is_marked_default_where_the_container_allows(
    run: Any, out: Path, flags: tuple[str, ...], page: str, video: str, expected: list[str]
) -> None:
    code, _ = run("-y", "-s", *flags, f"https://fake.test/{page}")
    assert code == 0
    assert len(subtitle_streams(out / video)) == 2
    assert default_flags(out / video) == expected


def test_sub_langs_alone_turns_subtitles_on(run: Any, replay: Any, out: Path, again: Path) -> None:
    code, output = run("-y", "--sub-langs", "en", SUBS)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-s" in argv
    assert argv[argv.index("--sub-langs") + 1] == "en"

    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


@pytest.mark.parametrize("url", ["https://fake.test/subs-fr", VIDEO])
def test_no_subtitles_in_the_languages_is_a_warning(run: Any, out: Path, url: str) -> None:
    code, output = run("-y", "-s", url)
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("done")) == 1
    assert output.count(t("subs_none", "es,en")) == 1
    assert len(history_rows()) == 1


def test_a_subtitle_ffmpeg_cannot_read_is_not_a_failure(run: Any, out: Path) -> None:
    url = "https://fake.test/subs-ttml"
    code, output = run("-y", "-s", url)
    assert code == 0
    assert output.count(t("done")) == 1
    assert t("failed", url) not in output
    assert t("subs_not_embedded", "Clip.mp4") in output
    assert never_said("subs_none", output)
    # Nothing left from the embed attempt: no Clip.temp.mp4.
    assert names(out) == ["Clip.en.ttml", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == []
    assert len(history_rows()) == 1


def test_a_subtitle_left_out_in_a_relative_folder_starting_with_a_dash(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    # The last -o wins over the one the fixture passes.
    code, output = run("-y", "-s", "--output=-clips", "https://fake.test/subs-ttml")
    assert code == 0
    assert names(work / "-clips") == ["Clip.en.ttml", "Clip.mp4"]
    assert t("subs_not_embedded", "Clip.mp4") in output


def test_srt_cannot_go_in_a_webm(run: Any, out: Path) -> None:
    code, output = run("-y", "-s", "--video-format", "webm", "https://fake.test/subs-webm-srt")
    assert code == 0
    assert t("subs_not_embedded", "Clip.webm") in output
    assert names(out) == ["Clip.en.srt", "Clip.webm"]
    assert output.count(t("done")) == 1


def test_carousel_with_subtitles_on_one_item(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", "https://fake.test/subs-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [s1].en.vtt", "01 - Clip [s1].mp4", "02 - Clip [s2].mp4"]
    assert subtitle_streams(out / "01 - Clip [s1].mp4") == [("mov_text", "eng")]
    assert subtitle_streams(out / "02 - Clip [s2].mp4") == []
    assert never_said("subs_none", output)
    assert never_said("partial_result", output)
    assert output.count(t("done")) == 1


def test_carousel_item_with_an_unreadable_subtitle_still_counts(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", "https://fake.test/subs-ttml-carousel")
    assert code == 0
    assert names(out) == [
        "01 - Clip [s1].en.vtt",
        "01 - Clip [s1].mp4",
        "02 - Clip [s2].en.ttml",
        "02 - Clip [s2].mp4",
    ]
    assert subtitle_streams(out / "01 - Clip [s1].mp4") == [("mov_text", "eng")]
    assert t("subs_not_embedded", "02 - Clip [s2].mp4") in output
    assert t("subs_not_embedded", "01 - Clip [s1].mp4") not in output
    assert never_said("partial_result", output)
    assert output.count(t("done")) == 1
    assert len(history_rows()) == 1


@pytest.mark.parametrize("audio_format", ["mp3", "m4a"])
def test_audio_keeps_subtitles_as_files_only(run: Any, out: Path, audio_format: str) -> None:
    code, output = run("-y", "-a", "-f", audio_format, "-s", SUBS)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.es.vtt", f"Clip.{audio_format}"]
    assert subtitle_streams(out / f"Clip.{audio_format}") == []
    assert never_said("subs_not_embedded", output)
    assert never_said("subs_none", output)


def test_audio_without_subtitles_warns(run: Any, out: Path) -> None:
    code, output = run("-y", "-a", "-s", VIDEO)
    assert code == 0
    assert names(out) == ["Clip.mp3"]
    assert output.count(t("subs_none", "es,en")) == 1


def test_subs_rerun_keeps_one_stream_per_language(run: Any, out: Path) -> None:
    run("-y", "-s", SUBS)
    code, output = run("-y", "-s", SUBS)
    assert code == 0
    assert names(out) == SUBS_FILES
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "spa"), ("mov_text", "eng")]
    assert never_said("subs_not_embedded", output)


def test_wizard_carries_subs_from_the_command_line(run: Any, out: Path, answers: Any) -> None:
    answers(*SINGLE_WALK)
    code, _ = run("-i", "-s", SUBS)
    assert code == 0
    assert names(out) == SUBS_FILES
    assert len(subtitle_streams(out / "Clip.mp4")) == 2


def test_plain_wizard_writes_no_subtitles(run: Any, out: Path, answers: Any) -> None:
    answers(*SINGLE_WALK)
    code, output = run("-i", SUBS)
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == []
    assert never_said("subs_none", output)


# Spec 21, attacked: the embed step never fails a run, never loses a file.
def leftovers(folder: Path) -> list[str]:
    """Anything an interrupted embed or merge leaves behind."""
    return [name for name in names(folder) if ".temp." in name or name.endswith(".part")]


def test_one_broken_language_blocks_the_embed_but_keeps_every_file(run: Any, out: Path) -> None:
    url = "https://fake.test/subs-one-broken"  # en.vtt, plus a TTML file as es.
    code, output = run("-y", "-s", url)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.es.ttml", "Clip.mp4"]
    assert leftovers(out) == []
    # One ffmpeg call carries every language, so the good one goes too: the warning says so.
    assert subtitle_streams(out / "Clip.mp4") == []
    assert streams(out / "Clip.mp4") == {"video", "audio"}
    assert output.count(t("subs_not_embedded", "Clip.mp4")) == 1
    assert t("failed", url) not in output
    assert never_said("subs_none", output)
    assert output.count(t("done")) == 1
    assert len(history_rows()) == 1


def test_an_embed_that_fails_after_starting_leaves_no_temp_file(run: Any, out: Path) -> None:
    url = "https://fake.test/subs-webm-wrong-media"
    code, output = run("-y", "-s", "--video-format", "webm", url)
    assert code == 0
    # ffmpeg created Clip.temp.webm before the webm muxer refused the stream.
    assert names(out) == ["Clip.en.vtt", "Clip.webm"]
    assert streams(out / "Clip.webm") == {"video", "audio"}
    assert output.count(t("subs_not_embedded", "Clip.webm")) == 1
    assert t("failed", url) not in output
    assert output.count(t("done")) == 1
    assert len(history_rows()) == 1


def test_a_garbage_subtitle_file_never_fails_the_run(run: Any, out: Path) -> None:
    # ffmpeg reads it as an empty track; whether it warns is ffmpeg's call, the files are ours.
    code, output = run("-y", "-s", "https://fake.test/subs-garbage")
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert leftovers(out) == []
    assert streams(out / "Clip.mp4") >= {"video", "audio"}
    assert output.count(t("done")) == 1


@pytest.mark.parametrize(
    ("flags", "page", "saved", "expected"),
    [
        ((), "subs-srt", ["Clip.en.srt", "Clip.mp4"], [("mov_text", "eng")]),
        ((), "subs-ass", ["Clip.en.ass", "Clip.mp4"], [("mov_text", "eng")]),
        (
            ("--video-format", "mkv"),
            "subs-merged-srt",
            ["Clip.en.srt", "Clip.mkv"],
            [("subrip", "eng")],
        ),
        (
            ("--video-format", "mkv"),
            "subs-merged-ass",
            ["Clip.en.ass", "Clip.mkv"],
            [("ass", "eng")],
        ),
        (
            (),
            "subs-merged",
            ["Clip.en.vtt", "Clip.es.vtt", "Clip.mp4"],
            [("mov_text", "spa"), ("mov_text", "eng")],
        ),
    ],
    ids=["srt-mp4", "ass-mp4", "srt-mkv", "ass-mkv", "merged-mp4"],
)
def test_each_container_takes_the_formats_it_can_hold(
    run: Any, out: Path, flags: tuple[str, ...], page: str, saved: list[str], expected: list[Any]
) -> None:
    code, output = run("-y", "-s", *flags, f"https://fake.test/{page}")
    assert code == 0
    assert names(out) == saved
    assert subtitle_streams(out / saved[-1]) == expected
    assert never_said("subs_not_embedded", output)
    assert never_said("subs_none", output)


@pytest.mark.parametrize(
    ("flags", "page", "saved", "kept"),
    [
        (("--video-format", "webm"), "subs-webm-ass", ["Clip.en.ass", "Clip.webm"], []),
        # webm takes the vtt and skips the srt: one stream for two files.
        (
            ("--video-format", "webm"),
            "subs-webm-mixed",
            ["Clip.en.vtt", "Clip.es.srt", "Clip.webm"],
            [("webvtt", "eng")],
        ),
    ],
    ids=["ass-webm", "srt-next-to-vtt-webm"],
)
def test_a_container_that_cannot_hold_every_file_warns(
    run: Any, out: Path, flags: tuple[str, ...], page: str, saved: list[str], kept: list[Any]
) -> None:
    url = f"https://fake.test/{page}"
    code, output = run("-y", "-s", *flags, url)
    assert code == 0
    assert names(out) == saved
    assert subtitle_streams(out / saved[-1]) == kept
    assert output.count(t("subs_not_embedded", saved[-1])) == 1
    assert t("failed", url) not in output
    assert output.count(t("done")) == 1


def test_a_subtitle_404_still_aborts_a_single_video(run: Any, out: Path) -> None:
    # Out of scope (spec 21): pinned so a change shows up, not endorsed.
    url = "https://fake.test/subs-404"
    code, output = run("-y", "-s", url)
    assert code == 1
    assert t("failed", url) in output
    assert names(out) == []
    assert never_said("subs_none", output)
    assert never_said("subs_not_embedded", output)
    assert history_rows() == []


def test_a_subtitle_404_in_playlist_mode_saves_the_video_and_says_so(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", "https://fake.test/subs-404-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [s1].mp4", "02 - Clip [s2].mp4"]
    assert output.count(t("subs_none", "es,en")) == 1
    assert never_said("subs_not_embedded", output)
    assert never_said("partial_result", output)


def test_carousel_with_images_and_one_subtitled_video(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", "https://fake.test/subs-image-carousel")
    assert code == 0
    assert names(out) == ["02 - Clip [s1].en.vtt", "02 - Clip [s1].mp4"]
    assert subtitle_streams(out / "02 - Clip [s1].mp4") == [("mov_text", "eng")]
    assert t("partial_result", 1, 3, 2, 0) in output
    assert never_said("subs_none", output)
    assert never_said("subs_not_embedded", output)


def test_carousel_where_no_item_has_subtitles_warns_once(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", "-s", "https://fake.test/nosubs-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [s1].mp4", "02 - Clip [s2].mp4"]
    assert output.count(t("subs_none", "es,en")) == 1
    assert output.count(t("done")) == 1


def test_single_mode_carousel_embeds_per_item_and_survives_a_bad_track(run: Any, out: Path) -> None:
    # Without -p, ignoreerrors is off: an escaping embed error would fail the whole URL.
    url = "https://fake.test/subs-ttml-carousel"
    code, output = run("-y", "-s", url)
    assert code == 0
    assert names(out) == ["Clip [s1].en.vtt", "Clip [s1].mp4", "Clip [s2].en.ttml", "Clip [s2].mp4"]
    assert subtitle_streams(out / "Clip [s1].mp4") == [("mov_text", "eng")]
    assert output.count(t("subs_not_embedded", "Clip [s2].mp4")) == 1
    assert t("subs_not_embedded", "Clip [s1].mp4") not in output
    assert t("failed", url) not in output
    assert leftovers(out) == []


@pytest.mark.parametrize(
    ("urls", "warned_after_first_done"),
    [((SUBS, VIDEO), True), ((VIDEO, SUBS), False)],
    ids=["second-has-none", "first-has-none"],
)
def test_each_url_gets_its_own_subtitle_report(
    run: Any, out: Path, urls: tuple[str, str], warned_after_first_done: bool
) -> None:
    code, output = run("-y", "-s", *urls)
    assert code == 0
    warning = t("subs_none", "es,en")
    assert output.count(warning) == 1
    assert output.count(t("done")) == 2
    first_done = output.index(t("done"))
    assert (output.index(warning) > first_done) is warned_after_first_done
    assert len(history_rows()) == 2


def test_each_url_names_its_own_file_that_could_not_hold_subtitles(run: Any, out: Path) -> None:
    code, output = run("-y", "-s", "https://fake.test/subs-ttml", SUBS)
    assert code == 0
    # The second video takes "Clip [v1]" (another page owns Clip.mp4), and embeds fine.
    assert subtitle_streams(out / "Clip [v1].mp4") == [("mov_text", "spa"), ("mov_text", "eng")]
    warning = t("subs_not_embedded", "Clip.mp4")
    assert output.count(warning) == 1
    assert output.index(warning) < output.index(t("done"))
    assert t("subs_not_embedded", "Clip [v1].mp4") not in output


def test_same_title_from_another_page_gets_its_own_subtitles(run: Any, out: Path) -> None:
    run("-y", "-s", SUBS)
    code, _ = run("-y", "-s", "https://fake.test/subs-en")
    assert code == 0
    assert names(out) == ["Clip [v1].en.vtt", "Clip [v1].mp4", *SUBS_FILES]
    assert subtitle_streams(out / "Clip [v1].mp4") == [("mov_text", "eng")]
    # The first page's file is untouched.
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "spa"), ("mov_text", "eng")]


def test_no_subtitles_names_the_languages_asked_for(run: Any, out: Path) -> None:
    code, output = run("-y", "--sub-langs", "fr,de", SUBS)
    assert code == 0
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("subs_none", "fr,de")) == 1
    assert t("subs_none", "es,en") not in output


def test_embed_step_keeps_yt_dlps_own_tag(run: Any, out: Path) -> None:
    _, output = run("-y", "-s", SUBS)
    assert f'[EmbedSubtitle] Embedding subtitles in "{out / "Clip.mp4"}"' in output


def test_cookie_fallback_retry_carries_the_subtitle_report(
    run: Any, out: Path, fake_site: list[Any]
) -> None:
    code, output = run("-y", "-s", "--cookies", "brave", "https://fake.test/rejects-cookies")
    assert code == 0
    assert t("cookies_retry", "brave") in output
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("subs_none", "es,en")) == 1


@pytest.mark.parametrize(
    ("flags", "url", "saved"),
    [
        (
            ("--video-format", "mkv"),
            "https://fake.test/subs-merged",
            ["Clip.en.vtt", "Clip.es.vtt", "Clip.mkv"],
        ),
        (("--video-format", "webm"), "https://fake.test/subs-webm", ["Clip.en.vtt", "Clip.webm"]),
    ],
    ids=["mkv", "webm"],
)
def test_subs_rerun_keeps_the_source_tag_and_one_stream_per_language(
    run: Any, out: Path, flags: tuple[str, ...], url: str, saved: list[str]
) -> None:
    run("-y", "-s", *flags, url)
    before = subtitle_streams(out / saved[-1])
    assert before
    code, output = run("-y", "-s", *flags, url)
    assert code == 0
    # A lost purl/comment tag would make the rerun save "Clip [v1].*" next to it.
    assert names(out) == saved
    assert subtitle_streams(out / saved[-1]) == before
    assert never_said("subs_not_embedded", output)


def test_embedding_keeps_the_source_url_tag(run: Any, out: Path) -> None:
    run("-y", "-s", SUBS)
    assert subtitle_streams(out / "Clip.mp4")
    # The tags reruns read to tell this video from another one with the same title.
    probe = ["ffprobe", "-v", "error", "-show_entries", "format_tags=purl,comment"]
    probe += ["-of", "default=nw=1:nk=1", str(out / "Clip.mp4")]
    tags = subprocess.run(probe, capture_output=True, text=True, timeout=15).stdout.split()
    assert SUBS in tags


def test_rerun_with_more_languages_replaces_the_tracks(run: Any, out: Path) -> None:
    run("-y", "--sub-langs", "en", SUBS)
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    code, _ = run("-y", "-s", SUBS)
    assert code == 0
    assert names(out) == SUBS_FILES
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "spa"), ("mov_text", "eng")]


def test_rerun_with_subtitles_over_a_plain_file_embeds_them(run: Any, out: Path) -> None:
    run("-y", SUBS)
    assert subtitle_streams(out / "Clip.mp4") == []
    code, output = run("-y", "-s", SUBS)
    assert code == 0
    assert names(out) == SUBS_FILES
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "spa"), ("mov_text", "eng")]
    assert never_said("subs_none", output)


def test_sub_langs_alone_starting_with_a_dash_turns_subtitles_on(
    run: Any, replay: Any, out: Path, again: Path
) -> None:
    code, output = run("-y", "--sub-langs=-es,en", SUBS)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-s" in argv
    assert "--sub-langs=-es,en" in argv

    code, _ = replay(line, again)
    assert code == 0
    assert names(again) == names(out)


def test_sub_langs_alone_with_audio_keeps_the_files_only(run: Any, out: Path) -> None:
    code, output = run("-y", "-a", "--sub-langs", "en", SUBS)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp3"]
    [line] = commands(output)
    argv = shlex.split(line)
    assert {"-a", "-s"} <= set(argv)
    assert argv[argv.index("--sub-langs") + 1] == "en"


def test_sub_langs_alone_in_direct_mode_after_the_preview(
    run: Any, out: Path, answers: Any
) -> None:
    prompts = answers("")
    code, output = run("--sub-langs", "en", SUBS)
    assert code == 0
    assert len(prompts) == 1
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    assert "-s" in shlex.split(commands(output)[0])


def test_wizard_carries_sub_langs_alone_and_prints_them(run: Any, out: Path, answers: Any) -> None:
    answers(*SINGLE_WALK)
    code, output = run("-i", "--sub-langs", "en", SUBS)
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-s" in argv
    assert argv[argv.index("--sub-langs") + 1] == "en"


@pytest.mark.parametrize(
    ("lang", "none", "not_embedded"),
    [
        (
            "es",
            "No se pudo guardar ningún subtítulo en es,en; el contenido se guardó sin ellos.",
            "Clip.mp4: uno o más subtítulos no se pudieron incrustar; "
            "todos se conservan como archivos aparte.",
        ),
        (
            "en",
            "No subtitles in es,en could be saved; the media was saved without them.",
            # The page's only track is TTML: none was embedded, and the message must hold.
            "Clip.mp4: one or more subtitles couldn't be embedded; "
            "all of them are kept as separate files.",
        ),
    ],
)
def test_subtitle_warnings_in_both_languages(
    run: Any, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str, none: str, not_embedded: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    code, output = run("-y", "-s", "https://fake.test/subs-ttml")
    assert code == 0
    assert not_embedded in output
    code, output = run("-y", "-s", "https://fake.test/subs-fr")
    assert code == 0
    assert none in output


# A browser whose cookies can't be read (spec 26). The isolated home holds no browser at all,
# and isolated_env's dead session bus stands for a keyring that can't be reached.
CHROME = str(BROWSERS.index("chrome") + 2)
KEYRING_ERROR = "[Errno 2] No such file or directory"


def no_browser(browser: str = "chrome") -> str:
    return t("cookies_no_browser", browser)


def keyring_hint(browser: str = "chrome") -> str:
    return t("cookies_keyring_hint", browser)


@pytest.fixture
def direct(
    media_url: str, home: Path, out: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, str]]:
    """Run loopback pages through the real generic extractor in direct mode.

    ``replies`` answer the preview's confirmation, once per URL; without them, any prompt fails.
    """

    def _direct(
        *argv: str, pages: tuple[str, ...], replies: tuple[str, ...] = ()
    ) -> tuple[int, str]:
        prompts = answers(*replies)
        code = cli.main(["-o", str(out), *argv, *(f"{media_url}/{page}" for page in pages)])
        captured = capfd.readouterr()
        assert prompts == [t("ask_confirm")] * len(replies)
        return code, captured.out + captured.err

    return _direct


@pytest.fixture
def unreadable_chrome() -> None:
    """A Chrome profile with one v11 cookie: decrypting it asks the keyring for its key."""
    profile = Path(os.environ["XDG_CONFIG_HOME"]) / "google-chrome" / "Default"
    profile.mkdir(parents=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '20')")
    con.execute(
        "CREATE TABLE cookies (host_key BLOB, name BLOB, value BLOB, encrypted_value BLOB,"
        " path BLOB, expires_utc INTEGER, is_secure INTEGER)"
    )
    con.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, 0, 0)",
        (b"127.0.0.1", b"s", b"", b"v11" + b"\0" * 16, b"/"),
    )
    con.commit()
    con.close()


def test_a_missing_browser_does_not_stop_a_playlist(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("multi.html",))
    assert code == 0
    assert names(out) == MULTI
    assert output.count(no_browser()) == 1
    assert "Traceback" not in output
    assert t("cookies_retry", "chrome") not in output
    assert keyring_hint() not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert len(history_rows()) == 1


def test_a_missing_browser_keeps_the_chosen_items(direct: Any, out: Path) -> None:
    flags = ("-y", "--playlist-items", "1,2", "--cookies", "chrome")
    code, output = direct(*flags, pages=("multi.html",))
    assert code == 0
    assert len(names(out)) == 2
    assert no_browser() in output
    [line] = commands(output)
    assert "--playlist-items 1,2" in line
    assert "--cookies" not in shlex.split(line)


def test_a_missing_firefox_is_named(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "firefox", pages=("multi.html",))
    assert code == 0
    assert no_browser("firefox") in output
    assert names(out) == MULTI


def test_a_missing_browser_is_named_for_every_url(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("multi.html", "poster.html"))
    assert code == 0
    assert len(names(out)) == 5
    assert output.count(no_browser()) == 2
    assert "Traceback" not in output


def test_a_dead_page_with_a_missing_browser_fails_without_a_keyring_hint(
    direct: Any, media_url: str, out: Path
) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("status/404",))
    assert code == 1
    assert t("failed", f"{media_url}/status/404") in output
    assert no_browser() in output
    assert keyring_hint() not in output
    assert GIVE_UP not in output
    assert "Traceback" not in output
    [line] = commands(output)
    assert "--cookies chrome" in line
    assert history_rows() == []
    assert names(out) == []


def test_a_single_video_with_a_missing_browser_says_so_instead_of_retrying(
    direct: Any, out: Path
) -> None:
    code, output = direct("-y", "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    assert len(names(out)) == 1
    assert no_browser() in output
    assert t("cookies_retry", "chrome") not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


def test_a_single_dead_page_with_a_missing_browser_gets_no_keyring_hint(direct: Any) -> None:
    code, output = direct("-y", "--cookies", "chrome", pages=("status/404",))
    assert code == 1
    assert no_browser() in output
    assert keyring_hint() not in output
    assert "--cookies chrome" in commands(output)[0]


def test_a_login_wall_with_a_missing_browser_suggests_another_browser(direct: Any) -> None:
    code, output = direct("-y", "--cookies", "chrome", pages=("status/401",))
    assert code == 1
    assert no_browser() in output
    assert GIVE_UP in output
    assert keyring_hint() not in output


def test_formats_are_listed_without_a_missing_browser(direct: Any, media_url: str) -> None:
    code, output = direct("-l", "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    assert no_browser() in output
    assert t("failed", f"{media_url}/poster.html") not in output
    assert "Available formats" in output


def test_a_missing_browser_at_the_last_wizard_step_saves_the_picked_items(
    generic: Any, home: Path
) -> None:
    code, output = generic("multi.html", *PICKER_WALK[:-1], CHROME)
    assert code == 0
    assert names(home / "Videos") == MULTI
    assert no_browser() in output
    assert "Traceback" not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


NO_DATABASE = "could not find chrome cookies database"


def without_cookies(browser: str = "chrome") -> str:
    return t("done_without_cookies", browser)


def after_done(output: str) -> list[str]:
    """The line after each t("done")."""
    lines = [line.strip() for line in output.splitlines()]
    return [lines[i + 1] for i, line in enumerate(lines) if line == t("done")]


@pytest.mark.parametrize(
    ("flags", "page"),
    [
        ((), "poster.html"),
        (("-p",), "multi.html"),
        (("--playlist-items", "1,2"), "multi.html"),
    ],
)
def test_a_run_without_the_asked_cookies_says_so_after_done(
    direct: Any, flags: tuple[str, ...], page: str
) -> None:
    code, output = direct("-y", *flags, "--cookies", "chrome", pages=(page,))
    assert code == 0
    assert output.count(without_cookies()) == 1
    assert after_done(output) == [without_cookies()]


def test_a_partial_result_without_the_asked_cookies_says_so_after_done(direct: Any) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("half-gone.html",))
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    assert after_done(output) == [without_cookies()]


def test_nothing_saved_without_the_asked_cookies_has_no_closing_line(direct: Any) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("status/404",))
    assert code == 1
    assert without_cookies() not in output


def test_cookies_refused_by_the_site_say_so_after_done(run: Any, out: Path) -> None:
    code, output = run("-y", "--cookies", "brave", "https://fake.test/rejects-cookies")
    assert code == 0
    assert len(names(out)) == 1
    assert output.index(t("cookies_retry", "brave")) < output.index(t("done"))
    assert after_done(output) == [without_cookies("brave")]


def test_a_missing_browser_at_the_last_wizard_step_says_so_after_done(generic: Any) -> None:
    code, output = generic("multi.html", *PICKER_WALK[:-1], CHROME)
    assert code == 0
    assert after_done(output) == [without_cookies()]


@pytest.mark.parametrize(
    "argv",
    [
        ("--cookies", "firefox", "https://fake.test/needs-cookies"),  # The cookies were used.
        (VIDEO,),
    ],
)
def test_no_closing_line_without_a_drop(run: Any, argv: tuple[str, ...]) -> None:
    code, output = run("-y", *argv)
    assert code == 0
    assert t("done") in output
    assert unreadable() not in output
    assert t("done_without_cookies", "\0").partition("\0")[0] not in output


def test_no_closing_line_after_the_wizard_probe_drops_the_browser(generic: Any) -> None:
    # The walk already shows "No cookies", and the download had no browser left to drop.
    code, output = generic("poster.html", *SINGLE_WALK, flags=("-i", "--cookies", "chrome"))
    assert code == 0
    assert no_browser() in output
    assert t("done") in output
    assert without_cookies() not in output


def test_the_preview_names_a_missing_browser_and_still_shows_the_page(
    direct: Any, out: Path
) -> None:
    code, output = direct("--cookies", "chrome", pages=("poster.html",), replies=("",))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert output.index(no_browser()) < output.index(t("preview"))
    assert f"  {t('p_title')}: poster (1)" in output
    # yt-dlp's own line: the download doesn't read the browser a second time.
    assert output.count(NO_DATABASE) == 1
    assert names(out) == ["poster (1).mp4"]
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert len(history_rows()) == 1


def test_a_preview_cancelled_after_a_missing_browser_still_said_it(direct: Any, out: Path) -> None:
    # The exit code is row cancel-exits'.
    _, output = direct("--cookies", "chrome", pages=("poster.html",), replies=("n",))
    assert output.count(no_browser()) == 1
    assert output.index(no_browser()) < output.index(t("preview"))
    assert f"  {t('p_title')}: poster (1)" in output
    assert t("cancelled") in output
    assert names(out) == []
    assert commands(output) == []


def test_a_dead_page_after_the_preview_with_a_missing_browser(
    direct: Any, media_url: str, out: Path
) -> None:
    code, output = direct("--cookies", "chrome", pages=("status/404",), replies=("",))
    assert code == 1
    assert output.count(no_browser()) == 1
    assert output.index(no_browser()) < output.index(t("preview"))
    assert output.count(NO_DATABASE) == 1
    assert t("failed", f"{media_url}/status/404") in output
    assert keyring_hint() not in output
    assert without_cookies() not in output
    assert "Traceback" not in output
    [line] = commands(output)
    assert "--cookies chrome" in line
    assert names(out) == []


def test_every_preview_names_its_missing_browser(direct: Any, out: Path) -> None:
    pages = ("poster.html", "multi.html")
    code, output = direct("--cookies", "chrome", pages=pages, replies=("", ""))
    assert code == 0
    assert output.count(no_browser()) == 2
    assert output.count(NO_DATABASE) == 2
    assert len(names(out)) == 5  # Single mode keeps every video of a multi-video page.
    assert after_done(output) == [without_cookies()] * 2
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome")
def test_an_unreachable_keyring_named_by_hand_gets_no_keyring_hint(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome+gnomekeyring", pages=("multi.html",))
    assert code == 0
    assert names(out) == MULTI
    assert t("cookies_unreadable", "chrome", KEYRING_ERROR) in output
    assert no_browser() not in output
    assert keyring_hint() not in output
    assert "Traceback" not in output


@pytest.mark.usefixtures("unreadable_chrome")
def test_an_unreachable_desktop_keyring_suggests_another_keyring(
    direct: Any, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("multi.html",))
    assert code == 0
    assert names(out) == MULTI
    unreadable = t("cookies_unreadable", "chrome", KEYRING_ERROR)
    assert output.index(unreadable) < output.index(keyring_hint())


def test_safari_on_linux_is_unreadable(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "safari", pages=("multi.html",))
    assert code == 0
    assert t("cookies_unreadable", "safari", "unsupported platform: linux") in output
    assert names(out) == MULTI


def test_cookies_that_load_and_find_only_images_are_not_retried(
    run: Any, fake_site: list[Any]
) -> None:
    code, output = run("-y", "-p", "--cookies", "brave", ONLY_IMAGES)
    assert code == 1
    assert t("no_supported_items") in output
    assert t("cookies_retry", "brave") not in output
    assert fake_site == [("brave",)]


# Spec 26, around the edges: a browser that loads, one that loads but decrypts nothing, the
# printed commands, both languages, and the wizard walks around the new message.
def unreadable(browser: str = "chrome") -> str:
    """The start of t("cookies_unreadable"), whatever the reason."""
    return t("cookies_unreadable", browser, "\0").partition("\0")[0]


@pytest.fixture
def token() -> str:
    """A fresh /cookie/ or /seen/ path on the loopback server, so requests can be counted."""
    return uuid.uuid4().hex


def sent(token: str) -> list[tuple[str, str]]:
    """Path and Cookie header of every request made under ``token``."""
    return sorted(GATE_REQUESTS.get(token, []))


def chrome_cookie(value: bytes = b"ok", encrypted: bytes = b"") -> None:
    """A Chrome profile with one cookie for the loopback host: no keyring involved."""
    profile = Path(os.environ["XDG_CONFIG_HOME"]) / "google-chrome" / "Default"
    profile.mkdir(parents=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '20')")
    con.execute(
        "CREATE TABLE cookies (host_key BLOB, name BLOB, value BLOB, encrypted_value BLOB,"
        " path BLOB, expires_utc INTEGER, is_secure INTEGER)"
    )
    con.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, 0, 0)",
        (b"127.0.0.1", b"dm", value, encrypted, b"/"),
    )
    con.commit()
    con.close()


def test_a_browser_that_loads_sends_its_cookies_to_every_item(
    direct: Any, out: Path, token: str
) -> None:
    chrome_cookie()
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=(f"cookie/{token}/multi.html",))
    assert code == 0
    assert names(out) == MULTI
    assert no_browser() not in output
    assert unreadable() not in output
    assert t("cookies_retry", "chrome") not in output
    requests = sent(token)
    assert len(requests) == 5  # The page and its four videos.
    assert all("dm=ok" in cookie for _, cookie in requests)
    [line] = commands(output)
    assert "--cookies chrome" in line
    assert len(history_rows()) == 1


def test_a_browser_that_loads_costs_no_extra_request(
    direct: Any, out: Path, tmp_path: Path, token: str
) -> None:
    chrome_cookie()
    code, _ = direct("-y", "--cookies", "chrome", pages=(f"cookie/{token}/poster.html",))
    assert code == 0
    assert len(names(out)) == 1
    without = uuid.uuid4().hex
    code, _ = direct("-y", "-o", str(tmp_path / "again"), pages=(f"seen/{without}/poster.html",))
    assert code == 0
    assert [path for path, _ in sent(token)] == [path for path, _ in sent(without)]
    assert all("dm=ok" in cookie for _, cookie in sent(token))
    assert not any(cookie for _, cookie in sent(without))


def test_formats_are_listed_with_a_browser_that_loads(direct: Any, token: str) -> None:
    chrome_cookie()
    code, output = direct("-l", "--cookies", "chrome", pages=(f"cookie/{token}/poster.html",))
    assert code == 0
    assert "Available formats" in output
    assert no_browser() not in output
    assert sent(token) and all("dm=ok" in cookie for _, cookie in sent(token))


def test_a_preview_with_cookies_that_load_keeps_them(direct: Any, out: Path, token: str) -> None:
    chrome_cookie()
    page = f"cookie/{token}/poster.html"
    code, output = direct("--cookies", "chrome", pages=(page,), replies=("",))
    assert code == 0
    assert no_browser() not in output
    assert unreadable() not in output
    # The page answers 401 without the cookie: a title means the preview sent it.
    assert f"  {t('p_title')}: poster (1)" in output
    assert names(out) == ["poster (1).mp4"]
    pages = [path for path, _ in sent(token) if path == "/poster.html"]
    assert len(pages) >= 2  # The preview, then the download.
    assert all("dm=ok" in cookie for _, cookie in sent(token))
    assert t("cookies_retry", "chrome") not in output
    assert without_cookies() not in output
    [line] = commands(output)
    assert "--cookies chrome" in line


def test_cookies_that_load_and_get_refused_still_retry(direct: Any, out: Path, token: str) -> None:
    # A real 401 behind cookies that loaded is spec 18's path, not a cookie-load failure.
    chrome_cookie(b"stale")
    code, output = direct("-y", "--cookies", "chrome", pages=(f"cookie/{token}/poster.html",))
    assert code == 1
    assert t("cookies_retry", "chrome") in output
    assert no_browser() not in output
    assert unreadable() not in output
    assert keyring_hint() in output
    cookies = [cookie for _, cookie in sent(token)]
    assert cookies == sorted(["dm=stale", ""])
    assert names(out) == []


def test_the_wizard_says_cookies_that_load_and_get_refused_are_not_enough(
    generic: Any, token: str
) -> None:
    chrome_cookie(b"stale")
    page = f"cookie/{token}/poster.html"
    # URL, the manual flow's four steps, the late cookies step, the preview.
    code, output = generic(page, "", *VIDEO_STEPS, "", "", flags=("-i", "--cookies", "chrome"))
    assert code == 1
    assert output.count(t("probing")) == 1
    assert t("auth_still_needed", "chrome") in output
    assert no_browser() not in output
    assert unreadable() not in output


@pytest.fixture
def no_kwallet_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PATH with ffmpeg and ffprobe only: kwallet-query is not installed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        assert found
        (bin_dir / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(bin_dir))


@pytest.fixture
def kwallet_query_fails(no_kwallet_query: None, tmp_path: Path) -> None:
    """A kwallet-query that always exits 1: yt-dlp logs it, in every mode, and carries on."""
    tool = tmp_path / "bin" / "kwallet-query"
    tool.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tool.chmod(0o755)


@pytest.fixture
def empty_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """A session keyring that answers, and holds no item for the browser."""
    import yt_dlp.cookies

    class Collection:
        def close(self) -> None: ...

        def get_all_items(self) -> list[Any]:
            return []

    stub = types.SimpleNamespace(dbus_init=Collection, get_default_collection=lambda con: con)
    monkeypatch.setattr(yt_dlp.cookies, "secretstorage", stub)


KWALLET_MISSING = "kwallet-query command not found"


@pytest.mark.usefixtures("unreadable_chrome", "no_kwallet_query")
def test_a_keyring_tool_that_is_missing_in_a_playlist_is_unreadable(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome+kwallet6", pages=("multi.html",))
    assert code == 0
    assert names(out) == MULTI
    [said] = [line for line in output.splitlines() if line.startswith(unreadable())]
    assert KWALLET_MISSING in said
    assert keyring_hint() not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome", "no_kwallet_query")
def test_a_keyring_tool_that_is_missing_in_single_mode_is_unreadable(
    direct: Any, out: Path
) -> None:
    code, output = direct("-y", "--cookies", "chrome+kwallet6", pages=("poster.html",))
    assert code == 0
    assert len(names(out)) == 1
    [said] = [line for line in output.splitlines() if line.startswith(unreadable())]
    assert KWALLET_MISSING in said
    assert keyring_hint() not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


def test_one_failed_url_keeps_the_missing_browser_in_the_command(
    direct: Any, media_url: str, out: Path
) -> None:
    # Spec 09: --cookies goes only when every URL saved without it. Here one failed without it,
    # so the command keeps what the user asked for.
    code, output = direct("-y", "--cookies", "chrome", pages=("poster.html", "status/404"))
    assert code == 1
    assert output.count(no_browser()) == 2
    assert names(out) == ["poster (1).mp4"]
    [line] = commands(output)
    assert "--cookies chrome" in line
    assert f"{media_url}/poster.html {media_url}/status/404" in line
    assert len(history_rows()) == 1


def test_formats_of_a_dead_page_with_a_missing_browser_get_no_keyring_hint(
    direct: Any, media_url: str
) -> None:
    code, output = direct("-l", "--cookies", "chrome", pages=("status/404",))
    assert code == 1
    assert no_browser() in output
    assert t("failed", f"{media_url}/status/404") in output
    assert keyring_hint() not in output
    assert "Traceback" not in output


def test_a_missing_browser_does_not_count_as_a_failed_item(direct: Any, out: Path) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("half-gone.html",))
    assert code == 0
    assert len(names(out)) == 1
    assert no_browser() in output
    assert t("partial_result", 1, 2, 0, 1) in output


@pytest.mark.parametrize(
    ("flags", "page", "saved"),
    [
        (("-P",), "poster.html", ["poster (1).mp4"]),
        (("-a", "-p"), "multi.html", [name.replace(".mp4", ".mp3") for name in MULTI]),
    ],
)
def test_a_missing_browser_in_other_modes_goes_on_without_cookies(
    direct: Any, out: Path, flags: tuple[str, ...], page: str, saved: list[str]
) -> None:
    code, output = direct("-y", *flags, "--cookies", "chrome", pages=(page,))
    assert code == 0
    assert names(out) == saved
    assert output.count(no_browser()) == 1
    assert t("cookies_retry", "chrome") not in output


@pytest.mark.parametrize("lang", ["es", "en"])
def test_unreadable_cookies_in_both_languages(
    direct: Any, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    said = i18n._MESSAGES[lang]
    other = i18n._MESSAGES["en" if lang == "es" else "es"]
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=("multi.html",))
    assert code == 0
    assert said["cookies_no_browser"].format("chrome") in output
    assert other["cookies_no_browser"].format("chrome") not in output
    code, output = direct("-y", "-p", "--cookies", "safari", pages=("poster.html",))
    assert code == 0
    assert said["cookies_unreadable"].format("safari", "unsupported platform: linux") in output
    assert other["cookies_unreadable"].format("safari", "unsupported platform: linux") not in output
    assert said["done"] in output
    assert said["done_without_cookies"].format("safari") in output
    assert other["done_without_cookies"].format("safari") not in output


def test_the_spanish_notices_say_the_run_goes_on() -> None:
    # "Se sigue sin cookies" also reads as "still no cookies".
    spanish = i18n._MESSAGES["es"]
    for key in ("cookies_no_browser", "cookies_unreadable"):
        assert spanish[key].endswith(" Se continúa sin cookies.")
    assert not any("Se sigue sin cookies" in text for text in spanish.values())


# Spec 50, beyond the developer's cases: every way a run reaches the download.


def _closing_lines(output: str) -> list[int]:
    lines = output.splitlines()
    return [i for i, line in enumerate(lines) if line.strip() == without_cookies()]


@pytest.mark.parametrize("flags", [("-P",), ("-y", "-P"), ("-y", "-a")])
def test_runs_without_a_preview_say_so_after_done(direct: Any, flags: tuple[str, ...]) -> None:
    # -P skips the preview even without -y: the notice comes at the download, once.
    code, output = direct(*flags, "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert output.count(NO_DATABASE) == 1
    assert t("preview") not in output
    assert after_done(output) == [without_cookies()]
    assert "\x1b" not in output  # Not a terminal: no colour codes around either line.


def test_a_playlist_url_with_all_at_the_mode_prompt_says_so_after_done(
    media_url: str, out: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    prompts = answers("2")
    code = cli.main(["-o", str(out), "--cookies", "chrome", f"{media_url}/multi.html?list=1"])
    output = "".join(capfd.readouterr())
    assert prompts == [t("ask_choice", "1")]
    assert code == 0
    assert names(out) == MULTI
    assert output.count(no_browser()) == 1
    assert after_done(output) == [without_cookies()]


def test_a_playlist_url_taken_as_single_names_the_browser_in_its_preview(
    media_url: str, out: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    prompts = answers("1", "")
    code = cli.main(["-o", str(out), "--cookies", "chrome", f"{media_url}/multi.html?list=1"])
    output = "".join(capfd.readouterr())
    assert prompts == [t("ask_choice", "1"), t("ask_confirm")]
    assert code == 0
    assert output.count(no_browser()) == 1
    assert output.index(no_browser()) < output.index(t("preview"))
    assert output.count(NO_DATABASE) == 1
    assert after_done(output) == [without_cookies()]


def test_each_closing_line_follows_its_own_url(direct: Any, media_url: str) -> None:
    pages = ("multi.html", "poster.html")
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=pages)
    assert code == 0
    assert after_done(output) == [without_cookies()] * 2
    first, second = _closing_lines(output)
    lines = output.splitlines()
    second_url = next(i for i, line in enumerate(lines) if f"URL: {media_url}/poster.html" in line)
    assert first < second_url < second


def test_a_failed_url_next_to_a_saved_one_gets_no_closing_line(
    direct: Any, media_url: str, out: Path
) -> None:
    code, output = direct("-y", "--cookies", "chrome", pages=("status/404", "poster.html"))
    assert code == 1
    assert output.count(no_browser()) == 2
    assert after_done(output) == [without_cookies()]
    [closing] = _closing_lines(output)
    lines = output.splitlines()
    failed = next(
        i for i, line in enumerate(lines) if t("failed", f"{media_url}/status/404") in line
    )
    assert failed < closing
    assert names(out) == ["poster (1).mp4"]
    assert len(history_rows()) == 1


def test_a_preview_cancelled_on_the_second_url_keeps_the_first_closing_line(
    direct: Any, out: Path
) -> None:
    pages = ("poster.html", "multi.html")
    _, output = direct("--cookies", "chrome", pages=pages, replies=("", "n"))
    assert output.count(no_browser()) == 2
    assert after_done(output) == [without_cookies()]
    assert t("cancelled") in output
    assert names(out) == ["poster (1).mp4"]


def test_formats_listed_without_a_missing_browser_have_no_closing_line(direct: Any) -> None:
    code, output = direct("-l", "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert without_cookies() not in output


@pytest.mark.parametrize(
    ("flags", "page", "said"),
    [
        # Every item fails under ignoreerrors: the run returns with nothing saved, no exception.
        (("-p",), "all-junk.html", ("failed", "{url}")),
        # The list doesn't hold what was asked for.
        (("--playlist-items", "9"), "multi.html", ("items_not_found", 4, "9")),
    ],
)
def test_a_run_that_saved_nothing_without_the_asked_cookies_has_no_closing_line(
    direct: Any, media_url: str, flags: tuple[str, ...], page: str, said: tuple[Any, ...]
) -> None:
    code, output = direct("-y", *flags, "--cookies", "chrome", pages=(page,))
    assert code == 1
    assert output.count(no_browser()) == 1
    key, *args = said
    assert t(key, *(str(a).format(url=f"{media_url}/{page}") for a in args)) in output
    assert t("done") not in output
    assert without_cookies() not in output
    assert history_rows() == []


def test_the_closing_line_in_spanish(direct: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "LANG", "es")
    code, output = direct("-y", "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    assert (
        "No se encontraron cookies de chrome: ¿está chrome instalado y se ha abierto al menos "
        "una vez? Se continúa sin cookies."
    ) in output
    lines = [line.strip() for line in output.splitlines()]
    done = lines.index("Listo.")
    assert lines[done + 1] == (
        "Guardado sin las cookies de chrome: es lo que el sitio muestra sin iniciar sesión."
    )


def test_the_preview_refused_by_the_site_retries_once_and_says_so_after_done(
    run: Any, out: Path, answers: Any
) -> None:
    prompts = answers("")
    code, output = run("--cookies", "brave", "https://fake.test/rejects-cookies")
    assert prompts == [t("ask_confirm")]
    assert code == 0
    assert len(names(out)) == 1
    assert output.count(t("cookies_retry", "brave")) == 1
    assert no_browser("brave") not in output
    assert unreadable("brave") not in output
    assert after_done(output) == [without_cookies("brave")]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


def test_the_manual_flow_preview_names_a_browser_picked_at_the_last_step(
    generic: Any, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A probe that fails for its own reasons: the walk goes manual, and cli.py previews the URL.
    def broken(url: str, cookies_browser: str | None = None) -> Probe:
        return Probe(url, "generic", ContentKind.UNKNOWN, failed=True, error_message="broke")

    monkeypatch.setattr(wizard, "probe", broken)
    # URL, the manual flow's four steps, chrome at the late cookies step, the preview.
    code, output = generic("poster.html", "", *VIDEO_STEPS, CHROME, "")
    assert code == 0
    assert t("probe_falling_back") in output
    assert output.count(no_browser()) == 1
    assert output.index(no_browser()) < output.index(t("preview"))
    assert f"  {t('p_title')}: poster (1)" in output
    assert output.count(NO_DATABASE) == 1
    assert len(names(home / "Videos")) == 1
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


@pytest.fixture
def no_secretstorage(monkeypatch: pytest.MonkeyPatch) -> None:
    """yt-dlp without secretstorage, as on a broken install: its own reason, word for word."""
    import yt_dlp.cookies

    monkeypatch.setattr(yt_dlp.cookies, "secretstorage", None)
    monkeypatch.setattr(
        yt_dlp.cookies,
        "_SECRETSTORAGE_UNAVAILABLE_REASON",
        "as the `secretstorage` module is not installed.",
    )


SECRETSTORAGE_REASON = "secretstorage not available as the `secretstorage` module is not installed."


@pytest.mark.usefixtures("unreadable_chrome", "no_secretstorage")
@pytest.mark.parametrize("replies", [(), ("",)], ids=["-y", "preview"])
def test_yt_dlps_label_stays_out_of_the_reason(
    direct: Any, out: Path, replies: tuple[str, ...]
) -> None:
    # Single mode: yt-dlp's report_error labels the cause before it reaches the tool.
    flags = () if replies else ("-y",)
    spec = ("--cookies", "chrome+gnomekeyring")
    code, output = direct(*flags, *spec, pages=("poster.html",), replies=replies)
    assert code == 0
    assert len(names(out)) == 1
    assert output.count(t("cookies_unreadable", "chrome", SECRETSTORAGE_REASON)) == 1
    assert "(ERROR:" not in output
    assert "sudo pacman -S python-secretstorage" in output
    assert after_done(output) == [without_cookies()]


KWALLET_FAILED = (
    "kwallet-query failed with return code 1. Please consult the kwallet-query man page for details"
)
KEYRING_CAUSES = [
    pytest.param("kwallet_query_fails", "chrome+kwallet6", KWALLET_FAILED, id="kwallet-fails"),
    pytest.param(
        "no_kwallet_query",
        "chrome+kwallet6",
        "kwallet-query command not found. KWallet and kwallet-query must be installed to read"
        " from KWallet. kwallet-query should beincluded in the kwallet package for...",
        id="kwallet-missing",
    ),
    pytest.param("no_secretstorage", "chrome+gnomekeyring", SECRETSTORAGE_REASON, id="no-module"),
    pytest.param(
        "empty_keyring", "chrome+gnomekeyring", "failed to read from keyring", id="no-item"
    ),
]


def no_cookie_sent(token: str) -> bool:
    return bool(sent(token)) and not any(cookie for _, cookie in sent(token))


def kde_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The desktop yt-dlp reads to pick kwallet for a bare ``chrome``."""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setenv("KDE_SESSION_VERSION", "6")


@pytest.mark.usefixtures("unreadable_chrome")
@pytest.mark.parametrize(("cause", "spec", "reason"), KEYRING_CAUSES)
@pytest.mark.parametrize(
    ("flags", "saved"),
    [(("-p",), MULTI), (("--playlist-items", "1,2"), MULTI[:2])],
    ids=["-p", "--playlist-items"],
)
def test_a_keyring_error_goes_on_without_cookies(
    direct: Any,
    out: Path,
    token: str,
    request: pytest.FixtureRequest,
    cause: str,
    spec: str,
    reason: str,
    flags: tuple[str, ...],
    saved: list[str],
) -> None:
    request.getfixturevalue(cause)
    code, output = direct("-y", *flags, "--cookies", spec, pages=(f"seen/{token}/multi.html",))
    assert code == 0
    assert names(out) == saved
    assert output.count(t("cookies_unreadable", "chrome", reason)) == 1
    assert not said_partial_result(output)
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert len(history_rows()) == 1
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_in_single_mode_goes_on_without_cookies(
    direct: Any, out: Path, token: str
) -> None:
    flags = ("-y", "--cookies", "chrome+kwallet6")
    code, output = direct(*flags, pages=(f"seen/{token}/poster.html",))
    assert code == 0
    assert len(names(out)) == 1
    assert output.count(t("cookies_unreadable", "chrome", KWALLET_FAILED)) == 1
    assert not said_partial_result(output)
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert len(history_rows()) == 1
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_is_named_before_the_preview(direct: Any, out: Path) -> None:
    code, output = direct("--cookies", "chrome+kwallet6", pages=("poster.html",), replies=("",))
    assert code == 0
    notice = t("cookies_unreadable", "chrome", KWALLET_FAILED)
    assert output.count(notice) == 1
    assert output.index(notice) < output.index(t("preview"))
    assert f"  {t('p_title')}: poster (1)" in output
    # yt-dlp's own line: the download doesn't read the browser a second time.
    assert output.count(f"ERROR: {KWALLET_FAILED}") == 1
    assert after_done(output) == [without_cookies()]
    assert len(names(out)) == 1


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_preview_cancelled_after_a_keyring_error_still_said_it(direct: Any, out: Path) -> None:
    _, output = direct("--cookies", "chrome+kwallet6", pages=("poster.html",), replies=("n",))
    notice = t("cookies_unreadable", "chrome", KWALLET_FAILED)
    assert output.index(notice) < output.index(t("preview"))
    assert t("cancelled") in output
    assert names(out) == []
    assert commands(output) == []


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_formats_are_listed_after_a_keyring_error(direct: Any, token: str) -> None:
    flags = ("-l", "--cookies", "chrome+kwallet6")
    code, output = direct(*flags, pages=(f"seen/{token}/poster.html",))
    assert code == 0
    assert t("cookies_unreadable", "chrome", KWALLET_FAILED) in output
    assert "Available formats" in output
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_desktop_keyring_error_suggests_another_keyring(
    direct: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kde_desktop(monkeypatch)
    code, output = direct("-y", "--cookies", "chrome", pages=("poster.html",))
    assert code == 0
    notice = t("cookies_unreadable", "chrome", KWALLET_FAILED)
    assert output.index(notice) < output.index(keyring_hint())
    assert after_done(output) == [without_cookies()]


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize("flags", [("-p",), ()], ids=["-p", "single"])
def test_a_dead_page_after_a_keyring_error_fails_without_a_retry_line(
    direct: Any, media_url: str, flags: tuple[str, ...]
) -> None:
    code, output = direct("-y", *flags, "--cookies", "chrome+kwallet6", pages=("status/401",))
    assert code == 1
    assert t("cookies_unreadable", "chrome", KWALLET_FAILED) in output
    assert t("failed", f"{media_url}/status/401") in output
    assert GIVE_UP in output
    assert t("cookies_retry", "chrome+kwallet6") not in output
    assert keyring_hint() not in output
    [line] = commands(output)
    assert "--cookies chrome+kwallet6" in line
    assert without_cookies() not in output


@pytest.mark.usefixtures("kwallet_query_fails")
@pytest.mark.parametrize("flags", [("-y",), ("-y", "-p")], ids=["single", "-p"])
def test_a_page_only_the_readable_cookie_opens_now_fails(
    direct: Any, out: Path, token: str, flags: tuple[str, ...]
) -> None:
    # Spec 45 D1's accepted cost: the jar is dropped whole, cookies that did decrypt included.
    chrome_cookie()
    con = sqlite3.connect(
        Path(os.environ["XDG_CONFIG_HOME"]) / "google-chrome" / "Default" / "Cookies"
    )
    con.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, 0, 0)",
        (b"127.0.0.1", b"s", b"", b"v11" + b"\0" * 16, b"/"),
    )
    con.commit()
    con.close()
    code, output = direct(
        *flags, "--cookies", "chrome+kwallet6", pages=(f"cookie/{token}/poster.html",)
    )
    assert code == 1
    assert t("cookies_unreadable", "chrome", KWALLET_FAILED) in output
    assert t("cookies_retry", "chrome+kwallet6") not in output
    assert names(out) == []
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("kwallet_query_fails")
def test_a_jar_that_never_asks_the_keyring_keeps_its_cookies(
    direct: Any, out: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = pbkdf2_sha1(b"peanuts", salt=b"saltysalt", iterations=1, key_length=16)
    chrome_cookie(b"", encrypted=b"v10" + aes_cbc_encrypt_bytes(b"ok", key, b" " * 16))
    kde_desktop(monkeypatch)
    code, output = direct("-y", "-p", "--cookies", "chrome", pages=(f"seen/{token}/multi.html",))
    assert code == 0
    assert names(out) == MULTI
    assert unreadable() not in output
    assert without_cookies() not in output
    assert sent(token) and all("dm=ok" in cookie for _, cookie in sent(token))
    [line] = commands(output)
    assert "--cookies chrome" in line


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_the_wizard_names_a_keyring_error_at_the_probe(generic: Any, home: Path) -> None:
    flags = ("-i", "--cookies", "chrome+kwallet6")
    code, output = generic("multi.html", *PICKER_WALK, flags=flags)
    assert code == 0
    assert output.count(t("cookies_unreadable", "chrome", KWALLET_FAILED)) == 1
    assert t("probe_partial", "\0").partition("\0")[0] not in output
    assert output.count(tag("kind_video")) == 4
    assert without_cookies() not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert names(home / "Videos") == MULTI
    assert not said_partial_result(output)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_at_the_last_wizard_step_says_so_after_done(
    generic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kde_desktop(monkeypatch)
    code, output = generic("multi.html", *PICKER_WALK[:-1], CHROME)
    assert code == 0
    assert output.count(t("cookies_unreadable", "chrome", KWALLET_FAILED)) == 1
    assert keyring_hint() in output
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--playlist-items 1,2,3,4" in line
    assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize("audio", [(), ("-a",)], ids=["video", "-a"])
def test_a_keyring_error_and_a_broken_item_count_the_item_once(
    direct: Any, audio: tuple[str, ...]
) -> None:
    flags = ("-y", "-p", *audio, "--cookies", "chrome+kwallet6")
    code, output = direct(*flags, pages=("junk-and-mp4.html",))
    assert code == 0
    assert t("cookies_unreadable", "chrome", KWALLET_FAILED) in output
    assert t("partial_result", 1, 2, 0, 1) in output
    assert after_done(output) == [without_cookies()]


def kwallet_notice() -> str:
    return t("cookies_unreadable", "chrome", KWALLET_FAILED)


LOGIN_WALL = "Unable to download webpage: HTTP Error 401: Unauthorized"


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_login_wall_after_a_keyring_error_gets_the_retry_prompt(
    generic: Any, home: Path, media_url: str
) -> None:
    flags = ("-i", "--cookies", "chrome+kwallet6")
    code, output = generic("status/401", "", NO_COOKIES, *VIDEO_STEPS, "", flags=flags)
    assert code == 1
    assert output.count(kwallet_notice()) == 1
    assert output.index(kwallet_notice()) < output.index(asked_for_login(LOGIN_WALL))
    assert output.index(asked_for_login(LOGIN_WALL)) < output.index(t("probe_failed", LOGIN_WALL))
    # The wrong advice: the browser wasn't refused, it was never read.
    assert t("auth_still_needed", "chrome") not in output
    assert t("failed", f"{media_url}/status/401") in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert names(home / "Videos") == []


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_at_the_login_prompt_keeps_the_cookieless_probe(
    generic: Any, home: Path, media_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kde_desktop(monkeypatch)
    code, output = generic("status/401", "", CHROME, *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(kwallet_notice()) == 1
    assert keyring_hint() in output
    assert output.count(t("probing")) == 2
    assert t("probe_failed", LOGIN_WALL) in output
    assert t("auth_still_needed", "chrome") not in output
    assert t("failed", f"{media_url}/status/401") in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert names(home / "Videos") == []


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize(
    ("flags", "page", "ext", "count"),
    [
        (("-y", "-P"), "multi.html", ".mp4", 4),
        (("-P",), "multi.html", ".mp4", 4),
        (("-y", "-a"), "poster.html", ".mp3", 1),
        (("-y", "-p", "-a"), "multi.html", ".mp3", 4),
    ],
    ids=["-y -P", "-P", "-y -a", "-y -p -a"],
)
def test_a_keyring_error_reads_the_same_in_the_other_modes(
    direct: Any, out: Path, token: str, flags: tuple[str, ...], page: str, ext: str, count: int
) -> None:
    code, output = direct(*flags, "--cookies", "chrome+kwallet6", pages=(f"seen/{token}/{page}",))
    assert code == 0
    assert len(names(out)) == count
    assert all(name.endswith(ext) for name in names(out))
    assert output.count(kwallet_notice()) == 1
    assert not said_partial_result(output)
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_keeps_the_chosen_items_in_the_command(direct: Any, out: Path) -> None:
    flags = ("-y", "--playlist-items", "1", "--cookies", "chrome+kwallet6")
    code, output = direct(*flags, pages=("multi.html",))
    assert code == 0
    assert names(out) == MULTI[:1]
    [line] = commands(output)
    assert "--playlist-items 1" in line
    assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize("flags", [("-y",), ("-y", "-p")], ids=["-y", "-p"])
def test_a_keyring_error_is_named_for_each_url(
    direct: Any, out: Path, token: str, flags: tuple[str, ...]
) -> None:
    pages = (f"seen/{token}/multi.html", f"seen/{token}/poster.html")
    code, output = direct(*flags, "--cookies", "chrome+kwallet6", pages=pages)
    assert code == 0
    assert len(names(out)) == 5
    assert output.count(kwallet_notice()) == 2
    assert not said_partial_result(output)
    assert after_done(output) == [without_cookies()] * 2
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)
    assert len(history_rows()) == 2
    assert no_cookie_sent(token)


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_error_is_named_before_each_preview(direct: Any, out: Path) -> None:
    pages = ("poster.html", "multi.html")
    code, output = direct("--cookies", "chrome+kwallet6", pages=pages, replies=("", ""))
    assert code == 0
    assert len(names(out)) == 5
    assert output.count(kwallet_notice()) == 2
    assert output.count(f"ERROR: {KWALLET_FAILED}") == 2
    assert after_done(output) == [without_cookies()] * 2


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_a_keyring_that_answers_and_decrypts_nothing_stays_as_it_was(
    direct: Any, out: Path, token: str
) -> None:
    # Spec 45, Out: nothing is logged as an error, so there's no failure to name.
    flags = ("-y", "--cookies", "chrome+basictext")
    code, output = direct(*flags, pages=(f"seen/{token}/poster.html",))
    assert code == 0
    assert len(names(out)) == 1
    assert unreadable() not in output
    assert without_cookies() not in output
    [line] = commands(output)
    assert "--cookies chrome+basictext" in line


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize("flags", [("-y",), ("-y", "-p")], ids=["-y", "-p"])
def test_a_keyring_error_with_one_url_failing_keeps_the_browser_in_the_command(
    direct: Any, out: Path, media_url: str, flags: tuple[str, ...]
) -> None:
    pages = ("status/401", "poster.html")
    code, output = direct(*flags, "--cookies", "chrome+kwallet6", pages=pages)
    assert code == 1
    assert len(names(out)) == 1
    assert output.count(kwallet_notice()) == 2
    assert t("failed", f"{media_url}/status/401") in output
    assert after_done(output) == [without_cookies()]
    [line] = commands(output)
    assert "--cookies chrome+kwallet6" in line


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_the_wizard_names_a_keyring_error_for_each_url(
    media_url: str, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    prompts = answers(*[""] * 13)  # One walk of six steps, one of seven: the picker.
    urls = [f"{media_url}/poster.html", f"{media_url}/multi.html"]
    code = cli.main(["-i", "--cookies", "chrome+kwallet6", *urls])
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert code == 0
    assert len(prompts) == 13
    assert output.count(kwallet_notice()) == 2
    assert output.count(t("probing")) == 4
    assert len(names(home / "Videos")) == 5
    assert not said_partial_result(output)
    for line in commands(output):
        assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome")
@pytest.mark.parametrize(("cause", "spec", "reason"), KEYRING_CAUSES)
@pytest.mark.parametrize(
    ("flags", "replies", "saved"),
    [(("-y",), (), 1), (("-l",), (), 0), ((), ("",), 1)],
    ids=["-y", "-l", "preview"],
)
def test_each_keyring_cause_is_named_outside_a_playlist_too(
    direct: Any,
    out: Path,
    token: str,
    request: pytest.FixtureRequest,
    cause: str,
    spec: str,
    reason: str,
    flags: tuple[str, ...],
    replies: tuple[str, ...],
    saved: int,
) -> None:
    request.getfixturevalue(cause)
    page = f"seen/{token}/poster.html"
    code, output = direct(*flags, "--cookies", spec, pages=(page,), replies=replies)
    assert code == 0
    assert len(names(out)) == saved
    assert output.count(t("cookies_unreadable", "chrome", reason)) == 1
    assert no_cookie_sent(token)
    assert not said_partial_result(output)
    assert after_done(output) == ([without_cookies()] if saved else [])


@pytest.mark.usefixtures("unreadable_chrome")
@pytest.mark.parametrize(("cause", "spec", "reason"), KEYRING_CAUSES)
def test_the_wizard_names_each_keyring_cause_at_the_probe(
    generic: Any,
    home: Path,
    token: str,
    request: pytest.FixtureRequest,
    cause: str,
    spec: str,
    reason: str,
) -> None:
    request.getfixturevalue(cause)
    page = f"seen/{token}/multi.html"
    code, output = generic(page, *PICKER_WALK, flags=("-i", "--cookies", spec))
    assert code == 0
    assert output.count(t("cookies_unreadable", "chrome", reason)) == 1
    assert partly_inspected("\0").partition("\0")[0] not in output
    assert names(home / "Videos") == MULTI
    assert no_cookie_sent(token)
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


@pytest.mark.usefixtures("unreadable_chrome", "no_secretstorage")
def test_a_missing_secretstorage_in_a_playlist_names_the_package(direct: Any) -> None:
    code, output = direct("-y", "-p", "--cookies", "chrome+gnomekeyring", pages=("multi.html",))
    assert code == 0
    assert "sudo pacman -S python-secretstorage" in output


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize(
    ("flags", "replies", "closing"),
    [(("-y", "-p"), (), True), ((), ("",), True), (("-l",), (), False)],
    ids=["-y -p", "preview", "-l"],
)
def test_a_desktop_keyring_error_suggests_another_keyring_in_the_other_modes(
    direct: Any,
    monkeypatch: pytest.MonkeyPatch,
    flags: tuple[str, ...],
    replies: tuple[str, ...],
    closing: bool,
) -> None:
    kde_desktop(monkeypatch)
    code, output = direct(*flags, "--cookies", "chrome", pages=("poster.html",), replies=replies)
    assert code == 0
    assert output.index(kwallet_notice()) < output.index(keyring_hint())
    assert output.count(keyring_hint()) == 1
    assert after_done(output) == ([without_cookies()] if closing else [])


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
@pytest.mark.parametrize(("flags", "replies"), [((), ("",)), (("-l",), ())], ids=["preview", "-l"])
def test_a_dead_page_after_a_keyring_error_in_the_other_modes(
    direct: Any, media_url: str, flags: tuple[str, ...], replies: tuple[str, ...]
) -> None:
    pages = ("status/401",)
    code, output = direct(*flags, "--cookies", "chrome+kwallet6", pages=pages, replies=replies)
    assert code == 1
    assert output.count(kwallet_notice()) == 1
    assert t("failed", f"{media_url}/status/401") in output
    assert GIVE_UP in output
    assert t("cookies_retry", "chrome+kwallet6") not in output
    assert without_cookies() not in output


@pytest.mark.usefixtures("unreadable_chrome", "kwallet_query_fails")
def test_every_item_broken_after_a_keyring_error_is_a_failure(direct: Any, media_url: str) -> None:
    flags = ("-y", "-p", "-a", "--cookies", "chrome+kwallet6")
    code, output = direct(*flags, pages=("all-junk.html",))
    assert code == 1
    assert kwallet_notice() in output
    assert t("failed", f"{media_url}/all-junk.html") in output
    assert t("done") not in output
    assert without_cookies() not in output
    assert not said_partial_result(output)
    [line] = commands(output)
    assert "--cookies chrome+kwallet6" in line


@pytest.mark.parametrize("flags", [("-p", "-a"), ("--playlist-items", "1,2,3", "-a")], ids=str)
def test_two_broken_items_count_two(generic: Any, out: Path, flags: tuple[str, ...]) -> None:
    code, output = generic("two-junk-and-mp4.html", flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert t("partial_result", 1, 3, 0, 2) in output


def test_a_missing_item_and_a_broken_one_are_reported_apart(generic: Any, out: Path) -> None:
    flags = ("-y", "--playlist-items", "1,2,9", "-a", "-o", str(out))
    code, output = generic("junk-and-mp4.html", flags=flags)
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output
    assert t("items_not_found", 2, "9") in output


@pytest.mark.parametrize("flags", [("-p",), ("-p", "-a")], ids=["video", "-a"])
def test_a_message_yt_dlp_does_not_call_an_error_does_not_add_to_a_failure(
    run: Any, flags: tuple[str, ...]
) -> None:
    code, output = run("-y", *flags, "https://fake.test/unscoped-and-broken")
    assert code == 0
    assert t("partial_result", 1, 2, 0, 1) in output


def test_a_message_yt_dlp_does_not_call_an_error_is_no_partial_probe(
    wizard_on: Any, out: Path
) -> None:
    code, output = wizard_on("https://fake.test/unscoped-and-broken", *PICKER_WALK)
    assert code == 0
    assert partly_inspected("\0").partition("\0")[0] not in output
    assert t("partial_result", 1, 2, 0, 1) in output
    assert len(names(out)) == 2  # The broken item's body stays (spec 28, Out).


@pytest.mark.parametrize("flags", [("-p", "-a"), ("--playlist-items", "1,2", "-a")], ids=str)
def test_a_cover_step_that_raises_on_a_failed_item_keeps_the_rest(
    run: Any, out: Path, flags: tuple[str, ...]
) -> None:
    code, output = run("-y", *flags, "https://fake.test/unprobeable-carousel")
    assert code == 0
    assert [name for name in names(out) if name.endswith(".mp3")] == ["02 - Clip [t2].mp3"]
    assert cover_leftovers(out) == []
    assert t("partial_result", 1, 2, 0, 1) in output
    assert t("done") in output


@pytest.mark.parametrize("reported", [0, 2], ids=["only-the-raise", "two-before-it"])
def test_an_item_that_raises_counts_once_whatever_it_reported_first(
    monkeypatch: pytest.MonkeyPatch, run: Any, reported: int
) -> None:
    tag = _TagSource.run

    def step(self: Any, info: dict[str, Any]) -> Any:
        if info.get("id") != "a1":
            return tag(self, info)
        for _ in range(reported):
            self._downloader.report_error("a failed step")
        raise KeyError("streams")

    monkeypatch.setattr(_TagSource, "run", step)
    code, output = run("-y", "-p", "https://fake.test/query-carousel")
    assert code == 0
    assert t("partial_result", 2, 3, 0, 1) in output
    assert t("done") in output


@pytest.mark.parametrize("reported", [0, 1], ids=["clean", "after-an-error"])
@pytest.mark.parametrize("retry", ["saves", "fails"])
def test_an_item_yt_dlp_extracts_again_counts_by_its_last_try(
    monkeypatch: pytest.MonkeyPatch, run: Any, out: Path, reported: int, retry: str
) -> None:
    # ReExtractInfo is the one exception yt-dlp catches without reporting it: it runs the
    # item again, so only what the last try reported can make it a failure.
    tag = _TagSource.run
    tries: list[str] = []

    def step(self: Any, info: dict[str, Any]) -> Any:
        if info.get("id") != "a1":
            return tag(self, info)
        tries.append("a1")
        if len(tries) == 1:
            for _ in range(reported):
                self._downloader.report_error("a failed step")
            raise ReExtractInfo("the stream needs to be re-extracted", expected=True)
        if retry == "fails":
            raise KeyError("streams")
        return tag(self, info)

    monkeypatch.setattr(_TagSource, "run", step)
    code, output = run("-y", "-p", "https://fake.test/query-carousel")
    assert code == 0
    assert tries == ["a1", "a1"]
    assert t("done") in output
    if retry == "saves":
        assert not said_partial_result(output)
        assert len(names(out)) == 3
    else:
        assert t("partial_result", 2, 3, 0, 1) in output


@pytest.mark.usefixtures("unreadable_chrome")
@pytest.mark.parametrize(("cause", "spec", "reason"), KEYRING_CAUSES)
@pytest.mark.parametrize("ignoreerrors", [False, True], ids=["single", "ignoreerrors"])
@pytest.mark.parametrize("ydl_class", [YoutubeDL, _Ydl], ids=["preview", "download"])
def test_a_real_keyring_error_leaves_report_error_alone(
    request: pytest.FixtureRequest,
    cause: str,
    spec: str,
    reason: str,
    ignoreerrors: bool,
    ydl_class: Any,
) -> None:
    request.getfixturevalue(cause)
    params = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": ignoreerrors,
        "cookiesfrombrowser": ydl_cookies(spec),
    }
    with ydl_class(params) as ydl:
        with pytest.raises(CookiesUnavailable) as caught:
            load_browser_cookies(ydl, spec)
        assert caught.value.failure == CookieFailure(False, reason)
        assert "report_error" not in vars(ydl)


@pytest.mark.parametrize("ydl_class", [YoutubeDL, _Ydl], ids=["preview", "download"])
def test_a_real_load_that_works_leaves_report_error_alone(ydl_class: Any) -> None:
    chrome_cookie()
    params = {"quiet": True, "cookiesfrombrowser": ydl_cookies("chrome")}
    with ydl_class(params) as ydl:
        load_browser_cookies(ydl, "chrome")
        assert "report_error" not in vars(ydl)
        assert [cookie.name for cookie in ydl.cookiejar] == ["dm"]


def test_a_missing_browser_at_the_last_wizard_step_saves_a_single_video(
    generic: Any, home: Path
) -> None:
    code, output = generic("poster.html", *SINGLE_WALK[:-1], CHROME)
    assert code == 0
    assert len(names(home / "Videos")) == 1
    assert output.count(no_browser()) == 1
    assert t("cookies_retry", "chrome") not in output
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


def test_every_wizard_walk_reports_its_own_missing_browser(
    media_url: str, home: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    prompts = answers(*SINGLE_WALK, *PICKER_WALK)
    pages = [f"{media_url}/poster.html", f"{media_url}/multi.html"]
    code = cli.main(["-i", "--cookies", "chrome", *pages])
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert len(prompts) == len(SINGLE_WALK) + len(PICKER_WALK)
    assert code == 0
    # Once per walk, at its probe; the downloads already run without the browser.
    assert output.count(no_browser()) == 2
    assert output.count(t("probing")) == 4
    assert detected("kind_video") in output
    assert len(names(home / "Videos")) == 5
    lines = commands(output)
    assert len(lines) == 2
    assert not any("--cookies" in shlex.split(line) for line in lines)


def test_a_login_wall_after_a_missing_browser_up_front_asks_once_more_only(
    generic: Any, home: Path
) -> None:
    # Up front: chrome can't be read, the cookieless probe meets the login wall, and the retry
    # prompt gets one more browser. That one can't be read either: no fourth probe, no loop.
    page = "status/401/login?from=x.com"
    code, output = generic(page, "", CHROME, CHROME, *VIDEO_STEPS, "")
    assert code == 1
    assert output.count(no_browser()) == 2
    assert output.count(t("ask_cookies")) == 2
    assert output.count(t("probing")) == 3
    reason = "Unable to download webpage: HTTP Error 401: Unauthorized"
    assert t("probe_failed", reason) in output
    assert t("auth_still_needed", "chrome") not in output
    assert "could not find chrome cookies database" not in output
    assert names(home / "Videos") == []


def test_cancelling_after_a_missing_browser_downloads_nothing(generic: Any, home: Path) -> None:
    code, output = generic("poster.html", "", "c", flags=("-i", "--cookies", "chrome"))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert t("cancelled") in output
    assert t("equiv_cmd") not in output
    assert names(home / "Videos") == []


def test_going_back_after_a_missing_browser_does_not_probe_again(generic: Any, home: Path) -> None:
    # Back at the first step re-asks it; the walk keeps the cookieless probe.
    replies = ("", "b", *SINGLE_WALK[1:])
    code, output = generic("poster.html", *replies, flags=("-i", "--cookies", "chrome"))
    assert code == 0
    assert output.count(no_browser()) == 1
    assert output.count(t("probing")) == 2
    assert len(names(home / "Videos")) == 1
    [line] = commands(output)
    assert "--cookies" not in shlex.split(line)


# Errors that aren't a DownloadError (spec 42): each fails only its URL.
CRASH = "https://fake.test/crash"
CRASH_BUG = "TypeError: 'NoneType' object is not subscriptable"


def blocked_folder(tmp_path: Path) -> tuple[Path, Path]:
    """A regular file, and a folder below it that can't be created."""
    afile = tmp_path / "afile"
    afile.write_text("keep", encoding="utf-8")
    return afile, afile / "sub"


def test_a_folder_that_cannot_be_created_fails_each_url(
    run: Any, tmp_path: Path, out: Path
) -> None:
    afile, bad = blocked_folder(tmp_path)
    code, output = run("-y", "-o", str(bad), VIDEO, VIDEO_B)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 2
    assert t("failed", VIDEO) in output
    assert t("failed", VIDEO_B) in output
    assert t("done") not in output
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("-o") + 1] == str(bad)
    assert VIDEO in argv and VIDEO_B in argv
    assert history_rows() == []
    assert afile.read_text(encoding="utf-8") == "keep"
    assert names(out) == []


def test_a_folder_that_cannot_be_created_fails_a_list_once(run: Any, tmp_path: Path) -> None:
    _, bad = blocked_folder(tmp_path)
    code, output = run("-y", "-p", "-o", str(bad), CAROUSEL)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 1
    assert t("failed", CAROUSEL) in output
    assert len(commands(output)) == 1


def test_wizard_with_a_folder_that_cannot_be_created_saves_the_next_url(
    run: Any, tmp_path: Path, out: Path, answers: Any
) -> None:
    _, bad = blocked_folder(tmp_path)
    # URL, type, quality, container, folder, cookies; the second walk keeps -o.
    answers("", "", "", "", str(bad), "", *SINGLE_WALK)
    code, output = run("-i", VIDEO, VIDEO_B)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 1
    assert t("failed", VIDEO) in output
    assert t("failed", VIDEO_B) not in output
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("done")) == 1
    folders = []
    for line in commands(output):
        argv = shlex.split(line)
        folders.append(argv[argv.index("-o") + 1])
    assert sorted(folders) == sorted([str(bad), str(out)])
    assert len(history_rows()) == 1


def test_an_extractor_bug_fails_only_its_url(run: Any, out: Path) -> None:
    code, output = run("-y", CRASH, VIDEO)
    assert code == 1
    assert "Traceback" in output
    assert "TypeError" in output
    assert t("unexpected_bug", CRASH_BUG) in output
    assert t("failed", CRASH) in output
    assert t("failed", VIDEO) not in output
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("done")) == 1
    [line] = commands(output)
    argv = shlex.split(line)
    assert CRASH in argv and VIDEO in argv
    assert len(history_rows()) == 1


def test_an_extractor_bug_in_a_list_is_still_reported_by_yt_dlp(run: Any, out: Path) -> None:
    code, output = run("-y", "-p", CRASH, VIDEO)
    assert code == 1
    assert t("unexpected_bug", CRASH_BUG) not in output
    assert "Traceback" not in output
    assert "'NoneType' object is not subscriptable" in output
    assert t("failed", CRASH) in output
    assert names(out) == ["Clip.mp4"]
    assert output.count(t("done")) == 1
    assert len(commands(output)) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes to a read-only folder")
def test_a_thumbnail_that_cannot_be_written_fails_with_the_system_error(
    run: Any, tmp_path: Path
) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    url = "https://fake.test/junk-thumb"
    try:
        code, output = run("-y", "-a", "-o", str(ro), url)
    finally:
        ro.chmod(0o755)
    assert code == 1
    assert "Traceback" not in output
    prefix = t("unexpected_error", "")
    [line] = [x for x in output.splitlines() if x.startswith(prefix)]
    assert "Permission denied" in line
    assert ".cover.jpg" in line
    assert t("failed", url) in output
    assert names(ro) == []
    assert history_rows() == []
    assert len(commands(output)) == 1


def test_an_extractor_bug_while_listing_formats_is_reported(run: Any) -> None:
    code, output = run("-y", "-l", CRASH)
    assert code == 1
    assert t("unexpected_bug", CRASH_BUG) in output
    assert t("failed", CRASH) in output


# Spec 42, from the outside: every mode, both streams, both languages.
def read_only(folder: Path) -> Path:
    folder.mkdir()
    folder.chmod(0o500)
    return folder


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        # A regular file where the folder should be: mkdir(exist_ok=True) still refuses it.
        (lambda tmp: blocked_folder(tmp)[0], "File exists"),
        (lambda tmp: blocked_folder(tmp)[1], "Not a directory"),
        pytest.param(
            lambda tmp: read_only(tmp / "ro") / "sub",
            "Permission denied",
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode"),
        ),
    ],
)
def test_each_reason_a_folder_cannot_be_created_is_named(
    run: Any, tmp_path: Path, make: Callable[[Path], Path], reason: str
) -> None:
    bad = make(tmp_path)
    afile = tmp_path / "afile"
    try:
        code, output = run("-y", "-o", str(bad), VIDEO)
    finally:
        if (tmp_path / "ro").exists():
            (tmp_path / "ro").chmod(0o755)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, reason)) == 1
    assert t("failed", VIDEO) in output
    assert len(commands(output)) == 1
    assert history_rows() == []
    if afile.exists():
        assert afile.read_text(encoding="utf-8") == "keep"
    if (tmp_path / "ro").exists():
        assert names(tmp_path / "ro") == []


def test_folder_and_bug_messages_go_to_stderr(
    fake_site: list[Any], tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _, bad = blocked_folder(tmp_path)
    code = cli.main(["-y", "-o", str(bad), VIDEO])
    captured = capfd.readouterr()
    assert code == 1
    assert t("output_unusable", bad, "Not a directory") in captured.err
    assert t("output_unusable", bad, "Not a directory") not in captured.out
    assert t("failed", VIDEO) in captured.out

    code = cli.main(["-y", "-o", str(tmp_path / "out"), CRASH])
    captured = capfd.readouterr()
    assert code == 1
    assert "Traceback" in captured.err
    assert t("unexpected_bug", CRASH_BUG) in captured.err
    assert "Traceback" not in captured.out
    assert t("unexpected_bug", CRASH_BUG) not in captured.out
    # The traceback comes first: the message points at "the output above".
    assert captured.err.index("Traceback") < captured.err.index(t("unexpected_bug", CRASH_BUG))
    assert t("failed", CRASH) in captured.out


def test_a_folder_that_cannot_be_created_after_the_preview(
    run: Any, tmp_path: Path, answers: Any
) -> None:
    # Direct mode without -y: the preview and its confirmation come first, as today.
    _, bad = blocked_folder(tmp_path)
    prompts = answers("")
    code, output = run("-o", str(bad), VIDEO)
    assert len(prompts) == 1
    assert code == 1
    assert t("preview") in output
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 1
    assert t("failed", VIDEO) in output
    [line] = commands(output)
    assert "-y" in shlex.split(line)


@pytest.mark.parametrize(
    "flags", [("-P",), ("--playlist-items", "2"), ("-p",)], ids=["-P", "items", "-p"]
)
def test_a_folder_that_cannot_be_created_fails_a_carousel_once_in_every_mode(
    run: Any, tmp_path: Path, fake_site: list[Any], flags: tuple[str, ...]
) -> None:
    _, bad = blocked_folder(tmp_path)
    code, output = run("-y", *flags, "-o", str(bad), CAROUSEL)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 1
    assert output.count(t("failed", CAROUSEL)) == 1
    assert t("items_not_found", 4, "2") not in output
    # Nothing was asked of yt-dlp.
    assert fake_site == []
    assert len(commands(output)) == 1


def test_an_extractor_bug_without_a_list_in_no_playlist_mode(run: Any, out: Path) -> None:
    code, output = run("-y", "-P", CRASH, VIDEO)
    assert code == 1
    assert output.count("Traceback") == 1
    assert t("unexpected_bug", CRASH_BUG) in output
    assert t("failed", CRASH) in output
    assert names(out) == ["Clip.mp4"]
    [line] = commands(output)
    argv = shlex.split(line)
    assert CRASH in argv and VIDEO in argv


def test_an_extractor_bug_between_two_urls(run: Any, out: Path) -> None:
    code, output = run("-y", VIDEO, CRASH, VIDEO_B)
    assert code == 1
    assert output.count(t("done")) == 2
    assert len(names(out)) == 2
    assert output.count(t("unexpected_bug", CRASH_BUG)) == 1
    assert t("failed", VIDEO) not in output
    assert t("failed", VIDEO_B) not in output
    # Done, failed, done: in the order the URLs ran.
    first_done = output.index(t("done"))
    failed = output.index(t("failed", CRASH))
    assert first_done < failed < output.index(t("done"), first_done + 1)
    [line] = commands(output)
    argv = shlex.split(line)
    assert [a for a in argv if a.startswith("https://")] == [VIDEO, CRASH, VIDEO_B]
    rows = history_rows()
    assert len(rows) == 2
    assert not any(CRASH in row for row in rows)


def test_every_url_failing_for_its_own_reason(run: Any, tmp_path: Path) -> None:
    # Total failure: a bug and a yt-dlp error, each named, and one command.
    code, output = run("-y", CRASH, PRIVATE)
    assert code == 1
    assert t("unexpected_bug", CRASH_BUG) in output
    assert t("failed", CRASH) in output
    assert t("failed", PRIVATE) in output
    assert t("done") not in output
    assert len(commands(output)) == 1
    assert history_rows() == []


def test_cookies_are_not_retried_for_a_folder_or_a_bug(
    run: Any, tmp_path: Path, fake_site: list[Any]
) -> None:
    _, bad = blocked_folder(tmp_path)
    code, output = run("-y", "--cookies", "brave", "-o", str(bad), VIDEO)
    assert code == 1
    assert t("output_unusable", bad, "Not a directory") in output
    assert t("cookies_retry", "brave") not in output
    assert fake_site == []

    code, output = run("-y", "--cookies", "brave", CRASH)
    assert code == 1
    assert output.count(t("unexpected_bug", CRASH_BUG)) == 1
    assert t("cookies_retry", "brave") not in output
    assert len(fake_site) == 1
    [line] = commands(output)
    assert "--cookies" in shlex.split(line)


def test_wizard_on_an_extractor_bug_falls_back_then_fails_cleanly(
    fake_site: list[Any], out: Path, answers: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    # The probe fails, the manual flow runs, and the download reports the bug.
    prompts = answers("", "", "", "", "", "", "")
    code = cli.main(["-i", "-o", str(out), CRASH])
    captured = capfd.readouterr()
    assert len(prompts) == 7
    assert code == 1
    assert t("unexpected_bug", CRASH_BUG) in captured.err
    assert t("failed", CRASH) in captured.out
    assert len(commands(captured.out)) == 1
    assert history_rows() == []


def test_wizard_picker_with_a_folder_that_cannot_be_created(
    run: Any, tmp_path: Path, answers: Any, fake_site: list[Any]
) -> None:
    _, bad = blocked_folder(tmp_path)
    # URL, item selection, type, quality, container, folder, cookies.
    answers("", "", "", "", "", str(bad), "")
    code, output = run("-i", CAROUSEL)
    assert code == 1
    assert "Traceback" not in output
    assert output.count(t("output_unusable", bad, "Not a directory")) == 1
    assert t("failed", CAROUSEL) in output
    [line] = commands(output)
    argv = shlex.split(line)
    assert argv[argv.index("-o") + 1] == str(bad)


def test_listing_formats_never_creates_the_folder(run: Any, tmp_path: Path) -> None:
    afile, bad = blocked_folder(tmp_path)
    code, output = run("-y", "-l", "-o", str(bad), VIDEO)
    assert code == 0
    assert t("output_unusable", bad, "Not a directory") not in output
    assert afile.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("lang", ["es", "en"])
def test_unexpected_errors_in_both_languages(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    said = i18n._MESSAGES[lang]
    other = i18n._MESSAGES["en" if lang == "es" else "es"]
    _, bad = blocked_folder(tmp_path)
    code, output = run("-y", "-o", str(bad), VIDEO)
    assert code == 1
    assert said["output_unusable"].format(bad, "Not a directory") in output
    assert other["output_unusable"].split("{}")[0] not in output
    assert said["failed"].format(VIDEO) in output

    code, output = run("-y", CRASH)
    assert code == 1
    assert said["unexpected_bug"].format(CRASH_BUG) in output
    assert other["unexpected_bug"].split("(")[0] not in output
    assert said["failed"].format(CRASH) in output

    code, output = run("-y", "-l", CRASH)
    assert code == 1
    assert said["unexpected_bug"].format(CRASH_BUG) in output


# Spec 30: the container choice holds on single-format sources.


def codecs(path: Path) -> dict[str, str]:
    """The codec of the first video and audio stream ffprobe finds in ``path``, by type."""
    probe = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name"]
    out = subprocess.run([*probe, "-of", "json", str(path)], capture_output=True, text=True)
    found: dict[str, str] = {}
    for stream in json.loads(out.stdout or "{}").get("streams", []):
        found.setdefault(stream.get("codec_type"), stream.get("codec_name"))
    return {kind: name for kind, name in found.items() if kind in ("video", "audio")}


def container_steps(output: str) -> int:
    return output.count("[VideoConvertor]") + output.count("[VideoRemuxer]")


@pytest.mark.parametrize("page", ["webm.html", "tiny.webm", "video-ogg.html", "tiny.flv"])
def test_the_default_mp4_converts_a_single_format_source(
    generic: Any, out: Path, page: str
) -> None:
    code, output = generic(page, flags=("-y", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp4")
    assert codecs(out / saved) == {"video": "h264", "audio": "aac"}
    assert "Converting video" in output
    assert t("done") in output


def test_wizard_summary_matches_the_saved_container(generic: Any, home: Path, out: Path) -> None:
    label = wizard._summary_label("container")
    code, output = generic("webm.html", *SINGLE_WALK)
    assert code == 0
    assert f"{label}: mp4" in output
    [saved] = names(home / "Videos")
    assert saved.endswith(".mp4")

    code, output = generic(
        "tiny.mp4", *SINGLE_WALK, flags=("-i", "--video-format", "mkv", "-o", str(out))
    )
    assert code == 0
    assert f"{label}: mkv" in output
    assert names(out) == ["tiny.mkv"]


@pytest.mark.parametrize(
    ("page", "source"),
    [
        ("tiny.mp4", "tiny.mp4"),
        ("webm.html", "tiny.webm"),
        ("video-ogg.html", "theora.ogg"),
        ("tiny.flv", "tiny.flv"),
    ],
    ids=["tiny.mp4", "webm.html", "theora.ogg", "tiny.flv"],
)
def test_mkv_remuxes_without_re_encoding(
    generic: Any, media_url: str, out: Path, tmp_path: Path, page: str, source: str
) -> None:
    code, output = generic(page, flags=("-y", "--video-format", "mkv", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mkv")
    with urllib.request.urlopen(f"{media_url}/{source}") as response:
        (tmp_path / source).write_bytes(response.read())
    assert codecs(out / saved) == codecs(tmp_path / source)
    assert "Remuxing video" in output
    assert "Converting video" not in output


def test_webm_re_encodes_what_it_cannot_hold(generic: Any, out: Path) -> None:
    code, output = generic("tiny.mp4", flags=("-y", "--video-format", "webm", "-o", str(out)))
    assert code == 0
    assert names(out) == ["tiny.webm"]
    found = codecs(out / "tiny.webm")
    assert found["video"] in {"vp8", "vp9", "av1"}
    assert found["audio"] in {"vorbis", "opus"}
    assert t("done") in output


@pytest.mark.parametrize(
    ("flags", "url", "saved", "streams_in"),
    [
        ((), "tiny.mp4", "tiny.mp4", {"video": "h264", "audio": "aac"}),
        (("--video-format", "webm"), "tiny.webm", "tiny.webm", {"video": "vp8", "audio": "vorbis"}),
        (("--video-format", "mkv"), "merged", "Clip.mkv", {"video": "h264", "audio": "aac"}),
    ],
    ids=["mp4-tiny.mp4", "webm-tiny.webm", "mkv-merged"],
)
def test_a_file_already_in_the_container_is_left_alone(
    request: pytest.FixtureRequest,
    out: Path,
    flags: tuple[str, ...],
    url: str,
    saved: str,
    streams_in: dict[str, str],
) -> None:
    # fake_site replaces yt-dlp's extractors, so each source asks for its own fixture.
    if url == "merged":
        run = request.getfixturevalue("run")
        code, output = run("-y", *flags, f"https://fake.test/{url}")
    else:
        generic = request.getfixturevalue("generic")
        code, output = generic(url, flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert names(out) == [saved]
    assert container_steps(output) == 0
    assert codecs(out / saved) == streams_in


@pytest.mark.parametrize("lang", ["es", "en"])
def test_a_video_that_cannot_be_converted_is_kept(
    run: Any, out: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    code, output = run("-y", "https://fake.test/attached")
    assert code == 0
    assert names(out) == ["Clip.mkv"]
    assert codecs(out / "Clip.mkv") == {"video": "h264", "audio": "aac"}
    assert output.count(t("not_converted", "Clip.mkv", "mp4")) == 1
    assert t("done") in output
    assert t("failed", "https://fake.test/attached") not in output
    assert step_leftovers(out) == []
    assert len(history_rows()) == 1


def _broken_ffprobe(fault: str) -> Callable[..., Any]:
    """subprocess.run, with every ffprobe call failing the way ``fault`` names."""
    real = subprocess.run

    def fake(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if not (isinstance(cmd, list) and cmd and cmd[0] == "ffprobe"):
            return real(cmd, *args, **kwargs)
        if fault == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 15)
        if fault == "missing":
            raise FileNotFoundError(2, "No such file or directory", "ffprobe")
        if fault == "fails":
            return subprocess.CompletedProcess(cmd, 1, "", "ffprobe: something went wrong\n")
        return subprocess.CompletedProcess(cmd, 0, "not what ffprobe prints\n", "")

    return fake


@pytest.mark.parametrize("fault", ["timeout", "missing", "fails", "unparsable"])
def test_a_video_ffprobe_cannot_read_is_kept_and_named(
    run: Any, out: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # Only ffprobe breaks: yt-dlp runs its own processes, and ffmpeg stays real.
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", _broken_ffprobe(fault))
        code, output = run("-y", "https://fake.test/single-webm")
    assert code == 0
    assert names(out) == ["Clip.webm"]
    assert codecs(out / "Clip.webm") == {"video": "vp8", "audio": "vorbis"}
    assert container_steps(output) == 0
    assert output.count(t("not_converted", "Clip.webm", "mp4")) == 1
    assert t("done") in output
    assert step_leftovers(out) == []
    assert len(history_rows()) == 1


def test_a_junk_body_ffprobe_cannot_read_is_not_converted(
    generic: Any, media_url: str, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A conversion would turn the text body into a .mkv holding a subtitle track.
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", _broken_ffprobe("missing"))
        code, output = generic(JUNK, flags=("-y", "--video-format", "mkv", "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/{JUNK}") in output
    assert names(out) == ["junk.mp4"]
    assert container_steps(output) == 0
    assert history_rows() == []


@pytest.mark.parametrize("lang", ["es", "en"])
def test_wizard_names_a_video_ffprobe_cannot_read(
    generic: Any, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    monkeypatch.setattr(i18n, "LANG", lang)
    said = i18n._MESSAGES[lang]
    other = i18n._MESSAGES["en" if lang == "es" else "es"]
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", _broken_ffprobe("timeout"))
        code, output = generic("webm.html", *SINGLE_WALK)
    assert code == 0
    [saved] = names(home / "Videos")
    assert saved.endswith(".webm")
    assert output.count(said["not_converted"].format(saved, "mp4")) == 1
    assert other["not_converted"].split("{}")[1] not in output
    assert said["done"] in output
    assert container_steps(output) == 0
    assert len(history_rows()) == 1


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ffprobe prints 'video,' for a stream with side data: read as no answer",
)
@pytest.mark.parametrize(
    ("page", "flags", "saved"),
    [
        ("rotated.mov", (), "rotated.mp4"),
        ("rotated.mp4", ("--video-format", "mkv"), "rotated.mkv"),
    ],
    ids=["mov-default", "mp4-to-mkv"],
)
def test_a_rotated_video_is_converted(
    generic: Any, out: Path, page: str, flags: tuple[str, ...], saved: str
) -> None:
    # A phone video carries a display matrix; ffmpeg converts it like any other video.
    code, output = generic(page, flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert t("done") in output
    assert t("not_converted", page, saved.rsplit(".")[-1]) not in output
    assert names(out) == [saved]
    assert codecs(out / saved) == {"video": "h264", "audio": "aac"}


@pytest.mark.parametrize(
    ("flags", "saved"),
    [
        ((), ["Clip [v1].mp4", "Clip [x1].mkv"]),
        (("-p",), ["01 - Clip [x1].mkv", "02 - Clip [v1].mp4"]),
    ],
    ids=["single", "p"],
)
def test_a_failed_conversion_does_not_stop_a_carousel(
    run: Any, out: Path, flags: tuple[str, ...], saved: list[str]
) -> None:
    code, output = run("-y", *flags, "https://fake.test/attached-carousel")
    assert code == 0
    assert names(out) == saved
    kept = next(name for name in saved if name.endswith(".mkv"))
    assert output.count(t("not_converted", kept, "mp4")) == 1
    assert never_said("partial_result", output)
    assert t("done") in output


@pytest.mark.parametrize("container", ["mkv", "webm"])
def test_a_junk_body_still_fails_when_converted(
    generic: Any, media_url: str, out: Path, container: str
) -> None:
    # ffmpeg reads this text body as a WebVTT track, which mkv and webm would both take.
    code, output = generic(JUNK, flags=("-y", "--video-format", container, "-o", str(out)))
    assert code == 1
    assert t("failed", f"{media_url}/{JUNK}") in output
    assert names(out) == ["junk.mp4"]  # The body itself stays, as without a conversion.
    assert container_steps(output) == 0
    assert never_said("not_converted", output)
    assert history_rows() == []


def test_a_rerun_after_a_conversion_downloads_nothing(generic: Any, out: Path) -> None:
    code, output = generic("webm.html", flags=("-y", "-o", str(out)))
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(".mp4")
    code, output = generic("webm.html", flags=("-y", "-o", str(out)))
    assert code == 0
    assert names(out) == [saved]
    assert "has already been downloaded" in output
    assert "Converting video" not in output


def test_same_title_videos_both_survive_the_conversion(run: Any, out: Path) -> None:
    code, _ = run("-y", "https://fake.test/single-webm")
    assert code == 0
    code, _ = run("-y", "https://fake.test/single-webm-b")
    assert code == 0
    assert names(out) == ["Clip [single-webm-b].mp4", "Clip.mp4"]
    assert _source_url(out / "Clip.mp4") == "https://fake.test/single-webm"
    assert _source_url(out / "Clip [single-webm-b].mp4") == "https://fake.test/single-webm-b"


def test_an_image_named_like_a_converted_video_is_still_saved(
    generic: Any, media_url: str, out: Path
) -> None:
    # yt-dlp's final_ext makes it look for tiny.mp4 first, for the image too.
    code, _ = generic("tiny.mp4", flags=("-y", "-o", str(out)))
    assert code == 0
    code, output = generic("tiny.jpg", flags=("-y", "-o", str(out)))
    assert code == 0
    assert "has already been downloaded" not in output
    [image] = [name for name in names(out) if name.endswith(".jpg")]
    assert streams(out / image) == {"video"}
    assert _source_url(out / "tiny.mp4") == f"{media_url}/tiny.mp4"


def test_images_and_audio_keep_their_extension_in_video_mode(
    generic: Any, request: pytest.FixtureRequest, out: Path, tmp_path: Path
) -> None:
    code, output = generic("jpg-alone.html", flags=("-y", "-o", str(out)))
    assert code == 0
    assert names(out) == ["jpg-alone (1).jpg"]
    gifs = tmp_path / "gifs"
    code, output = generic("tiny.gif", flags=("-y", "-o", str(gifs)))
    assert code == 0
    assert names(gifs) == ["tiny.gif"]
    assert container_steps(output) == 0
    # After the generic pages: fake_site replaces yt-dlp's extractors.
    run = request.getfixturevalue("run")
    query = tmp_path / "query"
    code, output = run("-y", "-p", "-o", str(query), "https://fake.test/query-carousel")
    assert code == 0
    assert [name for name in names(query) if name.endswith(".m4a")] == ["01 - Clip [a1].m4a"]
    assert [name for name in names(query) if name.endswith(".jpg")] == ["02 - Clip [p1].jpg"]
    carousel = tmp_path / "carousel"
    code, output = run("-y", "-p", "-o", str(carousel), IG_CAROUSEL)
    assert code == 0
    assert names(carousel) == IG_NUMBERED


def test_webm_prefers_a_webm_format(run: Any, out: Path) -> None:
    code, output = run("-y", "--video-format", "webm", "https://fake.test/mp4-or-webm")
    assert code == 0
    assert names(out) == ["Clip.webm"]
    assert "Downloading 1 format(s): w" in output
    assert "Converting video" not in output


def test_webm_on_merged_h264_streams(run: Any, out: Path) -> None:
    code, output = run("-y", "--video-format", "webm", "https://fake.test/merged")
    assert code == 0
    assert names(out) == ["Clip.webm"]
    found = codecs(out / "Clip.webm")
    assert found["video"] in {"vp8", "vp9", "av1"}
    assert found["audio"] in {"vorbis", "opus"}
    assert t("done") in output


@pytest.mark.parametrize(
    ("flags", "saved"), [((), "Clip.mp4"), (("--video-format", "mkv"), "Clip.mkv")]
)
def test_merges_into_mp4_and_mkv_are_unchanged(
    run: Any, out: Path, flags: tuple[str, ...], saved: str
) -> None:
    code, output = run("-y", *flags, "https://fake.test/merged")
    assert code == 0
    assert names(out) == [saved]
    assert codecs(out / saved) == {"video": "h264", "audio": "aac"}
    assert container_steps(output) == 0


def test_subtitles_embed_in_the_converted_file(run: Any, out: Path) -> None:
    code, output = run("-y", "-s", "https://fake.test/subs-flv")
    assert code == 0
    assert names(out) == ["Clip.en.vtt", "Clip.mp4"]
    assert subtitle_streams(out / "Clip.mp4") == [("mov_text", "eng")]
    assert never_said("subs_not_embedded", output)
    assert t("done") in output


# Spec 30, past the developer's cases: reruns, the other modes, and the step's own lines.


@pytest.mark.parametrize(
    ("page", "flags", "line"),
    [
        ("webm.html", (), "[VideoConvertor] Converting video from webm to mp4; Destination: "),
        (
            "tiny.mp4",
            ("--video-format", "mkv"),
            "[VideoRemuxer] Remuxing video from mp4 to mkv; Destination: ",
        ),
    ],
    ids=["recode", "remux"],
)
def test_the_container_step_speaks_as_yt_dlp(
    generic: Any, out: Path, page: str, flags: tuple[str, ...], line: str
) -> None:
    # D6: yt-dlp's own lines, under its own tags, are all the user sees of the step.
    code, output = generic(page, flags=("-y", *flags, "-o", str(out)))
    assert code == 0
    assert output.count(line) == 1
    assert "Deleting original file" in output
    assert len(names(out)) == 1


@pytest.mark.parametrize(
    ("page", "container"), [("webm.html", "mkv"), ("tiny.mp4", "webm"), ("theora.ogx", "mp4")]
)
def test_a_rerun_in_each_container_downloads_nothing(
    generic: Any, out: Path, page: str, container: str
) -> None:
    flags = ("-y", "--video-format", container, "-o", str(out))
    code, _ = generic(page, flags=flags)
    assert code == 0
    [saved] = names(out)
    assert saved.endswith(f".{container}")
    first = codecs(out / saved)
    code, output = generic(page, flags=flags)
    assert code == 0
    assert names(out) == [saved]
    assert "has already been downloaded" in output
    assert container_steps(output) == 0
    # The metadata step tags the file again, as on any rerun: same streams, not same bytes.
    assert codecs(out / saved) == first
    assert len(history_rows()) == 2


def test_a_rerun_of_a_merge_into_webm_downloads_nothing(run: Any, out: Path) -> None:
    # D5's merge goes through mkv, so yt-dlp has to find the .webm by final_ext.
    code, _ = run("-y", "--video-format", "webm", "https://fake.test/merged")
    assert code == 0
    assert names(out) == ["Clip.webm"]
    code, output = run("-y", "--video-format", "webm", "https://fake.test/merged")
    assert code == 0
    assert names(out) == ["Clip.webm"]
    assert "has already been downloaded" in output
    assert container_steps(output) == 0


def test_a_rerun_under_p_downloads_nothing_and_warns_again(generic: Any, out: Path) -> None:
    flags = ("-y", "-p", "-o", str(out))
    code, _ = generic("webm-and-attached.html", flags=flags)
    assert code == 0
    saved = names(out)
    assert [name.rsplit(".", 1)[1] for name in saved] == ["mp4", "mkv"]
    code, output = generic("webm-and-attached.html", flags=flags)
    assert code == 0
    assert names(out) == saved
    assert output.count("has already been downloaded") == 2
    assert "Converting video from webm" not in output
    # The item ffmpeg can't convert is tried again, and fails the same way.
    assert output.count(t("not_converted", saved[1], "mp4")) == 1
    assert not said_partial_result(output)
    assert step_leftovers(out) == []


def test_a_rerun_after_a_failed_conversion_keeps_one_file(run: Any, out: Path) -> None:
    for _ in range(2):
        code, output = run("-y", "https://fake.test/attached")
        assert code == 0
        assert names(out) == ["Clip.mkv"]
        assert output.count(t("not_converted", "Clip.mkv", "mp4")) == 1
        assert t("done") in output
    assert "has already been downloaded" in output
    assert len(history_rows()) == 2


def test_a_failed_conversion_under_playlist_items(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "1", "https://fake.test/attached-carousel")
    assert code == 0
    assert names(out) == ["01 - Clip [x1].mkv"]
    assert output.count(t("not_converted", "01 - Clip [x1].mkv", "mp4")) == 1
    assert not said_partial_result(output)
    assert t("done") in output


def test_several_urls_one_not_converted_and_one_broken(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code, output = several(media_url, out, capfd, "attached.mkv", JUNK, "webm.html")
    assert code == 1
    assert names(out) == ["attached.mkv", "junk.mp4", "webm (1).mp4"]
    # The whole line: the warning names the file, not its path.
    assert t("not_converted", "attached.mkv", "mp4") in output.splitlines()
    assert output.count(t("not_converted", "", "")[:4]) == 1
    assert output.count(t("done")) == 2
    assert t("failed", f"{media_url}/{JUNK}") in output
    assert t("failed", f"{media_url}/attached.mkv") not in output
    assert len(history_rows()) == 2


@pytest.mark.parametrize("container", ["mkv", "webm"])
def test_audio_mode_ignores_the_container(generic: Any, out: Path, container: str) -> None:
    code, output = generic(
        "webm.html", flags=("-y", "-a", "--video-format", container, "-o", str(out))
    )
    assert code == 0
    assert names(out) == ["webm (1).mp3"]
    assert container_steps(output) == 0


def test_listing_formats_converts_nothing(generic: Any, home: Path) -> None:
    code, output = generic("webm.html", flags=("-l", "--video-format", "mkv"))
    assert code == 0
    assert container_steps(output) == 0
    assert list(home.rglob("*.webm")) == list(home.rglob("*.mkv")) == []


def test_audio_named_like_a_converted_video_is_still_saved(
    generic: Any, media_url: str, out: Path
) -> None:
    # The step skips a .weba, but yt-dlp still looks for tiny.mp4 first (final_ext).
    weba = "typed/audio/webm/audio.webm/tiny.weba"
    code, _ = generic("tiny.webm", flags=("-y", "-o", str(out)))
    assert code == 0
    assert names(out) == ["tiny.mp4"]
    code, output = generic(weba, flags=("-y", "-o", str(out)))
    assert code == 0
    assert "has already been downloaded" not in output
    [audio] = [name for name in names(out) if name.endswith(".weba")]
    assert streams(out / audio) == {"audio"}
    assert _source_url(out / "tiny.mp4") == f"{media_url}/tiny.webm"
    assert len(names(out)) == 2


def test_a_merge_in_another_container_keeps_the_earlier_file(run: Any, out: Path) -> None:
    code, _ = run("-y", "https://fake.test/merged")
    assert code == 0
    before = (out / "Clip.mp4").read_bytes()
    code, _ = run("-y", "--video-format", "webm", "https://fake.test/merged")
    assert code == 0
    assert names(out) == ["Clip.mp4", "Clip.webm"]
    assert (out / "Clip.mp4").read_bytes() == before


@pytest.mark.parametrize(
    ("page", "first", "second", "container"),
    [
        ("tiny.mp4", (), ("--video-format", "webm"), "webm"),
        # A .webm saved as is: what main left behind for every single-format webm.
        ("webm.html", ("--video-format", "webm"), (), "mp4"),
    ],
    ids=["mp4-then-webm", "webm-then-mp4"],
)
def test_another_container_keeps_the_file_an_earlier_run_saved(
    generic: Any,
    out: Path,
    page: str,
    first: tuple[str, ...],
    second: tuple[str, ...],
    container: str,
) -> None:
    code, _ = generic(page, flags=("-y", *first, "-o", str(out)))
    assert code == 0
    [earlier] = names(out)
    before = codecs(out / earlier)
    code, _ = generic(page, flags=("-y", *second, "-o", str(out)))
    assert code == 0
    assert (out / earlier).exists()
    assert codecs(out / earlier) == before
    assert any(name.endswith(f".{container}") for name in names(out))
    # Run again in the same container: the converted file is the one already downloaded.
    kept = names(out)
    code, output = generic(page, flags=("-y", *second, "-o", str(out)))
    assert code == 0
    assert "has already been downloaded" in output
    assert "Converting video" not in output
    assert names(out) == kept


@pytest.mark.parametrize(
    "mode", [("-p",), ("--playlist-items", "1,2")], ids=["p", "playlist-items"]
)
def test_another_container_keeps_the_items_an_earlier_run_saved(
    run: Any, out: Path, mode: tuple[str, ...]
) -> None:
    url = "https://fake.test/attached-carousel"
    code, _ = run("-y", *mode, url)
    assert code == 0
    # The .mkv couldn't go into mp4, and the .mp4 already was one: both as served.
    earlier = names(out)
    assert earlier == ["01 - Clip [x1].mkv", "02 - Clip [v1].mp4"]
    before = {name: codecs(out / name) for name in earlier}
    code, output = run("-y", *mode, "--video-format", "webm", url)
    assert code == 0
    assert output.count("has already been downloaded") == 2
    assert {name: codecs(out / name) for name in earlier} == before
    assert sorted(set(names(out)) - set(earlier)) == ["01 - Clip [x1].webm", "02 - Clip [v1].webm"]


# Spec 30, round 2: what the container step deletes when yt-dlp reuses a file.


def video_format(output: str) -> str:
    """The --video-format of the one equivalent command a run printed (mp4 when left out)."""
    [line] = commands(output)
    argv = shlex.split(line)
    assert "-y" in argv
    return argv[argv.index("--video-format") + 1] if "--video-format" in argv else "mp4"


def test_a_third_container_keeps_both_earlier_files(generic: Any, out: Path) -> None:
    containers = ["mp4", "webm", "mkv"]
    for container in containers:
        code, output = generic(
            "tiny.mp4", flags=("-y", "--video-format", container, "-o", str(out))
        )
        assert code == 0
        assert t("done") in output
        assert "Deleting original file" not in output
        assert video_format(output) == container
    assert names(out) == ["tiny.mkv", "tiny.mp4", "tiny.webm"]
    assert codecs(out / "tiny.mp4") == codecs(out / "tiny.mkv") == {"video": "h264", "audio": "aac"}
    assert codecs(out / "tiny.webm")["video"] in {"vp8", "vp9", "av1"}
    # Each container again: its own file is the one already downloaded, and nothing converts.
    for container in containers:
        code, output = generic(
            "tiny.mp4", flags=("-y", "--video-format", container, "-o", str(out))
        )
        assert code == 0
        assert "has already been downloaded" in output
        assert container_steps(output) == 0
        assert t("done") in output
    assert names(out) == ["tiny.mkv", "tiny.mp4", "tiny.webm"]
    assert len(history_rows()) == 6


def test_a_failed_conversion_leaves_an_earlier_file_whole(run: Any, out: Path) -> None:
    url = "https://fake.test/attached"
    code, _ = run("-y", "--video-format", "mkv", url)
    assert code == 0
    assert names(out) == ["Clip.mkv"]
    assert streams(out / "Clip.mkv") == {"video", "audio", "attachment"}
    code, output = run("-y", url)
    assert code == 0
    assert "has already been downloaded" in output
    assert output.count(t("not_converted", "Clip.mkv", "mp4")) == 1
    assert t("done") in output
    assert names(out) == ["Clip.mkv"]
    assert streams(out / "Clip.mkv") == {"video", "audio", "attachment"}
    assert step_leftovers(out) == []
    # Into a container that takes it, the earlier file stays next to the conversion.
    code, output = run("-y", "--video-format", "webm", url)
    assert code == 0
    assert never_said("not_converted", output)
    assert names(out) == ["Clip.mkv", "Clip.webm"]
    assert streams(out / "Clip.mkv") == {"video", "audio", "attachment"}
    assert len(history_rows()) == 3


@pytest.mark.parametrize(
    "left", [["Clip.fv.mp4"], ["Clip.fa.mp4", "Clip.fv.mp4"]], ids=["video-stream", "both-streams"]
)
def test_a_merge_that_reuses_a_stream_file_is_replaced_by_its_conversion(
    run: Any, media_url: str, out: Path, left: list[str]
) -> None:
    # yt-dlp counts the merge as this run's download, even when it reused every stream.
    out.mkdir(parents=True)
    with urllib.request.urlopen(f"{media_url}/tiny.mp4") as response:
        body = response.read()
    for name in left:
        (out / name).write_bytes(body)
    code, output = run("-y", "--video-format", "webm", "https://fake.test/merged")
    assert code == 0
    assert output.count("has already been downloaded") == len(left)
    assert names(out) == ["Clip.webm"]
    found = codecs(out / "Clip.webm")
    assert found["video"] in {"vp8", "vp9", "av1"}
    assert found["audio"] in {"vorbis", "opus"}
    assert t("done") in output


def test_a_resumed_download_is_replaced_by_its_conversion(
    generic: Any, media_url: str, out: Path
) -> None:
    out.mkdir(parents=True)
    with urllib.request.urlopen(f"{media_url}/tiny.webm") as response:
        body = response.read()
    (out / "tiny.webm.part").write_bytes(body[: len(body) // 2])
    code, output = generic("tiny.webm", flags=("-y", "-o", str(out)))
    assert code == 0
    assert names(out) == ["tiny.mp4"]
    assert codecs(out / "tiny.mp4") == {"video": "h264", "audio": "aac"}
    assert "Deleting original file" in output
    assert t("done") in output


def test_several_urls_into_a_folder_with_earlier_files(
    media_url: str, out: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code, _ = several(media_url, out, capfd, "tiny.mp4", "webm.html")
    assert code == 0
    assert names(out) == ["tiny.mp4", "webm (1).mp4"]
    urls = [f"{media_url}/tiny.mp4", f"{media_url}/webm.html"]
    code = cli.main(["-y", "--video-format", "mkv", "-o", str(out), *urls])
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert code == 0
    assert output.count(t("done")) == 2
    # tiny.mp4 is reused and kept. The page's .webm went into its mp4, so it comes again,
    # and only that fresh download gives way to its conversion.
    assert output.count("has already been downloaded") == 1
    assert output.count("Deleting original file") == 1
    assert names(out) == ["tiny.mkv", "tiny.mp4", "webm (1).mkv", "webm (1).mp4"]
    assert video_format(output) == "mkv"
    assert len(history_rows()) == 4


def test_wizard_into_a_folder_with_an_earlier_file(generic: Any, out: Path) -> None:
    code, _ = generic("tiny.mp4", flags=("-y", "-o", str(out)))
    assert code == 0
    flags = ("-i", "--video-format", "webm", "-o", str(out))
    code, output = generic("tiny.mp4", *SINGLE_WALK, flags=flags)
    assert code == 0
    assert f"{wizard._summary_label('container')}: webm" in output
    assert "has already been downloaded" in output
    assert names(out) == ["tiny.mp4", "tiny.webm"]
    assert codecs(out / "tiny.mp4") == {"video": "h264", "audio": "aac"}
    assert video_format(output) == "webm"
    assert len(history_rows()) == 2
