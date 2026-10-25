"""Deterministic gates: the target's verify commands plus the task's hidden acceptance tests.

Acceptance tests live in the harness (tasks/<target>/<id>/acceptance), never in the
worktree. Each run copies them to the run directory and points pytest at them with the
worktree on PYTHONPATH, so the agent can neither read nor edit them.
"""

from __future__ import annotations

import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from swarm import archlint
from swarm.config import Target
from swarm.sandbox import CHECK, FIX, HOST, Box, Mounts
from swarm.task import Task
from swarm.util import CmdResult


@dataclass
class StageResult:
    name: str
    ok: bool
    returncode: int
    seconds: float
    output: str
    timed_out: bool = False
    detail: str = ""

    @classmethod
    def from_cmd(cls, name: str, res: CmdResult, detail: str = "") -> StageResult:
        return cls(
            name, res.ok, res.returncode, round(res.seconds, 2), res.output, res.timed_out, detail
        )


ARCHITECTURE = "architecture rules"


def expand(cmd: list[str], python: str) -> list[str]:
    return [python if part == "{python}" else part for part in cmd]


def display(cmd: list[str]) -> str:
    return " ".join("python" if part == "{python}" else part for part in cmd)


def short_name(name: str) -> str:
    """'python -m ruff format --check .' -> 'ruff format'; for progress lines."""
    if name == "hidden acceptance tests":
        return "acceptance"
    if name == ARCHITECTURE:
        return "architecture"
    words = name.removeprefix("python -m ").split()
    return " ".join(w for w in words[:2] if not w.startswith("-") and w != ".")


def env_for(wt: Path, target: Target, box: Box) -> dict[str, str]:
    """Verification executes agent-written code; the box runs it with the same locked-down
    env as the agent. PYTHONPATH points at the worktree so imports never resolve to another
    checkout."""
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    if target.pythonpath:
        env["PYTHONPATH"] = box.pathsep.join(box.in_worktree(wt, p) for p in target.pythonpath)
    return env


def import_check(wt: Path, target: Target, box: Box = HOST) -> str | None:
    """Return an error if the target package would be imported from outside the worktree
    (classic cause: an editable install pointing at the main checkout)."""
    if not target.import_check:
        return None
    probe = (
        "import importlib.util, sys; s = importlib.util.find_spec(sys.argv[1]); "
        "print(s.origin if s and s.origin else '')"
    )
    cmd = [box.python, "-c", probe, target.import_check]
    res = box.run(cmd, wt, role=CHECK, env=env_for(wt, target, box), timeout=60)
    origin = res.output.strip().splitlines()[-1] if res.output.strip() else ""
    if not res.ok or not origin:
        return f"cannot import {target.import_check!r} from the worktree:\n{res.output}"
    if not box.contains(wt, origin):
        return (
            f"{target.import_check!r} resolves to {origin}, outside the worktree {wt}. "
            "Tests would exercise the wrong code (editable install of another checkout?)."
        )
    return None


def _run_all(
    cmds: list[list[str]], wt: Path, target: Target, timeout: float, box: Box, role: str
) -> list[StageResult]:
    env = env_for(wt, target, box)
    return [
        StageResult.from_cmd(
            display(cmd), box.run(expand(cmd, box.python), wt, role=role, env=env, timeout=timeout)
        )
        for cmd in cmds
    ]


def run_autofix(wt: Path, target: Target, timeout: float, box: Box = HOST) -> list[StageResult]:
    """Deterministic fixers (formatters, import sorters) the harness applies after each
    agent edit, like a pre-commit hook. Their exit codes don't matter; the gates decide."""
    return _run_all(target.autofix, wt, target, timeout, box, FIX)


def run_architecture(wt: Path, target: Target) -> StageResult | None:
    """Static import rules. Parses files without executing them, so it runs on the host
    even for sandboxed targets."""
    if target.architecture is None:
        return None
    started = time.monotonic()
    result = archlint.check(wt, target.architecture)
    seconds = round(time.monotonic() - started, 2)
    detail = "" if result.ok else f"{len(result.violations)} violation(s)"
    out = archlint.report(result)
    return StageResult(ARCHITECTURE, result.ok, 0 if result.ok else 1, seconds, out, False, detail)


def run_gates(wt: Path, target: Target, timeout: float, box: Box = HOST) -> list[StageResult]:
    """Run every gate (no short-circuit) so one iteration surfaces all failures."""
    stages = _run_all(target.verify, wt, target, timeout, box, CHECK)
    arch = run_architecture(wt, target)
    return stages + ([arch] if arch else [])


def _junit_counts(path: Path) -> dict[str, int] | None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    keys = ("tests", "failures", "errors", "skipped")
    return {k: sum(int(s.get(k, "0")) for s in suites) for k in keys}


def run_acceptance(
    wt: Path,
    task: Task,
    target: Target,
    work_dir: Path,
    timeout: float,
    tb: str = "short",
    box: Box = HOST,
) -> StageResult | None:
    if task.acceptance_dir is None:
        return None
    acc = work_dir / "acceptance"
    shutil.rmtree(acc, ignore_errors=True)
    shutil.copytree(task.acceptance_dir, acc, ignore=shutil.ignore_patterns("__pycache__"))
    junit = work_dir / "acceptance.xml"
    junit.unlink(missing_ok=True)

    io = Mounts(box)
    tests = io.input(acc)  # read-only in the sandbox
    cmd = [
        box.python,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "--rootdir",
        box.workdir(wt),
        "--confcutdir",
        tests,
        f"--tb={tb}",
        "--junitxml",
        io.output(junit),
    ]
    if (wt / "pyproject.toml").is_file():
        cmd += ["-c", box.in_worktree(wt, "pyproject.toml")]
    cmd.append(tests)
    env = env_for(wt, target, box)
    res = box.run(cmd, wt, role=CHECK, env=env, mounts=io.items, timeout=timeout)

    counts = _junit_counts(junit)
    name = "hidden acceptance tests"
    if counts is None:
        return StageResult(
            name,
            False,
            res.returncode,
            round(res.seconds, 2),
            res.output,
            res.timed_out,
            "no test report (crash or timeout)",
        )
    bad = counts["failures"] + counts["errors"] + counts["skipped"]
    passed = counts["tests"] - bad
    detail = f"{passed}/{counts['tests']} passed"
    if counts["skipped"]:
        detail += f", {counts['skipped']} skipped (skips count as failures)"
    stage = StageResult.from_cmd(name, res, detail)
    stage.ok = res.ok and counts["tests"] > 0 and bad == 0
    return stage
