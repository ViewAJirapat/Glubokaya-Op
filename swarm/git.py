"""Thin git wrappers. Every harness git call goes through `git()` so failures are loud."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Paths from the agent's worktree may contain [ ] * ?; never let git read them as globs.
LITERAL = "--literal-pathspecs"
FALLBACK_IDENTITY = ["-c", "user.name=swarm-agent", "-c", "user.email=swarm-agent@localhost"]
AGENT_AUTHOR = "swarm-agent <swarm-agent@localhost>"


class GitError(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {cwd}:\n{msg}")
    return proc.stdout


def ok(cwd: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False).returncode == 0


def rev_parse(cwd: Path, ref: str) -> str:
    return git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def identity_args(repo: Path) -> list[str]:
    has_name = bool(git(repo, "config", "user.name", check=False).strip())
    has_email = bool(git(repo, "config", "user.email", check=False).strip())
    return [] if has_name and has_email else FALLBACK_IDENTITY


def add_worktree(repo: Path, path: Path, branch: str, base_sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(path), base_sha)


def remove_worktree(repo: Path, path: Path) -> None:
    if path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune")


def ensure_excludes(repo: Path, patterns: list[str]) -> None:
    """Add patterns to the repo-wide info/exclude (shared by all worktrees)."""
    common = Path(git(repo, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    missing = [p for p in patterns if p not in existing]
    if missing:
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write("\n# added by swarm\n" + "\n".join(missing) + "\n")


def file_at(repo: Path, sha: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


@dataclass(frozen=True)
class Change:
    status: str  # M(odified) A(dded) D(eleted) T(ype change)
    path: str


def changes(wt: Path, base_sha: str) -> list[Change]:
    """Working tree vs base commit, including new untracked (non-ignored) files."""
    untracked = git(wt, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    untracked = [p for p in untracked if p]
    if untracked:
        # Intent-to-add makes new files visible to `git diff <base>`.
        git(wt, LITERAL, "add", "--intent-to-add", "--", *untracked)
    out = git(wt, "diff", "--name-status", "--no-renames", "-z", base_sha)
    parts = [p for p in out.split("\0") if p]
    return [Change(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def added_lines(wt: Path, base_sha: str) -> dict[str, list[str]]:
    """Lines added relative to base, per file (call after `changes`)."""
    out = git(wt, "diff", "-U0", "--no-color", "--no-renames", "--no-ext-diff", base_sha)
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else None
        elif line.startswith("+") and current is not None:
            result.setdefault(current, []).append(line[1:])
    return result


def revert(wt: Path, base_sha: str, change: Change) -> None:
    if ok(wt, "cat-file", "-e", f"{base_sha}:{change.path}"):
        git(wt, LITERAL, "checkout", base_sha, "--", change.path)
    else:
        git(wt, LITERAL, "rm", "-f", "-q", "--cached", "--ignore-unmatch", "--", change.path)
        (wt / change.path).unlink(missing_ok=True)


def diff_patch(wt: Path, base_sha: str) -> str:
    return git(wt, "diff", "--binary", "--no-color", base_sha)


def fold_commits(wt: Path, base_sha: str) -> bool:
    """If the agent committed on its own, move HEAD back to base keeping the changes."""
    if rev_parse(wt, "HEAD") == base_sha:
        return False
    git(wt, "reset", "--soft", base_sha)
    git(wt, "reset", "-q")
    return True


def reset_index(wt: Path) -> None:
    """Make the index match HEAD. The guard and the gates judge the working tree; anything
    staged differently (git add, then restore the file) would otherwise be committed unseen."""
    git(wt, "reset", "-q")


def commit(wt: Path, repo: Path, paths: list[str], message: str) -> str:
    """Commit exactly `paths` as they are in the working tree, nothing else from the index."""
    reset_index(wt)
    git(wt, LITERAL, "add", "-A", "--", *paths)
    git(wt, *identity_args(repo), "commit", "-q", "--author", AGENT_AUTHOR, "-m", message)
    return rev_parse(wt, "HEAD")


def branches(repo: Path, pattern: str) -> list[str]:
    out = git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{pattern}")
    return [b for b in out.splitlines() if b]
