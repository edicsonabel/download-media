# Workflow: parallel agents with git worktrees

One orchestrator, two pipelines, and a planner ahead of them. Each item advances through stages, each stage belongs to one role, and each feature or fix lives in its own worktree so several can move without colliding.

```
          /roadmap ──► PLANNER ──► .ai/specs/PLAN.md   (what comes next, ranked)
                                        │  the human picks an item
                                        ▼
                     .ai/orchestration/state.json   ← the truth
                                 │
                       ORCHESTRATOR (/orchestrate)
                    reads state → launches role → updates
                                 │
          ┌──────────────────────┴───────────────────────┐
          ▼                                              ▼
   FEATURE / FIX                                  RELEASE
   .worktrees/<type>-<id>                         main worktree

   planning      architect                        preparation   release-manager
       ⏸ human approves the spec                      ⏸ human approves publishing
   development   developer  ◄──────────┐          publishing    release-manager
   testing       test-engineer ────────┤          verification  release-manager
   qa_review     qa-reviewer ──────────┘          published
       ⏸ human approves the merge    changes_requested
   integration   tech-lead
   validation    architect
   complete
```

`⏸` = the orchestrator stops and asks. There is no other way past those points.

---

## Roles

| Role              | Pipeline      | Stages                                | Edits                                   |
| ----------------- | ------------- | ------------------------------------- | --------------------------------------- |
| `planner`         | none          | `/roadmap`                            | `.ai/specs/PLAN.md`                     |
| `architect`       | feature / fix | planning, validation                  | `.ai/specs/`                            |
| `developer`       | feature / fix | development                           | its worktree                            |
| `test-engineer`   | feature / fix | testing                               | `tests/` in its worktree                |
| `qa-reviewer`     | feature / fix | qa_review                             | nothing                                 |
| `tech-lead`       | feature / fix | integration                           | `main` (merge)                          |
| `release-manager` | release       | preparation, publishing, verification | versions, `.ai/releases/`, tags, remote |
| orchestrator      | all           | every transition                      | `state.json`, worktrees                 |

Each role is defined in `.ai/roles/<role>.md`. The files in `.ai/agents/` are only per-tool wrappers.

---

## State (`state.json`)

One `items` map, keyed by branch (or by `release/<version>`):

```json
{
  "version": 1,
  "updated": "2026-11-06T10:00:00-04:00",
  "items": {
    "feature/instagram-images": {
      "type": "feature",
      "title": "Download carousel images",
      "stage": "testing",
      "status": "in_progress",
      "next_agent": "test-engineer",
      "doc": ".ai/specs/01-instagram-images.md",
      "branch": "feature/instagram-images",
      "worktree": ".worktrees/feature-instagram-images",
      "commits": ["a1b2c3d"],
      "review": null,
      "history": [
        { "stage": "planning", "agent": "architect", "result": "done", "at": "2026-11-06T09:00:00-04:00" },
        { "stage": "planning", "agent": "human", "result": "approved", "at": "2026-11-06T09:10:00-04:00" },
        { "stage": "development", "agent": "developer", "result": "done", "at": "2026-11-06T09:50:00-04:00" }
      ]
    }
  }
}
```

