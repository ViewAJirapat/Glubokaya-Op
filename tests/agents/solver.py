"""Scripted agent for the studio tests: implements the feature named in the prompt by putting
a reference handler (argv[1]/<feature>/handler.py) below the stub's CONTRACT docstring."""

import ast
import re
import sys
from pathlib import Path

solutions = Path(sys.argv[1])
prompt = sys.stdin.read()
m = re.search(r"Implement the feature `(\w+)` in `([^`]+)`", prompt)
assert m, "no feature in the prompt"
feature, handler = m.group(1), Path(m.group(2))
doc = ast.get_docstring(ast.parse(handler.read_text(encoding="utf-8")), clean=False) or ""
body = (solutions / feature / "handler.py").read_text(encoding="utf-8")
handler.write_text(f'"""{doc}\n"""\n\n{body}', encoding="utf-8")
print(f"solver wrote {handler}")
