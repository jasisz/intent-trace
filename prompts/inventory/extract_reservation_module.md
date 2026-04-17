# Change request: separate reservation handling from inventory

The `Inventory` type and its module are doing too much: they manage warehouses, SKU definitions, on-hand stock, AND reservations, all in one data structure.

Extract reservation handling into its own concern. The core inventory (warehouses, SKUs, on-hand stock) should be usable without any concept of reservations; code that wants to reserve stock should go through a separate reservation-handling layer built on top of that core inventory.

Callers that only do `receiveShipment` / `receive_shipment` or query on-hand totals shouldn't have to know reservations exist. Callers that do use reservations should still have the same safety guarantees as before (can't over-reserve, can't release more than reserved, etc.).
