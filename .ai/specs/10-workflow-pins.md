# 10 — Pin and bound the workflows

Status: approved
Item: `fix/workflow-pins`

## Problem

In CI run 42315736194, attempt 1, the step `sudo apt-get update && sudo apt-get install -y ffmpeg` ran for 15m10s until the job's 15-minute cap cancelled it. Attempt 2 fetched the same 69.3 MB in 4 s. The same step in `deploy-aur.yml`'s `test` job has no cap at all, so the release workflow would sit there for GitHub's default of 360 minutes. On top of that, both deploy workflows run Node 20 actions that GitHub now forces onto Node 24, and every job runs on `ubuntu-latest`, a label GitHub is moving to a new Ubuntu right now.

## Goal

A slow mirror fails a run within minutes, with the step that caused it named, instead of holding it for 15 minutes or 6 hours. Every job has a time limit. The deploy workflows use the Node 24 majors `ci.yml` already uses. All jobs run on a named image that only changes when a commit changes it.

## Evidence

Collected with read-only `gh` calls as `edicsonabel` (`gh api user --jq .login`).

| Finding | Evidence |
| ------- | -------- |
| **The apt step didn't hang. It crawled.** Bytes kept arriving at about 58 KB/s, so apt's own connection timeouts never fire. Only a step or job time limit stops it. | `gh api repos/{owner}/{repo}/actions/jobs/128227745660/logs`: `libavcodec62` (10.2 MB) from 00:09:13 to 00:12:07, `libflite1` (13.5 MB) from 00:13:20 to 00:17:15. At 00:17:46 it had reached package 57 of 89, and then `##[error]The operation was canceled.` |
| The retry landed on another runner, in another region, and was fast. | Attempt 1: `Region: westus2`. Attempt 2 (job 128233324834): `Region: southcentralus`, `Fetched 69.3 MB in 4s (19.8 MB/s)`. Both used `azure.archive.ubuntu.com` |
| A normal apt step takes 13 to 45 s. | Step 4 timings for runs 42290009423, 42290009457, 42291987025, 42315736194/2, 42337330776 (45 s, the slowest), 42389879992, 42415755776, 42427002268, 42427671722 and 42432662032 |
| A whole `gate` job takes 5m23s to 6m05s now. The gate step alone takes 4m48s to 5m30s. Before spec 17's media fixtures it took 14 s. | Runs 42415755776, 42427002268, 42427671722 and 42432662032 (`gh api .../runs/<id>/jobs`). Run 42291987025: gate step 14 s |
| The deploy jobs are short. | `Deploy to AUR` v0.2.0 (run 42290009457): `test` 50 s, `deploy` 39 s. `Deploy GitHub Pages` runs 42315736151 and 42291987021: 26 s and 80 s |
| No job in either deploy workflow sets `timeout-minutes`. `ci.yml` sets 15. | `.github/workflows/*.yml` on `main` (`5c30ee5`) |
| **Node 20 is forced onto Node 24 in both deploy workflows.** Pages lists `actions/upload-artifact@v4`, which `upload-pages-artifact@v3` calls internally. | Annotations (`gh api .../check-runs/<job>/annotations`): run 42290009457 names `actions/checkout@v4, actions/setup-python@v5`; runs 42315736151 and 42291987021 name `actions/checkout@v4, actions/configure-pages@v5, actions/deploy-pages@v4, actions/upload-artifact@v4` |
| `ci.yml`'s `@v7` actions get no Node warning. | Run 42432662032: one annotation only, the `ubuntu-latest` notice |
| **Every job since 2026-11-06 ran on `ubuntu-26.04`,** with ffmpeg `7:8.0.1-3ubuntu2` from Ubuntu's archive. So the gate, including spec 17's fixtures, has only ever passed on 26.04. | `Image: ubuntu-26.04` in the logs of runs 42290009423, 42290009457 (both jobs), 42315736151, 42315736194 (both attempts), 42337330776 and 42432662032. `Setting up ffmpeg (7:8.0.1-3ubuntu2)` in every gate and test job among them |
| The image already changed under this repo with no commit. | Run 37965861632 (v0.1.0, 2026-10-09): `Image: ubuntu-24.04`. Run 42290009457 (v0.2.0, 2026-11-06): `Image: ubuntu-26.04` |
| `ubuntu-latest` is mid-rollout: GitHub is moving it from 24.04 to 26.04 between 2026-10-19 and 2026-11-19. | `actions/runner-images` issue 14748, still open, and the notice on every run. The runner-images README still lists `ubuntu-latest` next to 24.04 |
| ffmpeg isn't on the runner image: apt installs it each time, along with 88 dependencies. | `The following NEW packages will be installed: ffmpeg ...`, `0 upgraded, 89 newly installed` |
| The gate's fixtures encode with libx264, aac, libmp3lame, flac, libopus, libvorbis, libvpx, libtheora, wmv2 and wmav2. | `tests/conftest.py:312-363` (`media_url`) |
| Arch ships ffmpeg 9.0.2 today. Ubuntu 26.04 ships 8.0.1, and Ubuntu 24.04 ships 6.1.1. | `pacman -Q ffmpeg` → `2:9.0.2-2`. `packages.ubuntu.com/noble/ffmpeg` → `7:6.1.1-3ubuntu5`. Never run on 24.04 for this repo |

