---
description: 'Adds an idea to the roadmap, or reviews it and recommends what comes next. Launches the planner.'
---

Act as the download-media orchestrator (read `.ai/roles/orchestrator.md`).

Idea (optional; if empty, the planner reviews the whole roadmap): $ARGUMENTS

1. Launch the `planner` subagent with the idea, if any, and any follow-up notes from recent reports.
2. Show me what changed in `.ai/specs/PLAN.md` and its recommendation.

Register nothing in `.ai/orchestration/state.json` and start no item.
