"""Release runbook invariants: the release-manager role file, read as text.

Each check pins a step of the 0.2.0 release that went wrong or nearly did (spec 15).
"""

from __future__ import annotations

import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parent.parent / ".ai" / "roles" / "release-manager.md"

PREFLIGHT = "Preflight"
PREPARATION = "Stage `preparation`"
PUBLISHING = "Stage `publishing`"
VERIFICATION = "Stage `verification`"
SECTIONS = [PREFLIGHT, PREPARATION, PUBLISHING, VERIFICATION]


def _text() -> str:
    return ROLE.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    sections: dict[str, str] = {}
    title = ""
    lines: list[str] = []
    for line in _text().splitlines():
        if line.startswith("## "):
            sections[title] = "\n".join(lines)
            title, lines = line[3:].strip(), []
        else:
            lines.append(line)
    sections[title] = "\n".join(lines)
    return sections


def _section(title: str) -> str:
    sections = _sections()
    assert title in sections, f"release-manager.md has no '## {title}' section"
    return sections[title]


def _assert_contains(text: str, needles: list[str], where: str) -> None:
    missing = [n for n in needles if n not in text]
    assert not missing, f"{where} doesn't mention {missing}"


@pytest.mark.parametrize("title", SECTIONS)
def test_sections_exist(title: str) -> None:
    _section(title)


def test_version_follows_agents_md() -> None:
    _assert_contains(_section(PREPARATION), ["AGENTS.md", "`Versioning`", "i18n.py"], PREPARATION)
    text = _text()
    stale = [arrow for arrow in ("→ patch", "→ minor", "→ major") if arrow in text]
    assert not stale, f"release-manager.md still maps commit types with {stale}"


def test_preflight_checks_write_access() -> None:
    _assert_contains(
        _section(PREFLIGHT),
        [
            "gh api user --jq .login",
            ".permissions.push",
            "ssh -T git@github.com",
            "git ls-remote --exit-code --tags",
        ],
        PREFLIGHT,
    )
    for line in _text().splitlines():
        if "gh auth switch" in line:
            assert "never" in line.lower(), f"'gh auth switch' without a 'never': {line!r}"


def test_publishing_order() -> None:
    section = _section(PUBLISHING)
    steps = [
        PREFLIGHT,
        "git push origin main",
        "gh run watch",
        'git commit -am "build: release',
        "git tag v",
        'git push --atomic origin main "v<version>"',
        "gh release create",
    ]
    _assert_contains(section, steps, PUBLISHING)
    for before, after in pairwise(steps):
        assert section.index(before) < section.index(after), (
            f"{PUBLISHING}: {before!r} must come before {after!r}"
        )
    release = [line for line in section.splitlines() if "gh release create" in line]
    assert any("--verify-tag" in line for line in release), (
        f"{PUBLISHING}: 'gh release create' has no --verify-tag"
    )


def test_publishing_needs_review_and_waivers() -> None:
    _assert_contains(_section(PUBLISHING), ["delegated", "waived"], PUBLISHING)


def test_verification_waits_for_the_tag_run_and_the_aur() -> None:
    _assert_contains(
        _section(VERIFICATION),
        [
            "headBranch",
            "Node.js 20",
            "workflow_dispatch",
            "cgit",
            "rpc/v5/info/download-media",
            "timeout 540",
        ],
        VERIFICATION,
    )
    assert "--limit 1" not in _text(), "release-manager.md picks a run with '--limit 1'"


def test_preparation_builds_the_checklist() -> None:
    _assert_contains(
        _section(PREPARATION), ["Live check", "skipped", "delegated", "Preflight"], PREPARATION
    )


def test_verification_runs_the_cgit_check() -> None:
    # "cgit" also appears in the prose, so pin the command itself.
    _assert_contains(
        _section(VERIFICATION),
        ["aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=download-media", "grep -x 'pkgver="],
        VERIFICATION,
    )


def test_tag_leaves_only_through_the_atomic_push() -> None:
    pushes = [
        line
        for line in _section(PUBLISHING).splitlines()
        if "git push" in line and "v<version>" in line
    ]
    assert pushes, f"{PUBLISHING} never pushes the tag"
    lone = [line for line in pushes if "--atomic" not in line]
    assert not lone, f"{PUBLISHING} pushes the tag without --atomic: {lone}"


def test_version_evidence_sees_every_message_key() -> None:
    from download_media import i18n

    line = next(
        (ln for ln in _section(PREPARATION).splitlines() if ln.strip().startswith("keys()")), ""
    )
    assert line, f"{PREPARATION} has no keys() helper"
    pattern = line.split("grep -oE '", 1)[1].split("'", 1)[0]
    found = subprocess.run(
        ["grep", "-oE", pattern, i18n.__file__], capture_output=True, text=True, check=False
    ).stdout
    seen = {k.strip().strip('":') for k in found.splitlines()}
    expected = set(i18n._MESSAGES["en"]) | set(i18n._MESSAGES["es"])
    missing = sorted(expected - seen)
    assert not missing, f"keys() misses message keys {missing}"


def test_preparation_checks_live_env_before_sourcing_it() -> None:
    # Sourcing a missing file fails the shell before the intended `blocked` result.
    lines = _section(PREPARATION).splitlines()
    source = next((i for i, ln in enumerate(lines) if ". .ai/live.env" in ln), None)
    assert source is not None, f"{PREPARATION} never sources .ai/live.env"
    checks = [ln for ln in lines[:source] if "-f .ai/live.env" in ln]
    assert checks, f"{PREPARATION} sources .ai/live.env before checking that it exists"


def test_commands_avoid_zsh_modifiers() -> None:
    # The release-manager's shell may be zsh, where "$1:src" reads ":s" as a modifier
    # and fails with "bad substitution". "${1}:src" works in bash and zsh alike.
    text = _text()
    code = re.findall(r"```.*?```", text, flags=re.DOTALL)
    code += re.findall(r"`[^`\n]+`", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
    hits = [m for block in code for m in re.findall(r"\$\w+:[A-Za-z]\S*", block)]
    assert not hits, f"release-manager.md has zsh-unsafe expansions {hits}"
