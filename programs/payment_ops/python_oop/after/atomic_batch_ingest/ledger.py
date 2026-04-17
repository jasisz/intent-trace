"""Append-only event ledger with replay helpers.

Backoffice review gets easier when webhook ingestion becomes append-only events
and every current state is rebuilt from those events in plain code. The
``PaymentLedger`` owns the event log and the replay entry points; it mutates
its internal list on ingest but returns fresh frozen ``PaymentState`` values
from replay.
"""

from __future__ import annotations

from dataclasses import replace

from models import (
    Authorized,
    BatchError,
    Captured,
    EmptyReplayError,
    PaymentEvent,
    PaymentEventRecord,
    PaymentOpsError,
    PaymentState,
    RawWebhook,
    Refunded,
    UnknownEventError,
)
from normalize import Normalizer


class PaymentLedger:
    """Append-only log of canonical payment events.

    The ledger is the single source of truth for what happened to a payment.
    Ingestion is idempotent by (provider, source_id) so retrying a webhook
    does not corrupt the log. Bursty webhook traffic is applied through
    :meth:`apply_batch`, which commits all events or none of them so the
    log never ends up half-updated.
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

    def ingest_webhook(self, provider: str, raw: RawWebhook) -> PaymentEventRecord:
        """Normalize and append one provider webhook as a single-element batch.

        Exists so single-webhook callers don't need to build a list of one;
        the heavy lifting (normalize + dedupe + append) lives in
        :meth:`apply_batch`, which keeps the one- and many-event paths
        identical.
        """
        applied = self.apply_batch([(provider, raw)])
        return applied[0]

    def apply_batch(
        self, ops: list[tuple[str, RawWebhook]]
    ) -> list[PaymentEventRecord]:
        """Atomically normalize and append a batch of provider webhooks.

        Implements the working-copy / snapshot pattern: the event list is
        cloned, each op is normalized and appended onto the clone, and only
        when every op succeeds is the clone swapped in as the new
        authoritative log. If any op blows up the clone is discarded and
        :class:`BatchError` is raised with the failing op's index so callers
        can locate the bad webhook in their input list.

        Duplicate webhooks within the batch (matching an already-ingested
        ``(provider, source_id)`` pair, or colliding with an earlier op in
        the same batch) are skipped rather than re-appended, mirroring the
        existing idempotency guarantees of the single-webhook path.
        """
        snapshot = list(self._events)
        working = list(self._events)
        applied: list[PaymentEventRecord] = []
        for index, (provider, raw) in enumerate(ops):
            try:
                record = self._stage_one(working, provider, raw)
            except PaymentOpsError as exc:
                # Roll back to the untouched snapshot before reporting.
                self._events = snapshot
                raise BatchError(index, exc) from exc
            applied.append(record)
        self._events = working
        return applied

    def _stage_one(
        self,
        working: list[PaymentEventRecord],
        provider: str,
        raw: RawWebhook,
    ) -> PaymentEventRecord:
        """Normalize one webhook against a working copy of the event log.

        Lives here (rather than inline) so the batch loop stays readable and
        the dedup/next-seq scan is performed against the in-progress working
        copy, not ``self._events`` — otherwise two webhooks for the same
        payment in one batch would both be assigned ``seq=1``.
        """
        canonical_provider = self._normalizer.canonical_provider(provider)
        for entry in working:
            if (
                entry.provider == canonical_provider
                and entry.source_id == raw.source_id
            ):
                return entry
        seq = self._next_seq_in(working, canonical_provider, raw.payment_id)
        record = self._normalizer.webhook(canonical_provider, seq, raw)
        working.append(record)
        return record

    @staticmethod
    def _next_seq_in(
        events: list[PaymentEventRecord], provider: str, payment_id: str
    ) -> int:
        current = 1
        for entry in events:
            if entry.provider == provider and entry.payment_id == payment_id:
                current = entry.seq + 1
        return current

    # ------------------------------------------------------------------
    # Replay.
    # ------------------------------------------------------------------

    def replay_payment(self, payment_id: str) -> PaymentState:
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
    # Replay without ledger context.
    # ------------------------------------------------------------------

    @classmethod
    def replay_events(cls, events: list[PaymentEventRecord]) -> PaymentState:
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
    # State transitions.
    # ------------------------------------------------------------------

    @classmethod
    def _empty_state(cls, entry: PaymentEventRecord) -> PaymentState:
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
        """Apply one canonical event and record suspicious transitions."""
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
