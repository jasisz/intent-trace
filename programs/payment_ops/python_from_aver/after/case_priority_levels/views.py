"""Read-model helpers for payment summaries and status text.

This module keeps CLI rendering simple by giving it small derived values
instead of making the shell layer reinterpret the ledger.

This variant also exposes case priority — both as a short text label per
case and as an aggregate per-provider view — so downstream consumers can
filter or sort manual-review queues without reimporting the domain rules.
"""

from __future__ import annotations

from . import cases as Cases
from .models import (
    CasePriority,
    CaseStatus,
    PaymentEventRecord,
    PaymentState,
    ProviderSummary,
    ReviewCase,
    SettlementRow,
)


def statusText(state: PaymentState) -> str:
    """User-facing payment status derived from totals and anomalies."""
    if len(state.anomalyNotes) > 0:
        return "review"
    if state.refundedAmount > 0:
        if state.capturedAmount == state.refundedAmount:
            return "refunded"
        return "partially_refunded"
    if state.capturedAmount > 0:
        return "captured"
    if state.authorizedAmount > 0:
        return "authorized"
    return "empty"


def anomalySummary(state: PaymentState) -> str:
    """Short text summary for CLI details."""
    if not state.anomalyNotes:
        return "-"
    return " | ".join(state.anomalyNotes)


def casePriorityText(item: ReviewCase) -> str:
    """Expose each case's priority as a CLI-friendly label."""
    return Cases.priorityText(item.priority)


def providerSummary(
    provider: str,
    events: list[PaymentEventRecord],
    states: list[PaymentState],
    rows: list[SettlementRow],
    cases: list[ReviewCase],
) -> ProviderSummary:
    """Aggregate one provider for CLI reporting."""
    return ProviderSummary(
        provider=provider,
        payments=_countProviderPayments(states, rows, provider),
        events=_countProviderEvents(events, provider),
        settlements=_countProviderRows(rows, provider),
        openCases=_countOpenCases(cases, provider),
        capturedAmount=_capturedTotal(states, provider),
        refundedAmount=_refundedTotal(states, provider),
    )


def openCasesByPriority(
    cases: list[ReviewCase], provider: str
) -> dict[CasePriority, int]:
    """Count open cases per priority for one provider.

    Downstream dashboards typically render this as a 4-column bar, so the
    return value keeps every priority even when the count is zero — callers
    do not have to defensively key the dictionary.
    """
    counts = {priority: 0 for priority in CasePriority}
    for item in cases:
        if item.provider != provider:
            continue
        if item.status is not CaseStatus.Open:
            continue
        counts[item.priority] = counts[item.priority] + 1
    return counts


def _countProviderPayments(
    states: list[PaymentState], rows: list[SettlementRow], provider: str
) -> int:
    """Count unique payments seen either in replay or in settlement imports."""
    acc: list[str] = []
    for state in states:
        if state.provider == provider and state.paymentId not in acc:
            acc.append(state.paymentId)
    for row in rows:
        if row.provider == provider and row.paymentId not in acc:
            acc.append(row.paymentId)
    return len(acc)


def _countProviderEvents(events: list[PaymentEventRecord], provider: str) -> int:
    """Count canonical event rows for the provider summary."""
    return sum(1 for entry in events if entry.provider == provider)


def _countProviderRows(rows: list[SettlementRow], provider: str) -> int:
    """Count imported settlement rows for a provider."""
    return sum(1 for row in rows if row.provider == provider)


def _countOpenCases(cases: list[ReviewCase], provider: str) -> int:
    """Count open cases for a provider."""
    return sum(
        1
        for item in cases
        if item.provider == provider and item.status is CaseStatus.Open
    )


def _capturedTotal(states: list[PaymentState], provider: str) -> int:
    """Total captured amount for a provider."""
    return sum(state.capturedAmount for state in states if state.provider == provider)


def _refundedTotal(states: list[PaymentState], provider: str) -> int:
    """Total refunded amount for a provider."""
    return sum(state.refundedAmount for state in states if state.provider == provider)
