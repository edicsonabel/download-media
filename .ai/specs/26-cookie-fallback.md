# 26 — Handle a browser whose cookies can't be read

Status: done
Item: `fix/cookie-fallback`

## Problem

When yt-dlp can't load the cookies of the browser passed to `--cookies` (not installed, no cookie database, keyring unreachable), `-p`, `--playlist-items` and a picker run end in a traceback, and the other modes print a misleading message and keyring hint.

Reproduced on `main` at `da69440`, with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. The real console script (`.venv/bin/download-media`) ran against a scratch loopback `http.server` under a clean environment: `PATH=/usr/bin:/bin`, `HOME` and `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` in a fresh temp folder per run, `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `LANG=en_US.UTF-8`, an absolute `-o`, no clipboard, and `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/bus`, so no run could reach a real keyring. A browser that isn't installed is the empty `HOME`. `one.html` holds one `<video>`, `two.html` two. No real cookie database or keyring was read.

```bash
download-media -y -p --cookies chrome -o <abs dir> http://127.0.0.1:<port>/two.html
# [generic] two: Downloading webpage
# Extracting cookies from chrome
# ERROR: could not find chrome cookies database in "$HOME/config/google-chrome"
# Traceback (most recent call last):
#   ...
# yt_dlp.cookies.CookieLoadError: failed to load cookies
# exit 1, no files
```

- **What happened:** a traceback, exit 1, nothing saved, no printed command. With several URLs, the later ones never run.
- **What should happen:** one clear message saying the browser's cookies couldn't be read and why, then the download goes on without cookies, as single mode already does. Exit by what that gives. No traceback, no keyring hint where it doesn't apply.

## Findings

### Every path, measured on `main`

| Run (browser not installed unless said)                                      | Output                                                                                                         | Files            | Exit |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ---------------- | ---- |
| `-y -p --cookies chrome two.html`                                            | yt-dlp `ERROR: could not find chrome cookies database…`, **traceback**                                         | none             | 1    |
| `-y -p --cookies firefox two.html`                                           | Same, with Firefox's five search paths                                                                         | none             | 1    |
| `-y --playlist-items 1 --cookies chrome two.html`                            | **Traceback**                                                                                                  | none             | 1    |
| `-y -p --cookies chrome two.html one.html`                                   | **Traceback** on the first URL; the second never runs                                                          | none             | 1    |
| Wizard on `two.html`: picker, Enter ×4, late cookies step `3` (chrome)        | **Traceback** after six answers                                                                                | none             | 1    |
| `-y -p --cookies chrome` with an empty `google-chrome/Default` folder         | Same as not installed: "could not find chrome cookies database"                                                | none             | 1    |
| `-y -p --cookies chrome+gnomekeyring`, fake profile with one v11 cookie, no D-Bus | `ERROR: [Errno 2] No such file or directory`, **traceback**                                                | none             | 1    |
| `-y --cookies chrome one.html`                                               | `Failed with chrome cookies. Retrying without cookies...`, `Done.`, command without `--cookies`                | `one (1).mp4`    | 0    |
| `-y --cookies chrome+gnomekeyring one.html`, no D-Bus                        | Same, `Failed with chrome+gnomekeyring cookies…`                                                               | `one (1).mp4`    | 0    |
| `-y --cookies chrome nope.html` (404)                                        | `Failed with chrome cookies…`, `Download failed`, **`→ if chrome stores its keys in another keyring…`**, command keeps `--cookies chrome` | none | 1 |
| `-y --cookies chrome+gnomekeyring nope.html`, no D-Bus                       | Same, no keyring hint (an explicit keyring turns it off)                                                       | none             | 1    |
| `-l --cookies chrome one.html`                                               | `Download failed` and **the keyring hint**, no format list                                                     | —                | 1    |

`Failed with chrome cookies` is false: nothing was ever fetched with cookies. The keyring hint after a missing browser points at the wrong cause. Row 26's note, `cli.py:329` and `cli.py:351`, is confirmed at `cli.py:329` (`except DownloadError`) and `cli.py:351-352` (the hint).

### Why single mode retries and `-p` doesn't (yt-dlp source)

- yt-dlp loads browser cookies lazily, on the first request, through `YoutubeDL.cookiejar` (`YoutubeDL.py:4218-4226`). `load_cookies` turns **any** exception into `CookieLoadError('failed to load cookies')`, keeping the real one as `__context__` (`cookies.py:92-113`). `cookiejar` reports that cause with `report_error`, then re-raises.
- Without `ignoreerrors`, `report_error` raises `DownloadError` itself, whose `exc_info[1]` is the `CookieLoadError`. That's what single mode's fallback catches.
- With `ignoreerrors` (`-p`, `--playlist-items`, a picker run), `report_error` only prints, and the bare `CookieLoadError` is re-raised. `_handle_extraction_exceptions` (`YoutubeDL.py:1729`) and `__download_wrapper` (`:3684`) re-raise it on purpose. `CookieLoadError` subclasses `YoutubeDLError`, not `DownloadError` (`cookies.py:89`).
- Measured with a scratch script on an empty `HOME`: `ignoreerrors=False` gives `DownloadError` → `exc_info[1]` `CookieLoadError` → `__context__` `FileNotFoundError('could not find chrome cookies database in "…"')`; `ignoreerrors=True` gives `CookieLoadError` with the same `__context__`. `cookiejar` is a `cached_property`, so a failure isn't cached: every access retries the load.

### What can fail to load

| Cause                                        | `__context__`                                                                 | Seen          |
| -------------------------------------------- | ----------------------------------------------------------------------------- | ------------- |
| Browser not installed, or a folder with no cookie database | `FileNotFoundError("could not find <browser> cookies database in …")` (`cookies.py:146`, `:318`) | measured |
| Keyring unreachable (no D-Bus, locked or dismissed) | Whatever `secretstorage`/`jeepney` raises: here `FileNotFoundError(2, …)`, with no "could not find" text | measured (no D-Bus) |
| `--cookies safari` on Linux                  | `ValueError('unsupported platform: linux')` (`cookies.py:569-570`)            | source        |
| Missing `secretstorage` or `kwallet-query`, single mode | yt-dlp's `logger.error` raises `DownloadError` inside `load_cookies`: becomes `CookieLoadError` | source |
| Same, under `ignoreerrors`                   | Nothing raised: yt-dlp logs an error and goes on with cookies it can't decrypt | source        |

"A wrong profile" can't be named: `--cookies` takes `BROWSER[+KEYRING]` and no profile, so yt-dlp picks the newest cookie database. A browser folder without one is the "not installed" row. A profile that isn't logged in, and a wrong keyring that decrypts nothing, raise nothing: the site answers as logged out, which spec 18 handles ("still gated").

### An empty `--cookies` result

`download()` raises only when yt-dlp's return code is non-zero and nothing was saved (`downloader.py:485`). Since spec 29, a `-p` run where every item fails takes that path, and the fallback retries it (`test_playlist_where_no_item_is_media_with_cookies_still_fails`). What's left of "comes back empty without an error": a carousel of images only, an empty list, and `--playlist-items` that selects nothing. All three end in an honest failure, exit 1. None is caused by cookies in any reproduction: the one cookie case the code comments name, Instagram's empty API answer, raises an error (`instagram.py:522`, spec 18), so the retry already runs. See D4.

### Row 42 (`unexpected-errors`)

Also confirmed: `download-media -y -o /proc/x <url> <url2>` ends in a `FileNotFoundError` traceback from `downloader.py:449`, and the second URL never runs. It's a different exception (the output folder), a different message and a different design (a catch-all), so it stays its own row (D7). Nothing here blocks it: this item catches only cookie-load failures, below `_download_one`, so a later catch-all there doesn't see them.

### The wizard's probe

`probe()` runs with `ignoreerrors` and a silent logger, and catches every exception (`probe.py:362-366`). On a missing browser it reports `Could not inspect URL: could not find chrome cookies database in "…"` and falls back to the manual flow, so the picker is lost on a URL that is perfectly readable without cookies. The download then hits the cookie failure again. Tests pin this today: `test_a_browser_picked_at_the_retry_that_is_not_installed_is_named` and `test_a_browser_that_is_not_installed_is_named_and_the_retry_still_saves` (`tests/test_e2e.py:4007-4027`).

## Goal

A browser whose cookies can't be read gets one message of the tool's own saying which browser and why, then the run goes on without cookies, in every mode. No traceback, no keyring hint unless a keyring is the likely cause, and later URLs always run.

## Scope

### In

- Detect a cookie-load failure the same way in every mode, before any request (D2).
- One message for a browser that isn't there, one for cookies that can't be read, with yt-dlp's reason (D3).
- Go on without cookies after it: direct, `-y`, `-p`, `-P`, `--playlist-items`, `-l`, multi-URL (D1).
- The wizard's probe: say it, drop the browser, re-probe without cookies, or reuse the cookieless probe it already has (D5).
- The hint after the cookieless attempt fails is the one for a run without cookies: no keyring hint.
- `isolated_env` guards the session bus, so no test can reach a real keyring.

### Out

- **Retrying an empty `--cookies` result** (D4): nothing reproduces it; the planner closes that half of row 26.
- **Row 42's catch-all** (D7): its own row.
- **The direct-mode preview** (`_show_preview`, `cli.py:182-186`) swallows the cookie failure and shows a preview with no title or size. No traceback and no wrong message, only missing metadata; the download that follows prints the message. For the planner.
- **`secretstorage` or `kwallet-query` missing under `-p`:** yt-dlp raises nothing, logs its error and goes on with cookies it can't decrypt. Nothing to catch; `secretstorage` is a required dependency anyway.
- **`safari` offered on Linux** by `--cookies` and the wizard, where it can never work. It reaches this item's generic message. For the planner.
- **yt-dlp's own `ERROR:` line** about the cookie database stays, as for every other yt-dlp error; it names the folders searched. The tool adds one line of its own.
- **Wrong keyring, logged-out profile:** no error from yt-dlp; spec 18's "still gated" path.

## Files

| Action | File                                  |
| ------ | ------------------------------------- |
| modify | `src/download_media/cookies.py`       |
| modify | `src/download_media/downloader.py`    |
| modify | `src/download_media/cli.py`           |
| modify | `src/download_media/probe.py`         |
| modify | `src/download_media/wizard.py`        |
| modify | `src/download_media/i18n.py`          |
| modify | `README.md` (one sentence, line 15)   |
| modify | `tests/conftest.py` (`isolated_env`)  |
| modify | `tests/test_e2e.py`                   |
| modify | `tests/test_cookies.py` (the classifier)  |
| modify | `tests/test_isolation.py` (the dead bus) |
| modify | `tests/test_probe.py`                 |

## Design

### yt-dlp behaviour relied on

All confirmed in the 2026.10.26 source and by the scratch measurements above:

1. Browser cookies load in `YoutubeDL.cookiejar`, a `cached_property` that isn't cached on failure. Reading it before `download()` loads them once, and the request director reuses that jar.
2. Every load failure surfaces as `CookieLoadError` (`yt_dlp.cookies`, also importable from `yt_dlp`), either bare (`ignoreerrors`) or as `DownloadError.exc_info[1]` (no `ignoreerrors`). Its `__context__` is the real cause.
3. The browser-missing cause is a `FileNotFoundError` whose text starts with `could not find ` and contains ` cookies database` (`cookies.py:146`, `:318`, `:584`). Anything else is "can't be read".

### `cookies.py`

A small classifier shared by the download and the probe:

```python
@dataclass(frozen=True)
class CookieFailure:
    missing: bool   # no cookie database: browser not installed, or never opened
    reason: str     # yt-dlp's cause, first line, at most ~160 chars

