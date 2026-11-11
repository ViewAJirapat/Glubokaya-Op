"""A studio project: one app, planned by the chief and built by the harness, task by task.

work/studio/<name>/
  app/              the app's git repo: the frame, the planned stubs, and on `main` only what
                    passed every gate, the hidden tests and the regression review
  tasks/            generated harness tasks (task.toml + hidden acceptance tests); outside the
                    repo, so the worker never sees the hidden tests
  runs/ worktrees/  the harness's own run logs and per-run worktrees
  logs/<task>.log   what the UI's log view shows
  plan.json plan.md state.json preview.log

The chief loop: request -> plan (the owner approves or comments) -> stubs, tasks and hidden
tests generated from the plan -> `run_task` per task (worktree, gates, hidden tests,
retries) -> review (`integrate`: every gate and every accepted task's hidden tests on the
merge) -> main fast-forwards, the preview restarts -> next task. A task that fails all its
attempts stops the build until the owner retries or skips it.
"""

from __future__ import annotations

import ast
import json
import queue
import re
import threading
import time
import tomllib
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from swarm import config as config_mod
from swarm import dispatch, git, sandbox, verify
from swarm.config import SANDBOX_KINDS, Config, Target
from swarm.engines import make_engine
from swarm.runner import ERROR, INVALID, PASSED, RunSummary, new_run_id, run_task
from swarm.studio import plan as planmod
from swarm.studio.events import Events
from swarm.studio.plan import Feature, Plan
from swarm.studio.preview import Preview
from swarm.task import load_task

HARNESS_ROOT = Path(__file__).resolve().parents[2]
FRAME = HARNESS_ROOT / "frames" / "webapp"
FRAME_TARGET = HARNESS_ROOT / "frames" / "webapp.toml"
TARGET = "app"
CONTRACTS = "tests/contracts.json"
UI_FILE = "src/webapp/ui.json"
MODEL_MARK = "  | "
ATTEMPT_FILES = ("prompt.md", "feedback.md", "gates.log", "aider.chat.md", "diff.patch")

Say = Callable[[str], None]


class StudioError(Exception):
    pass


class Planner(Protocol):
    label: str  # shown in the UI, e.g. "qwen2.5-coder:14b (Ollama)"

    def draft(
        self,
        request: str,
        previous: Plan | None,
        comment: str | None,
        built: list[Feature],
        progress: Say,
    ) -> Plan: ...


@dataclass
class StudioSettings:
    """[studio] in swarm.toml."""

    engine: str = "studio-14b"  # the worker
    chief_model: str = "qwen2.5-coder:14b"  # the planner; the same model never swaps the GPU
    ollama: str = "http://127.0.0.1:11434"
    num_ctx: int = 8192  # equal to the worker's num_ctx, or Ollama reloads the model
    num_predict: int = 3072
    projects_dir: str = "work/studio"
    feedback_chars: int = 3000  # failure output per retry; 8k-context models need it small
    sandbox: str = "none"  # "docker": worker and checks run in containers (swarm/sandbox.py)

    @classmethod
    def load(cls, config_path: Path) -> StudioSettings:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh).get("studio", {})
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise config_mod.ConfigError(f"[studio]: unknown keys {sorted(unknown)}")
        settings = cls(**raw)
        if settings.sandbox not in SANDBOX_KINDS:
            raise config_mod.ConfigError(f"[studio]: sandbox must be one of {SANDBOX_KINDS}")
        return settings


@dataclass
class TaskState:
    id: str
    feature: str
    title: str
    status: str = "planned"  # planned | running | passed | failed | error | skipped
    runs: list[str] = field(default_factory=list)  # harness run ids, newest last
    branch: str | None = None
    commit: str | None = None
    base_sha: str | None = None
    attempts: int = 0
    max_attempts: int = 0
    seconds: float = 0.0
    note: str = ""


