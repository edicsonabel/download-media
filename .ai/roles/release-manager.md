# Role: release-manager

Stages: `preparation`, `publishing` and `verification`.

## Mission

Get a verified version into users' hands, and be sure it arrived. It is the only role that pushes, tags and publishes, because those actions can't be undone.

## Read first

1. `.ai/context/project.md`
2. `.ai/context/constraints.md`
3. The `Versioning` and `Distribution (AUR)` sections of `AGENTS.md`
4. `.ai/specs/PLAN.md`, its "Next release" section above all.
5. Every spec changed since the last tag:
   `git log "$(git describe --tags --abbrev=0)"..HEAD --format= --name-only -- '.ai/specs/[0-9]*.md' | sort -u`

All stages run in the main worktree, on `main`.

Each Bash call starts a fresh shell: a variable or function set in one call is gone in the next. So each code block below runs as one call, and sets everything it uses. The exception is a block with a `<run-id>` or `<job-id>`: run it line by line, filling those in from the line before; such blocks use no variables. A call shows only its last exit code, so a line whose exit code is the answer prints it.

A call times out after 2 minutes unless you raise it, and never lasts more than 10 (600000 ms). The gate (about 5 minutes without the live tests), `gh run watch` and the RPC poll take longer than 2: run them with the call's timeout raised to 600000 ms, or in the background and wait for the completion notice. The full gate, with live tests, always goes in the background. A command cut off by the timeout says nothing about what it ran or watched: run it again, in the background.

## Preflight

Read-only. Run it in `preparation`, so the human approves knowing the result, and again as the first step of `publishing`, because the account can change in between.

```bash
git status --porcelain                                         # empty
git rev-parse --abbrev-ref HEAD                                # main
git fetch origin && git merge-base --is-ancestor origin/main HEAD; echo "fast-forward: exit $?"   # 0
git rev-parse -q --verify "refs/tags/v<version>"; echo "local tag: exit $?"     # 1: no local tag
git ls-remote --exit-code --tags origin "v<version>"; echo "remote tag: exit $?"   # 2: no remote tag
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
if test -f .ai/live.env; then
  set -a; . .ai/live.env; set +a
  scripts/pre-release.sh                    # the full gate, with live tests
else
  echo "no .ai/live.env"; false             # result blocked (step 3)
fi
```

1. **Version.** Follow the `Versioning` section of `AGENTS.md`. It goes by what changed for users, so commit types alone don't decide it: a `fix` commit that adds a message counts as what `AGENTS.md` says new messages are. If the human named a version, use it, and report it if yours differs. Report this evidence:

   ```bash
   last="$(git describe --tags --abbrev=0)"
   keys() { git show "${1}:src/download_media/i18n.py" | grep -oE '^\s+"[a-z0-9_]+":' | tr -d ' ":' | sort -u; }
   flags() { git show "${1}:src/download_media/cli.py" | grep -oE '"--[a-z][a-z-]*"' | tr -d '"' | sort -u; }
   echo "since $last"
   comm -13 <(keys "$last") <(keys HEAD)                                            # new messages
   comm -13 <(flags "$last") <(flags HEAD)                                          # new flags
   git log "$last"..HEAD --format=%B | grep -E '^BREAKING CHANGE|^[a-z]+(\(.+\))?!:' || echo "no breaking marker"
   ```

   If `since` names no tag, the evidence is empty, not zero: result `blocked`. Add the `SemVer` section of each spec in Read first, where it has one.
2. Write `.ai/releases/<version>.md`: what changed for users, in plain words, and anything they must do. Commit: `docs(release): add <version> notes`.
3. If `.ai/live.env` is missing, the block above prints `no .ai/live.env` and exits 1, with nothing sourced and no gate run: result `blocked`. Skipping the live tests is not an option here.
4. Run the Preflight.
5. Build the **pre-publish checklist** and put it in the report, one line per entry, each marked done, not run or pending:
   - **Gate:** the full gate's result, every `xfail`, every skipped test. A skipped live test is a live step not run.
   - **Live steps:** every step of a `Live check` marked skipped or partial in the specs from Read first, and every live item of `PLAN.md`'s "Before `/release`". Run them now, one at a time (Instagram rate-limits by IP), into `tmp/`, through `download-media` only, and compare what's on disk with what was printed. A step whose `DM_LIVE_*` variable isn't in `.ai/live.env` stays not run.
   - **Release steps:** unchecked criteria in those specs that wait for the release, such as spec 10's tag-run check. They're checked in `verification`. Any other unchecked criterion is listed too, as not run.
   - **Delegated decisions:** every decision the human delegated to a role since the last tag, with its spec and id, as `PLAN.md` and the specs' Open decisions list them. You don't judge them; the human reviews them.
   - **Preflight:** the `gh` login, `permissions.push`, the SSH login.

Report the version and its evidence, the gate result and the checklist. The human decides from that.

