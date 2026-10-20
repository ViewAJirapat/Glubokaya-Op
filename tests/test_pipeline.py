"""End-to-end runs against a throwaway demo repo, with scripted agents (no model needed)."""

from __future__ import annotations

import shutil
from pathlib import Path

from swarm import git
from swarm.config import Config, EngineSpec
from swarm.runner import ERROR, FAILED, INVALID, PASSED, run_task
from swarm.task import Task, find_tasks, load_task
from tests.conftest import ROOT, quiet

PAGINATION = "demo-001-pagination"


def _task(cfg: Config, task_id: str = PAGINATION) -> Task:
    return find_tasks(cfg.target(None).tasks_dir, [task_id])[0]


def test_oracle_passes_commits_and_cleans_up(cfg: Config) -> None:
    target = cfg.target(None)
    s = run_task(cfg, target, _task(cfg), EngineSpec("oracle", "oracle"), log=quiet)
    assert s.status == PASSED, s.error
    assert s.iterations == 1
    assert s.worktree is None  # removed on success
    assert not (cfg.worktrees_dir / target.name / s.run_id).exists()
    assert git.rev_parse(target.repo, s.branch) == s.commit
    files = git.git(target.repo, "show", "--name-only", "--format=", s.branch).split()
    assert files == ["src/shopkit/pagination.py"]  # explicit paths only, no junk
    author = git.git(target.repo, "log", "-1", "--format=%an", s.branch).strip()
    assert author == "swarm-agent"


def test_cheating_is_reverted_and_blocked(cfg: Config) -> None:
    target = cfg.target(None)
    s = run_task(cfg, target, _task(cfg), cfg.engine("cheater"), max_iterations=1, log=quiet)
    assert s.status == FAILED
    rec = s.records[0]
    assert any(r.startswith("tests/test_pagination.py") for r in rec.reverted)
    assert any(r.startswith("conftest.py") for r in rec.reverted)
    assert any("type: ignore" in v for v in rec.violations)
    assert s.reverted_total == 2

    wt = Path(str(s.worktree))
    assert wt.is_dir()  # kept for inspection
    assert not (wt / "conftest.py").exists()
    assert "test_nothing" not in (wt / "tests" / "test_pagination.py").read_text(encoding="utf-8")
    assert (cfg.runs_dir / s.run_id / "final.patch").is_file()


def test_noop_agent_fails_without_running_gates(cfg: Config) -> None:
    s = run_task(cfg, cfg.target(None), _task(cfg), cfg.engine("noop"), max_iterations=2, log=quiet)
    assert s.status == FAILED
    assert all(r.changed == [] and r.stages == [] for r in s.records)
    prompt2 = (cfg.runs_dir / s.run_id / "iter-2" / "prompt.md").read_text(encoding="utf-8")
    assert "No file changes remain" in prompt2


def test_retry_prompt_keeps_task_and_reports_hidden_failures(cfg: Config) -> None:
    task = _task(cfg)
    s = run_task(cfg, cfg.target(None), task, cfg.engine("cosmetic"), max_iterations=2, log=quiet)
    assert s.status == FAILED
    first = s.records[0]
    visible = [st for st in first.stages if st["name"] != "hidden acceptance tests"]
    hidden = [st for st in first.stages if st["name"] == "hidden acceptance tests"]
    assert all(st["ok"] for st in visible)  # the visible suite alone would have passed
    assert hidden and not hidden[0]["ok"]

    prompt2 = (cfg.runs_dir / s.run_id / "iter-2" / "prompt.md").read_text(encoding="utf-8")
    assert task.title in prompt2 and task.prompt.splitlines()[0] in prompt2
    assert "Attempt 1 did not pass" in prompt2
    assert "FAILED: hidden acceptance tests" in prompt2
    assert "WARNING" not in prompt2

    # Attempt 2 changed nothing further, so the next feedback flags the repeat.
    feedback2 = (cfg.runs_dir / s.run_id / "iter-2" / "feedback.md").read_text(encoding="utf-8")
    assert feedback2.startswith("WARNING: attempt 2 produced exactly the same code")


def test_task_whose_hidden_tests_already_pass_is_invalid(cfg: Config, tmp_path: Path) -> None:
    task_dir = tmp_path / "trivial"
    shutil.copytree(
        ROOT / "tasks" / "demo" / PAGINATION,
        task_dir,
        ignore=shutil.ignore_patterns("acceptance", "__pycache__"),
    )
    (task_dir / "acceptance").mkdir()
    (task_dir / "acceptance" / "test_ok.py").write_text("def test_ok() -> None:\n    pass\n")
    target = cfg.target(None)

    s = run_task(cfg, target, load_task(task_dir), cfg.engine("noop"), log=quiet)
    assert s.status == INVALID
    assert s.error is not None and "already pass" in s.error
    assert s.iterations == 0
    assert not git.branches(target.repo, "agent")  # branch and worktree removed


def test_agent_error_without_changes_stops_immediately(cfg: Config) -> None:
    s = run_task(cfg, cfg.target(None), _task(cfg), cfg.engine("crasher"), log=quiet)
    assert s.status == ERROR
    assert s.iterations == 1  # not retried 4 times
    assert s.error is not None and "Failed to authenticate" in s.error


def test_autofix_formats_a_correct_but_sloppy_fix(cfg: Config) -> None:
    s = run_task(cfg, cfg.target(None), _task(cfg), cfg.engine("messy"), log=quiet)
    assert s.status == PASSED, s.error
    assert s.iterations == 1
    assert (cfg.runs_dir / s.run_id / "iter-1" / "autofix.log").is_file()
