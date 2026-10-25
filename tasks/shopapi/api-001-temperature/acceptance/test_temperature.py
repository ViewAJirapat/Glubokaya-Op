import importlib

import pytest

from shopapi import create_app


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/temperature/100/c", {"value": 212.0, "unit": "f"}),
        ("/temperature/0/c", {"value": 32.0, "unit": "f"}),
        ("/temperature/-40/F", {"value": -40.0, "unit": "c"}),
        ("/temperature/98.6/f", {"value": 37.0, "unit": "c"}),
        ("/temperature/36.6/C", {"value": 97.9, "unit": "f"}),
        ("/temperature/-273.15/c", {"value": -459.7, "unit": "f"}),
    ],
)
def test_converts(path: str, expected: dict[str, object]) -> None:
    resp = create_app().handle("GET", path)
    assert resp.status == 200
    assert resp.body == expected
    assert isinstance(resp.body, dict) and type(resp.body["value"]) is float


@pytest.mark.parametrize(
    "path",
    [
        "/temperature/abc/c",
        "/temperature/nan/c",
        "/temperature/inf/f",
        "/temperature/10/k",
        "/temperature/-300/c",
        "/temperature/-460/f",
        "/temperature/1,5/c",
    ],
)
def test_rejects_bad_input(path: str) -> None:
    resp = create_app().handle("GET", path)
    assert resp.status == 400
    assert isinstance(resp.body, dict) and "error" in resp.body


def test_lives_in_its_own_feature_folder() -> None:
    importlib.import_module("shopapi.features.temperature.handler")
