"""Step-by-step wizard for choosing download options.

Three phases:

1. **Pre-probe** (linear, no back-nav): URL → optional cookies if the site
   usually requires them.
2. **Probe** (no UI): yt-dlp inspects the URL. On auth failure, we re-prompt
   cookies and retry. On clean failure, we fall back to a manual flow.
3. **Post-probe state machine** (with back/cancel): we walk a list of steps
   tailored to the detected content kind. Steps that don't apply (e.g.
   ``quality`` when the user picked audio) are silently skipped.

This shape lets us avoid asking the user about quality/container before we
even know whether the URL is a video, an image carousel, or an audio track.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .clipboard import read_clipboard
from .config import (
    AUDIO_FORMATS,
    BROWSERS,
    VIDEO_FORMATS,
    DownloadOptions,
    xdg_dir,
)
from .cookies import browser_of, report_failure
from .i18n import LANG, is_yes, t
from .probe import (
    ContentKind,
    Probe,
    ProbeItem,
    detect_site,
    is_image_post_error,
    probe,
    site_likely_needs_cookies,
)
from .selection import parse_selection, to_yt_dlp_items
from .ui import C, fmt_bytes, fmt_duration, info, warn


def _cookies_set(opts: DownloadOptions) -> bool:
    """Indirect read so mypy doesn't keep narrowing across mutating calls."""
    return opts.cookies_browser is not None


class _Back(Exception):
    """Raised inside a step to rewind to the previous step."""


class _Cancel(Exception):
    """Raised inside a step to abort the wizard entirely."""


class _Refused(Exception):
    """The probe found nothing the wizard can save at this URL."""


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------
def _read_choice(default: str, valid: list[str]) -> str:
    while True:
        try:
            reply = input(t("ask_choice", default)).strip().lower()
        except EOFError as e:
            raise _Cancel from e
        reply = reply or default
        if reply == "b":
            raise _Back
        if reply == "c":
            raise _Cancel
        if reply in valid:
            return reply
        warn(t("invalid_retry"))


def _read_text(prompt: str, default: str = "", *, allow_back: bool = True) -> str:
    parts = []
    if allow_back:
        parts.append(f"{C.YELLOW}b{C.RESET}={t('back')}")
    parts.append(f"{C.RED}c{C.RESET}={t('cancel')}")
    full = f"{prompt} {C.DIM}({', '.join(parts)}){C.RESET}: "
    try:
        reply = input(full).strip()
    except EOFError as e:
        raise _Cancel from e
    low = reply.lower()
    if low == "c":
        raise _Cancel
    if allow_back and low == "b":
        raise _Back
    return reply or default


def _opt(indicator: str, label: str) -> None:
    print(f"  {C.GREEN}{indicator}{C.RESET}) {label}")


def _back_opt() -> None:
    print(f"  {C.YELLOW}b{C.RESET}) ← {t('back')}")


