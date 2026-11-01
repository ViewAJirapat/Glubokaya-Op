"""In-memory data store: named collections of JSON objects. Each app has its own store.

Ids are whole numbers per collection: 1, 2, 3, ... in insertion order, never reused after a
delete. Every method returns copies, so changing a returned item never changes the store.
"""

import copy
import threading
from typing import Any

from webapp.core.errors import HTTPError

Item = dict[str, Any]


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[int, Item]] = {}
        self._next_id: dict[str, int] = {}

    def insert(self, collection: str, item: Item) -> Item:
        """Store a copy of `item` with a new "id" and return it (id first, then the fields)."""
        with self._lock:
            new_id = self._next_id.get(collection, 1)
            self._next_id[collection] = new_id + 1
            stored = {"id": new_id, **{k: v for k, v in item.items() if k != "id"}}
            self._items.setdefault(collection, {})[new_id] = copy.deepcopy(stored)
            return stored

    def all(self, collection: str) -> list[Item]:
        """Every item of the collection, oldest first (by id)."""
        with self._lock:
            items = self._items.get(collection, {})
            return [copy.deepcopy(items[i]) for i in sorted(items)]

    def get(self, collection: str, item_id: int) -> Item | None:
        """The item with this id, or None."""
        with self._lock:
            item = self._items.get(collection, {}).get(item_id)
            return copy.deepcopy(item) if item is not None else None

    def require(self, collection: str, item_id: int) -> Item:
        """Like get(), but raises HTTPError(404) when the item does not exist."""
        item = self.get(collection, item_id)
        if item is None:
            raise HTTPError(404, f"{collection} {item_id} not found")
        return item

    def update(self, collection: str, item_id: int, changes: Item) -> Item | None:
        """Merge `changes` into the item (its "id" never changes); return it, or None."""
        with self._lock:
            item = self._items.get(collection, {}).get(item_id)
            if item is None:
                return None
            item.update({k: copy.deepcopy(v) for k, v in changes.items() if k != "id"})
            return copy.deepcopy(item)

    def delete(self, collection: str, item_id: int) -> bool:
        """Remove the item; True if it existed."""
        with self._lock:
            return self._items.get(collection, {}).pop(item_id, None) is not None

    def count(self, collection: str) -> int:
        with self._lock:
            return len(self._items.get(collection, {}))
