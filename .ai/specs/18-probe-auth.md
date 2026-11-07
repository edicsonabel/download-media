# 18 — Make the probe see extraction errors

Status: approved
Item: `fix/probe-auth`

## Problem

The wizard's probe can't see why extraction failed, so its ask-for-cookies step never fires and the user reads "unknown".

Reproduced on `main` at `bf10163`, with yt-dlp 2026.10.26.

On `fake_site`, through `cli.main` with the real yt-dlp:

```python
probe("https://fake.test/needs-cookies")
# failed=True  needs_cookies=False  error_message=None
```

With the real console script and the real generic extractor on a loopback server, `HOME` and `XDG_*` pointed at a temp folder:

```bash
# server.py answers /sNNN with that status and serves static files otherwise
printf '\n\n\n\n\n\n\n\n' | download-media -i -o <folder> http://127.0.0.1:<port>/s401
# Could not inspect URL: unknown
# Continuing in manual mode (you'll choose everything by hand).
# ...six questions and a preview later...
# ERROR: [generic] s401: Unable to download webpage: HTTP Error 401: Unauthorized
# Download failed, exit 1
```

- **What happened:** "Could not inspect URL: unknown" on every failure tried: 401, 403, 404, 429, 451 and an unsupported page. No cookie prompt on a page that needs a login.
- **What should happen:** the wizard says why, and asks for cookies when cookies are the answer.

Two consequences, both measured on `main`:

- **A gated carousel loses its item picker.** On a scratch `gated-carousel` page, answering the late cookies step with a browser saves `Clip [v1].mp4` and `Clip [v2].mp4` through single mode: no picker, exit 0.
- **A user who declines up front is never asked again.** On a cookie-hungry site, `cookies_asked` removes the late step, so a gated post ends in "unknown", then `Download failed`, exit 1.

## Goal

When the wizard can't inspect a URL, the user reads the real reason. If cookies can help, the wizard asks once, re-probes, and goes on with the real content, picker included. Direct modes keep their files and exit codes.

## Findings

Every result here comes from a run, unless it says "source". "Prototype" is the design below, applied to a scratch copy of the tree; `src/` wasn't touched.

### What the plan's notes got right, and what they didn't

