"""Scripted walks through the wizard state machine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from download_media import wizard
from download_media.config import BROWSERS, DownloadOptions
from download_media.i18n import t
from download_media.probe import ContentKind, Probe, ProbeItem

URL = "https://site.test/watch"
BRAVE = str(BROWSERS.index("brave") + 2)
FIREFOX = str(BROWSERS.index("firefox") + 2)


def video_probe(url: str = URL) -> Probe:
    return Probe(url=url, site="generic", content_kind=ContentKind.SINGLE_VIDEO, title="Clip")


def login_wall() -> Probe:
    return Probe(
        url=URL,
        site="generic",
        content_kind=ContentKind.UNKNOWN,
        failed=True,
        needs_cookies=True,
        error_message="Login required",
    )


def carousel(items: list[ProbeItem] | None = None) -> Probe:
    default = [
        ProbeItem(1, ContentKind.SINGLE_IMAGE, False, "(image #1)"),
        ProbeItem(2, ContentKind.SINGLE_VIDEO, True, "Clip", 3),
        ProbeItem(3, ContentKind.SINGLE_AUDIO, True, "Track"),
    ]
    return Probe(url=URL, site="generic", content_kind=ContentKind.PLAYLIST, items=items or default)


@pytest.fixture
def walk(monkeypatch: pytest.MonkeyPatch, answers: Any) -> Callable[..., DownloadOptions | None]:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: None)

    def _walk(
        probes: list[Probe] | Probe,
        *replies: Any,
        opts: DownloadOptions | None = None,
    ) -> DownloadOptions | None:
        queue = probes if isinstance(probes, list) else [probes]
        pending = list(queue)
        monkeypatch.setattr(wizard, "probe", lambda url, cookies: pending.pop(0))
        answers(*replies)
        return wizard.run(opts or DownloadOptions(urls=[URL]))

    return _walk


def test_defaults_for_a_video(walk: Any) -> None:
    opts = walk(video_probe(), "", "", "", "", "", "")
    assert opts.audio_only is False
    assert opts.quality == "1080"
    assert opts.video_format == "mp4"
    assert opts.cookies_browser is None
    assert opts.playlist_mode == "no"


def test_every_choice_is_applied(walk: Any, tmp_path: Path) -> None:
    opts = walk(video_probe(), "", "1", "2", "2", str(tmp_path), FIREFOX)
    assert opts.quality == "720"
    assert opts.video_format == "mkv"
    assert opts.output == tmp_path
    assert opts.cookies_browser == "firefox"


def test_audio_branch_skips_quality_and_container(walk: Any) -> None:
    opts = walk(video_probe(), "", "2", "2", "", "")
    assert opts.audio_only is True
    assert opts.audio_format == "m4a"


def test_back_returns_to_previous_step(walk: Any) -> None:
    # Video, then back from quality, switch to audio.
    opts = walk(video_probe(), "", "1", "b", "2", "", "", "")
    assert opts.audio_only is True


def test_back_on_first_step_prompts_again(walk: Any) -> None:
    opts = walk(video_probe(), "", "b", "", "", "", "", "")
    assert opts is not None


def test_back_skips_steps_that_did_not_run(walk: Any) -> None:
    # Audio: back from the folder lands on the format, not on the container.
    opts = walk(video_probe(), "", "2", "1", "b", "3", "", "")
    assert opts.audio_format == "opus"


def test_invalid_choice_asks_again(walk: Any) -> None:
    opts = walk(video_probe(), "", "9", "2", "", "", "")
    assert opts.audio_only is True


@pytest.mark.parametrize("position", range(1, 6))
def test_cancel_at_any_step(walk: Any, position: int) -> None:
    assert walk(video_probe(), *[""] * position, "c") is None


def test_eof_cancels(walk: Any) -> None:
    assert walk(video_probe(), "", EOFError) is None


def test_eof_in_text_prompt_cancels(walk: Any) -> None:
    assert walk(video_probe(), EOFError) is None


def test_empty_url_aborts(walk: Any) -> None:
    with pytest.raises(SystemExit):
        walk(video_probe(), "", opts=DownloadOptions(urls=[]))


def test_url_can_be_typed(walk: Any) -> None:
    opts = walk(video_probe(), "http://typed", "", "", "", "", "", opts=DownloadOptions(urls=[]))
    assert opts.urls == ["http://typed"]


def test_clipboard_url_is_offered(walk: Any, monkeypatch: pytest.MonkeyPatch, answers: Any) -> None:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: "http://clip")
    monkeypatch.setattr(wizard, "probe", lambda url, cookies: video_probe(url))
    answers("", "", "", "", "", "")
    opts = wizard.run(DownloadOptions(urls=[]))
    assert opts is not None
    assert opts.urls == ["http://clip"]


def test_clipboard_url_can_be_refused(
    walk: Any, monkeypatch: pytest.MonkeyPatch, answers: Any
) -> None:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: "http://clip")
    monkeypatch.setattr(wizard, "probe", lambda url, cookies: video_probe(url))
    answers("n", "http://typed", "", "", "", "", "")
    opts = wizard.run(DownloadOptions(urls=[]))
    assert opts is not None
    assert opts.urls == ["http://typed"]


def test_clipboard_prompt_eof_cancels(
    walk: Any, monkeypatch: pytest.MonkeyPatch, answers: Any
) -> None:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: "http://clip")
    answers(EOFError)
    assert wizard.run(DownloadOptions(urls=[])) is None


def test_cookie_hungry_site_asks_before_probing(walk: Any) -> None:
    site = DownloadOptions(urls=["https://www.instagram.com/p/x/"])
    opts = walk(video_probe(), "", BRAVE, "", "", "", "", opts=site)
    assert opts.cookies_browser == "brave"


def test_back_from_first_cookies_prompt_restarts_url(walk: Any) -> None:
    site = DownloadOptions(urls=["https://www.instagram.com/p/x/"])
    opts = walk(video_probe(), "", "b", "", "1", "", "", "", "", opts=site)
    assert opts is not None


def test_cancel_at_first_cookies_prompt(walk: Any) -> None:
    site = DownloadOptions(urls=["https://www.instagram.com/p/x/"])
    assert walk(video_probe(), "", "c", opts=site) is None


def test_cancel_after_back_from_first_cookies_prompt(walk: Any) -> None:
    site = DownloadOptions(urls=["https://www.instagram.com/p/x/"])
    assert walk(video_probe(), "", "b", "c", opts=site) is None


def test_auth_failure_asks_cookies_and_probes_again(walk: Any) -> None:
    denied = login_wall()
    opts = walk([denied, video_probe()], "", BRAVE, "", "", "", "")
    assert opts.cookies_browser == "brave"
    assert opts.playlist_mode == "no"


def test_cancel_at_auth_retry(walk: Any) -> None:
    denied = login_wall()
    assert walk(denied, "", "c") is None


def test_declining_cookies_at_auth_retry_goes_manual(walk: Any) -> None:
    denied = login_wall()
    opts = walk(denied, "", "1", "", "", "", "")
    assert opts.cookies_browser is None
    assert opts.playlist_mode == "auto"


def test_failed_probe_falls_back_to_manual_flow(walk: Any, capsys: Any) -> None:
    failed = Probe(
        url=URL, site="generic", content_kind=ContentKind.UNKNOWN, failed=True, error_message="boom"
    )
    opts = walk(failed, "", "", "", "", "", "")
    assert opts.playlist_mode == "auto"
    assert t("probe_failed", "boom") in capsys.readouterr().out


def test_single_image_cancels(walk: Any) -> None:
    image = Probe(url=URL, site="generic", content_kind=ContentKind.SINGLE_IMAGE)
    assert walk(image, "") is None


def test_single_audio_skips_type_step(walk: Any) -> None:
    audio = Probe(url=URL, site="generic", content_kind=ContentKind.SINGLE_AUDIO)
    opts = walk(audio, "", "", "", "")
    assert opts.audio_only is True


def test_detection_banner_shows_metadata(walk: Any, capsys: Any) -> None:
    rich = Probe(
        url=URL,
        site="generic",
        content_kind=ContentKind.SINGLE_VIDEO,
        title="Clip",
        uploader="someone",
        duration=65,
        filesize=2048,
    )
    walk(rich, "", "", "", "", "", "")
    out = capsys.readouterr().out
    assert "Clip" in out
    assert "someone" in out


def test_playlist_with_nothing_downloadable_cancels(walk: Any) -> None:
    images = [ProbeItem(1, ContentKind.SINGLE_IMAGE, False, "(image #1)")]
    assert walk(carousel(images), "") is None


def test_a_refused_playlist_still_says_what_failed(walk: Any, capsys: Any) -> None:
    refused = carousel([ProbeItem(1, ContentKind.UNKNOWN, False, "(item #1)")])
    refused.error_message = "HTTP Error 500"
    assert walk(refused, "") is None
    out = capsys.readouterr().out
    assert t("probe_partial", "HTTP Error 500") in out
    assert t("no_supported_items") in out


def test_playlist_of_only_unknown_items_is_offered(walk: Any) -> None:
    items = [
        ProbeItem(1, ContentKind.UNKNOWN, True, "a.jpg"),
        ProbeItem(2, ContentKind.UNKNOWN, True, "b.jpg"),
    ]
    opts = walk(carousel(items), "", "", "", "", "", "", "")
    assert opts.playlist_items == "1,2"


def test_playlist_of_unsupported_unknown_items_is_refused(
    walk: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert walk(carousel([ProbeItem(1, ContentKind.UNKNOWN, False, "a")]), "") is None
    assert t("no_supported_items") in capsys.readouterr().out


def test_one_unknown_item_among_images_is_enough_to_offer_the_playlist(walk: Any) -> None:
    items = [
        ProbeItem(1, ContentKind.SINGLE_IMAGE, False, "(image #1)"),
        ProbeItem(2, ContentKind.UNKNOWN, True, "a.jpg"),
    ]
    opts = walk(carousel(items), "", "", "", "", "", "", "")
    assert opts.playlist_items == "2"
    assert opts.playlist_mode == "yes"


MIXED = [
    ProbeItem(1, ContentKind.SINGLE_IMAGE, False, "(image #1)"),
    ProbeItem(2, ContentKind.UNKNOWN, True, "a.jpg"),
    ProbeItem(3, ContentKind.SINGLE_VIDEO, True, "Clip"),
    ProbeItem(4, ContentKind.SINGLE_AUDIO, True, "Track"),
]


@pytest.mark.parametrize(
    "items,selected",
    [(MIXED, "3"), ([MIXED[0], MIXED[1], MIXED[3]], "2,4"), ([MIXED[3]], "4")],
    ids=["videos-win", "fallback-keeps-audio-and-unknown", "audio-alone"],
)
def test_enter_on_mixed_kinds(walk: Any, items: list[ProbeItem], selected: str) -> None:
    opts = walk(carousel(items), "", "", "", "", "", "", "")
    assert opts.playlist_items == selected


def test_summary_and_preselection_on_a_playlist_of_unknown_items(
    walk: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [
        ProbeItem(1, ContentKind.UNKNOWN, True, "a"),
        ProbeItem(2, ContentKind.UNKNOWN, True, "b"),
    ]
    # URL, item selection, type (preselected), format, folder, cookies.
    opts = walk(
        carousel(items), "", "", "", "", "", "", opts=DownloadOptions(urls=[URL], audio_only=True)
    )
    assert opts.audio_only is True
    assert "1,2" in capsys.readouterr().out


def test_carousel_default_selects_videos(walk: Any) -> None:
    opts = walk(carousel(), "", "", "", "", "", "", "")
    assert opts.playlist_mode == "yes"
    assert opts.playlist_items == "2"


def test_carousel_without_videos_defaults_to_supported_items(walk: Any) -> None:
    items = [
        ProbeItem(1, ContentKind.SINGLE_AUDIO, True, "A"),
        ProbeItem(2, ContentKind.SINGLE_IMAGE, False, "(image #2)"),
    ]
    opts = walk(carousel(items), "", "", "", "", "", "", "")
    assert opts.playlist_items == "1"


def test_carousel_all_keyword(walk: Any) -> None:
    opts = walk(carousel(), "", "all", "", "", "", "", "")
    assert opts.playlist_items == ""


def test_carousel_explicit_selection(walk: Any) -> None:
    opts = walk(carousel(), "", "2-3", "", "", "", "", "")
    assert opts.playlist_items == "2,3"


def test_carousel_invalid_selection_asks_again(walk: Any) -> None:
    opts = walk(carousel(), "", "99", "2", "", "", "", "", "")
    assert opts.playlist_items == "2"


def test_carousel_drops_unsupported_items_from_selection(walk: Any, capsys: Any) -> None:
    opts = walk(carousel(), "", "1,2", "", "", "", "", "")
    assert opts.playlist_items == "2"
    assert t("selection_warning_unsupported", "1") in capsys.readouterr().out


def test_carousel_selection_of_only_unsupported_asks_again(walk: Any) -> None:
    opts = walk(carousel(), "", "1", "2", "", "", "", "", "")
    assert opts.playlist_items == "2"


def test_carousel_cancel_and_eof(walk: Any) -> None:
    assert walk(carousel(), "", "c") is None
    assert walk(carousel(), "", EOFError) is None


def test_carousel_back_on_first_step_prompts_again(walk: Any) -> None:
    opts = walk(carousel(), "", "b", "2", "", "", "", "", "")
    assert opts.playlist_items == "2"


def test_explicit_keyring_survives_the_cookies_step(walk: Any) -> None:
    preset = DownloadOptions(urls=[URL], cookies_browser="brave+kwallet6")
    opts = walk(video_probe(), "", "", "", "", "", "", opts=preset)
    assert opts.cookies_browser == "brave+kwallet6"


def test_picking_another_browser_replaces_the_spec(walk: Any) -> None:
    preset = DownloadOptions(urls=[URL], cookies_browser="brave+kwallet6")
    opts = walk(video_probe(), "", "", "", "", "", FIREFOX, opts=preset)
    assert opts.cookies_browser == "firefox"


def test_no_cookies_clears_the_spec(walk: Any) -> None:
    preset = DownloadOptions(urls=[URL], cookies_browser="brave+kwallet6")
    opts = walk(video_probe(), "", "", "", "", "", "1", opts=preset)
    assert opts.cookies_browser is None


def test_preselected_flags_become_defaults(walk: Any, tmp_path: Path) -> None:
    preset = DownloadOptions(
        urls=[URL], audio_only=True, audio_format="wav", output=tmp_path, cookies_browser="firefox"
    )
    opts = walk(video_probe(), "", "", "", "", "", opts=preset)
    assert opts.audio_only is True
    assert opts.audio_format == "wav"
    assert opts.output == tmp_path
    assert opts.cookies_browser == "firefox"


def test_unknown_step_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown step"):
        wizard._run_step("nope", DownloadOptions(urls=[URL]), [])


# Several URLs: one walk each (spec 19).
OTHER = "https://site.test/other"
IMAGE = "https://site.test/image"


@pytest.fixture
def walk_all(
    monkeypatch: pytest.MonkeyPatch, answers: Any
) -> Callable[..., tuple[list[tuple[str, DownloadOptions | None]] | None, list[str]]]:
    monkeypatch.setattr(wizard, "read_clipboard", lambda: None)

    def probe_for(url: str, cookies: str | None) -> Probe:
        if url == IMAGE:
            return Probe(url=url, site="generic", content_kind=ContentKind.SINGLE_IMAGE)
        return video_probe(url)

    monkeypatch.setattr(wizard, "probe", probe_for)

    def _walk_all(
        urls: list[str], *replies: Any
    ) -> tuple[list[tuple[str, DownloadOptions | None]] | None, list[str]]:
        prompts = answers(*replies)
        return wizard.run_all(DownloadOptions(urls=urls)), prompts

    return _walk_all


def test_run_all_walks_each_url(walk_all: Any) -> None:
    walked, prompts = walk_all([URL, OTHER], *[""] * 6, *[""] * 6)
    assert len(prompts) == 12
    assert [url for url, _ in walked] == [URL, OTHER]
    assert [opts.urls for _, opts in walked] == [[URL], [OTHER]]


def test_run_all_starts_each_walk_from_the_command_line(walk_all: Any) -> None:
    # The first walk picks audio: URL, type, format, folder, cookies.
    walked, _ = walk_all([URL, OTHER], "", "2", "", "", "", *[""] * 6)
    assert [opts.audio_only for _, opts in walked] == [True, False]


def test_run_all_records_a_refusal_and_goes_on(walk_all: Any) -> None:
    walked, prompts = walk_all([IMAGE, OTHER], "", *[""] * 6)
    assert len(prompts) == 7
    assert walked[0] == (IMAGE, None)
    assert walked[1][0] == OTHER
    assert walked[1][1] is not None


@pytest.mark.parametrize("stop", ["c", EOFError])
def test_run_all_cancel_in_a_later_walk_cancels_everything(walk_all: Any, stop: Any) -> None:
    walked, _ = walk_all([URL, OTHER], *[""] * 6, "", stop)
    assert walked is None


def test_run_all_splits_urls_typed_together(walk_all: Any) -> None:
    # The first walk asks for the URL, the second starts at its type step.
    walked, prompts = walk_all([], f"{URL}  {OTHER}", *[""] * 5, *[""] * 5)
    assert len(prompts) == 11
    assert [url for url, _ in walked] == [URL, OTHER]
    assert sum(t("ask_url") in prompt for prompt in prompts) == 1


def test_run_all_splits_urls_typed_after_back_from_the_first_cookies_prompt(
    walk_all: Any,
) -> None:
    hungry = "https://www.instagram.com/p/x/"
    # URL, back from cookies, URL again, cookies, type, quality, container, folder.
    first = ("", "b", f"{hungry} {OTHER}", "1", "", "", "", "")
    walked, prompts = walk_all([hungry], *first, *[""] * 5)
    assert len(prompts) == 13
    assert [url for url, _ in walked] == [hungry, OTHER]
    assert [opts.urls for _, opts in walked] == [[hungry], [OTHER]]
