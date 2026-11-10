"""Tests for wizard activation logic and intent-flag detection."""

from __future__ import annotations

import pytest

from download_media import cli
from download_media.cli import build_parser, has_intent_flags, should_run_wizard


def test_intent_flags_detected() -> None:
    assert has_intent_flags(["-a", "url"])
    assert has_intent_flags(["-q", "720", "url"])
    assert has_intent_flags(["--cookies", "firefox", "url"])
    assert has_intent_flags(["--quality=720", "url"])
    assert has_intent_flags(["--video-format", "mkv", "url"])


def test_no_intent_flags_when_only_url() -> None:
    assert not has_intent_flags(["url"])
    assert not has_intent_flags([])
    assert not has_intent_flags(["-i"])
    assert not has_intent_flags(["-y"])


@pytest.mark.parametrize("flag", ["-as", "-sa", "-q720", "-ao/tmp/x", "-fopus"])
def test_bundled_and_attached_intent_flags_are_detected(flag: str) -> None:
    assert has_intent_flags([flag, "u"])


@pytest.mark.parametrize(
    "flags",
    [
        ["--aud"],
        ["--qual", "720"],
        ["--qual=720"],
        ["--sub-l", "en"],
        ["--no-p"],
        ["--playlist-i", "1"],
        ["--video-f", "mkv"],
        ["--cook", "firefox"],
        ["--out", "x"],
    ],
)
def test_abbreviated_intent_flags_are_detected(flags: list[str]) -> None:
    assert has_intent_flags([*flags, "u"])


@pytest.mark.parametrize("flag", ["-l", "--list", "--li"])
def test_list_is_an_intent_flag(flag: str) -> None:
    assert has_intent_flags([flag, "u"])
    assert not should_run_wizard(_parse([flag, "u"]), [flag, "u"])
    assert should_run_wizard(_parse([flag]), [flag])


def test_tokens_after_double_dash_are_urls_not_flags() -> None:
    assert not has_intent_flags(["--", "-a"])
    assert not has_intent_flags(["--", "--cookies"])


@pytest.mark.parametrize("flags", [["-q", "1080"], ["-f", "mp3"], ["--video-format", "mp4"]])
def test_a_default_value_given_explicitly_still_counts(flags: list[str]) -> None:
    assert has_intent_flags([*flags, "u"])


@pytest.mark.parametrize("flag", ["-i", "--inter", "-y", "-iy"])
def test_non_intent_flags_do_not_count(flag: str) -> None:
    assert not has_intent_flags([flag, "u"])


def test_every_option_is_an_intent_flag_or_named_as_not_one() -> None:
    not_intent = {"help", "version", "urls", "interactive", "no_confirm"}
    for action in build_parser()._actions:
        assert action.dest in cli._INTENT_DESTS or action.dest in not_intent, action.dest


def _parse(argv):
    return build_parser().parse_args(argv)


# Every spelling argparse accepts, attacked (spec 22).
_VALUES = {
    "quality": "720",
    "audio_format": "opus",
    "video_format": "mkv",
    "output": "x",
    "playlist_items": "1",
    "sub_langs": "en",
    "cookies_browser": "firefox",
}
_LONG = [s for a in build_parser()._actions for s in a.option_strings if s.startswith("--")]
# The intent list in AGENTS.md, by dest: written out so a dest dropped from cli is noticed.
_SPEC_INTENT = (
    "audio", "quality", "audio_format", "video_format", "output", "list_formats",
    "playlist", "no_playlist", "playlist_items", "subs", "sub_langs", "cookies_browser",
)  # fmt: skip


def _spellings(dest: str) -> list[list[str]]:
    """``dest``'s option, as typed: short, attached, long, ``=``, and every unique prefix."""
    action = next(a for a in build_parser()._actions if a.dest == dest)
    value = _VALUES.get(dest)
    forms: list[list[str]] = []
    for option in action.option_strings:
        if not option.startswith("--"):
            forms += [[option, value], [option + value]] if value else [[option]]
            continue
        for end in range(3, len(option) + 1):
            prefix = option[:end]
            if prefix != option and sum(s.startswith(prefix) for s in _LONG) > 1:
                continue
            forms += [[prefix, value], [f"{prefix}={value}"]] if value else [[prefix]]
    return forms


_INTENT_SPELLINGS = [(dest, form) for dest in sorted(_SPEC_INTENT) for form in _spellings(dest)]


@pytest.mark.parametrize(("dest", "form"), _INTENT_SPELLINGS, ids=str)
def test_every_spelling_of_every_intent_flag_skips_the_wizard(dest: str, form: list[str]) -> None:
    for argv in ([*form, "u"], ["u", *form]):
        args = _parse(argv)
        assert getattr(args, dest) != getattr(_parse(["u"]), dest), argv  # It parsed as dest.
        assert has_intent_flags(argv), argv
        assert not should_run_wizard(args, argv), argv
        assert should_run_wizard(_parse(["-i", *argv]), ["-i", *argv]), argv


@pytest.mark.parametrize(("dest", "form"), _INTENT_SPELLINGS, ids=str)
def test_no_spelling_of_an_intent_flag_counts_after_double_dash(dest: str, form: list[str]) -> None:
    # After "--" every token is a URL, so it's the wizard on those URLs.
    argv = ["--", *form]
    assert _parse(argv).urls == form
    assert not has_intent_flags(argv)
    assert should_run_wizard(_parse(argv), argv)


