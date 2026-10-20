"""Where agent and gate commands run: on the host, or in a throwaway Docker container.

With sandbox = "docker" every command gets a fresh container (--rm) that sees only:

  /work            the task's worktree (read-only while the checks run)
  /work/.git       a read-only gitfile pointing at /swarm/git. It shadows the host's
                   gitfile, which the agent could otherwise repoint at another repository
                   before the harness commits there.
  /swarm/git       a git dir made for this one call: HEAD, a copy of the index, config.
                   Objects come from /swarm/objects through alternates, so commits or ref
                   edits made inside the container land here and are thrown away.
  /swarm/objects   the target repo's object store, read-only (no refs, hooks or config)
  /swarm/in        the call's inputs (prompt, rules, hidden tests), read-only
  /swarm/out       the call's log files, read-write

No host environment or credentials, a non-root user without capabilities, memory and
process limits. Checks run with no network; the agent gets one so it can reach Ollama
(host.docker.internal) or its API. The guard, commits and pushes stay on the host.
"""

from __future__ import annotations

import importlib.metadata
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from swarm import git
from swarm.config import Config, SandboxSettings, Target
from swarm.util import LOCKDOWN_ENV, CmdResult, agent_env, force_rmtree, run_cmd

# What a command is for. Decides network access and whether it may write the worktree.
AGENT, FIX, CHECK = "agent", "fix", "check"

WORK = "/work"
GIT_DIR = "/swarm/git"
OBJECTS = "/swarm/objects"
IN = "/swarm/in"
OUT = "/swarm/out"
GATES_PYTHON = "/opt/gates/bin/python"  # the image's gate toolchain (see docker/Dockerfile)
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
GATE_TOOLS = {"pytest": "PYTEST_VERSION", "ruff": "RUFF_VERSION", "mypy": "MYPY_VERSION"}

# Host git settings that change how git reads the checkout. Without them git in the
# container would report every CRLF file as modified.
_MIRRORED = r"^(core\.(autocrlf|eol|safecrlf|symlinks|ignorecase)|extensions\.objectformat)$"
_LOOPBACK = re.compile(r"(?<=://)(?:127\.0\.0\.1|localhost|\[::1\])(?=[:/]|$)")


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class Mount:
    host: Path
    target: str  # absolute path inside the container
    writable: bool = False


class Box(ABC):
    """Runs commands against a worktree. Commands must spell paths the way the box sees
    them: use workdir/in_worktree for the worktree and Mounts for anything outside it."""

    kind: str
    python: str  # what {python} expands to
    pathsep: str

    def preflight(self) -> None:
        return None

    @abstractmethod
    def exe(self, name: str) -> str | None: ...

    @abstractmethod
    def workdir(self, wt: Path) -> str: ...

    @abstractmethod
    def in_worktree(self, wt: Path, rel: str) -> str: ...

    @abstractmethod
    def path(self, mount: Mount) -> str: ...

    @abstractmethod
    def contains(self, wt: Path, path: str) -> bool:
        """Is `path`, as a command in this box reported it, inside the worktree?"""

    @abstractmethod
    def run(
        self,
        argv: Sequence[str],
        wt: Path,
        *,
        role: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
        pass_env: Sequence[str] = (),
        mounts: Sequence[Mount] = (),
        stdin_text: str | None = None,
    ) -> CmdResult: ...


class Mounts:
    """Collects the host files one command needs; returns the paths to put in its argv."""

    def __init__(self, box: Box) -> None:
        self.box = box
        self.items: list[Mount] = []

    def input(self, host: Path, name: str | None = None) -> str:
        return self._add(Mount(host, f"{IN}/{name or host.name}"))

    def output(self, host: Path) -> str:
        host.touch()  # a bind mount needs an existing file
        return self._add(Mount(host, f"{OUT}/{host.name}", writable=True))

    def _add(self, mount: Mount) -> str:
        self.items.append(mount)
        return self.box.path(mount)


class HostBox(Box):
    """v0 behaviour: commands run as the current user with a scrubbed environment."""

    kind = "none"
    python = sys.executable
    pathsep = os.pathsep

    def exe(self, name: str) -> str | None:
        return shutil.which(name)

    def workdir(self, wt: Path) -> str:
        return str(wt)

    def in_worktree(self, wt: Path, rel: str) -> str:
        return str(wt / rel)

    def path(self, mount: Mount) -> str:
        return str(mount.host)

    def contains(self, wt: Path, path: str) -> bool:
        return Path(path).resolve().is_relative_to(wt.resolve())

    def run(
        self,
        argv: Sequence[str],
        wt: Path,
        *,
        role: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
        pass_env: Sequence[str] = (),
        mounts: Sequence[Mount] = (),
        stdin_text: str | None = None,
    ) -> CmdResult:
        env = agent_env(pass_env, env or {})
        return run_cmd(argv, wt, env=env, timeout=timeout, stdin_text=stdin_text)