### Target action versions

Checked with `gh api repos/actions/<a>/commits/<tag>` and the `runs.using` field of `action.yml` at that tag:

| Action | Today | Target | Resolves to | `runs.using` |
| ------ | ----- | ------ | ----------- | ------------ |
| `actions/checkout` | `@v4` (node20) | `@v7` = v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` | node24 |
| `actions/setup-python` | `@v5` (node20) | `@v7` = v7.1.0 | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` | node24 |
| `actions/configure-pages` | `@v5` (node20) | `@v6` = v6.0.0 | `45bfe0192ca1faeb007ade9deae92b16b8254a0d` | node24 |
| `actions/upload-pages-artifact` | `@v3` (composite, calls `upload-artifact@v4`) | `@v5` = v5.0.0 | `fc324d3547104276b827a68afc52ff2a11cc49c9` | composite, calls `upload-artifact@bbbca2d` (v7.0.0, node24) |
| `actions/deploy-pages` | `@v4` (node20) | `@v5` = v5.0.1 | `368f82528645a54fb793d4d04e342629a3f51346` | node24 |

The `checkout` and `setup-python` SHAs are the ones `ci.yml`'s runs already download (`Download action repository 'actions/checkout@v7' (SHA:3d3c42e...)`).

Breaking changes between today's versions and the targets, from the release notes:

- `upload-pages-artifact` v4 leaves dotfiles out of the artifact. `docs/` holds only `index.html`, so nothing changes.
- `upload-pages-artifact` v3 or newer needs `deploy-pages` v4 or newer. v5 with v5 is fine.
- `configure-pages` v6 and `deploy-pages` v5 only move to Node 24. `deploy-pages`' own deployment timeout stays 10 minutes (`timeout` input, default `600000`).
- `checkout` v7 and `setup-python` v7: spec 03 already checked them, and `ci.yml` has run them green 10 times.

## Scope

### In

- All three workflows. `ci.yml` is in because the slow apt step happened there (run 42315736194).
- Pin `runs-on` in every job (D2).
- `timeout-minutes` on every job (D3).
- A step-level `timeout-minutes` on the two `Install ffmpeg` steps (D4).
- Move the Node 20 actions in both deploy workflows to the targets above (D1).
- A test that fails on today's workflows and passes on the fixed ones (D5).

### Out

- **SHA pinning and Dependabot** (D1, option B). That would be a new convention for all three files, so it belongs on its own roadmap row.
- **Replacing the apt install** with a static build, a `.deb` cache or an Arch container (D4, options C to E). Running the gate on Arch is row 16 (`aur-build-check`).
- **Removing `checkout` from the AUR `deploy` job.** It only needs `PKGBUILD`, which is already inside the tarball the job downloads. That's a redesign of the publishing job, so it's a planner note.
- **`pacman -Syu` and `Initialize containers` in the `deploy` job.** The job time limit covers them, and nothing in the runs shows them slow.
- **`--no-install-recommends` or any change to the list of packages installed.** It would change the proven ffmpeg install for a few MB.
- **A Python matrix, required checks and branch protection:** rows 11 and 13.
- **`AGENTS.md`.** Nothing it says about the workflows changes.

## Files

| Action | File                                  |
| ------ | ------------------------------------- |
| modify | `.github/workflows/ci.yml`            |
| modify | `.github/workflows/deploy-aur.yml`    |
| modify | `.github/workflows/deploy-pages.yml`  |
| create | `tests/test_workflows.py` (D5)        |

