"""The chief's logic with a scripted model: one outline call, one scenario call per feature,
one retry when plain-code checks find problems, and scenarios kept across revisions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from swarm.studio import plan as planmod
from swarm.studio.chief import (
    OUTLINE_SCHEMA,
    SCENARIO_SCHEMA,
    Chief,
    ChiefError,
    FilePlanner,
    Ollama,
)

HERE = Path(__file__).resolve().parent / "studio"
TODO: dict[str, Any] = json.loads((HERE / "todo_plan.json").read_text(encoding="utf-8"))


def _outline(raw: dict[str, Any] = TODO) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    for f in out["features"]:
        f.pop("scenarios", None)
    return out


def _scenarios(name: str) -> dict[str, Any]:
    f = next(f for f in TODO["features"] if f["name"] == name)
    return {"scenarios": copy.deepcopy(f["scenarios"])}


class FakeLLM(Ollama):
    """Answers each call from a script; records (kind, user message)."""

    def __init__(self, answers: list[Any]) -> None:
        super().__init__("http://127.0.0.1:9", "fake-model", 8192, 1000)
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    def chat_json(
        self, system: str, user: str, schema: dict[str, Any], progress: Any = None
    ) -> Any:
        kind = "outline" if schema is OUTLINE_SCHEMA else "scenarios"
        assert schema in (OUTLINE_SCHEMA, SCENARIO_SCHEMA)
        self.calls.append((kind, user))
        if progress:
            progress("working")
        return self.answers.pop(0)


def _quiet(_m: str) -> None:
    pass


def test_outline_then_scenarios_per_feature() -> None:
    llm = FakeLLM(
        [_outline(), _scenarios("todos"), _scenarios("todo_edit"), _scenarios("todo_stats")]
    )
    plan = Chief(llm).draft("a todo app", None, None, [], _quiet)
    assert [k for k, _ in llm.calls] == ["outline", "scenarios", "scenarios", "scenarios"]
    assert planmod.problems(plan) == []
    # a later feature's scenario call lists the earlier features' routes
    assert "POST /api/todos" in llm.calls[2][1] and "Earlier features" in llm.calls[2][1]


def test_outline_problems_get_one_retry() -> None:
    bad = _outline()
    bad["ui"]["sections"][0]["action"] = "POST /api/items"  # no such route
    llm = FakeLLM(
        [bad, _outline(), _scenarios("todos"), _scenarios("todo_edit"), _scenarios("todo_stats")]
    )
    plan = Chief(llm).draft("a todo app", None, None, [], _quiet)
    assert [k for k, _ in llm.calls][:2] == ["outline", "outline"]
    assert "'POST /api/items' is not a planned route" in llm.calls[1][1]
    assert planmod.problems(plan) == []


def test_scenario_problems_get_one_retry() -> None:
    bad = _scenarios("todo_edit")
    bad["scenarios"][1]["steps"][0]["path"] = "/api/todos/{id}"
    llm = FakeLLM(
        [
            _outline(),
            _scenarios("todos"),
            bad,
            _scenarios("todo_edit"),
            _scenarios("todo_stats"),
        ]
    )
    plan = Chief(llm).draft("a todo app", None, None, [], _quiet)
    assert len(llm.calls) == 5
    assert "use a real value" in llm.calls[3][1]
    assert planmod.problems(plan) == []


def test_revision_keeps_scenarios_of_unchanged_features() -> None:
    previous = planmod.plan_from_dict(copy.deepcopy(TODO))
    revised = _outline()
    revised["features"][2]["routes"][0]["reply"] = '200 {"total": 2, "done": 1, "left": 1}'
    new_stats = _scenarios("todo_stats")
    llm = FakeLLM([revised, new_stats])
    plan = Chief(llm).draft("a todo app", previous, "call it left, not open", [], _quiet)
    assert [k for k, _ in llm.calls] == ["outline", "scenarios"]  # only todo_stats changed
    assert "call it left, not open" in llm.calls[0][1] and "previous plan" in llm.calls[0][1]
    assert plan.features[0].scenarios == previous.features[0].scenarios


def test_built_features_stay_as_built() -> None:
    built = planmod.plan_from_dict(copy.deepcopy(TODO)).features[:1]
    answer = _outline()
    answer["features"][0]["routes"][0]["does"] = "something the model made up"
    llm = FakeLLM([answer, _scenarios("todo_edit"), _scenarios("todo_stats")])
    plan = Chief(llm).draft("add editing and counts", None, None, built, _quiet)
    assert plan.features[0] == built[0]
    assert "already built" in llm.calls[0][1]
    assert [k for k, _ in llm.calls] == ["outline", "scenarios", "scenarios"]


def test_unreachable_ollama_is_a_clear_error() -> None:
    llm = Ollama("http://127.0.0.1:9", "qwen2.5-coder:14b", 8192, 100)
    with pytest.raises(ChiefError, match="not reachable"):
        Chief(llm).draft("x", None, None, [], _quiet)


def test_file_planner_reads_a_plan() -> None:
    plan = FilePlanner(HERE / "todo_plan.json").draft("ignored", None, None, [], _quiet)
    assert plan.title == "Todo" and planmod.problems(plan) == []
