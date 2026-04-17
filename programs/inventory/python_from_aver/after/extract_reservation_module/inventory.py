"""Multi-warehouse inventory with on-hand stock and a separate reservation layer.

The module is split into two concerns that share this single file:

1. Core inventory (``Inventory``): warehouses, SKU definitions, and on-hand stock.
   Callers that only receive shipments, rebalance, or query on-hand totals use this
   layer exclusively and need no knowledge of reservations.

2. Reservation layer (``ReservedInventory``): wraps a core ``Inventory`` and tracks
   per-warehouse/per-SKU reservations on top of it. Callers that reserve, release,
   or fulfill orders go through this layer, which preserves the original safety
   guarantees (no over-reservation, no releasing more than reserved, etc.).

Operations raise ``ValueError`` on invalid input (unknown warehouse/sku,
non-positive qty, over-commit).

Design decisions:

* Reservations are separated from the core inventory. The original design put
  both on-hand stock and pending reservations on a single record, which
  conflated two concerns: a warehouse-management view (where are units
  sitting?) and an order-acceptance view (how many units are already
  promised?). Splitting them lets the core ``Inventory`` stay usable on its
  own — shipping, receiving, and reporting do not need to know reservations
  exist — while reservations are layered on top as a separate record that
  holds a reference to a core ``Inventory`` plus a per-(warehouse, sku)
  reserved counter. Alternatives considered: keep one combined record,
  reservations as a separate parallel store, optional reserved field.

* Available stock and reserved stock stay tracked separately (reserved lives
  on the reservation layer; on-hand lives on the core). Collapsing reserved
  into available would allow a second reservation to over-commit the same
  physical units; keeping the two counters apart makes the invariant
  reserved <= on_hand checkable at any point. Alternatives considered: single
  available counter, negative available as reserved.

* Invalid operations raise ValueError rather than returning a tagged result.
  Unknown warehouse/sku, non-positive quantities, and over-reservations are
  all expected at runtime, and Python callers branch on them via try/except.
  (The Aver source returns Result to make failure modes explicit in the call
  site; here the Pythonic equivalent is a narrow exception type.)
  Alternatives considered: silent no-op, panic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


# --------------------------------------------------------------------------- #
# Core inventory: warehouses, SKUs, on-hand stock (no reservations).          #
# --------------------------------------------------------------------------- #


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
class Inventory:
    """Core inventory state. Knows nothing about reservations."""

    skus: dict[str, Sku] = field(default_factory=dict)
    warehouses: dict[str, Warehouse] = field(default_factory=dict)
    on_hand: dict[str, dict[str, int]] = field(default_factory=dict)


def empty_inventory() -> Inventory:
    """Build an empty core inventory with no warehouses, SKUs, or on-hand stock."""
    return Inventory()


def register_sku(inv: Inventory, s: Sku) -> Inventory:
    """Add or replace an SKU definition in the core inventory."""
    return replace(inv, skus={**inv.skus, s.id: s})


def register_warehouse(inv: Inventory, w: Warehouse) -> Inventory:
    """Add or replace a warehouse definition in the core inventory."""
    return replace(inv, warehouses={**inv.warehouses, w.id: w})


def known_warehouse(inv: Inventory, warehouse_id: str) -> bool:
    """True if the warehouse has been registered in the core inventory."""
    return warehouse_id in inv.warehouses


def known_sku(inv: Inventory, sku_id: str) -> bool:
    """True if the SKU has been registered in the core inventory."""
    return sku_id in inv.skus


def get_on_hand(inv: Inventory, warehouse_id: str, sku_id: str) -> int:
    """Return the on-hand quantity at (warehouse, sku); zero if none recorded."""
    per_sku = inv.on_hand.get(warehouse_id)
    if per_sku is None:
        return 0
    return per_sku.get(sku_id, 0)


def set_on_hand(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Store an on-hand quantity for (warehouse, sku); overwrite any previous value."""
    per_sku = dict(inv.on_hand.get(warehouse_id, {}))
    per_sku[sku_id] = qty
    new_on_hand = {**inv.on_hand, warehouse_id: per_sku}
    return replace(inv, on_hand=new_on_hand)


