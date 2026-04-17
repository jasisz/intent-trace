"""Expense-report approval workflow.

Each report moves through Draft → Submitted → (Approved or Rejected). Approved
reports can finally be Paid. Transitions are guarded: submitting requires a
non-empty title and positive amount; approval is gated by the approver's
authority limit; payment requires a prior approval. The original submitter can
withdraw a Submitted report, sending it back to Draft for edits. Every
transition is recorded in an audit trail attached to the report.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class Status(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


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
    return r.status


def events_of(r: Report) -> list[Event]:
    """Return audit trail in chronological order (oldest first)."""
    return list(r.events)


def _record_event(r: Report, e: Event) -> Report:
    return replace(r, events=r.events + (e,))


def with_title_and_amount(r: Report, title: str, amount: Money) -> Report:
    """Update title and amount on a Draft report. Not permitted after submission."""
    return replace(r, title=title, amount=amount)


def amount_is_positive(m: Money) -> bool:
    return m.cents > 0


def title_is_present(r: Report) -> bool:
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
    return approver.approval_limit_cents >= amount.cents


def approve_report(r: Report, approver: User, timestamp_ms: int) -> Report:
    """Move a Submitted report to Approved. Approver must have sufficient authority."""
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot approve from status {r.status.value}")
    if not _approver_has_authority(approver, r.amount):
        raise ValueError(f"Approver {approver.id} limit too low for amount")
    approved = replace(r, status=Status.APPROVED)
    return _record_event(approved, Event(timestamp_ms=timestamp_ms, actor_id=approver.id, note="approved"))


def reject_report(r: Report, actor_id: str, reason: str, timestamp_ms: int) -> Report:
    """Move a Submitted report to Rejected with a reason."""
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot reject from status {r.status.value}")
    rejected = replace(r, status=Status.REJECTED)
    return _record_event(rejected, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note=f"rejected: {reason}"))


def withdraw_report(r: Report, actor_id: str, timestamp_ms: int) -> Report:
    """Return a Submitted report to Draft so the submitter can edit and resubmit.

    Only the original submitter may withdraw. The audit trail is preserved
    (including the original submission event); a withdrawal event is appended.
    """
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot withdraw from status {r.status.value}")
    if actor_id != r.submitter_id:
        raise ValueError(f"Only submitter {r.submitter_id} may withdraw this report")
    withdrawn = replace(r, status=Status.DRAFT)
    return _record_event(withdrawn, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="withdrawn"))


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

    # Rejection path.
    r2 = with_title_and_amount(empty_report("R2", "U2"), "Office chair", Money(cents=30000))
    r2 = submit_report(r2, "U2", 1000)
    r2 = reject_report(r2, manager.id, "out of budget", 2000)
    assert status_of(r2) is Status.REJECTED
    assert is_terminal(status_of(r2))

    # Withdrawal path: submitter pulls a Submitted report back to Draft,
    # edits, and resubmits. Original submission event is preserved.
    r3 = with_title_and_amount(empty_report("R3", "U3"), "Conference pass", Money(cents=80000))
    r3 = submit_report(r3, "U3", 1000)
    assert status_of(r3) is Status.SUBMITTED
    r3 = withdraw_report(r3, "U3", 1500)
    assert status_of(r3) is Status.DRAFT
    withdrawn_events = events_of(r3)
    assert len(withdrawn_events) == 2
    assert withdrawn_events[0].note == "submitted"
    assert withdrawn_events[1].note == "withdrawn"
    assert withdrawn_events[1].actor_id == "U3"
    # Edit + resubmit works after withdrawal.
    r3 = with_title_and_amount(r3, "Conference pass (revised)", Money(cents=60000))
    r3 = submit_report(r3, "U3", 2000)
    assert status_of(r3) is Status.SUBMITTED
    assert len(events_of(r3)) == 3
    assert events_of(r3)[2].note == "submitted"

    # Withdrawal by a non-submitter is rejected.
    r4 = with_title_and_amount(empty_report("R4", "U4"), "Laptop", Money(cents=120000))
    r4 = submit_report(r4, "U4", 1000)
    try:
        withdraw_report(r4, "M1", 1500)
        raise AssertionError("expected non-submitter withdrawal to fail")
    except ValueError as e:
        assert "submitter" in str(e).lower()
    # Report stays Submitted; only the submission event is recorded.
    assert status_of(r4) is Status.SUBMITTED
    assert len(events_of(r4)) == 1

    # Withdrawal from terminal states is rejected.
    # Paid (from r above).
    try:
        withdraw_report(r, "U1", 5000)
        raise AssertionError("expected withdraw-from-paid to fail")
    except ValueError as e:
        assert "paid" in str(e).lower()
    # Rejected (from r2 above).
    try:
        withdraw_report(r2, "U2", 5000)
        raise AssertionError("expected withdraw-from-rejected to fail")
    except ValueError as e:
        assert "rejected" in str(e).lower()
    # Approved: withdraw is also not allowed once an approver has acted.
    r5 = with_title_and_amount(empty_report("R5", "U5"), "Monitor", Money(cents=40000))
    r5 = submit_report(r5, "U5", 1000)
    r5 = approve_report(r5, manager, 1500)
    try:
        withdraw_report(r5, "U5", 2000)
        raise AssertionError("expected withdraw-from-approved to fail")
    except ValueError as e:
        assert "approved" in str(e).lower()

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
