"""Entry point with smoke tests that mirror the Aver `verify` cases.

Covers the multi-currency refactor: foreign-currency events become anomalies
rather than silent conversions, settlement totals are per-currency, and views
refuse to cross currencies when aggregating.
"""

from __future__ import annotations

import sys

# Support running as a plain script (python3 main.py) as well as a package.
if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from add_multi_currency_support import cases, ledger, models, normalize, reconcile, views
else:
    from . import cases, ledger, models, normalize, reconcile, views

from add_multi_currency_support.models import (
    AuditEntry,
    Authorized,
    CaseDraft,
    CaseStatus,
    Captured,
    PaymentEventRecord,
    PaymentState,
    ProviderSummary,
    RawSettlement,
    RawWebhook,
    Refunded,
    ReviewCase,
    SettlementKind,
    SettlementRow,
)


# ---------------------------------------------------------------------------
# Reusable fixtures shared with the Aver verify blocks.
# ---------------------------------------------------------------------------


def sampleAuthorized() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-1",
        paymentId="pay-1",
        seq=1,
        event=Authorized(1000, "USD", "2026-03-10T09:00:00Z"),
    )


def sampleCaptured() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-2",
        paymentId="pay-1",
        seq=2,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
    )


def sampleRefundBeforeCapture() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-9",
        paymentId="pay-2",
        seq=1,
        event=Refunded(400, "USD", "2026-03-10T11:00:00Z"),
    )


def sampleOtherPayment() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-7",
        paymentId="pay-2",
        seq=1,
        event=Authorized(700, "EUR", "2026-03-10T12:00:00Z"),
    )


def sampleState() -> PaymentState:
    return PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=1000,
        capturedAmount=1000,
        refundedAmount=0,
        latestAt="2026-03-10T09:05:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )


def sampleCaptureRow() -> SettlementRow:
    return SettlementRow(
        provider="stripe",
        rowId="row-1",
        paymentId="pay-1",
        kind=SettlementKind.Capture,
        amount=1000,
        currency="USD",
        settledOn="2026-03-10",
    )


def sampleRefundRow() -> SettlementRow:
    return SettlementRow(
        provider="stripe",
        rowId="row-2",
        paymentId="pay-1",
        kind=SettlementKind.Refund,
        amount=200,
        currency="USD",
        settledOn="2026-03-10",
    )


def sampleCase() -> ReviewCase:
    return ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        paymentId="pay-1",
        kind="x",
        detail="d",
        status=CaseStatus.Open,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt=None,
        resolution=None,
    )


def sampleEvent() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-1",
        paymentId="pay-1",
        seq=1,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
    )


def sampleForeignRefund() -> PaymentEventRecord:
    """Refund paid out in a currency Stripe sold (EUR) on a USD-locked stream."""
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-3",
        paymentId="pay-1",
        seq=3,
        event=Refunded(180, "EUR", "2026-03-10T10:00:00Z"),
    )


# ---------------------------------------------------------------------------
# Helpers for Result-style expectations.
# ---------------------------------------------------------------------------


