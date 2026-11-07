# 20 — Report `--playlist-items` truthfully, and drop `NA` from single-video names

Status: draft
Item: `fix/playlist-items-fix`

## Problem

`--playlist-items` hides or misreports what it skipped, playlist mode names a single video `NA - …`, and under `-i` the item picker ignores the flag that every other step preselects.

Reproduced on `main` at `71250f4`, with yt-dlp 2026.10.26, ffmpeg 9.0.2 and Python 3.14.9. The real console script ran against a loopback `http.server` serving a tiny mp4 and two pages for yt-dlp's real generic extractor: `multi.html`, four `<video>` tags, which yt-dlp reads as a 4-entry playlist, and `poster.html`, one `<video>`. `HOME` and every `XDG_*` pointed at a scratch folder, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `-o` absolute, `LANG=en_US.UTF-8`.

```bash
download-media -y -o <out> --playlist-items 9 http://127.0.0.1:<port>/multi.html
# [generic] Playlist video: Downloading 0 items of 4
# No items are downloadable. Cancelling.
# exit 1, nothing on disk, no history row

download-media -y -o <out> --playlist-items 2,9 http://127.0.0.1:<port>/multi.html
# [generic] Playlist video: Downloading 1 items of 4
# Done.
# exit 0, on disk: "02 - multi (2) [multi-2].mp4". Item 9 is never mentioned by download-media

download-media -y -o <out> --playlist-items 2 http://127.0.0.1:<port>/poster.html
# Done.   exit 0, on disk: "NA - poster (1) [poster-1].mp4"
download-media -y -o <out> -p http://127.0.0.1:<port>/poster.html
# Done.   exit 0, on disk: "NA - poster (1) [poster-1].mp4"
# Without -p the same page saves "poster (1).mp4"

printf '\n\n\n\n\n\n\n\n' | download-media -i --playlist-items 2 -o <out> http://127.0.0.1:<port>/multi.html
# Pick items (Enter = supported videos only, 'all' = try everything, c = cancel): Selected: 1,2,3,4
# Output folder [<out>] ...      <- the -o flag is preselected, the item flag isn't
# exit 0, on disk: all 4 items; printed command: --playlist-items 1,2,3,4
```

- **What happened:** an out-of-range item reads as "nothing is downloadable, cancelling", although 4 items are downloadable and nothing was cancelled. A partly out-of-range spec says `Done.` and hides the missing item. A single video in playlist mode gets `NA` for its missing `playlist_index`. Under `-i`, Enter throws the flag away: on `fake_site`'s carousel it gives `2,4` (`test_wizard_carousel_on_defaults_prints_only_the_selection` pins that Enter), on `multi.html` all four.
- **What should happen:** the run names the items that aren't in the list and fails only if nothing was saved; a single video gets the name it gets without `-p`; under `-i` the picker's Enter keeps the flag's selection.

## Goal

What `--playlist-items` reports matches what is on disk, `-p` and `--playlist-items` never write `NA` into a file name, and `-i` preselects `--playlist-items` like it preselects every other intent flag.

## Findings

Each claim comes from a run: the console script on loopback (as above), a scratch script driving `YoutubeDL` directly, or `cli.main` on `fake_site`. "Prototype" means the design below applied to a scratch copy of the tree, `src/` untouched.

### QA's list, checked

| #   | Claim (QA, spec 09, planner)                                                   | Verdict                                                                                                                                                                                                                                                                                         |
| --- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `9` on a 4-item carousel fails with "No items are downloadable. Cancelling."   | Confirmed, real process and `fake_site`. Pinned by `test_playlist_items_out_of_range_is_a_failure`, `test_cookie_retry_that_finds_nothing_is_a_failure` and `test_what_the_user_reads_in_both_languages`, which spells out the literal in both languages                                              |
| 2   | `2,9` saves 1 file and says `Done.`, dropping 9 silently                       | Confirmed. yt-dlp's own `[generic] … Downloading 1 items of 4` is the only trace                                                                                                                                                                                                                 |
| 3   | `2` on a plain video saves `NA - … [id].mp4`; `-p` on a plain URL does too      | Confirmed on `poster.html`, and on `fake_site`'s `video` (`NA - Clip [v1].mp4`). Spec 17's pins in `test_direct_runs_on_media_without_codec_info_are_unchanged` record three `NA` names: `poster`, `og-video` and `audio`, each under `-y -p`                                                       |
| 4   | `-i --playlist-items 2`, Enter gives `2,4`                                     | Confirmed. `step_select` ignores `opts.playlist_items` on entry. Spec 09's case table said the step replaces the flag; `AGENTS.md` says intent flags become preselected defaults; the folder step already preselects `-o`                                                                      |
| 5   | Specs 18 and 19 changed the probe and the wizard                               | Read. Spec 19's `run_all` starts each URL's walk from the command-line options (`wizard.py:491`), so a preset reaches every URL's picker. Spec 18 keeps the picker on a gated carousel. Neither touches `step_select`'s Enter or the download path. No conflict                                  |

