# 23 — Cancel exits

Status: done
Item: `fix/cancel-exits`

## Problem

Ctrl-C ends every run in a Python traceback. The exit codes of a cancel and of a wizard refusal depend on where they happen: a cancelled wizard exits 0, even on a closed stdin with nothing saved, while a declined preview exits 1. Two messages also say `Cancelling.` where nothing is cancelled.

Reproduced on `main` at `1e74654`, with yt-dlp 2026.10.26 and Python 3.14.9. The console script `.venv/bin/download-media` ran in its own process group, with `HOME` and all `XDG_*` folders fresh in a scratch folder and a dead D-Bus address. A loopback server served `/fast/<file>` at full speed, `/slow/<file>` at about 64 KB/s, and `/hang/<file>` after 60 s. Its files were a 1.6 MB H.264/AAC `clip.mp4`, a copy named `second.mp4`, and a 60-minute AAC `long.m4a`. "Ctrl-C" means SIGINT sent to the process group, which is what a terminal does (`os.killpg(pid, SIGINT)`).

| #   | Command and moment                                                                     | What happened                                                                                                                                                                                                                                                         |
| --- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | `download-media <fast/clip.mp4>`, Ctrl-C at the URL prompt, and again at `Select [1]:` of the type step | Traceback ending in `KeyboardInterrupt` from `input()`. The process died by SIGINT (`returncode -2`, so a shell shows 130). Nothing on disk, no history row                                                                                         |
| R2  | `download-media <hang/clip.mp4>`, Ctrl-C during `Inspecting content...`                | 30-frame traceback through yt-dlp's `extract_info`, SIGINT death, nothing on disk                                                                                                                                                                                     |
| R3  | `download-media -o <dir> <fast/clip.mp4>`, Ctrl-C at `Proceed with download? [Y/n]:`   | Traceback from `_show_preview` (`cli.py:283`), SIGINT death                                                                                                                                                                                                           |
| R4  | `download-media -y -o <dir> <slow/clip.mp4>`, Ctrl-C at 15 %                           | `[download]  15.1% of 1.64MiB … ETA 00:21Cancelled.`: the line is glued to yt-dlp's progress line, which has no newline. Then a 25-frame traceback and SIGINT death. `clip.mp4.part` (260,667 B) left, no history row, no equivalent command                          |
| R4b | Same command again, on `fast/`                                                         | `[download] Resuming download at byte 260667`, `Done.`, exit 0: yt-dlp resumes its own `.part`                                                                                                                                                                       |
| R5  | `download-media -y -a -o <dir> <fast/long.m4a>`, Ctrl-C 1.5 s after ffmpeg starts      | `[ExtractAudio] Destination: …/long.mp3`, `Cancelled.`, traceback, SIGINT death. Left: `long.m4a` (the source, 29.7 MB) and a truncated `long.mp3` (3,145,728 B; ffprobe reads 346 s of 3600)                                                                         |
| R5b | Same command again                                                                     | `long.mp3` has no source tag, so the rerun saves `long [long].mp3` (3600 s), `Done.`, exit 0. The source and the truncated mp3 stay                                                                                                                                  |
| R6  | `download-media -y -o <dir> <fast/clip.mp4> <slow/second.mp4>`, Ctrl-C at 15 % of the second | First URL `Done.`, saved and logged. Then `…ETA 00:21Cancelled.`, traceback, SIGINT death. `second.mp4.part` left, no command for the first URL                                                                                                     |
| R7  | `download-media <fast/clip.mp4> </dev/null`                                            | `Video URL [...] (c=cancel): Cancelled.`, **exit 0**, nothing saved. A script that forgot `-y` reads success                                                                                                                                                            |
| R8  | `LANG=es_ES.UTF-8 download-media -o <dir> <fast/clip.mp4>`, reply `n`                  | `Cancelado.`, exit 1                                                                                                                                                                                                                                                  |

The existing tests pin the rest. `pytest tests/test_e2e.py tests/test_cli.py -k "<the 11 names below>"` gives 15 passed on `1e74654`:

- A wizard `c` exits 0 (`test_wizard_cancelled_downloads_nothing`, `test_main_cancelled_wizard_exits_clean`).
- A declined preview, or EOF at it, exits 1 (`test_direct_mode_preview_can_be_declined`, `…_treats_eof_as_cancel`, `test_every_url_declined_prints_nothing`, `test_failed_declined_and_saved_urls_in_one_run`).
- A wizard refusal exits 0: a lone image, and every URL refused with `Cancelled.` printed (`test_a_lone_image_is_refused_at_once`, `test_wizard_with_every_url_refused_is_cancelled`).
- Direct mode on a list with nothing downloadable says `No items are downloadable. Cancelling.` and exits 1 (`test_carousel_of_only_images_is_a_failure`; the line is `cli.py:386`).
- `_download_one` prints `Cancelled.` and re-raises (`test_interrupt_propagates_after_cancel_message`, `test_cancelling_after_the_thumbnail_leaves_none`).
- On the same URL the two modes disagree. `-y` on `thumbnail-image`, which the wizard refuses, exits 1 with nothing saved in all five direct modes (`test_direct_modes_keep_their_exit_codes_and_files[…thumbnail-image]`, 5 passed). The wizard exits 0 on it.

