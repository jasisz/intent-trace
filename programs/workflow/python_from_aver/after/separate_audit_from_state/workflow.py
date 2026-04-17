"""Expense-report approval workflow.

Each report moves through Draft -> Submitted -> (Approved or Rejected).
Approved reports can finally be Paid. Transitions are guarded: submitting
requires a non-empty title and positive amount; approval is gated by the
approver's authority limit; payment requires a prior approval.

Structurally, we separate two concerns:

* ``ReportCore`` holds the current lifecycle state (id, title, amount,
  submitter, status) and nothing else. It is what pure business-logic
  queries should take and return.
* ``AuditedReport`` wraps a ``ReportCore`` alongside its chronological
  event log. It is what the workflow layer uses when it wants to both
  advance state and keep a paper trail.

The core transitions (``submit_core``, ``approve_core``, ``reject_core``,
``pay_core``) operate only on ``ReportCore`` and return a new ``ReportCore``.
The audited transitions (``submit``, ``approve``, ``reject``, ``pay``) call
the core transitions and additionally append an ``Event`` to the audit log.

Design decisions:

* Status is a closed sum type (Enum), not a string. Expense reports have a
  small, fixed set of lifecycle states, and closing the type lets the
  type-checker flag any future code path that forgets to handle one.
  String-typed status fields would silently accept typos and new values.
  Alternatives considered: string status codes, integer status codes.

* The audit trail lives alongside the core on a wrapper record
  (``AuditedReport``), not inline on the core and not in a fully external
  store. The previous inline shape mixed current lifecycle fields with the
  full event history on one record: pure business-logic queries ended up
  carrying the audit payload, and the type gave no signal about which
  concern each caller touched. Splitting yields ``ReportCore`` for
  state-only queries and ``AuditedReport`` for the combined view.
  Transition semantics are unchanged; only the location of the event list
  moves. An externalised audit store was rejected because replay and
  explanation still want the events on hand, just not inside the core
  record. Alternatives considered: inline audit log on the core, external
  audit store, no audit log.
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
class ReportCore:
    """Current lifecycle state of a report. No audit payload."""
    id: str
    title: str
    amount: Money
    submitter_id: str
    status: Status


@dataclass(frozen=True)
class AuditedReport:
    """A report core paired with its chronological event log."""
    core: ReportCore
    events: tuple[Event, ...] = ()


# ---------------------------------------------------------------------------
# Core-only operations: act on ReportCore, no audit trail involved.
# ---------------------------------------------------------------------------


def empty_core(report_id: str, submitter_id: str) -> ReportCore:
    """Create a fresh Draft core with no title and zero amount."""
    return ReportCore(
        id=report_id,
        title="",
        amount=Money(cents=0),
        submitter_id=submitter_id,
        status=Status.DRAFT,
    )


def status_of(c: ReportCore) -> Status:
    """Return the current lifecycle status of a core report."""
    return c.status


def amount_is_positive(m: Money) -> bool:
    """True if the money amount is strictly positive."""
    return m.cents > 0


def title_is_present(c: ReportCore) -> bool:
    """True if the core report title is non-empty."""
    return len(c.title) > 0


def is_terminal(s: Status) -> bool:
    """True if the state does not permit further transitions."""
    return s is Status.PAID or s is Status.REJECTED


def with_title_and_amount(c: ReportCore, title: str, amount: Money) -> ReportCore:
    """Update title and amount on a Draft core. Not permitted after submission."""
    if c.status is not Status.DRAFT:
        raise ValueError(f"Cannot edit from status {c.status.value}")
    return replace(c, title=title, amount=amount)


def _approver_has_authority(approver: User, amount: Money) -> bool:
    """True if the approver's limit covers the report amount."""
    return approver.approval_limit_cents >= amount.cents


