"""Shared domain types for the payment operations showcase.

This project models dirty payment ingestion, settlement import, reconciliation,
and manual-review cases without hiding the state machine in infrastructure.

Multi-currency note: ``PaymentState.currency`` records the *locked* currency of
one payment stream — whatever the first normalized event sets. Any subsequent
event arriving in a different currency is surfaced as an anomaly rather than
silently folded into the captured/refunded totals, so sums always stay
single-currency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class RawWebhook:
    sourceId: str
    paymentId: str
    kind: str
    amount: int
    currency: str
    occurredAt: str


@dataclass(frozen=True)
class RawSettlement:
    rowId: str
    paymentId: str
    kind: str
    amount: int
    currency: str
    settledOn: str


# PaymentEvent ADT — tagged dataclass union.
@dataclass(frozen=True)
class PaymentEvent:
    """Base class for canonical payment events."""


@dataclass(frozen=True)
class Authorized(PaymentEvent):
    amount: int
    currency: str
    at: str


@dataclass(frozen=True)
class Captured(PaymentEvent):
    amount: int
    currency: str
    at: str


@dataclass(frozen=True)
class Refunded(PaymentEvent):
    amount: int
    currency: str
    at: str


@dataclass(frozen=True)
class PaymentEventRecord:
    provider: str
    sourceId: str
    paymentId: str
    seq: int
    event: PaymentEvent


class SettlementKind(Enum):
    Capture = "Capture"
    Refund = "Refund"


@dataclass(frozen=True)
class SettlementRow:
    provider: str
    rowId: str
    paymentId: str
    kind: SettlementKind
    amount: int
    currency: str
    settledOn: str


@dataclass(frozen=True)
class PaymentState:
    """Replayed state of one payment stream.

    ``currency`` is the locked currency of the stream (the first event's
    currency). Captured and refunded totals are tracked only for that locked
    currency; foreign-currency events are recorded as anomalies instead of
    silently being summed in.
    """

    paymentId: str
    provider: str
    currency: str
    authorizedAmount: int
    capturedAmount: int
    refundedAmount: int
    latestAt: str
    anomalyKeys: tuple[str, ...]
    anomalyNotes: tuple[str, ...]


class CaseStatus(Enum):
    Open = "Open"
    Resolved = "Resolved"


@dataclass(frozen=True)
class CaseDraft:
    key: str
    provider: str
    paymentId: str
    kind: str
    detail: str


@dataclass(frozen=True)
class ReviewCase:
    id: str
    key: str
    provider: str
    paymentId: str
    kind: str
    detail: str
    status: CaseStatus
    createdAt: str
    resolvedAt: str | None
    resolution: str | None


@dataclass(frozen=True)
class AuditEntry:
    key: str
    subjectId: str
    action: str
    message: str
    createdAt: str


@dataclass(frozen=True)
class PaymentDetail:
    state: PaymentState
    events: tuple[PaymentEventRecord, ...]
    settlements: tuple[SettlementRow, ...]
    openCases: tuple[ReviewCase, ...]


@dataclass(frozen=True)
class ProviderSummary:
    provider: str
    payments: int
    events: int
    settlements: int
    openCases: int
    capturedAmount: int
    refundedAmount: int