def cookie_failure(exc: BaseException) -> CookieFailure | None:
    """The cookie-load failure behind ``exc``, if that's what it is."""
```

It returns a failure for a `CookieLoadError`, or for a `DownloadError` whose `exc_info[1]` is one; `None` for anything else. And one reporter, used by `cli.py` and `wizard.py`, which prints:

- `missing` → `t("cookies_no_browser", browser)`.
- otherwise → `t("cookies_unreadable", browser, reason)`, then the same secondary line `_hint_for_download_error` would choose for that reason: the `secretstorage` install hint if the reason names `secretstorage`, else `cookies_keyring_hint` if `uses_keyring(spec)`. Never the keyring hint after `cookies_no_browser`.

`browser` is `browser_of(spec)`.

### `downloader.py`

A `CookiesUnavailable(Exception)` carrying the `CookieFailure`. In `download()`, right after `with _Ydl(ydl_opts) as ydl:` and before `ydl.download`, and in `list_formats()` the same way: when `ydl.params` has `cookiesfrombrowser`, read `ydl.cookiejar`; on `CookieLoadError` or `DownloadError`, `cookie_failure(exc)` decides: a failure raises `CookiesUnavailable`, anything else re-raises. The output folder may already exist by then; that's fine, it's what single mode's retry does today.

`fake_site`'s `FakeYdl` pops `cookiesfrombrowser`, so the check never runs there and no `fake_site` test changes.

### `cli.py`

- `_download_with_cookie_fallback`: on `CookiesUnavailable`, report it (above), then download once without cookies, and return `cookies_dropped=True`. The `DownloadError` branch and `cookies_retry` stay as they are, for cookies that loaded and broke the site. A `DownloadError` from the cookieless attempt propagates as itself, not as the cookie failure.
- `_download_one`: when that cookieless attempt fails, `_hint_for_download_error` gets the options without cookies. So a 404 gets no hint at all, a login wall gets `→ try again with --cookies <browser>`, and nothing gets the keyring hint. How the flag travels is the developer's choice, for example an attribute on the `CookiesUnavailable` path or a small result type.
- `main`'s `-l` branch: on `CookiesUnavailable`, report it and list again without cookies; the return code is yt-dlp's.
- `_print_equivalent` doesn't change (D6): the outcome already carries `cookies_dropped`.

### `probe.py`

`Probe` gains `cookie_failure: CookieFailure | None = None`. In the `except Exception as exc:` branch, `cookie_failure(exc)` fills it. The other fields stay as today (`failed=True`, the reason), so nothing else reads differently.

### `wizard.py` (D5)

In `_walk_url`, after each `probe(...)` made with a browser:

- If `probe_result.cookie_failure`: report it, set `opts.cookies_browser = None`, then
  - first probe of the walk (the browser came from `--cookies` or the up-front prompt): probe again without cookies and go on with that result. A login wall there reaches the retry prompt as usual, so the user can pick another browser, once.
  - re-probe after the retry prompt: keep the cookieless result the walk already has (failed, needs a login) and go on to the failed branch. No third probe, no loop.
- `auth_still_needed` isn't printed: `cookies_browser` is now `None`.
- The late cookies step needs nothing here: there's no probe after it, and the download handles it.

### `i18n.py`

Two messages, both blocks:

| Key                  | en                                                                                                            | es                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `cookies_no_browser` | No {0} cookies found: is {0} installed, and has it been opened at least once? Going on without cookies.        | No se encontraron cookies de {0}: ¿está {0} instalado y se ha abierto al menos una vez? Se sigue sin cookies.         |
| `cookies_unreadable` | Could not read the {0} cookies ({1}). Going on without cookies.                                                | No se pudieron leer las cookies de {0} ({1}). Se sigue sin cookies.                                                  |

The reason stays in English: it's yt-dlp's text, as in spec 18.

### `tests/conftest.py`

Outside live tests, `isolated_env` sets `DBUS_SESSION_BUS_ADDRESS` to a socket path under `tmp_path` that doesn't exist, and deletes `XDG_CURRENT_DESKTOP`, `DESKTOP_SESSION`, `KDE_FULL_SESSION` and `GNOME_DESKTOP_SESSION_ID` unless a test sets them. A test that builds a fake Chrome profile with an encrypted cookie would otherwise ask the real session keyring for "Chrome Safe Storage". Measured: with the dead bus, `secretstorage` fails at once with `[Errno 2] No such file or directory`.

### What the user sees

```
$ download-media -y -p --cookies chrome -o ~/tmp/x 'http://127.0.0.1:8000/two.html'
Downloading...
  ...