| Note                                                                       | Result                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ignoreerrors` turns errors into `None` (`probe.py:216`)                   | Confirmed. `wizard.py:508` then prints "unknown"                                                                                                                                                                              |
| `test_probe_never_raises` only covers this with a mock                     | Confirmed, and so do the three auth tests in `test_wizard_flow.py:170-192`, which script a `Probe`. Nothing runs `needs_cookies=True` through the real yt-dlp                                                                 |
| Dropping `ignoreerrors` also needs `ignore_no_formats_error`               | **True for carousels, and a trap.** See below: that option hides the very errors this item has to see                                                                                                                         |
| The thumbnails branch and `image_only_skipped` become reachable            | Confirmed under the recommended design, but only partly for real Instagram. Without cookies, a lone image raises "There is no video in this post" at `instagram.py:530` (source), before any thumbnail is read                |
| `_media_kind` uses `or` where `_get_formats` uses `is None`                | Confirmed. `{url, formats: []}` reads as video. Fixing it moves a row of `test_probe_and_match_filter_agree_on_nothing_to_fetch`: see Design                                                                                  |
| `_is_auth_error` is enough once errors are visible                         | **Not in the notes, and false.** It misses yt-dlp's default login text, and sends rate limits to the cookie prompt. See "What counts as an auth failure"                                                                      |

### `ignore_no_formats_error` hides what this item needs to see

The option isn't only read by `YoutubeDL`. Extractors read it too (`extractor/common.py:1245-1272`):

- `raise_no_formats(msg, expected=True)` becomes a warning.
- `raise_login_required(msg, metadata_available=True)` becomes a warning.
- `raise_geo_restricted(msg, metadata_available=True)` becomes a warning.

YouTube reports age gates, private videos, members-only videos and rate limits through `raise_no_formats(reason, expected=True)` (`extractor/youtube/_video.py:4083`), and then returns its metadata, thumbnails included. So with the option set, each of them arrives as `formats: []` plus thumbnails, which is what the probe calls an image.

Measured on pages built with yt-dlp's own raisers, which then return metadata as real extractors do:

| Page                                        | With `ignore_no_formats_error`   | Without it                                   |
| ------------------------------------------- | -------------------------------- | -------------------------------------------- |
| Age gate                                    | `image`, no cookie prompt        | Failed, asks for cookies                     |
| Private video                               | `image`, no cookie prompt        | Failed, asks for cookies                     |
| Members-only                                | `image`, no cookie prompt        | Failed, asks for cookies                     |
| Rate limit                                  | `image`                          | Failed, real reason                          |
| Geo block with metadata                     | `image`                          | Failed, real reason                          |
| Paid post (`metadata_available=True`)       | `image`, no cookie prompt        | Failed, asks for cookies                     |
| DRM video with a thumbnail                  | `image`                          | Failed, "This video is DRM protected"        |

So every one of those would tell the user "This content is a standalone image, not supported by yt-dlp" and exit 0. That's today's bug with a worse message. 49 `raise_no_formats` calls in yt-dlp pass `expected=True` on the same line, and 5 `raise_login_required` calls pass `metadata_available=True`. The behavior is the same at the floor.

### How the options compare

Measured over 37 pages on yt-dlp 2026.10.26, and again at the `pyproject.toml` floor, 2024.10.07. All 148 results, four options by 37 pages, were the same on both.

| Option                                                                     | Gated pages seen as errors | One bad entry                                    | Images in a carousel                                        | Lone image                       |
| -------------------------------------------------------------------------- | -------------------------- | ------------------------------------------------ | ----------------------------------------------------------- | -------------------------------- |
| **A.** Drop `ignoreerrors`, add `ignore_no_formats_error`                  | No: they read `image`      | **Fails the whole probe**, picker lost           | Dicts with `formats: []`                                    | `image`                          |
| **B.** Keep `ignoreerrors`, read errors as `_Ydl` does                     | Yes                        | Survives, reads `[image]`                        | `None`, and each adds a "No video formats found!" error     | Failed, "No video formats found!" |
| **C.** Keep `ignoreerrors`, add `ignore_no_formats_error`, read errors     | No: they read `image`      | Survives                                         | Dicts with `formats: []`                                    | `image`                          |
| **D.** B, plus a `process_video_result` override that hands back images    | Yes                        | Survives, and can be told apart from an image    | Dicts, no error noise                                       | `image`, when it has a thumbnail |

B works, but it can't say which `None` is an image and which is a failure without pairing errors to entries by position, and that pairing breaks on an entry the extractor left empty, which has no error. D removes the ambiguity: `None` then only ever means a failure or a hole.

### What counts as an auth failure

An auth failure is one where **browser cookies are the user's next move**. Today's `_AUTH_ERROR_HINTS` (`probe.py:114`) fails that test in both directions.

| Shape                            | Message                                                                                             | Where it comes from              | `_is_auth_error` today | Proposed |
| -------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------- | ---------------------- | -------- |
| Default login                    | "This video is only available for registered users. Use --cookies-from-browser or --cookies for..." | `common.py:1245`, any site       | **No**                 | Asks     |
| Instagram, followers only        | "This content is only available for registered users who follow this account. Use..."               | `instagram.py:502`               | **No**                 | Asks     |
| Instagram, empty response        | "Instagram sent an empty media response. Check if this post is accessible... use --cookies..."      | `instagram.py:521`               | **No**                 | Asks     |
| Instagram, anonymous limit       | "The webpage request was redirected to the login page. You have exceeded the rate-limit for accessing posts anonymously. Use..." | `instagram.py:508` | Yes, by accident       | Asks (D4) |
| Instagram at the floor           | "Requested content is not available, rate-limit reached or login required. Use..."                  | floor `instagram.py:459`         | Yes                    | Asks     |
| Instagram, lone image, no login  | "There is no video in this post"                                                                    | `instagram.py:530`               | No                     | No       |
| YouTube age gate                 | "Sign in to confirm your age. ... Use --cookies-from-browser..."                                    | Site text, hint from `:4071`     | Yes                    | Asks     |
| YouTube private                  | "Private video. Sign in if you've been granted access... Use..."                                    | Site text, hint from `:4071`     | Yes                    | Asks     |
| YouTube members-only             | "Join this channel to get access to members-only content..."                                        | Site text                        | Yes                    | Asks     |
| TikTok                           | "Fresh cookies (not necessarily logged in) are needed"                                              | `tiktok.py:1505`                 | **No**                 | Asks     |
| HTTP 401                         | "Unable to download webpage: HTTP Error 401: Unauthorized"                                          | Measured on loopback             | **No**                 | Asks     |
| YouTube rate limit               | "This content isn't available, try again later. The current session has been rate-limited..."       | `_video.py:4079`                 | **Yes, wrongly**       | No       |
| Other rate limits                | "You have exceeded the rate limit. Try again later"; "HTTP Error 429: Too Many Requests"            | `bilibili.py:738`, loopback      | No                     | No       |
| Geo block                        | "...geo restriction" plus "You might want to use a VPN or a proxy server..."                        | `YoutubeDL.py:1743`, every geo error | No                 | No       |
| HTTP 403                         | "HTTP Error 403: Forbidden"                                                                         | Measured on loopback             | No                     | No       |
| HTTP 404, unsupported URL, DRM   | As yt-dlp words them                                                                                | Measured                         | No                     | No       |
| Missing browser                  | `could not find chrome cookies database in "..."`                                                   | Measured on loopback             | No                     | No       |

403 doesn't ask because yt-dlp gives no login hint with it, and bot walls and geo blocks answer 403 too. A site where it means "log in" still gets the real reason and the late cookies step.

The strongest signal is one no per-site list needs: `raise_login_required` appends yt-dlp's own hint (`_login_hint`, `common.py:595`), so nearly every login error names `--cookies` or `--username`, at the floor too.

Run over every literal error message in the 2026.10.26 extractors, the proposed rule gives:

| Call                   | Asks | Doesn't | Not a literal |
| ---------------------- | ---- | ------- | ------------- |
| `raise_login_required` | 139  | 1       |               |
| `raise_geo_restricted` | 0    | 89      |               |
| `raise_no_formats`     | 0    | 80      | 12            |
| `ExtractorError`       | 70   | 523     | 144           |

The 70 are nearly all "Unable to log in" and "Login failed", which need `--username`, and download-media never passes it. The false positives left are harmless, such as "Model is in a private show": one extra prompt, and the reason is on screen. A false negative costs what it costs today, minus the "unknown".

Matching runs on the site's words only. Without that, `Unsupported URL: https://example.com/private/page` and `[generic] login: ... HTTP Error 404` both asked for cookies, measured.

### Wrong browser and wrong keyring

Measured with the real console script and fake profiles in a temp `HOME`:

| Case                                                   | Probe today                         | Download today                                              |
| ------------------------------------------------------ | ----------------------------------- | ----------------------------------------------------------- |
| Browser not installed, single mode                     | "failed to load cookies"            | `CookieLoadError` reaches the retry as a `DownloadError`: drops cookies, saves |
| Browser not installed, `-p` or a picker run            | Same                                | **Traceback, exit 1.** Under `ignoreerrors` it isn't a `DownloadError` |
| Browser with no cookie for the site (empty profile)    | No error from the browser           | The site answers as if logged out                           |
| Wrong keyring (`brave+basictext`, undecryptable row)   | No error from the browser           | "Extracted 0 cookies from brave (1 could not be decrypted)", then as above |

A wrong keyring never surfaces as an error, so to the wizard it's the "cookies given and still gated" case. The traceback is outside this item: see Out.

## Scope

### In

