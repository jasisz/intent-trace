"""Entry point with smoke tests that exercise the OOP payment_ops port."""

from __future__ import annotations

import cases
import ledger
import models
import normalize
import reconcile
import views
from cases import CaseDraftFactory, CaseRegistry
from ledger import PaymentLedger
from models import (
    AuditEntry,
    Authorized,
    BatchError,
    CaseDraft,
    CaseStatus,
    Captured,
    EmptyReplayError,
    PaymentEventRecord,
    PaymentOpsError,
    PaymentState,
    ProviderSummary,
    RawSettlement,
    RawWebhook,
    Refunded,
    ReviewCase,
    SettlementKind,
    SettlementRow,
    UnknownCaseError,
    UnknownCaseFilterError,
    UnknownCaseStatusError,
    UnknownProviderError,
    UnsupportedProviderError,
    UnsupportedSettlementKindError,
    UnsupportedWebhookKindError,
)
from normalize import AdyenNormalizer, Normalizer, StripeNormalizer
from reconcile import BothMissing, Exact, Mismatch, RealtimeOnly, Reconciler, SettledOnly
from views import PaymentView, ProviderReporter


# ---------------------------------------------------------------------------
# Reusable fixtures for the smoke tests.
# ---------------------------------------------------------------------------


def sample_authorized() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        source_id="evt-1",
        payment_id="pay-1",
        seq=1,
        event=Authorized(1000, "USD", "2026-03-10T09:00:00Z"),
    )


def sample_captured() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        source_id="evt-2",
        payment_id="pay-1",
        seq=2,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
    )


def sample_refund_before_capture() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        source_id="evt-9",
        payment_id="pay-2",
        seq=1,
        event=Refunded(400, "USD", "2026-03-10T11:00:00Z"),
    )


def sample_other_payment() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        source_id="evt-7",
        payment_id="pay-2",
        seq=1,
        event=Authorized(700, "EUR", "2026-03-10T12:00:00Z"),
    )


def sample_state() -> PaymentState:
    return PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=1000,
        captured_amount=1000,
        refunded_amount=0,
        latest_at="2026-03-10T09:05:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )


def sample_capture_row() -> SettlementRow:
    return SettlementRow(
        provider="stripe",
        row_id="row-1",
        payment_id="pay-1",
        kind=SettlementKind.Capture,
        amount=1000,
        currency="USD",
        settled_on="2026-03-10",
    )


def sample_refund_row() -> SettlementRow:
    return SettlementRow(
        provider="stripe",
        row_id="row-2",
        payment_id="pay-1",
        kind=SettlementKind.Refund,
        amount=200,
        currency="USD",
        settled_on="2026-03-10",
    )


def sample_case() -> ReviewCase:
    return ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        payment_id="pay-1",
        kind="x",
        detail="d",
        status=CaseStatus.Open,
        created_at="2026-03-10T12:00:00Z",
        resolved_at=None,
        resolution=None,
    )


def sample_event() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        source_id="evt-1",
        payment_id="pay-1",
        seq=1,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
    )


# ---------------------------------------------------------------------------
# Helpers for error-case expectations.
# ---------------------------------------------------------------------------


def _expect_error(fn, expected: type[Exception], message: str) -> None:
    try:
        fn()
    except expected as exc:
        assert str(exc) == message, f"expected {message!r}, got {exc!r}"
        return
    raise AssertionError(
        f"expected {expected.__name__}({message!r}) but no error was raised"
    )


# ---------------------------------------------------------------------------
# Smoke tests.
# ---------------------------------------------------------------------------


