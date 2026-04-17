"""Expense-report workflow with core state split from the audit trail."""
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


def _actor_id(actor: User | str) -> str:
    return actor.id if isinstance(actor, User) else actor


@dataclass
class ReportCore:
    """Mutable core state of a report: identity and lifecycle, without audit history."""

    id: str
    submitter_id: str
    title: str = ""
    amount: Money = field(default_factory=lambda: Money(cents=0))
    status: Status = Status.DRAFT

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

    def submit(self) -> None:
        if not self.is_draft:
            raise InvalidTransitionError(
                f"Cannot submit from status {self.status.value}"
            )
        if not self.title:
            raise InvalidReportError("Report title must not be empty")
        if not self.amount.is_positive:
            raise InvalidReportError("Report amount must be positive")
        self.status = Status.SUBMITTED

    def approve(self, approver: User) -> None:
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

    def reject(self) -> None:
        if not self.is_submitted:
            raise InvalidTransitionError(
                f"Cannot reject from status {self.status.value}"
            )
        self.status = Status.REJECTED

    def pay(self) -> None:
        if not self.is_approved:
            raise InvalidTransitionError(
                f"Cannot pay from status {self.status.value}"
            )
        self.status = Status.PAID


@dataclass
class AuditedReport:
    """Report that composes `ReportCore` with an audit log of transition events.

    Each transition delegates to the core for validation and state change, and
    appends a matching `Event` only after the core mutation succeeds.
    """

    core: ReportCore
    events: list[Event] = field(default_factory=list)

    @classmethod
    def new(cls, id: str, submitter_id: str) -> AuditedReport:
        return cls(core=ReportCore(id=id, submitter_id=submitter_id))

    # Read-through helpers so callers can use the audited wrapper as a report view.
    @property
    def id(self) -> str:
        return self.core.id

    @property
    def submitter_id(self) -> str:
        return self.core.submitter_id

    @property
    def title(self) -> str:
        return self.core.title

    @property
    def amount(self) -> Money:
        return self.core.amount

    @property
    def status(self) -> Status:
        return self.core.status

    @property
    def is_terminal(self) -> bool:
        return self.core.is_terminal

    @property
    def is_draft(self) -> bool:
        return self.core.is_draft

    @property
    def is_submitted(self) -> bool:
        return self.core.is_submitted

    @property
    def is_approved(self) -> bool:
        return self.core.is_approved

    @property
    def is_rejected(self) -> bool:
        return self.core.is_rejected

    @property
    def is_paid(self) -> bool:
        return self.core.is_paid

    def set_details(self, title: str, amount: Money) -> None:
        self.core.set_details(title, amount)

    def submit(self, actor: User | str, timestamp_ms: int) -> None:
        self.core.submit()
        self._log(actor, timestamp_ms, "submitted")

    def approve(self, approver: User, timestamp_ms: int) -> None:
        self.core.approve(approver)
        self._log(approver, timestamp_ms, "approved")

    def reject(self, actor: User | str, reason: str, timestamp_ms: int) -> None:
        self.core.reject()
        self._log(actor, timestamp_ms, f"rejected: {reason}")

    def pay(self, actor: User | str, timestamp_ms: int) -> None:
        self.core.pay()
        self._log(actor, timestamp_ms, "paid")

    def _log(self, actor: User | str, timestamp_ms: int, note: str) -> None:
        self.events.append(
            Event(timestamp_ms=timestamp_ms, actor_id=_actor_id(actor), note=note)
        )


def _smoke_tests_core() -> None:
    # Core exposes state transitions without any audit payload.
    c = ReportCore(id="R0", submitter_id="U0")
    assert c.is_draft
    assert not hasattr(c, "events")

    try:
        c.submit()
        raise AssertionError("expected empty title to fail")
    except InvalidReportError:
        pass

    c.set_details("Hotel", Money(cents=20000))

    try:
        c.set_details("still draft? no", Money(cents=1))
        c.submit()
    except WorkflowError as e:
        raise AssertionError(f"unexpected core failure: {e}") from e
    assert c.is_submitted

    manager = User(id="M0", name="Manager", approval_limit_cents=100000)
    c.approve(manager)
    assert c.is_approved

    c.pay()
    assert c.is_paid
    assert c.is_terminal

    try:
        c.pay()
        raise AssertionError("expected terminal rejection")
    except InvalidTransitionError:
        pass


def _smoke_tests_audited() -> None:
    r = AuditedReport.new(id="R1", submitter_id="U1")
    assert r.is_draft
    assert r.events == []

    try:
        r.submit("U1", 1000)
        raise AssertionError("expected empty title to fail")
    except InvalidReportError as e:
        assert "title" in str(e).lower()
    # Failed transition must not leak an event into the audit log.
    assert r.events == []

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
    # Authority failure must not record an approval event.
    assert len(r.events) == 1

    r.approve(manager, 2000)
    assert r.is_approved

    r.pay("F1", 3000)
    assert r.is_paid
    assert r.is_terminal

    assert len(r.events) == 3
    assert r.events[0].note == "submitted"
    assert r.events[1].note == "approved"
    assert r.events[2].note == "paid"

    # Terminal states reject further transitions, at both layers.
    try:
        r.pay("F1", 4000)
        raise AssertionError("expected terminal rejection")
    except InvalidTransitionError:
        pass
    assert len(r.events) == 3

    # Rejection path.
    r2 = AuditedReport.new(id="R2", submitter_id="U2")
    r2.set_details("Office chair", Money(cents=30000))
    r2.submit("U2", 1000)
    r2.reject(manager, "out of budget", 2000)
    assert r2.is_rejected
    assert r2.is_terminal
    assert r2.events[-1].note == "rejected: out of budget"

    # Core within an audited report is directly inspectable and shares state.
    assert r2.core.status is Status.REJECTED
    assert r2.status is r2.core.status


def _smoke_tests() -> None:
    _smoke_tests_core()
    _smoke_tests_audited()
    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
