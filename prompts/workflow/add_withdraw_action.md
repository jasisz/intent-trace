# Change request: allow submitter to withdraw a submitted report

Add a new action: the original submitter can withdraw their own report while it's `Submitted` (not yet approved or rejected). Withdrawal moves the report back to `Draft` so they can edit it and resubmit.

Only the original submitter is allowed to withdraw — attempts by other users should fail with a clear error. Withdrawing after approval/rejection/payment should also fail.

Every withdrawal must be logged in the audit trail. The submitter's original submission event should remain in the trail (history is not rewritten).
