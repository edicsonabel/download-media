---
description: 'Registers a bug and launches the architect to write a spec that starts from a reproduction.'
---

Act as the download-media orchestrator (read `.ai/roles/orchestrator.md`, section "Registering a new item").

Bug: $ARGUMENTS

1. If it's in `.ai/specs/PLAN.md`, use its id and number. If not, launch the `planner` to add it first.
2. Register the item `fix/<id>` in `.ai/orchestration/state.json` at stage `planning`, with `type: fix`.
3. Launch the `architect` subagent with the dispatch message. The spec must start from a reproduction; if it can't be reproduced, the result is `blocked`.
4. When it delivers the spec, leave the item in `awaiting_human`, summarize it for me and ask whether I approve it.
