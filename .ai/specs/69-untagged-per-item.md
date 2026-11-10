# 69 — Name an item in a warning only once it counts as saved

Status: draft
Item: `fix/untagged-per-item`

> **For the human's review before the release:** the architect took D1 to D4 under the overnight delegation, each on the recommended option. Three warnings change against 0.3.0, all under `-p` or `--playlist-items`, and only on a run where an item failed: `untagged` and `subs_not_embedded` stop naming the failed item, and `subs_none` is now said when the only item that got subtitles is one that failed (D1). No flag, default, message text, exit status or on-disk path changes. It reads as a patch, inside 0.4.0's minor.

## Problem

Under `-p` or `--playlist-items`, an item that fails is still named in the per-file warnings, as if it had been saved.

Reproduced on `main` at `43ef19a` (yt-dlp 2026.10.26, ffmpeg on `PATH`, `LANG=en_US.UTF-8`, fresh `HOME` and XDG folders, dead session bus). The test was a scratch loopback server (`python -I -m http.server`) serving `page.html` = `<audio src="clip.oga"></audio><audio src="a1.m4a"></audio>`. `clip.oga` is a silent H.264 `.mov` (made with `-an -c copy`), the same body as `tests/conftest.py`'s `silent.mov`, and `a1.m4a` is AAC audio:

```
download-media -y -a -p -o <scratch>/out http://127.0.0.1:<port>/page.html
```

```
ERROR: WARNING: unable to obtain file audio codec with ffprobe
[Metadata] Adding metadata to ".../01 - page (1) [page-1].oga"
...
1 of 2 items downloaded (0 without downloadable media, 1 failed).
01 - page (1) [page-1].oga: saved, but ffmpeg couldn't write its metadata, so a rerun won't recognize it and may save another copy.
Done.
```

Exit 0. On disk: `01 - page (1) [page-1].oga` (the failed item's body, row 46's to remove) and `02 - page (2) [page-2].mp3`. `--playlist-items 1,2` printed the same lines. Single mode (`-y -a`) stopped at the extraction error with `Download failed`, exit 1, and never reached the metadata step: it isn't affected.

The pinned test fails for that reason. `pytest --runxfail "tests/test_e2e.py::test_an_item_that_failed_is_not_named_as_untagged"` → 2 failed, `AssertionError` on `t("untagged", failed) not in output`, in both parametrizations.

**What should happen:** the item counts as failed, so no warning calls it saved. The line `1 of 2 items downloaded (… 1 failed)` stays as it is.

## Goal

Every per-file warning after a download names only files the run counted as saved, as `not_converted` does since spec 30 (`a2dbda3`).

## Findings

### Why the failed item is named

- Under `-p` and `--playlist-items`, `build_ydl_opts` sets `ignoreerrors: True` (`downloader.py:485`). yt-dlp's `run_pp` then reports a failing step and returns, without raising (`YoutubeDL.py:3803-3813`). `run_all_pps` goes on to the next step (`:3826-3831`), and the post hooks still run (`:3666-3671`). Spec 45 recorded this, and it's why `keep()` asks `ydl.item_failed()` (`downloader.py:926-927`, `:373-375`).
- Under `-a`, `FFmpegExtractAudio` fails on a file with no audio stream. `_TagSource` runs next (`downloader.py:945`). The `.oga` has a video stream, so `_has_media_streams` says yes (`:778`), and the name goes straight onto the run's `untagged` list (`:780`). `keep()` drops the item from `saved`, but by then `untagged` already holds the name.
- Single mode is safe: without `ignoreerrors`, `run_pp` re-raises (`YoutubeDL.py:3813`), and `process_info` ends the item (`:3663-3665`) before any later step runs.
- The same applies to any step that fails before `_TagSource`. That covers yt-dlp's fixups and the format merger, which run first as `__postprocessors` (`YoutubeDL.py:3847`). Only the extraction case was measured.

### The other per-file warnings

Each list `download()` returns was checked to see whether it's filled per item (in `keep()`, only for a saved item) or per run (straight from a step).

