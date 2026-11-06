# 09 — Make the equivalent command repeat the run

Status: draft
Item: `fix/equivalent-direct`

## Problem

The command printed after a run doesn't always repeat that run: on all defaults it reopens the wizard, and on other paths it silently repeats something else.

Reproduced on `main` at `561196f`, with the real console script against a loopback server, and `HOME` and `XDG_*` pointed under `tmp/`:

```bash
python -m http.server 18765 --bind 127.0.0.1 --directory <folder with tiny.mp4> &
printf '\n\n\n\n\n\n\n\n' | download-media http://127.0.0.1:18765/tiny.mp4
# prints: download-media http://127.0.0.1:18765/tiny.mp4
download-media http://127.0.0.1:18765/tiny.mp4 </dev/null
# prints: "Download wizard", the URL prompt, "Cancelled."
```

- **What happened:** the printed command opened the wizard. `should_run_wizard` is `True` for it.
- **What should happen:** pasting the command repeats the download, as `AGENTS.md`, `README.md` and `docs/index.html` promise.

## Goal

Whatever the path, the printed command repeats what actually ran, without the wizard, and nothing is printed when there is nothing to repeat.

## Findings

Each one comes from running `cli.main` with the real yt-dlp and ffmpeg on the `fake_site` fixture, then parsing the printed line with `shlex.split` and replaying it. The gate passes on `main` before the change: `scripts/pre-release.sh --no-live`, 324 passed in `es` and in `en`.

| #   | Run                                                       | Printed                       | On replay                                                                           |
| --- | --------------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------- |
| F1  | Wizard, all defaults                                      | `download-media <url>`        | Wizard opens                                                                        |
| F1  | `--cookies brave -P <url>`, cookies dropped by the retry  | `download-media <url>`        | Wizard opens. Same for any run whose flags all get omitted                          |
| F2  | `-y -o <dir> <url>`                                       | no `-y`                       | Asks for confirmation; the qa-reviewer's note, confirmed                            |
| F3  | `-y -s --sub-langs fr,de <url>`                           | `--sub-langs fr,de`, no `-s`  | `subs=False`: **no subtitles**. `--sub-langs` alone doesn't enable them             |
| F4  | `-s --sub-langs 'fr, de' <url>`                           | `--sub-langs fr, de` unquoted | `de` becomes a URL. The only free-text value `build` doesn't quote                  |
| F5  | Wizard, carousel, item 2 picked: 1 file                   | `-p <url>`                    | **2 files.** The selection is lost, and no flag can express it                      |
| F6  | Direct, playlist-like URL, prompt answered "all"          | no `-p`                       | Asks again. `main` prints `opts`, whose mode is still `auto`; only `effective` knew |
| F6  | Same, answered "single"                                   | no `-P`                       | Asks again                                                                          |
| F7  | Direct, two URLs, one "all" and one plain                 | one command, no flag          | No single flag fits: `-p` on a plain URL saves `NA - Clip [v1].mp4`, not `Clip.mp4` |
| F8  | Direct, preview declined (exit 1)                         | Full command                  | Offers what the user just refused                                                   |
| F8  | Direct, two URLs, first declined                          | Both URLs                     | Includes the declined URL                                                           |
| F8  | Wizard cancelled (`cli.py:346-348`, exit 0)               | Nothing                       | Contradicts `AGENTS.md`                                                             |

Paths that already work, and must stay that way:

- URLs and `-o` paths with spaces, `&`, `'` and `$` round-trip through `shlex.split`.
- `--cookies brave+kwallet6` survives. The value is validated by `parse_spec`, so it needs no quoting.
- The cookie-drop logic works, and so does the wizard's Enter default on a carousel: it stores `2,4`, and `-p` saves the same two files.
- `-i` prints like the wizard, and `-i -y` is `-y`, because `should_run_wizard` checks `no_confirm` first.

`-l` returns at `cli.py:365` before the print, and prints no command. That's right: the message says "repeat this download", and `-l` downloads nothing.

`no_confirm` is read in only three places: `should_run_wizard` (`cli.py:146`), the playlist question (`:213`) and the preview (`:248`). So `-y` means exactly "no wizard, no single/all question, no preview".

