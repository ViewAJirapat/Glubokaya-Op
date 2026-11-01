# Agent rules for webapp

You work in an isolated git worktree. A harness gives you one task, checks your work with
automated gates and hidden tests, and commits it only if everything passes.

## The app
- A JSON web API. Each feature is one folder `src/webapp/features/<name>/` with an
  `__init__.py` and a `handler.py`. The app finds it at startup; never register it elsewhere.
- Your task's `handler.py` already exists as a stub. Keep its CONTRACT docstring at the top
  exactly as it is, keep every `@route(...)` line and function name, and replace each
  `raise HTTPError(501, "not implemented")` with the real code.
- Import only from `webapp.core.api`: `route`, `Request`, `Response`, `HTTPError`, `Item`.
  Never import another feature.

## Request, response, data
- `request.param_int("id")`: the `{id}` path segment as an int (404 when it is not one).
- `request.text("title")`: a body field that must be a non-empty string; returns it stripped
  (400 otherwise). `request.flag("done")`: a body field that must be true or false (400
  otherwise). `request.number("amount")`: an int or float (400 otherwise).
  `request.has("title")`: whether the body has that field.
- `request.store`: the app's data. `insert(collection, item)` returns the item with a new
  `"id"` (1, 2, 3, ...); `all(collection)` lists items oldest first; `get(collection, id)`
  returns the item or None; `require(collection, id)` returns it or raises 404;
  `update(collection, id, changes)` returns the changed item or None; `delete(collection, id)`
  returns True if it existed; `count(collection)`.
- Return `Response(status, body)` with a JSON-serializable body. For client errors,
  `raise HTTPError(400, "message")`; the app answers `{"error": "message"}`.

## Do
- Make the smallest change that fully solves the task. Copy the style of
  `src/webapp/features/health/handler.py`.
- Keep code fully typed (mypy --strict) and formatted (ruff, line length 100).
- Handle every case the CONTRACT lists; hidden tests check them.

## Don't
- Don't edit anything outside your feature folder: the harness reverts it.
- Don't silence checks: no `# type: ignore`, `# noqa`, skip or xfail markers.
- Don't add dependencies. Standard library only.
- Don't run git commands that commit, push or switch branches.
