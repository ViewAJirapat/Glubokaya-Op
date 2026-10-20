from shopkit.pagination import paginate


def test_first_page() -> None:
    page = paginate(list(range(10)), 1, 5)
    assert page.items == [0, 1, 2, 3, 4]
    assert page.pages == 2
    assert page.has_next
    assert not page.has_prev


def test_last_full_page() -> None:
    page = paginate(list(range(10)), 2, 5)
    assert page.items == [5, 6, 7, 8, 9]
    assert not page.has_next
    assert page.has_prev
