"""Thin wrapper around :class:`yt_dlp.YoutubeDL`.

We translate :class:`~download_media.config.DownloadOptions` into the dict of
flags yt-dlp expects, then drive the download. No shelling out — yt-dlp is
imported as a library so we get exceptions, typed entries, and direct access
to extracted metadata.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegEmbedSubtitlePP, FFmpegMetadataPP
from yt_dlp.utils import DownloadError, PostProcessingError, prepend_extension, replace_extension

from .config import DownloadOptions
from .cookies import ydl_cookies
from .i18n import t
from .selection import missing_items


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """What one URL produced; multi-entry URLs can succeed only in part."""

    saved: int = 0
    skipped: int = 0
    failed: int = 0
    # --playlist-items segments that select nothing; not counted in total.
    missing: tuple[str, ...] = ()
    playlist_count: int | None = None
    # Subtitles were asked for, and no saved item got a subtitle file.
    subs_missing: bool = False
    # Files holding fewer subtitle streams than subtitle files saved for them.
    subs_not_embedded: tuple[str, ...] = ()
    # Files saved whole that ffmpeg couldn't write tags to.
    untagged: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.saved + self.skipped + self.failed


class _Ydl(YoutubeDL):  # type: ignore[misc]
    """Keeps the errors that ``ignoreerrors`` would otherwise only print."""

    count_entries = False
    # len(errors) when the current item started.
    item_errors = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.errors: list[str] = []
        super().__init__(*args, **kwargs)

    def process_info(self, info_dict: Any) -> Any:
        self.item_errors = len(self.errors)
        return super().process_info(info_dict)

    def item_failed(self) -> bool:
        # ignoreerrors reports a failed post-processing step, then runs the post hooks anyway.
        return len(self.errors) > self.item_errors

    def report_error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(message))
        super().report_error(message, *args, **kwargs)

    def process_ie_result(self, ie_result: Any, *args: Any, **kwargs: Any) -> Any:
        if self.count_entries and ie_result.get("_type") in ("playlist", "multi_video"):
            # yt-dlp keeps an extractor's declared count over the entries it read, and YouTube
            # declares the header's "N videos", hidden ones included. Without it, yt-dlp counts
            # the list once it reads to the end, and leaves the count unset otherwise.
            ie_result.pop("playlist_count", None)
        return super().process_ie_result(ie_result, *args, **kwargs)


def build_ydl_opts(opts: DownloadOptions, output_dir: Path) -> dict[str, Any]:
    """Translate our high-level options into a yt-dlp options dict."""
    ydl: dict[str, Any] = {
        "no_warnings": True,
        "noprogress": False,
    }

    is_playlist = opts.playlist_mode == "yes"
    if is_playlist:
        # Carousel items get an index prefix and an ID suffix (_AvoidNameClash) so
        # they can't collide; a lone video has no index and keeps its plain name.
        ydl["outtmpl"] = str(
            output_dir / "%(playlist_index&{:02d} - |)s%(title)s%(_name_suffix|)s.%(ext)s"
        )
        ydl["noplaylist"] = False
        if opts.playlist_items:
            ydl["playlist_items"] = opts.playlist_items
        # Carousels often mix media types — one un-extractable item shouldn't
        # abort the rest of the batch.
        ydl["ignoreerrors"] = True
    else:
        # The suffix is only filled in by _AvoidNameClash when needed.
        ydl["outtmpl"] = str(output_dir / "%(title)s%(_name_suffix|)s.%(ext)s")
        ydl["noplaylist"] = True

    if opts.cookies_browser:
        ydl["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)

    if opts.subs:
        ydl.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [s.strip() for s in opts.sub_langs.split(",") if s.strip()],
            }
        )

    if opts.audio_only:
        ydl.update(
            {
                "format": "bestaudio/best",
                "writethumbnail": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": opts.audio_format,
                        "preferredquality": "0",
                    },
                    {"key": "EmbedThumbnail"},
                ],
            }
        )
    else:
        # Format selector falls back to "best" so image entries (e.g. items in
        # an Instagram carousel) still download — they have no video stream.
        if opts.quality == "best":
            fmt = "bv*+ba/b/best"
        else:
            q = opts.quality
            # `<=?` keeps formats with unknown height — Instagram's progressive
            # H.264 files don't report one, and the filter would drop them.
            fmt = f"bv*[height<=?{q}]+ba/b[height<=?{q}]/b/best"
        if opts.video_format == "mp4":
            # mp4 means "plays everywhere": WhatsApp, iOS and most TVs reject
            # VP9/AV1 even inside an mp4 container.
            ydl["format_sort"] = ["vcodec:h264", "acodec:aac"]
        ydl.update(
            {
                "format": fmt,
                "merge_output_format": opts.video_format,
                # download() adds the metadata step (_TagSource).
                "postprocessors": [],
            }
        )

    return ydl


def _source_url(path: Path) -> str | None:
    """URL that FFmpegMetadata stored in a file we downloaded earlier, if any."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=purl,comment"]
        cmd += ["-of", "default=nw=1:nk=1", str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line for line in out.splitlines() if "://" in line), None)


