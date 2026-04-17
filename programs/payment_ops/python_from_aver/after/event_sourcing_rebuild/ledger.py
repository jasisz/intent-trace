"""Pure replay and dedupe helpers for the payment ledger.

Backoffice review gets easier when webhook ingestion becomes append-only events
and every current state is rebuilt from those events by one pure rebuilder.

This module is organised around one canonical entry point,
``rebuildPaymentState``, which reconstructs a ``PaymentState`` from scratch
given a list of events in arrival order. Every other ledger operation that
would otherwise want to "patch" a field incrementally is expressed as a
composition on top of the rebuilder so the two paths cannot drift out of sync.

The benefit this unlocks is replay: an operator can ask "what would the state
look like if event X had never arrived?" by filtering the event list and
re-invoking the rebuilder — see ``rebuildPaymentStateWithout`` and
``rebuildPaymentStateWhere``.

Design decisions:

* A single pure ``rebuildPaymentState`` is the only function that turns events
  into state; every other ledger operation, including ``replayPayment`` and
  ``replayAll``, composes on top of it. Derivation and event-application used
  to be interleaved in ``replayPayment``, which made it easy for
  field-updating helpers to drift out of sync with the event log. Keeping one
  canonical rebuilder lets replay experiments like "what if event X had never
  arrived?" become a filter plus one call, and guarantees every code path
  sees the same derived state. Alternatives considered: incremental field
  updates per operation, cached state with patches.
"""

from __future__ import annotations

from collections.abc import Callable
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


# ---------------------------------------------------------------------------
# Canonical rebuilder — the only place payment state is ever constructed.
# ---------------------------------------------------------------------------


def rebuildPaymentState(events: list[PaymentEventRecord]) -> PaymentState:
    """Reconstruct a full payment state from its event log, in order.

    This is the single source of truth for how a ``PaymentState`` is derived
    from a sequence of ``PaymentEventRecord`` values. Every other ledger
    operation that needs a payment state (``replayPayment``, ``replayAll``,
    the "what-if" helpers below) composes on top of this function instead of
    updating ``PaymentState`` fields independently.

    The list is consumed from scratch: no partially-built state leaks in, so
    filtering events and calling the rebuilder again always produces the
    exact state implied by the filtered log.
    """
    if not events:
        raise ValueError("Cannot replay empty payment event list")
    state = _emptyState(events[0])
    for entry in events:
        state = _applyEvent(state, entry)
    return state


def replayPayment(events: list[PaymentEventRecord]) -> PaymentState:
    """Rebuild one payment state from its event stream.

    Kept for call-site stability; all real work flows through
    ``rebuildPaymentState`` so the two entry points cannot disagree.
    """
    return rebuildPaymentState(events)


def replayAll(events: list[PaymentEventRecord]) -> list[PaymentState]:
    """Replay the whole event log into one state per payment.

    Implemented on top of ``rebuildPaymentState``: we group events by
    ``(provider, paymentId)`` in first-seen order and hand each group to the
    rebuilder. This guarantees that every ``PaymentState`` returned here is
    byte-for-byte identical to ``rebuildPaymentState`` called on the same
    filtered sub-log.
    """
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[PaymentEventRecord]] = {}
    for entry in events:
        key = (entry.provider, entry.paymentId)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(entry)
    return [rebuildPaymentState(grouped[key]) for key in order]


# ---------------------------------------------------------------------------
# "What-if" helpers — all phrased as filtering + rebuilding.
# ---------------------------------------------------------------------------


def rebuildPaymentStateWhere(
    events: list[PaymentEventRecord],
    predicate: Callable[[PaymentEventRecord], bool],
) -> PaymentState:
    """Rebuild the state using only the events satisfying ``predicate``.

    Because derivation lives in one function, asking "what would the state
    look like if we kept only events X?" is simply a filtered call to the
    rebuilder — no bespoke replay path is needed.
    """
    return rebuildPaymentState([entry for entry in events if predicate(entry)])


def rebuildPaymentStateWithout(
    events: list[PaymentEventRecord], provider: str, sourceId: str
) -> PaymentState:
    """Rebuild the state as if the given webhook source ID never arrived.

    Operators use this to troubleshoot a specific duplicate or stale event:
    filter it out of the log and let the canonical rebuilder show what the
    state would have been.
    """
    return rebuildPaymentStateWhere(
        events,
        lambda entry: not (entry.provider == provider and entry.sourceId == sourceId),
    )


# ---------------------------------------------------------------------------
# Private application — the only code path that ever advances a PaymentState.
# These helpers exist exclusively as the implementation of the rebuilder;
# no public function updates state through any other path.
# ---------------------------------------------------------------------------


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
    """Apply one canonical event and record suspicious transitions as anomalies.

    Only ever invoked by ``rebuildPaymentState``; callers that want a fresh
    state should go through the rebuilder so the reconstruction path stays
    singular.
    """
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
