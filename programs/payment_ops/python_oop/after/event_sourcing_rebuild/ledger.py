"""Append-only event ledger with a single pure rebuilder for replay.

The ledger owns the append-only event log. Every replay path — whether it
targets one payment, every payment, or a filtered subset for an operator
asking "what if event X had never arrived?" — funnels through one pure
function: :meth:`PaymentLedger.rebuild_state`. That function is the only
place that knows how to fold events into a ``PaymentState``; every other
ledger operation is a thin composition on top of it.
"""

from __future__ import annotations

from dataclasses import replace

from .models import (
    Authorized,
    Captured,
    EmptyReplayError,
    PaymentEvent,
    PaymentEventRecord,
    PaymentState,
    Refunded,
    UnknownEventError,
)
from .normalize import Normalizer


class PaymentLedger:
    """Append-only log of canonical payment events."""

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
        return self._events

    def __len__(self) -> int:
        return len(self._events)

    def has_source_id(self, provider: str, source_id: str) -> bool:
        return any(
            entry.provider == provider and entry.source_id == source_id
            for entry in self._events
        )

    def next_seq(self, provider: str, payment_id: str) -> int:
        """Next event sequence number for one payment stream."""
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
    # Replay. Every path here composes on :meth:`rebuild_state`, the only
    # place that folds events into a ``PaymentState``.
    # ------------------------------------------------------------------

    @classmethod
    def rebuild_state(cls, events: list[PaymentEventRecord]) -> PaymentState:
        """Reconstruct one payment state strictly from an event list, in order.

        This is the single source of truth for replay. A caller wanting to
        ask "what if event X had never arrived?" simply filters the event
        list before passing it in and re-invokes this rebuilder.
        """
        if not events:
            raise EmptyReplayError("Cannot replay empty payment event list")
        state = cls._empty_state(events[0])
        for entry in events:
            state = cls._apply_event(state, entry)
        return state

    @classmethod
    def rebuild_states_by_payment(
        cls, events: list[PaymentEventRecord]
    ) -> list[PaymentState]:
        """Group events by payment and rebuild one state per group.

        Composes on :meth:`rebuild_state` — no independent fold here.
        """
        order: list[tuple[str, str]] = []
        buckets: dict[tuple[str, str], list[PaymentEventRecord]] = {}
        for entry in events:
            key = (entry.provider, entry.payment_id)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(entry)
        return [cls.rebuild_state(buckets[key]) for key in order]

    def replay_payment(self, payment_id: str) -> PaymentState:
        """Rebuild one payment state from the stored log."""
        return self.rebuild_state(self.events_for_payment(payment_id))

    def replay_all(self) -> list[PaymentState]:
        return self.rebuild_states_by_payment(self._events)

    def state_for(self, payment_id: str) -> PaymentState:
        return self.replay_payment(payment_id)

    def replay_without(
        self, payment_id: str, skip_source_ids: tuple[str, ...]
    ) -> PaymentState:
        """Replay ``payment_id`` with the given source ids filtered out.

        Exposes the "what if event X had never arrived?" workflow directly:
        filter the event list and feed it back into :meth:`rebuild_state`.
        """
        skip = set(skip_source_ids)
        filtered = [
            entry
            for entry in self.events_for_payment(payment_id)
            if entry.source_id not in skip
        ]
        return self.rebuild_state(filtered)

    # ------------------------------------------------------------------
    # Back-compat aliases for the old replay_* spellings. Both delegate
    # to :meth:`rebuild_state` so the fold lives in exactly one place.
    # ------------------------------------------------------------------

    @classmethod
    def replay_events(cls, events: list[PaymentEventRecord]) -> PaymentState:
        return cls.rebuild_state(events)

    @classmethod
    def replay_events_by_payment(
        cls, events: list[PaymentEventRecord]
    ) -> list[PaymentState]:
        return cls.rebuild_states_by_payment(events)

    # ------------------------------------------------------------------
    # State transitions. Classmethods so they can be unit tested without
    # constructing a full ledger. Only :meth:`rebuild_state` should call
    # these directly — everything else goes through the rebuilder.
    # ------------------------------------------------------------------

    @classmethod
    def _empty_state(cls, entry: PaymentEventRecord) -> PaymentState:
        """Zero-value state seeded from the first event identifiers."""
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
        )

    @classmethod
    def _apply_event(
        cls, state: PaymentState, entry: PaymentEventRecord
    ) -> PaymentState:
        event = entry.event
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
        updated = replace(
            state,
            currency=event.currency,
            authorized_amount=max(state.authorized_amount, event.amount),
            latest_at=event.at,
        )
        return cls._check_currency(updated, source_id, event.currency)

    @classmethod
    def _apply_captured(
        cls, state: PaymentState, source_id: str, event: Captured
    ) -> PaymentState:
        """Capture increases the captured total and flags suspicious totals."""
        updated = cls._check_currency(
            replace(
                state,
                currency=event.currency,
                captured_amount=state.captured_amount + event.amount,
                latest_at=event.at,
            ),
            source_id,
            event.currency,
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
        updated = cls._check_currency(
            replace(
                state,
                currency=event.currency,
                refunded_amount=state.refunded_amount + event.amount,
                latest_at=event.at,
            ),
            source_id,
            event.currency,
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
    def _check_currency(
        cls, state: PaymentState, source_id: str, currency: str
    ) -> PaymentState:
        if state.currency == currency:
            return state
        return state.with_anomaly(
            f"currency-mismatch:{source_id}",
            f"Currency changed inside one payment stream '{state.payment_id}'",
        )


__all__ = ["PaymentLedger"]
