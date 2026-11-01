"""tools/restore_contracts.py puts a deleted or misplaced CONTRACT back on top."""

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

CONTRACT = 'x: things.\n\nCONTRACT (keep this docstring unchanged):\nGET /api/x -> {"a": 1}'
CODE = "from webapp.core.api import Request\n\n\ndef f(request: Request) -> int:\n    return 1\n"


def _tool() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "tools" / "restore_contracts.py"
    spec = importlib.util.spec_from_file_location("restore_contracts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(path: Path) -> str | None:
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")), clean=True)


def test_deleted_contract_is_put_back(tmp_path: Path) -> None:
    f = tmp_path / "handler.py"
    f.write_text(CODE, encoding="utf-8")
    assert _tool().restore(f, CONTRACT)
    assert _doc(f) == CONTRACT
    assert f.read_text(encoding="utf-8").endswith(CODE)


def test_contract_below_the_imports_moves_to_the_top(tmp_path: Path) -> None:
    f = tmp_path / "handler.py"
    f.write_text(CODE.replace("\n\n\n", f'\n\n"""{CONTRACT}\n"""\n\n\n', 1), encoding="utf-8")
    assert _doc(f) is None
    assert _tool().restore(f, CONTRACT)
    assert _doc(f) == CONTRACT
    assert f.read_text(encoding="utf-8").count("CONTRACT") == 1


def test_changed_contract_is_replaced_and_an_intact_one_is_left_alone(tmp_path: Path) -> None:
    f = tmp_path / "handler.py"
    f.write_text(f'"""{CONTRACT.replace("1", "2")}\n"""\n\n{CODE}', encoding="utf-8")
    tool = _tool()
    assert tool.restore(f, CONTRACT)
    assert _doc(f) == CONTRACT
    assert not tool.restore(f, CONTRACT)
