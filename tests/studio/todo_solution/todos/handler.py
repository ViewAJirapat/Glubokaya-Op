from webapp.core.api import Request, Response, route


@route("POST", "/api/todos")
def post_todos(request: Request) -> Response:
    item = request.store.insert("todos", {"title": request.text("title"), "done": False})
    return Response(201, item)


@route("GET", "/api/todos")
def get_todos(request: Request) -> Response:
    return Response(200, {"todos": request.store.all("todos")})
