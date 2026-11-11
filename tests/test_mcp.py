"""`swarm mcp`: a scripted MCP client over the server's JSON-RPC, one call per tool, plus
the refusal of any edit."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from swarm.config import Config
from swarm.mcp import NEVER, Server
from tests.studio_helpers import TODO

TOOLS = {
    "list_targets",
    "list_tasks",
    "task",
    "plan_from_examples",
    "write_task",
    "validate",
    "run",
    "status",
    "diff",
    "integrate",
}


def rpc(server: Server, method: str, params: Any = None, rid: int = 1) -> dict[str, Any]:
    reply = server.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    assert reply is not None
    return reply


def call(server: Server, name: str, **arguments: Any) -> Any:
    reply = rpc(server, "tools/call", {"name": name, "arguments": arguments})
    assert "error" not in reply, reply
    result = reply["result"]
    if result.get("isError"):
        raise AssertionError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


def tool_error(server: Server, name: str, **arguments: Any) -> str:
    reply = rpc(server, "tools/call", {"name": name, "arguments": arguments})
    result = reply["result"]
    assert result["isError"], result
    return str(result["content"][0]["text"])


def wait(server: Server, job: str) -> dict[str, Any]:
    thread = server.jobs[job].thread
    assert thread is not None
    thread.join(timeout=600)
    return call(server, "status", job=job)


@pytest.fixture
def server(cfg: Config, tmp_path: Path) -> Server:
    return Server(cfg)


def test_initialize_and_tool_list(server: Server) -> None:
    init = rpc(server, "initialize", {"protocolVersion": "2024-11-05"})["result"]
    assert init["protocolVersion"] and "tools" in init["capabilities"]
    assert "never" in init["instructions"] or NEVER in init["instructions"]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc(server, "ping")["result"] == {}
    listed = rpc(server, "tools/list")["result"]["tools"]
    assert {t["name"] for t in listed} == TOOLS
    for t in listed:
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert rpc(server, "resources/list")["error"]["code"] == -32601


@pytest.mark.parametrize("name", ["edit_file", "write_file", "Edit", "bash", "delete_task"])
def test_any_edit_tool_is_refused(server: Server, name: str) -> None:
    text = tool_error(server, name, path="src/x.py", content="x")
    assert f"unknown tool {name!r}" in text and "no tool that edits" in text


def test_targets_tasks_and_one_task(server: Server, cfg: Config) -> None:
    targets = call(server, "list_targets")
    demo = next(t for t in targets["targets"] if t["name"] == "demo")
    assert demo["repo"] == str(cfg.target("demo").repo) and demo["gates"]
    assert targets["default_target"] in {t["name"] for t in targets["targets"]}
    tasks = call(server, "list_tasks", target="demo")
    assert tasks and all(t["hidden_tests"] for t in tasks)
    one = call(server, "task", id=tasks[0]["id"])
    assert one["prompt"] and one["hidden_tests"] and one["dir"]
    assert "not found" in tool_error(server, "list_tasks", target="nope").lower() or tool_error(
        server, "list_tasks", target="nope"
    )
    assert tool_error(server, "task", id="no-such-task")


def test_plan_from_examples_checks_and_generates_without_writing(server: Server) -> None:
    out = call(server, "plan_from_examples", plan=copy.deepcopy(TODO))
    assert out["problems"] == []
    assert [t["id"] for t in out["tasks"]] == ["01-todos", "02-todo-edit", "03-todo-stats"]
    assert "title =" in out["tasks"][0]["task_toml"]
    assert "def test_" in out["tasks"][0]["acceptance"]["test_todos.py"]
    broken = copy.deepcopy(TODO)
    broken["features"][1]["scenarios"] = []
    assert call(server, "plan_from_examples", plan=broken)["problems"]
    assert "object" in tool_error(server, "plan_from_examples", plan="nope")


def test_write_task_creates_once_and_never_edits(
    server: Server, cfg: Config, tmp_path: Path
) -> None:
    target = cfg.target("demo")
    target.tasks_dir = tmp_path / "tasks"  # never the repo's own task folder
    target.tasks_dir.mkdir()
    out = call(
        server,
        "write_task",
        target="demo",
        id="t-1",
        title="A title",
        prompt='Do the thing. Keep """ and \\ intact.',
        files=["src/a.py"],
        scope=["src/**"],
        acceptance={"test_t1.py": "def test_x():\n    assert True\n"},
    )
    assert out["id"] == "t-1" and out["hidden_tests"] == ["test_t1.py"]
    task = call(server, "task", id="t-1", target="demo")
    assert task["prompt"] == 'Do the thing. Keep """ and \\ intact.' or task["prompt"].startswith(
        "Do the thing. Keep "
    )
    assert task["files"] == ["src/a.py"] and task["scope"] == ["src/**"]
    assert "exists" in tool_error(
        server,
        "write_task",
        target="demo",
        id="t-1",
        title="x",
        prompt="y",
        acceptance={"test_a.py": ""},
    )
    assert "bad task id" in tool_error(
        server,
        "write_task",
        target="demo",
        id="Bad Id",
        title="x",
        prompt="y",
        acceptance={"test_a.py": ""},
    )
    assert "test_*.py" in tool_error(
        server,
        "write_task",
        target="demo",
        id="t-2",
        title="x",
        prompt="y",
        acceptance={"conftest.py": ""},
    )
    assert "at least one" in tool_error(
        server, "write_task", target="demo", id="t-3", title="x", prompt="y", acceptance={}
    )
    assert not (target.tasks_dir / "t-2").exists() and not (target.tasks_dir / "t-3").exists()
    assert (
        "missing argument"
        in rpc(server, "tools/call", {"name": "write_task", "arguments": {"id": "t-4"}})["error"][
            "message"
        ]
    )


def test_run_status_diff_and_integrate_as_jobs(server: Server, cfg: Config) -> None:
    tasks = call(server, "list_tasks", target="demo")
    started = call(
        server, "run", target="demo", tasks=[tasks[0]["id"]], engine="noop", max_iterations=1
    )
    assert started["status"] == "running" and started["poll"] == "status"
    done = wait(server, started["job"])
    assert done["status"] == "done", done
    assert done["result"]["passed"] is False
    [run] = done["result"]["runs"]
    assert run["task_id"] == tasks[0]["id"] and run["iterations"] == 1
    assert any("noop" in line or tasks[0]["id"] in line for line in done["log"])
    assert "committed nothing" in tool_error(server, "diff", run_id=run["run_id"])
    assert "unknown run" in tool_error(server, "diff", run_id="nope")
    assert "unknown job" in tool_error(server, "status", job="nope")
    assert "unknown engine" in tool_error(server, "run", engine="nope").lower() or tool_error(
        server, "run", engine="nope"
    )

    merged = wait(server, call(server, "integrate", target="demo")["job"])
    assert merged["status"] == "done" and merged["result"] == {
        "ok": False,
        "integration_branch": None,
    }
    assert any("nothing to integrate" in line for line in merged["log"])


def test_serve_speaks_newline_delimited_json_rpc(server: Server) -> None:
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        "not json",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    out = io.BytesIO()
    server.serve(io.BytesIO(("\n".join(lines) + "\n").encode()), out)
    replies = [json.loads(line) for line in out.getvalue().decode().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700
    assert {t["name"] for t in replies[2]["result"]["tools"]} == TOOLS
