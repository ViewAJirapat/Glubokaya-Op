"""The app: dispatches requests to discovered routes. Also a WSGI app for a real server."""

import json
from collections.abc import Iterable
from http import HTTPStatus
from urllib.parse import parse_qsl
from wsgiref.types import StartResponse, WSGIEnvironment

from shopapi.core.http import HTTPError, Request, Response
from shopapi.core.registry import Route, discover, match, specificity


class App:
    def __init__(self, routes: list[Route]) -> None:
        # Most specific first: /items/new wins over /items/{id} from any feature.
        self.routes = sorted(routes, key=lambda r: specificity(r.pattern))

    def handle(
        self,
        method: str,
        path: str,
        body: object = None,
        query: dict[str, str] | None = None,
    ) -> Response:
        method = method.upper()
        allowed: list[str] = []
        for r in self.routes:
            params = match(r.pattern, path)
            if params is None:
                continue
            if r.method != method:
                allowed.append(r.method)
                continue
            try:
                return r.handler(Request(method, path, params, dict(query or {}), body))
            except HTTPError as exc:
                return Response(exc.status, {"error": exc.message})
        if allowed:
            return Response(405, {"error": "method not allowed", "allow": sorted(set(allowed))})
        return Response(404, {"error": "not found"})

    def wsgi(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        resp = self._from_environ(environ, "GET" if method == "HEAD" else method)
        data = json.dumps(resp.body).encode("utf-8")
        try:
            reason = HTTPStatus(resp.status).phrase
        except ValueError:
            reason = "Unknown"
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(data)))]
        start_response(f"{resp.status} {reason}", headers)
        return [b""] if method == "HEAD" else [data]

    def _from_environ(self, environ: WSGIEnvironment, method: str) -> Response:
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
        path = _utf8(str(environ.get("PATH_INFO", "/")))
        query = dict(parse_qsl(_utf8(str(environ.get("QUERY_STRING", "")))))
        return self.handle(method, path, body, query)


def _utf8(value: str) -> str:
    """PEP 3333 hands over the path and query as latin-1-decoded bytes; URLs are UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return value  # already decoded by a non-PEP 3333 caller


def create_app() -> App:
    return App(discover())