- `probe()` sees every extraction error and reports the real reason (D1).
- One rule for "cookies can help" (D2), which drops rate limits and geo blocks.
- `needs_cookies` works on single URLs, gated carousels and carousels where only some entries are gated.
- A failed entry stops reading as an image, and the wizard says why it failed (D3).
- `_media_kind` matches `_get_formats`.
- `b` at the retry cookies prompt, which becomes reachable here and raises an uncaught `_Back` (measured: `RAISED _Back`).

### Out

For the planner, all reproduced on `main`:

- **`-y -p --cookies <browser not installed>` ends in a traceback,** exit 1, and so does a picker run that picks one at the late step. `_download_with_cookie_fallback` only catches `DownloadError`, and under `ignoreerrors` yt-dlp raises `CookieLoadError` bare. This item doesn't make it easier to reach: a re-probe that fails on the browser leaves `playlist_mode` on `auto`, where the retry works. `fake_site` can't see it, since it never loads cookies.
- **`b`, Enter, `b` at the up-front prompt raises `_Back`** (`wizard.py:473-477`): a traceback before any network call.
- **A failure that can't recover still walks the manual flow:** six questions, then `Download failed`, on a rate limit, a geo block or a 404. It stays because the manual flow is what saves `rejects-cookies`, and telling the two apart is its own decision.
- **The running summary doesn't list cookies chosen before the walk.** Same as the up-front prompt today. The printed command does carry them.
- **After a missing browser, `_hint_for_download_error` prints the keyring hint,** which doesn't apply.

Handled by other rows: the exit code of a wizard refusal, which a lone image now reaches (`cancel-exits`); the English `→ try again with --cookies` hint and the hardcoded `(item #N)` and `[no soportado]` (`i18n-leftovers`); `-l` opening the wizard (`intent-flags`); `--playlist-items` under `-i` (`playlist-items-fix`); the up-front prompt itself (`wizard-ig-cookies`, see below); whether `-y` should continue past a broken item (`single-broken-item`).

## Files

| Action | File                            |
| ------ | ------------------------------- |
| modify | `src/download_media/probe.py`   |
| modify | `src/download_media/wizard.py`  |
| modify | `src/download_media/i18n.py`    |
| modify | `src/download_media/cli.py`     |
| modify | `tests/conftest.py`             |
| modify | `tests/test_probe.py`           |
| modify | `tests/test_wizard_flow.py`     |
| modify | `tests/test_e2e.py`             |
| modify | `tests/test_cli.py`             |

`cli.py` and `test_cli.py` change only under D2's recommended option. `downloader.py` doesn't change, and neither do flags, defaults or dependencies. Row 19 (`wizard-multi-url`) and row 27 (`audio-type-step`) also touch `wizard.py`, at other lines.

## Design

Written for the recommended option of each decision. The prototype produced every "after" result here.

### `probe.py`: seeing errors

Keep `ignoreerrors`, and add a subclass of `downloader._Ydl`, which already records what `report_error` receives, before any `ERROR:` prefix or color:

```python
class _ProbeYdl(_Ydl):
    """Hands back what has nothing to fetch, so ``errors`` holds real failures only.

    ``ignore_no_formats_error`` can't do this: extractors read it too, and turn a
    site's own "sign in" or "private" into a warning.
    """

    def process_video_result(self, info_dict: dict[str, Any], download: bool = True) -> Any:
        kind = _media_kind(info_dict)
        known = info_dict.get("playlist_index") or _has_thumbnail(info_dict)
        if kind == ContentKind.SINGLE_IMAGE or (kind is None and known):
            return info_dict
        return super().process_video_result(info_dict, download=download)
```

Confirmed in the source of both versions:

- **The hook exists with this signature** at 2026.10.26 (`YoutubeDL.py:2833`, called at `:1943`) and at 2024.10.07 (`:2720`, called at `:1841`). It has no leading underscore. If yt-dlp ever renames it, the override stops running, images come back as `None`, and the e2e tests fail at the gate.
- **It runs after extraction,** so anything an extractor raises has already been raised. It can't hide a site's error.
- **It decides on raw formats.** A DRM video still has its formats here, so it reads as video, goes to `super()`, and fails with the honest message. That's why this isn't done by setting `ignore_no_formats_error` around the call, which would read DRM as `image`.
- **Entries already carry `playlist_index` here,** since `process_ie_result` adds it first. That's what keeps every entry with nothing to fetch as `[image]`, as today, thumbnail or not.
- **A lone result with no thumbnail goes to `super()`,** so yt-dlp's own "No video formats found!" reaches the user. Calling that an image would be a guess.

A raw result can carry `thumbnail` without `thumbnails`, since yt-dlp hasn't normalized it yet, so the check reads both:

```python
def _has_thumbnail(info: dict[str, Any]) -> bool:
    return bool(info.get("thumbnails") or info.get("thumbnail"))
```

`_classify_single` uses it for its thumbnails branch. Without that, a measured `thumbnail`-only page read `Unknown`.

`probe()`:

- Build `_ProbeYdl` inside the `try`, keep a reference to `ydl.errors`, and read it afterwards.
- `except Exception as exc: errors = errors or [str(exc)]`. A missing browser reports its real cause through `report_error`, then raises `CookieLoadError("failed to load cookies")`, so the recorded error goes first. `test_probe_never_raises` still holds: the constructor is inside the `try`.
- `needs_cookies` is true if any error is an auth error. It looks at all of them, so a gated entry in a nested list counts too.
- `error_message` is the short form of the first auth error, or else of the first error.
- **Failed:** `data` isn't a dict, or `entries` is non-empty and all `None`. `failed=True`, with both fields set.
- **Partial:** at least one entry is a dict. `failed=False`, `PLAYLIST`, with both fields set.
- **A single result** that came back fine stays as it is.

