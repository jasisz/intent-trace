"""Expense-report approval workflow.

Each report moves through Draft → Submitted → (Approved or Rejected). Approved
reports can finally be Paid. Transitions are guarded: submitting requires a
non-empty title and positive amount; approval is gated by the approver's
authority limit; payment requires a prior approval. Every transition is
recorded in an audit trail attached to the report.
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

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
