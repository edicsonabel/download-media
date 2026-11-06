"""End-to-end runs through ``cli.main`` with real yt-dlp and ffmpeg, loopback only."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from download_media import cli
from download_media.config import AUDIO_FORMATS, history_file
from download_media.i18n import t

CAROUSEL = "https://fake.test/carousel"
LIST_URL = "https://fake.test/carousel?list=1"  # Looks multi-entry, so direct mode asks.


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
