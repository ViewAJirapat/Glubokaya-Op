"""`swarm studio`: start a project's server and open the studio in a desktop window.

Window, in order of preference (--window auto):
- webview: a native window (pywebview on the Edge WebView2 runtime built into Windows 11);
  optional, pip-installable into the venv (about 1.2 MB with its dependencies);
- edge: Microsoft Edge in app mode (its own window, no address bar; nothing to install);
- browser: the default browser;
- none: only print the URL.
Closing the window stops the studio, the preview and any running task's next step.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from swarm.studio.chief import Chief, FilePlanner, Ollama
from swarm.studio.project import Planner, Project, StudioSettings, open_project
from swarm.studio.server import StudioServer

WINDOWS = ("auto", "webview", "edge", "browser", "none")
EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _edge() -> str | None:
    for p in EDGE_PATHS:
        if p.is_file():
            return str(p)
    return shutil.which("msedge") or shutil.which("microsoft-edge")


def _has_webview() -> bool:
    return importlib.util.find_spec("webview") is not None  # optional dependency


def _open_webview(title: str, url: str) -> None:
    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.create_window(title, url, width=1500, height=950, min_size=(900, 600))
    webview.start()  # returns when the window closes


def _open_edge(url: str, profile: Path) -> None:
    exe = _edge()
    assert exe
    profile.mkdir(parents=True, exist_ok=True)
    # Its own profile: the process lives exactly as long as the window, and the owner's
    # browser profile is untouched.
    proc = subprocess.Popen(
        [
            exe,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--window-size=1500,950",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    )
    proc.wait()


def _wait_forever(log: Callable[[str], None]) -> None:
    log("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


def run(
    config_path: Path,
    project_name: str,
    *,
    engine: str | None,
    port: int,
    window: str,
    plan_file: Path | None = None,
    log: Callable[[str], None] = print,
) -> int:
    settings = StudioSettings.load(config_path)
    planner: Planner
    if plan_file:
        planner = FilePlanner(plan_file.resolve())
    else:
        llm = Ollama(settings.ollama, settings.chief_model, settings.num_ctx, settings.num_predict)
        planner = Chief(llm)
    project: Project = open_project(
        project_name, planner, harness_config=config_path, engine=engine
    )
    project.start()
    server = StudioServer(project, port)
    server.start()
    log(f"Swarm Studio, project {project.name} ({project.root})")
    log(f"  studio:  {server.url}")
    log(f"  preview: {project.preview.url}")
    log(f"  chief {planner.label} | worker {project.engine.label}")
    if window == "auto":
        window = "webview" if _has_webview() else ("edge" if _edge() else "browser")
    try:
        if window == "webview":
            _open_webview(f"Swarm Studio - {project.name}", server.url)
        elif window == "edge" and _edge():
            _open_edge(server.url, project.root.parent / ".edge-profile")
        elif window in ("browser", "edge"):
            webbrowser.open(server.url)
            _wait_forever(log)
        else:
            _wait_forever(log)
    finally:
        log("stopping")
        server.stop()
        project.close()
    return 0
