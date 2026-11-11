"""Autofix (run by the harness after each edit): put every planned feature's CONTRACT
docstring back as the first statement of its file, exactly as tests/contracts.json has it.

Small models delete the block, or keep it but move it below the imports, where Python no
longer reads it as the docstring. The spec belongs to the studio, not the worker, so the
harness restores it, as ruff restores formatting. Any other top-level string that holds the
CONTRACT (a copy the model left lower down) is removed.
"""

import ast
import inspect
import json
import sys
from pathlib import Path


def restore(path: Path, expected: str) -> bool:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False  # ruff and mypy report it; don't guess
    if ast.get_docstring(tree, clean=True) == expected:
        return False
    # Split exactly as the parser counts lines (read_text already turned \r\n and \r into \n);
    # str.splitlines would also split on form feeds and other characters and shift the lines.
    lines = text.split("\n")
    drop: list[tuple[int, int]] = []
    for n, node in enumerate(tree.body):
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if not (n == 0 or "CONTRACT" in inspect.cleandoc(node.value.value)):
            continue
        start, end = node.lineno, node.end_lineno or node.lineno
        after = lines[end - 1].encode("utf-8")[node.end_col_offset or 0 :]
        if node.col_offset == 0 and not after.strip():  # alone on its lines: safe to cut
            drop.append((start, end))
    for start, end in sorted(drop, reverse=True):
        del lines[start - 1 : end]
    body = "\n".join(lines).lstrip("\n")
    doc = expected.replace("\\", "\\\\").replace('"""', "'''")
    path.write_text(f'"""{doc}\n"""\n\n{body}', encoding="utf-8")
    return True


def main() -> int:
    root = Path.cwd()
    contracts = json.loads((root / "tests" / "contracts.json").read_text(encoding="utf-8"))
    for rel, expected in sorted(contracts.items()):
        path = root / rel
        if path.is_file() and restore(path, expected):
            print(f"restored the CONTRACT docstring at the top of {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
