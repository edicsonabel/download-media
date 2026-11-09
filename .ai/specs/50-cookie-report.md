# 50 — Finish the cookie-failure report

Status: done
Item: `fix/cookie-report`

## Problem

When the browser passed to `--cookies` can't be read, spec 26's notice is missing from the direct preview, scrolls away before `Done.`, carries yt-dlp's `ERROR:` label in its reason, and reads ambiguously in Spanish.

Reproduced on `main` at `807a4c8`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script (`.venv/bin/download-media`) ran against a scratch loopback `http.server` on `127.0.0.1` under `env -i`, with `PATH=/usr/bin:/bin`, `HOME` and the four `XDG_*_HOME` in a fresh temp folder per run (so no browser is installed), `LANG` as stated, `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/bus`, and an absolute `-o`. `one.html` holds one `<video>`, `two.html` two. No real cookie database or keyring was read.

**1. The direct preview** (no `-y`, single URL):

```bash
download-media --cookies chrome -o <abs dir> http://127.0.0.1:8765/one.html     # Enter at the prompt
# ERROR: could not find chrome cookies database in "<home>/config/google-chrome"
# Fetching info...
#
# Preview
#
# Proceed with download? [Y/n]: Extracting cookies from chrome
# Downloading...
# ERROR: could not find chrome cookies database in "<home>/config/google-chrome"
# No chrome cookies found: is chrome installed, and has it been opened at least once? Going on without cookies.
# ...
# Done.
# exit 0, one (1).mp4
```

- **What happened:** an empty preview (the same URL without `--cookies` shows `Title: one (1)`), yt-dlp's error line twice, and the tool's own notice only after the user confirmed.
- **What should happen:** the notice before the preview, a preview with the title, yt-dlp's line once, and no second attempt to read the browser.

**2. The end of a long run:**

```bash
download-media -y -p --cookies chrome -o <abs dir> http://127.0.0.1:8765/two.html
# ...
# No chrome cookies found: ... Going on without cookies.
# [generic] two: Downloading webpage
# ... (every item's progress) ...
# Done.
# exit 0, 2 files
```

- **What happened:** the only notice sits above every item's output; `Done.` and exit 0 say nothing about cookies. On a login-gated page with a public version, the user gets the public version and nothing at the end tells them.
- **What should happen:** a line right after `Done.` saying the files were saved without the browser's cookies.

