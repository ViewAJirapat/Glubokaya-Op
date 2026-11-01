import io
import json

from webapp import create_app
from webapp.app import App
from webapp.core.registry import Route


def test_health_and_meta() -> None:
    app = create_app()
    assert (app.handle("GET", "/api/health").status, app.handle("GET", "/api/health").body) == (
        200,
        {"status": "ok"},
    )
    meta = app.handle("GET", "/api/_app")
    assert meta.status == 200
    assert isinstance(meta.body, dict)
    assert {"method": "GET", "path": "/api/health", "feature": "health"} in meta.body["routes"]
    assert app.handle("POST", "/api/_app").status == 405


def test_unknown_path_and_wrong_method() -> None:
    app = create_app()
    assert app.handle("GET", "/api/nowhere").status == 404
    wrong = app.handle("DELETE", "/api/health")
    assert (wrong.status, wrong.body) == (405, {"error": "method not allowed", "allow": ["GET"]})


def test_each_app_has_its_own_store() -> None:
    a, b = create_app(), create_app()
    a.store.insert("todos", {})
    assert b.store.all("todos") == []


def test_handlers_get_the_store_and_errors_become_json() -> None:
    from webapp.core.api import HTTPError, Request, Response

    def add(request: Request) -> Response:
        return Response(201, request.store.insert("things", {"name": request.text("name")}))

    def boom(request: Request) -> Response:
        raise HTTPError(418, "teapot")

    app = App([Route("POST", "/api/things", add, "t"), Route("GET", "/api/boom", boom, "t")])
    assert app.handle("POST", "/api/things", {"name": "x"}).body == {"id": 1, "name": "x"}
    assert app.handle("POST", "/api/things", {}).status == 400
    assert (app.handle("GET", "/api/boom").status, app.handle("GET", "/api/boom").body) == (
        418,
        {"error": "teapot"},
    )


def _wsgi(method: str, path: str, length: str = "", body: bytes = b"") -> tuple[str, bytes]:
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": length,
        "wsgi.input": io.BytesIO(body),
    }
    seen: list[str] = []

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        seen.append(status)

    chunks = create_app().wsgi(environ, start_response)
    return seen[0], b"".join(chunks)


def test_wsgi_api_and_shell() -> None:
    status, data = _wsgi("GET", "/api/health")
    assert (status, json.loads(data)) == ("200 OK", {"status": "ok"})
    status, page = _wsgi("GET", "/")
    assert status == "200 OK" and b"/static/shell.js" in page
    assert _wsgi("GET", "/static/shell.js")[0] == "200 OK"
    assert _wsgi("GET", "/static/style.css")[0] == "200 OK"
    assert _wsgi("GET", "/static/../app.py")[0] == "404 Not Found"
    assert _wsgi("GET", "/static/nope.js")[0] == "404 Not Found"
    assert _wsgi("GET", "/elsewhere")[0] == "404 Not Found"


def test_wsgi_edge_cases() -> None:
    assert _wsgi("HEAD", "/api/health") == ("200 OK", b"")
    assert _wsgi("POST", "/api/health", "-1")[0] == "400 Bad Request"
    assert _wsgi("POST", "/api/health", "abc")[0] == "400 Bad Request"
    assert _wsgi("POST", "/api/health", "2", b"{x")[0] == "400 Bad Request"
