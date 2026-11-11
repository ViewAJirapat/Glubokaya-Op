"""Regression tests for the problems the 2026-11-02 multi-agent review confirmed."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from swarm import config as config_mod
from swarm import dispatch, git, verify
from swarm.studio import chief as chiefmod
from swarm.studio import plan as planmod
from swarm.studio import project as projectmod
from swarm.studio.project import Project, StudioSettings
from swarm.studio.server import StudioServer, static_file
from tests.studio_helpers import TODO, make_project, todo_plan

# -- plan checks and generated code ------------------------------------------------------------


def _problems(change: Any) -> list[str]:
    raw = copy.deepcopy(TODO)
    change(raw)
    return planmod.problems(planmod.plan_from_dict(raw))


def test_two_routes_with_one_function_name_are_a_problem() -> None:
    def change(raw: dict[str, Any]) -> None:
        raw["features"][2]["routes"].append(
            {
                "method": "GET",
                "path": "/api/todo_stats",
                "does": "x",
                "body": "",
                "reply": "200 {}",
                "errors": [],
            }
        )

    assert any("would both be function get_todo_stats" in p for p in _problems(change))


def test_long_titles_are_wrapped_and_overlong_lines_are_a_problem() -> None:
    raw = copy.deepcopy(TODO)
    raw["features"][0]["title"] = "Add and list todo items with a title, a due date and a flag " * 2
    plan = planmod.plan_from_dict(raw)
    f = plan.features[0]
    assert max(len(line) for line in planmod.stub_source(f).splitlines()) <= 100
    assert max(len(line) for line in planmod.init_source(f).splitlines()) <= 100
    ast.parse(planmod.init_source(f))
    assert not [p for p in planmod.problems(plan) if "characters long" in p]

    def long_path(raw: dict[str, Any]) -> None:
        raw["features"][1]["routes"][1]["path"] = "/api/" + "very-long-segment-" * 6 + "{id}"

    assert any("characters long" in p for p in _problems(long_path))


def test_expect_must_be_json_not_text_and_json_strings_are_parsed() -> None:
    def text_expect(raw: dict[str, Any]) -> None:
        raw["features"][0]["scenarios"][1]["steps"][0]["expect"] = '200 {"todos": []}'

    assert any("expect must be the reply JSON" in p for p in _problems(text_expect))
    step = planmod.step_from_dict(
        {"method": "GET", "path": "/api/todos", "status": 200, "expect": '{"todos": []}'}
    )
    assert step.expect == {"todos": []}


def test_del_character_is_dropped_so_task_toml_stays_valid() -> None:
    raw = copy.deepcopy(TODO)
    raw["features"][0]["routes"][0]["does"] = "Add\x7f a todo."
    plan = planmod.plan_from_dict(raw)
    assert "\x7f" not in plan.features[0].routes[0].does
    tomllib.loads(planmod.task_toml(plan.features[0]))


def _run_generated_test(tmp_path: Path, feature: planmod.Feature, app_source: str) -> str:
    """Run a generated hidden test against a stand-in `webapp.app`."""
    pkg = tmp_path / "fake" / "webapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "app.py").write_text(app_source, encoding="utf-8")
    test = tmp_path / "test_generated.py"
    test.write_text(planmod.acceptance_source(feature), encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test)],
        cwd=tmp_path,
        env={**_base_env(), "PYTHONPATH": str(tmp_path / "fake")},
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout + res.stderr


def _base_env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}


def test_hidden_tests_judge_the_json_the_app_serves(tmp_path: Path) -> None:
    feature = planmod.Feature(
        "stats",
        "Stats",
        "",
        [planmod.RouteSpec("GET", "/api/stats", "counts", reply='200 {"by": {"1": 2}}')],
        [
            planmod.Scenario(
                "count", [planmod.Step("GET", "/api/stats", 200, expect={"by": {"1": 2}})]
            )
        ],
    )
    stub = (
        "class R:\n    def __init__(self, s, b):\n        self.status, self.body = s, b\n"
        "class A:\n    def handle(self, m, p, b, q):\n        return R(200, BODY)\n"
        "def create_app():\n    return A()\n"
    )
    # int keys serialize to the right JSON: passes
    assert "1 passed" in _run_generated_test(
        tmp_path / "a", feature, "BODY = {'by': {1: 2}}\n" + stub
    )
    # a set can't be served: fails with a clear message, even though the expect would not see it
    feature.scenarios[0].steps[0].expect = None
    out = _run_generated_test(tmp_path / "b", feature, "BODY = {'by': {1, 2}}\n" + stub)
    assert "1 failed" in out and "not JSON-serializable" in out


# -- the chief's scenario reuse ------------------------------------------------------------------


class ScriptedLLM(chiefmod.Ollama):
    def __init__(self, answers: list[Any]) -> None:
        super().__init__("http://127.0.0.1:9", "fake", 8192, 100)
        self.answers = answers
        self.kinds: list[str] = []

    def complete(self, system: str, user: str, schema: dict[str, Any], progress: Any = None) -> Any:
        self.kinds.append("outline" if schema is chiefmod.OUTLINE_SCHEMA else "scenarios")
        return self.answers.pop(0)


def _outline(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    for f in out["features"]:
        f.pop("scenarios", None)
    return out


def _scen(name: str) -> dict[str, Any]:
    return {
        "scenarios": copy.deepcopy(
            next(f for f in TODO["features"] if f["name"] == name)["scenarios"]
        )
    }


def test_scenarios_are_regenerated_when_an_earlier_feature_changed() -> None:
    previous = todo_plan()
    revised = _outline(TODO)
    revised["features"][0]["routes"][0]["reply"] = (
        '201 {"id": 1, "title": "x", "done": false, "n": 0}'
    )
    llm = ScriptedLLM([revised, _scen("todos"), _scen("todo_edit"), _scen("todo_stats")])
    chiefmod.Chief(llm).draft("todo", previous, "add n", [], lambda _m: None)
    # todos changed, so todo_edit and todo_stats (which set up data with it) are redone too
    assert llm.kinds == ["outline", "scenarios", "scenarios", "scenarios"]


def test_reused_scenarios_must_still_pass_the_checks() -> None:
    previous = todo_plan()
    previous.features[2].scenarios[0].steps[0].path = "/api/todos/{id}"  # broken earlier
    llm = ScriptedLLM([_outline(TODO), _scen("todo_stats")])
    plan = chiefmod.Chief(llm).draft("todo", previous, "nothing", [], lambda _m: None)
    assert llm.kinds == ["outline", "scenarios"]  # only the broken feature was redone
    assert planmod.problems(plan) == []


# -- the project: approve, build start, review errors, notes, frame updates -------------------


def test_a_failed_apply_returns_to_proposed_and_leaves_the_app_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path, TODO)
    project.start(with_preview=False)
    try:
        project.chat("a todo list")
        assert project.wait_idle(60)

        def broken(self: Project, plan: planmod.Plan, new: list[planmod.Feature]) -> None:
            (self.app / "half-written.txt").write_text("x", encoding="utf-8")
            raise projectmod.StudioError("the generated stubs fail the frame's gates")

        monkeypatch.setattr(Project, "_write_plan_files", broken)
        project.approve()
        assert project.wait_idle(60)
        assert project.state.plan_status == "proposed"
        assert project.state.tasks == []
        assert git.git(project.app, "status", "--porcelain") == ""
        monkeypatch.undo()
        project.approve()  # can be approved again
        assert project.wait_idle(120)
        assert project.state.plan_status == "approved"
    finally:
        project.close()


def test_an_interrupted_apply_is_cleaned_up_on_the_next_start(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO)
    project.start(with_preview=False)
    project.chat("a todo list")
    assert project.wait_idle(60)
    project.state.plan_status = "applying"  # as if the studio died while applying
    project._save()
    project.close()
    (project.app / "src" / "webapp" / "features" / "todos").mkdir(parents=True)
    (project.app / "src" / "webapp" / "features" / "todos" / "handler.py").write_text("x")

    again = make_project(tmp_path, TODO)
    assert again.state.plan_status == "proposed"
    again.start(with_preview=False)
    try:
        assert git.git(again.app, "status", "--porcelain") == ""
        assert "interrupted" in again.state.messages[-1]["text"]
    finally:
        again.close()


def test_a_build_that_cannot_start_pauses_with_the_reason(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO, engine="broken")
    project.start(with_preview=False)
    try:
        project.chat("a todo list")
        assert project.wait_idle(60)
        project.approve()
        assert project.wait_idle(120)
        assert project.state.build == "paused"
        assert all(t.status == "planned" for t in project.state.tasks)
        assert "can't start" in project.state.messages[-1]["text"]
    finally:
        project.close()


def test_a_review_error_marks_the_task_so_it_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = copy.deepcopy(TODO)
    raw["features"] = raw["features"][:1]
    raw["ui"]["sections"] = [raw["ui"]["sections"][0]]
    project = make_project(tmp_path, raw)
    project.start(with_preview=False)
    try:
        project.chat("todos")
        assert project.wait_idle(60)

        def boom(*_a: Any, **_k: Any) -> str:
            raise git.GitError("merge failed")

        monkeypatch.setattr(dispatch, "integrate", boom)
        project.approve()
        assert project.wait_idle(300)
        task = project.state.tasks[0]
        assert task.status == "error" and "review hit an error" in task.note
        assert project.state.build == "paused"
    finally:
        project.close()


def test_notes_sent_during_a_build_are_planned_when_it_stops(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO)
    project.start(with_preview=False)
    try:
        project.chat("a todo list")
        assert project.wait_idle(60)
        project.state.pending = ["also count overdue todos"]
        project._stop_build("paused", "Build paused.")
        assert project.wait_idle(60)
        assert ("also count overdue todos", "also count overdue todos") in project.planner.calls
        assert project.state.pending == []
    finally:
        project.close()


def test_a_frame_update_that_breaks_a_built_feature_is_not_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path, TODO)
    project.start(with_preview=False)
    project.chat("a todo list")
    assert project.wait_idle(60)
    project.apply_plan(planmod.plan_from_dict(project.state.plan or {}))
    project.state.tasks[0].status = "passed"  # pretend 01 was built
    project._save()
    project.close()
    git.git(project.app, "rm", "-q", "tools/restore_contracts.py")
    git.git(project.app, "commit", "-qm", "older frame")
    head = git.rev_parse(project.app, "HEAD")

    def failing(*_a: Any, **_k: Any) -> verify.StageResult:
        return verify.StageResult("hidden acceptance tests", False, 1, 0.0, "boom")

    monkeypatch.setattr(verify, "run_acceptance", failing)
    again = make_project(tmp_path, TODO)
    assert again.update_frame() == []
    assert git.rev_parse(again.app, "HEAD") == head
    assert git.git(again.app, "status", "--porcelain") == ""


def test_log_lines_are_numbered_across_restarts(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO)
    seen: list[tuple[int, str]] = []
    project._log("01-x", "first\nsecond")
    project._log("01-x", "third", "model")
    events = project.events.since(0, 0) or []
    seen = [(e.data["n"], e.data["line"]) for e in events if e.kind == "log"]
    assert seen == [(1, "first"), (2, "second"), (3, projectmod.MODEL_MARK + "third")]
    again = make_project(tmp_path, TODO)
    again._log("01-x", "fourth")
    last = (again.events.since(0, 0) or [])[-1]
    assert last.data["n"] == 4


# -- sandbox, preview environment, server -------------------------------------------------------


def test_the_sandbox_setting_reaches_the_studio_target(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO, sandbox="docker")
    assert project.target.sandbox == "docker"
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[studio]\nsandbox = "vm"\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="sandbox"):
        StudioSettings.load(cfg)


def test_the_preview_gets_the_scrubbed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    project = make_project(tmp_path, TODO)
    env = project.preview.child_env()
    assert "GH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["PORT"] == str(project.preview.port)


@pytest.mark.parametrize(
    "path",
    [
        "//host/share",
        "/static//host/share",
        "/static/..\\server.py",
        "/static/C:/x",
        "/static/../app.py",
        "/x",
    ],
)
def test_static_paths_are_checked_before_the_filesystem(path: str) -> None:
    assert static_file(path) is None


def test_a_non_ascii_token_is_a_plain_403(tmp_path: Path) -> None:
    project = make_project(tmp_path, TODO)
    server = StudioServer(project)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/state?token=%C3%A9"
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(url, timeout=10)
        assert err.value.code == 403
        assert static_file("/") is not None and json.dumps({})  # the page still serves
    finally:
        server.stop()
