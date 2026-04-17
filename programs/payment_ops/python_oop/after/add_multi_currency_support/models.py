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


class UnknownComparisonError(PaymentOpsError):
    """Raised when an amount-comparison value falls outside the tagged union."""


class CurrencyMismatchError(PaymentOpsError):
    """Raised when amounts in different currencies are summed together.

    Domain logic refuses to perform silent conversion across currencies.
    Callers must group amounts by currency before summing, or explicitly
    handle the mismatch upstream (typically by opening a review case).
    """


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
# Money helpers. Amounts are integers tagged with an ISO currency code; any
# attempt to add values in different currencies fails loudly rather than
# performing a silent conversion inside domain logic.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Money:
    """Integer amount carrying its currency tag.

    ``Money`` refuses to add across currencies — callers must either group by
    currency first or surface the mismatch as an anomaly.
    """

    amount: int
    currency: str

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot sum amounts in different currencies: {self.currency} and {other.currency}"
            )
        return Money(self.amount + other.amount, self.currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(0, currency)


def sum_money(values: list[Money]) -> Money | None:
    """Sum a list of ``Money`` values, requiring a single currency.

    Returns ``None`` for an empty list; callers decide how to treat an empty
    aggregate. Any currency divergence raises :class:`CurrencyMismatchError`.
    """
    if not values:
        return None
    total = values[0]
    for item in values[1:]:
        total = total + item
    return total


# ---------------------------------------------------------------------------
# Replayed payment state. Frozen because it is a pure value object produced
# by replay; transitions go through ``with_anomaly``/``replace`` rather than
# in-place mutation so sharing a state across call sites stays safe.
#
# Currency semantics: ``currency`` is pinned by the first event on the
# payment stream and never changes. Events that arrive with a different
# currency are recorded as anomalies and their amounts are NOT added to the
# running totals — all totals are in ``currency``.
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
    foreign_events: tuple["ForeignCurrencyEvent", ...] = ()

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomaly_notes) > 0

    @property
    def is_fully_refunded(self) -> bool:
        return self.refunded_amount > 0 and self.captured_amount == self.refunded_amount

    @property
    def is_partially_refunded(self) -> bool:
        return self.refunded_amount > 0 and self.captured_amount != self.refunded_amount

    @property
    def captured_money(self) -> Money:
        return Money(self.captured_amount, self.currency)

    @property
    def refunded_money(self) -> Money:
        return Money(self.refunded_amount, self.currency)

    def with_anomaly(self, key: str, note: str) -> PaymentState:
        """Append one anomaly marker in replay order."""
        return replace(
            self,
            anomaly_keys=self.anomaly_keys + (key,),
            anomaly_notes=self.anomaly_notes + (note,),
        )

    def with_foreign_event(self, foreign: "ForeignCurrencyEvent") -> PaymentState:
        """Record an event whose currency did not match the pinned one."""
        return replace(self, foreign_events=self.foreign_events + (foreign,))


@dataclass(frozen=True)
class ForeignCurrencyEvent:
    """One event whose currency did not match the payment's pinned currency.

    Captured verbatim so manual review has the original amount, currency,
    and kind without needing to re-walk the raw event log.
    """

    source_id: str
    kind: str
    amount: int
    currency: str
    at: str


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
    captured_by_currency: tuple[tuple[str, int], ...]
    refunded_by_currency: tuple[tuple[str, int], ...]


__all__ = [
    "AuditEntry",
    "Authorized",
    "CaseDraft",
    "CaseStatus",
    "Captured",
    "CurrencyMismatchError",
    "EmptyReplayError",
    "ForeignCurrencyEvent",
    "Money",
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
    "sum_money",
]
