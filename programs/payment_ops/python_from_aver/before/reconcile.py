"""Settlement-vs-realtime comparison for the payment operations showcase.

The goal is not perfect accounting; it is to make mismatch rules visible as normal
functions instead of hiding them behind batch jobs and SQL views.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import cases as Cases
from .models import CaseDraft, PaymentState, SettlementKind, SettlementRow


# AmountComparison ADT — tagged dataclass union.
@dataclass(frozen=True)
class AmountComparison:
    """Base class for capture/refund comparison outcomes."""


@dataclass(frozen=True)
class BothMissing(AmountComparison):
    pass


@dataclass(frozen=True)
class SettledOnly(AmountComparison):
    amount: int


@dataclass(frozen=True)
class RealtimeOnly(AmountComparison):
    amount: int


@dataclass(frozen=True)
class Exact(AmountComparison):
    pass


@dataclass(frozen=True)
class Mismatch(AmountComparison):
    realtime: int
    settled: int


def settlementsForProvider(
    rows: list[SettlementRow], provider: str
) -> list[SettlementRow]:
    """Filter imported settlement rows by provider."""
    return [row for row in rows if row.provider == provider]


def settlementsForPayment(
    rows: list[SettlementRow], paymentId: str
) -> list[SettlementRow]:
    """Filter imported settlement rows by payment ID."""
    return [row for row in rows if row.paymentId == paymentId]


def reconcileProvider(
    provider: str, states: list[PaymentState], rows: list[SettlementRow]
) -> list[CaseDraft]:
    """Compare replayed payments with imported settlements for one provider."""
    paymentIds = _paymentIdsFrom(states, rows)
    drafts: list[CaseDraft] = []
    for paymentId in paymentIds:
        drafts.extend(
            _comparePayment(
                provider,
                paymentId,
                _findState(states, provider, paymentId),
                settlementsForPayment(rows, paymentId),
            )
        )
    return drafts


def _comparePayment(
    provider: str,
    paymentId: str,
    state: PaymentState | None,
    rows: list[SettlementRow],
) -> list[CaseDraft]:
    """Produce zero or more settlement-vs-realtime cases for one payment."""
    captureSettled = _totalByKind(rows, SettlementKind.Capture)
    refundSettled = _totalByKind(rows, SettlementKind.Refund)
    settlementCurrency = _firstCurrency(rows)
    if state is None:
        if not rows:
            return []
        return [
            Cases.makeCaseDraft(
                provider,
                paymentId,
                "settlement_without_realtime",
                "Settlement exists without any realtime payment events",
                f"reconcile:settlement-without-realtime:{provider}:{paymentId}",
            )
        ]
    return [
        *_compareCapture(provider, state, captureSettled),
        *_compareRefund(provider, state, refundSettled),
        *_compareCurrency(provider, state, settlementCurrency),
    ]


def _compareCapture(
    provider: str, state: PaymentState, settled: int
) -> list[CaseDraft]:
    """Realtime-vs-settlement rules for captures."""
    cmp = _compareAmounts(state.capturedAmount, settled)
    if isinstance(cmp, (BothMissing, Exact)):
        return []
    if isinstance(cmp, SettledOnly):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "settlement_without_capture",
                f"Settlement shows capture {cmp.amount} but realtime captured nothing",
                f"reconcile:settlement-without-capture:{provider}:{state.paymentId}:{cmp.amount}",
            )
        ]
    if isinstance(cmp, RealtimeOnly):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "realtime_missing_settlement",
                f"Realtime captured {cmp.amount} but no settlement row was imported",
                f"reconcile:missing-settlement:{provider}:{state.paymentId}:{cmp.amount}",
            )
        ]
    if isinstance(cmp, Mismatch):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "settlement_capture_mismatch",
                f"Realtime captured {cmp.realtime} but settlement shows {cmp.settled}",
                f"reconcile:capture-mismatch:{provider}:{state.paymentId}:{cmp.realtime}:{cmp.settled}",
            )
        ]
    raise ValueError(f"Unknown comparison: {cmp!r}")


def _compareRefund(
    provider: str, state: PaymentState, settled: int
) -> list[CaseDraft]:
    """Realtime-vs-settlement rules for refunds."""
    cmp = _compareAmounts(state.refundedAmount, settled)
    if isinstance(cmp, (BothMissing, Exact)):
        return []
    if isinstance(cmp, SettledOnly):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "settlement_without_refund",
                f"Settlement shows refund {cmp.amount} but realtime refunded nothing",
                f"reconcile:settlement-without-refund:{provider}:{state.paymentId}:{cmp.amount}",
            )
        ]
    if isinstance(cmp, RealtimeOnly):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "refund_missing_settlement",
                f"Realtime refunded {cmp.amount} but no settlement refund row was imported",
                f"reconcile:refund-missing-settlement:{provider}:{state.paymentId}:{cmp.amount}",
            )
        ]
    if isinstance(cmp, Mismatch):
        return [
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "settlement_refund_mismatch",
                f"Realtime refunded {cmp.realtime} but settlement shows {cmp.settled}",
                f"reconcile:refund-mismatch:{provider}:{state.paymentId}:{cmp.realtime}:{cmp.settled}",
            )
        ]
    raise ValueError(f"Unknown comparison: {cmp!r}")


def _compareAmounts(realtime: int, settled: int) -> AmountComparison:
    """Classify realtime-vs-settlement amount relationships once for capture and refund rules."""
    realtimeMissing = _amountMissing(realtime)
    settledMissing = _amountMissing(settled)
    if realtimeMissing and settledMissing:
        return BothMissing()
    if realtimeMissing:
        return SettledOnly(settled)
    if settledMissing:
        return RealtimeOnly(realtime)
    if realtime == settled:
        return Exact()
    return Mismatch(realtime, settled)


def _amountMissing(amount: int) -> bool:
    """Treat zero as missing evidence for settlement-vs-realtime comparison."""
    return amount == 0


def _compareCurrency(
    provider: str, state: PaymentState, settlementCurrency: str | None
) -> list[CaseDraft]:
    """Flag currency mismatches between replay and settlement."""
    if settlementCurrency is None or settlementCurrency == state.currency:
        return []
    return [
        Cases.makeCaseDraft(
            provider,
            state.paymentId,
            "settlement_currency_mismatch",
            f"Realtime currency is {state.currency} but settlement currency is {settlementCurrency}",
            f"reconcile:currency-mismatch:{provider}:{state.paymentId}:{state.currency}:{settlementCurrency}",
        )
    ]


def _paymentIdsFrom(
    states: list[PaymentState], rows: list[SettlementRow]
) -> list[str]:
    """Build a unique union of payment IDs from replayed state and settlements."""
    acc: list[str] = []
    for state in states:
        if state.paymentId not in acc:
            acc.append(state.paymentId)
    for row in rows:
        if row.paymentId not in acc:
            acc.append(row.paymentId)
    return acc


def _totalByKind(rows: list[SettlementRow], kind: SettlementKind) -> int:
    """Sum settlement amounts by capture or refund."""
    return sum(row.amount for row in rows if row.kind is kind)


def _firstCurrency(rows: list[SettlementRow]) -> str | None:
    """Use the first imported settlement currency as the comparison target."""
    return rows[0].currency if rows else None


def _findState(
    states: list[PaymentState], provider: str, paymentId: str
) -> PaymentState | None:
    """Look up one replayed state for reconciliation."""
    for state in states:
        if state.provider == provider and state.paymentId == paymentId:
            return state
    return None
