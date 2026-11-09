# 45 — Count failures per item, and name the keyring error behind them

Status: draft
Item: `fix/p-double-count`

> **For the 0.4.0 review list:** this item changes published behaviour in three ways. See SemVer. D1 is the one that needs the human's eye: a page that only opened thanks to the cookies yt-dlp could still decrypt goes from a save, exit 0, to `Download failed`, exit 1.

## Problem

`failed` is `len(ydl.errors)` (`downloader.py:671`), so a run reports yt-dlp's error lines as failed items, and a keyring error yt-dlp only logs ends in `Done.`, exit 0, a wrong count, and no word about the cookies.

Reproduced on `main` at `728834b`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script (`.venv/bin/download-media`) ran against a scratch `http.server` on `127.0.0.1` that logs each request's `Cookie` header, under `env -i`, with `PATH=/usr/bin:/bin`, `HOME` and the four `XDG_*_HOME` in a fresh temp folder per run, `LANG=en_US.UTF-8`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/bus`, and an absolute `-o` in a temp folder. The browser is a fake Chrome profile in that throwaway config folder: `google-chrome/Default/Cookies` with one `v11` cookie for `127.0.0.1`, encrypted with a made-up key. `one.html` holds one `<video>`, `two.html` two, `junkmix.html` a text file served as `.mp4` and a real one. No real cookie database or keyring was read.

**1. One video:**

```bash
download-media -y --cookies chrome+kwallet6 -o <abs dir> http://127.0.0.1:18945/one.html
# ERROR: kwallet-query failed with return code 1. Please consult the kwallet-query man page for details
# Extracted 1 cookies from chrome
# 1 of 2 items downloaded (0 without downloadable media, 1 failed).
# Done.
#   download-media -y -o <abs dir> --cookies chrome+kwallet6 http://127.0.0.1:18945/one.html
# exit 0. On disk: one (1).mp4. The server saw: Cookie: s=
```

**2. Two videos, a keyring cause with `-p`** (the same result for each of the four causes in Findings):

```bash
download-media -y -p --cookies chrome+kwallet6 -o <abs dir> http://127.0.0.1:18945/two.html
# 2 of 3 items downloaded (0 without downloadable media, 1 failed).
# Done.
# exit 0. On disk: 2 files. The server saw: Cookie: s=
```

**3. No cookies at all, one broken item that fails in two steps:**

```bash
download-media -y -p -a -o <abs dir> http://127.0.0.1:18945/junkmix.html
# ERROR: WARNING: unable to obtain file audio codec with ffprobe
# ERROR: Error opening input files: Invalid data found when processing input
# 1 of 3 items downloaded (0 without downloadable media, 2 failed).
# Done.
# exit 0. On disk: the junk file and one .mp3. The page holds 2 items.
```

- **What happened:** `N of M` counts items nobody asked for: one for a keyring error, one more for each extra failed step. With both on `junkmix.html`, `-y -p -a` says `1 of 4 … 3 failed`. In runs 1 and 2 nothing the tool writes mentions cookies, the request carries a cookie it couldn't decrypt, and the printed command repeats the same thing.
- **What should happen:** runs 1 and 2 say `Could not read the chrome cookies (…). Going on without cookies.`, save without cookies, print no count, and repeat that after `Done.`. Run 3 says `1 of 2 … 1 failed`.

## Findings

### Every keyring cause, by mode (`main`)

| Cause (yt-dlp `cookies.py`)          | `-y`, one video                                                   | `-y -p`, two videos                                     |
| ------------------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------- |
| `kwallet-query` fails (`:935`)       | `1 of 2`, `Done.`, no notice, `Cookie: s=`, command keeps `--cookies` | `2 of 3`, `Done.`, no notice, `Cookie: s=`, command keeps `--cookies` |
| `kwallet-query` missing (`:919`)     | `cookies_unreadable`, no cookies sent, closing line, command drops `--cookies` | Same as the row above: `2 of 3`, no notice              |
| `secretstorage` missing (`:960`)     | `cookies_unreadable` and the `pacman` hint, no cookies sent, closing line, command drops `--cookies` | `2 of 3`, no notice                                     |
| No item in the keyring (`:971`)      | `cookies_unreadable`, no cookies sent, closing line, command drops `--cookies` | `2 of 3`, no notice                                     |