@dataclass
class State:
    request: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None  # the latest draft, or the approved plan
    plan_status: str = "none"  # none | drafting | proposed | approved
    plan_problems: list[str] = field(default_factory=list)
    approved: dict[str, Any] | None = None  # the last approved plan
    tasks: list[TaskState] = field(default_factory=list)
    build: str = "idle"  # idle | running | paused | stopped | done
    pending: list[str] = field(default_factory=list)  # requests that arrived during a build

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> State:
        tasks = [TaskState(**t) for t in raw.pop("tasks", [])]
        known = {f.name for f in fields(cls)}
        return cls(tasks=tasks, **{k: v for k, v in raw.items() if k in known})


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


_CACHES = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _frame_owned(rel: str) -> bool:
    """Files of frames/webapp that stay the frame's in a project; the rest is the plan's."""
    parts = rel.split("/")
    if _CACHES & set(parts) or parts[0].endswith(".egg-info"):
        return False
    if rel in (UI_FILE, CONTRACTS):
        return False
    if rel.startswith("src/webapp/features/"):
        return rel == "src/webapp/features/__init__.py" or rel.startswith(
            "src/webapp/features/health/"
        )
    return True


def project_config(root: Path, harness_config: Path, settings: StudioSettings) -> Config:
    """The harness config for one project: the engines and defaults of swarm.toml, plus a
    target for the project's app repo built from frames/webapp.toml."""
    with harness_config.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    with FRAME_TARGET.open("rb") as fh:
        target: dict[str, Any] = tomllib.load(fh)
    target.update(repo=str(root / "app"), tasks=str(root / "tasks"), template=str(FRAME))
    target["sandbox"] = settings.sandbox  # [studio] sandbox, or `swarm --sandbox ... studio`
    defaults = dict(data.get("defaults", {}))
    defaults.update(
        target=TARGET,
        engine=settings.engine,
        worktrees_dir=str(root / "worktrees"),
        runs_dir=str(root / "runs"),
        feedback_chars=settings.feedback_chars,
    )
    merged = {
        "defaults": defaults,
        "targets": {TARGET: target},
        "engines": data.get("engines", {}),
        "sandbox": data.get("sandbox", {}),
    }
    return config_mod.from_dict(merged, harness_config.resolve().parent)


