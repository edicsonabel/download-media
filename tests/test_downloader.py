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
    assert ydl["format"] == "bv*[height<=?1080]+ba/b[height<=?1080]/b/best"
    assert ydl["format_sort"] == ["vcodec:h264", "acodec:aac"]
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


def test_non_mp4_container_keeps_default_codec_order() -> None:
    opts = DownloadOptions(urls=["http://x"], video_format="mkv")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert "format_sort" not in ydl


def test_audio_only_skips_h264_preference() -> None:
    opts = DownloadOptions(urls=["http://x"], audio_only=True)
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert "format_sort" not in ydl


def _clash(tmp_path: Path, monkeypatch, *, exists: bool, stored_url: str | None, final_ext=None):
    from yt_dlp import YoutubeDL

    from download_media import downloader

    target = tmp_path / "Video by someone.mp4"
    if exists:
        (target.with_suffix(f".{final_ext}") if final_ext else target).write_bytes(b"")
    monkeypatch.setattr(downloader, "_source_url", lambda path: stored_url)
    ydl = YoutubeDL(
        {"outtmpl": str(tmp_path / "%(title)s%(_name_suffix|)s.%(ext)s"), "quiet": True}
    )
    pp = downloader._AvoidNameClash(final_ext)
    pp.set_downloader(ydl)
    info = {
        "id": "NEW",
        "title": "Video by someone",
        "ext": "mp4",
        "webpage_url": "https://site/p/NEW/",
    }
    _, info = pp.run(info)
    return info.get("_name_suffix")


def test_name_clash_with_other_video_appends_id(tmp_path: Path, monkeypatch) -> None:
    assert _clash(tmp_path, monkeypatch, exists=True, stored_url="https://site/p/OLD/") == " [NEW]"


def test_name_clash_with_untagged_file_appends_id(tmp_path: Path, monkeypatch) -> None:
    assert _clash(tmp_path, monkeypatch, exists=True, stored_url=None) == " [NEW]"


def test_same_video_keeps_name_so_yt_dlp_skips_it(tmp_path: Path, monkeypatch) -> None:
    assert _clash(tmp_path, monkeypatch, exists=True, stored_url="https://site/p/NEW/") is None


def test_no_existing_file_keeps_name(tmp_path: Path, monkeypatch) -> None:
    assert _clash(tmp_path, monkeypatch, exists=False, stored_url=None) is None


def test_audio_checks_final_extension(tmp_path: Path, monkeypatch) -> None:
    got = _clash(tmp_path, monkeypatch, exists=True, stored_url=None, final_ext="mp3")
    assert got == " [NEW]"


def test_single_video_template_has_optional_suffix() -> None:
    ydl = build_ydl_opts(DownloadOptions(urls=["http://x"]), Path("/tmp/out"))
    assert ydl["outtmpl"].endswith("%(title)s%(_name_suffix|)s.%(ext)s")
