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
from yt_dlp.networking.exceptions import IncompleteRead, RequestError
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.embedthumbnail import EmbedThumbnailPP
from yt_dlp.postprocessor.ffmpeg import (
    FFmpegEmbedSubtitlePP,
    FFmpegMetadataPP,
    FFmpegVideoConvertorPP,
    FFmpegVideoRemuxerPP,
    resolve_mapping,
)
from yt_dlp.utils import (
    MEDIA_EXTENSIONS,
    DownloadError,
    PostProcessingError,
    int_or_none,
    prepend_extension,
    replace_extension,
    subtitles_filename,
)
from yt_dlp.utils._utils import _UnsafeExtensionError

from .config import DownloadOptions
from .cookies import (
    CookieFailure,
    cookie_failure,
    jar_failure,
    logged_failure,
    short_reason,
    ydl_cookies,
)
from .i18n import t
from .selection import missing_items

# yt-dlp lists these as audio, yet they can hold a video it would keep. A wrong
# "audio" converts and deletes that video; a wrong "video" only keeps the raw file.
# .m4a isn't one: yt-dlp's metadata step already strips video from it, though only
# from an extension written "m4a".
_AUDIO_LISTED_BUT_MAY_HOLD_VIDEO = frozenset({"asf", "m4b", "ogg", "ogx", "wma"})


def _ext_says_audio(ext: Any) -> bool:
    if not isinstance(ext, str):
        return False
    low = ext.lower()  # yt-dlp keeps the URL's case.
    # yt-dlp's metadata step strips the video only from an extension written "m4a"
    # (target_ext == 'm4a'): an .M4A keeps it, like an .m4b.
    if low == "m4a" and ext != "m4a":
        return False
    return low in MEDIA_EXTENSIONS.audio and low not in _AUDIO_LISTED_BUT_MAY_HOLD_VIDEO


# The generic extractor leaves an image's codecs unknown, so yt-dlp's own guard lets it through.
_IMAGE_EXTS = frozenset({*MEDIA_EXTENSIONS.thumbnails, "gif", "jpeg"})


def _converts(info: dict[str, Any]) -> bool:
    """What the probe calls video: the container choice applies to it."""
    # Lowercased first, so an .M4A stays raw: converting an uppercase name doubles its
    # extension (yt-dlp's replace_extension only replaces a lowercase one).
    ext = (info.get("ext") or "").lower()
    vcodec = info.get("vcodec")
    if vcodec == "none" or ext in _IMAGE_EXTS:
        return False
    return bool(vcodec) or not _ext_says_audio(ext)


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
    # Videos kept as downloaded because ffmpeg couldn't put them in the chosen container.
    not_converted: tuple[str, ...] = ()
    # Per saved file, a subtitle language that couldn't be fetched, and yt-dlp's reason.
    subs_failed: tuple[tuple[str, str, str], ...] = ()
    # Subtitle files that hold no subtitle text; kept, never embedded.
    subs_empty: tuple[str, ...] = ()

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


def is_saveable_image(info: Any) -> bool:
    """An entry yt-dlp finds no formats for, with a thumbnail: an Instagram carousel image."""
    if info.get("_type", "video") != "video":
        return False
    # yt-dlp's _get_formats: a result with no formats list is its own single format.
    formats = info.get("formats")
    no_formats = not formats if formats is not None else not info.get("url")
    return no_formats and bool(info.get("thumbnails") or info.get("thumbnail"))


# What ``yt-dlp --ignore-no-formats-error --skip-download --write-thumbnail`` sets, scoped to
# one image entry: set globally, they would hide a site's own errors, skip the videos and
# leave a .jpg next to each one. overwrites=False reuses an image already saved. no_warnings
# hides the "No video formats found!" that ignore_no_formats_error turns into a warning; only
# -l doesn't set it already, and it names the image as skipped instead.
_IMAGE_PARAMS: dict[str, Any] = {
    "ignore_no_formats_error": True,
    "skip_download": True,
    "writethumbnail": True,
    "overwrites": False,
    "no_warnings": True,
}


