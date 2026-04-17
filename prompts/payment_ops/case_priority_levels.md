# Change request: priority levels for manual-review cases

Right now every manual-review case looks the same to operators — no way to tell a $50 duplicate refund apart from a $500k reconciliation break. Operators need priority.

Add a priority to each case: Low, Normal, High, Urgent. Priority is derived automatically from the case's kind and amount (large-amount anomalies become Urgent; routine mismatches stay Normal). Operators can also override priority manually when resolving or re-prioritizing.

Views that list cases must expose priority so downstream consumers can filter/sort. The case-resolution flow should preserve the priority in the audit trail.

Keep the existing case-kind semantics; this change adds a dimension, it doesn't replace anything.