| `DownloadResult` field | Filled by | Per item? |
| --- | --- | --- |
| `untagged` | `_TagSource.run`, straight onto the run's list (`:780`) | **No.** Measured above |
| `subs_not_embedded` | `_EmbedSubtitles.run`, straight onto the run's list (`:723`) | **No.** Measured below |
| `subs_missing` (from `subs_seen`, prints `subs_none`) | `_SubtitlesSeen.run`, straight onto the run's list (`:667`) | **No.** Measured below |
| `no_cover` | `_Ydl.item_no_cover`, read in `keep()` (`:929-930`) | Yes |
| `not_converted` | `_Ydl.item_not_converted`, read in `keep()` (`:931`), spec 30 `a2dbda3` | Yes |
| `subs_failed` | `_Ydl.item_subs_failed`, read in `keep()` (`:933`), spec 36 | Yes |
| `subs_empty` | `_Ydl.item_subs_empty`, read in `keep()` (`:934`), spec 36 | Yes |
| `saved` (images) | `_Ydl.item_images`, added to `images` only when the item didn't fail (`:270-275`) | Yes |
| `skipped`, `missing`, `playlist_count` | The match filter and the playlist step, before any download | Not relevant |

Measured on the same `43ef19a`, same scratch setup. These pages use `<track kind="subtitles" src="en.vtt" srclang="en">`, which yt-dlp's generic extractor reads as subtitles (`common.py:3454-3461`). `junk.mp4` holds WebVTT text, like the repo's `JUNK`.

- **`subs_none` missing.** The page held `<video src="junk.mp4">` with an `en.vtt` track, then `<video src="tiny.mp4">` with none. `-y -p -s` gave `1 of 2 items downloaded (0 without downloadable media, 1 failed).` and `Done.`, with no `subs_none`. No `.vtt` was on disk: spec 36's `drop_subs` removed the failed item's file (`:270-273`). So no saved item has a subtitle, the rule spec 21 set and `DownloadResult.subs_missing` documents (`:96`), and the run says nothing. `-y -a -p -s`, with the silent `.oga` as item 1 and an `en.vtt` track, gave the same: no `subs_none`, no `.vtt`. It also named the `.oga` as `untagged`.
- **`subs_not_embedded` names the failed item.** Item 1 had both an `en.vtt` and an `es.ttml` track, so the embed fails, and ffprobe reads one subtitle stream in the WebVTT body, which is fewer than 2 files. `-y -p -s --sub-langs en,es` printed `01 - subs-junk-ttml (1) [subs-junk-ttml-1].mp4: one or more subtitles couldn't be embedded; all of them are kept as separate files.` next to `1 failed`. Neither subtitle file was on disk. That makes the line wrong twice: the item wasn't saved, and its subtitles weren't kept.

On 0.3.0 (read, `git show v0.3.0:src/download_media/downloader.py`), `_TagSource` already appended at once, so `untagged` has had this defect since spec 29 shipped. 0.3.0 had no `drop_subs`, so a failed item's subtitle files stayed on disk. `subs_not_embedded`'s "kept as separate files" and the missing `subs_none` only became wrong once spec 36 (unreleased) started removing them.

### Side findings, out of scope

- In the first subtitle run, `_EmbedSubtitles` rewrote the failed item's 44 B WebVTT body into a 762 B mp4 holding subtitle streams. Steps keep running on an item that has already failed, so the body row 46 would delete gets changed first. Belongs with row 46 (see Out).
- yt-dlp's extraction error reads `ERROR: WARNING: unable to obtain file audio codec with ffprobe`. The text comes from yt-dlp (`postprocessor/ffmpeg.py:488`), and the tool's own summary follows it. Not worth a row unless the human asks.

## Scope

### In

- `untagged`, `subs_not_embedded` and `subs_missing` record per item on `_Ydl`, and `keep()` adds them to the run's lists only when the item didn't fail. This is spec 30's `a2dbda3` pattern (D1, D2).
- Flip the strict xfail, and add the tests in Acceptance criteria.

### Out

- **Removing the failed item's body** (the `.oga` above, the junk `.mp4`): row 46.
- **Skipping later steps once an item has failed** (it would also avoid the rewrite above): a behaviour change on disk that touches every step. Per-item recording is needed anyway, because a step can record a name and a later one can still fail the item, which is `a2dbda3`'s case (container step, then the metadata step). Noted for row 46.
- **Single mode, `-l`, the wizard's prompts, `cli.py`'s warning loop, i18n:** unchanged. The messages and their order stay as they are.
- **`downloader.py:699`'s `getattr(..., "item_subs_empty", ())`:** row 76 already lists it.

