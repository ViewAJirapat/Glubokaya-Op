from webapp.core.api import Request, Response, route


@route("GET", "/api/health")
def health(request: Request) -> Response:
    return Response(200, {"status": "ok"})
