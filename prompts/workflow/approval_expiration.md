# Change request: approvals expire if not paid

Real approval workflows don't leave approvals open forever — if a report is approved but not paid within a bounded period, the approval should expire and the report needs re-approval before it can be paid.

Add an expiration mechanism. Each approval carries the timestamp of when it was granted. A new function checks, given a "now" timestamp and a maximum-age threshold, whether an approved report's approval has gone stale. A stale approval should move the report back to `Submitted` so it can go through approval again.

Don't break payment semantics for fresh approvals — `payReport` / `pay_report` should still work when the approval is within the age window.
