"""Multi-warehouse inventory with per-SKU stock levels, reservations, and reorder policy.

Available stock is tracked separately from reserved stock so that pending orders do not
silently over-commit the same physical units. Operations raise ValueError on invalid
input (unknown warehouse/sku, non-positive qty, over-commit).

Reorder points can be overridden per (warehouse, SKU). The SKU-level reorder point is
used as a default whenever a warehouse has not set its own override.

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

* Reorder points are overridable per (warehouse, SKU), not fixed to a single
  value on the SKU. A single reorder point per SKU forces every warehouse to
  share the same threshold, but demand differs: a flagship urban warehouse
  turns stock much faster than a regional one, so they need different reorder
  triggers. The SKU's ``reorder_point`` stays as the default and the Inventory
  carries a ``warehouse_reorder_points`` override map keyed by
  (warehouse_id, sku_id). Storing overrides on Inventory rather than on
  Warehouse keeps Warehouse a pure physical descriptor (id, location) and lets
  reorder policy evolve without touching warehouse registration.
  ``needs_reorder`` consults the override first and falls back to the SKU
  default, so existing data without overrides keeps its current behaviour.
  Alternatives considered: reorder point on Warehouse record, replace SKU
  default entirely, override on StockLevel.
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
    # Per-warehouse overrides for reorder points: warehouse_id -> sku_id -> reorder_point.
    # When absent, the SKU's own reorder_point is used as the default.
    warehouse_reorder_points: dict[str, dict[str, int]] = field(default_factory=dict)


def empty_inventory() -> Inventory:
    """Build an empty inventory with no warehouses, SKUs, stock, or overrides."""
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


def set_warehouse_reorder_point(
    inv: Inventory, warehouse_id: str, sku_id: str, reorder_point: int
) -> Inventory:
    """Override the reorder point for a specific (warehouse, SKU) pair.

    Raises ValueError for unknown warehouse/sku or a negative reorder point.
    """
    if reorder_point < 0:
        raise ValueError("Reorder point must be non-negative")
    if not known_warehouse(inv, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    per_sku = dict(inv.warehouse_reorder_points.get(warehouse_id, {}))
    per_sku[sku_id] = reorder_point
    new_overrides = {**inv.warehouse_reorder_points, warehouse_id: per_sku}
    return replace(inv, warehouse_reorder_points=new_overrides)


def get_reorder_point(inv: Inventory, warehouse_id: str, sku_id: str) -> int | None:
    """Return the effective reorder point for (warehouse, SKU).

    Uses the warehouse-specific override if present, otherwise falls back to the
    SKU-level reorder point. Returns None when the SKU is unknown and no override exists.
    """
    per_sku = inv.warehouse_reorder_points.get(warehouse_id)
    if per_sku is not None and sku_id in per_sku:
        return per_sku[sku_id]
    sku = inv.skus.get(sku_id)
    if sku is None:
        return None
    return sku.reorder_point


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
    """True when available stock at this warehouse is at or below the effective reorder point.

    The effective reorder point is the warehouse-specific override when set, otherwise the
    SKU-level reorder point. Unknown SKUs never trigger a reorder.
    """
    threshold = get_reorder_point(inv, warehouse_id, sku_id)
    if threshold is None:
        return False
    return available_stock(inv, warehouse_id, sku_id) <= threshold


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

    # SKU-level fallback: without any per-warehouse override both warehouses use the SKU's
    # reorder_point of 10. W2 holds 5 (<= 10) -> reorder; W1 holds 12 (> 10) -> no reorder.
    assert get_reorder_point(inv, "W1", "S1") == 10
    assert get_reorder_point(inv, "W2", "S1") == 10
    assert needs_reorder(inv, "W2", "S1") is True
    assert needs_reorder(inv, "W1", "S1") is False

    # Per-warehouse override: raise W1's threshold to 15 so it now needs reorder even
    # though its available stock (12) is still above the SKU-level default (10).
    inv = set_warehouse_reorder_point(inv, "W1", "S1", 15)
    assert get_reorder_point(inv, "W1", "S1") == 15
    # W2 still falls back to the SKU-level default.
    assert get_reorder_point(inv, "W2", "S1") == 10
    assert needs_reorder(inv, "W1", "S1") is True
    assert needs_reorder(inv, "W2", "S1") is True

    # Lower W2's override below its stock so the SKU-level default would have flagged it
    # but the warehouse-specific override suppresses the reorder.
    inv = set_warehouse_reorder_point(inv, "W2", "S1", 2)
    assert get_reorder_point(inv, "W2", "S1") == 2
    assert needs_reorder(inv, "W2", "S1") is False

    # A zero override is a valid "never reorder" signal and must not fall back to the SKU.
    inv = set_warehouse_reorder_point(inv, "W1", "S1", 0)
    assert get_reorder_point(inv, "W1", "S1") == 0
    assert needs_reorder(inv, "W1", "S1") is False

    # Unknown SKU: no override, no SKU-level default -> no reorder and no threshold.
    assert get_reorder_point(inv, "W1", "UNKNOWN") is None
    assert needs_reorder(inv, "W1", "UNKNOWN") is False

    # Validation on override inputs.
    try:
        set_warehouse_reorder_point(inv, "W1", "S1", -1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "non-negative" in str(e)

    try:
        set_warehouse_reorder_point(inv, "UNKNOWN", "S1", 5)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Unknown warehouse" in str(e)

    try:
        set_warehouse_reorder_point(inv, "W1", "UNKNOWN", 5)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Unknown sku" in str(e)

    try:
        reserve_stock(inv, "W1", "S1", 1000)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Insufficient" in str(e)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