Collision: rows 13 (`ci-pr-proof`) and 16 (`aur-build-check`) will touch these workflows too. Neither is in flight (`state.json`). Whichever goes second rebases.

## Design

No `yt-dlp`, `src/` or runtime change. With the recommended options, the change is exactly this diff, checked in a scratch copy:

```diff
--- ci.yml
   gate:
-    runs-on: ubuntu-latest
+    runs-on: ubuntu-26.04
     timeout-minutes: 15
@@
       - name: Install ffmpeg
+        timeout-minutes: 5
         run: sudo apt-get update && sudo apt-get install -y ffmpeg

--- deploy-aur.yml
   test:
-    runs-on: ubuntu-latest
+    runs-on: ubuntu-26.04
+    timeout-minutes: 15
     steps:
-      - uses: actions/checkout@v4
+      - uses: actions/checkout@v7

-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@v7
@@
       - name: Install ffmpeg
+        timeout-minutes: 5
         run: sudo apt-get update && sudo apt-get install -y ffmpeg
@@
   deploy:
     needs: test
-    runs-on: ubuntu-latest
+    runs-on: ubuntu-26.04
+    timeout-minutes: 10
     container:
@@
-      - uses: actions/checkout@v4
+      - uses: actions/checkout@v7

--- deploy-pages.yml
-    runs-on: ubuntu-latest
+    runs-on: ubuntu-26.04
+    timeout-minutes: 15
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/configure-pages@v5
-      - uses: actions/upload-pages-artifact@v3
+      - uses: actions/checkout@v7
+      - uses: actions/configure-pages@v6
+      - uses: actions/upload-pages-artifact@v5
         with:
           path: docs/
       - id: deployment
-        uses: actions/deploy-pages@v4
+        uses: actions/deploy-pages@v5
```

Why each number:

- **Apt step, 5 minutes.** The slowest normal run took 45 s, so 5 minutes is more than six times that. The step timeout covers `apt-get update` too. When it fires, GitHub fails the step and the job, and skips the steps after it. In the slow run, the job would have failed after 5 minutes instead of 15.
- **`gate` and `test` jobs, 15 minutes.** It's the same job in two files, so it gets the same limit: a gate that passes in CI won't time out on a tag. Worst case today: 5 minutes of apt, about 20 s for the venv, and about 5.5 minutes for the gate, roughly 11 minutes in total. If a green run's gate step ever goes past about 10 minutes, raise both limits together.
- **AUR `deploy`, 10 minutes.** It takes 39 s today. The tarball wait tops out at about a minute (10 tries, 6 s apart). The job is idempotent: if nothing changed, it says "Nothing to publish" and exits 0, and the push either happens whole or not at all. So a timeout followed by a re-run is safe.
- **Pages, 15 minutes.** It takes 26 to 80 s. The limit is above `deploy-pages`' own 10-minute deployment timeout, so the action cancels its own deployment cleanly before the runner kills the job.

The AUR `deploy` job runs `checkout` inside `archlinux:base-devel`. In v0.2.0's run, `checkout@v4` already ran there on Node 24 (forced, per the annotation), so Node 24 works in that container. Steps stay in their current order: `git` is installed before `checkout` runs.

### Test (D5)

`tests/test_workflows.py` reads `.github/workflows/*.yml` as text. It adds no dependency: PyYAML isn't in the `dev` extra. It checks:

1. Every job under `jobs:` has its own `timeout-minutes:`.
2. Every `runs-on:` is `ubuntu-26.04`.
3. Every step whose `run:` calls `apt-get` has its own step-level `timeout-minutes:`.
4. Every `uses: actions/<name>@v<N>` is at least `checkout` 7, `setup-python` 7, `configure-pages` 6, `upload-pages-artifact` 5 and `deploy-pages` 5. An `actions/*` action missing from that list fails, so a newly added action has to be added to it.
5. It finds exactly the 3 workflow files and 4 jobs, so a parsing slip can't pass by checking nothing.

The same rules, written against PyYAML, flag 16 problems on `main` and none on the diff above: `python3 -I <check.py> <dir>`, system Python, PyYAML 6.0.3. The test must flag the same 16 on today's files.

### Commits