`_classify_entry(None)` becomes `ProbeItem(idx, ContentKind.UNKNOWN, False, f"(item #{idx})")`. `None` no longer means an image. It's a failure, or a hole the extractor left, and neither has an error we can pin to a row. The picker prints `[?] [no soportado]`, and `ig_image_warning` stops firing on it.

`_media_kind`:

```python
formats = info.get("formats")
if formats is None:
    formats = [info] if info.get("url") else []
```

This is what `_get_formats` does (`YoutubeDL.py:3973`). Without it, `{url, formats: []}` reads as video, goes to `super()`, fails, and shows as `[?]` with a false warning. Measured: `test_items_yt_dlp_cannot_fetch_are_never_offered_and_never_saved` fails.

`_skip_items_without_media` keeps `or`, so the two now disagree on that one shape, on purpose: the filter passes it, and yt-dlp reports it as a failed item, which `test_items_yt_dlp_cannot_fetch...` pins as `partial_result(1, 4, 0, 3)`. Aligning them would change `-y -p` counts. So the row `{"url": "http://x/f.mp4", "formats": []}` leaves `test_probe_and_match_filter_agree_on_nothing_to_fetch` for a test of its own, which says they differ and why. Spec 17's `:280` pin holds unchanged.

### `probe.py`: the auth rule

```python
_LOGIN_HINTS = ("--cookies", "--username")  # yt-dlp's own hint, from raise_login_required.
_NOT_AUTH_ERRORS = (
    "rate-limit", "rate limit", "too many requests", "try again later",
    "use a vpn",  # yt-dlp adds this to every geo error.
)
_AUTH_ERROR_HINTS = (
    "sign in", "log in", "login", "logged in", "private", "registered users",
    "members-only", "premium", "http error 401",
)
```

`is_auth_error(message)`, public now:

1. Drop the `[site] id: ` prefix and any URL. Lowercase.
2. A login hint means yes. It outranks the next step, because a message with the hint is yt-dlp saying cookies are the way.
3. A `_NOT_AUTH_ERRORS` match means no.
4. Otherwise, any `_AUTH_ERROR_HINTS` match.

Dropped from today's list: `rate-limit`, `requested content is not available`, whose only real source is the floor's Instagram message, which carries the hint; and `this video is unavailable`, which cookies don't fix.

### `probe.py`: the reason

yt-dlp's text stays in English: it comes from 1800 sites, and translating it isn't possible. It does need cutting, for three reasons: it can run past 300 characters, unexpected errors end in a bug-report request, and its hints name yt-dlp's flags. `--cookies` means a cookie file there and a browser here, so showing it would mislead.

`_short_reason(message)`, used only for what's displayed:

