"""Architecture gate, task scope and context files: rules, imports, scanning, and the loop."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swarm import archlint, config, dispatch, git, guard, runner
from swarm.archlint import Forbid, Rules, matches, rules_from_dict
from swarm.cli import main
from swarm.config import Config, ConfigError, EngineSpec
from swarm.engines import AgentContext, AgentResult, Engine, make_engine
from swarm.runner import FAILED, PASSED, build_prompt, run_task
from swarm.sandbox import HostBox
from swarm.task import TaskError, find_tasks, load_task
from swarm.util import CmdResult
from tests.conftest import ROOT, quiet

RULES = Rules(
    root="src",
    independent=["app.features.*"],
    forbid=[
        Forbid(["app.core.**"], ["app.features.**"], fix="core must not know features"),
        Forbid(
            ["app.features.**"],
            ["app.**"],
            allow=["app.core.api", "app.features.**"],
            fix="use app.core.api",
        ),
    ],
)
PACKAGED = Rules(root="src", packages=["app"], independent=RULES.independent, forbid=RULES.forbid)

BASE = {
    "src/app/__init__.py": "",
    "src/app/core/__init__.py": "",
    "src/app/core/api.py": "from app.core.internal import thing\n",
    "src/app/core/internal.py": "thing = 1\n",
    "src/app/features/__init__.py": "",
    "src/app/features/one/__init__.py": "",
    "src/app/features/one/helpers.py": "",
    "src/app/features/two/__init__.py": "",
    "src/app/features/two/a.py": "",
    "src/app/features/two/b.py": "",
    "src/app/features/two/c.py": "",
    "src/app/features/two/d.py": "",
}


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _with(tmp_path: Path, extra: dict[str, str]) -> Path:
    return _tree(tmp_path, {**BASE, **extra})


def _found(repo: Path, rules: Rules = RULES) -> set[tuple[str, int, str, str]]:
    def kind(v: archlint.Violation) -> str:
        return "independent" if "independent" in v.rule else "forbid"

    return {(v.path, v.line, v.target, kind(v)) for v in archlint.check(repo, rules).violations}


def _rules(**raw: Any) -> Rules:
    return rules_from_dict(raw, "src", "t")


# --- patterns and rule loading ---------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "pattern", "expected"),
    [
        ("a.b", "a.**", True),
        ("a", "a.**", True),
        ("ab", "a.**", False),
        ("a.b", "a.*", True),
        ("a.b.c", "a.*", False),
        ("x.y.handler", "**.handler", True),
        ("handler", "**.handler", True),
        ("a.bc", "a.b*", True),
        ("a.c", "a.b*", False),
    ],
)
def test_module_patterns(module: str, pattern: str, expected: bool) -> None:
    assert matches(module, pattern) is expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"independent": ["a..b"]}, "bad module pattern"),
        ({"independent": ["a.**"]}, "can't use '\\*\\*'"),
        ({"forbid": [{"from": "a"}]}, "needs 'from' and 'to'"),
        ({"forbid": [{"from": "a", "to": "b", "unless": "c"}]}, "unknown key"),
        ({"forbid": [{"from": "a", "to": "b", "allow": ["c..d"]}]}, "bad module pattern"),
        ({"forbid": [{"from": "a", "to": "b", "fix": 3}]}, "expected a string"),
        ({"forbid": {"from": "a", "to": "b"}}, "expected a list of tables"),
        ({"independent": 5}, "expected a string or a list"),
        ({"packages": ["my-pkg"]}, "not top-level package names"),
        ({"root": "../outside"}, "must be a folder inside the repo"),
        ({"root": "C:/abs"}, "must be a folder inside the repo"),
        ({"root": "/abs"}, "must be a folder inside the repo"),
        ({"layers": []}, "unknown key"),
    ],
)
def test_bad_rules_fail_on_load(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        rules_from_dict(raw, ".", "t")


def _config_with(architecture: Any, pythonpath: list[str] | None = None) -> Config:
    target: dict[str, Any] = {"repo": ".", "verify": [], "architecture": architecture}
    if pythonpath is not None:
        target["pythonpath"] = pythonpath
    data = {"targets": {"t": target}, "engines": {"o": {"kind": "oracle"}}}
    return config.from_dict(data, ROOT)


def test_config_wraps_bad_rules_and_defaults_root_to_pythonpath() -> None:
    with pytest.raises(ConfigError, match="targets.t.architecture: expected a table"):
        _config_with("src")
    with pytest.raises(ConfigError, match=r"forbid\[0\]: unknown key"):
        _config_with({"forbid": [{"from": "a", "to": "b", "x": 1}]})
    rules = _config_with({"independent": ["a.*"]}, pythonpath=["src"]).target("t").architecture
    assert rules is not None and rules.root == "src"
    rules = _config_with({"independent": ["a.*"]}).target("t").architecture
    assert rules is not None and rules.root == "."


# --- imports ---------------------------------------------------------------------------


def test_clean_tree_passes(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "src/app/features/one/handler.py": (
                "from typing import TYPE_CHECKING\n"
                "from app.core.api import thing\n"
                "from . import helpers\n"
                "from .helpers import *\n"
                "import json\n"
                "import app.features.one.helpers\n"
                "import app.core.api\n"
                "x = app.core.api.thing\n"  # attribute chain inside the contract: fine
            ),
            "src/app/features/one/__init__.py": "from . import helpers\n",
        },
    )
    result = archlint.check(repo, PACKAGED)
    assert result.violations == []
    assert result.scanned == 13
    assert "no violations" in archlint.report(result)


def test_cross_feature_and_internal_imports_are_caught(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "src/app/features/one/handler.py": (
                "from app.core import internal\n"  # past the public contract
                "from app.features.two import a\n"  # another feature (absolute)
                "if False:\n"
                "    import app.features.two.b\n"  # nested statements count too
            ),
            "src/app/features/two/handler.py": "from ..one import helpers\n",  # relative
            "src/app/core/wiring.py": "import app.features.one\n",  # core -> feature
            "src/app/core/__init__.py": "from ..features.one import helpers\n",  # package-relative
        },
    )
    assert _found(repo) == {
        ("src/app/features/one/handler.py", 1, "app.core.internal", "forbid"),
        ("src/app/features/one/handler.py", 2, "app.features.two.a", "independent"),
        ("src/app/features/one/handler.py", 4, "app.features.two.b", "independent"),
        ("src/app/features/two/handler.py", 1, "app.features.one.helpers", "independent"),
        ("src/app/core/wiring.py", 1, "app.features.one", "forbid"),
        ("src/app/core/__init__.py", 1, "app.features.one.helpers", "forbid"),
    }


def test_literal_dynamic_imports_are_caught_in_every_spelling(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "src/app/features/one/handler.py": (
                "import importlib as il\n"
                "from importlib import import_module as im\n"
                "il.import_module('app.features.two.a')\n"
                "im(name='app.features.two.b')\n"
                "im('.c', package='app.features.two')\n"
                "__import__('app.features.two', fromlist=['d'])\n"
                "im(computed_name)\n"  # can't be seen statically: no finding, no crash
                "__import__('two.a', globals(), None, [], 2)\n"  # relative, level 2
            ),
        },
    )
    path = "src/app/features/one/handler.py"
    assert _found(repo) == {
        (path, 3, "app.features.two.a", "independent"),
        (path, 4, "app.features.two.b", "independent"),
        (path, 5, "app.features.two.c", "independent"),
        (path, 6, "app.features.two", "independent"),
        (path, 6, "app.features.two.d", "independent"),
        (path, 8, "app.features.two.a", "independent"),
    }


def test_attribute_chains_through_import_bindings_are_caught(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {"src/app/features/one/handler.py": "import app.core.api\nx = app.core.internal.thing\n"},
    )
    assert _found(repo) == {
        ("src/app/features/one/handler.py", 2, "app.core.internal", "forbid"),
    }


# --- packages, scanning and setup ------------------------------------------------------


def test_code_outside_the_packages_cant_launder_imports(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "src/bridge.py": "from app.core.internal import thing\n",
            "helper.py": "",  # repo-root module (importable via cwd)
            "src/app/features/one/handler.py": (
                "from bridge import thing\nimport helper\nimport src.app.core.internal\n"
            ),
        },
    )
    violations = archlint.check(repo, PACKAGED).violations
    assert {(v.path, v.target) for v in violations if "outside" in v.rule} == {
        ("src/app/features/one/handler.py", "bridge"),
        ("src/app/features/one/handler.py", "helper"),
        ("src/app/features/one/handler.py", "src.app.core.internal"),
    }  # src/bridge.py itself is fine: only package code importing it is a problem


def test_src_as_namespace_package_counts_even_without_files_of_its_own(tmp_path: Path) -> None:
    repo = _with(tmp_path, {"src/app/features/one/handler.py": "import src.app.core.internal\n"})
    targets = {v.target for v in archlint.check(repo, PACKAGED).violations}
    assert targets == {"src.app.core.internal"}


def test_extra_pythonpath_entries_count_as_outside(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "lib/bridge.py": "from app.core.internal import thing\n",
            "src/app/features/one/handler.py": "from bridge import thing\n",
        },
    )
    rules = Rules(root="src", packages=["app"], paths=["lib"])
    assert [v.target for v in archlint.check(repo, rules).violations] == ["bridge"]


def test_no_false_alarms_for_installed_packages_or_flat_layouts(tmp_path: Path) -> None:
    # alembic/ without __init__ is a namespace folder; the installed alembic package wins
    repo = _with(
        tmp_path / "src-layout",
        {
            "alembic/env.py": "",
            "src/app/core/db.py": "from alembic import command\n",
        },
    )
    assert archlint.check(repo, PACKAGED).ok
    flat = _tree(
        tmp_path / "flat",
        {
            "app/__init__.py": "",
            "app/core.py": "import json\n",
            "conftest.py": "import app.core\n",
            "tests/test_core.py": "import app.core\nimport conftest\n",
            "docs/conf.py": "",
        },
    )
    assert archlint.check(flat, Rules(root=".", packages=["app"])).ok


def test_shadowing_modules_and_unusual_names_are_scanned(tmp_path: Path) -> None:
    repo = _with(
        tmp_path,
        {
            "src/app/core/internal/__init__.py": "",  # shadows core/internal.py
            "src/app/features/temp-conv/__init__.py": "",
            "src/app/features/temp-conv/handler.pyw": "from app.features.two import a\n",
            "src/app/features/one/upper.PY": "from app.features.two import b\n",
            "src/app/features/one/__pycache__/hidden.py": "from app.features.two import c\n",
        },
    )
    found = _found(repo)
    for path, target in [
        ("src/app/features/one/upper.PY", "app.features.two.b"),  # Windows imports .PY
        ("src/app/features/one/__pycache__/hidden.py", "app.features.two.c"),
    ]:
        assert (path, 1, target, "independent") in found
    violations = archlint.check(repo, RULES).violations
    assert any("all define module app.core.internal" in v.source for v in violations)
    assert ("src/app/features/temp-conv/handler.pyw", 1, "app.features.two.a", "independent") in (
        _found(repo)
    )


def test_unparsable_file_fails_without_echoing_its_source(tmp_path: Path) -> None:
    repo = _with(
        tmp_path, {"src/app/features/one/handler.py": "SECRET = 'hunter2'\ndef broken(:\n"}
    )
    result = archlint.check(repo, RULES)
    assert [(v.path, v.line, v.target) for v in result.violations] == [
        ("src/app/features/one/handler.py", 2, "")
    ]
    assert "hunter2" not in archlint.report(result)


@pytest.mark.parametrize("files", [{}, {"src/readme.txt": "no python here"}])
def test_missing_or_empty_root_fails_instead_of_passing(
    tmp_path: Path, files: dict[str, str]
) -> None:
    result = archlint.check(_tree(tmp_path, files), RULES)
    assert not result.ok
    assert "scanned root `src`" in archlint.report(result)


def test_rules_that_match_nothing_are_noted(tmp_path: Path) -> None:
    rules = Rules(root="src", independent=["app.plugins.*"])
    result = archlint.check(_with(tmp_path, {}), rules)
    assert result.ok
    assert "`app.plugins.*` matched no module" in archlint.report(result)


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":  # a junction needs no special privilege
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_links_are_reported_not_followed(tmp_path: Path) -> None:
    outside = _tree(tmp_path / "outside", {"evil.py": "import app.features.one\n"})
    repo = _tree(tmp_path / "repo", BASE)
    _link_dir(repo / "src/app/core/linked", outside)
    result = archlint.check(repo, RULES)
    assert [(v.path, v.source) for v in result.violations] == [
        ("src/app/core/linked", "link to a folder")
    ]


def test_file_links_are_reported(tmp_path: Path) -> None:
    outside = _tree(tmp_path / "outside", {"evil.py": "import app.features.one\n"})
    repo = _tree(tmp_path / "repo", BASE)
    try:
        os.symlink(outside / "evil.py", repo / "src/app/core/evil.py")
    except OSError:
        pytest.skip("file symlinks need extra privileges on this system")
    sources = [v.source for v in archlint.check(repo, RULES).violations]
    assert sources == ["link to a file"]


def test_root_that_resolves_outside_the_repo_fails(tmp_path: Path) -> None:
    elsewhere = _tree(tmp_path / "elsewhere", {"app/__init__.py": ""})
    repo = tmp_path / "repo"
    repo.mkdir()
    _link_dir(repo / "src", elsewhere)
    result = archlint.check(repo, RULES)
    assert not result.ok
    assert "resolves outside the repo" in archlint.report(result)


def test_relative_and_non_canonical_paths_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree(tmp_path / "real", BASE)
    monkeypatch.chdir(tmp_path)
    assert archlint.check(Path("real"), RULES).ok
    _link_dir(tmp_path / "via-link", tmp_path / "real")
    assert archlint.check(Path("via-link"), RULES).ok


def test_cli_checks_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(ROOT / "demo" / "shopapi", tmp_path / "repo")
    monkeypatch.chdir(tmp_path)
    toml = str(ROOT / "swarm.toml")
    assert main(["-c", toml, "-t", "shopapi", "archlint", "repo"]) == 0
    assert "no violations" in capsys.readouterr().out
    bad = tmp_path / "repo/src/shopapi/features/health/handler.py"
    bad.write_text("from shopapi.core.registry import route\n", encoding="utf-8")
    assert main(["-c", toml, "-t", "shopapi", "archlint", "--json", "repo"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["violations"][0]["target"] == "shopapi.core.registry"


# --- prompt, scope, context files, end to end --------------------------------------------


def test_prompt_lists_rules_scope_and_context(api_cfg: Config) -> None:
    target = api_cfg.target(None)
    task = find_tasks(target.tasks_dir, ["api-001"])[0]
    prompt = build_prompt(task, target, 1, None)
    assert "`shopapi.features.*` are independent" in prompt
    assert "allowed: `shopapi.core.api`" in prompt
    assert "may only change these paths" in prompt and "features/temperature/**" in prompt
    assert "Read-only reference (the API to code against): `src/shopapi/core/api.py`" in prompt


def test_prompt_unchanged_for_targets_without_the_new_settings(cfg: Config) -> None:
    target = cfg.target(None)
    prompt = build_prompt(find_tasks(target.tasks_dir, ["demo-001"])[0], target, 1, None)
    for text in ("import rules", "Read-only reference", "may only change"):
        assert text not in prompt


def test_scope_allows_only_matching_paths() -> None:
    scope = ["src/app/features/one/**"]
    assert guard.in_scope("src/app/features/one/handler.py", scope)
    assert not guard.in_scope("src/app/features/two/handler.py", scope)
    assert not guard.in_scope("src/bridge.py", scope)
    assert guard.in_scope("anything.py", [])
    path = "src/app/features/one/handler.py"
    for spelling in [
        "src\\app\\features\\one\\**",
        "src/app/features/one",
        "src/app/features/one/",
        "./src/app/features/one/**",
    ]:
        assert guard.in_scope(path, [spelling]), spelling
    assert not guard.in_scope("src/app/features/one_more/x.py", ["src/app/features/one"])


def test_scope_must_be_a_list(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text(
        'title = "t"\nprompt = "p"\nscope = "src/**"\n', encoding="utf-8"
    )
    with pytest.raises(TaskError, match="'scope' must be a list of paths"):
        load_task(tmp_path)


def test_run_task_hands_context_files_to_the_agent(
    api_cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    class Recorder(Engine):
        def run(self, prompt: str, ctx: AgentContext) -> AgentResult:
            seen.append(ctx.read_only)
            return AgentResult(CmdResult(["x"], 1, "", 0.0), reported_error="stop here")

    monkeypatch.setattr(runner, "make_engine", lambda spec: Recorder(spec))
    target = api_cfg.target(None)
    task = find_tasks(target.tasks_dir, ["api-001"])[0]
    run_task(api_cfg, target, task, api_cfg.engine("tangled"), max_iterations=1, log=quiet)
    assert seen == [["src/shopapi/core/api.py", "src/shopapi/core/http.py"]]


def test_only_what_the_guard_saw_gets_committed(api_cfg: Config) -> None:
    """Staged-then-restored edits, `git rm --cached` and glob-named files can't sneak in."""
    target = api_cfg.target(None)
    task = find_tasks(target.tasks_dir, ["api-001"])[0]
    s = run_task(api_cfg, target, task, api_cfg.engine("stager"), max_iterations=1, log=quiet)
    assert s.status == PASSED, s.error
    files = git.git(target.repo, "show", "--name-status", "--format=", s.branch).split("\n")
    assert sorted(f for f in files if f) == [
        "A\tsrc/shopapi/features/temperature/__init__.py",
        "A\tsrc/shopapi/features/temperature/handler.py",
    ]