class _WholeBody:
    """A response that fails when its body ends short of the length it stated.

    yt-dlp's thumbnail writer copies whatever arrives, so a cut-off image would be kept as
    saved. Its own downloader makes the same check on media (ContentTooShortError).
    """

    def __init__(self, response: Any) -> None:
        self._response = response
        self._read = 0
        # A decoded body doesn't match the length of the encoded one.
        encoding = (response.get_header("Content-Encoding") or "identity").strip().lower()
        length = int_or_none(response.get_header("Content-Length"))
        self._expected = length if encoding == "identity" else None

    def read(self, amt: int | None = None) -> bytes:
        data: bytes = self._response.read(amt)
        self._read += len(data)
        ended = not data or amt is None or amt < 0
        if ended and self._expected is not None and self._read < self._expected:
            raise IncompleteRead(self._read, self._expected - self._read)
        return data

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


def _subtitle_reason(lang: str, messages: list[str]) -> str:
    """yt-dlp's first word on a subtitle it couldn't write, without its own framing."""
    if not messages:
        return "?"
    _, framed, reason = messages[0].partition(f"Unable to download video subtitles for {lang!r}: ")
    return short_reason(reason if framed else messages[0])


class _Ydl(YoutubeDL):  # type: ignore[misc]
    """Keeps the errors that ``ignoreerrors`` would otherwise only print."""

    count_entries = False
    # Take list items that are images past yt-dlp's "No video formats found!", in every mode:
    # one behind a link reaches the match_filter only after that check, and the filter
    # decides whether it's saved (-a and -l skip it).
    save_images = False
    # len(errors) when the current item started.
    item_errors = 0
    # Errors beyond the first of an item that failed in several steps.
    repeated_errors = 0
    # While yt-dlp writes one subtitle: its errors are the subtitle's, not the item's.
    writing_subs = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.errors: list[str] = []
        # Thumbnail files this run wrote for the current item, and whether it lost its cover.
        self.item_thumbs: list[str] = []
        self.item_no_cover = False
        # Files of the current item kept as downloaded: named only if the item is saved.
        self.item_not_converted: list[str] = []
        # Images saved, or found already saved. Post hooks never run for them.
        self.images: list[str] = []
        self.item_images: list[str] = []
        self.saving_image = False
        self.fetching_image = False
        # Candidate paths that were free when yt-dlp checked them, while fetching one image.
        self.image_new: set[str] = set()
        # What yt-dlp reported while writing the current subtitle.
        self.sub_reasons: list[str] = []
        # Subtitle files this run wrote for the current item, and the languages it couldn't.
        self.item_subs: list[str] = []
        self.item_subs_failed: list[tuple[str, str]] = []
        self.item_subs_empty: list[str] = []
        super().__init__(*args, **kwargs)

    def existing_file(self, filepaths: Any, *, default_overwrite: bool = True) -> Any:
        if self.fetching_image:
            for path in dict.fromkeys(filepaths):
                if path in self.image_new:
                    # Left by a candidate cut off mid-body (one fetched whole ends the search):
                    # with overwrites=False, yt-dlp would take it for an image already saved.
                    Path(path).unlink(missing_ok=True)
                elif not os.path.lexists(path):
                    self.image_new.add(path)
        return super().existing_file(filepaths, default_overwrite=default_overwrite)

    def urlopen(self, req: Any) -> Any:
        response = super().urlopen(req)
        return _WholeBody(response) if self.fetching_image else response

    def process_video_result(self, info_dict: Any, download: bool = True) -> Any:
        image = (
            self.save_images and info_dict.get("playlist_index") and is_saveable_image(info_dict)
        )
        if not image:
            return super().process_video_result(info_dict, download=download)
        # The entry is already extracted, so no extractor reads ignore_no_formats_error here.
        previous = {key: self.params[key] for key in _IMAGE_PARAMS if key in self.params}
        self.params.update(_IMAGE_PARAMS)
        self.saving_image = True
        try:
            return super().process_video_result(info_dict, download=download)
        finally:
            self.saving_image = False
            for key in _IMAGE_PARAMS:
                self.params.pop(key, None)
            self.params.update(previous)

    def process_info(self, info_dict: Any) -> Any:
        self.item_errors = len(self.errors)
        self.item_thumbs, self.item_no_cover, self.item_images = [], False, []
        self.item_not_converted = []
        self.item_subs, self.item_subs_failed, self.item_subs_empty = [], [], []
        try:
            result = super().process_info(info_dict)
        except BaseException:
            # Without ignoreerrors, report_error raises from inside process_info.
            # An exception yt-dlp catches gets its own report, which is the item's one count; a
            # retry counts afresh, and anything else leaves download() before the count is read.
            self.repeated_errors += len(self.errors) - self.item_errors
            self.drop_thumbs()
            self.drop_subs()
            raise
        if self.item_failed():
            self.repeated_errors += len(self.errors) - self.item_errors - 1
            self.drop_thumbs()
            self.drop_subs()
        else:
            self.images += self.item_images
        return result

    def _write_thumbnails(
        self, label: Any, info_dict: Any, filename: Any, thumb_filename_base: Any = None
    ) -> Any:
        if self.saving_image:
            # yt-dlp's own writer, without the .cover name. Never in item_thumbs: an image
            # found already saved isn't this run's to delete.
            self.image_new = set()
            self.fetching_image = True
            try:
                images = super()._write_thumbnails(label, info_dict, filename, thumb_filename_base)
            except (RequestError, _UnsafeExtensionError):
                images = None
            finally:
                self.fetching_image = False
            # yt-dlp moves on from a candidate that failed mid-body, and leaves what it wrote.
            kept = {path for path, _ in images or []}
            for path in self.image_new - kept:
                Path(path).unlink(missing_ok=True)
            if not images:
                # Raises without ignoreerrors: single mode stops, as it does for a broken video.
                self.report_error(t("image_not_saved", info_dict.get("id")))
                return None
            self.item_images += [final for _, final in images]
            return images
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

    def _write_subtitles(self, info_dict: Any, filename: Any) -> Any:
        subtitles = info_dict.get("requested_subtitles")
        wanted = self.params.get("writesubtitles") or self.params.get("writeautomaticsub")
        if not (subtitles and wanted):
            return super()._write_subtitles(info_dict, filename)
        # Without ignoreerrors, a subtitle that can't be fetched ends the item before its media
        # is requested. One language per call, so each reason belongs to its language.
        written: list[Any] = []
        try:
            for lang, sub in subtitles.items():
                info_dict["requested_subtitles"] = {lang: sub}
                path = subtitles_filename(filename, lang, sub["ext"], info_dict.get("ext"))
                part = Path(f"{path}.part")  # The downloader's scratch file.
                part_was_there = part.exists()
                # yt-dlp deletes an earlier run's copy and writes it again: still not this run's.
                file_was_there = Path(path).exists()
                self.sub_reasons = []
                had_ignore, ignore = "ignoreerrors" in self.params, self.params.get("ignoreerrors")
                self.params["ignoreerrors"] = True
                self.writing_subs = True
                try:
                    files = super()._write_subtitles(info_dict, filename)
                finally:
                    self.writing_subs = False
                    self.params.pop("ignoreerrors", None)
                    if had_ignore:
                        self.params["ignoreerrors"] = ignore
                # After an empty body, the downloader returns without raising, and yt-dlp
                # records a path it never wrote.
                saved = sub.get("filepath")
                if saved and Path(saved).is_file():
                    written += files or []
                    if not file_was_there:
                        self.item_subs.append(saved)
                    continue
                sub.pop("filepath", None)
                if not part_was_there:
                    part.unlink(missing_ok=True)
                self.item_subs_failed.append((lang, _subtitle_reason(lang, self.sub_reasons)))
        finally:
            info_dict["requested_subtitles"] = subtitles
        return written

    def drop_thumbs(self) -> None:
        for path in self.item_thumbs:
            Path(path).unlink(missing_ok=True)
        self.item_thumbs = []

    def drop_subs(self) -> None:
        for path in self.item_subs:
            Path(path).unlink(missing_ok=True)
        self.item_subs = []

    def item_failed(self) -> bool:
        # ignoreerrors reports a failed post-processing step, then runs the post hooks anyway.
        return len(self.errors) > self.item_errors

    def report_error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        if self.writing_subs:
            # Named after the download; an ERROR: line above "Done." would read as a failure.
            self.report_warning(message)
            return
        if kwargs.get("is_error", True):
            self.errors.append(str(message))
        super().report_error(message, *args, **kwargs)

    def report_warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        if self.writing_subs:
            self.sub_reasons.append(str(message))
        super().report_warning(message, *args, **kwargs)

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


