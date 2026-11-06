---
description: 'Shows the agent pipeline: every item, its stage and what it waits for.'
---

Read `.ai/orchestration/state.json` and `git worktree list`, and show the table in the "Output" section of `.ai/roles/orchestrator.md`.

Flag any inconsistency: a worktree with no item, an item with no worktree at a stage that needs one, or an already merged branch still open.

Launch no role and change nothing.

Filter (optional): $ARGUMENTS
