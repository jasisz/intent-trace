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


class RejectionCategory(Enum):
    BUDGET_EXCEEDED = "budget-exceeded"
    MISSING_DOCUMENTATION = "missing-documentation"
    POLICY_VIOLATION = "policy-violation"
    DUPLICATE_SUBMISSION = "duplicate-submission"
    OTHER = "other"


class WorkflowError(Exception):
    """Base class for any violation of the expense-report workflow rules."""


class InvalidTransitionError(WorkflowError):
    """Raised when a state transition is attempted from a disallowed status."""


class InsufficientAuthorityError(WorkflowError):
    """Raised when an approver's limit is below the report amount."""


class InvalidReportError(WorkflowError):
    """Raised when a report fails its own invariants (empty title, non-positive amount)."""


class InvalidRejectionReasonError(WorkflowError):
    """Raised when a rejection reason is malformed (e.g. OTHER without a note)."""


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


@dataclass(frozen=True)
class RejectionReason:
    """Closed-category rejection reason with an optional free-form note.

    Note is mandatory when category is OTHER.
    """

    category: RejectionCategory
    note: str | None = None

    def __post_init__(self) -> None:
        if self.category is RejectionCategory.OTHER and not self.note:
            raise InvalidRejectionReasonError(
                "OTHER category requires a non-empty note"
            )

    def render(self) -> str:
        """Human-readable audit-trail form: `<category>` or `<category>: <note>`."""
        if self.note:
            return f"{self.category.value}: {self.note}"
        return self.category.value


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

    def reject(
        self,
        actor: User | str,
        reason: RejectionReason,
        timestamp_ms: int,
    ) -> None:
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot reject from status {self.status.value}"
            )
        self.status = Status.REJECTED
        self._log(actor, timestamp_ms, f"rejected: {reason.render()}")

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

    # Rejection path: BUDGET_EXCEEDED with note.
    r2 = Report(id="R2", submitter_id="U2")
    r2.set_details("Office chair", Money(cents=30000))
    r2.submit("U2", 1000)
    r2.reject(
        manager,
        RejectionReason(RejectionCategory.BUDGET_EXCEEDED, "out of budget"),
        2000,
    )
    assert r2.is_rejected
    assert r2.is_terminal
    assert r2.events[-1].note == "rejected: budget-exceeded: out of budget"

    # Rejection path: MISSING_DOCUMENTATION, no note.
    r3 = Report(id="R3", submitter_id="U3")
    r3.set_details("Client dinner", Money(cents=12000))
    r3.submit("U3", 1000)
    r3.reject(
        manager,
        RejectionReason(RejectionCategory.MISSING_DOCUMENTATION),
        2000,
    )
    assert r3.is_rejected
    assert r3.events[-1].note == "rejected: missing-documentation"

    # Rejection path: POLICY_VIOLATION, no note.
    r4 = Report(id="R4", submitter_id="U4")
    r4.set_details("Gift card", Money(cents=5000))
    r4.submit("U4", 1000)
    r4.reject(
        manager,
        RejectionReason(RejectionCategory.POLICY_VIOLATION),
        2000,
    )
    assert r4.is_rejected
    assert r4.events[-1].note == "rejected: policy-violation"

    # Rejection path: DUPLICATE_SUBMISSION with note.
    r5 = Report(id="R5", submitter_id="U5")
    r5.set_details("Flight", Money(cents=80000))
    r5.submit("U5", 1000)
    r5.reject(
        manager,
        RejectionReason(RejectionCategory.DUPLICATE_SUBMISSION, "see R2"),
        2000,
    )
    assert r5.is_rejected
    assert r5.events[-1].note == "rejected: duplicate-submission: see R2"

    # Rejection path: OTHER requires a note.
    r6 = Report(id="R6", submitter_id="U6")
    r6.set_details("Misc", Money(cents=2000))
    r6.submit("U6", 1000)
    r6.reject(
        manager,
        RejectionReason(RejectionCategory.OTHER, "see email thread"),
        2000,
    )
    assert r6.is_rejected
    assert r6.events[-1].note == "rejected: other: see email thread"

    # OTHER without a note is rejected at construction time.
    try:
        RejectionReason(RejectionCategory.OTHER)
        raise AssertionError("expected OTHER without note to fail")
    except InvalidRejectionReasonError as e:
        assert "other" in str(e).lower()

    try:
        RejectionReason(RejectionCategory.OTHER, "")
        raise AssertionError("expected OTHER with empty note to fail")
    except InvalidRejectionReasonError:
        pass

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
