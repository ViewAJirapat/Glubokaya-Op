"""The plan artifact: what the chief proposes and the owner approves, and the code generated
from it.

A plan is data (features, routes, example scenarios, page sections). Everything the worker
gets is generated from it by plain code, never written by a model:
- a stub handler.py per feature whose CONTRACT docstring is the spec;
- task.toml (the worker's prompt, scope and files);
- hidden acceptance tests, built from the scenarios (request -> expected status and reply);
- ui.json, the page the frame's shell renders.
"""

from __future__ import annotations

import json
import re
import textwrap
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
SECTION_TYPES = ("form", "list", "values")
INPUTS = ("text", "number", "checkbox")
FEATURES_DIR = "src/webapp/features"
FRAME_ROUTES = [("GET", "/api/health"), ("GET", "/api/_app")]
RESERVED = {"health", "core", "app", "static", "features", "webapp", "test", "tests"}
MAX_FEATURES = 6
MAX_ROUTES = 4  # one handler file of ~120 lines: the size the 14B handles reliably
MAX_SCENARIOS = 5
MAX_STEPS = 10
WIDTH = 92  # docstring lines; ruff allows 100

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_SEGMENT = re.compile(r"^(?:[a-z0-9][a-z0-9_-]*|\{[a-z][a-z0-9_]*\})$")
_ASCII = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        " ": " ",
        "→": "->",
    }
)


def ascii_text(text: str) -> str:
    """Specs and tested strings stay ASCII: a model may rewrite a fancy character in its
    answer, and a test expecting the original would then fail."""
    text = text.translate(_ASCII)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # TOML refuses the other control characters, DEL (U+007F) included
    return "".join(c for c in text if c in "\n\t" or 32 <= ord(c) < 127)


def _ascii_value(value: Any) -> Any:
    if isinstance(value, str):
        return ascii_text(value)
    if isinstance(value, list):
        return [_ascii_value(v) for v in value]
    if isinstance(value, dict):
        return {ascii_text(str(k)): _ascii_value(v) for k, v in value.items()}
    return value


def snake(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", ascii_text(text).lower()).strip("_")
    return out if out[:1].isalpha() else f"f_{out}"


def _path(text: str) -> str:
    """'todos/{id}/' or '/todos/{id}' -> '/api/todos/{id}'."""
    path = "/" + ascii_text(text).strip().strip("/")
    path = path.split("?", 1)[0]
    return path if path.startswith("/api/") or path == "/api" else "/api" + path


def _action(text: str) -> str:
    """'patch /todos/{id}' -> 'PATCH /api/todos/{id}'."""
    parts = ascii_text(text).strip().split(None, 1)
    if len(parts) != 2:
        return ascii_text(text).strip()
    return f"{parts[0].upper()} {_path(parts[1])}"


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def path_matches(pattern: str, path: str) -> bool:
    want, got = _segments(pattern), _segments(path)
    return len(want) == len(got) and all(
        (_is_param(w) and bool(g)) or w == g for w, g in zip(want, got, strict=True)
    )


def _shape(pattern: str) -> str:
    return "/".join("{}" if _is_param(s) else s for s in _segments(pattern))


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(", ", ": "))


@dataclass
class RouteSpec:
    method: str
    path: str
    does: str
    body: str = ""  # request body, e.g. '{"title": non-empty text}'; "" for none
    reply: str = ""  # success reply, e.g. '201 {"id": 1, "title": "Buy milk", "done": false}'
    errors: list[str] = field(default_factory=list)  # e.g. "404 when the todo does not exist"

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def function(self) -> str:
        parts = [self.method.lower()]
        for seg in _segments(self.path)[1:]:
            parts.append(f"by_{seg[1:-1]}" if _is_param(seg) else seg.replace("-", "_"))
        return "_".join(parts)


@dataclass
class Step:
    method: str
    path: str
    status: int
    body: Any = None
    expect: Any = None  # a subset of the reply; None checks only the status

    def describe(self) -> str:
        line = f"{self.method} {self.path}"
        if self.body is not None:
            line += f" {_compact(self.body)}"
        line += f" -> {self.status}"
        if self.expect is not None:
            line += f" {_compact(self.expect)}"
        return line