Extracting cookies from chrome
ERROR: could not find chrome cookies database in "/home/u/.config/google-chrome"
No chrome cookies found: is chrome installed, and has it been opened at least once? Going on without cookies.
[generic] two: Downloading webpage
...
Done.

Equivalent command to repeat this download:
  download-media -y -p -o /home/u/tmp/x http://127.0.0.1:8000/two.html
```

## Cases

"Today" is measured on `main` unless marked *source*. All with the browser not installed unless said.

| Case                                                              | Today                                                              | After                                                                                                           |
| ----------------------------------------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| `-y -p`, 2-video page                                             | Traceback, exit 1, no files, no command                            | `cookies_no_browser`, 2 files, `Done.`, exit 0, command without `--cookies`                                     |
| `-y --playlist-items 1`                                           | Traceback                                                          | Message, 1 file, exit 0, command keeps `--playlist-items 1`, no `--cookies`                                     |
| `-y -p --cookies firefox`                                         | Traceback                                                          | `cookies_no_browser` with `firefox`, saves                                                                      |
| `-y -p`, two URLs                                                 | Traceback on the first; the second never runs                      | Message once per URL (D8), both saved, exit 0                                                                   |
| `-y -p`, page that 404s                                           | Traceback                                                          | Message, `Download failed`, no hint, exit 1, command keeps `--cookies chrome`, no history row                   |
| `-y` / `-P`, single page                                          | `cookies_retry` (false), saves, exit 0                              | `cookies_no_browser` instead of `cookies_retry`; same file, exit and command                                     |
| `-y`, page that 404s                                              | `cookies_retry`, `Download failed`, keyring hint, exit 1           | Message, `Download failed`, no hint, exit 1, command keeps `--cookies`                                          |
| `-y`, login wall (401)                                            | `cookies_retry`, `Download failed`, keyring hint *(source)*        | Message, `Download failed`, `→ try again with --cookies <browser>`, no keyring hint, exit 1                     |
| direct with preview, single                                       | Blank preview, then as `-y`                                        | Blank preview (Out), then as `-y`                                                                               |
| `-l`                                                              | `Download failed`, keyring hint, exit 1                            | Message, the format list without cookies, exit 0                                                                |
| Empty `google-chrome/Default` folder                              | As not installed                                                   | As not installed: `cookies_no_browser`                                                                          |
| `chrome+gnomekeyring`, keyring unreachable, `-p`                  | `ERROR: [Errno 2]…`, traceback                                     | `cookies_unreadable` with `[Errno 2] No such file or directory`, no keyring hint (explicit keyring), saves      |
| `chrome+gnomekeyring`, keyring unreachable, single                | `cookies_retry`, saves                                             | `cookies_unreadable`, saves                                                                                     |
| `chrome`, GNOME desktop, keyring unreachable                      | As above *(source)*                                                | `cookies_unreadable`, then `cookies_keyring_hint`, saves                                                        |
| `safari` on Linux, `-p`                                           | Traceback *(source)*                                               | `cookies_unreadable` with `unsupported platform: linux`, saves                                                  |
| Wizard, late step picks a missing browser, picker run             | Traceback after six answers                                        | Message, selected items saved, exit 0, command keeps the items, no `--cookies`                                  |
| Wizard, late step picks a missing browser, single video           | `cookies_retry`, saves *(source)*                                   | Message, saves                                                                                                  |
| Wizard, `-i --cookies chrome` on a readable page                  | `Could not inspect URL: could not find…`, manual flow, preview, saves (7 prompts) | Message, re-probe, real detection, no preview (6 prompts), saves, command without `--cookies`     |
| Wizard, up-front prompt (cookie-hungry URL) picks a missing browser | Probe fails on the browser, manual flow *(source)*                 | Message, cookieless probe: real content (picker on a carousel); a login wall reaches the retry prompt once     |
| Wizard, retry prompt picks a missing browser (401)                | `Could not inspect URL: could not find…`, manual flow, exit 1      | Message, `probe_failed` with the login reason, manual flow, exit 1. No `auth_still_needed`, no third prompt     |
| Wizard, several URLs                                              | Per walk                                                           | Per walk: each starts from the command line, so each reports its own failure                                    |
| Cookies loaded and rejected by the site (`rejects-cookies`)       | `cookies_retry`, saves                                             | Unchanged                                                                                                       |
| Cookies loaded, empty result (`only-images`)                      | No retry, `no_supported_items`, exit 1                             | Unchanged (D4)                                                                                                  |
| `-a`                                                              | As video                                                           | As video: nothing audio-specific                                                                                |
| Partial result after going on without cookies                     | —                                                                  | `partial_result` as for any run                                                                                 |

Rerun doesn't apply: nothing on disk depends on the cookies.

## Acceptance criteria

Every `[fix]` must fail on `main`, shown by reverting the fix. Cookie-failure tests run the real yt-dlp: the `generic` and `home` fixtures and the loopback `media_url` pages (`multi.html` has four videos, `poster.html` one, `status/NNN` answers that status), with `-o` under `tmp_path` where the case is direct. A missing browser is the empty `HOME` and `XDG_CONFIG_HOME` that `isolated_env` and `home` give. No test reads a real cookie database or keyring.

- [x] `scripts/pre-release.sh --no-live` passes (tech-lead's run on the merge, see Validation)
- [x] **[fix]** `test_a_missing_browser_does_not_stop_a_playlist` (`tests/test_e2e.py`). `-y -p --cookies chrome` on `multi.html`: exit 0, 4 files, `t("cookies_no_browser", "chrome")` once, `Traceback` and `t("cookies_retry", "chrome")` and `t("cookies_keyring_hint", "chrome")` not in the output, printed command without `--cookies`, one history row
- [x] **[fix]** `test_a_missing_browser_keeps_the_chosen_items`. `-y --playlist-items 1,2 --cookies chrome` on `multi.html`: exit 0, 2 files, command keeps `--playlist-items 1,2`
- [x] **[fix]** `test_a_missing_firefox_is_named`. `-y -p --cookies firefox` on `multi.html`: exit 0, `t("cookies_no_browser", "firefox")`
- [x] **[fix]** `test_a_missing_browser_is_named_for_every_url`. `-y -p --cookies chrome multi.html poster.html`: exit 0, 5 files, the message twice
- [x] **[fix]** `test_a_dead_page_with_a_missing_browser_fails_without_a_keyring_hint`. `-y -p --cookies chrome` on `status/404`: exit 1, `t("failed", url)`, the message, no keyring hint, no `Traceback`, command keeps `--cookies chrome`, no history row
- [x] **[fix]** `test_a_single_video_with_a_missing_browser_says_so_instead_of_retrying`. `-y --cookies chrome` on `poster.html`: exit 0, 1 file, the message, `t("cookies_retry", "chrome")` not in the output. Replaces the assertions of `test_a_browser_that_is_not_installed_is_named_and_the_retry_still_saves` for the direct case
- [x] **[fix]** `test_a_single_dead_page_with_a_missing_browser_gets_no_keyring_hint`. `-y --cookies chrome` on `status/404`: exit 1, `t("cookies_keyring_hint", "chrome")` not in the output
- [x] **[fix]** `test_a_login_wall_with_a_missing_browser_suggests_another_browser`. `-y --cookies chrome` on `status/401`: exit 1, `try again with --cookies` in the output, no keyring hint
- [x] **[fix]** `test_formats_are_listed_without_a_missing_browser`. `-l --cookies chrome` on `poster.html`: exit 0, the message, `t("failed", url)` not in the output, the format table printed
- [x] **[fix]** `test_a_missing_browser_at_the_last_wizard_step_saves_the_picked_items`. Wizard on `multi.html`: Enter at the URL, picker, type, quality, container and folder, then chrome at the late step: exit 0, the 4 files in `home/Videos`, the message, no `Traceback`, command without `--cookies`
- [x] **[fix]** `test_an_unreachable_keyring_named_by_hand_gets_no_keyring_hint` (fixture `unreadable_chrome`). Keyring unreachable: a fake Chrome profile under `XDG_CONFIG_HOME/google-chrome/Default/Cookies` (a `meta` table with version 20, a `cookies` table with one `v11` row), the dead bus from `isolated_env`. `-y -p --cookies chrome+gnomekeyring` on `multi.html`: exit 0, 4 files, `t("cookies_unreadable", "chrome", <reason>)`, no keyring hint
- [x] **[fix]** `test_an_unreachable_desktop_keyring_suggests_another_keyring`. Same profile, `XDG_CURRENT_DESKTOP=GNOME`, `-y -p --cookies chrome`: `t("cookies_unreadable", …)` then `t("cookies_keyring_hint", "chrome")`
- [x] **[fix]** `test_safari_on_linux_is_unreadable`. `-y -p --cookies safari` on `multi.html`: exit 0, `t("cookies_unreadable", "safari", "unsupported platform: linux")`
- [x] **[fix]** `test_a_browser_that_is_not_installed_is_named_and_the_page_still_detected` (renamed from the spec 18 test). Wizard `-i --cookies chrome` on `poster.html`: 6 prompts, the message once, `t("probe_failed", …)` not in the output, 1 file, command without `--cookies`. Replaces the 7-prompt walk of `test_a_browser_that_is_not_installed_is_named_and_the_retry_still_saves`
- [x] **[fix]** `test_a_browser_picked_at_the_retry_that_is_not_installed_ends_the_retry` (renamed from `test_a_browser_picked_at_the_retry_that_is_not_installed_is_named`). Wizard on `status/401`, chrome at the retry prompt: exit 1, the message once, `t("probe_failed", <login reason>)`, `t("probe_falling_back")`, `t("auth_still_needed", "chrome")` and `could not find chrome cookies database` not in the output, `t("ask_cookies")` printed once. Replaces `test_a_browser_picked_at_the_retry_that_is_not_installed_is_named`
- [x] **[fix]** `test_a_browser_that_is_not_installed_picked_up_front_still_detects_the_page`. Wizard on a cookie-hungry loopback URL (`poster.html?from=instagram.com`, as spec 18), chrome at the up-front prompt: the message, the page detected as a video, no second cookies prompt, 1 file, exit 0
- [x] **[fix]** `test_a_browser_that_is_not_installed_is_a_cookie_failure` and `test_a_probe_without_a_browser_has_no_cookie_failure` (`tests/test_probe.py`). `probe(<media_url>/poster.html, "chrome")` with the empty home: `cookie_failure.missing` is true. Without a browser: `cookie_failure is None`
- [x] **[fix]** `test_a_browser_that_is_not_installed_is_missing`, `test_without_ignoreerrors_the_failure_comes_inside_a_download_error`, `test_any_other_cause_is_unreadable_with_its_reason`, `test_anything_else_is_not_a_cookie_failure` (`tests/test_cookies.py`). Unit: `cookie_failure` on a `CookieLoadError` whose `__context__` is `FileNotFoundError('could not find chrome cookies database in "x"')` → `missing`; on a `DownloadError` whose `exc_info[1]` is that error → the same; on a `CookieLoadError` with `ValueError('unsupported platform: linux')` → not `missing`, that reason; on a plain `DownloadError` → `None`
- [x] **[pin]** `test_cookie_fallback_recovers` (direct and `-p`, asserts `cookies_retry`), `test_failure_with_cookies_reports_original_error_and_keyring_hint`, and on `rejects-cookies-carousel` `test_selection_survives_the_cookie_retry` and `test_cookie_drop_applies_to_every_mode` (they assert the save and the dropped `--cookies`, not the `cookies_retry` text). None changed in the merge, and `_download_with_cookie_fallback`'s `DownloadError` branch is unchanged; on a real browser that loads, `test_cookies_that_load_and_get_refused_still_retry` asserts the message. `fake_site` cookie tests unchanged: `rejects-cookies` and `rejects-cookies-carousel` still print `t("cookies_retry", "brave")` and save; `test_failure_with_cookies_reports_original_error_and_keyring_hint` still passes
- [x] **[pin]** `test_cookies_that_load_and_find_only_images_are_not_retried`. `-y -p --cookies brave` on `fake_site` `only-images`: exit 1, `t("no_supported_items")`, no `cookies_retry`, `fake_site == [("brave",)]`
- [x] **[pin]** `test_no_test_can_reach_the_real_session_bus` and `test_no_desktop_picks_a_keyring` (`tests/test_isolation.py`); `test_live_tests_keep_the_real_home` checks live tests keep both. With the dead bus set by `isolated_env`, no test outside `tests/live` can open the session bus: a test asserting `os.environ["DBUS_SESSION_BUS_ADDRESS"]` points under `tmp_path`
- [x] `test_es_and_en_have_same_keys` (`tests/test_i18n.py`); `test_unreadable_cookies_in_both_languages`. Both messages exist in the `es` and `en` blocks of `i18n.py`
- [x] Checked in the merge diff: `README.md` line 15 gains one sentence: if the browser's cookies can't be read, the download says why and goes on without them

**Commits**, so D5 can be reverted on its own:

1. `fix(cli): report unreadable browser cookies and go on without them`: `cookies.py`, `downloader.py`, `cli.py`, `i18n.py`, `conftest.py`, README, their tests.
2. `fix(wizard): re-probe without cookies when the browser can't be read`: `probe.py`, `wizard.py`, their tests and the two replaced spec 18 tests.

