"""Protected blocks: the spec block at the top of a file stays where it is (PLAN.md phase A).

Small models delete the CONTRACT block a file begins with, or keep it but move it below the
imports, or edit it. ChatBook and Swarm Studio each wrote their own fix as an autofix
command; this is the harness's. After the agent's edit and the target's autofix, and before
the guard, every changed file whose base version begins with a protected block gets that
block put back, verbatim, at the top. The iteration's record and the feedback say so.

A block is the run of lines a file begins with, in one of two shapes:
- a triple-quoted string (a Python module docstring);
- a run of comment lines with the same prefix (`//`, `#`, `--`).
It is protected when it contains one of the target's `protected_blocks` markers (for
example `CONTRACT`). Blank lines before it are ignored. One block per file: the leading one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from swarm import git

QUOTES = ('"""', "'''")
PREFIXES = ("//", "#", "--")


class BlockError(Exception):
    pass


@dataclass(frozen=True)
class Block:
    kind: str  # "string" | "comment"
    text: str  # the block's lines, each ending in "\n"


@dataclass(frozen=True)
class Restored:
    path: str
    what: str  # deleted | moved | edited | recreated

    def __str__(self) -> str:
        return f"{self.path} ({self.what})"


def normalise(text: str) -> str:
    """Line endings become \\n and a leading byte-order mark goes: the only differences a
    block may have from its base version."""
    return text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿")


def _region(lines: list[str], start: int) -> tuple[str, int] | None:
    """The block shape starting at line `start`, as (kind, end exclusive), or None."""
    first = lines[start]
    for q in QUOTES:
        if first.startswith(q):
            rest = first[len(q) :]
            if q in rest:  # opened and closed on one line
                return "string", start + 1
            for j in range(start + 1, len(lines)):
                if q in lines[j]:
                    return "string", j + 1
            return None  # never closed: not a block
    for p in PREFIXES:
        if first.startswith(p):
            end = start
            while end < len(lines) and lines[end].startswith(p):
                end += 1
            return "comment", end
    return None


def leading_block(text: str) -> Block | None:
    """The block the file begins with (blank lines before it ignored), or None."""
    lines = normalise(text).split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        return None
    found = _region(lines, start)
    if found is None:
        return None
    kind, end = found
    return Block(kind, "".join(line + "\n" for line in lines[start:end]))


def find(text: str, markers: list[str]) -> Block | None:
    """The file's leading block when it holds one of the markers; else None."""
    block = leading_block(text)
    if block and any(m in block.text for m in markers):
        return block
    return None


def _copies(text: str, kind: str, markers: list[str]) -> list[tuple[int, int]]:
    """Line ranges of every block of `kind` at column 0 that holds a marker: the edited or
    moved copies to drop. Ranges are (start, end) over text.split("\\n"), end exclusive."""
    lines = text.split("\n")
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        found = _region(lines, i)
        if found and found[0] == kind:
            end = found[1]
            region = "\n".join(lines[i:end])
            if any(m in region for m in markers):
                out.append((i, end))
            i = end
        else:
            i += 1
    return out


def restore_text(current: str, block: Block, markers: list[str]) -> tuple[str, str] | None:
    """Put `block` back at the top of `current`. Returns (new text, what was wrong), or None
    when the file already begins with the block."""
    text = normalise(current)
    body = text.lstrip("\n")
    if body.startswith(block.text):
        return None
    what = "deleted"
    if block.text in text:
        what = "moved"
        text = text.replace(block.text + "\n", "", 1).replace(block.text, "")
    lines = text.split("\n")
    copies = _copies(text, block.kind, markers)
    if copies:
        what = "edited" if what == "deleted" else what
        for start, end in reversed(copies):
            if end < len(lines) and not lines[end].strip():
                end += 1  # the blank line that followed the copy goes with it
            del lines[start:end]
        text = "\n".join(lines)
    return block.text + "\n" + text.lstrip("\n"), what


def restore(wt: Path, base_sha: str, paths: list[str], markers: list[str]) -> list[Restored]:
    """For each changed path whose base version begins with a protected block, make the
    working copy begin with that block again (a deleted file is recreated with the block).
    Raises BlockError when a file cannot be read as text."""
    out: list[Restored] = []
    if not markers:
        return out
    for rel in paths:
        base = git.file_at(wt, base_sha, rel)
        if base is None:
            continue  # new in this edit: nothing to protect
        block = find(base, markers)
        if block is None:
            continue
        file = wt / rel
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(block.text, encoding="utf-8")
            out.append(Restored(rel, "recreated"))
            continue
        try:
            current = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise BlockError(
                f"{rel}: not a text file; its protected block cannot be restored"
            ) from None
        fixed = restore_text(current, block, markers)
        if fixed is None:
            continue
        text, what = fixed
        file.write_text(text, encoding="utf-8")
        out.append(Restored(rel, what))
    return out
