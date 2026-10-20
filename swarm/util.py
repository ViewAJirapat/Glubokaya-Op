"""Subprocess helpers: timeouts that kill the whole process tree, output clipping."""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"


@dataclass
class CmdResult:
    cmd: list[str]
    returncode: int
    output: str  # stdout and stderr, interleaved
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_cmd(
    cmd: Sequence[str],
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin_text: str | None = None,
) -> CmdResult:
    """Run a command, never hang: on timeout the entire process tree is killed."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(cmd),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            # Own process group/session, so a timeout can kill grandchildren too.
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            start_new_session=not IS_WINDOWS,
        )
    except FileNotFoundError as exc:
        return CmdResult(list(cmd), 127, f"command not found: {exc}", 0.0)

    try:
        out, _ = proc.communicate(input=stdin_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        timed_out = True
    return CmdResult(
        cmd=list(cmd),
        returncode=proc.returncode if not timed_out else -1,
        output=out or "",
        seconds=time.monotonic() - start,
        timed_out=timed_out,
    )


def clip(text: str, limit: int) -> str:
    """Keep the head and (mostly) the tail; test runners put summaries at the end."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.3)
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} chars omitted] ...\n{text[-tail:]}"


# Variables the agent process may see. Everything else (tokens, cloud creds,
# SSH agent sockets, ...) is dropped. Engines add what they need via pass_env.
_BASE_ENV = {
    # Windows essentials
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "USERNAME",
    "COMPUTERNAME",
    # POSIX essentials
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
}


# Set for every agent and gate process, on the host and in the sandbox. Git is configured
# (via GIT_CONFIG_* env) so the agent cannot use stored credentials or reach any remote:
# pushing is the harness's job, after the gate.
LOCKDOWN_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_CONFIG_KEY_1": "protocol.allow",
    "GIT_CONFIG_VALUE_1": "never",
}


def agent_env(pass_env: Sequence[str], extra: Mapping[str, str]) -> dict[str, str]:
    """Build a minimal environment for an agent process on the host."""
    allowed = {k.upper() for k in _BASE_ENV} | {k.upper() for k in pass_env}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(LOCKDOWN_ENV)
    env.update(extra)
    return env


def force_rmtree(path: Path) -> None:
    def on_error(func: Callable[..., Any], p: str, _exc: BaseException) -> None:
        os.chmod(p, stat.S_IWRITE)  # git marks object files read-only on Windows
        func(p)

    shutil.rmtree(path, onexc=on_error)