@dataclass
class Scenario:
    name: str
    steps: list[Step]


@dataclass
class Feature:
    name: str
    title: str
    data: str  # which store collection it uses and the item fields
    routes: list[RouteSpec]
    scenarios: list[Scenario] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return f"{FEATURES_DIR}/{self.name}"

    @property
    def handler(self) -> str:
        return f"{self.folder}/handler.py"


@dataclass
class Plan:
    title: str
    summary: str
    features: list[Feature]
    ui: dict[str, Any]  # {"subtitle": str, "sections": [...]}, as in the frame's ui.json
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def feature(self, name: str) -> Feature | None:
        return next((f for f in self.features if f.name == name), None)

    def ui_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.ui.get("subtitle", self.summary),
            "sections": self.ui.get("sections", []),
        }


# ---------------------------------------------------------------------------
# From model output (or a saved plan) to a Plan, fixing what is safe to fix
# ---------------------------------------------------------------------------


def _str(raw: dict[str, Any], key: str, default: str = "") -> str:
    value = raw.get(key, default)
    return ascii_text(value if isinstance(value, str) else default).strip()


def _route(raw: dict[str, Any]) -> RouteSpec:
    errors = raw.get("errors", [])
    return RouteSpec(
        method=_str(raw, "method").upper(),
        path=_path(_str(raw, "path")),
        does=_str(raw, "does"),
        body=_str(raw, "body"),
        reply=_str(raw, "reply"),
        errors=[ascii_text(str(e)).strip() for e in errors if str(e).strip()]
        if isinstance(errors, list)
        else [],
    )