def _test_normalize() -> None:
    normalizer = Normalizer()

    assert normalizer.webhook(
        "stripe",
        1,
        RawWebhook("evt-1", "pay-1", "charge.captured", 1200, "USD", "2026-03-10T10:00:00Z"),
    ) == PaymentEventRecord(
        "stripe", "evt-1", "pay-1", 1, Captured(1200, "USD", "2026-03-10T10:00:00Z")
    )
    assert normalizer.webhook(
        "adyen",
        2,
        RawWebhook("psp-2", "pay-2", "REFUND", 500, "EUR", "2026-03-10T11:00:00Z"),
    ) == PaymentEventRecord(
        "adyen", "psp-2", "pay-2", 2, Refunded(500, "EUR", "2026-03-10T11:00:00Z")
    )
    _expect_error(
        lambda: normalizer.webhook(
            "unknown",
            1,
            RawWebhook("evt-9", "pay-9", "charge.captured", 100, "USD", "2026-03-10T10:00:00Z"),
        ),
        UnknownProviderError,
        "Provider must be one of: stripe, adyen",
    )

    assert normalizer.settlement(
        "stripe", RawSettlement("row-1", "pay-1", "charge", 1200, "USD", "2026-03-10")
    ) == SettlementRow(
        "stripe", "row-1", "pay-1", SettlementKind.Capture, 1200, "USD", "2026-03-10"
    )
    assert normalizer.settlement(
        "adyen", RawSettlement("row-2", "pay-2", "Refunded", 500, "EUR", "2026-03-10")
    ) == SettlementRow(
        "adyen", "row-2", "pay-2", SettlementKind.Refund, 500, "EUR", "2026-03-10"
    )
    _expect_error(
        lambda: normalizer.settlement(
            "unknown",
            RawSettlement("row-9", "pay-9", "charge", 100, "USD", "2026-03-10"),
        ),
        UnknownProviderError,
        "Provider must be one of: stripe, adyen",
    )

    assert normalizer.canonical_provider("stripe") == "stripe"
    assert normalizer.canonical_provider("Adyen") == "adyen"
    _expect_error(
        lambda: normalizer.canonical_provider("unknown"),
        UnknownProviderError,
        "Provider must be one of: stripe, adyen",
    )

    assert normalizer.webhook_event(
        "stripe",
        RawWebhook(
            "evt-1",
            "pay-1",
            "payment_intent.amount_capturable_updated",
            1000,
            "USD",
            "2026-03-10T09:00:00Z",
        ),
    ) == Authorized(1000, "USD", "2026-03-10T09:00:00Z")
    assert normalizer.webhook_event(
        "adyen",
        RawWebhook("psp-1", "pay-1", "CAPTURE", 1000, "USD", "2026-03-10T09:05:00Z"),
    ) == Captured(1000, "USD", "2026-03-10T09:05:00Z")
    _expect_error(
        lambda: normalizer.webhook_event(
            "worldpay",
            RawWebhook("evt-1", "pay-1", "CAPTURE", 1000, "USD", "2026-03-10T09:05:00Z"),
        ),
        UnsupportedProviderError,
        "Unsupported provider: worldpay",
    )

    stripe = StripeNormalizer()
    assert stripe.webhook_event(
        RawWebhook("evt-1", "pay-1", "charge.refunded", 300, "USD", "2026-03-10T10:30:00Z")
    ) == Refunded(300, "USD", "2026-03-10T10:30:00Z")
    _expect_error(
        lambda: stripe.webhook_event(
            RawWebhook("evt-1", "pay-1", "weird", 300, "USD", "2026-03-10T10:30:00Z")
        ),
        UnsupportedWebhookKindError,
        "Unsupported stripe webhook kind: weird",
    )

    adyen = AdyenNormalizer()
    assert adyen.webhook_event(
        RawWebhook("psp-1", "pay-1", "AUTHORISATION", 900, "EUR", "2026-03-10T08:00:00Z")
    ) == Authorized(900, "EUR", "2026-03-10T08:00:00Z")
    _expect_error(
        lambda: adyen.webhook_event(
            RawWebhook("psp-1", "pay-1", "BOOM", 900, "EUR", "2026-03-10T08:00:00Z")
        ),
        UnsupportedWebhookKindError,
        "Unsupported adyen webhook kind: BOOM",
    )

    assert normalizer.settlement_kind("stripe", "charge") == SettlementKind.Capture
    assert normalizer.settlement_kind("adyen", "Refunded") == SettlementKind.Refund
    _expect_error(
        lambda: normalizer.settlement_kind("worldpay", "charge"),
        UnsupportedProviderError,
        "Unsupported provider: worldpay",
    )

    assert stripe.settlement_kind("charge") == SettlementKind.Capture
    _expect_error(
        lambda: stripe.settlement_kind("other"),
        UnsupportedSettlementKindError,
        "Unsupported stripe settlement kind: other",
    )

    assert adyen.settlement_kind("Settled") == SettlementKind.Capture
    _expect_error(
        lambda: adyen.settlement_kind("other"),
        UnsupportedSettlementKindError,
        "Unsupported adyen settlement kind: other",
    )


