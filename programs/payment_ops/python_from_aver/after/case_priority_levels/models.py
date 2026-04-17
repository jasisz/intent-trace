"""Shared domain types for the payment operations showcase.

This project models dirty payment ingestion, settlement import, reconciliation,
and manual-review cases without hiding the state machine in infrastructure.

Compared to the baseline, this variant adds an operator-facing priority
dimension to every manual-review case. Priority is carried alongside the
existing kind semantics; it does not replace them.
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


class CasePriority(Enum):
    """Operator-facing severity banding for manual-review cases.

    The ordering Low < Normal < High < Urgent is used when downstream views
    rank cases for review queues. The enum is intentionally small because the
    goal is routing, not risk scoring.
    """

    Low = "Low"
    Normal = "Normal"
    High = "High"
    Urgent = "Urgent"


@dataclass(frozen=True)
class CaseDraft:
    """One proposed manual-review case.

    `amount` carries the monetary context used to derive priority at
    materialization time. Replay-anomaly drafts use the largest observed
    amount in the payment stream, while reconciliation drafts use the
    concrete mismatch amount. Zero means "no amount signal available" and
    keeps priority at Normal.
    """

    key: str
    provider: str
    paymentId: str
    kind: str
    detail: str
    amount: int = 0


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
    priority: CasePriority = CasePriority.Normal


@dataclass(frozen=True)
class AuditEntry:
    """Stable audit row for case lifecycle changes.

    The optional `priority` field makes the severity visible in the audit
    trail, so downstream consumers can reconstruct operator triage without
    having to join against the live case table.
    """

    key: str
    subjectId: str
    action: str
    message: str
    createdAt: str
    priority: CasePriority | None = None


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