1. Take the first line. Geo errors add a VPN line.
2. Drop the `[site] id: ` prefix.
3. Cut at `; please report`, `, please report` and ` (caused by`.
4. Drop every sentence from the first one naming a flag on, matching `(?:^|[\s`])--?[a-z]`. This catches `Use --cookies...`, `Use --list-formats...` and `` `-t sleep` ``.
5. `textwrap.shorten(text, 200, placeholder="...")`.

| yt-dlp's text                                                                                | Shown                                                                                                 |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `[Instagram] x: Instagram sent an empty media response. Check if... If it is not, then use --cookies-from-browser...` | Instagram sent an empty media response. Check if this post is accessible in your browser without being logged-in. |
| `[generic] s401: Unable to download webpage: HTTP Error 401: Unauthorized (caused by <HTTPError 401: Unauthorized>)` | Unable to download webpage: HTTP Error 401: Unauthorized                                              |
| `[fakesite] bug: Unable to extract video data; please report this issue on...`               | Unable to extract video data                                                                          |
| `[fakesite] i1: Requested format is not available. Use --list-formats...`                    | Requested format is not available.                                                                    |

`is_auth_error` always gets the raw message, because step 4 removes the hint it looks for.

### Messages

`probe_failed` doesn't change. One message changes and two are new:

| Key                   | en                                                                             | es                                                                                                 |
| --------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `auth_needed_retry`   | This content needs a login: {}                                                 | Este contenido requiere iniciar sesión: {}                                                         |
| `auth_still_needed`   | Still no access with {} cookies: check that you are logged in on that browser. | Sigue sin haber acceso con las cookies de {}: comprueba que hayas iniciado sesión en ese navegador. |
| `probe_partial`       | Some items could not be inspected: {}                                          | Algunos items no se pudieron inspeccionar: {}                                                      |

Today's `auth_needed_retry` says "Retrying with cookies", which is false if the user declines. A Spanish user reads, for example, `No se pudo inspeccionar la URL: Unable to download webpage: HTTP Error 429: Too Many Requests`, measured: the frame is translated and the site's words are not.

The `or "unknown"` at `wizard.py:508` stays for what's left: an extractor that returns nothing and reports no error.

### `wizard.py`

The re-probe logic at `:483-494` already does the right thing once `needs_cookies` is true. Three changes:

1. `:484` passes the reason: `warn(t("auth_needed_retry", probe_result.error_message))`.
2. `:485-488` adds `except _Back: pass`, with a comment. `step_cookies` offers `b`, and here there's nothing to go back to, since the URL is already probed. It acts as declining, and with no catch it's a traceback.
3. Phase 2.5:
   - Before the `no_supported_items` check: `if probe_result.error_message: warn(t("probe_partial", ...))`, so a refusal also says why.
   - In the failed branch, after `probe_failed`: `if probe_result.needs_cookies and opts.cookies_browser: warn(t("auth_still_needed", browser_of(opts.cookies_browser)))`.

There can't be a loop: the prompt needs `not opts.cookies_browser`, it's in straight-line code, and it runs at most once.

`_build_post_probe_plan` doesn't change. `cookies_asked` removes the late step after any prompt. Without one, as on a rate limit, the late step stays, because the user was never asked.

### `cli.py` (D2)

`_hint_for_download_error` (`:334-336`) replaces its three substrings with `is_auth_error(message)`. The `secretstorage` branch and the `not opts.cookies_browser` condition stay.

### Does the cookie retry keep working?

Yes: `_download_with_cookie_fallback`, `_Outcome.cookies_dropped` and `_print_equivalent` aren't touched, and they only read `opts`. Measured:

- `-i --cookies brave` on `rejects-cookies`: probe fails with "Failed to parse JSON", manual flow, cookies dropped, saved, exit 0, printed command has no `--cookies`.
- The existing retry tests pass unchanged, including `rejects-cookies-carousel`.

### `wizard-ig-cookies` (row 06)

Nothing here waits for it, and it needn't change. This item is what makes row 06 safe: Instagram's login messages all carry yt-dlp's hint, so the retry catches them with no up-front prompt.

Until row 06 lands, someone who declines up front on a gated post is asked twice, the second time with the reason. See D5.

## Cases

Only the wizard and `-i` call `probe()` (`wizard.py:482`, `:494`), checked again here. `-l` alone opens the wizard, so it goes through the probe too (`intent-flags`). `_show_preview` builds its own `YoutubeDL`.

"Manual flow" means `probe_failed`, `probe_falling_back`, then the steps. Prompts count every `input()`.

| Case                                                                  | Expected                                                                                                                                                                         | Prompts |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| wizard, gated single, gives a browser                                 | `auth_needed_retry`, re-probe, `Video`, saved, exit 0. No preview, no late step. Command has `--cookies`                                                                         | 6       |
| wizard, gated single, declines                                        | `auth_needed_retry`, manual flow, `Download failed`, exit 1, `try again with --cookies`. Not asked again                                                                         | 7       |
| wizard, gated single, `b` at the prompt                               | Same as declining                                                                                                                                                                | 7       |
| wizard, gated single, `c` or EOF at the prompt                        | `Cancelled.`, exit 0, no command                                                                                                                                                 | 2       |
| wizard, gated carousel, gives a browser                               | **Picker kept.** Enter saves the videos. Command has `--cookies` and `--playlist-items`                                                                                          | 7       |
| wizard, gated carousel, declines                                      | Manual flow, exit 1                                                                                                                                                              | 7       |
| cookie-hungry site, declines twice                                    | Up-front prompt, then `auth_needed_retry` and a second prompt, manual flow, exit 1. No third prompt                                                                              | 8       |
| cookie-hungry site, declines, then gives a browser                    | Saved, exit 0                                                                                                                                                                    | 7       |
| cookie-hungry site, browser up front                                  | Probe works first time, no second prompt, exit 0                                                                                                                                 | 6       |
| cookie-hungry site, public carousel, declines                         | One prompt only. Picker, as today                                                                                                                                                | 7       |
| browser given at the prompt, still gated                              | `probe_failed`, `auth_still_needed`, manual flow, retry without cookies, `Download failed`, exit 1. No loop                                                                      | 7       |
| `-i --cookies <browser>`, still gated                                 | Same, and no retry prompt. The late step stays, as nothing asked                                                                                                                 | 7       |
| wrong keyring                                                         | Same as still gated. The keyring hint comes from the download, as today                                                                                                          |         |
| browser not installed, picked at the prompt                           | `probe_failed` with `could not find chrome cookies database...`, no `auth_still_needed`, manual flow, retry without cookies, exit by what that gives                             |         |
| cookies rejected by the site (`rejects-cookies`)                      | Probe fails, manual flow, retry without cookies saves it, command drops `--cookies`                                                                                              | 7       |
| rate limit                                                            | Real reason, **no cookie prompt**, manual flow, exit 1. Late step stays                                                                                                          | 7       |
| geo block, 403, 404, 429, 451, unsupported URL, DRM                   | Same: real reason, no prompt                                                                                                                                                     | 7       |
| 401, age gate, private, members-only, paid post, default login text   | As a gated single                                                                                                                                                                |         |
| Instagram's anonymous limit                                           | As a gated single (D4)                                                                                                                                                           |         |
| extractor bug                                                         | Reason without the bug-report tail. Manual flow, exit 1                                                                                                                          | 7       |
| carousel, one entry gated, declines                                   | `auth_needed_retry`, `probe_partial`, picker with `[video]`, `[image]`, `[?]`. Enter saves the video, exit 0, command has `--playlist-items 1`                                   | 7       |
| carousel, one entry gated, gives a browser                            | Picker shows it as `[video]`. Both videos saved, exit 0                                                                                                                          | 7       |
| carousel, one entry rate-limited                                      | `probe_partial`, no prompt, picker, Enter saves the rest                                                                                                                         | 7       |
| carousel, one entry broken, `all`                                     | `partial_result(1, 3, 1, 1)`, exit 0, command has `-p`, as `-y -p`                                                                                                               | 7       |
| carousel, every entry gated, declines                                 | Failed, not "no items": `auth_needed_retry`, manual flow, exit 1                                                                                                                 | 7       |
| carousel with an empty entry and no error                             | `[?]`, no warning. Today: `[image]`                                                                                                                                              | 7       |
| public carousel, `only-images`, `empty`                               | Unchanged                                                                                                                                                                        |         |
| lone image with a thumbnail, or only image formats                    | `image_only_skipped`, exit 0. **Today: manual flow, then exit 1**                                                                                                                | 1       |
| lone result with nothing, no thumbnail (`fake.test/image`)            | `probe_failed("No video formats found!")`, manual flow, exit 1. Today: same, with "unknown"                                                                                      | 7       |
| `-i -a`, gated, gives a browser                                       | `.mp3` saved, command has `-a --cookies`                                                                                                                                         | 5       |
| `-i -P`, gated carousel, gives a browser                              | Picker, as on any multi-entry URL (spec 17)                                                                                                                                      | 7       |
| `-l` alone, gated, gives a browser                                    | Asks, re-probes, then lists formats                                                                                                                                              | 6       |
| direct with preview, `-y`, `-p`, `-P`, `-y -a`, `-y -l`               | Same exit code and same files on all 160 runs compared, 20 pages by 8 modes. Under D2, 24 of them gain the `--cookies` hint line. The other 6 differences were progress timing and a random IP |  |
| with / without cookies                                                | Above. Cookies given up front skip the prompt                                                                                                                                    |         |
| rerun                                                                 | Printed command on a gated single or carousel replays with the same files and no prompt                                                                                          |         |
| printed command, summary                                              | The command carries the chosen browser, or drops it if the retry did. The summary doesn't list cookies chosen before the walk (see Out)                                          |         |
| history                                                               | One row per run that saved something; none when the run fails or is refused                                                                                                     |         |

## Acceptance criteria

**[fix]** needs a test that fails without the fix, shown by reverting it (`constraints.md`, Verification 5). **[pin]** protects behavior that must not change, so it passes before and after.

Tests that depend on yt-dlp use the real thing:

- **Add pages to `FakeSiteIE` built with yt-dlp's own raisers,** so the text is yt-dlp's and not a copy.
- **After `raise_no_formats` or a `metadata_available=True` raiser, the page must `return` metadata with `formats: []` and thumbnails,** as real extractors do. If it falls through instead, the page lands on the fixture's final "This post is private", and the test can't catch `ignore_no_formats_error`. That happened during this spec.
- **`fake_site`:** drop the `probe_module.YoutubeDL` patch, and patch `probe_module._ProbeYdl` with `class FakeProbeYdl(FakeYdl, probe_module._ProbeYdl)`. `test_probe_never_raises` patches `_ProbeYdl` too. Measured working.
- **A cookie-hungry site on `fake_site`:** `https://fake.test/needs-cookies?from=instagram.com`. `detect_site` searches the whole URL, and `_VALID_URL` isn't anchored at the end.
- **`_QuietHandler`** answers `/status/NNN` with that code, for the `generic` fixture.
- **`fake_site` never loads cookies.** A missing browser needs `generic` with the `home` fixture, which keeps it off the real profile, and `isolated_env`.
- The `answers` fixture fails on an unscripted prompt, so scripted walks also prove the prompt count.