## Scope

### In

- Keep the printed command direct on every path (F1), and carry `-y` (F2).
- Always emit `-s` when subtitles are on, and quote `--sub-langs` (F3, F4).
- Print the mode each URL really used, one command per mode when they differ (F6, F7).
- Print only URLs that reached the download, and nothing if none did (F8).
- Repeat a wizard item selection (F5): see D3.
- Align `AGENTS.md`, `README.md` and `docs/index.html` with the result.

### Out

For the planner, all reproduced except where marked:

- **`-l <url>` opens the wizard:** `-l` isn't in `_INTENT_FLAGS`, so the user walks every step only to list formats.
- **`-l` with several URLs lists only the first,** silently.
- **The wizard silently drops every URL after the first:** `download-media <a> <b>` saves only `<a>`. The printed command is faithful to that run, but the run is wrong.
- **A wizard folder of `~/x` creates a literal `~` directory.** The printed `-o '~/x'` faithfully repeats it.
- **`--sub-langs` doesn't imply `-s`,** although `README.md:14` reads as if it did. Changing that alters a flag's behavior, so here the command just prints both.
- **Cancelling exits 0 in the wizard and 1 at the preview.** `test_direct_mode_preview_can_be_declined` pins the 1.
- **Ctrl-C escapes `main` as a `KeyboardInterrupt`,** so nothing is printed. Unchanged here.
- **An output folder starting with `-`:** the printed `-o -x` is rejected by argparse (exit 2). Rare, and only reachable by typing it.
- **A relative `-o` stays relative,** so it only repeats from the same folder. Not run, read in `equivalent.py:40`; it is what any shell command does.

## Files

| Action | File                               |
| ------ | ---------------------------------- |
| modify | `src/download_media/equivalent.py` |
| modify | `src/download_media/cli.py`        |
| modify | `tests/test_equivalent.py`         |
| modify | `tests/test_cli.py`                |
| modify | `tests/test_e2e.py`                |
| modify | `AGENTS.md`                        |
| modify | `README.md`                        |
| modify | `docs/index.html`                  |

No new runtime dependency. No new message under the recommended options.

## Design

Written for the recommended option of each decision. Each decision says what changes if the human picks otherwise.

### `equivalent.py`

- `parts` starts as `["download-media", "-y"]` (D1). Update the module docstring, which still says only "wizard run".
- Subtitles: append `-s` whenever `opts.subs`, then `--sub-langs <shlex.quote(value)>` if it isn't the default.
- Playlist: with mode `yes`, emit `--playlist-items <shlex.quote(items)>` if `opts.playlist_items` is non-empty, otherwise `-p` (D3). An empty string means the wizard's `all`.
- Keep the `-P` branch as it is, and reword its comment: under `-y` it no longer prevents a question, but it records the decision on a URL that looks multi-entry. `test_no_playlist_flag_kept_for_instagram_posts` stays valid.

### `cli.py`

`_download_one` returns a `NamedTuple`:

```python
class _Outcome(NamedTuple):
    ok: bool
    cookies_dropped: bool
    mode: PlaylistMode | None  # None: cancelled before the download started
```

Both cancel returns give `mode=None`. Every other return, failures included, gives the resolved `pmode`.

A helper replaces `cli.py:374-383`:

1. Keep the URLs whose `mode` isn't `None`. If there are none, print nothing.
2. Apply the cookie-drop rule over those URLs only.
3. Group them by mode, in first-seen order.
4. Print the `equiv_cmd` header once, then one `equivalent.build(replace(shown, urls=group, playlist_mode=mode))` per group.

`exit_code` doesn't change. The wizard cancel at `cli.py:346-348` doesn't change either.

### `--playlist-items` (D3)

yt-dlp's parameter is `playlist_items` (`YoutubeDL.py:299`), which `downloader.py:65-66` already passes. The name mirrors yt-dlp's `--playlist-items`. It's long-only, because `-I` next to `-i` invites typos.

