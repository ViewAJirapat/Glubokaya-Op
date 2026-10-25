import importlib

import pytest

from shopapi import create_app


@pytest.mark.parametrize(
    ("raw", "number", "roman"),
    [
        ("1", 1, "I"),
        ("4", 4, "IV"),
        ("9", 9, "IX"),
        ("14", 14, "XIV"),
        ("40", 40, "XL"),
        ("90", 90, "XC"),
        ("400", 400, "CD"),
        ("1994", 1994, "MCMXCIV"),
        ("2024", 2024, "MMXXIV"),
        ("3999", 3999, "MMMCMXCIX"),
        ("007", 7, "VII"),
    ],
)
def test_converts(raw: str, number: int, roman: str) -> None:
    resp = create_app().handle("GET", f"/roman/{raw}")
    assert resp.status == 200
    assert resp.body == {"number": number, "roman": roman}


@pytest.mark.parametrize("raw", ["0", "4000", "-5", "+7", "3.5", "abc", "١٢"])
def test_rejects_bad_input(raw: str) -> None:
    resp = create_app().handle("GET", f"/roman/{raw}")
    assert resp.status == 400
    assert isinstance(resp.body, dict) and "error" in resp.body


def test_lives_in_its_own_feature_folder() -> None:
    importlib.import_module("shopapi.features.roman.handler")
