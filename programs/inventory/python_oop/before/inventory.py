"""Multi-warehouse inventory with per-SKU stock levels, reservations, and reorder policy."""
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
    on_hand: int = 0
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


class Inventory:
    """Multi-warehouse stock ledger: tracks SKUs, warehouses, and per-warehouse stock levels."""

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
        """Ship qty out: drop from both reserved and on-hand."""
        if qty <= 0:
            raise NonPositiveQtyError("Fulfill")
        lvl = self._mutable_level(warehouse_id, sku_id)
        if qty > lvl.reserved:
            raise InsufficientReservationError("fulfill", qty, lvl.reserved)
        lvl.reserved -= qty
        lvl.on_hand -= qty

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


def _smoke_tests() -> None:
    inv = Inventory()
    inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    inv.register_warehouse(Warehouse(id="W2", location="LA"))
    inv.register_sku(Sku(id="S1", name="Widget", reorder_point=10))

    assert inv.warehouse_count == 2
    assert inv.sku_count == 1
    assert inv.available("W1", "S1") == 0

    inv.receive_shipment("W1", "S1", 20)
    assert inv.available("W1", "S1") == 20

    inv.reserve("W1", "S1", 5)
    assert inv.available("W1", "S1") == 15
    assert inv.level("W1", "S1").reserved == 5

    inv.fulfill_reservation("W1", "S1", 3)
    assert inv.level("W1", "S1").on_hand == 17
    assert inv.level("W1", "S1").reserved == 2

    inv.release_reservation("W1", "S1", 2)
    assert inv.level("W1", "S1").reserved == 0

    inv.rebalance("W1", "W2", "S1", 5)
    assert inv.level("W1", "S1").on_hand == 12
    assert inv.level("W2", "S1").on_hand == 5

    assert inv.total_on_hand("S1") == 17
    assert inv.needs_reorder("W2", "S1") is True
    assert inv.needs_reorder("W1", "S1") is False

    try:
        inv.reserve("W1", "S1", 1000)
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
        inv.reserve("W1", "S1", 0)
        raise AssertionError("expected NonPositiveQtyError")
    except NonPositiveQtyError:
        pass

    # StockLevel.available property
    assert StockLevel(on_hand=10, reserved=3).available == 7

    # All custom exceptions descend from InventoryError
    assert issubclass(InsufficientStockError, InventoryError)
    assert issubclass(UnknownSkuError, InventoryError)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
