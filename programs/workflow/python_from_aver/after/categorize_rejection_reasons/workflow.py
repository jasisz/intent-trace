"""Expense-report approval workflow.

Each report moves through Draft → Submitted → (Approved or Rejected). Approved
reports can finally be Paid. Transitions are guarded: submitting requires a
non-empty title and positive amount; approval is gated by the approver's
authority limit; payment requires a prior approval. Every transition is
recorded in an audit trail attached to the report.

Rejections carry a categorized reason (one of a closed set) plus an optional
free-form note. The ``OTHER`` category requires a note so the audit trail
remains informative.

Design decisions:

* Status is a closed sum type (Enum), not a string. Expense reports have a
  small, fixed set of lifecycle states, and closing the type lets the
  type-checker flag any future code path that forgets to handle one.
  String-typed status fields would silently accept typos and new values.
  Alternatives considered: string status codes, integer status codes.

* The audit trail lives inline on the Report record, not in a separate store.
  Callers routinely need to show who did what when; keeping events on the
  report keeps replay and explanation queries cheap. A separate audit store
  would force every consumer to join two sources. Alternatives considered:
  external audit store, no audit log.

* Rejection reasons are a closed sum type (``RejectionCategory``) plus an
  optional note, not a free-form string. Free-form strings make aggregation
  unreliable: 'policy' and 'policy violation' read the same to a human but
  differ to code. A closed category gives analytics a clean key while the
  optional note keeps human context. The ``OTHER`` escape hatch still
  forces a note so no rejection stays unexplained. Alternatives considered:
  free-form strings, string enum codes, a map from category to note.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Status(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class RejectionCategory(Enum):
    BUDGET_EXCEEDED = "budget-exceeded"
    MISSING_DOCUMENTATION = "missing-documentation"
    POLICY_VIOLATION = "policy-violation"
    DUPLICATE_SUBMISSION = "duplicate-submission"
    OTHER = "other"


@dataclass(frozen=True)
class RejectionReason:
    """Categorized rejection reason with an optional free-form note.

    The category is drawn from a closed set (:class:`RejectionCategory`).
    ``OTHER`` requires a note; for the well-known categories the note is
    optional and simply adds context to the audit trail.
    """

    category: RejectionCategory
    note: str | None = None

    def __post_init__(self) -> None:
        if self.category is RejectionCategory.OTHER:
            if self.note is None or not self.note.strip():
                raise ValueError("RejectionCategory.OTHER requires a non-empty note")
        if self.note is not None and not isinstance(self.note, str):
            raise TypeError("RejectionReason note must be a string or None")


def rejection_reason(category: RejectionCategory, note: str | None = None) -> RejectionReason:
    """Convenience constructor mirroring the dataclass, for readability at call sites."""
    return RejectionReason(category=category, note=note)


def format_rejection_reason(reason: RejectionReason) -> str:
    """Render a rejection reason for the audit trail: category plus note if present."""
    if reason.note is not None and reason.note.strip():
        return f"{reason.category.value}: {reason.note}"
    return reason.category.value


@dataclass(frozen=True)
class Money:
    cents: int


@dataclass(frozen=True)
class User:
    id: str
    name: str
    approval_limit_cents: int


@dataclass(frozen=True)
class Event:
    timestamp_ms: int
    actor_id: str
    note: str


@dataclass(frozen=True)
class Report:
    id: str
    title: str
    amount: Money
    submitter_id: str
    status: Status
    events: tuple[Event, ...] = ()


def empty_report(report_id: str, submitter_id: str) -> Report:
    """Create a fresh Draft report with no title, zero amount, no events."""
    return Report(
        id=report_id,
        title="",
        amount=Money(cents=0),
        submitter_id=submitter_id,
        status=Status.DRAFT,
        events=(),
    )


def status_of(r: Report) -> Status:
    """Return the current lifecycle status of the report."""
    return r.status


def events_of(r: Report) -> list[Event]:
    """Return audit trail in chronological order (oldest first)."""
    return list(r.events)


def _record_event(r: Report, e: Event) -> Report:
    """Append an event to the report's audit trail."""
    return replace(r, events=r.events + (e,))


def with_title_and_amount(r: Report, title: str, amount: Money) -> Report:
    """Update title and amount on a Draft report. Not permitted after submission."""
    return replace(r, title=title, amount=amount)


def amount_is_positive(m: Money) -> bool:
    """True if the money amount is strictly positive."""
    return m.cents > 0


def title_is_present(r: Report) -> bool:
    """True if the report title is non-empty."""
    return len(r.title) > 0


def submit_report(r: Report, actor_id: str, timestamp_ms: int) -> Report:
    """Move a Draft report to Submitted. Raises ValueError if invariants fail."""
    if r.status is not Status.DRAFT:
        raise ValueError(f"Cannot submit from status {r.status.value}")
    if not title_is_present(r):
        raise ValueError("Report title must not be empty")
    if not amount_is_positive(r.amount):
        raise ValueError("Report amount must be positive")
    submitted = replace(r, status=Status.SUBMITTED)
    return _record_event(submitted, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="submitted"))


def _approver_has_authority(approver: User, amount: Money) -> bool:
    """True if the approver's limit covers the report amount."""
    return approver.approval_limit_cents >= amount.cents


def approve_report(r: Report, approver: User, timestamp_ms: int) -> Report:
    """Move a Submitted report to Approved. Approver must have sufficient authority."""
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot approve from status {r.status.value}")
    if not _approver_has_authority(approver, r.amount):
        raise ValueError(f"Approver {approver.id} limit too low for amount")
    approved = replace(r, status=Status.APPROVED)
    return _record_event(approved, Event(timestamp_ms=timestamp_ms, actor_id=approver.id, note="approved"))


