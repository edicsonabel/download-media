# NN — Item name

Status: draft · approved · done
Item: `feature/<id>` or `fix/<id>`

## Problem

[What it solves, in one sentence. For a fix: the exact command, what happened, what should happen.]

## Goal

[What changes for the user when it's done]

## Scope

### In

- [thing 1]

### Out

- [excluded thing, and why]

## Files

| Action | File                            |
| ------ | ------------------------------- |
| create | `src/download_media/...`        |
| modify | `src/download_media/cli.py`     |

## Design

[How it's done: which `yt-dlp` options, confirmed in its source, and which modules. Just enough that the developer doesn't guess.]

## Cases

| Case                        | Expected |
| --------------------------- | -------- |
| wizard                      |          |
| direct, `-y`, `-p`, `-P`    |          |
| `-l`                        |          |
| with / without cookies      |          |
| single, carousel, playlist  |          |
| rerun                       |          |
| partial and total failure   |          |

Remove the rows that don't apply, and say why.

## Acceptance criteria

- [ ] `scripts/pre-release.sh --no-live` passes
- [ ] [criterion verifiable by a test or a command]

## Live check

[The real command on a real URL, and what must be on disk. Or "not needed", and why.]

## Open decisions

[What needs the human: a dependency, a default, a breaking change. Empty if none.]