One logical change each. The test goes last, so the gate passes at every commit:

1. `ci: pin every job to the ubuntu-26.04 runner`
2. `ci: cap every job and the ffmpeg install with timeouts`
3. `ci: move the deploy workflows to node 24 actions`
4. `test: guard workflow runners, timeouts and action majors`

## Cases

The tool doesn't change, so the cases are CI events:

| Event | Expected |
| ----- | -------- |
| Push to `main` | `CI` runs on `ubuntu-26.04`. The apt step is capped at 5 minutes and the job at 15 |
| Push that touches `deploy-pages.yml` or `docs/**` | `Deploy GitHub Pages` runs and redeploys the same `docs/`, with no Node 20 warning. **The push that lands this item triggers it** |
| `v*` tag, or `workflow_dispatch` with a version | `Deploy to AUR`: `test` (15 min, apt 5 min), then `deploy` (10 min), with no Node 20 warning |
| Slow mirror | The apt step fails at 5 minutes with GitHub's timeout error and the job fails. Fix it with "Re-run failed jobs", which gets a new runner. On a tag run, `deploy` waits, because it `needs: test` |
| Hung test | The job is cancelled at 15 minutes |
| PR | Same as a push to `main`, on `ci.yml` |

Wizard, direct, `-y`, `-p`, `-P`, `-l`, cookies, single, carousel, playlist, rerun and failure modes are removed: no runtime path changes.

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] The workflow diff matches the Design diff (or the options the human picks): `git diff main -- .github/workflows` touches only `runs-on`, `timeout-minutes` and `uses:` lines
- [ ] `grep -rnE 'ubuntu-latest|checkout@v4|setup-python@v5|configure-pages@v5|upload-pages-artifact@v3|deploy-pages@v4' .github/workflows` finds nothing
- [ ] `grep -c 'timeout-minutes' .github/workflows/ci.yml .github/workflows/deploy-aur.yml .github/workflows/deploy-pages.yml` gives 2, 3 and 1
- [ ] All three files are still valid YAML: `python3 -I -c 'import sys,yaml; [yaml.safe_load(open(f)) for f in sys.argv[1:]]' .github/workflows/*.yml` exits 0 (system Python, PyYAML)
- [ ] `podman run --rm -v "$PWD:/repo:ro,Z" -w /repo docker.io/rhysd/actionlint:latest` exits 0. In the architect's session the permission classifier blocked this command, so the human may need to allow it. If it stays blocked, say so in the report, and the YAML check above plus the first CI run stand in for it
- [ ] `tests/test_workflows.py` passes on the branch, and fails when the three workflows are reverted to `main`'s (`git checkout main -- .github/workflows`, run the test, restore): it must name the same 16 problems
- [ ] `git diff main --stat` lists only the four files in Files
- [ ] After the push (D6), `gh run list --workflow ci.yml --commit <full sha> --json conclusion,databaseId` shows `success`. That job's log has `Image: ubuntu-26.04`, and `gh api .../jobs --jq '.jobs[].labels'` shows `ubuntu-26.04`
- [ ] After the same push, `gh run list --workflow deploy-pages.yml --commit <full sha>` shows `success`, and its job's annotations have no `Node.js 20` warning. `curl -s https://edicsonabel.github.io/download-media/` still shows the same `vX.Y.Z` label as `docs/index.html`
- [ ] At the next release, the `release-manager` checks that the `Deploy to AUR` tag run succeeded with no `Node.js 20` warning on either job. This one can't be checked at validation, so record it as a release step, not a validation failure

## Live check

No download check is needed: no download path changes. The live check here is the first real run of each workflow, and nothing local stands in for it. Two of them happen on the push that lands this item: `CI` and `Deploy GitHub Pages`, the latter because its own file changed. `Deploy to AUR` only runs on a tag, or on a `workflow_dispatch` that would publish to the AUR. So it gets proven at the next release, and its `test` job is the same steps `ci.yml` proves on that push.

If the tag run's `deploy` job fails because of this change: fix it on `main`, then run `workflow_dispatch` with the same version. A dispatch runs the workflow file from the default branch, so nobody has to re-tag.

## Open decisions

Resolved by the roles under the human's delegation, each on the recommended option: D1 A, D2 A, D3 A, D4 A, D5 A, D6 A (push of main after the merge, before validation, under the same delegation).

