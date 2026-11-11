"""The app: JSON API under /api/ (discovered feature routes) plus the page shell at /.

GET /api/_app describes the page: ui.json (sections that call the API) and the routes.
"""

import json
import mimetypes
from collections.abc import Iterable
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from wsgiref.types import StartResponse, WSGIEnvironment

from webapp.core.errors import HTTPError
from webapp.core.http import Request, Response
from webapp.core.registry import Route, discover, match, specificity
from webapp.core.store import Store

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
UI_FILE = HERE / "ui.json"


def load_ui() -> dict[str, Any]:
    data = json.loads(UI_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{UI_FILE} must hold a JSON object")
    return data


class App:
    def __init__(self, routes: list[Route], ui: dict[str, Any] | None = None) -> None:
        # Most specific first: /api/items/new wins over /api/items/{id} from any feature.
        self.routes = sorted(routes, key=lambda r: specificity(r.pattern))
        self.ui = ui if ui is not None else {"title": "App", "sections": []}
        self.store = Store()

    def describe(self) -> dict[str, Any]:
        routes = [
            {"method": r.method, "path": r.pattern, "feature": r.feature} for r in self.routes
        ]
        return {**self.ui, "routes": routes}

    def handle(
        self,
        method: str,
        path: str,
        body: object = None,
        query: dict[str, str] | None = None,
    ) -> Response:
        method = method.upper()
        if path == "/api/_app":
            return Response(200, self.describe()) if method == "GET" else _not_allowed(["GET"])
        allowed: list[str] = []
        for r in self.routes:
            params = match(r.pattern, path)
            if params is None:
                continue
            if r.method != method:
                allowed.append(r.method)
                continue
            request = Request(method, path, self.store, params, dict(query or {}), body)
            try:
                return r.handler(request)
            except HTTPError as exc:
                return Response(exc.status, {"error": exc.message})
        if allowed:
            return _not_allowed(allowed)
        return Response(404, {"error": "not found"})

    def wsgi(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = _utf8(str(environ.get("PATH_INFO", "/")))
        if not path.startswith("/api/"):
            status, ctype, data = self._static(path)
        else:
            resp = self._from_environ(environ, path, "GET" if method == "HEAD" else method)
            status, ctype, data = resp.status, "application/json", json.dumps(resp.body).encode()
        try:
            reason = HTTPStatus(status).phrase
        except ValueError:
            reason = "Unknown"
        headers = [("Content-Type", ctype), ("Content-Length", str(len(data)))]
        headers.append(("Cache-Control", "no-store"))
        start_response(f"{status} {reason}", headers)
        return [b""] if method == "HEAD" else [data]

    def _static(self, path: str) -> tuple[int, str, bytes]:
        # Check the name before touching the filesystem: on Windows, resolving a path like
        # //host/share would open a network share.
        if path == "/":
            parts = ["index.html"]
        elif path.startswith("/static/"):
            parts = path.removeprefix("/static/").split("/")
        else:
            return 404, "text/plain", b"not found"
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            return 404, "text/plain", b"not found"
        target = STATIC.joinpath(*parts)
        if not target.is_file():
            return 404, "text/plain", b"not found"
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return 200, ctype, target.read_bytes()

    def _from_environ(self, environ: WSGIEnvironment, path: str, method: str) -> Response:
        raw_length = str(environ.get("CONTENT_LENGTH") or "0").strip()
        # ASCII digits only: rejects negatives (read(-1) would block) and digits like "²"
        if not (raw_length.isascii() and raw_length.isdigit()):
            return Response(400, {"error": "bad Content-Length"})
        length = int(raw_length)
        raw = environ["wsgi.input"].read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            return Response(400, {"error": "request body is not valid JSON"})
        query = dict(parse_qsl(_utf8(str(environ.get("QUERY_STRING", "")))))
        return self.handle(method, path, body, query)


def _not_allowed(allowed: list[str]) -> Response:
    return Response(405, {"error": "method not allowed", "allow": sorted(set(allowed))})


def _utf8(value: str) -> str:
    """PEP 3333 hands over the path and query as latin-1-decoded bytes; URLs are UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return value  # already decoded by a non-PEP 3333 caller


def create_app() -> App:
    """A fresh app with its own empty store."""
    return App(discover(), load_ui())