**3. The reason** (a broken install with no `secretstorage`, simulated by blocking the import in the tool's own process; a fake Chrome profile with one `v11` cookie; `-y --cookies chrome+gnomekeyring`, single):

```
Could not read the chrome cookies (ERROR: secretstorage not available as the `secretstorage` module is not installed. Please install by running `python3 -m pip install secretstorage`). Going on without cookies.
```

On a pseudo-TTY with `TERM=xterm-256color` (`script -q -c ...`), the reason carries the colour codes too: `(^[[0;31mERROR:^[[0m secretstorage not available ...`.

- **Why:** in single mode, yt-dlp's `YDLLogger.error` goes through `report_error`, which labels the message (`YoutubeDL.py:1162`, coloured when stderr allows it) and raises `DownloadError` with that text (`:1100`). `load_cookies` turns it into `CookieLoadError`, keeping the `DownloadError` as `__context__` (`cookies.py:93-113`), and `cookies.cookie_failure` takes `str(__context__)` as the reason. Reachable causes: `secretstorage not available` (`cookies.py:960`), `failed to read from keyring` (`:971`), `could not find local state file`, `no encrypted key in Local State`, `invalid key` (`:1020-1035`). The `kwallet-query` errors (`:919`, `:935`) are caught by yt-dlp itself and never reach us (measured: `chrome+kwallet6` with the dead bus goes on with cookies it can't decrypt, which is row 45's miscount).
- **What should happen:** `Could not read the chrome cookies (secretstorage not available ...)`, no label, no escape codes.

**4. Spanish:** `LANG=es_ES.UTF-8 download-media -y --cookies chrome ...` prints `No se encontraron cookies de chrome: ¿está chrome instalado y se ha abierto al menos una vez? Se sigue sin cookies.` QA (spec 26) found `Se sigue sin cookies` ambiguous: it also reads "still no cookies".

## Goal

Whenever a run goes on without the cookies the user asked for, they're told before they confirm, and again after `Done.`, in plain words in both languages.

## Scope

### In

- The direct preview reads the browser's cookies first. On a failure it prints spec 26's notice, previews the page without cookies, and the download goes on without cookies without reading the browser again (D1).
- A new line right after `Done.` when the saved result came from a run without the requested cookies (D2).
- `cookie_failure`'s reason without yt-dlp's `ERROR:` label or terminal escape codes (D6).
- The Spanish ending of `cookies_no_browser` and `cookies_unreadable`: `Se continúa sin cookies.` (D3).

### Out

- **Row 63 (`preview-carousel`)**: the preview stays empty for an Instagram carousel because yt-dlp raises on its first image. A different cause and fix, needing its own reproduction (D5). This item keeps the `except Exception: data = None` branch for every non-cookie error, so row 63 starts from the same code.
- **Row 02's hardcoded hints**: `→ try again with --cookies <browser> ...` (`cli.py:423`) and `cookies.secretstorage_hint()` stay in English and in row 02 (D4).
- **Exit codes**: unchanged. A run that saved without cookies still exits 0 (D7). Spec 26's D1 stands.
- **yt-dlp's own lines** (`ERROR: could not find chrome cookies database ...`, and its doubled `ERROR: ERROR: secretstorage ...`): they stay, as for every yt-dlp error. Only the tool's reason is cleaned.
- **yt-dlp's `pip install` advice inside the `secretstorage` reason**, next to the tool's own `pacman` hint: only on a broken install (`secretstorage` is a required dependency). For the planner.
- **The wizard's probe**: when it drops an unreadable browser (spec 26 D5), the notice prints at the probe and the late cookies step is still offered, with `No cookies` as its default. The summary lists no cookies line in this walk (corrected at validation, see Validation). The download has no browser left to drop, so there's no closing line (D2).
- **`-l`**: no `Done.`; its notice already prints just above the format list.
- **A failed run after going on without cookies**: it ends in `Download failed` and the hint for a cookieless run (spec 26), which already speaks of cookies where they matter.
- **Row 45's miscount** under a keyring that decrypts nothing (measured here in single mode too: `1 of 2 items downloaded (0 without downloadable media, 1 failed)` for one video): row 45.

## Files

| Action | File                               |
| ------ | ---------------------------------- |
| modify | `src/download_media/cli.py`        |
| modify | `src/download_media/downloader.py` |
| modify | `src/download_media/cookies.py`    |
| modify | `src/download_media/i18n.py`       |
| modify | `tests/test_e2e.py`                |
| modify | `tests/test_cookies.py`            |

`README.md` line 15 stays true as written. No change to `wizard.py`, `probe.py`, `equivalent.py`, `config.py`.

## Design

### yt-dlp behaviour relied on

All from spec 26, re-read in the 2026.10.26 source:

1. `YoutubeDL.cookiejar` (`YoutubeDL.py:4218-4226`) loads browser cookies on first read and isn't cached on failure. On failure it calls `report_error(str(cause))`, which prints yt-dlp's line even with `quiet=True` (`trouble` → `to_stderr`, `:1082`, `:1013-1019`), then raises.
2. The preview's `YoutubeDL` has no `ignoreerrors`, so a load failure arrives as `DownloadError` whose `exc_info[1]` is the `CookieLoadError`. `downloader._load_browser_cookies` already turns both shapes into `CookiesUnavailable`.
3. `report_error` prefixes `ERROR:` through `_format_err` (`:1132`, `:1157-1162`), coloured only when `_allow_colors.error` is true. `yt_dlp.utils.remove_terminal_sequences` strips the colour (`probe.py` already uses it).

### `downloader.py`

Rename `_load_browser_cookies` to `load_browser_cookies` (public: `cli._show_preview` calls it). Same body.

### `cli.py`

- **`_show_preview`** reports whether the browser's cookies failed there, besides the confirmation (shape is the developer's choice: a tuple, a small result type). Inside its `with YoutubeDL(ydl_opts) as ydl:`, call `load_browser_cookies(ydl, opts.cookies_browser)` before `extract_info`. On `CookiesUnavailable`: `report_failure(exc.spec, exc.failure)`, then extract again with a `YoutubeDL` built from the same options minus `cookiesfrombrowser`. Every other exception keeps today's `data = None`.
- **`_download_one`**: when the preview said the cookies failed, the download runs once with `cookies_browser=None`, with no second load and no second notice, exactly like `_download_with_cookie_fallback`'s `CookiesUnavailable` branch: on success `cookies_dropped=True` (so spec 26 D6 drops `--cookies` from the printed command, as today); on `DownloadError`, the hint is for a cookieless run (no keyring hint), and the command keeps `--cookies` (as today). For example, a keyword on `_download_with_cookie_fallback` that skips the first attempt.
- **After `ok(t("done"))`**: if `cookies_dropped` and `opts.cookies_browser`, `warn(t("done_without_cookies", browser_of(opts.cookies_browser)))`. `cookies_dropped` is already true on all three paths: the load failure, the preview's failure (above), and the site-refused retry (`cookies_retry`). One line per URL, after that URL's `Done.`, like spec 26 D8.

