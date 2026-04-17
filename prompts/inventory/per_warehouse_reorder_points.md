# Change request: per-warehouse reorder points

Right now every warehouse uses the same reorder point for a given SKU, because the reorder point lives on the SKU itself. That's wrong for real operations: a warehouse in a big city has different demand than a regional one, so each warehouse should be able to set its own reorder point for each SKU.

Change the data model so reorder points are per (warehouse, SKU), not just per SKU. The `needsReorder` / `needs_reorder` function should use the warehouse-specific reorder point. Keep the SKU-level reorder point as a default that applies when a warehouse hasn't overridden it.
