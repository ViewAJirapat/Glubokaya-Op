"""Money math. All amounts are integer cents."""


def line_total(unit_price_cents: int, quantity: int) -> int:
    if quantity < 0:
        raise ValueError("quantity must be >= 0")
    return unit_price_cents * quantity
