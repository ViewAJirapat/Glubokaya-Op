"""The Claude chief backend, against a fake `claude` CLI on PATH (tests/agents/fake_claude.py)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from swarm import config as config_mod
from swarm.studio.app import make_planner
from swarm.studio.chief import OUTLINE_SCHEMA, Chief, ChiefError, Usage
from swarm.studio.chief_claude import ClaudeChief
from swarm.studio.project import ChiefSpec, StudioSettings
from tests.conftest import AGENTS, ROOT
from tests.test_studio_chief import _outline, _quiet, _scenarios

WINDOWS = sys.platform == "win32"


class FakeCli:
    """Puts a `claude` on PATH that answers from a script and logs its calls."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = AGENTS / "fake_claude.py"
        if WINDOWS:
            (bin_dir / "claude.cmd").write_text(
                f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8"
            )
        else:
            exe = bin_dir / "claude"
            exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
            exe.chmod(0o755)
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
        self.answers = tmp_path / "answers.json"
        self.log = tmp_path / "calls.jsonl"
        self.answers.write_text("[]", encoding="utf-8")

    def script(self, *answers: Any) -> None:
        self.answers.write_text(json.dumps(list(answers)), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def backend(self, **kw: Any) -> ClaudeChief:
        env = {"FAKE_CLAUDE_ANSWERS": str(self.answers), "FAKE_CLAUDE_LOG": str(self.log)}
        return ClaudeChief(env=env, **kw)


def result(structured: Any = None, text: str | None = None, **more: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "result",
        "is_error": False,
        "result": text if text is not None else json.dumps(structured),
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20},
    }
    if structured is not None:
        out["structured_output"] = structured
    out.update(more)
    return out


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCli:
    return FakeCli(tmp_path, monkeypatch)


def test_structured_output_is_the_reply_and_the_call_has_no_tools(cli: FakeCli) -> None:
    cli.script(result({"a": 1}))
    backend = cli.backend(model="claude-sonnet-5-5")
    assert backend.label == "claude-sonnet-5-5 (Claude CLI)"
    seen: list[str] = []
    reply = backend.complete("SYS", "USER", {"type": "object"}, seen.append)
    assert reply == {"a": 1}
    [call] = cli.calls()
    argv = call["argv"]
    assert call["stdin"] == "SYS\n\n---\n\nUSER"  # the system text rides on stdin
    assert "--system-prompt" not in argv and all("\n" not in a for a in argv)
    assert "-p" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--tools") + 1] == "" and "--restricted" in argv
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert seen and "asking Claude" in seen[0]


def test_usage_is_metered_per_call(cli: FakeCli) -> None:
    cli.script(result({"a": 1}), result({"b": 2}, total_cost_usd=0.5))
    backend = cli.backend()
    backend.complete("s", "u", {})
    backend.complete("s", "u", {})
    assert backend.usage == Usage(
        calls=2, cost_usd=pytest.approx(0.5123), input_tokens=300, output_tokens=40
    )
    assert "2 call(s), $0.5123" in backend.usage.text()
    assert Chief(backend).usage is backend.usage


def test_text_reply_is_parsed_and_fences_stripped(cli: FakeCli) -> None:
    cli.script(result(text='```json\n{"x": [1, 2]}\n```'))
    assert cli.backend().complete("s", "u", {}) == {"x": [1, 2]}


def test_a_non_json_reply_is_retried_once_with_the_problem(cli: FakeCli) -> None:
    cli.script(result(text="Sure! Here is the plan:"), result({"ok": True}))
    assert cli.backend().complete("s", "u", {}) == {"ok": True}
    first, second = cli.calls()
    assert first["stdin"].endswith("---\n\nu")
    assert second["stdin"].endswith(
        "u\n\nYour previous reply was not valid (not JSON: Expecting value: line 1 column 1 "
        "(char 0)). Reply with one JSON object only, matching the schema, and nothing else."
    )
    cli.script(result(text="nope"), result(text="still nope"))
    with pytest.raises(ChiefError, match="not JSON after a retry"):
        cli.backend().complete("s", "u", {})


