"""Expense-report approval workflow modeled as a state machine on `Report`."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class WorkflowError(Exception):
    """Base class for any violation of the expense-report workflow rules."""


class InvalidTransitionError(WorkflowError):
    """Raised when a state transition is attempted from a disallowed status."""


class InsufficientAuthorityError(WorkflowError):
    """Raised when an approver's limit is below the report amount."""


class InvalidReportError(WorkflowError):
    """Raised when a report fails its own invariants (empty title, non-positive amount)."""


class ApprovalExpiredError(WorkflowError):
    """Raised when payment is attempted on an approval that has gone stale."""


@dataclass(frozen=True)
class Money:
    cents: int

    @property
    def is_positive(self) -> bool:
        return self.cents > 0


@dataclass(frozen=True)
class User:
    id: str
    name: str
    approval_limit_cents: int

    def can_approve(self, amount: Money) -> bool:
        return self.approval_limit_cents >= amount.cents


@dataclass(frozen=True)
class Event:
    timestamp_ms: int
    actor_id: str
    note: str


_TERMINAL_STATUSES = frozenset({Status.PAID, Status.REJECTED})


@dataclass
class Report:
    """Mutable expense report that transitions through the approval workflow.

    Each transition method validates the current status and domain invariants,
    mutates `self.status`, and appends an `Event` to `self.events`. Raises a
    subclass of `WorkflowError` on any violation.
    """

    id: str
    submitter_id: str
    title: str = ""
    amount: Money = field(default_factory=lambda: Money(cents=0))
    status: Status = Status.DRAFT
    events: list[Event] = field(default_factory=list)
    approved_at_ms: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def is_draft(self) -> bool:
        return self.status is Status.DRAFT

    @property
    def is_submitted(self) -> bool:
        return self.status is Status.SUBMITTED

    @property
    def is_approved(self) -> bool:
        return self.status is Status.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.status is Status.REJECTED

    @property
    def is_paid(self) -> bool:
        return self.status is Status.PAID

    def set_details(self, title: str, amount: Money) -> None:
        """Set title and amount on a Draft report; not permitted after submission."""
        if not self.is_draft:
            raise InvalidTransitionError(
                f"Cannot edit details from status {self.status.value}"
            )
        self.title = title
        self.amount = amount

    def submit(self, actor: User | str, timestamp_ms: int) -> None:
        if not self.is_draft:
            raise InvalidTransitionError(
                f"Cannot submit from status {self.status.value}"
            )
        if not self.title:
            raise InvalidReportError("Report title must not be empty")
        if not self.amount.is_positive:
            raise InvalidReportError("Report amount must be positive")
        self.status = Status.SUBMITTED
        self._log(actor, timestamp_ms, "submitted")

    def approve(self, approver: User, timestamp_ms: int) -> None:
        """Move a Submitted report to Approved; approver must have sufficient authority."""
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot approve from status {self.status.value}"
            )
        if not approver.can_approve(self.amount):
            raise InsufficientAuthorityError(
                f"Approver {approver.id} limit too low for amount"
            )
        self.status = Status.APPROVED
        self.approved_at_ms = timestamp_ms
        self._log(approver, timestamp_ms, "approved")

    def reject(self, actor: User | str, reason: str, timestamp_ms: int) -> None:
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot reject from status {self.status.value}"
            )
        self.status = Status.REJECTED
        self._log(actor, timestamp_ms, f"rejected: {reason}")

    def is_approval_stale(self, now_ms: int, max_age_ms: int) -> bool:
        """True when the report is Approved and the approval is older than `max_age_ms`."""
        if not self.is_approved or self.approved_at_ms is None:
            return False
        return (now_ms - self.approved_at_ms) > max_age_ms

    def expire_stale_approval(self, now_ms: int, max_age_ms: int) -> bool:
        """If the approval is stale, revert to Submitted and clear the approval timestamp."""
        if not self.is_approval_stale(now_ms, max_age_ms):
            return False
        self.status = Status.SUBMITTED
        self.approved_at_ms = None
        self._log("system", now_ms, "approval expired")
        return True

    def pay(
        self,
        actor: User | str,
        timestamp_ms: int,
        max_age_ms: int | None = None,
    ) -> None:
        """Pay an Approved report; optionally reject if the approval is stale."""
        if not self.is_approved:
            raise InvalidTransitionError(
                f"Cannot pay from status {self.status.value}"
            )
        if max_age_ms is not None and self.is_approval_stale(timestamp_ms, max_age_ms):
            raise ApprovalExpiredError(
                f"Approval for report {self.id} is stale"
            )
        self.status = Status.PAID
        self._log(actor, timestamp_ms, "paid")

    def _log(self, actor: User | str, timestamp_ms: int, note: str) -> None:
        actor_id = actor.id if isinstance(actor, User) else actor
        self.events.append(
            Event(timestamp_ms=timestamp_ms, actor_id=actor_id, note=note)
        )


