# 22 — Intent flags in any spelling, and `-l` for every URL

Status: done
Item: `fix/intent-flags`

> **For the human's review (0.4.0 list): D1, D2 and D3 change published behaviour.** The roles decided them under the overnight delegation, each on the recommended option. None changes a default or renames a flag, so none is breaking under `AGENTS.md`. What changes: `-l <url>` lists at once instead of walking the wizard first, and it now lists every URL instead of only the first (D1, D4). Intent flags count in any spelling argparse accepts: bundled (`-as`), with the value attached (`-q720`), abbreviated (`--aud`). Those runs now skip the wizard, as `AGENTS.md` and the README already say they do (D2). `-l` with no URL, or with `-i`, only asks for the URLs (D3). What a user loses: `-l` on a login wall no longer offers cookies along the way. It fails with the existing `--cookies` hint, as `-y -l` always has. See Cases.

## Problem

`-l <url>`, `-q720 <url>`, `-as <url>` and `--aud <url>` all open the wizard, although each passes an intent flag. `-l` lists only the first URL, silently, in every mode.

Reproduced on `main` at `e447bea` (after the `fix/subs-failures` merge), with yt-dlp 2026.10.26 and Python 3.14.9. Two harnesses were used. The console script `.venv/bin/download-media` ran with stdin at `/dev/null`, against a loopback `http.server` serving two 1 s H.264/AAC clips (`clip.mp4`, `second.mp4`). `HOME` and every `XDG_*` folder were fresh in a scratch folder, with a dead D-Bus address and no display. The second harness was a scratch pytest file that imports `tests/test_e2e.py`'s fixtures (`wizard_urls`, `home`, the `fake_site` extractor). It feeds blank replies to every prompt, counts the prompts, and records which URLs `FakeSiteIE._real_extract` saw.

