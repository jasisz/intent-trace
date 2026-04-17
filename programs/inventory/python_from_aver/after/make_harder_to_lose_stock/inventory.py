"""Multi-warehouse inventory with per-SKU stock levels, reservations, and reorder policy.

Available stock is tracked separately from reserved stock so that pending orders do not
silently over-commit the same physical units. Operations raise ValueError on invalid
input (unknown warehouse/sku, non-positive qty, over-commit).

Reservation traceability
------------------------
Every reservation is persisted as a :class:`Reservation` record keyed by a unique id
returned from :func:`reserve_stock`. Releases and fulfilments reference that id rather
than a raw quantity, so a reservation cannot be made and then silently forgotten:

* Live reservations are always enumerable via :func:`active_reservations`.
* Operators can find reservations that have been open too long with
  :func:`stale_reservations` (timestamp-aware; pure, no wall-clock calls inside).
* Stale reservations can be expired in bulk via :func:`expire_stale_reservations`,
  which atomically releases the reserved units and returns the list of expired
  records so the caller can log / alert on them.

Time is always passed in explicitly (as an integer epoch-like value) to keep these
functions pure and easy to test; the module never reads the clock itself.

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

* Reservations are individually tracked records, not a bare reserved counter.
  A plain counter loses every property that would let operators find
  abandoned orders: identity, age, and which caller owns the commitment. A
  downstream crash that never calls release or fulfill would silently freeze
  stock forever. Storing reservations as first-class records with an id and a
  ``created_at`` timestamp lets release/fulfill address a specific reservation
  and lets ``stale_reservations`` surface old ones. The cost is a richer
  ``Inventory`` and more verbose callers, which we accept: losing stock
  silently is strictly worse than forcing callers to name what they committed
  to. Alternatives considered: reserved counter only, external reservation
  ledger.
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
class Reservation:
    """A single open reservation. Immutable; expired/fulfilled entries are dropped."""

    id: str
    warehouse_id: str
    sku_id: str
    qty: int
    created_at: int


@dataclass(frozen=True)
class Inventory:
    skus: dict[str, Sku] = field(default_factory=dict)
    warehouses: dict[str, Warehouse] = field(default_factory=dict)
    levels: dict[str, dict[str, StockLevel]] = field(default_factory=dict)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    # Monotonic counter used to mint reservation ids deterministically.
    next_reservation_seq: int = 1


def empty_inventory() -> Inventory:
    """Build an empty inventory with no warehouses, SKUs, stock, or reservations."""
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


def reserve_stock(
    inv: Inventory, warehouse_id: str, sku_id: str, qty: int, now: int
) -> tuple[Inventory, str]:
    """Reserve qty for an order and return the new inventory plus a reservation id.

    The caller is obliged to hold on to the returned id: it is the only way to
    release or fulfil this reservation, which makes it structurally hard to "lose"
    a reservation without leaving a trace in :attr:`Inventory.reservations`.

    Raises ``ValueError`` if qty is non-positive, the warehouse/sku is unknown, or
    the requested quantity would exceed available stock.
    """
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

    reservation_id = f"R{inv.next_reservation_seq}"
    record = Reservation(
        id=reservation_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        qty=qty,
        created_at=now,
    )
    inv_with_level = set_level(
        inv,
        warehouse_id,
        sku_id,
        StockLevel(on_hand=lvl.on_hand, reserved=lvl.reserved + qty),
    )
    inv_with_record = replace(
        inv_with_level,
        reservations={**inv_with_level.reservations, reservation_id: record},
        next_reservation_seq=inv.next_reservation_seq + 1,
    )
    return inv_with_record, reservation_id


def get_reservation(inv: Inventory, reservation_id: str) -> Reservation:
    """Return the reservation record, or raise ValueError if unknown."""
    record = inv.reservations.get(reservation_id)
    if record is None:
        raise ValueError(f"Unknown reservation: {reservation_id}")
    return record


def _drop_reservation(inv: Inventory, reservation_id: str) -> Inventory:
    """Remove a reservation record from the ledger without touching stock levels."""
    new_reservations = {k: v for k, v in inv.reservations.items() if k != reservation_id}
    return replace(inv, reservations=new_reservations)


def release_reservation(inv: Inventory, reservation_id: str) -> Inventory:
    """Cancel a reservation in full and return the units to availability.

    Because reservations are referenced by id (not by raw warehouse/sku/qty), the
    caller cannot accidentally release "some" phantom quantity: either the id
    exists or the operation fails loudly.
    """
    record = get_reservation(inv, reservation_id)
    lvl = get_level(inv, record.warehouse_id, record.sku_id)
    if record.qty > lvl.reserved:
        # Defensive: should never happen if reservations are the single source of
        # truth, but guards against external tampering with levels.
        raise ValueError(
            f"Reservation {reservation_id} inconsistent with level: "
            f"qty {record.qty} > reserved {lvl.reserved}"
        )
    inv_with_level = set_level(
        inv,
        record.warehouse_id,
        record.sku_id,
        StockLevel(on_hand=lvl.on_hand, reserved=lvl.reserved - record.qty),
    )
    return _drop_reservation(inv_with_level, reservation_id)


def fulfill_reservation(inv: Inventory, reservation_id: str) -> Inventory:
    """Ship the reserved units out: remove from both reserved and on-hand."""
    record = get_reservation(inv, reservation_id)
    lvl = get_level(inv, record.warehouse_id, record.sku_id)
    if record.qty > lvl.reserved:
        raise ValueError(
            f"Reservation {reservation_id} inconsistent with level: "
            f"qty {record.qty} > reserved {lvl.reserved}"
        )
    inv_with_level = set_level(
        inv,
        record.warehouse_id,
        record.sku_id,
        StockLevel(on_hand=lvl.on_hand - record.qty, reserved=lvl.reserved - record.qty),
    )
    return _drop_reservation(inv_with_level, reservation_id)


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


def active_reservations(inv: Inventory) -> list[Reservation]:
    """All currently open reservations, ordered by creation time then id.

    This is the primary audit hook: operators can enumerate every outstanding
    reservation at any moment. A reservation that exists but was forgotten will
    still show up here.
    """
    return sorted(inv.reservations.values(), key=lambda r: (r.created_at, r.id))


def stale_reservations(inv: Inventory, now: int, max_age: int) -> list[Reservation]:
    """Reservations whose age at ``now`` is strictly greater than ``max_age``.

    Pure function — the caller supplies the current time, making the behaviour
    deterministic and trivially testable. ``max_age`` must be non-negative.
    """
    if max_age < 0:
        raise ValueError("max_age must be non-negative")
    cutoff = now - max_age
    return sorted(
        (r for r in inv.reservations.values() if r.created_at < cutoff),
        key=lambda r: (r.created_at, r.id),
    )


def expire_stale_reservations(
    inv: Inventory, now: int, max_age: int
) -> tuple[Inventory, list[Reservation]]:
    """Release every reservation older than ``max_age`` and report which were expired.

    Returns ``(new_inventory, expired)``. The returned list preserves the order
    produced by :func:`stale_reservations`, so callers can log / alert on each
    expiry deterministically. If no reservations are stale, the inventory is
    returned unchanged.
    """
    expired = stale_reservations(inv, now, max_age)
    new_inv = inv
    for record in expired:
        new_inv = release_reservation(new_inv, record.id)
    return new_inv, expired


def _smoke_tests() -> None:
    inv = empty_inventory()
    inv = register_warehouse(inv, Warehouse(id="W1", location="NYC"))
    inv = register_warehouse(inv, Warehouse(id="W2", location="LA"))
    inv = register_sku(inv, Sku(id="S1", name="Widget", reorder_point=10))

    assert available_stock(inv, "W1", "S1") == 0

    inv = receive_shipment(inv, "W1", "S1", 20)
    assert available_stock(inv, "W1", "S1") == 20

    # --- reservation lifecycle by id ---
    inv, r1 = reserve_stock(inv, "W1", "S1", 5, now=100)
    assert r1 == "R1"
    assert available_stock(inv, "W1", "S1") == 15
    assert get_level(inv, "W1", "S1").reserved == 5
    assert get_reservation(inv, r1).qty == 5
    assert get_reservation(inv, r1).created_at == 100

    # Fulfilment drops the reservation record and decrements on-hand + reserved.
    inv = fulfill_reservation(inv, r1)
    assert get_level(inv, "W1", "S1").on_hand == 15
    assert get_level(inv, "W1", "S1").reserved == 0
    assert r1 not in inv.reservations
    try:
        fulfill_reservation(inv, r1)
        raise AssertionError("expected ValueError for unknown reservation id")
    except ValueError as e:
        assert "Unknown reservation" in str(e)

    # Second reservation, then release by id.
    inv, r2 = reserve_stock(inv, "W1", "S1", 4, now=110)
    assert r2 == "R2"
    assert get_level(inv, "W1", "S1").reserved == 4
    inv = release_reservation(inv, r2)
    assert get_level(inv, "W1", "S1").reserved == 0
    assert r2 not in inv.reservations

    inv = rebalance(inv, "W1", "W2", "S1", 5)
    assert get_level(inv, "W1", "S1").on_hand == 10
    assert get_level(inv, "W2", "S1").on_hand == 5

    assert total_on_hand(inv, "S1") == 15
    assert needs_reorder(inv, "W2", "S1") is True
    assert needs_reorder(inv, "W1", "S1") is True

    try:
        reserve_stock(inv, "W1", "S1", 1000, now=200)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Insufficient" in str(e)

    # --- stale reservation detection and bulk expiry ---
    inv, r_old = reserve_stock(inv, "W1", "S1", 2, now=1000)
    inv, r_mid = reserve_stock(inv, "W2", "S1", 1, now=1500)
    inv, r_new = reserve_stock(inv, "W1", "S1", 3, now=1900)
    assert [r.id for r in active_reservations(inv)] == [r_old, r_mid, r_new]

    # Nothing stale yet when max_age is generous.
    assert stale_reservations(inv, now=2000, max_age=10_000) == []
    # With a 600-unit window at t=2000, cutoff=1400: r_old (1000) qualifies, r_mid (1500) does not.
    stale = stale_reservations(inv, now=2000, max_age=600)
    assert [r.id for r in stale] == [r_old]

    # Expire everything older than 200 units at t=2000 -> cutoff=1800 -> r_old and r_mid.
    inv_after, expired = expire_stale_reservations(inv, now=2000, max_age=200)
    assert [r.id for r in expired] == [r_old, r_mid]
    assert r_old not in inv_after.reservations
    assert r_mid not in inv_after.reservations
    assert r_new in inv_after.reservations
    # Released units are back in availability at their respective warehouses.
    assert get_level(inv_after, "W1", "S1").reserved == 3  # only r_new left on W1
    assert get_level(inv_after, "W2", "S1").reserved == 0

    # Calling expire again with the same window is a no-op.
    inv_again, expired_again = expire_stale_reservations(inv_after, now=2000, max_age=200)
    assert expired_again == []
    assert inv_again.reservations == inv_after.reservations

    # max_age must be non-negative.
    try:
        stale_reservations(inv_after, now=2000, max_age=-1)
        raise AssertionError("expected ValueError for negative max_age")
    except ValueError as e:
        assert "non-negative" in str(e)

    # Unknown reservation id on release also fails loudly.
    try:
        release_reservation(inv_after, "R-does-not-exist")
        raise AssertionError("expected ValueError for unknown reservation id")
    except ValueError as e:
        assert "Unknown reservation" in str(e)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