| Page                | Built with                                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------------------------- |
| `login-hint`        | `raise_login_required()`                                                                                    |
| `gated-carousel`    | No cookies: `raise_login_required("This content is only available for registered users who follow this account")`. Cookies: image, video, video |
| `anonymous-limit`   | `raise_login_required` with `instagram.py:508`'s text                                                       |
| `age-gate`          | `raise_no_formats(f"Sign in to confirm your age. {self._login_hint(method='cookies')}", expected=True)`, then metadata |
| `rate-limited`      | `raise_no_formats` with `_video.py:4079`'s text, `expected=True`, then metadata                             |
| `geo`               | `raise_geo_restricted(countries=["US"])`                                                                    |
| `geo-metadata`      | `raise_geo_restricted("...", ["US"], metadata_available=True)`, then metadata                               |
| `paid-post`         | `raise_login_required("This post requires a subscription", metadata_available=True)`, then metadata         |
| `no-video-post`     | `raise_no_formats("There is no video in this post", expected=True)`, then metadata                          |
| `drm`               | One format with `has_drm: True`, and thumbnails                                                             |
| `extractor-bug`     | `ExtractorError("Unable to extract video data")`, not `expected`                                            |
| `partial-gated`     | Video, image, `url_result` to `needs-cookies`                                                               |
| `partial-rate`      | Video, `url_result` to `rate-limited`                                                                       |
| `partial-broken`    | Video, image, `url_result` to `extractor-bug`                                                               |
| `all-gated`         | Two `url_result`s to gated pages                                                                            |
| `hole`              | Video, `None`                                                                                               |
| `thumbnail-image`   | `_image` plus a `thumbnail` key                                                                             |

Eight existing tests pin what this item changes. They're the only ones that failed on the prototype, on both yt-dlp versions, out of 795 run. `tests/test_packaging.py` wasn't part of that run, because it needs files outside the scratch copy, and nothing here touches what it reads:

| Test                                                                            | Becomes                                                                                  |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `test_wizard_browser_choice_repeats`                                            | Script `"", "2", "", "", "", ""`: the prompt now comes first, with no preview            |
| `test_wizard_on_a_lone_image_still_fails_the_probe`                             | `t("probe_failed", "No video formats found!")`                                           |
| `test_wizard_on_a_lone_format_without_streams_still_fails_the_probe`, three ids | `image_only_skipped`, exit 0, one prompt. Rename it                                      |
| `test_is_auth_error[Rate-limit reached-True]`                                   | `False`                                                                                  |
| `test_classify_entry_none_is_image`                                             | `UNKNOWN`, unsupported, no "image" in the title. Rename it                               |
| `test_probe_and_match_filter_agree_on_nothing_to_fetch[extra10]`                | Row moves to its own test                                                                |

The three `Probe` mocks in `test_wizard_flow.py:170-192` pass but print `None` as the reason: give them an `error_message`. `probe()` can't produce that pair.

**Gate**

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] No test that isn't in the table above needed changing

**Seeing the error, `fake_site`**

- [ ] **[fix]** `probe()` on `needs-cookies`: `failed`, `needs_cookies`, `error_message == "Login required"`. Replaces the mock as the proof for `test_probe_never_raises`
- [ ] **[fix]** Parametrized over `age-gate`, `paid-post`, `rate-limited`, `geo-metadata`, `drm`, `no-video-post`: `failed`, `content_kind` isn't image. **Fails if `ignore_no_formats_error` is ever set in the probe**
- [ ] **[fix]** Parametrized over `age-gate`, `paid-post`, `login-hint`, `anonymous-limit`: `needs_cookies`
- [ ] **[fix]** Parametrized over `rate-limited`, `geo`, `geo-metadata`, `drm`, `no-video-post`, `extractor-bug`: not `needs_cookies`
- [ ] **[fix]** `extractor-bug`: `"please report" not in error_message`
- [ ] **[pin]** `carousel`, `ig-carousel`, `only-images`, `url-only-carousel`, `empty`: same kinds and items as measured on `main`, `error_message` empty, not `needs_cookies`