The human delegated these to the roles. Each has a recommendation, preferring the option that's easiest to undo. Every one of them is undone by a one-line revert.

**D1. How to reference the actions.**

- **A (recommended):** major tags (`@v7`, `@v6`, `@v5`), as `ci.yml` already does. No new convention, and security fixes within a major arrive on their own.
- **B:** full SHAs with a `# vX.Y.Z` comment, in all three files. Immutable, but someone has to bump them by hand (there's no Dependabot config) and it changes `ci.yml`'s convention. The only action that runs in a job with a secret (`AUR_SSH_PRIVATE_KEY`) is `actions/checkout`, which GitHub publishes. If wanted, give it its own row, with Dependabot.

**D2. The runner image.**

- **A (recommended):** `ubuntu-26.04` in every job. It's what every run since 2026-11-06 got, so the change is a no-op in practice. It also ends the rollout lottery before 2026-11-19. Its ffmpeg 8.0.1 has every encoder the fixtures use (proven green on run 42432662032), and it's the closest to Arch's 9.0.2. The cost: the next Ubuntu needs a commit, and GitHub's image deprecation notices will say when.
- **B:** `ubuntu-24.04`. Older ffmpeg (6.1.1), further from Arch, and the gate has never run on it with spec 17's fixtures.
- **C:** keep `ubuntu-latest`. No maintenance, but CI's ffmpeg and system libraries change with no commit. That already happened once: the v0.1.0 tag run (37965861632, 2026-10-09) got `ubuntu-24.04`, and v0.2.0's (42290009457, 2026-11-06) got `ubuntu-26.04`.

**D3. Time limits.**

- **A (recommended):** `gate` and `test` 15, AUR `deploy` 10, Pages 15, as justified in Design.
- **B:** `gate` and `test` 20, for headroom as the suite grows. It costs 5 more minutes on a real hang. Raising the limit later is a one-line change, so wait for evidence.

**D4. Bounding the apt step.**

- **A (recommended):** a 5-minute step `timeout-minutes` on both `Install ffmpeg` steps, and nothing else. The failure fails fast and names its step, and a re-run gets a new runner, as attempt 2 did. Two lines, ffmpeg unchanged, and it stays a distro build.
- **B:** A plus a retry inside the step that, on timeout, points apt at `archive.ubuntu.com` instead of the Azure mirror. It would recover on its own, but it rewrites `/etc/apt` sources (deb822 on 26.04) and can't be tested without a slow mirror. A plain retry on the same runner wouldn't help: the slow run was steady at 58 KB/s against the same host.
- **C:** cache the `.deb` files with `actions/cache`, keyed on `ImageOS` and `ImageVersion`. A cache hit skips the mirror for the downloads, but `apt-get update` still needs it. It's also a new action, and every image release starts cold.
- **D:** a static ffmpeg build (for example from BtbN's GitHub releases). Fast, with no Ubuntu mirror involved, but it's a third-party binary in the release workflow, a moving target, and built differently from both Ubuntu's and Arch's packages.
- **E:** run the gate in an `archlinux` container with pacman's ffmpeg. That's the closest to users, but it also changes Python (3.14) and the mirrors, which is row 16's job.

**D5. A regression test for the workflows.**

- **A (recommended):** `tests/test_workflows.py`, text-based, as in Design. It fails on today's files, so it meets "every fix ships with a test", it keeps `ubuntu-latest` and missing limits from creeping back, and it adds no dependency. To undo it, delete the file.
- **B:** the same checks with PyYAML added to the `dev` extra. Sturdier parsing, but it adds a pinned dev dependency.
- **C:** no test, relying on annotations in CI. Nothing would catch a regression until someone reads a run.

**D6. When the first real runs happen.** Only `release-manager` pushes, with the human's approval.

- **A (recommended):** the human authorizes a push of `main` after the merge and before validation, as in spec 03 D3. Validation then checks the `CI` and Pages criteria for real. The push also publishes the 3 local commits already waiting (`f4839f6` to `5c30ee5`, `.ai/` only), and it redeploys Pages with the same `docs/`.
- **B:** validate on local evidence only and let the next release's push prove everything. The item would close unproven, and the release would be the first run of all three changes at once.

## SemVer

None. Nothing the package installs changes: the workflows aren't in the wheel, and `PKGBUILD` doesn't use them. The commits are `ci:` and `test:`.