**What should happen:** Ctrl-C prints one line and leaves the shell's view of the exit unchanged. Every cancel and every refusal exits non-zero, as a failure does. A message only says "cancel" when the user cancelled.

## Goal

Ctrl-C never shows a traceback. It says whether something unfinished may be on disk, keeps the exit status scripts already see, and prints the equivalent command when a download had started. One exit rule holds across the wizard and direct mode: 0 only when every URL given was saved.

## Scope

### In

- Catch Ctrl-C in `main` and in the per-URL loop. Print one line on a fresh line, then end the process by SIGINT, as today (D1, D2).
- After a Ctrl-C during a download, print the equivalent command for the URLs that reached the download (D3).
- Exit 1 for a wizard cancel (D4) and for a wizard refusal (D5).
- Drop `Cancelling.` from `image_only_skipped` and `no_supported_items`, and stop printing `Cancelled.` when every URL was refused (D6).
- Document the exit status in `AGENTS.md` and `README.md`.

### Out

- **Cleaning up what an interrupted download leaves.** yt-dlp keeps its `.part` file so that a rerun resumes it (R4b, `continuedl` defaults to true in `downloader/common.py:440-447`). The tool wraps that and doesn't delete it. The truncated `long.mp3` of R5 is different. yt-dlp's `FFmpegExtractAudioPP` writes straight to the final name when the extension changes (`postprocessor/ffmpeg.py:509-523`: `temp_path = new_path`), and the rerun then saves a copy beside it (R5b). Read in the source, not measured: a Ctrl-C while an image is written leaves a partial image under its final name. `_Ydl._write_thumbnails` cleans up `image_new` only after a normal return (`downloader.py:211-220`), and a rerun would take that partial image as already saved. Both belong with row 46 (`failure-leftovers`); notes ask the planner to extend it to Ctrl-C.
- **The `-l` path's refusal wording.** `wizard.run` turns a refusal into `None`, so `-l` on a lone image still ends in `Cancelled.` after the refusal line. It takes the new exit code (1) and nothing else. Row 22 (`intent-flags`) makes `-l` skip the wizard.
- **Live streams.** On a live stream, yt-dlp ends the recording at Ctrl-C and still runs the post-processors (`downloader/fragment.py:421-429`, `:516-518`). The tool then sees a normal return, says `Done.` and exits 0. That's yt-dlp's design, and it stays.
- **`partial_result` runs** (some items saved) keep exit 0. They're warned, and the rule is "every URL saved", not "every item".
- **Line order when redirected** (the second URL's `Extracting URL` above the first's `Done.` in R6) is row 52.

## Files

| Action | File                               | What                                                                                                                                                       |
| ------ | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| modify | `src/download_media/cli.py`        | imports (`:10-15`: `os`, `signal`); `_Interrupted` and `EXIT_INTERRUPTED` near `_Outcome` (`:320-323`); `_download_one`'s `except KeyboardInterrupt` (`:371-373`); `main` (`:534-580`); new `entry()`; `__all__` (`:585`) |
| modify | `src/download_media/__main__.py`   | call `entry()`                                                                                                                                             |
| modify | `pyproject.toml`                   | `[project.scripts]` (`:47`): `download-media = "download_media.cli:entry"`                                                                                  |
| modify | `src/download_media/i18n.py`       | add `interrupted` after `cancelled` (`:41`, `:141`); reword `image_only_skipped` and `no_supported_items` (`:105-106`, `:205-206`)                          |
| modify | `AGENTS.md`                        | Execution modes: exit status and Ctrl-C                                                                                                                    |
| modify | `README.md`                        | a short `## Exit status` section after `## All options`                                                                                                    |
| modify | `tests/test_cli.py`                | the unit tests below; replace `test_interrupt_propagates_after_cancel_message` (`:402-412`); flip `test_main_cancelled_wizard_exits_clean` (`:661-667`)     |
| modify | `tests/test_e2e.py`                | the e2e tests below; flip the exit code of every wizard cancel and refusal test; `test_cancelling_after_the_thumbnail_leaves_none` (`:2800-2811`)           |
| modify | `tests/test_packaging.py`          | the console-script pin                                                                                                                                     |
| modify | `tests/test_i18n.py`               | the wording pin                                                                                                                                            |

`wizard.py` doesn't change. Its prompts and `probe()` already let `KeyboardInterrupt` through: `_walk_plan` and `run_all` catch only `_Back`, `_Cancel` and `_Refused` (R1, R2).

