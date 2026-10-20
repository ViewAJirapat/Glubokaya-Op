"""Scripted agent with a correct fix in sloppy formatting (unsorted imports, spacing)."""

from pathlib import Path

Path("src/shopkit/pagination.py").write_text(
    '''"""Split sequences into numbered pages."""
from dataclasses import dataclass
from collections.abc import Sequence
@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    number: int
    per_page: int
    total: int
    pages: int
    @property
    def has_next(self) -> bool:
        return self.number<self.pages
    @property
    def has_prev(self) -> bool:
        return self.number>1
def paginate[T](items: Sequence[T], page: int, per_page: int) -> Page[T]:
    if per_page < 1: raise ValueError("per_page must be >= 1")
    total=len(items)
    pages=max(1,-(-total//per_page))
    if not 1<=page<=pages: raise ValueError("page out of range")
    start=(page-1)*per_page
    return Page(list(items[start:start+per_page]),page,per_page,total,pages)
''',
    encoding="utf-8",
)
