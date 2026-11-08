# 15 — Release runbook

Status: draft
Item: `fix/release-runbook`

## Problem

Following `.ai/roles/release-manager.md` as written, three steps of the 0.2.0 release went wrong or nearly did (`release/0.2.0` in `state.json`):

| Step | What the role says | What happened | What should happen |
| ---- | ------------------ | ------------- | ------------------ |
| Version | "`fix` → patch, `feat` → minor" (line 29) | `git log v0.1.0..v0.2.0` holds 2 `fix` and no `feat`, so the rule gives **0.1.1**. `AGENTS.md` counts new messages and flags as a minor: 4 new message keys and a wider `--cookies` made it **0.2.0**. The human had to name the version | The role follows `AGENTS.md` `Versioning` and reports the evidence: new message keys, new flags, breaking markers |
| Publishing | bump → commit → tag → `git push origin main "v<version>"` → `gh release create` (lines 39-46), no access check | The active `gh` account (`edicsonabelkc`, read-only) differed from the SSH push identity (`edicsonabel`). The role noticed it by hand, before step 1. Unnoticed, the tag push would have started the AUR deploy and `gh release create` would have failed after it | A read-only preflight before anything leaves the machine: `gh` login, `permissions.push`, SSH login. On a failure the role stops and asks; it never switches accounts |
| Verification | One look at `https://aur.archlinux.org/rpc/v5/info/download-media` (line 57) | The RPC served 0.2.0-1 about 4.5 minutes after the deploy pushed (deploy finished 20:39:42Z; RPC `LastModified` 20:39:36Z, so the AUR had it and the answer was stale) | Check the AUR git at once (cgit), then poll the RPC with a bound |

Since then, more release steps landed in other places and the role doesn't mention them: spec 10 asks the release-manager to confirm the `Deploy to AUR` tag run is green with no `Node.js 20` annotation, and how to recover a failed deploy job; spec 18 left live steps that must run before the release; `PLAN.md` lists decisions the roles took under the human's delegation, which the human must review first. And `gh run list --workflow deploy-aur.yml --limit 1` (line 53) picks the newest run, not the tag's.

## Goal

The release-manager can run the next release (0.3.0) from its role file alone, with no step caught by hand: the version follows `AGENTS.md`, no irreversible step runs before write access is proven, the human sees every pending decision and live step before approving, and verification waits for the AUR with a bound.

## Evidence

All read-only, run in this session.

