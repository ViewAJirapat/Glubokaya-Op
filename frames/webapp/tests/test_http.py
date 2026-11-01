from collections.abc import Callable

import pytest

from webapp.core.api import HTTPError, Request, Store


def _req(body: object = None, **params: str) -> Request:
    return Request("POST", "/api/x", Store(), params, {}, body)


def _status(fn: Callable[[], object]) -> int:
    with pytest.raises(HTTPError) as err:
        fn()
    return err.value.status


def test_param_int() -> None:
    assert _req(id="12").param_int("id") == 12
    assert _status(lambda: _req(id="x").param_int("id")) == 404
    assert _status(lambda: _req(id="-1").param_int("id")) == 404
    assert _status(lambda: _req().param_int("id")) == 404


def test_body_helpers() -> None:
    r = _req({"title": "  Buy milk ", "done": True, "n": 2.5, "flag": 1})
    assert r.text("title") == "Buy milk"
    assert r.flag("done") is True
    assert r.number("n") == 2.5
    assert r.has("title") and not r.has("other")
    assert _status(lambda: r.flag("flag")) == 400  # 1 is not a bool
    assert _status(lambda: r.number("done")) == 400  # a bool is not a number
    assert _status(lambda: _req({"title": "   "}).text("title")) == 400
    assert _status(lambda: _req({"title": "x" * 201}).text("title")) == 400
    assert _status(lambda: _req([1]).fields()) == 400
    assert _status(lambda: _req(None).text("title")) == 400
    assert not _req(None).has("title")
