# Agent rules for shopkit

You are working in an isolated git worktree. A harness gives you one task at a time,
checks your work with automated gates and hidden tests, and commits it only if
everything passes. The task prompt lists the exact verification commands.

## Do
- Read the relevant source before editing. Never guess file paths or APIs.
- Make the smallest change that fully solves the task. Keep existing public
  signatures unless the task says to change them.
- Keep code fully typed (mypy --strict) and formatted (ruff format, line length 100).
- Handle every edge case the task statement lists; hidden tests check them.

## Don't
- Don't edit tests, `pyproject.toml` or tool configuration. The harness reverts those edits.
- Don't silence checks: no `# type: ignore`, `# noqa`, skip or xfail markers. They block the change.
- Don't add dependencies. Standard library only.
- Don't leave stubs, placeholder return values or TODO comments.
- Don't run git commands that commit, push or switch branches.

## Layout
- `src/shopkit/`: the package (pure Python, standard library only)
- `tests/`: visible tests (read-only for you)
