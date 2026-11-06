#!/usr/bin/env python3
"""Connect .ai/ to Claude Code and OpenCode through symlinks.

.ai/ is the only source: roles, agents, commands and skills are edited there.
.claude/ and .opencode/ aren't versioned; this script regenerates them. It is
idempotent: what already points right is left alone, and anything else in the
way is reported, never touched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LINKS = [
    (".claude/agents", ".ai/agents/claude"),
    (".claude/commands", ".ai/commands"),
    (".opencode/agents", ".ai/agents/opencode"),
    (".opencode/commands", ".ai/commands"),
]


def skill_links() -> list[tuple[str, str]]:
    # One by one, not the folder: they coexist with externally installed skills.
    skills = ROOT / ".ai" / "skills"
    names = sorted(p.name for p in skills.iterdir()) if skills.is_dir() else []
    return [
        (f"{tool}/skills/{n}", f".ai/skills/{n}")
        for n in names
        for tool in (".claude", ".opencode")
    ]


def link(dest: str, src: str) -> bool:
    dest_path = ROOT / dest
    target = os.path.relpath(ROOT / src, dest_path.parent)
    if dest_path.is_symlink() and os.readlink(dest_path) == target:
        print(f"  ok      {dest}")
        return True
    if dest_path.exists() or dest_path.is_symlink():
        print(f"  exists  {dest} (not the expected symlink, left alone)", file=sys.stderr)
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.symlink_to(target)
    print(f"  created {dest} -> {target}")
    return True


def main() -> int:
    results = [link(dest, src) for dest, src in [*LINKS, *skill_links()]]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
