from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from swarm import config, dispatch
from swarm.config import Config

ROOT = Path(__file__).resolve().parent.parent
AGENTS = Path(__file__).resolve().parent / "agents"


def quiet(_msg: str) -> None:
    pass


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """The real swarm.toml, pointed at a throwaway demo repo, plus scripted engines."""
    with (ROOT / "swarm.toml").open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    data["defaults"]["worktrees_dir"] = str(tmp_path / "worktrees")
    data["defaults"]["runs_dir"] = str(tmp_path / "runs")
    target = data["targets"]["demo"]
    target["repo"] = str(tmp_path / "repo")
    target["template"] = str(ROOT / "demo" / "shopkit")
    target["tasks"] = str(ROOT / "tasks" / "demo")
    target["sandbox"] = "none"  # the scripted agents are host scripts; docker tests opt in
    for name in ("noop", "cheater", "cosmetic", "crasher", "messy", "mover", "talker"):
        script = str(AGENTS / f"{name}.py")
        data["engines"][name] = {"kind": "command", "cmd": [sys.executable, script]}

    cfg = config.from_dict(data, ROOT)
    t = cfg.target(None)
    assert t.template is not None
    dispatch.demo_init(cfg, t, t.template, force=False, log=quiet)
    return cfg


@pytest.fixture
def api_cfg(tmp_path: Path) -> Config:
    """Like `cfg`, for the shopapi target (auto-discovered features + architecture rules)."""
    with (ROOT / "swarm.toml").open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    data["defaults"]["target"] = "shopapi"
    data["defaults"]["worktrees_dir"] = str(tmp_path / "worktrees")
    data["defaults"]["runs_dir"] = str(tmp_path / "runs")
    target = data["targets"]["shopapi"]
    target["repo"] = str(tmp_path / "repo")
    target["template"] = str(ROOT / "demo" / "shopapi")
    target["tasks"] = str(ROOT / "tasks" / "shopapi")
    target["sandbox"] = "none"
    for name in ("tangled", "stager"):
        script = str(AGENTS / f"{name}.py")
        data["engines"][name] = {"kind": "command", "cmd": [sys.executable, script]}

    cfg = config.from_dict(data, ROOT)
    t = cfg.target(None)
    assert t.template is not None
    dispatch.demo_init(cfg, t, t.template, force=False, log=quiet)
    return cfg