**Collisions.** container-choice adds two lines to `_download_one`'s warning block (`cli.py:392-393` on its branch, after the `except` chain). It also adds `not_converted` to `i18n.py`, and cookies-undecrypted adds a key after `cookies_unreadable`. Neither touches the chain, `main`, the three keys edited here or `pyproject.toml`. m4a-cover has no branch, and its spec touches only `downloader.py` and tests. After those three merge, the line numbers move by a few: `main` by +2, the `i18n.py` refusal keys by +2 to +3. Re-measure, the text is unchanged. The ~20 e2e tests whose exit code flips may sit next to tests those items add; the collisions are textual only.

## Design

### Ctrl-C (D1, D2, D3)

`cli.py`:

```python
EXIT_INTERRUPTED = 130  # What a shell reports for a process ended by SIGINT.

class _Interrupted(KeyboardInterrupt):
    """Ctrl-C during a download: that URL's outcome, for the equivalent command."""
    def __init__(self, outcome: _Outcome) -> None: ...
```

- **`_download_one`**: the `except KeyboardInterrupt` at `:371-373` becomes `print()`, then `warn(t("interrupted", url, effective.resolve_output()))`, then `raise _Interrupted(_Outcome(False, False, pmode)) from None`. A Ctrl-C at the playlist question (`:330`) or the preview (`:343`) is not caught here. It leaves `_download_one` as a plain `KeyboardInterrupt`.
- **`main`**: the body moves to a helper, and `main` wraps it. Any `KeyboardInterrupt` that reaches it (a wizard prompt, the probe, `-l`, the clipboard, or a second Ctrl-C while the closing lines print) gives `print()`, `warn(t("cancelled"))` and `return EXIT_INTERRUPTED`.
- **The download loop** (`:575`) becomes an explicit loop:
  - `_Interrupted` appends its outcome and stops the loop.
  - A plain `KeyboardInterrupt` (preview, playlist question, or between two URLs) prints `print()` and `warn(t("cancelled"))`, appends `_Outcome(False, False, None)` and stops.
  - URLs never reached get `_Outcome(False, False, None)`, so `_print_equivalent`'s `zip(strict=True)` holds.
  - Then the usual tail runs: the `wizard_skipped` lines and `_print_equivalent`. Then `return EXIT_INTERRUPTED`.
- **`entry()`** is the console script and `python -m download_media`:

  ```python
  def entry() -> NoReturn:
      code = main()
      if code == EXIT_INTERRUPTED:
          sys.stdout.flush(); sys.stderr.flush()
          signal.signal(signal.SIGINT, signal.SIG_DFL)
          os.kill(os.getpid(), signal.SIGINT)
      sys.exit(code)
  ```

  The flush is required. Measured: a child that prints to a pipe and then kills itself loses the line without it (`stdout=''`), and keeps it with it. `main` stays the in-process API, and the tests call it, so no test can kill pytest. Nothing in the tool or in yt-dlp needs an `atexit` handler on Linux: the only one in yt-dlp is its Windows updater (`update.py:555`).
- **The fresh line.** `print()` before the message, always. After a prompt or a progress line, the cursor sits mid-line (R4). yt-dlp's own CLI does the same: `'\nERROR: Interrupted by user'` (`__init__.py:1085-1086`).
- **History:** unchanged. The row is written after `Done.`, so an interrupted URL gets none, even when `-p` had saved some of its items. Earlier URLs keep theirs (R6).
- **yt-dlp needs no change and gets none.** KeyboardInterrupt passes through `ignoreerrors`: `__download_wrapper` (`YoutubeDL.py:3677-3690`) and `_handle_extraction_exceptions` catch named exceptions only. So Ctrl-C under `-p` stops the whole run, as without it. `_Ydl.process_info` already drops the item's covers on any `BaseException` (`downloader.py:189-195`, pinned by `test_cancelling_after_the_thumbnail_leaves_none`). `Popen.communicate_or_kill` kills ffmpeg if Python gets the signal alone (`utils/_utils.py:904-909`).

### Exit codes (D4, D5)

The rule `main` implements, which `AGENTS.md` and `README.md` will state:

| Status                            | When                                                                                                                                         |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 0                                 | Every URL given was saved (warnings allowed)                                                                                                 |
| 1                                 | Any URL wasn't saved: it failed, the wizard refused it, or it was cancelled or declined at a prompt (`c`, `n`, EOF). Also a missing or empty URL |
| 2                                 | Usage error (argparse)                                                                                                                       |
| ended by SIGINT (130 in a shell)  | Ctrl-C                                                                                                                                       |

In `main`:

- The three `return 0` after `warn(t("cancelled"))` (`:546`, `:552`, `:557`) become 1. The third disappears under D6.
- The final line becomes `return 0 if outcomes and not refused and all(o.ok for o in outcomes) else 1`.

### Wording (D6)

