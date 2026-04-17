"""Provider-specific normalization into canonical payment events and settlement rows."""

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
    UnknownProviderError,
    UnsupportedProviderError,
    UnsupportedSettlementKindError,
    UnsupportedWebhookKindError,
)


class ProviderNormalizer:
    """Contract every provider-specific normalizer follows."""

    provider: str = ""

    def webhook_event(self, raw: RawWebhook) -> PaymentEvent:
        raise NotImplementedError

    def settlement_kind(self, kind: str) -> SettlementKind:
        raise NotImplementedError


class StripeNormalizer(ProviderNormalizer):
    provider = "stripe"

    def webhook_event(self, raw: RawWebhook) -> PaymentEvent:
        if raw.kind == "payment_intent.amount_capturable_updated":
            return Authorized(raw.amount, raw.currency, raw.occurred_at)
        if raw.kind == "charge.captured":
            return Captured(raw.amount, raw.currency, raw.occurred_at)
        if raw.kind == "charge.refunded":
            return Refunded(raw.amount, raw.currency, raw.occurred_at)
        raise UnsupportedWebhookKindError(
            "Unsupported stripe webhook kind: " + raw.kind
        )

    def settlement_kind(self, kind: str) -> SettlementKind:
        if kind == "charge":
            return SettlementKind.Capture
        if kind == "refund":
            return SettlementKind.Refund
        raise UnsupportedSettlementKindError(
            "Unsupported stripe settlement kind: " + kind
        )


class AdyenNormalizer(ProviderNormalizer):
    provider = "adyen"

    def webhook_event(self, raw: RawWebhook) -> PaymentEvent:
        if raw.kind == "AUTHORISATION":
            return Authorized(raw.amount, raw.currency, raw.occurred_at)
        if raw.kind == "CAPTURE":
            return Captured(raw.amount, raw.currency, raw.occurred_at)
        if raw.kind == "REFUND":
            return Refunded(raw.amount, raw.currency, raw.occurred_at)
        raise UnsupportedWebhookKindError(
            "Unsupported adyen webhook kind: " + raw.kind
        )

    def settlement_kind(self, kind: str) -> SettlementKind:
        if kind == "Settled":
            return SettlementKind.Capture
        if kind == "Refunded":
            return SettlementKind.Refund
        raise UnsupportedSettlementKindError(
            "Unsupported adyen settlement kind: " + kind
        )


class Normalizer:
    """Front door for turning raw provider payloads into canonical models."""

    _SUPPORTED = ("stripe", "adyen")

    def __init__(self) -> None:
        self._normalizers: dict[str, ProviderNormalizer] = {
            StripeNormalizer.provider: StripeNormalizer(),
            AdyenNormalizer.provider: AdyenNormalizer(),
        }

    def canonical_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized in self._normalizers:
            return normalized
        raise UnknownProviderError(
            "Provider must be one of: " + ", ".join(self._SUPPORTED)
        )

    def _normalizer_for(self, provider: str) -> ProviderNormalizer:
        try:
            return self._normalizers[provider]
        except KeyError as exc:
            raise UnsupportedProviderError(
                "Unsupported provider: " + provider
            ) from exc

    def webhook(self, provider: str, seq: int, raw: RawWebhook) -> PaymentEventRecord:
        """Map one provider-specific webhook row into the canonical event log."""
        normalized_provider = self.canonical_provider(provider)
        event = self._normalizer_for(normalized_provider).webhook_event(raw)
        return PaymentEventRecord(
            provider=normalized_provider,
            source_id=raw.source_id,
            payment_id=raw.payment_id,
            seq=seq,
            event=event,
        )

    def settlement(self, provider: str, raw: RawSettlement) -> SettlementRow:
        normalized_provider = self.canonical_provider(provider)
        kind = self._normalizer_for(normalized_provider).settlement_kind(raw.kind)
        return SettlementRow(
            provider=normalized_provider,
            row_id=raw.row_id,
            payment_id=raw.payment_id,
            kind=kind,
            amount=raw.amount,
            currency=raw.currency,
            settled_on=raw.settled_on,
        )

    def webhook_event(self, provider: str, raw: RawWebhook) -> PaymentEvent:
        return self._normalizer_for(provider).webhook_event(raw)

    def settlement_kind(self, provider: str, kind: str) -> SettlementKind:
        return self._normalizer_for(provider).settlement_kind(kind)


__all__ = [
    "AdyenNormalizer",
    "Normalizer",
    "ProviderNormalizer",
    "StripeNormalizer",
]
