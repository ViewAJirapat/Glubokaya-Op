from webapp.core.api import Request, Response, route


@route("GET", "/api/todo-stats")
def get_todo_stats(request: Request) -> Response:
    todos = request.store.all("todos")
    done = sum(1 for t in todos if t.get("done"))
    return Response(200, {"total": len(todos), "done": done, "open": len(todos) - done})
