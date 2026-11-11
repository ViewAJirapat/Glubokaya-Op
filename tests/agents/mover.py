"""Scripted agent: applies the reference fix for demo-001-pagination, and moves the CONTRACT
docstring of src/shopkit/contracted.py below its imports, with a copy edited in a comment
block (the 14B's habits). Runs in the worktree."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
patch = ROOT / "tasks" / "demo" / "demo-001-pagination" / "solution.patch"
subprocess.run(["git", "apply", str(patch)], check=True)

f = Path("src/shopkit/contracted.py")
text = f.read_text(encoding="utf-8")
head, sep, rest = text.partition('"""\n\n')  # the docstring and the blank line after it
doc = head + '"""\n'
f.write_text(rest + "\n" + doc + "\nVALUE = 2\n", encoding="utf-8")