## Files

| Action | File | Lines on `43ef19a` |
| --- | --- | --- |
| modify | `src/download_media/downloader.py` | `_Ydl.__init__` `:205-206` and `process_info` `:258` (three per-item fields); `_SubtitlesSeen` `:660-667`; `_EmbedSubtitles` `:680-682`, `:723`; `_TagSource` `:754-756`, `:780`; `download()` `:921` (two declarations move up from `:955-956`), `keep()` `:926-934`, `:945`, `:959`, `:961` |
| modify | `tests/test_e2e.py` | drop the xfail marker at `:9850-9854`; new tests right after `:9868`, **not at the end of the file** |
| modify | `tests/conftest.py` | two `FakeSiteIE` pages next to `subs-junk-404-carousel` (`:1101-1106`) |
| modify | `tests/test_downloader.py` | `:480-504`, the `_EmbedSubtitles` unit test |

Not touched: `cli.py`, `i18n.py`, `config.py`, `DownloadResult`'s fields, the return block of `download()` (`:970-983`).

### Collisions with what's in flight

- **`feature/ig-lone-images`** (`375265b`) touches `downloader.py` in these hunks: `DownloadResult` (`:107`), `_Ydl`'s class attributes (`:186-195`), `process_video_result` (`:235-245`), `_AvoidNameClash` (`:582`), the match filter (`:870-899`), `download()`'s `match_filter` line (`:913-919`, where `:917-919` are only context), the end of the return block (`:980-983`, appending `lone_images=`), and `list_formats` (`:987-993`). None of those overlaps this item. The closest are this item's two declarations inserted after `:921`, two lines past that hunk's context, and the `__init__`/`process_info` edits, about ten lines from its `_Ydl` hunks. The return block is left alone on purpose. In tests, it appends at the end of `test_e2e.py` (`:11046+`), adds `conftest.py` pages at `:597`, and edits `test_downloader.py` at `:281-334`. This item stays clear of all three. If it merges first, expect only a context shift.
- **`fix/intent-flags`** (`7b9720a`) touches `cli.py`, `wizard.py` and `AGENTS.md`, which this item doesn't touch. In `test_e2e.py` it edits `:2034`, `:5260`, `:5441`, `:6104` and `:10458`, none of them near `:9850-9870`. No collision expected.

## Design

All in `downloader.py`, mirroring `item_not_converted`.

1. **`_Ydl` per-item fields.** Next to `item_not_converted` (`:205-206`, under the comment "Files of the current item … named only if the item is saved"):
   - `item_untagged: list[str] = []`
   - `item_not_embedded: list[str] = []`
   - `item_subs_seen = False`

   Reset all three in `process_info` with `item_not_converted` (`:258`). This also covers a `ReExtractInfo` retry: `process_info` runs again, and the first try's names go (spec 45).
2. **`_TagSource`.** Drop the `untagged` parameter: `__init__(self, downloader)`, or remove the override entirely if nothing is left in it. Replace `self._untagged.append(...)` (`:780`) with `self._downloader.item_untagged.append(Path(path).name)`. Add to the docstring that it needs `_Ydl`, as `_EmbedCover`'s says.
3. **`_EmbedSubtitles`.** Drop the `not_embedded` parameter: `__init__(self, downloader)` still calls `super().__init__(downloader, already_have_subtitle=True)`. At `:723`, append to `self._downloader.item_not_embedded`. Same docstring note.
4. **`_SubtitlesSeen`.** Drop the `seen` parameter, so it constructs as `_SubtitlesSeen()`. On files, set `self._downloader.item_subs_seen = True`. Leave the `item_subs_empty` line as it is.
5. **`download()`.**
   - Move `subs_seen: list[bool] = []` and `not_embedded: list[str] = []` up from `:955-956` to just after `subs_empty` (`:921`). Keep both as lists, so `keep()` needs no `nonlocal` and the return block stays the same.
   - In `keep()`, inside the existing `if not ydl.item_failed():` block, add:
     ```python
     untagged.extend(ydl.item_untagged)
     not_embedded.extend(ydl.item_not_embedded)
     if ydl.item_subs_seen:
         subs_seen.append(True)
     ```
   - Wire `_TagSource(ydl)` (`:945`), `_SubtitlesSeen()` (`:959`) and `_EmbedSubtitles(ydl)` (`:961`).
   - Keep `untagged`'s declaration where it is (`:917`): it's context in ig-lone-images' hunk.

