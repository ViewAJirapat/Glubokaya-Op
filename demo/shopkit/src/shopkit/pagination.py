"""Split sequences into numbered pages."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    number: int
    per_page: int
    total: int
    pages: int

    @property
    def has_next(self) -> bool:
        return self.number < self.pages

    @property
    def has_prev(self) -> bool:
        return self.number > 1


def paginate[T](items: Sequence[T], page: int, per_page: int) -> Page[T]:
    """Return page number `page` (1-based) of `items`, with `per_page` items per page."""
    total = len(items)
    pages = total // per_page
    start = (page - 1) * per_page
    return Page(list(items[start : start + per_page]), page, per_page, total, pages)
