"""CI invariants: pinned runners, time limits everywhere, Node 24 action majors, one gate.

Read as text on purpose: PyYAML isn't a dev dependency, and these files only use
plain block-style keys.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
GATE = ROOT / "scripts" / "pre-release.sh"

RUNNER = "ubuntu-26.04"
# Lowest major of each action that runs on Node 24. A new actions/* action must be added here.
MIN_MAJOR = {
    "checkout": 7,
    "setup-python": 7,
    "configure-pages": 6,
    "upload-pages-artifact": 5,
    "deploy-pages": 5,
}
GATE_JOBS = [("ci.yml", "gate"), ("deploy-aur.yml", "test")]
EXPECTED_JOBS = [
    ("ci.yml", "gate"),
    ("deploy-aur.yml", "test"),
    ("deploy-aur.yml", "deploy"),
    ("deploy-pages.yml", "deploy"),
]

_KEY = re.compile(r"^(?P<indent> *)(?P<dash>- +)?(?P<key>[\w-]+):(?: +(?P<value>.*))?$")


@dataclass
class Step:
    keys: dict[str, str] = field(default_factory=dict)
    run: str = ""


@dataclass
class Job:
    workflow: str
    name: str
    keys: dict[str, str] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(value: str | None) -> str:
    # 'actions/checkout@v4' is the same action to GitHub as the bare form.
    value = value or ""
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _parse(workflow: str, text: str) -> list[Job]:
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    start = lines.index("jobs:") + 1
    body = []
    for line in lines[start:]:
        if _indent(line) == 0:
            break
        body.append(line)

    jobs: list[Job] = []
    job_indent = _indent(body[0])
    key_indent: int | None = None
    step_indent: int | None = None
    in_steps = False
    run_indent: int | None = None
    for line in body:
        indent = _indent(line)
        if run_indent is not None and indent > run_indent:
            jobs[-1].steps[-1].run += line.strip() + "\n"
            continue
        run_indent = None
        match = _KEY.match(line)
        if indent == job_indent:
            assert match and match["value"] is None, line
            jobs.append(Job(workflow, match["key"]))
            key_indent = step_indent = None
            in_steps = False
            continue
        job = jobs[-1]
        if key_indent is None:
            key_indent = indent
        if indent == key_indent and match and not match["dash"]:
            in_steps = match["key"] == "steps"
            job.keys[match["key"]] = _scalar(match["value"])
            continue
        if not in_steps or not match:
            continue
        if match["dash"]:
            step_indent = indent + len(match["dash"])
            job.steps.append(Step())
        elif indent != step_indent:
            continue
        step = job.steps[-1]
        value = _scalar(match["value"])
        step.keys[match["key"]] = value
        if match["key"] == "run":
            step.run = "" if value in ("|", ">") else value + "\n"
            run_indent = step_indent
    return jobs


def _all_jobs() -> list[Job]:
    jobs: list[Job] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        jobs += _parse(path.name, path.read_text(encoding="utf-8"))
    return jobs


def _apt_steps(job: Job) -> list[Step]:
    return [step for step in job.steps if "apt-get" in step.run]


def timeout_problems(jobs: list[Job]) -> list[str]:
    return [
        f"{job.workflow}: job {job.name} has no timeout-minutes"
        for job in jobs
        if "timeout-minutes" not in job.keys
    ]


def runner_problems(jobs: list[Job]) -> list[str]:
    return [
        f"{job.workflow}: job {job.name} runs on {job.keys.get('runs-on')!r}, not {RUNNER}"
        for job in jobs
        if job.keys.get("runs-on") != RUNNER
    ]


def apt_timeout_problems(jobs: list[Job]) -> list[str]:
    return [
        f"{job.workflow}: job {job.name}, apt-get step {step.keys.get('name')!r} "
        "has no timeout-minutes"
        for job in jobs
        for step in _apt_steps(job)
        if "timeout-minutes" not in step.keys
    ]


def action_problems(jobs: list[Job]) -> list[str]:
    problems = []
    for job in jobs:
        for step in job.steps:
            uses = step.keys.get("uses", "")
            if not uses.startswith("actions/"):
                continue
            name, _, ref = uses.removeprefix("actions/").partition("@")
            where = f"{job.workflow}: job {job.name} uses {uses}"
            major = re.fullmatch(r"v(\d+)", ref)
            if name not in MIN_MAJOR:
                problems.append(f"{where}, which has no minimum major in MIN_MAJOR")
            elif not major:
                problems.append(f"{where}, which is not a major tag")
            elif int(major[1]) < MIN_MAJOR[name]:
                problems.append(f"{where}, below v{MIN_MAJOR[name]}")
    return problems


def test_every_job_has_a_time_limit() -> None:
    assert timeout_problems(_all_jobs()) == []


def test_every_job_runs_on_the_pinned_runner() -> None:
    assert runner_problems(_all_jobs()) == []


def test_every_apt_step_has_its_own_time_limit() -> None:
    assert apt_timeout_problems(_all_jobs()) == []


def test_every_action_is_at_its_node_24_major() -> None:
    assert action_problems(_all_jobs()) == []


def test_quoted_values_are_read_like_bare_ones() -> None:
    text = (
        "jobs:\n"
        "  build:\n"
        "    runs-on: 'ubuntu-26.04'\n"
        "    timeout-minutes: 5\n"
        "    steps:\n"
        "      - uses: 'actions/checkout@v4'\n"
        '      - uses: "actions/deploy-pages@v5"\n'
    )
    jobs = _parse("x.yml", text)
    assert runner_problems(jobs) == []
    assert action_problems(jobs) == ["x.yml: job build uses actions/checkout@v4, below v7"]


def test_the_parser_sees_every_workflow_and_job() -> None:
    jobs = _all_jobs()
    assert [(job.workflow, job.name) for job in jobs] == EXPECTED_JOBS
    assert sum(len(_apt_steps(job)) for job in jobs) == 2
    assert all(job.steps for job in jobs)


def test_the_gate_checks_formatting() -> None:
    lines = [line.strip() for line in GATE.read_text(encoding="utf-8").splitlines()]
    assert ".venv/bin/ruff format --check src tests" in lines


def test_ci_and_release_run_the_same_gate() -> None:
    # The checks live in pre-release.sh only; a workflow that inlines them drifts from it.
    jobs = {(job.workflow, job.name): job for job in _all_jobs()}
    for key in GATE_JOBS:
        runs = [step.run for step in jobs[key].steps]
        assert "scripts/pre-release.sh --no-live\n" in runs, key


def test_nothing_skips_or_softens_the_gate() -> None:
    # A gate job that can't fail, or a deploy that doesn't wait for it, publishes a broken tag.
    jobs = {(job.workflow, job.name): job for job in _all_jobs()}
    for key in GATE_JOBS:
        job = jobs[key]
        [step] = [step for step in job.steps if step.run == "scripts/pre-release.sh --no-live\n"]
        assert not {"if", "continue-on-error"} & (job.keys.keys() | step.keys.keys()), key
    assert jobs[("deploy-aur.yml", "deploy")].keys.get("needs") == "test"


def _run_gate(tmp_path: Path, fail: str = "<nothing>") -> tuple[int, str, list[str]]:
    """Run a copy of the gate against stub tools that log their arguments.

    The stub whose "tool args" starts with `fail` exits 1.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GATE, repo / "scripts" / "pre-release.sh")
    tools = repo / ".venv" / "bin"
    tools.mkdir(parents=True)
    log = tmp_path / "calls.log"
    for tool in ("ruff", "mypy", "pytest"):
        stub = tools / tool
        stub.write_text(
            "#!/bin/sh\n"
            f'echo "{tool} $*" >> "$GATE_LOG"\n'
            f'case "{tool} $*" in "$GATE_FAIL"*) exit 1;; esac\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    env = {**os.environ, "GATE_LOG": str(log), "GATE_FAIL": fail}
    found = subprocess.run(
        ["bash", str(repo / "scripts" / "pre-release.sh"), "--no-live"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = [line.rstrip() for line in log.read_text(encoding="utf-8").splitlines()]
    return found.returncode, found.stdout, calls


def test_the_gate_checks_formatting_between_lint_and_types(tmp_path: Path) -> None:
    code, out, calls = _run_gate(tmp_path)
    assert code == 0, out
    assert calls[:3] == ["ruff check src tests", "ruff format --check src tests", "mypy"]
    assert "==> format" in out


def test_a_misformatted_file_stops_the_gate_at_format(tmp_path: Path) -> None:
    code, out, calls = _run_gate(tmp_path, fail="ruff format")
    assert code == 1
    assert calls == ["ruff check src tests", "ruff format --check src tests"]
    assert "==> format" in out
    assert "==> types" not in out
    assert "All checks passed" not in out
