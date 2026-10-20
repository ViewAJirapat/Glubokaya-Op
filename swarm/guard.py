"""Tamper checks run on the agent's diff before verification.

Rules in AGENTS.md are requests to the model; this module is what enforces them:
- edits to protected paths (tests, tool config, the rules file) are reverted;
- added lines matching forbidden patterns (skip markers, ignore pragmas) block a pass;
- commits the agent made on its own are folded back into the working tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from swarm import git
from swarm.config import Target

MAX_VIOLATIONS = 20


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def path_matches(path: str, pattern: str) -> bool:
    """gitignore-flavoured: a pattern without '/' matches any path component;
    a pattern with '/' is anchored at the repo root. '**' spans directories."""
    path = path.replace("\\", "/").strip("/")
    pattern = pattern.strip().lstrip("/")
    if "/" not in pattern:
        rx = _glob_regex(pattern)
        return any(rx.fullmatch(part) for part in path.split("/"))
    return _glob_regex(pattern).fullmatch(path) is not None


def protected_by(path: str, patterns: list[str]) -> str | None:
    return next((p for p in patterns if path_matches(path, p)), None)


@dataclass
class GuardReport:
    changes: list[git.Change] = field(default_factory=list)  # what remains after reverts
    reverted: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    folded_commits: bool = False

    @property
    def changed_paths(self) -> list[str]:
        return [c.path for c in self.changes]


def enforce(wt: Path, base_sha: str, target: Target) -> GuardReport:
    report = GuardReport(folded_commits=git.fold_commits(wt, base_sha))

    changes = git.changes(wt, base_sha)
    for change in changes:
        pattern = protected_by(change.path, target.protected)
        if pattern:
            git.revert(wt, base_sha, change)
            report.reverted.append(f"{change.path} (protected by {pattern!r})")
    report.changes = git.changes(wt, base_sha) if report.reverted else changes

    regexes = [re.compile(p) for p in target.forbidden_added]
    for path, lines in git.added_lines(wt, base_sha).items():
        for line in lines:
            for rx in regexes:
                if rx.search(line):
                    report.violations.append(
                        f"{path}: added `{line.strip()[:120]}` (forbidden pattern {rx.pattern!r})"
                    )
    del report.violations[MAX_VIOLATIONS:]
    return report
