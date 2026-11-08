# 05 — Format baseline

Status: draft
Item: `fix/format-baseline`

## Problem

`AGENTS.md` says code is formatted with `ruff format`, but nothing checks it, and six files have never been formatted. On `main` at `5ffc4e8`, with the pinned ruff in the venv (`.venv/bin/ruff --version` → `ruff 0.17.4`, matching `ruff==0.17.4` in `pyproject.toml`):

```
$ .venv/bin/ruff format --check src tests
...
6 files would be reformatted, 31 files already formatted        # exit 1
```

The six: `src/download_media/cli.py`, `clipboard.py`, `config.py`, `probe.py`, `wizard.py` and `tests/test_config.py`. `scripts/pre-release.sh --no-live` still passes, because its only ruff step is `ruff check` (`scripts/pre-release.sh:27`). It should fail until the files are formatted, then pass.

### Why they drift despite the pin

It isn't the ruff version. The venv, `pyproject.toml` and CI all use 0.17.4: CI installs `.[dev]`, which pins it (`88f3ede`). There is no other ruff config. `[tool.ruff]` sets `line-length = 100`, and `[tool.ruff.format]` only sets `quote-style = "double"` and `indent-style = "space"`, both defaults. No `ruff.toml` or `.ruff.toml` exists.

The cause is that the formatter never ran on this code, and no step checks it. The code is hand-laid-out: aligned columns (`("youtube",   re.compile(...))` in `probe.py`, the `dispatch` dict in `wizard.py`), several values per line in `argparse` calls and literals, hugged brackets (`frozenset({`), and signatures split by hand that fit in 100 columns. Black-style formatting has always rewritten every one of these, so any ruff version would flag them. The alignment in `probe.py` dates from the first Python commit (`6fd8fdd`, found with `git log -S`).

## Goal

Every Python file in `src` and `tests` is formatted by the pinned ruff. The gate (`scripts/pre-release.sh`) fails when one isn't, locally, in CI and before an AUR release. The gate stays the one place where this is checked.

## Scope

### In

- Run `ruff format` on the six files, with nothing else in that commit.
- Add a `format` step, `ruff format --check src tests`, to `scripts/pre-release.sh`.
- Tests that pin the step and pin that both CI gate jobs run `pre-release.sh`.
- The `AGENTS.md` "Verification" comment for the format command (open decision 2).
- `.git-blame-ignore-revs` with the formatting commit (open decision 1).

### Out

- New lint rules, or any change to `[tool.ruff]`/`[tool.ruff.format]`. That's a separate decision, and it would grow the diff.
- A ruff version bump. A future bump that changes the style has to reformat in the same commit. The new step makes that visible, so it needs no rule here.
- A pre-commit hook or editor config. That's tooling choice, not the gate.
- A step in `ci.yml` or `deploy-aur.yml`. Both already run `scripts/pre-release.sh --no-live` (`ci.yml` "Run the gate", `deploy-aur.yml` "Run the release gate"), so they get the check from the script. A second copy would be a second source of truth.
- `legacy/`. It isn't built, and the check only covers `src tests`, like `ruff check`.
- Any logic, comment or name change in the six files, even an obvious one. Put it in `notes`.

## Files

| Action | File                                   |
| ------ | -------------------------------------- |
| modify | `src/download_media/cli.py`            |
| modify | `src/download_media/clipboard.py`      |
| modify | `src/download_media/config.py`         |
| modify | `src/download_media/probe.py`          |
| modify | `src/download_media/wizard.py`         |
| modify | `tests/test_config.py`                 |
| modify | `scripts/pre-release.sh`               |
| modify | `tests/test_workflows.py`              |
| modify | `AGENTS.md` (one comment in Development) |
| create | `.git-blame-ignore-revs`               |

Collisions: the reformat touches `cli.py`, `probe.py` and `wizard.py` broadly, so no other item may be in flight with any of the six files. None is now. Row 04 (`stale-docs`) also edits `AGENTS.md`, but on other lines.