def _test_cases() -> None:
    assert CaseDraftFactory.make(
        "stripe", "pay-1", "refund_before_capture", "Refund arrived first", "k"
    ) == CaseDraft("k", "stripe", "pay-1", "refund_before_capture", "Refund arrived first")

    state_with_anomaly = PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=100,
        latest_at="2026-03-10T10:00:00Z",
        anomaly_keys=("refund-before-capture:evt-1",),
        anomaly_notes=("Refund arrived before any capture for payment 'pay-1'",),
    )
    assert CaseDraftFactory.drafts_from_state(state_with_anomaly) == [
        CaseDraft(
            "refund-before-capture:evt-1",
            "stripe",
            "pay-1",
            "refund_before_capture",
            "Refund arrived before any capture for payment 'pay-1'",
        )
    ]

    assert CaseDraftFactory.drafts_from_states([state_with_anomaly]) == [
        CaseDraft(
            "refund-before-capture:evt-1",
            "stripe",
            "pay-1",
            "refund_before_capture",
            "Refund arrived before any capture for payment 'pay-1'",
        )
    ]

    assert CaseDraftFactory.kind_from_key("refund-before-capture:evt-1") == "refund_before_capture"
    assert CaseDraftFactory.kind_from_key("x") == "payment_integrity"

    assert CaseStatus.Open.label == "open"
    assert CaseStatus.Resolved.label == "resolved"

    assert CaseStatus.parse("open") is CaseStatus.Open
    assert CaseStatus.parse("resolved") is CaseStatus.Resolved
    _expect_error(
        lambda: CaseStatus.parse("x"),
        UnknownCaseStatusError,
        "Case status must be one of: open, resolved",
    )

    draft = CaseDraftFactory.make(
        "stripe", "pay-1", "refund_before_capture", "Refund arrived first", "k"
    )
    registry = CaseRegistry()
    opened = registry.open_cases([draft], "2026-03-10T12:00:00Z")
    expected_case = ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        payment_id="pay-1",
        kind="refund_before_capture",
        detail="Refund arrived first",
        status=CaseStatus.Open,
        created_at="2026-03-10T12:00:00Z",
        resolved_at=None,
        resolution=None,
    )
    assert opened == [expected_case]
    assert registry.open_cases([draft], "2026-03-10T12:30:00Z") == []
    assert registry.cases == [expected_case]

    registry.resolve("case-1", "duplicate webhook", "2026-03-10T13:00:00Z")
    assert registry.cases == [
        ReviewCase(
            id="case-1",
            key="k",
            provider="stripe",
            payment_id="pay-1",
            kind="refund_before_capture",
            detail="Refund arrived first",
            status=CaseStatus.Resolved,
            created_at="2026-03-10T12:00:00Z",
            resolved_at="2026-03-10T13:00:00Z",
            resolution="duplicate webhook",
        )
    ]
    _expect_error(
        lambda: CaseRegistry().resolve("case-1", "x", "2026-03-10T13:00:00Z"),
        UnknownCaseError,
        "Unknown case: case-1",
    )

    assert CaseRegistry().filter(None) == []
    assert CaseRegistry([sample_case()]).filter("open") == [sample_case()]
    _expect_error(
        lambda: CaseRegistry().filter("weird"),
        UnknownCaseFilterError,
        "Case filter must be one of: open, resolved, all",
    )

    assert CaseRegistry([sample_case()]).open_for_payment("pay-1") == [sample_case()]

    assert CaseRegistry([sample_case()]).find_by_id("case-1") == sample_case()
    assert CaseRegistry().find_by_id("case-1") is None

    assert AuditEntry.for_opened_case(sample_case()) == AuditEntry(
        key="case-opened:k",
        subject_id="payment:pay-1",
        action="case.opened",
        message="[x] d",
        created_at="2026-03-10T12:00:00Z",
    )

    resolved_case = ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        payment_id="pay-1",
        kind="x",
        detail="d",
        status=CaseStatus.Resolved,
        created_at="2026-03-10T12:00:00Z",
        resolved_at="2026-03-10T13:00:00Z",
        resolution="duplicate",
    )
    assert AuditEntry.for_resolved_case(resolved_case) == AuditEntry(
        key="case-resolved:k",
        subject_id="payment:pay-1",
        action="case.resolved",
        message="duplicate",
        created_at="2026-03-10T13:00:00Z",
    )


