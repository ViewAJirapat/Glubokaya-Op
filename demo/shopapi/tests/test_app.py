import io
import json

from shopapi import create_app


def test_health() -> None:
    resp = create_app().handle("GET", "/health")
    assert (resp.status, resp.body) == (200, {"status": "ok"})


def test_product_found_and_missing() -> None:
    app = create_app()
    found = app.handle("GET", "/products/tea-01")
    assert found.status == 200
    assert found.body == {"sku": "tea-01", "name": "Green Tea", "price_cents": 450}
    missing = app.handle("GET", "/products/nope")
    assert missing.status == 404
    assert missing.body == {"error": "unknown sku 'nope'"}


def test_unknown_path_and_wrong_method() -> None:
    app = create_app()
    assert app.handle("GET", "/nowhere").status == 404
    wrong = app.handle("DELETE", "/health")
    assert (wrong.status, wrong.body) == (405, {"error": "method not allowed", "allow": ["GET"]})


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


def test_wsgi_round_trip() -> None:
    status, data = _wsgi("GET", "/products")
    assert status == "200 OK"
    assert json.loads(data) == {"skus": ["mug-02", "tea-01"]}


def test_wsgi_edge_cases() -> None:
    assert _wsgi("HEAD", "/health") == ("200 OK", b"")
    assert _wsgi("DELETE", "/health")[0] == "405 Method Not Allowed"
    assert _wsgi("POST", "/health", "-1")[0] == "400 Bad Request"
    assert _wsgi("POST", "/health", "abc")[0] == "400 Bad Request"
    assert _wsgi("POST", "/health", "\u00b2")[0] == "400 Bad Request"  # str.isdigit() says yes
    assert _wsgi("GET", "/health", "0 ")[0] == "200 OK"  # surrounding whitespace is fine
    # PEP 3333 passes the path as latin-1; the app reads it as UTF-8
    status, data = _wsgi("GET", "/products/caf\u00c3\u00a9")
    assert status == "404 Not Found"
    assert json.loads(data) == {"error": "unknown sku 'caf\u00e9'"}


def test_query_string_is_read_as_utf8() -> None:
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/health",
        "QUERY_STRING": "q=caf\u00c3\u00a9",
        "wsgi.input": io.BytesIO(b""),
    }
    seen: list[object] = []
    app = create_app()
    original = app.handle

    def spy(method: str, path: str, body: object = None, query: object = None) -> object:
        seen.append(query)
        return original(method, path, body)

    app.handle = spy  # type-safe enough for a test; mypy only checks src
    app.wsgi(environ, lambda status, headers: None)
    assert seen == [{"q": "caf\u00e9"}]
