"""Shared domain types for the payment operations showcase.

This project models dirty payment ingestion, settlement import, reconciliation,
and manual-review cases without hiding the state machine in infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


# ---------------------------------------------------------------------------
# Exception hierarchy. Callers can catch PaymentOpsError for a broad net, or
# one of the concrete subclasses to handle a specific failure.
# ---------------------------------------------------------------------------


class PaymentOpsError(Exception):
    """Base class for every domain-level failure in payment operations."""


class UnknownProviderError(PaymentOpsError):
    """Raised when a provider is not in the supported set."""


class UnsupportedProviderError(PaymentOpsError):
    """Raised when normalization dispatches on an unsupported provider."""


class UnsupportedWebhookKindError(PaymentOpsError):
    """Raised when a provider webhook kind is not recognized."""


class UnsupportedSettlementKindError(PaymentOpsError):
    """Raised when a provider settlement kind is not recognized."""


class UnknownEventError(PaymentOpsError):
    """Raised when a non-canonical event escapes the tagged union."""


class EmptyReplayError(PaymentOpsError):
    """Raised when asked to replay an empty event stream."""


class UnknownCaseError(PaymentOpsError):
    """Raised when resolving a case id that was never opened."""


class UnknownCaseStatusError(PaymentOpsError):
    """Raised when parsing a case status that is not open or resolved."""


class UnknownCaseFilterError(PaymentOpsError):
    """Raised when a case list filter is not open, resolved, or all."""


class UnknownCasePriorityError(PaymentOpsError):
    """Raised when parsing a priority label outside the canonical set."""


class UnknownComparisonError(PaymentOpsError):
    """Raised when an amount-comparison value falls outside the tagged union."""


# ---------------------------------------------------------------------------
# Raw ingestion payloads. These mirror dirty provider spellings and stay
# frozen because they represent immutable incoming messages.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawWebhook:
    source_id: str
    payment_id: str
    kind: str
    amount: int
    currency: str
    occurred_at: str


@dataclass(frozen=True)
class RawSettlement:
    row_id: str
    payment_id: str
    kind: str
    amount: int
    currency: str
    settled_on: str


# ---------------------------------------------------------------------------
# Canonical payment events. A small hierarchy with shared behavior via
# properties and subclass specialization via ``.name``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentEvent:
    """Base class for canonical payment events."""

    amount: int
    currency: str
    at: str

    @property
    def name(self) -> str:
        """Stable display name for this canonical event."""
        raise UnknownEventError(f"Unknown event: {self!r}")


@dataclass(frozen=True)
class Authorized(PaymentEvent):
    @property
    def name(self) -> str:
        return "Authorized"


@dataclass(frozen=True)
class Captured(PaymentEvent):
    @property
    def name(self) -> str:
        return "Captured"


@dataclass(frozen=True)
class Refunded(PaymentEvent):
    @property
    def name(self) -> str:
        return "Refunded"


@dataclass(frozen=True)
class PaymentEventRecord:
    provider: str
    source_id: str
    payment_id: str
    seq: int
    event: PaymentEvent


class SettlementKind(Enum):
    Capture = "Capture"
    Refund = "Refund"


@dataclass(frozen=True)
class SettlementRow:
    provider: str
    row_id: str
    payment_id: str
    kind: SettlementKind
    amount: int
    currency: str
    settled_on: str


# ---------------------------------------------------------------------------
# Replayed payment state. Frozen because it is a pure value object produced
# by replay; transitions go through ``with_anomaly``/``replace`` rather than
# in-place mutation so sharing a state across call sites stays safe.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentState:
    payment_id: str
    provider: str
    currency: str
    authorized_amount: int
    captured_amount: int
    refunded_amount: int
    latest_at: str
    anomaly_keys: tuple[str, ...] = ()
    anomaly_notes: tuple[str, ...] = ()

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomaly_notes) > 0

    @property
    def is_fully_refunded(self) -> bool:
        return self.refunded_amount > 0 and self.captured_amount == self.refunded_amount

    @property
    def is_partially_refunded(self) -> bool:
        return self.refunded_amount > 0 and self.captured_amount != self.refunded_amount

    def with_anomaly(self, key: str, note: str) -> PaymentState:
        """Append one anomaly marker in replay order."""
        return replace(
            self,
            anomaly_keys=self.anomaly_keys + (key,),
            anomaly_notes=self.anomaly_notes + (note,),
        )


class CaseStatus(Enum):
    Open = "Open"
    Resolved = "Resolved"

    @classmethod
    def parse(cls, raw: str) -> CaseStatus:
        """Parse one stored case status, accepting lowercase or title case."""
        lowered = raw.lower()
        if lowered == "open":
            return cls.Open
        if lowered == "resolved":
            return cls.Resolved
        raise UnknownCaseStatusError("Case status must be one of: open, resolved")

    @property
    def label(self) -> str:
        """Stable on-disk and CLI spelling for this status."""
        if self is CaseStatus.Open:
            return "open"
        if self is CaseStatus.Resolved:
            return "resolved"
        raise UnknownCaseStatusError(f"Unknown status: {self!r}")


class CasePriority(Enum):
    """Operator-visible priority tiers, ordered from least to most urgent."""

    Low = "Low"
    Normal = "Normal"
    High = "High"
    Urgent = "Urgent"

    @classmethod
    def parse(cls, raw: str) -> CasePriority:
        """Parse one stored priority label, accepting any letter casing."""
        lowered = raw.lower()
        if lowered == "low":
            return cls.Low
        if lowered == "normal":
            return cls.Normal
        if lowered == "high":
            return cls.High
        if lowered == "urgent":
            return cls.Urgent
        raise UnknownCasePriorityError(
            "Case priority must be one of: low, normal, high, urgent"
        )

    @property
    def label(self) -> str:
        """Stable on-disk and CLI spelling for this priority tier."""
        if self is CasePriority.Low:
            return "low"
        if self is CasePriority.Normal:
            return "normal"
        if self is CasePriority.High:
            return "high"
        if self is CasePriority.Urgent:
            return "urgent"
        raise UnknownCasePriorityError(f"Unknown priority: {self!r}")

    @property
    def rank(self) -> int:
        """Numeric ordering (higher = more urgent) for sort and promotion math."""
        if self is CasePriority.Low:
            return 0
        if self is CasePriority.Normal:
            return 1
        if self is CasePriority.High:
            return 2
        if self is CasePriority.Urgent:
            return 3
        raise UnknownCasePriorityError(f"Unknown priority: {self!r}")

    @classmethod
    def from_rank(cls, rank: int) -> CasePriority:
        """Clamp an integer rank into the enum, capped at Urgent and floored at Low."""
        if rank <= 0:
            return cls.Low
        if rank == 1:
            return cls.Normal
        if rank == 2:
            return cls.High
        return cls.Urgent

    def bumped_up(self, steps: int = 1) -> CasePriority:
        """Return a new priority promoted by ``steps`` levels, clamped at Urgent."""
        return CasePriority.from_rank(self.rank + steps)


DEFAULT_PRIORITY: CasePriority = CasePriority.Normal


@dataclass(frozen=True)
class CaseDraft:
    """Pre-registration description of a case, including auto-derived priority."""

    key: str
    provider: str
    payment_id: str
    kind: str
    detail: str
    amount: int = 0
    priority: CasePriority = DEFAULT_PRIORITY


@dataclass
class ReviewCase:
    """Mutable review case. Lifecycle transitions flip status and priority in place."""

    id: str
    key: str
    provider: str
    payment_id: str
    kind: str
    detail: str
    status: CaseStatus
    priority: CasePriority
    created_at: str
    resolved_at: str | None = None
    resolution: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status is CaseStatus.Open

    @property
    def is_resolved(self) -> bool:
        return self.status is CaseStatus.Resolved

    @property
    def is_urgent(self) -> bool:
        return self.priority is CasePriority.Urgent

    def resolve(
        self,
        resolution: str,
        resolved_at: str,
        priority: CasePriority | None = None,
    ) -> None:
        """Close the case. Optional ``priority`` lets operators finalize the tier."""
        self.status = CaseStatus.Resolved
        self.resolved_at = resolved_at
        self.resolution = resolution
        if priority is not None:
            self.priority = priority

    def reprioritize(self, priority: CasePriority) -> None:
        """Operator-driven manual override outside the resolve flow."""
        self.priority = priority


@dataclass(frozen=True)
class AuditEntry:
    key: str
    subject_id: str
    action: str
    message: str
    created_at: str

    @classmethod
    def for_opened_case(cls, case: ReviewCase) -> AuditEntry:
        return cls(
            key=f"case-opened:{case.key}",
            subject_id=f"payment:{case.payment_id}",
            action="case.opened",
            message=f"[{case.priority.label}][{case.kind}] {case.detail}",
            created_at=case.created_at,
        )

    @classmethod
    def for_resolved_case(cls, case: ReviewCase) -> AuditEntry:
        resolution = case.resolution if case.resolution is not None else "resolved"
        return cls(
            key=f"case-resolved:{case.key}",
            subject_id=f"payment:{case.payment_id}",
            action="case.resolved",
            message=f"[{case.priority.label}] {resolution}",
            created_at=case.resolved_at if case.resolved_at is not None else case.created_at,
        )


@dataclass(frozen=True)
class PaymentDetail:
    state: PaymentState
    events: tuple[PaymentEventRecord, ...]
    settlements: tuple[SettlementRow, ...]
    open_cases: tuple[ReviewCase, ...]


@dataclass(frozen=True)
class ProviderSummary:
    provider: str
    payments: int
    events: int
    settlements: int
    open_cases: int
    urgent_cases: int
    captured_amount: int
    refunded_amount: int


__all__ = [
    "AuditEntry",
    "Authorized",
    "CaseDraft",
    "CasePriority",
    "CaseStatus",
    "Captured",
    "DEFAULT_PRIORITY",
    "EmptyReplayError",
    "PaymentDetail",
    "PaymentEvent",
    "PaymentEventRecord",
    "PaymentOpsError",
    "PaymentState",
    "ProviderSummary",
    "RawSettlement",
    "RawWebhook",
    "Refunded",
    "ReviewCase",
    "SettlementKind",
    "SettlementRow",
    "UnknownCaseError",
    "UnknownCaseFilterError",
    "UnknownCasePriorityError",
    "UnknownCaseStatusError",
    "UnknownComparisonError",
    "UnknownEventError",
    "UnknownProviderError",
    "UnsupportedProviderError",
    "UnsupportedSettlementKindError",
    "UnsupportedWebhookKindError",
]
