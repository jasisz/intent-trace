"""Read-model helpers for payment summaries and status text.

This module keeps CLI rendering simple by giving it small derived values
instead of making the shell layer reinterpret the ledger. Classes here are
pure reporters; they do not own any state of their own.
"""

from __future__ import annotations

from models import (
    CaseStatus,
    PaymentEventRecord,
    PaymentState,
    ProviderSummary,
    ReviewCase,
    SettlementRow,
)


class PaymentView:
    """Presentation helpers that read from one :class:`PaymentState`."""

    def __init__(self, state: PaymentState) -> None:
        self._state = state

    @property
    def state(self) -> PaymentState:
        return self._state

    @property
    def status_text(self) -> str:
        """User-facing payment status derived from totals and anomalies."""
        state = self._state
        if state.has_anomalies:
            return "review"
        if state.refunded_amount > 0:
            if state.captured_amount == state.refunded_amount:
                return "refunded"
            return "partially_refunded"
        if state.captured_amount > 0:
            return "captured"
        if state.authorized_amount > 0:
            return "authorized"
        return "empty"

    @property
    def anomaly_summary(self) -> str:
        if not self._state.anomaly_notes:
            return "-"
        return " | ".join(self._state.anomaly_notes)


class ProviderReporter:
    """Aggregates one provider's events, states, settlements, and cases."""

    def __init__(
        self,
        events: list[PaymentEventRecord],
        states: list[PaymentState],
        rows: list[SettlementRow],
        cases: list[ReviewCase],
    ) -> None:
        self._events = events
        self._states = states
        self._rows = rows
        self._cases = cases

    def summary(self, provider: str) -> ProviderSummary:
        return ProviderSummary(
            provider=provider,
            payments=self.payment_count(provider),
            events=self.event_count(provider),
            settlements=self.settlement_count(provider),
            open_cases=self.open_case_count(provider),
            captured_amount=self.captured_total(provider),
            refunded_amount=self.refunded_total(provider),
        )

    def payment_count(self, provider: str) -> int:
        """Count unique payments seen either in replay or in settlement imports."""
        seen: list[str] = []
        for state in self._states:
            if state.provider == provider and state.payment_id not in seen:
                seen.append(state.payment_id)
        for row in self._rows:
            if row.provider == provider and row.payment_id not in seen:
                seen.append(row.payment_id)
        return len(seen)

    def event_count(self, provider: str) -> int:
        return sum(1 for entry in self._events if entry.provider == provider)

    def settlement_count(self, provider: str) -> int:
        return sum(1 for row in self._rows if row.provider == provider)

    def open_case_count(self, provider: str) -> int:
        return sum(
            1
            for case in self._cases
            if case.provider == provider and case.status is CaseStatus.Open
        )

    def captured_total(self, provider: str) -> int:
        return sum(
            state.captured_amount
            for state in self._states
            if state.provider == provider
        )

    def refunded_total(self, provider: str) -> int:
        return sum(
            state.refunded_amount
            for state in self._states
            if state.provider == provider
        )


__all__ = ["PaymentView", "ProviderReporter"]
