"""Protected blocks (swarm/blocks.py): the CONTRACT block a file begins with stays there."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swarm import blocks, git
from swarm.blocks import Block, BlockError, find, leading_block, restore, restore_text
from swarm.config import Config
from swarm.runner import PASSED, build_prompt, run_task
from swarm.task import find_tasks
from tests.conftest import quiet

M = ["CONTRACT"]
DOC = (
    '"""todos: Add and list todos.\n\n'
    'CONTRACT (keep this docstring unchanged):\nGET /api/todos -> []\n"""\n'
)
CODE = (
    "from webapp.core.api import route\n\n\n"
    "@route('GET', '/api/todos')\ndef todos():\n    return []\n"
)
JS_BLOCK = (
    "// CONTRACT for page.js\n// - renders the list\n// - buttons are busy while a call runs\n"
)
JS_CODE = "const x = 1;\n\nfunction render() {}\n"


# -- finding the block -----------------------------------------------------------------------------


def test_leading_docstring_and_comment_blocks() -> None:
    assert leading_block(DOC + "\n" + CODE) == Block("string", DOC)
    assert leading_block("\n\n" + JS_BLOCK + JS_CODE) == Block("comment", JS_BLOCK)
    assert leading_block("# a\n# b\ncode\n# c\n") == Block("comment", "# a\n# b\n")
    assert leading_block('"""one line"""\ncode\n') == Block("string", '"""one line"""\n')
    assert leading_block("-- sql\n-- CONTRACT\nselect 1;\n") == Block(
        "comment", "-- sql\n-- CONTRACT\n"
    )


def test_no_block_when_the_file_starts_with_code_or_is_empty() -> None:
    assert leading_block(CODE) is None
    assert leading_block("") is None
    assert leading_block("\n\n") is None
    assert leading_block('"""never closed\ncode\n') is None


def test_find_needs_a_marker() -> None:
    assert find(DOC + CODE, M) == Block("string", DOC)
    assert find('"""plain docstring"""\n' + CODE, M) is None
    assert find(JS_BLOCK + JS_CODE, ["CONTRACT", "SPEC"]) is not None
    assert find(JS_BLOCK + JS_CODE, ["SPEC"]) is None
    assert find(DOC + CODE, []) is None


def test_crlf_and_bom_are_normalised() -> None:
    text = "﻿" + (DOC + CODE).replace("\n", "\r\n")
    assert find(text, M) == Block("string", DOC)


# -- restoring the text ----------------------------------------------------------------------


def test_untouched_block_is_left_alone() -> None:
    block = find(DOC + CODE, M)
    assert block
    assert restore_text(DOC + "\n" + CODE, block, M) is None
    assert restore_text("\n\n" + DOC + CODE, block, M) is None  # leading blank lines are fine
    assert restore_text((DOC + CODE).replace("\n", "\r\n"), block, M) is None


def test_deleted_block_is_put_back_on_top() -> None:
    block = Block("string", DOC)
    assert restore_text(CODE, block, M) == (DOC + "\n" + CODE, "deleted")


def test_moved_block_is_cut_from_below_and_put_back() -> None:
    block = Block("string", DOC)
    moved = "import os\n\n" + DOC + "\n" + CODE
    assert restore_text(moved, block, M) == (DOC + "\nimport os\n\n" + CODE, "moved")


def test_edited_copy_is_dropped_and_the_original_restored() -> None:
    block = Block("string", DOC)
    edited = DOC.replace("GET /api/todos -> []", "GET /api/todos -> [1]") + "\n" + CODE
    assert restore_text(edited, block, M) == (DOC + "\n" + CODE, "edited")
    # an edited copy below the imports, with the original gone
    below = "import os\n" + edited
    assert restore_text(below, block, M) == (DOC + "\nimport os\n" + CODE, "edited")


def test_comment_blocks_work_the_same_way() -> None:
    block = Block("comment", JS_BLOCK)
    assert restore_text(JS_CODE, block, M) == (JS_BLOCK + "\n" + JS_CODE, "deleted")
    moved = JS_CODE + "\n" + JS_BLOCK
    assert restore_text(moved, block, M) == (JS_BLOCK + "\n" + JS_CODE + "\n", "moved")
    edited = JS_BLOCK.replace("busy", "enabled") + JS_CODE
    assert restore_text(edited, block, M) == (JS_BLOCK + "\n" + JS_CODE, "edited")


def test_only_blocks_of_the_same_kind_count_as_copies() -> None:
    block = Block("string", DOC)
    # a comment that mentions CONTRACT is the model's own; it stays
    text = CODE + "\n# see the CONTRACT above\n"
    assert restore_text(text, block, M) == (DOC + "\n" + text, "deleted")
    # an indented docstring (inside a function) is not a top-level copy
    inner = CODE + '\n\ndef f():\n    """CONTRACT copy inside."""\n'
    assert restore_text(inner, block, M) == (DOC + "\n" + inner, "deleted")