| Finding | Command and result |
| ------- | ------------------ |
| 0.2.0 by commit type is a patch | `git log v0.1.0..v0.2.0 --format=%s --no-merges`: 2 `fix`, 0 `feat`, no `!` or `BREAKING CHANGE` |
| 0.2.0 added 4 message keys | Key-set diff (Design, preparation step 1) between `v0.1.0` and `v0.2.0`: `cookies_bad_spec`, `cookies_keyring_hint`, `item_no_media`, `partial_result`. Matches `PLAN.md`'s "4 new ones" |
| 0.3.0 adds 12 keys and 1 flag; the commit rule gives minor only because of one `feat` | Same command, `v0.2.0` to `HEAD`: 12 new keys (`untagged`, `no_cover`, `wizard_skipped`, ...). `8cc3d30 feat(cli): add --playlist-items`. 35 `fix`, 1 `feat`, no breaking marker. Had `8cc3d30` been typed `fix`, the role would say 0.2.1 |
| A naive `git diff` grep over-counts | `git diff v0.2.0..HEAD -- i18n.py \| grep '^+ "key":'` gives 13: it counts the reworded `auth_needed_retry`. The key-set diff gives 12 |
| `gh` holds two accounts on this machine | `gh auth status`: `edicsonabel` (active) and `edicsonabelkc`, both logged in |
| The preflight reads what it needs | `gh api user --jq .login` → `edicsonabel`. `gh api repos/{owner}/{repo} --jq .permissions.push` → `true`. `ssh -T git@github.com` → `Hi edicsonabel! ...` (exit 1 by design). `git remote -v`: `origin` is `git@github.com:edicsonabel/download-media.git` |
| Tag absence is checkable | `git ls-remote --exit-code --tags origin v9.9.9` → exit 2; `v0.2.0` → exit 0. `git rev-parse -q --verify refs/tags/v9.9.9` → exit 1 |
| The tag run can be picked by tag | `gh run list --workflow deploy-aur.yml --event push --json databaseId,headBranch,conclusion --jq '.[] \| select(.headBranch == "v0.2.0")'` → run 42290009457, `success`. The same list also holds v0.1.0 and v0.0.2 runs, so `--limit 1` is only right by luck |
| The Node 20 annotation is readable | `gh api repos/{owner}/{repo}/check-runs/128140651499/annotations --jq '.[].message'` (job `test` of run 42290009457) → `Node.js 20 is deprecated. ... actions/checkout@v4, actions/setup-python@v5` |
| The AUR deploy doesn't need the GitHub release | `deploy-aur.yml` downloads `archive/v<version>.tar.gz`, the tag's archive. So a failed `gh release create` leaves AUR publishing intact, and a missing GitHub release only |
| `gh release create` can refuse to make a tag | `gh release create --help`: `--verify-tag  Abort in case the git tag doesn't already exist in the remote repository` |
| The AUR git answers at once | `curl -fsS 'https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=download-media' \| grep -x 'pkgver=0.2.0'` → match. RPC: `"Version":"0.2.0-1"` |
| Pages runs can be picked by commit | `gh run list --workflow deploy-pages.yml --commit 8f24c5f5e5ae7d86d2b10606ab5240bd9f091cab` → run 42435709966. Pages only runs when `docs/**` changes, which the bump does |
| Deferred live steps since `v0.2.0` | Of the specs changed since the tag (`git log v0.2.0..HEAD --format= --name-only -- '.ai/specs/[0-9]*.md'`, 11 specs), spec 18's `Live check` is "partial" (3 steps skipped for lack of `DM_LIVE_IG_GATED`, `DM_LIVE_AGE_GATED`, `DM_LIVE_IG_IMAGE`), and spec 10 has two unchecked criteria: the tag-run check, and `actionlint`, never run |
| A missing live variable passes the gate | `tests/live/test_live.py:27-28` skip on a missing `DM_LIVE_IG_CAROUSEL` or `DM_LIVE_BROWSER`, and `pre-release.sh` still prints `All checks passed.` Spec 18's steps aren't pytest tests at all: they're commands in the spec |
| Agents' commands are capped at 10 minutes | The Bash tool's maximum timeout is 600000 ms, so a single 20-minute poll loop would be killed. Hence two bounded runs |

## Scope

### In

- `.ai/roles/release-manager.md`: version rule, preflight, publishing order, verification (tag run, Node 20 annotation, failed-job recovery, AUR cgit then bounded RPC poll, Pages by commit), pre-publish checklist.
- `.ai/commands/release.md` and the release table of `.ai/orchestration/WORKFLOW.md`: the orchestrator shows the checklist at the preparation stop and passes the human's review and waivers on to publishing.
- A test that reads the role file as text and fails on today's version.

### Out

- **`AGENTS.md`.** Its `Distribution (AUR)` flow (bump → tag → push → release) stays true in outline; the role adds steps around it. It also says the deploy "waits for the GitHub release tarball", when it really fetches the tag's archive. A planner note, not this item.
- **`.ai/live.env.example`.** Listing spec 18's variables there would tie a generic file to one release's steps. The checklist names them instead.
- **Making spec live steps pytest tests.** Spec 18's steps need a human-chosen post and, for the gated one, a browser menu. A roadmap row if wanted.
- **The workflows.** No change to `deploy-aur.yml`; recovering a failed job uses what spec 10 described.
- **Annotated or signed tags.** `git tag v<version>` stays lightweight, as for every earlier tag.
- **Building or installing the AUR package:** row 16.
- **Running the release.** This item only changes the runbook; 0.3.0 is its first real use.

## Files

