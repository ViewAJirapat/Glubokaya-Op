from shopapi.core.api import Request, Response, route


@route("GET", "/health")
def health(request: Request) -> Response:
    return Response(200, {"status": "ok"})
