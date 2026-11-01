"""The chief: a local model (Ollama) that turns the owner's request into a plan.

A 14B model plans badly in free form, so its job is kept small and checkable:
1. outline: features, their routes and the page, as JSON that Ollama forces to a schema;
2. scenarios: per feature, example requests with the expected replies (the hidden tests);
3. plain code normalizes the result and lists its problems (plan.problems); the model gets
   one retry with that list, and whatever is left goes to the owner, who must approve.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from swarm.studio import plan as planmod
from swarm.studio.plan import Feature, Plan

Say = Callable[[str], None]

METHOD = {"type": "string", "enum": list(planmod.METHODS)}
STR = {"type": "string"}

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": STR,
        "summary": STR,
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": STR,
                    "title": STR,
                    "data": STR,
                    "routes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "method": METHOD,
                                "path": STR,
                                "does": STR,
                                "body": STR,
                                "reply": STR,
                                "errors": {"type": "array", "items": STR},
                            },
                            "required": ["method", "path", "does", "body", "reply", "errors"],
                        },
                    },
                },
                "required": ["name", "title", "data", "routes"],
            },
        },
        "ui": {
            "type": "object",
            "properties": {
                "subtitle": STR,
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": list(planmod.SECTION_TYPES)},
                            "title": STR,
                            "action": STR,
                            "source": STR,
                            "fields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": STR,
                                        "label": STR,
                                        "input": {"type": "string", "enum": list(planmod.INPUTS)},
                                    },
                                    "required": ["name", "label", "input"],
                                },
                            },
                            "submit": STR,
                            "items": STR,
                            "text": STR,
                            "toggle_field": STR,
                            "toggle_action": STR,
                            "buttons": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"label": STR, "action": STR},
                                    "required": ["label", "action"],
                                },
                            },
                        },
                        "required": ["type", "title"],
                    },
                },
            },
            "required": ["subtitle", "sections"],
        },
    },
    "required": ["title", "summary", "features", "ui"],
}

SCENARIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": STR,
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "method": METHOD,
                                "path": STR,
                                "body": {},
                                "status": {"type": "integer"},
                                "expect": {},
                            },
                            "required": ["method", "path", "body", "status", "expect"],
                        },
                    },
                },
                "required": ["name", "steps"],
            },
        }
    },
    "required": ["scenarios"],
}

EXAMPLE_OUTLINE = {
    "title": "Reading list",
    "summary": "Books to read, marked as read, with counts.",
    "features": [
        {
            "name": "books",
            "title": "Add and list books",
            "data": 'collection "books"; each item {"id": int, "title": str, "author": str, '
            '"read": bool}',
            "routes": [
                {
                    "method": "POST",
                    "path": "/api/books",
                    "does": "Add a book. A new book is not read.",
                    "body": '{"title": non-empty text, "author": non-empty text}',
                    "reply": '201 {"id": 1, "title": "Dune", "author": "Herbert", "read": false}',
                    "errors": ["400 when title or author is missing or empty"],
                },
                {
                    "method": "GET",
                    "path": "/api/books",
                    "does": "List all books, oldest first.",
                    "body": "",
                    "reply": '200 {"books": [{"id": 1, "title": "Dune", "author": "Herbert", '
                    '"read": false}]}',
                    "errors": [],
                },
            ],
        },
        {
            "name": "book_edit",
            "title": "Mark books read and remove them",
            "data": 'the "books" collection',
            "routes": [
                {
                    "method": "PATCH",
                    "path": "/api/books/{id}",
                    "does": "Set whether the book is read.",
                    "body": '{"read": true or false}',
                    "reply": '200 the whole book, e.g. {"id": 1, "title": "Dune", '
                    '"author": "Herbert", "read": true}',
                    "errors": [
                        "404 when the book does not exist",
                        "400 when read is not true/false",
                    ],
                },
                {
                    "method": "DELETE",
                    "path": "/api/books/{id}",
                    "does": "Remove the book.",
                    "body": "",
                    "reply": '200 {"deleted": 1}',
                    "errors": ["404 when the book does not exist"],
                },
            ],
        },
        {
            "name": "book_stats",
            "title": "Count books",
            "data": 'reads the "books" collection',
            "routes": [
                {
                    "method": "GET",
                    "path": "/api/book-stats",
                    "does": "Count all books, read and unread.",
                    "body": "",
                    "reply": '200 {"total": 3, "read": 1, "unread": 2}',
                    "errors": [],
                }
            ],
        },
    ],
    "ui": {
        "subtitle": "What to read next.",
        "sections": [
            {
                "type": "form",
                "title": "Add a book",
                "action": "POST /api/books",
                "fields": [
                    {"name": "title", "label": "Title", "input": "text"},
                    {"name": "author", "label": "Author", "input": "text"},
                ],
                "submit": "Add",
            },
            {
                "type": "list",
                "title": "Books",
                "source": "GET /api/books",
                "items": "books",
                "text": "title",
                "toggle_field": "read",
                "toggle_action": "PATCH /api/books/{id}",
                "buttons": [{"label": "Remove", "action": "DELETE /api/books/{id}"}],
            },
            {"type": "values", "title": "Counts", "source": "GET /api/book-stats"},
        ],
    },
}

EXAMPLE_SCENARIOS = {
    "scenarios": [
        {
            "name": "mark read then remove",
            "steps": [
                {
                    "method": "POST",
                    "path": "/api/books",
                    "body": {"title": "Dune", "author": "Herbert"},
                    "status": 201,
                    "expect": None,
                },
                {
                    "method": "PATCH",
                    "path": "/api/books/1",
                    "body": {"read": True},
                    "status": 200,
                    "expect": {"id": 1, "title": "Dune", "author": "Herbert", "read": True},
                },
                {
                    "method": "DELETE",
                    "path": "/api/books/1",
                    "body": None,
                    "status": 200,
                    "expect": {"deleted": 1},
                },
                {
                    "method": "GET",
                    "path": "/api/books",
                    "body": None,
                    "status": 200,
                    "expect": {"books": []},
                },
            ],
        },
        {
            "name": "unknown book",
            "steps": [
                {
                    "method": "DELETE",
                    "path": "/api/books/7",
                    "body": None,
                    "status": 404,
                    "expect": None,
                }
            ],
        },
    ]
}

FRAME = """THE FRAME (already built; you cannot change it):
- A JSON web API. Each feature is one small Python file with 1 to 4 routes under /api/.
- Data lives in an in-memory store of named collections. Inserting gives an item an "id":
  1, 2, 3, ... per collection in insert order; deleted ids are never reused; lists come back
  oldest first. The store starts empty.