def _test_ledger() -> None:
    assert Authorized(100, "USD", "2026-03-10T09:00:00Z").name == "Authorized"
    assert Captured(100, "USD", "2026-03-10T10:00:00Z").name == "Captured"
    assert Refunded(50, "USD", "2026-03-10T10:00:00Z").name == "Refunded"

    auth = Authorized(100, "USD", "2026-03-10T09:00:00Z")
    assert auth.at == "2026-03-10T09:00:00Z"
    assert auth.currency == "USD"
    assert auth.amount == 100

    cap = Captured(1200, "GBP", "2026-03-10T10:00:00Z")
    assert cap.at == "2026-03-10T10:00:00Z"
    assert cap.currency == "GBP"
    assert cap.amount == 1200

    ref = Refunded(200, "EUR", "2026-03-10T11:00:00Z")
    assert ref.at == "2026-03-10T11:00:00Z"
    assert ref.currency == "EUR"
    assert ref.amount == 200

    book = PaymentLedger([sample_authorized(), sample_captured()])
    assert book.has_source_id("stripe", "evt-1") is True
    assert book.has_source_id("stripe", "missing") is False

    assert PaymentLedger().next_seq("stripe", "pay-1") == 1
    assert book.next_seq("stripe", "pay-1") == 3
    assert book.next_seq("stripe", "pay-2") == 1

    assert PaymentLedger(
        [sample_authorized(), sample_captured(), sample_other_payment()]
    ).events_for_payment("pay-1") == [sample_authorized(), sample_captured()]

    assert PaymentLedger.replay_events(
        [sample_authorized(), sample_captured()]
    ) == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=1000,
        captured_amount=1000,
        refunded_amount=0,
        latest_at="2026-03-10T09:05:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )
    assert PaymentLedger.replay_events([sample_refund_before_capture()]) == PaymentState(
        payment_id="pay-2",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=400,
        latest_at="2026-03-10T11:00:00Z",
        anomaly_keys=(
            "refund-before-capture:evt-9",
            "refund-exceeds-capture:evt-9",
        ),
        anomaly_notes=(
            "Refund arrived before any capture for payment 'pay-2'",
            "Refunded amount exceeds captured amount for payment 'pay-2'",
        ),
    )
    _expect_error(
        lambda: PaymentLedger.replay_events([]),
        EmptyReplayError,
        "Cannot replay empty payment event list",
    )

    assert PaymentLedger(
        [sample_authorized(), sample_captured()]
    ).replay_payment("pay-1") == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=1000,
        captured_amount=1000,
        refunded_amount=0,
        latest_at="2026-03-10T09:05:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )

    multi = PaymentLedger(
        [sample_authorized(), sample_captured(), sample_other_payment()]
    )
    assert multi.replay_all() == [
        PaymentState(
            payment_id="pay-1",
            provider="stripe",
            currency="USD",
            authorized_amount=1000,
            captured_amount=1000,
            refunded_amount=0,
            latest_at="2026-03-10T09:05:00Z",
            anomaly_keys=(),
            anomaly_notes=(),
        ),
        PaymentState(
            payment_id="pay-2",
            provider="stripe",
            currency="EUR",
            authorized_amount=700,
            captured_amount=0,
            refunded_amount=0,
            latest_at="2026-03-10T12:00:00Z",
            anomaly_keys=(),
            anomaly_notes=(),
        ),
    ]

    empty = PaymentLedger._empty_state(sample_authorized())
    assert empty == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=0,
        latest_at="2026-03-10T09:00:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )

    assert PaymentLedger._apply_event(empty, sample_authorized()) == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=1000,
        captured_amount=0,
        refunded_amount=0,
        latest_at="2026-03-10T09:00:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )

    captured_event = PaymentEventRecord(
        provider="stripe",
        source_id="evt-2",
        payment_id="pay-1",
        seq=1,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
    )
    assert PaymentLedger._apply_event(empty, captured_event) == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=1000,
        refunded_amount=0,
        latest_at="2026-03-10T09:05:00Z",
        anomaly_keys=("capture-without-authorize:evt-2",),
        anomaly_notes=("Capture arrived before any authorization for payment 'pay-1'",),
    )

    refunded_event = PaymentEventRecord(
        provider="stripe",
        source_id="evt-3",
        payment_id="pay-1",
        seq=1,
        event=Refunded(200, "USD", "2026-03-10T10:00:00Z"),
    )
    assert PaymentLedger._apply_event(empty, refunded_event) == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=200,
        latest_at="2026-03-10T10:00:00Z",
        anomaly_keys=(
            "refund-before-capture:evt-3",
            "refund-exceeds-capture:evt-3",
        ),
        anomaly_notes=(
            "Refund arrived before any capture for payment 'pay-1'",
            "Refunded amount exceeds captured amount for payment 'pay-1'",
        ),
    )

    assert PaymentLedger._check_currency(empty, "evt-9", "USD") == empty

    assert empty.with_anomaly("k", "n") == PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=0,
        latest_at="2026-03-10T09:00:00Z",
        anomaly_keys=("k",),
        anomaly_notes=("n",),
    )

    live = PaymentLedger()
    first = live.ingest_webhook(
        "stripe",
        RawWebhook("evt-1", "pay-1", "charge.captured", 500, "USD", "2026-03-10T09:05:00Z"),
    )
    assert len(live) == 1
    second = live.ingest_webhook(
        "stripe",
        RawWebhook("evt-1", "pay-1", "charge.captured", 500, "USD", "2026-03-10T09:05:00Z"),
    )
    assert len(live) == 1
    assert first == second