def test_two_markers_in_one_file_protect_only_the_leading_block() -> None:
    # the model may keep its own CONTRACT-mentioning comment lower down
    text = DOC + "\n" + CODE + "\n# CONTRACT note by the model\n"
    assert find(text, M) == Block("string", DOC)
    assert restore_text(text, Block("string", DOC), M) is None


# -- restoring in a worktree -----------------------------------------------------------------


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "handler.py").write_text(DOC + "\n" + CODE, encoding="utf-8")
    (repo / "page.js").write_text(JS_BLOCK + "\n" + JS_CODE, encoding="utf-8")
    (repo / "plain.py").write_text(CODE, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
        cwd=repo,
        check=True,
    )
    return repo, git.rev_parse(repo, "main")


def test_restore_in_a_worktree(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "handler.py").write_text("import os\n\n" + DOC + "\n" + CODE, encoding="utf-8")
    (repo / "page.js").write_text(JS_CODE, encoding="utf-8")
    (repo / "plain.py").write_text(CODE + "x = 1\n", encoding="utf-8")
    (repo / "new.py").write_text("# CONTRACT in a new file is the model's\n", encoding="utf-8")
    paths = ["handler.py", "page.js", "plain.py", "new.py"]
    out = restore(repo, base, paths, M)
    assert [str(r) for r in out] == ["handler.py (moved)", "page.js (deleted)"]
    assert (repo / "handler.py").read_text(encoding="utf-8") == DOC + "\nimport os\n\n" + CODE
    assert (repo / "page.js").read_text(encoding="utf-8") == JS_BLOCK + "\n" + JS_CODE
    assert (repo / "plain.py").read_text(encoding="utf-8") == CODE + "x = 1\n"
    assert (repo / "new.py").read_text(encoding="utf-8").startswith("# CONTRACT in a new")
    assert restore(repo, base, paths, M) == []  # idempotent
    assert restore(repo, base, paths, []) == []  # no markers: nothing is protected


def test_deleted_file_is_recreated_with_its_block(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "handler.py").unlink()
    assert [str(r) for r in restore(repo, base, ["handler.py"], M)] == ["handler.py (recreated)"]
    assert (repo / "handler.py").read_text(encoding="utf-8") == DOC


def test_binary_content_cannot_be_restored(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "handler.py").write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(BlockError, match="not a text file"):
        restore(repo, base, ["handler.py"], M)


# -- through run_task ------------------------------------------------------------------------


def _commit_contracted(repo: Path) -> None:
    (repo / "src" / "shopkit" / "contracted.py").write_text(DOC + "\n" + "VALUE = 1\n", "utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "contract"],
        cwd=repo,
        check=True,
    )


def test_run_task_restores_the_block_and_tells_the_agent(cfg: Config) -> None:
    target = cfg.target(None)
    target.protected_blocks = ["CONTRACT"]
    _commit_contracted(target.repo)
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    prompt = build_prompt(task, target, 1, None)
    assert "begins with a `CONTRACT` block keeps it" in prompt

    s = run_task(cfg, target, task, cfg.engine("mover"), max_iterations=1, log=quiet)
    assert s.status == PASSED, s.error
    rec = s.records[0]
    assert rec.restored == ["src/shopkit/contracted.py (moved)"]
    assert "src/shopkit/contracted.py" in rec.changed
    assert (cfg.runs_dir / s.run_id / "iter-1" / "restored.txt").read_text(encoding="utf-8") == (
        "src/shopkit/contracted.py (moved)\n"
    )
    committed = git.file_at(target.repo, str(s.commit), "src/shopkit/contracted.py")
    assert committed is not None and committed.startswith(DOC)
    assert "VALUE = 2" in committed and committed.count("CONTRACT") == 1


def test_run_task_without_markers_changes_nothing(cfg: Config) -> None:
    target = cfg.target(None)
    _commit_contracted(target.repo)
    task = find_tasks(target.tasks_dir, ["demo-001"])[0]
    s = run_task(cfg, target, task, cfg.engine("mover"), max_iterations=1, log=quiet)
    assert s.status == PASSED and s.records[0].restored == []
    committed = git.file_at(target.repo, str(s.commit), "src/shopkit/contracted.py")
    assert committed is not None and not committed.startswith(DOC)


def test_feedback_names_the_restored_files() -> None:
    from swarm.guard import GuardReport
    from swarm.runner import build_feedback

    text = build_feedback(GuardReport(changes=[]), [], None, 1000, "full", ["a.py (moved)"])
    assert "put the protected block at the top of these files back" in text
    assert "- a.py (moved)" in text
    assert blocks.Restored("a.py", "moved").path == "a.py"