### yt-dlp behaviour the design relies on

Confirmed in the installed source, and at the `pyproject.toml` floor (2024.10.7, downloaded into a scratch folder): `_ReplacementFormatter`, `run_all_pps('playlist', …)` and `PlaylistEntries` exist there with the same shape.

- **Out-of-range items are silently dropped.** `PlaylistEntries.__getitem__` (`utils/_utils.py:2517`) ends a slice at the first `IndexError` and yields nothing for it. `__process_playlist` (`YoutubeDL.py:2078`) then processes 0 or more entries, and `ydl.download` returns 0. yt-dlp's own CLI exits 0 on `-I 9`; download-media already fails that case (spec 09's guard).
- **`playlist_items` is ignored on a URL that isn't a playlist.** `get_requested_items` only runs inside `__process_playlist`. `--playlist-items 2` on `poster.html` saved the video, exit 0.
- **A `playlist` post-processor sees the list's size.** `__process_playlist` ends with `ie_result = self.run_all_pps('playlist', ie_result)` (`:2192`), after `playlist_count` is filled in (`:2099-2101`). Measured: `9` on `multi.html` gives `playlist_count` 4; `2,9` gives 4; a plain video never runs it.
- **Nested lists run it inner first, outer last.** `--playlist-items 1,3` on `fake_site`'s `nested-gated` (outer list of 1 entry, holding a 3-item list) called it with count 3, then count 1. The last call is the outermost list.
- **`playlist_items` filters inner lists too** (row 32). That same run fetched the inner list's items 1 and 3, so the inner item 2 was dropped silently, whatever this item does.
- **The output template's `&` replacement formats the value.** `%(playlist_index&{:02d} - |)s` gives `02 - ` on a carousel item and an empty string when `playlist_index` is unset (`YoutubeDL.py:1433-1435`, `string.Formatter` with the field value). Measured with `_AvoidNameClash` added in playlist mode: carousel items keep `02 - Clip [v1].mp4`, `multi.html` keeps `01 - multi (1) [multi-1].mp4` to `04 - …`, and `poster.html` gets `poster (1).mp4`.
- **`PlaylistEntries` answers "does this segment select anything in a list of N" with yt-dlp's own rules,** given `PlaylistEntries(None, {"entries": [None] * n})`. On n=4: `9` → nothing, `-7` → nothing, `0` → nothing, `5-9` → nothing, `3-9` → 3 and 4, `-1` → 4, `-2:` → 3 and 4, `::2` → 1 and 3, `4:1:-1` → 4 to 1. `parse_playlist_items` raises `ValueError` on a bad spec.

### The prototype

- Full suite on the prototype, `tests/test_packaging.py` left out (no `PKGBUILD` path in the scratch copy): 1369 passed, 3 xfailed, 7 failed, in `en` and in `es`. The 7 failures are exactly the pre-existing tests listed under "Existing tests that change". 11 of the 1369 are the new criteria, written as scratch tests.
- The same scratch tests on `main`: 10 fail, and the one unchanged-behaviour pin (empty playlist) passes.
- `mypy` (strict, from `pyproject.toml`) and `ruff check src` pass on the prototype.
- The console-script reproductions above, on the prototype: `9` prints `Not in this playlist (4 items), skipped: 9` and `Download failed: <url>`, exit 1, nothing saved; `2,9` prints the same note before `Done.`, exit 0, one file, one history row; `--playlist-items 2` and `-p` on `poster.html` save `poster (1).mp4`; `-i --playlist-items 2` shows `Pick items (Enter = 2, …)`, saves `02 - multi (2) [multi-2].mp4`, and prints `--playlist-items 2`.

