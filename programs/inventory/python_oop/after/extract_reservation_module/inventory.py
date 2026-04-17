"""Multi-warehouse inventory split into two structural layers.

``Inventory`` owns warehouses, SKU definitions, and on-hand stock; it knows
nothing about reservations. ``ReservedInventory`` wraps an ``Inventory`` and
layers per-warehouse/per-SKU reservation bookkeeping on top while preserving
the original safety guarantees (no over-reserving, no over-releasing, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass


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
class StockLevel:
    """Snapshot of on-hand and reserved units for a (warehouse, sku) pair."""

    on_hand: int = 0
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


# --------------------------------------------------------------------------- #
# Core inventory: warehouses, SKUs, on-hand stock (no reservations).          #
# --------------------------------------------------------------------------- #


class Inventory:
    """Core stock ledger: warehouses, SKUs, and on-hand units per warehouse.

    Callers that only receive shipments, rebalance, or query on-hand totals
    use this class directly and never need to know reservations exist.
    """

    def __init__(self) -> None:
        self._skus: dict[str, Sku] = {}
        self._warehouses: dict[str, Warehouse] = {}
        self._on_hand: dict[str, dict[str, int]] = {}

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
        self._on_hand.setdefault(warehouse.id, {})

    def known_warehouse(self, warehouse_id: str) -> bool:
        return warehouse_id in self._warehouses

    def known_sku(self, sku_id: str) -> bool:
        return sku_id in self._skus

    def sku(self, sku_id: str) -> Sku | None:
        return self._skus.get(sku_id)

    def warehouse_ids(self) -> list[str]:
        return list(self._warehouses)

    def on_hand(self, warehouse_id: str, sku_id: str) -> int:
        """Return on-hand units; an unseen (warehouse, sku) pair reads as zero."""
        per_sku = self._on_hand.get(warehouse_id)
        if per_sku is None:
            return 0
        return per_sku.get(sku_id, 0)

    def total_on_hand(self, sku_id: str) -> int:
        return sum(self.on_hand(wid, sku_id) for wid in self._warehouses)

    def receive_shipment(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        if qty <= 0:
            raise NonPositiveQtyError("Shipment")
        self._require_warehouse(warehouse_id)
        self._require_sku(sku_id)
        per_sku = self._on_hand.setdefault(warehouse_id, {})
        per_sku[sku_id] = per_sku.get(sku_id, 0) + qty

    def rebalance(self, from_id: str, to_id: str, sku_id: str, qty: int) -> None:
        """Transfer qty of on-hand stock between warehouses.

        This core-layer call moves physical stock only; it does not inspect
        reservations. ``ReservedInventory.rebalance`` offers the reservation-
        aware variant that refuses to ship away committed units.
        """
        if qty <= 0:
            raise NonPositiveQtyError("Rebalance")
        self._require_warehouse(from_id)
        self._require_warehouse(to_id)
        self._require_sku(sku_id)
        src_qty = self.on_hand(from_id, sku_id)
        if qty > src_qty:
            raise InsufficientStockError(qty, src_qty, at=from_id)
        src_bucket = self._on_hand.setdefault(from_id, {})
        dst_bucket = self._on_hand.setdefault(to_id, {})
        src_bucket[sku_id] = src_qty - qty
        dst_bucket[sku_id] = dst_bucket.get(sku_id, 0) + qty

    # Package-internal helper used by ReservedInventory to fulfill orders by
    # subtracting on-hand without requiring a separate public API.
    def _consume_on_hand(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        per_sku = self._on_hand.setdefault(warehouse_id, {})
        per_sku[sku_id] = per_sku.get(sku_id, 0) - qty

    def _require_warehouse(self, warehouse_id: str) -> None:
        if warehouse_id not in self._warehouses:
            raise UnknownWarehouseError(warehouse_id)

    def _require_sku(self, sku_id: str) -> None:
        if sku_id not in self._skus:
            raise UnknownSkuError(sku_id)


# --------------------------------------------------------------------------- #
# Reservation layer: sits on top of a core Inventory.                         #
# --------------------------------------------------------------------------- #


class ReservedInventory:
    """Reservation ledger layered on top of a core ``Inventory``.

    Invariant: for every (warehouse, sku), reserved units never exceed the
    on-hand units reported by the underlying core inventory.
    """

    def __init__(self, core: Inventory | None = None) -> None:
        self._core = core if core is not None else Inventory()
        self._reserved: dict[str, dict[str, int]] = {}

    @property
    def core(self) -> Inventory:
        return self._core

    # Convenience pass-throughs so callers that only touch this layer don't
    # need to reach into ``core`` for registration.
    def register_sku(self, sku: Sku) -> None:
        self._core.register_sku(sku)

    def register_warehouse(self, warehouse: Warehouse) -> None:
        self._core.register_warehouse(warehouse)

    def receive_shipment(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        self._core.receive_shipment(warehouse_id, sku_id, qty)

    def reserved(self, warehouse_id: str, sku_id: str) -> int:
        per_sku = self._reserved.get(warehouse_id)
        if per_sku is None:
            return 0
        return per_sku.get(sku_id, 0)

    def level(self, warehouse_id: str, sku_id: str) -> StockLevel:
        return StockLevel(
            on_hand=self._core.on_hand(warehouse_id, sku_id),
            reserved=self.reserved(warehouse_id, sku_id),
        )

    def available(self, warehouse_id: str, sku_id: str) -> int:
        return self._core.on_hand(warehouse_id, sku_id) - self.reserved(warehouse_id, sku_id)

    def needs_reorder(self, warehouse_id: str, sku_id: str) -> bool:
        """True when available stock at this warehouse is at or below the sku's reorder point."""
        sku = self._core.sku(sku_id)
        if sku is None:
            return False
        return self.available(warehouse_id, sku_id) <= sku.reorder_point

    def reserve(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        """Reserve qty against available stock; fails if it would over-commit."""
        if qty <= 0:
            raise NonPositiveQtyError("Reserve")
        self._core._require_warehouse(warehouse_id)
        self._core._require_sku(sku_id)
        avail = self.available(warehouse_id, sku_id)
        if qty > avail:
            raise InsufficientStockError(qty, avail)
        self._bump_reserved(warehouse_id, sku_id, qty)

    def release_reservation(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        if qty <= 0:
            raise NonPositiveQtyError("Release")
        current = self.reserved(warehouse_id, sku_id)
        if qty > current:
            raise InsufficientReservationError("release", qty, current)
        self._bump_reserved(warehouse_id, sku_id, -qty)

    def fulfill_reservation(self, warehouse_id: str, sku_id: str, qty: int) -> None:
        """Ship qty out: drop from both reserved and core on-hand."""
        if qty <= 0:
            raise NonPositiveQtyError("Fulfill")
        current = self.reserved(warehouse_id, sku_id)
        if qty > current:
            raise InsufficientReservationError("fulfill", qty, current)
        self._bump_reserved(warehouse_id, sku_id, -qty)
        self._core._consume_on_hand(warehouse_id, sku_id, qty)

    def rebalance(self, from_id: str, to_id: str, sku_id: str, qty: int) -> None:
        """Transfer unreserved stock between warehouses.

        Unlike ``Inventory.rebalance``, this refuses to move units already
        reserved at the source warehouse.
        """
        if qty <= 0:
            raise NonPositiveQtyError("Rebalance")
        self._core._require_warehouse(from_id)
        self._core._require_warehouse(to_id)
        self._core._require_sku(sku_id)
        avail = self.available(from_id, sku_id)
        if qty > avail:
            raise InsufficientStockError(qty, avail, at=from_id)
        self._core.rebalance(from_id, to_id, sku_id, qty)

    def total_on_hand(self, sku_id: str) -> int:
        return self._core.total_on_hand(sku_id)

    def _bump_reserved(self, warehouse_id: str, sku_id: str, delta: int) -> None:
        per_sku = self._reserved.setdefault(warehouse_id, {})
        per_sku[sku_id] = per_sku.get(sku_id, 0) + delta


# --------------------------------------------------------------------------- #
# Smoke tests                                                                 #
# --------------------------------------------------------------------------- #


def _smoke_core() -> None:
    """Exercise the core inventory layer without touching reservations."""
    inv = Inventory()
    inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    inv.register_warehouse(Warehouse(id="W2", location="LA"))
    inv.register_sku(Sku(id="S1", name="Widget", reorder_point=10))

    assert inv.warehouse_count == 2
    assert inv.sku_count == 1
    assert inv.known_warehouse("W1")
    assert not inv.known_warehouse("W9")
    assert inv.known_sku("S1")
    assert not inv.known_sku("S9")

    assert inv.on_hand("W1", "S1") == 0
    assert inv.total_on_hand("S1") == 0

    inv.receive_shipment("W1", "S1", 20)
    assert inv.on_hand("W1", "S1") == 20
    inv.receive_shipment("W2", "S1", 4)
    assert inv.total_on_hand("S1") == 24

    inv.rebalance("W1", "W2", "S1", 5)
    assert inv.on_hand("W1", "S1") == 15
    assert inv.on_hand("W2", "S1") == 9
    assert inv.total_on_hand("S1") == 24

    # Core layer is reservation-unaware: no reserve/release/fulfill on this object.
    assert not hasattr(inv, "reserve")
    assert not hasattr(inv, "release_reservation")
    assert not hasattr(inv, "fulfill_reservation")

    # Core-layer errors.
    try:
        inv.receive_shipment("W1", "S1", 0)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass
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
        inv.rebalance("W1", "W2", "S1", 1000)
        raise AssertionError("expected InsufficientStockError")
    except InsufficientStockError as e:
        assert "Insufficient" in str(e)


def _smoke_reservations() -> None:
    """Exercise the reservation layer end-to-end."""
    ri = ReservedInventory()
    ri.register_warehouse(Warehouse(id="W1", location="NYC"))
    ri.register_warehouse(Warehouse(id="W2", location="LA"))
    ri.register_sku(Sku(id="S1", name="Widget", reorder_point=10))

    assert ri.available("W1", "S1") == 0
    assert ri.reserved("W1", "S1") == 0
    assert ri.level("W1", "S1") == StockLevel(on_hand=0, reserved=0)

    ri.receive_shipment("W1", "S1", 20)
    assert ri.available("W1", "S1") == 20

    ri.reserve("W1", "S1", 5)
    assert ri.available("W1", "S1") == 15
    assert ri.level("W1", "S1") == StockLevel(on_hand=20, reserved=5)

    ri.fulfill_reservation("W1", "S1", 3)
    assert ri.level("W1", "S1") == StockLevel(on_hand=17, reserved=2)

    ri.release_reservation("W1", "S1", 2)
    assert ri.level("W1", "S1") == StockLevel(on_hand=17, reserved=0)

    ri.rebalance("W1", "W2", "S1", 5)
    assert ri.core.on_hand("W1", "S1") == 12
    assert ri.core.on_hand("W2", "S1") == 5

    assert ri.total_on_hand("S1") == 17
    assert ri.needs_reorder("W2", "S1") is True
    assert ri.needs_reorder("W1", "S1") is False
    assert ri.needs_reorder("W1", "S9") is False  # unknown sku -> False

    # Reservation-layer errors.
    try:
        ri.reserve("W1", "S1", 1000)
        raise AssertionError("expected InsufficientStockError")
    except InsufficientStockError as e:
        assert "Insufficient" in str(e)
    try:
        ri.reserve("W1", "S1", 0)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass
    try:
        ri.release_reservation("W1", "S1", 99)
        raise AssertionError("expected InsufficientReservationError")
    except InsufficientReservationError as e:
        assert "more than reserved" in str(e)
    try:
        ri.fulfill_reservation("W1", "S1", 99)
        raise AssertionError("expected InsufficientReservationError")
    except InsufficientReservationError as e:
        assert "more than reserved" in str(e)

    # Reserved-layer rebalance refuses to ship away units already committed.
    ri.reserve("W1", "S1", 10)
    try:
        ri.rebalance("W1", "W2", "S1", 5)
        raise AssertionError("expected InsufficientStockError: reserved units protected")
    except InsufficientStockError as e:
        assert "Insufficient" in str(e)

    # Sanity: the same state, poked through the raw core, *would* allow the move
    # (core is reservation-unaware). This demonstrates the layer separation.
    ri.core.rebalance("W1", "W2", "S1", 5)
    assert ri.core.on_hand("W1", "S1") == 7


def _smoke_wrap_existing_core() -> None:
    """ReservedInventory can wrap an externally built core Inventory."""
    core = Inventory()
    core.register_warehouse(Warehouse(id="W1", location="NYC"))
    core.register_sku(Sku(id="S1", name="Widget", reorder_point=10))
    core.receive_shipment("W1", "S1", 30)

    ri = ReservedInventory(core)
    assert ri.core is core
    assert ri.available("W1", "S1") == 30
    ri.reserve("W1", "S1", 12)
    assert ri.available("W1", "S1") == 18
    # The shared core reflects the layered state too.
    assert core.on_hand("W1", "S1") == 30


def _smoke_misc() -> None:
    """Property checks and exception hierarchy."""
    assert StockLevel(on_hand=10, reserved=3).available == 7
    assert StockLevel().available == 0

    assert issubclass(InsufficientStockError, InventoryError)
    assert issubclass(InsufficientReservationError, InventoryError)
    assert issubclass(UnknownSkuError, InventoryError)
    assert issubclass(UnknownWarehouseError, InventoryError)
    assert issubclass(NonPositiveQtyError, InventoryError)


def _smoke_tests() -> None:
    _smoke_core()
    _smoke_reservations()
    _smoke_wrap_existing_core()
    _smoke_misc()
    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