class _AvoidNameClash(PostProcessor):  # type: ignore[misc]
    """Append ``[id]`` to the filename when another video already took the name.

    Titles aren't unique (every Instagram post by a user is "Video by <user>"),
    and yt-dlp treats an existing file as "already downloaded" — silently
    skipping the new video and re-tagging the old file with its metadata.
    """

    def __init__(self, final_ext: str | None) -> None:
        super().__init__()
        # Audio extraction changes the extension after download.
        self._final_ext = final_ext

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        # Carousel items share both title and webpage_url, so the check below
        # can't tell them apart.
        if info.get("playlist_index"):
            info["_name_suffix"] = f" [{info.get('id')}]"
            return [], info
        path = Path(self._downloader.prepare_filename(info))
        if self._final_ext:
            path = path.with_suffix(f".{self._final_ext}")
        if path.exists() and _source_url(path) != info.get("webpage_url"):
            info["_name_suffix"] = f" [{info.get('id')}]"
        return [], info


class _PlaylistCount(PostProcessor):  # type: ignore[misc]
    """Record each list's size as yt-dlp read it (see ``_Ydl``); the last call is the outer list."""

    def __init__(self, counts: list[int | None]) -> None:
        super().__init__()
        self._counts = counts

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        self._counts.append(info.get("playlist_count"))
        return [], info


def _subtitle_files(info: dict[str, Any]) -> list[str]:
    """Subtitle files yt-dlp wrote, or found already written, for this item."""
    tracks = (info.get("requested_subtitles") or {}).values()
    paths = (track.get("filepath") for track in tracks)
    return [path for path in paths if path and Path(path).is_file()]


def _subtitle_streams(path: str) -> int | None:
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index"]
        cmd += ["-of", "csv=p=0", path]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return len(done.stdout.split()) if done.returncode == 0 else None


class _SubtitlesSeen(PostProcessor):  # type: ignore[misc]
    """Record whether an item that reached post-processing got any subtitle file."""

    def __init__(self, seen: list[bool]) -> None:
        super().__init__()
        self._seen = seen

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        if _subtitle_files(info):
            self._seen.append(True)
        return [], info


class _EmbedSubtitles(FFmpegEmbedSubtitlePP):  # type: ignore[misc]
    """Embed the subtitle files in the video, keep them, and never fail the item over it.

    yt-dlp's step raises on any ffmpeg error (a TTML track, say), which would report a
    video already on disk as a failed download.
    """

    def __init__(self, downloader: Any, not_embedded: list[str]) -> None:
        super().__init__(downloader, already_have_subtitle=True)
        self._not_embedded = not_embedded

    @classmethod
    def pp_key(cls) -> str:
        # yt-dlp derives it from the class name; keep the [EmbedSubtitle] tag it prints.
        return "EmbedSubtitle"

    def run_ffmpeg_multiple_files(
        self, input_paths: list[str], out_path: str, opts: list[str], **kwargs: Any
    ) -> Any:
        # ffmpeg marks the first track default, so players would show the first --sub-langs
        # language (es) unasked. mkv and webm obey this; the mp4 muxer ignores it.
        opts = [*opts, "-disposition:s", "0"]
        return super().run_ffmpeg_multiple_files(input_paths, out_path, opts, **kwargs)

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        files = _subtitle_files(info)
        try:
            super().run(info)
        except PostProcessingError:
            Path(prepend_extension(info["filepath"], "temp")).unlink(missing_ok=True)
        # Covers the failure above, containers that can't hold subtitles, srt in webm, json.
        streams = _subtitle_streams(info["filepath"]) if files else None
        if streams is not None and streams < len(files):
            self._not_embedded.append(Path(info["filepath"]).name)
        return [], info


