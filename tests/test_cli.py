"""Tests for CLI argument parsing → DownloadOptions translation."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_playlist_items_implies_playlist_mode() -> None:
    opts = parse(["--playlist-items", "2,4", "http://x"])
    assert opts.playlist_mode == "yes"
    assert opts.playlist_items == "2,4"


def test_no_playlist_items_by_default() -> None:
    assert parse(["-p", "http://x"]).playlist_items is None


@pytest.mark.parametrize("argv", [["--playlist-items", "2"], ["--playlist-items=2"]])
def test_playlist_items_is_an_intent_flag(argv: list[str]) -> None:
    from download_media.cli import should_run_wizard

    full = [*argv, "http://x"]
    assert should_run_wizard(build_parser().parse_args(full), full) is False


@pytest.mark.parametrize("flag", ["-p", "-P"])
def test_playlist_items_excludes_the_other_playlist_flags(flag: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        parse([flag, "--playlist-items", "2", "http://x"])
    assert exc.value.code == 2
    assert "--playlist-items" in capsys.readouterr().err


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

    from download_media.downloader import DownloadResult

    def fake(url, opts):
        calls.append(opts.cookies_browser)
        if opts.cookies_browser and fail_with_cookies:
            raise DownloadError("with cookies")
        if not opts.cookies_browser and fail_without:
            raise DownloadError("without cookies")
        return DownloadResult(saved=1)

    return fake


def test_cookie_fallback_retries_without_cookies(monkeypatch) -> None:
    from download_media import cli

    calls: list = []
    monkeypatch.setattr(cli, "download", _fake_download(True, False, calls))
    opts = parse(["--cookies", "brave", "http://x"])
    assert cli._download_with_cookie_fallback("http://x", opts)[1] is True
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
    assert cli._download_with_cookie_fallback("http://x", opts)[1] is False
    assert calls == ["brave"]


def _run_main(monkeypatch, capsys, argv: list[str], results: list) -> str:
    from download_media import cli

    outcomes = iter(cli._Outcome(*result) for result in results)
    monkeypatch.setattr(cli, "_download_one", lambda url, opts: next(outcomes))
    cli.main(argv)
    return capsys.readouterr().out


def test_equivalent_drops_cookies_when_every_url_needed_the_retry(monkeypatch, capsys) -> None:
    output = _run_main(
        monkeypatch, capsys, ["-y", "--cookies", "brave", "a", "b"], [(True, True, "no")] * 2
    )
    assert "--cookies" not in output


def test_equivalent_keeps_cookies_when_some_url_used_them(monkeypatch, capsys) -> None:
    results = [(True, True, "no"), (True, False, "no")]
    output = _run_main(monkeypatch, capsys, ["-y", "--cookies", "brave", "a", "b"], results)
    assert "--cookies brave" in output


def test_equivalent_keeps_cookies_when_a_download_failed(monkeypatch, capsys) -> None:
    output = _run_main(
        monkeypatch, capsys, ["-y", "--cookies", "brave", "a"], [(False, False, "no")]
    )
    assert "--cookies brave" in output


def test_equivalent_cookie_rule_ignores_urls_never_attempted(monkeypatch, capsys) -> None:
    results = [(False, False, None), (True, True, "no")]
    output = _run_main(monkeypatch, capsys, ["-y", "--cookies", "brave", "a", "b"], results)
    assert "--cookies" not in output
    assert output.strip().splitlines()[-1].strip() == "download-media -y b"


def test_equivalent_is_silent_when_nothing_was_attempted(monkeypatch, capsys) -> None:
    from download_media.i18n import t

    output = _run_main(monkeypatch, capsys, ["-y", "a", "b"], [(False, False, None)] * 2)
    assert t("equiv_cmd") not in output


def test_equivalent_groups_urls_by_mode_in_first_seen_order(monkeypatch, capsys) -> None:
    from download_media.i18n import t

    results = [(True, False, "yes"), (True, False, "no"), (True, False, "yes")]
    output = _run_main(monkeypatch, capsys, ["-y", "a", "b", "c"], results)
    assert output.count(t("equiv_cmd")) == 1
    assert [line.strip() for line in output.strip().splitlines()[-2:]] == [
        "download-media -y -p a c",
        "download-media -y b",
    ]


def test_playlist_items_excludes_both_playlist_flags(capsys) -> None:
    import pytest

    for flag in ("-p", "-P"):
        with pytest.raises(SystemExit) as exc:
            parse([flag, "--playlist-items", "2", "http://x"])
        assert exc.value.code == 2
        assert "--playlist-items" in capsys.readouterr().err


def test_playlist_items_equals_form_is_an_intent_flag() -> None:
    from download_media import cli

    argv = ["--playlist-items=2", "http://x"]
    assert cli.should_run_wizard(cli.build_parser().parse_args(argv), argv) is False
    assert parse(argv).playlist_items == "2"
    assert parse(argv).playlist_mode == "yes"


def test_cookies_accepts_keyring_spec() -> None:
    assert parse(["--cookies", "brave+gnomekeyring", "http://x"]).cookies_browser == (
        "brave+gnomekeyring"
    )


def test_cookies_rejects_unknown_browser(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        parse(["--cookies", "netscape", "http://x"])
    assert exc.value.code == 2
    assert "netscape" in capsys.readouterr().err


def test_direct_mode_yes_stays_single_on_playlist_urls() -> None:
    from download_media import cli

    opts = parse(["-y", "https://site.test/playlist?list=1"])
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) == ("no", None)


def test_direct_mode_plain_url_never_asks() -> None:
    from download_media import cli

    opts = parse(["-a", "https://site.test/video"])
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) == ("no", None)


def test_direct_mode_forced_modes_never_ask() -> None:
    from download_media import cli

    opts = parse(["-p", "https://site.test/playlist"])
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) == ("yes", None)


def test_direct_mode_playlist_question(answers) -> None:
    from download_media import cli

    opts = parse(["-a", "https://site.test/playlist"])
    answers("2")
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) == ("yes", None)
    answers("")
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) == ("no", None)
    answers(EOFError)
    assert cli._resolve_playlist_for_direct_mode(opts.urls[0], opts) is None


def test_cancelling_playlist_question_stops_the_url(answers, capsys) -> None:
    from download_media import cli

    answers(EOFError)
    outcome = cli._download_one("https://site.test/playlist", parse(["-a", "x"]))
    assert outcome == cli._Outcome(ok=False, cookies_dropped=False, mode=None)


def test_high_quality_mp4_warns_about_h264_cap(monkeypatch, capsys, tmp_path) -> None:
    from download_media import cli
    from download_media.downloader import DownloadResult
    from download_media.i18n import t

    monkeypatch.setattr(cli, "download", lambda url, opts: DownloadResult(saved=1))
    opts = parse(["-y", "-q", "2160", "-o", str(tmp_path), "http://x"])
    assert cli._download_one("http://x", opts) == cli._Outcome(True, False, "no")
    assert t("mp4_h264_cap", "2160") in capsys.readouterr().out


def test_interrupt_propagates_after_cancel_message(monkeypatch, tmp_path) -> None:
    import pytest

    from download_media import cli

    def interrupted(url, opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "download", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli._download_one("http://x", parse(["-y", "-o", str(tmp_path), "http://x"]))


def _hint(capsys, message: str, argv: list[str]) -> str:
    from download_media import cli

    cli._hint_for_download_error(message, parse([*argv, "http://x"]))
    return capsys.readouterr().out


def test_hint_for_missing_secretstorage(capsys) -> None:
    assert "python-secretstorage" in _hint(capsys, "SecretStorage not available", [])


def test_hint_suggests_cookies_for_auth_errors(capsys) -> None:
    assert "--cookies <browser>" in _hint(capsys, "Login required", [])
    assert "--cookies <browser>" not in _hint(capsys, "Login required", ["--cookies", "firefox"])


@pytest.mark.parametrize(
    "message",
    ["Sign in to confirm you're not a bot", "Login required", "This video is private"],
)
def test_every_auth_error_hints_cookies_only_without_them(capsys, message: str) -> None:
    assert "--cookies <browser>" in _hint(capsys, message, [])
    assert _hint(capsys, message, ["--cookies", "firefox"]) == ""


def test_hint_stays_quiet_for_unrelated_errors(capsys) -> None:
    assert _hint(capsys, "HTTP Error 500", []) == ""


@pytest.mark.parametrize(
    "message",
    [
        "[site] x: This video is only available for registered users. Use --cookies for it.",
        "[site] x: The webpage request was redirected to the login page. You have exceeded the "
        "rate-limit for accessing posts anonymously. Use --cookies for it.",
    ],
)
def test_hint_follows_the_probe_on_what_cookies_can_fix(capsys, message: str) -> None:
    assert "--cookies <browser>" in _hint(capsys, message, [])


@pytest.mark.parametrize(
    "message",
    [
        "[generic] private: Unable to download webpage: HTTP Error 429: Too Many Requests",
        "This private video is blocked here\nYou might want to use a VPN or a proxy server.",
    ],
)
def test_hint_stays_quiet_when_cookies_cannot_help(capsys, message: str) -> None:
    assert _hint(capsys, message, []) == ""


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [generic] login: Unable to download webpage: HTTP Error 404: Not Found",
        "\x1b[0;31mERROR:\x1b[0m [generic] login: Unable to download webpage: HTTP Error 404",
        "[fakesite] a: HTTP Error 404: Not Found\n[fakesite] login: HTTP Error 404: Not Found",
    ],
    ids=["plain", "coloured", "second-error"],
)
def test_hint_ignores_the_id_of_a_real_download_error(capsys, message: str) -> None:
    assert _hint(capsys, message, []) == ""


def test_keyring_hint_only_when_it_can_help(capsys) -> None:
    assert "+gnomekeyring" in _hint(capsys, "boom", ["--cookies", "brave"])
    assert _hint(capsys, "boom", ["--cookies", "brave+kwallet6"]) == ""
    assert _hint(capsys, "boom", ["--cookies", "firefox"]) == ""


def test_main_without_url_uses_clipboard(monkeypatch, capsys) -> None:
    from download_media import cli

    urls: list[str] = []
    monkeypatch.setattr(cli, "read_clipboard", lambda: "http://clip")
    outcome = cli._Outcome(True, False, "no")
    monkeypatch.setattr(cli, "_download_one", lambda url, opts: (urls.append(url), outcome)[1])
    assert cli.main(["-y"]) == 0
    assert urls == ["http://clip"]


def test_main_without_url_or_clipboard_dies(monkeypatch) -> None:
    import pytest

    from download_media import cli

    monkeypatch.setattr(cli, "read_clipboard", lambda: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["-y"])
    assert exc.value.code != 0


def test_main_cancelled_wizard_exits_clean(monkeypatch, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    monkeypatch.setattr(cli.wizard, "run", lambda opts: None)
    assert cli.main([]) == 0
    assert t("cancelled") in capsys.readouterr().out


def test_module_entry_point_is_importable() -> None:
    import download_media.__main__  # noqa: F401