## Design

Three commits, in this order. In this order, every commit passes the gate, which keeps `git bisect` clean.

### Commit 1: `style: format the files ruff format flags`

```
.venv/bin/ruff format src tests
```

Run it with the pinned 0.17.4, and verify with `.venv/bin/ruff --version` first. It must change exactly the six files listed (`6 files reformatted, 31 files left unchanged`). Commit only that output, with no hand edits. The body says it's formatter output only, and names the ruff version.

**How the reviewer proves there's no logic change.** `ruff format` doesn't change the AST. `git diff -w` doesn't show that, because the formatter moves tokens across lines. The proof is to compare ASTs, file by file, between the commit's parent and the commit:

```
git diff --name-only <c>^ <c> -- '*.py' | while read f; do
  .venv/bin/python -I -c 'import ast,sys; a,b=(ast.dump(ast.parse(open(p).read())) for p in sys.argv[1:]); sys.exit(a!=b)' \
    <(git show <c>^:"$f") <(git show <c>:"$f") || echo "AST differs: $f"
done
```

It must print nothing. `ast.dump` leaves out line and column positions by default, so only layout may differ. A dry run on a copy of `5ffc4e8` gave 37 files and 0 ASTs differing. `ruff check`, `mypy` and `ruff format --check` also passed on that copy. The other proofs are that `git diff --stat <c>^ <c>` lists only the six files, and that the gate passes.

### Commit 2: `ci: check formatting in the release gate`

In `scripts/pre-release.sh`, right after the `lint` step and before `types`, so a cheap failure shows first:

```bash
step "format"
.venv/bin/ruff format --check src tests
```

`set -euo pipefail` (line 8) already stops the gate on a non-zero exit, as spec 10 confirmed when it dropped row 12.

Tests, in `tests/test_workflows.py`, whose docstring is about what CI runs:

- `test_the_gate_checks_formatting`: read `scripts/pre-release.sh` and assert that a stripped line equals `.venv/bin/ruff format --check src tests`. Without the step, it fails.
- `test_ci_and_release_run_the_same_gate`: with the existing `_all_jobs()` parser, assert that `ci.yml`/`gate` and `deploy-aur.yml`/`test` each have a step whose `run` is `scripts/pre-release.sh --no-live\n`. This is what makes the script the one source of truth. If someone later inlines the steps into a workflow, the test fails. On `main` the parser already returns exactly that for both jobs.

In `AGENTS.md`, in Development, keep the fixer command and say the gate checks it (open decision 2):

```
.venv/bin/ruff format src tests  # format (the gate runs it with --check)
```

### Commit 3: `chore: ignore the format commit in git blame`

Create `.git-blame-ignore-revs` at the repo root. GitHub's blame view reads it there automatically. Locally, it's opt-in through `git config blame.ignoreRevsFile .git-blame-ignore-revs`. The file holds:

```
# Formatter-only commits. Locally: git config blame.ignoreRevsFile .git-blame-ignore-revs
# style: format the files ruff format flags
<full 40-char sha of commit 1>
```

It needs commit 1's final SHA, so it comes last. The tech-lead merges with `--no-ff`, which keeps the SHA. If the branch is rebased before the merge, the developer updates the SHA. No test checks it: CI checks out at depth 1 and can't resolve old commits. Validation checks it with a command (see the criteria).

### Proving the gate fails without formatting

This is in addition to the text test, which only proves the line exists. Run it in a throwaway copy, so nothing is left in the item worktree:

```
P=<scratch>/gate-probe
git worktree add --detach "$P" HEAD
ln -s "$PWD/.venv" "$P/.venv"
printf 'x = [1,\n  2]\n' > "$P/tests/_fmt_probe.py"
"$P/scripts/pre-release.sh" --no-live; echo "gate=$?"   # ==> format fails, gate=1, no "==> types"
git worktree remove --force "$P"
git worktree list                                      # the probe is gone
```