Exit 0 and every file saved in all eight. The last three causes were measured with `PATH` holding only ffmpeg and ffprobe (`:919`), or through a scratch runner that sets `yt_dlp.cookies.secretstorage` to `None` (`:960`) or to a stub whose collection is empty (`:971`). It calls `cli.main` in-process and reaches no real keyring.

**Why the modes differ.** `logger.error` reaches `report_error` (`utils/_utils.py:5708`). Without `ignoreerrors` it raises `DownloadError`. Three of the causes sit outside any `try`, so that exception reaches `load_cookies`, becomes `CookieLoadError`, and spec 26 catches it. `:935` sits inside the `try` at `:926`, whose `except Exception` (`:953`) swallows it and only logs a warning. Under `ignoreerrors` (`-p`, `--playlist-items`, a picker run) `report_error` only prints, and yt-dlp goes on (spec 26, Out). So one failure has two outcomes, depending on the mode.

### The other modes (`main`, `chrome+kwallet6`)

| Run                                                     | Result                                                                                                     |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Direct with preview, Enter                              | yt-dlp's line before the preview and again after it, no notice, `1 of 2`, `Done.`, exit 0                   |
| `-y --playlist-items 1`                                 | `1 of 2`, `Done.`, exit 0, 1 file                                                                          |
| `-l`                                                    | yt-dlp's lines, no notice, the format list, exit 0, `Cookie: s=`                                           |
| `-y --cookies chrome` under `XDG_CURRENT_DESKTOP=KDE`, `KDE_SESSION_VERSION=6` (yt-dlp picks the keyring) | `1 of 2`, `Done.`, no notice, no keyring hint. This is the case a real KDE user reaches without typing a keyring |
| Wizard `-i` on `one.html`                               | Silent at the probe, `1 of 2`, `Done.`, exit 0                                                             |
| Wizard `-i` on `two.html`                               | `Some items could not be inspected: kwallet-query failed …` (`probe_partial`, false: every item was inspected), then `2 of 3`, `Done.` |
| `-y` on a page that answers 401                         | `Failed with chrome+kwallet6 cookies. Retrying without cookies...`, `Download failed`, exit 1              |

### What the jar holds after a keyring error

- yt-dlp asks the keyring only for the first `v11` cookie (`_v11_key` is a `cached_property`, `cookies.py:448-451`). So a keyring error means at least one cookie needs that key. A profile with only `v10` cookies never asks: on the KDE run above with one `v10` cookie, no error, `Done.`, no notice, `Cookie: good=1`.
- After the error the password is `b''`, and yt-dlp decrypts with a key derived from it. `_decrypt_aes_cbc_multi` returns whatever decodes (`:1044-1054`), and `unpad_pkcs7` trims by the wrong key's last byte, so a cookie encrypted with the real key can come back empty rather than failed. Measured: `Extracted 1 cookies from chrome` with no `(could not be decrypted)`, and `Cookie: s=` on the wire.
- So **yt-dlp's `Extracted N cookies (M could not be decrypted)` can't tell the tool what's usable**, and neither can the size of the jar.
- A mixed profile, one `v10` cookie that opens the page and one `v11`: `Cookie: good=1; s=`, the gated page saves, `1 of 2`, exit 0, with `-y` and with `-y -p`. This is what D1's option A gives up.

### Where the count goes wrong

| Source of the extra entry in `ydl.errors`                                                 | Example, `main`                                                         |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| An error logged while loading the cookies, before any item                                | Runs 1 and 2 above                                                      |
| A second failed step of one item: `run_pp` reports and carries on under `ignoreerrors` (`YoutubeDL.py:3811-3813`) | Run 3 above: `ExtractAudio` and the metadata step, `-p -a`, and `-f wav` as well |
| `report_error(..., is_error=False)`, which yt-dlp itself doesn't call an error (`YoutubeDL.py:1841`, an extractor's unscoped `cookies`) | Scratch extractor through `downloader.download`: the file saved, `saved=1, failed=1`, in single mode and under `-p`. `_Ydl.report_error` (`downloader.py:248`) records every call |

What's already right, and stays: one error per entry that fails outside `process_info`, since `__process_iterable_entry` catches it once and moves on, and spec 29's rule that a failed item isn't also saved. In single mode any real error raises (`YoutubeDL.py:1100`, source, not a run), so with the sources above gone `failed` is 0 whenever a result comes back.

