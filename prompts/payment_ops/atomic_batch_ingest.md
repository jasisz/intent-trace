# Change request: atomic batch ingestion of webhooks

Webhooks arrive in bursts — sometimes 50-200 events for the same payment arrive in one polling interval. Currently each event is normalized and applied to the ledger one at a time; if the 47th event fails to normalize, the first 46 are already committed to state and operators are left with a half-updated payment.

Change the ingestion flow so a batch of raw webhooks is either fully applied or fully rejected. If any event in the batch fails normalization or ledger application, the entire batch is rolled back to the pre-batch state, and the operator gets a clear error pointing at the offending event.

Single-webhook ingestion can be expressed as a one-element batch — don't keep two parallel code paths.

Preserve the case-opening behavior: anomalies discovered within a successfully-applied batch still open manual-review cases.