def _smoke_tests() -> None:
    r = Report(id="R1", submitter_id="U1")
    assert r.is_draft

    try:
        r.submit("U1", 1000)
        raise AssertionError("expected empty title to fail")
    except InvalidReportError as e:
        assert "title" in str(e).lower()

    r.set_details("Travel - Q2", Money(cents=45000))
    r.submit("U1", 1000)
    assert r.is_submitted

    manager = User(id="M1", name="Manager", approval_limit_cents=100000)
    junior = User(id="J1", name="Junior", approval_limit_cents=10000)

    try:
        r.approve(junior, 1500)
        raise AssertionError("expected limit failure")
    except InsufficientAuthorityError as e:
        assert "limit" in str(e).lower()

    r.approve(manager, 2000)
    assert r.is_approved
    assert r.approved_at_ms == 2000

    r.pay("F1", 3000)
    assert r.is_paid
    assert r.is_terminal

    assert len(r.events) == 3
    assert r.events[0].note == "submitted"
    assert r.events[1].note == "approved"
    assert r.events[2].note == "paid"

    # Terminal states reject further transitions.
    try:
        r.pay("F1", 4000)
        raise AssertionError("expected terminal rejection")
    except InvalidTransitionError:
        pass

    # Rejection path.
    r2 = Report(id="R2", submitter_id="U2")
    r2.set_details("Office chair", Money(cents=30000))
    r2.submit("U2", 1000)
    r2.reject(manager, "out of budget", 2000)
    assert r2.is_rejected
    assert r2.is_terminal

    # Approval expiration: stale approval -> is_approval_stale True, expire reverts to Submitted.
    r3 = Report(id="R3", submitter_id="U3")
    r3.set_details("Laptop", Money(cents=60000))
    r3.submit("U3", 1000)
    r3.approve(manager, 2000)
    assert r3.is_approved
    assert r3.approved_at_ms == 2000

    max_age = 10_000
    assert not r3.is_approval_stale(5000, max_age)
    assert r3.is_approval_stale(20_000, max_age)

    # Fresh approval: pay with max_age works.
    r_fresh = Report(id="R4", submitter_id="U4")
    r_fresh.set_details("Monitor", Money(cents=50000))
    r_fresh.submit("U4", 1000)
    r_fresh.approve(manager, 2000)
    r_fresh.pay("F1", 5000, max_age_ms=max_age)
    assert r_fresh.is_paid

    # Stale approval: pay with max_age raises.
    try:
        r3.pay("F1", 20_000, max_age_ms=max_age)
        raise AssertionError("expected stale approval to block payment")
    except ApprovalExpiredError:
        pass

    # Expire stale approval reverts to SUBMITTED and clears timestamp.
    expired = r3.expire_stale_approval(20_000, max_age)
    assert expired is True
    assert r3.is_submitted
    assert r3.approved_at_ms is None
    assert r3.events[-1].note == "approval expired"

    # Re-approval works after expiration.
    r3.approve(manager, 21_000)
    assert r3.is_approved
    assert r3.approved_at_ms == 21_000
    r3.pay("F1", 22_000, max_age_ms=max_age)
    assert r3.is_paid

    # expire_stale_approval is a no-op when approval is still fresh.
    r5 = Report(id="R5", submitter_id="U5")
    r5.set_details("Keyboard", Money(cents=15000))
    r5.submit("U5", 1000)
    r5.approve(manager, 2000)
    assert r5.expire_stale_approval(5000, max_age) is False
    assert r5.is_approved

    # pay without max_age keeps old semantics even if approval is old.
    r6 = Report(id="R6", submitter_id="U6")
    r6.set_details("Desk", Money(cents=80000))
    r6.submit("U6", 1000)
    r6.approve(manager, 2000)
    r6.pay("F1", 1_000_000)
    assert r6.is_paid

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