### yt-dlp behaviour relied on

Confirmed in the 2026.10.26 source. At the 2024.10.07 floor it was read on GitHub, not run: `_YDLLogger.error` calling `report_error`, `_v11_key` as a `cached_property`, the `try` around `:935`, and `CookieLoadError` are the same.

1. yt-dlp's cookie logger calls `ydl.report_error` on the instance (`_YDLLogger.error`, `utils/_utils.py:5708-5710`), as does `cookiejar` (`YoutubeDL.py:4226`), so an instance attribute sees both.
2. `cookiejar` is a `cached_property`, read once by `load_browser_cookies` before any request (spec 26). Every keyring message therefore lands before the first item.
3. `run_pp`, `process_info` and the post hooks work as spec 29 measured.

## Goal

`N of M` counts items in every mode. A keyring error yt-dlp only logs is treated like one it raises: one notice, the run goes on without cookies, and the closing line follows `Done.`, the same in every mode.

This closes the first of spec 50's two named exceptions to its Goal (`-p` with a keyring cause). The second, the wizard's probe, stays as spec 50 D2 left it.

## Scope

### In

- `load_browser_cookies` raises `CookiesUnavailable` when an error was logged during the load, whichever mode (D1).
- `probe()` goes through `load_browser_cookies`, so the wizard says it once at the probe, as spec 26 D5 does for a missing browser (D2).
- `failed` counts one per item: errors after an item's first don't add to it, and `is_error=False` isn't an error (D3).

### Out

- **Row 66 (`cookie-reason`):** the `pip install` advice and the bare `[Errno 2]` inside the reason. The `-p` paths now print the same reason single mode already does, so row 66 covers them too.
- **Spec 50 D2:** no closing line after the wizard's probe drops the browser. It stays pending the 0.4.0 review, and this item adds keyring errors to the walks it describes.
- **A keyring that answers without an error but decrypts nothing:** `chrome+basictext` on a `v11` cookie gives `Extracted 0 cookies from chrome (1 could not be decrypted)`, `Done.`, no notice (measured); so does a wrong key from a working keyring, per spec 26. yt-dlp logs no error, and its `could not be decrypted` count can't be trusted (Findings). For the planner.
- **The warnings on the same path** (`failed to read NetworkWallet`, `exception running kwallet-query`, `:905`, `:954`), and kwallet's `failed to read` answer (`:941`), which is only a debug line. No error, nothing to catch.
- **yt-dlp's own lines** (`ERROR: kwallet-query failed …`, `Extracted 1 cookies from chrome`): they stay, as in spec 50 Out. `Extracted 1 cookies` right above `Could not read the chrome cookies` reads oddly, but it's yt-dlp's text.
- **Row 08** (single mode stopping at a broken item), **row 31** (`partial_result` under `-a`), **row 46** (the junk file left on disk), **row 52** (line order when redirected), **row 23** (the exit code of a cancel).

## Files

| Action | File                                  |
| ------ | ------------------------------------- |
| modify | `src/download_media/downloader.py`    |
| modify | `src/download_media/cookies.py`       |
| modify | `src/download_media/probe.py`         |
| modify | `tests/conftest.py` (one `fake_site` shape) |
| modify | `tests/test_e2e.py`                   |
| modify | `tests/test_downloader.py`            |
| modify | `tests/test_cookies.py`               |
| modify | `tests/test_probe.py`                 |

No change to `cli.py`, `wizard.py`, `i18n.py`, `equivalent.py`, `README.md` or `AGENTS.md`: every door already handles `CookiesUnavailable`, no message is new, and `README.md` line 15 already says this. Collisions: row 63 (`preview-carousel`) reads the same `_show_preview` but this item doesn't edit it; row 66 edits `cookie_failure`'s neighbourhood; row 46 and row 43 touch `downloader.py`.

## Design

Written for the recommended option of each decision. A prototype of it on a scratch copy of `src/` (42 changed diff lines, no `cli.py`, `wizard.py` or `i18n.py`) gave every "After" in Cases, with ruff and `mypy --strict` clean. `src/` itself wasn't touched. The whole suite against it in `en`, with `TMPDIR` in a long scratch path: 1972 passed, 2 xfailed, 3 failed. One is the flip named under Acceptance criteria. The other two, `test_an_unreachable_*`, fail the same way on `main` under that `TMPDIR` (`AF_UNIX path too long`, from `isolated_env`'s bus socket) and pass on both with pytest's default temp folder. A keyring and count selection in `es`: 75 passed, and the same flip.

