# 06 — Fix the up-front cookies prompt

Status: done
Item: `fix/wizard-ig-cookies`

## Problem

The wizard's cookies prompt before the probe has three faults. `b`, Enter, `b` ends in a traceback. The prompt appears on every Instagram URL, though public posts need no cookies. And the running summary never lists what the user answered there.

Reproduced on `main` at `2d967f8`, with yt-dlp 2026.10.26, `curl_cffi` 0.17.0 and Python 3.14. Nothing reached the network.

**1. `b`, Enter, `b`: traceback.** The real console script, under `env -i` with `HOME` and `XDG_STATE_HOME` in a temp folder and a dead D-Bus address. Port 9 on loopback, so any request would be refused. `detect_site` reads the whole URL, so the query makes it an Instagram URL:

```bash
printf '\nb\n\nb\n' | LANG=en_US.UTF-8 .venv/bin/download-media -i 'http://127.0.0.1:9/p?from=instagram.com'
# Download wizard
# Video URL [http://127.0.0.1:9/p?from=instagram.com] (c=cancel):   This site requires browser cookies to work.
# Use browser cookies? (required for logged-in YouTube, private content)
# Select [1]: Video URL [...] (c=cancel): Use browser cookies? (...)
# Select [1]: Traceback (most recent call last):
#   File ".../wizard.py", line 545, in _walk_url
#     step_cookies(opts)
#   ...
# download_media.wizard._Back
#
# During handling of the above exception, another exception occurred:
#   File ".../cli.py", line 474, in main
#     walked = wizard.run_all(opts)
#   File ".../wizard.py", line 552, in _walk_url
#     step_cookies(opts)
#   ...
# download_media.wizard._Back
# exit 1
```

`LANG=es_ES.UTF-8` gives the same traceback, after `Este sitio requiere cookies del navegador para funcionar.` and `Selecciona [1]:`. The cause is `wizard.py:548-554`. The first `_Back` is caught and the URL is asked again, but the second `step_cookies` call is in the same `try` as the first handler, so its `_Back` has nothing to catch it. `-l` goes through `run()` → `_walk_url`, so it crashes the same way.

**2. Instagram is asked before the probe, and the summary leaves the answer out.** A scratch test ran `cli.main` with the `fake_site` fixture (real yt-dlp and ffmpeg, loopback only), `answers` scripting every prompt. Both `i18n.LANG` and `wizard.LANG` were patched, since `wizard.py` binds `LANG` at import. Output in English. Spanish is the same, word for word, with the Spanish labels:

```text
$ -i https://fake.test/carousel?from=instagram.com      replies: "", "1", "", "", "", "", ""
Download wizard
  This site requires browser cookies to work.
Use browser cookies? (required for logged-in YouTube, private content)
  ...
Inspecting content...
✓ Detected: instagram / Carousel/Playlist
...                       public carousel: the probe needed no cookies; 2 files, exit 0, 7 prompts

$ -i https://fake.test/needs-cookies?from=instagram.com replies: "", "2", "", "", "", ""
  This site requires browser cookies to work.
Use browser cookies? ...
✓ Detected: instagram / Video
What do you want to download?
...
Selections so far:
  ✓ Type: Video (with audio)
  ✓ Quality: 1080p
  ✓ Container: mp4
...
  download-media -y -o <out> --cookies firefox 'https://fake.test/needs-cookies?from=instagram.com'
```

The summary never says `Cookies: firefox`, though the printed command carries it. The same goes for the cookie prompt that spec 18 shows after a login wall: on `https://fake.test/needs-cookies`, answering `2` there gives the same three summary lines and no `Cookies` line. In Spanish: `Selecciones hasta ahora:`, `✓ Tipo: Video (con audio)`, and no `✓ Cookies:` line.

- **What happened:** a traceback; a cookies question on every Instagram URL; a summary that hides one of the answers.
- **What should happen:** `b` at the up-front prompt can be pressed any number of times; Instagram is probed first, and asked for cookies only when the probe hits a login wall; the summary lists the cookies answer wherever it was given.

## Goal

On Instagram, the wizard asks for cookies only when the post needs them, and says why. On the sites that still get the prompt up front, `b` always goes back to the URL, and every cookies answer shows in the running summary.

## Findings

### Why Instagram can leave the list (yt-dlp 2026.10.26 source, plus spec 18)

The up-front prompt dates from the first Python commit (`6fd8fdd`). It came before spec 18, when the probe couldn't see login errors. Spec 18 is now on `main`, so a login wall asks for cookies after the probe and probes again (`wizard.py:564-580`). Every Instagram path that cookies can fix gets there without the up-front prompt:

