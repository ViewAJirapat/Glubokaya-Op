"""Task specs: tasks/<target>/<task-id>/task.toml plus hidden acceptance tests."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    dir: Path
    type: str = "fix"  # Conventional Commit type for the final commit
    files: list[str] = field(default_factory=list)  # hint: files the agent should start from

    @property
    def acceptance_dir(self) -> Path | None:
        d = self.dir / "acceptance"
        return d if d.is_dir() else None

    @property
    def solution_patch(self) -> Path | None:
        p = self.dir / "solution.patch"
        return p if p.is_file() else None


class TaskError(Exception):
    pass


def load_task(task_dir: Path) -> Task:
    spec = task_dir / "task.toml"
    if not spec.is_file():
        raise TaskError(f"missing {spec}")
    with spec.open("rb") as fh:
        raw = tomllib.load(fh)
    for key in ("title", "prompt"):
        if not raw.get(key):
            raise TaskError(f"{spec}: missing {key!r}")
    return Task(
        id=raw.get("id", task_dir.name),
        title=raw["title"],
        prompt=raw["prompt"].strip(),
        dir=task_dir.resolve(),
        type=raw.get("type", "fix"),
        files=list(raw.get("files", [])),
    )


def load_tasks(tasks_dir: Path) -> list[Task]:
    if not tasks_dir.is_dir():
        raise TaskError(f"tasks directory not found: {tasks_dir}")
    return [load_task(d) for d in sorted(tasks_dir.iterdir()) if (d / "task.toml").is_file()]


def find_tasks(tasks_dir: Path, selectors: list[str]) -> list[Task]:
    """Resolve task ids (or unique prefixes); no selectors means every task."""
    tasks = load_tasks(tasks_dir)
    if not selectors:
        return tasks
    chosen: list[Task] = []
    for sel in selectors:
        exact = [t for t in tasks if t.id == sel]
        matches = exact or [t for t in tasks if t.id.startswith(sel)]
        if len(matches) != 1:
            names = ", ".join(t.id for t in matches) or "none"
            raise TaskError(f"task selector {sel!r} matched {len(matches)} tasks ({names})")
        chosen.append(matches[0])
    return chosen