# ---------------------------------------------------------------------------
# Atomic-batch smoke tests for the new change request.
# ---------------------------------------------------------------------------


def _burst_of_webhooks() -> list[tuple[str, RawWebhook]]:
    """A short burst of well-formed webhooks for one payment."""
    return [
        (
            "stripe",
            RawWebhook(
                "evt-10",
                "pay-10",
                "payment_intent.amount_capturable_updated",
                1000,
                "USD",
                "2026-03-10T09:00:00Z",
            ),
        ),
        (
            "stripe",
            RawWebhook(
                "evt-11",
                "pay-10",
                "charge.captured",
                1000,
                "USD",
                "2026-03-10T09:05:00Z",
            ),
        ),
        (
            "stripe",
            RawWebhook(
                "evt-12",
                "pay-10",
                "charge.refunded",
                400,
                "USD",
                "2026-03-10T10:00:00Z",
            ),
        ),
    ]


def _test_atomic_batch_ingest() -> None:
    # Happy path: every op in a multi-webhook batch is applied and the
    # returned records reflect assigned sequence numbers and canonical
    # providers.
    book = PaymentLedger()
    applied = book.apply_batch(_burst_of_webhooks())
    assert len(book) == 3
    assert [rec.source_id for rec in applied] == ["evt-10", "evt-11", "evt-12"]
    assert [rec.seq for rec in applied] == [1, 2, 3]
    assert all(rec.provider == "stripe" for rec in applied)

    # After a clean burst the payment replays to the obvious totals with
    # no anomalies — so downstream code treats it like one-at-a-time ingest.
    state = book.state_for("pay-10")
    assert state.authorized_amount == 1000
    assert state.captured_amount == 1000
    assert state.refunded_amount == 400
    assert state.anomaly_keys == ()

    # Partial failure path: the 3rd op is malformed. The ledger must be
    # restored to its pre-batch snapshot and BatchError must point at the
    # offending index.
    prior = PaymentLedger()
    prior.ingest_webhook(
        "stripe",
        RawWebhook("evt-0", "pay-9", "charge.captured", 200, "USD", "2026-03-10T08:00:00Z"),
    )
    pre_batch_events = list(prior.events)
    bad_batch: list[tuple[str, RawWebhook]] = [
        (
            "stripe",
            RawWebhook(
                "evt-20",
                "pay-11",
                "payment_intent.amount_capturable_updated",
                500,
                "USD",
                "2026-03-10T09:00:00Z",
            ),
        ),
        (
            "stripe",
            RawWebhook(
                "evt-21",
                "pay-11",
                "charge.captured",
                500,
                "USD",
                "2026-03-10T09:05:00Z",
            ),
        ),
        (
            "stripe",
            RawWebhook(
                "evt-22",
                "pay-11",
                "not_a_real_event_kind",
                123,
                "USD",
                "2026-03-10T09:10:00Z",
            ),
        ),
    ]
    raised: BatchError | None = None
    try:
        prior.apply_batch(bad_batch)
    except BatchError as exc:
        raised = exc
    assert raised is not None, "expected BatchError for malformed op"
    assert raised.failing_op_index == 2
    assert isinstance(raised.reason, UnsupportedWebhookKindError)
    # Rollback: ledger is exactly what it was before the batch started.
    assert prior.events == pre_batch_events
    # BatchError is catchable as the domain base class.
    assert isinstance(raised, PaymentOpsError)

    # Unknown provider failing on op 0 rolls the whole batch back.
    fresh = PaymentLedger()
    _expect_batch_error(
        fresh,
        [
            (
                "worldpay",
                RawWebhook(
                    "evt-30",
                    "pay-12",
                    "CAPTURE",
                    100,
                    "USD",
                    "2026-03-10T09:00:00Z",
                ),
            ),
        ],
        0,
    )
    assert fresh.events == []

    # Single-webhook ingest goes through the batch path: the old public
    # method is preserved and still dedupes by (provider, source_id).
    solo = PaymentLedger()
    record = solo.ingest_webhook(
        "stripe",
        RawWebhook("evt-40", "pay-40", "charge.captured", 100, "USD", "2026-03-10T09:00:00Z"),
    )
    assert len(solo) == 1
    assert record.seq == 1
    again = solo.ingest_webhook(
        "stripe",
        RawWebhook("evt-40", "pay-40", "charge.captured", 100, "USD", "2026-03-10T09:00:00Z"),
    )
    assert len(solo) == 1
    assert again == record

    # A duplicate ``source_id`` inside one batch must not be double-appended
    # and must not assign two ``seq=1`` records to the same payment.
    dedupe = PaymentLedger()
    dedupe_applied = dedupe.apply_batch(
        [
            (
                "stripe",
                RawWebhook(
                    "evt-50",
                    "pay-50",
                    "payment_intent.amount_capturable_updated",
                    1000,
                    "USD",
                    "2026-03-10T09:00:00Z",
                ),
            ),
            (
                "stripe",
                RawWebhook(
                    "evt-50",
                    "pay-50",
                    "payment_intent.amount_capturable_updated",
                    1000,
                    "USD",
                    "2026-03-10T09:00:00Z",
                ),
            ),
            (
                "stripe",
                RawWebhook(
                    "evt-51",
                    "pay-50",
                    "charge.captured",
                    1000,
                    "USD",
                    "2026-03-10T09:05:00Z",
                ),
            ),
        ]
    )
    assert len(dedupe) == 2
    assert [rec.source_id for rec in dedupe_applied] == ["evt-50", "evt-50", "evt-51"]
    assert [rec.seq for rec in dedupe_applied] == [1, 1, 2]

    # Empty batch is a no-op that returns an empty applied list.
    empty = PaymentLedger()
    assert empty.apply_batch([]) == []
    assert len(empty) == 0

    # Case-opening behavior is preserved: anomalies surfaced by a
    # successfully-applied batch still feed CaseRegistry. The operator
    # sees the batch commit cleanly and the anomaly case turn up in
    # manual review afterwards.
    review = PaymentLedger()
    review.apply_batch(
        [
            (
                "stripe",
                RawWebhook(
                    "evt-60",
                    "pay-60",
                    "charge.refunded",
                    300,
                    "USD",
                    "2026-03-10T10:00:00Z",
                ),
            ),
        ]
    )
    review_state = review.state_for("pay-60")
    assert "refund-before-capture:evt-60" in review_state.anomaly_keys
    drafts = CaseDraftFactory.drafts_from_state(review_state)
    registry = CaseRegistry()
    opened = registry.open_cases(drafts, "2026-03-10T10:00:05Z")
    assert [case.kind for case in opened] == [
        "refund_before_capture",
        "refund_exceeds_capture",
    ]


