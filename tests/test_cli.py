"""Tests for CLI argument parsing → DownloadOptions translation."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_media.cli import build_parser, options_from_args, should_run_wizard
from download_media.config import DEFAULT_SUB_LANGS


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


def test_sub_langs_alone_turns_subtitles_on() -> None:
    opts = parse(["--sub-langs", "fr", "http://x"])
    assert opts.subs is True
    assert opts.sub_langs == "fr"


def test_no_subtitle_flag_leaves_subtitles_off() -> None:
    opts = parse(["http://x"])
    assert opts.subs is False
    assert opts.sub_langs == DEFAULT_SUB_LANGS


def test_sub_langs_set_to_the_default_still_turns_subtitles_on() -> None:
    opts = parse(["--sub-langs", DEFAULT_SUB_LANGS, "http://x"])
    assert opts.subs is True
    assert opts.sub_langs == DEFAULT_SUB_LANGS


def test_s_alone_uses_the_default_languages() -> None:
    opts = parse(["-s", "http://x"])
    assert opts.subs is True
    assert opts.sub_langs == DEFAULT_SUB_LANGS


@pytest.mark.parametrize(
    ("argv", "langs"),
    [
        (["-a", "--sub-langs", "en", "http://x"], "en"),
        (["--sub-langs=-live_chat", "http://x"], "-live_chat"),
        (["-i", "--sub-langs", "en", "http://x"], "en"),
    ],
    ids=["audio", "dash-led", "interactive"],
)
def test_sub_langs_turns_subtitles_on_with_other_flags(argv: list[str], langs: str) -> None:
    opts = parse(argv)
    assert opts.subs is True
    assert opts.sub_langs == langs


@pytest.mark.parametrize("argv", [["--sub-langs", "en", "u"], ["--sub-langs=en", "u"]])
def test_sub_langs_alone_skips_the_wizard(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    assert should_run_wizard(args, argv) is False


def test_help_describes_subtitles(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "Download subtitles, embed them in videos and keep them as files." in text
    assert (
        f"Subtitle languages, comma-separated; implies -s (default: {DEFAULT_SUB_LANGS})." in text
    )


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


def _equivalent_lines(capsys, runs: list, results: list) -> list[str]:
    from download_media import cli

    cli._print_equivalent(runs, [cli._Outcome(*result) for result in results])
    lines = (line.strip() for line in capsys.readouterr().out.splitlines())
    return [line for line in lines if line.startswith("download-media ")]


def test_equivalent_shares_one_command_between_equal_wizard_walks(capsys) -> None:
    runs = [("a", parse(["a"])), ("b", parse(["b"]))]
    lines = _equivalent_lines(capsys, runs, [(True, False, "no")] * 2)
    assert lines == ["download-media -y a b"]


def test_equivalent_gives_different_wizard_walks_a_command_each(capsys) -> None:
    runs = [("a", parse(["a"])), ("b", parse(["-a", "b"])), ("c", parse(["c"]))]
    lines = _equivalent_lines(capsys, runs, [(True, False, "no")] * 3)
    assert lines == ["download-media -y a c", "download-media -y -a b"]


def test_equivalent_cookie_drop_is_decided_per_browser(capsys) -> None:
    runs = [
        ("a", parse(["--cookies", "brave", "a"])),
        ("b", parse(["--cookies", "firefox", "b"])),
        ("c", parse(["--cookies", "firefox", "c"])),
    ]
    results = [(True, True, "no"), (True, False, "no"), (True, True, "no")]
    lines = _equivalent_lines(capsys, runs, results)
    assert lines == ["download-media -y a", "download-media -y --cookies firefox b c"]


@pytest.mark.parametrize("dropped_first", [True, False])
def test_equivalent_merges_a_walk_whose_cookies_were_dropped_with_one_without(
    capsys, dropped_first: bool
) -> None:
    dropped = (("a", parse(["--cookies", "brave", "a"])), (True, True, "no"))
    plain = (("b", parse(["b"])), (True, False, "no"))
    pairs = [dropped, plain] if dropped_first else [plain, dropped]
    lines = _equivalent_lines(capsys, [run for run, _ in pairs], [result for _, result in pairs])
    assert lines == ["download-media -y a b" if dropped_first else "download-media -y b a"]


def test_equivalent_leaves_out_a_declined_walk(capsys) -> None:
    runs = [("a", parse(["a"])), ("b", parse(["-a", "b"])), ("c", parse(["c"]))]
    results = [(True, False, "no"), (False, False, None), (False, False, "no")]
    lines = _equivalent_lines(capsys, runs, results)
    assert lines == ["download-media -y a c"]


def test_equivalent_keeps_cookies_when_a_walk_with_them_failed(capsys) -> None:
    runs = [("a", parse(["--cookies", "brave", "a"])), ("b", parse(["--cookies", "brave", "b"]))]
    lines = _equivalent_lines(capsys, runs, [(True, True, "no"), (False, False, "no")])
    assert lines == ["download-media -y --cookies brave a b"]


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


def test_ctrl_c_mid_download_names_the_url_and_folder(monkeypatch, tmp_path, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    def interrupted(url, opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "download", interrupted)
    with pytest.raises(cli._Interrupted) as exc:
        cli._download_one("http://x", parse(["-y", "-o", str(tmp_path), "http://x"]))
    assert isinstance(exc.value, KeyboardInterrupt)
    assert exc.value.outcome == cli._Outcome(False, False, "no")
    output = capsys.readouterr().out
    assert "\n" + t("interrupted", "http://x", tmp_path) in output
    assert t("cancelled") not in output


@pytest.mark.parametrize("flags", [[], ["--cookies", "firefox"]])
def test_system_error_fails_only_the_url(monkeypatch, tmp_path, capsys, flags) -> None:
    from download_media import cli
    from download_media.i18n import t

    calls: list[str | None] = []
    error = OSError(28, "No space left on device", "/x/f")

    def full_disk(url, opts):
        calls.append(opts.cookies_browser)
        raise error

    monkeypatch.setattr(cli, "download", full_disk)
    opts = parse(["-y", *flags, "-o", str(tmp_path), "http://x"])
    assert cli._download_one("http://x", opts) == cli._Outcome(False, False, "no")
    captured = capsys.readouterr()
    assert t("unexpected_error", error) in captured.err
    assert "Traceback" not in captured.err
    assert t("failed", "http://x") in captured.out
    # Not about cookies: no retry without them.
    assert len(calls) == 1


@pytest.mark.parametrize("error", [SystemExit(3), GeneratorExit()])
def test_base_exceptions_are_not_swallowed(monkeypatch, tmp_path, capsys, error) -> None:
    from download_media import cli
    from download_media.i18n import t

    def stopped(url, opts):
        raise error

    monkeypatch.setattr(cli, "download", stopped)
    with pytest.raises(type(error)):
        cli._download_one("http://x", parse(["-y", "-o", str(tmp_path), "http://x"]))
    assert t("failed", "http://x") not in capsys.readouterr().out


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(3)])
def test_listing_formats_lets_base_exceptions_through(monkeypatch, capsys, error) -> None:
    from download_media import cli
    from download_media.i18n import t

    def stopped(url, opts):
        raise error

    monkeypatch.setattr(cli, "list_formats", stopped)
    with pytest.raises(type(error)):
        cli._list_formats("http://x", parse(["-l", "http://x"]))
    assert t("failed", "http://x") not in capsys.readouterr().out


def test_listing_formats_reports_a_system_error_in_one_line(monkeypatch, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    error = PermissionError(13, "Permission denied", "/x/cache")

    def denied(url, opts):
        raise error

    monkeypatch.setattr(cli, "list_formats", denied)
    assert cli._list_formats("http://x", parse(["-l", "http://x"])) == 1
    captured = capsys.readouterr()
    assert t("unexpected_error", error) in captured.err
    assert "Traceback" not in captured.err
    assert t("unexpected_bug", f"PermissionError: {error}") not in captured.err
    assert t("failed", "http://x") in captured.out


def test_a_bug_on_the_retry_without_cookies_is_the_one_shown(monkeypatch, tmp_path, capsys) -> None:
    from yt_dlp.utils import DownloadError

    from download_media import cli
    from download_media.i18n import t

    calls: list[str | None] = []

    def first_cookies_then_bug(url, opts):
        calls.append(opts.cookies_browser)
        if opts.cookies_browser:
            raise DownloadError("ERROR: empty response")
        raise TypeError("boom")

    monkeypatch.setattr(cli, "download", first_cookies_then_bug)
    opts = parse(["-y", "--cookies", "firefox", "-o", str(tmp_path), "http://x"])
    assert cli._download_one("http://x", opts) == cli._Outcome(False, False, "no")
    captured = capsys.readouterr()
    assert calls == ["firefox", None]
    assert t("cookies_retry", "firefox") in captured.out
    assert "Traceback" in captured.err
    assert t("unexpected_bug", "TypeError: boom") in captured.err
    assert t("failed", "http://x") in captured.out


def test_an_unnamed_system_error_still_says_something(monkeypatch, tmp_path, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    def odd(url, opts):
        raise OSError("device went away")

    monkeypatch.setattr(cli, "download", odd)
    opts = parse(["-y", "-o", str(tmp_path), "http://x"])
    assert cli._download_one("http://x", opts) == cli._Outcome(False, False, "no")
    assert t("unexpected_error", "device went away") in capsys.readouterr().err


def test_main_exits_1_and_prints_the_command_after_a_bug(monkeypatch, tmp_path, capsys) -> None:
    from download_media import cli
    from download_media.downloader import DownloadResult
    from download_media.i18n import t

    def bug_on_first(url, opts):
        if url == "http://a":
            raise AttributeError("'NoneType' object has no attribute 'get'")
        return DownloadResult(saved=1)

    monkeypatch.setattr(cli, "download", bug_on_first)
    assert cli.main(["-y", "-o", str(tmp_path), "http://a", "http://b"]) == 1
    captured = capsys.readouterr()
    assert t("failed", "http://a") in captured.out
    assert t("failed", "http://b") not in captured.out
    assert t("done") in captured.out
    assert "http://a http://b" in captured.out


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


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [generic] login: Unable to download webpage: [Errno 111] Connection refused",
        "\x1b[0;31mERROR:\x1b[0m [generic] login: Unable to download webpage: Connection refused",
        "[fakesite] a: This post is gone\n[fakesite] login: This post is gone",
    ],
    ids=["plain", "coloured", "second-error"],
)
def test_hint_ignores_the_id_of_any_download_error(capsys, message: str) -> None:
    assert _hint(capsys, message, []) == ""


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [generic] s401: Unable to download webpage: HTTP Error 401: Unauthorized",
        "\x1b[0;31mERROR:\x1b[0m [site] x: Private video",
        "ERROR: Login required",
        "[fakesite] a: This post is gone\n[fakesite] b: Login required",
    ],
    ids=["401", "coloured", "no-site", "second-error"],
)
def test_hint_still_reads_the_sites_words_in_a_download_error(capsys, message: str) -> None:
    assert "--cookies <browser>" in _hint(capsys, message, [])


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


def test_main_cancelled_wizard_exits_1(monkeypatch, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    monkeypatch.setattr(cli.wizard, "run_all", lambda opts: None)
    assert cli.main([]) == 1
    assert t("cancelled") in capsys.readouterr().out


def test_main_returns_the_interrupt_status_after_ctrl_c_in_the_wizard(monkeypatch, capsys) -> None:
    from download_media import cli
    from download_media.i18n import t

    def interrupted(opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.wizard, "run_all", interrupted)
    assert cli.main([]) == cli.EXIT_INTERRUPTED == 130
    captured = capsys.readouterr()
    assert t("cancelled") in captured.out.splitlines()
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("code", [0, 1, 130])
def test_entry_dies_by_sigint_after_an_interrupt(monkeypatch, code: int) -> None:
    import os
    import signal

    from download_media import cli

    calls: list[tuple[str, int, object]] = []
    monkeypatch.setattr(cli, "main", lambda: code)
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: calls.append(("signal", sig, handler))
    )
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
    with pytest.raises(SystemExit) as exc:
        cli.entry()
    if code == 130:
        assert calls == [
            ("signal", signal.SIGINT, signal.SIG_DFL),
            ("kill", os.getpid(), signal.SIGINT),
        ]
    else:
        assert calls == []
        assert exc.value.code == code


def test_ctrl_c_ends_the_process_by_sigint_without_a_traceback() -> None:
    import os
    import selectors
    import signal
    import subprocess
    import sys
    import time

    from download_media.i18n import t

    # Same $LANG as this process, so t() matches what the child prints.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for name in ("WAYLAND_DISPLAY", "DISPLAY"):
        env.pop(name, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "download_media", "-i", "http://127.0.0.1:9/x"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        seen = b""
        prompt = t("ask_url").encode()
        deadline = time.monotonic() + 30
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while prompt not in seen and time.monotonic() < deadline:
                if selector.select(timeout=1):
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    seen += chunk
        assert prompt in seen, seen
        os.killpg(proc.pid, signal.SIGINT)
        rest, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == -signal.SIGINT
    assert t("cancelled") in (seen + rest).decode()
    assert "Traceback" not in err.decode()


def test_module_entry_point_is_importable() -> None:
    import download_media.__main__  # noqa: F401


# Ctrl-C on the real process, attacked (spec 23). Signals go only to the group a test started.
_NOWHERE = "http://127.0.0.1:9/x"


def _child_env(lang: str | None = None) -> dict[str, str]:
    """The test's environment; without ``lang``, its $LANG too, so t() matches the child."""
    import os

    # Buffered, as a real pipe is: PYTHONUNBUFFERED would hide a lost line.
    drop = {"WAYLAND_DISPLAY", "DISPLAY", "PYTHONUNBUFFERED"}
    env = {name: value for name, value in os.environ.items() if name not in drop}
    if lang is not None:
        env.update(LANG=lang, LC_ALL=lang)
    return env