| Instagram case, no cookies                                  | What `instagram.py` does                                                                                            | `is_auth_error` | Wizard without the up-front prompt                                 |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------- | ------------------------------------------------------------------ |
| Public post, video or carousel                              | Logged-out GraphQL with `impersonate=True` (`:472-488`), `if_not_gated_logged_out` (`:491`)                          | n/a             | Probed and detected first time. Picker, no cookies question before |
| Private, followers only (long shortcode)                    | `raise_login_required('This content is only available for registered users who follow this account')` (`:502-503`) | yes (hint)      | `auth_needed_retry` with the reason, one prompt, re-probe          |
| Restricted (age) post                                       | `raise_login_required(error)` (`:495-496`)                                                                          | yes (hint)      | Same                                                               |
| Anonymous rate limit (login redirect)                       | `raise_login_required(...rate-limit...)` (`:507-510`)                                                               | yes (spec 18 D4) | Same                                                              |
| Empty media response                                        | `ExtractorError` that embeds `self._login_hint()` (`:520-524`)                                                      | yes (hint)      | Same                                                               |
| Stories (`InstagramStoryIE`)                                | `raise_login_required(...)` (`:730`, `:745`)                                                                        | yes (hint)      | Same: one round trip before the prompt                             |
| Other refusal from `get_ruling_for_content`                 | `ExtractorError(title: description, expected=True)`, no hint (`:497-498`)                                           | by its words    | If not auth: real reason, manual flow, and the **late** cookies step is still there |
| Lone image                                                  | `raise_no_formats('There is no video in this post', expected=True)` (`:530`)                                        | no              | `probe_failed`, manual flow, then `Download failed`, as with a decline today. Images aren't supported either way |

`raise_login_required` appends `_login_hint()` (`common.py:1252`), which names `--cookies` (`common.py:595-604`). Spec 18's rule treats that hint as an auth error. Logged in, the extractor takes `/media/{id}/info/` (`:437-443`), so a browser given after the probe gets the same result a browser given up front gets today.

Measured on `fake_site` with a prototype (below): `https://fake.test/needs-cookies?from=instagram.com` prints no up-front prompt, then `This content needs a login: Login required`, one cookies prompt, re-probe, `Clip.mp4`, exit 0. Today the same decline costs two prompts (spec 18 D5).

Live evidence for the public case: `tests/live/test_live.py::test_instagram_carousel_without_cookies` (`-y`, no cookies) is part of every release gate. Spec 18's live step 1 also ran the wizard on `DM_LIVE_IG_CAROUSEL`, declining up front, so the probe ran without cookies. It passed at `e69dda4`.

What gets worse: an Instagram post whose refusal has no login hint and no auth words (the `:497-498` row) no longer gets the browser on its first probe. Today, someone who picked a browser up front would be logged in there. After this change, that user gets the real reason, the manual flow and the late cookies step. That step still saves with cookies, so it costs the picker and nothing more. No reproduction exists: the texts come from Instagram's API, not from yt-dlp.

### X and Facebook (source only)

- **X:** the default API is `graphql` (`twitter.py:99-102`). Logged out, it gets a guest token (`:124-127`), and on HTTP 429 it falls back to the syndication endpoint (`:1196-1201`). Protected and NSFW tweets, and API "not authorized" errors, go through `raise_login_required` (`:139-141`, `:1089-1092`). `Could not retrieve guest token` (`:110`) is a plain `ExtractorError` with no hint.
- **Facebook:** a login page leads to `raise_login_required()` with the default text and hint (`facebook.py:755-762`).

So the source says public content on both works without cookies, and their login walls would reach spec 18's retry. But nothing has run against either site, and X's guest access has broken before. When it breaks, the error isn't an auth error, so without the up-front prompt the wizard would fall back to the manual flow. See D1.

### `detect_site` calls Dropbox "x"

`_SITE_PATTERNS` matches `x\.com` anywhere in the URL (`probe.py:82`). Measured with `.venv/bin/python`:

| URL                                         | `detect_site` | Up-front prompt |
| ------------------------------------------- | ------------- | --------------- |
| `https://www.dropbox.com/s/abc/clip.mp4`    | `x`           | yes             |
| `https://app.box.com/s/abc`                 | `x`           | yes             |
| `https://www.fox.com/watch/x`               | `x`           | yes             |
| `https://www.netflix.com/title/1`           | `x`           | yes             |
| `https://x.com/u/status/1`                  | `x`           | yes             |

So a Dropbox link gets "This site requires browser cookies to work." and a `x / Video` banner. `\b(twitter|x)\.com\b` keeps the last row and drops the other four. It still matches `mobile.twitter.com` and the tests' `?from=x.com`. `site` is read only by the banner and the up-front prompt (`wizard.py:358`, `:540`), so direct modes can't change. See D2.

### Prototype

