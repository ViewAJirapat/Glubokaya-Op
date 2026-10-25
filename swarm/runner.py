"""One task, one worktree, one closed loop.

baseline  -> base must pass the gates and fail the hidden tests (else the task is invalid)
loop      -> agent edits; guard reverts protected edits and flags forbidden lines;
             gates + hidden tests run; failures are fed back together with the
             original task (every agent call starts with no memory)
pass      -> harness commits explicit paths; worktree removed, branch kept
fail      -> worktree and final.patch kept for inspection
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from swarm import archlint, git, guard, sandbox, verify
from swarm.config import Config, EngineSpec, Target
from swarm.engines import AgentContext, make_engine
from swarm.sandbox import HOST, Box
from swarm.task import Task
from swarm.util import clip, run_cmd

Log = Callable[[str], None]

PASSED, FAILED, ERROR, INVALID = "passed", "failed", "error", "invalid_task"
TB_FOR_MODE = {"full": "short", "summary": "line", "none": "no"}


@dataclass
class IterationRecord:
    n: int
    agent_seconds: float
    agent_returncode: int
    agent_timed_out: bool
    cost_usd: float | None
    changed: list[str]
    reverted: list[str]
    violations: list[str]
    folded_commits: bool
    stages: list[dict[str, object]]
    passed: bool


@dataclass
class RunSummary:
    run_id: str
    task_id: str
    target: str
    engine: str
    model: str | None
    branch: str
    base_sha: str = ""
    status: str = ERROR
    iterations: int = 0
    max_iterations: int = 0
    seconds: float = 0.0
    commit: str | None = None
    worktree: str | None = None  # set when the worktree is kept
    error: str | None = None
    cost_usd: float | None = None
    reverted_total: int = 0  # edits the guard reverted (protected paths, outside scope)
    pushed: bool = False
    pr_url: str | None = None
    sandbox: str = "none"
    records: list[IterationRecord] = field(default_factory=list)


class RunError(Exception):
    pass


def new_run_id(task: Task, spec: EngineSpec) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{task.id}--{spec.name}--{stamp}-{secrets.token_hex(2)}"


def build_prompt(task: Task, target: Target, attempt: int, feedback: str | None) -> str:
    gates = "\n".join(f"- `{verify.display(c)}`" for c in target.verify)
    lines = [f"# Task {task.id}: {task.title}", "", task.prompt, ""]
    if task.files:
        lines += ["Relevant files to start from: " + ", ".join(f"`{f}`" for f in task.files), ""]
    if target.context_files:
        shown = ", ".join(f"`{f}`" for f in target.context_files)
        lines += [f"Read-only reference (the API to code against): {shown}", ""]
    lines += [
        "## How your work is checked",
        "When you stop, the harness (not you) runs these from the repository root. "
        "Every command must exit 0:",
        gates,
    ]
    if target.architecture:
        lines.append("The harness also checks these import rules (a static check of every module):")
        lines += [f"- {rule}" for rule in archlint.describe_rules(target.architecture)]
    lines += [
        "Hidden acceptance tests for this task also run. You cannot see them, so implement "
        "exactly what the task describes, including the edge cases it mentions.",
    ]
    if target.autofix:
        fixers = ", ".join(f"`{verify.display(c)}`" for c in target.autofix)
        lines.append(f"Before checking, the harness auto-applies: {fixers}.")
    lines += [
        "",
        "## Rules the harness enforces",
        "- Edits to these paths are reverted automatically: "
        + ", ".join(f"`{p}`" for p in target.protected),
        "- Added lines matching these patterns block the change: "
        + ", ".join(f"`{p}`" for p in target.forbidden_added),
    ]
    if task.scope:
        lines.append(
            "- This task may only change these paths; edits anywhere else are reverted: "
            + ", ".join(f"`{p}`" for p in task.scope)
        )
    lines += [
        "- Do not commit, push or switch branches. The harness commits after the checks pass.",
        "- Work non-interactively: edit the files directly; nobody will answer questions.",
    ]
    if feedback:
        lines += [
            "",
            f"## Attempt {attempt - 1} did not pass",
            "Your earlier changes are still in the working tree. Fix the problems below and "
            "keep what already works.",
            "",
            feedback,
        ]
    return "\n".join(lines) + "\n"


def build_feedback(
    report: guard.GuardReport,
    stages: list[verify.StageResult],
    acceptance: verify.StageResult | None,
    budget: int,
    acceptance_mode: str,
) -> str:
    parts: list[str] = []
    if report.folded_commits:
        parts.append("You created commits; the harness turned them back into uncommitted edits.")
    if report.reverted:
        parts.append(
            "These edits were REVERTED (protected paths, or outside this task's scope). "
            "Solve the task without them:\n" + "\n".join(f"- {r}" for r in report.reverted)
        )
    if not report.changes:
        parts.append("No file changes remain. Edit the source files to complete the task.")
    if report.violations:
        parts.append(
            "These added lines are forbidden and must be removed:\n"
            + "\n".join(f"- {v}" for v in report.violations)
        )

    failed = [s for s in stages if not s.ok]
    acc_failed = acceptance is not None and not acceptance.ok
    shown = len(failed) + (1 if acc_failed and acceptance_mode != "none" else 0)
    per_stage = budget // max(1, shown)
    for s in failed:
        why = "timed out" if s.timed_out else f"exit {s.returncode}"
        parts.append(f"### FAILED: `{s.name}` ({why})\n```\n{clip(s.output, per_stage)}\n```")
    if acceptance is not None and acc_failed:
        header = f"### FAILED: hidden acceptance tests ({acceptance.detail})"
        if acceptance_mode == "none":
            parts.append(header + "\nOutput withheld. Re-read the task statement.")
        else:
            parts.append(f"{header}\n```\n{clip(acceptance.output, per_stage)}\n```")
    passing = [f"`{s.name}`" for s in stages if s.ok]
    if passing and (failed or acc_failed):
        parts.append("Already passing: " + ", ".join(passing))
    return "\n\n".join(parts)


def _stage_dicts(stages: list[verify.StageResult]) -> list[dict[str, object]]:
    return [
        {
            "name": s.name,
            "ok": s.ok,
            "seconds": s.seconds,
            "detail": s.detail,
            "timed_out": s.timed_out,
        }
        for s in stages
    ]


def _write_stage_log(path: Path, stages: list[verify.StageResult]) -> None:
    chunks = [
        f"===== {s.name} | ok={s.ok} rc={s.returncode} {s.seconds}s {s.detail}\n{s.output}"
        for s in stages
    ]
    path.write_text("\n".join(chunks), encoding="utf-8")


def check_baseline(
    wt: Path, task: Task, target: Target, cfg: Config, work: Path, box: Box = HOST
) -> str | None:
    """A task is only meaningful if base is green and the hidden tests fail on it."""
    work.mkdir(parents=True, exist_ok=True)
    timeout = cfg.settings.verify_timeout
    stages = verify.run_gates(wt, target, timeout, box)
    acceptance = verify.run_acceptance(wt, task, target, work, timeout, box=box)
    _write_stage_log(work / "baseline.log", stages + ([acceptance] if acceptance else []))
    red = [s.name for s in stages if not s.ok]
    if red:
        return f"base commit fails its own gates ({', '.join(red)}); see {work / 'baseline.log'}"
    if acceptance is None:
        return f"task {task.id} has no acceptance/ tests, so a no-op change would pass"
    if acceptance.ok:
        return "hidden acceptance tests already pass on base, so a no-op change would pass"
    return None


def _commit_message(task: Task, spec: EngineSpec, s: RunSummary, rec: IterationRecord) -> str:
    gates = "\n".join(f"- {st['name']}: ok {st['detail']}".rstrip() for st in rec.stages)
    attempted = (
        f"\nEdits reverted during the run (protected or out of scope): {s.reverted_total}\n"
        if s.reverted_total
        else ""
    )
    return (
        f"{task.type}({task.id}): {task.title}\n\n"
        f"Automated change by swarm engine {spec.label}.\n"
        f"Passed after {s.iterations} of {s.max_iterations} iteration(s).\n\n"
        f"Gates:\n{gates}\n{attempted}\nSwarm-Run: {s.run_id}\n"
    )


def _push(target: Target, task: Task, s: RunSummary, pr: bool, log: Log) -> None:
    """Runs with the harness's own credentials, only after the gate passed."""
    git.git(target.repo, "push", "-u", "origin", s.branch)
    s.pushed = True
    log(f"pushed {s.branch}")
    if not pr:
        return
    gh = shutil.which("gh")
    if not gh:
        log("gh not found; skipping PR")
        return
    body = (
        f"Automated change for task `{task.id}` by `{s.engine}` ({s.model}).\n\n"
        f"Passed the verify gates and hidden acceptance tests after {s.iterations} "
        f"iteration(s). Review before merging.\n\nSwarm-Run: `{s.run_id}`"
    )
    res = run_cmd(
        [
            gh,
            "pr",
            "create",
            "--head",
            s.branch,
            "--base",
            target.base,
            "--title",
            f"{task.type}({task.id}): {task.title}",
            "--body",
            body,
        ],
        target.repo,
        timeout=120,
    )
    if res.ok:
        s.pr_url = res.output.strip().splitlines()[-1]
        log(f"PR: {s.pr_url}")
    else:
        log(f"gh pr create failed:\n{res.output}")


