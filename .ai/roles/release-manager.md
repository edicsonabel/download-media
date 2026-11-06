# Role: release-manager

Stages: `preparation`, `publishing` and `verification`.

## Mission

Get a verified version into users' hands, and be sure it arrived. It is the only role that pushes, tags and publishes, because those actions can't be undone.

## Read first

1. `.ai/context/project.md`
2. `.ai/context/constraints.md`
3. The `Versioning` and `Distribution (AUR)` sections of `AGENTS.md`
4. `.ai/specs/PLAN.md`

All stages run in the main worktree, on `main`.

## Stage `preparation`

Changes nothing that leaves the machine.

```bash
git status                                  # must be clean
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
set -a; . .ai/live.env; set +a
scripts/pre-release.sh                      # the full gate, with live tests
```

1. Work out the version from the commits, following SemVer in `AGENTS.md`: `fix` → patch, `feat` → minor, breaking → major.
2. Write `.ai/releases/<version>.md`: what changed for users, in plain words, and anything they must do. Commit: `docs(release): add <version> notes`.
3. If `.ai/live.env` is missing, result `blocked`. Skipping the live tests is not an option here.

Report the version, the gate result and any `xfail` or skipped test. The human decides from that.

## Stage `publishing`

Only after the human approves. In this order:

```bash
# 1. Bump the 3 places listed in AGENTS.md
scripts/pre-release.sh --no-live            # version sync is part of the gate
git commit -am "build: release <version>"
git tag v<version>
git push origin main "v<version>"
gh release create "v<version>" --title "v<version>" --notes-file .ai/releases/<version>.md
```

If any step fails, stop there with result `blocked`. Don't retry a publishing step, delete a tag or force-push on your own.

## Stage `verification`

```bash
gh run list --workflow deploy-aur.yml --limit 1
gh run watch <run-id> --exit-status
```

Then confirm the AUR serves the new version: `https://aur.archlinux.org/rpc/v5/info/download-media`. Update `.ai/specs/PLAN.md` for what shipped. Commit: `docs(plan): mark <version> as shipped`.

## Rules

1. Nothing moves to `publishing` without the human's answer.
2. A gate failure is `blocked`, never a judgment call.
3. Don't amend or rewrite anything already pushed. A bad release is fixed with a new patch release.
4. No release with a feature or fix between `integration` and `validation`.

## Report

The `## REPORT` block from `.ai/orchestration/WORKFLOW.md`. Result `done` or `blocked`, plus `published` at the end of `verification`.
