# 19 — Stop the wizard dropping every URL after the first

Status: draft
Item: `fix/wizard-multi-url`

## Problem

With several URLs and no intent flag, the wizard walks the first one, downloads it, says `Done.`, exits 0, and never mentions the others.

Reproduced on `main` at `6d8ffb9`, with yt-dlp 2026.10.26 and Python 3.14.9. The real console script ran against a loopback `http.server` serving two copies of a tiny mp4, with `HOME` and `XDG_*` pointed at a temp folder and no clipboard:

```bash
printf '\n\n\n\n%s\n\n' <folder> | download-media http://127.0.0.1:18931/a.mp4 http://127.0.0.1:18931/b.mp4
# Download wizard
# Video URL [http://127.0.0.1:18931/a.mp4] (c=cancel):
# ...type, quality, container, folder, cookies...
# Done.
# Equivalent command to repeat this download:
#   download-media -y -o <folder> http://127.0.0.1:18931/a.mp4
# exit 0. On disk: a.mp4 only. History: one row, a.mp4.
```

- **What happened:** `wizard.py:177` (`opts.urls = [new_url]`) replaces the URL list with the one URL the prompt showed. `b.mp4` is never probed, downloaded, logged, printed or mentioned.
- **What should happen:** every URL is downloaded, or the run says which one wasn't and why.

`-i -o <folder> a b` does the same: one walk, `a.mp4` only, exit 0. The direct mode (`-o <folder> a b`) previews both, saves both and prints one command with both URLs, so the bug is the wizard's alone. It goes back to the Bash prototype (`legacy/bin/download-media`, `URLS=("$url_input")`).

## Goal

With several URLs, the wizard walks each URL in turn, with its own probe, cookie prompt and item picker, then downloads them all. What it reports, logs and prints matches what is on disk.

## Findings

Every result comes from a run: either the console script on loopback, as above, or `cli.main` with the real yt-dlp and ffmpeg on `fake_site`, `HOME` under `tmp_path`. "Prototype" means the design below applied to a scratch copy of the tree, with `src/` left untouched.

### On `main`

| #   | Run (no intent flag unless shown)                 | Result on `main`                                                                                              |
| --- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| F1  | `video video-b`                                   | `Clip.mp4` only, one history row, exit 0, the command lists `video` alone                                     |
| F2  | `carousel video`                                  | The picker for the carousel, its 2 files, and the video is never asked about                                 |
| F3  | `video needs-cookies`                             | The second URL's login wall is never probed, so no cookie prompt (spec 18) ever shows                         |
| F4  | `video ig-image`                                  | The image is never refused, just dropped                                                                     |
| F5  | `-i -o <dir> video video-b`                       | Same as F1                                                                                                    |
| F6  | No URL, typed `a.mp4 b.mp4` at the prompt (loopback) | One URL with a space in it: the probe fails with a 404, manual flow, `Download failed`, exit 1. The printed command quotes both as one URL |
| F7  | Same, typed `video video-b` (`fake_site`)         | `Clip.mp4`, exit 0, the second silently gone. yt-dlp matches `_VALID_URL` with `re.match` (`extractor/common.py:624`), so an unanchored pattern takes the first URL as a prefix |
| F8  | `-l video video-b`                                | Six wizard prompts, then lists `video` only. Row 22 (`intent-flags`) owns both halves of this                |

What direct mode does with several URLs today, measured on `fake_site`. It doesn't change here:

| Run                                   | Result                                                                                     |
| ------------------------------------- | ------------------------------------------------------------------------------------------ |
| `-y -o <dir> video video-b`           | `Clip.mp4`, `Clip [v2].mp4`, two history rows, exit 0, one command with both URLs          |
| `-y -o <dir> video image`             | `Clip.mp4`, `Download failed: …/image`, exit 1, both URLs in the command                   |
| `-y -o <dir> video needs-cookies`     | `Clip.mp4`, `Download failed`, exit 1. Direct mode has no cookie prompt                    |
| `-o <dir> video carousel`, `n` then Enter | First preview declined (`Cancelled.`), carousel saved, exit 1, command lists the carousel only |

### The prototype

