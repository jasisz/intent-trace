"""Multi-warehouse inventory with dated batches, reservations, and reorder policy.

Stock at a (warehouse, sku) is tracked as an ordered collection of dated batches
rather than a single on-hand count. Reservations remain a scalar count and are
effectively backed by the oldest batches (FIFO by expiration date) at fulfillment
time. Operations raise ValueError on invalid input (unknown warehouse/sku,
non-positive qty, over-commit).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date


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
class Batch:
    """A quantity of a SKU with a single expiration date."""
    qty: int
    expires_on: date


@dataclass(frozen=True)
class StockLevel:
    """Dated batches (FIFO by expires_on) plus a scalar reservation count.

    `batches` is maintained sorted by expires_on ascending so the oldest batch
    is always at index 0.
    """
    batches: tuple[Batch, ...] = ()
    reserved: int = 0

    @property
    def on_hand(self) -> int:
        return sum(b.qty for b in self.batches)


@dataclass(frozen=True)
class Inventory:
    skus: dict[str, Sku] = field(default_factory=dict)
    warehouses: dict[str, Warehouse] = field(default_factory=dict)
    levels: dict[str, dict[str, StockLevel]] = field(default_factory=dict)


def empty_inventory() -> Inventory:
    return Inventory()


def register_sku(inv: Inventory, s: Sku) -> Inventory:
    return replace(inv, skus={**inv.skus, s.id: s})


def register_warehouse(inv: Inventory, w: Warehouse) -> Inventory:
    return replace(inv, warehouses={**inv.warehouses, w.id: w})


def get_level(inv: Inventory, warehouse_id: str, sku_id: str) -> StockLevel:
    per_sku = inv.levels.get(warehouse_id)
    if per_sku is None:
        return StockLevel()
    return per_sku.get(sku_id, StockLevel())


def set_level(inv: Inventory, warehouse_id: str, sku_id: str, lvl: StockLevel) -> Inventory:
    per_sku = dict(inv.levels.get(warehouse_id, {}))
    per_sku[sku_id] = lvl
    new_levels = {**inv.levels, warehouse_id: per_sku}
    return replace(inv, levels=new_levels)


def available_stock(inv: Inventory, warehouse_id: str, sku_id: str) -> int:
    lvl = get_level(inv, warehouse_id, sku_id)
    return lvl.on_hand - lvl.reserved


def known_warehouse(inv: Inventory, warehouse_id: str) -> bool:
    return warehouse_id in inv.warehouses


def known_sku(inv: Inventory, sku_id: str) -> bool:
    return sku_id in inv.skus


def _insert_batch(batches: tuple[Batch, ...], new_batch: Batch) -> tuple[Batch, ...]:
    """Insert `new_batch` keeping the tuple sorted by expires_on ascending.

    Batches with identical expires_on are merged so we don't fragment stock.
    """
    merged: list[Batch] = []
    inserted = False
    for b in batches:
        if not inserted and b.expires_on == new_batch.expires_on:
            merged.append(Batch(qty=b.qty + new_batch.qty, expires_on=b.expires_on))
            inserted = True
        elif not inserted and new_batch.expires_on < b.expires_on:
            merged.append(new_batch)
            merged.append(b)
            inserted = True
        else:
            merged.append(b)
    if not inserted:
        merged.append(new_batch)
    return tuple(merged)


def _consume_fifo(batches: tuple[Batch, ...], qty: int) -> tuple[Batch, ...]:
    """Drop `qty` units from the head (oldest) of the batch list.

    Caller must ensure sum(b.qty) >= qty.
    """
    remaining = qty
    out: list[Batch] = []
    for b in batches:
        if remaining == 0:
            out.append(b)
        elif b.qty <= remaining:
            remaining -= b.qty
        else:
            out.append(Batch(qty=b.qty - remaining, expires_on=b.expires_on))
            remaining = 0
    return tuple(out)


def receive_shipment(
    inv: Inventory,
    warehouse_id: str,
    sku_id: str,
    qty: int,
    expires_on: date,
) -> Inventory:
    """Add a dated batch to on-hand. Raises on unknown warehouse/sku or non-positive qty."""
    if qty <= 0:
        raise ValueError("Shipment qty must be positive")
    if not known_warehouse(inv, warehouse_id):
        raise ValueError(f"Unknown warehouse: {warehouse_id}")
    if not known_sku(inv, sku_id):
        raise ValueError(f"Unknown sku: {sku_id}")
    lvl = get_level(inv, warehouse_id, sku_id)
    new_batches = _insert_batch(lvl.batches, Batch(qty=qty, expires_on=expires_on))
    return set_level(inv, warehouse_id, sku_id, StockLevel(batches=new_batches, reserved=lvl.reserved))


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
    return set_level(inv, warehouse_id, sku_id, StockLevel(batches=lvl.batches, reserved=lvl.reserved + qty))


def release_reservation(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Cancel a previously made reservation. Raises if qty exceeds current reserved."""
    if qty <= 0:
        raise ValueError("Release qty must be positive")
    lvl = get_level(inv, warehouse_id, sku_id)
    if qty > lvl.reserved:
        raise ValueError(f"Cannot release more than reserved: requested {qty}, reserved {lvl.reserved}")
    return set_level(inv, warehouse_id, sku_id, StockLevel(batches=lvl.batches, reserved=lvl.reserved - qty))