def _expect_error(fn, message: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert str(exc) == message, f"expected {message!r}, got {exc!r}"
        return
    raise AssertionError(f"expected ValueError({message!r}) but no error was raised")


def _expect_error_prefix(fn, prefix: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert str(exc).startswith(prefix), f"expected prefix {prefix!r}, got {exc!r}"
        return
    raise AssertionError(f"expected ValueError starting with {prefix!r} but no error was raised")


# ---------------------------------------------------------------------------
# Smoke tests mirroring every Aver verify block.
# ---------------------------------------------------------------------------


def _test_normalize() -> None:
    # normalizeWebhook
    assert normalize.normalizeWebhook(
        "stripe",
        1,
        RawWebhook("evt-1", "pay-1", "charge.captured", 1200, "USD", "2026-03-10T10:00:00Z"),
    ) == PaymentEventRecord(
        "stripe", "evt-1", "pay-1", 1, Captured(1200, "USD", "2026-03-10T10:00:00Z")
    )
    assert normalize.normalizeWebhook(
        "adyen",
        2,
        RawWebhook("psp-2", "pay-2", "REFUND", 500, "EUR", "2026-03-10T11:00:00Z"),
    ) == PaymentEventRecord(
        "adyen", "psp-2", "pay-2", 2, Refunded(500, "EUR", "2026-03-10T11:00:00Z")
    )
    _expect_error(
        lambda: normalize.normalizeWebhook(
            "unknown",
            1,
            RawWebhook("evt-9", "pay-9", "charge.captured", 100, "USD", "2026-03-10T10:00:00Z"),
        ),
        "Provider must be one of: stripe, adyen",
    )

    # normalizeSettlement
    assert normalize.normalizeSettlement(
        "stripe", RawSettlement("row-1", "pay-1", "charge", 1200, "USD", "2026-03-10")
    ) == SettlementRow(
        "stripe", "row-1", "pay-1", SettlementKind.Capture, 1200, "USD", "2026-03-10"
    )
    assert normalize.normalizeSettlement(
        "adyen", RawSettlement("row-2", "pay-2", "Refunded", 500, "EUR", "2026-03-10")
    ) == SettlementRow(
        "adyen", "row-2", "pay-2", SettlementKind.Refund, 500, "EUR", "2026-03-10"
    )
    _expect_error(
        lambda: normalize.normalizeSettlement(
            "unknown",
            RawSettlement("row-9", "pay-9", "charge", 100, "USD", "2026-03-10"),
        ),
        "Provider must be one of: stripe, adyen",
    )

    # normalizeProvider
    assert normalize.normalizeProvider("stripe") == "stripe"
    assert normalize.normalizeProvider("Adyen") == "adyen"
    _expect_error(
        lambda: normalize.normalizeProvider("unknown"),
        "Provider must be one of: stripe, adyen",
    )

    # normalizeWebhookEvent
    assert normalize.normalizeWebhookEvent(
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
    assert normalize.normalizeWebhookEvent(
        "adyen",
        RawWebhook("psp-1", "pay-1", "CAPTURE", 1000, "USD", "2026-03-10T09:05:00Z"),
    ) == Captured(1000, "USD", "2026-03-10T09:05:00Z")
    _expect_error(
        lambda: normalize.normalizeWebhookEvent(
            "worldpay",
            RawWebhook("evt-1", "pay-1", "CAPTURE", 1000, "USD", "2026-03-10T09:05:00Z"),
        ),
        "Unsupported provider: worldpay",
    )

    # normalizeStripeWebhook
    assert normalize.normalizeStripeWebhook(
        RawWebhook("evt-1", "pay-1", "charge.refunded", 300, "USD", "2026-03-10T10:30:00Z")
    ) == Refunded(300, "USD", "2026-03-10T10:30:00Z")
    _expect_error(
        lambda: normalize.normalizeStripeWebhook(
            RawWebhook("evt-1", "pay-1", "weird", 300, "USD", "2026-03-10T10:30:00Z")
        ),
        "Unsupported stripe webhook kind: weird",
    )

    # normalizeAdyenWebhook
    assert normalize.normalizeAdyenWebhook(
        RawWebhook("psp-1", "pay-1", "AUTHORISATION", 900, "EUR", "2026-03-10T08:00:00Z")
    ) == Authorized(900, "EUR", "2026-03-10T08:00:00Z")
    _expect_error(
        lambda: normalize.normalizeAdyenWebhook(
            RawWebhook("psp-1", "pay-1", "BOOM", 900, "EUR", "2026-03-10T08:00:00Z")
        ),
        "Unsupported adyen webhook kind: BOOM",
    )

    # normalizeSettlementKind
    assert normalize.normalizeSettlementKind("stripe", "charge") == SettlementKind.Capture
    assert normalize.normalizeSettlementKind("adyen", "Refunded") == SettlementKind.Refund
    _expect_error(
        lambda: normalize.normalizeSettlementKind("worldpay", "charge"),
        "Unsupported provider: worldpay",
    )

    # normalizeStripeSettlement
    assert normalize.normalizeStripeSettlement("charge") == SettlementKind.Capture
    _expect_error(
        lambda: normalize.normalizeStripeSettlement("other"),
        "Unsupported stripe settlement kind: other",
    )

    # normalizeAdyenSettlement
    assert normalize.normalizeAdyenSettlement("Settled") == SettlementKind.Capture
    _expect_error(
        lambda: normalize.normalizeAdyenSettlement("other"),
        "Unsupported adyen settlement kind: other",
    )


def _test_cases() -> None:
    # makeCaseDraft
    assert cases.makeCaseDraft(
        "stripe", "pay-1", "refund_before_capture", "Refund arrived first", "k"
    ) == CaseDraft("k", "stripe", "pay-1", "refund_before_capture", "Refund arrived first")

    # draftsFromState
    state_with_anomaly = PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=100,
        latestAt="2026-03-10T10:00:00Z",
        anomalyKeys=("refund-before-capture:evt-1",),
        anomalyNotes=("Refund arrived before any capture for payment 'pay-1'",),
    )
    assert cases.draftsFromState(state_with_anomaly) == [
        CaseDraft(
            "refund-before-capture:evt-1",
            "stripe",
            "pay-1",
            "refund_before_capture",
            "Refund arrived before any capture for payment 'pay-1'",
        )
    ]

    # draftsFromStates
    assert cases.draftsFromStates([state_with_anomaly]) == [
        CaseDraft(
            "refund-before-capture:evt-1",
            "stripe",
            "pay-1",
            "refund_before_capture",
            "Refund arrived before any capture for payment 'pay-1'",
        )
    ]

    # kindFromKey — old keys still map, plus the new foreign-currency bucket.
    assert cases._kindFromKey("refund-before-capture:evt-1") == "refund_before_capture"
    assert cases._kindFromKey("foreign-currency-event:evt-3") == "foreign_currency_event"
    assert cases._kindFromKey("currency-mismatch:evt-1") == "currency_mismatch"
    assert cases._kindFromKey("x") == "payment_integrity"

    # statusText
    assert cases.statusText(CaseStatus.Open) == "open"
    assert cases.statusText(CaseStatus.Resolved) == "resolved"

    # parseStatus
    assert cases.parseStatus("open") is CaseStatus.Open
    assert cases.parseStatus("resolved") is CaseStatus.Resolved
    _expect_error(
        lambda: cases.parseStatus("x"),
        "Case status must be one of: open, resolved",
    )

    # materializeNewCases
    draft = cases.makeCaseDraft(
        "stripe", "pay-1", "refund_before_capture", "Refund arrived first", "k"
    )
    expected_case = ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        paymentId="pay-1",
        kind="refund_before_capture",
        detail="Refund arrived first",
        status=CaseStatus.Open,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt=None,
        resolution=None,
    )
    assert cases.materializeNewCases([], [draft], "2026-03-10T12:00:00Z") == [expected_case]
    assert cases.materializeNewCases(
        [expected_case], [draft], "2026-03-10T12:30:00Z"
    ) == []

    # resolveCase
    resolved = cases.resolveCase(
        [expected_case], "case-1", "duplicate webhook", "2026-03-10T13:00:00Z"
    )
    assert resolved == [
        ReviewCase(
            id="case-1",
            key="k",
            provider="stripe",
            paymentId="pay-1",
            kind="refund_before_capture",
            detail="Refund arrived first",
            status=CaseStatus.Resolved,
            createdAt="2026-03-10T12:00:00Z",
            resolvedAt="2026-03-10T13:00:00Z",
            resolution="duplicate webhook",
        )
    ]
    _expect_error(
        lambda: cases.resolveCase([], "case-1", "x", "2026-03-10T13:00:00Z"),
        "Unknown case: case-1",
    )

    # filterByStatusLabel
    assert cases.filterByStatusLabel([], None) == []
    assert cases.filterByStatusLabel([sampleCase()], "open") == [sampleCase()]
    _expect_error(
        lambda: cases.filterByStatusLabel([], "weird"),
        "Case filter must be one of: open, resolved, all",
    )

    # openCasesForPayment
    assert cases.openCasesForPayment([sampleCase()], "pay-1") == [sampleCase()]

    # findCaseById
    assert cases.findCaseById([sampleCase()], "case-1") == sampleCase()
    assert cases.findCaseById([], "case-1") is None

    # auditForOpenedCase
    assert cases.auditForOpenedCase(sampleCase()) == AuditEntry(
        key="case-opened:k",
        subjectId="payment:pay-1",
        action="case.opened",
        message="[x] d",
        createdAt="2026-03-10T12:00:00Z",
    )

    # auditForResolvedCase
    resolved_case = ReviewCase(
        id="case-1",
        key="k",
        provider="stripe",
        paymentId="pay-1",
        kind="x",
        detail="d",
        status=CaseStatus.Resolved,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt="2026-03-10T13:00:00Z",
        resolution="duplicate",
    )
    assert cases.auditForResolvedCase(resolved_case) == AuditEntry(
        key="case-resolved:k",
        subjectId="payment:pay-1",
        action="case.resolved",
        message="duplicate",
        createdAt="2026-03-10T13:00:00Z",
    )


def _test_ledger() -> None:
    # eventName / eventAt / eventCurrency / eventAmount
    assert ledger.eventName(Authorized(100, "USD", "2026-03-10T09:00:00Z")) == "Authorized"
    assert ledger.eventName(Captured(100, "USD", "2026-03-10T10:00:00Z")) == "Captured"
    assert ledger.eventName(Refunded(50, "USD", "2026-03-10T10:00:00Z")) == "Refunded"

    assert ledger.eventAt(Authorized(100, "USD", "2026-03-10T09:00:00Z")) == "2026-03-10T09:00:00Z"
    assert ledger.eventAt(Captured(100, "USD", "2026-03-10T10:00:00Z")) == "2026-03-10T10:00:00Z"
    assert ledger.eventAt(Refunded(50, "USD", "2026-03-10T11:00:00Z")) == "2026-03-10T11:00:00Z"

    assert ledger.eventCurrency(Authorized(50, "USD", "2026-03-10T09:00:00Z")) == "USD"
    assert ledger.eventCurrency(Captured(50, "GBP", "2026-03-10T10:00:00Z")) == "GBP"
    assert ledger.eventCurrency(Refunded(50, "EUR", "2026-03-10T10:00:00Z")) == "EUR"

    assert ledger.eventAmount(Authorized(900, "USD", "2026-03-10T09:00:00Z")) == 900
    assert ledger.eventAmount(Captured(1200, "USD", "2026-03-10T10:00:00Z")) == 1200
    assert ledger.eventAmount(Refunded(200, "USD", "2026-03-10T11:00:00Z")) == 200

    # hasSourceId
    assert ledger.hasSourceId([sampleAuthorized(), sampleCaptured()], "stripe", "evt-1") is True
    assert ledger.hasSourceId([sampleAuthorized()], "stripe", "missing") is False

    # nextSeq
    assert ledger.nextSeq([], "stripe", "pay-1") == 1
    assert ledger.nextSeq([sampleAuthorized(), sampleCaptured()], "stripe", "pay-1") == 3
    assert ledger.nextSeq([sampleAuthorized(), sampleCaptured()], "stripe", "pay-2") == 1

    # eventsForPayment
    assert ledger.eventsForPayment(
        [sampleAuthorized(), sampleCaptured(), sampleOtherPayment()], "pay-1"
    ) == [sampleAuthorized(), sampleCaptured()]

    # replayPayment — single-currency happy path unchanged.
    assert ledger.replayPayment([sampleAuthorized(), sampleCaptured()]) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=1000,
        capturedAmount=1000,
        refundedAmount=0,
        latestAt="2026-03-10T09:05:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )
    assert ledger.replayPayment([sampleRefundBeforeCapture()]) == PaymentState(
        paymentId="pay-2",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=400,
        latestAt="2026-03-10T11:00:00Z",
        anomalyKeys=(
            "refund-before-capture:evt-9",
            "refund-exceeds-capture:evt-9",
        ),
        anomalyNotes=(
            "Refund arrived before any capture for payment 'pay-2'",
            "Refunded amount exceeds captured amount for payment 'pay-2'",
        ),
    )
    _expect_error(
        lambda: ledger.replayPayment([]),
        "Cannot replay empty payment event list",
    )

    # replayPayment — foreign-currency refund becomes an anomaly rather than a silent sum.
    assert ledger.replayPayment(
        [sampleAuthorized(), sampleCaptured(), sampleForeignRefund()]
    ) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=1000,
        capturedAmount=1000,
        refundedAmount=0,
        latestAt="2026-03-10T10:00:00Z",
        anomalyKeys=("foreign-currency-event:evt-3",),
        anomalyNotes=(
            "Foreign-currency refund in EUR arrived on payment 'pay-1' locked to USD; "
            "amount skipped from totals",
        ),
    )

    # replayAll
    assert ledger.replayAll(
        [sampleAuthorized(), sampleCaptured(), sampleOtherPayment()]
    ) == [
        PaymentState(
            paymentId="pay-1",
            provider="stripe",
            currency="USD",
            authorizedAmount=1000,
            capturedAmount=1000,
            refundedAmount=0,
            latestAt="2026-03-10T09:05:00Z",
            anomalyKeys=(),
            anomalyNotes=(),
        ),
        PaymentState(
            paymentId="pay-2",
            provider="stripe",
            currency="EUR",
            authorizedAmount=700,
            capturedAmount=0,
            refundedAmount=0,
            latestAt="2026-03-10T12:00:00Z",
            anomalyKeys=(),
            anomalyNotes=(),
        ),
    ]

    # emptyState seed check — first event locks the currency.
    empty = ledger._emptyState(sampleAuthorized())
    assert empty == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=0,
        latestAt="2026-03-10T09:00:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )

    # applyEvent single Authorized
    assert ledger._applyEvent(empty, sampleAuthorized()) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=1000,
        capturedAmount=0,
        refundedAmount=0,
        latestAt="2026-03-10T09:00:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )

    # applyCaptured without auth raises anomaly
    assert ledger._applyCaptured(
        empty, "evt-2", 1000, "USD", "2026-03-10T09:05:00Z"
    ) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=1000,
        refundedAmount=0,
        latestAt="2026-03-10T09:05:00Z",
        anomalyKeys=("capture-without-authorize:evt-2",),
        anomalyNotes=("Capture arrived before any authorization for payment 'pay-1'",),
    )

    # applyCaptured in a foreign currency: totals untouched, foreign-currency anomaly added.
    assert ledger._applyCaptured(
        empty, "evt-5", 1000, "EUR", "2026-03-10T09:10:00Z"
    ) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=0,
        latestAt="2026-03-10T09:10:00Z",
        anomalyKeys=("foreign-currency-event:evt-5",),
        anomalyNotes=(
            "Foreign-currency capture in EUR arrived on payment 'pay-1' locked to USD; "
            "amount skipped from totals",
        ),
    )

    # applyRefunded without capture raises two anomalies
    assert ledger._applyRefunded(
        empty, "evt-3", 200, "USD", "2026-03-10T10:00:00Z"
    ) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=200,
        latestAt="2026-03-10T10:00:00Z",
        anomalyKeys=(
            "refund-before-capture:evt-3",
            "refund-exceeds-capture:evt-3",
        ),
        anomalyNotes=(
            "Refund arrived before any capture for payment 'pay-1'",
            "Refunded amount exceeds captured amount for payment 'pay-1'",
        ),
    )

    # applyAuthorized in a foreign currency: authorizedAmount stays zero,
    # locked currency unchanged, foreign-currency anomaly added.
    assert ledger._applyAuthorized(
        empty, "evt-4", 2000, "EUR", "2026-03-10T09:30:00Z"
    ) == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=0,
        latestAt="2026-03-10T09:30:00Z",
        anomalyKeys=("foreign-currency-event:evt-4",),
        anomalyNotes=(
            "Foreign-currency authorization in EUR arrived on payment 'pay-1' locked to USD; "
            "amount skipped from totals",
        ),
    )

    # sumAmountsInCurrency — refuses silent conversion by construction.
    assert ledger.sumAmountsInCurrency(
        [(100, "USD"), (200, "EUR"), (50, "USD")], "USD"
    ) == 150
    assert ledger.sumAmountsInCurrency(
        [(100, "USD"), (200, "EUR")], "GBP"
    ) == 0

    # addAnomaly
    assert ledger._addAnomaly(empty, "k", "n") == PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=0,
        capturedAmount=0,
        refundedAmount=0,
        latestAt="2026-03-10T09:00:00Z",
        anomalyKeys=("k",),
        anomalyNotes=("n",),
    )