- Add it to the existing `-p`/`-P` mutually exclusive group, with `dest="playlist_items"`, and to `_INTENT_FLAGS`.
- `options_from_args` passes it through and sets the mode to `yes`.
- `_resolve_playlist_for_direct_mode` already returns the items when the mode isn't `auto`, so the rest of the path needs nothing.
- An invalid spec needs no new handling. Confirmed: `'x'` reaches `_download_one` as a `DownloadError`, raised from `parse_playlist_items` (`utils/_utils.py:2452`).
- **An out-of-range spec needs a guard.** Confirmed: `playlist_items="9"` on a 4-item carousel returns `DownloadResult(0, 0, 0)`, and `_download_one` reports success with no files. The wizard can't reach this, but the flag can. Change `cli.py:277` to `if not result.saved:`, reusing `no_supported_items`.
- The guard doesn't affect repeats. Confirmed: a second run on the same folder still gives `saved=1` for a single video and `saved=2` for a carousel, with and without items.

### Docs

`AGENTS.md:122-124` becomes:

> After every run that reached the download, whether it worked or not, the equivalent direct-mode command is printed (`equivalent.py`), so the user can repeat it without the wizard or any prompt. It always carries `-y`, and otherwise omits flags whose values match the defaults. URLs that ended in different playlist modes get one command each. Nothing is printed when the run was cancelled before downloading: a cancelled wizard only holds half-made choices.

Also in `AGENTS.md`: add `--playlist-items` to the intent flags under "Execution modes".

`README.md:20` and `docs/index.html:349` say "after every wizard run", but direct runs print it too. Reword both to "after every download". `README.md` also gets `--playlist-items` in its flag list.

## Cases

| Case                                     | Expected                                                                                    |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- |
| wizard, single video, all defaults       | `download-media -y <url>`; replay saves the same file, no prompt                            |
| wizard, probe failed (mode stays `auto`) | Prints the resolved mode                                                                    |
| wizard, carousel, Enter                  | `-y --playlist-items 2,4 <url>`; replay saves the same files                                |
| wizard, carousel, `all`                  | `-y -p <url>`                                                                               |
| wizard, carousel, subset                 | `-y --playlist-items <n> <url>`; replay saves the same subset                               |
| wizard cancelled, or nothing supported   | No command, exit 0, as today                                                                |
| `-i`                                     | As the wizard; no `-i` in the command                                                       |
| `-i --playlist-items N`                  | The wizard's selection step replaces it, like its other prompts                             |
| direct, plain URL                        | `-y` plus the non-default flags                                                             |
| direct, prompt answered "all"            | `-y -p`                                                                                     |
| direct, prompt answered "single"         | `-y -P`                                                                                     |
| direct, preview declined                 | No command, exit 1                                                                          |
| `-y`, playlist-like URL                  | `-y -P`: `-P` is new, and it's what ran                                                     |
| `-p`, `-P`                               | Kept, plus `-y`                                                                             |
| `-l`                                     | No command, as today                                                                        |
| with cookies, `+keyring`                 | `--cookies <spec>`, unchanged                                                               |
| cookies dropped on every attempted URL   | No `--cookies`; stays direct through `-y`                                                   |
| several URLs, same mode                  | One command                                                                                 |
| several URLs, different modes            | One command per mode, one header                                                            |
| several URLs, one declined               | The command leaves that URL out                                                             |
| partial failure                          | Printed, as today                                                                           |
| total failure                            | Printed, exit 1, as today: a failed run's choices are worth retrying                        |

Carousels and playlists follow the same rows, since `build` only sees the mode and the items. Rerun: the replay criteria cover it.

## Acceptance criteria

