# Change request: separate audit trail from core report state

The `Report` currently mixes two things: the current lifecycle state (status, title, amount, etc.) and the full audit history (events). That works for small projects but makes the shape of `Report` ambiguous — callers doing pure business-logic queries get the audit payload whether they want it or not.

Refactor so that the core report state is its own type (no events embedded), and the audit log is tracked alongside as a separate concern. Callers that only need to know the current state shouldn't carry the event list; callers that care about history should still be able to get it.

Preserve all existing transition semantics (draft/submitted/approved/rejected/paid) and keep recording every transition in the audit log. The split should be structural, not behavioral — what a transition does should be identical; where the audit record lives should change.
