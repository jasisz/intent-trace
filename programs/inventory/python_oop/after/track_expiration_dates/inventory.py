"""Multi-warehouse inventory with dated batches, reservations, and reorder policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class InventoryError(Exception):
    """Base class for inventory-related errors."""


class UnknownWarehouseError(InventoryError):
    def __init__(self, warehouse_id: str) -> None:
        super().__init__(f"Unknown warehouse: {warehouse_id}")
        self.warehouse_id = warehouse_id


class UnknownSkuError(InventoryError):
    def __init__(self, sku_id: str) -> None:
        super().__init__(f"Unknown sku: {sku_id}")
        self.sku_id = sku_id


class NonPositiveQtyError(InventoryError):
    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} qty must be positive")
        self.operation = operation


class InsufficientStockError(InventoryError):
    def __init__(self, needed: int, have: int, *, at: str | None = None) -> None:
        where = f" at {at}" if at else ""
        super().__init__(f"Insufficient available stock{where}: need {needed}, have {have}")
        self.needed = needed
        self.have = have


class InsufficientReservationError(InventoryError):
    def __init__(self, operation: str, requested: int, reserved: int) -> None:
        super().__init__(
            f"Cannot {operation} more than reserved: requested {requested}, reserved {reserved}"
        )
        self.requested = requested
        self.reserved = reserved


@dataclass(frozen=True)
class Sku:
    id: str
    name: str
    reorder_point: int


@dataclass(frozen=True)
class Warehouse:
    id: str
    location: str


@dataclass
class Batch:
    """A quantity of a SKU with a single expiration date."""
    qty: int
    expires_on: date


@dataclass
class StockLevel:
    """Dated batches (FIFO by expires_on) plus a scalar reservation count.

    `batches` is kept sorted by expires_on ascending so the oldest is at index 0.
    """
    batches: list[Batch] = field(default_factory=list)
    reserved: int = 0

    @property
    def on_hand(self) -> int:
        return sum(b.qty for b in self.batches)

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved

    def add_batch(self, qty: int, expires_on: date) -> None:
        """Insert a new batch, merging with an existing batch of the same date."""
        for b in self.batches:
            if b.expires_on == expires_on:
                b.qty += qty
                return
        for i, b in enumerate(self.batches):
            if expires_on < b.expires_on:
                self.batches.insert(i, Batch(qty=qty, expires_on=expires_on))
                return
        self.batches.append(Batch(qty=qty, expires_on=expires_on))

    def consume_fifo(self, qty: int) -> list[Batch]:
        """Remove `qty` units from the oldest batches; returns the consumed slices.

        Caller must ensure sum(b.qty) >= qty.
        """
        remaining = qty
        taken: list[Batch] = []
        while remaining > 0 and self.batches:
            head = self.batches[0]
            if head.qty <= remaining:
                taken.append(Batch(qty=head.qty, expires_on=head.expires_on))
                remaining -= head.qty
                self.batches.pop(0)
            else:
                taken.append(Batch(qty=remaining, expires_on=head.expires_on))
                head.qty -= remaining
                remaining = 0
        return taken


class Inventory:
    """Multi-warehouse stock ledger: tracks SKUs, warehouses, and dated batches per (warehouse, sku)."""

    def __init__(self) -> None:
        self._skus: dict[str, Sku] = {}
        self._warehouses: dict[str, Warehouse] = {}
        self._levels: dict[str, dict[str, StockLevel]] = {}

    @property
    def warehouse_count(self) -> int:
        return len(self._warehouses)

    @property
    def sku_count(self) -> int:
        return len(self._skus)

    def register_sku(self, sku: Sku) -> None:
        self._skus[sku.id] = sku

    def register_warehouse(self, warehouse: Warehouse) -> None:
        self._warehouses[warehouse.id] = warehouse
        self._levels.setdefault(warehouse.id, {})

    def level(self, warehouse_id: str, sku_id: str) -> StockLevel:
        """Return the current stock level; an unseen (warehouse, sku) pair reads as empty."""
        per_sku = self._levels.get(warehouse_id)
        if per_sku is None:
            return StockLevel()
        return per_sku.get(sku_id, StockLevel())

    def available(self, warehouse_id: str, sku_id: str) -> int:
        return self.level(warehouse_id, sku_id).available

    def total_on_hand(self, sku_id: str) -> int:
        return sum(self.level(wid, sku_id).on_hand for wid in self._warehouses)

    def needs_reorder(self, warehouse_id: str, sku_id: str) -> bool:
        """True when available stock at this warehouse is at or below the sku's reorder point."""
        sku = self._skus.get(sku_id)
        if sku is None:
            return False
        return self.available(warehouse_id, sku_id) <= sku.reorder_point

    def expired_quantity(self, warehouse_id: str, sku_id: str, today: date) -> int:
        """Total on-hand units in batches whose expires_on is strictly before `today`.

        Does not mutate inventory; downstream code decides the purge policy.
        """
        lvl = self.level(warehouse_id, sku_id)
        return sum(b.qty for b in lvl.batches if b.expires_on < today)

    def receive_shipment(
        self, warehouse_id: str, sku_id: str, qty: int, expires_on: date
    ) -> None:
        """Add a dated batch to on-hand at this warehouse."""
        if qty <= 0:
            raise NonPositiveQtyError("Shipment")
        self._require_warehouse(warehouse_id)
        self._require_sku(sku_id)
        lvl = self._mutable_level(warehouse_id, sku_id)
        lvl.add_batch(qty, expires_on)

    def reserve(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        """Reserve qty against available stock; fails if it would over-commit."""
        if qty <= 0:
            raise NonPositiveQtyError("Reserve")
        self._require_warehouse(warehouse_id)
        self._require_sku(sku_id)
        lvl = self._mutable_level(warehouse_id, sku_id)
        if qty > lvl.available:
            raise InsufficientStockError(qty, lvl.available)
        lvl.reserved += qty

    def release_reservation(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        if qty <= 0:
            raise NonPositiveQtyError("Release")
        lvl = self._mutable_level(warehouse_id, sku_id)
        if qty > lvl.reserved:
            raise InsufficientReservationError("release", qty, lvl.reserved)
        lvl.reserved -= qty

    def fulfill_reservation(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        """Ship qty out, consuming the oldest batches first (FIFO by expires_on)."""
        if qty <= 0:
            raise NonPositiveQtyError("Fulfill")
        lvl = self._mutable_level(warehouse_id, sku_id)
        if qty > lvl.reserved:
            raise InsufficientReservationError("fulfill", qty, lvl.reserved)
        lvl.consume_fifo(qty)
        lvl.reserved -= qty

    def rebalance(self, from_id: str, to_id: str, sku_id: str, qty: int) -> None:
        """Transfer qty of unreserved stock between warehouses, oldest batches first."""
        if qty <= 0:
            raise NonPositiveQtyError("Rebalance")
        self._require_warehouse(from_id)
        self._require_warehouse(to_id)
        self._require_sku(sku_id)
        src = self._mutable_level(from_id, sku_id)
        if qty > src.available:
            raise InsufficientStockError(qty, src.available, at=from_id)
        moved = src.consume_fifo(qty)
        dst = self._mutable_level(to_id, sku_id)
        for b in moved:
            dst.add_batch(b.qty, b.expires_on)

    def _require_warehouse(self, warehouse_id: str) -> None:
        if warehouse_id not in self._warehouses:
            raise UnknownWarehouseError(warehouse_id)

    def _require_sku(self, sku_id: str) -> None:
        if sku_id not in self._skus:
            raise UnknownSkuError(sku_id)

    def _mutable_level(self, warehouse_id: str, sku_id: str) -> StockLevel:
        per_sku = self._levels.setdefault(warehouse_id, {})
        lvl = per_sku.get(sku_id)
        if lvl is None:
            lvl = StockLevel()
            per_sku[sku_id] = lvl
        return lvl


def _smoke_tests() -> None:
    inv = Inventory()
    inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    inv.register_warehouse(Warehouse(id="W2", location="LA"))
    inv.register_sku(Sku(id="S1", name="Widget", reorder_point=10))

    assert inv.warehouse_count == 2
    assert inv.sku_count == 1
    assert inv.available("W1", "S1") == 0

    d_old = date(2026, 1, 15)
    d_mid = date(2026, 3, 1)
    d_new = date(2026, 6, 30)

    inv.receive_shipment("W1", "S1", 20, d_mid)
    assert inv.available("W1", "S1") == 20
    assert inv.level("W1", "S1").on_hand == 20

    # Second shipment at same warehouse — tracked independently.
    inv.receive_shipment("W1", "S1", 8, d_old)
    lvl = inv.level("W1", "S1")
    assert lvl.on_hand == 28
    assert len(lvl.batches) == 2
    assert lvl.batches[0].expires_on == d_old  # oldest first
    assert lvl.batches[1].expires_on == d_mid

    inv.reserve("W1", "S1", 5)
    assert inv.available("W1", "S1") == 23
    assert inv.level("W1", "S1").reserved == 5

    # FIFO fulfillment: pulls from the d_old batch first.
    inv.fulfill_reservation("W1", "S1", 3)
    lvl = inv.level("W1", "S1")
    assert lvl.on_hand == 25
    assert lvl.reserved == 2
    assert lvl.batches[0].expires_on == d_old
    assert lvl.batches[0].qty == 5  # 8 - 3

    # Fulfill enough to drain the oldest batch and dip into the next.
    inv.fulfill_reservation("W1", "S1", 2)
    lvl = inv.level("W1", "S1")
    assert lvl.reserved == 0
    assert lvl.on_hand == 23
    assert lvl.batches[0].expires_on == d_old  # 3 units left in old batch
    assert lvl.batches[0].qty == 3

    inv.reserve("W1", "S1", 4)
    inv.fulfill_reservation("W1", "S1", 4)
    lvl = inv.level("W1", "S1")
    # d_old had 3 left -> drained; remaining 1 came from d_mid.
    assert lvl.batches[0].expires_on == d_mid
    assert lvl.batches[0].qty == 19
    assert lvl.on_hand == 19

    # Release reservation leaves batches untouched.
    inv.reserve("W1", "S1", 7)
    assert inv.level("W1", "S1").reserved == 7
    inv.release_reservation("W1", "S1", 7)
    assert inv.level("W1", "S1").reserved == 0
    assert inv.level("W1", "S1").on_hand == 19

    # Merging shipments with the same date keeps a single batch.
    inv.receive_shipment("W1", "S1", 6, d_mid)
    lvl = inv.level("W1", "S1")
    assert len(lvl.batches) == 1
    assert lvl.batches[0].qty == 25

    # Rebalance carries batch dates with it, oldest moved first.
    inv.receive_shipment("W1", "S1", 4, d_old)
    inv.rebalance("W1", "W2", "S1", 5)
    w1 = inv.level("W1", "S1")
    w2 = inv.level("W2", "S1")
    assert w1.on_hand == 24
    assert w2.on_hand == 5
    # W2 first received the 4 d_old units, then 1 more from d_mid.
    assert w2.batches[0].expires_on == d_old
    assert w2.batches[0].qty == 4
    assert w2.batches[1].expires_on == d_mid
    assert w2.batches[1].qty == 1

    assert inv.total_on_hand("S1") == 29
    assert inv.needs_reorder("W2", "S1") is True
    assert inv.needs_reorder("W1", "S1") is False

    # Expiration query — strictly-before semantics.
    today = date(2026, 2, 1)
    # W1 now holds only the d_mid batch.
    assert inv.level("W1", "S1").batches[0].expires_on == d_mid
    assert inv.expired_quantity("W1", "S1", today) == 0
    # At W2 there are 4 units of d_old (2026-01-15).
    assert inv.expired_quantity("W2", "S1", today) == 4
    # On the d_old date itself, strictly-before means nothing is expired.
    assert inv.expired_quantity("W2", "S1", date(2026, 1, 15)) == 0
    # After d_mid also expires, both batches at W2 count.
    assert inv.expired_quantity("W2", "S1", date(2026, 7, 1)) == 5

    # Add a d_new shipment and confirm it sorts to the end.
    inv.receive_shipment("W2", "S1", 2, d_new)
    w2 = inv.level("W2", "S1")
    assert [b.expires_on for b in w2.batches] == [d_old, d_mid, d_new]

    try:
        inv.reserve("W1", "S1", 1000)
        raise AssertionError("expected InsufficientStockError")
    except InsufficientStockError as e:
        assert "Insufficient" in str(e)

    try:
        inv.receive_shipment("W9", "S1", 1, d_mid)
        raise AssertionError("expected UnknownWarehouseError")
    except UnknownWarehouseError:
        pass

    try:
        inv.receive_shipment("W1", "S9", 1, d_mid)
        raise AssertionError("expected UnknownSkuError")
    except UnknownSkuError:
        pass

    try:
        inv.reserve("W1", "S1", 0)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass

    try:
        inv.receive_shipment("W1", "S1", 0, d_mid)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass

    try:
        inv.fulfill_reservation("W1", "S1", 1)
        raise AssertionError("expected InsufficientReservationError")
    except InsufficientReservationError:
        pass

    # StockLevel.available property on dated batches.
    sl = StockLevel(batches=[Batch(qty=10, expires_on=d_mid)], reserved=3)
    assert sl.on_hand == 10
    assert sl.available == 7

    # All custom exceptions descend from InventoryError.
    assert issubclass(InsufficientStockError, InventoryError)
    assert issubclass(UnknownSkuError, InventoryError)
    assert issubclass(InsufficientReservationError, InventoryError)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
