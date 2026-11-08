"""Tests for the yt-dlp options builder.

We don't hit the network here — we only assert the dict we hand to yt-dlp is
what we mean to hand to it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from yt_dlp.networking.exceptions import IncompleteRead

from download_media.config import DownloadOptions
from download_media.downloader import _WholeBody, build_ydl_opts
from download_media.i18n import t


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


@pytest.mark.parametrize("audio_format", ["mp3", "m4a", "opus", "wav"])
def test_audio_asks_for_a_cover_only_where_one_can_go(audio_format: str) -> None:
    opts = DownloadOptions(urls=["http://x"], audio_only=True, audio_format=audio_format)
    ydl = build_ydl_opts(opts, Path("/tmp/out"))
    assert ydl["writethumbnail"] is (audio_format != "wav")
    # download() adds the cover step, which never fails the item.
    assert "EmbedThumbnail" not in [pp["key"] for pp in ydl["postprocessors"]]
    # Nothing would embed or delete a list's own thumbnail.
    assert ydl["allow_playlist_files"] is False


def test_video_asks_for_no_thumbnail() -> None:
    ydl = build_ydl_opts(DownloadOptions(urls=["http://x"]), Path("/tmp/out"))
    assert "writethumbnail" not in ydl


def test_playlist_mode_changes_output_template() -> None:
    opts = DownloadOptions(urls=["http://x"], playlist_mode="yes", playlist_items="1-3,5")
    ydl = build_ydl_opts(opts, Path("/tmp/out"))

    assert ydl["noplaylist"] is False
    assert "playlist_index" in ydl["outtmpl"]
    assert ydl["playlist_items"] == "1-3,5"


def test_playlist_template_leaves_out_a_missing_index() -> None:
    opts = DownloadOptions(urls=["http://x"], playlist_mode="yes")
    tmpl = build_ydl_opts(opts, Path("/tmp/out"))["outtmpl"]

    assert "%(playlist_index&{:02d} - |)s" in tmpl
    assert "%(_name_suffix|)s" in tmpl
    assert "%(playlist_index)02d" not in tmpl


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
    assert ydl["writeautomaticsub"] is True
    assert ydl["subtitleslangs"] == ["en", "fr", "es"]
    # A yt-dlp command-line option the library never reads.
    assert "embedsubtitles" not in ydl


@pytest.mark.parametrize("audio_only", [False, True])
def test_subs_leave_the_postprocessors_alone(audio_only: bool) -> None:
    # The embed step is added in download(); a postprocessor set here was overwritten.
    plain = DownloadOptions(urls=["http://x"], audio_only=audio_only)
    subs = DownloadOptions(urls=["http://x"], audio_only=audio_only, subs=True)
    expected = build_ydl_opts(plain, Path("/tmp/out"))["postprocessors"]
    assert build_ydl_opts(subs, Path("/tmp/out"))["postprocessors"] == expected


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


def test_carousel_items_always_get_their_id(tmp_path: Path) -> None:
    from yt_dlp import YoutubeDL

    from download_media import downloader

    pp = downloader._AvoidNameClash(None)
    pp.set_downloader(YoutubeDL({"outtmpl": str(tmp_path / "%(title)s.%(ext)s"), "quiet": True}))
    info = {"id": "A", "title": "t", "ext": "mp4", "playlist_index": 3}
    assert pp.run(info)[1]["_name_suffix"] == " [A]"


def _filtered(keep_images: bool = True, **info) -> tuple[str | None, list[str]]:
    from download_media.downloader import _skip_items_without_media

    skipped: list[str] = []
    match_filter = _skip_items_without_media(skipped, keep_images=keep_images)
    return match_filter(info, incomplete=True), skipped


IMAGE_THUMBS = [{"url": "http://x/img.jpg"}]


@pytest.mark.parametrize("keep_images", [True, False])
def test_filter_skips_playlist_entry_without_media(keep_images: bool) -> None:
    reason, skipped = _filtered(keep_images, id="a", playlist_index=1, formats=[])
    assert reason == t("item_no_media", "a")
    assert skipped == ["a"]


@pytest.mark.parametrize("extra", [{"thumbnails": IMAGE_THUMBS}, {"thumbnail": "http://x/i.jpg"}])
def test_filter_passes_an_image_when_images_are_kept(extra: dict[str, Any]) -> None:
    assert _filtered(True, id="a", playlist_index=1, formats=[], **extra) == (None, [])


@pytest.mark.parametrize("extra", [{"thumbnails": IMAGE_THUMBS}, {"thumbnail": "http://x/i.jpg"}])
def test_filter_skips_an_image_as_an_image_in_audio_and_list_modes(extra: dict[str, Any]) -> None:
    reason, skipped = _filtered(False, id="a", playlist_index=1, formats=[], **extra)
    assert reason == t("item_image_skipped", "a")
    assert skipped == ["a"]


@pytest.mark.parametrize("keep_images", [True, False])
def test_filter_passes_playlist_metadata(keep_images: bool) -> None:
    assert _filtered(keep_images, playlist="Post", playlist_id="p") == (None, [])


@pytest.mark.parametrize("keep_images", [True, False])
def test_filter_passes_lone_urls(keep_images: bool) -> None:
    assert _filtered(keep_images, id="a", formats=[]) == (None, [])
    assert _filtered(keep_images, id="a", formats=[], thumbnails=IMAGE_THUMBS) == (None, [])


@pytest.mark.parametrize("keep_images", [True, False])
def test_filter_passes_entries_with_media(keep_images: bool) -> None:
    media = {"id": "a", "playlist_index": 1, "thumbnails": IMAGE_THUMBS}
    assert _filtered(keep_images, **media, formats=[{"url": "u"}]) == (None, [])
    assert _filtered(keep_images, **media, url="u") == (None, [])


def test_filter_passes_entries_resolved_later() -> None:
    assert _filtered(id="a", playlist_index=1, _type="url") == (None, [])
    assert _filtered(False, id="a", playlist_index=1, _type="url", thumbnails=IMAGE_THUMBS) == (
        None,
        [],
    )


def test_filter_ignores_complete_entries() -> None:
    from download_media.downloader import _skip_items_without_media

    skipped: list[str] = []
    match_filter = _skip_items_without_media(skipped, keep_images=False)
    assert match_filter({"id": "a", "playlist_index": 1}, incomplete=False) is None
    image = {"id": "a", "playlist_index": 1, "formats": [], "thumbnails": IMAGE_THUMBS}
    assert match_filter(image, incomplete=False) is None
    assert skipped == []


def test_result_total() -> None:
    from download_media.downloader import DownloadResult

    assert DownloadResult(saved=1, skipped=2, failed=3).total == 6


@pytest.mark.parametrize("audio_only", [False, True])
def test_metadata_step_is_added_in_download_not_in_the_options(audio_only: bool) -> None:
    # download() adds a metadata step that tells an untagged file from a broken one.
    opts = DownloadOptions(urls=["http://x"], audio_only=audio_only)
    keys = [pp["key"] for pp in build_ydl_opts(opts, Path("/tmp/out"))["postprocessors"]]
    assert "FFmpegMetadata" not in keys


def fake_ffprobe(folder: Path, script: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put an ``ffprobe`` running ``script`` first and alone on PATH."""
    folder.mkdir()
    ffprobe = folder / "ffprobe"
    ffprobe.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("echo video", True),
        ("echo audio", True),
        ("printf 'data\\nvideo\\n'", True),
        # A file holding no audio or video doesn't count as saved.
        ("printf 'subtitle\\ndata\\nattachment\\n'", False),
        ("true", False),
        # ffprobe failed: its output can't be trusted.
        ("echo video; exit 1", False),
    ],
)
def test_media_streams_by_what_ffprobe_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, expected: bool
) -> None:
    from download_media.downloader import _has_media_streams

    fake_ffprobe(tmp_path / "bin", script, monkeypatch)
    assert _has_media_streams(str(tmp_path / "clip.ogx")) is expected


