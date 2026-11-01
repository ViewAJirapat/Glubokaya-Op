from webapp.core.api import HTTPError, Request, Response, route


@route("PATCH", "/api/todos/{id}")
def patch_todos_by_id(request: Request) -> Response:
    todo_id = request.param_int("id")
    request.store.require("todos", todo_id)
    item = request.store.update("todos", todo_id, {"done": request.flag("done")})
    if item is None:
        raise HTTPError(404, "no such todo")
    return Response(200, item)


@route("DELETE", "/api/todos/{id}")
def delete_todos_by_id(request: Request) -> Response:
    todo_id = request.param_int("id")
    if not request.store.delete("todos", todo_id):
        raise HTTPError(404, "no such todo")
    return Response(200, {"deleted": todo_id})
