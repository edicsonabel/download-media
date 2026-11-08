"""Thin wrapper around :class:`yt_dlp.YoutubeDL`.

We translate :class:`~download_media.config.DownloadOptions` into the dict of
flags yt-dlp expects, then drive the download. No shelling out — yt-dlp is
imported as a library so we get exceptions, typed entries, and direct access
to extracted metadata.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.cookies import CookieLoadError
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.embedthumbnail import EmbedThumbnailPP
from yt_dlp.postprocessor.ffmpeg import FFmpegEmbedSubtitlePP, FFmpegMetadataPP
from yt_dlp.utils import DownloadError, PostProcessingError, prepend_extension, replace_extension
from yt_dlp.utils._utils import _UnsafeExtensionError

from .config import DownloadOptions
from .cookies import CookieFailure, cookie_failure, ydl_cookies
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
    # Audio files saved without the cover image they were meant to get.
    no_cover: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.saved + self.skipped + self.failed


def _cover_name(path: str, ext: str | None) -> str:
    """The media's path with a ``.cover`` part, for yt-dlp to name a thumbnail after.

    yt-dlp swaps the media's extension for the one in the thumbnail's link, so a link ending
    in .m4a or .mp3 put the cover at the media's own path.
    """
    stem, real_ext = os.path.splitext(path)
    return f"{stem}.cover{real_ext}" if real_ext[1:] == ext else f"{path}.cover"


class _Ydl(YoutubeDL):  # type: ignore[misc]
    """Keeps the errors that ``ignoreerrors`` would otherwise only print."""

    count_entries = False
    # len(errors) when the current item started.
    item_errors = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.errors: list[str] = []
        # Thumbnail files this run wrote for the current item, and whether it lost its cover.
        self.item_thumbs: list[str] = []
        self.item_no_cover = False
        super().__init__(*args, **kwargs)

    def process_info(self, info_dict: Any) -> Any:
        self.item_errors = len(self.errors)
        self.item_thumbs, self.item_no_cover = [], False
        try:
            result = super().process_info(info_dict)
        except BaseException:
            # Without ignoreerrors, report_error raises from inside process_info.
            self.drop_thumbs()
            raise
        if self.item_failed():
            self.drop_thumbs()
        return result

    def _write_thumbnails(
        self, label: Any, info_dict: Any, filename: Any, thumb_filename_base: Any = None
    ) -> Any:
        # yt-dlp only catches network errors here; a URL with no scheme (a relative og:image),
        # or an extension it won't write (thumb.php?id=), raises before the media is fetched,
        # and would lose the download over its cover.
        ext = info_dict.get("ext")
        if filename:
            filename = _cover_name(filename, ext)
        if thumb_filename_base:
            thumb_filename_base = _cover_name(thumb_filename_base, ext)
        try:
            written = super()._write_thumbnails(label, info_dict, filename, thumb_filename_base)
        except (RequestError, _UnsafeExtensionError):
            self.item_no_cover = True
            return []
        # All of them are this run's: overwrites is unset, so yt-dlp deletes a file already at
        # the path instead of reusing it.
        self.item_thumbs += [path for path, _ in written or []]
        return written

    def drop_thumbs(self) -> None:
        for path in self.item_thumbs:
            Path(path).unlink(missing_ok=True)
        self.item_thumbs = []

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


class CookiesUnavailable(Exception):
    """The browser's cookies couldn't be loaded; nothing was requested yet."""

    def __init__(self, spec: str, failure: CookieFailure) -> None:
        super().__init__(failure.reason)
        self.spec = spec
        self.failure = failure


def _load_browser_cookies(ydl: YoutubeDL, spec: str | None) -> None:
    """Load the browser's cookies before any request, so a failure looks the same in every mode.

    yt-dlp loads them on the first request. Under ignoreerrors (-p, --playlist-items) the
    failure then escapes as a bare CookieLoadError; without it, as a DownloadError.
    """
    if not spec or not ydl.params.get("cookiesfrombrowser"):
        return
    try:
        _ = ydl.cookiejar  # Not cached on failure: every read retries the load.
    except (CookieLoadError, DownloadError) as exc:
        failure = cookie_failure(exc)
        if failure is None:
            raise
        raise CookiesUnavailable(spec, failure) from exc


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
                # yt-dlp can't embed a cover in wav.
                "writethumbnail": opts.audio_format != "wav",
                # Nothing would embed or delete a list's own thumbnail.
                "allow_playlist_files": False,
                # download() adds the cover step (_EmbedCover) and the metadata step.
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": opts.audio_format,
                        "preferredquality": "0",
                    },
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


def _probe_path(path: str) -> str:
    # A relative path starting with '-' would reach ffprobe as an option.
    return str(Path(path).absolute())


def _subtitle_streams(path: str) -> int | None:
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index"]
        cmd += ["-of", "csv=p=0", _probe_path(path)]
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
        temp = Path(prepend_extension(info["filepath"], "temp"))
        temp_was_there = temp.exists()
        try:
            super().run(info)
        except PostProcessingError:
            # ffmpeg fails before opening the temp file when it can't read a track.
            if not temp_was_there:
                temp.unlink(missing_ok=True)
        # Covers the failure above, containers that can't hold subtitles, srt in webm, json.
        streams = _subtitle_streams(info["filepath"]) if files else None
        if streams is not None and streams < len(files):
            self._not_embedded.append(Path(info["filepath"]).name)
        return [], info


def _has_media_streams(path: str) -> bool:
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]
        cmd.append(_probe_path(path))
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
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
        path = info["filepath"]
        # The step writes to a temp file and replaces the original only on success.
        temp = Path(prepend_extension(path, "temp"))
        temp_was_there = temp.exists()
        try:
            return super().run(info)  # type: ignore[no-any-return]
        except PostProcessingError:
            # Only what this step wrote: ffmpeg fails before opening the temp file when it
            # has no muxer, and yt-dlp writes the .meta file only for chapters.
            if not temp_was_there:
                temp.unlink(missing_ok=True)
            if self._add_chapters and info.get("chapters"):
                Path(replace_extension(path, "meta")).unlink(missing_ok=True)
            # Asking ffprobe, not reading ffmpeg's error text: that varies by version.
            if not _has_media_streams(path):
                raise
            self._untagged.append(Path(path).name)
            return [], info


class _EmbedCover(EmbedThumbnailPP):  # type: ignore[misc]
    """yt-dlp's cover step, except that a cover it can't embed never fails the audio.

    It raises for a body that isn't an image, a conversion ffmpeg can't do, and opus
    without mutagen. Needs ``_Ydl``, which records the thumbnails the run wrote.
    """

    @classmethod
    def pp_key(cls) -> str:
        # Keeps the [EmbedThumbnail] tag it prints, and its postprocessor_args key.
        return "EmbedThumbnail"

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        ydl = self._downloader
        temp = Path(prepend_extension(info["filepath"], "temp"))
        temp_was_there = temp.exists()
        pngs = {Path(replace_extension(path, "png")) for path in ydl.item_thumbs}
        pngs_there = {png for png in pngs if png.exists()}
        try:
            return super().run(info)  # type: ignore[no-any-return]
        except PostProcessingError:
            # Only what this step wrote: its temp file, and its conversion of our thumbnail.
            if not temp_was_there:
                temp.unlink(missing_ok=True)
            for png in pngs - pngs_there:
                png.unlink(missing_ok=True)
            # fixup_webp may have renamed our thumbnail to .webp, and recorded the new path.
            thumbs = info.get("thumbnails") or []
            ydl.item_thumbs += [thumb["filepath"] for thumb in thumbs if thumb.get("filepath")]
            ydl.drop_thumbs()
            ydl.item_no_cover = True
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
    """Download a single URL. Raises ``DownloadError`` if nothing could be saved.

    Raises ``CookiesUnavailable`` before any request if the browser's cookies can't be loaded.
    """
    output_dir = opts.resolve_output()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped: list[str] = []
    ydl_opts = build_ydl_opts(opts, output_dir)
    ydl_opts["match_filter"] = _skip_items_without_media(skipped)
    untagged: list[str] = []
    no_cover: list[str] = []
    with _Ydl(ydl_opts) as ydl:
        _load_browser_cookies(ydl, opts.cookies_browser)

        def keep(path: str) -> None:
            if not ydl.item_failed():
                saved.append(path)
                if ydl.item_no_cover:
                    no_cover.append(Path(path).name)

        ydl.add_post_hook(keep)
        # Appended after the params' postprocessors: audio extraction comes first.
        if ydl_opts.get("writethumbnail"):
            ydl.add_post_processor(_EmbedCover(ydl), when="post_process")
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
        no_cover=tuple(no_cover),
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
        _load_browser_cookies(ydl, opts.cookies_browser)
        return int(ydl.download([url]))
