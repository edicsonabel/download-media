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