class OutputUnusable(Exception):
    """The output folder can't be created; nothing was requested yet."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason


def load_browser_cookies(ydl: YoutubeDL, spec: str | None) -> None:
    """Load the browser's cookies before any request, so a failure looks the same in every mode.

    yt-dlp loads them on the first request. Under ignoreerrors (-p, --playlist-items) the
    failure then escapes as a bare CookieLoadError; without it, as a DownloadError.

    A keyring error can also end up only logged: always under ignoreerrors, and in any mode when
    a try inside yt-dlp swallows it. Nothing tells the jar it then returns, decrypted with an
    empty password, from a good one, so that's a failure too.

    A load with no error can still hand back a jar where no cookie has a value: none decrypted,
    or the database is empty. No site can use it, so it's a failure as well.
    """
    if not spec or not ydl.params.get("cookiesfrombrowser"):
        return
    logged: list[str] = []
    warned: list[str] = []
    report_error = ydl.report_error
    report_warning = ydl.report_warning

    def note(message: Any, *args: Any, **kwargs: Any) -> None:
        logged.append(str(message))
        report_error(message, *args, **kwargs)

    def heed(message: Any, *args: Any, **kwargs: Any) -> None:
        warned.append(str(message))
        report_warning(message, *args, **kwargs)

    # What yt-dlp's cookie logger calls. On the instance: the preview passes a plain YoutubeDL.
    ydl.report_error = note
    ydl.report_warning = heed
    try:
        jar = ydl.cookiejar  # Not cached on failure: every read retries the load.
    except (CookieLoadError, DownloadError) as exc:
        failure = cookie_failure(exc)
        if failure is None:
            raise
        raise CookiesUnavailable(spec, failure) from exc
    finally:
        del ydl.report_error
        del ydl.report_warning
    if logged:
        raise CookiesUnavailable(spec, logged_failure(logged[0]))
    failure = jar_failure(spec, len(jar), any(cookie.value for cookie in jar), bool(warned))
    if failure:
        raise CookiesUnavailable(spec, failure)


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
                # download() adds the metadata step (_TagSource), then the cover step
                # (_EmbedCover).
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
        merge = opts.video_format
        if opts.video_format == "webm":
            # A webm on offer needs no re-encode; streams webm can't hold merge into mkv,
            # which the container step then re-encodes.
            ydl["format_sort"] = ["ext:webm:webm"]
            merge = "webm/mkv"
        ydl.update(
            {
                "format": fmt,
                "merge_output_format": merge,
                # As yt-dlp's --remux-video and --recode-video set it: a rerun finds the
                # converted file and downloads nothing.
                "final_ext": opts.video_format,
                # download() adds the container step and the metadata step (_TagSource).
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

    def __init__(self, final_ext: str | None, *, container: str | None = None) -> None:
        super().__init__()
        # Audio extraction changes the extension after download, and so does the container
        # step for what it converts.
        self._final_ext = final_ext
        self._container = container

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        # Carousel items share both title and webpage_url, so the check below
        # can't tell them apart.
        if info.get("playlist_index"):
            info["_name_suffix"] = f" [{info.get('id')}]"
            return [], info
        path = Path(self._downloader.prepare_filename(info))
        paths = [path]
        if self._final_ext:
            paths = [path.with_suffix(f".{self._final_ext}")]
        elif self._container:
            # yt-dlp's final_ext: it looks for the container's name first, then the item's own,
            # for any item, and takes the first it finds as this one already downloaded.
            paths.insert(0, path.with_suffix(f".{self._container}"))
        taken = next((p for p in paths if p.exists()), None)
        if taken and _source_url(taken) != info.get("webpage_url"):
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


def _subtitle_packets(path: str) -> int | None:
    """Subtitle packets ffprobe reads in the file; None when it finds no subtitle stream."""
    try:
        cmd = ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "s"]
        cmd += ["-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", _probe_path(path)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    # As in _has_media_streams: side data adds a field, stream groups add blank rows.
    rows = [row.split(",", 1)[0].strip() for row in done.stdout.splitlines()]
    counts = [row for row in rows if row]
    if done.returncode != 0 or not counts:
        return None
    # N/A: a stream with nothing in it, such as binary junk or a header with no cue.
    return sum(int(row) if row.isdigit() else 0 for row in counts)


class _SubtitlesSeen(PostProcessor):  # type: ignore[misc]
    """Record whether an item that reached post-processing got any subtitle file.

    Also notes the files that hold no subtitle text: the embed would make an empty track of them.
    """

    def __init__(self, seen: list[bool]) -> None:
        super().__init__()
        self._seen = seen

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        files = _subtitle_files(info)
        if files:
            self._seen.append(True)
        empty = [path for path in files if _subtitle_packets(path) == 0]
        self._downloader.item_subs_empty.extend(empty)
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
        requested = info.get("requested_subtitles")
        empty = set(getattr(self._downloader, "item_subs_empty", ()))
        tracks = {
            lang: sub for lang, sub in (requested or {}).items() if sub.get("filepath") not in empty
        }
        files = _subtitle_files({"requested_subtitles": tracks})
        temp = Path(prepend_extension(info["filepath"], "temp"))
        temp_was_there = temp.exists()
        left_out = len(tracks) < len(requested or {})
        # With every file left out for holding no text, there's nothing to embed.
        if files or not left_out:
            if left_out:
                info["requested_subtitles"] = tracks
            try:
                super().run(info)
            except PostProcessingError:
                # ffmpeg fails before opening the temp file when it can't read a track.
                if not temp_was_there:
                    temp.unlink(missing_ok=True)
            finally:
                if left_out:
                    info["requested_subtitles"] = requested
        # Covers the failure above, containers that can't hold subtitles, srt in webm, json.
        streams = _subtitle_streams(info["filepath"]) if files else None
        if streams is not None and streams < len(files):
            self._not_embedded.append(Path(info["filepath"]).name)
        return [], info


_STREAM_TYPES = frozenset({"video", "audio", "subtitle", "data", "attachment", "unknown"})


def _has_media_streams(path: str) -> bool | None:
    """Whether ffprobe finds audio or video in the file; None when ffprobe can't answer."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]
        cmd.append(_probe_path(path))
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    # A stream's side data (a phone's display matrix, HDR metadata, stereo 3D) prints as an
    # empty field after its type: "video,". Stream groups and programs add blank rows.
    rows = (row.split(",", 1)[0].strip() for row in done.stdout.splitlines())
    types = {row for row in rows if row}
    if done.returncode != 0 or not types <= _STREAM_TYPES:
        return None
    return bool({"audio", "video"} & types)


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


