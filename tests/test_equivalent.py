"""Tests for the equivalent-command builder."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest

from download_media.cli import build_parser, options_from_args, should_run_wizard
from download_media.config import DownloadOptions
from download_media.equivalent import build


def parse_back(cmd: str) -> DownloadOptions:
    argv = shlex.split(cmd)
    assert argv[0] == "download-media"
    args = build_parser().parse_args(argv[1:])
    assert should_run_wizard(args, argv[1:]) is False
    return options_from_args(args)


def test_minimal_video_stays_direct() -> None:
    cmd = build(DownloadOptions(urls=["https://example.com/v"]))
    assert cmd == "download-media -y https://example.com/v"


def test_audio_only_with_default_format() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], audio_only=True))
    assert cmd == "download-media -y -a https://x"


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


def test_subs_with_custom_langs_stay_enabled() -> None:
    opts = parse_back(build(DownloadOptions(urls=["https://x"], subs=True, sub_langs="fr,de")))
    assert opts.subs is True
    assert opts.sub_langs == "fr,de"


def test_sub_langs_with_a_space_stay_one_value() -> None:
    opts = parse_back(build(DownloadOptions(urls=["https://x"], subs=True, sub_langs="fr, de")))
    assert opts.urls == ["https://x"]
    assert opts.sub_langs == "fr, de"


def test_sub_langs_without_subs_are_not_emitted() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], subs=False, sub_langs="fr,de"))
    assert "--sub-langs" not in cmd


def test_playlist_modes_emit_short_flags() -> None:
    yes = build(DownloadOptions(urls=["https://x"], playlist_mode="yes"))
    no = build(DownloadOptions(urls=["https://x/watch?v=1&list=2"], playlist_mode="no"))
    assert " -p " in yes or yes.endswith(" -p")
    assert " -P " in no


def test_selected_items_replace_the_playlist_flag() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="yes", playlist_items="2,4"))
    assert cmd == "download-media -y --playlist-items 2,4 https://x"
    opts = parse_back(cmd)
    assert opts.playlist_mode == "yes"
    assert opts.playlist_items == "2,4"


@pytest.mark.parametrize("items", [None, ""])
def test_no_selection_means_the_whole_playlist(items: str | None) -> None:
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="yes", playlist_items=items))
    assert cmd == "download-media -y -p https://x"


def test_free_text_items_stay_one_value() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="yes", playlist_items="1, 3"))
    opts = parse_back(cmd)
    assert opts.urls == ["https://x"]
    assert opts.playlist_items == "1, 3"


@pytest.mark.parametrize("items", ["1:3", "::2", "1-3,5", "2,4"])
def test_item_specs_need_no_quotes_and_stay_intact(items: str) -> None:
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="yes", playlist_items=items))
    assert parse_back(cmd).playlist_items == items


def test_shell_metacharacters_in_items_stay_one_value() -> None:
    items = "1;rm $x'"
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="yes", playlist_items=items))
    opts = parse_back(cmd)
    assert opts.urls == ["https://x"]
    assert opts.playlist_items == items


def test_empty_items_on_the_command_line_mean_the_whole_playlist() -> None:
    args = build_parser().parse_args(["--playlist-items", "", "https://x"])
    assert build(options_from_args(args)) == "download-media -y -p https://x"


@pytest.mark.parametrize(
    "fields",
    [
        {"playlist_mode": "yes", "playlist_items": "-2:"},
        {"playlist_mode": "yes", "playlist_items": "-5::2"},
        {"subs": True, "sub_langs": "-live_chat"},
    ],
    ids=["items-tail", "items-step", "sub-langs"],
)
def test_values_starting_with_a_dash_can_be_parsed_back(fields: dict[str, Any]) -> None:
    argv = shlex.split(build(DownloadOptions(urls=["https://x"], **fields)))[1:]
    parser = build_parser()
    # What argparse uses before 3.14, and CI runs 3.12.
    parser._negative_number_matcher = re.compile(r"^-\d+$|^-\d*\.\d+$")
    opts = options_from_args(parser.parse_args(argv))
    assert all(getattr(opts, name) == value for name, value in fields.items())


@pytest.mark.parametrize(
    ("fields", "printed"),
    [
        ({"subs": True, "sub_langs": "-x, y"}, "-s --sub-langs='-x, y'"),
        ({"subs": True, "sub_langs": "-live_chat"}, "-s --sub-langs=-live_chat"),
        ({"playlist_mode": "yes", "playlist_items": "-2:"}, "--playlist-items=-2:"),
        ({"subs": True, "sub_langs": "fr, de"}, "-s --sub-langs 'fr, de'"),
        ({"playlist_mode": "yes", "playlist_items": "1:3"}, "--playlist-items 1:3"),
    ],
    ids=["dash-quoted", "dash-langs", "dash-items", "plain-langs", "plain-items"],
)
def test_only_values_starting_with_a_dash_use_the_equals_form(
    fields: dict[str, Any], printed: str
) -> None:
    cmd = build(DownloadOptions(urls=["https://x"], **fields))
    assert cmd == f"download-media -y {printed} https://x"


@pytest.mark.parametrize("value", ["-y", "-s", "--help", "-x=y", "-a b", "-;rm $x'"])
def test_a_value_that_reads_like_a_flag_stays_a_value(value: str) -> None:
    cmd = build(DownloadOptions(urls=["https://x"], subs=True, sub_langs=value))
    opts = parse_back(cmd)
    assert opts.urls == ["https://x"]
    assert opts.sub_langs == value


def test_items_are_ignored_outside_playlist_mode() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], playlist_mode="no", playlist_items="2"))
    assert "--playlist-items" not in cmd


def test_no_playlist_flag_omitted_for_plain_urls() -> None:
    cmd = build(DownloadOptions(urls=["https://youtube.com/watch?v=1"], playlist_mode="no"))
    assert "-P" not in cmd


def test_no_playlist_flag_kept_for_instagram_posts() -> None:
    # Could be a carousel: -P records that this run took it as a single item.
    cmd = build(DownloadOptions(urls=["https://www.instagram.com/p/X/"], playlist_mode="no"))
    assert " -P " in cmd


def test_urls_are_quoted_when_special_chars_present() -> None:
    cmd = build(DownloadOptions(urls=["https://x?v=1&t=2"]))
    # shlex.quote wraps URL in single quotes due to ? and &
    assert "'https://x?v=1&t=2'" in cmd


def test_shell_metacharacters_round_trip() -> None:
    url = "https://x/a b?c=1&d='e'$f"
    folder = Path("/tmp/it's a $dir & more")
    opts = parse_back(build(DownloadOptions(urls=[url], output=folder)))
    assert opts.urls == [url]
    assert opts.output == folder


def test_custom_output_path_emitted() -> None:
    cmd = build(DownloadOptions(urls=["https://x"], output=Path("/tmp/abc")))
    assert "-o" in cmd
    assert "/tmp/abc" in cmd


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing: build() never emits '--', so a URL that starts with '-' is "
    "printed as a flag and the command no longer repeats the run",
)
@pytest.mark.parametrize("url", ["-a", "-x"])
def test_a_url_that_starts_with_a_dash_round_trips(url: str) -> None:
    # `download-media -y -- -a` reaches the download (spec 22 sends `-- -a` to the wizard).
    cmd = build(DownloadOptions(urls=[url]))
    try:
        assert parse_back(cmd).urls == [url], cmd
    except SystemExit:
        pytest.fail(f"usage error: {cmd}")
