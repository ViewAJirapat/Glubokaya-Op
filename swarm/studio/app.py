"""`swarm studio`: start a project's server and open the studio in a desktop window.

Window, in order of preference (--window auto):
- webview: a native window (pywebview on the Edge WebView2 runtime built into Windows 11);
  install with `uv sync --extra desktop` (about 1.2 MB with its dependencies);
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

from swarm import config as config_mod
from swarm.studio.chief import Chief, ChiefError, FilePlanner, Ollama
from swarm.studio.chief_claude import ClaudeChief
from swarm.studio.project import ChiefSpec, Planner, Project, StudioSettings, open_project
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


def make_planner(
    settings: StudioSettings,
    chiefs: dict[str, ChiefSpec],
    name: str | None = None,
    plan_file: Path | None = None,
) -> Planner:
    """The planner for a project: a plan file, or the chief named by --chief / [studio] chief."""
    if plan_file:
        return FilePlanner(plan_file.resolve())
    chosen = name or settings.chief
    spec = chiefs.get(chosen)
    if spec is None:
        raise config_mod.ConfigError(
            f"unknown chief {chosen!r}; swarm.toml defines [chiefs.*]: {', '.join(sorted(chiefs))}"
        )
    if spec.kind == "claude":
        claude = ClaudeChief(model=spec.model, extra_args=spec.extra_args, timeout=spec.timeout)
        try:
            claude.preflight()
        except ChiefError as exc:
            raise config_mod.ConfigError(f"chief {chosen!r}: {exc}") from None
        return Chief(claude)
    model = spec.model or settings.chief_model
    return Chief(Ollama(settings.ollama, model, settings.num_ctx, settings.num_predict))


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
    sandbox: str | None = None,
    chief: str | None = None,
    log: Callable[[str], None] = print,
) -> int:
    settings = StudioSettings.load(config_path)
    planner = make_planner(settings, ChiefSpec.load_all(config_path), chief, plan_file)
    project: Project = open_project(
        project_name, planner, harness_config=config_path, engine=engine, sandbox=sandbox
    )
    project.start()
    try:
        server = StudioServer(project, port)
    except OSError as exc:
        project.close()
        log(f"error: can't listen on 127.0.0.1:{port} ({exc}); pick another --port")
        return 2
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