### `downloader.py`

**1. One door for cookie failures.** In `load_browser_cookies`, note what's logged while reading the jar:

```python
logged: list[str] = []
report_error = ydl.report_error

def note(message: Any, *args: Any, **kwargs: Any) -> None:
    logged.append(str(message))
    report_error(message, *args, **kwargs)

ydl.report_error = note  # What yt-dlp's cookie logger calls.
try:
    _ = ydl.cookiejar
except (CookieLoadError, DownloadError) as exc:
    ...  # As today.
finally:
    del ydl.report_error
if logged:
    raise CookiesUnavailable(spec, logged_failure(logged[0]))
```

An instance attribute, not `_Ydl.errors`, because `cli._show_preview` passes a plain `YoutubeDL`, and `:935` is swallowed there too. `fake_site` is unaffected: `FakeYdl` pops `cookiesfrombrowser`, so the function returns early. Raising leaves the `_Ydl` holding that error behind: every caller builds a new one for the run without cookies, so the entry never reaches a count.

**2. One count per item.** In `_Ydl`:

```python
repeated_errors = 0  # Errors beyond the first of an item that failed in several steps.

def process_info(self, info_dict):
    ...
    if self.item_failed():
        self.repeated_errors += len(self.errors) - self.item_errors - 1
        self.drop_thumbs()
```

`DownloadResult.failed` becomes `len(ydl.errors) - ydl.repeated_errors`. `ydl.errors` itself keeps every line, since `DownloadError("\n".join(ydl.errors))` and the hints read it.

**3. What counts as an error.** `_Ydl.report_error` records the message only if `kwargs.get("is_error", True)`. It still calls `super()`, so yt-dlp prints as before. `_ProbeYdl` inherits this, which also keeps those lines out of `probe_partial`.

### `cookies.py`

Add `logged_failure(message: str) -> CookieFailure`, with `missing=False` and the same reason `cookie_failure` builds. Split `cookie_failure`'s cleaning (label, escape codes, first line, length) into a helper both use, so the reason reads the same wherever the failure came from.

### `probe.py`

In `probe()`, inside the `with`, call `load_browser_cookies(ydl, cookies_browser)` before `extract_info`. Catch `CookiesUnavailable` next to `except Exception`: `_read_result(url, site, None, [exc.failure.reason])`, with `cookie_failure` set to `exc.failure`. `wizard._drop_unreadable_browser` needs no change. The `except Exception` branch's `cookie_failure(exc)` stays for anything raised later, and is otherwise unreachable for a load failure; the developer may simplify it if no test objects.

### What the user sees

```
$ download-media -y -p --cookies chrome+kwallet6 -o ~/tmp/x 'http://127.0.0.1:18945/two.html'
Extracting cookies from chrome
Downloading...
  ...
ERROR: kwallet-query failed with return code 1. Please consult the kwallet-query man page for details
Extracted 1 cookies from chrome
Could not read the chrome cookies (kwallet-query failed with return code 1. Please consult the kwallet-query man page for details). Going on without cookies.
[download] Downloading playlist: two
...
Done.
Saved without chrome cookies: this is what the site shows without logging in.

Equivalent command to repeat this download:
  download-media -y -o /home/u/tmp/x -p http://127.0.0.1:18945/two.html
```

## Cases

"Today" is measured on `728834b`, "After" on the prototype, both with the fake profile and the environment above, unless marked. A notice is `cookies_unreadable`; the closing line is `done_without_cookies`.