- Conventions: 201 for a create, 200 otherwise; 400 for bad input; 404 for an unknown id. A
  list route replies {"<collection>": [...]}; create and update reply the whole item; delete
  replies {"deleted": <id>}.
- The page is built from sections. "form": inputs posted as a JSON body to one route.
  "list": the items of a GET route, each with an optional checkbox (toggle_field and
  toggle_action: sends {"<field>": true or false} to a route with the item's {id}) and
  buttons (each calls a route with the item's {id}). "values": the fields of a GET reply."""

OUTLINE_SYSTEM = f"""You are the chief engineer of a small local app builder. You turn the
owner's request into a build plan for a fixed app frame. A small local model implements each
feature from your plan, one at a time, so every detail must be exact and simple.

{FRAME}

RULES:
- 1 to 4 features, in build order. A later feature may use the data of an earlier one.
- Feature names are snake_case. Paths look like /api/books or /api/books/{{id}}, lowercase.
- For every route state exactly: what it does; the body fields and their types ("" if none);
  the success status with an example reply JSON; and each error with its status.
- Keep it as small as the request allows: no users, login, search or paging unless asked.
- Every action and source in the page must be one of your routes, written "METHOD /path".
- Plain ASCII only.

Reply with JSON only. Example for a different app:
{json.dumps(EXAMPLE_OUTLINE, indent=1)}"""

SCENARIO_SYSTEM = f"""You write the example requests for one feature of a planned app. They
become its hidden acceptance tests: each scenario starts with an EMPTY store and sends its
requests in order; the test compares each status, and each reply with "expect".

{FRAME}

RULES:
- 2 or 3 scenarios with 1 to 6 steps each. The first is the normal case. The others cover
  edge cases and the errors the routes list.
- Each step: method; path with real values (/api/books/1, never {{id}}); body (a JSON object,
  or null); status; expect (the reply JSON, or null to check only the status).
- Create data first by calling the routes of this feature or of the earlier features listed.
- "expect" follows the route's reply description exactly, with the ids the store gives.
- Error steps: expect null. Plain ASCII only.

Reply with JSON only. Example for a book_edit feature:
{json.dumps(EXAMPLE_SCENARIOS, indent=1)}"""


class ChiefError(Exception):
    pass


class Ollama:
    def __init__(self, base: str, model: str, num_ctx: int, num_predict: int) -> None:
        self.base = base.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    def chat_json(
        self, system: str, user: str, schema: dict[str, Any], progress: Say | None = None
    ) -> Any:
        """One structured-output chat call, streamed so the UI can show progress."""
        payload = {
            "model": self.model,
            "stream": True,
            "format": schema,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {
                "num_ctx": self.num_ctx,  # equal to the worker's, so nothing reloads
                "num_predict": self.num_predict,
                "temperature": 0.2,
            },
        }
        request = urllib.request.Request(
            f"{self.base}/api/chat",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        parts: list[str] = []
        started, shown = time.monotonic(), 0.0
        try:
            with urllib.request.urlopen(request, timeout=900) as resp:
                for raw in resp:
                    if not raw.strip():
                        continue
                    chunk = json.loads(raw)
                    if chunk.get("error"):
                        raise ChiefError(f"Ollama: {chunk['error']}")
                    parts.append(chunk.get("message", {}).get("content", ""))
                    now = time.monotonic()
                    if progress and now - shown > 1.0:
                        shown = now
                        progress(f"{sum(map(len, parts))} characters, {now - started:.0f}s")
                    if chunk.get("done"):
                        if chunk.get("done_reason") == "length":
                            raise ChiefError(
                                f"the plan did not fit in {self.num_predict} tokens; ask for less"
                            )
                        break
        except (urllib.error.URLError, OSError) as exc:
            raise ChiefError(f"Ollama is not reachable at {self.base} ({exc})") from None
        text = "".join(parts)
        try:
            return json.loads(text)
        except ValueError:
            raise ChiefError(f"the model's reply is not JSON: {text[:300]}") from None


def _route_line(r: planmod.RouteSpec) -> str:
    line = f"{r.key}: {r.does}"
    if r.body:
        line += f" Body: {r.body}."
    line += f" Reply: {r.reply}."
    for e in r.errors:
        line += f" Error: {e}."
    return line


def _feature_text(f: Feature) -> str:
    lines = [f"feature {f.name} ({f.title}); data: {f.data}"]
    lines += [f"  {_route_line(r)}" for r in f.routes]
    return "\n".join(lines)


def _outline_dict(plan: Plan) -> dict[str, Any]:
    raw = plan.to_dict()
    for f in raw["features"]:
        f.pop("scenarios", None)
    raw.pop("version", None)
    return raw


class Chief:
    def __init__(self, llm: Ollama) -> None:
        self.llm = llm
        self.label = f"{llm.model} (Ollama)"

    def draft(
        self,
        request: str,
        previous: Plan | None,
        comment: str | None,
        built: list[Feature],
        progress: Say,
    ) -> Plan:
        plan = self._outline(request, previous, comment, built, progress)
        reuse = {f.name: f for f in (previous.features if previous else [])}
        built_names = {f.name for f in built}
        new = [f for f in plan.features if f.name not in built_names]
        for n, f in enumerate(new, 1):
            old = reuse.get(f.name)
            if (
                old
                and old.routes == f.routes
                and old.scenarios
                and not comment_mentions(comment, f)
            ):
                f.scenarios = old.scenarios
                continue
            label = f"examples for {f.name} ({n}/{len(new)})"
            f.scenarios = self._scenarios(plan, f, None, _prefixed(progress, label))
            found = planmod.scenario_problems(plan, f)
            if found:
                retry = _prefixed(progress, f"retrying {label}")
                f.scenarios = self._scenarios(plan, f, found, retry)
        return plan

    def _outline(
        self,
        request: str,
        previous: Plan | None,
        comment: str | None,
        built: list[Feature],
        progress: Say,
    ) -> Plan:
        user = [f"The owner's request:\n{request.strip()}"]
        if built:
            user.append(
                "These features are already built. Copy them unchanged, first and in this order; "
                "you may only add features after them and change the page:\n"
                + json.dumps(_outline_dict(Plan("", "", built, {}))["features"], indent=1)
            )
        if previous and comment:
            user.append(
                "Your previous plan:\n"
                + json.dumps(_outline_dict(previous), indent=1)
                + f"\n\nThe owner asks for these changes:\n{comment.strip()}\n"
                "Reply with the whole revised plan."
            )
        raw = self.llm.chat_json(
            OUTLINE_SYSTEM, "\n\n".join(user), OUTLINE_SCHEMA, lambda m: progress(f"outline: {m}")
        )
        plan = self._with_built(planmod.plan_from_dict(raw), built)
        found = _outline_problems(plan, built)
        if found:
            user.append(
                "Your plan:\n"
                + json.dumps(_outline_dict(plan), indent=1)
                + "\n\nIt has these problems. Reply with the whole plan, fixed:\n"
                + "\n".join(f"- {p}" for p in found)
            )
            raw = self.llm.chat_json(
                OUTLINE_SYSTEM,
                "\n\n".join(user),
                OUTLINE_SCHEMA,
                lambda m: progress(f"fixing the outline: {m}"),
            )
            plan = self._with_built(planmod.plan_from_dict(raw), built)
        return plan

    @staticmethod
    def _with_built(plan: Plan, built: list[Feature]) -> Plan:
        """Built features are fixed: whatever the model wrote for them, keep the originals."""
        names = {f.name for f in built}
        plan.features = list(built) + [f for f in plan.features if f.name not in names]
        return plan

    def _scenarios(
        self, plan: Plan, feature: Feature, problems: list[str] | None, progress: Say
    ) -> list[planmod.Scenario]:
        index = plan.features.index(feature)
        earlier = plan.features[:index]
        user = [f"The app: {plan.title}. {plan.summary}", "Write the scenarios for this feature:"]
        user.append(_feature_text(feature))
        if earlier:
            user.append("Earlier features (their routes may set up data):")
            user += [_feature_text(f) for f in earlier]
        if problems:
            user.append(
                "Your previous scenarios:\n"
                + json.dumps(
                    [
                        {"name": s.name, "steps": [vars(st) for st in s.steps]}
                        for s in feature.scenarios
                    ]
                )
                + "\nThey have these problems; reply with all scenarios, fixed:\n"
                + "\n".join(f"- {p}" for p in problems)
            )
        raw = self.llm.chat_json(SCENARIO_SYSTEM, "\n".join(user), SCENARIO_SCHEMA, progress)
        return planmod.scenarios_from_list(raw.get("scenarios") if isinstance(raw, dict) else [])


class FilePlanner:
    """Skips the model: every draft is the plan in a JSON file (`swarm studio --plan-file`).
    For trying the studio, or a plan you wrote yourself, without the GPU."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.label = f"plan file {path.name}"

    def draft(
        self,
        request: str,
        previous: Plan | None,
        comment: str | None,
        built: list[Feature],
        progress: Say,
    ) -> Plan:
        progress(f"reading {self.path.name}")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return Chief._with_built(planmod.plan_from_dict(raw), built)


def _prefixed(progress: Say, label: str) -> Say:
    return lambda message: progress(f"{label}: {message}")


def comment_mentions(comment: str | None, feature: Feature) -> bool:
    if not comment:
        return False
    text = comment.lower()
    return feature.name.lower() in text or feature.name.replace("_", " ") in text


def _outline_problems(plan: Plan, built: list[Feature]) -> list[str]:
    """Problems an outline can have before any scenario exists."""
    return [p for p in planmod.problems(plan, {f.name for f in built}) if "scenario" not in p]