### `cookies.py`

In `cookie_failure`, clean the cause's text before anything reads it: `remove_terminal_sequences`, then drop leading `ERROR:` labels and the spaces after them (`re.sub(r"^(?:ERROR:\s*)+", "", text)`), then today's first line and `textwrap.shorten`. `missing` reads the same cleaned text; a `FileNotFoundError` never carries a label, so it's unchanged. The `secretstorage` hint check reads the cleaned reason, also unchanged.

### `i18n.py`

| Key                    | Block | Text                                                                                                              |
| ---------------------- | ----- | ----------------------------------------------------------------------------------------------------------------- |
| `cookies_no_browser`   | es    | No se encontraron cookies de {0}: ¿está {0} instalado y se ha abierto al menos una vez? Se continúa sin cookies.     |
| `cookies_unreadable`   | es    | No se pudieron leer las cookies de {0} ({1}). Se continúa sin cookies.                                              |
| `done_without_cookies` | en    | Saved without {0} cookies: this is what the site shows without logging in.                                        |
| `done_without_cookies` | es    | Guardado sin las cookies de {0}: es lo que el sitio muestra sin iniciar sesión.                                    |

The English `cookies_no_browser` and `cookies_unreadable` don't change. `Saved without` fits both drop paths: after `cookies_retry` the cookies *were* sent once, but what was saved came without them.

### What the user sees

```
$ download-media --cookies chrome -o ~/tmp/x 'http://127.0.0.1:8765/one.html'
Fetching info...
ERROR: could not find chrome cookies database in "/home/u/.config/google-chrome"
No chrome cookies found: is chrome installed, and has it been opened at least once? Going on without cookies.

Preview
  Title: one (1)

Proceed with download? [Y/n]:
Downloading...
  ...
Done.
Saved without chrome cookies: this is what the site shows without logging in.

Equivalent command to repeat this download:
  download-media -y -o /home/u/tmp/x http://127.0.0.1:8765/one.html
```

The order of yt-dlp's stderr line against the tool's stdout depends on buffering when redirected (spec 26 Validation); on a terminal it's as above.

## Cases

