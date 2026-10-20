"""A shopping cart."""

from dataclasses import dataclass, field

from shopkit.pricing import line_total


@dataclass
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


@dataclass
class Cart:
    items: list[LineItem] = field(default_factory=list)

    def add(self, sku: str, unit_price_cents: int, quantity: int = 1) -> None:
        for item in self.items:
            if item.sku == sku:
                item.quantity += quantity
                return
        self.items.append(LineItem(sku, unit_price_cents, quantity))

    def subtotal_cents(self) -> int:
        return sum(line_total(i.unit_price_cents, i.quantity) for i in self.items)

    def total_cents(self) -> int:
        return self.subtotal_cents()
