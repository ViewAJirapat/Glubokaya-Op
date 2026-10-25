import importlib

import pytest

from shopapi import create_app


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello world", {"chars": 11, "words": 2, "lines": 1, "longest": "hello"}),
        ("", {"chars": 0, "words": 0, "lines": 0, "longest": ""}),
        ("a\nbb ccc\n", {"chars": 9, "words": 3, "lines": 2, "longest": "ccc"}),
        ("  ab \t cd  ", {"chars": 11, "words": 2, "lines": 1, "longest": "ab"}),
        ("   ", {"chars": 3, "words": 0, "lines": 1, "longest": ""}),
    ],
)
def test_stats(text: str, expected: dict[str, object]) -> None:
    resp = create_app().handle("POST", "/text/stats", {"text": text})
    assert resp.status == 200
    assert resp.body == expected


@pytest.mark.parametrize("body", [None, ["text"], {"txt": "x"}, {"text": 5}, {"text": None}])
def test_rejects_bad_body(body: object) -> None:
    resp = create_app().handle("POST", "/text/stats", body)
    assert resp.status == 400
    assert isinstance(resp.body, dict) and "error" in resp.body


def test_get_is_not_allowed() -> None:
    assert create_app().handle("GET", "/text/stats").status == 405


def test_lives_in_its_own_feature_folder() -> None:
    importlib.import_module("shopapi.features.textstats.handler")