## Live check

Not needed. A missing browser is fully simulated by an empty `HOME`, with the real yt-dlp and the real generic extractor on loopback. A real keyring only changes which exception reaches `load_cookies`, and yt-dlp turns every one of them into `CookieLoadError` (`cookies.py:112-113`), which is all the design reads. Running it against a locked real keyring would mean touching the human's keyring for no extra information.

## SemVer

`fix`. Nothing breaking: no flag, default or path changes. Exit codes go from a traceback's 1 to 0 when the cookieless run saves; `-l` with an unreadable browser goes from 1 to 0. In the wizard, `-i --cookies <missing>` asks one prompt fewer (no preview), since the page is now detected. Two new messages, which `AGENTS.md` counts as minor; 0.3.0 is already a minor, so the next version doesn't change.

**Release note (for the planner):** "When the browser passed to `--cookies`, or picked in the wizard, isn't installed or its cookies can't be read, the tool now says so and goes on without cookies. `-p`, `--playlist-items` and the wizard's item picker used to end in a traceback, and the URLs after it never ran. Single downloads no longer say `Failed with <browser> cookies` or suggest another keyring when the browser simply isn't there. `-l` lists the formats without cookies instead of failing."

## Validation

On `main` at `c4421e3` (merge `a6f31bf`), yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14.9. GitHub CI run 42432662032 was in progress at validation time; the orchestrator checks its result.

