---
description: 'Starts a release: runs the full gate, proposes the version and stops for approval before publishing.'
---

Act as the download-media orchestrator (read `.ai/roles/orchestrator.md`, section "Registering a new item").

Version (optional; if empty, the release-manager works it out): $ARGUMENTS

1. Check you're in the main worktree, on `main`, with a clean tree (`.ai/orchestration/state.json` doesn't count), and that no item sits between `integration` and `validation`. If not, stop and say so.
2. Register the item `release/<version>` in `.ai/orchestration/state.json` at stage `preparation`.
3. Launch the `release-manager` subagent.
4. When it reports, leave the item in `awaiting_human`, show me the version and its evidence, the gate result, the notes and the pre-publish checklist, and ask whether to publish. Publishing can't be undone. Record in `history` my review of the delegated decisions and every live step I waive, by name, and pass both to the release-manager when launching `publishing`.
