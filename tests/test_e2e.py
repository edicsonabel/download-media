"""End-to-end runs through ``cli.main`` with real yt-dlp and ffmpeg, loopback only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from download_media import cli
from download_media.config import AUDIO_FORMATS, history_file
from download_media.i18n import t

CAROUSEL = "https://fake.test/carousel"


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def history_rows() -> list[str]:
    log = history_file()
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


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


def test_detected_keyring_reaches_wizard_probe(
    run: Any, fake_site: list[Any], tmp_path: Path, answers: Any
) -> None:
    _flags_file(tmp_path)
    # URL, type, quality, container, folder, cookies.
    answers("", "", "", "", "", "")
    run("-i", "--cookies", "brave", "https://fake.test/needs-cookies")
    assert set(fake_site) == {("brave", None, "GNOMEKEYRING", None)}
