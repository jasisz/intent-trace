"""Settlement-vs-realtime comparison for the payment operations showcase.

The goal is not perfect accounting; it is to make mismatch rules visible as normal
functions instead of hiding them behind batch jobs and SQL views.

Multi-currency rules (this refactor):

* Settlement totals are computed *per currency* for a given payment. The locked
  stream currency (``PaymentState.currency``) selects the slice that gets
  compared against the replayed capture/refund totals.
* Any settlement slice in a *different* currency surfaces as a dedicated
  ``settlement_foreign_currency`` case with enough detail for manual review.
* Summing across currencies is never silent: :func:`_totalByKindInCurrency`
  only ever sums one currency at a time.

Design decisions:

* Settlement rows are treated as evidence to compare against replayed state,
  not as unquestioned truth that overwrites it. Settlement files are
  operationally important but not always clean or complete, so compare-and-
  escalate into manual-review cases leaves room for explicit review instead of
  silently letting either side win. Alternatives considered: settlement wins
  always, realtime wins always.

* Settlement totals are bucketed per canonical currency and compared one slice
  at a time, with any off-currency rows opening their own review cases. A
  single settlement export can contain rows in several currencies for one
  payment, and silently summing their amounts would invent numbers that cannot
  match realtime totals; keeping the comparison inside the payment's canonical
  currency and emitting explicit cases for every off-currency settlement row
  makes the mismatch auditable. Alternatives considered: sum all rows
  regardless of currency, silent conversion at compare time.
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
    """Produce zero or more settlement-vs-realtime cases for one payment.

    When the realtime stream exists, only settlement rows in the stream's
    locked currency participate in the capture/refund totals. Foreign-currency
    settlement slices are emitted as dedicated review cases so the split is
    always explicit.
    """
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
    matchingRows = [row for row in rows if row.currency == state.currency]
    captureSettled = _totalByKindInCurrency(rows, SettlementKind.Capture, state.currency)
    refundSettled = _totalByKindInCurrency(rows, SettlementKind.Refund, state.currency)
    return [
        *_compareCapture(provider, state, captureSettled),
        *_compareRefund(provider, state, refundSettled),
        *_compareCurrency(provider, state, _firstCurrency(matchingRows)),
        *_reportForeignCurrencySettlements(provider, state, rows),
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
    """Flag currency mismatches between replay and settlement.

    Only fires when the first *matching-currency* settlement row disagrees with
    the locked stream currency — which is an impossible state the callers
    should surface, not a legitimate multi-currency refund (those are handled
    by :func:`_reportForeignCurrencySettlements`).
    """
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


def _reportForeignCurrencySettlements(
    provider: str, state: PaymentState, rows: list[SettlementRow]
) -> list[CaseDraft]:
    """Emit one case per foreign-currency settlement slice for a payment.

    A foreign slice is any ``(kind, currency)`` pair whose currency differs
    from the stream's locked currency. Aggregating per ``(kind, currency)``
    keeps case keys stable across re-runs and avoids emitting one case per
    individual row.
    """
    buckets: dict[tuple[SettlementKind, str], int] = {}
    order: list[tuple[SettlementKind, str]] = []
    for row in rows:
        if row.currency == state.currency:
            continue
        bucketKey = (row.kind, row.currency)
        if bucketKey not in buckets:
            buckets[bucketKey] = 0
            order.append(bucketKey)
        buckets[bucketKey] += row.amount
    drafts: list[CaseDraft] = []
    for bucketKey in order:
        kind, currency = bucketKey
        drafts.append(
            Cases.makeCaseDraft(
                provider,
                state.paymentId,
                "settlement_foreign_currency",
                (
                    f"Settlement {_kindLabel(kind)} of {buckets[bucketKey]} {currency} "
                    f"does not match locked payment currency {state.currency}"
                ),
                f"reconcile:foreign-currency-settlement:{provider}:{state.paymentId}:"
                f"{_kindLabel(kind)}:{currency}",
            )
        )
    return drafts


def _kindLabel(kind: SettlementKind) -> str:
    """Stable lowercase label for a settlement kind in case keys and messages."""
    if kind is SettlementKind.Capture:
        return "capture"
    if kind is SettlementKind.Refund:
        return "refund"
    raise ValueError(f"Unknown settlement kind: {kind!r}")


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
    """Sum settlement amounts by capture or refund, refusing mixed currencies.

    Because the domain forbids silent currency conversion, this helper raises
    when the filtered rows contain more than one currency. Callers that need a
    currency-scoped total should use :func:`_totalByKindInCurrency` directly.
    """
    matching = [row for row in rows if row.kind is kind]
    currencies = {row.currency for row in matching}
    if len(currencies) > 1:
        raise ValueError(
            "Cannot sum settlement amounts across mixed currencies: "
            + ", ".join(sorted(currencies))
        )
    return sum(row.amount for row in matching)


def _totalByKindInCurrency(
    rows: list[SettlementRow], kind: SettlementKind, currency: str
) -> int:
    """Sum settlement amounts for one kind inside a single currency slice."""
    return sum(
        row.amount
        for row in rows
        if row.kind is kind and row.currency == currency
    )


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