This was dry-run on a `git archive` copy of `5ffc4e8`, with the six files formatted and the step added. With the probe file, it printed `==> lint` (passed), then `==> format` (`1 file would be reformatted`), then `gate=1`, and never reached `types`. Without the probe file, it printed `37 files already formatted` and went on to `types`.

## Cases

| Case                                  | Expected |
| ------------------------------------- | -------- |
| Gate on the branch                    | `==> format` prints `37 files already formatted` (more if tests are added), and the gate passes |
| Gate with a misformatted file under `src` or `tests` | Stops at `==> format` with exit 1, before `types` |
| CI on push/PR (`ci.yml`)              | Gets the step through `pre-release.sh --no-live`, with no workflow change |
| Tag push (`deploy-aur.yml` `test` job) | Same, through `pre-release.sh --no-live`. A misformatted tag doesn't publish |
| Full gate with live tests             | Same step. It runs before the `live` block |

Wizard, direct, `-y`, `-p`, `-P`, `-l`, cookies, single, carousel, playlist, rerun and failures don't apply. No runtime behavior changes, and commit 1 has the same AST.

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes on the branch and, after the merge, on `main`.
- [ ] `.venv/bin/ruff format --check src tests` exits 0 on the branch. `.venv/bin/ruff --version` prints `ruff 0.17.4`.
- [ ] Commit 1 is `style:`, and `git diff --stat <c1>^ <c1>` lists exactly the six files.
- [ ] The AST comparison in Design prints nothing for commit 1.
- [ ] `scripts/pre-release.sh` has a `format` step, `.venv/bin/ruff format --check src tests`, between `lint` and `types`.
- [ ] `test_the_gate_checks_formatting` fails with the step removed from `pre-release.sh`, and passes with it. Prove it by reverting the step.
- [ ] `test_ci_and_release_run_the_same_gate` passes, and fails if the `run:` of either gate job is changed.
- [ ] The throwaway-worktree probe in Design ends with `gate=1` at `==> format`, and afterwards `git worktree list` and `git status --porcelain` show nothing left behind.
- [ ] `ci.yml` and `deploy-aur.yml` are unchanged (`git diff main -- .github/` is empty).
- [ ] If decision 1 is taken: on `main`, the SHA in `.git-blame-ignore-revs` resolves (`git cat-file -t <sha>` → `commit`), its subject starts with `style:`, and `git blame --ignore-revs-file .git-blame-ignore-revs src/download_media/probe.py` attributes the `_SITE_PATTERNS` lines to commits before it.

## Live check

Not needed. Nothing changes at runtime: commit 1 has the same AST, and commits 2 and 3 touch only the gate, tests, docs and git metadata. CI on the PR or push is the real-world check that the step runs there.

## Open decisions

1. **`.git-blame-ignore-revs`.**
   - **Recommended: add it** (commit 3). The reformat rewrites about 200 lines across `cli`, `probe` and `wizard`. Without the file, `git blame` on those lines points at a layout-only commit, which hides the commit that wrote them. GitHub applies the file with no setup. The cost is one small file and keeping the SHA right if the branch is rebased.
   - Skip it. Blame on those lines shows the `style:` commit, and `git blame <c1>^ -- <file>` gets around it by hand.
2. **The format line in `AGENTS.md` Development.**
   - **Recommended: keep `ruff format src tests` and add `(the gate runs it with --check)` to its comment.** Developers run the fixer before committing, and the comment says where it's enforced.
   - Replace it with `ruff format --check src tests`. It matches the gate exactly, but then contributors have to work out the fixer command for themselves.
   - Leave `AGENTS.md` as it is. Accurate, but it doesn't say that formatting can now fail the gate.
3. **Where the tests live.**
   - **Recommended: `tests/test_workflows.py`.** It already parses the workflows, and the second test needs that parser.
   - `tests/test_packaging.py` ("release invariants"). The first test would fit there, but the second would have to import the workflow parser across modules.