def test_no_ffprobe_means_no_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from download_media.downloader import _has_media_streams

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert _has_media_streams(str(tmp_path / "clip.ogx")) is False


def test_a_hung_ffprobe_is_given_up_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from download_media.downloader import _has_media_streams

    fake_ffprobe(tmp_path / "bin", "exec sleep 30", monkeypatch)
    real_run = subprocess.run

    def short_run(*args: Any, **kwargs: Any) -> Any:
        assert kwargs.get("timeout"), "without a timeout, a hung ffprobe hangs the download"
        return real_run(*args, **{**kwargs, "timeout": 0.5})

    monkeypatch.setattr(subprocess, "run", short_run)
    assert _has_media_streams(str(tmp_path / "clip.ogx")) is False


def test_a_failed_subtitle_embed_leaves_a_temp_file_it_did_not_write(
    tmp_path: Path, media_url: str
) -> None:
    # Driven directly: in a full run yt-dlp's metadata step goes first and takes the same name.
    from urllib.request import urlopen

    from yt_dlp import YoutubeDL

    from download_media.downloader import _EmbedSubtitles

    video, track = tmp_path / "Clip.mp4", tmp_path / "Clip.en.ttml"
    for path, name in ((video, "tiny.mp4"), (track, "en.ttml")):
        with urlopen(f"{media_url}/{name}") as body:
            path.write_bytes(body.read())
    (tmp_path / "Clip.temp.mp4").write_text("mine", encoding="utf-8")
    info = {
        "filepath": str(video),
        "ext": "mp4",
        "requested_subtitles": {"en": {"ext": "ttml", "filepath": str(track)}},
    }
    not_embedded: list[str] = []
    with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        _EmbedSubtitles(ydl, not_embedded).run(info)
    # ffmpeg can't read the TTML track, so it fails before opening its temp file.
    assert not_embedded == ["Clip.mp4"]
    assert (tmp_path / "Clip.temp.mp4").read_text(encoding="utf-8") == "mine"


