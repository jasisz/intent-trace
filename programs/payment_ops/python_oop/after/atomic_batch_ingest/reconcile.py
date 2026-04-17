"""Settlement-vs-realtime comparison for the payment operations showcase.

The goal is not perfect accounting; it is to make mismatch rules visible as
normal classes instead of hiding them behind batch jobs and SQL views. The
``Reconciler`` walks every payment mentioned in either replay state or the
settlement import and produces case drafts for anything that does not agree.
"""

from __future__ import annotations

from dataclasses import dataclass

from cases import CaseDraftFactory
from models import (
    CaseDraft,
    PaymentState,
    SettlementKind,
    SettlementRow,
    UnknownComparisonError,
)


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

    @staticmethod
    def of(realtime: int, settled: int) -> AmountComparison:
        """Classify realtime-vs-settlement amount relationships.

        Treats zero as missing evidence on either side so a missing row does
        not look like a hard mismatch.
        """
        realtime_missing = realtime == 0
        settled_missing = settled == 0
        if realtime_missing and settled_missing:
            return BothMissing()
        if realtime_missing:
            return SettledOnly(settled)
        if settled_missing:
            return RealtimeOnly(realtime)
        if realtime == settled:
            return Exact()
        return Mismatch(realtime, settled)


class Reconciler:
    """Compares replayed payment state with imported settlement rows."""

    @staticmethod
    def settlements_for_provider(
        rows: list[SettlementRow], provider: str
    ) -> list[SettlementRow]:
        return [row for row in rows if row.provider == provider]

    @staticmethod
    def settlements_for_payment(
        rows: list[SettlementRow], payment_id: str
    ) -> list[SettlementRow]:
        return [row for row in rows if row.payment_id == payment_id]

    def reconcile_provider(
        self,
        provider: str,
        states: list[PaymentState],
        rows: list[SettlementRow],
    ) -> list[CaseDraft]:
        """Compare replayed payments with imported settlements for one provider."""
        payment_ids = self._payment_ids_from(states, rows)
        drafts: list[CaseDraft] = []
        for payment_id in payment_ids:
            state = self._find_state(states, provider, payment_id)
            drafts.extend(
                self.compare_payment(
                    provider,
                    payment_id,
                    state,
                    self.settlements_for_payment(rows, payment_id),
                )
            )
        return drafts

    def compare_payment(
        self,
        provider: str,
        payment_id: str,
        state: PaymentState | None,
        rows: list[SettlementRow],
    ) -> list[CaseDraft]:
        """Produce zero or more settlement-vs-realtime cases for one payment."""
        if state is None:
            if not rows:
                return []
            return [
                CaseDraftFactory.make(
                    provider,
                    payment_id,
                    "settlement_without_realtime",
                    "Settlement exists without any realtime payment events",
                    f"reconcile:settlement-without-realtime:{provider}:{payment_id}",
                )
            ]
        capture_settled = self._total_by_kind(rows, SettlementKind.Capture)
        refund_settled = self._total_by_kind(rows, SettlementKind.Refund)
        settlement_currency = self._first_currency(rows)
        return [
            *self.compare_capture(provider, state, capture_settled),
            *self.compare_refund(provider, state, refund_settled),
            *self.compare_currency(provider, state, settlement_currency),
        ]

    def compare_capture(
        self, provider: str, state: PaymentState, settled: int
    ) -> list[CaseDraft]:
        cmp = Mismatch.of(state.captured_amount, settled)
        if isinstance(cmp, (BothMissing, Exact)):
            return []
        if isinstance(cmp, SettledOnly):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "settlement_without_capture",
                    f"Settlement shows capture {cmp.amount} but realtime captured nothing",
                    f"reconcile:settlement-without-capture:{provider}:{state.payment_id}:{cmp.amount}",
                )
            ]
        if isinstance(cmp, RealtimeOnly):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "realtime_missing_settlement",
                    f"Realtime captured {cmp.amount} but no settlement row was imported",
                    f"reconcile:missing-settlement:{provider}:{state.payment_id}:{cmp.amount}",
                )
            ]
        if isinstance(cmp, Mismatch):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "settlement_capture_mismatch",
                    f"Realtime captured {cmp.realtime} but settlement shows {cmp.settled}",
                    f"reconcile:capture-mismatch:{provider}:{state.payment_id}:{cmp.realtime}:{cmp.settled}",
                )
            ]
        raise UnknownComparisonError(f"Unknown comparison: {cmp!r}")

    def compare_refund(
        self, provider: str, state: PaymentState, settled: int
    ) -> list[CaseDraft]:
        cmp = Mismatch.of(state.refunded_amount, settled)
        if isinstance(cmp, (BothMissing, Exact)):
            return []
        if isinstance(cmp, SettledOnly):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "settlement_without_refund",
                    f"Settlement shows refund {cmp.amount} but realtime refunded nothing",
                    f"reconcile:settlement-without-refund:{provider}:{state.payment_id}:{cmp.amount}",
                )
            ]
        if isinstance(cmp, RealtimeOnly):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "refund_missing_settlement",
                    f"Realtime refunded {cmp.amount} but no settlement refund row was imported",
                    f"reconcile:refund-missing-settlement:{provider}:{state.payment_id}:{cmp.amount}",
                )
            ]
        if isinstance(cmp, Mismatch):
            return [
                CaseDraftFactory.make(
                    provider,
                    state.payment_id,
                    "settlement_refund_mismatch",
                    f"Realtime refunded {cmp.realtime} but settlement shows {cmp.settled}",
                    f"reconcile:refund-mismatch:{provider}:{state.payment_id}:{cmp.realtime}:{cmp.settled}",
                )
            ]
        raise UnknownComparisonError(f"Unknown comparison: {cmp!r}")

    def compare_currency(
        self,
        provider: str,
        state: PaymentState,
        settlement_currency: str | None,
    ) -> list[CaseDraft]:
        if settlement_currency is None or settlement_currency == state.currency:
            return []
        return [
            CaseDraftFactory.make(
                provider,
                state.payment_id,
                "settlement_currency_mismatch",
                f"Realtime currency is {state.currency} but settlement currency is {settlement_currency}",
                f"reconcile:currency-mismatch:{provider}:{state.payment_id}:{state.currency}:{settlement_currency}",
            )
        ]

    @staticmethod
    def _payment_ids_from(
        states: list[PaymentState], rows: list[SettlementRow]
    ) -> list[str]:
        acc: list[str] = []
        for state in states:
            if state.payment_id not in acc:
                acc.append(state.payment_id)
        for row in rows:
            if row.payment_id not in acc:
                acc.append(row.payment_id)
        return acc

    @staticmethod
    def _total_by_kind(rows: list[SettlementRow], kind: SettlementKind) -> int:
        return sum(row.amount for row in rows if row.kind is kind)

    @staticmethod
    def _first_currency(rows: list[SettlementRow]) -> str | None:
        return rows[0].currency if rows else None

    @staticmethod
    def _find_state(
        states: list[PaymentState], provider: str, payment_id: str
    ) -> PaymentState | None:
        for state in states:
            if state.provider == provider and state.payment_id == payment_id:
                return state
        return None


__all__ = [
    "AmountComparison",
    "BothMissing",
    "Exact",
    "Mismatch",
    "RealtimeOnly",
    "Reconciler",
    "SettledOnly",
]
