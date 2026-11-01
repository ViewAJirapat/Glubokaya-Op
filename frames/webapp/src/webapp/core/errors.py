"""The one exception handlers raise on purpose."""


class HTTPError(Exception):
    """Raise from a handler to answer with an error status and {"error": message}."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