| Key                  | es (new)                                                   | en (new)                                                        |
| -------------------- | ---------------------------------------------------------- | --------------------------------------------------------------- |
| `interrupted` (new)  | `Interrumpido mientras se descargaba {0}. Pueden quedar archivos sin terminar en {1}.` | `Interrupted while downloading {0}. Unfinished files may be left in {1}.` |
| `image_only_skipped` | `El contenido es una imagen y no es soportado por yt-dlp.` | `This content is a standalone image, not supported by yt-dlp.` |
| `no_supported_items` | `Ningún item es descargable.`                              | `No items are downloadable.`                                    |

Only ` Cancelando.` and ` Cancelling.` are removed. Row 02 rewords other keys, not these. In `main`, when every walked URL was refused, the `if not runs: warn(t("cancelled")); return 0` branch goes away. The run falls through to the `wizard_skipped` lines, which already name each refused URL, and to exit 1. `_print_equivalent` prints nothing, since no URL reached the download.

The reword also fixes `step_select` (`wizard.py:335-337`). A pick of only unsupported items warns `no_supported_items` and asks again, so the old `Cancelling.` was false there too.

The `interrupted` line says "may" because it's true in every case. Depending on the moment, the folder may hold a `.part` file (resumable), yt-dlp's `.fNNN` format files or `.temp` file, a source next to a truncated output (R5), or nothing at all.

## Cases

| Case                                                        | Before (`1e74654`)                                                                  | After                                                                                                                                       |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Wizard, Ctrl-C at any prompt, any walk                      | Traceback, SIGINT death, nothing on disk (R1)                                       | Blank line, `Cancelled.`, SIGINT death. Nothing on disk, no history row, no command                                                         |
| Wizard, Ctrl-C during the probe                             | Same (R2)                                                                           | Same as the line above                                                                                                                      |
| Wizard, `c` or EOF, single or several URLs                  | `Cancelled.`, exit 0 (R7)                                                           | `Cancelled.`, **exit 1**                                                                                                                    |
| Wizard, every URL refused                                   | Refusal lines, `Cancelled.`, exit 0                                                 | Refusal lines without `Cancelling.`, one `wizard_skipped` per URL, **exit 1**, no command                                                   |
| Wizard, one URL refused and one saved                       | `wizard_skipped`, exit 0                                                            | Same lines, **exit 1**                                                                                                                      |
| Direct, Ctrl-C at the preview                               | Traceback (R3)                                                                      | `Cancelled.`, SIGINT death. Later URLs don't run. The command lists the earlier URLs that reached the download                              |
| Direct, `n` or EOF at the preview                           | `Cancelled.`, exit 1 (R8)                                                           | Unchanged                                                                                                                                   |
| Direct, playlist question: EOF / Ctrl-C                     | `Cancelled.`, exit 1 / traceback                                                    | Unchanged / `Cancelled.`, SIGINT death                                                                                                      |
| Ctrl-C during a download (direct, `-y`, wizard)             | `Cancelled.` glued to the progress line, traceback, `.part` left, no command (R4)  | Fresh line, `interrupted` naming the URL and folder, `.part` left (a rerun resumes, R4b). Command for the URLs that reached the download, this one included. No history row for it. SIGINT death |
| Ctrl-C during post-processing                               | Same, plus the source and a truncated output left (R5)                              | `interrupted`. Files left as yt-dlp leaves them (Out, row 46)                                                                              |
| `-p`, Ctrl-C mid-list                                       | Traceback, earlier items on disk                                                    | `interrupted`, earlier items on disk, no `partial_result`, no history row, command with `-p`, SIGINT death                                  |
| Between URLs (first `Done.`, second running)                | First saved and logged, traceback, no command (R6)                                 | First saved and logged. `interrupted` or `Cancelled.` for the second, depending on whether its download had started. Command for what reached the download. Later URLs neither run nor listed |
| Ctrl-C during the cookie retry (`cookies_retry`)            | Traceback                                                                           | A download Ctrl-C                                                                                                                          |
| Preview with an unreadable browser, then `n`                | Notice `Going on without cookies.`, title, `Cancelled.`, exit 1                     | Unchanged (D7)                                                                                                                             |
| Direct, a list with nothing downloadable                    | `No items are downloadable. Cancelling.`, exit 1                                    | `No items are downloadable.`, exit 1                                                                                                       |
| `-l`, Ctrl-C                                                | Traceback                                                                           | `Cancelled.`, SIGINT death                                                                                                                 |
| Wizard `-l`, `c`, or a lone image                           | `Cancelled.`, exit 0                                                                | `Cancelled.`, exit 1 (Out: wording)                                                                                                        |
| Second Ctrl-C while the closing lines print                 | n/a                                                                                 | `Cancelled.`, SIGINT death                                                                                                                 |
| `-y` in a bash `for` loop, Ctrl-C                           | The child dies by SIGINT and bash stops the loop                                    | Unchanged (D1, measured)                                                                                                                   |
| Live stream, Ctrl-C                                         | yt-dlp ends the recording, `Done.`, exit 0                                          | Unchanged (Out)                                                                                                                            |
| Rerun after an interrupt                                    | Resumes the `.part` (R4b); after R5, a second copy (R5b)                           | Unchanged (Out, row 46)                                                                                                                    |

