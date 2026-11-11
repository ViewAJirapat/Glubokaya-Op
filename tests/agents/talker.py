"""Scripted agent: applies the reference fix for demo-001-pagination and prints aider-style
`Tokens:` lines; with $FAKE_OLLAMA_LOG set, appends an overflow line to that file (what the
Ollama server log shows when the context shifts). Runs in the worktree."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
patch = ROOT / "tasks" / "demo" / "demo-001-pagination" / "solution.patch"
subprocess.run(["git", "apply", str(patch)], check=True)
print("Tokens: 5.3k sent, 1.6k received. Cost: $0.00")
print("Tokens: 612 sent, 98 received.")
log = os.environ.get("FAKE_OLLAMA_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write('time=2026-11-02 level=INFO msg="slot update_slots" truncated = 1\n')