class _ContainerStep(FFmpegVideoConvertorPP):  # type: ignore[misc]
    """yt-dlp's recode or remux into the chosen container, applied to what the probe calls video.

    A file ffmpeg can't convert is kept as downloaded: yt-dlp would report a file already on
    disk as a failed download, and stop a carousel in single mode.
    """

    def __init__(self, downloader: Any, container: str) -> None:
        super().__init__(downloader, preferedformat=container)

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        path, ext = info["filepath"], (info.get("ext") or "").lower()
        target, already = resolve_mapping(ext, self.mapping)
        # yt-dlp's own skip prints a line, which nearly every mp4 download would get.
        if already or not _converts(info):
            return [], info
        # ffmpeg reads a text body as a subtitle track, and would convert it into a file that
        # passes for saved. The metadata step fails it, as it does without a conversion.
        media = _has_media_streams(path)
        if media is False:
            return [], info
        if media is None:
            # Not knowing what the file holds, converting it could pass a text body for saved.
            # Keep it as downloaded and say so: the metadata step still fails a body it can't tag.
            self._downloader.item_not_converted.append(Path(path).name)
            return [], info
        converted = Path(replace_extension(path, target, ext))
        converted_was_there = converted.exists()
        try:
            delete, info = super().run(info)
        except PostProcessingError:
            # ffmpeg leaves an empty file at the target.
            if not converted_was_there:
                converted.unlink(missing_ok=True)
            self._downloader.item_not_converted.append(Path(path).name)
            return [], info
        # yt-dlp takes a file an earlier run saved in another container as this one, already
        # downloaded (final_ext). Only what this run downloaded gives way to its conversion.
        return (delete if info.get("__real_download") else []), info


