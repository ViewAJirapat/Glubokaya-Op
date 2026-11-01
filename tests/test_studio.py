"""Swarm Studio without a model: a scripted planner (the todo plan fixture) and a scripted
worker (tests/agents/solver.py) drive the real pipeline: plan -> stubs, tasks and hidden tests
-> harness runs -> review -> main -> preview."""

from __future__ import annotations

import ast
import copy
import json
import socket
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from swarm import git, verify
from swarm.studio import plan as planmod
from swarm.studio.plan import Feature, Plan
from swarm.studio.project import Project, StudioSettings
from swarm.studio.server import StudioServer
from swarm.task import load_task
from tests.conftest import AGENTS, ROOT

HERE = Path(__file__).resolve().parent / "studio"
TODO = json.loads((HERE / "todo_plan.json").read_text(encoding="utf-8"))


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


def _config(tmp_path: Path) -> Path:
    """The real swarm.toml plus the scripted engines."""
    text = (ROOT / "swarm.toml").read_text(encoding="utf-8")
    for name, args in {
        "solver": [str(AGENTS / "solver.py"), str(HERE / "todo_solution")],
        "noop": [str(AGENTS / "noop.py")],
    }.items():
        cmd = json.dumps([sys.executable, *args])
        text += f'\n[engines.{name}]\nkind = "command"\ncmd = {cmd}\nstdin = "prompt"\n'
    path = tmp_path / "swarm.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _project(tmp_path: Path, raw: dict[str, Any], engine: str = "solver") -> Project:
    cfg = _config(tmp_path)
    settings = StudioSettings(engine=engine)
    return Project(tmp_path / "proj", FakePlanner(raw), harness_config=cfg, settings=settings)


# -- the plan ------------------------------------------------------------------------------------


def test_todo_plan_is_valid_and_normalized() -> None:
    plan = todo_plan()
    assert planmod.problems(plan) == []
    assert [f.name for f in plan.features] == ["todos", "todo_edit", "todo_stats"]
    lst = plan.ui["sections"][1]
    assert lst["toggle"] == {"field": "done", "action": "PATCH /api/todos/{id}"}
    assert [r.function for r in plan.features[1].routes] == [
        "patch_todos_by_id",
        "delete_todos_by_id",
    ]
    # a saved plan loads back unchanged
    assert planmod.plan_from_dict(plan.to_dict()) == plan


def test_sloppy_model_output_is_fixed_where_safe() -> None:
    raw = copy.deepcopy(TODO)
    raw["features"][0]["name"] = "To Dos"
    raw["features"][0]["routes"][0]["method"] = "post"
    raw["features"][0]["routes"][0]["path"] = "todos/"
    raw["ui"]["sections"][0]["action"] = "post /todos"
    raw["features"][0]["scenarios"][0]["steps"][0]["path"] = "/todos"
    raw["title"] = "Todo — “best” list…"
    plan = planmod.plan_from_dict(raw)
    assert plan.features[0].name == "to_dos"
    assert plan.features[0].routes[0].key == "POST /api/todos"
    assert plan.ui["sections"][0]["action"] == "POST /api/todos"
    assert plan.features[0].scenarios[0].steps[0].path == "/api/todos"
    assert plan.title == 'Todo - "best" list...'


def _problems_after(change: Callable[[dict[str, Any]], None]) -> list[str]:
    raw = copy.deepcopy(TODO)
    change(raw)
    return planmod.problems(planmod.plan_from_dict(raw))


def _set(raw: dict[str, Any], path: list[Any], value: Any) -> None:
    node: Any = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (["features", 0, "name"], "health", "snake_case and not one of"),
        (["features", 2, "routes", 0, "path"], "/api/todos?x=1", "clashes with feature"),
        (["features", 1, "routes", 0, "path"], "/api/Todos/{id}", "bad path"),
        (["features", 2, "routes", 0, "path"], "/api/health", "clashes with the frame"),
        (["features", 0, "routes", 0, "reply"], "", "does not say what it replies"),
        (["ui", "sections", 0, "action"], "POST /api/nothing", "not a planned route"),
        (["ui", "sections", 2, "source"], "POST /api/todos", "must be a GET route"),
        (
            ["features", 0, "scenarios", 1, "steps", 0, "path"],
            "/api/todo-stats",
            "matches no route",
        ),
        (["features", 1, "scenarios", 1, "steps", 0, "path"], "/api/todos/{id}", "a real value"),
        (["features", 0, "scenarios", 0, "steps", 0, "status"], 42, "not an HTTP status"),
        (["features", 2, "scenarios"], [], "no example scenarios"),
    ],
)
def test_plan_problems_are_reported(path: list[Any], value: Any, expected: str) -> None:
    found = _problems_after(lambda raw: _set(raw, path, value))
    assert any(expected in p for p in found), found


def test_scenario_must_call_its_own_feature() -> None:
    def change(raw: dict[str, Any]) -> None:
        raw["features"][1]["scenarios"][1]["steps"] = [
            {"method": "GET", "path": "/api/todos", "status": 200, "expect": None}
        ]

    assert any("no step calls this feature" in p for p in _problems_after(change))


def test_generated_files_parse() -> None:
    plan = todo_plan()
    for f in plan.features:
        tree = ast.parse(planmod.stub_source(f))
        doc = ast.get_docstring(tree) or ""
        assert doc.startswith(f"{f.name}: {f.title}.")
        assert "CONTRACT" in doc and "Example (the hidden tests" in doc
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        assert names == [r.function for r in f.routes]
        assert max(len(line) for line in planmod.stub_source(f).splitlines()) <= 100
        ast.parse(planmod.acceptance_source(f))
        raw = tomllib.loads(planmod.task_toml(f))
        assert raw["scope"] == [f"src/webapp/features/{f.name}/**"]
        assert f"Implement the feature `{f.name}`" in raw["prompt"]
        # hidden scenarios (all but the first) never reach the worker
        shown = {st.describe() for st in f.scenarios[0].steps}
        for sc in f.scenarios[1:]:
            for st in sc.steps:
                if st.describe() not in shown:
                    assert st.describe() not in raw["prompt"]


