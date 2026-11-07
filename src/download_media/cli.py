"""Command-line entry point.

Phase 3: full feature set wired up — wizard, clipboard fallback, carousel
item picker, browser cookies, subtitle support, history log, and the
equivalent-command hint at the end.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from . import __version__, equivalent, history, selection, wizard
from .clipboard import read_clipboard
from .config import (
    AUDIO_FORMATS,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_QUALITY,
    DEFAULT_SUB_LANGS,
    DEFAULT_VIDEO_FORMAT,
    QUALITIES,
    VIDEO_FORMATS,
    DownloadOptions,
    PlaylistMode,
)
from .cookies import browser_of, parse_spec, uses_keyring, ydl_cookies
from .downloader import DownloadResult, build_ydl_opts, download, list_formats
from .i18n import is_yes, t
from .probe import is_auth_error
from .ui import C, die, fmt_bytes, fmt_duration, info, ok, warn

# Flags that signal "I know what I want; skip the wizard".
_INTENT_FLAGS: frozenset[str] = frozenset({
    "-a", "--audio",
    "-q", "--quality",
    "-f", "--format",
    "--video-format",
    "-o", "--output",
    "-s", "--subs",
    "--sub-langs",
    "-p", "--playlist",
    "-P", "--no-playlist",
    "--playlist-items",
    "--cookies",
})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="download-media",
        description=(
            "Interactive yt-dlp wrapper. Downloads video, audio, and images "
            "from 1800+ sites with a step-by-step wizard or direct CLI flags."
        ),
        epilog="Quote URLs to protect them from shell metacharacters (? &).",
    )
    p.add_argument("-v", "--version", action="version", version=f"download-media {__version__}")
    p.add_argument("urls", nargs="*", help="One or more URLs to download.")

    p.add_argument("-a", "--audio", action="store_true", help="Audio only (mp3 by default).")
    p.add_argument(
        "-q", "--quality", choices=QUALITIES, default=DEFAULT_QUALITY,
        help=f"Max video quality (default: {DEFAULT_QUALITY}).",
    )
    p.add_argument(
        "-f", "--format", choices=AUDIO_FORMATS, default=DEFAULT_AUDIO_FORMAT,
        dest="audio_format", help=f"Audio format (default: {DEFAULT_AUDIO_FORMAT}).",
    )
    p.add_argument(
        "--video-format", choices=VIDEO_FORMATS, default=DEFAULT_VIDEO_FORMAT,
        dest="video_format",
        help=f"Video container (default: {DEFAULT_VIDEO_FORMAT}). "
        "mp4 prefers H.264/AAC so it plays on WhatsApp, iPhone and TVs.",
    )

    p.add_argument("-o", "--output", type=Path, help="Output folder (default: ~/Videos or ~/Music).")
    p.add_argument(
        "-l", "--list", action="store_true", dest="list_formats",
        help="List available formats and exit.",
    )

    pl = p.add_mutually_exclusive_group()
    pl.add_argument("-p", "--playlist", action="store_true", help="Force full playlist download.")
    pl.add_argument("-P", "--no-playlist", action="store_true", dest="no_playlist",
                    help="Force single-video download.")
    pl.add_argument("--playlist-items", dest="playlist_items", metavar="ITEMS",
                    help="Download only these playlist items, e.g. 1-3,5.")

    p.add_argument("-s", "--subs", action="store_true", help="Download subtitles.")
    p.add_argument(
        "--sub-langs", default=DEFAULT_SUB_LANGS, dest="sub_langs",
        help=f"Subtitle languages, comma-separated (default: {DEFAULT_SUB_LANGS}).",
    )

    p.add_argument(
        "--cookies", type=parse_spec, dest="cookies_browser", metavar="BROWSER[+KEYRING]",
        help="Use cookies from this browser (firefox, brave, chrome, ...). The keyring is "
        "detected from the browser's flags file; force one with e.g. brave+gnomekeyring.",
    )

    p.add_argument(
        "-i", "--interactive", action="store_true",
        help="Force wizard mode even when intent flags are present.",
    )
    p.add_argument(
        "-y", "--yes", action="store_true", dest="no_confirm",
        help="Skip wizard and all confirmation prompts.",
    )
    return p


def options_from_args(args: argparse.Namespace) -> DownloadOptions:
    pmode: PlaylistMode = "auto"
    if args.playlist or args.playlist_items is not None:
        pmode = "yes"
    elif args.no_playlist:
        pmode = "no"
    return DownloadOptions(
        urls=list(args.urls),
        audio_only=args.audio,
        quality=args.quality,
        audio_format=args.audio_format,
        video_format=args.video_format,
        output=args.output,
        cookies_browser=args.cookies_browser,
        subs=args.subs,
        sub_langs=args.sub_langs,
        playlist_mode=pmode,
        playlist_items=args.playlist_items,
        no_confirm=args.no_confirm,
        list_formats=args.list_formats,
    )


def has_intent_flags(argv: list[str]) -> bool:
    """True if the user passed any "I know what I want" flag."""
    for token in argv:
        if token in _INTENT_FLAGS:
            return True
        # Handle --flag=value form
        if "=" in token:
            head = token.split("=", 1)[0]
            if head in _INTENT_FLAGS:
                return True
    return False


def should_run_wizard(args: argparse.Namespace, argv: list[str]) -> bool:
    if args.no_confirm:
        return False
    if args.interactive:
        return True
    if not args.urls:
        return True
    return not has_intent_flags(argv)


# ---------------------------------------------------------------------------
# Per-URL pipeline (preview, playlist resolution, download)
# ---------------------------------------------------------------------------
def _show_preview(url: str, opts: DownloadOptions) -> bool:
    """Probe a single URL, print metadata, ask user to confirm. Returns False if cancelled."""
    info(t("fetching"))
    from typing import Any

    from yt_dlp import YoutubeDL

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    if opts.cookies_browser:
        ydl_opts["cookiesfrombrowser"] = ydl_cookies(opts.cookies_browser)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception:
        data = None

    print()
    print(f"{C.BOLD}{t('preview')}{C.RESET}")
    if isinstance(data, dict):
        title = data.get("title") or data.get("id")
        uploader = data.get("uploader") or data.get("channel")
        if title:
            print(f"  {t('p_title')}: {title}")
        if uploader:
            print(f"  {t('p_uploader')}: {uploader}")
        if data.get("duration"):
            print(f"  {t('p_duration')}: {fmt_duration(data['duration'])}")
        size = data.get("filesize_approx") or data.get("filesize")
        if size:
            print(f"  {t('p_size')}: {fmt_bytes(size)}")
    print()

    try:
        reply = input(t("ask_confirm")).strip()
    except EOFError:
        return False
    return not reply or is_yes(reply)


def _resolve_playlist_for_direct_mode(
    url: str, opts: DownloadOptions
) -> tuple[PlaylistMode, str | None] | None:
    """Decide playlist_mode + playlist_items for direct mode (no wizard).

    Returns ``None`` if the user cancels. In direct mode we don't probe — we
    rely on URL pattern + flags. ``-y`` short-circuits to single mode unless
    ``-p`` was also passed.
    """
    if opts.playlist_mode != "auto":
        return opts.playlist_mode, opts.playlist_items
    if opts.no_confirm or not selection.is_playlist_url(url):
        return "no", None
    # Direct-mode playlist URL without -y: ask single vs all (no probe).
    print()
    info(t("ask_pl_mode"))
    print(f"  {C.GREEN}1{C.RESET}) {t('pl_single')}")
    print(f"  {C.GREEN}2{C.RESET}) {t('pl_all')}")
    try:
        reply = input(t("ask_choice", "1")).strip() or "1"
    except EOFError:
        return None
    if reply == "2":
        return "yes", None
    return "no", None


class _Outcome(NamedTuple):
    ok: bool
    cookies_dropped: bool  # It took retrying without the requested browser cookies.
    mode: PlaylistMode | None  # None: cancelled before the download started.


def _download_one(url: str, opts: DownloadOptions) -> _Outcome:
    """Run the full per-URL pipeline."""
    # Wizard mode already resolved playlist + items in opts.
    if opts.playlist_mode == "auto":
        pl = _resolve_playlist_for_direct_mode(url, opts)
        if pl is None:
            warn(t("cancelled"))
            return _Outcome(False, False, None)
        pmode, items = pl
    else:
        pmode = opts.playlist_mode
        items = opts.playlist_items

    # Direct-mode single: still preview before downloading
    direct_single = (
        opts.playlist_mode == "auto" and pmode == "no" and not opts.no_confirm
    )
    if direct_single and not _show_preview(url, opts):
        warn(t("cancelled"))
        return _Outcome(False, False, None)

    effective = replace(opts, playlist_mode=pmode, playlist_items=items)

    if not opts.audio_only and opts.video_format == "mp4" and opts.quality in ("1440", "2160"):
        warn(t("mp4_h264_cap", opts.quality))

    info(t("downloading"))
    print(f"{C.DIM}  URL: {url}{C.RESET}")
    print(f"{C.DIM}  → {effective.resolve_output()}{C.RESET}\n")

    from yt_dlp.utils import DownloadError

    try:
        result, cookies_dropped = _download_with_cookie_fallback(url, effective)
    except DownloadError as exc:
        # yt-dlp already printed the underlying error to stderr; we add a one-line
        # friendly summary so the user knows the program is exiting cleanly, not
        # crashing.
        warn(t("failed", url))
        _hint_for_download_error(str(exc), opts)
        return _Outcome(False, False, pmode)
    except KeyboardInterrupt:
        warn(t("cancelled"))
        raise
    if not result.saved:
        warn(t("no_supported_items"))
        return _Outcome(False, cookies_dropped, pmode)
    if result.skipped or result.failed:
        warn(t("partial_result", result.saved, result.total, result.skipped, result.failed))
    ok(t("done"))
    history.append(
        url,
        "audio" if opts.audio_only else "video",
        effective.resolve_output(),
    )
    return _Outcome(True, cookies_dropped, pmode)


def _download_with_cookie_fallback(
    url: str, opts: DownloadOptions
) -> tuple[DownloadResult, bool]:
    """Download ``url``; if browser cookies were used and it fails, retry once without them.

    Returns the download result and whether the cookies were dropped.

    A partially decrypted cookie jar (keyring issues with Brave/Chrome) can break
    extractors that work fine anonymously — Instagram answers with an empty API
    response instead of a login error. If the retry also fails, the original
    error is re-raised since it's the one tied to what the user asked for.
    """
    from yt_dlp.utils import DownloadError

    try:
        return download(url, opts), False
    except DownloadError as exc:
        if not opts.cookies_browser:
            raise
        first_error = exc

    warn(t("cookies_retry", opts.cookies_browser))
    try:
        return download(url, replace(opts, cookies_browser=None)), True
    except DownloadError:
        raise first_error from None


def _hint_for_download_error(message: str, opts: DownloadOptions) -> None:
    """Print a one-line actionable hint based on common yt-dlp error patterns."""
    msg = message.lower()
    if "secretstorage" in msg:
        warn(
            "  → install python-secretstorage so cookies from Brave/Chrome can be decrypted:"
        )
        warn("      sudo pacman -S python-secretstorage")
    elif is_auth_error(message) and not opts.cookies_browser:
        warn("  → try again with --cookies <browser> (firefox, brave, chrome, ...)")
    if opts.cookies_browser and uses_keyring(opts.cookies_browser):
        warn(t("cookies_keyring_hint", browser_of(opts.cookies_browser)))


def _print_equivalent(opts: DownloadOptions, outcomes: list[_Outcome]) -> None:
    """Print how to repeat the URLs that reached the download, one command per mode."""
    attempted: list[_Outcome] = []
    by_mode: dict[PlaylistMode, list[str]] = {}
    for url, outcome in zip(opts.urls, outcomes, strict=True):
        if outcome.mode is not None:
            attempted.append(outcome)
            by_mode.setdefault(outcome.mode, []).append(url)
    if not attempted:
        return

    # If every URL only worked once the cookies were dropped, repeating the
    # command with --cookies would just fail and retry again.
    shown = opts
    if opts.cookies_browser and all(o.ok and o.cookies_dropped for o in attempted):
        shown = replace(opts, cookies_browser=None)

    print()
    print(f"{C.BOLD}{C.CYAN}{t('equiv_cmd')}{C.RESET}")
    for mode, urls in by_mode.items():
        command = equivalent.build(replace(shown, urls=urls, playlist_mode=mode))
        print(f"  {C.DIM}{command}{C.RESET}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(raw_argv)
    opts = options_from_args(args)

    # Wizard
    if should_run_wizard(args, raw_argv):
        result = wizard.run(opts)
        if result is None:
            warn(t("cancelled"))
            return 0
        opts = result

    # Direct mode without URL: try clipboard once (no prompt — just use it if found)
    if not opts.urls:
        clip = read_clipboard()
        if clip:
            print(f"{C.DIM}{t('from_clipboard')}{C.RESET} {clip}")
            opts.urls.append(clip)

    if not opts.urls:
        die(t("url_required"))

    if opts.list_formats:
        from yt_dlp.utils import DownloadError

        try:
            return list_formats(opts.urls[0], opts)
        except DownloadError as exc:
            warn(t("failed", opts.urls[0]))
            _hint_for_download_error(str(exc), opts)
            return 1

    outcomes = [_download_one(url, opts) for url in opts.urls]
    _print_equivalent(opts, outcomes)
    return 0 if all(outcome.ok for outcome in outcomes) else 1


# Re-export the build helper so tests / callers can introspect what we'd pass
# to yt-dlp without actually downloading.
__all__ = ["build_parser", "build_ydl_opts", "main", "options_from_args"]
