"""The live preview: the app's accepted code (its repo's main checkout) served on a local port.
Restarted whenever main moves, so the preview always shows what passed every check."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from swarm.util import IS_WINDOWS


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_our_app(port: int) -> bool:
    """The frame's own meta route answers: not just any server on that port."""
    try:
        with _DIRECT.open(f"http://127.0.0.1:{port}/api/_app", timeout=1) as resp:
            return resp.status == 200 and "routes" in json.load(resp)
    except (OSError, ValueError):
        return False


class Preview:
    def __init__(self, app_dir: Path, log_file: Path, module: str = "webapp") -> None:
        self.app_dir = app_dir
        self.log_file = log_file
        self.module = module
        self.port = free_port()
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def restart(self) -> bool:
        with self._lock:
            self._stop()
            if self._start():
                return True
            if self._proc is None:  # it exited: most likely another app took the port
                self.port = free_port()
                return self._start()
            return False

    def _start(self) -> bool:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.upper().startswith(("PYTHON", "VIRTUAL_ENV"))
        }
        env.update(
            PYTHONPATH=str(self.app_dir / "src"),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1",
            HOST="127.0.0.1",
            PORT=str(self.port),
        )
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("ab") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", self.module],
                cwd=self.app_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0,
            )
        self._proc = proc
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and proc.poll() is None:
            if _is_our_app(self.port):
                self.error = None
                return True
            time.sleep(0.2)
        self.error = f"the app did not start; see {self.log_file}"
        if proc.poll() is not None:
            self._proc = None
        return False

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
