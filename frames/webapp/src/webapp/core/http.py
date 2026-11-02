"""Request and response types shared by the app and every feature.

The helpers on Request raise the right HTTPError themselves, so a handler needs few ifs:
- request.param_int("id")         path segment {id} as int; 404 when it is not a whole number
- request.text("title")           body field: a non-empty string (stripped); 400 otherwise.
                                  request.text("title", max_len=80) also gives 400 when the
                                  stripped text is longer than 80 characters
- request.flag("done")            body field: true or false; 400 otherwise
- request.number("amount")        body field: an int or float (not a bool); 400 otherwise
- request.has("title")            True when the JSON body has that field
"""

from dataclasses import dataclass, field
from typing import Any

from webapp.core.errors import HTTPError
from webapp.core.store import Store


@dataclass(frozen=True)
class Response:
    status: int
    body: object  # anything json.dumps accepts


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    store: Store
    params: dict[str, str] = field(default_factory=dict)  # from {name} segments in the route
    query: dict[str, str] = field(default_factory=dict)
    body: object = None  # parsed JSON, or None when the request has no body

    def param_int(self, name: str) -> int:
        raw = self.params.get(name, "")
        if not (raw.isascii() and raw.isdigit()):
            raise HTTPError(404, f"no such {name}: {raw!r}")
        return int(raw)

    def fields(self) -> dict[str, Any]:
        """The JSON body as a dict; 400 when the body is missing or not a JSON object."""
        if not isinstance(self.body, dict):
            raise HTTPError(400, "the request body must be a JSON object")
        return self.body

    def has(self, name: str) -> bool:
        return isinstance(self.body, dict) and name in self.body

    def text(self, name: str, max_len: int | None = None) -> str:
        value = self.fields().get(name)
        if not isinstance(value, str) or not value.strip():
            raise HTTPError(400, f"{name!r} must be a non-empty string")
        if max_len is not None and len(value.strip()) > max_len:
            raise HTTPError(400, f"{name!r} is longer than {max_len} characters")
        return value.strip()

    def flag(self, name: str) -> bool:
        value = self.fields().get(name)
        if not isinstance(value, bool):
            raise HTTPError(400, f"{name!r} must be true or false")
        return value

    def number(self, name: str) -> int | float:
        value = self.fields().get(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise HTTPError(400, f"{name!r} must be a number")
        return value