"Today" is measured on `main` at `807a4c8` unless marked *source*. Browser not installed unless said. Pages are `fake_site`'s or the loopback `media_url` ones (see Acceptance criteria).

| Case                                                                 | Today                                                                                       | After                                                                                                          |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Direct with preview, single, readable page                           | yt-dlp line, empty preview, confirm, yt-dlp line again, notice, `Done.`, exit 0, no `--cookies` | yt-dlp line once, notice, preview with title, confirm, `Done.`, closing line, exit 0, no `--cookies`           |
| Direct with preview, page that 404s                                  | Empty preview, notice after confirming, `Download failed`, no hint, command keeps `--cookies`, exit 1 | Notice before the empty preview, `Download failed`, no keyring hint, no closing line, command keeps `--cookies` |
| Direct with preview, cancelled at the prompt (`n`)                   | Empty preview, `Cancelled.`, exit 1                                                          | Notice, preview with title, `Cancelled.`, no command, exit unchanged (1, row `cancel-exits`'s)                  |
| Direct with preview, two URLs                                        | Per URL as above                                                                            | Per URL: notice in each preview, one closing line after each `Done.` (D8 of spec 26)                            |
| Direct, playlist URL, "all" at the mode prompt                       | No preview; notice at the download                                                           | Unchanged, plus the closing line                                                                                |
| `-y`, single                                                         | Notice, `Done.`, exit 0                                                                       | Same, plus the closing line                                                                                     |
| `-y -p`, `-y --playlist-items 1,2`                                   | Notice above every item, `Done.`, exit 0                                                      | Same, plus the closing line after `Done.`                                                                       |
| `-y -P`                                                              | As `-y` single                                                                               | As `-y` single                                                                                                  |
| Partial result after going on without cookies                        | `partial_result`, `Done.`                                                                     | Same, plus the closing line                                                                                     |
| Nothing saved after going on without cookies                         | `Download failed` / `no_supported_items`, exit 1                                              | Unchanged, no closing line                                                                                      |
| Cookies loaded, refused by the site (`rejects-cookies`), saved       | `cookies_retry`, `Done.`, exit 0                                                              | Same, plus the closing line with that browser                                                                   |
| Cookies loaded and used, saved (`needs-cookies`)                     | `Done.`                                                                                       | Unchanged: no closing line                                                                                      |
| No `--cookies`                                                       | `Done.`                                                                                       | Unchanged: no closing line                                                                                      |
| Single mode, cookies unreadable with a labelled cause (`secretstorage` missing) | Reason starts with `ERROR: `; on a terminal with colour codes                        | Reason without the label or codes; `secretstorage` hint still printed                                           |
| `-p`, the same cause *(source)*                                      | yt-dlp logs it and goes on, nothing raised (spec 26 Out)                                     | Unchanged                                                                                                       |
| Browser not installed, reason                                        | `could not find chrome cookies database in "..."` inside `cookies_no_browser`'s check         | Unchanged (`missing` still true)                                                                                |
| `LANG=es*`, either notice                                            | `... Se sigue sin cookies.`                                                                   | `... Se continúa sin cookies.`                                                                                  |
| `LANG=es*`, closing line                                             | —                                                                                            | `Guardado sin las cookies de chrome: es lo que el sitio muestra sin iniciar sesión.`                           |
| `-l --cookies chrome`                                                | Notice, format list, exit 0                                                                   | Unchanged (no `Done.`)                                                                                          |
| Wizard, probe drops the browser (`-i --cookies chrome`)              | Notice at the probe, late cookies step offered, `Done.`                                       | Unchanged: the download had no browser to drop, so no closing line (D2)                                         |
| Wizard, late cookies step picks a missing browser                    | Notice at the download, `Done.`                                                               | Same, plus the closing line                                                                                     |
| Wizard, manual flow (probe failed), late step picks a missing browser, single | Empty preview, notice after confirming *(source)*                                    | As "direct with preview": notice in the preview, one notice in all                                              |
| `-a`                                                                 | As video                                                                                      | As video                                                                                                        |

Rerun doesn't apply: nothing on disk depends on the cookies.

## Acceptance criteria

Every `[fix]` fails on `main`, shown by reverting the fix. A missing browser is the empty `HOME` and `XDG_CONFIG_HOME` that `isolated_env` and `home` give. Cookie-*load* failures can't be shown on `fake_site`: its `FakeYdl` pops `cookiesfrombrowser` (`tests/conftest.py:1062`), also for the preview, which imports `yt_dlp.YoutubeDL`. So those tests run the real yt-dlp and generic extractor on the loopback `media_url` pages, as spec 26's do (`direct` fixture, extended to script the preview's answer; `poster.html` one video, `multi.html` four, `status/404`). The site-refused retry uses `fake_site`'s `rejects-cookies`. No test reads a real cookie database or keyring.

- [x] `scripts/pre-release.sh --no-live` passes
- [x] **[fix]** `test_the_preview_names_a_missing_browser_and_still_shows_the_page`. Direct, no `-y`, `--cookies chrome` on `poster.html`, Enter at the prompt: one prompt (`t("ask_confirm")`), `t("cookies_no_browser", "chrome")` exactly once and before `t("preview")` in the output, the preview's `t("p_title")` line present, `could not find chrome cookies database` exactly once, exit 0, 1 file, command without `--cookies`
- [x] **[fix]** `test_a_preview_cancelled_after_a_missing_browser_still_said_it`. Same, `n` at the prompt: the notice once, before `t("preview")`, `t("cancelled")`, no files, no command. The exit code isn't asserted: a cancelled preview exits 1 today, which is row `cancel-exits`'s
- [x] **[fix]** `test_a_dead_page_after_the_preview_with_a_missing_browser`. Same on `status/404`, Enter: exit 1, the notice once, `t("failed", url)`, `t("cookies_keyring_hint", "chrome")` and `t("done_without_cookies", "chrome")` not in the output, command keeps `--cookies chrome`
- [x] **[fix]** `test_a_run_without_the_asked_cookies_says_so_after_done`. Parametrized `-y` on `poster.html`, `-y -p` and `-y --playlist-items 1,2` on `multi.html`, all `--cookies chrome`: exit 0, `t("done_without_cookies", "chrome")` once, right after `t("done")` (the next line)
- [x] **[fix]** `test_cookies_refused_by_the_site_say_so_after_done`. `fake_site` `rejects-cookies`, `-y --cookies brave`: exit 0, `t("cookies_retry", "brave")`, then `t("done_without_cookies", "brave")` after `t("done")`
- [x] **[fix]** `test_a_missing_browser_at_the_last_wizard_step_says_so_after_done`. Wizard on `multi.html`, chrome at the late step (spec 26's `PICKER_WALK`): `t("done_without_cookies", "chrome")` after `t("done")`
- [x] **[pin]** `test_no_closing_line_without_a_drop`. `fake_site`: `-y --cookies firefox` on `needs-cookies` (cookies used) and `-y` without `--cookies` on a plain video: `t("done_without_cookies", ...)` not in either output. Wizard `-i --cookies chrome` on `poster.html` (probe drops the browser): no closing line
- [x] **[fix]** `test_a_labelled_cause_loses_its_label` (`tests/test_cookies.py`). `cookie_failure` on a `CookieLoadError` whose `__context__` is `DownloadError("ERROR: secretstorage not available ...")` → reason starts with `secretstorage not available`; on `DownloadError("\x1b[0;31mERROR:\x1b[0m failed to read from keyring")` → `failed to read from keyring`; `missing` false for both. Update `SECRETSTORAGE_MISSING`'s users so `report_failure` still prints the `pacman` hint
- [x] **[pin]** spec 26's `cookie_failure` unit tests unchanged: `could not find chrome cookies database in "x"` still `missing`, `unsupported platform: linux` still the reason
- [x] **[fix]** `test_unreadable_cookies_in_both_languages` gains the closing line in both languages; plus a check that both `es` notices end in `Se continúa sin cookies.` and no `es` message contains `Se sigue sin cookies`
- [x] `test_es_and_en_have_same_keys` passes with `done_without_cookies` in both blocks
- [x] **[pin]** spec 26's tests pass unchanged apart from the closing line, which none of them forbids today (`grep -n done_without_cookies` before the change is empty)

## Live check

Not needed. Every path is the real yt-dlp on loopback: a missing browser is an empty `HOME`, and the labelled reason is a unit test on yt-dlp's own exception shapes, measured above with the real console script. A real site only changes what the cookieless run returns, which this item doesn't read. A real keyring would mean touching the human's keyring for no extra information.

## SemVer

`fix`, nothing breaking: no flag, default, path or exit code changes. One new message (`done_without_cookies`) and two reworded Spanish ones; `AGENTS.md` counts new messages as minor, and 0.4.0 is already a minor.

**For the "Next release" review list:** published behaviour changes in four visible ways. The direct preview now prints the cookie notice before asking and shows the page's metadata, and yt-dlp's cookie error appears once instead of twice. A new line follows `Done.` when the saved files came without the requested cookies, including after `Failed with <browser> cookies. Retrying without cookies...`. The reason no longer starts with `ERROR:`. The Spanish notices end in `Se continúa sin cookies.`

**Release note (for the planner):** "When the browser's cookies can't be used, the direct-mode preview now says so before asking, and still shows the title. A line after `Done.` repeats that the files were saved without the browser's cookies, so a long playlist run doesn't hide it."

## Validation

On `main` at `3a88477` (merge `8b405d9` plus one `state.json` commit), yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14.9. GitHub CI run 42620145342 on `3a88477`: success.

- **Gate:** `scripts/pre-release.sh --no-live` re-run on `3a88477`: exit 0, ruff and mypy clean, 37 files formatted, 1975 passed, 9 deselected, 2 xfailed in each locale, coverage 98.80%. The tech-lead's run on `8b405d9` gave the same numbers.
- **Criteria:** each named test also ran on its own, in `es` and in `en`: 21 passed each (the named tests with their parameters, plus `test_a_labelled_secretstorage_cause_still_gets_its_hint` and `test_yt_dlps_label_stays_out_of_the_reason`). The removed test lines since the merge base are the `direct` fixture's signature and docstring and the `ERROR: ` in `SECRETSTORAGE_MISSING`, which the criterion asks for, so spec 26's tests stand. `git grep done_without_cookies b15b74d -- src tests` is empty.
- **Reverting the fix:** on scratch copies of the merge's tests, not on `main`. With `src/` at the merge base `b15b74d` and only the merged `i18n.py` kept (so a failure is about behaviour, not a missing key): 16 of the 21 fail, which is every `[fix]` test above except the Spanish check. The 5 that pass are the 3 no-closing-line pins, `test_es_and_en_have_same_keys` and that Spanish check. With only the Spanish wording reverted: `test_the_spanish_notices_say_the_run_goes_on` fails, 20 pass.
- **Reproduction:** the Problem's steps, with the real console script against a scratch `http.server` on `127.0.0.1`, under the Problem's environment (`env`-clean, fresh `HOME` and `XDG_*_HOME` per run, dead D-Bus address). Downloads went to `tmp/`. No real cookie database or keyring was read: the only Chrome profile was a fake one in the throwaway `HOME`.

| Step | Run | Result |
| ---- | --- | ------ |
| 1 | Direct, `--cookies chrome`, Enter, piped and on a pseudo-TTY | yt-dlp's line once, notice once and before `Preview`, `Title: one (1)`, no `Extracting cookies from chrome`, `Done.`, closing line on the next line, command without `--cookies`, exit 0, 1 file, 1 history row. The pseudo-TTY's order is "What the user sees" |
| 1 | Same, `n` | Notice, title, `Cancelled.`, no command, exit 1, no files, no history row |
| 1 | Same, a page that 404s | Notice before an empty preview, `Download failed`, no keyring hint, no closing line, command keeps `--cookies chrome`, exit 1, no files |
| 1 | Same, two URLs | One notice, one yt-dlp line and one closing line per URL, 3 files, 2 history rows |
| 2 | `-y -p`, `-y --playlist-items 1,2` | Closing line right after `Done.`, exit 0, 2 files each |
| 3 | `-y --cookies chrome+gnomekeyring`, fake profile, `secretstorage` import blocked, piped and on a pseudo-TTY with `TERM=xterm-256color`; preview on the pseudo-TTY | `Could not read the chrome cookies (secretstorage not available ...)`: no label, no escape codes inside the reason, `pacman` hint printed. yt-dlp's own `ERROR: ERROR:` line stays (Out). 1 file |
| 4 | `LANG=es_ES.UTF-8`: `-y`, preview, the labelled cause | Both notices end in `Se continúa sin cookies.`, `Guardado sin las cookies de chrome: ...` after `Listo.`, no `Se sigue` |

- **Cases, run the same way:** no `--cookies`, `-y -P`, `-y -a`, `-l`, `-y` on a 404, the wizard whose probe drops the browser, the wizard's late step picking chrome. All as the table says.
- **Cases, tests only:** `rejects-cookies` and `needs-cookies` (they need `fake_site`), a partial result, a `-p` run that saves nothing, a playlist URL at the mode prompt, and the manual flow.
- **Not run:** live tests. The spec needs no live check, so nothing touched Instagram, a real browser or the real keyring.
- **Scope:** outside `.ai/`, `b15b74d..8b405d9` touches the six files in the Files table. No change to `wizard.py`, `probe.py`, `equivalent.py`, `config.py`, `pyproject.toml`, `PKGBUILD`, `README.md`, `AGENTS.md` or the version.

### What changed after approval

- **The wizard's `Cookies: No cookies` line doesn't exist.** Out, the Cases table and D2's option C said the walk shows it after the probe drops the browser. Measured with `-i --cookies chrome` on `807a4c8` and on `main`, the same output: the summary lists type, quality, container and folder, and nothing about cookies. The cookies step is the walk's last, and only cookies asked before the walk are listed (`wizard.py`, `asked`). No criterion or test reads that line, and the code does what it did. Out and the Cases row are corrected above; D2's table keeps the text that was approved, with a note below it. D2 stands on its other grounds: option C needs a new `DownloadOptions` field, and the walk offers the step again, so the user answers it. What the user is told in that walk is less than D2 assumed: the notice once, at the probe, and the menu's default. For the human to review.
- **`-p` with the labelled cause, marked *source* in the table, is now measured:** `-y -p --cookies chrome+gnomekeyring`, fake profile, `secretstorage` blocked: yt-dlp's line, `Extracted 1 cookies from chrome`, no notice, `2 of 3 items downloaded (0 without downloadable media, 1 failed).`, `Done.`, no closing line, exit 0, 2 files, command keeps `--cookies`. As the row says: unchanged. So the Goal's "whenever" has two exceptions the spec already names: this one (row 45's) and the wizard above.
- **Tests:** the wizard half of `test_no_closing_line_without_a_drop` is its own test, `test_no_closing_line_after_the_wizard_probe_drops_the_browser`. The test-engineer added cases beyond the criteria, including `test_a_preview_with_cookies_that_load_keeps_them`, `test_every_preview_names_its_missing_browser` and `test_yt_dlps_label_stays_out_of_the_reason`.
- **Within what Design left to the developer:** `_show_preview` returns a `_Preview` named tuple. The fallback takes a `cookies_failed` keyword, and a new `_download_without_cookies` holds the run both load-failure paths share.

### Remaining notes (for the planner)

1. `PLAN.md`'s "Next" paragraph still lists `cookie-report`, and the 0.4.0 review list needs the four behaviour changes under SemVer.
2. Line references moved: row 02's `cli.py:384` hint is now `cli.py:459`, and row 63's `cli.py:202` is now `cli.py:263`. Row 63 can start: this item kept that branch.
3. With `-p` and a keyring cause, a run can use cookies it couldn't decrypt and end in `Done.` with no notice at all (measured above). Row 45 only names the miscount.

## Open decisions

The human delegated these to the roles. Each has a recommendation, chosen to lose nothing and to be easy to undo. None is breaking.

### D1: the preview with an unreadable browser

| Option                                                                                              | Effect                                                                                                                   |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **A. Report in the preview, preview without cookies, download without reading the browser again (recommended)** | The user knows before confirming; the title shows; one notice and one yt-dlp line. `_download_one` carries one more flag |
| B. Report in the preview; the download reads the browser again                                      | Smaller, but the notice and yt-dlp's line print twice per URL                                                            |
| C. Preview without cookies silently; the download reports                                           | Title shows, but the notice still comes after the confirmation, which is what the row fixes                             |

### D2: when the closing line prints

| Option                                                                                                       | Effect                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| **A. After `Done.` whenever the download dropped the requested browser: load failure, preview failure, site-refused retry (recommended)** | One condition, `cookies_dropped`, already computed. A refused-and-retried run is the same "public version" risk          |
| B. Only after a load failure                                                                                 | Leaves `cookies_retry` runs ending silently, the same problem                                                                 |
| C. A, plus wizard walks whose probe dropped the browser                                                      | Needs a new `DownloadOptions` field carried from the walk to the download; the walk already showed `Cookies: No cookies` and offered the step again |

Corrected at validation: the walk never shows `Cookies: No cookies`, so that part of option C's effect is wrong. It does offer the step again. See Validation.

### D3: the Spanish wording

| Option                                | Effect                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------ |
| **A. `Se continúa sin cookies.` (recommended)** | QA's suggestion; fits the download, `-l` and the wizard's probe alike   |
| B. `Se descargará sin cookies.`       | Wrong for `-l` and for the probe, which download nothing                       |
| C. `Continuando sin cookies.`         | Fine, but a gerund sentence reads less naturally as a standalone statement     |

### D4: the English hints row 02 names

| Option                                              | Effect                                                                                                                    |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **A. Stay in row 02 (recommended)**                 | `→ try again with --cookies ...` prints for any login failure without cookies, not just this report; row 02 moves it with the `secretstorage` hint and the rest of the hardcoded text |
| B. Move both cookie hints here                      | Two more messages in this item, and row 02 splits its own subject                                                        |

### D5: row 63 (`preview-carousel`)

| Option                                       | Effect                                                                                                                             |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **A. Stays separate, after this item (recommended)** | Different cause (yt-dlp raising on an image during extraction) and fix; both touch `_show_preview`, so the orchestrator runs them one after the other, not in parallel |
| B. Fold it in                                | Mixes a cookie report with an extraction change, and needs its own `fake_site` reproduction                                        |

### D6: how far to clean the reason

| Option                                                         | Effect                                                                         |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **A. Strip terminal sequences, then leading `ERROR:` labels (recommended)** | Covers the plain and the coloured label (both measured)          |
| B. Strip only a literal `ERROR: `                               | Misses the terminal case, where the label is wrapped in colour codes           |

### D7: exit code after a run without the requested cookies

| Option                         | Effect                                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **A. Unchanged, 0 (recommended)** | Spec 26 D1 stands; the closing line is the flag the row asks for                                         |
| B. Non-zero when it saved      | Breaks scripts that save public content with `--cookies` set by habit; a changed outcome, so breaking      |
