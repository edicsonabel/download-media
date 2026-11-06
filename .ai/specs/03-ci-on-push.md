# 03 — Run the gate on every push and PR

Status: draft
Item: `feature/ci-on-push`

## Problem

The gate only runs on `v*` tags and `workflow_dispatch` (`deploy-aur.yml`, job `test`), so a push to `main` or a PR runs no tests and a failure first shows up at release time.

## Goal

Every push to `main` and every PR gets a pass or fail from `scripts/pre-release.sh --no-live` on GitHub, and that gate is green on a fresh install, not only on the machine that wrote the code.

## Findings that shape this spec

Checked before designing, from a fresh clone of `86ca0d8`:

| Finding | Evidence |
| ------- | -------- |
| **The gate fails on a fresh install today.** A new workflow alone would be red from its first run. | `ubuntu:24.04`, Python 3.12.3, non-root, `pip install -e '.[dev]'` → ruff 0.17.4 → `SIM102` at `src/download_media/cli.py:327`, exit 1 |
| It passes locally only because the local tools are older. | Local `.venv`: ruff 0.16.10, mypy 2.4.0. `pyproject.toml` says `ruff>=0.6`, `mypy>=1.8`, with no lockfile |
| That lint error is the only failure on 3.12. | Same container: mypy clean, 321 passed in `es` and in `en` |
| The gate has never run on GitHub. | The commit that added the job (`ef82636`) is one of 7 not pushed. The last tag run, `v0.1.0` on 2026-10-09, has only a `deploy` job |
| Nothing in the feature pipeline pushes. | `constraints.md`, Process 6: only `release-manager` pushes, with human approval. So validation can't see a run on GitHub without a decision (D3) |

## Scope

### In

- A new workflow, `.github/workflows/ci.yml`, running the gate.
- The one-condition change in `cli.py` that the gate needs to pass on current ruff.
- Pinning `ruff` and `mypy` in the `dev` extra (pending D1).
- One line in the `AGENTS.md` layout tree.

### Out

- **Changing `deploy-aur.yml` or `deploy-pages.yml`,** including sharing the job through `workflow_call`. That would save about 15 duplicated lines, but it edits the publishing workflow, which can only be exercised by a real tag. The `test` job also stays: a tag can point at any commit.
- **Bumping the actions in those two workflows** (`checkout@v4`, `setup-python@v5`, both Node 20). `checkout@v4` still ran on 2026-10-09, after the announced removal date, so it isn't urgent. For the planner.
- **A Python version matrix.** On 3.10, the declared minimum, 2 e2e tests fail. That is a separate question for the planner (see the report). This item matches the `3.12` the release gate already uses.
- **Branch protection or required checks.** `main` is unprotected and has no rulesets, and merges happen locally, so CI reports a failure rather than preventing it. It's a repository setting, not code.
- **`ruff format` in the gate:** item 05.
- **Moving the `cli.py` hints to `t()`:** item 02. The text of the hint stays exactly as it is.
- **README badge, dependency caching, `concurrency`, path filters.** The gate takes about 30 seconds locally, and a path filter would break a future required check.
- **Pinning `yt-dlp`.** Staying on the latest is deliberate: it's what Arch users get, and CI should see upstream changes.

## Files

| Action | File                                                      |
| ------ | --------------------------------------------------------- |
| create | `.github/workflows/ci.yml`                                |
| modify | `src/download_media/cli.py` (`_hint_for_download_error`)  |
| modify | `pyproject.toml` (`dev` extra, pending D1)                |
| modify | `AGENTS.md` (layout tree)                                 |

Collision: item 02 rewrites the same hint in `cli.py`, and item 05 reformats that file. Neither is in flight. Whichever goes second rebases.

## Design

No `yt-dlp` behavior is involved, and no runtime dependency changes. `PKGBUILD` has no `check()` and doesn't use the `dev` extra, so packaging is unaffected.

### 1. `cli.py`

Merge the nested `if` that `SIM102` flags. **The parentheses are required:** `and` binds tighter than `or`, so without them the hint would also print when cookies are already set.

```python
    elif (
        "sign in to confirm you" in msg or "login required" in msg or "private" in msg
    ) and not opts.cookies_browser:
        warn("  → try again with --cookies <browser> (firefox, brave, chrome, ...)")
```

There's no `else` on that chain, so behavior doesn't change. `ruff format` leaves this form alone. No new test is needed: `test_hint_suggests_cookies_for_auth_errors` already fails on the unparenthesized version (proved by applying it: 1 failed, 320 passed).

### 2. `pyproject.toml` (pending D1)

```toml
    "ruff==0.17.4",
    "mypy==2.4.1",
```

Both tools can fail a commit that didn't change, just by releasing. `pytest` and `pytest-cov` stay as they are: a new version doesn't add findings to the code. After this lands, `main`'s `.venv` needs `.venv/bin/pip install -e '.[dev]'` before validation, or it keeps checking with the old versions.

### 3. `.github/workflows/ci.yml`

Use this content (it passes `actionlint` 1.7.12):

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  gate:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'

      - name: Install ffmpeg
        run: sudo apt-get update && sudo apt-get install -y ffmpeg

      - name: Set up the venv pre-release.sh expects
        run: |
          python -m venv .venv
          .venv/bin/pip install -e '.[dev]'

      - name: Run the gate
        run: scripts/pre-release.sh --no-live
