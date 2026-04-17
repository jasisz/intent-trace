"""Read-model helpers for payment summaries and status text.

This module keeps CLI rendering simple by giving it small derived values
instead of making the shell layer reinterpret the ledger. Classes here are
pure reporters; they do not own any state of their own. The case listing
helpers expose priority so downstream consumers can filter and sort.
"""

from __future__ import annotations

from .models import (
    CasePriority,
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
        """Short text summary for CLI details."""
        if not self._state.anomaly_notes:
            return "-"
        return " | ".join(self._state.anomaly_notes)


class CaseListView:
    """Read-only projection of review cases for downstream consumers.

    The view never mutates the underlying list; it only slices and
    reorders. Every row exposes priority as a first-class field so
    filter-by-priority and sort-by-priority are cheap.
    """

    def __init__(self, cases: list[ReviewCase]) -> None:
        self._cases = list(cases)

    @property
    def cases(self) -> list[ReviewCase]:
        return self._cases

    def __len__(self) -> int:
        return len(self._cases)

    def with_priority(self, priority: CasePriority) -> list[ReviewCase]:
        """Keep only the rows at the given priority tier."""
        return [case for case in self._cases if case.priority is priority]

    def sorted_by_priority(self, descending: bool = True) -> list[ReviewCase]:
        """Return a copy ordered by priority rank (Urgent first by default)."""
        sign = -1 if descending else 1
        return sorted(self._cases, key=lambda case: sign * case.priority.rank)

    def priority_counts(self) -> dict[CasePriority, int]:
        """Histogram by priority for dashboards; every tier appears at least as zero."""
        counts: dict[CasePriority, int] = {tier: 0 for tier in CasePriority}
        for case in self._cases:
            counts[case.priority] = counts[case.priority] + 1
        return counts

    def render_row(self, case: ReviewCase) -> str:
        """Single-line CLI row including priority and status labels."""
        return (
            f"{case.id} [{case.priority.label}] ({case.status.label}) "
            f"{case.kind}: {case.detail}"
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
        """Aggregate one provider for CLI reporting."""
        return ProviderSummary(
            provider=provider,
            payments=self.payment_count(provider),
            events=self.event_count(provider),
            settlements=self.settlement_count(provider),
            open_cases=self.open_case_count(provider),
            urgent_cases=self.urgent_case_count(provider),
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
        """Count canonical event rows for the provider summary."""
        return sum(1 for entry in self._events if entry.provider == provider)

    def settlement_count(self, provider: str) -> int:
        """Count imported settlement rows for a provider."""
        return sum(1 for row in self._rows if row.provider == provider)

    def open_case_count(self, provider: str) -> int:
        """Count open cases for a provider."""
        return sum(
            1
            for case in self._cases
            if case.provider == provider and case.status is CaseStatus.Open
        )

    def urgent_case_count(self, provider: str) -> int:
        """Count open cases at Urgent priority so dashboards can alert."""
        return sum(
            1
            for case in self._cases
            if case.provider == provider
            and case.status is CaseStatus.Open
            and case.priority is CasePriority.Urgent
        )

    def captured_total(self, provider: str) -> int:
        """Total captured amount for a provider."""
        return sum(
            state.captured_amount
            for state in self._states
            if state.provider == provider
        )

    def refunded_total(self, provider: str) -> int:
        """Total refunded amount for a provider."""
        return sum(
            state.refunded_amount
            for state in self._states
            if state.provider == provider
        )


__all__ = ["CaseListView", "PaymentView", "ProviderReporter"]
