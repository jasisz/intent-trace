"""Provider-specific normalization into canonical payment events and settlement rows.

The project accepts ugly external spellings, but it stores one internal event model
so replay and reconciliation stay provider-agnostic.

Currency strings are preserved verbatim from the source payload; the ledger and
reconcile layers decide what to do when currencies drift apart inside a single
payment stream.

Design decisions:

* Incoming provider payloads are collapsed into a canonical event and settlement
  model as the very first step, instead of letting provider-specific statuses
  leak through into replay and reconciliation. Dirty backoffice work starts
  with incompatible provider payloads, and normalizing early keeps replay,
  reconciliation, and case logic typed and reviewable. Alternatives considered:
  provider-specific replay everywhere, stringly-typed statuses.
"""

from __future__ import annotations

from .models import (
    Authorized,
    Captured,
    PaymentEvent,
    PaymentEventRecord,
    RawSettlement,
    RawWebhook,
    Refunded,
    SettlementKind,
    SettlementRow,
)


def normalizeWebhook(provider: str, seq: int, raw: RawWebhook) -> PaymentEventRecord:
    """Map one provider-specific webhook row into the canonical event log."""
    normalizedProvider = normalizeProvider(provider)
    event = normalizeWebhookEvent(normalizedProvider, raw)
    return PaymentEventRecord(
        provider=normalizedProvider,
        sourceId=raw.sourceId,
        paymentId=raw.paymentId,
        seq=seq,
        event=event,
    )


def normalizeSettlement(provider: str, raw: RawSettlement) -> SettlementRow:
    """Map one provider-specific settlement row into the canonical settlement model."""
    normalizedProvider = normalizeProvider(provider)
    kind = normalizeSettlementKind(normalizedProvider, raw.kind)
    return SettlementRow(
        provider=normalizedProvider,
        rowId=raw.rowId,
        paymentId=raw.paymentId,
        kind=kind,
        amount=raw.amount,
        currency=raw.currency,
        settledOn=raw.settledOn,
    )


def normalizeProvider(provider: str) -> str:
    """Accept the small supported provider set with case-insensitive input."""
    normalized = provider.strip().lower()
    if normalized == "stripe":
        return "stripe"
    if normalized == "adyen":
        return "adyen"
    raise ValueError("Provider must be one of: stripe, adyen")


def normalizeWebhookEvent(provider: str, raw: RawWebhook) -> PaymentEvent:
    """Dispatch webhook normalization after provider canonicalization."""
    if provider == "stripe":
        return normalizeStripeWebhook(raw)
    if provider == "adyen":
        return normalizeAdyenWebhook(raw)
    raise ValueError("Unsupported provider: " + provider)


def normalizeStripeWebhook(raw: RawWebhook) -> PaymentEvent:
    """Stripe uses verbose event names."""
    if raw.kind == "payment_intent.amount_capturable_updated":
        return Authorized(raw.amount, raw.currency, raw.occurredAt)
    if raw.kind == "charge.captured":
        return Captured(raw.amount, raw.currency, raw.occurredAt)
    if raw.kind == "charge.refunded":
        return Refunded(raw.amount, raw.currency, raw.occurredAt)
    raise ValueError("Unsupported stripe webhook kind: " + raw.kind)


def normalizeAdyenWebhook(raw: RawWebhook) -> PaymentEvent:
    """Adyen uses short uppercase webhook names."""
    if raw.kind == "AUTHORISATION":
        return Authorized(raw.amount, raw.currency, raw.occurredAt)
    if raw.kind == "CAPTURE":
        return Captured(raw.amount, raw.currency, raw.occurredAt)
    if raw.kind == "REFUND":
        return Refunded(raw.amount, raw.currency, raw.occurredAt)
    raise ValueError("Unsupported adyen webhook kind: " + raw.kind)


def normalizeSettlementKind(provider: str, kind: str) -> SettlementKind:
    """Map provider-specific settlement labels into capture or refund."""
    if provider == "stripe":
        return normalizeStripeSettlement(kind)
    if provider == "adyen":
        return normalizeAdyenSettlement(kind)
    raise ValueError("Unsupported provider: " + provider)


def normalizeStripeSettlement(kind: str) -> SettlementKind:
    """Stripe settlement exports use short lowercase labels."""
    if kind == "charge":
        return SettlementKind.Capture
    if kind == "refund":
        return SettlementKind.Refund
    raise ValueError("Unsupported stripe settlement kind: " + kind)


def normalizeAdyenSettlement(kind: str) -> SettlementKind:
    """Adyen settlement exports use title-cased labels."""
    if kind == "Settled":
        return SettlementKind.Capture
    if kind == "Refunded":
        return SettlementKind.Refund
    raise ValueError("Unsupported adyen settlement kind: " + kind)
