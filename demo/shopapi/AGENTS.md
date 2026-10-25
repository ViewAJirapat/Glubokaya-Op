# Agent rules for shopapi

You are working in an isolated git worktree. A harness gives you one task at a time,
checks your work with automated gates and hidden tests, and commits it only if
everything passes. The task prompt lists the exact verification commands, import rules and
the paths your task may change.

## How a feature is added
- One feature = one folder: `src/shopapi/features/<name>/` with an `__init__.py` (a one-line
  docstring; without it the app refuses to start) and a `handler.py`. Helpers for the
  feature go in the same folder.
- In `handler.py`, mark each handler with `@route("GET", "/path/{param}")`. The app finds the
  folder on startup. Never register a feature anywhere else.
- Import only from `shopapi.core.api` (`route`, `Request`, `Response`, `HTTPError`) and from
  your own feature folder. Never import another feature, and don't add modules outside
  your feature folder.
- Read `src/shopapi/features/products/handler.py` first: copy its style.

## Request and response
- `request.params`: the `{param}` path segments, as strings.
- `request.query`: the query string parsed into a dict of strings (`?a=1` -> `{"a": "1"}`).
- `request.body`: the parsed JSON body, or `None` when there is none.
- Return `Response(status, body)` where body is JSON-serializable. For client errors,
  `raise HTTPError(400, "message")`; the app turns it into `{"error": "message"}`.

## Do
- Make the smallest change that fully solves the task.
- Keep code fully typed (mypy --strict) and formatted (ruff format, line length 100).
- Handle every edge case the task statement lists; hidden tests check them.

## Don't
- Don't edit anything outside your feature folder: `core/`, `app.py`, the package's
  `__init__.py`/`__main__.py`, other features, tests and tool configuration are off limits,
  and the harness reverts those edits.
- Don't silence checks: no `# type: ignore`, `# noqa`, skip or xfail markers. They block the change.
- Don't add dependencies. Standard library only.
- Don't run git commands that commit, push or switch branches.
