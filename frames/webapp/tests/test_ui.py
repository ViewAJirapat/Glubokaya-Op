"""ui.json (written by Swarm Studio) only uses routes the app really has."""

import re

from webapp.app import create_app, load_ui

KINDS = {"form": "action", "list": "source", "values": "source"}
ACTION = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/api/[^ ]*)$")


def _calls(section: dict[str, object]) -> list[str]:
    calls = [str(section[k]) for k in ("action", "source") if k in section]
    toggle = section.get("toggle")
    if isinstance(toggle, dict):
        calls.append(str(toggle.get("action")))
    buttons = section.get("buttons")
    if isinstance(buttons, list):
        calls += [str(b.get("action")) for b in buttons if isinstance(b, dict)]
    return calls


def test_sections_use_existing_routes() -> None:
    ui = load_ui()
    assert isinstance(ui.get("title"), str)
    routes = {f"{r.method} {r.pattern}" for r in create_app().routes}
    problems: list[str] = []
    for n, section in enumerate(ui.get("sections", []), 1):
        kind = section.get("type")
        if kind not in KINDS:
            problems.append(f"section {n}: unknown type {kind!r}")
            continue
        if KINDS[kind] not in section:
            problems.append(f"section {n} ({kind}): missing {KINDS[kind]!r}")
        for call in _calls(section):
            if not ACTION.match(call):
                problems.append(f"section {n}: bad action {call!r}")
            elif call not in routes:
                problems.append(f"section {n}: no route {call!r}")
    assert not problems, "\n".join(problems)