There are no separate rows for cookies or carousels. With cookies, the only path that differs is the retry, listed above. A carousel is a `-p` run, and its images are a download Ctrl-C.

## Acceptance criteria

`[fix]` fails on `main`, shown by reverting the fix. `[pin]` passes before and after. Unit tests script prompts with the `answers` fixture, which already raises exception types. Anything that runs yt-dlp is on `fake_site` or `media_url`.

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** `test_ctrl_c_mid_download_names_the_url_and_folder` (`test_cli.py`, replaces `test_interrupt_propagates_after_cancel_message`). `download` raises `KeyboardInterrupt`. `_download_one` raises `cli._Interrupted`, an instance of `KeyboardInterrupt`, with `.outcome == _Outcome(False, False, "no")`. Stdout has `"\n" + t("interrupted", url, folder)` and no `t("cancelled")`
- [x] **[fix]** `test_main_returns_the_interrupt_status_after_ctrl_c_in_the_wizard` (`test_cli.py`). `wizard.run_all` raises `KeyboardInterrupt`: `cli.main([]) == cli.EXIT_INTERRUPTED == 130`, `t("cancelled")` on its own line, no `Traceback` in stderr
- [x] **[fix]** `test_entry_dies_by_sigint_after_an_interrupt` (`test_cli.py`). `cli.main` is patched to return 130, and `signal.signal` and `os.kill` to record. `entry()` sets `SIG_DFL` for SIGINT and sends SIGINT to its own pid. With 0 or 1 it raises `SystemExit` with that code and sends nothing
- [x] **[fix]** `test_ctrl_c_ends_the_process_by_sigint_without_a_traceback` (`test_cli.py`). A real subprocess, `sys.executable -m download_media -i http://127.0.0.1:9/x`, in its own session, with `PYTHONUNBUFFERED=1` and stdin a pipe kept open. Once the URL prompt is read from stdout, SIGINT goes to its group (only the process the test started). Then: `returncode == -signal.SIGINT`, `t("cancelled")` in stdout, no `Traceback` in stderr
- [x] **[fix]** `test_the_console_script_runs_the_interrupt_aware_entry` (`test_packaging.py`). `pyproject.toml`'s `download-media` script is `download_media.cli:entry`
- [x] **[fix]** `test_ctrl_c_at_a_wizard_prompt_downloads_nothing` (e2e). `wizard_on(VIDEO, "", KeyboardInterrupt)`: 130, `t("cancelled")`, nothing on disk, no history row, no `t("equiv_cmd")`, no `Traceback`
- [x] **[fix]** `test_ctrl_c_during_the_wizard_probe_downloads_nothing` (e2e). `wizard.probe` patched to raise `KeyboardInterrupt`: same assertions
- [x] **[fix]** `test_ctrl_c_mid_download_keeps_earlier_urls_and_prints_their_command` (e2e). `-y VIDEO VIDEO_B`, with `YoutubeDL.dl` raising `KeyboardInterrupt` on the second URL only. 130, the first URL's file only, one history row (`VIDEO`), `t("interrupted", VIDEO_B, out)`, no `t("cancelled")`. One command listing both URLs, with `-y -o {out}`
- [x] **[fix]** `test_ctrl_c_at_the_second_preview_prints_the_first_url_s_command` (e2e). Direct `VIDEO VIDEO_B`, `answers("", KeyboardInterrupt)`: 130, `t("cancelled")`, the first file only, a command listing `VIDEO` only
- [x] **[fix]** `test_ctrl_c_mid_playlist_stops_the_run` (e2e). `-y -p CAROUSEL`, `dl` raising on the second downloadable item. 130, the first item on disk, no `partial_result`, no history row, the command carries `-p`
- [x] **[fix]** `test_ctrl_c_during_post_processing_says_files_may_be_left` (e2e, `media_url`). `-y -a` on `audio.html`, with `FFmpegExtractAudioPP.run_ffmpeg` raising `KeyboardInterrupt`: 130, `t("interrupted", …)`, no `t("done")`, no history row. The downloaded source is still on disk: the tool doesn't delete yt-dlp's files
- [x] **[fix]** `test_cancelling_after_the_thumbnail_leaves_none`: `cli.main(...) == cli.EXIT_INTERRUPTED` instead of `pytest.raises(KeyboardInterrupt)`. `names(out) == []` stays
- [x] **[fix]** Exit-code flips, with no other assertion changed:
  - `test_wizard_cancelled_downloads_nothing`, `test_main_cancelled_wizard_exits_clean` (renamed `…_exits_1`), `test_a_lone_image_is_refused_at_once` and `test_wizard_skips_a_refused_url_and_says_so_at_the_end` now expect 1.
  - `test_a_refusal_before_a_saved_url_still_exits_clean` is renamed `…_exits_1` and expects 1.
  - `test_wizard_with_every_url_refused_is_cancelled` is renamed `test_wizard_with_every_url_refused_names_them_and_exits_1`: 1, no `t("cancelled")`, `t("wizard_skipped", url)` for both.
  - Every other wizard cancel or refusal test that asserts 0 flips to 1. These include `test_cancel_in_the_second_walk_cancels_the_whole_run`, `test_cancel_at_any_prompt_of_a_later_walk_downloads_nothing` and `test_cancelling_after_a_missing_browser_downloads_nothing`, about 20 in all, some parametrized. The developer lists them in the report, and every other failing test is a regression.
