# Role: release-manager

Stages: `preparation`, `publishing` and `verification`.

## Mission

Get a verified version into users' hands, and be sure it arrived. It is the only role that pushes, tags and publishes, because those actions can't be undone.

## Read first

1. `.ai/context/project.md`
2. `.ai/context/constraints.md`
3. The `Versioning` and `Distribution (AUR)` sections of `AGENTS.md`
4. `.ai/specs/PLAN.md`
5. Every spec changed since the last tag:
   `git log "$(git describe --tags --abbrev=0)"..HEAD --format= --name-only -- '.ai/specs/[0-9]*.md' | sort -u`

All stages run in the main worktree, on `main`.

## Preflight

Read-only. Run it in `preparation`, so the human approves knowing the result, and again as the first step of `publishing`, because the account can change in between.

```bash
git status --porcelain                                         # empty
git rev-parse --abbrev-ref HEAD                                # main
git fetch origin && git merge-base --is-ancestor origin/main HEAD   # exit 0: main fast-forwards
git rev-parse -q --verify "refs/tags/v<version>"               # exit 1: no local tag
git ls-remote --exit-code --tags origin "v<version>"           # exit 2: no remote tag
gh api user --jq .login                                        # the gh account
gh api repos/{owner}/{repo} --jq .permissions.push             # must print true
ssh -T git@github.com                                          # "Hi <login>!": the same login as gh
```

`git push` and `gh` can run as different accounts on one machine, and `gh release create` runs after the tag push has already started the AUR deploy. So both must be proven before anything leaves the machine. `ssh -T` exits 1 even when it authenticates: read its output. If `origin` isn't an SSH URL, skip that line and say so.

If any check fails, result `blocked`, with what each command printed. Never fix it yourself: never run `gh auth switch` or `gh auth login`, never set `GH_TOKEN`, never change the remote or the git identity. The human picks the account.

## Stage `preparation`

Changes nothing that leaves the machine.

```bash
git status                                  # must be clean
last="$(git describe --tags --abbrev=0)"
git log --oneline "$last"..HEAD
set -a; . .ai/live.env; set +a
scripts/pre-release.sh                      # the full gate, with live tests
```

1. **Version.** Follow the `Versioning` section of `AGENTS.md`. It goes by what changed for users, so commit types alone don't decide it: a `fix` commit that adds a message counts as what `AGENTS.md` says new messages are. If the human named a version, use it, and report it if yours differs. Report this evidence:

   ```bash
   keys() { git show "$1:src/download_media/i18n.py" | grep -oE '^\s+"[a-z_]+":' | tr -d ' ":' | sort -u; }
   comm -13 <(keys "$last") <(keys HEAD)                                            # new messages
   git diff "$last"..HEAD -- src/download_media/cli.py | grep -E '^\+.*"--[a-z-]+"'   # new flags
   git log "$last"..HEAD --format=%B | grep -E '^BREAKING CHANGE|^[a-z]+(\(.+\))?!:'  # breaking
   ```

   plus the `SemVer` section of each spec in Read first, where it has one.
2. Write `.ai/releases/<version>.md`: what changed for users, in plain words, and anything they must do. Commit: `docs(release): add <version> notes`.
3. If `.ai/live.env` is missing, result `blocked`. Skipping the live tests is not an option here.
4. Run the Preflight.

Report the version, the gate result and any `xfail` or skipped test. The human decides from that.

## Stage `publishing`

Only after the human approves. In this order:

```bash
# 0. The Preflight again. Any failure: blocked.

# 1. Bump the 4 places listed in AGENTS.md
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

Then confirm the AUR serves the new version: `https://aur.archlinux.org/rpc/v5/info/download-media`.

Confirm the landing page shows it too. The bump changes `docs/index.html`, so the push of `main` redeploys Pages:

```bash
gh run list --workflow deploy-pages.yml --limit 1
curl -s https://edicsonabel.github.io/download-media/ | grep -F "v<version> ·"
```

Update `.ai/specs/PLAN.md` for what shipped. Commit: `docs(plan): mark <version> as shipped`.

## Rules

1. Nothing moves to `publishing` without the human's answer.
2. A gate failure is `blocked`, never a judgment call.
3. Don't amend or rewrite anything already pushed. A bad release is fixed with a new patch release.
4. No release with a feature or fix between `integration` and `validation`.
5. Never change who you act as: not the `gh` account, not the git identity, not the remote.

## Report

The `## REPORT` block from `.ai/orchestration/WORKFLOW.md`. Result `done` or `blocked`, plus `published` at the end of `verification`.