| Field        | What it is                                                                                                                                         |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`       | `feature`, `fix` or `release`                                                                                                                      |
| `stage`      | Current stage (see the diagram)                                                                                                                    |
| `status`     | `awaiting_human`, `in_progress`, `in_review`, `changes_requested`, `approved`, `merged`, `validated`, `validation_failed`, `blocked`, `published` |
| `next_agent` | Role that must run now, `human`, or `null` if finished                                                                                             |
| `doc`        | Spec (`.ai/specs/NN-<id>.md`) or release notes (`.ai/releases/<version>.md`)                                                                       |
| `review`     | Latest review result: `{ "approved": bool, "findings": [...] }`                                                                                    |
| `history`    | Every transition, including human decisions. Never deleted                                                                                         |

**Only the orchestrator writes this file,** and only from the main worktree. Worktrees hold an old copy nobody touches, so merges never conflict on it.

---

## Transitions

### Feature / fix

| From → to                    | Trigger                            | Orchestrator action                                       |
| ---------------------------- | ---------------------------------- | --------------------------------------------------------- |
| `planning` → ⏸               | architect delivers the spec        | `status: awaiting_human`. Asks to approve the spec        |
| ⏸ → `development`            | human approves                     | Creates worktree and branch. Launches developer           |
| `development` → `testing`    | developer: `done` with commits     | Launches test-engineer                                    |
| `testing` → `qa_review`      | test-engineer: `done`              | Launches qa-reviewer                                      |
| `testing` → `development`    | test-engineer: `changes_requested` | Stores findings in `review`. Relaunches developer         |
| `qa_review` → `development`  | qa: `changes_requested`            | Stores findings in `review`. Relaunches developer         |
| `qa_review` → ⏸              | qa: `approved`                     | `status: awaiting_human`. Asks to approve the merge       |
| ⏸ → `integration`            | human approves                     | Launches tech-lead                                        |
| `integration` → `validation` | tech-lead: `merged`                | Launches architect in validation mode                     |
| `validation` → `complete`    | architect: `validated`             | Removes worktree and branch. Reports                      |
| `validation` → ⏸             | architect: `validation_failed`     | Asks what to do (revert, new item, accept)                |

After a `changes_requested` from `testing`, the item returns to `testing` before `qa_review`, so the test-engineer can confirm the fix. After one from `qa_review`, it goes back through `testing` as well: new code gets the same scrutiny.

### Release

| From → to                      | Trigger                        | Orchestrator action                                    |
| ------------------------------ | ------------------------------ | ------------------------------------------------------ |
| `preparation` → ⏸              | release-manager: `done`        | `status: awaiting_human`. Shows version, gate, notes and the pre-publish checklist |
| ⏸ → `publishing`               | human approves, having reviewed the delegated decisions and waived by name any live step not run | Records the review and the waivers in `history`. Launches release-manager with them |
| `publishing` → `verification`  | release-manager: `done`        | Launches release-manager                               |
| `verification` → `published`   | release-manager: `published`   | Reports the AUR version                                |
| any → ⏸                        | release-manager: `blocked`     | `status: awaiting_human`. A publishing step is never retried without asking |

---

## Every role's report

No role touches `state.json`. They end with this block, and the orchestrator reads it:

```
## REPORT
item: feature/instagram-images
role: developer
result: done            # done | approved | changes_requested | merged | validated | validation_failed | published | blocked
commits: [a1b2c3d]
findings: []            # reviews only: [{severity, file:line, description}]
evidence: scripts/pre-release.sh --no-live → passed, 321 tests
notes: Images without a thumbnail are skipped; worth a roadmap row.
```

`blocked` is for when the role can't continue without the human (a missing decision, access or dependency). The orchestrator moves it to `awaiting_human` with the note.

---

## Worktrees

```bash
# The orchestrator creates them on entering development
git worktree add .worktrees/feature-<id> -b feature/<id> main
git worktree add .worktrees/fix-<id> -b fix/<id> main

# Each worktree needs its own venv; the developer sets it up
python -m venv .venv && .venv/bin/pip install -e '.[dev]'

# What's in flight
git worktree list

# The orchestrator removes them on completion
git worktree remove --force .worktrees/feature-<id>
git branch -d feature/<id>
```

`--force` is there because the worktree's `.venv` counts as untracked content. Check `git status` in the worktree first: anything besides `.venv` means work would be lost.

`.worktrees/` is in `.gitignore`, and outside what ruff, mypy, pytest and the wheel read.

### Parallelism

Items can advance together, since each has its worktree. What is **not** parallelized:

- Two merges into `main`.
- Two roles on the same item.
- A release and an integration.
- Live tests of two items at once: Instagram rate-limits by IP.

---

## Commands

| Command              | What it does                                                    |
| -------------------- | --------------------------------------------------------------- |
| `/roadmap [idea]`    | Adds an idea to the plan, or reviews it and proposes what's next |
| `/feature <id/idea>` | Registers a feature and launches the architect for the spec     |
| `/fix <id/bug>`      | Same, for a bug, starting from a reproduction                   |
| `/orchestrate [item]` | Advances everything possible up to the next human point        |
| `/pipeline`          | Table of every item and what it waits for                       |
| `/release`           | Starts the release pipeline                                     |

Approving a ⏸ is as simple as answering the orchestrator ("approved", "yes, merge"). It records that in `history` with `agent: human`.
