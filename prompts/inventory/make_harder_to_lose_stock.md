# Change request: make it harder to lose track of stock

The current inventory has a subtle gap: reservations can be created and forgotten. If an order is reserved and then the downstream system crashes or never follows up, the reserved quantity stays reserved forever — silently unavailable for new customers, and nobody knows why.

Make this harder to happen. The system should be able to identify reservations that have been around too long, and make them visible to operators in some structured way — so that stale reservations can't quietly eat inventory indefinitely.

Use your judgment about what "structured way" means here — the important property is that you can no longer lose a reservation without leaving a trace.
