# Change request: track expiration dates on stock

Each shipment received should carry an expiration date. Stock is no longer a single `onHand` count per (warehouse, sku) — it's a collection of dated batches.

When fulfilling a reservation, ship the oldest stock first (FIFO by expiration date). When the same SKU is received twice at the same warehouse, both batches must be tracked independently — the earlier one is consumed first.

Also add an operation that returns the quantity of stock that has expired as of a given "today" date, so downstream code can decide what to do with it (purge, discount, etc.). Don't implement the purge policy — just surface the quantity.

Existing reservation semantics (reserved vs on-hand, release, cancel) should keep working, but now reservations effectively pull from specific batches in FIFO order.
