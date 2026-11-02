"""The studio's local HTTP server: the UI (static files), a JSON API and an event stream.

Only 127.0.0.1. Every /api/ call needs the session token (header X-Studio-Token, or ?token=
for the event stream) and a Host header naming this server, so another web page open on
the same PC can neither drive the studio nor read it (DNS rebinding included).
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from swarm.studio.project import Project, StudioError
from swarm.util import IS_WINDOWS

STATIC = Path(__file__).resolve().parent / "static"
KEEPALIVE = 15.0


def static_file(path: str) -> Path | None:
    """The UI file a URL path names, checked before the filesystem is touched: on Windows a
    path such as //host/share would make resolve() open a network share."""
    if path == "/":
        parts = ["index.html"]
    elif path.startswith("/static/"):
        parts = path.removeprefix("/static/").split("/")
    else:
        return None
    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
        return None
    target = STATIC.joinpath(*parts)
    return target if target.is_file() else None


class ExclusiveServer(ThreadingHTTPServer):
    """On Windows, SO_REUSEADDR (Python's default for HTTP servers) lets a second server bind
    a port another app already listens on, and the browser then reaches either one. Seen
    here: the studio shared a port with another local app. Bind exclusively instead."""

    daemon_threads = True
    allow_reuse_address = not IS_WINDOWS

    def server_bind(self) -> None:
        if IS_WINDOWS:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class StudioServer:
    def __init__(self, project: Project, port: int = 0) -> None:
        self.project = project
        self.token = secrets.token_urlsafe(18)
        self.httpd = ExclusiveServer(("127.0.0.1", port), _handler(self))
        self.closing = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.closing.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def _handler(server: StudioServer) -> type[BaseHTTPRequestHandler]:
    project = server.project

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # quiet console
            return None

        # -- plumbing ----------------------------------------------------------------------

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _text(self, text: str) -> None:
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")

        def _allowed(self, query: dict[str, list[str]]) -> bool:
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{server.port}", f"localhost:{server.port}"):
                return False
            given = self.headers.get("X-Studio-Token") or (query.get("token") or [""])[0]
            # bytes: compare_digest raises TypeError on non-ASCII str (a forged token)
            return secrets.compare_digest(given.encode("utf-8", "replace"), server.token.encode())

        def _body(self) -> dict[str, Any]:
            length = self.headers.get("Content-Length", "0")
            if not (length.isascii() and length.isdigit()) or int(length) > 1_000_000:
                return {}
            raw = self.rfile.read(int(length)) if int(length) else b""
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        # -- GET ---------------------------------------------------------------------------

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            path, query = parts.path, parse_qs(parts.query)
            if not path.startswith("/api/"):
                return self._static(path)
            if not self._allowed(query):
                return self._json(403, {"error": "forbidden"})
            try:
                if path == "/api/state":
                    return self._json(200, project.snapshot())
                if path == "/api/events":
                    return self._events(int((query.get("since") or ["0"])[0] or 0))
                m = re.fullmatch(r"/api/tasks/([\w-]+)/(log|diff|attempts|file)", path)
                if m:
                    tid, what = m.groups()
                    if what == "log":
                        return self._text(project.task_log(tid))
                    if what == "diff":
                        return self._text(project.task_diff(tid))
                    if what == "attempts":
                        return self._json(200, project.task_attempts(tid))
                    run = (query.get("run") or [""])[0]
                    n = int((query.get("n") or ["0"])[0] or 0)
                    name = (query.get("name") or [""])[0]
                    return self._text(project.attempt_file(tid, run, n, name))
            except (StudioError, ValueError) as exc:
                return self._json(404, {"error": str(exc)})
            return self._json(404, {"error": "not found"})

        def _static(self, path: str) -> None:
            target = static_file(path)
            if target is None:
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)

        def _events(self, since: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            seq = since
            try:
                while not server.closing.is_set():
                    events = project.events.since(seq, KEEPALIVE)
                    if events is None:  # the client fell too far behind: reload everything
                        self.wfile.write(b"event: reset\ndata: {}\n\n")
                        seq = project.events.seq
                    elif not events:
                        self.wfile.write(b": keepalive\n\n")
                    for e in events or []:
                        data = json.dumps(e.data)
                        self.wfile.write(f"id: {e.seq}\nevent: {e.kind}\ndata: {data}\n\n".encode())
                        seq = e.seq
                    self.wfile.flush()
            except (ConnectionError, OSError):
                return

        # -- POST --------------------------------------------------------------------------

        def do_POST(self) -> None:
            parts = urlsplit(self.path)
            if not self._allowed(parse_qs(parts.query)):
                return self._json(403, {"error": "forbidden"})
            body = self._body()
            path = parts.path
            try:
                if path == "/api/chat":
                    project.chat(str(body.get("text", ""))[:4000])
                elif path == "/api/plan/approve":
                    project.approve()
                elif path == "/api/build/pause":
                    project.pause()
                elif path == "/api/build/resume":
                    project.resume()
                elif path == "/api/preview/restart":
                    project.restart_preview()
                else:
                    m = re.fullmatch(r"/api/tasks/([\w-]+)/(retry|skip)", path)
                    if not m:
                        return self._json(404, {"error": "not found"})
                    tid, action = m.groups()
                    (project.retry if action == "retry" else project.skip)(tid)
            except StudioError as exc:
                return self._json(409, {"error": str(exc)})
            return self._json(200, {"ok": True})

    return Handler
