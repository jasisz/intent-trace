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


class UnauthorizedActorError(WorkflowError):
    """Raised when an actor other than the original submitter attempts a submitter-only action."""


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
        self._log(approver, timestamp_ms, "approved")

    def reject(self, actor: User | str, reason: str, timestamp_ms: int) -> None:
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot reject from status {self.status.value}"
            )
        self.status = Status.REJECTED
        self._log(actor, timestamp_ms, f"rejected: {reason}")

    def withdraw(self, actor: User | str, timestamp_ms: int) -> None:
        """Return a Submitted report to Draft; only the original submitter may withdraw."""
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot withdraw from status {self.status.value}"
            )
        actor_id = actor.id if isinstance(actor, User) else actor
        if actor_id != self.submitter_id:
            raise UnauthorizedActorError(
                f"Actor {actor_id} is not the submitter; only {self.submitter_id} may withdraw"
            )
        self.status = Status.DRAFT
        self._log(actor, timestamp_ms, "withdrawn")

    def pay(self, actor: User | str, timestamp_ms: int) -> None:
        if not self.is_approved:
            raise InvalidTransitionError(
                f"Cannot pay from status {self.status.value}"
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

    # Withdraw path: submitter withdraws a submitted report back to Draft, edits, resubmits.
    r3 = Report(id="R3", submitter_id="U3")
    r3.set_details("Conference fee", Money(cents=20000))
    r3.submit("U3", 1000)
    assert r3.is_submitted
    r3.withdraw("U3", 1500)
    assert r3.is_draft
    # Original submission event is preserved; withdrawal is appended.
    assert len(r3.events) == 2
    assert r3.events[0].note == "submitted"
    assert r3.events[1].note == "withdrawn"
    assert r3.events[1].actor_id == "U3"
    # Edit and resubmit after withdrawal.
    r3.set_details("Conference fee (updated)", Money(cents=25000))
    r3.submit("U3", 2000)
    assert r3.is_submitted
    assert len(r3.events) == 3
    assert r3.events[2].note == "submitted"

    # Non-submitter cannot withdraw.
    r4 = Report(id="R4", submitter_id="U4")
    r4.set_details("Laptop", Money(cents=80000))
    r4.submit("U4", 1000)
    try:
        r4.withdraw("U5", 1500)
        raise AssertionError("expected non-submitter rejection")
    except UnauthorizedActorError as e:
        assert "submitter" in str(e).lower()
    # Status unchanged and no event logged for failed withdrawal.
    assert r4.is_submitted
    assert len(r4.events) == 1
    # Also reject a User who is not the submitter.
    other_user = User(id="U6", name="Other", approval_limit_cents=0)
    try:
        r4.withdraw(other_user, 1600)
        raise AssertionError("expected non-submitter User rejection")
    except UnauthorizedActorError:
        pass
    assert r4.is_submitted
    assert len(r4.events) == 1

    # Terminal-state rejection: cannot withdraw after payment or rejection.
    r5 = Report(id="R5", submitter_id="U7")
    r5.set_details("Hotel", Money(cents=40000))
    r5.submit("U7", 1000)
    r5.approve(manager, 2000)
    r5.pay("F1", 3000)
    assert r5.is_paid
    try:
        r5.withdraw("U7", 4000)
        raise AssertionError("expected terminal (paid) withdraw rejection")
    except InvalidTransitionError:
        pass

    r6 = Report(id="R6", submitter_id="U8")
    r6.set_details("Taxi", Money(cents=5000))
    r6.submit("U8", 1000)
    r6.reject(manager, "duplicate", 2000)
    assert r6.is_rejected
    try:
        r6.withdraw("U8", 3000)
        raise AssertionError("expected terminal (rejected) withdraw rejection")
    except InvalidTransitionError:
        pass

    # Cannot withdraw from Approved (not a terminal state but also not Submitted).
    r7 = Report(id="R7", submitter_id="U9")
    r7.set_details("Monitor", Money(cents=60000))
    r7.submit("U9", 1000)
    r7.approve(manager, 2000)
    assert r7.is_approved
    try:
        r7.withdraw("U9", 3000)
        raise AssertionError("expected approved withdraw rejection")
    except InvalidTransitionError:
        pass

    # Cannot withdraw from Draft.
    r8 = Report(id="R8", submitter_id="U10")
    try:
        r8.withdraw("U10", 1000)
        raise AssertionError("expected draft withdraw rejection")
    except InvalidTransitionError:
        pass

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
