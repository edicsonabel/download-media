---
description: 'Registers a new feature and launches the architect to write its spec.'
---

Act as the download-media orchestrator (read `.ai/roles/orchestrator.md`, section "Registering a new item").

Feature: $ARGUMENTS

1. If it's in `.ai/specs/PLAN.md`, use its id and number. If not, launch the `planner` to add it first.
2. Register the item `feature/<id>` in `.ai/orchestration/state.json` at stage `planning`.
3. Launch the `architect` subagent with the dispatch message.
4. When it delivers the spec, leave the item in `awaiting_human`, summarize the spec for me and ask whether I approve it.
