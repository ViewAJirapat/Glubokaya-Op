"""`swarm mcp`: the harness as an MCP server on stdio (PLAN.md phase C.2).

Claude Code, Claude Desktop or Antigravity connects with one `.mcp.json` entry and becomes
the chief: it reads the targets and tasks, checks a plan, writes new tasks with their hidden
tests, runs the local model through the harness and reads the results. There is no tool
that edits a target's files or an existing task: the outside chief plans and reads, the
harness and the local model do the work, and the guard stays in charge.

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio transport); the
server's own messages go to stderr. No SDK: the protocol here is initialize, ping,
tools/list and tools/call.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, BinaryIO

from swarm import dispatch, git
from swarm.config import Config, ConfigError, EngineSpec, Target
from swarm.runner import PASSED, RunSummary
from swarm.studio import plan as planmod
from swarm.task import TaskError, find_tasks, load_task

PROTOCOL_VERSION = "2024-11-05"
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")
TEST_FILE = re.compile(r"^test_[a-z0-9_]+\.py$")
NEVER = (
    "this server has no tool that edits a target's files or an existing task: the chief "
    "plans and reads; the harness and the local model do the work"
)

Handler = Callable[[dict[str, Any]], Any]


class ToolError(Exception):
    """A tool's failure, reported to the client as an error result (not a protocol error)."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Handler


@dataclass
class Job:
    """A long harness command (validate, run, integrate) runs in a thread; `status` polls it."""

    id: str
    kind: str
    status: str = "running"  # running | done | failed
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    thread: threading.Thread | None = None

    def say(self, line: str) -> None:
        self.log.append(line)

    def view(self, tail: int = 40) -> dict[str, Any]:
        return {
            "job": self.id,
            "kind": self.kind,
            "status": self.status,
            "log": self.log[-tail:],
            "result": self.result,
            "error": self.error,
        }


def _prop(kind: str, description: str, **more: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **more}