The Design below, applied to a scratch copy of `src/` and `tests/` (from `git archive HEAD`), run with the main venv and `PYTHONPATH` pointed at the copy. `src/` in the repo wasn't touched.

- `wizard.py` +13 −6 lines, `probe.py` 2 lines. `mypy` (strict) and `ruff check` clean.
- The suite, unchanged, minus the three files that read repo paths outside the copy (`test_packaging.py`, `test_release_runbook.py`, `test_workflows.py`): **10 failed, 1735 passed, 2 xfailed.** All 10 use Instagram as the cookie-hungry site, and they're listed in Acceptance criteria. With their URL moved to `x.com`, the four affected files pass: 1345 passed, 2 xfailed.
- 18 new scratch tests, the behaviours in Cases: all pass on the prototype. On `main`, 15 of them fail and 3 pass. The 3 are the pins: back then a URL off the list, no `Cookies` line without a prompt, `--cookies` preselected.

## Scope

### In

- Instagram leaves the up-front list (D1). `x` and `facebook` stay.
- `b` at the up-front prompt goes back to the URL prompt every time it's pressed. After each URL answer, the URL decides again whether to ask (D4).
- The summary lists the cookies answer from any prompt before the walk, up front or after a login wall (D3).
- `detect_site` matches `x.com` and `twitter.com` only as whole names (D2).

### Out

- **`b` at the first step after the probe can't go back to that cookies answer.** It asks the same step again, as spec 19 left it. The summary now lists an answer the user can't go back to. That's honest, since the probe already used it. Planner, if it bothers anyone.
- **`b` typed at the URL prompt is taken as the URL `b`.** That prompt has `allow_back=False`, and it's the same at the first URL prompt today. Planner.
- **`auth_needed_pre` says "This site requires browser cookies to work",** which overstates for X and Facebook. Rewording adds no message, but it's a change of wording on its own. Planner (`i18n-leftovers`).
- **X and Facebook leaving the list,** unless D1 picks B. A planner row with live evidence.
- **Instagram's lone image** (`ig-image`-style rows): it fails the anonymous probe as today's decline does.
- **The double blank line** between the detection banner and the first summary. It's cosmetic, and the picker already shows it.

## Files

| Action | File                            |
| ------ | ------------------------------- |
| modify | `src/download_media/probe.py`   |
| modify | `src/download_media/wizard.py`  |
| modify | `tests/test_probe.py`           |
| modify | `tests/test_wizard_flow.py`     |
| modify | `tests/test_e2e.py`             |

No change to `i18n.py`: no message is added or removed. `README.md:69` ("Every prompt shows the choices made so far… if it needs a login it offers browser cookies right after the URL") and `AGENTS.md` ("plus cookies up front for sites that always need them") stay true. `cli.py`, `downloader.py`, flags, defaults and dependencies don't change.

## Design

Written for the recommended option of each decision.

### `probe.py`

```python
("x",         re.compile(r"\b(twitter|x)\.com\b", re.IGNORECASE)),
...
_COOKIE_HUNGRY_SITES: frozenset[str] = frozenset({"x", "facebook"})
```

Update the comment above `_COOKIE_HUNGRY_SITES` so it gives the reason: on these sites the anonymous probe isn't trusted yet, and on every site a login wall reaches the retry after the probe (spec 18). `detect_site` keeps searching the whole URL, so the tests' `?from=x.com` trick still works.

### `wizard.py`: the up-front prompt

Replace Phase 1.5 (`:539-555`) with a loop that asks again whether the URL needs the prompt after every URL answer:

```python
cookies_asked = False
while site_likely_needs_cookies(detect_site(opts.urls[0])) and not opts.cookies_browser:
    info(f"  {t('auth_needed_pre')}")
    try:
        step_cookies(opts)
    except _Cancel:
        return None
    except _Back:
        # Back to the URL prompt; the URL typed there decides whether to ask again.
        try:
            step_url(opts)
        except _Cancel:
            return None
        continue
    cookies_asked = True
    break
```

