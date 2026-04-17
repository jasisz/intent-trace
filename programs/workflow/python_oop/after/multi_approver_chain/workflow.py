"""Expense-report approval workflow with multi-approver chain for large amounts."""
from __future__ import annotations

from dataclasses import dataclass, field
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


class WorkflowError(Exception):
    """Base class for any violation of the expense-report workflow rules."""


class InvalidTransitionError(WorkflowError):
    """Raised when a state transition is attempted from a disallowed status."""


class InsufficientAuthorityError(WorkflowError):
    """Raised when an approver's limit is below the report amount."""


class InvalidReportError(WorkflowError):
    """Raised when a report fails its own invariants (empty title, non-positive amount)."""


class DuplicateApproverError(WorkflowError):
    """Raised when the same approver tries to sign a report twice."""


@dataclass(frozen=True)
class Money:
    cents: int

    @property
    def is_positive(self) -> bool:
        return self.cents > 0

    @property
    def requires_approver_chain(self) -> bool:
        """True if the amount is large enough to require multiple approvers."""
        return self.cents > HIGH_AMOUNT_THRESHOLD_CENTS

    @property
    def required_approver_count(self) -> int:
        """Number of distinct approvers needed before the report can be Approved."""
        if self.requires_approver_chain:
            return CHAIN_APPROVER_COUNT
        return SINGLE_APPROVER_COUNT


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
_AWAITING_STATUSES = frozenset({Status.SUBMITTED, Status.PENDING_APPROVAL})


