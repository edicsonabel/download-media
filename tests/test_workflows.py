"""CI invariants: pinned runners, time limits everywhere, Node 24 action majors.

Read as text on purpose: PyYAML isn't a dev dependency, and these files only use
plain block-style keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

RUNNER = "ubuntu-26.04"
# Lowest major of each action that runs on Node 24. A new actions/* action must be added here.
MIN_MAJOR = {
    "checkout": 7,
    "setup-python": 7,
    "configure-pages": 6,
    "upload-pages-artifact": 5,
    "deploy-pages": 5,
}
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
            job.keys[match["key"]] = match["value"] or ""
            continue
        if not in_steps or not match:
            continue
        if match["dash"]:
            step_indent = indent + len(match["dash"])
            job.steps.append(Step())
        elif indent != step_indent:
            continue
        step = job.steps[-1]
        value = match["value"] or ""
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


def test_the_parser_sees_every_workflow_and_job() -> None:
    jobs = _all_jobs()
    assert [(job.workflow, job.name) for job in jobs] == EXPECTED_JOBS
    assert sum(len(_apt_steps(job)) for job in jobs) == 2
    assert all(job.steps for job in jobs)