def _has_media_streams(path: str) -> bool:
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]
        done = subprocess.run([*cmd, path], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool({"audio", "video"} & set(done.stdout.split()))


class _TagSource(FFmpegMetadataPP):  # type: ignore[misc]
    """yt-dlp's metadata step, except that a file ffmpeg can't tag still counts as saved.

    ffmpeg can read an .ogx, a .weba or a renamed file and still have no muxer for it, and
    yt-dlp would report a file already on disk as a failed download.
    """

    def __init__(self, downloader: Any, untagged: list[str]) -> None:
        super().__init__(downloader)
        self._untagged = untagged

    @classmethod
    def pp_key(cls) -> str:
        # Keeps the [Metadata] tag it prints, and its postprocessor_args key.
        return "Metadata"

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        try:
            return super().run(info)  # type: ignore[no-any-return]
        except PostProcessingError:
            # The step writes to a temp file and replaces the original only on success.
            path = info["filepath"]
            Path(prepend_extension(path, "temp")).unlink(missing_ok=True)
            Path(replace_extension(path, "meta")).unlink(missing_ok=True)  # Chapters.
            # Asking ffprobe, not reading ffmpeg's error text: that varies by version.
            if not _has_media_streams(path):
                raise
            self._untagged.append(Path(path).name)
            return [], info


def _skip_items_without_media(skipped: list[str]) -> Callable[..., str | None]:
    """``match_filter`` dropping carousel items yt-dlp has no formats for.

    Instagram images come through as such items, and yt-dlp downloads carousels
    even with ``noplaylist``. Without this the first image would abort a
    single-mode run. yt-dlp also calls this with the playlist's own metadata
    (no ``id``) and for lone URLs (no ``playlist_index``); those must pass, so a
    lone URL with no formats still fails as an error.
    """

    def match_filter(info: Any, *, incomplete: Any = False) -> str | None:
        item = info.get("id")
        if not (incomplete and item and info.get("playlist_index")):
            return None
        if info.get("_type", "video") != "video" or info.get("formats") or info.get("url"):
            return None
        skipped.append(str(item))
        return t("item_no_media", item)

    return match_filter


def download(url: str, opts: DownloadOptions) -> DownloadResult:
    """Download a single URL. Raises ``DownloadError`` if nothing could be saved."""
    output_dir = opts.resolve_output()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped: list[str] = []
    ydl_opts = build_ydl_opts(opts, output_dir)
    ydl_opts["match_filter"] = _skip_items_without_media(skipped)
    untagged: list[str] = []
    with _Ydl(ydl_opts) as ydl:

        def keep(path: str) -> None:
            if not ydl.item_failed():
                saved.append(path)

        ydl.add_post_hook(keep)
        # Appended after the params' postprocessors: audio extraction comes first.
        ydl.add_post_processor(_TagSource(ydl, untagged), when="post_process")
        final_ext = opts.audio_format if opts.audio_only else None
        ydl.add_post_processor(_AvoidNameClash(final_ext), when="video")
        counts: list[int | None] = []
        items = opts.playlist_items if opts.playlist_mode == "yes" else None
        ydl.count_entries = bool(items)
        if items:
            ydl.add_post_processor(_PlaylistCount(counts), when="playlist")
        subs_seen: list[bool] = []
        not_embedded: list[str] = []
        if opts.subs:
            # After the metadata step, so embedding keeps its source URL tag.
            ydl.add_post_processor(_SubtitlesSeen(subs_seen), when="post_process")
            if not opts.audio_only:
                ydl.add_post_processor(_EmbedSubtitles(ydl, not_embedded), when="post_process")
        returncode = int(ydl.download([url]))
    # ignoreerrors turns a total failure into a bare return code.
    if returncode != 0 and not saved:
        raise DownloadError("\n".join(ydl.errors) or t("failed", url))
    count = counts[-1] if counts else None
    # yt-dlp drops items past the end without a word; an empty list keeps its own report.
    missing = tuple(missing_items(items, count)) if items and count else ()
    return DownloadResult(
        saved=len(saved),
        skipped=len(skipped),
        failed=len(ydl.errors),
        missing=missing,
        playlist_count=count,
        subs_missing=opts.subs and not subs_seen,
        subs_not_embedded=tuple(not_embedded),
        untagged=tuple(untagged),
    )


def list_formats(url: str, opts: DownloadOptions) -> int:
    """Print available formats for a URL (equivalent to ``yt-dlp -F``)."""
    ydl_opts: dict[str, Any] = {
        "listformats": True,
        "match_filter": _skip_items_without_media([]),
    }
    if opts.cookies_browser:
        ydl_opts["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)
    with YoutubeDL(ydl_opts) as ydl:
        return int(ydl.download([url]))