def _read_until(fd: int, marker: bytes, timeout: float = 30) -> bytes:
    import os
    import selectors
    import time

    seen = b""
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while marker not in seen and time.monotonic() < deadline:
            if selector.select(timeout=1):
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                seen += chunk
    assert marker in seen, seen
    return seen


def _spawn(argv: list[str], env: dict[str, str]):
    import subprocess

    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )


def _reap(proc) -> None:
    import os
    import signal

    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def _launchers() -> list:
    import sys

    script = Path(sys.executable).parent / "download-media"
    return [
        pytest.param([sys.executable, "-m", "download_media"], id="module"),
        pytest.param(
            [str(script)],
            id="console-script",
            marks=pytest.mark.skipif(not script.exists(), reason="no console script"),
        ),
    ]


@pytest.mark.parametrize("launcher", _launchers())
@pytest.mark.parametrize("lang", ["es", "en"])
def test_ctrl_c_with_stdout_piped_keeps_its_line_on_a_line_of_its_own(
    lang: str, launcher: list[str]
) -> None:
    import os
    import signal

    from download_media.i18n import _MESSAGES

    messages = _MESSAGES[lang]
    locale = {"es": "es_ES.UTF-8", "en": "en_US.UTF-8"}[lang]
    proc = _spawn([*launcher, "-i", _NOWHERE], _child_env(locale))
    try:
        assert proc.stdout is not None
        seen = _read_until(proc.stdout.fileno(), messages["ask_url"].encode())
        os.killpg(proc.pid, signal.SIGINT)
        rest, err = proc.communicate(timeout=30)
    finally:
        _reap(proc)
    assert proc.returncode == -signal.SIGINT
    # Lost without the flush before the self-kill; glued to the prompt without the fresh line.
    assert messages["cancelled"] in (seen + rest).decode().splitlines()
    assert "Traceback" not in err.decode()