| Action | File |
| ------ | ---- |
| modify | `.ai/roles/release-manager.md` |
| modify | `.ai/commands/release.md` |
| modify | `.ai/orchestration/WORKFLOW.md` (release transitions table only) |
| create | `tests/test_release_runbook.py` |

No collision: no item in flight touches these files (`state.json`). The developer must not touch `.ai/orchestration/state.json`, which only the orchestrator writes; `WORKFLOW.md` is a different file.

## Design

No `src/` or `yt-dlp` change. The target text below is the recommended option of each decision. Wording can change; the commands, their order and the rules can't, and the test pins the parts listed under Test.

### `release-manager.md`, target

Sections, in this order: Mission (unchanged), Read first, Preflight, Stage `preparation`, Stage `publishing`, Stage `verification`, Rules, Report (unchanged).

**Read first** gains two entries:

4. `.ai/specs/PLAN.md`, its "Next release" section above all.
5. Every spec changed since the last tag:
   `git log "$(git describe --tags --abbrev=0)"..HEAD --format= --name-only -- '.ai/specs/[0-9]*.md' | sort -u`

**Preflight** (new section, D2):

> Read-only. Run it in `preparation`, so the human approves knowing the result, and again as the first step of `publishing`, because the account can change in between.
>
> ```bash
> git status --porcelain                                         # empty
> git rev-parse --abbrev-ref HEAD                                # main
> git fetch origin && git merge-base --is-ancestor origin/main HEAD   # exit 0: main fast-forwards
> git rev-parse -q --verify "refs/tags/v<version>"               # exit 1: no local tag
> git ls-remote --exit-code --tags origin "v<version>"           # exit 2: no remote tag
> gh api user --jq .login                                        # the gh account
> gh api repos/{owner}/{repo} --jq .permissions.push             # must print true
> ssh -T git@github.com                                          # "Hi <login>!": the same login as gh
> ```
>
> `git push` and `gh` can run as different accounts on one machine, and `gh release create` runs after the tag push has already started the AUR deploy. So both must be proven before anything leaves the machine. `ssh -T` exits 1 even when it authenticates: read its output. If `origin` isn't an SSH URL, skip that line and say so.
>
> If any check fails, result `blocked`, with what each command printed. Never fix it yourself: never run `gh auth switch` or `gh auth login`, never set `GH_TOKEN`, never change the remote or the git identity. The human picks the account.

**Stage `preparation`:**

> Changes nothing that leaves the machine.
>
> ```bash
> git status                                  # must be clean
> last="$(git describe --tags --abbrev=0)"
> git log --oneline "$last"..HEAD
> set -a; . .ai/live.env; set +a
> scripts/pre-release.sh                      # the full gate, with live tests
> ```
>
> 1. **Version.** Follow the `Versioning` section of `AGENTS.md`. It goes by what changed for users, so commit types alone don't decide it: a `fix` commit that adds a message counts as what `AGENTS.md` says new messages are. If the human named a version, use it, and report it if yours differs. Report this evidence:
>
>    ```bash
>    keys() { git show "$1:src/download_media/i18n.py" | grep -oE '^\s+"[a-z_]+":' | tr -d ' ":' | sort -u; }
>    comm -13 <(keys "$last") <(keys HEAD)                                            # new messages
>    git diff "$last"..HEAD -- src/download_media/cli.py | grep -E '^\+.*"--[a-z-]+"'   # new flags
>    git log "$last"..HEAD --format=%B | grep -E '^BREAKING CHANGE|^[a-z]+(\(.+\))?!:'  # breaking
>    ```
>
>    plus the `SemVer` section of each spec in Read first, where it has one.
> 2. Write `.ai/releases/<version>.md` (unchanged text).
> 3. If `.ai/live.env` is missing, result `blocked` (unchanged).
> 4. Run the Preflight.
> 5. Build the **pre-publish checklist** and put it in the report, one line per entry, each marked done, not run or pending:
>    - **Gate:** the full gate's result, every `xfail`, every skipped test. A skipped live test is a live step not run.
>    - **Live steps:** every step of a `Live check` marked skipped or partial in the specs from Read first, and every live item of `PLAN.md`'s "Before `/release`". Run them now, one at a time (Instagram rate-limits by IP), into `tmp/`, through `download-media` only, and compare what's on disk with what was printed. A step whose `DM_LIVE_*` variable isn't in `.ai/live.env` stays not run.
>    - **Release steps:** unchecked criteria in those specs that wait for the release, such as spec 10's tag-run check. They're checked in `verification`. Any other unchecked criterion is listed too, as not run.
>    - **Delegated decisions:** every decision a role took under the human's delegation since the last tag, with its spec and id, as `PLAN.md` and the specs' Open decisions list them. You don't judge them; the human reviews them.
>    - **Preflight:** the `gh` login, `permissions.push`, the SSH login.
>
> Report the version and its evidence, the gate result and the checklist. The human decides from that.