def test_a_failed_cover_removes_its_thumbnail_and_nothing_it_did_not_write(
    tmp_path: Path, media_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Driven directly: in a full run yt-dlp's metadata step takes the same temp name next.
    from urllib.request import urlopen

    from yt_dlp.postprocessor import embedthumbnail

    from download_media.downloader import _EmbedCover, _Ydl

    monkeypatch.setattr(embedthumbnail, "mutagen", None)  # opus needs it for the cover
    audio, thumb = tmp_path / "Clip.opus", tmp_path / "Clip.jpg"
    for path, name in ((audio, "tiny.opus"), (thumb, "tiny.jpg")):
        with urlopen(f"{media_url}/{name}") as body:
            path.write_bytes(body.read())
    own = [tmp_path / "Clip.temp.opus", tmp_path / "Clip.png"]
    for path in own:
        path.write_text("mine", encoding="utf-8")
    info = {"filepath": str(audio), "ext": "opus", "thumbnails": [{"filepath": str(thumb)}]}
    with _Ydl({"quiet": True, "no_warnings": True}) as ydl:
        ydl.item_thumbs = [str(thumb)]
        assert _EmbedCover(ydl).run(info) == ([], info)
        assert ydl.item_no_cover
    assert not thumb.exists()
    assert audio.exists()
    assert [path.read_text(encoding="utf-8") for path in own] == ["mine", "mine"]


def test_a_folder_that_cannot_be_created_raises_before_yt_dlp_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from download_media import downloader

    def no_ydl(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("yt-dlp started")

    monkeypatch.setattr(downloader, "_Ydl", no_ydl)
    afile = tmp_path / "afile"
    afile.write_text("keep", encoding="utf-8")
    bad = afile / "sub"
    with pytest.raises(downloader.OutputUnusable) as caught:
        downloader.download("http://x", DownloadOptions(urls=["http://x"], output=bad))
    assert caught.value.path == bad
    # The OS text only: the message prints the path itself.
    assert caught.value.reason == "Not a directory"
    assert isinstance(caught.value.__cause__, NotADirectoryError)
    assert afile.read_text(encoding="utf-8") == "keep"


class _Body:
    """A response that hands out its body in the slices given, then nothing."""

    def __init__(self, slices: list[bytes], headers: dict[str, str]) -> None:
        self.slices = list(slices)
        self.headers = headers
        self.status = 200

    def get_header(self, name: str) -> str | None:
        return self.headers.get(name)

    def read(self, amt: int | None = None) -> bytes:
        if amt is None or amt < 0:
            data, self.slices = b"".join(self.slices), []
            return data
        return self.slices.pop(0) if self.slices else b""


def _drain(body: Any, amt: int | None) -> bytes:
    if amt is None:
        return bytes(body.read())
    data = b""
    while chunk := body.read(amt):
        data += chunk
    return data


@pytest.mark.parametrize("amt", [None, 4], ids=["read()", "read(n)"])
def test_an_image_body_cut_short_of_its_length_fails(amt: int | None) -> None:
    body = _WholeBody(_Body([b"ab", b"cd"], {"Content-Length": "10"}))
    with pytest.raises(IncompleteRead):
        _drain(body, amt)


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": "4"},
        {},
        {"Content-Length": "not a number"},
        {"Content-Length": "10", "Content-Encoding": "gzip"},
        {"Content-Length": "10", "Content-Encoding": " GZIP "},
    ],
    ids=["exact", "no-length", "bad-length", "gzip", "gzip-spelled-oddly"],
)
@pytest.mark.parametrize("amt", [None, 4], ids=["read()", "read(n)"])
def test_an_image_body_with_nothing_to_hold_it_to_reads_whole(
    headers: dict[str, str], amt: int | None
) -> None:
    # A read(n) may return less than n before the end: that's no reason to fail.
    body = _WholeBody(_Body([b"a", b"bcd"], headers))
    assert _drain(body, amt) == b"abcd"
    assert body.status == 200


def test_an_image_body_spelled_identity_is_still_held_to_its_length() -> None:
    body = _WholeBody(_Body([b"ab"], {"Content-Length": "3", "Content-Encoding": " Identity"}))
    with pytest.raises(IncompleteRead):
        _drain(body, 4)