- [x] **[fix]** `test_refusals_do_not_say_cancelling` (`test_i18n.py`). In both blocks, `image_only_skipped` and `no_supported_items` contain neither `Cancel` nor `Cancelando`
- [x] **[pin]** `test_direct_mode_preview_can_be_declined`, `test_direct_mode_preview_treats_eof_as_cancel`, `test_failed_declined_and_saved_urls_in_one_run`, `test_carousel_of_only_images_is_a_failure`, `test_base_exceptions_are_not_swallowed`, `test_listing_formats_lets_base_exceptions_through` pass unchanged
- [x] `interrupted` exists in both blocks (`test_es_and_en_have_same_keys`)
- [x] **Console check** on loopback, after `pip install -e .`, which regenerates `.venv/bin/download-media` from the new `[project.scripts]`. Repeat R1 and R4 with the console script in its own process group and SIGINT sent to the group:
  - R1: a blank line, `Cancelled.`, no `Traceback`, `returncode -2`.
  - R4: `Interrupted while downloading <url>. Unfinished files may be left in <dir>.` on its own line, then `Equivalent command…` with the `-y` command, no `Traceback`, `returncode -2`, `clip.mp4.part` on disk, no history row.
  - With `LANG=es_ES.UTF-8`: `Cancelado.` and `Interrumpido mientras se descargaba …`.

## Live check

Not needed. Nothing here depends on a site. Signals, exit codes and messages are local, and yt-dlp's interrupt paths are the same for every extractor. The console check above runs the real process path: the entry point, signal delivery to the process group, and ffmpeg in the group for R5. A terminal's Ctrl-C sends the same SIGINT to the same group.

## Validation

On `main` at `cf87835` (merge `5b41234`, then `.ai/` commits only: `git diff --stat 5b41234 HEAD -- src tests pyproject.toml` is empty), yt-dlp 2026.10.26, Python 3.14.9. GitHub CI on `cf87835` was still running when this was written.

- **Gate:** not re-run. The tech-lead's `scripts/pre-release.sh --no-live` on the merge `5b41234`: `All checks passed.`, exit 0, 2339 passed, 9 deselected, 7 xfailed. No file outside `.ai/` has changed since.
- **Criteria tests:** a `pytest -k` selection of every test the criteria name, `[fix]` and `[pin]`, including the renamed exit-code flips and `test_es_and_en_have_same_keys`, ran on `main`: 48 passed, each name confirmed in the `-rA` output. The revert proof of the `[fix]` tests is the test-engineer's.
- **Merged tests:** every test function in either parent of `5b41234` is at the merge, except seven, and none is new. Each is a rename or replacement made on purpose by its own commit, and its successor is on `main`:
  - From this branch, all as this spec asks: `test_interrupt_propagates_after_cancel_message` becomes `test_ctrl_c_mid_download_names_the_url_and_folder` (`eeefe62`). `test_main_cancelled_wizard_exits_clean` and `test_a_refusal_before_a_saved_url_still_exits_clean` become `…_exits_1` (`67d5e21`). `test_wizard_with_every_url_refused_is_cancelled` becomes `…_names_them_and_exits_1` (`ee9278a`).
  - From `main`: `test_a_keyring_that_answers_and_decrypts_nothing_stays_as_it_was` becomes `…_is_named` (`feb1576`, spec 67). `test_wizard_keeps_an_ogx_and_reports_it_untagged` becomes `test_wizard_converts_an_ogx_video_and_tags_it`, and `test_wizard_keeps_audio_in_such_a_container_raw_when_nothing_says_it_is_audio` drops `raw_` (`862f4df`, spec 30, whose message lists both).
  - The branch's whole diff to `src/` and `tests/` reaches the merge line for line. `main`'s does too, apart from hunks that only realign.
- **Console check:** `main`'s real console script, `.venv/bin/download-media`, which imports `entry`. Each run had its own process group (`start_new_session`), stdin a pipe kept open, a fresh `HOME`, all `XDG_*` folders and `XDG_RUNTIME_DIR` in the scratchpad, and a dead D-Bus address. A scratch `http.server` served an 8 s, 1.5 MB H.264/AAC `clip.mp4` under `/fast/` and under `/slow/` (about 64 KB/s). "Ctrl-C" is `os.killpg(pid, SIGINT)`, sent only to the group the driver started. Everything was deleted afterwards.