**Stage `publishing`** (D3, D6, D7):

> Starts only when the orchestrator's message says the human (a) approved publishing `<version>`, (b) reviewed the delegated decisions of the checklist, and (c) for every live step not run, waived it by name. If any of the three is missing, result `blocked`, nothing done.
>
> In this order:
>
> ```bash
> # 0. The Preflight again. Any failure: blocked.
>
> # 1. Push what's waiting on main, and let CI prove it before anything is tagged
> git push origin main
> gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" --json databaseId,conclusion
> gh run watch <run-id> --exit-status
>
> # 2. Bump the 4 places listed in AGENTS.md
> scripts/pre-release.sh --no-live            # version sync is part of the gate
> git commit -am "build: release <version>"
> git tag v<version>
>
> # 3. Both refs, or neither
> git push --atomic origin main "v<version>"
>
> # 4. The release, on the tag just pushed, never a new tag
> gh release create "v<version>" --verify-tag --title "v<version>" --notes-file .ai/releases/<version>.md
> ```
>
> - Step 1: the run can take a few seconds to appear after the push; list again. If `HEAD` was already on `origin`, its existing run counts. A red run is `blocked`: nothing is tagged, and the fix goes through the pipeline.
> - Step 4: if it fails, the tag is out and the AUR deploy runs anyway, since it downloads the tag's archive, not a release asset. Result `blocked`; the human decides whether to run step 4 again.
> - If any step fails, stop there with result `blocked`. Don't retry a publishing step, delete or move a tag, or force-push on your own (unchanged).

**Stage `verification`** (D4, D5):