- **Gate:** not re-run here, to keep the load down. The tech-lead ran `scripts/pre-release.sh --no-live` on the merge commit: exit 0, ruff and mypy clean, 1715 passed, 9 deselected, 2 xfailed in each locale run. `c4421e3` only changes `.ai/orchestration/state.json`.
- **Criteria:** each one is mapped to its test above, all in the gate's run.
- **Reverting the fix:** not repeated here; the testing and review stages showed it.
- **Reproduction:** the Problem's command with the real console script (`.venv/bin/download-media`, this checkout's `src/`) against a scratch `python -m http.server` on `127.0.0.1` serving `two.html` (two `<video>` tags, 0.2 s mp4s made with ffmpeg). Under `env -i` with `PATH=/usr/bin:/bin`, `HOME` and `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` in an empty temp folder (so no chrome), `XDG_VIDEOS_DIR` and `XDG_MUSIC_DIR` unset, `LANG=en_US.UTF-8`, `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/bus`, an absolute `-o`. No real cookie database or keyring was read.
  - `download-media -y -p --cookies chrome -o "$OUT" http://127.0.0.1:<port>/two.html`: yt-dlp's `ERROR: could not find chrome cookies database in "<tmp home>/config/google-chrome"`, then `No chrome cookies found: is chrome installed, and has it been opened at least once? Going on without cookies.` once, both items downloaded, `Done.`, the command `download-media -y -o <out> -p <url>` without `--cookies`, exit 0. No `Traceback`, no `Failed with chrome cookies`, no keyring hint.
  - On disk: 2 files, `01 - two (1) [two-1].mp4` and `02 - two (2) [two-2].mp4`, no `.part`. One history row. Before the fix: traceback, exit 1, no files.
  - On a pseudo-TTY (`script`), the lines come in the order of "What the user sees". With stdout redirected to a file, yt-dlp's `Extracting cookies from chrome` lands above `Downloading...` and the tool's message below `[generic] Extracting URL`. That's the tool's buffered `print` and yt-dlp's direct writes sharing one stream, true of every run, not of this item.