| Run | Output | Exit | Disk, history |
| --- | ------ | ---- | ------------- |
| R1, `download-media`, Ctrl-C at `Video URL (c=cancel):` | `\nCancelled.` on its own line, no `Traceback` | `-2` | nothing, 0 rows |
| R1, `download-media <fast>`, Enter at the URL, Ctrl-C at `Select [1]:` of the type step | same | `-2` | nothing, 0 rows |
| same with `LANG=es_ES.UTF-8` | `Selecciona [1]: \nCancelado.` | `-2` | nothing, 0 rows |
| R4, `-y -o <dir> <slow>`, Ctrl-C at about 20 % | progress line, newline, `Interrupted while downloading <url>. Unfinished files may be left in <dir>.`, then `Equivalent command…` with `download-media -y -o <dir> <url>`, no `Traceback` | `-2` | `clip.mp4.part` (325,932 B), 0 rows |
| same with `LANG=es_ES.UTF-8` | `Interrumpido mientras se descargaba <url>. Pueden quedar archivos sin terminar en <dir>.`, `Comando equivalente…` | `-2` | `clip.mp4.part`, 0 rows |
| R4b, the printed command on R4's folder, served from `/fast/` | `Resuming download at byte 325932`, `Done.` | 0 | one `clip.mp4`, 8.0 s, decodes clean |
| Wizard `c`, `download-media <fast>`, `c` at the URL prompt | `Cancelled.` | **1** | nothing, 0 rows |
| Saved, `-y -o <dir> <fast>` | `Done.`, the command | 0 | one `clip.mp4`, 1 row |

- **Cases, tests only:** the probe, the preview, `-p`, post-processing, a second URL, a second Ctrl-C, `| tee`, refusals and the wizard's exit-code flips. Each is a criterion test or one of the test-engineer's additions, all in the gate. The `| tee` and second-Ctrl-C paths were also run for real by the test-engineer (28 runs), not here.
- **Scope:** `3419952..02346f3` touches exactly the ten files in the Files table. No `wizard.py`, `PKGBUILD` or version change.

### What changed after approval

- **Code, beyond Design:** two guards in `entry()`, from the test-engineer's round 1.
  - **The flush** (`043c0b3`). With `download-media … | tee log`, the same Ctrl-C kills `tee` first, so the flush before the self-kill raised `BrokenPipeError`: a traceback, exit 120, and a calling bash loop went on. A flush that fails now points the stream at `/dev/null`, and the self-kill goes ahead.
  - **A second Ctrl-C** (`6667735`). One that arrives while `main` prints its cancel line escaped `main` and showed a traceback before the SIGINT death. `entry()` now catches `KeyboardInterrupt` around `main()` and treats it as 130.
- **Known window, QA, read in the code:** `ok(t("done"))` (`cli.py:414`) and `history.append` (`:418-422`) sit after the `try`. A Ctrl-C in the few milliseconds between them reaches the loop's plain `except KeyboardInterrupt`. It prints `Cancelled.`, writes no history row, and leaves that URL out of the equivalent command, although its file is saved. It still ends by SIGINT. Not fixed; it's in `PLAN.md`'s known residuals.
- **Minor, QA:** `outcomes and` in the final `return` is redundant: `outcomes` is empty only when `runs` is, and then `refused` isn't. It's harmless.
- **Spec text:** none. The 0.4.0 review items below moved to `PLAN.md` as items 29 to 33.

## Decisions

All are recommended under the overnight delegation. **None is breaking under `AGENTS.md`**, whose list covers renamed flags, changed defaults and moved paths or state. Spec 45's exit 0 → 1 (0.4.0 review item 12) set the precedent that an exit-code change isn't on that list. Each change below still goes on the 0.4.0 review list, since scripts read exit codes.

### D1. The exit status of Ctrl-C

Measured with a `bash -c 'for i in 1 2 3; do <child>; done'` loop, SIGINT sent to the group after 1 s:

| Option | What scripts see | Measured |
| --- | --- | --- |
| **A. Print a line, then end by SIGINT, through `entry()` (recommended)** | Exactly today's status: a shell shows 130, and a calling bash loop stops | `selfkill`: bash ended by SIGINT, loop stopped, same as `uncaught` (today) |
| B. `return 130` from `main` | A shell still shows 130, but bash assumes the child handled SIGINT and **keeps looping**: one Ctrl-C per URL | `exit130`: `child 1 status 130`, then the second child started; the loop had to be killed |
| C. Exit 1, as yt-dlp's CLI does (`__init__.py:1085`) | Same loop problem as B, and Ctrl-C reads as a download failure | Not run; same mechanism as B |