def test_cli_errors_are_chief_errors(cli: FakeCli) -> None:
    cli.script(result(text="Not logged in", is_error=True))
    with pytest.raises(ChiefError, match="Claude: Not logged in"):
        cli.backend().complete("s", "u", {})
    cli.script("garbage without any json")
    with pytest.raises(ChiefError, match="no JSON result"):
        cli.backend().complete("s", "u", {})


def test_missing_cli_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ChiefError, match="claude.*not found"):
        ClaudeChief().preflight()


def test_the_chief_plans_a_todo_app_through_claude(cli: FakeCli) -> None:
    cli.script(
        result(_outline()),
        result(_scenarios("todos")),
        result(_scenarios("todo_edit")),
        result(_scenarios("todo_stats")),
    )
    chief = Chief(cli.backend())
    plan = chief.draft("a todo app", None, None, [], _quiet)
    assert [f.name for f in plan.features] == ["todos", "todo_edit", "todo_stats"]
    schemas = [json.loads(c["argv"][c["argv"].index("--json-schema") + 1]) for c in cli.calls()]
    assert schemas[0] == OUTLINE_SCHEMA and len(schemas) == 4
    assert chief.usage is not None and chief.usage.calls == 4


# -- configuration -------------------------------------------------------------------------------


def test_chiefs_from_the_real_config_and_make_planner(cli: FakeCli) -> None:
    chiefs = ChiefSpec.load_all(ROOT / "swarm.toml")
    assert chiefs["local"].kind == "local" and chiefs["claude"].kind == "claude"
    settings = StudioSettings.load(ROOT / "swarm.toml")
    assert settings.chief == "local"
    local = make_planner(settings, chiefs)
    assert local.label == "qwen2.5-coder:14b (Ollama)"
    claude = make_planner(settings, chiefs, "claude")
    assert claude.label == "default model (Claude CLI)"
    with pytest.raises(config_mod.ConfigError, match="unknown chief 'gpt'"):
        make_planner(settings, chiefs, "gpt")


def test_chief_config_errors(tmp_path: Path) -> None:
    path = tmp_path / "swarm.toml"
    path.write_text('[chiefs.x]\nkind = "openai"\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="kind must be one of"):
        ChiefSpec.load_all(path)
    path.write_text('[chiefs.x]\nkind = "claude"\nmodle = "typo"\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="unknown keys"):
        ChiefSpec.load_all(path)
    path.write_text("[studio]\n", encoding="utf-8")
    assert list(ChiefSpec.load_all(path)) == ["local"]


def test_claude_chief_without_the_cli_fails_at_make_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    chiefs = {"claude": ChiefSpec("claude", kind="claude")}
    with pytest.raises(config_mod.ConfigError, match="chief 'claude'.*not found"):
        make_planner(StudioSettings(), chiefs, "claude")


# -- the cost line in a project --------------------------------------------------------------------


def test_a_project_records_what_the_claude_chief_spent(cli: FakeCli, tmp_path: Path) -> None:
    from swarm.studio.project import Project
    from tests.studio_helpers import harness_config

    cli.script(
        result(_outline()),
        result(_scenarios("todos")),
        result(_scenarios("todo_edit")),
        result(_scenarios("todo_stats"), total_cost_usd=0.1),
    )
    project = Project(
        tmp_path / "proj",
        Chief(cli.backend(model="m")),
        harness_config=harness_config(tmp_path),
        settings=StudioSettings(engine="solver"),
    )
    project.start(with_preview=False)
    try:
        project.chat("Build me a todo list")
        assert project.wait_idle(60)
        assert project.state.plan_status == "proposed", project.state.messages
    finally:
        project.close()
    usage = project.state.chief_usage
    assert usage["label"] == "m (Claude CLI)" and usage["calls"] == 4
    assert usage["cost_usd"] == pytest.approx(0.1369) and usage["input_tokens"] == 600
    cost_lines = [m["text"] for m in project.state.messages if "Planner cost" in m["text"]]
    spent = "4 call(s), $0.1369, 600 in / 80 out tokens"
    assert cost_lines == [f"Planner cost: {spent}; this project so far: {spent}."]
    assert project.snapshot()["chief_usage"]["calls"] == 4