def receive_shipment(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Add qty to on-hand. Raises ValueError on unknown warehouse/sku or non-positive qty."""
    if qty <= 0:
        raise ValueError("Shipment qty must be positive")
    if not known_warehouse(inv, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    current = get_on_hand(inv, warehouse_id, sku_id)
    return set_on_hand(inv, warehouse_id, sku_id, current + qty)


def rebalance(inv: Inventory, from_id: str, to_id: str, sku_id: str, qty: int) -> Inventory:
    """Transfer qty of on-hand stock between warehouses.

    This core-layer function moves physical stock only; it does not inspect
    reservations. The reservation layer offers ``reserved_rebalance`` which
    guarantees reserved units are not shipped away.
    """
    if qty <= 0:
        raise ValueError("Rebalance qty must be positive")
    if not known_warehouse(inv, from_id):
        raise ValueError(f"Unknown warehouse: {from_id}")
    if not known_warehouse(inv, to_id):
        raise ValueError(f"Unknown warehouse: {to_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    from_qty = get_on_hand(inv, from_id, sku_id)
    if qty > from_qty:
        raise ValueError(f"Insufficient stock at {from_id}: need {qty}, have {from_qty}")
    to_qty = get_on_hand(inv, to_id, sku_id)
    with_from = set_on_hand(inv, from_id, sku_id, from_qty - qty)
    return set_on_hand(with_from, to_id, sku_id, to_qty + qty)


def total_on_hand(inv: Inventory, sku_id: str) -> int:
    """Sum on-hand across all warehouses for this sku."""
    return sum(get_on_hand(inv, wid, sku_id) for wid in inv.warehouses)


# --------------------------------------------------------------------------- #
# Reservation layer: sits on top of a core Inventory and tracks reservations. #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReservedInventory:
    """Inventory with a reservation ledger layered on top.

    ``core`` holds the warehouse/SKU/on-hand state exactly as the reservation-unaware
    core layer sees it. ``reserved`` is a parallel map ``{warehouse_id: {sku_id: qty}}``
    tracking how much of the on-hand stock is currently committed to orders.

    Invariant: for every (warehouse_id, sku_id), ``reserved[wid][sid] <= core.on_hand[wid][sid]``.
    """

    core: Inventory = field(default_factory=Inventory)
    reserved: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class StockLevel:
    """Snapshot of on-hand and reserved units for a (warehouse, sku) pair."""

    on_hand: int
    reserved: int


def empty_reserved_inventory() -> ReservedInventory:
    """Build an empty reservation-aware inventory wrapping an empty core."""
    return ReservedInventory()


def reserved_register_sku(ri: ReservedInventory, s: Sku) -> ReservedInventory:
    """Register an SKU on the underlying core inventory."""
    return replace(ri, core=register_sku(ri.core, s))


def reserved_register_warehouse(ri: ReservedInventory, w: Warehouse) -> ReservedInventory:
    """Register a warehouse on the underlying core inventory."""
    return replace(ri, core=register_warehouse(ri.core, w))


def get_reserved(ri: ReservedInventory, warehouse_id: str, sku_id: str) -> int:
    """Return the reserved quantity at (warehouse, sku); zero if none recorded."""
    per_sku = ri.reserved.get(warehouse_id)
    if per_sku is None:
        return 0
    return per_sku.get(sku_id, 0)


def _set_reserved(ri: ReservedInventory, warehouse_id: str, sku_id: str, qty: int) -> ReservedInventory:
    """Store a reserved quantity for (warehouse, sku); overwrite any previous value."""
    per_sku = dict(ri.reserved.get(warehouse_id, {}))
    per_sku[sku_id] = qty
    new_reserved = {**ri.reserved, warehouse_id: per_sku}
    return replace(ri, reserved=new_reserved)


def get_level(ri: ReservedInventory, warehouse_id: str, sku_id: str) -> StockLevel:
    """Return a combined snapshot of on-hand and reserved units at (warehouse, sku)."""
    return StockLevel(
        on_hand=get_on_hand(ri.core, warehouse_id, sku_id),
        reserved=get_reserved(ri, warehouse_id, sku_id),
    )


def available_stock(ri: ReservedInventory, warehouse_id: str, sku_id: str) -> int:
    """Return units available for new reservations: on_hand minus reserved."""
    return get_on_hand(ri.core, warehouse_id, sku_id) - get_reserved(ri, warehouse_id, sku_id)


def reserved_receive_shipment(
    ri: ReservedInventory, warehouse_id: str, sku_id: str, qty: int
) -> ReservedInventory:
    """Receive a shipment through the reservation layer (delegates to core)."""
    return replace(ri, core=receive_shipment(ri.core, warehouse_id, sku_id, qty))


def reserve_stock(
    ri: ReservedInventory, warehouse_id: str, sku_id: str, qty: int
) -> ReservedInventory:
    """Reserve qty for an order. Raises if qty would exceed available."""
    if qty <= 0:
        raise ValueError("Reserve qty must be positive")
    if not known_warehouse(ri.core, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(ri.core, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    avail = available_stock(ri, warehouse_id, sku_id)
    if qty > avail:
        raise ValueError(f"Insufficient available stock: need {qty}, have {avail}")
    current = get_reserved(ri, warehouse_id, sku_id)
    return _set_reserved(ri, warehouse_id, sku_id, current + qty)


def release_reservation(
    ri: ReservedInventory, warehouse_id: str, sku_id: str, qty: int
) -> ReservedInventory:
    """Cancel a previously made reservation. Raises if qty exceeds current reserved."""
    if qty <= 0:
        raise ValueError("Release qty must be positive")
    current = get_reserved(ri, warehouse_id, sku_id)
    if qty > current:
        raise ValueError(f"Cannot release more than reserved: requested {qty}, reserved {current}")
    return _set_reserved(ri, warehouse_id, sku_id, current - qty)


def fulfill_reservation(
    ri: ReservedInventory, warehouse_id: str, sku_id: str, qty: int
) -> ReservedInventory:
    """Ship qty out: remove from both reserved and on-hand. Raises if qty exceeds reserved."""
    if qty <= 0:
        raise ValueError("Fulfill qty must be positive")
    current_reserved = get_reserved(ri, warehouse_id, sku_id)
    if qty > current_reserved:
        raise ValueError(
            f"Cannot fulfill more than reserved: requested {qty}, reserved {current_reserved}"
        )
    current_on_hand = get_on_hand(ri.core, warehouse_id, sku_id)
    new_core = set_on_hand(ri.core, warehouse_id, sku_id, current_on_hand - qty)
    with_new_core = replace(ri, core=new_core)
    return _set_reserved(with_new_core, warehouse_id, sku_id, current_reserved - qty)


def reserved_rebalance(
    ri: ReservedInventory, from_id: str, to_id: str, sku_id: str, qty: int
) -> ReservedInventory:
    """Transfer unreserved stock between warehouses.

    Unlike the core ``rebalance``, this refuses to move units that are already
    reserved at the source warehouse.
    """
    if qty <= 0:
        raise ValueError("Rebalance qty must be positive")
    if not known_warehouse(ri.core, from_id):
        raise ValueError(f"Unknown warehouse: {from_id}")
    if not known_warehouse(ri.core, to_id):
        raise ValueError(f"Unknown warehouse: {to_id}")
    if not known_sku(ri.core, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    avail = available_stock(ri, from_id, sku_id)
    if qty > avail:
        raise ValueError(f"Insufficient stock at {from_id}: need {qty}, have {avail}")
    new_core = rebalance(ri.core, from_id, to_id, sku_id, qty)
    return replace(ri, core=new_core)


def needs_reorder(ri: ReservedInventory, warehouse_id: str, sku_id: str) -> bool:
    """True when available stock at this warehouse is at or below the sku's reorder point."""
    sku = ri.core.skus.get(sku_id)
    if sku is None:
        return False
    return available_stock(ri, warehouse_id, sku_id) <= sku.reorder_point


def reserved_total_on_hand(ri: ReservedInventory, sku_id: str) -> int:
    """Sum on-hand across all warehouses for this sku (ignoring reservations)."""
    return total_on_hand(ri.core, sku_id)


# --------------------------------------------------------------------------- #
# Smoke tests                                                                 #
# --------------------------------------------------------------------------- #


def _smoke_core() -> None:
    """Exercise the core inventory layer without touching reservations."""
    inv = empty_inventory()
    inv = register_warehouse(inv, Warehouse(id="W1", location="NYC"))
    inv = register_warehouse(inv, Warehouse(id="W2", location="LA"))
    inv = register_sku(inv, Sku(id="S1", name="Widget", reorder_point=10))

    assert known_warehouse(inv, "W1")
    assert not known_warehouse(inv, "W9")
    assert known_sku(inv, "S1")
    assert not known_sku(inv, "S9")

    assert get_on_hand(inv, "W1", "S1") == 0
    assert total_on_hand(inv, "S1") == 0

    inv = receive_shipment(inv, "W1", "S1", 20)
    assert get_on_hand(inv, "W1", "S1") == 20
    inv = receive_shipment(inv, "W2", "S1", 4)
    assert total_on_hand(inv, "S1") == 24

    inv = rebalance(inv, "W1", "W2", "S1", 5)
    assert get_on_hand(inv, "W1", "S1") == 15
    assert get_on_hand(inv, "W2", "S1") == 9
    assert total_on_hand(inv, "S1") == 24

    # Core-layer errors.
    try:
        receive_shipment(inv, "W1", "S1", 0)
        raise AssertionError("expected ValueError for non-positive qty")
    except ValueError as e:
        assert "positive" in str(e)
    try:
        receive_shipment(inv, "W9", "S1", 1)
        raise AssertionError("expected ValueError for unknown warehouse")
    except ValueError as e:
        assert "warehouse" in str(e).lower()
    try:
        receive_shipment(inv, "W1", "S9", 1)
        raise AssertionError("expected ValueError for unknown sku")
    except ValueError as e:
        assert "sku" in str(e).lower()
    try:
        rebalance(inv, "W1", "W2", "S1", 1000)
        raise AssertionError("expected ValueError for over-rebalance")
    except ValueError as e:
        assert "Insufficient" in str(e)


def _smoke_reservations() -> None:
    """Exercise the reservation layer end-to-end."""
    ri = empty_reserved_inventory()
    ri = reserved_register_warehouse(ri, Warehouse(id="W1", location="NYC"))
    ri = reserved_register_warehouse(ri, Warehouse(id="W2", location="LA"))
    ri = reserved_register_sku(ri, Sku(id="S1", name="Widget", reorder_point=10))

    assert available_stock(ri, "W1", "S1") == 0
    assert get_reserved(ri, "W1", "S1") == 0

    ri = reserved_receive_shipment(ri, "W1", "S1", 20)
    assert available_stock(ri, "W1", "S1") == 20
    assert get_level(ri, "W1", "S1") == StockLevel(on_hand=20, reserved=0)

    ri = reserve_stock(ri, "W1", "S1", 5)
    assert available_stock(ri, "W1", "S1") == 15
    assert get_level(ri, "W1", "S1") == StockLevel(on_hand=20, reserved=5)

    ri = fulfill_reservation(ri, "W1", "S1", 3)
    assert get_level(ri, "W1", "S1") == StockLevel(on_hand=17, reserved=2)

    ri = release_reservation(ri, "W1", "S1", 2)
    assert get_level(ri, "W1", "S1") == StockLevel(on_hand=17, reserved=0)

    ri = reserved_rebalance(ri, "W1", "W2", "S1", 5)
    assert get_on_hand(ri.core, "W1", "S1") == 12
    assert get_on_hand(ri.core, "W2", "S1") == 5

    assert reserved_total_on_hand(ri, "S1") == 17
    assert needs_reorder(ri, "W2", "S1") is True
    assert needs_reorder(ri, "W1", "S1") is False
    assert needs_reorder(ri, "W1", "S9") is False  # unknown sku → False

    # Reservation-layer errors.
    try:
        reserve_stock(ri, "W1", "S1", 1000)
        raise AssertionError("expected ValueError for over-reserve")
    except ValueError as e:
        assert "Insufficient" in str(e)
    try:
        reserve_stock(ri, "W1", "S1", 0)
        raise AssertionError("expected ValueError for non-positive reserve qty")
    except ValueError as e:
        assert "positive" in str(e)
    try:
        release_reservation(ri, "W1", "S1", 99)
        raise AssertionError("expected ValueError for over-release")
    except ValueError as e:
        assert "more than reserved" in str(e)
    try:
        fulfill_reservation(ri, "W1", "S1", 99)
        raise AssertionError("expected ValueError for over-fulfill")
    except ValueError as e:
        assert "more than reserved" in str(e)

    # Reserved rebalance refuses to move units already committed to an order.
    ri2 = reserve_stock(ri, "W1", "S1", 10)
    try:
        reserved_rebalance(ri2, "W1", "W2", "S1", 5)
        raise AssertionError("expected ValueError: reserved units must not be rebalanced")
    except ValueError as e:
        assert "Insufficient" in str(e)

    # Core-layer rebalance on the same state *would* succeed (it ignores reservations) —
    # this demonstrates the separation: raw core is unaware of reservation semantics.
    raw_after = rebalance(ri2.core, "W1", "W2", "S1", 5)
    assert get_on_hand(raw_after, "W1", "S1") == 7


def _smoke_tests() -> None:
    _smoke_core()
    _smoke_reservations()
    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