A is the only option that removes the traceback without changing what a script sees. It costs one function and the `[project.scripts]` target. Python's own handling of an uncaught interrupt kills the process by SIGINT for the same reason.

### D2. What Ctrl-C says

- **A. `Cancelled.` when nothing was downloading, and the new `interrupted` line, with the URL and folder, during a download (recommended).** The only Ctrl-C that can leave something on disk says so and where. One new message.
- B. `Cancelled.` everywhere. No new message, but after R4 or R5 the user isn't told that a `.part` file or a truncated mp3 sits in the folder.

### D3. The equivalent command after Ctrl-C

- **A. Print it for the URLs that reached the download, the interrupted one included (recommended).** This is `AGENTS.md`'s rule as written: "after every run that reached the download, whether it worked or not". Running the command resumes the `.part` (R4b). URLs never reached are left out, as after a declined preview.
- B. Print nothing after Ctrl-C. Simpler, but it drops the wizard's choices for a download the user may want to resume, and it adds an exception to `AGENTS.md`.
- C. Also list the wizard URLs that were walked but never reached. That would resume the whole run, but it stretches "reached the download", and direct-mode URLs are already in the shell's history.

### D4. The exit code of a cancel

- A. Keep it: the wizard exits 0, the preview 1. Inconsistent, and R7: a script that forgot `-y` gets exit 0 with nothing saved, against "a failure never reads as success".
- **B. Every cancel exits 1 (recommended).** The wizard's `c` and EOF, the `-l` wizard, and the preview and playlist question as today. This matches the preview's pinned 1, and pacman and apt, which exit 1 on `n` at their prompt. The cost: an interactive `c` sets `$?` to 1, which a user only sees if their prompt shows it.
- C. Every cancel exits 0. The preview flips 1 → 0, and `download-media -o x <url> </dev/null` would read as success too.
- D. A separate code for a cancel (say 3). That's a new published code, and no script is known to need the difference.

**For the 0.4.0 review:** this is the most visible change here. If the human prefers A, the code to change is the three `return` lines in `main`, plus the flipped tests.

### D5. The exit code of a wizard refusal

- **A. A refused URL counts as not saved, so exit 1, alone or among saved URLs (recommended).** Direct mode does exactly this on the same URLs (`thumbnail-image`: exit 1 in all five modes). Spec 19 D6 left refusals at 0 for now and handed both cases to this row.
- B. Exit 1 only when every URL was refused, and 0 in a mix. That keeps spec 19's D6 but makes the rule depend on the other URLs.
- C. Keep 0. The wizard and `-y` keep disagreeing on the same URL.

### D6. The refusal wording

- **A. Drop ` Cancelling.` / ` Cancelando.` from both messages, and print `wizard_skipped` instead of `Cancelled.` when every URL was refused (recommended).** Nothing is cancelled in those cases: the run goes on to the next URL, or `step_select` asks again, or direct mode reports a failure.
- B. Keep the text, as spec 19 did, reading "Cancelling." as "this URL". That still leaves it false in `step_select` and in direct mode.

### D7. The preview's cookie notice, then `n`

- **A. Keep it (recommended).** The notice (`… Going on without cookies.`) says what confirming will do, then the user declines. `Cancelled.` and exit 1 follow, as for any declined preview. Nothing contradicts.
- B. Reword the notice at the preview only. That needs a new message for little gain.

## Review list for 0.4.0

In `PLAN.md`'s "Next release" list as items 29 to 33 since validation, in the order D4, D5 and D6, D1 to D3, the wording, the entry point. None is breaking by `AGENTS.md`'s list.

1. Ctrl-C prints one line instead of a traceback, and still ends the process by SIGINT (a shell shows 130, and a calling loop stops). After a Ctrl-C during a download, the line names the URL and folder, and the equivalent command is printed (D1–D3). New message: `interrupted`.
2. A cancelled wizard (`c`, EOF, `-i -l` included) exits 1 instead of 0 (D4).
3. A wizard refusal exits 1 instead of 0, also when other URLs saved, which replaces spec 19 D6's interim answer. When every URL is refused, `Cancelled.` gives way to one `wizard_skipped` line per URL (D5, D6).
4. `image_only_skipped` and `no_supported_items` no longer end in `Cancelling.` / `Cancelando.` (D6).
5. The console script now points at `download_media.cli:entry`. The `download-media` command and the PKGBUILD don't change, since the wheel writes the script. `cli.main()` called in-process returns 130 on Ctrl-C instead of raising. That is not a public API.

Release note: "Ctrl-C no longer ends in a traceback. It says what was interrupted and where unfinished files may be, and prints the command to resume. Cancelling the wizard, or a URL the wizard can't save, now exits 1, as it already did in direct mode, so a script can tell that nothing was saved."

## Open decisions

None that needs the human before development: nothing is breaking by `AGENTS.md`, and D1–D7 were taken on the recommended options under the delegation. D4 is the one to look at in the 0.4.0 review (item 2 above).