- The full suite, minus `test_packaging.py` (it reads `PKGBUILD`, absent from the scratch copy): 1150 passed. One failure, `test_main_cancelled_wizard_exits_clean`, which patches `cli.wizard.run`, no longer the entry point. Every `test_equivalent.py`, `test_cli.py` and `test_e2e.py` test of the printed command passed unchanged, so direct mode prints what it printed before.
- The ten e2e criteria below, written as scratch tests: all pass on the prototype in `en` and `es`. On `main`, the nine that pin a behaviour change fail, and the `-l` pin passes.
- `mypy --strict` found one narrowing to fix (`outcome.mode` is `PlaylistMode | None` where `replace` wants `PlaylistMode`). `ruff check` passed.

### Tests and the real `~/Videos`

`isolated_env` sets `XDG_STATE_HOME` and `XDG_CONFIG_HOME`, not `HOME`. A scratch run that took the wizard's default folder wrote `Clip.mp4` into the real `~/Videos`, which it created. Both were removed. The `home` fixture in `test_e2e.py` exists for this. Every test here that can reach a folder default must use it, or answer the folder prompt. See notes.

## Scope

### In

- With several URLs and no intent flags, or with `-i`: one walk per URL, then every download (D1).
- A typed answer at the URL prompt with spaces in it gives several URLs (D2).
- A URL the wizard refuses (image, nothing downloadable) is skipped, the others go on, and the run says so at the end (D5).
- The equivalent command, history and exit code follow from per-URL options.
- `AGENTS.md` and `README.md` describe the result.

### Out

- **Direct mode, `-y`, `-p`, `-P`, `--playlist-items`** without `-i`: unchanged. They already handle several URLs.
- **`-l`** keeps today's behaviour: the wizard walks the first URL, which is listed (F8, D7). Row 22 takes `-l` out of the wizard and makes it list every URL.
- **Ctrl-C** still ends in a traceback, whether during a walk (nothing downloaded) or a download (earlier URLs saved and logged, no command). **The exit codes of cancels and wizard refusals** stay as they are. Both are row 23 (`cancel-exits`), which must now cover the multi-URL run too.
- **A clipboard holding several URLs.** `clipboard.py`'s pattern (`^https?://[^\s]+$`) rejects it, so nothing is offered, as today.
- **`b` from one URL's walk back into the previous URL's.** At the first step after the probe, `b` asks the same step again, as it does today.
- **A summary line for direct mode** listing the URLs that failed. Each failure is already printed inline as `Download failed: <url>`.

## Files

| Action | File                            |
| ------ | ------------------------------- |
| modify | `src/download_media/wizard.py`  |
| modify | `src/download_media/cli.py`     |
| modify | `src/download_media/i18n.py`    |
| modify | `tests/test_wizard_flow.py`     |
| modify | `tests/test_cli.py`             |
| modify | `tests/test_e2e.py`             |
| modify | `AGENTS.md`                     |
| modify | `README.md`                     |

No new dependency, flag or default. One new message under D5's recommended option. Collisions: rows 22 and 23 change `cli.main` too, and rows 27 and 31 change other parts of `wizard.py` (the type step and the picker).

## Design

Written for the recommended option of each decision. Each decision says what changes if another option is picked. No new yt-dlp option is involved: each URL goes through the same `probe()` and `_download_one()` it would go through alone.

### `wizard.py`

1. **`_Refused`**, a new internal exception. The two refusals in phase 2.5 (`image_only_skipped` at `:501-502`, `no_supported_items` at `:505-507`) print their warning as today, then raise it instead of returning `None`. The `raise _Cancel` in `step_select` (`:305-306`) stays a cancel.
2. **`_walk_url(opts)`**: today's `run` from phase 1.5 on, for `opts.urls[0]`. It returns the options, or `None` on a cancel, and raises `_Refused`.
3. **`run(opts) -> DownloadOptions | None`** keeps its signature and its single-URL behaviour: the title, `step_url`, then `_walk_url`, with `_Refused` turned into `None`. Only the `-l` path calls it now. `test_wizard_flow.py` stays unchanged, and passing.
4. **`run_all(opts) -> list[tuple[str, DownloadOptions | None]] | None`**: one pair per URL, in order. `None` options means the wizard refused that URL. `None` overall means the user cancelled.
   - The pending list starts from `opts.urls`, or from one empty slot when there's none.
   - Each URL starts from `replace(opts, urls=[url])`: the command line's options, never the previous walk's (D3).
   - A URL from the command line gets the URL prompt with itself as the default, as a single URL does today. Its answer is split on whitespace (D2) and replaces that slot. URLs that came from a split aren't asked again. A clipboard URL accepted at the prompt counts as answered.
   - Before each walk: `wizard_title` as today when there's one URL, `wizard_title (k/n)` when there are several. No new message: the counter is digits.
   - `_Cancel` anywhere, EOF included, returns `None` at once (D4). `_Refused` records the pair with `None` and goes on to the next URL.
