"""End-to-end runs through ``cli.main`` with real yt-dlp and ffmpeg, loopback only."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from download_media import cli, i18n
from download_media.config import AUDIO_FORMATS, BROWSERS, history_file
from download_media.i18n import t

CAROUSEL = "https://fake.test/carousel"
LIST_URL = "https://fake.test/carousel?list=1"  # Looks multi-entry, so direct mode asks.
EMPTY = "https://fake.test/empty"
PRIVATE = "https://fake.test/private"
VIDEO = "https://fake.test/video"
VIDEO_B = "https://fake.test/video-b"
BOTH_ITEMS = ["02 - Clip [v1].mp4", "04 - Clip [v2].mp4"]


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def history_rows() -> list[str]:
    log = history_file()
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def commands(output: str) -> list[str]:
    """The equivalent commands a run printed."""
    lines = (line.strip() for line in output.splitlines())
    return [line for line in lines if line.startswith("download-media ")]


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
def replay(
    fake_site: list[Any], capfd: pytest.CaptureFixture[str], answers: Any
) -> Callable[..., tuple[int, str]]:
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


def test_playlist_items_out_of_range_is_a_failure(run: Any, out: Path) -> None:
    code, output = run("-y", "--playlist-items", "9", CAROUSEL)
    assert code == 1
    assert names(out) == []
    assert t("no_supported_items") in output
    assert t("done") not in output
    assert history_rows() == []


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
        (("-s",), (VIDEO,)),
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
    assert t("no_supported_items") in output
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
        ("-1", ["04 - Clip [v2].mp4"]),
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
    # URL, type, quality, container, folder, cookies, then the preview.
    answers("", "", "", "", "", "2", "")
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
            "Ningún item es descargable.",
            "Listo.",
        ),
        (
            "en",
            "Equivalent command to repeat this download:",
            "No items are downloadable.",
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
