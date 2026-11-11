"""swarm: closed-loop coding-agent harness.

swarm demo-init               create the demo target repo (work/demo_repo)
swarm validate                every task: base green, hidden tests red, reference solution passes
swarm run TASK [TASK...]      run tasks through an engine, one worktree each
swarm bench                   run all tasks, print a pass-rate table, save JSON
swarm integrate               merge passing agent branches and re-run every gate
swarm archlint [PATH]         check a checkout against the target's architecture rules
swarm docker-build            build the image for targets with sandbox = "docker"
swarm studio [PROJECT]        desktop app: describe an app in a chat; the chief plans it and
                              the harness builds it, with live logs, diffs and a preview
swarm runs | tasks | clean
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from swarm import archlint, dispatch, sandbox
from swarm import config as config_mod
from swarm.config import SANDBOX_KINDS, Config, EngineSpec
from swarm.engines import EngineError
from swarm.runner import PASSED
from swarm.sandbox import SandboxError
from swarm.task import TaskError, find_tasks

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _config_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    if env := os.environ.get("SWARM_CONFIG"):
        return Path(env)
    local = Path.cwd() / "swarm.toml"
    return local if local.exists() else Path(__file__).resolve().parent.parent / "swarm.toml"


def _add_run_opts(p: argparse.ArgumentParser, *, engine: bool = True) -> None:
    p.add_argument("tasks", nargs="*", help="task ids or unique prefixes (default: all)")
    if engine:
        p.add_argument("-e", "--engine", help="engine name from swarm.toml")
    p.add_argument("-n", "--max-iterations", type=int)
    p.add_argument(
        "-j",
        "--parallel",
        type=int,
        default=1,
        help="concurrent tasks (keep 1 for a single local GPU)",
    )
    p.add_argument("--keep", action="store_true", help="keep the worktree even on success")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="swarm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-c", "--config", help="path to swarm.toml")
    ap.add_argument("-t", "--target", help="target name from swarm.toml")
    ap.add_argument(
        "--sandbox", choices=SANDBOX_KINDS, help="override the target's sandbox setting"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo-init", help="create the demo target repo from its template")
    p.add_argument("--force", action="store_true", help="delete and recreate it")

    p = sub.add_parser("run", help="run tasks through an engine")
    _add_run_opts(p)
    p.add_argument("--push", action="store_true", help="push passing branches to origin")
    p.add_argument("--pr", action="store_true", help="with --push: open a PR via gh")

    p = sub.add_parser("bench", help="run tasks and report pass rate")
    _add_run_opts(p)
    p.add_argument("-r", "--repeat", type=int, default=1, help="runs per task")

    p = sub.add_parser("validate", help="check tasks with their reference solutions")
    _add_run_opts(p, engine=False)

    p = sub.add_parser("integrate", help="merge passing agent branches and re-verify")
    p.add_argument("branches", nargs="*", help="default: newest passing branch per task")

    p = sub.add_parser("clean", help="remove worktrees (and optionally agent branches)")
    p.add_argument("--branches", action="store_true", help="also delete agent/* integration/*")

    p = sub.add_parser("archlint", help="check import rules ([targets.*.architecture])")
    p.add_argument("path", nargs="?", help="checkout to check (default: the target repo)")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("studio", help="open Swarm Studio (chat -> plan -> build -> preview)")
    p.add_argument("project", nargs="?", default="todo", help="project name (default: todo)")
    p.add_argument("-e", "--engine", help="worker engine (default: [studio] engine)")
    p.add_argument("--port", type=int, default=0, help="studio port (default: any free one)")
    p.add_argument(
        "--window",
        choices=["auto", "webview", "edge", "browser", "none"],
        default="auto",
        help="desktop window (auto: pywebview if installed, else Edge app mode)",
    )
    p.add_argument(
        "--plan-file",
        help="skip the chief model: every draft is this plan JSON (try the studio without a GPU)",
    )

    sub.add_parser("docker-build", help="build the sandbox image (docker/Dockerfile)")
    sub.add_parser("tasks", help="list tasks")
    p = sub.add_parser("runs", help="list recent runs")
    p.add_argument("--last", type=int, default=20)
    return ap


def _run_like(cfg: Config, args: argparse.Namespace, spec: EngineSpec) -> int:
    target = cfg.target(args.target)
    tasks = find_tasks(target.tasks_dir, args.tasks)
    log(f"target {target.name} | engine {spec.label} | {len(tasks)} task(s)")
    summaries = dispatch.run_many(
        cfg,
        target,
        tasks,
        spec,
        parallel=args.parallel,
        repeat=getattr(args, "repeat", 1),
        log=log,
        max_iterations=args.max_iterations,
        keep_worktree=args.keep,
        push=getattr(args, "push", False),
        pr=getattr(args, "pr", False),
    )
    log("\n" + dispatch.table(summaries))
    if args.cmd == "bench":
        log(f"saved {dispatch.write_bench(cfg, spec, summaries)}")
    return 0 if all(s.status == PASSED for s in summaries) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config_mod.load(_config_path(args.config))
        target = cfg.target(args.target)
        if args.sandbox:
            target.sandbox = args.sandbox
        match args.cmd:
            case "demo-init":
                if target.template is None:
                    raise config_mod.ConfigError(f"target {target.name} has no 'template'")
                dispatch.demo_init(cfg, target, target.template, args.force, log)
            case "run" | "bench":
                return _run_like(cfg, args, cfg.engine(args.engine))
            case "validate":
                return _run_like(cfg, args, EngineSpec(name="oracle", kind="oracle"))
            case "integrate":
                return 0 if dispatch.integrate(cfg, target, args.branches, log) else 1
            case "clean":
                dispatch.clean(cfg, target, args.branches, log)
            case "archlint":
                if target.architecture is None:
                    raise config_mod.ConfigError(f"target {target.name} has no architecture rules")
                where = Path(args.path).resolve() if args.path else target.repo
                if not where.is_dir():
                    raise config_mod.ConfigError(f"not a directory: {where}")
                result = archlint.check(where, target.architecture)
                render = archlint.report_json if args.json else archlint.report
                log(render(result))
                return 0 if result.ok else 1
            case "docker-build":
                return 0 if sandbox.build_image(cfg.sandbox, log) else 1
            case "studio":
                from swarm.studio import app

                return app.run(
                    _config_path(args.config),
                    args.project,
                    engine=args.engine,
                    port=args.port,
                    window=args.window,
                    plan_file=Path(args.plan_file) if args.plan_file else None,
                    sandbox=args.sandbox,  # the studio builds its own target
                    log=log,
                )
            case "tasks":
                for t in find_tasks(target.tasks_dir, []):
                    hidden = "hidden tests" if t.acceptance_dir else "NO hidden tests"
                    ref = ", reference solution" if t.solution_patch else ""
                    log(f"{t.id:<28} {t.type:<6} {t.title}  [{hidden}{ref}]")
            case "runs":
                runs = [s for s in dispatch.load_summaries(cfg) if s.target == target.name]
                log(dispatch.table(runs[-args.last :]))
    except (
        config_mod.ConfigError,
        TaskError,
        EngineError,
        SandboxError,
        FileExistsError,
        PermissionError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