def _cancel_opt() -> None:
    print(f"  {C.RED}c{C.RESET}) ✗ {t('cancel')}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
_SUMMARY_LABELS_ES = {
    "select": "Items",
    "type": "Tipo",
    "quality": "Calidad",
    "container": "Contenedor",
    "aformat": "Formato",
    "output": "Carpeta",
    "cookies": "Cookies",
}
_SUMMARY_LABELS_EN = {
    "select": "Items",
    "type": "Type",
    "quality": "Quality",
    "container": "Container",
    "aformat": "Format",
    "output": "Folder",
    "cookies": "Cookies",
}


def _summary_label(step: str) -> str:
    table = _SUMMARY_LABELS_ES if LANG == "es" else _SUMMARY_LABELS_EN
    return table.get(step, step)


def _summary_value(step: str, opts: DownloadOptions) -> str | None:
    if step == "type":
        return t("type_audio") if opts.audio_only else t("type_video")
    if step == "quality":
        return "best" if opts.quality == "best" else f"{opts.quality}p"
    if step == "container":
        return opts.video_format
    if step == "aformat":
        return opts.audio_format
    if step == "select":
        return opts.playlist_items or "all"
    if step == "output":
        return str(opts.output) if opts.output else ""
    if step == "cookies":
        return opts.cookies_browser or t("no_cookies")
    return None


def _print_summary(history: list[str], opts: DownloadOptions) -> None:
    if not history:
        return
    print()
    print(f"{C.DIM}{t('summary')}{C.RESET}")
    for step in history:
        value = _summary_value(step, opts)
        if value is None:
            continue
        print(f"  {C.GREEN}✓{C.RESET} {_summary_label(step)}: {value}")
    print()


# ---------------------------------------------------------------------------
# Atomic steps
# ---------------------------------------------------------------------------
def step_url(opts: DownloadOptions) -> None:
    current = opts.urls[0] if opts.urls else ""
    if not current:
        clip = read_clipboard()
        if clip:
            print(f"{C.DIM}{t('from_clipboard')}{C.RESET} {clip}")
            try:
                reply = input(t("ask_use_clip")).strip()
            except EOFError as e:
                raise _Cancel from e
            if not reply or is_yes(reply):
                opts.urls = [clip]
                return
    label = t("ask_url")
    prompt = f"{label} [{current}]" if current else label
    new_url = _read_text(prompt, default=current, allow_back=False)
    if not new_url:
        from .ui import die

        die(t("empty_url"))
    # A URL can't hold unencoded whitespace, so spaces separate several.
    opts.urls = new_url.split()


def step_cookies(opts: DownloadOptions) -> None:
    print(t("ask_cookies"))
    _opt("1", t("no_cookies"))
    for i, browser in enumerate(BROWSERS, 2):
        _opt(str(i), browser)
    _back_opt()
    _cancel_opt()
    current = browser_of(opts.cookies_browser) if opts.cookies_browser else None
    default = str(BROWSERS.index(current) + 2) if current in BROWSERS else "1"
    valid = [str(i) for i in range(1, len(BROWSERS) + 2)]
    choice = _read_choice(default, valid)
    n = int(choice)
    if n == 1:
        opts.cookies_browser = None
    elif BROWSERS[n - 2] != current:  # Same browser keeps its explicit +keyring.
        opts.cookies_browser = BROWSERS[n - 2]


def step_type(opts: DownloadOptions) -> None:
    print(t("ask_type"))
    _opt("1", t("type_video"))
    _opt("2", t("type_audio"))
    _back_opt()
    _cancel_opt()
    default = "2" if opts.audio_only else "1"
    choice = _read_choice(default, ["1", "2"])
    opts.audio_only = choice == "2"


def step_quality(opts: DownloadOptions) -> None:
    print(f"{t('ask_quality')}:")
    for i, label in enumerate(["480p", "720p", "1080p", "1440p (2K)", "2160p (4K)", "best"], 1):
        _opt(str(i), label)
    _back_opt()
    _cancel_opt()
    idx = {"480": "1", "720": "2", "1080": "3", "1440": "4", "2160": "5", "best": "6"}
    default = idx.get(opts.quality, "3")
    choice = _read_choice(default, ["1", "2", "3", "4", "5", "6"])
    rev = {"1": "480", "2": "720", "3": "1080", "4": "1440", "5": "2160", "6": "best"}
    opts.quality = rev[choice]


def step_container(opts: DownloadOptions) -> None:
    print(f"{t('ask_vformat')}:")
    for i, fmt in enumerate(VIDEO_FORMATS, 1):
        _opt(str(i), f"{fmt:<5} {C.DIM}— {t('vformat_desc_' + fmt)}{C.RESET}")
    _back_opt()
    _cancel_opt()
    default = (
        str(VIDEO_FORMATS.index(opts.video_format) + 1)
        if opts.video_format in VIDEO_FORMATS
        else "1"
    )
    choice = _read_choice(default, [str(i) for i in range(1, len(VIDEO_FORMATS) + 1)])
    opts.video_format = VIDEO_FORMATS[int(choice) - 1]


def step_aformat(opts: DownloadOptions) -> None:
    print(f"{t('ask_aformat')}:")
    for i, fmt in enumerate(AUDIO_FORMATS, 1):
        _opt(str(i), fmt)
    _back_opt()
    _cancel_opt()
    default = (
        str(AUDIO_FORMATS.index(opts.audio_format) + 1)
        if opts.audio_format in AUDIO_FORMATS
        else "1"
    )
    choice = _read_choice(default, [str(i) for i in range(1, len(AUDIO_FORMATS) + 1)])
    opts.audio_format = AUDIO_FORMATS[int(choice) - 1]


def step_output(opts: DownloadOptions) -> None:
    if opts.output:
        default_path = opts.output
    else:
        default_path = xdg_dir("MUSIC", "Music") if opts.audio_only else xdg_dir("VIDEOS", "Videos")
    prompt = t("ask_output", default_path).rstrip(": ")
    answer = _read_text(prompt, default=str(default_path))
    opts.output = Path(answer)


def step_select(opts: DownloadOptions, items: list[ProbeItem], preset: str | None = None) -> None:
    """Show carousel items and ask the user which to download.

    Default (Enter) keeps the command line's ``--playlist-items`` as given,
    or else selects every supported item. Unsupported items are visually
    flagged and skipped if selected.
    """
    print()
    for it in items:
        if it.supported and it.kind == ContentKind.SINGLE_VIDEO:
            tag, color = t("kind_video").lower(), C.GREEN
        elif it.supported and it.kind == ContentKind.SINGLE_AUDIO:
            tag, color = t("kind_audio").lower(), C.CYAN
        elif it.supported and it.kind == ContentKind.SINGLE_IMAGE:
            tag, color = t("kind_image").lower(), C.YELLOW
        elif it.kind == ContentKind.SINGLE_IMAGE:
            tag, color = t("kind_image").lower(), C.DIM
        else:
            tag, color = "?", C.DIM
        suffix = "" if it.supported else f" {C.DIM}[no soportado]{C.RESET}"
        print(f"  {it.index:2d}. {color}[{tag}]{C.RESET}{suffix}  {it.title}")

    while True:
        print()
        try:
            reply = input(
                t("ask_selection_default_preset", preset) if preset else t("ask_selection_default")
            )
        except EOFError as e:
            raise _Cancel from e
        low = reply.strip().lower()
        if low == "c":
            raise _Cancel
        if low == "b":
            raise _Back
        if not low and preset:
            opts.playlist_items = preset
        elif not low:
            indices = [it.index for it in items if it.supported]
            if not indices:
                warn(t("no_supported_items"))
                raise _Cancel
            opts.playlist_items = to_yt_dlp_items(indices)
        elif low == "all":
            opts.playlist_items = ""
            print(f"{C.DIM}{t('selected', f'all (1-{len(items)})')}{C.RESET}")
            return
        else:
            parsed = parse_selection(reply, len(items))
            if parsed is None:
                warn(t("invalid_selection"))
                continue
            unsup = [str(i) for i in parsed if not items[i - 1].supported]
            if unsup:
                warn(t("selection_warning_unsupported", ",".join(unsup)))
                parsed = [i for i in parsed if items[i - 1].supported]
                if not parsed:
                    warn(t("no_supported_items"))
                    continue
            opts.playlist_items = to_yt_dlp_items(parsed)
        print(f"{C.DIM}{t('selected', opts.playlist_items)}{C.RESET}")
        return


# ---------------------------------------------------------------------------
# Detection banner
# ---------------------------------------------------------------------------
_KIND_LABEL = {
    ContentKind.SINGLE_VIDEO: "kind_video",
    ContentKind.SINGLE_AUDIO: "kind_audio",
    ContentKind.SINGLE_IMAGE: "kind_image",
    ContentKind.PLAYLIST: "kind_playlist",
    ContentKind.UNKNOWN: "kind_unknown",
}


def _print_detection(p: Probe) -> None:
    print()
    print(
        f"{C.GREEN}✓{C.RESET} {C.BOLD}{t('detected')}{C.RESET}: "
        f"{p.site} / {t(_KIND_LABEL[p.content_kind])}"
    )
    if p.is_playlist:
        sup = sum(1 for it in p.items if it.supported)
        unsup = len(p.items) - sup
        if unsup:
            print(f"  {C.DIM}{t('items_count', len(p.items), sup, unsup)}{C.RESET}")
        else:
            print(f"  {C.DIM}{t('items_count_simple', len(p.items))}{C.RESET}")
    else:
        bits: list[str] = []
        if p.title:
            bits.append(p.title)
        if p.duration:
            bits.append(fmt_duration(p.duration))
        if p.filesize:
            bits.append(fmt_bytes(p.filesize))
        if p.uploader:
            bits.append(f"@ {p.uploader}")
        if bits:
            print(f"  {C.DIM}{' · '.join(bits)}{C.RESET}")
    print()


# ---------------------------------------------------------------------------
# Plan builder + state machine
# ---------------------------------------------------------------------------
def _build_post_probe_plan(probe_result: Probe, cookies_asked: bool) -> list[str]:
    """Return ordered list of step names for the post-probe phase."""
    steps: list[str] = []
    if probe_result.is_playlist:
        steps.append("select")
    # An image is saved as it is: no type, quality, container or audio format applies.
    if probe_result.content_kind != ContentKind.SINGLE_IMAGE:
        # SINGLE_AUDIO sources don't get a video/audio choice, unless only a label says audio.
        if probe_result.content_kind != ContentKind.SINGLE_AUDIO or probe_result.may_hold_video:
            steps.append("type")
        # We list both branches; the runner skips the inapplicable one based on
        # opts.audio_only at run-time.
        steps.extend(["quality", "container", "aformat"])
    steps.append("output")
    if not cookies_asked:
        steps.append("cookies")
    return steps


def _images_only(opts: DownloadOptions, items: list[ProbeItem]) -> bool:
    """Whether every item the selection names is an image the tool can save."""
    if not items or opts.playlist_items is None:
        return False
    picked = parse_selection(opts.playlist_items, len(items))
    if picked is None:  # A --playlist-items spec only yt-dlp reads, like "-1".
        return False
    chosen = [items[i - 1] for i in picked] if picked else items
    return all(it.supported and it.kind == ContentKind.SINGLE_IMAGE for it in chosen)


def _step_applies(name: str, opts: DownloadOptions, images_only: bool = False) -> bool:
    """Whether a step actually has work to do given current opts."""
    if images_only and name in ("type", "quality", "container", "aformat"):
        return False
    if name in ("quality", "container"):
        return not opts.audio_only
    if name == "aformat":
        return opts.audio_only
    return True


def _run_step(
    name: str, opts: DownloadOptions, items: list[ProbeItem], preset: str | None = None
) -> None:
    dispatch: dict[str, Callable[[], None]] = {
        "url": lambda: step_url(opts),
        "cookies": lambda: step_cookies(opts),
        "type": lambda: step_type(opts),
        "quality": lambda: step_quality(opts),
        "container": lambda: step_container(opts),
        "aformat": lambda: step_aformat(opts),
        "output": lambda: step_output(opts),
        "select": lambda: step_select(opts, items, preset),
    }
    fn = dispatch.get(name)
    if fn is None:
        raise ValueError(f"unknown step: {name}")
    fn()


def _walk_plan(
    plan: list[str],
    opts: DownloadOptions,
    items: list[ProbeItem],
    preset: str | None = None,
    asked: tuple[str, ...] = (),
) -> bool:
    """Walk the post-probe plan with back/cancel. Returns False if cancelled.

    ``asked`` holds steps answered before the walk: listed in the summary, never gone back to.
    """
    history: list[str] = []
    # -a as it stood before a selection of images only turned it off.
    audio_before_images: bool | None = None
    i = 0
    while i < len(plan):
        name = plan[i]
        images_only = _images_only(opts, items)
        if images_only and audio_before_images is None:
            # Images are saved as images: audio mode would skip them all.
            audio_before_images, opts.audio_only = opts.audio_only, False
        elif not images_only and audio_before_images is not None:
            opts.audio_only, audio_before_images = audio_before_images, None
        if not _step_applies(name, opts, images_only):
            # Silent skip; don't push to history (so back navigates correctly)
            i += 1
            continue
        _print_summary([*asked, *history], opts)
        try:
            _run_step(name, opts, items, preset)
        except _Back:
            # Pop the last step that actually ran and re-enter it
            if history:
                last = history.pop()
                for j in range(len(plan) - 1, -1, -1):
                    if plan[j] == last:
                        i = j
                        break
            # else: at first step, just re-prompt current
            continue
        except _Cancel:
            return False
        history.append(name)
        i += 1
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _print_title(k: int, n: int) -> None:
    counter = f" ({k}/{n})" if n > 1 else ""
    print(f"{C.BOLD}{C.CYAN}{t('wizard_title')}{counter}{C.RESET}")


def run(opts: DownloadOptions) -> DownloadOptions | None:
    """Walk the wizard for the first URL only. Returns updated opts, or ``None``."""
    _print_title(1, 1)
    try:
        step_url(opts)
    except _Cancel:
        return None
    opts.urls = opts.urls[:1]
    try:
        return _walk_url(opts)
    except _Refused:
        return None


def ask_urls(opts: DownloadOptions) -> list[str] | None:
    """Only the URL prompt, once per URL given: all -l needs. ``None`` if cancelled."""
    given = opts.urls or [""]
    urls: list[str] = []
    for k, url in enumerate(given, 1):
        _print_title(k, len(given))
        current = replace(opts, urls=[url] if url else [])
        try:
            step_url(current)
        except _Cancel:
            return None
        urls += current.urls
    return urls


def run_all(opts: DownloadOptions) -> list[tuple[str, DownloadOptions | None]] | None:
    """Walk the wizard once per URL, in order.

    Returns one pair per URL, with ``None`` options for a URL the wizard
    refused, or ``None`` if the user cancelled.
    """
    # (url, still to be asked at the URL prompt)
    pending: list[tuple[str, bool]] = [(url, True) for url in opts.urls] or [("", True)]
    walked: list[tuple[str, DownloadOptions | None]] = []
    k = 0
    while k < len(pending):
        url, ask = pending[k]
        _print_title(k + 1, len(pending))
        # Every walk starts from the command line, never from the previous walk.
        current = replace(opts, urls=[url] if url else [])
        try:
            if ask:
                step_url(current)
                pending[k : k + 1] = [(u, False) for u in current.urls]
                current.urls = current.urls[:1]
            result = _walk_url(current)
        except _Cancel:
            return None
        except _Refused:
            result = None
        else:
            if result is None:
                return None
        # The walk can ask for the URL again (back from the first cookies prompt).
        pending[k + 1 : k + 1] = [(u, False) for u in current.urls[1:]]
        current.urls = current.urls[:1]
        walked.append((current.urls[0], result))
        k += 1
    return walked


def _drop_unreadable_browser(probe_result: Probe, opts: DownloadOptions) -> bool:
    """Say why the browser's cookies couldn't be read, and stop using them. True if so."""
    if probe_result.cookie_failure is None or opts.cookies_browser is None:
        return False
    report_failure(opts.cookies_browser, probe_result.cookie_failure)
    opts.cookies_browser = None
    return True


def _walk_url(opts: DownloadOptions) -> DownloadOptions | None:
    """Everything after the URL prompt, for ``opts.urls[0]``.

    Returns ``None`` if cancelled, and raises ``_Refused`` when there's
    nothing to save.
    """
    # The command line's value: run_all starts every walk from it.
    preset = opts.playlist_items
    # ---- Phase 1.5: pre-emptive cookies for sites that always need them ----
    cookies_asked = False
    while site_likely_needs_cookies(detect_site(opts.urls[0])) and not opts.cookies_browser:
        info(f"  {t('auth_needed_pre')}")
        try:
            step_cookies(opts)
        except _Cancel:
            return None
        except _Back:
            # Back to the URL prompt; the URL typed there decides whether to ask again.
            try:
                step_url(opts)
            except _Cancel:
                return None
            continue
        cookies_asked = True
        break  # "No cookies" leaves cookies_browser unset, so the condition alone would ask again.

    # ---- Phase 2: probe with auth retry ----
    info(t("probing"))
    probe_result = probe(opts.urls[0], opts.cookies_browser)
    if _drop_unreadable_browser(probe_result, opts):
        # Nothing was inspected: look again without cookies. A login wall still gets the retry.
        info(t("probing"))
        probe_result = probe(opts.urls[0])
    if probe_result.needs_cookies and not opts.cookies_browser:
        if is_image_post_error(probe_result.error_message or ""):
            warn(t("image_post_needs_login"))
        else:
            warn(t("auth_needed_retry", probe_result.error_message))
        try:
            step_cookies(opts)
        except _Cancel:
            return None
        except _Back:
            pass  # The URL is already probed, so there's nothing to go back to: same as no.
        cookies_asked = True
        # step_cookies mutates opts.cookies_browser; mypy's narrowing inside
        # this branch can't see that, so we use an unrelated read path.
        if _cookies_set(opts):
            info(t("probing"))
            retried = probe(opts.urls[0], opts.cookies_browser)
            # A browser that can't be read changes nothing: keep the cookieless result.
            if not _drop_unreadable_browser(retried, opts):
                probe_result = retried

    # ---- Phase 2.5: handle special outcomes ----
    if not probe_result.failed:
        kind = probe_result.content_kind
        if kind == ContentKind.SINGLE_IMAGE and not probe_result.saveable_image:
            warn(t("image_only_skipped"))
            raise _Refused
        if probe_result.error_message:
            warn(t("probe_partial", probe_result.error_message))
        if probe_result.is_playlist and not any(it.supported for it in probe_result.items):
            warn(t("no_supported_items"))
            raise _Refused
        _print_detection(probe_result)
        if probe_result.may_hold_video:
            warn(t("audio_may_hold_video"))
        elif probe_result.content_kind == ContentKind.SINGLE_AUDIO:
            opts.audio_only = True
        elif probe_result.saveable_image:
            # Saved as an image: audio mode would skip it.
            opts.audio_only = False
    else:
        warn(t("probe_failed", probe_result.error_message or "unknown"))
        if probe_result.needs_cookies and opts.cookies_browser:
            warn(t("auth_still_needed", browser_of(opts.cookies_browser)))
        warn(t("probe_falling_back"))

    # Lock in the playlist decision so cli.py doesn't re-ask.
    if probe_result.is_playlist:
        opts.playlist_mode = "yes"
    elif probe_result.content_kind in (
        ContentKind.SINGLE_VIDEO,
        ContentKind.SINGLE_AUDIO,
        ContentKind.SINGLE_IMAGE,
    ):
        opts.playlist_mode = "no"

    # ---- Phase 3: post-probe state machine ----
    plan = _build_post_probe_plan(probe_result, cookies_asked)
    asked = ("cookies",) if cookies_asked else ()
    if not _walk_plan(plan, opts, probe_result.items, preset, asked):
        return None
    return opts
