"""Tests for wizard activation logic and intent-flag detection."""

from __future__ import annotations

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