def submit_core(c: ReportCore) -> ReportCore:
    """Move a Draft core to Submitted. Raises ValueError if invariants fail."""
    if c.status is not Status.DRAFT:
        raise ValueError(f"Cannot submit from status {c.status.value}")
    if not title_is_present(c):
        raise ValueError("Report title must not be empty")
    if not amount_is_positive(c.amount):
        raise ValueError("Report amount must be positive")
    return replace(c, status=Status.SUBMITTED)


def approve_core(c: ReportCore, approver: User) -> ReportCore:
    """Move a Submitted core to Approved. Approver must have sufficient authority."""
    if c.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot approve from status {c.status.value}")
    if not _approver_has_authority(approver, c.amount):
        raise ValueError(f"Approver {approver.id} limit too low for amount")
    return replace(c, status=Status.APPROVED)


def reject_core(c: ReportCore) -> ReportCore:
    """Move a Submitted core to Rejected."""
    if c.status is not Status.SUBMITTED:
        raise ValueError(f"Cannot reject from status {c.status.value}")
    return replace(c, status=Status.REJECTED)


def pay_core(c: ReportCore) -> ReportCore:
    """Move an Approved core to Paid."""
    if c.status is not Status.APPROVED:
        raise ValueError(f"Cannot pay from status {c.status.value}")
    return replace(c, status=Status.PAID)


# ---------------------------------------------------------------------------
# Audited operations: act on AuditedReport, advance the core and append an
# Event. Transition semantics are identical to the core-only versions.
# ---------------------------------------------------------------------------


def empty_report(report_id: str, submitter_id: str) -> AuditedReport:
    """Create a fresh Draft audited report with no events."""
    return AuditedReport(core=empty_core(report_id, submitter_id), events=())


def core_of(r: AuditedReport) -> ReportCore:
    """Return the pure lifecycle core, stripped of the audit trail."""
    return r.core


def events_of(r: AuditedReport) -> list[Event]:
    """Return audit trail in chronological order (oldest first)."""
    return list(r.events)


def audited_status_of(r: AuditedReport) -> Status:
    """Return the current lifecycle status of an audited report."""
    return r.core.status


def _with_event(r: AuditedReport, core: ReportCore, e: Event) -> AuditedReport:
    """Build a new AuditedReport with an updated core and an appended event."""
    return AuditedReport(core=core, events=r.events + (e,))


def edit_title_and_amount(r: AuditedReport, title: str, amount: Money) -> AuditedReport:
    """Update title and amount on a Draft report's core. No event is recorded."""
    return AuditedReport(core=with_title_and_amount(r.core, title, amount), events=r.events)


def submit_report(r: AuditedReport, actor_id: str, timestamp_ms: int) -> AuditedReport:
    """Audited variant of ``submit_core``: also appends a 'submitted' event."""
    submitted = submit_core(r.core)
    return _with_event(r, submitted, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="submitted"))


def approve_report(r: AuditedReport, approver: User, timestamp_ms: int) -> AuditedReport:
    """Audited variant of ``approve_core``: also appends an 'approved' event."""
    approved = approve_core(r.core, approver)
    return _with_event(r, approved, Event(timestamp_ms=timestamp_ms, actor_id=approver.id, note="approved"))


def reject_report(r: AuditedReport, actor_id: str, reason: str, timestamp_ms: int) -> AuditedReport:
    """Audited variant of ``reject_core``: also appends a 'rejected: <reason>' event."""
    rejected = reject_core(r.core)
    return _with_event(r, rejected, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note=f"rejected: {reason}"))


def pay_report(r: AuditedReport, actor_id: str, timestamp_ms: int) -> AuditedReport:
    """Audited variant of ``pay_core``: also appends a 'paid' event."""
    paid = pay_core(r.core)
    return _with_event(r, paid, Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note="paid"))