@dataclass
class Report:
    """Mutable expense report that transitions through the approval workflow.

    Large amounts require a chain of approvers: each qualified approver signs
    off in turn, and the report sits in ``PENDING_APPROVAL`` until the last
    required approver signs. Small amounts approve on the first sign-off.
    """

    id: str
    submitter_id: str
    title: str = ""
    amount: Money = field(default_factory=lambda: Money(cents=0))
    status: Status = Status.DRAFT
    events: list[Event] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)

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
    def is_pending_approval(self) -> bool:
        return self.status is Status.PENDING_APPROVAL

    @property
    def is_approved(self) -> bool:
        return self.status is Status.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.status is Status.REJECTED

    @property
    def is_paid(self) -> bool:
        return self.status is Status.PAID

    @property
    def is_awaiting_approval(self) -> bool:
        return self.status in _AWAITING_STATUSES

    @property
    def remaining_approvals(self) -> int:
        """How many more approvers are still needed for full approval."""
        needed = self.amount.required_approver_count
        have = len(self.approvals)
        return max(0, needed - have)

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
        """Record one approval; advances status to Approved on the final sign-off.

        Each approver must individually have authority for the full amount. The
        same approver cannot sign twice. If any approver in the chain lacks
        sufficient authority, the step fails and the report stays in its
        intermediate state.
        """
        if not self.is_awaiting_approval:
            raise InvalidTransitionError(
                f"Cannot approve from status {self.status.value}"
            )
        if not approver.can_approve(self.amount):
            raise InsufficientAuthorityError(
                f"Approver {approver.id} limit too low for amount"
            )
        if approver.id in self.approvals:
            raise DuplicateApproverError(
                f"Approver {approver.id} has already approved this report"
            )

        self.approvals.append(approver.id)
        needed = self.amount.required_approver_count
        is_final = len(self.approvals) >= needed
        if is_final:
            self.status = Status.APPROVED
            note = "approved"
        else:
            self.status = Status.PENDING_APPROVAL
            note = f"approval {len(self.approvals)}/{needed}"
        self._log(approver, timestamp_ms, note)

    def reject(self, actor: User | str, reason: str, timestamp_ms: int) -> None:
        if not self.is_awaiting_approval:
            raise InvalidTransitionError(
                f"Cannot reject from status {self.status.value}"
            )
        self.status = Status.REJECTED
        self._log(actor, timestamp_ms, f"rejected: {reason}")

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

    # Single-approver path for a small amount (below threshold).
    r.set_details("Travel - Q2", Money(cents=45000))
    assert r.amount.required_approver_count == SINGLE_APPROVER_COUNT
    assert not r.amount.requires_approver_chain
    r.submit("U1", 1000)
    assert r.is_submitted

    manager = User(id="M1", name="Manager", approval_limit_cents=100000)
    junior = User(id="J1", name="Junior", approval_limit_cents=10000)
    director = User(id="D1", name="Director", approval_limit_cents=500000)
    vp = User(id="V1", name="VP", approval_limit_cents=1000000)

    try:
        r.approve(junior, 1500)
        raise AssertionError("expected limit failure")
    except InsufficientAuthorityError as e:
        assert "limit" in str(e).lower()

    r.approve(manager, 2000)
    assert r.is_approved
    assert r.approvals == ["M1"]
    assert r.remaining_approvals == 0

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

    # Rejection path from Submitted (small amount).
    r2 = Report(id="R2", submitter_id="U2")
    r2.set_details("Office chair", Money(cents=30000))
    r2.submit("U2", 1000)
    r2.reject(manager, "out of budget", 2000)
    assert r2.is_rejected
    assert r2.is_terminal

    # Multi-approver chain for a large amount (above threshold).
    big = Report(id="R3", submitter_id="U3")
    big.set_details("Conference sponsorship", Money(cents=250_000))
    assert big.amount.requires_approver_chain
    assert big.amount.required_approver_count == CHAIN_APPROVER_COUNT
    big.submit("U3", 1000)
    assert big.is_submitted
    assert big.remaining_approvals == CHAIN_APPROVER_COUNT

    # Approver without authority for the full amount cannot participate even in the chain.
    try:
        big.approve(manager, 1100)  # manager limit 100_000 < 250_000
        raise AssertionError("expected chain-step authority failure")
    except InsufficientAuthorityError as e:
        assert "limit" in str(e).lower()
    # Failed step leaves the report untouched (still Submitted, no approvals recorded).
    assert big.is_submitted
    assert big.approvals == []

    # First qualified approver signs; report moves to PendingApproval.
    big.approve(director, 1200)
    assert big.is_pending_approval
    assert big.approvals == ["D1"]
    assert big.remaining_approvals == 1

    # Same approver cannot sign twice.
    try:
        big.approve(director, 1300)
        raise AssertionError("expected duplicate-approver rejection")
    except DuplicateApproverError as e:
        assert "already" in str(e).lower()

    # Cannot pay a partially approved report.
    try:
        big.pay("F1", 1400)
        raise AssertionError("expected pay-before-approved rejection")
    except InvalidTransitionError:
        pass

    # Second qualified approver finalizes approval.
    big.approve(vp, 1500)
    assert big.is_approved
    assert big.approvals == ["D1", "V1"]
    assert big.remaining_approvals == 0

    big.pay("F1", 1600)
    assert big.is_paid

    assert [e.note for e in big.events] == [
        "submitted",
        "approval 1/2",
        "approved",
        "paid",
    ]
    assert [e.actor_id for e in big.events] == ["U3", "D1", "V1", "F1"]

    # Rejection from PendingApproval is also allowed.
    big2 = Report(id="R4", submitter_id="U4")
    big2.set_details("Vendor contract", Money(cents=300_000))
    big2.submit("U4", 1000)
    big2.approve(director, 1100)
    assert big2.is_pending_approval
    big2.reject(vp, "needs revision", 1200)
    assert big2.is_rejected
    assert big2.is_terminal

    # Boundary: amount exactly at threshold is still single-approver (strictly above triggers chain).
    at_threshold = Report(id="R5", submitter_id="U5")
    at_threshold.set_details("Edge", Money(cents=HIGH_AMOUNT_THRESHOLD_CENTS))
    assert not at_threshold.amount.requires_approver_chain
    assert at_threshold.amount.required_approver_count == SINGLE_APPROVER_COUNT
    at_threshold.submit("U5", 1000)
    at_threshold.approve(manager, 1100)
    assert at_threshold.is_approved

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