class _RecodeTo(_ContainerStep):
    @classmethod
    def pp_key(cls) -> str:
        # Keeps the [VideoConvertor] tag it prints, and its postprocessor_args key.
        return "VideoConvertor"


class _RemuxTo(_ContainerStep, FFmpegVideoRemuxerPP):  # type: ignore[misc]
    @classmethod
    def pp_key(cls) -> str:
        return "VideoRemuxer"


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


def _skip_items_without_media(
    skipped: list[str], *, keep_images: bool
) -> Callable[..., str | None]:
    """``match_filter`` dropping carousel items with nothing to fetch.

    yt-dlp downloads carousels even with ``noplaylist``, so without this the
    first such item would abort a single-mode run. Images (no formats, a
    thumbnail) pass when ``keep_images``, for ``_Ydl`` to save. yt-dlp also
    calls this with the playlist's own metadata (no ``id``) and for lone URLs
    (no ``playlist_index``); those must pass, so a lone URL with no formats
    still fails as an error.
    """

    def match_filter(info: Any, *, incomplete: Any = False) -> str | None:
        item = info.get("id")
        if not (incomplete and item and info.get("playlist_index")):
            return None
        if is_saveable_image(info):
            if keep_images:
                return None
            skipped.append(str(item))
            return t("item_image_skipped", item)
        if info.get("_type", "video") != "video" or info.get("formats") or info.get("url"):
            return None
        skipped.append(str(item))
        return t("item_no_media", item)

    return match_filter