def _smoke_tests() -> None:
    # -----------------------------------------------------------------------
    # Core-only path: drive a report through its lifecycle without any audit.
    # -----------------------------------------------------------------------
    c = empty_core("R0", "U0")
    assert status_of(c) is Status.DRAFT

    # Empty title rejected.
    try:
        submit_core(c)
        raise AssertionError("expected empty title to fail")
    except ValueError as e:
        assert "title" in str(e).lower()

    c = with_title_and_amount(c, "Conference fee", Money(cents=25000))

    # Zero amount rejected.
    c_zero = with_title_and_amount(empty_core("R0z", "U0"), "Zero", Money(cents=0))
    try:
        submit_core(c_zero)
        raise AssertionError("expected zero amount to fail")
    except ValueError as e:
        assert "amount" in str(e).lower()

    c = submit_core(c)
    assert status_of(c) is Status.SUBMITTED

    manager = User(id="M1", name="Manager", approval_limit_cents=100000)
    junior = User(id="J1", name="Junior", approval_limit_cents=10000)

    # Junior cannot approve 25000 > 10000.
    try:
        approve_core(c, junior)
        raise AssertionError("expected limit failure")
    except ValueError as e:
        assert "limit" in str(e).lower()

    c = approve_core(c, manager)
    assert status_of(c) is Status.APPROVED

    c = pay_core(c)
    assert status_of(c) is Status.PAID
    assert is_terminal(status_of(c))
    # ReportCore carries no events attribute at all.
    assert not hasattr(c, "events")

    # Further transition from terminal state is rejected.
    try:
        pay_core(c)
        raise AssertionError("expected terminal rejection")
    except ValueError:
        pass

    # Editing a non-draft core is rejected.
    try:
        with_title_and_amount(c, "Late edit", Money(cents=1))
        raise AssertionError("expected edit-after-submit to fail")
    except ValueError:
        pass

    # -----------------------------------------------------------------------
    # Audited path: identical transitions, plus a recorded event log.
    # -----------------------------------------------------------------------
    r = empty_report("R1", "U1")
    assert audited_status_of(r) is Status.DRAFT
    assert events_of(r) == []

    try:
        submit_report(r, "U1", 1000)
        raise AssertionError("expected empty title to fail")
    except ValueError as e:
        assert "title" in str(e).lower()

    r = edit_title_and_amount(r, "Travel - Q2", Money(cents=45000))
    r = submit_report(r, "U1", 1000)
    assert audited_status_of(r) is Status.SUBMITTED

    try:
        approve_report(r, junior, 1500)
        raise AssertionError("expected limit failure")
    except ValueError as e:
        assert "limit" in str(e).lower()

    r = approve_report(r, manager, 2000)
    assert audited_status_of(r) is Status.APPROVED

    r = pay_report(r, "F1", 3000)
    assert audited_status_of(r) is Status.PAID
    assert is_terminal(audited_status_of(r))

    events = events_of(r)
    assert len(events) == 3
    assert events[0].note == "submitted"
    assert events[1].note == "approved"
    assert events[2].note == "paid"
    assert events[0].actor_id == "U1"
    assert events[1].actor_id == manager.id
    assert events[2].actor_id == "F1"

    # Core extracted from the audited report is the same shape we drove by hand.
    extracted = core_of(r)
    assert isinstance(extracted, ReportCore)
    assert extracted.status is Status.PAID
    assert not hasattr(extracted, "events")

    # Terminal states reject further transitions on the audited path too.
    try:
        pay_report(r, "F1", 4000)
        raise AssertionError("expected terminal rejection")
    except ValueError:
        pass

    # Rejection path with audit.
    r2 = edit_title_and_amount(empty_report("R2", "U2"), "Office chair", Money(cents=30000))
    r2 = submit_report(r2, "U2", 1000)
    r2 = reject_report(r2, manager.id, "out of budget", 2000)
    assert audited_status_of(r2) is Status.REJECTED
    assert is_terminal(audited_status_of(r2))
    ev2 = events_of(r2)
    assert len(ev2) == 2
    assert ev2[1].note == "rejected: out of budget"

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