- **Scope:** outside `.ai/`, `5d20d8e..a6f31bf` touches the twelve files in the Files table plus `AGENTS.md` (one line, below). No change to `pyproject.toml`, `PKGBUILD`, `equivalent.py` or the version.

### What changed after approval

- **`isolated_env`'s desktop variables.** The design asked for them to be dropped; the implementation does it outside live tests only, and two tests nobody listed pin it: `test_no_desktop_picks_a_keyring`, and `test_live_tests_keep_the_real_home`, which now also checks that live tests keep the real bus and `XDG_CURRENT_DESKTOP`. A test that wants a desktop sets it (`test_an_unreachable_desktop_keyring_suggests_another_keyring` sets `GNOME`).
- **The `secretstorage` hint moved** out of `cli._hint_for_download_error` into `cookies.secretstorage_hint()`, so `report_failure` and the download error hint print the same two lines. Same text, still hardcoded English, as before (row 02).
- **`AGENTS.md`**, Testing: `isolated_env` keeps tests away from "the session keyring (a dead D-Bus address)". One line, not in the Files table.
- **`tests/conftest.py`** also gained, from the test-engineer, the `/cookie/<token>/` and `/seen/<token>/` loopback routes and `GATE_REQUESTS`, to pin that a browser whose cookies load still sends them and costs no extra request (`test_a_browser_that_loads_sends_its_cookies_to_every_item`, `test_a_browser_that_loads_costs_no_extra_request`).
- `cli.py` carries the "failed without cookies" flag as `_FailedWithoutCookies(DownloadError)`, and `-l` goes through a new `_list_formats` helper. Both are within what Design left to the developer.