class _Capture(HostBox):
    def __init__(self) -> None:
        self.argv: list[str] = []

    def exe(self, name: str) -> str | None:
        return name  # Aider needn't be installed: nothing actually runs

    def run(self, argv: Any, wt: Path, **kwargs: Any) -> CmdResult:
        self.argv = list(argv)
        return CmdResult(list(argv), 0, "", 0.0)


def test_aider_gets_context_and_out_of_scope_hints_read_only(
    api_cfg: Config, tmp_path: Path
) -> None:
    target = api_cfg.target(None)
    task = find_tasks(target.tasks_dir, ["api-001"])[0]
    box = _Capture()
    ctx = AgentContext(
        target.repo,
        tmp_path,
        tmp_path / "prompt.md",
        None,
        task,
        60,
        hidden=target.protected,
        read_only=target.context_files,
        box=box,
    )
    make_engine(api_cfg.engine("local-14b-arch")).run("prompt", ctx)
    pairs = list(zip(box.argv, box.argv[1:], strict=False))
    assert ("--read", "src/shopapi/core/api.py") in pairs
    assert ("--read", "src/shopapi/core/http.py") in pairs
    assert ("--read", "src/shopapi/features/products/handler.py") in pairs  # hint, out of scope
    assert "--file" not in box.argv