| Case                                                                 | Today                                                       | After                                                                                                   |
| -------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `-y`, `kwallet-query` fails                                          | `1 of 2`, `Done.`, no notice, `Cookie: s=`, keeps `--cookies` | Notice, no cookies sent, no count, `Done.`, closing line, drops `--cookies`, exit 0, 1 file               |
| `-y`, the other three causes                                         | Already as "After" (Findings)                               | Unchanged                                                                                               |
| `-y -p`, each of the four causes                                     | `2 of 3`, `Done.`, no notice, `Cookie: s=`, keeps `--cookies` | Notice (with the `pacman` hint for `secretstorage`), no cookies sent, no count, closing line, drops `--cookies`, exit 0, 2 files |
| `-y --playlist-items 1`                                              | `1 of 2`                                                     | As `-y -p`, command keeps `--playlist-items 1`, 1 file                                                  |
| `-y -P`                                                              | As `-y`                                                     | As `-y`                                                                                                 |
| Direct with preview, Enter                                           | Line twice, no notice, `1 of 2`                              | yt-dlp's line once, notice before `Preview`, title shown, no second read of the browser, closing line, exit 0 |
| Direct with preview, `n`                                             | yt-dlp's line, title, `Cancelled.`, no notice, exit 1       | Notice, title, `Cancelled.`, no command, exit 1 (row 23's)                                              |
| `-l`                                                                 | No notice, the list, `Cookie: s=`                            | Notice, the list, no cookies sent, exit 0                                                               |
| Bare `chrome` on KDE (yt-dlp picks kwallet)                          | `1 of 2`, no notice                                          | Notice, then `cookies_keyring_hint`, closing line, exit 0                                               |
| Wizard `-i`, single                                                  | Silent, `1 of 2`, `Done.`                                    | Notice once at the probe, cookies step defaults to `No cookies`, no count, `Done.`, no closing line (spec 50 D2), drops `--cookies`, exit 0 |
| Wizard `-i`, carousel                                                | False `probe_partial`, `2 of 3`, `Done.`                     | As above: no `probe_partial`, the picker, 2 files                                                       |
| Wizard, late step picks chrome, KDE desktop, carousel                | `2 of 3`, `Done.`, no notice, keeps `--cookies`             | Notice and keyring hint at the download (no probe follows the step), closing line, drops `--cookies`, keeps the items |
| Wizard, page that answers 401                                        | `auth_still_needed` (`check that you are logged in`, the wrong advice), manual flow, retry, `Download failed` | Notice, `This content needs a login`, the retry prompt, manual flow on `No cookies`, `Download failed`, exit 1 |
| `-y -p`, two URLs                                                    | `1 of 2`, then `2 of 3`, both `Done.`, keeps `--cookies`    | Notice and closing line per URL (spec 26 D8), no count, 3 files, drops `--cookies`, exit 0              |
| Page that answers 401, `-y` and `-y -p`                              | `Failed with chrome+kwallet6 cookies. Retrying…`, `Download failed` | Notice, `Download failed`, `→ try again with --cookies <browser>`, no retry line, keeps `--cookies`, exit 1 |
| Mixed profile, page opened by the `v10` cookie, `-y` and `-y -p`     | Saves, `1 of 2`, exit 0, `Cookie: good=1; s=`                | **Notice, `Download failed`, exit 1, nothing saved** (D1)                                               |
| `v10`-only profile, KDE                                              | `Done.`, `Cookie: good=1`, keeps `--cookies`                 | Unchanged: the keyring is never asked                                                                   |
| Plaintext profile, or a browser that loads                           | Cookies sent                                                 | Unchanged                                                                                               |
| Browser not installed, dead bus                                      | Spec 26's                                                    | Unchanged                                                                                               |
| `chrome+basictext` on a `v11` cookie                                 | `Extracted 0 cookies … (1 could not be decrypted)`, `Done.`  | Unchanged (Out)                                                                                         |
| `-y -p`, one item broken, one good, no cookies                       | `1 of 2 … 1 failed`                                          | Unchanged                                                                                               |
| `-y -p -a`, same page, any `-f`                                      | `1 of 3 … 2 failed`, 2 files, exit 0                         | `1 of 2 … 1 failed`, same files, exit 0                                                                 |
| `-y -p`, same page, with a keyring cause                             | `1 of 3 … 2 failed`; `-a`: `1 of 4 … 3 failed`               | Notice, `1 of 2 … 1 failed`, closing line                                                               |
| `-y -p -a`, every item broken                                        | `Download failed`, exit 1                                    | Unchanged                                                                                               |
| An item fails extraction under `-p` (`fake_site` `partial-*`)        | Counts one                                                   | Unchanged                                                                                               |
| An extractor's unscoped `cookies`, single and `-p`                   | Saved, `1 of 2 … 1 failed`, `Done.`                          | Saved, no count, `Done.`; yt-dlp's `ERROR:` line stays                                                  |
| `LANG=es*`                                                           | —                                                            | The approved Spanish texts: `… Se continúa sin cookies.`, `Listo.`, `Guardado sin las cookies de chrome: …` |