def _test_reconcile() -> None:
    # reconcileProvider no mismatch
    assert reconcile.reconcileProvider("stripe", [sampleState()], [sampleCaptureRow()]) == []

    # reconcileProvider capture mismatch
    mismatched_row = SettlementRow(
        provider="stripe",
        rowId="row-1",
        paymentId="pay-1",
        kind=SettlementKind.Capture,
        amount=900,
        currency="USD",
        settledOn="2026-03-10",
    )
    assert reconcile.reconcileProvider("stripe", [sampleState()], [mismatched_row]) == [
        CaseDraft(
            "reconcile:capture-mismatch:stripe:pay-1:1000:900",
            "stripe",
            "pay-1",
            "settlement_capture_mismatch",
            "Realtime captured 1000 but settlement shows 900",
        )
    ]

    # reconcileProvider with a foreign-currency refund row: no silent sum, just a dedicated case.
    foreignRefundRow = SettlementRow(
        provider="stripe",
        rowId="row-eur",
        paymentId="pay-1",
        kind=SettlementKind.Refund,
        amount=180,
        currency="EUR",
        settledOn="2026-03-10",
    )
    assert reconcile.reconcileProvider(
        "stripe", [sampleState()], [sampleCaptureRow(), foreignRefundRow]
    ) == [
        CaseDraft(
            "reconcile:foreign-currency-settlement:stripe:pay-1:refund:EUR",
            "stripe",
            "pay-1",
            "settlement_foreign_currency",
            "Settlement refund of 180 EUR does not match locked payment currency USD",
        )
    ]

    # comparePayment settlement without realtime
    orphan_row = SettlementRow(
        provider="stripe",
        rowId="row-x",
        paymentId="pay-9",
        kind=SettlementKind.Capture,
        amount=400,
        currency="USD",
        settledOn="2026-03-10",
    )
    assert reconcile._comparePayment("stripe", "pay-9", None, [orphan_row]) == [
        CaseDraft(
            "reconcile:settlement-without-realtime:stripe:pay-9",
            "stripe",
            "pay-9",
            "settlement_without_realtime",
            "Settlement exists without any realtime payment events",
        )
    ]

    # comparePayment realtime missing settlement
    assert reconcile._comparePayment("stripe", "pay-1", sampleState(), []) == [
        CaseDraft(
            "reconcile:missing-settlement:stripe:pay-1:1000",
            "stripe",
            "pay-1",
            "realtime_missing_settlement",
            "Realtime captured 1000 but no settlement row was imported",
        )
    ]

    # compareCapture
    assert reconcile._compareCapture("stripe", sampleState(), 1000) == []
    assert reconcile._compareCapture("stripe", sampleState(), 900) == [
        CaseDraft(
            "reconcile:capture-mismatch:stripe:pay-1:1000:900",
            "stripe",
            "pay-1",
            "settlement_capture_mismatch",
            "Realtime captured 1000 but settlement shows 900",
        )
    ]

    # compareRefund
    assert reconcile._compareRefund("stripe", sampleState(), 0) == []
    refund_state = PaymentState(
        paymentId="pay-1",
        provider="stripe",
        currency="USD",
        authorizedAmount=1000,
        capturedAmount=1000,
        refundedAmount=200,
        latestAt="2026-03-10T10:00:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )
    assert reconcile._compareRefund("stripe", refund_state, 100) == [
        CaseDraft(
            "reconcile:refund-mismatch:stripe:pay-1:200:100",
            "stripe",
            "pay-1",
            "settlement_refund_mismatch",
            "Realtime refunded 200 but settlement shows 100",
        )
    ]

    # compareAmounts
    assert isinstance(reconcile._compareAmounts(0, 0), reconcile.BothMissing)
    assert reconcile._compareAmounts(0, 100) == reconcile.SettledOnly(100)
    assert reconcile._compareAmounts(100, 0) == reconcile.RealtimeOnly(100)
    assert isinstance(reconcile._compareAmounts(100, 100), reconcile.Exact)
    assert reconcile._compareAmounts(100, 90) == reconcile.Mismatch(100, 90)

    # amountMissing
    assert reconcile._amountMissing(0) is True
    assert reconcile._amountMissing(1) is False

    # compareCurrency — receives only the matching-currency slice in the new flow,
    # so it should stay silent when that slice matches or is empty.
    assert reconcile._compareCurrency("stripe", sampleState(), None) == []
    assert reconcile._compareCurrency("stripe", sampleState(), "USD") == []
    assert reconcile._compareCurrency("stripe", sampleState(), "EUR") == [
        CaseDraft(
            "reconcile:currency-mismatch:stripe:pay-1:USD:EUR",
            "stripe",
            "pay-1",
            "settlement_currency_mismatch",
            "Realtime currency is USD but settlement currency is EUR",
        )
    ]

    # reportForeignCurrencySettlements buckets per (kind, currency) pair.
    fxCaptureRow = SettlementRow(
        provider="stripe",
        rowId="row-eur-cap",
        paymentId="pay-1",
        kind=SettlementKind.Capture,
        amount=600,
        currency="EUR",
        settledOn="2026-03-10",
    )
    fxRefundRow = SettlementRow(
        provider="stripe",
        rowId="row-eur-ref",
        paymentId="pay-1",
        kind=SettlementKind.Refund,
        amount=180,
        currency="EUR",
        settledOn="2026-03-10",
    )
    assert reconcile._reportForeignCurrencySettlements(
        "stripe", sampleState(), [sampleCaptureRow(), fxCaptureRow, fxRefundRow]
    ) == [
        CaseDraft(
            "reconcile:foreign-currency-settlement:stripe:pay-1:capture:EUR",
            "stripe",
            "pay-1",
            "settlement_foreign_currency",
            "Settlement capture of 600 EUR does not match locked payment currency USD",
        ),
        CaseDraft(
            "reconcile:foreign-currency-settlement:stripe:pay-1:refund:EUR",
            "stripe",
            "pay-1",
            "settlement_foreign_currency",
            "Settlement refund of 180 EUR does not match locked payment currency USD",
        ),
    ]
    assert reconcile._reportForeignCurrencySettlements(
        "stripe", sampleState(), [sampleCaptureRow()]
    ) == []

    # settlementsForProvider / settlementsForPayment
    assert reconcile.settlementsForProvider(
        [sampleCaptureRow(), sampleRefundRow()], "stripe"
    ) == [sampleCaptureRow(), sampleRefundRow()]
    assert reconcile.settlementsForProvider([sampleCaptureRow()], "adyen") == []
    assert reconcile.settlementsForPayment(
        [sampleCaptureRow(), sampleRefundRow()], "pay-1"
    ) == [sampleCaptureRow(), sampleRefundRow()]
    assert reconcile.settlementsForPayment([sampleCaptureRow()], "missing") == []

    # _totalByKind — still fine on a single-currency batch, but now refuses mixed currencies.
    assert reconcile._totalByKind(
        [sampleCaptureRow(), sampleRefundRow()], SettlementKind.Capture
    ) == 1000
    assert reconcile._totalByKind(
        [sampleCaptureRow(), sampleRefundRow()], SettlementKind.Refund
    ) == 200
    mixed_capture_row = SettlementRow(
        provider="stripe",
        rowId="row-eur",
        paymentId="pay-1",
        kind=SettlementKind.Capture,
        amount=900,
        currency="EUR",
        settledOn="2026-03-10",
    )
    _expect_error_prefix(
        lambda: reconcile._totalByKind(
            [sampleCaptureRow(), mixed_capture_row], SettlementKind.Capture
        ),
        "Cannot sum settlement amounts across mixed currencies",
    )

    # _totalByKindInCurrency is currency-scoped and never raises.
    assert reconcile._totalByKindInCurrency(
        [sampleCaptureRow(), mixed_capture_row], SettlementKind.Capture, "USD"
    ) == 1000
    assert reconcile._totalByKindInCurrency(
        [sampleCaptureRow(), mixed_capture_row], SettlementKind.Capture, "EUR"
    ) == 900
    assert reconcile._totalByKindInCurrency(
        [sampleCaptureRow()], SettlementKind.Refund, "USD"
    ) == 0

    # firstCurrency
    assert reconcile._firstCurrency([sampleCaptureRow()]) == "USD"
    assert reconcile._firstCurrency([]) is None

    # paymentIdsFrom
    assert reconcile._paymentIdsFrom([sampleState()], [sampleCaptureRow()]) == ["pay-1"]


def _test_views() -> None:
    # statusText
    assert views.statusText(sampleState()) == "captured"
    assert views.statusText(
        PaymentState(
            paymentId="pay-1",
            provider="stripe",
            currency="USD",
            authorizedAmount=0,
            capturedAmount=0,
            refundedAmount=100,
            latestAt="2026-03-10T10:00:00Z",
            anomalyKeys=("x",),
            anomalyNotes=("bad",),
        )
    ) == "review"

    # anomalySummary
    assert views.anomalySummary(sampleState()) == "-"
    assert views.anomalySummary(
        PaymentState(
            paymentId="pay-1",
            provider="stripe",
            currency="USD",
            authorizedAmount=0,
            capturedAmount=0,
            refundedAmount=100,
            latestAt="2026-03-10T10:00:00Z",
            anomalyKeys=("x",),
            anomalyNotes=("bad",),
        )
    ) == "bad"

    # providerSummary — single-currency happy path unchanged.
    assert views.providerSummary(
        "stripe", [sampleEvent()], [sampleState()], [sampleCaptureRow()], [sampleCase()]
    ) == ProviderSummary(
        provider="stripe",
        payments=1,
        events=1,
        settlements=1,
        openCases=1,
        capturedAmount=1000,
        refundedAmount=0,
    )

    # providerSummary — mixed-currency payments refuse silent aggregation.
    eurState = PaymentState(
        paymentId="pay-2",
        provider="stripe",
        currency="EUR",
        authorizedAmount=500,
        capturedAmount=500,
        refundedAmount=0,
        latestAt="2026-03-10T13:00:00Z",
        anomalyKeys=(),
        anomalyNotes=(),
    )
    _expect_error_prefix(
        lambda: views.providerSummary(
            "stripe", [sampleEvent()], [sampleState(), eurState], [sampleCaptureRow()], []
        ),
        "Cannot aggregate amounts across mixed currencies for provider 'stripe'",
    )

    # providerCurrencies lists the locked currencies per provider.
    assert views.providerCurrencies([sampleState(), eurState], "stripe") == ["USD", "EUR"]
    assert views.providerCurrencies([sampleState(), eurState], "adyen") == []

    # countProviderPayments — two unique payment IDs
    extra_row = SettlementRow(
        provider="stripe",
        rowId="row-x",
        paymentId="pay-9",
        kind=SettlementKind.Capture,
        amount=400,
        currency="USD",
        settledOn="2026-03-10",
    )
    assert views._countProviderPayments([sampleState()], [extra_row], "stripe") == 2

    # countProviderEvents / countProviderRows / countOpenCases / capturedTotal / refundedTotal
    assert views._countProviderEvents([sampleEvent()], "stripe") == 1
    assert views._countProviderRows([sampleCaptureRow()], "stripe") == 1
    assert views._countProviderRows([sampleCaptureRow()], "adyen") == 0
    assert views._countOpenCases([sampleCase()], "stripe") == 1
    assert views._countOpenCases([sampleCase()], "adyen") == 0
    assert views._capturedTotal([sampleState()], "stripe") == 1000
    assert views._capturedTotal([sampleState()], "adyen") == 0
    assert views._refundedTotal([sampleState()], "stripe") == 0


def _smoke_tests() -> None:
    """Run every `verify` case from the Aver sources as Python assertions."""
    _test_normalize()
    _test_cases()
    _test_ledger()
    _test_reconcile()
    _test_views()


def main() -> int:
    _smoke_tests()
    print("payment_ops python port: all smoke tests passed")
    # Silence linters about unused imports while keeping the public surface explicit.
    _ = models
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
