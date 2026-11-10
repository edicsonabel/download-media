# 46 — A failed item leaves nothing this run downloaded

Status: draft
Item: `fix/failure-leftovers`

> **For the human's review before the release:** the architect took D1 to D6 under the overnight delegation, each on the recommended option. Three changes to published behaviour, for the 0.4.0 review list: a failed item's downloaded body is now deleted, in every mode (D2); under `-p` and `--playlist-items` a failed post-processing step now ends that item's processing, as it already does in single mode, so the item gets one `ERROR: Postprocessing: …` line where it got one bare `ERROR:` line per failed step (D1); and a rerun of a failed URL fetches it again instead of finding the leftover, so single mode stops adding a `junk [junk].mp4` copy per run. No flag, default, message, exit status or counted number changes. It reads as a patch, inside 0.4.0's minor. Row 46 was too big for one item: this spec covers failed items, and the Ctrl-C and full-disk leftovers go back to the planner (Out, D6).

## Problem

A download that fails leaves the body it fetched on disk, under its final name, and under `-p` a failure can then read as success on the next run.

Reproduced on `main` at `f0c99a8` (code as `0648e07`), yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14. The runs went through `cli.main` with the real yt-dlp and ffmpeg, using the e2e fixtures `run` (fake extractor) and `generic` (yt-dlp's real generic extractor) against the loopback `fake_site` server, with fresh `HOME`, `XDG_*` and a dead session bus (`isolated_env`). They ran from a scratch test file outside the repo, loaded with `PYTHONPATH=<repo> pytest -p tests.conftest <scratch>/test_repro46.py`. `JUNK` is `tests/conftest.py`'s text body served as `video/mp4`.

| #   | Command (flags after `download-media`)                                  | Output                                                                                       | Exit | On disk afterwards                                                                                 |
| --- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ---- | -------------------------------------------------------------------------------------------------- |
| R1  | `-y -o out <JUNK>`                                                      | `ERROR: Postprocessing: Conversion failed!`, `Download failed`                               | 1    | `junk.mp4` (44 B)                                                                                  |
| R2  | `-y -p -o out <all-junk.html>`                                          | two `ERROR: Conversion failed!`, `Download failed`                                           | 1    | `01 - all-junk (1) [all-junk-1].mp4`, `02 - all-junk (2) [all-junk-2].mp4`                        |
| R3  | `-y -p -o out <junk-and-mp4.html>`                                      | `1 of 2 items downloaded (0 without downloadable media, 1 failed)`, `Done.`                  | 0    | the junk item `01 - …mp4` (44 B) and the saved `02 - …mp4`                                         |
| R3b | `-y -o out <junk-and-mp4.html>` (single mode)                           | `Download failed`                                                                            | 1    | `junk-and-mp4 (1) [junk-and-mp4-1].mp4` (44 B)                                                     |
| R4  | `-y -a -p -o out <silent-oga-and-m4a.html>`                             | `ERROR: WARNING: unable to obtain file audio codec with ffprobe`, `1 of 2 … 1 failed`, `Done.` | 0    | the silent video `01 - …oga` (2181 B) and the saved `02 - …mp3`                                  |
| R5  | `-y -a -p https://fake.test/mixed-carousel`                             | `2 of 4 … (1 without downloadable media, 1 failed)`, `Done.`                                 | 0    | `02 - Clip [p1].jpg` (the direct `.jpg` link that failed extraction), plus the two saved `.mp3`    |
| R6a | `-y -p -s https://fake.test/subs-junk-carousel`                         | `ERROR: Conversion failed!`, `1 of 2 … 1 failed`, `Done.`                                    | 0    | `01 - Clip [s1].mp4` (762 B, ffprobe: **one `mov_text` subtitle stream**, no audio or video), `02 - Clip [s2].mp4` |
| R6b | the same command again                                                  | no `N of M` line, `Done.`                                                                    | 0    | `01 - Clip [s1].mp4` re-tagged (836 B) **and its `.en.vtt` kept**: the failed item now counts as saved |
| R6c | again without `-s`                                                      | no `N of M` line, `Done.`                                                                    | 0    | same as R6b                                                                                        |
| R7  | `-y -a -o out <JUNK>`                                                   | `ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe`             | 1    | `junk.mp4`                                                                                         |
| R8  | `-y -a -p -o out <junk-and-mp4.html>`                                   | two `ERROR:` lines (extraction, then metadata), `1 of 2 … 1 failed`                          | 0    | the junk item `01 - …mp4` and the saved `02 - …mp3`                                                |
| R9  | `-y -o out <JUNK>`, twice                                               | `Download failed` both times                                                                 | 1    | `junk.mp4` **and** `junk [junk].mp4`: each rerun adds a copy, since the leftover has no source tag  |
| R10 | `-y --cookies brave https://fake.test/subs-junk`                        | the cookieless retry runs, `Download failed`                                                 | 1    | `Clip.mp4` and `Clip [v1].mp4`: the retry adds a copy next to the first attempt's body             |
| R11 | `-y -a -p https://fake.test/unprobeable-carousel` (an HTML page as item 1, with a thumbnail) | three `ERROR:` lines, the last `ERROR: 'streams'`                    | 0    | the HTML body `01 - Clip [t1].mp4` (76 B) and the saved `02 - …mp3`                                |

R6 is the strict xfail `test_a_rerun_does_not_take_a_failed_item_as_saved[-s|no-subs]` (`tests/test_e2e.py:10380-10395`), and the most serious: a failure that reads as success. The row's other clauses, re-checked:

- `all-junk.html`, `junk.mp4` in single mode and under `-p`: R1, R2, R3, measured.
- `-y -a -p` leaving the silent video's `.oga` after `1 failed`: R4, measured.
- `-y -a -p mixed-carousel` leaving `02 - Clip [p1].jpg`: R5, measured.
- A partial image or cover after an `OSError` mid-write, and a partial image after Ctrl-C: still there by reading. `_Ydl._write_thumbnails` (`downloader.py:283-306`) removes a cut-off candidate only after the call returns or raises a `RequestError`; yt-dlp's writer (`YoutubeDL.py:4537-4550`) catches only network errors, so an `OSError` from `open` or `copyfileobj`, or a `KeyboardInterrupt`, skips that cleanup. A cover only joins `item_thumbs` after a normal return (`downloader.py:315-322`). Not measured: it needs a full disk or a signal mid-write. Out (D6).
- Ctrl-C mid-extraction leaving a truncated `long.mp3`: spec 23 R5 measured it; nothing in `downloader.py` has touched that path since, and yt-dlp's `FFmpegExtractAudioPP` still writes straight to the final name (`postprocessor/ffmpeg.py:509`). Out (D6).

**What should happen:** an item the run counts as failed leaves nothing this run downloaded for it. A file an earlier run left is never deleted. Under `-p` a failed step ends that item's processing, so no later step can turn the failed body into something that passes for saved.

## Goal

When the tool says an item failed, the folder holds nothing of that item that this run fetched, and a rerun can't take a failed item for a saved one.

## Findings

### What yt-dlp does (2026.10.26, read in the installed source)

- **Under `ignoreerrors: True`**, which `build_ydl_opts` sets for `-p` and `--playlist-items` (`downloader.py:490`), `run_pp` reports a failing step and returns (`YoutubeDL.py:3809-3813`, "Must be True and not 'only_download'"). `run_all_pps` goes on to the next step (`:3826-3831`), then the post hooks run (`:3666-3671`). So after `_TagSource` fails on a junk body, `_EmbedSubtitles` still embeds the subtitle into it (R6a), and after a failed extraction, `_TagSource` and `_EmbedCover` still run on the source (R8, R11).
- **Under `ignoreerrors: 'only_download'`**, yt-dlp's own CLI default (`__init__.py:155`, `options.py:385-387`, "Continue with next video on download errors … (default)"), `run_pp` re-raises; `process_info` catches the `PostProcessingError`, reports `Postprocessing: <reason>` and returns (`YoutubeDL.py:3663-3665`), before any later step and before the post hooks. `report_error` doesn't raise, since the value is truthy (`:1098`), so the list goes on to the next item. That is what single mode already does (there `report_error` raises, `:1098-1100`).
- Every other `ignoreerrors` check in yt-dlp is a truthiness check (`YoutubeDL.py:1098`, `:1748`, `:3541`), so `'only_download'` acts as `True` everywhere except `run_pp`, the subtitle writer (`:4494`) and comment extraction (`extractor/common.py:3896`). The subtitle writer is safe: `_Ydl._write_subtitles` sets `ignoreerrors: True` around each language and restores the previous value (`downloader.py:342-351`). The tool never asks for comments. The probe sets its own `ignoreerrors` (`probe.py:373`) and doesn't download.
- **`__real_download`** is set on the info dict by `process_info`: true when this run downloaded the file, resumed its `.part` included, or merged it (`YoutubeDL.py:3515`, `:3532`, `:3567-3574`, `:3585`); false when it found the file already downloaded (`existing_video_file`, `:3468-3475`). Spec 30's `_ContainerStep` already relies on it (`downloader.py:819-821`). `post_process` sets `info['filepath']` on the same dict, and the steps that change the file (`FFmpegExtractAudioPP`, the container step) update it in place (`postprocessor/ffmpeg.py:527-528`). After a failure, `info_dict['filepath']` is the item's file as it stood when the step failed.
- Images take the `skip_download` path (`YoutubeDL.py:3457-3461`) and never get `__real_download`.

### Prototype

Both halves were tried as monkeypatches from a scratch file (`src/` untouched): `ignoreerrors: 'only_download'` in place of `True`, and a wrapper around `_Ydl.process_info` that deletes `info['filepath']` when the item counted as failed (`item_failed()`, or a `DownloadError` out of it) and `__real_download` is set.

| #   | Prototype, both halves                                                  | Each half alone                                                                                                   |
| --- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| R1, R7 | exit 1, folder empty. A second run: the same, no copy (R9 closed)    |                                                                                                                   |
| R2  | exit 1, folder empty, two `ERROR: Postprocessing: Conversion failed!`   |                                                                                                                   |
| R3, R8 | exit 0, `1 of 2 … 1 failed`, only item 2 on disk; R8 gives one `ERROR:` line instead of two | `'only_download'` alone keeps the junk body                                         |
| R4, R5 | exit 0, same counts, only the `.mp3` files on disk                   |                                                                                                                   |
| R6  | every run: `1 of 2 … 1 failed`, `Done.`, exit 0, only `02 - Clip [s2].mp4` | Either half alone flips the xfail. `'only_download'` alone keeps the 44 B junk body unembedded, so the rerun finds it and fails it again; deletion alone lets the embed run, then deletes the result |
| R10 | exit 1, folder empty                                                    |                                                                                                                   |
| R11 | exit 0, only `02 - Clip [t2].mp3`, one `ERROR:` line, no `ERROR: 'streams'` | Deletion alone keeps the HTML body: `_EmbedCover` raises a `KeyError` (row 43), which isn't a `DownloadError`, so the item never reads as failed to the wrapper |
| P10 | `junk.mp4` pre-placed by hand, then `-y <JUNK>`: exit 1, `junk.mp4` kept byte for byte, `junk [junk].mp4` downloaded and deleted |                                               |
| P11 | item 1's file pre-placed, then `-y -p all-junk.html`: exit 1, item 1's file kept (yt-dlp found it already downloaded), item 2's deleted |                  |
| P15 | `KeyboardInterrupt` raised inside `_TagSource` on `tiny.mp4`: exit 130, `tiny.mp4` kept |                                                                                   |

A targeted subset of `tests/test_e2e.py` (359 tests: `-k "junk or oga or mixed or unprobeable or untagg or failed or fail or rerun or broken or leftover or partial or twice or again"`) under the prototype gave 8 failed, 343 passed, 8 xfailed in 85 s. All 8 are expected: the two strict xfails turn XPASS, and six tests pin the leftover body (the list is under Acceptance criteria). The rest of the suite wasn't run here (memory; other gates running).

### Why not an ffprobe verdict

Spec 29 kept the junk body (its Out) because "deleting on an ffprobe verdict risks deleting media if ffprobe misbehaves". This spec doesn't delete on a verdict. It deletes what the run already counted as failed, and only what the run itself fetched, so a rerun fetches it again. The cost is spelled out in D3.

## Scope

### In

- Under `-p` and `--playlist-items`, a failed post-processing step ends that item's processing (D1).
- An item that counts as failed loses the media file this run downloaded or merged for it, in every mode: single, `-P`, `-p`, `--playlist-items`, the wizard, and each attempt of the cookie fallback (D2).
- The strict xfails `test_a_rerun_does_not_take_a_failed_item_as_saved[-s]` and `[no-subs]` pass.

### Out

- **Ctrl-C and full-disk leftovers** (D6): the truncated audio extraction after Ctrl-C (spec 23 R5), the partial image after Ctrl-C or an `OSError`, and the partial cover after an `OSError`. Different mechanism (no failed item, an exception mid-write), and the cover half overlaps row 43 ("count a thumbnail write `OSError` as saved without a cover"). A Ctrl-C deletes nothing new here (D4). Back to the planner as a new row.
- **`.part` files** of a download cut off by the network. yt-dlp keeps them so a rerun resumes (spec 23, Out); unchanged.
- **`.fNNN` format files** left by a failed merge. yt-dlp's merger deletes them on success; after a failure they may be an earlier run's (`dl` reuses them), and nothing says which. Not measured (a merge that fails needs a broken ffmpeg).
- **Files an earlier run or an earlier version left**, such as a 0.3.0 `junk.mp4`. Never deleted: yt-dlp finds them already downloaded, and the item counts as failed again on each rerun, as on `main`.
- **A body that doesn't fail**: a WebVTT text served as `.webm` saves and says `Done.` (row 74). Nothing fails, so nothing is deleted.
- **The `KeyError: 'streams'` from `_EmbedCover`** (row 43). D1 stops it on this path, since the cover step no longer runs after a failed extraction (R11), but the step itself still doesn't catch it on other paths.
- **`repeated_errors`** (spec 45). It still covers single mode and errors outside post-processing; simplifying it is a refactor for later.
- Docs. `AGENTS.md` and the README say nothing about leftovers.

## Files

| Action | File                               | Lines on `main` (`f0c99a8`)                                                                                          |
| ------ | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| modify | `src/download_media/downloader.py` | `build_ydl_opts` `:488-490` (`ignoreerrors`); `_Ydl.process_info` `:259-281`; a `drop_media` helper next to `drop_thumbs`/`drop_subs` `:368-376`; the `item_failed` comment `:378-380` |
| modify | `tests/test_e2e.py`                | the xfail at `:10380-10395`; the six tests that pin a leftover (`:7166-7177`, `:9323-9330`, `:10018-10029`, `:10418-10429`, `:10629-10637`); new tests, placed after `:10395`, **not at the end of the file** |
| modify | `tests/test_downloader.py`         | one unit test near `test_playlist_mode_changes_output_template` (`:60`)                                               |

No `cli.py`, `i18n.py`, `wizard.py`, `probe.py`, `config.py`, docs or packaging.

### Collisions with what's in flight

`feature/ig-lone-images` (QA, `33a464c`) touches `downloader.py` at `DownloadResult` (`:107-110`), the `_Ydl` class attributes (`:186-196`), `process_video_result` (`:241-257`), `_AvoidNameClash.run` (`:585-589`), the match filter (`:870-898`) and `download()` (`:913-925`, `:980-995`). This item's hunks don't overlap any of them. The nearest is `process_info` (`:259`), two lines after the end of `process_video_result`; git's context may still merge it cleanly, and if not the conflict is a trivial one. **Leave the class attributes at `:186-200` alone** (the `repeated_errors` comment included) so that block stays theirs.

In `tests/test_e2e.py`, ig-lone-images appends 450 lines at the end of the file (`@@ -11356,3 +11364,450`), so new tests here go after the xfail at `:10395`, where they belong anyway. Its `tests/test_downloader.py` hunks are at `:281` and `:316`; the new unit test goes near `:60`.

Semantics: a lone image takes the `skip_download` path and never gets `__real_download`, and its failure is raised from `_write_thumbnails` before `filepath` is set, so `drop_media` never touches it. Whichever item merges second reruns `tests/test_e2e.py` whole.

## Design

### 1. A failed step ends the item (`build_ydl_opts`, D1)

`ydl["ignoreerrors"] = True` becomes `ydl["ignoreerrors"] = "only_download"`, with the comment saying why: it's yt-dlp's CLI default, and a failed step must end the item, as in single mode, so no later step works on a body that already failed (the embed turned a junk body into a subtitle-only mp4 that a rerun took for saved). `mypy --strict` accepts it: `ydl` is `dict[str, Any]`.

Nothing else reads the value as `True`: `_write_subtitles` restores whatever was there (`downloader.py:342-351`), and every other check is truthy (Findings).

### 2. Delete the failed item's download (`_Ydl.process_info`, D2 to D4)

A new method, next to `drop_thumbs` and `drop_subs`:

```python
def drop_media(self, info: Any) -> None:
    # Only a file this run downloaded or merged: one yt-dlp found already downloaded is an
    # earlier run's, which is never deleted.
    path = info.get("filepath")
    if path and info.get("__real_download"):
        Path(path).unlink(missing_ok=True)
```

`process_info` calls it on the same two paths that already call `drop_thumbs` and `drop_subs`, with one difference on the exception path: only a `DownloadError` (single mode's counted failure) deletes the media. A `KeyboardInterrupt`, or any other exception, leaves it (D4):

```python
except BaseException as exc:
    ...
    self.drop_thumbs()
    self.drop_subs()
    if isinstance(exc, DownloadError):
        self.drop_media(info_dict)
    raise
if self.item_failed():
    ...
    self.drop_thumbs()
    self.drop_subs()
    self.drop_media(info_dict)
```

`info_dict` is the dict yt-dlp processed in place (`YoutubeDL.py:3336`, `:3674`), so it carries `filepath` and `__real_download` after the call, raised or not. Deleting is silent (D5): the run already says the item failed.

The `item_failed` comment (`:378-380`) no longer describes the post hooks running after a failed step; reword it to what still holds (an error reported while the item ran, counted once).

### What the user sees

The lines and counts don't change, except the `ERROR:` lines under `-p` and `--playlist-items`: a failed step now prints `ERROR: Postprocessing: <reason>`, as single mode does, and an item that failed in several steps prints one line instead of one per step (R8, R11). The folder no longer holds the failed item.

## Cases

| Case                                                       | Before (`f0c99a8`)                                                    | After                                                                                         |
| ---------------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `-y`, a junk body (R1); `-P` on it is the same single mode | `Download failed`, exit 1, body kept                                  | same output, exit 1, folder empty                                                             |
| direct with preview (`-q 720`), wizard                     | same downloader as `-y`                                               | same as `-y`                                                                                  |
| `-y`, a junk body, twice (R9)                              | two copies                                                            | folder empty after each run                                                                   |
| `-y --cookies <browser>`, junk body (R10)                  | the retry adds a copy: two bodies                                     | folder empty                                                                                  |
| `-p` / `--playlist-items`, all junk (R2)                   | `Download failed`, exit 1, every body kept                            | same, exit 1, folder empty; `ERROR: Postprocessing: …` per item                               |
| `-p`, one junk among saved items (R3)                      | `1 of 2 … 1 failed`, exit 0, junk kept                                | same line and exit, junk gone                                                                 |
| `-p -s`, junk with a subtitle (R6), then a rerun           | junk turned into a subtitle-only mp4; rerun says `Done.` with no `N of M` | `1 of 2 … 1 failed` on every run; no `.vtt` or junk on disk                               |
| `-a`, junk (R7)                                            | `Download failed`, body kept                                          | same, folder empty                                                                            |
| `-a -p`, a silent video (R4), a direct `.jpg` (R5), junk (R8) | the source kept after `1 failed`; R8 two `ERROR:` lines            | source gone; one `ERROR:` line per failed item                                                |
| `-a -p`, an HTML body with a thumbnail (R11)               | body kept, `ERROR: 'streams'`                                         | body gone, cover gone (as today), no `ERROR: 'streams'`                                       |
| a file an earlier run left at the item's path (P10, P11)   | kept                                                                  | kept: in single mode the run downloads `[id]` beside it and deletes only that; under `-p` yt-dlp finds it already downloaded and it's never deleted |
| a saved item, any mode                                     | saved                                                                 | unchanged: nothing fails, nothing is deleted                                                  |
| network failure mid-download                               | `.part` kept                                                          | unchanged (no `filepath` yet)                                                                 |
| Ctrl-C in a post-processing step (P15)                     | file kept, exit 130                                                   | unchanged (D4)                                                                                |
| images (saved, cut off, failed)                            | spec 01's cleanup                                                     | unchanged: no `__real_download`                                                               |
| `-l`                                                       | no download                                                           | unchanged                                                                                     |
| history, equivalent command, exit codes, `N of M`          |                                                                       | unchanged                                                                                     |

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes.
- [ ] The strict xfail mark on `test_a_rerun_does_not_take_a_failed_item_as_saved` is removed, and both `[-s]` and `[no-subs]` pass. Reverting either half alone keeps them passing (Findings); reverting both fails them.
- [ ] The six tests that pin a leftover now assert it's gone, and each fails with the fix reverted:
  - `test_a_failed_video_leaves_no_subtitle_file[subs-junk-…]`: `left` becomes `[]`, and the comment naming row 46 goes.
  - `test_a_message_yt_dlp_does_not_call_an_error_is_no_partial_probe`: one file, not two; the comment citing spec 28's Out goes.
  - `test_a_junk_body_ffprobe_cannot_read_is_not_converted`: `names(out) == []`.
  - `test_a_junk_body_still_fails_when_converted[mkv]` and `[webm]`: `names(out) == []`.
  - `test_several_urls_one_not_converted_and_one_broken`: `["attached.mkv", "webm (1).mp4"]`.
- [ ] New e2e tests, each failing on `main`:
  - `test_a_failed_item_leaves_no_body`, parametrized: `-y <JUNK>` (exit 1, empty), `-y -p all-junk.html` (exit 1, empty), `-y -p junk-and-mp4.html` and `-y --playlist-items 1,2 junk-and-mp4.html` (exit 0, `partial_result 1, 2, 0, 1`, only item 2), and the wizard's picker walk on `junk-and-mp4.html` (only item 2).
  - `test_a_failed_audio_extraction_leaves_no_source`, parametrized: `-y -a -p silent-oga-and-m4a.html` (only the `.mp3`), `-y -a -p mixed-carousel` (`03 - Clip [v1].mp3`, `04 - Clip [a1].mp3`), `-y -a <JUNK>` (exit 1, empty).
  - `test_rerunning_a_failed_url_adds_no_copy`: `-y <JUNK>` twice, folder empty, exit 1 both times.
  - `test_the_cookie_retry_leaves_no_body`: `-y --cookies brave https://fake.test/subs-junk`, exit 1, folder empty.
  - `test_a_failed_step_ends_the_item_under_p`, parametrized `-p -a` / `--playlist-items 1,2 -a` on `junk-and-mp4.html`: exactly one `ERROR:` line (`error_lines`), starting `ERROR: Postprocessing:`; and `-y -a -p unprobeable-carousel`: no `'streams'` in the output, only `02 - Clip [t2].mp3` on disk, `cover_leftovers(out) == []`.
- [ ] New guard tests (they pass on `main` too, and must keep passing):
  - `test_a_failed_item_keeps_a_file_an_earlier_run_left`, parametrized: P10 (single mode, `junk.mp4` pre-placed, kept byte for byte) and P11 (`-p`, item 1's file pre-placed, kept byte for byte, item 2's gone).
  - `test_ctrl_c_in_a_step_keeps_the_download`: `_TagSource.run` raising `KeyboardInterrupt` on `tiny.mp4`, exit 130, `tiny.mp4` kept.
- [ ] Unit, `tests/test_downloader.py`: `build_ydl_opts` gives `ignoreerrors == "only_download"` for `playlist_mode="yes"` (with and without `playlist_items`) and no `ignoreerrors` key otherwise. Fails on `main`.
- [ ] Every other test that passes on `main` still passes, unchanged; the `partial_result` numbers in spec 45's and spec 69's tests don't move.
- [ ] `git diff main -- src/` touches only `downloader.py`, inside the line ranges of the Files table.

## Live check

Not needed. Nothing here depends on a site: the change is in how yt-dlp's post-processing ends an item and in a local file deletion, and the e2e tests run the real yt-dlp and ffmpeg on loopback. A real site serving a non-media body (an HTML login page as a video) can't be produced on demand, and its HTML body is the `unprobeable-carousel` case.

## Open decisions

Each was taken under the overnight delegation on the recommended option; all of them are listed for the human's 0.4.0 review.

### D1: ending an item after a failed step under `-p`

- **A. `ignoreerrors: 'only_download'` (recommended).** yt-dlp's own CLI default, and what single mode already does. One word. Cost: the `ERROR:` text under `-p` gains `Postprocessing: `, and an item that failed in several steps prints one line instead of several.
- B. Override `run_pp` to skip the remaining steps once the item has an error. Re-implements what A gets from yt-dlp.
- C. Don't stop the steps; only delete (D2). R11's HTML body stays (prototype), and the steps keep working on a failed body.

### D2: what a failed item loses

- **A. Its media file at `info['filepath']`, when `__real_download` says this run downloaded or merged it (recommended).** Spec 30's and spec 36's rule: never delete what this run didn't create. Uses the flag spec 30 already relies on.
- B. Every file that appeared in the folder while the item ran. Catches `.fNNN` and partial outputs too, but would delete a file another program writes to the same folder at that moment.
- C. Nothing: today's behaviour, R1 to R11.

### D3: the risk spec 29 named

- **A. Accept it (recommended).** The deletion follows the run's own count of a failed item, not an ffprobe verdict, and only removes what this run fetched, which a rerun fetches again. A misbehaving ffprobe already fails those items today; the difference is that the downloaded bytes go too.
- B. Keep the body when `_TagSource` couldn't get an answer from ffprobe (`_has_media_streams` is `None`). Measured: ffprobe exits 1 on an HTML body (`moov atom not found`, `Invalid data found when processing input`), so B keeps exactly the commonest real junk, a login or error page served as a video.

### D4: Ctrl-C

- **A. A Ctrl-C deletes nothing new (recommended).** Only a counted failure deletes: `item_failed()`, or a `DownloadError` out of `process_info`. A Ctrl-C mid-extraction would otherwise delete a whole downloaded source (spec 23 R5: 29.7 MB, 60 minutes) and keep the truncated output. The truncated output goes to the planner (D6).
- B. Delete on Ctrl-C too. Loses the source, which spec 23 leaves for the user.

### D5: telling the user

- **A. Silent (recommended).** The run already says the item failed; there's no file the user ever had. No new message.
- B. A new message naming the deleted file. Adds two i18n strings for something the user can't act on.

### D6: splitting the row

- **A. This item covers failed items; the Ctrl-C and `OSError` leftovers become a new row (recommended).** Those happen without a failed item, mid-write, and need `try`/`finally` cleanups in `_write_thumbnails` and a guard around `FFmpegExtractAudioPP`'s output, not this item's deletion. The cover half overlaps row 43.
- B. Everything in one item. Two mechanisms, two kinds of test, one review: against "one thing per item".
