import pytest

from shopkit.cart import Cart
from shopkit.pricing import line_total


def test_add_merges_same_sku() -> None:
    cart = Cart()
    cart.add("A", 100)
    cart.add("A", 100, 2)
    assert len(cart.items) == 1
    assert cart.items[0].quantity == 3


def test_subtotal_and_total() -> None:
    cart = Cart()
    cart.add("A", 1005)
    cart.add("B", 250, 2)
    assert cart.subtotal_cents() == 1505
    assert cart.total_cents() == 1505


def test_negative_quantity_rejected() -> None:
    with pytest.raises(ValueError):
        line_total(100, -1)