```

Why it looks like this:

- **Triggers:** `branches: [main]` is pending D2. A bare `push:` would also fire on tags and duplicate the `test` job in `deploy-aur.yml`.
- **`pull_request`, never `pull_request_target`:** fork PRs run untrusted code, so they must get no secrets and a read-only token.
- **`permissions: contents: read`:** the gate writes nothing.
- **`timeout-minutes`:** the suite uses a loopback server, and the default limit for a hung job is 6 hours.
- **`@v7`:** the current major of both actions, declaring `node24` in their `action.yml`. The only `v7` removal that could matter is setup-python's `pip-install` input, which isn't used here.
- **Job name `gate`:** keep it stable, a future required check would refer to it.
- `scripts/pre-release.sh` is `100755` in git, so it runs directly.

### 4. `AGENTS.md`

Add to the tree under `.github/workflows/`, keeping the comment column aligned:

```
│   ├── ci.yml                     # main push and PRs → the gate, no live tests
```

### Commits

One logical change each, in this order, so the gate holds at every step:

1. `refactor(cli): merge nested condition in the cookies hint`
2. `build: pin ruff and mypy in the dev extra` (if D1 is A)
3. `ci: run the gate on main pushes and pull requests`

## Cases

The tool's behavior doesn't change, so what matters here is CI events:

| Event                                | Expected                                                        |
| ------------------------------------ | --------------------------------------------------------------- |
| push to `main`                       | `CI` runs                                                       |
| PR from a branch of this repo        | `CI` runs once                                                  |
| PR from a fork                       | `CI` runs with no secrets and a read-only token                 |
| push to a branch with no PR          | Nothing runs (pending D2)                                       |
| `v*` tag                             | Only `Deploy to AUR` runs, as today                             |
| commit touching only docs or `.ai/`  | `CI` runs, no path filter                                       |
| lint, type or test failure           | Job fails, because the script's non-zero exit fails the step    |

Runtime cases, for the one branch touched in `_hint_for_download_error`:

| Case                                 | Expected                                  |
| ------------------------------------ | ----------------------------------------- |
| auth error, no cookies               | Hint printed, as today                    |
| auth error, with cookies             | No cookies hint, as today                 |
| unrelated error                      | Silent, as today                          |

Wizard, direct, `-y`, `-p`, `-P`, `-l`, carousel, playlist, rerun and partial or total failure are removed: nothing on those paths changes.

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] The gate exits 0 on a fresh install: fresh clone of the branch, `ubuntu:24.04`, non-root user, `apt-get install python3 python3-venv ffmpeg git`, `python3 -m venv .venv`, `.venv/bin/pip install -e '.[dev]'`, then `scripts/pre-release.sh --no-live`
- [ ] In that same run, `.venv/bin/ruff --version` reports 0.17.4 or newer
- [ ] The gate can fail: adding `def test_red() -> None: assert 1 == 2` in a throwaway copy makes it exit non-zero **at the tests step**. Don't use `assert False`: ruff rejects it (`B011`), and the run stops at lint without reaching the tests
- [ ] `podman run --rm -v "$PWD:/repo:ro,Z" -w /repo docker.io/rhysd/actionlint:latest .github/workflows/ci.yml` exits 0
- [ ] `grep -nE 'pull_request_target|secrets\.|tags:' .github/workflows/ci.yml` finds nothing
- [ ] `git diff main -- tests .github/workflows/deploy-aur.yml .github/workflows/deploy-pages.yml` is empty: `test_hint_suggests_cookies_for_auth_errors` passes unmodified, and the publishing workflows are untouched
- [ ] `AGENTS.md` lists `ci.yml` in the layout tree
- [ ] (Depends on D3) `gh run list --workflow ci.yml --branch main --limit 1` shows `success` for the pushed commit

## Live check

No download check is needed: no download path changes. The equivalent here is the first real run on GitHub, the last criterion, and it matters because the steps above stand in for a real runner without being one. The container was not a GitHub image, and it used Ubuntu's Python 3.12.3 rather than `setup-python`'s. `checkout@v7` and `setup-python@v7` have never run for this repo.

## Open decisions

The design above is written for the recommended option in each case. The human's answer can change it.

**D1. Pin `ruff` and `mypy`?** This is what made the gate pass locally and fail on a fresh install.

- **A (recommended):** fix the line and pin `ruff==0.17.4`, `mypy==2.4.1`. CI only changes when a commit changes it. The cost is bumping the pins by hand now and then.
- **B:** fix the line and keep the floating ranges. Less to maintain, but any ruff release can turn CI red with no commit, as has just happened.

**D2. Which pushes?** The roadmap row says "every push and PR". The design narrows that to pushes to `main` plus PRs.

- **A (recommended):** `push: branches: [main]` + `pull_request`. One run per commit. A branch pushed with no PR runs nothing, and today's pipeline doesn't push branches anyway.
- **B:** `push: branches: ['**']` + `pull_request`. Literally every push, but a PR from a branch of this repo runs twice. Tags stay excluded either way.

**D3. How does the first real run happen?** Only `release-manager` pushes, with human approval, so validation can't observe CI by itself.

- **A (recommended):** the human authorizes a push of `main` after the merge and before validation, and the last criterion is checked for real. This also publishes the 7 commits already waiting (`b07cbe0` to `86ca0d8`), including `.ai/`. It triggers neither deploy workflow: `Deploy to AUR` needs a tag, and those commits don't touch `docs/`.
- **B:** validate on local evidence, drop the last criterion, and let the first run happen at the next push. The item closes unproven on GitHub, which is the same blind spot it exists to remove.
