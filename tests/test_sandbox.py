"""Docker sandbox: command lines, the per-call git dir, and how engines and gates map their
paths into the container. None of this needs Docker, except the end-to-end tests at the
bottom (set SWARM_DOCKER_TESTS=1 and build the image first)."""

from __future__ import annotations

import csv
import importlib.metadata
import os
import re
import subprocess
from pathlib import Path

import pytest

from swarm import config, git, verify
from swarm.config import Config, EngineSpec, SandboxSettings
from swarm.engines import AgentContext, AiderEngine, CommandEngine
from swarm.runner import PASSED, run_task
from swarm.sandbox import (
    AGENT,
    CHECK,
    DOCKER_DIR,
    GATES_PYTHON,
    DockerBox,
    Mount,
    build_command,
    container_git_config,
    container_url,
    mount_arg,
)
from swarm.task import find_tasks
from swarm.util import CmdResult
from tests.conftest import ROOT, quiet

IMAGE = SandboxSettings().image
FLAGS = {"--rm", "--read-only", "--interactive"}  # docker run options without a value
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class RecordingBox(DockerBox):
    """Builds the real docker command lines (and git dirs) but runs nothing."""

    def __init__(self, scratch: Path, outputs: list[str] | None = None) -> None:
        super().__init__(SandboxSettings(), scratch, label="demo")
        self.calls: list[list[str]] = []
        self.outputs = outputs or []

    def _exec(
        self, cmd: list[str], name: str, wt: Path, timeout: float, stdin_text: str | None
    ) -> CmdResult:
        self.calls.append(cmd)
        return CmdResult(cmd, 0, self.outputs.pop(0) if self.outputs else "", 0.0)


