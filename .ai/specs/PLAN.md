# Plan

The roadmap, owned by the planner (`/roadmap`). The architect turns a row into a spec (`NN-<id>.md`) when the human runs `/feature <id>` or `/fix <id>`.

## Statuses

`backlog` → `spec` → `in progress` → `done` → `shipped <version>` · `dropped`

Priority: `P1` broken or misleading today · `P2` clear value · `P3` nice to have. Size: `S` · `M` · `L`.

## Backlog

| NN  | Id                   | Type  | Item                                                                     | Why                                                                                     | Pri | Size | Status  |
| --- | -------------------- | ----- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- | --- | ---- | ------- |
| 01  | `instagram-images`   | feat  | Save carousel images, if `yt-dlp` metadata exposes their URLs            | A real post lost 16 of 20 items; needs checking against the wrapper rule                | P2  | M    | backlog |
| 02  | `i18n-leftovers`     | fix   | Move hardcoded text to `t()`: `cli.py` hints, `[no soportado]` in wizard | Breaks the es/en rule: English hints for Spanish users, and the reverse                 | P2  | S    | backlog |
| 03  | `ci-on-push`         | ci    | Run `pre-release.sh --no-live` on every push and PR                      | Tests only run on tags today, so a failure shows up at release time                     | P2  | S    | done    |
| 04  | `stale-docs`         | docs  | Update `CONTRIBUTING.md` and `docs/index.html`                           | They still describe the Bash prototype (`bin/download-media`, "Bash wrapper")           | P2  | S    | backlog |
| 05  | `format-baseline`    | style | Format the 8 files `ruff format` flags, then enforce it in the gate      | `AGENTS.md` requires formatting and the gate can't check it yet                         | P3  | S    | backlog |
| 06  | `wizard-ig-cookies`  | fix   | Stop asking for cookies up front on Instagram                            | With `curl_cffi`, public posts download without them                                    | P3  | S    | backlog |
| 07  | `keyring-from-proc`  | feat  | Also detect `--password-store` from the running browser                  | Flags set in a `.desktop` file or alias aren't found; `+keyring` is the workaround      | P3  | S    | backlog |
| 08  | `single-broken-item` | fix   | Decide whether single mode should continue past a broken carousel item   | `-p` continues and `-y` stops, which is hard to justify to a user                       | P3  | S    | backlog |

Rows are added at the end. Finished rows stay: they are the record.