def _expect_batch_error(
    book: PaymentLedger,
    ops: list[tuple[str, RawWebhook]],
    expected_index: int,
) -> None:
    try:
        book.apply_batch(ops)
    except BatchError as exc:
        assert exc.failing_op_index == expected_index, (
            f"expected failing_op_index={expected_index}, got {exc.failing_op_index}"
        )
        return
    raise AssertionError("expected BatchError but no error was raised")


def _test_reconcile() -> None:
    reconciler = Reconciler()

    assert reconciler.reconcile_provider(
        "stripe", [sample_state()], [sample_capture_row()]
    ) == []

    mismatched_row = SettlementRow(
        provider="stripe",
        row_id="row-1",
        payment_id="pay-1",
        kind=SettlementKind.Capture,
        amount=900,
        currency="USD",
        settled_on="2026-03-10",
    )
    assert reconciler.reconcile_provider(
        "stripe", [sample_state()], [mismatched_row]
    ) == [
        CaseDraft(
            "reconcile:capture-mismatch:stripe:pay-1:1000:900",
            "stripe",
            "pay-1",
            "settlement_capture_mismatch",
            "Realtime captured 1000 but settlement shows 900",
        )
    ]

    orphan_row = SettlementRow(
        provider="stripe",
        row_id="row-x",
        payment_id="pay-9",
        kind=SettlementKind.Capture,
        amount=400,
        currency="USD",
        settled_on="2026-03-10",
    )
    assert reconciler.compare_payment("stripe", "pay-9", None, [orphan_row]) == [
        CaseDraft(
            "reconcile:settlement-without-realtime:stripe:pay-9",
            "stripe",
            "pay-9",
            "settlement_without_realtime",
            "Settlement exists without any realtime payment events",
        )
    ]

    assert reconciler.compare_payment("stripe", "pay-1", sample_state(), []) == [
        CaseDraft(
            "reconcile:missing-settlement:stripe:pay-1:1000",
            "stripe",
            "pay-1",
            "realtime_missing_settlement",
            "Realtime captured 1000 but no settlement row was imported",
        )
    ]

    assert reconciler.compare_capture("stripe", sample_state(), 1000) == []
    assert reconciler.compare_capture("stripe", sample_state(), 900) == [
        CaseDraft(
            "reconcile:capture-mismatch:stripe:pay-1:1000:900",
            "stripe",
            "pay-1",
            "settlement_capture_mismatch",
            "Realtime captured 1000 but settlement shows 900",
        )
    ]

    assert reconciler.compare_refund("stripe", sample_state(), 0) == []
    refund_state = PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=1000,
        captured_amount=1000,
        refunded_amount=200,
        latest_at="2026-03-10T10:00:00Z",
        anomaly_keys=(),
        anomaly_notes=(),
    )
    assert reconciler.compare_refund("stripe", refund_state, 100) == [
        CaseDraft(
            "reconcile:refund-mismatch:stripe:pay-1:200:100",
            "stripe",
            "pay-1",
            "settlement_refund_mismatch",
            "Realtime refunded 200 but settlement shows 100",
        )
    ]

    assert isinstance(Mismatch.of(0, 0), BothMissing)
    assert Mismatch.of(0, 100) == SettledOnly(100)
    assert Mismatch.of(100, 0) == RealtimeOnly(100)
    assert isinstance(Mismatch.of(100, 100), Exact)
    assert Mismatch.of(100, 90) == Mismatch(100, 90)

    assert reconciler.compare_currency("stripe", sample_state(), None) == []
    assert reconciler.compare_currency("stripe", sample_state(), "USD") == []
    assert reconciler.compare_currency("stripe", sample_state(), "EUR") == [
        CaseDraft(
            "reconcile:currency-mismatch:stripe:pay-1:USD:EUR",
            "stripe",
            "pay-1",
            "settlement_currency_mismatch",
            "Realtime currency is USD but settlement currency is EUR",
        )
    ]

    assert Reconciler.settlements_for_provider(
        [sample_capture_row(), sample_refund_row()], "stripe"
    ) == [sample_capture_row(), sample_refund_row()]
    assert Reconciler.settlements_for_provider([sample_capture_row()], "adyen") == []
    assert Reconciler.settlements_for_payment(
        [sample_capture_row(), sample_refund_row()], "pay-1"
    ) == [sample_capture_row(), sample_refund_row()]
    assert Reconciler.settlements_for_payment([sample_capture_row()], "missing") == []


