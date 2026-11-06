# Agent system

Context engineering for download-media: the project context, the roles and the process live here, versioned with the code, so no conversation has to start by explaining them. Claude Code and OpenCode read the same files.

Adapted from the Riquisano setup, itself based on [pivelcode/nexo-multiagentes](https://github.com/pivelcode/nexo-multiagentes) (roles + specs + orchestrator + git worktrees).

## Layout

```
.ai/
├── context/             what the project is and what is not negotiable
│   ├── project.md
│   └── constraints.md
├── roles/               each role's definition, the only source
│   ├── orchestrator.md  planner.md
│   ├── architect.md     developer.md     test-engineer.md
│   └── qa-reviewer.md   tech-lead.md     release-manager.md
├── agents/              per-tool wrappers: frontmatter + "read your role"
│   ├── claude/          → .claude/agents
│   └── opencode/        → .opencode/agents
├── commands/            /roadmap /feature /fix /orchestrate /pipeline /release
│                        → .claude/commands and .opencode/commands
├── orchestration/
│   ├── WORKFLOW.md      stages, transitions, worktrees, report format
│   └── state.json       every item's state (only the orchestrator writes it)
├── specs/               PLAN.md (roadmap) + one spec per item
├── releases/            release notes, one file per version
└── live.env.example     settings for the live tests (copy to live.env)
```

## Setup

`.claude/` and `.opencode/` are not versioned: they are symlinks into this folder. After cloning:

```bash
python scripts/ai-link.py
cp .ai/live.env.example .ai/live.env    # then fill it in; it is gitignored
```

Linking is idempotent; run it again after adding a skill. Restart the tool so it picks up the agents.

## Usage

```
/roadmap download carousel images      → planner adds it to PLAN.md, ranked
/roadmap                               → planner reviews the roadmap, proposes what's next

/feature instagram-images              → architect writes the spec → ⏸ you approve
/fix wrong-count-on-reruns             → same pipeline, spec starts from a reproduction
/orchestrate                           → developer → test-engineer → qa-reviewer
                                         → ⏸ you approve the merge
                                         → tech-lead → architect validates → complete

/release                               → release-manager runs the gate, drafts notes
                                         → ⏸ you approve publishing
                                         → bump, tag, push, GitHub release, AUR check

/pipeline                              → what's in flight and what each item waits for
```

The orchestrator is the main session: subagents can't launch subagents. In OpenCode you can also switch to the primary `orchestrator` agent with Tab.

`/plan` and `/status` are built-in Claude Code commands, hence `/roadmap` and `/pipeline`.

## Why it works this way

- **One role, one file.** The wrappers in `agents/` hold no logic: change `roles/qa-reviewer.md` and both tools follow.
- **Whoever reviews doesn't edit.** `qa-reviewer` has editing blocked: it sends findings back.
- **Whoever builds doesn't sign off.** `test-engineer` attacks what `developer` built, and `qa-reviewer` checks it against the spec.
- **State lives in a file, not in the model's memory.** Close the session, open another tool, and `/orchestrate` resumes.
- **Worktrees.** Each item has its own folder and branch, so several can advance without colliding.
- **You decide at the ⏸.** No agent merges or publishes without your answer.

## Maintenance

- New role: `roles/<role>.md`, a wrapper in `agents/claude/` and in `agents/opencode/` (copy one, change name, description and permissions), and add it to `orchestration/WORKFLOW.md`.
- New command: `commands/<name>.md` with only `description` in the frontmatter, which is what both tools understand. Check it doesn't collide with a built-in.
- Project conventions stay in `AGENTS.md`. Roles point there instead of copying them.