def reject_report(r: Report, actor_id: str, reason: RejectionReason, timestamp_ms: int) -> Report:
    """Move a Submitted report to Rejected with a categorized reason."""
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot reject from status {r.status.value}")
    if not isinstance(reason, RejectionReason):
        raise TypeError("reason must be a RejectionReason")
    rejected = replace(r, status=Status.REJECTED)
    note = f"rejected: {format_rejection_reason(reason)}"
    return _record_event(rejected, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note=note))


def pay_report(r: Report, actor_id: str, timestamp_ms: int) -> Report:
    """Move an Approved report to Paid."""
    if r.status is not Status.APPROVED:
        raise ValueError(f"Cannot pay from status {r.status.value}")
    paid = replace(r, status=Status.PAID)
    return _record_event(paid, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="paid"))


def is_terminal(s: Status) -> bool:
    """True if the state does not permit further transitions."""
    return s is Status.PAID or s is Status.REJECTED


def _smoke_tests() -> None:
    r = empty_report("R1", "U1")
    assert status_of(r) is Status.DRAFT

    try:
        submit_report(r, "U1", 1000)
        raise AssertionError("expected empty title to fail")
    except ValueError as e:
        assert "title" in str(e).lower()

    r = with_title_and_amount(r, "Travel - Q2", Money(cents=45000))
    r = submit_report(r, "U1", 1000)
    assert status_of(r) is Status.SUBMITTED

    manager = User(id="M1", name="Manager", approval_limit_cents=100000)
    junior = User(id="J1", name="Junior", approval_limit_cents=10000)

    try:
        approve_report(r, junior, 1500)
        raise AssertionError("expected limit failure")
    except ValueError as e:
        assert "limit" in str(e).lower()

    r = approve_report(r, manager, 2000)
    assert status_of(r) is Status.APPROVED

    r = pay_report(r, "F1", 3000)
    assert status_of(r) is Status.PAID
    assert is_terminal(status_of(r))

    events = events_of(r)
    assert len(events) == 3
    assert events[0].note == "submitted"
    assert events[1].note == "approved"
    assert events[2].note == "paid"

    # Terminal states reject further transitions.
    try:
        pay_report(r, "F1", 4000)
        raise AssertionError("expected terminal rejection")
    except ValueError:
        pass

    # --- Rejection path: one case per category. -----------------------------

    def _reject_scenario(title: str, reason: RejectionReason) -> Report:
        rep = with_title_and_amount(empty_report("RX", "U2"), title, Money(cents=30000))
        rep = submit_report(rep, "U2", 1000)
        return reject_report(rep, manager.id, reason, 2000)

    # Budget exceeded, no note.
    r_budget = _reject_scenario(
        "Office chair",
        rejection_reason(RejectionCategory.BUDGET_EXCEEDED),
    )
    assert status_of(r_budget) is Status.REJECTED
    assert is_terminal(status_of(r_budget))
    assert events_of(r_budget)[-1].note == "rejected: budget-exceeded"

    # Missing documentation, with note.
    r_docs = _reject_scenario(
        "Client dinner",
        rejection_reason(RejectionCategory.MISSING_DOCUMENTATION, "no receipt attached"),
    )
    assert events_of(r_docs)[-1].note == "rejected: missing-documentation: no receipt attached"

    # Policy violation, with note.
    r_policy = _reject_scenario(
        "Spa day",
        rejection_reason(RejectionCategory.POLICY_VIOLATION, "personal wellness not covered"),
    )
    assert events_of(r_policy)[-1].note == "rejected: policy-violation: personal wellness not covered"

    # Duplicate submission, no note.
    r_dup = _reject_scenario(
        "Taxi 2024-03-04",
        rejection_reason(RejectionCategory.DUPLICATE_SUBMISSION),
    )
    assert events_of(r_dup)[-1].note == "rejected: duplicate-submission"

    # Other — note is mandatory.
    r_other = _reject_scenario(
        "Mystery charge",
        rejection_reason(RejectionCategory.OTHER, "needs investigation by finance"),
    )
    assert events_of(r_other)[-1].note == "rejected: other: needs investigation by finance"

    # OTHER without a note is rejected at construction time.
    try:
        rejection_reason(RejectionCategory.OTHER)
        raise AssertionError("expected OTHER without note to fail")
    except ValueError as e:
        assert "other" in str(e).lower()

    # Blank notes on OTHER are also rejected.
    try:
        rejection_reason(RejectionCategory.OTHER, "   ")
        raise AssertionError("expected OTHER with blank note to fail")
    except ValueError:
        pass

    # Whitespace-only notes on other categories degrade to the bare category.
    r_blank_note = _reject_scenario(
        "Parking",
        rejection_reason(RejectionCategory.POLICY_VIOLATION, "   "),
    )
    assert events_of(r_blank_note)[-1].note == "rejected: policy-violation"

    # reject_report rejects non-RejectionReason inputs (no more raw strings).
    r_raw = with_title_and_amount(empty_report("R9", "U2"), "Legacy", Money(cents=1000))
    r_raw = submit_report(r_raw, "U2", 1000)
    try:
        reject_report(r_raw, manager.id, "just a string", 2000)  # type: ignore[arg-type]
        raise AssertionError("expected raw-string reason to be rejected")
    except TypeError:
        pass

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