def test_the_spelling_table_covers_every_intent_flag() -> None:
    assert set(_SPEC_INTENT) == set(cli._INTENT_DESTS)
    assert {dest for dest, _ in _INTENT_SPELLINGS} == set(_SPEC_INTENT)
    assert ["--qual", "720"] in [form for _, form in _INTENT_SPELLINGS]
    assert ["--no-p"] in [form for _, form in _INTENT_SPELLINGS]
    assert ["-q720"] in [form for _, form in _INTENT_SPELLINGS]


@pytest.mark.parametrize("dest", ["interactive", "no_confirm"])
def test_no_spelling_of_a_non_intent_flag_counts(dest: str) -> None:
    for form in _spellings(dest):
        assert not has_intent_flags([*form, "u"]), form


@pytest.mark.parametrize(
    "bundle", ["-as", "-sa", "-asl", "-lsa", "-ao/tmp/x", "-sq720", "-afopus", "-pa", "-Ps"]
)
def test_bundles_of_intent_flags_skip_the_wizard(bundle: str) -> None:
    assert not should_run_wizard(_parse([bundle, "u"]), [bundle, "u"])


@pytest.mark.parametrize("bundle", ["-ia", "-ai", "-iq720", "-il", "-sia"])
def test_interactive_in_a_bundle_still_opens_the_wizard(bundle: str) -> None:
    args = _parse([bundle, "u"])
    assert has_intent_flags([bundle, "u"])
    assert should_run_wizard(args, [bundle, "u"])


@pytest.mark.parametrize("bundle", ["-ya", "-ay", "-ays", "-yl", "-iy", "-yi", "-yia"])
def test_yes_in_a_bundle_never_opens_the_wizard(bundle: str) -> None:
    assert not should_run_wizard(_parse([bundle, "u"]), [bundle, "u"])
    assert not should_run_wizard(_parse([bundle]), [bundle])


def test_bundled_flags_preset_the_options_the_wizard_starts_from() -> None:
    opts = cli.options_from_args(_parse(["-iasq720", "u"]))
    assert (opts.audio_only, opts.subs, opts.quality) == (True, True, "720")


@pytest.mark.parametrize("flags", [["--quality", "1080"], ["-q1080"], ["-fmp3"], ["--video-f=mp4"]])
def test_a_default_value_in_any_spelling_still_counts(flags: list[str]) -> None:
    argv = [*flags, "u"]
    assert not should_run_wizard(_parse(argv), argv)


@pytest.mark.parametrize("argv", [["--play", "u"], ["--p", "u"], ["--sub", "u"], ["--v", "u"]])
def test_an_ambiguous_prefix_is_still_a_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # The main parse is unchanged: it rejects these before anything asks or downloads.
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "ambiguous option" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["-x"], ["-a", "--bogus", "u"], ["-q", "999", "u"]])
def test_a_bad_command_line_fails_before_the_intent_check(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_check(argv: list[str]) -> bool:
        raise AssertionError("the intent check ran")

    monkeypatch.setattr(cli, "has_intent_flags", no_check)
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


def test_a_double_dash_url_after_an_intent_flag_is_direct_mode() -> None:
    argv = ["-a", "--", "-x"]
    assert _parse(argv).urls == ["-x"]
    assert not should_run_wizard(_parse(argv), argv)


def test_a_url_that_looks_like_a_flag_needs_double_dash() -> None:
    assert should_run_wizard(_parse(["--", "-a"]), ["--", "-a"])
    assert _parse(["--", "-a"]).urls == ["-a"]
    with pytest.raises(SystemExit):
        _parse(["-abc"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--cookies", "Brave+GnomeKeyring", "-q720", "u"],
        ["-ao/tmp/x", "--sub-langs=-q", "u", "v"],
        ["-l", "--playlist-items", "1-3", "u"],
        [],
    ],
)
def test_the_intent_check_has_no_side_effects(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    before = vars(_parse(argv))
    has_intent_flags(argv)
    should_run_wizard(_parse(argv), argv)
    assert vars(_parse(argv)) == before
    assert capsys.readouterr() == ("", "")


def test_the_intent_check_reads_the_tokens_not_the_parsed_values() -> None:
    # The values -a set are the same either way; only the tokens say it was typed.
    assert not should_run_wizard(_parse(["-a", "u"]), ["-a", "u"])
    assert should_run_wizard(_parse(["-a", "u"]), ["u"])


def test_wizard_runs_with_no_args() -> None:
    assert should_run_wizard(_parse([]), [])


def test_wizard_runs_with_url_only() -> None:
    assert should_run_wizard(_parse(["http://x"]), ["http://x"])


def test_wizard_skipped_with_intent_flag() -> None:
    args = _parse(["-a", "http://x"])
    assert not should_run_wizard(args, ["-a", "http://x"])


def test_wizard_skipped_with_yes() -> None:
    args = _parse(["-y", "http://x"])
    assert not should_run_wizard(args, ["-y", "http://x"])


def test_wizard_forced_with_interactive_even_with_intent_flag() -> None:
    args = _parse(["-i", "-a", "http://x"])
    assert should_run_wizard(args, ["-i", "-a", "http://x"])


def test_yes_overrides_interactive() -> None:
    args = _parse(["-i", "-y", "http://x"])
    assert not should_run_wizard(args, ["-i", "-y", "http://x"])


def test_cookies_step_skipped_once_already_asked() -> None:
    from download_media.probe import ContentKind, Probe
    from download_media.wizard import _build_post_probe_plan

    video = Probe(url="http://x", site="instagram", content_kind=ContentKind.SINGLE_VIDEO)
    assert "cookies" in _build_post_probe_plan(video, cookies_asked=False)
    assert "cookies" not in _build_post_probe_plan(video, cookies_asked=True)