### Remaining risks (QA, minor, for the planner)

1. QA flagged the Spanish wording `Se sigue sin cookies.` in both messages. `Se continúa sin cookies.` is one option; a one-line change per message in the `es` block.
2. The going-on-without-cookies line prints only before the download. In a long `-p` run it scrolls away, and nothing at the end (`Done.` or the summary) says the run used no cookies.
3. `AGENTS.md` was touched but isn't in the Files table, so two items touching it at once wouldn't show up as a collision.

## Open decisions

**Changes shipped behaviour, for the human's review: D1.** It was decided by the roles under delegation and is merged. After a browser's cookies can't be loaded, every mode now goes on without cookies. `-p`, `--playlist-items` and the picker used to end in a traceback with exit 1, and now save what a cookieless run can, exit 0. `-l` lists the formats without cookies, exit 0, where it failed with exit 1. Single mode saved before too, but now prints `cookies_no_browser` or `cookies_unreadable` instead of `Failed with <browser> cookies` and the keyring hint. Moving to D1's option B means changing only the `CookiesUnavailable` branches in `_download_with_cookie_fallback` and `_list_formats`, to stop the URL instead of downloading again.

Resolved by the roles under the human's delegation, each on the recommended option: D1 A, D2 A, D3 A, D4 A, D5 B, D6 A, D7 A, D8 A. D1 (go on without cookies after a cookie-load failure) changes shipped behaviour; the human reviews.