| #   | Command                                                    | What happened                                                                                                                                                                         |
| --- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | `download-media -l <clip>` (console, stdin `/dev/null`)     | `Download wizard`, `Video URL [<clip>] (c=cancel): Cancelled.`, exit 1. Same for `-q720 <clip>`, `-as <clip>` and `--aud <clip>`                                                        |
| R2  | `download-media -y -l <clip> <second>` (console)            | `[generic] Extracting URL: <clip>`, its format table, exit 0. The server log has 1 request for `clip.mp4` and 0 for `second.mp4`. Nothing says the second URL was dropped               |
| R3  | `-l video` (pytest)                                         | 6 prompts (URL, type, quality, container, folder, cookies), then the table, exit 0. `--list` gives the same                                                                         |
| R4  | `-q720`, `--qual 720`, `--qual=720`, `--sub-l en`, `--no-p`, `--playlist-i 1`, `--video-f mkv` (pytest) | 6 prompts each: the wizard. `-q 720` with a space gives 1 prompt, the direct-mode preview                                                    |
| R5  | `-as`, `-sa`, `--aud` (pytest)                              | 5 prompts each: the wizard, audio preselected                                                                                                                                         |
| R6  | `-y -l video video-b`, `-a -l video video-b` (pytest)       | 0 prompts, one table, `video-b` never extracted, exit 0                                                                                                                               |
| R7  | `-l video video-b` (pytest)                                 | 6 prompts, one table, `video-b` never extracted (pinned by `test_list_formats_still_walks_and_lists_only_the_first_url`, `test_e2e.py:6107`)                                           |
| R8  | `-i -l`, `video video-b` typed at the URL prompt (pytest)   | 6 prompts, one table, `video-b` never extracted                                                                                                                                       |
| R9  | `-i -l https://x.com/u/status/1`, Enter, `b` at the cookies prompt, `video video-b` typed (spec 06's path) | 8 prompts, one table, `video-b` never extracted                                                                                         |
| R10 | `should_run_wizard` on `-- -a`, and on `-- --cookies`       | `False`: direct mode, with the URL `-a` or `--cookies`. Tokens after `--` are positionals for argparse, but the scan reads them as flags                                              |

The cause is `has_intent_flags` (`cli.py:204-214`). It compares raw `argv` tokens with the option strings in `_INTENT_FLAGS` (`cli.py:56-77`), plus a `--flag=value` split. Argparse also accepts bundled short flags (`-as`), a short flag with its value attached (`-q720`), and unique prefixes of long options (`allow_abbrev` defaults to true, and `build_parser` keeps it). The scan sees none of these. `-l`/`--list` isn't in `_INTENT_FLAGS` at all. Both `-l` branches in `_main` read only `opts.urls[0]`: `wizard.run` cuts the list to one URL (`wizard.py:506`), and the direct branch calls `_list_formats(opts.urls[0], opts)` (`cli.py:596-597`).

`README.md:82` lists `download-media -l <url>` under "Direct mode (any intent flag skips the wizard)", and `README.md:72` heads that list. Specs 09 (`:72-73`), 17, 18, 19 (D7, F8), 23 and 27 all handed both halves of this to row 22.

**What should happen:** any option in the intent list skips the wizard, however argparse let the user spell it. `-l` is one of them, and it lists every URL it was given.

## Goal

Whether a run opens the wizard depends on which options argparse parsed, not on how they were typed. `-l` lists the formats of every URL, in order, and exits 0 only when all of them were listed.

## Scope

### In

- `-l`/`--list` becomes an intent flag (D1).
- The intent check reads which options argparse parsed, through a second parse into a namespace that marks what was set (D2). Bundles, attached values and abbreviations count, and tokens after `--` don't.
- With `-l` and no URL, or with `-i`, the wizard only asks for the URLs. Type, quality, container, folder and cookies are never asked, because listing uses none of them (D3).
- `-l` lists every URL in order, goes on after a failed one, and exits 1 if any failed (D4).
- `AGENTS.md`: the intent list, and a paragraph on `-l`.

### Out

- **Cookies on a login wall during `-l`.** After D1 and D3, `-l` never probes before listing, so nothing offers cookies. A gated URL fails with `Download failed: <url>` and the existing `→ try again with --cookies <browser>` hint, as `-y -l` does today. Direct-mode downloads behave the same way. Today's `-l` reached the wizard's cookie retry only because it walked the whole wizard (`test_listing_formats_goes_through_the_cookie_retry`, `test_e2e.py:5444`). A retry for `-l` would be a new feature. Notes suggest it as a roadmap row if the human wants it.
- **A lone image under `-l`.** Today the wizard refuses it (`image_only_skipped`, exit 1). After D1, `-l <image>` does what `-y -l <image>` does on the same commit: yt-dlp's `No video formats found!`, exit 1, until spec 61 (D4) lands, and then `item_image_skipped`, exit 0. Spec 61 owns that wording. This spec only requires `-l` and `-y -l` to agree.
- **The cookie failure repeated per URL.** `_list_formats` drops an unreadable browser for its own URL only (`cli.py:497-500`), so with several URLs the report repeats. Downloads do the same (`_download_with_cookie_fallback`, `cli.py:446-452`). Changing that belongs with both, not here.
- **`-l` with `-p`, `-P` or `--playlist-items`.** `list_formats` reads only `cookies_browser` (`downloader.py:855-866`), so a playlist lists every entry whatever the playlist flags say. That's unchanged.
- **`wizard.run`.** After this change no code in `src/` calls it. It stays, because `tests/test_wizard_flow.py` drives `_walk_url` through it in eight places (`:60`, `:149`, `:160`, `:170`, `:259`, `:412`, `:434`, `:459`). Removing it would mean rewriting that harness, which is a separate item (see notes).
- **`--help` wording for `-l`** ("List available formats and exit."). It stays: `--help` is hardcoded English until row 02, and it's still true.
- **`allow_abbrev`.** It stays on. Turning it off would reject `--aud`, which parses today, and that is a breaking change.

## Files

| Action | File                            | What                                                                                                                                                                                                                                                                              |
| ------ | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| modify | `src/download_media/cli.py`     | `_INTENT_FLAGS` (`:55-77`) becomes `_INTENT_DESTS`. The body of `has_intent_flags` (`:204-214`). `_main` (`:563-623`): one `-l` dispatch after `options_from_args` (`:566`); the `-l` branches at `:571-577` and `:596-597` go; the clipboard lines (`:587-594`) move into a helper. A new `_list_every_url` after `_list_formats` (`:493-508`) |
| modify | `src/download_media/wizard.py`  | a new `ask_urls` between `run` (`:499-510`) and `run_all` (`:513`)                                                                                                                                                                                                                    |
| modify | `AGENTS.md`                     | the intent list in the Wizard bullet (`:99-100`), and a new `-l` paragraph between the equivalent-command paragraph and "Exit status" (after `:137`)                                                                                                                                  |
| modify | `tests/test_wizard_dispatch.py` | the unit criteria                                                                                                                                                                                                                                                                 |
| modify | `tests/test_e2e.py`             | the e2e criteria. Six tests that pin the old `-l` wizard change or go: `:2037`, `:5263`, `:5444`, `:6107`, `:10461`, `:10469` (see Acceptance criteria)                                                                                                                              |

No change to `i18n.py`, `README.md` or `docs/index.html`. `README.md:72` and `:82` become true as they stand, and the landing page doesn't mention `-l`.

### Collisions with items in flight

- **`feature/ig-lone-images` (approved).** It touches `cli.py` `_show_preview` (`:235-…`), the warning block in `_download_one` and `_hint_for_download_error` (`:473-482`), none of which this item edits. `_list_every_url` goes after `_list_formats` (`:493-508`), with `_list_formats` itself between it and `_hint_for_download_error`, so no hunk touches the other's lines. In `wizard.py` it edits `:386-400` and `:591-623`, and this item adds lines near `:511`. In `AGENTS.md` it edits the "Multi-entry URLs" paragraph (`:121-129`), and this item edits `:99-100` and adds after `:137`. **Tests:** it renames `IMAGE` (`test_e2e.py:5893`) to `image-format`, and this item deletes `test_a_lone_image_refused_by_the_list_formats_wizard_exits_1` (`:10469`), which uses `IMAGE`. If 61 also edits that test, whichever item lands second resolves the conflict by deleting it.
- **`fix/picker-honesty` (awaiting the human).** It touches `_download_one`'s nothing-saved branch in `cli.py`, `wizard.py` `step_type`, `step_select`, `_walk_plan` and `_walk_url` (`:558-645`), and the `-i` bullet of `AGENTS.md` (`:117-118`). None of those overlap. This item leaves the `-i` bullet alone on purpose: an edit at `:114-116` would sit right next to theirs and conflict.
- **`fix/subs-failures`** is already on `main` (`e447bea`). The line numbers above are from after that merge.

## Design

### Which options were given (D2)

`_INTENT_DESTS` replaces `_INTENT_FLAGS`. It is a tuple of `dest` names, in the order `build_parser` adds them:

```python
_INTENT_DESTS = (
    "audio", "quality", "audio_format", "video_format", "output", "list_formats",
    "playlist", "no_playlist", "playlist_items", "subs", "sub_langs", "cookies_browser",
)
```

`has_intent_flags(argv)` keeps its name and signature, so `should_run_wizard(args, argv)` and its callers in the tests stay as they are. It parses `argv` again with `build_parser()`, into a namespace whose intent attributes are already set to a private sentinel. Any attribute that is no longer the sentinel was set by an option on the command line:

```python
_UNSET = object()

def has_intent_flags(argv: list[str]) -> bool:
    """True if any "I know what I want" option was given, in any form argparse accepts."""
    given = argparse.Namespace(**dict.fromkeys(_INTENT_DESTS, _UNSET))
    build_parser().parse_args(argv, namespace=given)
    return any(getattr(given, dest) is not _UNSET for dest in _INTENT_DESTS)
```

This works because of how argparse fills defaults, checked in `/usr/lib/python3.14/argparse.py` and in CPython 3.10's `Lib/argparse.py`. `parse_known_args` sets a default only `if not hasattr(namespace, action.dest)` (`:2032-2042` on 3.14). The late conversion of string defaults runs only when the namespace value `is` the action's default (`:2336-2341`), so the sentinel never reaches a `type=` function. Only `parse_args(args, namespace)` is used, which is public API. Rejected: `set_defaults(**{dest: SUPPRESS})`. It also writes the literal `'==SUPPRESS=='` into the namespace through the `self._defaults` loop (`:2040-2042`), and then the late conversion feeds that string to `type=parse_spec` and `type=Path`.

The second parse can't fail where the first one passed, because it's the same parser on the same `argv`, and `_main` only calls `should_run_wizard` after the first parse. `parse_spec` and `Path` are pure, so running them twice changes nothing. The scratch prototype checks this on the 29 command lines below: a fresh parse after the sentinel parse returns the same `vars()` as one before it.

Measured with that prototype on `e447bea`:

| `argv`                                                  | given                     |
| ------------------------------------------------------- | ------------------------- |
| `u`, `-i u`, `--inter u`, `-y u`, `-iy u`, no arguments | none                      |
| `-l u`, `--list u`, `-l` (no URL)                       | `list_formats`            |
| `-q720 u`, `-q 1080 u`, `--qual 720 u`, `--qual=720 u`  | `quality`                 |
| `-as u`, `-sa u`                                        | `audio`, `subs`           |
| `-ao/tmp/x u`                                           | `audio`, `output`         |
| `--aud u`, `u -a`                                       | `audio`                   |
| `--sub-l en u`, `--sub-langs=-q u`                      | `sub_langs`               |
| `--no-p u` / `--playlist-i 1 u` / `--video-f mkv u` / `--cook firefox u`, `--cookies=brave u` / `-f mp3 u` | `no_playlist` / `playlist_items` / `video_format` / `cookies_browser` / `audio_format` |
| `-- -a`, `-- --cookies`                                 | none (the URL is `-a`, `--cookies`) |
| `-yl a b`                                               | `list_formats`            |

`-q 1080`, `-f mp3` and `--video-format mp4` repeat a default and still count. That's why a comparison with the defaults was rejected (D2 B).

### `-l` (D1, D3, D4)

`_main` sends `-l` to its own path right after `options_from_args`, before the wizard or direct-mode code:

```python
if opts.list_formats:
    return _list_every_url(opts, ask=should_run_wizard(args, raw_argv))
```

`should_run_wizard` is unchanged. With `-l` now an intent flag, it returns true in only two cases: `-i` without `-y`, or no URL without `-y`.

`_list_every_url(opts, *, ask: bool) -> int`:

1. If `ask`: `urls = wizard.ask_urls(opts)`. `None` means cancelled: `warn(t("cancelled"))`, `return 1`, the same line and code as a cancelled wizard today. Otherwise set `opts.urls = urls`.
2. Otherwise, the direct-mode clipboard fallback. The lines at `cli.py:587-594` (clipboard once, `from_clipboard`, then `die(t("url_required"))`) move into a helper `_url_or_clipboard(opts) -> None`, which the direct-mode download path calls as well, with no change in behaviour.
3. `codes = [_list_formats(url, opts) for url in opts.urls]`, one call per URL in the given order. Each call keeps its own cookie fallback and its own `failed` line. `return 0 if not any(codes) else 1`.

A Ctrl-C while listing reaches `main`'s handler as today: `Cancelled.`, return 130, and death by SIGINT. Later URLs don't run, as with downloads. `_list_formats` only catches `Exception`, which a `KeyboardInterrupt` isn't.

No per-URL header (D5). yt-dlp already prints `[<extractor>] Extracting URL: <url>` before each extraction, at normal verbosity (`extractor/common.py:762`, truncated past 100 characters), and that line names the URL each table belongs to.

`wizard.ask_urls(opts: DownloadOptions) -> list[str] | None`, placed after `run`:

```python
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
```

This is `run_all`'s URL phase without the walk. Each command-line URL gets its own prompt, preset to it, with the `(k/n)` counter. Several URLs typed at one prompt all count (`step_url` splits on whitespace), and so does the clipboard offer when nothing was given (`step_url`, `wizard.py:175-185`). An empty answer with no preset still ends in `die(t("empty_url"))`, exit 1, as today. `opts` itself is never mutated.

### Messages

None new. The new path uses `wizard_title`, `ask_url`, `ask_use_clip`, `from_clipboard`, `cancelled`, `failed`, `url_required` and `empty_url`, all already in both blocks.

### `AGENTS.md`

- `:99-100`: the list becomes `` (`-a`, `-q`, `-f`, `--video-format`, `-o`, `-l`, `-s`, `--sub-langs`, `-p`, `-P`, `--playlist-items`, `--cookies`, in any form argparse accepts: `-as`, `-q720`, `--aud`) ``. The sentence after it is unchanged.
- After `:137`, a new paragraph: "`-l` lists the formats of every URL, in order, and exits. It downloads nothing and prints no command. Like any intent flag it skips the wizard. With no URL, or with `-i`, it only asks for the URLs, since listing needs nothing else, so a login wall needs `--cookies` on the command line."
- "Exit status" (`:139`): after "0 only when every URL given was saved (warnings allowed)", add "(with `-l`, listed)".

### Equivalent command (spec 09)

Unchanged. `-l` returns before `_print_equivalent` on every path, as today (`test_list_formats_downloads_nothing`). A bundled or abbreviated download flag now goes to direct mode, and that run prints the canonical command (`-a -s`, `-q 720`) like any other direct run. `equivalent.build` never emits `--`, abbreviations or bundles, so every printed command still passes `should_run_wizard(...) is False` (`tests/test_equivalent.py:21`, `tests/test_e2e.py:146`).

## Cases

| Case                                                        | Today (`e447bea`)                                                   | After                                                                                                   |
| ----------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `<url>`, no flags                                           | wizard                                                              | unchanged                                                                                               |
| `-a <url>`, `-q 720 <url>`, `--quality=720 <url>`           | direct, preview                                                     | unchanged                                                                                               |
| `-as`, `-q720`, `-ao DIR`, `--aud`, `--qual 720`, `--sub-l en`, `--no-p`, `--playlist-i 1`, `--video-f mkv`, `--cook brave` + `<url>` | wizard, values preselected (R4, R5)            | direct mode, preview for a single URL (D2)                                                              |
| `-i` with any of those                                      | wizard, values preselected                                          | unchanged                                                                                               |
| `-y` with any of those                                      | direct, no prompt                                                   | unchanged                                                                                               |
| `-- -a`                                                     | direct mode on the URL `-a` (R10)                                   | wizard on the URL `-a`, as for any URL with no flags                                                     |
| `-l <url>`                                                  | 6 wizard prompts, then the table (R3)                               | the table, no prompt (D1)                                                                               |
| `-l a b`, `-y -l a b`, `-a -l a b`                          | only `a` listed, silently (R2, R6, R7)                              | `a` then `b`, exit 0 (D4)                                                                               |
| `-l a b`, `a` fails                                         | `a` fails, `b` never tried, exit 1                                  | `Download failed: a` (plus the hint), `b` listed, exit 1 (D4)                                            |
| `-l`, no URL                                                | URL prompt, the whole walk, the first URL listed                    | URL prompt only (clipboard offered as today), every URL typed is listed (D3)                            |
| `-i -l a b`                                                 | one walk for `a`, `a` listed                                        | two URL prompts preset to `a` and `b`, `(1/2)` and `(2/2)`, both listed (D3)                             |
| `-i -l`, typed after `b` at the cookies prompt (R9)          | `a` listed only                                                     | there is no cookies prompt: the URL prompt takes `a b`, both listed                                     |
| `-l`, `c` or EOF at the URL prompt                          | `Cancelled.`, exit 1                                                | unchanged                                                                                               |
| `-y -l`, no URL                                             | clipboard, or `Missing URL.`, exit 1                                | unchanged                                                                                               |
| `-l` with `-p`, `-P`, `--playlist-items`                    | wizard; the flags don't change the listing                          | direct; the flags don't change the listing (Out)                                                        |
| `-l --cookies B <url>`                                      | direct, cookies used                                                | unchanged, for every URL                                                                                |
| `-l --cookies B a b`, browser unreadable                    | `a` only: reported, listed without cookies                          | reported and listed without cookies for each URL (Out)                                                   |
| `-l <gated url>`, no `--cookies`                            | wizard, cookie retry at the login wall, then listed with cookies    | `Download failed`, `→ try again with --cookies <browser>`, exit 1, as `-y -l` (Out)                      |
| `-l` on Instagram or X (up-front cookie site)               | up-front cookie prompt, then the walk                               | no prompt: listed without cookies, or fails with the hint                                               |
| `-l <carousel>`                                             | wizard with picker, then all items listed                           | all items listed, images named (`item_image_skipped`, `item_no_media`), as `-y -l`                       |
| `-l <lone image>`                                           | wizard refusal, `image_only_skipped`, exit 1                         | same as `-y -l` on the same commit (Out)                                                                 |
| `-l`, Ctrl-C while listing                                  | `Cancelled.`, SIGINT death                                          | unchanged; later URLs not listed                                                                         |
| rerun, partial failure                                      | not applicable: `-l` saves nothing                                  |                                                                                                         |

## Acceptance criteria

`[fix]` criteria fail on `main` (`e447bea`) and pass with the change. `[pin]` criteria pass on both. Prove each `[fix]` by reverting `cli.py` and `wizard.py` only.

- [x] `scripts/pre-release.sh --no-live` passes.
- [x] **[fix]** unit `test_bundled_and_attached_intent_flags_are_detected`, parametrized over `-as`, `-sa`, `-q720`, `-ao/tmp/x`, `-fopus`: `has_intent_flags([flag, "u"])` is true.
- [x] **[fix]** unit `test_abbreviated_intent_flags_are_detected`, over `--aud`, `--qual 720`, `--qual=720`, `--sub-l en`, `--no-p`, `--playlist-i 1`, `--video-f mkv`, `--cook firefox`, `--out x`: true.
- [x] **[fix]** unit `test_list_is_an_intent_flag`, over `-l`, `--list`, `--li`: `has_intent_flags` is true, and `should_run_wizard` is false with a URL and true without one.
- [x] **[fix]** unit `test_tokens_after_double_dash_are_urls_not_flags`: `has_intent_flags(["--", "-a"])` and `has_intent_flags(["--", "--cookies"])` are false.
- [x] **[pin]** unit `test_a_default_value_given_explicitly_still_counts`, over `-q 1080`, `-f mp3`, `--video-format mp4`: true.
- [x] **[pin]** unit `test_non_intent_flags_do_not_count`, over `-i`, `--inter`, `-y`, `-iy`: false.
- [x] unit `test_every_option_is_an_intent_flag_or_named_as_not_one`: every `dest` in `build_parser()._actions` is in `cli._INTENT_DESTS` or in `{"help", "version", "urls", "interactive", "no_confirm"}`. This catches a future flag left out of either list.
- [x] **[fix]** e2e `test_list_formats_lists_every_url_without_a_prompt` (replaces the pin at `:6107`): `wizard_urls(["-l", VIDEO, VIDEO_B])` with no replies. 0 prompts, exit 0, both URLs extracted, `"Available formats for"` printed twice, no `t("equiv_cmd")`, nothing under `home`.
- [x] **[fix]** e2e `test_list_formats_lists_every_url_in_every_direct_mode`, over `("-y",)` and `("-a",)`: `-l VIDEO VIDEO_B`, both extracted in that order, exit 0.
- [x] **[fix]** e2e `test_list_formats_goes_on_after_a_failed_url_and_exits_1`: `-l PRIVATE VIDEO`, `t("failed", PRIVATE)` printed, `VIDEO` listed, exit 1.
- [x] **[fix]** e2e `test_list_formats_without_a_url_asks_only_for_it`: `wizard_urls(["-l"], f"{VIDEO} {VIDEO_B}")`. 1 prompt, both listed, exit 0.
- [x] **[fix]** e2e `test_interactive_list_formats_asks_each_url_and_nothing_else`: `wizard_urls(["-i", "-l", VIDEO, VIDEO_B], "", "")`. 2 prompts, `f"{t('wizard_title')} (2/2)"` in the output, both listed, exit 0.
- [x] **[fix]** e2e `test_list_formats_on_a_cookie_site_asks_nothing` (replaces `:5263`): `wizard_urls(["-l", X_CAROUSEL])`. 0 prompts, `t("auth_needed_pre")` not printed, `"Available formats for v1"` printed.
- [x] **[fix]** e2e `test_list_formats_on_a_login_wall_says_to_pass_cookies` (replaces `:5444`): `cli.main(["-l", GATED])` with no replies. Exit 1, `t("failed", GATED)`, `--cookies <browser>` in the output, no `t("ask_cookies")`.
- [x] **[fix]** e2e `test_list_formats_alone_lists_media_without_codec_info` (replaces `:2037`): `generic("multi.html", flags=("-l",))` with no replies. Exit 0, `"Available formats"`, no `t("no_supported_items")`, nothing under `home / "Videos"`.
- [x] **[fix]** e2e `test_list_formats_agrees_with_and_without_yes`, over `VIDEO`, `CAROUSEL`, `IMAGE`, `PRIVATE`: `-l <url>` and `-y -l <url>` give the same exit code and the same output, and `-l` asks nothing. This replaces `test_a_lone_image_refused_by_the_list_formats_wizard_exits_1` (`:10469`), which is deleted: the lone image's wording belongs to spec 61.
- [x] **[pin]** e2e `test_cancelling_the_list_formats_wizard_exits_1` (`:10461`), rewritten to `wizard_urls(["-l"], "c")`, and parametrized with `EOFError`: 1 prompt, exit 1, `t("cancelled")`, no `"Available formats"`.
- [x] **[fix]** e2e `test_bundled_and_abbreviated_flags_skip_the_wizard`, over `["-as"]`, `["-q720"]`, `["--aud"]`, `["--qual", "720"]`: `wizard_urls([*flags, VIDEO], "")` with `home`. 1 prompt (the preview), `t("wizard_title")` not printed, exit 0, and one file under `home`: `.mp3` in `Music` for the audio cases, `Clip.mp4` in `Videos` for the others.
- [x] `ruff check`, `ruff format --check` and `mypy` pass. `AGENTS.md` says what the Design section says. `grep -n "_INTENT_FLAGS" -r src tests` is empty.

## Live check

Not needed. Which mode runs is decided by argparse and this wrapper, before yt-dlp is involved. Listing still goes through the same `list_formats` call to yt-dlp's `listformats`, now once per URL, and the e2e criteria run that against the real yt-dlp on the `fake_site` loopback. `tests/live/test_live.py:110` (`-y -l <carousel>`) already covers a real carousel, and its path is unchanged.

## Decisions

Resolved by the roles under the human's overnight delegation, each on the recommended option: D1 A, D2 A, D3 C, D4 A, D5 A. None is breaking under `AGENTS.md`: no default changes, and no flag is renamed or removed. D1, D2 and D3 change published behaviour and are on the 0.4.0 review list (see the box at the top).

**D1. Is `-l` an intent flag?**

- **A. Yes (recommended).** `-l <url>` lists at once. `README.md:82` already documents it as direct mode, and the walk asks type, quality, container and folder, then throws the answers away. The cost: on a login wall, `-l` stops offering cookies along the way (Out).
- **B. No: keep the wizard, and move `README.md:82`.** It keeps the cookie retry, but six prompts to list formats are what row 22 reports.

**D2. How to tell which options were given.**

- **A. Parse again into a namespace with a sentinel per intent `dest` (recommended).** Argparse itself says what it parsed, in every spelling it accepts, and the wrapper re-implements nothing. A drift test keeps the list complete.
- **B. Compare the parsed values with the defaults.** It misses `-q 1080`, `-f mp3` and `--video-format mp4`, which are direct today and would start opening the wizard. That changes published behaviour for nothing.
- **C. Extend the token scan with bundles and prefixes.** This re-implements argparse's matching (`-ao/tmp/x`, ambiguous prefixes, `--`), and it is the bug that row 22 reports.
- **D. `allow_abbrev=False`.** It would reject `--aud`, which parses today: a breaking change. It also does nothing for bundles or attached values.

**D3. `-l` with no URL, or with `-i`.**

- **A. Keep the wizard walk, once per URL, then list each one.** That's six prompts per URL for nothing. Spec 19 rejected it for the same reason (D7).
- **B. No wizard at all.** With no URL, use the clipboard or `Missing URL.`, as `-y` does. `-i` would need its own rule saying it's ignored with `-l`. And `download-media -l` typed by a person stops asking for the URL, while `download-media -a` still asks.
- **C. Ask only for the URLs (recommended).** The rule "no URL means it asks" still holds for every intent flag, and so does "`-i` asks, with the command line preselected". `-l` then acts the same whether the URL was typed on the command line or at the prompt. The cost: no up-front cookie prompt for Instagram or X here either.

**D4. `-l` with several URLs when one fails.**

- **A. List the rest, then exit 1 (recommended).** This matches downloads: one failed URL keeps the rest (`test_one_failed_url_fails_the_run_but_keeps_the_rest`), and the exit is 0 only when every URL succeeded (`AGENTS.md:139`).
- **B. Stop at the first failure.** The later URLs are lost without a word, which is the bug again.
- **C. Exit 0 if any URL was listed.** A script can't tell a failure, which `project.md` rules out ("a failure never reads as success").

**D5. A header before each URL's table.**

- **A. None (recommended).** yt-dlp's `[<extractor>] Extracting URL: <url>` already names each URL (`extractor/common.py:762`). No new message is needed.
- **B. A new `listing_url` message in both blocks.** It would repeat a line yt-dlp prints right below it.

## Open decisions

None for the human beyond reviewing the decisions above. Nothing here adds a dependency, changes a default, renames a flag or moves state.

## Validation

On `main` at `27f42a4`: the merge `8ff961c`, then `untagged-per-item`'s merge `e070f3a`, then `.ai/` only. yt-dlp 2026.10.26, Python 3.14.9, ffmpeg on `PATH`. GitHub CI run 42802950238 on `590fc9b` (this merge plus `.ai/`) passed; the run on `27f42a4` (42805905463) was still in progress when this was written.

- **Gate:** not re-run. The tech-lead's `scripts/pre-release.sh --no-live` on `8ff961c`: exit 0, 2898 passed, 15 xfailed, in es and en. The gate on `e070f3a`, which carries this merge and `untagged-per-item`, also passed: exit 0, 2920 passed, 15 xfailed. Since then only `.ai/` has changed.
- **Criteria tests:** a `pytest -k` selection over `test_wizard_dispatch.py`, `test_e2e.py`, `test_equivalent.py` and `test_wizard_flow.py` covering every test the criteria name, `[fix]` and `[pin]`, plus every other `-l` test: 87 passed, 4 xfailed (the two strict xfails below), exit 0. The three tests the criteria remove are gone (`test_list_formats_still_walks_and_lists_only_the_first_url`, `test_listing_formats_goes_through_the_cookie_retry`, `test_a_lone_image_refused_by_the_list_formats_wizard_exits_1`). `grep -rn "_INTENT_FLAGS" src tests` is empty. `AGENTS.md` (the intent list, the `-l` paragraph, "(with `-l`, listed)") says what the Design section says, read on the merge diff. The revert proof of the `[fix]` tests belongs to the developer (36 fail with `cli.py` and `wizard.py` reverted) and the test-engineer (189 new tests fail on the old `src/`). It wasn't repeated.
- **Scope:** `src/` changes only in `cli.py` and `wizard.py`, as in the Files table. The code matches the Design snippets. No message, flag, default, path or dependency changes.
- **Console check:** `main`'s real console script, `.venv/bin/download-media`, under `env -i` with `LANG=en_US.UTF-8`, `PATH=/usr/bin:/bin`, a dead D-Bus address, no display, and a fresh `HOME` plus all four `XDG_*` folders for each run in the scratchpad. A scratch `http.server` on `127.0.0.1` served two 1 s H.264/AAC clips (`clip.mp4`, `second.mp4`) and a 64×64 `pic.jpg`. Everything was deleted afterwards.

| Run | stdin | Output | Exit | Disk |
| --- | --- | --- | --- | --- |
| `-l <clip>` (R1) | `/dev/null` | `[generic] Extracting URL`, the table; no `Download wizard` | 0 | nothing; one request for `clip.mp4` |
| `-q720 <clip>`, `-as <clip>`, `--aud <clip>` (R1) | `/dev/null` | `Fetching info...`, `Preview`, `Proceed with download? [Y/n]: Cancelled.`; no `Download wizard` | 1 | nothing |
| same three | Enter | preview, download, `Done.`; the printed command is `-y -q 720`, `-y -a -s` and `-y -a` | 0 | `Videos/clip.mp4`, then `Music/clip.mp3` twice, one history row each |
| `-l a b`, `-y -l a b`, `-a -l a b` (R2, R6, R7) | `/dev/null` | `Extracting URL` and `Available formats for` for `clip`, then `second` | 0 | nothing; one request for `second.mp4` per run (3 in all) |
| `-l <404> <clip>` | `/dev/null` | yt-dlp's `ERROR: … HTTP Error 404`, `Download failed: <404>`, then the table for `clip` | 1 | nothing |
| `-l`, `a b` typed (D3) | `a b` | `Download wizard`, one `Video URL` prompt, then both tables | 0 | nothing |
| `-i -l a b` (D3) | Enter, Enter | `Download wizard (1/2)`, the prompt preset to `a`, `(2/2)` preset to `b`, then both tables; no type, quality, folder or cookies prompt | 0 | nothing |
| `-l`, no URL | `/dev/null`, and `c` | `Video URL (c=cancel): Cancelled.` | 1 | nothing |
| `-y -l`, no URL | `/dev/null` | `Missing URL.` | 1 | nothing |
| `-- -a` (R10) | `/dev/null` | `Download wizard`, `Video URL [-a] (c=cancel): Cancelled.` | 1 | nothing |
| `-l <pic.jpg>` and `-y -l <pic.jpg>` | `/dev/null` | the same output apart from the command line: yt-dlp's two generic warnings and a one-row `jpg` table | 0, 0 | nothing |

With stdout on a pipe, the 404 run prints `Download failed: <404>` after the next URL's `Extracting URL` line. That's the pre-existing ordering defect the first strict xfail below pins. It wasn't run on a terminal here.

- **Cases, tests only:** these come from criterion or test-engineer tests in the selection above and weren't run by hand: the cookie site (`X_CAROUSEL`, no up-front prompt), the login wall (`--cookies <browser>` hint, no `ask_cookies`), the carousel and `multi.html` listings, `-l` with the playlist flags, `-l --cookies` with an unreadable browser for each URL, Ctrl-C while listing, and EOF at the URL prompt for `-i -l`. There's no live check (see Live check).

### What changed after approval

Nothing in `src/` or `AGENTS.md` departs from the Design. The test-engineer added tests outside the Files table: two `wizard.ask_urls` tests in `tests/test_wizard_flow.py` (it leaves the options it starts from alone, including on a cancel at the second URL), and the strict xfail in `tests/test_equivalent.py` below. `test_a_lone_image_refused_by_the_list_formats_wizard_exits_1` was deleted, as the criteria ask. Spec 61's branch adjusts its own `test_wizard_in_list_mode_names_a_lone_image` to a single reply, since `-i -l` now asks only for the URL (QA found it on the three-way tree; `ig-lone-images` merged `main` and cut it in `33a464c`).

### Strict xfails added in testing, both pre-existing

1. `test_e2e.py::test_a_failed_line_stays_before_the_next_url_when_stdout_is_a_file[-l|-y]`: with stdout on a file or pipe, the tool's `print` lines lag behind yt-dlp's, which writes to `sys.stdout.buffer`, so a URL's `Download failed` lands after the next URL's output. `-l` with several URLs makes it easy to see, and downloads do the same. Planner row 52 (`output-order`).
2. `test_equivalent.py::test_a_url_that_starts_with_a_dash_round_trips[-a|-x]`: `equivalent.build` never emits `--`. So `download-media -y -- -a`, once it reaches the download, prints `download-media -y -a` (audio with no URL), and `-- -x` prints a usage error. No planner row yet; the planner decides whether it gets one. This spec's "every printed command passes `should_run_wizard(...) is False`" (Equivalent command) holds only for URLs that don't start with `-`.

### Notes from review, no change made

1. **Stdout ordering with a file or pipe** (QA minor, `cli.py:511`): the first strict xfail above, row 52.
2. **The equivalent command never emits `--`** (QA minor): the second strict xfail above.
3. **`-l` with no URL prints the wizard title** (QA minor, `wizard.py:516`): `Download wizard` heads a run that only asks for the URL. Accepted under D3, which reuses `run_all`'s URL phase, title included, so `-l` and `-i -l` look like the start of any wizard run.
4. **For the human (0.4.0 review list):** `-l` on a login wall loses the wizard's cookie offer and gets the `--cookies` hint instead, as `-y -l` always has (row 82, `list-cookie-retry`, if the human wants it back). `-- -a` now opens the wizard.
