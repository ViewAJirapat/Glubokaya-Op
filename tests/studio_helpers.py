"""Shared fixtures for the studio tests: the todo plan, a scripted planner, scripted engines."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from swarm.studio import plan as planmod
from swarm.studio.plan import Feature, Plan
from swarm.studio.project import Project, StudioSettings
from tests.conftest import AGENTS, ROOT

HERE = Path(__file__).resolve().parent / "studio"
TODO: dict[str, Any] = json.loads((HERE / "todo_plan.json").read_text(encoding="utf-8"))


def todo_plan(raw: dict[str, Any] | None = None) -> Plan:
    return planmod.plan_from_dict(copy.deepcopy(raw or TODO))


class FakePlanner:
    """Returns a fixed plan; records what it was asked."""

    label = "fake planner"

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.calls: list[tuple[str, str | None]] = []

    def draft(
        self,
        request: str,
        previous: Plan | None,
        comment: str | None,
        built: list[Feature],
        progress: Callable[[str], None],
    ) -> Plan:
        self.calls.append((request, comment))
        progress("outline")
        return todo_plan(self.raw)


def harness_config(tmp_path: Path) -> Path:
    """The real swarm.toml plus scripted engines: `solver` writes the reference handlers,
    `noop` changes nothing, `broken` fails its preflight (no Ollama on port 9)."""
    text = (ROOT / "swarm.toml").read_text(encoding="utf-8")
    for name, args in {
        "solver": [str(AGENTS / "solver.py"), str(HERE / "todo_solution")],
        "noop": [str(AGENTS / "noop.py")],
    }.items():
        cmd = json.dumps([sys.executable, *args])
        text += f'\n[engines.{name}]\nkind = "command"\ncmd = {cmd}\nstdin = "prompt"\n'
    text += (
        '\n[engines.broken]\nkind = "aider"\nmodel = "ollama_chat/none"\n'
        'env = { OLLAMA_API_BASE = "http://127.0.0.1:9" }\n'
    )
    path = tmp_path / "swarm.toml"
    path.write_text(text, encoding="utf-8")
    return path


def make_project(
    tmp_path: Path, raw: dict[str, Any], engine: str = "solver", sandbox: str | None = None
) -> Project:
    settings = StudioSettings(engine=engine)
    return Project(
        tmp_path / "proj",
        FakePlanner(raw),
        harness_config=harness_config(tmp_path),
        settings=settings,
        sandbox=sandbox,
    )
