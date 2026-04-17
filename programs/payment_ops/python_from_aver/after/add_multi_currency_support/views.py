"""Read-model helpers for payment summaries and status text.

This module keeps CLI rendering simple by giving it small derived values
instead of making the shell layer reinterpret the ledger.

Multi-currency note: provider-level totals refuse to silently sum across
currencies. A provider whose payments span more than one locked currency
raises ``ValueError`` from :func:`providerSummary` so the CLI layer has to
surface the split explicitly rather than print a misleading scalar.
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


def providerSummary(
    provider: str,
    events: list[PaymentEventRecord],
    states: list[PaymentState],
    rows: list[SettlementRow],
    cases: list[ReviewCase],
) -> ProviderSummary:
    """Aggregate one provider for CLI reporting.

    Raises ``ValueError`` when the provider's payments are locked to more than
    one currency, because the scalar captured/refunded totals on
    :class:`ProviderSummary` cannot honestly represent mixed currencies.
    """
    return ProviderSummary(
        provider=provider,
        payments=_countProviderPayments(states, rows, provider),
        events=_countProviderEvents(events, provider),
        settlements=_countProviderRows(rows, provider),
        openCases=_countOpenCases(cases, provider),
        capturedAmount=_capturedTotal(states, provider),
        refundedAmount=_refundedTotal(states, provider),
    )


def providerCurrencies(states: list[PaymentState], provider: str) -> list[str]:
    """Every distinct locked currency observed for one provider, in first-seen order."""
    seen: list[str] = []
    for state in states:
        if state.provider == provider and state.currency not in seen:
            seen.append(state.currency)
    return seen


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
    """Total captured amount for a provider.

    Refuses to cross currencies: when the provider has payments in more than
    one locked currency, raises ``ValueError`` rather than silently summing.
    """
    _requireSingleCurrency(states, provider)
    return sum(state.capturedAmount for state in states if state.provider == provider)


def _refundedTotal(states: list[PaymentState], provider: str) -> int:
    """Total refunded amount for a provider.

    Refuses to cross currencies: when the provider has payments in more than
    one locked currency, raises ``ValueError`` rather than silently summing.
    """
    _requireSingleCurrency(states, provider)
    return sum(state.refundedAmount for state in states if state.provider == provider)


def _requireSingleCurrency(states: list[PaymentState], provider: str) -> None:
    """Raise when the provider's payments span more than one locked currency."""
    currencies = providerCurrencies(states, provider)
    if len(currencies) > 1:
        raise ValueError(
            "Cannot aggregate amounts across mixed currencies for provider '"
            + provider
            + "': "
            + ", ".join(currencies)
        )