def run_task(
    cfg: Config,
    target: Target,
    task: Task,
    spec: EngineSpec,
    *,
    max_iterations: int | None = None,
    keep_worktree: bool = False,
    push: bool = False,
    pr: bool = False,
    log: Log = print,
) -> RunSummary:
    settings = cfg.settings
    engine = make_engine(spec)
    box = sandbox.make_box(cfg, target)
    run_id = new_run_id(task, spec)
    run_dir = cfg.runs_dir / run_id
    run_dir.mkdir(parents=True)
    wt = cfg.worktrees_dir / target.name / run_id
    s = RunSummary(
        run_id=run_id,
        task_id=task.id,
        target=target.name,
        engine=spec.name,
        model=spec.model,
        branch=f"agent/{run_id}",
        max_iterations=max_iterations or settings.max_iterations,
        sandbox=box.kind,
    )

    def say(msg: str) -> None:
        log(f"[{task.id}] {msg}")

    started = time.monotonic()
    created = False
    try:
        s.base_sha = git.rev_parse(target.repo, target.base)
        git.ensure_excludes(target.repo, [".aider*"])
        git.add_worktree(target.repo, wt, s.branch, s.base_sha)
        created = True
        say(f"worktree {wt} on {s.branch} @ {s.base_sha[:10]}")
        if box is not HOST:
            where = "in a container" if engine.sandboxed else "on the host"
            say(f"sandbox {box.kind}: gates in containers, agent {where}")

        problem = verify.import_check(wt, target, box)
        if problem:
            raise RunError(problem)

        rules_file = None
        if target.rules_file:
            text = git.file_at(target.repo, s.base_sha, target.rules_file)
            if text is not None:  # from base, so an agent edit can't change its own rules
                rules_file = run_dir / "rules.md"
                rules_file.write_text(text, encoding="utf-8")

        if settings.baseline_check:
            say("baseline check")
            problem = check_baseline(wt, task, target, cfg, run_dir / "baseline", box)
            if problem:
                s.status, s.error = INVALID, problem
                return s

        tb = TB_FOR_MODE[settings.acceptance_feedback]
        feedback: str | None = None
        previous_diff = ""
        for n in range(1, s.max_iterations + 1):
            idir = run_dir / f"iter-{n}"
            idir.mkdir()
            prompt = build_prompt(task, target, n, feedback)
            prompt_file = idir / "prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")

            say(f"iteration {n}/{s.max_iterations}: running {spec.label}")
            ctx = AgentContext(
                wt,
                idir,
                prompt_file,
                rules_file,
                task,
                settings.agent_timeout,
                hidden=target.protected,
                read_only=target.context_files,
                box=box,
            )
            agent = engine.run(prompt, ctx)
            (idir / "agent.log").write_text(agent.res.output, encoding="utf-8")
            if agent.res.timed_out:
                say(f"agent timed out after {settings.agent_timeout}s (checking its edits anyway)")

            if target.autofix:
                fixes = verify.run_autofix(wt, target, settings.verify_timeout, box)
                _write_stage_log(idir / "autofix.log", fixes)
            report = guard.enforce(wt, s.base_sha, target, task.scope)
            s.reverted_total += len(report.reverted)
            if agent.error and not report.changes:
                # Auth failures, missing models, crashes: retrying only burns iterations.
                s.iterations = n
                raise RunError(f"agent failed without making changes: {agent.error}")
            stages: list[verify.StageResult] = []
            acceptance = None
            if report.changes:
                stages = verify.run_gates(wt, target, settings.verify_timeout, box)
                acceptance = verify.run_acceptance(
                    wt, task, target, idir, settings.verify_timeout, tb, box
                )
            all_stages = stages + ([acceptance] if acceptance else [])
            _write_stage_log(idir / "gates.log", all_stages)
            diff = git.diff_patch(wt, s.base_sha)
            (idir / "diff.patch").write_text(diff, encoding="utf-8")
            stuck = bool(diff) and diff == previous_diff
            previous_diff = diff

            passed = (
                bool(report.changes) and not report.violations and all(st.ok for st in all_stages)
            )
            rec = IterationRecord(
                n=n,
                agent_seconds=round(agent.res.seconds, 1),
                agent_returncode=agent.res.returncode,
                agent_timed_out=agent.res.timed_out,
                cost_usd=agent.cost_usd,
                changed=report.changed_paths,
                reverted=report.reverted,
                violations=report.violations,
                folded_commits=report.folded_commits,
                stages=_stage_dicts(all_stages),
                passed=passed,
            )
            s.records.append(rec)
            s.iterations = n
            if agent.cost_usd is not None:
                s.cost_usd = (s.cost_usd or 0.0) + agent.cost_usd
            line = (
                ", ".join(
                    f"{verify.short_name(st.name)}={'ok' if st.ok else 'FAIL'}" for st in all_stages
                )
                or "no changes"
            )
            if report.reverted:
                line += f" | reverted {len(report.reverted)} edit(s)"
            if report.violations:
                line += f" | {len(report.violations)} forbidden line(s)"
            say("  " + line)

            if passed:
                s.commit = git.commit(
                    wt, target.repo, report.changed_paths, _commit_message(task, spec, s, rec)
                )
                s.status = PASSED
                say(f"PASSED in {n} iteration(s); commit {s.commit[:10]} on {s.branch}")
                break
            feedback = build_feedback(
                report, stages, acceptance, settings.feedback_chars, settings.acceptance_feedback
            )
            if stuck:
                feedback = (
                    f"WARNING: attempt {n} produced exactly the same code as attempt {n - 1}. "
                    "Repeating it will fail again. Re-read the failures below and change the "
                    "logic that produces the wrong values.\n\n" + feedback
                )
            (idir / "feedback.md").write_text(feedback, encoding="utf-8")
        else:
            s.status = FAILED
            say(f"FAILED after {s.max_iterations} iteration(s)")
    except Exception as exc:  # recorded in summary.json; the worktree is kept
        s.status, s.error = ERROR, f"{type(exc).__name__}: {exc}"
        say(f"ERROR {s.error}")
    finally:
        s.seconds = round(time.monotonic() - started, 1)
        if created:
            if s.status == PASSED and not keep_worktree:
                git.remove_worktree(target.repo, wt)
            elif s.status == INVALID:
                git.remove_worktree(target.repo, wt)
                git.git(target.repo, "branch", "-D", s.branch, check=False)
            else:
                if s.status != PASSED:
                    try:
                        patch = git.diff_patch(wt, s.base_sha)
                        (run_dir / "final.patch").write_text(patch, encoding="utf-8")
                    except git.GitError:
                        pass
                s.worktree = str(wt)
        (run_dir / "summary.json").write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")

    if s.status == PASSED and push:
        try:
            _push(target, task, s, pr, say)
        except Exception as exc:
            say(f"push failed: {exc}")
        (run_dir / "summary.json").write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    return s
