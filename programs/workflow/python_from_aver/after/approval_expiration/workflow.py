"""Expense-report approval workflow.

Each report moves through Draft → Submitted → (Approved or Rejected). Approved
reports can finally be Paid. Transitions are guarded: submitting requires a
non-empty title and positive amount; approval is gated by the approver's
authority limit; payment requires a prior approval that has not expired.
Approvals carry the timestamp they were granted and go stale after a
configurable age window — a stale approval must be re-issued before the
report can be paid. Every transition is recorded in an audit trail attached
to the report.

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

* The approval timestamp is stored inline on the Report (``approved_at_ms``),
  not derived from the audit log. Approvals sitting unpaid indefinitely
  accumulate stale authorization; storing the timestamp directly keeps
  staleness checks O(1) and avoids coupling payment policy to audit-log
  parsing. ``None`` represents "never approved or re-submitted".
  Alternatives considered: scan the audit log on every check, keep a
  separate approval store.
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
    approved_at_ms: int | None = None


def empty_report(report_id: str, submitter_id: str) -> Report:
    """Create a fresh Draft report with no title, zero amount, no events."""
    return Report(
        id=report_id,
        title="",
        amount=Money(cents=0),
        submitter_id=submitter_id,
        status=Status.DRAFT,
        events=(),
        approved_at_ms=None,
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
    """Move a Submitted report to Approved. Approver must have sufficient authority.

    The approval timestamp is recorded on the report so freshness can be checked
    later (see `approval_is_stale` / `expire_stale_approval`).
    """
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot approve from status {r.status.value}")
    if not _approver_has_authority(approver, r.amount):
        raise ValueError(f"Approver {approver.id} limit too low for amount")
    approved = replace(r, status=Status.APPROVED, approved_at_ms=timestamp_ms)
    return _record_event(approved, Event(timestamp_ms=timestamp_ms, actor_id=approver.id, note="approved"))


def reject_report(r: Report, actor_id: str, reason: str, timestamp_ms: int) -> Report:
    """Move a Submitted report to Rejected with a reason."""
    if r.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot reject from status {r.status.value}")
    rejected = replace(r, status=Status.REJECTED)
    return _record_event(rejected, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note=f"rejected: {reason}"))


def approval_is_stale(r: Report, now_ms: int, max_age_ms: int) -> bool:
    """True if `r` is Approved and the approval is older than `max_age_ms`.

    Reports not in the Approved state are never considered stale — freshness
    only makes sense for a standing approval.
    """
    if r.status is not Status.APPROVED or r.approved_at_ms is None:
        return False
    return (now_ms - r.approved_at_ms) > max_age_ms


def expire_stale_approval(r: Report, now_ms: int, max_age_ms: int, actor_id: str) -> Report:
    """If `r`'s approval has gone stale, revert it to Submitted for re-approval.

    Returns the report unchanged when the approval is still fresh (or when the
    report isn't Approved at all). The approval timestamp is cleared on expiry
    so a later re-approval starts a new freshness window.
    """
    if not approval_is_stale(r, now_ms, max_age_ms):
        return r
    reverted = replace(r, status=Status.SUBMITTED, approved_at_ms=None)
    return _record_event(reverted, Event(timestamp_ms=now_ms, actor_id=actor_id, note="approval expired"))


def pay_report(r: Report, actor_id: str, timestamp_ms: int, max_age_ms: int) -> Report:
    """Move an Approved report to Paid.

    Fails if the report is not Approved, or if the approval has aged past
    `max_age_ms` relative to `timestamp_ms`. Stale approvals must be expired
    and re-issued before payment.
    """
    if r.status is not Status.APPROVED:
        raise ValueError(f"Cannot pay from status {r.status.value}")
    if approval_is_stale(r, timestamp_ms, max_age_ms):
        raise ValueError("Cannot pay: approval has expired, re-approval required")
    paid = replace(r, status=Status.PAID)
    return _record_event(paid, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="paid"))


def is_terminal(s: Status) -> bool:
    """True if the state does not permit further transitions."""
    return s is Status.PAID or s is Status.REJECTED


def _smoke_tests() -> None:
    # One day in milliseconds — the window beyond which an approval goes stale.
    max_age_ms = 24 * 60 * 60 * 1000

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
    assert r.approved_at_ms == 2000

    # Fresh approval: paying shortly after approval succeeds.
    now_fresh = 2000 + max_age_ms  # exactly at the boundary — still fresh
    assert not approval_is_stale(r, now_fresh, max_age_ms)
    r_paid = pay_report(r, "F1", now_fresh, max_age_ms)
    assert status_of(r_paid) is Status.PAID
    assert is_terminal(status_of(r_paid))

    events = events_of(r_paid)
    assert len(events) == 3
    assert events[0].note == "submitted"
    assert events[1].note == "approved"
    assert events[2].note == "paid"

    # Terminal states reject further transitions.
    try:
        pay_report(r_paid, "F1", now_fresh + 1, max_age_ms)
        raise AssertionError("expected terminal rejection")
    except ValueError:
        pass

    # Stale approval: paying beyond the age window fails, and expire reverts
    # the report to Submitted so it can be re-approved and then paid.
    now_stale = 2000 + max_age_ms + 1
    assert approval_is_stale(r, now_stale, max_age_ms)
    try:
        pay_report(r, "F1", now_stale, max_age_ms)
        raise AssertionError("expected stale approval to block payment")
    except ValueError as e:
        assert "expired" in str(e).lower()

    # expire_stale_approval is a no-op when the approval is still fresh.
    assert expire_stale_approval(r, now_fresh, max_age_ms, "SYS") is r

    expired = expire_stale_approval(r, now_stale, max_age_ms, "SYS")
    assert status_of(expired) is Status.SUBMITTED
    assert expired.approved_at_ms is None
    assert events_of(expired)[-1].note == "approval expired"

    # Re-approval resets the freshness window; payment then succeeds.
    re_approved = approve_report(expired, manager, now_stale + 1)
    assert status_of(re_approved) is Status.APPROVED
    assert re_approved.approved_at_ms == now_stale + 1
    re_paid = pay_report(re_approved, "F1", now_stale + 2, max_age_ms)
    assert status_of(re_paid) is Status.PAID

    # Rejection path.
    r2 = with_title_and_amount(empty_report("R2", "U2"), "Office chair", Money(cents=30000))
    r2 = submit_report(r2, "U2", 1000)
    r2 = reject_report(r2, manager.id, "out of budget", 2000)
    assert status_of(r2) is Status.REJECTED
    assert is_terminal(status_of(r2))

    # Non-approved reports are never "stale".
    assert not approval_is_stale(r2, 10**12, max_age_ms)

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
