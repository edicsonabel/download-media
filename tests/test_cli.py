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