## Scope

### In

- Name every `--playlist-items` segment that selects nothing in the list, and fail with a true reason when nothing was saved because of it (D3, D4).
- Name a single video in playlist mode as single mode does, for `-p` and `--playlist-items` alike (D2).
- Under `-i` and in the wizard, make the picker's Enter keep the command line's `--playlist-items` (D1, D6).
- Update the pre-existing tests that pin today's messages and `NA` names.
- One sentence in `AGENTS.md`.

### Out

- **Nested playlists** (row 32). `playlist_items` still truncates inner lists silently; this item doesn't touch what's passed to yt-dlp. The not-found check counts the outermost list only, so an inner list's missing items stay unreported. Row 32 owns both.
- **"No items are downloadable. Cancelling." in direct mode** when the list holds only images, or `--playlist-items 1` picks an image: "Cancelling" reads wrong there too, but the message is the wizard's, the cause is true, and changing it touches every refusal. For the planner (rows 23 or 31).
- **`-i -p` and `-i -P`** don't preselect the picker either: Enter still picks the supported videos. That's shipped behaviour (spec 17's case table records `-i -P`). For the planner.
- **A total failure with an error** (`DownloadError`, e.g. `1,9` where item 1 is broken) prints `Download failed` and the hints, as today, without the not-found note: the run already fails loudly, and the result never reaches `cli.py`.
- **Lists whose size yt-dlp doesn't learn** (a lazy, paged list not read to its end, with no count from the extractor): nothing is reported. When a requested item is past the end, yt-dlp reads to the end, so the count is known in every case that matters.
- **The picker doesn't check the preset against the probed list.** The download reports it (D6).
- **Plurals:** "(1 items)" on a 1-entry list, like the existing `items_count_simple`. Not worth a plural rule here.

## Files

| Action | File                               |
| ------ | ---------------------------------- |
| modify | `src/download_media/downloader.py` |
| modify | `src/download_media/selection.py`  |
| modify | `src/download_media/cli.py`        |
| modify | `src/download_media/wizard.py`     |
| modify | `src/download_media/i18n.py`       |
| modify | `tests/test_e2e.py`                |
| modify | `tests/test_selection.py`          |
| modify | `tests/test_downloader.py`         |
| modify | `tests/test_wizard_flow.py`        |
| modify | `AGENTS.md`                        |

No new dependency. Two new messages. `equivalent.py` doesn't change.

## Design

Written for the recommended option of each decision; each decision says what changes otherwise.

### `selection.py`

```python
def missing_items(spec: str, count: int) -> list[str]:
    """Segments of a yt-dlp item spec that select nothing in a list of ``count`` items."""
```

- Import `PlaylistEntries` from `yt_dlp.utils`. Pair `spec.split(",")` with `PlaylistEntries.parse_playlist_items(spec)` (one yield per comma segment, `zip(..., strict=True)`), and keep the segment text when `PlaylistEntries(None, {"entries": [None] * count})[segment]` yields nothing. This is yt-dlp's own parser and slicing, not a copy of it.
- Return `[]` on `ValueError`: a bad spec has already failed the download by then, but the helper must never raise.

### `downloader.py`

- **Template:** in playlist mode, `outtmpl` becomes `"%(playlist_index&{:02d} - |)s%(title)s%(_name_suffix|)s.%(ext)s"`.
- **`_AvoidNameClash` in both modes:** drop the `if opts.playlist_mode != "yes"` guard in `download()`. It already sets ` [id]` on anything with a `playlist_index`, so carousel names don't change, and a single video in playlist mode gets the single-mode name and clash check. Update the class docstring if it implies single mode only.
- **List size:** a small `PostProcessor` registered with `when="playlist"`, only when `opts.playlist_mode == "yes"` and `opts.playlist_items`. It appends `info.get("playlist_count")` to a list; the last value is the outermost list's.
- **`DownloadResult`** gains `missing: tuple[str, ...] = ()` and `playlist_count: int | None = None`. `total` doesn't count `missing`: those items don't exist. Fill `missing` with `missing_items(opts.playlist_items, count)` only when the count is known and at least 1 (D4: an empty list keeps today's report). The existing `DownloadError` path runs first and is unchanged.

### `cli.py`, `_download_one`

After the download:

1. If `result.missing`: `warn(t("items_not_found", result.playlist_count, ",".join(result.missing)))`.
2. If nothing was saved: when `result.missing` and `result.total == 0`, `warn(t("failed", url))`; otherwise `warn(t("no_supported_items"))` as today. Return the failed outcome, as today.
3. Otherwise as today: `partial_result` if anything was skipped or failed, then `done` and the history row. So `2,9` exits 0 (D3).

`exit_code`, the cookie fallback and `_print_equivalent` don't change. The printed command keeps the spec the user gave (`--playlist-items 2,9`), so the replay repeats the run, note included: spec 09's contract.

### `wizard.py`

- `_walk_url` reads `preset = opts.playlist_items` before the probe. That's the command-line value, since spec 19's `run_all` starts every walk from the command line's options. Pass it through `_walk_plan` and `_run_step` to `step_select` as an optional argument, so existing callers keep working (`tests/test_wizard_flow.py:418` calls `_run_step` with three arguments).
- In `step_select`, when `preset` is non-empty: the prompt is `t("ask_selection_default_preset", preset)`, and Enter sets `opts.playlist_items = preset` as given, then prints `selected` as today. `all`, a typed selection, `b` and `c` are unchanged, and so is Enter without a preset.
- An empty-string preset (`--playlist-items ''`, which `equivalent.py` already treats as "all") counts as no preset.
- Nothing else changes: on a single video the wizard still sets `playlist_mode="no"` and the command drops the items (`test_items_flag_is_dropped_when_the_wizard_finds_a_single_video`); on a failed probe the flag still reaches the download (`test_items_flag_survives_a_failed_wizard_probe`).

### `i18n.py`

| Key                            | `en`                                                                   | `es`                                                                       |
| ------------------------------ | ---------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `items_not_found`              | `Not in this playlist ({} items), skipped: {}`                         | `No están en esta playlist ({} items), se omiten: {}`                      |
| `ask_selection_default_preset` | `Pick items (Enter = {}, 'all' = try everything, c = cancel): `        | `Selecciona items (Enter = {}, 'all' = todo intentable, c = cancelar): `   |

The second mirrors `ask_selection_default_videos` with the preset in place of "supported videos only".

### `AGENTS.md`

In the "Multi-entry URLs" paragraph, after "`-p` must not report success on a total failure.", add: "`--playlist-items` names any item that isn't in the list, and a single video in playlist mode keeps its single-mode name." The `-i` bullet already says intent flags become preselected defaults, and with D1 A it becomes true for this flag, so it stays as it is.

## Cases

| Case                                                      | Expected                                                                                                                                                   |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| direct / `-y`, `--playlist-items 9`, 4-item list           | `items_not_found(4, "9")`, `failed`, no `no_supported_items`, no `Done.`, exit 1, no file, no history row. Command printed, as spec 09 does on any failure  |
| `--playlist-items 2,9`                                    | Item 2 saved, the note, then `Done.`, exit 0, one history row, command keeps `2,9`                                                                          |
| `--playlist-items 2,-7,5-9,3-9,-1`                        | Items 2 and 4 saved, image 3 skipped as today, note names `-7,5-9`, exit 0. `3-9` clamps like yt-dlp, `-1` is item 4                                       |
| `--playlist-items 0`                                      | Selects nothing: note and `failed`, exit 1 (`test_bad_item_specs_fail_cleanly` keeps passing)                                                              |
| bad spec (`x`, `1,,2`, `::0`)                              | Unchanged: `DownloadError`, `failed`, exit 1                                                                                                               |
| `--playlist-items 1` on a carousel whose item 1 is an image | Unchanged: `no_supported_items`, exit 1 (see Out)                                                                                                          |
| `--playlist-items 1` on an empty playlist                  | Unchanged: `no_supported_items`, no note (D4)                                                                                                              |
| `--playlist-items 2` on a plain video                      | `Clip.mp4`, `Done.`, exit 0, no note (D5)                                                                                                                  |
| `-p` on a plain video                                     | `Clip.mp4`, as without `-p` (D2). A second plain video with the same title gets `Clip [v2].mp4`, as single mode does                                       |
| `-p` and `--playlist-items` on a carousel or playlist      | File names unchanged: `02 - Clip [v1].mp4`                                                                                                                 |
| direct, playlist-like URL, prompt answered "all", plain video behind it | `Clip.mp4` instead of `NA - Clip [v1].mp4`                                                                                                   |
| `-a` with any of the above                                 | Same reports; audio names follow the same templates                                                                                                        |
| `--cookies`, dropped by the retry                          | The retry's result carries the note; `test_cookie_retry_that_finds_nothing_is_a_failure` changes its message only, and the command keeps `--cookies`        |
| several URLs                                              | Each URL reports its own missing items                                                                                                                     |
| rerun                                                     | Same files, same note, still a success when something is on disk                                                                                           |
| nested list, `--playlist-items 1,3`, outer list of 1       | Note names `3` with count 1, the outer list's. The inner list's truncation stays silent (row 32)                                                           |
| wizard, no flag                                           | Unchanged: Enter picks the supported videos                                                                                                                |
| `-i --playlist-items 2`, carousel                         | Prompt `Enter = 2`; Enter saves item 2 only; command `--playlist-items 2`                                                                                  |
| `-i --playlist-items 2`, typed `4` or `all`               | The typed answer wins, as today                                                                                                                            |
| `-i --playlist-items 9`, carousel, Enter                   | The download reports item 9, exit 1                                                                                                                        |
| `-i --playlist-items 4` with two carousels                 | Both pickers offer `Enter = 4`                                                                                                                             |
| `-i --playlist-items 2`, single video or failed probe      | Unchanged (pinned tests above)                                                                                                                             |
| `-l`                                                      | Not affected: it never downloads                                                                                                                           |
| total failure with an error                               | Unchanged: `failed` and hints, no note (see Out)                                                                                                           |

## Acceptance criteria

**[fix]** criteria need a test that fails without the fix, which the developer shows by reverting it (`constraints.md`, Verification 5). **[pin]** criteria pass before and after. Tests that can reach a default folder use the `home` fixture. E2e tests use `fake_site` and the existing helpers in `tests/test_e2e.py` (`run`, `answers`, `names`, `commands`, `history_rows`, `replay`).

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `-y --playlist-items 9 https://fake.test/carousel`: exit 1; `t("items_not_found", 4, "9")` and `t("failed", CAROUSEL)` in the output; `t("no_supported_items")` and `t("done")` not in it; no file; no history row
- [ ] **[fix]** `-y --playlist-items 2,9` on the carousel: exit 0; only `02 - Clip [v1].mp4`; `t("items_not_found", 4, "9")` appears before `t("done")`; one history row; the printed command has `--playlist-items 2,9`
- [ ] **[fix]** `-y --playlist-items=2,-7,5-9,3-9,-1` on the carousel: exit 0; `02 - Clip [v1].mp4` and `04 - Clip [v2].mp4`; `t("items_not_found", 4, "-7,5-9")` in the output
- [ ] **[fix]** `-y --playlist-items 1,3 https://fake.test/nested-gated`: `t("items_not_found", 1, "3")` in the output and `01 - Clip [v2].mp4` on disk (the outermost list's count; the first `playlist` call would give 3 and no note)
- [ ] **[pin]** `-y --playlist-items 1 https://fake.test/empty`: exit 1, `t("no_supported_items")` in the output, and no `items_not_found` note. `test_empty_playlist_is_a_failure` passes unchanged
- [ ] **[fix]** `-y -p https://fake.test/video` and `-y --playlist-items 2 https://fake.test/video`: exit 0, `Clip.mp4` only, `t("done")` in the output
- [ ] **[fix]** `-y -p https://fake.test/video`, then `-y -p https://fake.test/video-b` into the same folder: `Clip.mp4` and `Clip [v2].mp4`
- [ ] **[pin]** Every existing test that names a carousel or playlist file under `-p` or `--playlist-items` (`BOTH_ITEMS`, `MULTI` and the rest) passes unchanged
- [ ] **[fix]** `-i --playlist-items 2 https://fake.test/carousel` with `PICKER_WALK`: exit 0; only `02 - Clip [v1].mp4`; the prompts include `t("ask_selection_default_preset", "2")`; the command has `--playlist-items 2`, and its replay into an empty folder saves the same file
- [ ] **[fix]** `-i --playlist-items 9` on the carousel with `PICKER_WALK`: exit 1, `t("items_not_found", 4, "9")` in the output, no file
- [ ] **[fix]** `-i --playlist-items 4 https://fake.test/carousel https://fake.test/rejects-cookies-carousel`, `PICKER_WALK` twice: exit 0, `04 - Clip [v2].mp4` on disk, and both picker prompts are `t("ask_selection_default_preset", "4")`
- [ ] **[pin]** `test_wizard_selection_replaces_the_playlist_items_flag`, `test_wizard_all_replaces_the_playlist_items_flag`, `test_items_flag_is_dropped_when_the_wizard_finds_a_single_video`, `test_items_flag_survives_a_failed_wizard_probe` and `test_wizard_carousel_on_defaults_prints_only_the_selection` pass unchanged
- [ ] **[fix]** Unit, `tests/test_selection.py`: `missing_items("2,9,-7,5-9,3-9,-1,0", 4) == ["9", "-7", "5-9", "0"]`; `missing_items("1:", 4) == []`; `missing_items("x", 4) == []` and `missing_items("1,,2", 4) == []` (no exception)
- [ ] **[fix]** Unit, `tests/test_wizard_flow.py`: a scripted carousel walk with `DownloadOptions(urls=[URL], playlist_items="1-2")` and Enter at the picker keeps `opts.playlist_items == "1-2"`; the same walk without the preset still gives the supported videos (`test_carousel_default_selects_videos` unchanged)
- [ ] **[fix]** Unit, `tests/test_downloader.py`: the playlist-mode `outtmpl` contains `%(playlist_index&{:02d} - |)s` and `%(_name_suffix|)s`, and no bare `%(playlist_index)02d`
- [ ] `items_not_found` and `ask_selection_default_preset` are in both the `es` and `en` blocks (`test_es_and_en_have_same_keys` covers parity)
- [ ] The Problem's console-script reproductions, re-run on the merged code against a loopback server with `HOME` and `XDG_*` in a temp folder, give the prototype results listed under Findings, and the files on disk match what each run reports

### Existing tests that change

These, and no other pre-existing test, failed on the prototype in both locales:

| Test                                                                   | Change                                                                                                    |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `test_playlist_items_out_of_range_is_a_failure`                         | Expect `items_not_found(4, "9")` and `failed`, not `no_supported_items`. Becomes the first criterion       |
| `test_cookie_retry_that_finds_nothing_is_a_failure`                     | Same message swap; the cookie and command assertions stay                                                 |
| `test_what_the_user_reads_in_both_languages` (both rows)                | The "nothing" literal becomes the `items_not_found` text in each language: `No están en esta playlist (4 items), se omiten: 9` and `Not in this playlist (4 items), skipped: 9` |
| `test_direct_runs_on_media_without_codec_info_are_unchanged`, 3 `-y -p` rows | `poster (1).mp4`, `video.mp4` and `audio (1).m4a`, the same names as their `-y` rows                  |

Commits, one per logical change: `fix(downloader): keep the single-video name in playlist mode`, `fix(cli): name playlist items that are not in the list`, `fix(wizard): preselect --playlist-items in the item picker`. Each carries its own tests.

## Live check

Not needed. Everything depends on yt-dlp's playlist processing, output templates and post-processor stages, which the tests run for real on loopback through the generic extractor and `fake_site`. No site-specific extractor, network or cookie behaviour is involved.

## SemVer

`fix`, not breaking. No flag is renamed, no default changes, no state file moves. Two new messages, which `AGENTS.md` counts as a minor; 0.3.0 is already a minor (`PLAN.md`, "Next release"), so the next version doesn't change. No version bump in this item.

The one shipped behaviour that changes is the `-p` file name on a URL that turns out to be a single video: 0.2.0 saved `NA - Clip [v1].mp4`, this saves `Clip.mp4`. That's the bug being fixed, not a format the tool promised (D2). **Release note for 0.3.0:** "`-p` on a URL that holds a single video now saves it under the same name as without `-p`, instead of `NA - <title> [<id>]`. Re-running such a download after upgrading saves a new copy next to the old file." `--playlist-items` itself is new in 0.3.0, so its behaviour needs no note beyond its description.

## Open decisions

The human delegated these overnight. Each lists the options and a recommendation; the orchestrator records the recommendation and the human reviews it.

### D1: `-i` with `--playlist-items` (QA finding 4)

| Option                                       | Result                                                                                         | Cost                                                                                       |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| **A. Preselect: Enter keeps the flag**       | `AGENTS.md`'s rule holds for every intent flag; `-i --playlist-items 2` + Enter saves item 2   | One new message; a few lines in `wizard.py`                                                |
| B. Keep spec 09's "the step replaces it"     | No code change                                                                                 | `AGENTS.md` needs an exception, and Enter keeps silently picking items the user didn't ask for |

**Recommended: A.** The flag hasn't shipped, so neither option changes what users have, but the `-i` rule has shipped, and A is the option that keeps it. Undoing A means deleting the preset argument. Spec 09 decided the opposite in a case-table row, without a decision of its own: this supersedes that row.

### D2: the name of a single video in playlist mode (QA finding 3)

| Option                                            | `-p` on `video`         | Cost                                                                                    |
| ------------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------- |
| **A. Same as single mode**                        | `Clip.mp4`              | Template change plus `_AvoidNameClash` in both modes                                    |
| B. Drop only the `NA - ` prefix                   | `Clip [v1].mp4`         | Template change only. `-p` and no `-p` keep giving the same video two different names   |
| C. Leave it                                       | `NA - Clip [v1].mp4`    | Keeps a broken name                                                                     |

**Recommended: A.** Both A and B change the shipped `NA` name, which is the bug. A gives the name users already get without `-p` (spec 09's F7 named `Clip.mp4` as the expected result), so `-p` and no `-p` stop making duplicates of the same video. Carousel names don't change under either option (measured). Not breaking: the `AGENTS.md` trigger is about flags, defaults and state, and `NA` was never a promised format. If B: keep the guard around `_AvoidNameClash`, use `"%(playlist_index&{:02d} - |)s%(title)s [%(id)s].%(ext)s"`, and the D2 rows expect `Clip [v1].mp4`, `poster (1) [poster-1].mp4`, `video [tiny].mp4`, `audio (1) [audio-1].m4a`.

### D3: exit code when some items are missing but something was saved

| Option                                   | `2,9` on 4 items                                 |
| ---------------------------------------- | ------------------------------------------------ |
| **A. Exit 0, note before `Done.`**       | Same rule as `partial_result`: done if something was saved |
| B. Exit 1                                | Scripts notice a typo, but a run with a saved file reads as failed, unlike every other partial run |

**Recommended: A.** It's the project's existing rule ("A run only counts as done if something was saved"), and the note keeps the success from hiding anything. Switching to B is one condition in `_download_one`.

### D4: what counts as "not in the list"

| Option                                                       | `3-9` on 4 items        | `1` on an empty list         |
| ------------------------------------------------------------ | ----------------------- | ---------------------------- |
| **A. A segment that selects nothing, on a non-empty list**   | No note: yt-dlp clamps  | `no_supported_items`, as today |
| B. Also a range whose end passes the list                    | Note names `3-9`        | Note with count 0            |

**Recommended: A.** `1-100` meaning "up to 100" is how yt-dlp reads ranges, and with A nothing that exists is ever skipped without a word. On an empty list, "nothing is downloadable" is the more useful reason, and A keeps `test_empty_playlist_is_a_failure` unchanged. B needs the developer to decide what "passes the end" means for steps and negative bounds.

### D5: `--playlist-items` on a URL that isn't a playlist

| Option                                  | `--playlist-items 2` on `video` |
| --------------------------------------- | ------------------------------- |
| **A. Save it, as yt-dlp does**          | `Clip.mp4`, `Done.`, exit 0     |
| B. Save it and add a note               | A third new message             |
| C. Fail: item 2 doesn't exist           | Exit 1. Diverges from yt-dlp, and from the wizard, which drops the flag on a single video (pinned) |

**Recommended: A.** It's yt-dlp's behaviour, nothing that exists is skipped, and it matches the wizard. B can be added later without undoing anything.

### D6: what the picker's Enter does with a preset

| Option                                         | `-i --playlist-items 1` where item 1 is an image                                |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| **A. Use it as given**                         | Same as `-y --playlist-items 1`: nothing saved, `no_supported_items`, exit 1     |
| B. Filter it like a typed selection when the picker can parse it | Item 1 dropped with `selection_warning_unsupported`, then what? Needs a fallback, and yt-dlp-only syntax (`-2:`, `1:3`) still can't be filtered |

**Recommended: A.** "Preselected default" then means exactly what `-y` would do with the same flag, there's one rule, and the prompt shows the value. B adds two code paths and a fallback rule for a combination users have to type on purpose.
