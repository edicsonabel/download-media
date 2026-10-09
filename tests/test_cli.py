"""Tests for CLI argument parsing → DownloadOptions translation."""

from __future__ import annotations

from pathlib import Path

from download_media.cli import build_parser, options_from_args


def parse(argv: list[str]):
    return options_from_args(build_parser().parse_args(argv))


def test_minimal_args_produce_defaults() -> None:
    opts = parse(["http://x"])
    assert opts.urls == ["http://x"]
    assert opts.audio_only is False
    assert opts.quality == "1080"
    assert opts.video_format == "mp4"
    assert opts.cookies_browser is None
    assert opts.playlist_mode == "auto"


def test_audio_flag_and_format() -> None:
    opts = parse(["-a", "-f", "opus", "http://x"])
    assert opts.audio_only is True
    assert opts.audio_format == "opus"


def test_quality_and_video_format() -> None:
    opts = parse(["-q", "720", "--video-format", "mkv", "http://x"])
    assert opts.quality == "720"
    assert opts.video_format == "mkv"


def test_cookies_and_subs() -> None:
    opts = parse(["--cookies", "firefox", "-s", "--sub-langs", "en", "http://x"])
    assert opts.cookies_browser == "firefox"
    assert opts.subs is True
    assert opts.sub_langs == "en"


def test_playlist_modes_are_mutually_exclusive() -> None:
    opts_yes = parse(["-p", "http://x"])
    opts_no = parse(["-P", "http://x"])
    assert opts_yes.playlist_mode == "yes"
    assert opts_no.playlist_mode == "no"


def test_multiple_urls() -> None:
    opts = parse(["a", "b", "c"])
    assert opts.urls == ["a", "b", "c"]


def test_output_path_kept_as_path() -> None:
    opts = parse(["-o", "/tmp/x", "http://x"])
    assert opts.output == Path("/tmp/x")


def _fake_download(fail_with_cookies: bool, fail_without: bool, calls: list):
    from yt_dlp.utils import DownloadError

    def fake(url, opts):
        calls.append(opts.cookies_browser)
        if opts.cookies_browser and fail_with_cookies:
            raise DownloadError("with cookies")
        if not opts.cookies_browser and fail_without:
            raise DownloadError("without cookies")
        return 0

    return fake


def test_cookie_fallback_retries_without_cookies(monkeypatch) -> None:
    from download_media import cli

    calls: list = []
    monkeypatch.setattr(cli, "download", _fake_download(True, False, calls))
    opts = parse(["--cookies", "brave", "http://x"])
    assert cli._download_with_cookie_fallback("http://x", opts) == (0, True)
    assert calls == ["brave", None]


def test_cookie_fallback_reraises_original_error(monkeypatch) -> None:
    import pytest
    from yt_dlp.utils import DownloadError

    from download_media import cli

    calls: list = []
    monkeypatch.setattr(cli, "download", _fake_download(True, True, calls))
    with pytest.raises(DownloadError, match="with cookies"):
        cli._download_with_cookie_fallback("http://x", parse(["--cookies", "brave", "http://x"]))
    assert calls == ["brave", None]


def test_cookie_fallback_skipped_without_cookies(monkeypatch) -> None:
    import pytest
    from yt_dlp.utils import DownloadError

    from download_media import cli

    calls: list = []
    monkeypatch.setattr(cli, "download", _fake_download(False, True, calls))
    with pytest.raises(DownloadError):
        cli._download_with_cookie_fallback("http://x", parse(["http://x"]))
    assert calls == [None]


def test_cookie_fallback_reports_cookies_kept_when_they_work(monkeypatch) -> None:
    from download_media import cli

    calls: list = []
    monkeypatch.setattr(cli, "download", _fake_download(False, False, calls))
    opts = parse(["--cookies", "brave", "http://x"])
    assert cli._download_with_cookie_fallback("http://x", opts) == (0, False)
    assert calls == ["brave"]


def _run_main(monkeypatch, capsys, argv: list[str], results: list[tuple[bool, bool]]) -> str:
    from download_media import cli

    outcomes = iter(results)
    monkeypatch.setattr(cli, "_download_one", lambda url, opts: next(outcomes))
    cli.main(argv)
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_equivalent_drops_cookies_when_every_url_needed_the_retry(monkeypatch, capsys) -> None:
    line = _run_main(
        monkeypatch, capsys, ["-y", "--cookies", "brave", "a", "b"], [(True, True)] * 2
    )
    assert "--cookies" not in line


def test_equivalent_keeps_cookies_when_some_url_used_them(monkeypatch, capsys) -> None:
    results = [(True, True), (True, False)]
    line = _run_main(monkeypatch, capsys, ["-y", "--cookies", "brave", "a", "b"], results)
    assert "--cookies brave" in line


def test_equivalent_keeps_cookies_when_a_download_failed(monkeypatch, capsys) -> None:
    line = _run_main(monkeypatch, capsys, ["-y", "--cookies", "brave", "a"], [(False, False)])
    assert "--cookies brave" in line