> ```bash
> # The tag run, picked by its tag, never by position
> gh run list --workflow deploy-aur.yml --event push --json databaseId,headBranch,conclusion \
>   --jq '.[] | select(.headBranch == "v<version>")'
> gh run watch <run-id> --exit-status
> gh run view <run-id> --json jobs --jq '.jobs[] | "\(.databaseId) \(.name) \(.conclusion)"'
> gh api "repos/{owner}/{repo}/check-runs/<job-id>/annotations" --jq '.[].message'   # for each job
> ```
>
> Both jobs (`test`, `deploy`) must succeed. No annotation may contain `Node.js 20` (spec 10). If one does, the release still stands: report it in `notes` as a regression of spec 10, for the planner.
>
> A failed job is `blocked`, never re-run on your own. Tell the human which case it is:
> - `test` failed on the runner (an apt timeout, a lost runner): `gh run rerun <run-id> --failed` gets a new runner.
> - `deploy` failed because of the workflow: fix it on `main` through the pipeline, then `gh workflow run deploy-aur.yml -f version=<version>`. A `workflow_dispatch` runs `main`'s workflow file for the same version, so nobody re-tags.
> - `test` failed because of the code: a new patch release. The tag stays.
>
> Then the AUR. Its git has the version as soon as the deploy pushes; the RPC, which AUR helpers read, has trailed it by minutes (about 4.5 for 0.2.0):
>
> ```bash
> curl -fsS --max-time 15 'https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=download-media' | grep -x 'pkgver=<version>'
> timeout 540 sh -c 'until curl -fsS --max-time 15 https://aur.archlinux.org/rpc/v5/info/download-media | grep -qF "\"Version\":\"<version>-"; do sleep 30; done' && echo "rpc: ok" || echo "rpc: not yet"
> ```
>
> If cgit lacks the version, the deploy didn't push: read the `deploy` job's log (`Nothing to publish` means the AUR already had that `PKGBUILD`), result `blocked`. If the RPC says `not yet`, run the same poll once more (18 minutes in all, two runs because a command can't run longer than 10). Still not there with cgit right: result `blocked`, with both outputs. Never re-run the deploy because of RPC lag alone.
>
> Then the landing page, by the tagged commit (the bump changes `docs/index.html`, so the push of `main` redeploys Pages):
>
> ```bash
> gh run list --workflow deploy-pages.yml --commit "$(git rev-parse "v<version>^{commit}")" --json databaseId,conclusion
> curl -s https://edicsonabel.github.io/download-media/ | grep -F "v<version> ·"
> ```
>
> Update `.ai/specs/PLAN.md` for what shipped (unchanged).

**Rules** gain one: "5. Never change who you act as: not the `gh` account, not the git identity, not the remote."

### Orchestrator side

- `.ai/commands/release.md` step 4: show the version and its evidence, the gate result, the notes **and the pre-publish checklist**; ask whether to publish; record in `history` the human's review of the delegated decisions and any live step waived, by name. Pass those to the release-manager when launching `publishing`.
- `WORKFLOW.md`, release table: `preparation → ⏸` "Shows version, gate, notes and the pre-publish checklist"; `⏸ → publishing` "human approves, having reviewed the delegated decisions and waived by name any live step not run".

### Test

`tests/test_release_runbook.py` reads `.ai/roles/release-manager.md` as text (no dependency; the repo's `.ai/` is in every checkout the gate runs from, and `PKGBUILD` runs no tests from the tarball). It splits the file at `## ` headings and fails if `Preflight`, `` Stage `preparation` ``, `` Stage `publishing` `` or `` Stage `verification` `` is missing, so a renamed heading can't make it check nothing. Then:

1. **Version rule:** preparation mentions `AGENTS.md` and `` `Versioning` ``, and `i18n.py`. The whole file has no `→ patch`, `→ minor` or `→ major`.
2. **Preflight:** contains `gh api user --jq .login`, `.permissions.push`, `ssh -T git@github.com` and `git ls-remote --exit-code --tags`. Every line that mentions `gh auth switch` also says `never` or `Never`.
3. **Order in publishing**, by position in the section: `Preflight` < `git push origin main` < `gh run watch` < `git commit -am "build: release` < `git tag v` < `git push --atomic origin main "v<version>"` < `gh release create`. The `gh release create` line has `--verify-tag`.
4. **Approval gate:** publishing mentions `delegated` and `waived`.
5. **Verification:** contains `headBranch`, `Node.js 20`, `workflow_dispatch`, `cgit`, `rpc/v5/info/download-media` and `timeout 540`. The whole file has no `--limit 1`.
6. **Checklist:** preparation mentions `Live check`, `skipped`, `delegated` and `Preflight`.

Each assertion names the missing string in its message. On today's file the test fails on 1 to 6.

### Commits

One logical change each, the test last so the gate passes at every commit:

1. `docs(roles): follow agents.md for the release version`
2. `docs(roles): check gh and ssh write access before publishing`
3. `docs(roles): prove ci on main before tagging a release`
4. `docs(roles): pick the tag run and bound the aur wait`
5. `docs(roles): add the pre-publish checklist and approval gate` (with `release.md` and `WORKFLOW.md`)
6. `test: guard the release runbook`

## Cases

The tool doesn't change, so the cases are release situations. Wizard, direct, `-y`, `-p`, `-P`, `-l`, cookies, single, carousel, playlist and rerun rows are removed: no runtime path changes.

| Case | Expected from the role |
| ---- | ---------------------- |
| Only `fix` commits, new message keys | Version by `AGENTS.md` (minor), evidence lists the keys |
| Human names a version that differs from the evidence | Uses the human's, reports both |
| `.ai/live.env` missing | `blocked` in preparation (unchanged) |
| A spec live step's `DM_LIVE_*` variable missing | Step marked not run; publishing needs the human's waiver by name |
| A live test skipped in the full gate | Counted as a live step not run, same rule |
| Orchestrator's publishing message lacks the review or a waiver | `blocked`, nothing done |
| `gh` read-only, or `gh` login differs from the SSH login | `blocked` at the preflight, both logins reported, no `gh auth switch` |
| Tag already exists, locally or on `origin` | `blocked` at the preflight |
| Local `main` doesn't fast-forward `origin/main` | `blocked` at the preflight |
| CI red after step 1's push | `blocked`, nothing tagged |
| `git push --atomic` rejected | Neither ref moved; `blocked` |
| `gh release create` fails after the tag push | `blocked`; AUR deploy runs anyway; human decides on step 4 |
| `test` job fails on infra | `blocked`; suggests `gh run rerun --failed` |
| `deploy` job fails on the workflow | `blocked`; suggests fix on `main`, then `workflow_dispatch` with the same version |
| `Node.js 20` annotation on a green run | `published`, regression noted for the planner |
| RPC lags less than 18 minutes | Poll succeeds, `published` |
| RPC lags more, cgit right | `blocked` with both outputs, no re-run |
| cgit lacks the version | `blocked`, deploy log read |

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] `git diff main --stat` lists only the four files in Files. Check it on the branch, before the merge
- [ ] `grep -nE '→ (patch|minor|major)' .ai/roles/release-manager.md` finds nothing, and the preparation stage names `AGENTS.md` `Versioning`
- [ ] `grep -n -- '--limit 1' .ai/roles/release-manager.md` finds nothing
- [ ] Reading the role: the Preflight section has the eight commands of Design, runs in preparation and again as publishing's step 0, and says to stop and never switch accounts
- [ ] Reading the role: publishing's order is preflight, push of `main`, CI watch, bump and gate, commit, tag, `git push --atomic`, `gh release create --verify-tag`
- [ ] Reading the role: verification picks the tag run by `headBranch`, checks both jobs and their annotations for `Node.js 20`, names the three failed-job cases with `gh run rerun --failed` and `workflow_dispatch`, checks cgit, then polls the RPC with `timeout 540`, at most twice
- [ ] Reading the role: preparation builds the checklist (gate, live steps, release steps, delegated decisions, preflight), and publishing refuses to start without the human's review and named waivers
- [ ] `.ai/commands/release.md` step 4 and `WORKFLOW.md`'s release table mention the checklist
- [ ] `.venv/bin/pytest -q tests/test_release_runbook.py` passes on the branch, and fails when the role file is put back to `main`'s (`git show main:.ai/roles/release-manager.md > .ai/roles/release-manager.md`, run, then `git checkout -- .ai/roles/release-manager.md`), with failures on assertions 1 to 6. Check it before the merge: after it, `main` has the new file
- [ ] The commands the role uses, run read-only against v0.2.0, give what Evidence shows (tag-run `select`, annotations, `ls-remote --exit-code`, cgit and RPC greps). No push, tag, release or dispatch is run to check this item

## Live check

Not needed for downloads: no runtime path changes. The runbook's real check is the 0.3.0 release, the first run of the new publishing order, of the `checkout@v7` deploy job (spec 10) and of the RPC poll. The read-only commands were run against v0.2.0 in this session (Evidence). Nothing that writes to GitHub or the AUR can be tried before a real release.

## Open decisions

The human delegated these to the roles. Each has a recommendation, preferring the option that's easiest to undo; every one is a text change in a role file.

**D1. The version rule.**

- **A (recommended):** the role points at `AGENTS.md` `Versioning` without restating it, says commit types alone don't decide, and reports evidence (new keys by a key-set diff, new flags, breaking markers, spec `SemVer` sections). One source of truth; the 0.2.0 mismatch can't recur.
- **B:** restate `AGENTS.md`'s rule in the role. Two copies to keep in sync, which is how this bug happened.
- **C:** change `AGENTS.md` to follow commit types. It would make 0.2.0 a patch in hindsight, and contradicts the "new messages are minor" rule the project already shipped under.

**D2. The `gh` preflight.**

- **A (recommended):** check `gh api user --jq .login`, `permissions.push == true`, and that `ssh -T git@github.com` names the same login; plus a clean tree, `main`, fast-forward, no existing tag. Derived, so no account name is written in the repo. On any failure: `blocked`, never switch accounts. Run in preparation and again at publishing's start.
- **B:** as A, but only both accounts need write access, not the same login. Checking the SSH account's access needs another call (`gh api repos/{owner}/{repo}/collaborators/<ssh-login>/permission`). More permissive, and the tag and release could come from different people.
- **C:** hardcode the expected login in the role. Simplest to read, but puts an account in the repo and breaks for any other maintainer.
- **D:** `git push --dry-run` as the SSH check. Not verified here that GitHub refuses a read-only account at dry-run time, and it's a push command run before approval.

**D3. The order around tag, push and release.**

- **A (recommended):** preflight → `git push origin main` and wait for `ci.yml` green on that commit → bump, gate, commit, tag → `git push --atomic origin main "v<version>"` → `gh release create --verify-tag`. CI proves everything except the four version strings before any tag exists; the local gate checks those. The bump stays in the tagged commit (constraints, Process 7). About 6 more minutes.
- **B:** today's order plus the preflight. Faster, but a red gate on the tag run leaves a tag that can't be moved, so the next attempt needs a new patch number.
- **C:** push the bump commit, wait for CI on it, then push the tag. Proves the exact tagged commit, but a red CI leaves the bump on `main` untagged, and fixing it means tagging a later commit, which breaks "bump in the tagged commit", or bumping again.
- **D:** let `gh release create --target` create the tag through the API. One call, but not verified here that an API-made tag starts `on: push: tags`, and it departs from `AGENTS.md`'s flow.

**D4. RPC lag.**

- **A (recommended):** cgit first, then poll the RPC every 30 s under `timeout 540`, at most twice (18 minutes, four times the lag seen). Two runs because an agent's command can't run longer than 10 minutes.
- **B:** one 9-minute run. Twice the lag seen, and fewer false `blocked`s than no poll, but a slow day still blocks.
- **C:** no bound, or a fixed `sleep`. An unbounded loop is killed by the tool, and a sleep either wastes time or misses.

**D5. RPC still stale after the bound, cgit right.**

- **A (recommended):** `blocked`, with both outputs. The mission is to be sure it arrived, and helpers read the RPC. The human decides, without a re-run of the deploy.
- **B:** `published`, with a note. The AUR git is the source of truth and the RPC is a cache, but the role would report users can update before they can.

**D6. The human's review of delegated decisions.**

- **A (recommended):** preparation lists them in the checklist; publishing refuses to start unless the orchestrator's message says the human reviewed them. The review can't be skipped by accident.
- **B:** list them only, non-blocking. The human may approve publishing without noticing them.

**D7. Live steps not run.**

- **A (recommended):** a live step not run, a spec's skipped step or a skipped live test, blocks publishing unless the human waives it by name. The default holds the constraints (the full gate with live tests before any release), and the human can still ship when a step can't run, such as when no age-gated video is at hand.
- **B:** always block, no waiver. Strictest, but one unavailable URL stops a release.
- **C:** report only. 0.2.0's gate would pass with live tests skipped, which constraints Verification 2 doesn't allow.

**D8. A `Node.js 20` annotation on a green tag run.**

- **A (recommended):** the release stands, the regression goes in `notes` for the planner. The package is already out; blocking can't undo it.
- **B:** `blocked`. The human sees it at once, at the price of a stop with nothing to do.

## SemVer

None. Nothing the package installs changes: `.ai/`, the role files and `tests/` aren't in the wheel. Commits are `docs:` and `test:`.