def _test_views() -> None:
    assert PaymentView(sample_state()).status_text == "captured"
    review = PaymentState(
        payment_id="pay-1",
        provider="stripe",
        currency="USD",
        authorized_amount=0,
        captured_amount=0,
        refunded_amount=100,
        latest_at="2026-03-10T10:00:00Z",
        anomaly_keys=("x",),
        anomaly_notes=("bad",),
    )
    assert PaymentView(review).status_text == "review"

    assert PaymentView(sample_state()).anomaly_summary == "-"
    assert PaymentView(review).anomaly_summary == "bad"

    reporter = ProviderReporter(
        [sample_event()], [sample_state()], [sample_capture_row()], [sample_case()]
    )
    assert reporter.summary("stripe") == ProviderSummary(
        provider="stripe",
        payments=1,
        events=1,
        settlements=1,
        open_cases=1,
        captured_amount=1000,
        refunded_amount=0,
    )

    extra_row = SettlementRow(
        provider="stripe",
        row_id="row-x",
        payment_id="pay-9",
        kind=SettlementKind.Capture,
        amount=400,
        currency="USD",
        settled_on="2026-03-10",
    )
    assert (
        ProviderReporter([], [sample_state()], [extra_row], []).payment_count("stripe")
        == 2
    )

    assert ProviderReporter([sample_event()], [], [], []).event_count("stripe") == 1
    assert ProviderReporter([], [], [sample_capture_row()], []).settlement_count("stripe") == 1
    assert ProviderReporter([], [], [sample_capture_row()], []).settlement_count("adyen") == 0
    assert ProviderReporter([], [], [], [sample_case()]).open_case_count("stripe") == 1
    assert ProviderReporter([], [], [], [sample_case()]).open_case_count("adyen") == 0
    assert ProviderReporter([], [sample_state()], [], []).captured_total("stripe") == 1000
    assert ProviderReporter([], [sample_state()], [], []).captured_total("adyen") == 0
    assert ProviderReporter([], [sample_state()], [], []).refunded_total("stripe") == 0


def _smoke_tests() -> None:
    _test_normalize()
    _test_cases()
    _test_ledger()
    _test_atomic_batch_ingest()
    _test_reconcile()
    _test_views()


def main() -> int:
    _smoke_tests()
    print("payment_ops python port: all smoke tests passed")
    _ = (cases, ledger, models, normalize, reconcile, views, PaymentOpsError)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
