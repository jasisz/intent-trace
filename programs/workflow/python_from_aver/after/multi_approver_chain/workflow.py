"""Expense-report approval workflow with multi-approver chain.

Each report moves through Draft → Submitted → (Approved or Rejected). Approved
reports can finally be Paid. Transitions are guarded: submitting requires a
non-empty title and positive amount; approval is gated by the approver's
authority limit; payment requires a prior approval. Every transition is
recorded in an audit trail attached to the report.

Large amounts (above ``HIGH_AMOUNT_THRESHOLD_CENTS``) require a chain of
approvals: each qualified approver signs off in turn, and the report remains
in the intermediate ``PendingApproval`` state until the last required
approver has signed. Small amounts still approve on the first sign-off.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


# Amounts strictly above this threshold require a multi-approver chain.
HIGH_AMOUNT_THRESHOLD_CENTS: int = 100_000

# Number of approvers required in each regime.
SINGLE_APPROVER_COUNT: int = 1
CHAIN_APPROVER_COUNT: int = 2


class Status(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
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
    approvals: tuple[str, ...] = ()


def empty_report(report_id: str, submitter_id: str) -> Report:
    """Create a fresh Draft report with no title, zero amount, no events."""
    return Report(
        id=report_id,
        title="",
        amount=Money(cents=0),
        submitter_id=submitter_id,
        status=Status.DRAFT,
        events=(),
        approvals=(),
    )


def status_of(r: Report) -> Status:
    return r.status


def events_of(r: Report) -> list[Event]:
    """Return audit trail in chronological order (oldest first)."""
    return list(r.events)


def approvals_of(r: Report) -> list[str]:
    """Return approver ids collected so far, in order of sign-off."""
    return list(r.approvals)


def _record_event(r: Report, e: Event) -> Report:
    return replace(r, events=r.events + (e,))


def with_title_and_amount(r: Report, title: str, amount: Money) -> Report:
    """Update title and amount on a Draft report. Not permitted after submission."""
    return replace(r, title=title, amount=amount)


def amount_is_positive(m: Money) -> bool:
    return m.cents > 0


def title_is_present(r: Report) -> bool:
    return len(r.title) > 0


def requires_approver_chain(amount: Money) -> bool:
    """True if the amount is large enough to require multiple approvers."""
    return amount.cents > HIGH_AMOUNT_THRESHOLD_CENTS


def required_approver_count(amount: Money) -> int:
    """Number of distinct approvers needed before the report can be Approved."""
    if requires_approver_chain(amount):
        return CHAIN_APPROVER_COUNT
    return SINGLE_APPROVER_COUNT


def remaining_approvals(r: Report) -> int:
    """How many more approvers are still needed for full approval."""
    needed = required_approver_count(r.amount)
    have = len(r.approvals)
    return max(0, needed - have)


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


def _is_awaiting_approval(s: Status) -> bool:
    return s is Status.SUBMITTED or s is Status.PENDING_APPROVAL


def approve_report(r: Report, approver: User, timestamp_ms: int) -> Report:
    """Record one approval on a Submitted or PendingApproval report.

    Each approver must individually have authority for the full amount. Once
    the number of collected approvals reaches the required count, the report
    transitions to ``Approved``; otherwise it sits in ``PendingApproval``.
    An approver cannot sign the same report twice.
    """
    if not _is_awaiting_approval(r.status):
        raise ValueError(f"Cannot approve from status {r.status.value}")
    if not _approver_has_authority(approver, r.amount):
        raise ValueError(f"Approver {approver.id} limit too low for amount")
    if approver.id in r.approvals:
        raise ValueError(f"Approver {approver.id} has already approved this report")

    new_approvals = r.approvals + (approver.id,)
    needed = required_approver_count(r.amount)
    is_final = len(new_approvals) >= needed
    next_status = Status.APPROVED if is_final else Status.PENDING_APPROVAL
    note = "approved" if is_final else f"approval {len(new_approvals)}/{needed}"

    stepped = replace(r, status=next_status, approvals=new_approvals)
    return _record_event(stepped, Event(timestamp_ms=timestamp_ms, actor_id=approver.id, note=note))


def reject_report(r: Report, actor_id: str, reason: str, timestamp_ms: int) -> Report:
    """Move a Submitted or PendingApproval report to Rejected with a reason."""
    if not _is_awaiting_approval(r.status):
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

    # Single-approver path for a small amount (below threshold).
    r = with_title_and_amount(r, "Travel - Q2", Money(cents=45000))
    assert required_approver_count(r.amount) == SINGLE_APPROVER_COUNT
    assert not requires_approver_chain(r.amount)
    r = submit_report(r, "U1", 1000)
    assert status_of(r) is Status.SUBMITTED

    manager = User(id="M1", name="Manager", approval_limit_cents=100000)
    junior = User(id="J1", name="Junior", approval_limit_cents=10000)
    director = User(id="D1", name="Director", approval_limit_cents=500000)
    vp = User(id="V1", name="VP", approval_limit_cents=1000000)

    try:
        approve_report(r, junior, 1500)
        raise AssertionError("expected limit failure")
    except ValueError as e:
        assert "limit" in str(e).lower()

    r = approve_report(r, manager, 2000)
    assert status_of(r) is Status.APPROVED
    assert approvals_of(r) == ["M1"]
    assert remaining_approvals(r) == 0

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

    # Rejection path from Submitted (small amount).
    r2 = with_title_and_amount(empty_report("R2", "U2"), "Office chair", Money(cents=30000))
    r2 = submit_report(r2, "U2", 1000)
    r2 = reject_report(r2, manager.id, "out of budget", 2000)
    assert status_of(r2) is Status.REJECTED
    assert is_terminal(status_of(r2))

    # Multi-approver chain for a large amount (above threshold).
    big = with_title_and_amount(empty_report("R3", "U3"), "Conference sponsorship", Money(cents=250_000))
    assert requires_approver_chain(big.amount)
    assert required_approver_count(big.amount) == CHAIN_APPROVER_COUNT
    big = submit_report(big, "U3", 1000)
    assert status_of(big) is Status.SUBMITTED
    assert remaining_approvals(big) == CHAIN_APPROVER_COUNT

    # Approver without authority for the full amount cannot participate even in the chain.
    try:
        approve_report(big, manager, 1100)  # manager limit 100_000 < 250_000
        raise AssertionError("expected chain-step authority failure")
    except ValueError as e:
        assert "limit" in str(e).lower()
    # Failed step leaves the report untouched (still Submitted, no approvals recorded).
    assert status_of(big) is Status.SUBMITTED
    assert approvals_of(big) == []

    # First qualified approver signs; report moves to PendingApproval.
    big = approve_report(big, director, 1200)
    assert status_of(big) is Status.PENDING_APPROVAL
    assert approvals_of(big) == ["D1"]
    assert remaining_approvals(big) == 1

    # Same approver cannot sign twice.
    try:
        approve_report(big, director, 1300)
        raise AssertionError("expected duplicate-approver rejection")
    except ValueError as e:
        assert "already" in str(e).lower()

    # Cannot pay a partially approved report.
    try:
        pay_report(big, "F1", 1400)
        raise AssertionError("expected pay-before-approved rejection")
    except ValueError:
        pass

    # Second qualified approver finalizes approval.
    big = approve_report(big, vp, 1500)
    assert status_of(big) is Status.APPROVED
    assert approvals_of(big) == ["D1", "V1"]
    assert remaining_approvals(big) == 0

    big = pay_report(big, "F1", 1600)
    assert status_of(big) is Status.PAID

    big_events = events_of(big)
    assert [e.note for e in big_events] == [
        "submitted",
        "approval 1/2",
        "approved",
        "paid",
    ]
    assert [e.actor_id for e in big_events] == ["U3", "D1", "V1", "F1"]

    # Rejection from PendingApproval is also allowed.
    big2 = with_title_and_amount(empty_report("R4", "U4"), "Vendor contract", Money(cents=300_000))
    big2 = submit_report(big2, "U4", 1000)
    big2 = approve_report(big2, director, 1100)
    assert status_of(big2) is Status.PENDING_APPROVAL
    big2 = reject_report(big2, vp.id, "needs revision", 1200)
    assert status_of(big2) is Status.REJECTED
    assert is_terminal(status_of(big2))

    # Boundary: amount exactly at threshold is still single-approver (strictly above triggers chain).
    at_threshold = with_title_and_amount(
        empty_report("R5", "U5"), "Edge", Money(cents=HIGH_AMOUNT_THRESHOLD_CENTS)
    )
    assert not requires_approver_chain(at_threshold.amount)
    assert required_approver_count(at_threshold.amount) == SINGLE_APPROVER_COUNT
    at_threshold = submit_report(at_threshold, "U5", 1000)
    at_threshold = approve_report(at_threshold, manager, 1100)
    assert status_of(at_threshold) is Status.APPROVED

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
