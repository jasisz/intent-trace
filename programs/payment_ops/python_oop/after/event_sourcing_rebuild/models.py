"""Shared domain types for the payment operations showcase."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class PaymentOpsError(Exception):
    """Base class for every domain-level failure in payment operations."""


class UnknownProviderError(PaymentOpsError):
    pass


class UnsupportedProviderError(PaymentOpsError):
    pass


class UnsupportedWebhookKindError(PaymentOpsError):
    pass


class UnsupportedSettlementKindError(PaymentOpsError):
    pass


class UnknownEventError(PaymentOpsError):
    pass


class EmptyReplayError(PaymentOpsError):
    pass


class UnknownCaseError(PaymentOpsError):
    pass


class UnknownCaseStatusError(PaymentOpsError):
    pass


class UnknownCaseFilterError(PaymentOpsError):
    pass


class UnknownComparisonError(PaymentOpsError):
    pass


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


@dataclass(frozen=True)
class PaymentEvent:
    """Base class for canonical payment events."""

    amount: int
    currency: str
    at: str

    @property
    def name(self) -> str:
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


@dataclass(frozen=True)
class PaymentState:
    """Frozen snapshot produced by replay of the event log."""

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
        lowered = raw.lower()
        if lowered == "open":
            return cls.Open
        if lowered == "resolved":
            return cls.Resolved
        raise UnknownCaseStatusError("Case status must be one of: open, resolved")

    @property
    def label(self) -> str:
        if self is CaseStatus.Open:
            return "open"
        if self is CaseStatus.Resolved:
            return "resolved"
        raise UnknownCaseStatusError(f"Unknown status: {self!r}")


@dataclass(frozen=True)
class CaseDraft:
    key: str
    provider: str
    payment_id: str
    kind: str
    detail: str


@dataclass
class ReviewCase:
    """Mutable review case. Lifecycle transitions flip status in place."""

    id: str
    key: str
    provider: str
    payment_id: str
    kind: str
    detail: str
    status: CaseStatus
    created_at: str
    resolved_at: str | None = None
    resolution: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status is CaseStatus.Open

    @property
    def is_resolved(self) -> bool:
        return self.status is CaseStatus.Resolved

    def resolve(self, resolution: str, resolved_at: str) -> None:
        self.status = CaseStatus.Resolved
        self.resolved_at = resolved_at
        self.resolution = resolution


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
            message=f"[{case.kind}] {case.detail}",
            created_at=case.created_at,
        )

    @classmethod
    def for_resolved_case(cls, case: ReviewCase) -> AuditEntry:
        return cls(
            key=f"case-resolved:{case.key}",
            subject_id=f"payment:{case.payment_id}",
            action="case.resolved",
            message=case.resolution if case.resolution is not None else "resolved",
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
    captured_amount: int
    refunded_amount: int


__all__ = [
    "AuditEntry",
    "Authorized",
    "CaseDraft",
    "CaseStatus",
    "Captured",
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
    "UnknownCaseStatusError",
    "UnknownComparisonError",
    "UnknownEventError",
    "UnknownProviderError",
    "UnsupportedProviderError",
    "UnsupportedSettlementKindError",
    "UnsupportedWebhookKindError",
]
