"""Append-only event ledger with replay helpers.

Backoffice review gets easier when webhook ingestion becomes append-only events
and every current state is rebuilt from those events in plain code. The
``PaymentLedger`` owns the event log and the replay entry points; it mutates
its internal list on ingest but returns fresh frozen ``PaymentState`` values
from replay.

Currency handling: a payment's currency is pinned by the first canonical
event in the stream. Any later event arriving in a different currency is
recorded verbatim as a foreign-currency anomaly — the event's amount is
NOT added to the running totals, which stay in the pinned currency.
"""

from __future__ import annotations

from dataclasses import replace

from .models import (
    Authorized,
    Captured,
    EmptyReplayError,
    ForeignCurrencyEvent,
    PaymentEvent,
    PaymentEventRecord,
    PaymentState,
    Refunded,
    UnknownEventError,
)
from .normalize import Normalizer


class PaymentLedger:
    """Append-only log of canonical payment events.

    The ledger is the single source of truth for what happened to a payment.
    Ingestion is idempotent by (provider, source_id) so retrying a webhook
    does not corrupt the log.
    """

    def __init__(
        self,
        events: list[PaymentEventRecord] | None = None,
        normalizer: Normalizer | None = None,
    ) -> None:
        self._events: list[PaymentEventRecord] = list(events) if events else []
        self._normalizer = normalizer if normalizer is not None else Normalizer()

    # ------------------------------------------------------------------
    # Read-only views.
    # ------------------------------------------------------------------

    @property
    def events(self) -> list[PaymentEventRecord]:
        """Live list of event records in insertion order."""
        return self._events

    def __len__(self) -> int:
        return len(self._events)

    def has_source_id(self, provider: str, source_id: str) -> bool:
        """True when a webhook source id was already persisted for a provider."""
        return any(
            entry.provider == provider and entry.source_id == source_id
            for entry in self._events
        )

    def next_seq(self, provider: str, payment_id: str) -> int:
        current = 1
        for entry in self._events:
            if entry.provider == provider and entry.payment_id == payment_id:
                current = entry.seq + 1
        return current

    def events_for_payment(self, payment_id: str) -> list[PaymentEventRecord]:
        return [entry for entry in self._events if entry.payment_id == payment_id]

    # ------------------------------------------------------------------
    # Ingest.
    # ------------------------------------------------------------------

    def append(self, record: PaymentEventRecord) -> PaymentEventRecord:
        """Append a canonical event record. Deduplicated by (provider, source_id)."""
        if self.has_source_id(record.provider, record.source_id):
            return record
        self._events.append(record)
        return record

    def ingest_webhook(self, provider: str, raw) -> PaymentEventRecord:
        """Normalize and append a provider webhook. Returns the stored record.

        If the (provider, source_id) pair was already seen, the existing
        record is returned and the log is left untouched.
        """
        canonical_provider = self._normalizer.canonical_provider(provider)
        if self.has_source_id(canonical_provider, raw.source_id):
            for entry in self._events:
                if (
                    entry.provider == canonical_provider
                    and entry.source_id == raw.source_id
                ):
                    return entry
        seq = self.next_seq(canonical_provider, raw.payment_id)
        record = self._normalizer.webhook(canonical_provider, seq, raw)
        self._events.append(record)
        return record

    # ------------------------------------------------------------------
    # Replay.
    # ------------------------------------------------------------------

    def replay_payment(self, payment_id: str) -> PaymentState:
        """Rebuild one payment state from the log."""
        return self.replay_events(self.events_for_payment(payment_id))

    def replay_all(self) -> list[PaymentState]:
        """Replay the whole event log into one state per payment."""
        states: list[PaymentState] = []
        for entry in self._events:
            existing_idx = -1
            for idx, state in enumerate(states):
                if (
                    state.provider == entry.provider
                    and state.payment_id == entry.payment_id
                ):
                    existing_idx = idx
                    break
            if existing_idx < 0:
                states.append(self._apply_event(self._empty_state(entry), entry))
            else:
                states[existing_idx] = self._apply_event(states[existing_idx], entry)
        return states

    def state_for(self, payment_id: str) -> PaymentState:
        return self.replay_payment(payment_id)

    # ------------------------------------------------------------------
    # Replay without ledger context. Exposed for callers that want to
    # replay a subset of records (tests, reconciliation against historical
    # snapshots, etc.).
    # ------------------------------------------------------------------

    @classmethod
    def replay_events(cls, events: list[PaymentEventRecord]) -> PaymentState:
        """Rebuild one payment state from its event stream."""
        if not events:
            raise EmptyReplayError("Cannot replay empty payment event list")
        state = cls._apply_event(cls._empty_state(events[0]), events[0])
        for entry in events[1:]:
            state = cls._apply_event(state, entry)
        return state

    @classmethod
    def replay_events_by_payment(
        cls, events: list[PaymentEventRecord]
    ) -> list[PaymentState]:
        """Replay a bag of events into one state per payment."""
        states: list[PaymentState] = []
        for entry in events:
            existing_idx = -1
            for idx, state in enumerate(states):
                if (
                    state.provider == entry.provider
                    and state.payment_id == entry.payment_id
                ):
                    existing_idx = idx
                    break
            if existing_idx < 0:
                states.append(cls._apply_event(cls._empty_state(entry), entry))
            else:
                states[existing_idx] = cls._apply_event(states[existing_idx], entry)
        return states

    # ------------------------------------------------------------------
    # State transitions. Exposed as classmethods so they can be unit-
    # tested without constructing a full ledger.
    # ------------------------------------------------------------------

    @classmethod
    def _empty_state(cls, entry: PaymentEventRecord) -> PaymentState:
        """Zero-value state seeded from the first event identifiers.

        The first event pins the payment currency. Every later event that
        arrives in a different currency is routed into ``foreign_events``
        instead of accumulating into the totals.
        """
        event = entry.event
        return PaymentState(
            payment_id=entry.payment_id,
            provider=entry.provider,
            currency=event.currency,
            authorized_amount=0,
            captured_amount=0,
            refunded_amount=0,
            latest_at=event.at,
            anomaly_keys=(),
            anomaly_notes=(),
            foreign_events=(),
        )

    @classmethod
    def _apply_event(
        cls, state: PaymentState, entry: PaymentEventRecord
    ) -> PaymentState:
        """Apply one canonical event and record suspicious transitions.

        When the event currency does not match the payment's pinned
        currency the amount is quarantined in ``foreign_events`` and an
        anomaly is raised; the pinned totals stay untouched so the payment
        never silently straddles two currencies.
        """
        event = entry.event
        if state.currency != event.currency:
            return cls._apply_foreign_event(state, entry.source_id, event)
        if isinstance(event, Authorized):
            return cls._apply_authorized(state, entry.source_id, event)
        if isinstance(event, Captured):
            return cls._apply_captured(state, entry.source_id, event)
        if isinstance(event, Refunded):
            return cls._apply_refunded(state, entry.source_id, event)
        raise UnknownEventError(f"Unknown event: {event!r}")

    @classmethod
    def _apply_authorized(
        cls, state: PaymentState, source_id: str, event: Authorized
    ) -> PaymentState:
        """Authorization stores the highest authorized amount seen so far."""
        return replace(
            state,
            authorized_amount=max(state.authorized_amount, event.amount),
            latest_at=event.at,
        )

    @classmethod
    def _apply_captured(
        cls, state: PaymentState, source_id: str, event: Captured
    ) -> PaymentState:
        """Capture increases the captured total and flags suspicious totals."""
        updated = replace(
            state,
            captured_amount=state.captured_amount + event.amount,
            latest_at=event.at,
        )
        if state.authorized_amount == 0:
            updated = updated.with_anomaly(
                f"capture-without-authorize:{source_id}",
                f"Capture arrived before any authorization for payment '{state.payment_id}'",
            )
        if (
            updated.authorized_amount > 0
            and updated.captured_amount > updated.authorized_amount
        ):
            updated = updated.with_anomaly(
                f"captured-exceeds-authorized:{source_id}",
                f"Captured amount exceeds authorized amount for payment '{state.payment_id}'",
            )
        return updated

    @classmethod
    def _apply_refunded(
        cls, state: PaymentState, source_id: str, event: Refunded
    ) -> PaymentState:
        """Refund increases the refunded total and flags order or total mismatches."""
        updated = replace(
            state,
            refunded_amount=state.refunded_amount + event.amount,
            latest_at=event.at,
        )
        if state.captured_amount == 0:
            updated = updated.with_anomaly(
                f"refund-before-capture:{source_id}",
                f"Refund arrived before any capture for payment '{state.payment_id}'",
            )
        if updated.refunded_amount > updated.captured_amount:
            updated = updated.with_anomaly(
                f"refund-exceeds-capture:{source_id}",
                f"Refunded amount exceeds captured amount for payment '{state.payment_id}'",
            )
        return updated

    @classmethod
    def _apply_foreign_event(
        cls,
        state: PaymentState,
        source_id: str,
        event: PaymentEvent,
    ) -> PaymentState:
        """Quarantine an event whose currency does not match the pinned one.

        The event amount does not touch the running totals. Instead it is
        preserved verbatim under ``foreign_events`` and surfaced as an
        anomaly so a human can reconcile the cross-currency transition.
        """
        foreign = ForeignCurrencyEvent(
            source_id=source_id,
            kind=event.name,
            amount=event.amount,
            currency=event.currency,
            at=event.at,
        )
        updated = replace(state, latest_at=event.at).with_foreign_event(foreign)
        return updated.with_anomaly(
            f"foreign-currency-event:{source_id}",
            (
                f"Event in '{event.currency}' does not match payment currency "
                f"'{state.currency}' for payment '{state.payment_id}' "
                f"({event.name} {event.amount} {event.currency})"
            ),
        )


__all__ = ["PaymentLedger"]