HOST = HostBox()


def mount_arg(m: Mount) -> str:
    fields = ["type=bind", f"source={m.host}", f"target={m.target}"]
    if not m.writable:
        fields.append("readonly")
    # docker parses --mount as one CSV record
    return ",".join(f'"{f}"' if "," in f else f for f in fields)


def container_url(value: str) -> str:
    """http://127.0.0.1:11434 -> http://host.docker.internal:11434 (the host's loopback)."""
    return _LOOPBACK.sub("host.docker.internal", value)


def container_git_config(host_settings: str) -> str:
    """Config for the per-call git dir, from `git config --get-regexp` output on the host."""
    core = {"repositoryformatversion": "0", "bare": "false", "worktree": WORK, "filemode": "false"}
    extensions: dict[str, str] = {}
    for line in host_settings.splitlines():  # later lines win, as in git
        key, _, value = line.partition(" ")
        section, _, name = key.partition(".")
        (core if section == "core" else extensions)[name] = value
    if extensions:
        core["repositoryformatversion"] = "1"
    lines = ["[core]", *(f"\t{k} = {v}" for k, v in core.items())]
    if extensions:
        lines += ["[extensions]", *(f"\t{k} = {v}" for k, v in extensions.items())]
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    path.write_text(text + "\n", encoding="utf-8", newline="\n")  # git wants LF


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise SandboxError("docker not found on PATH. Install Docker Desktop (Linux containers).")
    return exe


