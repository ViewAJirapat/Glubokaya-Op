"""Every planned feature keeps its CONTRACT docstring exactly as planned.

tests/contracts.json (written by Swarm Studio from the approved plan) maps each stub file to
its docstring. The docstring is the feature's spec; a later fix of that file needs it intact.
"""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS: dict[str, str] = json.loads(
    (Path(__file__).resolve().parent / "contracts.json").read_text(encoding="utf-8")
)
FIX = "fix: restore it verbatim at the top of the file, then put your code below it"


def _problem(rel: str, expected: str, got: str | None) -> str | None:
    if got == expected:
        return None
    head = f"{rel}: CONTRACT docstring missing or changed ({FIX})"
    if got is None:
        detail = "    the file has no module docstring"
    else:
        want, have = expected.splitlines(), got.splitlines()
        n = next(
            (i for i, (a, b) in enumerate(zip(want, have, strict=False)) if a != b),
            min(len(want), len(have)),
        )
        line_want = want[n] if n < len(want) else "(end of docstring)"
        line_have = have[n] if n < len(have) else "(end of docstring)"
        detail = f"    docstring line {n + 1} must be {line_want!r} but is {line_have!r}"
    return f'{head}\n{detail}\n    the file must begin with:\n"""{expected}\n"""'


def test_contract_docstrings_are_unchanged() -> None:
    problems: list[str] = []
    for rel, expected in sorted(CONTRACTS.items()):
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}: file is missing")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # ruff and mypy report the syntax error itself
        problem = _problem(rel, expected, ast.get_docstring(tree, clean=True))
        if problem:
            problems.append(problem)
    assert not problems, "\n".join(problems)