def fulfill_reservation(inv: Inventory, warehouse_id: str, sku_id: str, qty: int) -> Inventory:
    """Ship qty out, consuming the oldest batches first (FIFO by expires_on).

    Raises if qty exceeds current reserved.
    """
    if qty <= 0:
        raise ValueError("Fulfill qty must be positive")
    lvl = get_level(inv, warehouse_id, sku_id)
    if qty > lvl.reserved:
        raise ValueError(f"Cannot fulfill more than reserved: requested {qty}, reserved {lvl.reserved}")
    new_batches = _consume_fifo(lvl.batches, qty)
    return set_level(inv, warehouse_id, sku_id, StockLevel(batches=new_batches, reserved=lvl.reserved - qty))


def rebalance(inv: Inventory, from_id: str, to_id: str, sku_id: str, qty: int) -> Inventory:
    """Transfer qty of unreserved stock between warehouses, oldest batches first."""
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
    moved = _taken_slice(from_lvl.batches, qty)
    remaining_from = _consume_fifo(from_lvl.batches, qty)
    to_lvl = get_level(inv, to_id, sku_id)
    merged_to = to_lvl.batches
    for b in moved:
        merged_to = _insert_batch(merged_to, b)
    with_from = set_level(
        inv, from_id, sku_id, StockLevel(batches=remaining_from, reserved=from_lvl.reserved)
    )
    return set_level(
        with_from, to_id, sku_id, StockLevel(batches=merged_to, reserved=to_lvl.reserved)
    )


def _taken_slice(batches: tuple[Batch, ...], qty: int) -> tuple[Batch, ...]:
    """Return the batches (in FIFO order) that would satisfy `qty` from the head."""
    remaining = qty
    out: list[Batch] = []
    for b in batches:
        if remaining == 0:
            break
        if b.qty <= remaining:
            out.append(b)
            remaining -= b.qty
        else:
            out.append(Batch(qty=remaining, expires_on=b.expires_on))
            remaining = 0
    return tuple(out)


def needs_reorder(inv: Inventory, warehouse_id: str, sku_id: str) -> bool:
    """True when available stock at this warehouse is at or below the sku's reorder point."""
    sku = inv.skus.get(sku_id)
    if sku is None:
        return False
    return available_stock(inv, warehouse_id, sku_id) <= sku.reorder_point


def total_on_hand(inv: Inventory, sku_id: str) -> int:
    """Sum on-hand across all warehouses for this sku."""
    return sum(get_level(inv, wid, sku_id).on_hand for wid in inv.warehouses)


def expired_quantity(inv: Inventory, warehouse_id: str, sku_id: str, today: date) -> int:
    """Total on-hand units in batches whose expires_on is strictly before `today`.

    Does not mutate inventory; purge policy is the caller's responsibility.
    """
    lvl = get_level(inv, warehouse_id, sku_id)
    return sum(b.qty for b in lvl.batches if b.expires_on < today)


