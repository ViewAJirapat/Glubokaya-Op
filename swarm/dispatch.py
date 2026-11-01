"""Many tasks: parallel dispatch, benchmarks, integration merges, cleanup, demo setup."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from swarm import git, sandbox, verify
from swarm.config import Config, EngineSpec, Target
from swarm.engines import make_engine
from swarm.runner import ERROR, PASSED, RunSummary, run_task
from swarm.task import Task, find_tasks
from swarm.util import force_rmtree, run_cmd

Log = Callable[[str], None]


def run_many(
    cfg: Config,
    target: Target,
    tasks: list[Task],
    spec: EngineSpec,
    *,
    parallel: int = 1,
    repeat: int = 1,
    log: Log = print,
    **kwargs: Any,
) -> list[RunSummary]:
    box = sandbox.make_box(cfg, target)
    box.preflight()  # fail before creating any worktree
    make_engine(spec).preflight(box)
    jobs = [t for t in tasks for _ in range(repeat)]
    if parallel <= 1:
        return [run_task(cfg, target, t, spec, log=log, **kwargs) for t in jobs]
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(run_task, cfg, target, t, spec, log=log, **kwargs) for t in jobs]
        return [f.result() for f in futures]


def _fmt_seconds(sec: float) -> str:
    return f"{sec:.0f}s" if sec < 90 else f"{sec // 60:.0f}m{sec % 60:02.0f}s"


def table(summaries: list[RunSummary]) -> str:
    width = max([len("TASK")] + [len(s.task_id) for s in summaries])
    rows = [f"{'TASK':<{width}}  {'STATUS':<12} {'ITER':<5} {'TIME':>7}  {'COST':>7}  NOTE"]
    for s in summaries:
        cost = f"${s.cost_usd:.3f}" if s.cost_usd is not None else "-"
        note = s.error or (s.worktree and f"kept {s.worktree}") or s.branch
        if s.reverted_total:
            note = f"[{s.reverted_total} protected edit(s) reverted] {note}"
        rows.append(
            f"{s.task_id:<{width}}  {s.status:<12} {s.iterations}/{s.max_iterations:<3} "
            f"{_fmt_seconds(s.seconds):>7}  {cost:>7}  {note}"
        )
    passed = [s for s in summaries if s.status == PASSED]
    if summaries:
        mean_iter = sum(s.iterations for s in passed) / len(passed) if passed else 0.0
        rows.append(
            f"\npass rate {len(passed)}/{len(summaries)} "
            f"({100 * len(passed) / len(summaries):.0f}%) | mean iterations when passed "
            f"{mean_iter:.1f} | total time {_fmt_seconds(sum(s.seconds for s in summaries))}"
        )
        errors = sum(s.status == ERROR for s in summaries)
        if errors:
            rows.append(f"{errors} run(s) hit infrastructure errors, not model failures (see NOTE)")
    return "\n".join(rows)


def write_bench(cfg: Config, spec: EngineSpec, summaries: list[RunSummary]) -> Path:
    path = cfg.runs_dir / f"bench-{spec.name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "engine": spec.name,
        "model": spec.model,
        "runs": [{k: v for k, v in asdict(s).items() if k != "records"} for s in summaries],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_summaries(cfg: Config) -> list[RunSummary]:
    out: list[RunSummary] = []
    for f in sorted(cfg.runs_dir.glob("*/summary.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        data["records"] = []
        out.append(RunSummary(**data))
    return out


def integrate(cfg: Config, target: Target, branches: list[str], log: Log = print) -> str | None:
    """Merge passing agent branches together and re-run every gate and hidden test.
    Separate branches never collide until merged; this is where collisions show up.
    Returns the integration branch if everything passed, else None."""
    existing = set(git.branches(target.repo, "agent"))
    passing = [
        s
        for s in load_summaries(cfg)
        if s.target == target.name and s.status == PASSED and s.branch in existing
    ]
    if branches:
        chosen = [s for s in passing if s.branch in branches]
        unknown = set(branches) - {s.branch for s in chosen}
        if unknown:
            log(f"not passing agent branches (ignored): {', '.join(sorted(unknown))}")
    else:
        latest: dict[str, RunSummary] = {}
        for s in passing:  # sorted by run id, so the newest run per task wins
            latest[s.task_id] = s
        chosen = list(latest.values())
    if not chosen:
        log("nothing to integrate: no passing agent branches")
        return None
    box = sandbox.make_box(cfg, target)
    box.preflight()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"integration/{stamp}"
    wt = cfg.worktrees_dir / target.name / f"integration-{stamp}"
    base = git.rev_parse(target.repo, target.base)
    git.add_worktree(target.repo, wt, branch, base)
    ident = git.identity_args(target.repo)

    merged: list[RunSummary] = []
    for s in chosen:
        res = run_cmd(["git", *ident, "merge", "--no-ff", "--no-edit", s.branch], wt, timeout=120)
        if res.ok:
            merged.append(s)
            log(f"merged    {s.branch}")
        else:
            git.git(wt, "merge", "--abort", check=False)
            log(f"CONFLICT  {s.branch}\n{res.output.strip()}")

    stages = verify.run_gates(wt, target, cfg.settings.verify_timeout, box)
    tasks = {t.id: t for t in find_tasks(target.tasks_dir, [])}
    work = cfg.runs_dir / f"integration-{stamp}"
    for s in merged:
        task = tasks.get(s.task_id)
        if task:
            acc = verify.run_acceptance(
                wt, task, target, work / task.id, cfg.settings.verify_timeout, box=box
            )
            if acc:
                acc.name = f"hidden acceptance tests [{task.id}]"
                stages.append(acc)
    for st in stages:
        log(f"{'ok  ' if st.ok else 'FAIL'}  {st.name} {st.detail}".rstrip())

    good = len(merged) == len(chosen) and all(st.ok for st in stages)
    if good:
        git.remove_worktree(target.repo, wt)
        log(f"integration OK: {len(merged)} branch(es) merged on {branch}")
    else:
        work.mkdir(parents=True, exist_ok=True)
        (work / "gates.log").write_text(
            "\n".join(f"===== {st.name} ok={st.ok}\n{st.output}" for st in stages), encoding="utf-8"
        )
        log(f"integration FAILED; worktree kept at {wt}, logs in {work}")
    return branch if good else None


def clean(cfg: Config, target: Target, delete_branches: bool, log: Log = print) -> None:
    root = cfg.worktrees_dir / target.name
    if root.is_dir() and target.repo.is_dir():
        for wt in sorted(root.iterdir()):
            git.remove_worktree(target.repo, wt)
            if wt.exists():
                force_rmtree(wt)
            log(f"removed worktree {wt}")
    if target.repo.is_dir():
        git.git(target.repo, "worktree", "prune")
        if delete_branches:
            for b in git.branches(target.repo, "agent") + git.branches(target.repo, "integration"):
                git.git(target.repo, "branch", "-D", b)
                log(f"deleted branch {b}")
    if target.sandbox == "docker":
        stale = sandbox.leftover_containers(target.name)
        sandbox.remove_containers(stale)
        if stale:
            log(f"removed {len(stale)} leftover sandbox container(s)")


def demo_init(cfg: Config, target: Target, template: Path, force: bool, log: Log = print) -> None:
    repo = target.repo
    if repo.exists():
        if not force:
            raise FileExistsError(f"{repo} exists; pass --force to recreate it")
        if not repo.resolve().is_relative_to(cfg.root.resolve()):
            raise PermissionError(f"refusing to delete {repo}: outside {cfg.root}")
        clean(cfg, target, delete_branches=False, log=log)
        force_rmtree(repo)
    shutil.copytree(
        template,
        repo,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"),
    )
    git.git(repo, "init", "-q", "-b", target.base)
    git.git(repo, "add", "-A")
    git.git(repo, *git.identity_args(repo), "commit", "-q", "-m", "chore: initial demo repo")
    log(f"demo repo ready at {repo} ({target.base} @ {git.rev_parse(repo, 'HEAD')[:10]})")