# -- the project: plan -> build -> review -> main -> preview -------------------------------------


def _get(url: str) -> tuple[int, Any]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, json.loads(body) if "json" in ctype else body.decode()


def test_chat_plan_approve_build_and_preview(tmp_path: Path) -> None:
    project = _project(tmp_path, TODO)
    project.start()
    try:
        assert project.preview.running
        status, page = _get(project.preview.url)
        assert status == 200 and "shell.js" in page

        project.chat("Build me a todo list")
        assert project.wait_idle(60)
        assert project.state.plan_status == "proposed", project.state.messages
        assert project.state.plan_problems == []
        assert project.state.messages[-1]["kind"] == "plan"

        project.approve()
        assert project.wait_idle(600), project.busy
        states = {t.id: (t.status, t.note) for t in project.state.tasks}
        assert project.state.build == "done", (states, project.state.messages[-3:])
        assert list(states) == ["01-todos", "02-todo-edit", "03-todo-stats"]

        app = project.app
        log = git.git(app, "log", "--format=%an|%s", "main")
        assert log.count("swarm-agent|feat(") == 3
        assert verify.run_gates(app, project.target, 300) and all(
            s.ok for s in verify.run_gates(app, project.target, 300)
        )
        for t in project.state.tasks:
            task = load_task(project.target.tasks_dir / t.id)
            acc = verify.run_acceptance(app, task, project.target, tmp_path / "acc" / t.id, 300)
            assert acc is not None and acc.ok, acc.output if acc else t.id
            assert "post_todos" in project.task_diff("01-todos")
            assert project.task_attempts(t.id)[0]["files"]

        # the preview now runs the merged app
        base = project.preview.url.rstrip("/")
        req = urllib.request.Request(
            f"{base}/api/todos",
            data=json.dumps({"title": "Try the preview"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 201
        assert _get(f"{base}/api/todo-stats") == (200, {"total": 1, "done": 0, "open": 1})
        assert "passed" in project.task_log("03-todo-stats").lower()
        # hidden tests stay outside the app repo
        tracked = git.git(app, "ls-files")
        assert "acceptance" not in tracked and "test_todos.py" not in tracked
    finally:
        project.close()


def test_failed_task_stops_the_build_until_retried(tmp_path: Path) -> None:
    raw = copy.deepcopy(TODO)
    raw["features"] = raw["features"][:1]
    raw["ui"]["sections"] = raw["ui"]["sections"][:2]
    raw["ui"]["sections"][1].pop("toggle_field")
    raw["ui"]["sections"][1].pop("toggle_action")
    raw["ui"]["sections"][1]["buttons"] = []
    project = _project(tmp_path, raw, engine="noop")
    project.start(with_preview=False)
    try:
        project.chat("todo list")
        assert project.wait_idle(60)
        project.approve()
        assert project.wait_idle(300)
        task = project.state.tasks[0]
        assert task.status == "failed" and project.state.build == "paused"
        assert "retry" in project.state.messages[-1]["text"]

        project.engine = project.cfg.engine("solver")
        project.retry(task.id)
        assert project.wait_idle(300)
        assert task.status == "passed", task.note
        assert len(task.runs) == 2 and project.state.build == "done"
    finally:
        project.close()


def test_state_survives_a_restart(tmp_path: Path) -> None:
    project = _project(tmp_path, TODO)
    project.start(with_preview=False)
    project.chat("todo list")
    assert project.wait_idle(60)
    project.close()

    again = _project(tmp_path, TODO)
    assert again.state.plan_status == "proposed"
    assert [m["role"] for m in again.state.messages] == ["chief", "user", "chief"]
    assert again.snapshot()["plan_markdown"].startswith("# Todo")


def _call(url: str, token: str | None, data: dict[str, Any] | None = None, **headers: str) -> int:
    if token is not None:
        headers["X-Studio-Token"] = token
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def test_server_needs_the_token_and_its_own_host(tmp_path: Path) -> None:
    project = _project(tmp_path, TODO)
    project.start(with_preview=False)
    server = StudioServer(project)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        assert _get(base + "/")[0] == 200  # the page itself holds no data
        assert _call(base + "/api/state", None) == 403
        assert _call(base + "/api/state", "wrong") == 403
        assert _call(base + "/api/state", server.token, Host="evil.example") == 403  # rebinding
        assert _call(base + "/api/chat", None, {"text": "x"}) == 403
        assert _call(base + "/static/../server.py", None) == 404
        assert _call(base + "/api/state", server.token) == 200

        assert _call(base + "/api/chat", server.token, {"text": "a todo app"}) == 200
        assert project.wait_idle(60)
        assert project.state.plan_status == "proposed"
        assert _call(base + "/api/tasks/nope/log", server.token) == 404

        url = f"{base}/api/events?since=0&token={server.token}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            kinds = set()
            while "message" not in kinds or "state" not in kinds:
                line = resp.readline().decode()
                if line.startswith("event: "):
                    kinds.add(line[7:].strip())
    finally:
        server.stop()
        project.close()


def test_server_refuses_a_port_another_app_listens_on(tmp_path: Path) -> None:
    """On Windows, SO_REUSEADDR used to let the studio share a busy port with another app."""
    project = _project(tmp_path, TODO)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        with pytest.raises(OSError):
            StudioServer(project, other.getsockname()[1])
