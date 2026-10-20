import pytest

from shopkit.cart import Cart
from shopkit.pricing import DISCOUNT_CODES, apply_discount


def make_cart() -> Cart:
    cart = Cart()
    cart.add("A", 1005)
    cart.add("B", 250, 2)
    return cart  # subtotal 1505


def test_codes_table() -> None:
    assert DISCOUNT_CODES == {"SAVE10": 10, "SAVE25": 25, "HALF": 50}


@pytest.mark.parametrize(
    ("amount", "percent", "expected"),
    [
        (1000, 10, 900),
        (1005, 10, 904),
        (1004, 10, 904),
        (1015, 10, 913),
        (999, 50, 499),
        (1, 50, 0),
        (0, 50, 0),
        (1000, 0, 1000),
        (1000, 100, 0),
    ],
)
def test_apply_discount(amount: int, percent: int, expected: int) -> None:
    result = apply_discount(amount, percent)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(("amount", "percent"), [(100, -1), (100, 101), (-1, 10)])
def test_apply_discount_rejects_bad_input(amount: int, percent: int) -> None:
    with pytest.raises(ValueError):
        apply_discount(amount, percent)


def test_no_code_by_default() -> None:
    cart = make_cart()
    assert cart.discount_code is None
    assert cart.total_cents() == 1505


def test_code_is_case_insensitive_and_trimmed() -> None:
    cart = make_cart()
    cart.apply_code("  save10 ")
    assert cart.discount_code == "SAVE10"
    assert cart.total_cents() == 1354
    assert cart.subtotal_cents() == 1505


def test_codes_replace_instead_of_stacking() -> None:
    cart = make_cart()
    cart.apply_code("SAVE10")
    cart.apply_code("HALF")
    assert cart.discount_code == "HALF"
    assert cart.total_cents() == 752


def test_unknown_code_keeps_previous() -> None:
    cart = make_cart()
    cart.apply_code("SAVE25")
    with pytest.raises(ValueError):
        cart.apply_code("BOGUS")
    assert cart.discount_code == "SAVE25"
    assert cart.total_cents() == 1129


def test_discount_covers_items_added_later() -> None:
    cart = Cart()
    cart.apply_code("HALF")
    cart.add("A", 300)
    assert cart.total_cents() == 150