Every behavior below needs a test that fails on `main`, shown by reverting the fix (`constraints.md`, Verification 5). A replay test parses the printed line with `shlex.split`, swaps the output folder for an empty one, and calls `cli.main` with no scripted `answers()`, so any prompt fails the test. The all-defaults and cookie-drop cases must not use the `run` fixture: it always adds `-o`, which hides F1. Point `HOME` at `tmp_path` and call `cli.main` directly.

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] `build(DownloadOptions(urls=["https://example.com/v"]))` is `download-media -y https://example.com/v`
- [ ] For every line printed in the replay tests, `should_run_wizard` is `False`
- [ ] Replay, wizard on all defaults: same file names, no prompt
- [ ] Replay, `--cookies brave -P https://fake.test/rejects-cookies` (cookies dropped): no `--cookies`, no prompt
- [ ] `-y` appears in the line printed by a `-y` run
- [ ] `build` with `subs=True, sub_langs="fr,de"` parses back to `subs is True` and `sub_langs == "fr,de"`
- [ ] `build` with `sub_langs="fr, de"` parses back to one URL and `sub_langs == "fr, de"`
- [ ] Direct on `https://fake.test/carousel?list=1`, answer `2`: the line has `-p`, and replay saves `02 - Clip [v1].mp4` and `04 - Clip [v2].mp4`
- [ ] Same URL, answer `1`: the line has `-P`, and replay saves `Clip [v1].mp4` and `Clip [v2].mp4`
- [ ] Direct on that URL plus `https://fake.test/video`, answers `2` and Enter: one header, two commands, and replaying both gives the same files as the run
- [ ] Wizard on `https://fake.test/carousel`, item `2`: replay saves only `02 - Clip [v1].mp4`
- [ ] Wizard on that carousel, Enter: replay saves the same two files as the run
- [ ] `--playlist-items 2 https://fake.test/carousel` skips the wizard, and `-P --playlist-items 2` exits 2
- [ ] `-y --playlist-items 9 https://fake.test/carousel`: exit 1, `t("done")` not in the output, no history row
- [ ] `-y --playlist-items x https://fake.test/carousel`: exit 1, `t("failed", url)` in the output, no traceback
- [ ] Preview declined: `t("equiv_cmd")` not in the output, exit still 1
- [ ] Two URLs, first declined: the printed line holds only the second
- [ ] Wizard cancelled: `t("equiv_cmd")` not in the output, exit still 0
- [ ] `-y -l <url>`: `t("equiv_cmd")` not in the output
- [ ] Total failure: the command is printed, exit 1
- [ ] A URL and folder with spaces, `&`, `'` and `$` round-trip through `shlex.split`
- [ ] The reproduction under Problem, run with the fix, prints a command that downloads with stdin closed

Existing tests that must change, and why:

| Test                                                | Change                                                                 |
| --------------------------------------------------- | ---------------------------------------------------------------------- |
| `test_minimal_video_emits_only_url` (rename it)     | Pins `download-media <url>`, the bug itself                            |
| `test_audio_only_with_default_format`               | Exact match; now has `-y`                                              |
| `test_subs_with_custom_langs_uses_long_form`        | Pins `" -s " not in cmd`, which is F3                                  |
| `tests/test_cli.py:128`, `:203`, `:213`, `:270`     | Fake or compare the 2-tuple `_download_one` returned; now an `_Outcome` |

Commits: `--playlist-items` and its guard are a `feat(cli):` of their own; the rest is `fix`.

**SemVer:** not breaking. No flag is renamed, no default changes, no on-disk state moves, and those are the `AGENTS.md` triggers; the printed line is a hint with no stable format. On its own the fix is a patch. With D3 it's a minor, for a new flag, or for a new message under D3 option B. The next release is already planned as 0.3.0, so this moves nothing. No version bump in this item.

## Live check

Not needed. No extractor, format or network behavior changes: the change is which string gets printed and which URLs it lists. The one yt-dlp parameter involved already ships, and the replay criteria run the real yt-dlp and ffmpeg on `fake_site`.

## Open decisions

### D1: what keeps the command direct

Measured by replaying each flag on `fake_site` with a plain URL, and on a playlist-like URL still in `auto` mode:

| Option                        | Wizard | Prompts (plain / playlist-like) | yt-dlp instances (plain) | Cost                                                                              |
| ----------------------------- | ------ | ------------------------------- | ------------------------ | --------------------------------------------------------------------------------- |
| **A. Always `-y`**            | no     | 0 / 0                           | 1                        | Pasted command starts at once. Rewords the `AGENTS.md` rule                       |
| B. `-q <default>` when needed | no     | 1 / 2                           | 2                        | Redundant flag; pins the quality; rewords the rule. `-y` still needed for F2      |
| C. `-P` when needed           | no     | 0 / 0                           | 1                        | Same result as `-y`, but reads wrong on a plain URL; breaks a current test's rule |
| D. `-o <folder>` when needed  | no     | 1 / 2                           | 2                        | Long, machine-specific path in the output                                         |
| E. New `--direct` flag        | no     | 1 / 2 (expected, not measured)  | 2 (expected)             | New public flag for what `-y` and `-q` already do                                 |