def test_ctrl_c_with_the_pipe_s_reader_gone_still_dies_by_sigint() -> None:
    import os
    import signal
    import sys

    from download_media.i18n import t

    proc = _spawn([sys.executable, "-m", "download_media", "-i", _NOWHERE], _child_env())
    try:
        assert proc.stdout is not None and proc.stderr is not None
        _read_until(proc.stdout.fileno(), t("ask_url").encode())
        # `download-media ... | tee log`: the same Ctrl-C kills tee first.
        proc.stdout.close()
        os.killpg(proc.pid, signal.SIGINT)
        err = proc.stderr.read()
        proc.wait(timeout=30)
    finally:
        _reap(proc)
    assert proc.returncode == -signal.SIGINT
    assert b"Traceback" not in err
    assert b"Exception ignored" not in err


# A reader that forwards up to the prompt and leaves, as `| tee log` does on the same Ctrl-C.
_READER = """
import os, sys
marker, seen = sys.argv[1].encode(), b""
while marker not in seen:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    seen += chunk
os.write(1, seen + b"\\nreader gone %d\\n" % os.getpid())
"""


def _wait_gone(pid: int, timeout: float = 30) -> None:
    """Until ``pid`` has exited (a zombie has closed its files already)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return
        if state in ("Z", "X"):
            return
        time.sleep(0.05)
    raise AssertionError(f"reader {pid} still running")


@pytest.mark.parametrize(
    "piped",
    [
        pytest.param(False, id="plain"),
        pytest.param(True, id="reader-gone"),
    ],
)
def test_ctrl_c_stops_a_calling_bash_loop(piped: bool) -> None:
    import os
    import shlex
    import shutil
    import signal
    import sys

    from download_media.i18n import t

    if shutil.which("bash") is None:
        pytest.skip("no bash")
    prompt = t("ask_url")
    child = shlex.join([sys.executable, "-m", "download_media", "-i", _NOWHERE])
    if piped:
        child += " | " + shlex.join([sys.executable, "-c", _READER, prompt])
    # A second iteration ends the loop at once, so a loop that goes on doesn't hang the test.
    script = f'for i in 1 2; do [ "$i" = 2 ] && echo looped && exit 0; {child}; done'
    proc = _spawn(["bash", "-c", script], _child_env())
    try:
        assert proc.stdout is not None
        marker = b"reader gone" if piped else prompt.encode()
        seen = _read_until(proc.stdout.fileno(), marker)
        if piped:
            while b"\n" not in seen.split(marker)[1]:
                seen += _read_until(proc.stdout.fileno(), b"\n")
            _wait_gone(int(seen.split(marker)[1].split()[0]))
        os.killpg(proc.pid, signal.SIGINT)
        rest, err = proc.communicate(timeout=30)
    finally:
        _reap(proc)
    assert b"looped" not in seen + rest
    # Bash ends itself by SIGINT only when its child did: that's what stops the loop.
    assert proc.returncode == -signal.SIGINT
    assert b"Traceback" not in err


def test_a_second_ctrl_c_on_the_cancel_line_shows_no_traceback(monkeypatch) -> None:
    import os
    import signal
    import sys

    from download_media import cli
    from download_media.i18n import t

    def interrupted(opts):
        raise KeyboardInterrupt

    real_warn = cli.warn
    pressed: list[str] = []

    def warn(message: str) -> None:
        if message == t("cancelled") and not pressed:
            pressed.append(message)
            raise KeyboardInterrupt  # The second press, while the line prints.
        real_warn(message)

    sent: list[int] = []
    monkeypatch.setattr(sys, "argv", ["download-media"])
    monkeypatch.setattr(cli.wizard, "run_all", interrupted)
    monkeypatch.setattr(cli, "warn", warn)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(sig))
    escaped = False
    try:
        cli.entry()
    except SystemExit:
        pass
    except KeyboardInterrupt:  # Caught here: pytest would end the whole session on it.
        escaped = True
    assert pressed
    assert not escaped
    assert sent == [signal.SIGINT]


@pytest.mark.xfail(
    strict=True,
    reason="Out of scope (pre-existing): a Ctrl-C while Python imports yt-dlp, before main() "
    "runs, still ends in a traceback; planner row",
)
def test_ctrl_c_while_the_tool_starts_shows_no_traceback(tmp_path: Path) -> None:
    import signal
    import subprocess
    import sys

    # Ctrl-C lands while `download_media.cli` imports yt-dlp, about 0.1 s after the launch.
    (tmp_path / "sitecustomize.py").write_text(
        "import os, signal, sys\n"
        "class _Press:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'yt_dlp':\n"
        "            sys.meta_path.remove(self)\n"
        "            os.kill(os.getpid(), signal.SIGINT)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Press())\n",
        encoding="utf-8",
    )
    env = {**_child_env(), "PYTHONPATH": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "download_media", "-y", _NOWHERE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
        timeout=60,
        start_new_session=True,
    )
    assert proc.returncode == -signal.SIGINT  # Holds already: Python's own SIGINT exit.
    assert b"Traceback" not in proc.stderr


def test_ctrl_c_during_the_retry_without_cookies_is_a_download_ctrl_c(
    monkeypatch, tmp_path, capsys
) -> None:
    from yt_dlp.utils import DownloadError

    from download_media import cli
    from download_media.i18n import t

    calls: list[str | None] = []

    def download(url, opts):
        calls.append(opts.cookies_browser)
        if len(calls) == 1:
            raise DownloadError("ERROR: [fake] x: This post is private")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "download", download)
    code = cli.main(["-y", "--cookies", "firefox", "-o", str(tmp_path), "http://x"])
    output = capsys.readouterr().out
    assert code == cli.EXIT_INTERRUPTED
    assert calls == ["firefox", None]
    assert t("cookies_retry", "firefox") in output
    assert t("interrupted", "http://x", tmp_path) in output.splitlines()
    assert t("cancelled") not in output
    assert f"download-media -y -o {tmp_path} --cookies firefox http://x" in output
