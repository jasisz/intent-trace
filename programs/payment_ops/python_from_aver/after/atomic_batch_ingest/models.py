"""Shared domain types for the payment operations showcase.

This project models dirty payment ingestion, settlement import, reconciliation,
and manual-review cases without hiding the state machine in infrastructure.
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


# --- Batch ingestion ---------------------------------------------------------
#
# A batch ingest takes a list of raw webhooks and either applies every one or
# leaves the caller's event log untouched. Failures travel back as a
# ``BatchIngestError`` that points at the offending entry so operators can fix
# or drop just that webhook and retry the whole batch.


@dataclass(frozen=True)
class BatchIngestItem:
    """One raw webhook inside a batch, tagged with its provider.

    The tag lives on the item rather than on the batch so a single burst can
    mix multiple providers — real webhook pollers often drain several queues
    in one tick.
    """

    provider: str
    raw: RawWebhook


class BatchIngestError(ValueError):
    """Raised when any webhook in a batch fails to normalize or append.

    Attributes mirror the failing batch slot so the operator UI can underline
    the exact offending row and its provider label without reparsing the
    original burst.
    """

    def __init__(self, index: int, item: BatchIngestItem, cause: BaseException) -> None:
        """Capture the failing slot so operators can see exactly which row broke."""
        self.index = index
        self.item = item
        self.cause = cause
        super().__init__(
            f"Batch ingest failed at index {index} "
            f"(provider={item.provider!r}, sourceId={item.raw.sourceId!r}): {cause}"
        )