def _status(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_text(value: Any) -> Any:
    """A model sometimes writes JSON as a string ('{"id": 1}'); parse that, keep the rest."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def step_from_dict(raw: dict[str, Any]) -> Step:
    body = _json_text(raw.get("body"))
    if body in ({}, "", "null"):
        body = None
    expect = _json_text(raw.get("expect"))
    if expect in ("", "null"):
        expect = None
    return Step(
        method=_str(raw, "method").upper(),
        path=ascii_text(str(raw.get("path", ""))).strip(),
        status=_status(raw.get("status")),
        body=_ascii_value(body),
        expect=_ascii_value(expect),
    )


def scenarios_from_list(raw: Any) -> list[Scenario]:
    out: list[Scenario] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        steps = [step_from_dict(s) for s in item.get("steps", []) if isinstance(s, dict)]
        for s in steps:
            if not s.path.startswith("/api"):
                s.path = _path(s.path)
        out.append(Scenario(name=_str(item, "name") or f"scenario {len(out) + 1}", steps=steps))
    return out


def _section(raw: dict[str, Any]) -> dict[str, Any]:
    kind = _str(raw, "type").lower()
    section: dict[str, Any] = {"type": kind, "title": _str(raw, "title") or kind.title()}
    if kind == "form":
        section["action"] = _action(_str(raw, "action"))
        fields = []
        for f in raw.get("fields", []) if isinstance(raw.get("fields"), list) else []:
            if isinstance(f, dict) and _str(f, "name"):
                kind_in = _str(f, "input", "text").lower()
                fields.append(
                    {
                        "name": _str(f, "name"),
                        "label": _str(f, "label") or _str(f, "name"),
                        "input": kind_in if kind_in in INPUTS else "text",
                    }
                )
        section["fields"] = fields
        section["submit"] = _str(raw, "submit") or "Save"
    else:
        section["source"] = _action(_str(raw, "source"))
    if kind == "list":
        if _str(raw, "items"):
            section["items"] = _str(raw, "items")
        section["text"] = _str(raw, "text") or "id"
        toggle = raw.get("toggle")
        field_name = _str(raw, "toggle_field")
        action = _str(raw, "toggle_action")
        if isinstance(toggle, dict):  # the nested form, as stored in ui.json
            field_name, action = _str(toggle, "field"), _str(toggle, "action")
        if field_name and action:
            section["toggle"] = {"field": field_name, "action": _action(action)}
        buttons = raw.get("buttons", [])
        section["buttons"] = [
            {"label": _str(b, "label") or "Go", "action": _action(_str(b, "action"))}
            for b in (buttons if isinstance(buttons, list) else [])
            if isinstance(b, dict) and _str(b, "action")
        ]
    return section


def plan_from_dict(raw: dict[str, Any]) -> Plan:
    """Accepts both the chief's outline (plus scenarios) and a saved Plan.to_dict()."""
    features: list[Feature] = []
    for f in raw.get("features", []) if isinstance(raw.get("features"), list) else []:
        if not isinstance(f, dict):
            continue
        routes = [_route(r) for r in f.get("routes", []) if isinstance(r, dict)]
        features.append(
            Feature(
                name=snake(_str(f, "name") or _str(f, "title") or "feature"),
                title=_str(f, "title") or _str(f, "name"),
                data=_str(f, "data"),
                routes=routes,
                scenarios=scenarios_from_list(f.get("scenarios", [])),
            )
        )
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    assert isinstance(ui, dict)
    sections = [_section(s) for s in ui.get("sections", []) if isinstance(s, dict)]
    return Plan(
        title=_str(raw, "title") or "My app",
        summary=_str(raw, "summary"),
        features=features,
        ui={"subtitle": _str(ui, "subtitle"), "sections": sections},
        version=_status(raw.get("version")) or 1,
    )


# ---------------------------------------------------------------------------
# Checks: problems the owner (or the chief, on a retry) must fix before approval
# ---------------------------------------------------------------------------


def _route_problems(plan: Plan, built: set[str]) -> list[str]:
    problems: list[str] = []
    seen_names: set[str] = set()
    shapes: dict[tuple[str, str], str] = {(m, _shape(p)): "the frame" for m, p in FRAME_ROUTES}
    if not plan.features:
        problems.append("the plan has no features")
    if len(plan.features) > MAX_FEATURES:
        problems.append(f"too many features ({len(plan.features)}); at most {MAX_FEATURES}")
    for f in plan.features:
        where = f"feature {f.name!r}"
        if not _NAME.match(f.name) or f.name in RESERVED:
            problems.append(
                f"{where}: the name must be snake_case and not one of {sorted(RESERVED)}"
            )
        if f.name in seen_names:
            problems.append(f"{where}: two features have this name")
        seen_names.add(f.name)
        if not f.routes:
            problems.append(f"{where}: no routes")
        if len(f.routes) > MAX_ROUTES:
            problems.append(f"{where}: {len(f.routes)} routes; split it (at most {MAX_ROUTES})")
        functions: dict[str, str] = {}
        for r in f.routes:
            if r.function in functions:
                problems.append(
                    f"{where}: {functions[r.function]} and {r.key} would both be function "
                    f"{r.function}; change one path"
                )
            functions[r.function] = r.key
            if r.method not in METHODS:
                problems.append(f"{where}: unknown method {r.method!r}")
            segs = _segments(r.path)
            if segs[0] != "api" or len(segs) < 2 or not all(_SEGMENT.match(s) for s in segs[1:]):
                problems.append(
                    f"{where}: bad path {r.path!r} (use /api/name or /api/name/{{id}}, "
                    "lowercase, no query string)"
                )
            if segs[1:2] == ["_app"]:
                problems.append(f"{where}: /api/_app belongs to the frame")
            key = (r.method, _shape(r.path))
            if key in shapes:
                problems.append(f"{where}: {r.key} clashes with {shapes[key]}")
            shapes[key] = where
            if not r.does:
                problems.append(f"{where}: {r.key} has no description")
            if not r.reply:
                problems.append(f"{where}: {r.key} does not say what it replies")
    problems += [p for p in _built_problems(plan, built)]
    return problems


def _built_problems(plan: Plan, built: set[str]) -> list[str]:
    names = {f.name for f in plan.features}
    return [f"feature {b!r} is already built and can't be removed" for b in built - names]


def _call_problems(plan: Plan, where: str, action: str, allowed: list[RouteSpec]) -> list[str]:
    parts = action.split(" ", 1)
    if len(parts) != 2 or parts[0] not in METHODS:
        return [f"{where}: bad action {action!r} (use 'METHOD /api/path')"]
    if not any(r.key == action for r in allowed):
        return [f"{where}: {action!r} is not a planned route"]
    return []


def _ui_problems(plan: Plan) -> list[str]:
    routes = [r for f in plan.features for r in f.routes]
    problems: list[str] = []
    sections = plan.ui.get("sections", [])
    if not sections:
        problems.append("the page has no sections")
    for n, s in enumerate(sections, 1):
        where = f"page section {n} ({s.get('title', '')!r})"
        kind = s.get("type")
        if kind not in SECTION_TYPES:
            problems.append(f"{where}: type must be one of {SECTION_TYPES}")
            continue
        main = "action" if kind == "form" else "source"
        problems += _call_problems(plan, where, str(s.get(main, "")), routes)
        if kind != "form" and not str(s.get(main, "")).startswith("GET "):
            problems.append(f"{where}: the source must be a GET route")
        if kind == "form" and not s.get("fields"):
            problems.append(f"{where}: a form needs at least one field")
        toggle = s.get("toggle")
        if isinstance(toggle, dict):
            problems += _call_problems(plan, f"{where} toggle", str(toggle.get("action")), routes)
        for b in s.get("buttons", []):
            problems += _call_problems(plan, f"{where} button", str(b.get("action")), routes)
    return problems


def scenario_problems(plan: Plan, feature: Feature) -> list[str]:
    """Steps may call this feature's routes and the routes of features planned before it."""
    index = plan.features.index(feature)
    earlier = [r for f in plan.features[:index] for r in f.routes]
    own = feature.routes
    usable = [(m, p) for m, p in FRAME_ROUTES] + [(r.method, r.path) for r in earlier + own]
    where = f"feature {feature.name!r}"
    problems: list[str] = []
    if not feature.scenarios:
        problems.append(f"{where}: no example scenarios (the hidden tests come from them)")
    if len(feature.scenarios) > MAX_SCENARIOS:
        problems.append(f"{where}: {len(feature.scenarios)} scenarios; at most {MAX_SCENARIOS}")
    for sc in feature.scenarios:
        label = f"{where} scenario {sc.name!r}"
        if not sc.steps:
            problems.append(f"{label}: no steps")
        if len(sc.steps) > MAX_STEPS:
            problems.append(f"{label}: {len(sc.steps)} steps; at most {MAX_STEPS}")
        hits_own = False
        for n, st in enumerate(sc.steps, 1):
            if not any(st.method == m and path_matches(p, st.path) for m, p in usable):
                problems.append(
                    f"{label} step {n}: {st.method} {st.path} matches no route of this "
                    "feature or an earlier one"
                )
            if any(st.method == r.method and path_matches(r.path, st.path) for r in own):
                hits_own = True
            if not 100 <= st.status <= 599:
                problems.append(f"{label} step {n}: status {st.status} is not an HTTP status")
            if st.body is not None and not isinstance(st.body, dict):
                problems.append(f"{label} step {n}: the body must be a JSON object or null")
            if st.expect is not None and not isinstance(st.expect, dict | list):
                problems.append(
                    f"{label} step {n}: expect must be the reply JSON (an object or a list) "
                    "or null, not text"
                )
            if any(_is_param(s) for s in _segments(st.path)):
                problems.append(f"{label} step {n}: use a real value in {st.path}, not a {{param}}")
        if sc.steps and not hits_own:
            problems.append(f"{label}: no step calls this feature's own routes")
    return problems


def _length_problems(feature: Feature) -> list[str]:
    """The frame's ruff allows 100 characters a line and can't fix longer ones."""
    longest = max((stub_source(feature) + init_source(feature)).splitlines(), key=len, default="")
    if len(longest) <= 100:
        return []
    return [
        f"feature {feature.name!r}: a generated line is {len(longest)} characters long "
        f"(at most 100): shorten the title, paths or texts ({longest.strip()[:60]}...)"
    ]


def problems(plan: Plan, built: set[str] | None = None) -> list[str]:
    out = _route_problems(plan, built or set()) + _ui_problems(plan)
    for f in plan.features:
        out += scenario_problems(plan, f)
        out += _length_problems(f)
    return out


# ---------------------------------------------------------------------------
# Generated files
# ---------------------------------------------------------------------------


def _wrap(text: str, indent: str = "") -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        lines += textwrap.wrap(
            para,
            WIDTH,
            initial_indent=indent,
            subsequent_indent=indent + "    ",
            break_long_words=False,
            break_on_hyphens=False,
        ) or [indent.rstrip()]
    return lines


def spec_lines(feature: Feature) -> list[str]:
    """The feature's spec: the CONTRACT docstring body and the core of the worker prompt."""
    lines: list[str] = []
    if feature.data:
        lines += _wrap(f"Data: {feature.data}")
        lines.append("")
    for r in feature.routes:
        lines += _wrap(f"{r.key} -> function {r.function}")
        lines += _wrap(r.does, "  ")
        if r.body:
            lines += _wrap(f"Body: {r.body}", "  ")
        lines += _wrap(f"Reply: {r.reply}", "  ")
        for e in r.errors:
            lines += _wrap(f"Error: {e}", "  ")
    if feature.scenarios:
        lines += ["", "Example (the hidden tests check more cases like it):"]
        for st in feature.scenarios[0].steps:
            lines += _wrap(st.describe(), "  ")
    return lines


def _docstring_safe(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"""', "'''")


def stub_source(feature: Feature) -> str:
    doc = [*_wrap(f"{feature.name}: {feature.title}.".replace("..", ".")), ""]
    doc += ["CONTRACT (keep this docstring unchanged; implement exactly this):"]
    doc += spec_lines(feature)
    body = "\n".join(line.rstrip() for line in doc)
    out = [f'"""{_docstring_safe(body)}\n"""', ""]
    out.append("from webapp.core.api import HTTPError, Request, Response, route")
    for r in feature.routes:
        out += [
            "",
            "",
            f'@route("{r.method}", "{r.path}")',
            f"def {r.function}(request: Request) -> Response:",
            '    raise HTTPError(501, "not implemented")',
        ]
    return "\n".join(out) + "\n"


def init_source(feature: Feature) -> str:
    lines = _wrap(_docstring_safe(f"{feature.title}.".replace("..", ".")))
    if len(lines) == 1:
        return f'"""{lines[0]}"""\n'
    return '"""' + "\n".join(lines) + '\n"""\n'


def task_id(order: int, feature: Feature) -> str:
    return f"{order:02d}-{feature.name.replace('_', '-')}"


def task_prompt(feature: Feature) -> str:
    fns = ", ".join(f"`{r.function}`" for r in feature.routes)
    head = (
        f"Implement the feature `{feature.name}` in `{feature.handler}`. The file is a stub: "
        "keep its CONTRACT docstring unchanged at the top of the file, keep its @route lines "
        f"and the function names {fns}, and replace each "
        '`raise HTTPError(501, "not implemented")` with the implementation below.'
    )
    tail = (
        "Status codes, JSON keys, values and list order must match exactly; extra keys in a "
        "reply are allowed. Error message texts are not tested. Use `request.store` for all "
        "data (see `src/webapp/core/store.py`) and the request helpers in "
        "`src/webapp/core/http.py`."
    )
    return "\n".join([head, "", *spec_lines(feature), "", tail])


def _toml_str(text: str) -> str:
    return json.dumps(text)  # a JSON string is a valid TOML basic string


def _toml_multiline(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"""', "'''")


def task_toml(feature: Feature) -> str:
    files = [feature.handler, f"{FEATURES_DIR}/health/handler.py"]
    return "\n".join(
        [
            f"title = {_toml_str(feature.title)}",
            'type = "feat"',
            f"files = [{', '.join(_toml_str(f) for f in files)}]",
            f"scope = [{_toml_str(feature.folder + '/**')}]",
            f'prompt = """\n{_toml_multiline(task_prompt(feature))}\n"""',
            "",
        ]
    )


def _test_id(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "scenario"
    out, n = base, 2
    while out in used:
        out, n = f"{base}-{n}", n + 1
    used.add(out)
    return out


_ACCEPTANCE = '''"""Hidden acceptance tests for feature {name!r}, generated by Swarm Studio from the
approved plan. Each scenario starts a fresh app and runs its requests in order."""

import json
import math
from typing import Any

import pytest

from webapp.app import create_app

SCENARIOS: list[dict[str, Any]] = json.loads({scenarios!r})


def _short(value: Any) -> str:
    text = json.dumps(value, default=repr)
    return text if len(text) <= 300 else text[:300] + "..."


def _mismatches(expected: Any, actual: Any, where: str) -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{{where}}: expected an object, got {{_short(actual)}}"]
        out: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{{where}}.{{key}}: missing")
            else:
                out += _mismatches(value, actual[key], f"{{where}}.{{key}}")
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{{where}}: expected a list, got {{_short(actual)}}"]
        if len(expected) != len(actual):
            return [f"{{where}}: expected {{len(expected)}} items, got {{len(actual)}}"]
        out = []
        for i, (e, a) in enumerate(zip(expected, actual)):
            out += _mismatches(e, a, f"{{where}}[{{i}}]")
        return out
    if isinstance(expected, bool) or isinstance(actual, bool):
        same = expected is actual
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        same = math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-9)
    else:
        same = expected == actual
    return [] if same else [f"{{where}}: expected {{_short(expected)}}, got {{_short(actual)}}"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids={ids!r})
def test_scenario(scenario: dict[str, Any]) -> None:
    app = create_app()
    for n, step in enumerate(scenario["steps"], 1):
        resp = app.handle(step["method"], step["path"], step.get("body"), {{}})
        problems = []
        if resp.status != step["status"]:
            problems.append(f"status: expected {{step['status']}}, got {{resp.status}}")
        try:  # judge the JSON the app serves, not the Python object
            body = json.loads(json.dumps(resp.body))
        except (TypeError, ValueError) as exc:
            body = None
            problems.append(f"the reply is not JSON-serializable: {{exc}}")
        if step.get("expect") is not None:
            problems += _mismatches(step["expect"], body, "reply")
        request = f"{{step['method']}} {{step['path']}}"
        if step.get("body") is not None:
            request += " " + _short(step["body"])
        assert not problems, (
            f"step {{n}}: {{request}}\\n"
            f"  got      {{resp.status}} {{_short(resp.body)}}\\n"
            f"  expected {{step['status']}} {{_short(step.get('expect'))}}\\n  "
            + "; ".join(problems)
        )
'''


def acceptance_source(feature: Feature) -> str:
    used: set[str] = set()
    ids = [_test_id(sc.name, used) for sc in feature.scenarios]
    data = [{"name": sc.name, "steps": [asdict(st) for st in sc.steps]} for sc in feature.scenarios]
    return _ACCEPTANCE.format(name=feature.name, scenarios=json.dumps(data), ids=ids)


def render_plan(plan: Plan) -> str:
    """The plan as Markdown, for the chat and the plan file in the app repo."""
    out = [f"# {plan.title}", "", plan.summary, "", "## Features (built in this order)"]
    for n, f in enumerate(plan.features, 1):
        out += ["", f"### {n}. {f.name}: {f.title}", "", "```", *spec_lines(f), "```"]
        for sc in f.scenarios[1:]:
            out += ["", f"Hidden check: {sc.name}", "```"]
            out += [st.describe() for st in sc.steps]
            out.append("```")
    out += ["", "## Page", "", "```json", json.dumps(plan.ui_json(), indent=2), "```", ""]
    return "\n".join(out)
