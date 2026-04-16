"""Order total calculation with discount and tax rate.

Validation happens at construction: if you have a Discount or TaxRate,
it's been vetted. Item is intentionally unconstrained — negative prices
model refunds, zero quantities model removed line items.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    price: float
    quantity: int


@dataclass(frozen=True)
class Discount:
    percent: float


@dataclass(frozen=True)
class TaxRate:
    percent: float


def make_discount(percent: float) -> Discount:
    """Build a Discount, rejecting out-of-range values."""
    if percent < 0.0:
        raise ValueError("Discount cannot be negative")
    if percent > 100.0:
        raise ValueError("Discount cannot exceed 100%")
    return Discount(percent=percent)


def make_tax_rate(percent: float) -> TaxRate:
    """Build a TaxRate, rejecting negatives."""
    if percent < 0.0:
        raise ValueError("Tax rate cannot be negative")
    return TaxRate(percent=percent)


def sum_items(items: list[Item]) -> float:
    return sum(item.price * item.quantity for item in items)


def apply_discount(subtotal: float, d: Discount) -> float:
    return subtotal - subtotal * (d.percent / 100.0)


def apply_tax(amount: float, t: TaxRate) -> float:
    return amount + amount * (t.percent / 100.0)


def calculate_total(items: list[Item], d: Discount, t: TaxRate) -> float:
    """Sum items, apply discount, apply tax. Assumes valid Discount and TaxRate."""
    subtotal = sum_items(items)
    after_discount = apply_discount(subtotal, d)
    return apply_tax(after_discount, t)


def _smoke_tests() -> None:
    assert make_discount(0.0) == Discount(0.0)
    assert make_discount(100.0) == Discount(100.0)
    try:
        make_discount(-0.1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "negative" in str(e)

    assert sum_items([]) == 0.0
    assert sum_items([Item(7.0, 4)]) == 28.0

    assert calculate_total([], Discount(50.0), TaxRate(20.0)) == 0.0
    assert calculate_total([Item(10.0, 2)], Discount(50.0), TaxRate(10.0)) == 11.0
    print("smoke tests passed")


if __name__ == "__main__":
    print("--- Order Total Demo ---")
    try:
        print(make_discount(-5.0))
    except ValueError as e:
        print(f"Error: {e}")
    print(calculate_total([Item(10.0, 2)], Discount(50.0), TaxRate(10.0)))
    _smoke_tests()
