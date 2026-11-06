---
description: 'Advances the agent pipeline: launches the role each item needs, up to the next human point.'
---

Act as the download-media orchestrator.

1. Read `.ai/context/project.md`, `.ai/context/constraints.md`, `.ai/orchestration/WORKFLOW.md` and `.ai/roles/orchestrator.md`, and follow that role.
2. Check you're in the main worktree, on `main`, with a clean tree (`.ai/orchestration/state.json` doesn't count). If not, stop and say so.
3. Read `.ai/orchestration/state.json` and run the role's loop.

Launch each role as a subagent: in Claude Code with the `Agent` tool (`subagent_type` = role name); in OpenCode with the `task` tool.

Specific item (optional; if empty, advance all): $ARGUMENTS