The human delegated these to the roles. Each one has a recommendation, chosen to lose no data and to be easy to undo.

### D1: after a browser's cookies can't be loaded

| Option                                                       | Effect                                                                                                                                                       |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Go on without cookies, every mode (recommended)**       | What single mode already does, so it changes no shipped outcome there. `-p` and the picker stop crashing and save what a cookieless run can                  |
| B. Stop that URL with the message, exit 1                    | Mirrors yt-dlp's own CLI. Single mode loses downloads it saves today: a regression                                                                           |
| C. Stop the whole run                                        | As B, and also drops the later URLs, which today's single mode runs                                                                                          |

**Recommendation: A.** The message says plainly that cookies were not used, so the result never reads as a logged-in download.

### D2: where the failure is detected

| Option                                                                               | Effect                                                                                                                                       |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Read `ydl.cookiejar` before `download()` and raise one exception (recommended)** | Same shape in every mode, before any request; `list_formats` gets it in the same place. Relies only on `cookiejar` and `CookieLoadError`  |
| B. Catch `CookieLoadError` next to `DownloadError` in the fallback, unwrap there     | Smaller, but unwraps in two places (`-l` too), and the failure still lands mid-extraction                                                   |
| C. Catch `YoutubeDLError`                                                            | Too wide: other errors would read as cookie failures, and it's row 42's ground                                                              |

**Recommendation: A.**

### D3: what the user is told

| Option                                                                                             | Effect                                                                                                    |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| **A. `cookies_no_browser` or `cookies_unreadable` with the reason; keyring hint only after the second, and only without an explicit keyring (recommended)** | Says what to do for the common case (not installed), and shows yt-dlp's cause for the rest |
| B. One generic message with yt-dlp's reason                                                        | One key, but "could not find chrome cookies database in …" for the most common case                      |
| C. Keep `cookies_retry`, drop only the keyring hint                                                | No new message, but keeps saying "Failed with chrome cookies" when nothing was tried                     |

**Recommendation: A.**

### D4: retry an empty `--cookies` result without cookies

| Option                                                 | Effect                                                                                                                                                                      |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. No retry; close that half of row 26 (recommended)** | No code. The all-failed `-p` case already retries since spec 29; the empty cases left (images only, empty list, items that select nothing) aren't caused by cookies anywhere measured |
| B. Retry when nothing was saved and cookies were given | A second fetch for every images-only post, on sites that rate-limit by IP, and a false `Failed with <browser> cookies` line                                                |

**Recommendation: A.** If a site is ever seen returning an empty list only with cookies, it comes back as a new row with that reproduction.

### D5: the wizard's probe with an unreadable browser

| Option                                                                                      | Effect                                                                                                                         |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| A. Leave it                                                                                 | No traceback either way, but `Could not inspect URL: could not find …`, the manual flow and a lost picker on a readable page, then the message again at the download |
| **B. Say it, drop the browser, re-probe without cookies or reuse the cookieless probe (recommended)** | One message, the real content and picker. A login wall reaches the existing retry prompt, which runs once                     |
| C. As B, but always re-ask the cookies step                                                  | Lets a mistyped choice be fixed at once, but adds a prompt and needs its own loop guard                                       |

**Recommendation: B**, in its own commit so it reverts alone. Under B, the retry prompt already gives C's second chance whenever cookies matter.

### D6: the printed command

| Option                                                                                         | Effect                                                                                                         |
| ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **A. Spec 09's rule unchanged (recommended)**                                                  | Direct: `--cookies` dropped when the cookieless run saved, kept when it failed. Wizard under D5: the browser is already dropped, so no `--cookies` |
| B. Always drop `--cookies` after a load failure                                                | Repeats exactly what ran, but forgets what the user asked for after a failure they may fix                    |
| C. Always keep it                                                                              | A successful run's command would hit the same failure every time                                               |

**Recommendation: A.**

### D7: row 42's catch-all

| Option                                       | Effect                                                                                                    |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| **A. Stays its own row (recommended)**       | One thing per item. Row 42 handles the output folder and anything else with a generic message; this item doesn't touch `_download_one`'s exception list |
| B. Fold it in                                | A bigger item mixing two failure classes and two message designs                                           |

**Recommendation: A.**

### D8: several URLs with the same unreadable browser

| Option                                      | Effect                                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| **A. Message per URL (recommended)**        | Each URL stands alone, as wizard walks do; no state across URLs                                |
| B. Once per run, then skip the browser      | Quieter, but needs run-wide state, and wizard walks can carry different browsers               |

**Recommendation: A.**