def test_agent_that_ignores_the_architecture_is_told_how_to_fix_it(api_cfg: Config) -> None:
    target = api_cfg.target(None)
    task = find_tasks(target.tasks_dir, ["api-001"])[0]
    s = run_task(api_cfg, target, task, api_cfg.engine("tangled"), max_iterations=1, log=quiet)
    assert s.status == FAILED
    rec = s.records[0]
    assert any(r.startswith("src/shopapi/core/registry.py (protected") for r in rec.reverted)
    assert "src/shopapi/features/products/handler.py (outside this task's scope)" in rec.reverted
    arch = next(st for st in rec.stages if st["name"] == "architecture rules")
    assert arch["ok"] is False
    feedback = (api_cfg.runs_dir / s.run_id / "iter-1" / "feedback.md").read_text("utf-8")
    assert "imports shopapi.features.products.handler" in feedback
    assert "imports shopapi.core.registry" in feedback
    assert "fix: A feature may import only the public contract" in feedback


def test_feature_branches_merge_without_conflicts(api_cfg: Config) -> None:
    """Auto-discovery's payoff: tasks that each add a feature never touch a shared file."""
    target = api_cfg.target(None)
    oracle = EngineSpec("oracle", "oracle")
    for task in find_tasks(target.tasks_dir, ["api-001", "api-003"]):
        s = run_task(api_cfg, target, task, oracle, log=quiet)
        assert s.status == PASSED, s.error
    lines: list[str] = []
    assert dispatch.integrate(api_cfg, target, [], lines.append), "\n".join(lines)
    assert not any(line.startswith("CONFLICT") for line in lines)
    assert any(line.startswith("ok    architecture rules") for line in lines)