5. **`step_url`**: `opts.urls = new_url.split()` where it now sets `[new_url]`. The empty-answer `die` stays. A URL can't hold unencoded whitespace (RFC 3986), and `clipboard.py` already rejects it.

### `cli.py`

`main`, on the wizard path:

- With `-l` (D7): `wizard.run(opts)`, then the `-l` branch, both as today.
- Otherwise `wizard.run_all(opts)`. `None` gives `Cancelled.`, exit 0, as today. Refused URLs are set aside. If no URL is left, `Cancelled.`, exit 0: that's what a refused single URL does today.
- The direct path builds the same list of `(url, opts)` pairs, every URL sharing one `opts`. The clipboard fallback and `url_required` stay on that path only.
- Then `_download_one(url, its_opts)` for each pair, in order. Then one `warn(t("wizard_skipped", url))` per refused URL (D5). Then `_print_equivalent`.
- Exit code: 1 if any URL that reached `_download_one` didn't save, 0 otherwise. Refused URLs don't count (D6).

A walk whose probe failed leaves `playlist_mode` at `auto`, so `_download_one` still asks the single/all question on a playlist-like URL and shows the preview, as it does for one URL today. With several URLs, those prompts come during the downloads, after the last walk. Measured on the prototype with `video private`.

**`_print_equivalent(runs, outcomes)`** takes the `(url, opts)` pairs instead of one `opts`:

1. Keep the pairs whose `outcome.mode` isn't `None`. If none are left, print nothing.
2. The cookie-drop rule runs once for each `cookies_browser` value, over the URLs that used it.
3. The key for each URL is `replace(opts, urls=[], playlist_mode=outcome.mode)`, after the cookie drop. URLs with equal keys share a command, in first-seen order. `DownloadOptions` can't be hashed, so compare with `==`.
4. One `equiv_cmd` header, then `equivalent.build(replace(key, urls=group))` per group.

In direct mode every URL shares one `opts`, so the groups are spec 09's groups by mode and the cookie rule covers every attempted URL. Output is the same, which the prototype's unchanged suite confirmed. This keeps spec 09's contract: one command per playlist mode, and one per set of wizard choices once the walks differ.

### `i18n.py`

| Key              | en                                | es                                       |
| ---------------- | --------------------------------- | ---------------------------------------- |
| `wizard_skipped` | Skipped, nothing to download: {}  | Omitida, no hay nada que descargar: {}   |

The refusal warnings themselves keep their text, "Cancelling." included. In a multi-URL direct run, `no_supported_items` already ends one URL's attempt with "Cancelling." and goes on to the next, so the word already means "this URL" there.

### Docs

- `AGENTS.md`, "Execution modes", after the wizard's three steps: "With several URLs, the wizard walks each one in turn, each starting from the command-line flags, then downloads them all. `c` cancels the whole run before anything downloads. A URL the wizard refuses is skipped, and named again at the end." In the equivalent-command paragraph: "URLs that ended in different playlist modes, or with different wizard choices, get one command each."
- `README.md:79` lists `download-media url1 url2 url3` under "Direct mode (any intent flag skips the wizard)", but it has no intent flag, so it runs the wizard. Move it to "Wizard mode" with the comment `# one walk per URL, then all the downloads`. Give direct mode `download-media -a url1 url2 url3   # several URLs, same options`. `docs/index.html:429` ("Multiple URLs in one go") becomes true as it stands.

## Cases

`fake_site` URLs are written by page name.

