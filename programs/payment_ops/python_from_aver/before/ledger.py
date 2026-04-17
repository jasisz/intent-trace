"""Pure replay and dedupe helpers for the payment ledger.

Backoffice review gets easier when webhook ingestion becomes append-only events
and every current state is rebuilt from those events in plain code.

Design decisions:

* Payment state is never mutated in place; every current state is derived by
  replaying the append-only canonical event log. Dirty payment integrations are
  easiest to audit when the stored history stays immutable, and replaying
  canonical events keeps dedupe and reconciliation readable instead of hiding
  rules in writes. Alternatives considered: mutable payment rows, ad-hoc status
  overrides.
"""

from __future__ import annotations

from dataclasses import replace

from .models import (
    Authorized,
    Captured,
    PaymentEvent,
    PaymentEventRecord,
    PaymentState,
    Refunded,
)


def eventName(event: PaymentEvent) -> str:
    """Stable display name for one canonical event."""
    if isinstance(event, Authorized):
        return "Authorized"
    if isinstance(event, Captured):
        return "Captured"
    if isinstance(event, Refunded):
        return "Refunded"
    raise ValueError(f"Unknown event: {event!r}")


def eventAt(event: PaymentEvent) -> str:
    """Timestamp carried by a canonical event."""
    if isinstance(event, (Authorized, Captured, Refunded)):
        return event.at
    raise ValueError(f"Unknown event: {event!r}")


def eventCurrency(event: PaymentEvent) -> str:
    """Currency carried by a canonical event."""
    if isinstance(event, (Authorized, Captured, Refunded)):
        return event.currency
    raise ValueError(f"Unknown event: {event!r}")


def eventAmount(event: PaymentEvent) -> int:
    """Amount carried by a canonical event."""
    if isinstance(event, (Authorized, Captured, Refunded)):
        return event.amount
    raise ValueError(f"Unknown event: {event!r}")


def hasSourceId(events: list[PaymentEventRecord], provider: str, sourceId: str) -> bool:
    """True when a webhook source ID was already persisted for a provider."""
    for entry in events:
        if entry.provider == provider and entry.sourceId == sourceId:
            return True
    return False


def nextSeq(events: list[PaymentEventRecord], provider: str, paymentId: str) -> int:
    """Next event sequence number for one payment stream."""
    current = 1
    for entry in events:
        if entry.provider == provider and entry.paymentId == paymentId:
            current = entry.seq + 1
    return current


def eventsForPayment(
    events: list[PaymentEventRecord], paymentId: str
) -> list[PaymentEventRecord]:
    """Filter the append-only log down to one payment stream."""
    return [entry for entry in events if entry.paymentId == paymentId]


def replayPayment(events: list[PaymentEventRecord]) -> PaymentState:
    """Rebuild one payment state from its event stream."""
    if not events:
        raise ValueError("Cannot replay empty payment event list")
    state = _applyEvent(_emptyState(events[0]), events[0])
    for entry in events[1:]:
        state = _applyEvent(state, entry)
    return state


def replayAll(events: list[PaymentEventRecord]) -> list[PaymentState]:
    """Replay the whole event log into one state per payment."""
    # Aver prepends new states then reverses at the end, so effective order is
    # first-seen. We achieve that directly with append.
    states: list[PaymentState] = []
    for entry in events:
        states = _upsertState(states, entry)
    return states


def _upsertState(
    states: list[PaymentState], entry: PaymentEventRecord
) -> list[PaymentState]:
    """Apply one event to the matching state or create a new one."""
    existing = _findState(states, entry.provider, entry.paymentId)
    if existing is None:
        return [*states, _applyEvent(_emptyState(entry), entry)]
    return _replaceState(states, _applyEvent(existing, entry))


def _findState(
    states: list[PaymentState], provider: str, paymentId: str
) -> PaymentState | None:
    """Look up one payment state inside the replay accumulator."""
    for state in states:
        if state.provider == provider and state.paymentId == paymentId:
            return state
    return None


def _replaceState(
    states: list[PaymentState], updated: PaymentState
) -> list[PaymentState]:
    """Replace one payment state in the replay accumulator, preserving order."""
    return [
        updated
        if state.provider == updated.provider and state.paymentId == updated.paymentId
        else state
        for state in states
    ]


