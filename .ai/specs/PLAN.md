# Plan

The roadmap, owned by the planner (`/roadmap`). The architect turns a row into a spec (`NN-<id>.md`) when the human runs `/feature <id>` or `/fix <id>`.

## Statuses

`backlog` → `spec` → `in progress` → `done` → `shipped <version>` · `dropped`

Priority: `P1` broken or misleading today · `P2` clear value · `P3` nice to have. Size: `S` · `M` · `L`.

## Backlog

| NN  | Id                   | Type  | Item                                                                                                                 | Why                                                                                                                    | Pri | Size | Status        |
| --- | -------------------- | ----- | -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | --- | ---- | ------------- |
| 01  | `instagram-images`   | feat  | Save carousel images, if `yt-dlp` metadata exposes their URLs                                                        | A real post lost 16 of 20 items; needs checking against the wrapper rule                                               | P2  | M    | backlog       |
| 02  | `i18n-leftovers`     | fix   | Move hardcoded text to `t()`: `cli.py` hints, `[no soportado]` in wizard                                             | Breaks the es/en rule: English hints for Spanish users, and the reverse                                                | P2  | S    | backlog       |
| 03  | `ci-on-push`         | ci    | Run `pre-release.sh --no-live` on every push and PR                                                                  | Tests only run on tags today, so a failure shows up at release time                                                    | P2  | S    | shipped 0.2.0 |
| 04  | `stale-docs`         | docs  | Update `CONTRIBUTING.md` and `docs/index.html`                                                                       | They still describe the Bash prototype (`bin/download-media`, "Bash wrapper")                                          | P2  | S    | backlog       |
| 05  | `format-baseline`    | style | Format the 8 files `ruff format` flags, then enforce it in the gate                                                  | `AGENTS.md` requires formatting and the gate can't check it yet                                                        | P3  | S    | backlog       |
| 06  | `wizard-ig-cookies`  | fix   | Stop asking for cookies up front on Instagram                                                                        | With `curl_cffi`, public posts download without them                                                                   | P3  | S    | backlog       |
| 07  | `keyring-from-proc`  | feat  | Also detect `--password-store` from the running browser                                                              | Flags set in a `.desktop` file or alias aren't found; `+keyring` is the workaround                                     | P3  | S    | backlog       |
| 08  | `single-broken-item` | fix   | Decide whether single mode should continue past a broken carousel item                                               | `-p` continues and `-y` stops, which is hard to justify to a user                                                      | P3  | S    | backlog       |
| 09  | `equivalent-direct`  | fix   | Make the printed equivalent command stay direct, and decide on `-y`                                                  | `equivalent.py` prints `download-media <url>` on all defaults, which reopens the wizard                                | P1  | S    | done          |
| 10  | `workflow-pins`      | ci    | Bump Node 20 actions, set `timeout-minutes` in both deploy workflows, pin the runner image                           | GitHub forces them onto Node 24 (v0.2.0 run); `ubuntu-latest` moved to 26.04 unasked; only `ci.yml` has a timeout      | P2  | S    | backlog       |
| 11  | `python-floor`       | build | Raise `requires-python` to 3.11, matching `yt-dlp`                                                                   | `yt-dlp` 2026.10.26 needs 3.11: 3.10 is stuck on 2026.10.21 and fails 2 e2e tests                                      | P3  | S    | backlog       |
| 12  | `gate-exit-guard`    | test  | Test that `pre-release.sh` exits non-zero when a step fails                                                          | Without `set -e` every failing step exits 0 and prints "All checks passed."                                            | P3  | S    | backlog       |
| 13  | `ci-pr-proof`        | ci    | Prove `ci.yml` on a throwaway red PR, then decide on a required check                                                | Only a `main` push has run; a required check now is bypassed or blocks the release push                                | P3  | S    | backlog       |
| 14  | `criteria-wording`   | docs  | Warn in `_TEMPLATE.md` about criteria that stop working after the merge                                              | `git diff main` is empty at validation; `gh run list --commit` needs the full SHA                                      | P3  | S    | backlog       |
| 15  | `release-runbook`    | docs  | Fix `release-manager.md`: align the version rule with `AGENTS.md`, check `gh` write access first, expect AUR RPC lag | Its rule implies 0.1.1 for 0.2.0; a read-only `gh` was caught only by hand; the RPC trailed the AUR git by 4.5 minutes | P2  | S    | backlog       |
| 16  | `aur-build-check`    | ci    | Build the package on Arch and smoke-test it before the AUR push                                                      | `deploy-aur.yml` only runs `makepkg --printsrcinfo`, and the gate's `curl_cffi` 0.17.0 isn't Arch's 0.16.3             | P3  | S    | backlog       |

Rows are added at the end. Finished rows stay: they are the record.

## Next release

**0.2.0 shipped on 2026-11-06.** It carried the keyring, carousel, `-p` and `-l` fixes, `curl_cffi`, and `ci-on-push`. It was a minor bump because `AGENTS.md` counts new messages as minor: there were 4 new ones, and `--cookies` now takes `BROWSER[+KEYRING]`. Nothing was breaking.

**Next:** `equivalent-direct` first: it is the only `P1`, and it still reproduces on `main`. Then `i18n-leftovers`, `workflow-pins` and `release-runbook`. That is 0.3.0, since `i18n-leftovers` adds messages. `release-runbook` has to merge before `/release`: none of these is a `feat`, so the current rule in `release-manager.md` would call it 0.2.1.