| Case                                                      | Expected                                                                                                                                 |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| wizard, one URL                                           | Unchanged: same prompts, title without a counter, same output and exit code                                                              |
| wizard, `video carousel`                                  | 6 prompts, then 7 with the picker. `Clip.mp4` plus both carousel items. Two history rows. Two commands, one header: `-y -o <dir> video` and `-y -o <dir> --playlist-items 2,4 carousel` |
| wizard, audio page and video (generic, loopback)          | No type step for the audio. `Music/audio (1).mp3` and `Videos/tiny.mp4`. History `audio`, then `video`. Commands `-y -a <audio>` and `-y <video>` |
| wizard, second URL behind a login (`needs-cookies`)       | That walk's cookie prompt (spec 18) re-probes with the browser. Both saved. Commands `-y -o <dir> video` and `-y -o <dir> --cookies brave needs-cookies` |
| wizard, second URL refused (`ig-image`)                   | `image_only_skipped` during its walk. Video saved. `Skipped, nothing to download: …/ig-image` after the downloads. Exit 0. Command lists the video only |
| wizard, every URL refused (`ig-image only-images`)        | Both refusal warnings, `Cancelled.`, exit 0, no command, nothing on disk                                                                 |
| wizard, `c` or EOF in the second walk                     | `Cancelled.`, exit 0, nothing downloaded or logged, no command                                                                           |
| wizard, Ctrl-C in any walk                                | Traceback, nothing downloaded (row 23)                                                                                                   |
| wizard, no URL, `video carousel` typed                    | One prompt for both. The carousel's walk starts at its picker. All three files saved                                                     |
| wizard, no URL, clipboard accepted                        | One URL, unchanged                                                                                                                       |
| wizard, partial failure (`video private`)                 | `private` probes as a login wall: cookies declined, manual steps, then its preview during the downloads. `Download failed: …/private`. Exit 1. One history row. One command with both URLs, since their options match |
| wizard, total failure                                     | Exit 1, every attempted URL in the command, as spec 09                                                                                   |
| two walks with the same choices                           | One command holding both URLs                                                                                                            |
| `-i` with flags, several URLs                             | Each walk starts from the flags (`-i -o <dir> video carousel`, all Enter, saves all three into `<dir>`)                                  |
| `-i -P` or `-i -p`, carousel among several                | Per URL, as spec 17: the wizard's own decision overrides `-P` on a multi-entry URL                                                       |
| direct, `-y`, `-p`, `-P`, `--playlist-items`              | Unchanged: the existing suite passes untouched                                                                                           |
| `-i -y`                                                   | `-y`: direct, unchanged                                                                                                                  |
| `-l`, several URLs                                        | Unchanged: one walk, lists the first (row 22)                                                                                            |
| with cookies, cookies dropped by the retry on one walk    | That URL's command loses `--cookies` only if every URL with that browser needed the drop                                                 |
| rerun                                                     | Each printed command replays its URLs with no prompt, as spec 09                                                                         |

## Acceptance criteria

Every behaviour-change criterion fails on `main` (the scratch tests did, measured), and the developer proves it by reverting the fix. Tests that can reach a default folder use the `home` fixture or answer the folder prompt.

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] e2e: `cli.main([video, carousel])` with 6 and then 7 replies, the folder answered: exactly 13 prompts, `Clip.mp4` and both carousel items on disk, history rows `video` then `carousel`, exit 0, `commands(output)` equals the two commands in Cases, `equiv_cmd` printed once, `wizard_title (2/2)` in the output
- [ ] e2e, generic loopback pages: an audio page and `tiny.mp4` under `home`: 10 prompts, `Music/audio (1).mp3`, `Videos/tiny.mp4`, history kinds `audio` then `video`, commands `-y -a <audio>` and `-y <video>`
- [ ] e2e: `video needs-cookies`, picking brave at the second walk's cookie prompt: 12 prompts, two files, the second command carries `--cookies brave` and the first doesn't
- [ ] e2e: `video ig-image`: 7 prompts, `Clip.mp4` only, `image_only_skipped` and `wizard_skipped` for the image in the output, exit 0, one command listing the video only
- [ ] e2e: `ig-image only-images`: 2 prompts, both refusal warnings, `Cancelled.`, exit 0, no command
- [ ] e2e: `c` at the second walk's picker: 8 prompts, nothing on disk, no history row, `Cancelled.`, exit 0, no command
- [ ] e2e: no URL, `"<video> <carousel>"` typed: 12 prompts in all, all three files saved
- [ ] e2e: `video private` with the preview accepted: 13 prompts, exit 1, `Clip.mp4` only, one history row, `Download failed` for `private`, one command with both URLs
- [ ] e2e: `-i -o <dir> video carousel`, all Enter: all three files in `<dir>`
- [ ] **[pin]** e2e: `-l video video-b`: 6 prompts, exit 0, `video-b` never extracted. Passes on `main` too: row 22 changes it
- [ ] unit (`test_wizard_flow.py`, scripted probes): `run_all` with two URLs returns two pairs; a refusal gives `None` options and the walk goes on; a cancel returns `None`; a typed `"a  b"` gives two URLs, and the second isn't prompted
- [ ] unit (`test_cli.py`): `_print_equivalent` gives one command for two URLs with equal options and mode, and two for different options; the cookie-drop rule is applied per browser
- [ ] `test_main_cancelled_wizard_exits_clean` patches the new entry point, with its assertions unchanged. No other existing test changes
- [ ] `wizard_skipped` exists in both the `es` and `en` blocks
- [ ] `AGENTS.md` and `README.md` say what Docs says

