# Change request: explicit multi-currency handling

The current reconcile/ledger logic assumes amounts share a single currency per payment. Production traffic doesn't work that way: a refund can come back in a different currency than the original capture (e.g., authorized in USD, captured in local currency, refunded in the currency Stripe sold).

Change the design so currency mismatches between events on the same payment are **made visible as anomalies** rather than silently accepted into sums. A payment state should carry its currency, and any event arriving with a different currency for that payment surfaces as an anomaly case (with enough detail for manual review).

Summing amounts across different currencies must fail explicitly; there should be no silent conversion happening inside the domain logic.

Preserve the single-currency happy path: existing payments in one currency continue to work unchanged.
