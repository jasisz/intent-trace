"""Multi-warehouse inventory with per-SKU stock levels, reservations, and reorder policy.

Batch operations (``apply_batch``) run atomically: either every operation in the batch
commits, or none do. On failure, ``apply_batch`` raises ``BatchError`` identifying the
offending op; the inventory is left in its pre-batch state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union


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


# --- Batch operation descriptors ---------------------------------------------


@dataclass(frozen=True)
class ShipmentOp:
    warehouse_id: str
    sku_id: str
    qty: int


@dataclass(frozen=True)
class ReserveOp:
    warehouse_id: str
    sku_id: str
    qty: int


@dataclass(frozen=True)
class ReleaseOp:
    warehouse_id: str
    sku_id: str
    qty: int


@dataclass(frozen=True)
class FulfillOp:
    warehouse_id: str
    sku_id: str
    qty: int


@dataclass(frozen=True)
class RebalanceOp:
    from_id: str
    to_id: str
    sku_id: str
    qty: int


BatchOp = Union[ShipmentOp, ReserveOp, ReleaseOp, FulfillOp, RebalanceOp]


class BatchError(InventoryError):
    """Raised when an operation inside ``apply_batch`` fails.

    The inventory is left in its pre-batch state. ``index`` is the zero-based
    position of the failing op; ``op`` is its descriptor; ``cause`` is the
    underlying InventoryError.
    """

    def __init__(self, index: int, op: BatchOp, cause: BaseException) -> None:
        self.index = index
        self.op = op
        self.cause = cause
        super().__init__(
            f"Batch operation {index} ({type(op).__name__}) failed: {cause}"
        )


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

    def apply_batch(self, ops: list[BatchOp]) -> None:
        """Apply every op atomically; rollback to pre-batch state on any failure.

        Raises ``BatchError`` with the failing op's index; inventory is unchanged.
        """
        snapshot = self._snapshot_levels()
        for i, op in enumerate(ops):
            try:
                self._dispatch(op)
            except InventoryError as exc:
                self._restore_levels(snapshot)
                raise BatchError(i, op, exc) from exc

    def _dispatch(self, op: BatchOp) -> None:
        if isinstance(op, ShipmentOp):
            self.receive_shipment(op.warehouse_id, op.sku_id, op.qty)
        elif isinstance(op, ReserveOp):
            self.reserve(op.warehouse_id, op.sku_id, op.qty)
        elif isinstance(op, ReleaseOp):
            self.release_reservation(op.warehouse_id, op.sku_id, op.qty)
        elif isinstance(op, FulfillOp):
            self.fulfill_reservation(op.warehouse_id, op.sku_id, op.qty)
        elif isinstance(op, RebalanceOp):
            self.rebalance(op.from_id, op.to_id, op.sku_id, op.qty)
        else:
            raise TypeError(f"Unknown batch op: {type(op).__name__}")

    def _snapshot_levels(self) -> dict[str, dict[str, StockLevel]]:
        """Deep copy of the per-warehouse/per-sku levels for rollback."""
        return {
            wid: {sid: StockLevel(lvl.on_hand, lvl.reserved) for sid, lvl in per_sku.items()}
            for wid, per_sku in self._levels.items()
        }

    def _restore_levels(self, snapshot: dict[str, dict[str, StockLevel]]) -> None:
        self._levels = {
            wid: {sid: StockLevel(lvl.on_hand, lvl.reserved) for sid, lvl in per_sku.items()}
            for wid, per_sku in snapshot.items()
        }

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
    assert issubclass(BatchError, InventoryError)

    # --- Atomic batch: success path -----------------------------------------
    # Multi-SKU reservation for a 3-line customer order, all committed together.
    base = Inventory()
    base.register_warehouse(Warehouse(id="W1", location="NYC"))
    base.register_sku(Sku(id="A", name="Alpha", reorder_point=0))
    base.register_sku(Sku(id="B", name="Beta", reorder_point=0))
    base.register_sku(Sku(id="C", name="Gamma", reorder_point=0))
    base.receive_shipment("W1", "A", 10)
    base.receive_shipment("W1", "B", 10)
    base.receive_shipment("W1", "C", 10)

    base.apply_batch(
        [
            ReserveOp("W1", "A", 2),
            ReserveOp("W1", "B", 3),
            ReserveOp("W1", "C", 4),
        ]
    )
    assert base.level("W1", "A").reserved == 2
    assert base.level("W1", "B").reserved == 3
    assert base.level("W1", "C").reserved == 4

    # Batch of shipments (single truck, many SKUs).
    base.apply_batch(
        [
            ShipmentOp("W1", "A", 5),
            ShipmentOp("W1", "B", 7),
        ]
    )
    assert base.level("W1", "A").on_hand == 15
    assert base.level("W1", "B").on_hand == 17

    # --- Atomic batch: rollback on partial failure --------------------------
    rollback_inv = Inventory()
    rollback_inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    rollback_inv.register_sku(Sku(id="A", name="Alpha", reorder_point=0))
    rollback_inv.register_sku(Sku(id="B", name="Beta", reorder_point=0))
    rollback_inv.register_sku(Sku(id="C", name="Gamma", reorder_point=0))
    rollback_inv.receive_shipment("W1", "A", 10)
    rollback_inv.receive_shipment("W1", "B", 10)
    rollback_inv.receive_shipment("W1", "C", 10)

    # Third op would over-commit SKU C; first two must NOT stick.
    try:
        rollback_inv.apply_batch(
            [
                ReserveOp("W1", "A", 2),
                ReserveOp("W1", "B", 3),
                ReserveOp("W1", "C", 999),  # fails here
            ]
        )
        raise AssertionError("expected BatchError")
    except BatchError as e:
        assert e.index == 2
        assert isinstance(e.op, ReserveOp)
        assert e.op.sku_id == "C"
        assert isinstance(e.cause, InsufficientStockError)
        assert "Batch operation 2" in str(e)

    # Caller's inventory unchanged after the failed batch.
    assert rollback_inv.level("W1", "A").reserved == 0
    assert rollback_inv.level("W1", "B").reserved == 0
    assert rollback_inv.level("W1", "C").reserved == 0
    assert rollback_inv.level("W1", "A").on_hand == 10
    assert rollback_inv.level("W1", "B").on_hand == 10
    assert rollback_inv.level("W1", "C").on_hand == 10

    # Rollback also applies to rebalance batches that fail midway.
    two_wh = Inventory()
    two_wh.register_warehouse(Warehouse(id="W1", location="NYC"))
    two_wh.register_warehouse(Warehouse(id="W2", location="LA"))
    two_wh.register_sku(Sku(id="A", name="Alpha", reorder_point=0))
    two_wh.register_sku(Sku(id="B", name="Beta", reorder_point=0))
    two_wh.receive_shipment("W1", "A", 10)
    two_wh.receive_shipment("W1", "B", 10)
    try:
        two_wh.apply_batch(
            [
                RebalanceOp("W1", "W2", "A", 4),
                RebalanceOp("W1", "W2", "B", 50),  # fails: only 10 at W1
            ]
        )
        raise AssertionError("expected BatchError")
    except BatchError as e:
        assert e.index == 1
        assert isinstance(e.op, RebalanceOp)
    # Nothing moved.
    assert two_wh.level("W1", "A").on_hand == 10
    assert two_wh.level("W2", "A").on_hand == 0
    assert two_wh.level("W1", "B").on_hand == 10
    assert two_wh.level("W2", "B").on_hand == 0

    # Unknown-sku op in a batch also rolls back preceding ops.
    err_inv = Inventory()
    err_inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    err_inv.register_sku(Sku(id="A", name="Alpha", reorder_point=0))
    err_inv.receive_shipment("W1", "A", 5)
    try:
        err_inv.apply_batch(
            [
                ShipmentOp("W1", "A", 3),
                ShipmentOp("W1", "ZZZ", 1),  # unknown sku
            ]
        )
        raise AssertionError("expected BatchError")
    except BatchError as e:
        assert e.index == 1
        assert isinstance(e.cause, UnknownSkuError)
    assert err_inv.level("W1", "A").on_hand == 5  # first op rolled back

    # Empty batch is a no-op.
    noop_inv = Inventory()
    noop_inv.register_warehouse(Warehouse(id="W1", location="NYC"))
    noop_inv.apply_batch([])
    assert noop_inv.warehouse_count == 1

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
