# 67 — Say so when the browser's cookies load but none of them can be used

Status: draft
Item: `fix/cookies-undecrypted`

> **For the 0.4.0 review list:** this item changes published behaviour. See SemVer. All of it moves a silent run onto the path specs 26, 45 and 50 already built: notice, no cookies, closing line. No exit code changes, and no page that saves today stops saving.

## Problem

When yt-dlp loads a Chromium browser's cookies without an error but none of them decrypts, the tool says nothing. The run ends in `Done.`, the site gets no cookie or an empty one, and the printed command keeps `--cookies`.

Reproduced on `main` at `c6e306c` (no `src/` change since the planner's `ae1fded`). yt-dlp 2026.10.26, ffmpeg n9.0.2, Python 3.14.9. The real console script (`.venv/bin/download-media`) ran against a scratch `http.server` on `127.0.0.1` that logs each request's `Cookie` header. It ran under `env -i`, with `PATH` holding only links to ffmpeg and ffprobe plus a fake `kwallet-query`. Each run had its own temp folder for `HOME` and the four `XDG_*_HOME`, plus `LANG=en_US.UTF-8` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/bus`. The browser is a fake Chrome profile in that throwaway config folder (`google-chrome/Default/Cookies`, meta version 20). No real cookie database or keyring was read, and the real `kwallet-query` and `dbus-send` weren't on `PATH`.

**1. The row's case:** one `v11` cookie, `chrome+basictext`:

```bash
download-media -y --cookies chrome+basictext -o <abs dir> http://127.0.0.1:18967/one.html
# Extracting cookies from chrome
# Downloading...
# Extracted 0 cookies from chrome (1 could not be decrypted)
# Done.
#   download-media -y -o <abs dir> --cookies chrome+basictext http://127.0.0.1:18967/one.html
# exit 0. On disk: one (1).mp4. The server saw no Cookie header.
```

**2. A keyring that answers with the wrong key:** one `v11` cookie `s=token`, encrypted under the password `realpass`. A fake `kwallet-query` answers `wrongpass`, exit 0, so yt-dlp logs no error:

```bash
download-media -y --cookies chrome+kwallet6 -o <abs dir> http://127.0.0.1:18967/one.html
# Extracted 1 cookies from chrome
# Done.
#   download-media -y -o <abs dir> --cookies chrome+kwallet6 http://127.0.0.1:18967/one.html
# exit 0. The server saw: Cookie: s=
```

- **What happened:** nothing the tool writes mentions cookies. In run 2, yt-dlp's own count says one cookie was extracted, and the site gets an empty one.
- **What should happen:** `Could not read the chrome cookies (none of them could be decrypted). Going on without cookies.`, then the keyring hint. The run goes on with no cookie sent, and `Saved without chrome cookies: …` follows `Done.`. The printed command drops `--cookies` (spec 26 D6), as it does after every other cookie failure.

## Findings

### Every shape, on `main` (`c6e306c`)

| Run | Profile, keyring | yt-dlp's line | The tool | Server saw |
| --- | ---------------- | ------------- | -------- | ---------- |
| R1 `-y` | one `v11`, `+basictext` | `Extracted 0 cookies (1 could not be decrypted)` | `Done.`, keeps `--cookies` | no `Cookie` |
| R2 `-y -p`, `two.html` | same | same | `Done.`, keeps `--cookies`, 2 files | no `Cookie` |
| R3 preview, Enter | same | same, at the download | `Preview`, title, `Done.`, keeps `--cookies` | no `Cookie` |
| R4 `-l` | same | same, plus `WARNING: cannot decrypt v11 cookies: no key found` (`-l` sets no `no_warnings`, row 66) | the format list | no `Cookie` |
| R5 wizard `-i`, `two.html` | same | silent at the probe | picker, the late cookies step defaults to chrome, `Done.`, keeps `--cookies` | no `Cookie` |
| R12 `-y`, bare `chrome`, no desktop set | one `v11` | same as R1: yt-dlp picks `basictext` for an unknown desktop (`cookies.py:865-868`) | as R1 | no `Cookie` |
| R6 `-y` | `s=token` under `realpass`, keyring answers `wrongpass` | `Extracted 1 cookies` | `Done.`, keeps `--cookies` | `Cookie: s=` |
| R7 `-y` | 300-char value, same keys | `Extracted 0 cookies (1 could not be decrypted)` | as R1 | no `Cookie` |
| R8 `-y` | `s=token`, `kwallet-query` prints `Failed to read…`, exit 0 (yt-dlp then uses an empty password, `:939-949`) | `Extracted 1 cookies` | as R6 | `Cookie: s=` |
| R9 `-y` | `dm=ok` as `v10` plus one `v11`, `+basictext` | `Extracted 1 cookies (1 could not be decrypted)` | `Done.`, keeps `--cookies` | `Cookie: dm=ok` |
| R10 `-y --cookies chrome` | a database with no cookies | `Extracted 0 cookies from chrome` | `Done.`, keeps `--cookies` | no `Cookie` |
| R15 `-y --cookies firefox` | an empty `cookies.sqlite` | `Extracted 0 cookies from firefox` | `Done.`, keeps `--cookies` | no `Cookie` |
| R11 control | `s=token` and a 300-char `t`, keyring answers `realpass` | `Extracted 2 cookies` | `Done.` | both cookies |
| R13 `-y`, a page that 404s | one `v11`, `+basictext` | — | `Failed with chrome+basictext cookies. Retrying without cookies...` (false: none was sent), two attempts, `Download failed`, keeps `--cookies`, exit 1 | 4 requests, no `Cookie` |

Exit 0 and every file saved wherever the page exists. R14 (`-y -p` on the 404) is the same as R13.

### What the tool can observe

It may not read the database or the keyring (`constraints.md`, Privacy 1). What yt-dlp's loader hands back, measured through a scratch runner that wraps the instance's `report_warning`, `report_error` and `to_screen` around `ydl.cookiejar` (same env, same fake profiles):

| Shape | Warnings yt-dlp reports during the load | Info line | The jar (name, value length) |
| ----- | --------------------------------------- | --------- | ---------------------------- |
| R1, `+basictext` on `v11` | `cannot decrypt v11 cookies: no key found` | `Extracted 0 … (1 could not be decrypted)` | empty |
| R6, wrong key, short value | only `exception while obtaining NetworkWallet: … 'dbus-send'` (no `dbus-send` on `PATH`) | `Extracted 1 cookies` | `s`, 0 |
| R7, wrong key, long value | NetworkWallet, then `failed to decrypt cookie (AES-CBC) … Possibly the key is wrong?` | `Extracted 0 … (1 could not be decrypted)` | empty |
| Wrong key, both values | NetworkWallet, then the AES-CBC warning | `Extracted 1 … (1 could not be decrypted)` | `s`, 0 |
| R8, empty password | NetworkWallet only | `Extracted 1 cookies` | `s`, 0 |
| R9, mixed | `cannot decrypt v11 cookies: no key found` | `Extracted 1 … (1 could not be decrypted)` | `dm`, 2 |
| R10, no cookies | none | `Extracted 0 cookies` | empty |
| R11, right key | NetworkWallet only | `Extracted 2 cookies` | `s`, 5; `t`, 300 |

What follows:

- **yt-dlp's count settles nothing.** R6 says `Extracted 1 cookies` with no failure for a useless jar. R9 says `(1 could not be decrypted)` for a jar that works. The count is neither needed nor enough, which confirms spec 45's Findings.
- **The warnings are noisy.** The right-key control (R11) has a warning too, from the NetworkWallet lookup. The decryption warnings are `only_once` texts that vary by cause. Matching them would tie the rule to yt-dlp's wording, and they're absent anyway when a wrong key turns every value empty (R6, R8).
- **The jar is the one honest signal.** It's yt-dlp's return value, not a text. A jar holding no cookie with a value can't log anyone in, whatever the cause. That's R1, R6, R7, R8, R10 and the both-values shape, and none of the working jars (R9, R11).
- **Why a wrong key gives empty values:** `_decrypt_aes_cbc_multi` (`cookies.py:1044-1054`) returns whatever decodes as UTF-8, and `unpad_pkcs7` (`aes.py:33-34`) trims by the last byte without checking the padding. A simulation with yt-dlp's own functions (made-up values, 4000 per row, both meta versions) shows how a wrong key decodes: cookies of 5 to 100 characters come back empty in 80–99% of cases, `None` in the rest, and as a non-empty garbage value in 0.3–0.8%. At 300 characters, 99% come back `None`. So a wrong key on a small profile leaves no usable value, which the rule catches. On a real profile with hundreds of cookies, a few garbage values that decode are likely (about 33% at 100 short cookies, 87% at 500, computed from those rates, not measured on a real profile), and the rule can't tell those from real ones. That part is Out.
- **`warned` tells an empty database from a failed one.** In a Chromium profile, the keyring is asked only when a `v11` cookie is decrypted (`_v11_key`, a `cached_property`, `:448-451`), and every `None` from `LinuxChromeCookieDecryptor.decrypt` reports a warning (`:482`, `:489`, `:1053`). So an empty jar with any warning means some cookie failed. An empty jar with none means the database holds no cookies (R10). The only other warning on that path is `Cannot extract cookies … without sqlite3 support` (`:298`). It would read as "none decrypted", and Arch's Python ships sqlite3.

### yt-dlp behaviour relied on

Confirmed in the 2026.10.26 source, and read at the 2024.10.07 floor on GitHub (same code, not run):

1. yt-dlp's cookie logger calls `ydl.report_warning(message, once)` on the instance (`YDLLogger.warning`, `cookies.py:53-55`, `_YDLLogger.warning`, `utils/_utils.py:5704-5706`). That holds with `no_warnings` and with a `logger` param (the probe's `_SilentLogger`). The `only_once` filtering happens later, in `_write_string`, so the wrapper sees every call. The same mechanism as spec 45's `report_error`.
2. `YoutubeDL.cookiejar` is the `cached_property` that `load_browser_cookies` already reads before any request (`YoutubeDL.py:4218-4227`). Every request reuses that jar.
3. `yt_dlp.cookies.CHROMIUM_BASED_BROWSERS` exists in both versions (`cookies.py:49`, floor `:50`). Firefox and Safari cookies aren't encrypted.

## Goal

When the browser's cookies load but none of them carries a value, the user is told, no cookie is sent, and `Done.` is followed by the closing line, in every mode, as specs 26, 45 and 50 do for cookies that can't be loaded.

## Scope

### In

- `load_browser_cookies` raises `CookiesUnavailable` after a clean load whose jar holds no cookie with a value (D1). For a Chromium browser that's a cookie failure, with a new reason, `none of them could be decrypted`. An empty jar with nothing reported is `missing`, in every browser (D2).
- The keyring hint follows that notice even when a keyring was named (D3).
- Every door already handles `CookiesUnavailable`: the download and its fallback, the preview, `-l`, and the wizard's probe. None changes (D5).

### Out

- **A wrong key that leaves at least one garbage value that decodes,** likely on a large real profile (Findings). The tool can't tell it from a working jar without reading the database. A ratio rule ("most values empty") would be a guess about real profiles that the tool may not measure (D1 option D). The fix belongs upstream. If `unpad_pkcs7` checked the padding, a wrong key would come back `None` almost every time, and this item's empty-jar rule would catch it. For row 64 (`upstream-reports`).
- **A partial decryption that leaves usable cookies** (R9: `+basictext` on a profile with `v10` and `v11` cookies). The jar works, and the site gets `dm=ok`. Saying "1 of N cookies couldn't be decrypted" without dropping anything would need a new message and a count only yt-dlp's text gives. For the planner, as a possible row.
- **A Firefox or Safari jar whose cookies all have empty values:** they aren't encrypted, so empty values are the site's own. Kept as today.
- **A jar that works but holds nothing for the target site** (a profile that isn't logged in): spec 18's "still gated" path.
- **yt-dlp's own lines** (`Extracted 0 cookies from chrome (1 could not be decrypted)`, and `Extracted 1 cookies` right above the notice in R6). They stay, as in specs 45 and 50.
- **Row 66** (`-l` printing yt-dlp's `WARNING:` lines, the reason's wording elsewhere), **row 02** (the English `→ try again with --cookies` hint after a cookieless failure), **spec 50 D2** (no closing line after the wizard's probe drops the browser; this item adds one more cause to the walks it describes).

## Files

| Action | File | What |
| ------ | ---- | ---- |
| modify | `src/download_media/downloader.py` | `load_browser_cookies` only: the `from .cookies import …` line (`:34` on `main`) and the function (`:286-317`) |
| modify | `src/download_media/cookies.py` | `CookieFailure` gains a field (`:103-108`); a new jar check near `logged_failure` (`:140-142`); `report_failure`'s hint condition (`:160`); the `yt_dlp.cookies` import (`:19`) |
| modify | `src/download_media/i18n.py` | one key in each block, after `cookies_unreadable` (`:77`, `:177`) |
| modify | `tests/test_e2e.py` | new fixtures next to `unreadable_chrome` (`:6370`) and `empty_keyring` (`:6817`); new tests; the flip at `:7475` |
| modify | `tests/test_cookies.py` | the jar check and the hint |
| modify | `tests/test_probe.py` | one test |
| modify | `tests/test_downloader.py` | `test_loading_cookies_leaves_report_error_alone` (`:455-477`) |

No change to `cli.py`, `wizard.py`, `probe.py`, `equivalent.py`, `README.md` or `AGENTS.md`. `README.md`'s cookie sentence (line 15) stays true.

**Collisions.** `fix/container-choice` (spec 30) and `fix/m4a-cover` (spec 41) merge first. Neither touches `cookies.py`. In `downloader.py`, spec 30 (branch at `a2dbda3`) moves `load_browser_cookies` to `:320-351` and the import to `:41`, and changes neither. Spec 41 edits `download()`'s step order (`:667-669` on `main`, `:783-785` after spec 30). This item touches neither region. Spec 30 adds `not_converted` to `i18n.py` at `:64` and `:164`, away from `:77` and `:177`. Expect no textual conflict, only line drift.

## Design

Written for the recommended option of each decision. A prototype of it, on a scratch `git archive` copy of `src/` (31 changed lines across the three files, ruff and `mypy --strict` clean), gave every "After" in Cases. `src/` itself wasn't touched.

### `downloader.py`: `load_browser_cookies`

Next to spec 45's `note`, wrap the instance's `report_warning` the same way, and remove it in the same `finally`:

```python
warned: list[str] = []
report_warning = ydl.report_warning

def heed(message: Any, *args: Any, **kwargs: Any) -> None:
    warned.append(str(message))
    report_warning(message, *args, **kwargs)

ydl.report_error = note
ydl.report_warning = heed
try:
    jar = ydl.cookiejar
except (CookieLoadError, DownloadError) as exc:
    ...  # As today.
finally:
    del ydl.report_error
    del ydl.report_warning
if logged:
    raise CookiesUnavailable(spec, logged_failure(logged[0]))  # Spec 45's, first.
failure = jar_failure(spec, len(jar), any(cookie.value for cookie in jar), bool(warned))
if failure:
    raise CookiesUnavailable(spec, failure)
```

The logged error keeps priority, so spec 45's notices and reasons don't change. `any(...)` stops at the first cookie with a value, so a working profile costs one step. `fake_site` is unaffected: `FakeYdl` pops `cookiesfrombrowser`, so the function returns early.

### `cookies.py`

- `CookieFailure` gains `undecrypted: bool = False`. The default keeps every existing construction as it is.
- The jar check (the name and signature are the developer's choice; it may take the jar instead of the counts):

```python
def jar_failure(spec: str, cookies: int, valued: bool, warned: bool) -> CookieFailure | None:
    """A jar that loaded without an error, but holds nothing a site could use."""
    if valued:
        return None
    # Chromium encrypts its cookies; a wrong or missing key leaves them out, or empty.
    if browser_of(spec) in CHROMIUM_BASED_BROWSERS and (cookies or warned):
        return CookieFailure(missing=False, reason=t("cookies_none_decrypted"), undecrypted=True)
    if not cookies:
        return CookieFailure(missing=True, reason="no cookies in the database")
    return None
```

  The `missing` reason is never printed: `report_failure` doesn't read it, and the wizard re-probes before it shows a probe's error. It only fills `CookiesUnavailable`'s text and the dropped probe's error list.
- `report_failure`: `elif failure.undecrypted or uses_keyring(spec):` prints `cookies_keyring_hint` (D3). The `secretstorage` branch before it can't match: the reason is the tool's own text.

### `i18n.py`

| Key | Block | Text |
| --- | ----- | ---- |
| `cookies_none_decrypted` | en | `none of them could be decrypted` |
| `cookies_none_decrypted` | es | `no se pudo descifrar ninguna` |

It fills `cookies_unreadable`'s `{1}`:

- en: `Could not read the chrome cookies (none of them could be decrypted). Going on without cookies.`
- es: `No se pudieron leer las cookies de chrome (no se pudo descifrar ninguna). Se continúa sin cookies.`

### What the user sees

```
$ download-media -y --cookies chrome+basictext -o ~/tmp/x 'http://127.0.0.1:18967/one.html'
Extracting cookies from chrome
Downloading...
  URL: http://127.0.0.1:18967/one.html
  → /home/u/tmp/x

Extracted 0 cookies from chrome (1 could not be decrypted)
Could not read the chrome cookies (none of them could be decrypted). Going on without cookies.
  → if chrome stores its keys in another keyring, name it: --cookies chrome+gnomekeyring (or +kwallet6, +basictext)
[download] Destination: /home/u/tmp/x/one (1).mp4
Done.
Saved without chrome cookies: this is what the site shows without logging in.

Equivalent command to repeat this download:
  download-media -y -o /home/u/tmp/x http://127.0.0.1:18967/one.html
```

## Cases

"Today" is measured on `c6e306c`. "After" is the prototype, run the same way, unless a row says *tests*. A notice is `t("cookies_unreadable", "chrome", t("cookies_none_decrypted"))`, the hint is `cookies_keyring_hint`, and the closing line is `done_without_cookies`.

| Case | Today | After |
| ---- | ----- | ----- |
| `-y`, `+basictext` on `v11` (R1) | `Done.`, keeps `--cookies` | Notice, hint, no `Cookie`, `Done.`, closing line, drops `--cookies`, exit 0, 1 file |
| `-y -p` (R2), `-y --playlist-items 1,2` (*tests*) | `Done.`, keeps `--cookies` | As R1, 2 files; the command keeps `-p` or `--playlist-items` |
| `-y -P` (*tests*) | As `-y` | As `-y` |
| Direct with preview, Enter (R3) | Preview, `Done.`, keeps `--cookies` | Notice and hint before `Preview`, title shown, no second read of the browser, closing line, drops `--cookies` |
| Direct with preview, `n` (*tests*) | Preview, `Cancelled.` | Notice, preview, `Cancelled.`, no command, exit 1 (row 23's) |
| `-l` (R4) | yt-dlp's warning, the list | Notice, hint, the list, no `Cookie`, exit 0 |
| Wizard `-i` (R5) | Silent probe, late step defaults to chrome, `Done.`, keeps `--cookies` | Notice and hint at the probe, re-probe without cookies, picker, late step defaults to `No cookies`, no closing line (spec 50 D2), command without `--cookies` |
| Wizard, late step picks chrome (*tests*) | `Done.`, keeps `--cookies` | Notice and hint at the download, closing line, drops `--cookies`, keeps the items |
| Bare `chrome`, no desktop set (R12) | As R1 | As R1. The hint shows, as it does for any bare Chromium browser |
| Wrong key, short value (R6) | `Done.`, `Cookie: s=` | As R1, no `Cookie` |
| Wrong key, long value (R7) | `Done.`, no `Cookie` | As R1 |
| `kwallet-query` answers `Failed to read` (R8) | `Done.`, `Cookie: s=` | As R1, no `Cookie` |
| Mixed `v10` and `v11` (R9) | `Cookie: dm=ok`, `Done.` | Unchanged |
| Empty cookie database, `--cookies chrome` (R10) | `Done.`, keeps `--cookies` | `No chrome cookies found: is chrome installed, and has it been opened at least once? Going on without cookies.`, no hint, closing line, drops `--cookies` (D2) |
| Empty Firefox database (R15, an empty `moz_cookies` under `$HOME/.mozilla/firefox/x.default/`) | `Extracted 0 cookies from firefox`, `Done.`, keeps `--cookies`, no `Cookie` | `No firefox cookies found: …`, closing line, drops `--cookies` (D2) |
| Right key (R11), plaintext profile, `v10`-only profile | Cookies sent | Unchanged |
| A keyring error yt-dlp logs (spec 45's four causes) | Spec 45's notice | Unchanged: the logged error comes first |
| Browser not installed, dead bus | Spec 26's | Unchanged |
| A page that 404s, `-y` (R13) and `-y -p` (R14) | `Failed with chrome+basictext cookies. Retrying…`, two attempts, `Download failed`, keeps `--cookies` | Notice, hint, one attempt, `Download failed`, no retry line, no closing line, keeps `--cookies`, exit 1 |
| A page that answers 401 (*tests*) | `cookies_retry`, `Download failed` | Notice, `Download failed`, `→ try again with --cookies <browser>` (row 02's English hint, as after spec 45), keeps `--cookies`, exit 1 |
| Two URLs, `-y -p` (*tests*) | `Done.` twice, keeps `--cookies` | Notice and closing line per URL (spec 26 D8), drops `--cookies` |
| `LANG=es*`, `-y` (R1's shape) | — | `No se pudieron leer las cookies de chrome (no se pudo descifrar ninguna). Se continúa sin cookies.`, the Spanish hint, `Listo.`, `Guardado sin las cookies de chrome: …` |
| `-a` | As video | As video |

Rerun doesn't apply: nothing on disk depends on the cookies. Carousel and playlist behave like `two.html` under `-p` and the picker.

## Acceptance criteria

Every `[fix]` must fail on `main`, shown by reverting the fix. The cookie tests run the real yt-dlp and generic extractor on the loopback `media_url` pages, through `direct` and `generic`, with `isolated_env`'s dead bus. No test reads a real cookie database or keyring, and none runs the real `kwallet-query`.

New fixtures in `tests/test_e2e.py`:

- `chrome_v11(value, password)`: a profile with one `v11` cookie `s=<value>`, encrypted with yt-dlp's `aes_cbc_encrypt_bytes` under the key yt-dlp derives from `password` (`pbkdf2_sha1(password, salt=b"saltysalt", iterations=1, key_length=16)`, as at `test_e2e.py:7310`).
- `keyring_says(password)`: a `yt_dlp.cookies.secretstorage` stub, like `empty_keyring`, whose collection holds one item labelled `Chrome Safe Storage` with that secret. Used with `chrome+gnomekeyring`, so no `PATH` or bus is involved.
- `empty_chrome` (a database with no cookies) and `empty_firefox` (a `cookies.sqlite` with an empty `moz_cookies` table, `user_version` ≤ 17, under `$HOME/.mozilla/firefox/<profile>/`).
- `UNDECRYPTED` parametrizes the shapes: `basictext` (`unreadable_chrome`, `chrome+basictext`), `wrong-key-short` (`chrome_v11(b"token", b"realpass")`, `keyring_says(b"wrongpass")`), `wrong-key-long` (a 300-byte value, same keys).

Criteria:

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] **[fix]** `test_cookies_that_decrypt_to_nothing_go_on_without_cookies`. Over `UNDECRYPTED`, run `-y` on `seen/<token>/poster.html`, plus `-y -p` and `-y --playlist-items 1,2` on `seen/<token>/multi.html`. Expect exit 0 and the expected files. The notice appears once, and the next line is `keyring_hint()`. `after_done(output) == [without_cookies()]`. The command has no `--cookies`, there's one history row, and `no_cookie_sent(token)` holds
- [ ] **[fix]** `test_undecryptable_cookies_are_named_before_the_preview`. `basictext`, no `-y`, Enter. Expect one prompt, the notice once and before `t("preview")`, the `t("p_title")` line, the closing line after `t("done")`, exit 0, 1 file
- [ ] **[fix]** `test_a_preview_cancelled_after_undecryptable_cookies_still_said_it`. Same, with `n`. Expect the notice before `t("preview")`, `t("cancelled")`, no files and no command
- [ ] **[fix]** `test_formats_are_listed_after_undecryptable_cookies`. `basictext`, `-l` on `seen/<token>/poster.html`. Expect exit 0, the notice, the format table, and `no_cookie_sent(token)`
- [ ] **[fix]** `test_the_wizard_names_undecryptable_cookies_at_the_probe`. `basictext`, `-i --cookies chrome+basictext` on `multi.html`, `PICKER_WALK`. Expect the notice once, the picker offered, `t("done_without_cookies", "chrome")` absent (spec 50 D2), a command without `--cookies`, 4 files
- [ ] **[fix]** `test_undecryptable_cookies_at_the_last_wizard_step_say_so_after_done`. `unreadable_chrome`, no desktop set (yt-dlp picks `basictext`), chrome at the late step on `multi.html`. Expect the notice once, `keyring_hint()`, the closing line after `t("done")`, a command that keeps the items and drops `--cookies`
- [ ] **[fix]** `test_a_dead_page_after_undecryptable_cookies_is_tried_once`. `basictext`, `-y` and `-y -p` on `status/404`. Expect exit 1, the notice, `t("failed", url)`, no `t("cookies_retry", "chrome+basictext")`, no closing line, a command that keeps `--cookies chrome+basictext`
- [ ] **[fix]** `test_undecryptable_cookies_are_named_for_each_url`. `basictext`, `-y -p` on `poster.html` and `multi.html`. Expect the notice twice and `after_done(output) == [without_cookies()] * 2`
- [ ] **[fix]** `test_an_empty_cookie_database_is_named` (D2). `empty_chrome` with `--cookies chrome`, and `empty_firefox` with `--cookies firefox`, `-y` on `seen/<token>/poster.html`. Expect `t("cookies_no_browser", <browser>)` once, no `keyring_hint`, the closing line, a command without `--cookies`, exit 0
- [ ] **[fix]** `test_undecryptable_cookies_in_both_languages`. `LANG=es*` and `en`, `-y`. Expect the notice built from each block's `cookies_unreadable` and `cookies_none_decrypted`, and the closing line
- [ ] **[pin]** `test_a_jar_with_one_usable_cookie_keeps_it`. The R9 profile (`chrome_cookie`'s `v10` row encrypted with `peanuts`, plus one `v11`), `chrome+basictext`, `-y` and `-y -p` on `cookie/<token>/…`. Expect no notice, `dm=ok` on every request, a command that keeps `--cookies`
- [ ] **[pin]** `test_the_right_key_sends_its_cookie`. `chrome_v11(b"token", b"realpass")`, `keyring_says(b"realpass")`. Expect `s=token` on every request and no notice
- [ ] **[pin]** spec 45's `KEYRING_CAUSES` tests, `test_a_browser_that_loads_sends_its_cookies_to_every_item`, `test_a_jar_that_never_asks_the_keyring_keeps_its_cookies` and `test_an_unreachable_keyring_named_by_hand_gets_no_keyring_hint` pass unchanged
- [ ] **[fix]** `test_cookies_that_decrypt_to_nothing_are_a_cookie_failure` (`tests/test_probe.py`). `probe(<media_url>/poster.html, "chrome+basictext")` on `unreadable_chrome`'s profile. Expect `failed` true, `cookie_failure.undecrypted` true, `missing` false
- [ ] **[fix]** `test_a_jar_with_nothing_usable_is_a_failure` (`tests/test_cookies.py`), one table over the jar check:
  - chrome, 0 cookies, warned → undecrypted, with the reason `t("cookies_none_decrypted")`
  - chrome, 1 cookie, no value, not warned → undecrypted
  - chrome, 0 cookies, not warned → missing
  - firefox, 0 cookies, not warned → missing
  - firefox, 1 cookie, no value → `None`
  - any browser with a value → `None`
  - `report_failure` on an undecrypted failure for `chrome+basictext` prints the hint, and on a `logged_failure` for `chrome+kwallet6` it doesn't
- [ ] **[flip]** `test_a_keyring_that_answers_and_decrypts_nothing_stays_as_it_was` (`test_e2e.py:7475`) pins today's behaviour. It becomes `test_a_keyring_that_answers_and_decrypts_nothing_is_named`, with the notice, the closing line and a command without `--cookies`
- [ ] **[flip]** `test_loading_cookies_leaves_report_error_alone` (`test_downloader.py:455`). The `loads` case returns an empty jar, which is now D2's `missing`. Its jar gets one cookie with a value. The test also asserts `"report_warning" not in vars(ydl)`, and gains a `warns` case: the stub calls `ydl.report_warning(...)` and returns an empty jar, for `chrome` → `CookiesUnavailable`
- [ ] On the prototype these were the only two existing tests that flipped. That covered the cookie tests by name in `test_e2e.py`, `test_cookies.py`, `test_probe.py` and `test_downloader.py` (in `en` and `es`), plus 138 other tests that mention cookies or a browser (333 passed). Any other flip must be explained in the developer's report
- [ ] `test_es_and_en_have_same_keys` passes, with `cookies_none_decrypted` in both blocks
- [ ] On disk, by hand, once, in the Problem's environment: R1, R2, R6, R9 and R10 compared with what was reported, and the server log checked for `Cookie`

## Live check

Not needed for the gate. Everything under test is yt-dlp's real loader and decryptor, run on fake profiles over loopback. The rule reads only the jar yt-dlp returns. A real keyring is off-limits outside the tool, and a real site only changes what the cookieless run returns.

Optional, for the human, through the tool only. On a machine where a Chromium browser keeps its key in a keyring, run `download-media -y --cookies <browser>+basictext -o tmp/ '<a public video URL>'`. `+basictext` asks no keyring. It should print the notice, save the video and end with the closing line. If it saves silently, the profile still holds `v10` cookies, which is R9's shape: no change by design. The result says how often real profiles land in each case, so it's worth recording either way.

## SemVer

`fix`. Nothing is breaking by `AGENTS.md`'s list: no flag, default, path or exit code changes. One new message (`cookies_none_decrypted`), so it needs the minor, and 0.4.0 is a minor already.

**For the "Next release" review list:** published behaviour changes in five ways.

1. **Cookies that load but carry no value now count as unreadable, in every mode.** That covers none decrypting (`+basictext` on `v11`, or a bare `chrome` on a desktop yt-dlp doesn't recognise) and a keyring whose wrong key empties every value. The run says `Could not read the <browser> cookies (none of them could be decrypted). Going on without cookies.` with the keyring hint, sends no cookie, and prints the closing line after `Done.`. It used to say nothing, and in the wrong-key case it sent `Cookie: s=`.
2. **The printed command drops `--cookies` after such a run when it saved** (spec 26 D6's rule), and keeps it when the run failed.
3. **D2: an empty cookie database is now named.** `No <browser> cookies found: …` and the closing line, for any browser, such as a Firefox profile that clears its cookies on close. The printed command drops `--cookies` there too.
4. **D3: the keyring hint now follows this notice even when the user named a keyring** (`chrome+basictext`). A keyring named by hand still gets no hint after spec 26's and 45's notices.
5. **On a dead page, one attempt instead of two,** and no more `Failed with <browser> cookies. Retrying without cookies...` when no cookie was sent.

None turns a save into a failure: the jars dropped held no value a site could use.

**Release note (for the planner):** "When a browser's cookies load but none of them can be decrypted, such as with the wrong keyring, the tool now says so, suggests another keyring, and goes on without cookies in every mode. It used to carry on silently, sometimes sending empty cookies. An empty cookie database is now named the same way."

## Open decisions

Delegated to the roles by the human ("sigue trabajando y haz lo que más puedas"). Each has a recommendation I'd defend in review. Changes to published behaviour are listed under SemVer.

### D1: the detection rule

| Option | Effect |
| ------ | ------ |
| **A. The jar holds no cookie with a value (recommended)** | Reads only yt-dlp's return value, no text. Catches the row's case every time (R1, R12), and a wrong key whenever no garbage value decodes (R6, R7, R8). Drops nothing that works: R9 and R11 keep their cookies, so no save turns into a failure. Misses a wrong key on a large real profile where a few garbage values decode (Out) |
| B. Any decryption warning drops the jar | Catches more wrong keys, but must match yt-dlp's warning texts (the right-key control warns too, R11). It drops R9's working `dm=ok`, turning a save into a failure, as spec 45 D1 did for logged errors. Still blind to R6 and R8, which warn nothing about decryption |
| C. Parse `Extracted 0 cookies … (M could not be decrypted)` | Depends on one yt-dlp sentence. Equals A on R1 and R7, misses R6 and R8 (`Extracted 1 cookies`), and can't name an empty database |
| D. A ratio: most values empty means a wrong key | Would catch most large wrong-key profiles, but the threshold is a guess about real profiles, which the tool may not read (`constraints.md`, Privacy 1). It risks dropping a working jar on a guess |

**Recommendation: A.** It's exact where it acts and costs nothing where it doesn't. The residual is best fixed by yt-dlp checking the padding (row 64).

### D2: an empty cookie database, with nothing reported

| Option | Effect |
| ------ | ------ |
| **A. Name it with `cookies_no_browser` and go on, as for a missing database (recommended)** | The existing text is literally true ("No chrome cookies found: … has it been opened at least once?"). The outcome is the same as today minus the silence, and one more `if`. Firefox profiles that clear cookies on close land here. For them the "opened at least once?" question is a little off, but the core of the notice is right |
| B. Stay silent, as today | Keeps the row's letter. A run the user asked to make with cookies sends none and says nothing, against "a success never hides what was skipped" |
| C. Treat it as undecrypted | One branch less, but "none of them could be decrypted" would be false for an empty database |

**Recommendation: A.** It's the same outcome the tool already gives a browser with no database, and the condition falls out of the same check.

### D3: the keyring hint after this notice

| Option | Effect |
| ------ | ------ |
| **A. Show it for an undecrypted jar even when a keyring was named (recommended)** | The named keyring is the likely cause, and the hint names the alternatives. Spec 26 turned the hint off for named keyrings because its causes there (a dead bus, a missing tool) weren't about which keyring. Here it is. The hint also lists `+basictext` after a failed `+basictext`, a wording nit for row 02 |
| B. Only for a bare browser, as today | `chrome+basictext`, the row's own case, gets no hint, though choosing another keyring is the fix |

**Recommendation: A.**

### D4: the wording

| Option | Effect |
| ------ | ------ |
| **A. `cookies_unreadable` with a new reason, `none of them could be decrypted` (recommended)** | Reads like the other cookie notices, and tests build it from two keys. One new key |
| B. A standalone message, e.g. `The chrome cookies loaded, but none could be decrypted. Going on without cookies.` | Says "loaded" precisely, but it's a fourth notice shape for the same outcome |
| C. yt-dlp's decryption warning as the reason | No new key, but it doesn't exist for R6 and R8, and it ties the text to yt-dlp's wording |

**Recommendation: A.**

### D5: what happens after the notice

| Option | Effect |
| ------ | ------ |
| **A. Spec 45 D1's outcome: no cookies, closing line, every mode, through the existing doors (recommended)** | No change to `cli.py`, `wizard.py` or `probe.py`. The jar dropped held no value, so nothing is lost; R6 and R8 stop sending `s=`. A dead page gets one attempt, without the false retry line |
| B. Keep the jar and only warn | Needs a new message, a `DownloadResult` field and a flag from the preview. The closing line would be false where an empty cookie was sent, and the same failure would read two ways depending on the cause |

**Recommendation: A.**