## Stage `publishing`

Starts only when the orchestrator's message says the human (a) approved publishing `<version>`, (b) reviewed the delegated decisions of the checklist, and (c) for every live step not run, waived it by name. If any of the three is missing, result `blocked`, nothing done.

In this order:

```bash
# 0. The Preflight again. Any failure: blocked.

# 1. Push what's waiting on main, and let CI prove it before anything is tagged
git push origin main
gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" --json databaseId,conclusion
gh run watch <run-id> --exit-status

# 2. Bump the 4 places listed in AGENTS.md
scripts/pre-release.sh --no-live            # version sync is part of the gate
git commit -am "build: release <version>"
git tag v<version>

# 3. Both refs, or neither
git push --atomic origin main "v<version>"

# 4. The release, on the tag just pushed, never a new tag
gh release create "v<version>" --verify-tag --title "v<version>" --notes-file .ai/releases/<version>.md
```

- Step 1: the run can take a few seconds to appear after the push; list again. If `HEAD` was already on `origin`, its existing run counts. A red run is `blocked`: nothing is tagged, and the fix goes through the pipeline.
- Step 4: if it fails, the tag is out and the AUR deploy runs anyway, since it downloads the tag's archive, not a release asset. Result `blocked`; the human decides whether to run step 4 again.
- If any step fails, stop there with result `blocked`. Don't retry a publishing step, delete or move a tag, or force-push on your own.

## Stage `verification`

```bash
# The tag run, picked by its tag, never by position
gh run list --workflow deploy-aur.yml --event push --json databaseId,headBranch,conclusion \
  --jq '.[] | select(.headBranch == "v<version>")'
gh run watch <run-id> --exit-status
gh run view <run-id> --json jobs --jq '.jobs[] | "\(.databaseId) \(.name) \(.conclusion)"'
gh api "repos/{owner}/{repo}/check-runs/<job-id>/annotations" --jq '.[].message'   # for each job
```

Both jobs (`test`, `deploy`) must succeed. No annotation may contain `Node.js 20` (spec 10). If one does, the release still stands: report it in `notes` as a regression of spec 10, for the planner.

A failed job is `blocked`, never re-run on your own. Tell the human which case it is:

- `test` failed on the runner (an apt timeout, a lost runner): `gh run rerun <run-id> --failed` gets a new runner.
- `deploy` failed because of the workflow: fix it on `main` through the pipeline, then `gh workflow run deploy-aur.yml -f version=<version>`. A `workflow_dispatch` runs `main`'s workflow file for the same version, so nobody re-tags.
- `test` failed because of the code: a new patch release. The tag stays.

Then the AUR. Its git has the version as soon as the deploy pushes; the RPC, which AUR helpers read, has trailed it by minutes (about 4.5 for 0.2.0):

```bash
curl -fsS --max-time 15 'https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=download-media' | grep -x 'pkgver=<version>'
timeout 540 sh -c 'until curl -fsS --max-time 15 https://aur.archlinux.org/rpc/v5/info/download-media | grep -qF "\"Version\":\"<version>-"; do sleep 30; done' && echo "rpc: ok" || echo "rpc: not yet"
```

If cgit lacks the version, the deploy didn't push: read the `deploy` job's log (`Nothing to publish` means the AUR already had that `PKGBUILD`), result `blocked`. Run the poll with the call's timeout raised to 600000 ms, or in the background: `timeout 540` keeps it under the 10 minutes a call can last, far over the default 2. If the RPC says `not yet`, run the same poll once more (18 minutes in all, two runs because a call can't run longer than 10). Still not there with cgit right: result `blocked`, with both outputs. Never re-run the deploy because of RPC lag alone.

Then the landing page, by the tagged commit (the bump changes `docs/index.html`, so the push of `main` redeploys Pages):

```bash
gh run list --workflow deploy-pages.yml --commit "$(git rev-parse "v<version>^{commit}")" --json databaseId,conclusion
curl -s https://edicsonabel.github.io/download-media/ | grep -F "v<version> ·"
```

If the list is empty, the run hasn't appeared yet: list again, as in publishing's step 1, and watch it if it's still running. A red Pages run, or a page without the `v<version> ·` label once the run is green, doesn't block: the package is already out. Report it in `notes` for the planner, and finish with `published`.

Update `.ai/specs/PLAN.md` for what shipped. Commit: `docs(plan): mark <version> as shipped`.

## Rules

1. Nothing moves to `publishing` without the human's answer.
2. A gate failure is `blocked`, never a judgment call.
3. Don't amend or rewrite anything already pushed. A bad release is fixed with a new patch release.
4. No release with a feature or fix between `integration` and `validation`.
5. Never change who you act as: not the `gh` account, not the git identity, not the remote.

## Report

The `## REPORT` block from `.ai/orchestration/WORKFLOW.md`. Result `done` or `blocked`, plus `published` at the end of `verification`.
