import pytest

from webapp.core.registry import RegistryError, Route, ensure_unique, match, specificity
from webapp.features.health.handler import health


def test_match_and_specificity() -> None:
    assert match("/api/todos/{id}", "/api/todos/3") == {"id": "3"}
    assert match("/api/todos/{id}", "/api/todos/") is None
    assert match("/api/todos", "/api/todos/3") is None
    assert specificity("/api/todos/new") < specificity("/api/todos/{id}")


def test_duplicate_routes_and_routes_outside_api_are_refused() -> None:
    with pytest.raises(RegistryError, match="defined by features"):
        ensure_unique(
            [Route("GET", "/api/a/{x}", health, "a"), Route("GET", "/api/a/{y}", health, "b")]
        )
    with pytest.raises(RegistryError, match="must start with /api/"):
        ensure_unique([Route("GET", "/todos", health, "a")])
