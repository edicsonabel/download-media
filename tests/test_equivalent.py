"""Tests for the equivalent-command builder."""

from __future__ import annotations

from pathlib import Path

from download_media.config import DownloadOptions
from download_media.equivalent import build


def test_minimal_video_emits_only_url() -> None:
    cmd = build(DownloadOptions(urls=["https://example.com/v"]))
    assert cmd == "download-media https://example.com/v"


def test_audio_only_with_default_format() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], audio_only=True))
    assert cmd == "download-media -a https://x"


def test_non_default_quality_emitted() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], quality="720"))
    assert "-q 720" in cmd


def test_non_default_audio_format() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], audio_only=True, audio_format="opus"))
    assert "-a" in cmd
    assert "-f opus" in cmd


def test_video_format_when_not_default() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], video_format="mkv"))
    assert "--video-format mkv" in cmd


def test_cookies_emitted() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], cookies_browser="firefox"))
    assert "--cookies firefox" in cmd


def test_subs_short_form_when_default_langs() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], subs=True))
    assert " -s" in cmd
    assert "--sub-langs" not in cmd


def test_subs_with_custom_langs_uses_long_form() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], subs=True, sub_langs="fr,de"))
    assert "--sub-langs fr,de" in cmd
    assert " -s " not in cmd


def test_playlist_modes_emit_short_flags() -> None:
    yes = build(DownloadOptions(urls=["https://x"], playlist_mode="yes"))
    no = build(DownloadOptions(urls=["https://x"], playlist_mode="no"))
    assert " -p " in yes or yes.endswith(" -p")
    assert " -P " in no or " -P " in no + " "


def test_urls_are_quoted_when_special_chars_present() -> None:
    cmd = build(DownloadOptions(urls=["https://x?v=1&t=2"]))
    # shlex.quote wraps URL in single quotes due to ? and &
    assert "'https://x?v=1&t=2'" in cmd


def test_custom_output_path_emitted() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], output=Path("/tmp/abc")))
    assert "-o" in cmd
    assert "/tmp/abc" in cmd