- The `break` matters. "No cookies" leaves `cookies_browser` at `None`, and without the `break` the loop would ask again.
- The loop can't spin on its own: every turn reads an `input()`, and EOF raises `_Cancel`.
- `step_url` sets `opts.urls` from whatever was typed, several URLs included. `run_all` already queues `current.urls[1:]` after the walk (`:515`, spec 19's `41df9b2`), so only the last answer counts, which is right.
- A URL off the list leaves the loop without asking, so `cookies_asked` stays `False` and the late step stays in the plan. The user is then asked once, later.
- The local `site` variable goes. The probe reports its own site for the banner.

### `wizard.py`: the summary

`_walk_plan` gains `asked: tuple[str, ...] = ()`, printed ahead of the history and never part of it:

```python
_print_summary([*asked, *history], opts)
...
asked = ("cookies",) if cookies_asked else ()
if not _walk_plan(plan, opts, probe_result.items, preset, asked):
```

- Keeping it out of `history` leaves back-navigation alone: `b` at the first step after the probe asks that step again, as today.
- `_summary_value("cookies")` already gives `opts.cookies_browser or t("no_cookies")`. After `_drop_unreadable_browser` (spec 26) it reads "No cookies", which is what the download will do.
- `cookies_asked` is `True` after either prompt before the walk, so both are listed. With `-i --cookies <browser>` nothing is asked up front: the late step stays, and it's listed once it has run, like any other step.
- The summary now prints at the first step of the walk when a prompt came before it. Today nothing prints there.

What the user sees (prototype, `es`, browser up front on `?from=x.com`):

```text
✓ Detectado: x / Video
  Clip


Selecciones hasta ahora:
  ✓ Cookies: firefox

¿Qué quieres descargar?
```

### Tests

- **A cookie-hungry URL on `fake_site` is now `?from=x.com`.** `COOKIE_HUNGRY` in `test_e2e.py` (`:3627`) and the literals at `:3757`, `:4038` and `:5861` move to it, and the banner assertion at `:4042` becomes `x / …`. In `test_wizard_flow.py`, `https://www.instagram.com/p/x/` at `:172-189` and `:510` becomes an X status URL. Some of these tests still pass with Instagram, but now for the wrong reason (for example `test_back_from_first_cookies_prompt_restarts_url`), so move them all.
- **An Instagram URL on `fake_site` stays `?from=instagram.com`.** It's still `instagram` for the banner, and now gets no up-front prompt.
- **es/en summary tests patch both `i18n.LANG` and `wizard.LANG`.** `_summary_label` reads `wizard.LANG`, which was bound at import.
- The `answers` fixture fails on an unscripted prompt, so the prompt counts below are checked by the scripts themselves.

## Cases

"Up front" is the prompt before the probe. "Retry" is spec 18's prompt after a login wall. "Late" is the `cookies` step at the end of the walk. Prompt counts include every `input()`. Measured on the prototype unless marked "unchanged".

| Case                                                                      | Expected                                                                                                                 | Prompts |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------- |
| Instagram, public carousel                                                | No up-front prompt, picker, late step asked once. Banner `instagram / …`. 2 files, exit 0                               | 7       |
| Instagram, gated, browser at the retry                                    | `auth_needed_retry` with `Login required`, one cookies prompt, re-probe, `Clip.mp4`, exit 0, command has `--cookies`     | 6       |
| Instagram, gated, declines                                                | One cookies prompt (two today), manual flow, exit 1                                                                      | 7       |
| Instagram, gated, `c` or EOF at the retry                                 | `Cancelled.`, exit 0, no command. Unchanged path                                                                         | 2       |
| X, public carousel, `b`, Enter, `b`, Enter, `1`                           | `auth_needed_pre` ×3, `ask_cookies` ×3, no exception, 2 files, exit 0                                                    | 11      |
| X, `b`, Enter, `b`, then `c` or EOF at the URL prompt                     | `Cancelled.`, exit 0, no command                                                                                         | 5       |
| X, `b`, then a URL off the list (`https://fake.test/carousel`)            | No second up-front prompt. Late step stays: `ask_cookies` ×2. 2 files                                                    | 9       |
| X, `b`, then several URLs                                                 | First walked now, the rest queued, not asked again (existing test, moved to X)                                           | 13      |
| X, `c` or EOF up front                                                    | `Cancelled.`, exit 0. Unchanged                                                                                          | 2       |
| X, browser up front                                                       | `✓ Cookies: firefox` from the first summary on. No retry, exit 0                                                         | 6       |
| X, declines up front, public                                              | `✓ Cookies: No cookies` / `✓ Cookies: No usar cookies`. One cookies prompt                                               | 7       |
| X, declines up front, then a login wall                                   | Retry prompt as today (spec 18 D5 stays for X). Summary lists the retry's answer                                         | 8       |
| Any site, browser at the retry                                            | `✓ Cookies: firefox` in the summary                                                                                      | 6       |
| X, declines up front, probe fails without a login wall (`rate-limited`)   | `probe_falling_back`, manual flow, no late step, summary lists `Cookies: No cookies`, exit 1                            | 7       |
| No prompt before the walk (`fake.test/carousel`)                          | No `Cookies:` line before the late step. Unchanged                                                                       | 7       |
| `-i --cookies firefox` on X                                               | No up-front prompt, late step preselected, asked once. Unchanged                                                         | 7       |
| Browser not installed, picked up front on X (spec 26)                     | Named once, cookieless re-probe, detected, saved. Existing test, moved to X                                              | 6       |
| Login wall after a missing browser up front (spec 26)                     | Two prompts, three probes, no loop. Existing test, moved to X                                                            | 8       |
| Several URLs, second is X: `b`, Enter, `b`, Enter, `1`                    | The second walk loops like a single URL. Both downloaded, exit 0                                                         | 17      |
| Several URLs, `c` after back at a later walk's up-front prompt            | Whole run cancelled, nothing downloaded. Existing parametrized test, moved to X                                         |         |
| Dropbox-shaped URL (`?from=dropbox.com`)                                  | No up-front prompt, banner `generic / …`                                                                                 | 7       |
| `-l` in the wizard on X, `b`, Enter, `b`                                  | Same loop: `run()` shares `_walk_url`                                                                                    |         |
| direct, `-y`, `-p`, `-P`, `-l` without the wizard                         | Unchanged: they never call `_walk_url`, and `site` is read only by the wizard                                            |         |
| with / without cookies                                                    | Above                                                                                                                    |         |
| single, carousel, playlist                                                | Above. Nothing here depends on the kind                                                                                  |         |
| rerun, history, printed command                                           | Unchanged: the command already carried the cookies answer, and the summary is display only                              |         |
| partial and total failure                                                 | Unchanged: failures come from the probe and the download, which this item doesn't touch                                 |         |

## Acceptance criteria

**[fix]** needs a test that fails without the fix, shown by reverting it (`constraints.md`, Verification 5). **[pin]** protects behavior that must not change, so it passes before and after. All on `fake_site` or in unit tests, loopback only. No test reads real cookies or a keyring.

**Gate**

- [x] `scripts/pre-release.sh --no-live` passes
- [x] The only existing tests that change are the moves in "Tests" above, plus `test_cookie_hungry_sites`. The 10 that fail on the prototype unchanged: `test_declining_twice_on_a_cookie_hungry_site_asks_no_third_time`, `test_a_decline_up_front_can_be_taken_back_at_the_retry`, `test_a_browser_given_up_front_needs_no_retry`, `test_a_public_carousel_on_a_cookie_hungry_site_asks_once`, `test_a_browser_that_is_not_installed_picked_up_front_still_detects_the_page`, `test_back_at_the_second_prompt_of_a_cookie_hungry_site_asks_no_third_time`, `test_a_login_wall_after_a_missing_browser_up_front_asks_once_more_only`, `test_cookie_hungry_sites`, `test_cookie_hungry_site_asks_before_probing`, `test_run_all_splits_urls_typed_after_back_from_the_first_cookies_prompt`

**The `_Back` traceback**

- [x] **[fix]** e2e, `?from=x.com` carousel, replies `""`, `b`, `""`, `b`, `""`, `1`, picker, type, quality, container, folder: exit 0, `t("auth_needed_pre")` and `t("ask_cookies")` 3 times each, 2 files
- [x] **[fix]** Same, then `c` and `EOFError` at the second URL prompt: exit 0, `t("cancelled")`, no `t("equiv_cmd")`
- [x] **[fix]** Unit (`test_wizard_flow.py`), `run_all` on an X URL: `b`, URL, `b`, URL, `1`: no exception, and the walk returns options
- [x] **[fix]** e2e, several URLs: the loop in the second walk, 17 prompts, both downloaded
- [x] **[pin]** `b`, then a URL off the list: one `t("auth_needed_pre")`, two `t("ask_cookies")`

**Instagram**

- [x] **[fix]** `test_cookie_hungry_sites`: `instagram` False, `x` and `facebook` True
- [x] **[fix]** e2e, `https://fake.test/carousel?from=instagram.com`, in `es` and `en`: no `t("auth_needed_pre")`, one `t("ask_cookies")`, `instagram /` in the banner, 2 files, 7 prompts
- [x] **[fix]** e2e, `https://fake.test/needs-cookies?from=instagram.com`, browser at the retry: `t("auth_needed_retry", "Login required")`, one `t("ask_cookies")`, `Clip.mp4`, `--cookies` in the printed command, 6 prompts
- [x] **[fix]** Same, declines: exit 1, one `t("ask_cookies")`, 7 prompts

**The summary**

- [x] **[fix]** In `es` and `en`, browser up front on `?from=x.com`: the summary line for cookies, `✓ Cookies: firefox`, is in the output before `t("ask_type")` (the label from `_summary_label("cookies")`)
- [x] **[fix]** Decline up front on a public X carousel: `✓ Cookies: ` + `t("no_cookies")`
- [x] **[fix]** Browser at the retry on `needs-cookies`: `✓ Cookies: firefox`
- [x] **[fix]** Decline up front on `rate-limited?from=x.com`: `t("probe_falling_back")`, one `t("ask_cookies")`, and the cookies line listed
- [x] **[pin]** `fake.test/carousel` with no prompt before the walk: no `Cookies:` line before the late step runs

**`detect_site`**

- [x] **[fix]** Unit: `detect_site` on Dropbox, Box, Fox and Netflix URLs is `generic`. On `x.com`, `mobile.twitter.com` and `twitter.com` it's `x`
- [x] **[fix]** e2e, `https://fake.test/carousel?from=dropbox.com`: no `t("auth_needed_pre")`, `generic /` in the banner

**Unchanged**

- [x] **[pin]** `-i --cookies firefox` on an X carousel: no `t("auth_needed_pre")`, one `t("ask_cookies")`
- [x] **[pin]** Every moved test passes with `?from=x.com`: X still gets the prompt up front, spec 18's double prompt and spec 26's missing browser included

**Commits** (developer's call, one logical change each): `fix(wizard): let back at the up-front cookies prompt repeat`, `fix(wizard): list cookies asked before the walk in the summary`, `fix(probe): stop asking for cookies up front on instagram`, `fix(probe): match x.com only as a whole host name`. Each with its tests.

**SemVer:** patch-level by `AGENTS.md`: no new message, flag, default or path. It ships in 0.3.0, which isn't tagged yet. No version bump here.

**For the human before release (published behaviour):**

1. Instagram URLs no longer get the cookies question before the probe. A post that needs a login asks after the probe, with the reason, once.
2. Dropbox, Box, Fox and Netflix links no longer read `x` in the banner, and they're no longer asked for cookies up front.
3. The summary lists the cookies answer given before the walk.

Items 1 and 2 belong in the 0.3.0 release notes.

## Live check

Not needed. The changed code is the wizard's own flow, and `fake_site` drives it with the real yt-dlp. What a live run would add is Instagram's real answer to an anonymous probe:

- **Public post:** already shown. `test_instagram_carousel_without_cookies` runs at every full release gate, and spec 18's live step 1 probed `DM_LIVE_IG_CAROUSEL` without cookies through the wizard.
- **Gated post:** needs `DM_LIVE_IG_GATED`, which isn't set. Spec 18's skipped gated-post step already covers it, and it's on the 0.3.0 release list. After this item, that step loses its "decline up front" reply: `.venv/bin/download-media -i -o tmp/probe-auth-gated "$DM_LIVE_IG_GATED"`, then pick the browser at the only cookies prompt.

Optional, at release, with variables already in `.ai/live.env` and no cookies: `printf '\n\n\n\n\n\n\n' | .venv/bin/download-media -i -o tmp/wizard-ig "$DM_LIVE_IG_CAROUSEL"`. It must print no `This site requires browser cookies`, show the picker, and save as many files as `[video]` rows were printed.

## Validation

On `main` at `13f2839` (merge `3199a0c`; `13f2839` only changes `.ai/orchestration/state.json`), with yt-dlp 2026.10.26, ffmpeg n9.0.2 and Python 3.14.9. GitHub CI run 42503757343 was running at validation time; the orchestrator checks its result.

- **Gate:** not re-run here, as the orchestrator asked. The tech-lead ran `scripts/pre-release.sh --no-live` on the merge: exit 0, 1839 passed. Here, `ruff check src tests` is clean and `mypy` reports `no issues found in 14 source files`. `ruff format --check src tests` flags the same 6 files as before (row 05). Its `probe.py` hunk takes in the changed `x` line only because the whole `_SITE_PATTERNS` table is column-aligned.
- **Targeted run:** `.venv/bin/pytest -q tests/test_probe.py tests/test_wizard_flow.py tests/test_e2e.py` gives 1400 passed, 2 xfailed (the two known opus/ogg rerun xfails, row 25).
- **Reverting the fix:** the merged `tests/` ran against the parent's `src/` (`3199a0c^1`). Both trees came from `git archive` into the scratchpad, with `PYTHONPATH` pointing at the copy. The same three files give **48 failed**, 1352 passed. The 48 are every **[fix]** criterion below, plus the test-engineer's cases on the same behaviour. They fail on the bug itself: `_Back` escaping, `auth_needed_pre` printed for Instagram, no `Cookies:` line, `x` for Dropbox, Box, Fox and Netflix. The **[pin]** tests pass on the parent, as they should, with one exception: `test_a_url_typed_after_back_that_needs_no_cookies_is_not_asked_up_front`. It also asserts that the second cookies question comes after `probing`, i.e. that it is the late step. The parent asks the new URL up front again (D4 C's quirk), so that assertion fails there. The test is stronger than a pin, and it's the right behaviour under D4 A.
- **Reproduction:** the Problem's command, moved to X since Instagram no longer gets the prompt. Run through the real console script under `env -i`, with `HOME` and `XDG_STATE_HOME` in empty scratch folders and a dead D-Bus address:
  - `printf '\nb\n\nb\n' | LANG=en_US.UTF-8 .venv/bin/download-media -i 'http://127.0.0.1:9/p?from=x.com'`: `This site requires browser cookies to work.` and `Use browser cookies?` twice, the URL prompt three times, then `Cancelled.` at the EOF, exit 0. No traceback, no equivalent command.
  - `LANG=es_ES.UTF-8` gives the same with `Este sitio requiere cookies del navegador para funcionar.`, `Selecciona [1]:` and `Cancelado.`, exit 0.
  - The original Instagram form (`?from=instagram.com`, replies Enter then `c`) now goes straight to `Inspecting content...` / `Inspeccionando contenido...`. The probe is refused on port 9, and the run continues in manual mode, `Cancelled.` / `Cancelado.` No cookies question.
  - Afterwards both scratch folders were still empty: no history row. Nothing left loopback. No cookies or keyring were touched.
- **Criteria, one by one** (all in `tests/test_e2e.py` unless marked):
  - Gate: the tech-lead's run above. The only existing lines that change are the moves in "Tests" and `test_cookie_hungry_sites` (`git diff 3199a0c^1 3199a0c -- tests/`, removed lines only: `COOKIE_HUNGRY`, the four literals, the banner assertion, the five `instagram.com/p/x/` URLs in `test_wizard_flow.py`, and the `instagram is True` line).
  - `b`, Enter, `b`, three prompts each, 2 files: `test_back_at_the_prompt_before_the_probe_can_be_pressed_again_and_again`.
  - Then `c` / EOF at the URL: `test_cancelling_at_the_url_prompt_after_backing_twice_ends_the_run[cancel, eof]`.
  - Unit `run_all`: `test_wizard_flow.py::test_run_all_survives_back_twice_at_the_first_cookies_prompt` (10 prompts, options returned), plus `test_back_at_the_first_cookies_prompt_can_be_pressed_again`.
  - Several URLs, 17 prompts: `test_back_at_a_later_walk_s_prompt_before_the_probe_can_be_pressed_again` (`asked == 17`, three `auth_needed_pre`, both saved).
  - URL off the list after `b`: `test_a_url_typed_after_back_that_needs_no_cookies_is_not_asked_up_front`.
  - `test_probe.py::test_cookie_hungry_sites`.
  - Public Instagram carousel, es and en: `test_a_public_instagram_carousel_is_probed_before_any_cookies_question[es, en]`.
  - Gated Instagram, browser at the retry: `test_a_gated_instagram_post_asks_for_cookies_once_after_the_probe`. Declining: `test_declining_cookies_on_a_gated_instagram_post_asks_once`.
  - Summary, browser up front, es and en, before `ask_type`: `test_the_summary_lists_a_browser_given_up_front[es, en]`. Decline: `test_the_summary_lists_a_decline_up_front[es, en]`. Browser at the retry: `test_the_summary_lists_a_browser_given_after_a_login_wall`. `rate-limited?from=x.com`: `test_the_summary_of_the_manual_flow_lists_a_decline_up_front`. Pin: `test_the_summary_has_no_cookies_line_before_the_late_step`.
  - `detect_site` unit: `test_probe.py::test_detect_site` (the four lookalikes are `generic`; `x.com`, `mobile.twitter.com`, `twitter.com` are `x`). e2e Dropbox: `test_a_host_that_only_ends_in_x_com_is_not_asked_for_cookies_up_front`.
  - `-i --cookies firefox` on X: `test_a_browser_from_the_command_line_skips_the_prompt_before_the_probe`.
  - Moved tests: all pass with `?from=x.com` in the targeted run, spec 18's double prompt and spec 26's missing browser included.
- **Scope:** `git diff 3199a0c^1 3199a0c --stat` touches exactly the five files in the Files table. Nothing in `i18n.py`, `cli.py`, `downloader.py`, `pyproject.toml`, `PKGBUILD`, `README.md`, `AGENTS.md` or the version.

### What changed after approval

- **A fifth developer commit, a pin.** `5b0ea45` (`test(wizard): pin a command-line browser skipping the up-front prompt`) isn't one of the four commits listed under Acceptance criteria. It adds `test_a_browser_from_the_command_line_skips_the_prompt_before_the_probe`, the e2e form of the `-i --cookies firefox` pin: the loop now reads `cookies_browser` on every turn, so it guards against asking anyway. Test only, one case. It passes on the parent, as a pin should.
- **The test-engineer added 39 cases, not 40.** `6f438b6` adds 18 functions. Collected counts from `git archive` copies run against `main`'s `src/`: 1772 (parent) → 1796 (the developer's four fix commits: 24) → 1797 (`5b0ea45`: 1) → 1836 (`6f438b6`: 39). The handoff said 40. By file: `test_e2e.py` 13 functions, 18 cases; `test_probe.py` 3 functions, 19 cases; `test_wizard_flow.py` 2 functions, 2 cases. What they cover: `c` / EOF after one or two backs, a browser picked after backing reaching the probe and the command, a decline after backing staying out of the command, the retry's answer and an unreadable browser (spec 26) in the summary, `Cookies` staying first after `b` at the first step, the manual flow with and without a prompt before it, a `BROWSER+KEYRING` from the command line, an Instagram URL typed after `b`, `-l` surviving two backs, one command per URL's cookies answer, URLs retyped after `b`, and `detect_site` on subdomains, case, ports, userinfo, and names that only contain `x.com`.
- **`fixupx.com`, `vxtwitter.com` and `fxtwitter.com` now read `generic`.** They used to read `x`, since each holds `x.com`, so they lose the banner and the cookies question before the probe. The first two are pinned in `test_detect_site_ignores_names_that_only_contain_x_com`; `fxtwitter.com` was checked by hand. That matches yt-dlp: `TwitterBaseIE._BASE_REGEX` (`twitter.py:38`) accepts only `twitter.com`, `x.com` and the onion host, with `www.`, `m.` or `mobile.`, so these mirrors were never X to yt-dlp either.
- **Some lookalikes still read `x`, as they did before.** Checked with `.venv/bin/python -I -c 'from download_media.probe import detect_site ...'`: `https://my-x.com/a`, `https://x.com.evil.org/a`, `https://x.com@evil.org/a` and `https://www.facebook.com/x.com/videos/1` all read `x` and get the prompt before the probe. `\b` treats `-`, `.`, `@` and `/` as word boundaries, and `detect_site` reads the whole URL by design (the tests' `?from=x.com` depends on it). The `x` pattern is checked before `facebook`, so the last one is `x` too. None of this is a regression: the old pattern matched all four. The worst outcome is one extra question and a wrong banner. Matching on the parsed host would fix it, but it would break the `?from=` trick the e2e tests rely on, so it's out of scope here.

## Open decisions

Resolved by the orchestrator under the human's delegation, each on the recommended option: D1 A, D2 A, D3 A, D4 A. D1 and D2 are on 0.3.0's review list in `PLAN.md`.

Delegated to the roles by the human. The orchestrator approves the recommended option. D1 and D2 change published behaviour, so they're in the list for the human's review before release.

### D1: which sites keep the prompt before the probe

| Option                                           | Effect                                                                                                                                                                                                                       |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Instagram leaves; X and Facebook stay (recommended)** | What the row asks for. Instagram rests on source and live evidence. X and Facebook keep today's flow, with the `_Back` fix and the summary line                                                              |
| B. Remove the prompt for every site              | Phase 1.5, `site_likely_needs_cookies`, `_COOKIE_HUNGRY_SITES` and `auth_needed_pre` go, and the `_Back` fix with them. Less code. But X and Facebook change with only source to go on, and a broken X guest token would mean the manual flow |
| C. Keep Instagram                                | Every public Instagram post keeps an extra question it doesn't need. Fails the row                                                                                                                                         |

**Recommendation: A.** B is worth a planner row once someone runs a public X post and a public Facebook video without cookies.

### D2: fix `detect_site` for `x.com` lookalikes

| Option                            | Effect                                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------------------- |
| **A. Fix it here (recommended)**  | One regex. Dropbox, Box, Fox and Netflix lose a wrong banner and a cookies question they don't need |
| B. A planner row                  | This item stays at three changes, and those links keep the prompt                          |

**Recommendation: A.** It decides who gets the prompt, which is this row's subject, and the change is small and fully measured.

### D3: which cookies answers the summary lists

| Option                                                          | Effect                                                                  |
| --------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **A. Any prompt before the walk: up front and retry (recommended)** | One rule (`cookies_asked`). On Instagram after D1, the retry is the only prompt before the walk |
| B. Up front only                                                | Needs a second flag, and the retry's answer stays hidden, as today      |

**Recommendation: A.**

### D4: what `b` does at the prompt before the probe

| Option                                                       | Effect                                                                                     |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| **A. Back to the URL each time; the new URL decides (recommended)** | `b` means the same at every press. A URL off the list isn't asked                     |
| B. A second `b` means "no cookies"                           | Smallest diff, but `b` changes meaning the second time                                     |
| C. Back to the URL each time, and always ask again           | Asks a Dropbox URL typed after `b` for cookies, which is today's quirk                     |

**Recommendation: A.**
