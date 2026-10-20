import pytest

from shopkit.pagination import paginate


def test_partial_last_page() -> None:
    page = paginate(list(range(11)), 3, 5)
    assert page.items == [10]
    assert page.pages == 3
    assert not page.has_next
    assert page.has_prev


def test_page_count_rounds_up() -> None:
    assert paginate(list(range(11)), 1, 5).pages == 3
    assert paginate(list(range(10)), 1, 5).pages == 2
    assert paginate(list(range(1)), 1, 5).pages == 1


def test_middle_page_has_next() -> None:
    assert paginate(list(range(11)), 2, 5).has_next


def test_empty_sequence_has_one_empty_page() -> None:
    page = paginate([], 1, 5)
    assert page.items == []
    assert page.pages == 1
    assert not page.has_next


@pytest.mark.parametrize(("page", "per_page"), [(0, 5), (-1, 5), (1, 0), (1, -3), (4, 5)])
def test_invalid_arguments(page: int, per_page: int) -> None:
    with pytest.raises(ValueError):
        paginate(list(range(11)), page, per_page)
