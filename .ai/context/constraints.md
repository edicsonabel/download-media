# Constraints

They apply to every role. If a role says otherwise, this file wins. `AGENTS.md` holds the conventions (naming, i18n, typing, commit format), and they apply as well.

## Technical

1. **Wrapper, not re-implementation.** If `yt-dlp` or `ffmpeg` can do it, use them. `yt-dlp` is a library here, never a subprocess.
2. **No new runtime dependencies** without explicit human approval. A new one goes in `pyproject.toml`, `PKGBUILD` and `AGENTS.md`.
3. **Every user-facing message** goes through `t()`, in both the `es` and `en` blocks.
4. **Breaking changes** (renamed flags, changed defaults, moved state) need human approval and `BREAKING CHANGE:` in the commit.

## Verification

1. **Evidence, not expectation.** "Should work" is not a report. Every claim names the command that proved it.
2. **The gate:** `scripts/pre-release.sh --no-live` passes before anything moves forward. The full gate, with live tests, passes before any release.
3. **Tests passing is not the same as working.** Mocks can't show how `yt-dlp` behaves: anything that depends on it needs a test on the `fake_site` fixture, and a run of the real command against a real URL when the spec says so.
4. **Check the disk.** After a download, count the files and compare with what the tool reported.
5. **Every fix ships with a test that fails without it.** Prove it by reverting the fix.

## Privacy

1. **Browser cookies only through the tool.** Use `--cookies` or the live tests. Never read a cookie database or a keyring directly, never call `yt-dlp` with the human's cookies outside `download-media`, and never print cookie values.
2. **No personal data in the repo:** no personal URLs, accounts or paths. `.ai/live.env` is gitignored for that reason.
3. **Downloads go to `tmp/`** in development, or to pytest's temp folders.

## Process

1. **Spec first.** Nothing is implemented without an approved spec in `.ai/specs/`.
2. **Mandatory review.** Every item passes through `test-engineer` and `qa-reviewer`.
3. **One worktree per item,** one branch per item, one logical change per commit.
4. **Only the orchestrator writes `.ai/orchestration/state.json`.** Everyone else returns a report.
5. **Commits follow `AGENTS.md`:** Conventional Commits, in English, never with AI attribution.
6. **No `git push`, tag, GitHub release or AUR publication** without human approval. Only `release-manager` does these.
7. **The version is bumped only at release,** in the same commit that gets tagged.
8. **Roles never talk to the human.** They report to the orchestrator, which speaks the human's language.

## Scope

1. **One thing per item.** If it grows, split it.
2. **Nothing outside the spec.** Anything worth doing goes in the report's `notes`, and the planner picks it up.
