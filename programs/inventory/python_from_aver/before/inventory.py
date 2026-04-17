"""Multi-warehouse inventory with per-SKU stock levels, reservations, and reorder policy.

Available stock is tracked separately from reserved stock so that pending orders do not
silently over-commit the same physical units. Operations raise ValueError on invalid
input (unknown warehouse/sku, non-positive qty, over-commit).

Design decisions:

* Available stock and reserved stock are tracked separately on each StockLevel,
  not collapsed into a single counter. Reserved stock represents orders that
  have been accepted but not yet shipped; collapsing reserved into available
  would allow a second reservation to over-commit the same physical units.
  Tracking them separately makes the invariant reserved <= on_hand checkable
  at any point. Alternatives considered: single available counter, negative
  available as reserved.

* Invalid operations raise ValueError rather than returning a tagged result.
  Unknown warehouse/sku, non-positive quantities, and over-reservations are
  all expected at runtime, and Python callers branch on them via try/except.
  (The Aver source returns Result to make failure modes explicit in the call
  site; here the Pythonic equivalent is a narrow exception type.) Alternatives
  considered: silent no-op, panic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Sku:
    id: str
    name: str
    reorder_point: int


@dataclass(frozen=True)
class Warehouse:
    id: str
    location: str


@dataclass(frozen=True)
class StockLevel:
    on_hand: int
    reserved: int


@dataclass(frozen=True)
class Inventory:
    skus: dict[str, Sku] = field(default_factory=dict)
    warehouses: dict[str, Warehouse] = field(default_factory=dict)
    levels: dict[str, dict[str, StockLevel]] = field(default_factory=dict)


def empty_inventory() -> Inventory:
    """Build an empty inventory with no warehouses, SKUs, or stock."""
    return Inventory()


def register_sku(inv: Inventory, s: Sku) -> Inventory:
    """Add or replace an SKU definition."""
    return replace(inv, skus={**inv.skus, s.id: s})


def register_warehouse(inv: Inventory, w: Warehouse) -> Inventory:
    """Add or replace a warehouse definition."""
    return replace(inv, warehouses={**inv.warehouses, w.id: w})


def get_level(inv: Inventory, warehouse_id: str, sku_id: str) -> StockLevel:
    """Return the stock level at (warehouse, sku); zero if none recorded."""
    per_sku = inv.levels.get(warehouse_id)
    if per_sku is None:
        return StockLevel(on_hand=0, reserved=0)
    return per_sku.get(sku_id, StockLevel(on_hand=0, reserved=0))


def set_level(inv: Inventory, warehouse_id: str, sku_id: str, lvl: StockLevel) -> Inventory:
    """Store a stock level for (warehouse, sku); overwrite any previous value."""
    per_sku = dict(inv.levels.get(warehouse_id, {}))
    per_sku[sku_id] = lvl
    new_levels = {**inv.levels, warehouse_id: per_sku}
    return replace(inv, levels=new_levels)


def available_stock(inv: Inventory, warehouse_id: str, sku_id: str) -> int:
    """Return units currently available for new reservations: on_hand minus reserved."""
    lvl = get_level(inv, warehouse_id, sku_id)
    return lvl.on_hand - lvl.reserved


def known_warehouse(inv: Inventory, warehouse_id: str) -> bool:
    """True if the warehouse has been registered."""
    return warehouse_id in inv.warehouses


def known_sku(inv: Inventory, sku_id: str) -> bool:
    """True if the SKU has been registered."""
    return sku_id in inv.skus


def receive_shipment(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Add qty to on-hand. Raises ValueError on unknown warehouse/sku or non-positive qty."""
    if qty <= 0:
        raise ValueError("Shipment qty must be positive")
    if not known_warehouse(inv, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    lvl = get_level(inv, warehouse_id, sku_id)
    return set_level(inv, warehouse_id, sku_id, StockLevel(on_hand=lvl.on_hand + qty, reserved=lvl.reserved))


def reserve_stock(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Reserve qty for an order. Raises if qty would exceed available."""
    if qty <= 0:
        raise ValueError("Reserve qty must be positive")
    if not known_warehouse(inv, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    lvl = get_level(inv, warehouse_id, sku_id)
    avail = lvl.on_hand - lvl.reserved
    if qty > avail:
        raise ValueError(f"Insufficient available stock: need {qty}, have {avail}")
    return set_level(inv, warehouse_id, sku_id, StockLevel(on_hand=lvl.on_hand, reserved=lvl.reserved + qty))


def release_reservation(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Cancel a previously made reservation. Raises if qty exceeds current reserved."""
    if qty <= 0:
        raise ValueError("Release qty must be positive")
    lvl = get_level(inv, warehouse_id, sku_id)
    if qty > lvl.reserved:
        raise ValueError(f"Cannot release more than reserved: requested {qty}, reserved {lvl.reserved}")
    return set_level(inv, warehouse_id, sku_id, StockLevel(on_hand=lvl.on_hand, reserved=lvl.reserved - qty))


def fulfill_reservation(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Ship qty out: remove from both reserved and on-hand. Raises if qty exceeds reserved."""
    if qty <= 0:
        raise ValueError("Fulfill qty must be positive")
    lvl = get_level(inv, warehouse_id, sku_id)
    if qty > lvl.reserved:
        raise ValueError(f"Cannot fulfill more than reserved: requested {qty}, reserved {lvl.reserved}")
    return set_level(inv, warehouse_id, sku_id, StockLevel(on_hand=lvl.on_hand - qty, reserved=lvl.reserved - qty))


def rebalance(inv: Inventory, from_id: str, to_id: str, sku_id: str, qty: int) -> Inventory:
    """Transfer qty of unreserved stock between warehouses."""
    if qty <= 0:
        raise ValueError("Rebalance qty must be positive")
    if not known_warehouse(inv, from_id):
        raise ValueError(f"Unknown warehouse: {from_id}")
    if not known_warehouse(inv, to_id):
        raise ValueError(f"Unknown warehouse: {to_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    from_lvl = get_level(inv, from_id, sku_id)
    avail = from_lvl.on_hand - from_lvl.reserved
    if qty > avail:
        raise ValueError(f"Insufficient stock at {from_id}: need {qty}, have {avail}")
    to_lvl = get_level(inv, to_id, sku_id)
    with_from = set_level(inv, from_id, sku_id, StockLevel(on_hand=from_lvl.on_hand - qty, reserved=from_lvl.reserved))
    return set_level(with_from, to_id, sku_id, StockLevel(on_hand=to_lvl.on_hand + qty, reserved=to_lvl.reserved))


def needs_reorder(inv: Inventory, warehouse_id: str, sku_id: str) -> bool:
    """True when available stock at this warehouse is at or below the sku's reorder point."""
    sku = inv.skus.get(sku_id)
    if sku is None:
        return False
    return available_stock(inv, warehouse_id, sku_id) <= sku.reorder_point


def total_on_hand(inv: Inventory, sku_id: str) -> int:
    """Sum on-hand across all warehouses for this sku."""
    return sum(get_level(inv, wid, sku_id).on_hand for wid in inv.warehouses)


def _smoke_tests() -> None:
    inv = empty_inventory()
    inv = register_warehouse(inv, Warehouse(id="W1", location="NYC"))
    inv = register_warehouse(inv, Warehouse(id="W2", location="LA"))
    inv = register_sku(inv, Sku(id="S1", name="Widget", reorder_point=10))

    assert available_stock(inv, "W1", "S1") == 0

    inv = receive_shipment(inv, "W1", "S1", 20)
    assert available_stock(inv, "W1", "S1") == 20

    inv = reserve_stock(inv, "W1", "S1", 5)
    assert available_stock(inv, "W1", "S1") == 15
    assert get_level(inv, "W1", "S1").reserved == 5

    inv = fulfill_reservation(inv, "W1", "S1", 3)
    assert get_level(inv, "W1", "S1").on_hand == 17
    assert get_level(inv, "W1", "S1").reserved == 2

    inv = release_reservation(inv, "W1", "S1", 2)
    assert get_level(inv, "W1", "S1").reserved == 0

    inv = rebalance(inv, "W1", "W2", "S1", 5)
    assert get_level(inv, "W1", "S1").on_hand == 12
    assert get_level(inv, "W2", "S1").on_hand == 5

    assert total_on_hand(inv, "S1") == 17
    assert needs_reorder(inv, "W2", "S1") is True
    assert needs_reorder(inv, "W1", "S1") is False

    try:
        reserve_stock(inv, "W1", "S1", 1000)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Insufficient" in str(e)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
