"""Request and response types shared by the app and every feature."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)  # from {name} segments in the route
    query: dict[str, str] = field(default_factory=dict)
    body: object = None  # parsed JSON, or None when the request has no body


@dataclass(frozen=True)
class Response:
    status: int
    body: object  # anything json.dumps accepts


class HTTPError(Exception):
    """Raise from a handler to answer with an error status and {"error": message}."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