def _schema(required: list[str] | None = None, **props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


_TARGET = _prop("string", "target name from swarm.toml (default: the default target)")
_IDS = _prop("array", "task ids or unique prefixes (default: every task)", items={"type": "string"})


def _summary_view(s: RunSummary) -> dict[str, Any]:
    data = asdict(s)
    data["records"] = [
        {k: r[k] for k in ("n", "agent_seconds", "passed", "changed", "reverted", "violations")}
        for r in data["records"]
    ]
    return data


class Server:
    def __init__(self, cfg: Config, log: Callable[[str], None] = lambda _m: None) -> None:
        self.cfg = cfg
        self.log = log
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.tools: dict[str, Tool] = {}
        for tool in self._tools():
            self.tools[tool.name] = tool

    # -- tools -----------------------------------------------------------------------------------

    def _tools(self) -> list[Tool]:
        return [
            Tool(
                "list_targets",
                "The targets in swarm.toml: repo, base branch, task folder, gates, engines.",
                _schema(),
                self.list_targets,
            ),
            Tool(
                "list_tasks",
                "The tasks of a target: id, type, title, whether hidden tests and a reference "
                "solution exist.",
                _schema(target=_TARGET),
                self.list_tasks,
            ),
            Tool(
                "task",
                "One task in full: task.toml fields, the prompt, its hidden test files and "
                "whether a reference solution exists.",
                _schema(["id"], id=_prop("string", "task id or unique prefix"), target=_TARGET),
                self.task,
            ),
            Tool(
                "plan_from_examples",
                "Check a Swarm Studio plan (title, summary, features with routes and example "
                "scenarios, page) the way the studio's chief loop does, and show the harness "
                "tasks and hidden tests it would generate. Writes nothing.",
                _schema(["plan"], plan=_prop("object", "the plan JSON (the studio's schema)")),
                self.plan_from_examples,
            ),
            Tool(
                "write_task",
                "Create a new task folder with task.toml and hidden acceptance tests. Refuses "
                "an id that exists: tasks are never edited through this server.",
                _schema(
                    ["id", "title", "prompt", "acceptance"],
                    target=_TARGET,
                    id=_prop("string", "new task id: lowercase, digits, . _ -"),
                    title=_prop("string", "one line"),
                    prompt=_prop("string", "the spec the model sees"),
                    type=_prop("string", "Conventional Commit type (default fix)"),
                    files=_prop("array", "files the model starts from", items={"type": "string"}),
                    scope=_prop("array", "paths the task may change", items={"type": "string"}),
                    acceptance=_prop(
                        "object",
                        'hidden tests: {"test_name.py": source, ...}; at least one file',
                        additionalProperties={"type": "string"},
                    ),
                ),
                self.write_task,
            ),
            Tool(
                "validate",
                "Start `swarm validate`: for each task, base green, hidden tests red, the "
                "reference solution passes. Returns a job to poll with `status`.",
                _schema(target=_TARGET, tasks=_IDS),
                self.validate,
            ),
            Tool(
                "run",
                "Start `swarm run`: the tasks through an engine (the local model by default), "
                "one worktree each. Returns a job to poll with `status`.",
                _schema(
                    target=_TARGET,
                    tasks=_IDS,
                    engine=_prop("string", "engine name from swarm.toml (default: the default)"),
                    max_iterations=_prop("integer", "attempts per task (default from swarm.toml)"),
                ),
                self.run,
            ),
            Tool(
                "status",
                "A job's state, log tail and result (the run summaries once done).",
                _schema(["job"], job=_prop("string", "job id from validate, run or integrate")),
                self.status,
            ),
            Tool(
                "diff",
                "The diff a passed run committed on its agent branch (base..commit).",
                _schema(["run_id"], run_id=_prop("string", "run id from a run summary")),
                self.diff,
            ),
            Tool(
                "integrate",
                "Start `swarm integrate`: merge passing agent branches and re-run every gate and "
                "hidden test. Returns a job to poll with `status`.",
                _schema(
                    target=_TARGET,
                    branches=_prop(
                        "array",
                        "agent branches (default: the newest passing run per task)",
                        items={"type": "string"},
                    ),
                ),
                self.integrate,
            ),
        ]

    def _target(self, params: dict[str, Any]) -> Target:
        try:
            return self.cfg.target(params.get("target"))
        except ConfigError as exc:
            raise ToolError(str(exc)) from None

    def list_targets(self, params: dict[str, Any]) -> Any:
        return {
            "default_target": self.cfg.default_target,
            "default_engine": self.cfg.default_engine,
            "engines": {n: e.label for n, e in self.cfg.engines.items()},
            "targets": [
                {
                    "name": t.name,
                    "repo": str(t.repo),
                    "base": t.base,
                    "tasks_dir": str(t.tasks_dir),
                    "gates": [" ".join(c) for c in t.verify],
                    "protected": t.protected,
                    "sandbox": t.sandbox,
                }
                for t in self.cfg.targets.values()
            ],
        }

    def list_tasks(self, params: dict[str, Any]) -> Any:
        target = self._target(params)
        try:
            tasks = find_tasks(target.tasks_dir, [])
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        return [
            {
                "id": t.id,
                "type": t.type,
                "title": t.title,
                "hidden_tests": t.acceptance_dir is not None,
                "reference_solution": t.solution_patch is not None,
            }
            for t in tasks
        ]

    def task(self, params: dict[str, Any]) -> Any:
        target = self._target(params)
        try:
            found = find_tasks(target.tasks_dir, [str(params["id"])])
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        t = found[0]
        tests = (
            sorted(p.name for p in t.acceptance_dir.glob("test_*.py")) if t.acceptance_dir else []
        )
        return {
            "id": t.id,
            "type": t.type,
            "title": t.title,
            "prompt": t.prompt,
            "files": t.files,
            "scope": t.scope,
            "dir": str(t.dir),
            "hidden_tests": tests,
            "reference_solution": t.solution_patch is not None,
        }

    def plan_from_examples(self, params: dict[str, Any]) -> Any:
        raw = params["plan"]
        if not isinstance(raw, dict):
            raise ToolError("plan must be a JSON object")
        plan = planmod.plan_from_dict(raw)
        problems = planmod.problems(plan)
        tasks = [
            {
                "id": planmod.task_id(n, f),
                "task_toml": planmod.task_toml(f),
                "acceptance": {f"test_{f.name}.py": planmod.acceptance_source(f)},
            }
            for n, f in enumerate(plan.features, 1)
        ]
        return {"problems": problems, "plan": plan.to_dict(), "tasks": tasks}

    def write_task(self, params: dict[str, Any]) -> Any:
        target = self._target(params)
        tid = str(params["id"])
        if not TASK_ID.match(tid):
            raise ToolError(f"bad task id {tid!r}: lowercase letters, digits, . _ - only")
        tdir = target.tasks_dir / tid
        if tdir.exists():
            raise ToolError(f"task {tid!r} exists; {NEVER}")
        acceptance = params["acceptance"]
        if not isinstance(acceptance, dict) or not acceptance:
            raise ToolError("acceptance must map at least one test_*.py file to its source")
        for name, source in acceptance.items():
            if not TEST_FILE.match(name) or not isinstance(source, str):
                raise ToolError(f"bad hidden test file {name!r}: a test_*.py name and a source")
        lines = [
            f"title = {json.dumps(str(params['title']))}",
            f"type = {json.dumps(str(params.get('type') or 'fix'))}",
            f"files = {json.dumps([str(f) for f in params.get('files') or []])}",
            f"scope = {json.dumps([str(f) for f in params.get('scope') or []])}",
            'prompt = """',
            str(params["prompt"]).strip().replace("\\", "\\\\").replace('"""', "'''"),
            '"""',
            "",
        ]
        (tdir / "acceptance").mkdir(parents=True)
        (tdir / "task.toml").write_text("\n".join(lines), encoding="utf-8")
        for name, source in acceptance.items():
            (tdir / "acceptance" / name).write_text(source, encoding="utf-8")
        try:
            task = load_task(tdir)
        except TaskError as exc:
            raise ToolError(f"written, but it does not load: {exc}") from None
        return {"id": task.id, "dir": str(tdir), "hidden_tests": sorted(acceptance)}

    # -- jobs ------------------------------------------------------------------------------------

    def _start(self, kind: str, work: Callable[[Job], Any]) -> dict[str, Any]:
        with self._lock:
            n = len(self.jobs) + 1
        job = Job(f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{n}", kind)

        def body() -> None:
            try:
                job.result = work(job)
                job.status = "done"
            except Exception as exc:  # the client sees it; the server keeps serving
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
                self.log(traceback.format_exc())

        job.thread = threading.Thread(target=body, name=job.id, daemon=True)
        with self._lock:
            self.jobs[job.id] = job
        job.thread.start()
        return {"job": job.id, "status": job.status, "poll": "status"}

    def _run_like(self, params: dict[str, Any], spec: EngineSpec, kind: str) -> dict[str, Any]:
        target = self._target(params)
        try:
            tasks = find_tasks(target.tasks_dir, [str(t) for t in params.get("tasks") or []])
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        extra: dict[str, Any] = {}
        if params.get("max_iterations") is not None:
            extra["max_iterations"] = int(params["max_iterations"])

        def work(job: Job) -> Any:
            job.say(f"target {target.name} | engine {spec.label} | {len(tasks)} task(s)")
            summaries = dispatch.run_many(self.cfg, target, tasks, spec, log=job.say, **extra)
            job.say(dispatch.table(summaries))
            return {
                "passed": all(s.status == PASSED for s in summaries),
                "runs": [_summary_view(s) for s in summaries],
            }

        return self._start(kind, work)

    def validate(self, params: dict[str, Any]) -> Any:
        return self._run_like(params, EngineSpec(name="oracle", kind="oracle"), "validate")

    def run(self, params: dict[str, Any]) -> Any:
        try:
            spec = self.cfg.engine(params.get("engine"))
        except ConfigError as exc:
            raise ToolError(str(exc)) from None
        return self._run_like(params, spec, "run")

    def integrate(self, params: dict[str, Any]) -> Any:
        target = self._target(params)
        branches = [str(b) for b in params.get("branches") or []]

        def work(job: Job) -> Any:
            branch = dispatch.integrate(self.cfg, target, branches, job.say)
            return {"ok": branch is not None, "integration_branch": branch}

        return self._start("integrate", work)

    def status(self, params: dict[str, Any]) -> Any:
        job = self.jobs.get(str(params["job"]))
        if job is None:
            raise ToolError(f"unknown job {params['job']!r}")
        return job.view()

    def diff(self, params: dict[str, Any]) -> Any:
        run_id = str(params["run_id"])
        found = [s for s in dispatch.load_summaries(self.cfg) if s.run_id == run_id]
        if not found:
            raise ToolError(f"unknown run {run_id!r}")
        s = found[0]
        if not s.commit:
            raise ToolError(f"run {run_id} did not pass ({s.status}); it committed nothing")
        target = self.cfg.target(s.target)
        return {
            "run_id": s.run_id,
            "branch": s.branch,
            "base": s.base_sha,
            "commit": s.commit,
            "diff": git.git(target.repo, "diff", s.base_sha, s.commit),
        }

    # -- JSON-RPC --------------------------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """One request or notification; the reply, or None for a notification."""
        rid = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if method is None:
            return None  # a response to nothing we sent
        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "swarm", "version": "1"},
                    "instructions": (
                        "The swarm harness. Plan tasks for the local model: read the targets "
                        "and tasks, write new tasks with hidden tests, run them, read the "
                        "results and the diffs, integrate what passed. " + NEVER + "."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {"name": t.name, "description": t.description, "inputSchema": t.schema}
                        for t in self.tools.values()
                    ]
                }
            elif method == "tools/call":
                result = self._call(str(params.get("name")), params.get("arguments") or {})
            elif method.startswith("notifications/"):
                return None
            else:
                return _error(rid, -32601, f"method not found: {method}")
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except KeyError as exc:
            return _error(rid, -32602, f"missing argument {exc}")
        except Exception as exc:
            self.log(traceback.format_exc())
            return _error(rid, -32603, f"{type(exc).__name__}: {exc}")
        if rid is None:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}; {NEVER}")
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be an object")
        result = tool.run(arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=1)}],
            "structuredContent": result if isinstance(result, dict) else {"items": result},
        }

    def serve(self, inp: BinaryIO, out: BinaryIO) -> None:
        for raw in inp:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                reply: dict[str, Any] | None = _error(None, -32700, "parse error")
            else:
                reply = self.handle(message) if isinstance(message, dict) else None
            if reply is not None:
                out.write((json.dumps(reply) + "\n").encode("utf-8"))
                out.flush()


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main(cfg: Config) -> int:
    def log(text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    server = Server(cfg, log)
    log(f"swarm mcp: {len(server.tools)} tools; config root {cfg.root}")
    server.serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0
