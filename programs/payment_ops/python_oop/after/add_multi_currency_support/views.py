"""Read-model helpers for payment summaries and status text.

This module keeps CLI rendering simple by giving it small derived values
instead of making the shell layer reinterpret the ledger. Classes here are
pure reporters; they do not own any state of their own.

Aggregations that used to collapse into a single integer total are now
grouped by currency, because domain logic refuses to sum across currencies
without an explicit conversion step.
"""

from __future__ import annotations

from .models import (
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

    @property
    def captured_text(self) -> str:
        """Captured total rendered with its currency tag."""
        return f"{self._state.captured_amount} {self._state.currency}"

    @property
    def refunded_text(self) -> str:
        return f"{self._state.refunded_amount} {self._state.currency}"

    @property
    def foreign_events_summary(self) -> str:
        """Human-readable list of quarantined cross-currency events."""
        foreigns = self._state.foreign_events
        if not foreigns:
            return "-"
        return " | ".join(
            f"{fx.kind} {fx.amount} {fx.currency}" for fx in foreigns
        )


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
        """Aggregate one provider for CLI reporting.

        ``captured_by_currency`` and ``refunded_by_currency`` carry the
        per-currency breakdown instead of a single integer total, because
        amounts in different currencies must not be silently summed.
        """
        return ProviderSummary(
            provider=provider,
            payments=self.payment_count(provider),
            events=self.event_count(provider),
            settlements=self.settlement_count(provider),
            open_cases=self.open_case_count(provider),
            captured_by_currency=self.captured_by_currency(provider),
            refunded_by_currency=self.refunded_by_currency(provider),
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

    def captured_by_currency(self, provider: str) -> tuple[tuple[str, int], ...]:
        """Per-currency captured totals across replayed payment states."""
        return self._grouped_totals(
            provider, lambda state: state.captured_amount
        )

    def refunded_by_currency(self, provider: str) -> tuple[tuple[str, int], ...]:
        """Per-currency refunded totals across replayed payment states."""
        return self._grouped_totals(
            provider, lambda state: state.refunded_amount
        )

    def _grouped_totals(
        self,
        provider: str,
        amount_of,
    ) -> tuple[tuple[str, int], ...]:
        """Group one amount dimension by currency across the provider's states."""
        buckets: list[list] = []
        for state in self._states:
            if state.provider != provider:
                continue
            amount = amount_of(state)
            if amount == 0:
                continue
            matched = False
            for bucket in buckets:
                if bucket[0] == state.currency:
                    bucket[1] += amount
                    matched = True
                    break
            if not matched:
                buckets.append([state.currency, amount])
        return tuple((bucket[0], bucket[1]) for bucket in buckets)


__all__ = ["PaymentView", "ProviderReporter"]