def _emptyState(entry: PaymentEventRecord) -> PaymentState:
    """Zero-value state seeded from the first event identifiers."""
    return PaymentState(
        paymentId=entry.paymentId,
        provider=entry.provider,
        currency=eventCurrency(entry.event),
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=0,
        latestAt=eventAt(entry.event),
        anomalyKeys=(),
        anomalyNotes=(),
    )


def _applyEvent(state: PaymentState, entry: PaymentEventRecord) -> PaymentState:
    """Apply one canonical event and record suspicious transitions as anomalies."""
    event = entry.event
    if isinstance(event, Authorized):
        return _applyAuthorized(
            state, entry.sourceId, event.amount, event.currency, event.at
        )
    if isinstance(event, Captured):
        return _applyCaptured(
            state, entry.sourceId, event.amount, event.currency, event.at
        )
    if isinstance(event, Refunded):
        return _applyRefunded(
            state, entry.sourceId, event.amount, event.currency, event.at
        )
    raise ValueError(f"Unknown event: {event!r}")


def _applyAuthorized(
    state: PaymentState, sourceId: str, amount: int, currency: str, at: str
) -> PaymentState:
    """Authorization stores the highest authorized amount seen so far."""
    nxt = replace(
        state,
        currency=currency,
        authorizedAmount=max(state.authorizedAmount, amount),
        latestAt=at,
    )
    return _withCurrencyCheck(nxt, sourceId, currency)


def _applyCaptured(
    state: PaymentState, sourceId: str, amount: int, currency: str, at: str
) -> PaymentState:
    """Capture increases the captured total and flags suspicious order or totals."""
    base = _withCurrencyCheck(
        replace(
            state,
            currency=currency,
            capturedAmount=state.capturedAmount + amount,
            latestAt=at,
        ),
        sourceId,
        currency,
    )
    if state.authorizedAmount == 0:
        missingAuth = _addAnomaly(
            base,
            f"capture-without-authorize:{sourceId}",
            f"Capture arrived before any authorization for payment '{state.paymentId}'",
        )
    else:
        missingAuth = base
    if (
        missingAuth.authorizedAmount > 0
        and missingAuth.capturedAmount > missingAuth.authorizedAmount
    ):
        return _addAnomaly(
            missingAuth,
            f"captured-exceeds-authorized:{sourceId}",
            f"Captured amount exceeds authorized amount for payment '{state.paymentId}'",
        )
    return missingAuth


def _applyRefunded(
    state: PaymentState, sourceId: str, amount: int, currency: str, at: str
) -> PaymentState:
    """Refund increases the refunded total and flags order or total mismatches."""
    base = _withCurrencyCheck(
        replace(
            state,
            currency=currency,
            refundedAmount=state.refundedAmount + amount,
            latestAt=at,
        ),
        sourceId,
        currency,
    )
    if state.capturedAmount == 0:
        missingCapture = _addAnomaly(
            base,
            f"refund-before-capture:{sourceId}",
            f"Refund arrived before any capture for payment '{state.paymentId}'",
        )
    else:
        missingCapture = base
    if missingCapture.refundedAmount > missingCapture.capturedAmount:
        return _addAnomaly(
            missingCapture,
            f"refund-exceeds-capture:{sourceId}",
            f"Refunded amount exceeds captured amount for payment '{state.paymentId}'",
        )
    return missingCapture


def _withCurrencyCheck(
    state: PaymentState, sourceId: str, currency: str
) -> PaymentState:
    """Flag a currency switch after the first event."""
    if state.currency == currency:
        return state
    return _addAnomaly(
        state,
        f"currency-mismatch:{sourceId}",
        f"Currency changed inside one payment stream '{state.paymentId}'",
    )


def _addAnomaly(state: PaymentState, key: str, note: str) -> PaymentState:
    """Append a new anomaly marker in replay order."""
    return replace(
        state,
        anomalyKeys=state.anomalyKeys + (key,),
        anomalyNotes=state.anomalyNotes + (note,),
    )