The order of names stays the order of items: `keep()` runs once per item, in download order. Images never reach these steps or `keep()` (post hooks don't run for them, `:207-208`), so the image path doesn't change.

No yt-dlp option changes. The design relies on `run_pp` under `ignoreerrors: True` (`YoutubeDL.py:3803-3813`) and on post hooks running after a reported failure (`:3666-3671`). Both were read on 2026.10.26 and are exercised by the existing e2e tests.

## Cases

| Case | Expected |
| --- | --- |
| `-y -a -p` / `--playlist-items 1,2`, silent video then audio | `1 of 2 … 1 failed`, no `untagged` line, `Done.`, exit 0. Today the failed `.oga` is named |
| `-y -p` / `--playlist-items`, an untaggable item that saves (`oga-and-mp4`) | `untagged` names it, once. Unchanged (`test_playlist_with_an_untaggable_item_saves_both_and_counts_each_once`, `test_playlist_items_on_the_untaggable_item_alone`) |
| single, `-y`, direct preview, `-P`: an untaggable file | `untagged`, unchanged (`test_a_saved_file_ffmpeg_cannot_tag_is_a_success`). A failing step raises before later steps run |
| `-y -p -s` / `--playlist-items`, junk item with `en`+`es` (one TTML) tracks, then a plain video | `1 of 2 … 1 failed`, no `subs_not_embedded`, `subs_none` once. Today the failed item is named and `subs_none` is missing |
| `-y -p -s` and `-y -a -p -s`, junk item with an `en.vtt`, then a plain item | `1 of 2 … 1 failed`, `subs_none` once, no `.vtt` on disk. Today there's no `subs_none` |
| `-p -s`, a saved item with subs and one without | No `subs_none`, unchanged (`test_carousel_with_subtitles_on_one_item`) |
| `-p -s`, a saved item whose TTML can't be embedded | `subs_not_embedded` names it, unchanged (`test_carousel_item_with_an_unreadable_subtitle_still_counts`) |
| wizard, `-i` | Same `download()` and the same lines, so it follows from the direct rows. No wizard prompt changes |
| `-l` | Not affected: `list_formats` adds no post-processors |
| with / without cookies | `_download_with_cookie_fallback` only acts on `DownloadError`. A partial result passes through unchanged |
| several URLs | One `DownloadResult` per URL, so the lists stay per URL |
| total failure under `-p` | `DownloadError` before any warning prints, unchanged |
| rerun | Unchanged: no on-disk state is involved |

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] **[fix]** `test_an_item_that_failed_is_not_named_as_untagged[-p|--playlist-items]` passes with its `xfail` marker removed. Add `assert never_said("untagged", output)`.
- [ ] **[fix]** New page `subs-junk-ttml-carousel` in `FakeSiteIE`: item `s1` is `JUNK` with `self._tracks(en="en.vtt", es="en.ttml")`, item `s2` is plain. New `test_an_item_that_failed_is_not_named_as_not_embedded[-p|--playlist-items]` runs `-y -s` plus the mode flags (`--playlist-items 1,2`). It asserts exit 0, `t("partial_result", 1, 2, 0, 1)`, `"02 - Clip [s2].mp4" in names(out)`, `never_said("subs_not_embedded", output)`, and `output.count(t("subs_none", "es,en")) == 1`.
- [ ] **[fix]** New page `subs-junk-carousel`: item `s1` is `JUNK` with `self._tracks(en="en.vtt")`, item `s2` is plain. New `test_subs_none_counts_only_saved_items[video|audio]` runs `-y -p -s`, adding `-a` for the audio row. It asserts exit 0, `t("partial_result", 1, 2, 0, 1)`, `output.count(t("subs_none", "es,en")) == 1`, and no `.vtt` in `names(out)`.
- [ ] Each of the three fails on `main` before the fix: the test-engineer reverts `downloader.py` and shows the failure (constraint 5). The two subtitle tests were measured on generic loopback pages, not on `FakeSiteIE`. If a `FakeSiteIE` page doesn't fail before the fix, the test-engineer says so and reports what it printed.
- [ ] `tests/test_downloader.py`'s `_EmbedSubtitles` unit test (`:480-504`) drives the step with a `_Ydl` (`quiet`, `no_warnings`) and asserts `ydl.item_not_embedded == ["Clip.mp4"]`. The temp-file assertion stays.
- [ ] These still pass unchanged: `test_a_saved_file_ffmpeg_cannot_tag_is_a_success`, `test_playlist_with_an_untaggable_item_saves_both_and_counts_each_once`, `test_playlist_items_on_the_untaggable_item_alone`, `test_untaggable_subtitled_file_warns_twice_and_keeps_both_files`, `test_rerun_of_an_untaggable_file_under_p_counts_each_run_once`, `test_carousel_with_subtitles_on_one_item`, `test_carousel_where_no_item_has_subtitles_warns_once`, `test_carousel_item_with_an_unreadable_subtitle_still_counts`, `test_an_item_that_failed_is_not_named_as_not_converted`, `test_every_item_kept_as_downloaded_is_named_once`, `test_a_failed_item_next_to_a_saved_one_never_names_its_failed_subtitle`.
- [ ] `git diff main -- src/download_media/cli.py src/download_media/i18n.py` is empty, and so is the diff of `DownloadResult`'s fields.
- [ ] `mypy` and `ruff check` / `ruff format --check` pass.