def download(url: str, opts: DownloadOptions) -> DownloadResult:
    """Download a single URL. Raises ``DownloadError`` if nothing could be saved.

    Raises ``CookiesUnavailable`` before any request if the browser's cookies can't be loaded.
    Raises ``OutputUnusable`` if the folder can't be created.
    """
    output_dir = opts.resolve_output()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputUnusable(output_dir, exc.strerror or str(exc)) from exc
    saved: list[str] = []
    skipped: list[str] = []
    ydl_opts = build_ydl_opts(opts, output_dir)
    ydl_opts["match_filter"] = _skip_items_without_media(skipped, keep_images=not opts.audio_only)
    untagged: list[str] = []
    no_cover: list[str] = []
    not_converted: list[str] = []
    subs_failed: list[tuple[str, str, str]] = []
    subs_empty: list[str] = []
    with _Ydl(ydl_opts) as ydl:
        load_browser_cookies(ydl, opts.cookies_browser)
        ydl.save_images = True

        def keep(path: str) -> None:
            if not ydl.item_failed():
                saved.append(path)
                if ydl.item_no_cover:
                    no_cover.append(Path(path).name)
                not_converted.extend(ydl.item_not_converted)
                name = Path(path).name
                subs_failed.extend((name, lang, why) for lang, why in ydl.item_subs_failed)
                subs_empty.extend(Path(sub).name for sub in ydl.item_subs_empty)

        ydl.add_post_hook(keep)
        container = None if opts.audio_only else opts.video_format
        if container:
            # First, so the file gets its tags and subtitles in its final container.
            step = _RemuxTo if container == "mkv" else _RecodeTo
            ydl.add_post_processor(step(ydl, container), when="post_process")
        # Appended after the params' postprocessors: audio extraction comes first.
        # Metadata before the cover, as yt-dlp's CLI orders them: the metadata step maps m4a
        # with -vn, which would drop a cover embedded before it.
        ydl.add_post_processor(_TagSource(ydl, untagged), when="post_process")
        if ydl_opts.get("writethumbnail"):
            ydl.add_post_processor(_EmbedCover(ydl), when="post_process")
        final_ext = opts.audio_format if opts.audio_only else None
        ydl.add_post_processor(_AvoidNameClash(final_ext, container=container), when="video")
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
    saved += ydl.images
    # ignoreerrors turns a total failure into a bare return code.
    if returncode != 0 and not saved:
        raise DownloadError("\n".join(ydl.errors) or t("failed", url))
    count = counts[-1] if counts else None
    # yt-dlp drops items past the end without a word; an empty list keeps its own report.
    missing = tuple(missing_items(items, count)) if items and count else ()
    return DownloadResult(
        saved=len(saved),
        skipped=len(skipped),
        failed=len(ydl.errors) - ydl.repeated_errors,
        missing=missing,
        playlist_count=count,
        subs_missing=opts.subs and not subs_seen,
        subs_not_embedded=tuple(not_embedded),
        untagged=tuple(untagged),
        no_cover=tuple(no_cover),
        not_converted=tuple(not_converted),
        subs_failed=tuple(subs_failed),
        subs_empty=tuple(subs_empty),
    )


def list_formats(url: str, opts: DownloadOptions) -> int:
    """Print available formats for a URL (equivalent to ``yt-dlp -F``)."""
    ydl_opts: dict[str, Any] = {
        "listformats": True,
        "match_filter": _skip_items_without_media([], keep_images=False),
    }
    if opts.cookies_browser:
        ydl_opts["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)
    with _Ydl(ydl_opts) as ydl:
        load_browser_cookies(ydl, opts.cookies_browser)
        ydl.save_images = True
        return int(ydl.download([url]))