def parse(cmd: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split a docker run command into its options and the argv run in the container."""
    assert cmd[:2] == ["docker", "run"]
    image_at = cmd.index(IMAGE)
    opts: dict[str, list[str]] = {}
    i = 2
    while i < image_at:
        if cmd[i] in FLAGS:
            opts.setdefault(cmd[i], []).append("")
            i += 1
        else:
            opts.setdefault(cmd[i], []).append(cmd[i + 1])
            i += 2
    return opts, cmd[image_at + 1 :]


def mounts(opts: dict[str, list[str]]) -> dict[str, tuple[str, bool]]:
    """container path -> (host path, read-only)"""
    out = {}
    for arg in opts["--mount"]:
        fields = next(csv.reader([arg]))
        kv = dict(f.split("=", 1) for f in fields if "=" in f)
        assert kv["type"] == "bind"
        out[kv["target"]] = (kv["source"], "readonly" in fields)
    return out


def envs(opts: dict[str, list[str]]) -> dict[str, str | None]:
    """--env KEY=VALUE -> value; --env KEY (inherited from the harness) -> None"""
    out: dict[str, str | None] = {}
    for item in opts.get("--env", []):
        key, sep, value = item.partition("=")
        out[key] = value if sep else None
    return out


def worktree(cfg: Config, tmp_path: Path, name: str = "wt") -> Path:
    target = cfg.target(None)
    wt = tmp_path / name
    git.add_worktree(target.repo, wt, f"agent/{name}", git.rev_parse(target.repo, target.base))
    return wt


# ---------------------------------------------------------------------------
# docker run command line
# ---------------------------------------------------------------------------
def test_check_command_is_locked_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    box = DockerBox(SandboxSettings(), tmp_path)
    cmd = box.command(
        ["python", "-m", "pytest"],
        name="swarm-test",
        role=CHECK,
        mounts=[Mount(tmp_path, "/work")],
        env=box.container_env({"PYTHONPATH": "/work/src"}),
    )
    opts, argv = parse(cmd)
    assert argv == ["python", "-m", "pytest"]
    assert "--rm" in opts and "--read-only" in opts and "--interactive" not in opts
    assert opts["--network"] == ["none"] and "--add-host" not in opts
    assert opts["--cap-drop"] == ["ALL"]
    assert opts["--security-opt"] == ["no-new-privileges"]
    assert opts["--user"] == ["1000:1000"]
    assert opts["--memory"] and opts["--pids-limit"]
    assert mounts(opts) == {"/work": (str(tmp_path), True)}

    env = envs(opts)
    assert env["PYTHONPATH"] == "/work/src" and env["HOME"] == "/tmp"
    assert env["GIT_CONFIG_VALUE_1"] == "never"  # no git remotes in the container either
    assert not {"PATH", "USERPROFILE", "APPDATA", "GITHUB_TOKEN"} & set(env)
    assert not any("ghp-secret" in arg for arg in cmd)


def test_agent_command_gets_network_and_host_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    box = DockerBox(SandboxSettings(), tmp_path)
    cmd = box.command(
        ["aider"],
        name="swarm-test",
        role=AGENT,
        mounts=[Mount(tmp_path, "/work", writable=True)],
        env=box.container_env({"OLLAMA_API_BASE": "http://127.0.0.1:11434"}),
        pass_env=["ANTHROPIC_API_KEY"],
        interactive=True,
    )
    opts, _ = parse(cmd)
    assert opts["--network"] == ["bridge"]
    assert opts["--add-host"] == ["host.docker.internal:host-gateway"]
    assert "--interactive" in opts
    assert mounts(opts) == {"/work": (str(tmp_path), False)}
    env = envs(opts)
    assert env["OLLAMA_API_BASE"] == "http://host.docker.internal:11434"
    assert env["ANTHROPIC_API_KEY"] is None  # passed by name; the value never hits argv
    assert not any("sk-secret" in arg for arg in cmd)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:11434", "http://host.docker.internal:11434"),
        ("http://localhost:11434/v1", "http://host.docker.internal:11434/v1"),
        ("http://localhost", "http://host.docker.internal"),
        ("http://[::1]:8080", "http://host.docker.internal:8080"),
        ("http://localhost.example.com", "http://localhost.example.com"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("127.0.0.1", "127.0.0.1"),  # not a URL
    ],
)
def test_container_url(value: str, expected: str) -> None:
    assert container_url(value) == expected


def test_mount_arg_quotes_commas() -> None:
    src = Path("E:/odd,name")
    assert mount_arg(Mount(src, "/work")) == f'type=bind,"source={src}",target=/work,readonly'
    assert mount_arg(Mount(Path("E:/x"), "/o", writable=True)).endswith("target=/o")


def test_container_git_config_mirrors_host_settings() -> None:
    text = container_git_config("core.autocrlf true\ncore.symlinks false\ncore.autocrlf input\n")
    assert "\tautocrlf = input" in text  # the last value wins, as in git
    assert "\tsymlinks = false" in text and "\tworktree = /work" in text
    assert "\trepositoryformatversion = 0" in text and "[extensions]" not in text

    sha256 = container_git_config("extensions.objectformat sha256\n")
    assert "\trepositoryformatversion = 1" in sha256
    assert "[extensions]\n\tobjectformat = sha256" in sha256


def test_build_command_pins_gate_tools_to_the_venv() -> None:
    cmd = build_command(SandboxSettings())
    assert cmd[:3] == ["docker", "build", "--load"] and IMAGE in cmd
    for dist, arg in (("pytest", "PYTEST_VERSION"), ("ruff", "RUFF_VERSION")):
        assert f"{arg}={importlib.metadata.version(dist)}" in cmd
    assert cmd[-1] == str(DOCKER_DIR) and (DOCKER_DIR / "Dockerfile").is_file()


def test_sandbox_config() -> None:
    raw = {
        "targets": {"t": {"repo": "r", "verify": [["true"]], "sandbox": "docker"}},
        "engines": {"e": {"kind": "oracle"}},
        "sandbox": {"memory": "2g", "cpus": 2},
    }
    cfg = config.from_dict(raw, ROOT)
    assert cfg.target("t").sandbox == "docker"
    assert cfg.sandbox.memory == "2g" and cfg.sandbox.cpus == 2
    assert cfg.sandbox.verify_network == "none" and cfg.sandbox.readonly_checks

    raw["targets"]["t"]["sandbox"] = "podman"
    with pytest.raises(config.ConfigError, match="sandbox"):
        config.from_dict(raw, ROOT)
    raw["targets"]["t"]["sandbox"] = "none"
    raw["sandbox"] = {"memroy": "2g"}
    with pytest.raises(config.ConfigError, match="memroy"):
        config.from_dict(raw, ROOT)


# ---------------------------------------------------------------------------
# per-call git dir
# ---------------------------------------------------------------------------
def test_git_mounts_share_only_the_object_store(cfg: Config, tmp_path: Path) -> None:
    target = cfg.target(None)
    git.ensure_excludes(target.repo, [".aider*"])
    wt = worktree(cfg, tmp_path)
    (wt / "new.py").write_text("x = 1\n", encoding="utf-8")
    git.git(wt, "add", "--intent-to-add", "new.py")
    host_gitfile = (wt / ".git").read_bytes()
    call = tmp_path / "call"
    call.mkdir()

    by_target = {m.target: m for m in DockerBox(SandboxSettings(), tmp_path).git_mounts(wt, call)}
    assert set(by_target) == {"/work/.git", "/swarm/git", "/swarm/objects"}
    gitfile, gd = by_target["/work/.git"], by_target["/swarm/git"]
    objects = by_target["/swarm/objects"]
    assert not gitfile.writable and not objects.writable and gd.writable
    assert gitfile.host.read_bytes() == b"gitdir: /swarm/git\n"
    assert objects.host.resolve() == (target.repo / ".git" / "objects").resolve()

    sha = git.rev_parse(wt, "HEAD")
    assert (gd.host / "HEAD").read_bytes() == b"ref: refs/heads/agent/wt\n"
    assert (gd.host / "refs" / "heads" / "agent" / "wt").read_bytes() == f"{sha}\n".encode()
    assert (gd.host / "objects" / "info" / "alternates").read_bytes() == b"/swarm/objects\n"
    index = Path(git.git(wt, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    assert (gd.host / "index").read_bytes() == index.read_bytes()
    assert ".aider*" in (gd.host / "info" / "exclude").read_text(encoding="utf-8")
    assert (wt / ".git").read_bytes() == host_gitfile  # the host's gitfile is untouched

    # The private dir is a coherent repo: with the alternates pointed at the host's object
    # store, git sees the same state as the worktree itself.
    (gd.host / "objects" / "info" / "alternates").write_text(str(objects.host), encoding="utf-8")
    private = git.git(wt, f"--git-dir={gd.host}", f"--work-tree={wt}", "status", "--porcelain")
    assert private == git.git(wt, "status", "--porcelain") == " A new.py\n"


# ---------------------------------------------------------------------------
# engines and gates in the box
# ---------------------------------------------------------------------------
def _ctx(cfg: Config, tmp_path: Path, box: DockerBox) -> AgentContext:
    target = cfg.target(None)
    wt = worktree(cfg, tmp_path)
    idir = tmp_path / "run" / "iter-1"
    idir.mkdir(parents=True)
    prompt = idir / "prompt.md"
    prompt.write_text("do it\n", encoding="utf-8")
    rules = tmp_path / "run" / "rules.md"
    rules.write_text("rules\n", encoding="utf-8")
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    return AgentContext(wt, idir, prompt, rules, task, 60, hidden=target.protected, box=box)


def test_aider_runs_in_the_box(cfg: Config, tmp_path: Path) -> None:
    box = RecordingBox(tmp_path / "scratch")
    ctx = _ctx(cfg, tmp_path, box)
    spec = cfg.engine("local-7b")
    AiderEngine(spec).run("do it", ctx)

    (cmd,) = box.calls
    opts, argv = parse(cmd)
    assert argv[0] == "aider"
    flag = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1) if argv[i].startswith("--")}
    assert flag["--message-file"] == "/swarm/in/prompt.md"
    assert flag["--read"] == "/swarm/in/rules.md"
    assert flag["--aiderignore"] == "/swarm/in/aiderignore"
    assert flag["--model-settings-file"] == "/swarm/in/aider.model.settings.yml"
    assert flag["--chat-history-file"] == "/swarm/out/aider.chat.md"
    assert not any(WINDOWS_PATH.match(a) for a in argv)  # no host paths inside the container

    m = mounts(opts)
    assert m["/work"] == (str(ctx.worktree), False)
    assert m["/swarm/in/prompt.md"] == (str(ctx.prompt_file), True)
    assert m["/swarm/out/aider.chat.md"] == (str(ctx.iter_dir / "aider.chat.md"), False)
    assert opts["--network"] == ["bridge"]
    assert envs(opts)["OLLAMA_API_BASE"] == "http://host.docker.internal:11434"
    assert not list(box.scratch.iterdir())  # per-call git dir removed


def test_command_engine_placeholders_in_the_box(cfg: Config, tmp_path: Path) -> None:
    box = RecordingBox(tmp_path / "scratch")
    ctx = _ctx(cfg, tmp_path, box)
    spec = EngineSpec(
        "mine",
        "command",
        cmd=["my-agent", "{prompt_file}", "{rules_file}", "{worktree}", "{python}"],
    )
    CommandEngine(spec).run("do it", ctx)
    _, argv = parse(box.calls[0])
    assert argv == ["my-agent", "/swarm/in/prompt.md", "/swarm/in/rules.md", "/work", GATES_PYTHON]


def test_gates_and_autofix_in_the_box(cfg: Config, tmp_path: Path) -> None:
    target = cfg.target(None)
    wt = worktree(cfg, tmp_path)
    box = RecordingBox(tmp_path / "scratch")

    verify.run_gates(wt, target, 60, box)
    assert len(box.calls) == len(target.verify)
    for cmd in box.calls:
        opts, argv = parse(cmd)
        assert argv[0] == GATES_PYTHON
        assert opts["--network"] == ["none"]
        assert mounts(opts)["/work"] == (str(wt), True)  # gates can't change what gets committed
        assert envs(opts)["PYTHONPATH"] == "/work/src"

    box.calls.clear()
    verify.run_autofix(wt, target, 60, box)
    for cmd in box.calls:
        opts, _ = parse(cmd)
        assert opts["--network"] == ["none"]
        assert mounts(opts)["/work"] == (str(wt), False)  # formatters must write


def test_acceptance_mounts_hidden_tests_read_only(cfg: Config, tmp_path: Path) -> None:
    target = cfg.target(None)
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    wt = worktree(cfg, tmp_path)
    box = RecordingBox(tmp_path / "scratch")
    work = tmp_path / "iter-1"
    work.mkdir()

    stage = verify.run_acceptance(wt, task, target, work, 60, box=box)
    assert stage is not None and not stage.ok  # the fake run wrote no test report

    opts, argv = parse(box.calls[0])
    flag = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
    assert argv[0] == GATES_PYTHON and argv[-1] == "/swarm/in/acceptance"
    assert flag["--rootdir"] == "/work" and flag["-c"] == "/work/pyproject.toml"
    assert flag["--confcutdir"] == "/swarm/in/acceptance"
    assert flag["--junitxml"] == "/swarm/out/acceptance.xml"
    m = mounts(opts)
    assert m["/swarm/in/acceptance"] == (str(work / "acceptance"), True)
    assert m["/swarm/out/acceptance.xml"] == (str(work / "acceptance.xml"), False)
    assert opts["--network"] == ["none"]


def test_import_check_in_the_box(cfg: Config, tmp_path: Path) -> None:
    target = cfg.target(None)
    wt = worktree(cfg, tmp_path)
    inside = RecordingBox(tmp_path / "s1", outputs=["/work/src/shopkit/__init__.py\n"])
    assert verify.import_check(wt, target, inside) is None
    _, argv = parse(inside.calls[0])
    assert argv[0] == GATES_PYTHON

    site = "/opt/gates/lib/python3.12/site-packages/shopkit/__init__.py\n"
    outside = RecordingBox(tmp_path / "s2", outputs=[site])
    problem = verify.import_check(wt, target, outside)
    assert problem is not None and "outside the worktree" in problem


# ---------------------------------------------------------------------------
# end to end, real containers (opt-in)
# ---------------------------------------------------------------------------
needs_docker = pytest.mark.skipif(
    os.environ.get("SWARM_DOCKER_TESTS") != "1",
    reason="set SWARM_DOCKER_TESTS=1 (needs Docker and `swarm docker-build`)",
)

# Runs inside the container as the agent: applies the reference fix, then tries to move
# refs and repoint the worktree's gitfile. None of that may reach the host repo.
ATTACKER = """
import subprocess, sys
subprocess.run(["git", "apply", "-"], input=sys.argv[1], text=True, check=True)
subprocess.run(["git", "commit", "-qam", "agent commit"], check=True)
subprocess.run(["git", "update-ref", "refs/heads/main", "HEAD"], check=True)
subprocess.run(["git", "branch", "evil"], check=True)
try:
    open(".git", "w").write("gitdir: /tmp/elsewhere\\n")
    sys.exit("gitfile was writable")
except OSError:
    pass
"""


@needs_docker
def test_docker_oracle_run_passes(cfg: Config) -> None:
    target = cfg.target(None)
    target.sandbox = "docker"
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    s = run_task(cfg, target, task, EngineSpec("oracle", "oracle"), log=quiet)
    assert s.status == PASSED, s.error


@needs_docker
def test_docker_agent_cannot_move_host_refs(cfg: Config) -> None:
    target = cfg.target(None)
    target.sandbox = "docker"
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    assert task.solution_patch is not None
    patch = task.solution_patch.read_text(encoding="utf-8")
    spec = EngineSpec("attacker", "command", cmd=["{python}", "-c", ATTACKER, patch])
    main_before = git.rev_parse(target.repo, "main")
    refs_before = git.git(target.repo, "for-each-ref", "--format=%(refname)")

    s = run_task(cfg, target, task, spec, max_iterations=1, log=quiet)
    assert s.status == PASSED, s.error
    assert git.rev_parse(target.repo, "main") == main_before
    refs_after = git.git(target.repo, "for-each-ref", "--format=%(refname)")
    assert set(refs_after.split()) - set(refs_before.split()) == {f"refs/heads/{s.branch}"}
    log = git.git(target.repo, "log", "--format=%an|%s", f"main..{s.branch}").splitlines()
    assert len(log) == 1 and log[0].startswith("swarm-agent|fix(demo-001")  # the harness commit
    # the run's private git dirs are gone
    leftovers = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", "label=swarm.target=demo"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert not leftovers
    assert not list((cfg.runs_dir / ".sandbox").iterdir())