## Live check

Not needed. The change is the tool's own bookkeeping after yt-dlp's post-processing, with nothing specific to any site. Real yt-dlp and ffmpeg on the `fake_site` and generic loopback fixtures already exercise the yt-dlp behaviour it relies on (`run_pp` under `ignoreerrors`, post hooks after a failure). A real URL with a silent video in a carousel would only repeat the loopback case.

## Decisions

Taken under the overnight delegation, on the recommended option. All four are cheap to undo.

**D1 — Scope: `untagged` only, or every per-run list with the same defect?**

| Option | Effect |
| --- | --- |
| **A. All three: `untagged`, `subs_not_embedded`, `subs_missing` (recommended)** | Same mechanism, same function, same five-line pattern in `keep()`. One item, size `S`. Changes `subs_none` and `subs_not_embedded` output on the measured cases (flagged above) |
| B. `untagged` only, and a new row for the other two | Smallest diff. Leaves `subs_not_embedded` claiming files that were deleted, in the same release that started deleting them (spec 36) |

**D2 — Where the per-item names live**

| Option | Effect |
| --- | --- |
| **A. On `_Ydl` (`item_untagged`, `item_not_embedded`, `item_subs_seen`), reset in `process_info`, read in `keep()` (recommended)** | `a2dbda3`'s pattern, already used by `item_no_cover`, `item_not_converted` and `item_subs_*`. The steps need `_Ydl`, as `_EmbedCover` and `_ContainerStep` already do |
| B. Keep the run lists, and have `keep()` truncate them to their length at the item's start | No signature change. Index bookkeeping across `process_info`, `keep()` and retries, which is easy to get wrong |
| C. Each step keeps its own per-item list, and `download()` reads and resets it | Three more objects to reset. Nothing gained over A |

**D3 — The `_EmbedSubtitles` unit test that uses a plain `YoutubeDL`**

| Option | Effect |
| --- | --- |
| **A. Drive it through `_Ydl` and assert `item_not_embedded` (recommended)** | The test matches how the step runs |
| B. A `getattr` fallback in the step, so a plain `YoutubeDL` still works | Keeps the test as it is, but miswiring would drop names silently. Row 76 already flags the one `getattr` there as inconsistent |

**D4 — `subs_none` when the only subtitled item failed**

| Option | Effect |
| --- | --- |
| **A. Say it: no saved item got a subtitle file (recommended)** | Spec 21's rule as written and as `DownloadResult` documents it. On disk there's no subtitle at all |
| B. Stay silent, treating "the site had subtitles" as enough | Keeps 0.3.0's output for this case. Contradicts the rule, and the files are gone |

## Open decisions

None blocking. For the 0.4.0 review list, the published behaviour that changes against 0.3.0, all under `-p`/`--playlist-items` and only on a run where an item failed:

1. `untagged` no longer names a failed item (D1). In 0.3.0 that line is false.
2. `subs_not_embedded` no longer names a failed item (D1). In 0.3.0 its subtitle files stayed on disk. Spec 36 removes them.
3. `subs_none` is now said when the only items whose subtitles were written failed (D1, D4). In 0.3.0 those files stayed and the run said nothing.
