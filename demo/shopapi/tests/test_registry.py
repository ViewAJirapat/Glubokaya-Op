import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from shopapi.app import App
from shopapi.core.http import Request, Response
from shopapi.core.registry import RegistryError, Route, discover, ensure_unique, match, route


def _ok(request: Request) -> Response:
    return Response(200, None)


def _returns(value: str) -> Callable[[Request], Response]:
    def handler(request: Request) -> Response:
        return Response(200, value)

    return handler


def test_features_are_discovered_from_their_folders() -> None:
    found = {(r.feature, r.method, r.pattern) for r in discover()}
    assert ("health", "GET", "/health") in found
    assert ("products", "GET", "/products/{sku}") in found


def test_match_extracts_params() -> None:
    assert match("/products/{sku}", "/products/tea-01") == {"sku": "tea-01"}
    assert match("/products/{sku}", "/products/") is None
    assert match("/products/{sku}", "/products/a/b") is None
    assert match("/health", "/health/") == {}


def test_two_features_claiming_one_route_fail_loudly() -> None:
    routes = [Route("GET", "/x/{a}", _ok, "one"), Route("GET", "/x/{b}", _ok, "two")]
    with pytest.raises(RegistryError, match="'one' and 'two'"):
        ensure_unique(routes)


def test_fixed_segments_win_over_params_whatever_the_feature_order() -> None:
    by_param = Route("GET", "/items/{id}", _returns("param"), "a_items")
    fixed = Route("GET", "/items/new", _returns("fixed"), "z_new")
    app = App([by_param, fixed])
    assert app.handle("GET", "/items/new").body == "fixed"
    assert app.handle("GET", "/items/7").body == "param"


def test_stacked_route_decorators_all_register() -> None:
    @route("GET", "/a")
    @route("POST", "/a")
    def both(request: Request) -> Response:
        return Response(200, request.method)

    app = App([Route(m, p, both, "f") for m, p in both.__shopapi_routes__])
    assert app.handle("GET", "/a").body == "GET"
    assert app.handle("POST", "/a").body == "POST"


def test_feature_folder_without_init_fails_loudly(tmp_path: Path) -> None:
    pkg = tmp_path / "fakefeatures"
    (pkg / "broken").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "broken" / "handler.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(RegistryError, match="'broken' has no __init__.py"):
            discover("fakefeatures")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("fakefeatures", None)


def test_discover_keeps_stacked_reexported_and_aliased_handlers(tmp_path: Path) -> None:
    feature = tmp_path / "fakefeats" / "multi"
    feature.mkdir(parents=True)
    (tmp_path / "fakefeats" / "__init__.py").write_text("", encoding="utf-8")
    (feature / "__init__.py").write_text("", encoding="utf-8")
    (feature / "impl.py").write_text(
        "from shopapi.core.api import Request, Response, route\n\n\n"
        '@route("GET", "/multi")\n'
        '@route("POST", "/multi")\n'
        "def both(request: Request) -> Response:\n"
        "    return Response(200, request.method)\n",
        encoding="utf-8",
    )
    (feature / "handler.py").write_text(
        "from fakefeats.multi.impl import both\n\nalias = both\n", encoding="utf-8"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        found = sorted((r.method, r.pattern, r.feature) for r in discover("fakefeats"))
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("fakefeats")]:
            del sys.modules[name]
    assert found == [("GET", "/multi", "multi"), ("POST", "/multi", "multi")]