## Live check

Not needed. No extractor, format or network behaviour changes: each URL goes through the same probe and download as when it's alone, and the change is the loop around them. Every criterion runs the real yt-dlp and ffmpeg, on `fake_site` or on the loopback server through the real generic extractor.

## SemVer

`fix(wizard)`. Nothing is breaking: no flag, default or on-disk path changes, and a single URL behaves as before. Under D5's recommended option it adds one message, which `AGENTS.md` counts as a minor. 0.3.0 is already a minor (`PLAN.md`, "Next release"), so the next version doesn't change. Without the message it's a patch.

## Open decisions

The human delegated the night's decisions to the roles. Each one has a recommendation, chosen to be easy to undo and to change as little as possible of what already works.

**D1. What several URLs with no intent flag do.**

- **A. Skip the wizard: several URLs mean direct mode.** One condition in `should_run_wizard`. `README.md:79` already lists `url1 url2 url3` under direct mode. But every URL is a video at 1080p: no audio detection, no picker, no cookie prompt (a gated URL fails with a hint), no image refusal. The documented rule "no intent flag means the wizard" gains an exception based on how many URLs were passed. And `-i a b` still needs C's machinery, or it keeps the bug.
- **B. One walk, its answers applied to every URL.** Wrong as soon as kinds differ: a carousel's picker selection, `playlist_mode` and cookies would carry into a video, and only the first URL would be probed. Rejected.
- **C. One walk per URL, every walk first, then the downloads** (recommended). Each URL gets what it would get alone, spec 18's cookie prompt included. The documented rule holds, and a single URL is unchanged. Downloads run unattended once the questions end, as in direct mode. Costs about 120 changed lines in the prototype, and N walks of questions.
- **C′. One walk per URL, each download right after its walk.** Simpler loop, but the user waits through each download before the next question, and a cancel mid-run leaves a half-done run with no clear exit code.
- **D. Ask first** ("2 URLs: walk each, or download all with defaults?"). One more prompt and two new messages, and it ends in A or C anyway.

**D2. Several URLs typed at the URL prompt.**

- **Split on whitespace** (recommended). Today's result is F6 or F7: a 404 or a silent drop, never a success. A URL can't hold unencoded whitespace, and the clipboard already rejects it.
- **Keep one URL per prompt,** and leave F6 and F7 for a new row.

**D3. Where a later walk's defaults come from.**

- **The command line, for every URL** (recommended). Each walk equals `download-media <flags> <url>` alone, which is easy to predict and to test. `-i` with flags presets every walk.
- **The previous walk's answers.** Saves retyping a folder, but leaks what wasn't an answer: a page detected as audio sets `audio_only`, so the next video's type step would default to audio. `playlist_items` would need resetting too.

**D4. `c` (or EOF) in walk k.**

- **Cancels the whole run** (recommended). It's what `c` says, it matches one URL, and nothing has downloaded yet.
- **Skips URL k** and goes on. Then nothing but Ctrl-C (a traceback, row 23) can stop the run.

**D5. How a refused URL is reported.**

- **Its refusal warning during the walk, plus `wizard_skipped` after the downloads** (recommended). The walk's warning scrolls away before the downloads, and project.md asks that a success never hide what was skipped. One new message.
- **Only the refusal warning.** No new message, but at the end only the missing command line shows the URL was skipped.

**D6. A refused URL and the exit code.**

- **Doesn't count** (recommended). A refused single URL exits 0 today, and row 23 is where the exit codes of refusals get settled. It must cover both cases together.
- **Counts as a failure (exit 1),** as a URL that saves nothing does in direct mode. This would decide part of row 23 here.

**D7. `-l` with several URLs.**

- **Unchanged:** one walk, the first URL listed (recommended). Row 22 rewrites this path.
- **Walk every URL, list the first.** More questions for nothing.
