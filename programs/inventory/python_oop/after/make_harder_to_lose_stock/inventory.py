"""Multi-warehouse inventory with per-SKU stock levels, identifiable reservations, and reorder policy.

Reservations are first-class records keyed by a unique id returned from ``reserve``.
Release and fulfilment reference that id rather than a raw quantity, so a reservation
cannot be made and then silently forgotten: every open commitment is enumerable via
``active_reservations`` and stale ones can be discovered with ``stale_reservations``
or expired in bulk via ``expire_stale_reservations``. Time is always passed in
explicitly — the module never reads the clock itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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


class UnknownReservationError(InventoryError):
    def __init__(self, reservation_id: str) -> None:
        super().__init__(f"Unknown reservation: {reservation_id}")
        self.reservation_id = reservation_id


class NonPositiveQtyError(InventoryError):
    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} qty must be positive")
        self.operation = operation


class NegativeAgeError(InventoryError):
    def __init__(self) -> None:
        super().__init__("max_age must be non-negative")


class InsufficientStockError(InventoryError):
    def __init__(self, needed: int, have: int, *, at: str | None = None) -> None:
        where = f" at {at}" if at else ""
        super().__init__(f"Insufficient available stock{where}: need {needed}, have {have}")
        self.needed = needed
        self.have = have


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
class Reservation:
    """A single open reservation. Released and fulfilled reservations are dropped."""

    id: str
    warehouse_id: str
    sku_id: str
    qty: int
    created_at: int

    def age_at(self, now: int) -> int:
        return now - self.created_at


@dataclass
class StockLevel:
    on_hand: int = 0
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


class Inventory:
    """Multi-warehouse stock ledger: SKUs, warehouses, stock levels, and reservation records."""

    def __init__(self) -> None:
        self._skus: dict[str, Sku] = {}
        self._warehouses: dict[str, Warehouse] = {}
        self._levels: dict[str, dict[str, StockLevel]] = {}
        self._reservations: dict[str, Reservation] = {}
        self._next_reservation_seq: int = 1

    @property
    def warehouse_count(self) -> int:
        return len(self._warehouses)

    @property
    def sku_count(self) -> int:
        return len(self._skus)

    @property
    def reservation_count(self) -> int:
        return len(self._reservations)

    def register_sku(self, sku: Sku) -> None:
        self._skus[sku.id] = sku

    def register_warehouse(self, warehouse: Warehouse) -> None:
        self._warehouses[warehouse.id] = warehouse
        self._levels.setdefault(warehouse.id, {})

    def level(self, warehouse_id: str, sku_id: str) -> StockLevel:
        """Return the current stock level; an unseen (warehouse, sku) pair reads as zero."""
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

    def receive_shipment(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        if qty <= 0:
            raise NonPositiveQtyError("Shipment")
        self._require_warehouse(warehouse_id)
        self._require_sku(sku_id)
        lvl = self._mutable_level(warehouse_id, sku_id)
        lvl.on_hand += qty

    def reserve(self, warehouse_id: str, sku_id: str, qty: int, now: int) -> str:
        """Reserve qty and return a new reservation id.

        The returned id is the only handle for subsequent release or fulfilment:
        a reservation cannot be made and then silently forgotten because it
        always lives in ``_reservations`` until one of those calls drops it.
        """
        if qty <= 0:
            raise NonPositiveQtyError("Reserve")
        self._require_warehouse(warehouse_id)
        self._require_sku(sku_id)
        lvl = self._mutable_level(warehouse_id, sku_id)
        if qty > lvl.available:
            raise InsufficientStockError(qty, lvl.available)
        reservation_id = f"R{self._next_reservation_seq}"
        self._next_reservation_seq += 1
        self._reservations[reservation_id] = Reservation(
            id=reservation_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            qty=qty,
            created_at=now,
        )
        lvl.reserved += qty
        return reservation_id

    def get_reservation(self, reservation_id: str) -> Reservation:
        record = self._reservations.get(reservation_id)
        if record is None:
            raise UnknownReservationError(reservation_id)
        return record

    def release_reservation(self, reservation_id: str) -> None:
        """Cancel a reservation in full; the reserved units return to availability."""
        record = self._pop_reservation(reservation_id)
        lvl = self._mutable_level(record.warehouse_id, record.sku_id)
        # Defensive: reservations are the single source of truth for the reserved counter,
        # so this branch guards only against external tampering with levels.
        if record.qty > lvl.reserved:
            raise InsufficientStockError(record.qty, lvl.reserved, at=record.warehouse_id)
        lvl.reserved -= record.qty

    def fulfill_reservation(self, reservation_id: str) -> None:
        """Ship the reserved units out: drop the record and decrement both reserved and on-hand."""
        record = self._pop_reservation(reservation_id)
        lvl = self._mutable_level(record.warehouse_id, record.sku_id)
        if record.qty > lvl.reserved:
            raise InsufficientStockError(record.qty, lvl.reserved, at=record.warehouse_id)
        lvl.reserved -= record.qty
        lvl.on_hand -= record.qty

    def active_reservations(self) -> list[Reservation]:
        """All currently open reservations, ordered by creation time then id.

        Primary audit hook: an outstanding reservation always shows up here,
        even if its creator has gone silent.
        """
        return sorted(self._reservations.values(), key=lambda r: (r.created_at, r.id))

    def stale_reservations(self, now: int, max_age: int) -> list[Reservation]:
        """Reservations whose age at ``now`` is strictly greater than ``max_age``."""
        if max_age < 0:
            raise NegativeAgeError()
        cutoff = now - max_age
        return sorted(
            (r for r in self._reservations.values() if r.created_at < cutoff),
            key=lambda r: (r.created_at, r.id),
        )

    def expire_stale_reservations(self, now: int, max_age: int) -> list[Reservation]:
        """Release every reservation older than ``max_age`` and return the expired records.

        The ordering matches ``stale_reservations`` so callers can log or alert on
        each expiry deterministically. A no-op returns an empty list and leaves the
        inventory untouched.
        """
        expired = self.stale_reservations(now, max_age)
        for record in expired:
            self.release_reservation(record.id)
        return expired

    def rebalance(self, from_id: str, to_id: str, sku_id: str, qty: int) -> None:
        """Transfer qty of unreserved stock between warehouses."""
        if qty <= 0:
            raise NonPositiveQtyError("Rebalance")
        self._require_warehouse(from_id)
        self._require_warehouse(to_id)
        self._require_sku(sku_id)
        src = self._mutable_level(from_id, sku_id)
        if qty > src.available:
            raise InsufficientStockError(qty, src.available, at=from_id)
        dst = self._mutable_level(to_id, sku_id)
        src.on_hand -= qty
        dst.on_hand += qty

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

    def _pop_reservation(self, reservation_id: str) -> Reservation:
        record = self._reservations.pop(reservation_id, None)
        if record is None:
            raise UnknownReservationError(reservation_id)
        return record


def _smoke_tests() -> None:
    inv = Inventory()
    inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    inv.register_warehouse(Warehouse(id="W2", location="LA"))
    inv.register_sku(Sku(id="S1", name="Widget", reorder_point=10))

    assert inv.warehouse_count == 2
    assert inv.sku_count == 1
    assert inv.available("W1", "S1") == 0
    assert inv.reservation_count == 0

    inv.receive_shipment("W1", "S1", 20)
    assert inv.available("W1", "S1") == 20

    # --- reservation lifecycle by id ---
    r1 = inv.reserve("W1", "S1", 5, now=100)
    assert r1 == "R1"
    assert inv.available("W1", "S1") == 15
    assert inv.level("W1", "S1").reserved == 5
    rec1 = inv.get_reservation(r1)
    assert rec1.qty == 5
    assert rec1.created_at == 100
    assert rec1.age_at(150) == 50

    # Fulfilment drops the reservation record and decrements on-hand + reserved.
    inv.fulfill_reservation(r1)
    assert inv.level("W1", "S1").on_hand == 15
    assert inv.level("W1", "S1").reserved == 0
    assert inv.reservation_count == 0
    try:
        inv.fulfill_reservation(r1)
        raise AssertionError("expected UnknownReservationError")
    except UnknownReservationError as e:
        assert e.reservation_id == r1

    # Second reservation, then release by id.
    r2 = inv.reserve("W1", "S1", 4, now=110)
    assert r2 == "R2"
    assert inv.level("W1", "S1").reserved == 4
    inv.release_reservation(r2)
    assert inv.level("W1", "S1").reserved == 0
    try:
        inv.get_reservation(r2)
        raise AssertionError("expected UnknownReservationError")
    except UnknownReservationError:
        pass

    inv.rebalance("W1", "W2", "S1", 5)
    assert inv.level("W1", "S1").on_hand == 10
    assert inv.level("W2", "S1").on_hand == 5

    assert inv.total_on_hand("S1") == 15
    assert inv.needs_reorder("W2", "S1") is True
    assert inv.needs_reorder("W1", "S1") is True

    try:
        inv.reserve("W1", "S1", 1000, now=200)
        raise AssertionError("expected InsufficientStockError")
    except InsufficientStockError as e:
        assert "Insufficient" in str(e)

    try:
        inv.receive_shipment("W9", "S1", 1)
        raise AssertionError("expected UnknownWarehouseError")
    except UnknownWarehouseError:
        pass

    try:
        inv.receive_shipment("W1", "S9", 1)
        raise AssertionError("expected UnknownSkuError")
    except UnknownSkuError:
        pass

    try:
        inv.reserve("W1", "S1", 0, now=0)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass

    # StockLevel.available property
    assert StockLevel(on_hand=10, reserved=3).available == 7

    # All custom exceptions descend from InventoryError
    assert issubclass(InsufficientStockError, InventoryError)
    assert issubclass(UnknownSkuError, InventoryError)
    assert issubclass(UnknownReservationError, InventoryError)
    assert issubclass(NegativeAgeError, InventoryError)

    # --- stale reservation detection and bulk expiry ---
    r_old = inv.reserve("W1", "S1", 2, now=1000)
    r_mid = inv.reserve("W2", "S1", 1, now=1500)
    r_new = inv.reserve("W1", "S1", 3, now=1900)
    assert inv.reservation_count == 3
    assert [r.id for r in inv.active_reservations()] == [r_old, r_mid, r_new]

    # Nothing is stale when the max_age window is generous.
    assert inv.stale_reservations(now=2000, max_age=10_000) == []
    # With a 600-unit window at t=2000, cutoff=1400: r_old (1000) qualifies, r_mid (1500) does not.
    stale = inv.stale_reservations(now=2000, max_age=600)
    assert [r.id for r in stale] == [r_old]

    # Expire everything older than 200 units at t=2000 -> cutoff=1800 -> r_old and r_mid.
    expired = inv.expire_stale_reservations(now=2000, max_age=200)
    assert [r.id for r in expired] == [r_old, r_mid]
    # Released units are back in availability at their respective warehouses.
    assert inv.level("W1", "S1").reserved == 3  # only r_new left on W1
    assert inv.level("W2", "S1").reserved == 0
    # r_new is still tracked and accessible.
    assert inv.get_reservation(r_new).qty == 3
    try:
        inv.get_reservation(r_old)
        raise AssertionError("expected UnknownReservationError")
    except UnknownReservationError:
        pass

    # Calling expire again with the same window is a no-op.
    expired_again = inv.expire_stale_reservations(now=2000, max_age=200)
    assert expired_again == []
    assert inv.reservation_count == 1

    # max_age must be non-negative.
    try:
        inv.stale_reservations(now=2000, max_age=-1)
        raise AssertionError("expected NegativeAgeError")
    except NegativeAgeError:
        pass

    # Unknown reservation id on release also fails loudly.
    try:
        inv.release_reservation("R-does-not-exist")
        raise AssertionError("expected UnknownReservationError")
    except UnknownReservationError as e:
        assert e.reservation_id == "R-does-not-exist"

    # Active-reservations listing remains stable and ordered.
    assert [r.id for r in inv.active_reservations()] == [r_new]

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