Rerun doesn't apply: nothing on disk depends on this. `-a` and video behave alike apart from the steps counted above.

## Acceptance criteria

Every `[fix]` must fail on `main`, shown by reverting the fix. Keyring tests run the real yt-dlp and generic extractor on the loopback `media_url` pages, through `direct` and `generic`, with `unreadable_chrome`'s fake profile and the dead bus from `isolated_env`. No test reads a real cookie database or keyring, and no test runs the real `kwallet-query`.

New fixtures, in `tests/test_e2e.py` next to `no_kwallet_query`: `kwallet_query_fails` (`PATH` holding ffmpeg, ffprobe and a `kwallet-query` script that does `exit 1`), and `empty_keyring` (`yt_dlp.cookies.secretstorage` set to a stub whose collection has no items). `no_kwallet_query` and `no_secretstorage` already exist. `KEYRING_CAUSES` parametrizes over the four, each with its spec and expected reason.

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `test_a_keyring_error_goes_on_without_cookies`. Parametrized over `KEYRING_CAUSES` and `-y -p`, `-y --playlist-items 1,2` on `seen/<token>/multi.html`: exit 0, the expected files, `t("cookies_unreadable", "chrome", <reason>)` once, `said_partial_result(output)` false, `after_done(output) == [without_cookies()]`, command without `--cookies`, one history row, and `sent(token)` shows no `Cookie` on any request
- [ ] **[fix]** `test_a_keyring_error_in_single_mode_goes_on_without_cookies`. `kwallet_query_fails`, `-y --cookies chrome+kwallet6` on `seen/<token>/poster.html`: as above, 1 file
- [ ] **[fix]** `test_a_keyring_error_is_named_before_the_preview`. `kwallet_query_fails`, no `-y`, Enter: one prompt, the notice once and before `t("preview")`, the `t("p_title")` line, `kwallet-query failed` exactly once in the output, closing line after `t("done")`, exit 0, 1 file
- [ ] **[fix]** `test_a_preview_cancelled_after_a_keyring_error_still_said_it`. Same, `n`: the notice before `t("preview")`, `t("cancelled")`, no files, no command
- [ ] **[fix]** `test_formats_are_listed_after_a_keyring_error`. `kwallet_query_fails`, `-l` on `seen/<token>/poster.html`: exit 0, the notice, the format table, no `Cookie` sent
- [ ] **[fix]** `test_a_desktop_keyring_error_suggests_another_keyring`. `kwallet_query_fails`, `XDG_CURRENT_DESKTOP=KDE`, `KDE_SESSION_VERSION=6`, `-y --cookies chrome`: the notice, then `keyring_hint()`, closing line
- [ ] **[fix]** `test_a_dead_page_after_a_keyring_error_fails_without_a_retry_line`. `kwallet_query_fails`, `-y -p` and `-y` on `status/401`: exit 1, the notice, `t("failed", url)`, `try again with --cookies`, `t("cookies_retry", …)` and `keyring_hint()` not in the output, command keeps `--cookies`, no closing line
- [ ] **[fix]** `test_a_page_only_the_readable_cookie_opens_now_fails` (pins D1's cost; goes if the human picks another option). A profile with `chrome_cookie()`'s plaintext cookie plus one `v11` row, `kwallet_query_fails`, `-y --cookies chrome+kwallet6` on `cookie/<token>/poster.html`: exit 1, the notice, no file, no `Cookie` sent
- [ ] **[pin]** `test_a_jar_that_never_asks_the_keyring_keeps_its_cookies`. A `v10`-only profile (the test encrypts it with yt-dlp's `aes_cbc_encrypt_bytes` and the `peanuts` key), `kwallet_query_fails`, KDE desktop, `-y -p --cookies chrome` on `seen/<token>/multi.html`: exit 0, the notice and the closing line not in the output, `Cookie` on every request, command keeps `--cookies`
- [ ] **[pin]** `test_a_browser_that_loads_sends_its_cookies_to_every_item`, `test_a_browser_that_loads_costs_no_extra_request`, `test_cookies_that_load_and_get_refused_still_retry` pass unchanged
- [ ] **[fix]** `test_the_wizard_names_a_keyring_error_at_the_probe`. `kwallet_query_fails`, `-i --cookies chrome+kwallet6` on `multi.html`, `PICKER_WALK`: the notice once, `t("probe_partial", …)` not in the output, the picker offered, `t("done_without_cookies", "chrome")` not in the output (spec 50 D2), command without `--cookies`, 4 files, `said_partial_result(output)` false
- [ ] **[fix]** `test_a_keyring_error_at_the_last_wizard_step_says_so_after_done`. `kwallet_query_fails`, `XDG_CURRENT_DESKTOP=KDE`, `KDE_SESSION_VERSION=6` (the late step offers bare browsers only), chrome at the late step on `multi.html` (spec 26's `PICKER_WALK`): the notice once, `keyring_hint()`, closing line after `t("done")`, command keeps the items and drops `--cookies`
- [ ] **[fix]** `test_a_keyring_error_is_a_cookie_failure` (`tests/test_probe.py`). `probe(<media_url>/poster.html, "chrome+kwallet6")` with `kwallet_query_fails`' `PATH` and a fake profile: `failed` true, `cookie_failure.missing` false, the reason names `kwallet-query failed`. The two existing tests, `test_a_browser_that_is_not_installed_is_a_cookie_failure` and `test_a_probe_without_a_browser_has_no_cookie_failure`, pass unchanged
- [ ] **[fix]** `test_a_logged_error_reads_like_a_raised_one` (`tests/test_cookies.py`). `logged_failure("kwallet-query failed with return code 1.\nmore")` → `missing` false, reason the first line; a label and escape codes come off as in `test_a_labelled_cause_loses_its_label`; a reason longer than the limit is shortened
- [ ] **[pin]** `test_loading_cookies_leaves_report_error_alone` (`tests/test_downloader.py`). After `load_browser_cookies`, on success and on `CookiesUnavailable`, `"report_error" not in vars(ydl)`
- [ ] **[fix]** `test_an_item_failing_in_two_steps_counts_once` (`tests/test_e2e.py`). Parametrized `-y -p -a`, `-y -p -a -f wav`, `-y --playlist-items 1,2 -a` on `junk-and-mp4.html`: exit 0, `t("partial_result", 1, 2, 0, 1)` in the output, `t("done")`
- [ ] **[fix]** `test_a_keyring_error_and_a_broken_item_count_the_item_once`. `kwallet_query_fails`, `-y -p -a --cookies chrome+kwallet6` on `junk-and-mp4.html`: exit 0, the notice, `t("partial_result", 1, 2, 0, 1)`, closing line after `t("done")`
- [ ] **[fix]** `test_a_message_yt_dlp_does_not_call_an_error_is_not_a_failure`. A new `fake_site` shape with an unscoped `cookies` field on its format (`tests/conftest.py`), `-y` and `-y -p`: exit 0, 1 file, `said_partial_result(output)` false, `t("done")`
- [ ] **[pin]** `test_playlist_counts_an_item_that_is_not_media_as_failed_only`, `test_playlist_where_no_item_is_media_is_a_failure` and the `t("partial_result", 1, 2, 0, 1)` tests on `partial-*`, `test_e2e.py:518`, `:1381`, `:1827`, `:2586` pass unchanged
- [ ] **[flip]** `test_a_keyring_that_decrypts_nothing_in_a_playlist_is_not_a_load_failure` (`test_e2e.py:6773`) pins today's behaviour and becomes `test_a_keyring_tool_that_is_missing_in_a_playlist_is_unreadable`, with the assertions of its single-mode sibling at `:6789`. It's the only existing test that flipped on the prototype; any other flip must be explained in the developer's report
- [ ] `test_es_and_en_have_same_keys` passes with no new key: `git diff 728834b -- src/download_media/i18n.py` is empty
- [ ] On disk, by hand, once, with the Problem's environment: runs 1, 2 and 3, comparing the files with what's reported

## Live check

Not needed. Every path is the real yt-dlp on loopback with a fake profile and a `kwallet-query` that fails. What's under test is what yt-dlp does after the keyring says no, not the keyring. A real one means touching the human's keyring, which `constraints.md` rules out outside the tool, for no extra information. A real site only changes what the cookieless run returns, which this item doesn't read.

## SemVer

`fix`, nothing breaking by `AGENTS.md`'s list: no flag, default, path or message changes. No new message, so nothing here needs the minor, and 0.4.0 is a minor already.

**For the "Next release" review list:** published behaviour changes in three ways.

1. **`N of M` now counts items.** A number in the line can only go down: `-p -a` on a page with one broken item says `1 of 2`, not `1 of 3`. A run whose only extra entry came from a keyring error, or from a message yt-dlp doesn't call an error, no longer prints the line. A script reading that line sees different numbers.
2. **A keyring error yt-dlp only logs now drops the browser's cookies.** Under `-p`, `--playlist-items`, a picker run, `-l` and the wizard's probe for all four causes, and in every mode for `kwallet-query` failing, the run used to send the jar it had. It now says why and sends none. The printed command drops `--cookies` when the run saved, by spec 26 D6's rule. The wizard's `Some items could not be inspected: kwallet-query failed …` is gone.
3. **Exit 0 to exit 1 in one shape:** a page that opens only with a cookie yt-dlp could still decrypt, in a profile where another cookie needs the keyring. D1.

**Release note (for the planner):** "When a browser's keyring can't be read (`kwallet-query` failing, `secretstorage` missing), the tool now says so and goes on without cookies in every mode. `-p`, `--playlist-items` and `-l` used to carry on silently with cookies yt-dlp couldn't decrypt, and to count the error as a failed item. The `N of M items downloaded` line now counts each item once, however many steps of it failed."

This makes 0.4.0's note for row 50 true for this path too: the user is told.

## Open decisions

### D1: after a keyring error yt-dlp only logs

| Option                                                                                     | Effect                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Treat it as a load failure: notice, no cookies, closing line, every mode (recommended)** | One outcome per cause in every mode, as spec 26 D1 chose and single mode already does for three of the four. No garbled cookie reaches the site, which `_download_with_cookie_fallback`'s docstring names as a cause of breakage. No new message, field or flag, and no `cli.py` change. **Cost: a mixed profile loses the cookies that did decrypt**, so a page that only they open goes from saved, exit 0, to `Download failed`, exit 1. The notice names the keyring, and the bare-browser case gets the keyring hint |
| B. Keep the jar and only say so                                                             | Nothing the site sees changes, and the mixed profile keeps saving. Needs two new messages (`done_without_cookies` would be false: some cookies were used), a new `DownloadResult` field, and a flag from the preview so the notice doesn't print twice. The garbled cookie stays on the wire, and the same cause keeps two outcomes by mode: `secretstorage` missing drops the browser in single mode and keeps it under `-p` |
| C. A, then retry with the jar when the cookieless run fails                                 | The mixed profile keeps saving, at one more full attempt on every keyring failure of a dead or gated page, against sites that rate-limit by IP (spec 26 D4). It adds a third state to the fallback, and the jar's cookies were sent after a notice saying they wouldn't be |

**Recommendation: A.** Mixed profiles exist, since a browser re-encrypts cookies as they change, but a login session is the cookie most likely to be rewritten recently, and so the one needing the keyring. If the human picks B, D2 and the first group of criteria change, and this item grows: send it back.

### D2: the wizard's probe

| Option                                                                                       | Effect                                                                                                                                                                        |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. The probe goes through `load_browser_cookies`; the walk says it once and drops the browser (recommended)** | The missing browser's path (spec 26 D5), so the picker and the steps are for what a cookieless run sees, the false `probe_partial` goes, and a login wall reaches the retry prompt. Five lines in `probe.py`. No closing line, so this joins spec 50 D2's open question |
| B. Leave the probe; the download says it                                                      | No `probe.py` change, and the walk gets the closing line. But the walk is shaped by a jar the download won't use, the false `probe_partial` stays, and the notice comes only after every question                                 |

**Recommendation: A.** If the human moves spec 50 D2 to its option C, this path gets the closing line with it.

### D3: messages yt-dlp itself doesn't call errors

| Option                                                                 | Effect                                                                                                                                                       |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Don't count them (recommended)**                                  | One condition in `_Ydl.report_error`. Beyond the row's text, but the same wrong number on a saved item, and in every mode. Only an extractor's unscoped `cookies` is known to reach it |
| B. Leave it, as a note for the planner                                 | The item stays closer to the row, and that rare `1 of 2` stays                                                                                               |

**Recommendation: A.** The goal is that the count means items, and this is the one line that makes that hold.
