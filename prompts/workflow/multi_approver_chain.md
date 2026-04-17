# Change request: multi-approver chain for large amounts

Right now any single approver with sufficient authority can approve a report. For larger amounts, that's not safe: organizations typically require two or more approvers in a chain for expenses above a threshold.

Change the approval model so that reports above a certain amount threshold require multiple approvers in sequence — each approval moves the report one step closer to being fully approved, but the report doesn't transition to the `Approved` state until the last required approver signs off. Below the threshold, a single approver is still enough.

Keep the existing audit trail semantics (every approval is logged with its approver and timestamp). If any approver in the chain lacks sufficient authority, the step fails and the report stays in its intermediate state.
