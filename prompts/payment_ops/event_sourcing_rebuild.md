# Change request: reconstruct payment state strictly from event log

The current ledger computes `PaymentState` by accumulating fields (authorized, captured, refunded amounts) as events arrive — the state is derived, but the current code interleaves derivation with the event-application step.

Refactor so that there is a single pure function `rebuildPaymentState(events: List<PaymentEvent>) -> PaymentState` that reconstructs the full state from scratch given a list of events, in order. All other ledger operations should compose on top of this single rebuilder — no independent field-updating helpers that could drift out of sync.

The observable behavior must not change: the same sequence of events produces the same final state as today.

The benefit this unlocks is replay: an operator troubleshooting a payment can ask "what would the state look like if event X had never arrived?" and get an answer by filtering the event list and re-invoking the rebuilder.
