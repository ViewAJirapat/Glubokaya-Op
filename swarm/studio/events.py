"""In-process event bus: the build thread publishes, the UI's event stream reads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

KEEP = 5000  # recent events kept in memory; a client that falls further behind reloads state


@dataclass
class Event:
    seq: int
    kind: str  # message | state | log | plan | preview
    data: dict[str, Any]
    at: float


class Events:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._items: list[Event] = []
        self._seq = 0

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def publish(self, kind: str, data: dict[str, Any]) -> Event:
        with self._cond:
            self._seq += 1
            event = Event(self._seq, kind, data, time.time())
            self._items.append(event)
            del self._items[:-KEEP]
            self._cond.notify_all()
            return event

    def since(self, seq: int, timeout: float) -> list[Event] | None:
        """Events after `seq`, waiting up to `timeout` for one. None: `seq` is too old."""
        with self._cond:
            if self._items and seq < self._items[0].seq - 1:
                return None
            if self._seq <= seq:
                self._cond.wait(timeout)
            return [e for e in self._items if e.seq > seq]