**Flows, `fake_site`, each with the prompts in Cases**

- [ ] **[fix]** Wizard on `needs-cookies`, browser: exit 0, `Clip.mp4`, `--cookies` in the printed command; replay saves the same file with no prompt
- [ ] **[fix]** Wizard on `needs-cookies`, declines: exit 1, `t("auth_needed_retry", "Login required")` and `t("probe_failed", "Login required")` in the output, `"unknown"` not in it
- [ ] **[fix]** Wizard on `needs-cookies`, `b`: as declining, no exception
- [ ] **[fix]** Wizard on `needs-cookies`, `c`, and `EOFError`: exit 0, `t("equiv_cmd")` not in the output
- [ ] **[fix]** Wizard on `gated-carousel`, browser: `[video]` in the output, 2 files named `NN - Clip [id].mp4`, `--cookies` and `--playlist-items` in the printed command; replay saves the same files
- [ ] **[fix]** Cookie-hungry `needs-cookies`, declines twice: exit 1, 8 prompts
- [ ] **[fix]** Cookie-hungry `needs-cookies`, declines then browser: exit 0, `Clip.mp4`
- [ ] **[pin]** Cookie-hungry `needs-cookies`, browser up front: 6 prompts, `t("auth_needed_retry", ...)` not in the output
- [ ] **[pin]** Cookie-hungry `carousel`, declines: 7 prompts
- [ ] **[fix]** Browser at the prompt on `private`: exit 1, `t("auth_still_needed", BROWSERS[0])` in the output, 7 prompts, so no loop
- [ ] **[fix]** `-i --cookies firefox` on `private`: `t("auth_still_needed", "firefox")` in the output, `t("auth_needed_retry", ...)` not in it
- [ ] **[fix]** `rate-limited` and `geo`: real reason in the output, `t("ask_cookies")` printed exactly once, by the late step
- [ ] **[fix]** `partial-gated`, declines: `t("probe_partial", "Login required")`, `[?]` in the output, 1 file, exit 0
- [ ] **[fix]** `partial-gated`, browser: 2 files, no `[?]`
- [ ] **[fix]** `partial-rate`: `t("probe_partial", ...)`, one `t("ask_cookies")`, from the late step, 1 file
- [ ] **[fix]** `partial-broken`, `all`: `t("partial_result", 1, 3, 1, 1)`, exit 0, and `-y -p` saves the same file
- [ ] **[fix]** `all-gated`, declines: `t("no_supported_items")` not in the output, exit 1
- [ ] **[fix]** `hole`: `[?]` for the empty entry, no `t("probe_partial", ...)`, `t("ig_image_warning")` not in the output
- [ ] **[fix]** `ig-image`, `image-format`, `storyboard-only`, `thumbnail-image`: `t("image_only_skipped")`, exit 0, 1 prompt, no files
- [ ] **[fix]** `fake.test/image`: `t("probe_failed", "No video formats found!")`, exit 1
- [ ] **[fix]** `undownloadable-carousel`: 3 `[image]`, 1 `[video]`, no `t("probe_partial", ...)`. Fails with `or` in `_media_kind`, measured
- [ ] **[pin]** `-i --cookies brave` on `rejects-cookies`: saved, exit 0, no `--cookies` in the printed command
- [ ] **[pin]** `-i -a` on `needs-cookies`, browser: `.mp3`
- [ ] **[pin]** Parametrized over the new pages and `needs-cookies`: `-y`, `-y -p`, `-y -P`, `-y -a` and `-y -l` give the exit code and file names measured on `main`

**Real generic extractor, loopback**

- [ ] **[fix]** Wizard on `/status/401`, declines: `t("auth_needed_retry", "Unable to download webpage: HTTP Error 401: Unauthorized")`, exit 1
- [ ] **[fix]** Parametrized over 403, 404, 429: real reason in `t("probe_failed", ...)`, no `t("auth_needed_retry", ...)`
- [ ] **[fix]** `-i --cookies chrome` on a real page, with `HOME` in a temp folder: `could not find chrome cookies database` in the output, `failed to load cookies` not in it, saved through the retry

**Unit, `tests/test_probe.py`**

- [ ] **[fix]** `is_auth_error` over every row of the table in "What counts as an auth failure", plus `Unsupported URL: https://example.com/private/page` and `[generic] login: ... HTTP Error 404: Not Found`, both `False`
- [ ] **[fix]** `_short_reason` over the table in Design, a geo error with its VPN line, and a 400-character message: at most 200 characters
- [ ] **[fix]** `_classify_single({"url": "http://x/f.mp4", "formats": []})` isn't video; `_media_kind` is `None`, while the filter passes it
- [ ] **[fix]** `_classify_single({"formats": [], "thumbnail": "http://x/i.jpg"})` is image
- [ ] **[pin]** Spec 17's `:280` and `:281` pins still pass

**Unit, `tests/test_cli.py`, if D2 is approved**

- [ ] **[fix]** `_hint` on yt-dlp's default login text, and on `anonymous-limit`'s: suggests `--cookies <browser>`
- [ ] **[fix]** `_hint` on `HTTP Error 429: Too Many Requests` and a geo error: empty

**Commits:** `fix(probe): see extraction errors so the cookie retry fires`, with the wizard and `i18n.py` changes, since neither works alone. `_media_kind` belongs with it, as the design depends on it. Under D2, `fix(cli): share the auth error rule with the probe` on its own.

**SemVer:** minor by `AGENTS.md`'s rule, because it adds new messages (`auth_still_needed`, `probe_partial`). Not breaking: no flag, default or on-disk path changes. The next release is already 0.3.0, so this moves nothing, and no version is bumped here.

One change belongs in the release notes: in the wizard, a lone image now stops at once with exit 0, where it used to walk six questions and exit 1. Its exit code is already on `cancel-exits`' list.

