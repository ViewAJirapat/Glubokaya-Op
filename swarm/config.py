"""Load swarm.toml into typed settings. Relative paths resolve against the config file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCEPTANCE_FEEDBACK_MODES = ("full", "summary", "none")
ENGINE_KINDS = ("aider", "claude", "command", "oracle")
SANDBOX_KINDS = ("none", "docker")


@dataclass
class Settings:
    max_iterations: int = 4
    agent_timeout: int = 900  # seconds per agent invocation
    verify_timeout: int = 300  # seconds per verification command
    feedback_chars: int = 6000  # total budget for failure output fed back to the agent
    acceptance_feedback: str = "full"  # how much hidden-test output the agent sees
    baseline_check: bool = True  # base must pass the gates and fail the hidden tests


@dataclass
class Target:
    name: str
    repo: Path
    base: str
    tasks_dir: Path
    verify: list[list[str]]
    protected: list[str]
    forbidden_added: list[str]  # regexes matched against added lines
    rules_file: str | None = None
    pythonpath: list[str] = field(default_factory=list)
    import_check: str | None = None
    template: Path | None = None  # source tree for `swarm demo-init`
    autofix: list[list[str]] = field(default_factory=list)  # run after each agent edit
    sandbox: str = "none"  # "docker": agent and gates run in a container (swarm/sandbox.py)


@dataclass
class SandboxSettings:
    """[sandbox] in swarm.toml; used by targets with sandbox = "docker"."""

    image: str = "swarm-sandbox:latest"
    memory: str = "4g"
    cpus: float | None = None
    pids_limit: int = 512
    network: str = "bridge"  # agent containers: reach Ollama (host.docker.internal) or an API
    verify_network: str = "none"  # gates execute agent-written code: no network
    readonly_checks: bool = True  # gates can't change the code that then gets committed


@dataclass
class EngineSpec:
    name: str
    kind: str
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    pass_env: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    model_settings: Path | None = None
    cmd: list[str] = field(default_factory=list)  # kind = "command"
    stdin: str | None = None  # kind = "command": "prompt" pipes the prompt on stdin

    @property
    def label(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name


@dataclass
class Config:
    root: Path
    settings: Settings
    default_target: str
    default_engine: str
    targets: dict[str, Target]
    engines: dict[str, EngineSpec]
    worktrees_dir: Path
    runs_dir: Path
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)

    def target(self, name: str | None) -> Target:
        key = name or self.default_target
        if key not in self.targets:
            raise ConfigError(f"unknown target {key!r}; known: {', '.join(self.targets)}")
        return self.targets[key]

    def engine(self, name: str | None) -> EngineSpec:
        key = name or self.default_engine
        if key not in self.engines:
            raise ConfigError(f"unknown engine {key!r}; known: {', '.join(self.engines)}")
        return self.engines[key]


class ConfigError(Exception):
    pass


def _path(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p).resolve()


def _target(name: str, raw: dict[str, Any], root: Path) -> Target:
    for key in ("repo", "verify"):
        if key not in raw:
            raise ConfigError(f"targets.{name}: missing {key!r}")
    sandbox = raw.get("sandbox", "none")
    if sandbox not in SANDBOX_KINDS:
        raise ConfigError(f"targets.{name}: sandbox must be one of {SANDBOX_KINDS}: {sandbox!r}")
    return Target(
        name=name,
        repo=_path(root, raw["repo"]),
        base=raw.get("base", "main"),
        tasks_dir=_path(root, raw.get("tasks", f"tasks/{name}")),
        verify=[list(map(str, cmd)) for cmd in raw["verify"]],
        protected=list(raw.get("protected", [])),
        forbidden_added=list(raw.get("forbidden_added", [])),
        rules_file=raw.get("rules_file"),
        pythonpath=list(raw.get("pythonpath", [])),
        import_check=raw.get("import_check"),
        template=_path(root, raw["template"]) if raw.get("template") else None,
        autofix=[list(map(str, cmd)) for cmd in raw.get("autofix", [])],
        sandbox=sandbox,
    )


def _engine(name: str, raw: dict[str, Any], root: Path) -> EngineSpec:
    kind = raw.get("kind", "")
    if kind not in ENGINE_KINDS:
        raise ConfigError(f"engines.{name}: kind must be one of {ENGINE_KINDS}, got {kind!r}")
    if kind == "command" and not raw.get("cmd"):
        raise ConfigError(f"engines.{name}: kind 'command' needs a 'cmd' list")
    settings = raw.get("model_settings")
    return EngineSpec(
        name=name,
        kind=kind,
        model=raw.get("model"),
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        pass_env=list(raw.get("pass_env", [])),
        extra_args=list(map(str, raw.get("extra_args", []))),
        model_settings=_path(root, settings) if settings else None,
        cmd=list(map(str, raw.get("cmd", []))),
        stdin=raw.get("stdin"),
    )


def from_dict(data: dict[str, Any], root: Path) -> Config:
    defaults = dict(data.get("defaults", {}))
    default_target = defaults.pop("target", None)
    default_engine = defaults.pop("engine", None)
    worktrees = defaults.pop("worktrees_dir", ".worktrees")
    runs = defaults.pop("runs_dir", "runs")
    try:
        settings = Settings(**defaults)
    except TypeError as exc:
        raise ConfigError(f"[defaults]: {exc}") from None
    if settings.acceptance_feedback not in ACCEPTANCE_FEEDBACK_MODES:
        raise ConfigError(f"acceptance_feedback must be one of {ACCEPTANCE_FEEDBACK_MODES}")
    try:
        sandbox = SandboxSettings(**data.get("sandbox", {}))
    except TypeError as exc:
        raise ConfigError(f"[sandbox]: {exc}") from None

    targets = {n: _target(n, t, root) for n, t in data.get("targets", {}).items()}
    engines = {n: _engine(n, e, root) for n, e in data.get("engines", {}).items()}
    if not targets:
        raise ConfigError("no [targets.*] defined")
    if not engines:
        raise ConfigError("no [engines.*] defined")
    return Config(
        root=root,
        settings=settings,
        default_target=default_target or next(iter(targets)),
        default_engine=default_engine or next(iter(engines)),
        targets=targets,
        engines=engines,
        worktrees_dir=_path(root, worktrees),
        runs_dir=_path(root, runs),
        sandbox=sandbox,
    )


def load(path: Path) -> Config:
    path = path.resolve()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    with path.open("rb") as fh:
        return from_dict(tomllib.load(fh), path.parent)