`--video-format mp4` measured the same as B. Audio never needs any of these: `-a` is already an intent flag.

**Recommended: A.** It's one rule with no conditions, it fixes F1 and F2 together, and it makes `docs/index.html` true ("repeat without prompts"). `-y` isn't a value matching a default, so the `AGENTS.md` rule only needs the short addition under Docs. Every other option also needs that rewording, except E.

If the human picks B: `parts` gets `-q <quality>` only when no other intent flag was emitted, and `-y` only when `opts.no_confirm`. The replay criteria then script the preview answer.

### D2: should the preview and confirmation survive in the repeated command?

**Recommended: no,** and it follows from D1 (A or C drop it; B or D keep it).

- Survival is already accidental: `-p` and `-P` skip the preview today, so it only ever appears for plain URLs. After the F6 fix, B would keep it on plain URLs only.
- The printed command only ever covers URLs the user already confirmed, or ran with `-y`. That's guaranteed by D4.
- Keeping it costs a second extraction per URL (2 yt-dlp instances against 1) to re-ask a settled question.

The argument for keeping it: a command pasted days later shows what it's about to fetch. If that matters more to the human, the answer is D1 option B.

### D3: wizard item selection (F5)

No flag can express a selection today, and under D1 option A a subset run printing `-y -p` would fetch everything without asking: 500 items after picking 3.

| Option                                         | Result                                                                                   | Cost                                                                                                                                                                                       |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A. Add `--playlist-items` here**             | Every selection repeats                                                                  | New public flag in a `fix` item; minor; needs the `saved` guard                                                                                                                            |
| B. Print a note instead of a command on subsets | Honest, but the run can't be repeated. Flag becomes a later row                          | New es/en message, also a minor. Needs a new field set in `wizard.py`: Enter stores `2,4` as well, so `playlist_items` alone can't tell a subset from the common default, which repeats fine |
| C. Leave it                                    | The tool keeps printing a command that does something else, now without any confirmation | Not recommended                                                                                                                                                                            |

**Recommended: A.** It's about the same size as B and it's the only one that meets the goal. It exposes what yt-dlp already does, so it fits the wrapper rule. It does stretch "one thing per item", so it's the human's call. If B: drop the `--playlist-items` design, criteria and `AGENTS.md`/`README.md` additions, add `wizard.py` to Files, and keep the guard out.

### D4: cancelled runs

**Recommended: `AGENTS.md` is wrong about the wizard, and the code is wrong about direct mode.** Print only what reached the download.

- **Wizard, keep silent.** `wizard.run` returns `None`, but `opts` is mutated in place, so `main` could print. Cancelling at the container step leaves `quality='720'`, `output=None` and `playlist_mode='no'`: a mix of real answers and steps never reached. That command would be something the user never chose, and with `-y` it would run unconfirmed. Cancelling at the URL step can leave no URL, and `image_only_skipped` and `no_supported_items` also return `None`, with nothing to repeat.
- **Direct, stop printing.** The user typed the command and just refused it, so it adds nothing, and with `-y` it would turn a "no" into a run with no question. With two URLs it also lists the declined one (F8).
- The Bash prototype already exited silently from a cancelled wizard (`legacy/bin/download-media:547-550`), and only its direct path reached the print. `README.md` and `docs/index.html` never claimed cancelled runs print.

The alternative is to make only `AGENTS.md` match the code: direct cancels keep printing. That needs no `cli.py` change for F8, but under D1 option A it prints a `-y` command for a download the user declined.

### D5: mixed playlist modes (F7)

**Recommended: one command per mode.** It's a few lines in the helper and needs no new message. The alternative is to keep one command and accept that it re-asks, which needs a direct marker other than `-y` and contradicts D1 option A.