class Project:
    def __init__(
        self,
        root: Path,
        planner: Planner,
        *,
        harness_config: Path = HARNESS_ROOT / "swarm.toml",
        settings: StudioSettings | None = None,
        engine: str | None = None,
        sandbox: str | None = None,
    ) -> None:
        self.root = root.resolve()
        self.name = self.root.name
        self.planner = planner
        self.settings = settings or StudioSettings()
        if engine:
            self.settings.engine = engine
        if sandbox:
            self.settings.sandbox = sandbox
        self.cfg = project_config(self.root, harness_config, self.settings)
        self.target: Target = self.cfg.target(TARGET)
        self.engine = self.cfg.engine(self.settings.engine)
        self.app = self.target.repo
        self.events = Events()
        self.preview = Preview(self.app, self.root / "preview.log")
        self.busy = ""
        self._lock = threading.RLock()
        self._log_lock = threading.Lock()
        self._log_lines: dict[str, int] = {}  # task id -> lines in logs/<task>.log
        self._jobs: queue.Queue[tuple[str, Callable[[], None]] | None] = queue.Queue()
        self._pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._interrupted_apply = False
        self.state = self._load()

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self, with_preview: bool = True) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.app.exists():
            dispatch.demo_init(self.cfg, self.target, FRAME, force=False, log=lambda _m: None)
        else:
            if self._interrupted_apply and git.git(self.app, "status", "--porcelain").strip():
                # Only the studio writes the app's working tree, and apply_plan was cut off
                # before its commit: these are its half-written stubs.
                self._discard_app_changes()
                self._say(
                    "chief",
                    "Writing the plan into the app was interrupted; I removed the half-written "
                    "files. Approve the plan again.",
                    kind="build",
                )
            updated = self.update_frame()
            if updated:
                self._say(
                    "chief",
                    "The app frame was updated from this studio version: " + ", ".join(updated),
                    kind="build",
                )
        if with_preview:
            self.restart_preview()
        self._worker = threading.Thread(target=self._work, name="studio-jobs", daemon=True)
        self._worker.start()
        if not self.state.messages:
            self._say(
                "chief",
                "Hi. Describe the app you want, in a sentence or two. I'll plan it as small "
                "features, you approve the plan, and the local model builds them one at a time. "
                "Every feature must pass the checks and its hidden tests before it reaches the "
                "preview.",
            )

    def close(self) -> None:
        self._pause.set()
        self._jobs.put(None)
        self.preview.stop()

    def update_frame(self) -> list[str]:
        """Bring an existing app's frame files (core, shell, tools, frame tests) up to this
        studio's frames/webapp, so fixes to the frame reach projects started before them.
        Planned and built features, ui.json and the CONTRACT snapshots are never touched.
        Committed on main only if the frame's gates still pass; otherwise nothing changes."""
        if git.git(self.app, "status", "--porcelain").strip():
            return []
        changed: list[str] = []
        for src in sorted(FRAME.rglob("*")):
            rel = src.relative_to(FRAME).as_posix()
            if not src.is_file() or not _frame_owned(rel):
                continue
            dst = self.app / rel
            data = src.read_bytes()
            if dst.is_file() and dst.read_bytes() == data:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            changed.append(rel)
        if not changed:
            return []
        # main holds only work that passes every gate and every accepted task's hidden tests;
        # a frame update must keep it that way.
        timeout = self.cfg.settings.verify_timeout
        red = [s.name for s in verify.run_gates(self.app, self.target, timeout) if not s.ok]
        for t in self.state.tasks:
            if t.status != "passed":
                continue
            task = load_task(self.target.tasks_dir / t.id)
            work = self.root / "frame-update" / t.id
            acc = verify.run_acceptance(self.app, task, self.target, work, timeout)
            if acc is not None and not acc.ok:
                red.append(f"hidden tests of {t.id}")
        if red:
            self._discard_app_changes()
            self._say(
                "chief",
                "A newer app frame is available but would break this app ("
                + ", ".join(red)
                + "), so the app keeps its frame.",
                kind="error",
            )
            return []
        git.git(self.app, "add", "-A")
        message = f"studio: frame update ({len(changed)} file(s))\n\n" + "\n".join(changed)
        git.git(self.app, *git.identity_args(self.app), "commit", "-q", "-m", message)
        return changed

    def _load(self) -> State:
        path = self.root / "state.json"
        if not path.is_file():
            return State()
        state = State.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for t in state.tasks:  # the process stopped mid-run: the run's worktree is kept
            if t.status == "running":
                t.status, t.note = "failed", "interrupted when the studio stopped"
        if state.build == "running":
            state.build = "paused"
        if state.plan_status == "drafting":
            state.plan_status = "proposed" if state.plan else "none"
        if state.plan_status == "applying":  # stopped while writing the plan into the app
            state.plan_status = "proposed"
            self._interrupted_apply = True
        return state

    def _save(self) -> None:
        # Called from HTTP threads and the job thread: one writer at a time, or the second
        # replace() of state.json fails on Windows while the first still has the file open.
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / "state.json.tmp"
            tmp.write_text(json.dumps(asdict(self.state), indent=1), encoding="utf-8")
            tmp.replace(self.root / "state.json")

    def _discard_app_changes(self) -> None:
        git.git(self.app, "reset", "-q", "--hard", "HEAD")
        git.git(self.app, "clean", "-q", "-fd")

    # -- what the UI reads ---------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self.state)
            data.update(
                name=self.name,
                seq=self.events.seq,
                busy=self.busy,
                engine=self.engine.label,
                chief=self.planner.label,
                preview={
                    "url": self.preview.url,
                    "running": self.preview.running,
                    "error": self.preview.error,
                },
                plan_markdown=planmod.render_plan(planmod.plan_from_dict(self.state.plan))
                if self.state.plan
                else "",
            )
            return data

    def _publish_state(self) -> None:
        self._save()
        self.events.publish("state", {"seq_hint": self.events.seq})

    def _say(self, role: str, text: str, kind: str = "text", **data: Any) -> None:
        with self._lock:
            msg = {
                "id": len(self.state.messages) + 1,
                "role": role,
                "text": text,
                "kind": kind,
                "time": _now(),
                **data,
            }
            self.state.messages.append(msg)
            self._save()
        self.events.publish("message", msg)

    def _set_busy(self, text: str) -> None:
        self.busy = text
        self.events.publish("busy", {"busy": text})

    def task(self, tid: str) -> TaskState:
        for t in self.state.tasks:
            if t.id == tid:
                return t
        raise StudioError(f"no task {tid!r}")

    def task_log(self, tid: str) -> str:
        """The whole log. Read under the log lock, so a client that buffers live `log`
        events while fetching this can drop exactly the lines it already has (event `n`)."""
        self.task(tid)
        path = self.root / "logs" / f"{tid}.log"
        with self._log_lock:
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def task_diff(self, tid: str) -> str:
        t = self.task(tid)
        if t.status == "passed" and t.commit and t.base_sha:
            return git.git(self.app, "diff", "--no-color", t.base_sha, t.commit)
        if not t.runs:
            return ""
        run_dir = self.cfg.runs_dir / t.runs[-1]
        final = run_dir / "final.patch"
        if final.is_file():
            return final.read_text(encoding="utf-8", errors="replace")
        iters = sorted(run_dir.glob("iter-*"), key=lambda p: int(p.name.split("-")[1]))
        for d in reversed(iters):
            if (d / "diff.patch").is_file():
                return (d / "diff.patch").read_text(encoding="utf-8", errors="replace")
        return ""

    def task_attempts(self, tid: str) -> list[dict[str, Any]]:
        t = self.task(tid)
        out: list[dict[str, Any]] = []
        for run_id in t.runs:
            run_dir = self.cfg.runs_dir / run_id
            records: dict[int, dict[str, Any]] = {}
            summary = run_dir / "summary.json"
            if summary.is_file():
                data = json.loads(summary.read_text(encoding="utf-8"))
                records = {r["n"]: r for r in data.get("records", [])}
            iters = sorted(run_dir.glob("iter-*"), key=lambda p: int(p.name.split("-")[1]))
            for d in iters:
                n = int(d.name.split("-")[1])
                out.append(
                    {
                        "run": run_id,
                        "n": n,
                        "files": [f for f in ATTEMPT_FILES if (d / f).is_file()],
                        "record": records.get(n),
                    }
                )
        return out

    def attempt_file(self, tid: str, run_id: str, n: int, name: str) -> str:
        t = self.task(tid)
        if run_id not in t.runs or name not in ATTEMPT_FILES:
            raise StudioError("unknown attempt file")
        path = self.cfg.runs_dir / run_id / f"iter-{n}" / name
        if not path.is_file():
            raise StudioError("no such file")
        return path.read_text(encoding="utf-8", errors="replace")

    # -- what the UI does ----------------------------------------------------------------------

    def chat(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._say("user", text)
        with self._lock:
            status, build = self.state.plan_status, self.state.build
            if status in ("drafting", "applying"):
                self.state.pending.append(text)
                self._save()
                self._say(
                    "chief", "I'm still working on the plan; I'll take this into account next."
                )
            elif build == "running":
                self.state.pending.append(text)
                self._save()
                self._say(
                    "chief",
                    "Noted. I'll plan it after the current build. Pause the build if you want "
                    "me to plan it sooner.",
                )
            elif status == "none":
                self.state.request = text
                self._submit("planning", lambda: self._draft(text, None, None))
            elif status == "proposed":
                previous = planmod.plan_from_dict(self.state.plan or {})
                self._submit(
                    "revising the plan", lambda: self._draft(self.state.request, previous, text)
                )
            else:  # approved: plan more features on top of what exists
                previous = planmod.plan_from_dict(self.state.approved or {})
                self._submit("planning additions", lambda: self._draft(text, previous, text))

    def approve(self) -> None:
        with self._lock:
            if self.state.plan_status != "proposed" or not self.state.plan:
                raise StudioError("there is no proposed plan to approve")
            if self.state.plan_problems:
                raise StudioError("the plan still has problems; comment to get them fixed")
            if self.state.build == "running":
                raise StudioError("a build is running")
            plan = planmod.plan_from_dict(self.state.plan)
            # "approved" only once apply_plan has committed; a failure goes back to "proposed"
            self.state.plan_status = "applying"
            self._publish_state()
        self._say("user", f"Approved plan v{plan.version}.", kind="action")
        self._submit("writing the plan into the app", lambda: self._apply_and_build(plan))

    def pause(self) -> None:
        self._pause.set()
        self._say("user", "Pause the build after the current task.", kind="action")

    def resume(self) -> None:
        with self._lock:
            if self.state.build == "running":
                return
        self._pause.clear()
        self._say("user", "Resume the build.", kind="action")
        self._submit("building", self._build)

    def retry(self, tid: str) -> None:
        with self._lock:
            t = self.task(tid)
            if t.status not in ("failed", "error", "skipped"):
                raise StudioError(f"task {tid} is {t.status}")
            t.status, t.note = "planned", ""
            self._publish_state()
        self._say("user", f"Retry {tid}.", kind="action")
        if self.state.build != "running":
            self._pause.clear()
            self._submit("building", self._build)

    def skip(self, tid: str) -> None:
        with self._lock:
            t = self.task(tid)
            if t.status not in ("failed", "error", "planned"):
                raise StudioError(f"task {tid} is {t.status}")
            t.status, t.note = "skipped", "skipped by the owner"
            self._publish_state()
        self._say(
            "user",
            f"Skip {tid}. Later features that use its routes will fail their hidden tests.",
            kind="action",
        )
        if self.state.build != "running":
            self._pause.clear()
            self._submit("building", self._build)

    # -- the job thread: one model job at a time (one GPU) ----------------------------------------

    def _submit(self, label: str, job: Callable[[], None]) -> None:
        self._jobs.put((label, job))

    def _work(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                self._jobs.task_done()
                return
            label, job = item
            self._set_busy(label)
            try:
                job()
            except Exception as exc:  # the UI shows it; the studio keeps running
                details = traceback.format_exc()
                try:
                    self._say("chief", f"Something went wrong while {label}: {exc}", kind="error")
                    with (self.root / "errors.log").open("a", encoding="utf-8") as fh:
                        fh.write(f"--- {datetime.now()} {label}\n{details}\n")
                except Exception:  # reporting must never end the only job thread
                    pass
            finally:
                try:
                    self._set_busy("")
                    self._publish_state()
                except Exception:
                    pass
                self._jobs.task_done()

    def wait_idle(self, timeout: float) -> bool:
        """True once every submitted job (and the jobs they submitted) has finished."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._jobs.unfinished_tasks == 0:
                return True
            time.sleep(0.1)
        return False

    def built_features(self) -> list[Feature]:
        if not self.state.approved:
            return []
        return planmod.plan_from_dict(self.state.approved).features

    def _draft(self, request: str, previous: Plan | None, comment: str | None) -> None:
        with self._lock:
            was = self.state.plan_status
            self.state.plan_status = "drafting"
            self._publish_state()
        built = self.built_features()
        try:
            plan = self.planner.draft(request, previous, comment, built, self._set_busy)
        except Exception:
            with self._lock:
                self.state.plan_status = was if was != "drafting" else "none"
            raise
        approved_version = (self.state.approved or {}).get("version", 0)
        plan.version = max(previous.version if previous else 0, approved_version) + 1
        found = planmod.problems(plan, {f.name for f in built})
        with self._lock:
            self.state.plan = plan.to_dict()
            self.state.plan_problems = found
            self.state.plan_status = "proposed"
            self._publish_state()
        (self.root / "plan.md").write_text(planmod.render_plan(plan), encoding="utf-8")
        new = [f for f in plan.features if f.name not in {b.name for b in built}]
        lines = [f"Plan v{plan.version}: {plan.title}. {plan.summary}".strip()]
        lines += [
            f"{n}. {f.name}: {f.title} ({len(f.routes)} routes)" for n, f in enumerate(new, 1)
        ]
        if found:
            lines.append(f"It has {len(found)} problem(s) to fix first; see the plan.")
        else:
            lines.append("Approve it, or tell me what to change.")
        self._say("chief", "\n".join(lines), kind="plan", version=plan.version)
        with self._lock:
            pending, self.state.pending = self.state.pending, []
        if pending and self.state.build != "running":
            note = "\n".join(pending)
            self._submit("revising the plan", lambda: self._draft(self.state.request, plan, note))

    # -- plan -> files in the app repo -------------------------------------------------------------

    def _apply_and_build(self, plan: Plan) -> None:
        try:
            self.apply_plan(plan)
        except BaseException:
            with self._lock:  # nothing was committed: the plan can be approved again
                self.state.plan_status = "proposed"
                self._publish_state()
            raise
        self._pause.clear()
        self._build()

    def apply_plan(self, plan: Plan) -> None:
        """Write stubs, contracts and the page for new features, check that the frame's gates
        still pass, commit on main, and generate one harness task per new feature."""
        planned = {t.feature for t in self.state.tasks}
        new = [f for f in plan.features if f.name not in planned]
        app = self.app
        status = git.git(app, "status", "--porcelain")
        if status.strip():
            raise StudioError(f"the app repo has uncommitted changes:\n{status}")
        for f in new:
            if (app / f.folder).exists():
                raise StudioError(f"{f.folder} already exists in the app")
        try:  # any failure before the commit leaves the app exactly as it was
            self._write_plan_files(plan, new)
            git.git(app, "add", "-A")
            names = ", ".join(f.name for f in new) or "page only"
            if git.git(app, "status", "--porcelain").strip():
                message = f"studio: plan v{plan.version} ({names})"
                git.git(app, *git.identity_args(app), "commit", "-q", "-m", message)
        except BaseException:
            self._discard_app_changes()
            raise
        start = len(self.state.tasks) + 1
        with self._lock:
            for order, f in enumerate(new, start):
                tid = planmod.task_id(order, f)
                tdir = self.target.tasks_dir / tid
                (tdir / "acceptance").mkdir(parents=True, exist_ok=True)
                (tdir / "task.toml").write_text(planmod.task_toml(f), encoding="utf-8")
                (tdir / "acceptance" / f"test_{f.name}.py").write_text(
                    planmod.acceptance_source(f), encoding="utf-8"
                )
                self.state.tasks.append(TaskState(id=tid, feature=f.name, title=f.title))
            self.state.approved = plan.to_dict()
            self.state.plan_status = "approved"
            self._publish_state()
        (self.root / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2), "utf-8")
        self.restart_preview()
        self._say(
            "chief",
            f"Plan v{plan.version} is in the app: {len(new)} stub(s), the page and "
            f"{sum(len(f.scenarios) for f in new)} hidden test scenario(s). The preview shows "
            "the page; parts not built yet are marked.",
        )

    def _write_plan_files(self, plan: Plan, new: list[Feature]) -> None:
        app = self.app
        for f in new:
            folder = app / f.folder
            folder.mkdir(parents=True)
            (folder / "__init__.py").write_text(planmod.init_source(f), encoding="utf-8")
            (folder / "handler.py").write_text(planmod.stub_source(f), encoding="utf-8")
        (app / UI_FILE).write_text(json.dumps(plan.ui_json(), indent=2) + "\n", encoding="utf-8")
        timeout = self.cfg.settings.verify_timeout
        verify.run_autofix(app, self.target, timeout)  # stubs exactly as ruff formats them
        contracts_path = app / CONTRACTS
        contracts: dict[str, str] = json.loads(contracts_path.read_text(encoding="utf-8"))
        for f in new:
            tree = ast.parse((app / f.handler).read_text(encoding="utf-8"))
            contracts[f.handler] = ast.get_docstring(tree, clean=True) or ""
        contracts_path.write_text(json.dumps(contracts, indent=2, sort_keys=True) + "\n", "utf-8")
        red = [s for s in verify.run_gates(app, self.target, timeout) if not s.ok]
        if red:
            details = "\n\n".join(f"{s.name}:\n{s.output[-1500:]}" for s in red)
            raise StudioError(f"the generated stubs fail the frame's gates:\n{details}")

    # -- the build ---------------------------------------------------------------------------------

    def _build(self) -> None:
        try:  # Ollama up, model pulled, aider installed; else the build waits for Resume
            box = sandbox.make_box(self.cfg, self.target)
            box.preflight()
            make_engine(self.engine).preflight(box)
        except Exception as exc:
            self._stop_build("paused", f"The build can't start: {exc}\nFix that, then Resume.")
            return
        with self._lock:
            self.state.build = "running"
            self._publish_state()
        try:
            for t in list(self.state.tasks):
                if t.status in ("passed", "skipped"):
                    continue
                if t.status in ("failed", "error"):
                    self._stop_build("paused", f"{t.id} needs you: retry or skip it.", t.id)
                    return
                if self._pause.is_set():
                    self._stop_build("paused", "Build paused.")
                    return
                self._run(t)
                if t.status != "passed":
                    self._stop_build("paused", self._failure_note(t), t.id)
                    return
        except Exception:
            with self._lock:  # never leave a task "running" with no Retry
                for t in self.state.tasks:
                    if t.status == "running":
                        t.status, t.note = "error", "the studio hit an error during this task"
            self._stop_build("paused", "")
            raise
        done = sum(t.status == "passed" for t in self.state.tasks)
        self._stop_build("done", f"Build finished: {done}/{len(self.state.tasks)} features built.")

    def _stop_build(self, status: str, note: str, task: str | None = None) -> None:
        with self._lock:
            self.state.build = status
            pending, self.state.pending = self.state.pending, []
            self._publish_state()
        if note:
            self._say("chief", note, kind="error" if task else "build", task=task)
        if pending:  # notes that arrived during the build ("I'll plan it after the build")
            previous = planmod.plan_from_dict(self.state.approved or {})
            request = "\n".join(pending)
            self._submit("planning additions", lambda: self._draft(request, previous, request))

    def _failure_note(self, t: TaskState) -> str:
        what = "did not pass" if t.status == "failed" else "hit an error"
        return (
            f"{t.id} {what}: {t.note}\nOpen the task to see the log, the diff and each "
            "attempt's feedback. Then retry it (the model's answers vary), or skip it."
        )

    def _log(self, tid: str, text: str, stream: str = "harness") -> None:
        """Append to logs/<task>.log and publish each line with its 1-based number `n`, so the
        UI can merge a fetched log with live events without losing or repeating lines."""
        path = self.root / "logs" / f"{tid}.log"
        with self._log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if tid not in self._log_lines:
                existing = (
                    path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
                )
                self._log_lines[tid] = len(existing.splitlines())
            with path.open("a", encoding="utf-8") as fh:
                for line in text.splitlines() or [""]:
                    if stream == "model":
                        line = MODEL_MARK + line  # a reloaded log still shows the model's lines
                    fh.write(line + "\n")
                    self._log_lines[tid] += 1
                    data = {"task": tid, "line": line, "stream": stream, "n": self._log_lines[tid]}
                    self.events.publish("log", data)

    def _run(self, t: TaskState) -> None:
        task = load_task(self.target.tasks_dir / t.id)
        run_id = new_run_id(task, self.engine)
        with self._lock:
            t.status, t.note = "running", ""
            t.runs.append(run_id)
            t.attempts, t.max_attempts = 0, self.cfg.settings.max_iterations
            self._publish_state()
        self._log(t.id, f"=== run {run_id} ({self.engine.label}) ===")
        self._set_busy(f"{t.id}: checking the base")

        def log(line: str) -> None:
            self._log(t.id, line)
            m = re.search(r"iteration (\d+)/(\d+)", line)
            if m:
                self._set_busy(f"{t.id}: attempt {m.group(1)}/{m.group(2)}, model writing")
                with self._lock:
                    t.attempts = int(m.group(1))
                    self._publish_state()
            elif re.search(r"\] {2}\S+=(ok|FAIL)|no changes$", line):
                self._set_busy(f"{t.id}: attempt {t.attempts}/{t.max_attempts}, checked")

        stop = threading.Event()
        tail = threading.Thread(
            target=self._tail, args=(t.id, self.cfg.runs_dir / run_id, stop), daemon=True
        )
        tail.start()
        try:
            s = run_task(self.cfg, self.target, task, self.engine, log=log, run_id=run_id)
        finally:
            stop.set()
            tail.join(timeout=5)
        with self._lock:
            t.attempts, t.seconds, t.base_sha = s.iterations, s.seconds, s.base_sha
        if s.status == PASSED:
            try:
                self._review(t, s)
            except Exception as exc:  # e.g. integrate or the fast-forward raised
                self._log(t.id, f"review error: {type(exc).__name__}: {exc}")
                with self._lock:
                    t.status = "error"
                    t.note = f"passed alone, but the review hit an error: {exc}"
                    self._publish_state()
        else:
            with self._lock:
                t.status = "error" if s.status in (ERROR, INVALID) else "failed"
                t.note = s.error or self._last_checks(s)
                self._publish_state()

    @staticmethod
    def _last_checks(s: RunSummary) -> str:
        if not s.records:
            return f"{s.status} after {s.iterations} attempt(s)"
        failed = [st["name"] for st in s.records[-1].stages if not st["ok"]]
        detail = ", ".join(verify.short_name(str(n)) for n in failed) or "no changes"
        return f"failed after {s.iterations} attempt(s); last attempt failed: {detail}"

    def _review(self, t: TaskState, s: RunSummary) -> None:
        """Merge every accepted branch plus this one, rerun all gates and every accepted
        task's hidden tests, and move main only if all of that passes."""
        accepted = [x.branch for x in self.state.tasks if x.status == "passed" and x.branch]
        self._set_busy(f"{t.id}: review (every check on the merge)")
        self._log(t.id, "--- review: merge with the accepted features, rerun every check ---")
        branch = dispatch.integrate(
            self.cfg, self.target, [*accepted, s.branch], lambda m: self._log(t.id, m)
        )
        if not branch:
            with self._lock:
                t.status = "failed"
                t.note = "passed alone, but the review failed (see the log)"
                self._publish_state()
            return
        git.git(self.app, "merge", "-q", "--ff-only", branch)
        with self._lock:
            t.status, t.branch, t.commit = "passed", s.branch, s.commit
            self._publish_state()
        self.restart_preview()
        tries = "first attempt" if s.iterations == 1 else f"attempt {s.iterations}"
        self._say(
            "chief",
            f"{t.id} passed on the {tries} ({s.seconds:.0f}s) and is merged; the preview "
            "now runs it.",
            kind="build",
            task=t.id,
        )

    def restart_preview(self) -> None:
        ok = self.preview.restart()
        self.events.publish(
            "preview", {"url": self.preview.url, "running": ok, "error": self.preview.error}
        )

    def _tail(self, tid: str, run_dir: Path, stop: threading.Event) -> None:
        """Stream the model's side of each attempt (Aider's chat transcript) into the log."""
        offsets: dict[Path, int] = {}
        while True:
            finished = stop.wait(1.0)
            for chat in sorted(run_dir.glob("iter-*/aider.chat.md")):
                try:
                    size = chat.stat().st_size
                    if size <= offsets.get(chat, 0):
                        continue
                    with chat.open("rb") as fh:
                        fh.seek(offsets.get(chat, 0))
                        data = fh.read(size - offsets.get(chat, 0))
                    offsets[chat] = size
                except OSError:
                    continue
                prompt_lines = 0
                for line in data.decode("utf-8", errors="replace").splitlines():
                    if line.startswith("#### "):  # Aider echoes the whole prompt this way
                        prompt_lines += 1
                        continue
                    if prompt_lines:
                        self._log(tid, f"[prompt sent: {prompt_lines} lines]", "model")
                        prompt_lines = 0
                    if line.strip():
                        self._log(tid, line, "model")
                if prompt_lines:
                    self._log(tid, f"[prompt sent: {prompt_lines} lines]", "model")
            if finished:
                return


def open_project(
    name: str,
    planner: Planner,
    *,
    harness_config: Path = HARNESS_ROOT / "swarm.toml",
    engine: str | None = None,
    sandbox: str | None = None,
) -> Project:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", name):
        raise StudioError(f"bad project name {name!r}: letters, digits, - and _ only")
    settings = StudioSettings.load(harness_config)
    projects = Path(settings.projects_dir)
    if not projects.is_absolute():
        projects = harness_config.resolve().parent / projects
    return Project(
        projects / name,
        planner,
        harness_config=harness_config,
        settings=settings,
        engine=engine,
        sandbox=sandbox,
    )