## Live check

Needed. Only a real site can confirm these, and nothing here has run against one:

- **YouTube's wording.** The source only adds the hint. "Sign in to confirm your age" and the rest come from YouTube's response.
- **What Instagram answers** on a post that needs a login, today.
- **Whether a lone Instagram image, logged in, arrives as `formats: []` plus thumbnails.** The source says so (`instagram.py:441`, which returns `_extract_product` directly), so `image_only_skipped` should fire there and not anonymously.

A rate limit can't be provoked on demand, so that text stays source-derived.

`.ai/live.env` holds `DM_LIVE_IG_CAROUSEL` and `DM_LIVE_BROWSER`. The human adds the rest, and a step whose variable is missing is skipped and reported as skipped:

| Variable            | What it is                                                                 |
| ------------------- | -------------------------------------------------------------------------- |
| `DM_LIVE_IG_GATED`  | Instagram post that needs a login, visible to the `DM_LIVE_BROWSER` account |
| `DM_LIVE_AGE_GATED` | Age-restricted YouTube video                                               |
| `DM_LIVE_IG_IMAGE`  | Public Instagram post with a single image                                  |

Run them alone: Instagram rate-limits by IP. Cookies go through `download-media` only.

```bash
# 1. Regression: a public carousel keeps its picker, and matches -y -p.
printf '\n1\n\n\n\n\n\n' | .venv/bin/download-media -i -o tmp/probe-auth "$DM_LIVE_IG_CAROUSEL"
.venv/bin/download-media -y -p -o tmp/probe-auth-y "$DM_LIVE_IG_CAROUSEL"

# 2. Age gate, declining: URL, cookies 1, then the manual flow.
printf '\n1\n\n\n\n\n\n\n' | .venv/bin/download-media -i -o tmp/probe-auth-age "$DM_LIVE_AGE_GATED"

# 3. Lone image: anonymous, then logged in.
printf '\n1\n\n\n\n\n\n\n\n' | .venv/bin/download-media -i -o tmp/probe-auth-img "$DM_LIVE_IG_IMAGE"
printf '\n' | .venv/bin/download-media -i --cookies "$DM_LIVE_BROWSER" -o tmp/probe-auth-img "$DM_LIVE_IG_IMAGE"
```

For the gated post, run it by hand, since the browser's menu number depends on `DM_LIVE_BROWSER`: `.venv/bin/download-media -i -o tmp/probe-auth-gated "$DM_LIVE_IG_GATED"`, decline up front, then pick the browser at the second prompt.

What must hold:

1. The file count in `tmp/probe-auth` equals the `[video]` rows printed, and matches `tmp/probe-auth-y`. No `Some items could not be inspected`.
2. `This content needs a login:` with YouTube's reason, no `--cookies-from-browser` in that line, exit 1, no files.
3. Anonymous: a reason, no "unknown", no files. Logged in: `This content is a standalone image`, exit 0. If either differs, record what was printed: that corrects this spec, and isn't by itself a failure.
4. Gated post: `This content needs a login:` appears after the up-front decline, the content is detected, and what's on disk matches what's reported.

## Open decisions

Resolved on approval: the human chose the recommended option in D1 (D, override `process_video_result`), D2 (share), D3 (keep the picker), D4 (ask for cookies) and D5 (accept).

### D1: how the probe sees errors

The options and measurements are under "How the options compare".

**Recommendation: D.** It's the only one that sees every gated page and also keeps images and failures apart.

Note that this moves away from the premise in the plan's notes, that dropping `ignoreerrors` plus `ignore_no_formats_error` is the way: A and C turn seven gated shapes into "standalone image". The cost of D is one override of a yt-dlp method. If you'd rather not subclass that far, B is the fallback: no override, but a failed entry keeps reading `[image]`, the `No video formats found!` noise has to be filtered out by text, and `image_only_skipped` stays unreachable.

### D2: one definition of "cookies can help", or two

| Option                        | Effect                                                                                                                                                                             |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Share it (recommended)** | `cli.py` uses `is_auth_error`. Direct modes keep files and exit codes, and gain one line, `→ try again with --cookies`, on login errors its three substrings miss: 24 of 160 runs. None lost it |
| B. Keep two                   | Direct modes don't change by a single byte. But `-y` on a default login error stays silent while the wizard asks, and the lists can drift apart                                     |

**Recommendation: A.** It's the one direct-mode change in this item, and it's why this is your call: the brief says direct modes don't change. Without it, `-y` users keep seeing yt-dlp's `--cookies` advice, which means a cookie file, with nothing saying that here it takes a browser.

### D3: a carousel where only some entries fail

| Option                                     | Effect                                                                                                              |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| **A. Keep the picker (recommended)**       | Failed entries show `[?]`, one line says why, the rest can be saved. Matches `-p`, which continues past a bad item   |
| B. A bad entry fails the probe             | One broken entry out of twenty costs the user the picker, and the wizard saves less than `-y -p`                     |

**Recommendation: A.** If any failed entry is an auth failure, the wizard also offers cookies, and declining still leaves the picker.

### D4: Instagram's anonymous rate limit

The brief says rate limits shouldn't lead to a cookie prompt. This one does, and it's the only exception: "You have exceeded the rate-limit for accessing posts anonymously". It comes from `raise_login_required`, it carries yt-dlp's hint, and logging in is the way past it.

**Recommendation: ask,** which is what the rule does with no special case. The alternative is letting `_NOT_AUTH_ERRORS` outrank the hint. Then this message gets no prompt, and neither does the floor's "rate-limit reached or login required".

### D5: two prompts until row 06

Someone who declines up front on a gated Instagram post is asked again, now with the reason. That's what the row asks for, and it's a little repetitive.

**Recommendation: accept it, and let row 06 remove the first prompt.** Row 06 is `P3`; this makes it worth moving up. The alternative is folding row 06 in here, which makes this item two changes.
