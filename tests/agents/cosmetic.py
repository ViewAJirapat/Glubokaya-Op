"""Scripted agent that makes a harmless change that doesn't solve anything."""

from pathlib import Path

src = Path("src/shopkit/pagination.py")
text = src.read_text(encoding="utf-8")
src.write_text(text.replace('"""Split sequences', '"""Split any sequences', 1), encoding="utf-8")
