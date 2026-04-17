# Change request: atomic batch operations

Add a way to perform multiple inventory operations as a single atomic batch. For example: reserve stock for a multi-line order where the customer wants 3 different SKUs — either all three reservations succeed, or none of them do and the inventory is left unchanged.

The same should work for batches of shipments received (e.g., a single truck delivering items for many SKUs), or for batches of rebalance moves.

If any operation in the batch would fail, the entire batch fails and returns a clear error about which operation caused it. No partial commits.

Preserve all existing single-operation behavior.