class DockerBox(Box):
    kind = "docker"
    python = GATES_PYTHON
    pathsep = ":"

    def __init__(self, settings: SandboxSettings, scratch: Path, label: str = "") -> None:
        self.settings = settings
        self.scratch = scratch  # host dir for the per-call git dirs
        self.label = label  # target name, so `swarm clean` can find leftover containers

    def preflight(self) -> None:
        docker = _docker()
        image = self.settings.image
        res = run_cmd([docker, "image", "inspect", image], Path.cwd(), timeout=60)
        if res.ok:
            return
        if "No such image" in res.output:
            raise SandboxError(f"no Docker image {image!r}. Build it: uv run swarm docker-build")
        raise SandboxError(f"Docker is not usable (is Docker Desktop running?):\n{res.output}")

    def exe(self, name: str) -> str | None:
        return name  # resolved inside the image

    def workdir(self, wt: Path) -> str:
        return WORK

    def in_worktree(self, wt: Path, rel: str) -> str:
        return posixpath.join(WORK, rel.replace("\\", "/"))

    def path(self, mount: Mount) -> str:
        return mount.target

    def contains(self, wt: Path, path: str) -> bool:
        return PurePosixPath(posixpath.normpath(path)).is_relative_to(WORK)

    def command(
        self,
        argv: Sequence[str],
        *,
        name: str,
        role: str,
        mounts: Sequence[Mount],
        env: Mapping[str, str],
        pass_env: Sequence[str] = (),
        interactive: bool = False,
    ) -> list[str]:
        """The `docker run` command line. Pure, so it can be tested without Docker."""
        s = self.settings
        network = s.network if role == AGENT else s.verify_network
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            f"swarm.target={self.label}",
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,mode=1777",
            "--memory",
            s.memory,
            "--pids-limit",
            str(s.pids_limit),
            "--network",
            network,
            "--workdir",
            WORK,
        ]
        if s.cpus:
            cmd += ["--cpus", str(s.cpus)]
        if network != "none":
            # Docker Desktop defines host.docker.internal itself; Docker on Linux needs this.
            cmd += ["--add-host", "host.docker.internal:host-gateway"]
        if interactive:
            cmd.append("--interactive")
        for m in mounts:
            cmd += ["--mount", mount_arg(m)]
        for key, value in env.items():
            cmd += ["--env", f"{key}={value}"]
        for key in pass_env:
            cmd += ["--env", key]  # value comes from the harness env, not the command line
        return [*cmd, s.image, *argv]

    def container_env(self, extra: Mapping[str, str]) -> dict[str, str]:
        env = {
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Tool caches go to the container's tmpfs: the worktree is read-only for checks.
            "MYPY_CACHE_DIR": "/tmp/.mypy_cache",
            "RUFF_CACHE_DIR": "/tmp/.ruff_cache",
            **LOCKDOWN_ENV,
        }
        env.update({k: container_url(v) for k, v in extra.items()})
        return env

    def git_mounts(self, wt: Path, call_dir: Path) -> list[Mount]:
        """A private git dir for one container. Only the object store is shared (read-only),
        so nothing the container does can move a ref or touch another branch."""
        out = git.git(
            wt,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            "--git-path",
            "index",
            "HEAD",
            "--symbolic-full-name",
            "HEAD",
        )
        common, index, head, ref = out.splitlines()[:4]
        common_dir, index_file = Path(common), Path(index)
        mirrored = git.git(wt, "config", "--get-regexp", _MIRRORED, check=False)

        gd = call_dir / "git"
        for sub in ("objects/info", "refs/heads", "info"):
            (gd / sub).mkdir(parents=True)
        _write(gd / "objects" / "info" / "alternates", OBJECTS)
        _write(gd / "config", container_git_config(mirrored))
        if ref.startswith("refs/heads/"):
            _write(gd / "HEAD", f"ref: {ref}")
            (gd / ref).parent.mkdir(parents=True, exist_ok=True)
            _write(gd / ref, head)
        else:
            _write(gd / "HEAD", head)
        if index_file.is_file():
            shutil.copyfile(index_file, gd / "index")
        exclude = common_dir / "info" / "exclude"
        if exclude.is_file():
            shutil.copyfile(exclude, gd / "info" / "exclude")
        gitfile = call_dir / "gitfile"
        _write(gitfile, f"gitdir: {GIT_DIR}")
        return [
            Mount(gitfile, f"{WORK}/.git"),
            Mount(gd, GIT_DIR, writable=True),
            Mount(common_dir / "objects", OBJECTS),
        ]

    def run(
        self,
        argv: Sequence[str],
        wt: Path,
        *,
        role: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
        pass_env: Sequence[str] = (),
        mounts: Sequence[Mount] = (),
        stdin_text: str | None = None,
    ) -> CmdResult:
        name = f"swarm-{secrets.token_hex(6)}"
        call_dir = self.scratch / name
        call_dir.mkdir(parents=True)
        try:
            writable = role != CHECK or not self.settings.readonly_checks
            cmd = self.command(
                argv,
                name=name,
                role=role,
                mounts=[Mount(wt, WORK, writable), *self.git_mounts(wt, call_dir), *mounts],
                env=self.container_env(env or {}),
                pass_env=pass_env,
                interactive=stdin_text is not None,
            )
            return self._exec(cmd, name, wt, timeout, stdin_text)
        finally:
            force_rmtree(call_dir)

    def _exec(
        self, cmd: list[str], name: str, wt: Path, timeout: float, stdin_text: str | None
    ) -> CmdResult:
        try:
            res = run_cmd(cmd, wt, timeout=timeout, stdin_text=stdin_text)
        except BaseException:  # Ctrl+C: don't leave the container running
            remove_containers([name])
            raise
        if res.timed_out:
            remove_containers([name])  # killing the docker client does not stop the container
        return res


def make_box(cfg: Config, target: Target) -> Box:
    if target.sandbox == "docker":
        return DockerBox(cfg.sandbox, cfg.runs_dir / ".sandbox", label=target.name)
    return HOST


def remove_containers(names: Sequence[str]) -> None:
    if names:
        subprocess.run(["docker", "rm", "--force", *names], capture_output=True, check=False)


def leftover_containers(target: str) -> list[str]:
    """Containers of a harness that was killed before it could remove them."""
    if not shutil.which("docker"):
        return []
    res = run_cmd(
        ["docker", "ps", "--all", "--quiet", "--filter", f"label=swarm.target={target}"],
        Path.cwd(),
        timeout=60,
    )
    return res.output.split() if res.ok else []


def build_command(settings: SandboxSettings, context: Path = DOCKER_DIR) -> list[str]:
    """Pins the image's gate tools to the harness venv's versions, so a gate gives the same
    verdict in the sandbox as on the host."""
    cmd = ["docker", "build", "--load", "--tag", settings.image]
    for dist, arg in GATE_TOOLS.items():
        try:
            cmd += ["--build-arg", f"{arg}={importlib.metadata.version(dist)}"]
        except importlib.metadata.PackageNotFoundError:
            pass  # keep the Dockerfile's default
    return [*cmd, str(context)]


def build_image(settings: SandboxSettings, log: Callable[[str], None]) -> bool:
    _docker()
    cmd = build_command(settings)
    log(" ".join(cmd))
    return subprocess.run(cmd, check=False).returncode == 0
