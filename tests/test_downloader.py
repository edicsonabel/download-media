"""Tests for the yt-dlp options builder.

We don't hit the network here — we only assert the dict we hand to yt-dlp is
what we mean to hand to it.
"""

from __future__ import annotations

from pathlib import Path

from download_media.config import DownloadOptions
from download_media.downloader import build_ydl_opts


def test_default_video_options() -> None:
    opts = DownloadOptions(urls=["http://x"])
    ydl = build_ydl_opts(opts, Path("/tmp/out"))

    assert ydl["noplaylist"] is True
    assert "playlist_index" not in ydl["outtmpl"]
    assert ydl["format"] == "bv*[height<=1080]+ba/b[height<=1080]/b/best"
    assert ydl["merge_output_format"] == "mp4"
    assert "cookiesfrombrowser" not in ydl


def test_audio_only_uses_extract_postprocessor() -> None:
    opts = DownloadOptions(urls=["http://x"], audio_only=True, audio_format="opus")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))

    keys = [pp["key"] for pp in ydl["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert ydl["postprocessors"][0]["preferredcodec"] == "opus"
    assert ydl["format"] == "bestaudio/best"


def test_playlist_mode_changes_output_template() -> None:
    opts = DownloadOptions(urls=["http://x"], playlist_mode="yes", playlist_items="1-3,5")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))

    assert ydl["noplaylist"] is False
    assert "playlist_index" in ydl["outtmpl"]
    assert ydl["playlist_items"] == "1-3,5"


def test_quality_best_uses_open_format_selector() -> None:
    opts = DownloadOptions(urls=["http://x"], quality="best")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert ydl["format"] == "bv*+ba/b/best"


def test_cookies_from_browser_is_a_tuple() -> None:
    opts = DownloadOptions(urls=["http://x"], cookies_browser="brave")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert ydl["cookiesfrombrowser"] == ("brave",)


def test_subs_enables_subtitle_options() -> None:
    opts = DownloadOptions(urls=["http://x"], subs=True, sub_langs="en,fr,es")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert ydl["writesubtitles"] is True
    assert ydl["embedsubtitles"] is True
    assert ydl["subtitleslangs"] == ["en", "fr", "es"]
