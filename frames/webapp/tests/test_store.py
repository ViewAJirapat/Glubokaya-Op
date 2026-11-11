import pytest

from webapp.core.api import HTTPError, Store


def test_ids_count_up_per_collection_and_are_never_reused() -> None:
    s = Store()
    assert s.insert("todos", {"title": "a"}) == {"id": 1, "title": "a"}
    assert s.insert("todos", {"title": "b", "id": 99})["id"] == 2  # "id" is the store's
    assert s.insert("notes", {})["id"] == 1
    assert s.delete("todos", 2)
    assert s.insert("todos", {"title": "c"})["id"] == 3
    assert [t["id"] for t in s.all("todos")] == [1, 3]
    assert s.count("todos") == 2


def test_get_update_delete() -> None:
    s = Store()
    s.insert("todos", {"title": "a", "done": False})
    assert s.get("todos", 1) == {"id": 1, "title": "a", "done": False}
    assert s.get("todos", 2) is None
    assert s.update("todos", 1, {"done": True, "id": 7}) == {"id": 1, "title": "a", "done": True}
    assert s.update("todos", 2, {"done": True}) is None
    assert s.delete("todos", 1) is True
    assert s.delete("todos", 1) is False
    assert s.all("todos") == [] and s.all("never") == []


def test_returns_copies() -> None:
    s = Store()
    item = s.insert("todos", {"tags": ["x"]})
    item["tags"].append("changed")
    s.all("todos")[0]["tags"].append("changed")
    assert s.get("todos", 1) == {"id": 1, "tags": ["x"]}


def test_require_raises_404() -> None:
    s = Store()
    with pytest.raises(HTTPError) as err:
        s.require("todos", 5)
    assert err.value.status == 404