def _smoke_tests() -> None:
    inv = empty_inventory()
    inv = register_warehouse(inv, Warehouse(id="W1", location="NYC"))
    inv = register_warehouse(inv, Warehouse(id="W2", location="LA"))
    inv = register_sku(inv, Sku(id="S1", name="Widget", reorder_point=10))

    assert available_stock(inv, "W1", "S1") == 0

    d_old = date(2026, 1, 15)
    d_mid = date(2026, 3, 1)
    d_new = date(2026, 6, 30)

    inv = receive_shipment(inv, "W1", "S1", 20, d_mid)
    assert available_stock(inv, "W1", "S1") == 20
    assert get_level(inv, "W1", "S1").on_hand == 20

    # Second shipment at same warehouse — tracked independently.
    inv = receive_shipment(inv, "W1", "S1", 8, d_old)
    lvl = get_level(inv, "W1", "S1")
    assert lvl.on_hand == 28
    assert len(lvl.batches) == 2
    assert lvl.batches[0].expires_on == d_old  # oldest first
    assert lvl.batches[1].expires_on == d_mid

    inv = reserve_stock(inv, "W1", "S1", 5)
    assert available_stock(inv, "W1", "S1") == 23
    assert get_level(inv, "W1", "S1").reserved == 5

    # FIFO fulfillment: pulls from the d_old batch first.
    inv = fulfill_reservation(inv, "W1", "S1", 3)
    lvl = get_level(inv, "W1", "S1")
    assert lvl.on_hand == 25
    assert lvl.reserved == 2
    assert lvl.batches[0].expires_on == d_old
    assert lvl.batches[0].qty == 5  # 8 - 3

    # Fulfill enough to drain the oldest batch and dip into the next.
    inv = fulfill_reservation(inv, "W1", "S1", 2)
    lvl = get_level(inv, "W1", "S1")
    assert lvl.reserved == 0
    assert lvl.on_hand == 23
    assert lvl.batches[0].expires_on == d_old  # 3 units left in old batch
    assert lvl.batches[0].qty == 3

    inv = reserve_stock(inv, "W1", "S1", 4)
    inv = fulfill_reservation(inv, "W1", "S1", 4)
    lvl = get_level(inv, "W1", "S1")
    # d_old had 3 left -> drained; remaining 1 came from d_mid.
    assert lvl.batches[0].expires_on == d_mid
    assert lvl.batches[0].qty == 19
    assert lvl.on_hand == 19

    # Release reservation leaves batches untouched.
    inv = reserve_stock(inv, "W1", "S1", 7)
    assert get_level(inv, "W1", "S1").reserved == 7
    inv = release_reservation(inv, "W1", "S1", 7)
    assert get_level(inv, "W1", "S1").reserved == 0
    assert get_level(inv, "W1", "S1").on_hand == 19

    # Merging shipments with the same date keeps a single batch.
    inv = receive_shipment(inv, "W1", "S1", 6, d_mid)
    lvl = get_level(inv, "W1", "S1")
    assert len(lvl.batches) == 1
    assert lvl.batches[0].qty == 25

    # Rebalance carries batch dates with it, oldest moved first.
    inv = receive_shipment(inv, "W1", "S1", 4, d_old)
    inv = rebalance(inv, "W1", "W2", "S1", 5)
    w1 = get_level(inv, "W1", "S1")
    w2 = get_level(inv, "W2", "S1")
    assert w1.on_hand == 24
    assert w2.on_hand == 5
    # W2 first received the 4 d_old units, then 1 more from d_mid.
    assert w2.batches[0].expires_on == d_old
    assert w2.batches[0].qty == 4
    assert w2.batches[1].expires_on == d_mid
    assert w2.batches[1].qty == 1

    assert total_on_hand(inv, "S1") == 29
    assert needs_reorder(inv, "W2", "S1") is True
    assert needs_reorder(inv, "W1", "S1") is False

    # Expiration query — strictly-before semantics.
    today = date(2026, 2, 1)
    assert expired_quantity(inv, "W1", "S1", today) == 0  # only d_mid and d_new remain at W1? no, d_old 0 left at W1
    # W1 has only the merged d_mid batch now (d_old was drained by rebalance + fulfills).
    assert get_level(inv, "W1", "S1").batches[0].expires_on == d_mid
    # At W2 there are 4 units of d_old (2026-01-15).
    assert expired_quantity(inv, "W2", "S1", today) == 4
    # Before d_old expires, nothing is expired.
    assert expired_quantity(inv, "W2", "S1", date(2026, 1, 15)) == 0
    # After d_mid also expires, both batches at W2 count.
    assert expired_quantity(inv, "W2", "S1", date(2026, 7, 1)) == 5

    # Add a d_new shipment and confirm it sorts to the end.
    inv = receive_shipment(inv, "W2", "S1", 2, d_new)
    w2 = get_level(inv, "W2", "S1")
    assert [b.expires_on for b in w2.batches] == [d_old, d_mid, d_new]

    try:
        reserve_stock(inv, "W1", "S1", 1000)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Insufficient" in str(e)

    try:
        receive_shipment(inv, "W1", "S1", 0, d_mid)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "positive" in str(e)

    try:
        fulfill_reservation(inv, "W1", "S1", 1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "reserved" in str(e)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
