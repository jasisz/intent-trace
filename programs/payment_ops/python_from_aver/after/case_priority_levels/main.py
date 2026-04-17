"""Entry point with smoke tests that mirror the Aver `verify` cases.

This variant covers the case-priority feature on top of the baseline smoke
tests: priority derivation, materialization, lifecycle transitions, audit
entries, and priority-aware views.
"""

from __future__ import annotations

import sys

# Support running as a plain script (python3 main.py) as well as a package.
if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from case_priority_levels import (
        cases,
        ledger,
        models,
        normalize,
        reconcile,
        views,
    )
    from case_priority_levels.models import (
        AuditEntry,
        Authorized,
        CaseDraft,
        CasePriority,
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
else:
    from . import cases, ledger, models, normalize, reconcile, views
    from .models import (
        AuditEntry,
        Authorized,
        CaseDraft,
        CasePriority,
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
        priority=CasePriority.Normal,
    )


def sampleEvent() -> PaymentEventRecord:
    return PaymentEventRecord(
        provider="stripe",
        sourceId="evt-1",
        paymentId="pay-1",
        seq=1,
        event=Captured(1000, "USD", "2026-03-10T09:05:00Z"),
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
    # makeCaseDraft — amount defaults to 0 and is preserved when supplied.
    assert cases.makeCaseDraft(
        "stripe", "pay-1", "refund_before_capture", "Refund arrived first", "k"
    ) == CaseDraft("k", "stripe", "pay-1", "refund_before_capture", "Refund arrived first", 0)
    assert cases.makeCaseDraft(
        "stripe", "pay-1", "settlement_capture_mismatch", "d", "k", 900
    ) == CaseDraft("k", "stripe", "pay-1", "settlement_capture_mismatch", "d", 900)

    # draftsFromState — amount is sourced from the matching rolling total.
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
            100,
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
            100,
        )
    ]

    # kindFromKey
    assert cases._kindFromKey("refund-before-capture:evt-1") == "refund_before_capture"
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

    # materializeNewCases — priority defaults to Normal when amount is zero.
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
        priority=CasePriority.Normal,
    )
    assert cases.materializeNewCases([], [draft], "2026-03-10T12:00:00Z") == [expected_case]
    assert cases.materializeNewCases(
        [expected_case], [draft], "2026-03-10T12:30:00Z"
    ) == []

    # materializeNewCases — priority is derived from kind and amount.
    urgent_draft = cases.makeCaseDraft(
        "stripe",
        "pay-7",
        "settlement_capture_mismatch",
        "big break",
        "reconcile:capture-mismatch:stripe:pay-7:50000000:45000000",
        50_000_000,
    )
    [urgent_case] = cases.materializeNewCases([], [urgent_draft], "2026-03-10T12:00:00Z")
    assert urgent_case.priority is CasePriority.Urgent

    low_draft = cases.makeCaseDraft(
        "stripe",
        "pay-8",
        "refund_before_capture",
        "tiny",
        "refund-before-capture:evt-low",
        50,
    )
    [low_case] = cases.materializeNewCases([], [low_draft], "2026-03-10T12:00:00Z")
    assert low_case.priority is CasePriority.Low

    currency_draft = cases.makeCaseDraft(
        "stripe",
        "pay-9",
        "currency_mismatch",
        "fx drift",
        "currency-mismatch:evt-fx",
        100,
    )
    [currency_case] = cases.materializeNewCases([], [currency_draft], "2026-03-10T12:00:00Z")
    assert currency_case.priority is CasePriority.High

    # resolveCase — default path preserves priority.
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
            priority=CasePriority.Normal,
        )
    ]
    _expect_error(
        lambda: cases.resolveCase([], "case-1", "x", "2026-03-10T13:00:00Z"),
        "Unknown case: case-1",
    )

    # resolveCase — operator override captures the escalation at resolve-time.
    escalated = cases.resolveCase(
        [expected_case],
        "case-1",
        "looks critical after all",
        "2026-03-10T13:00:00Z",
        CasePriority.Urgent,
    )
    assert escalated[0].priority is CasePriority.Urgent
    assert escalated[0].status is CaseStatus.Resolved

    # reprioritizeCase — lifecycle-neutral priority change.
    reprioritized = cases.reprioritizeCase([expected_case], "case-1", CasePriority.High)
    assert reprioritized[0].priority is CasePriority.High
    assert reprioritized[0].status is CaseStatus.Open
    _expect_error(
        lambda: cases.reprioritizeCase([], "case-1", CasePriority.High),
        "Unknown case: case-1",
    )

    # derivePriority — key rules spelled out as smoke tests.
    assert cases.derivePriority("settlement_capture_mismatch", 50_000_000) is CasePriority.Urgent
    assert cases.derivePriority("settlement_capture_mismatch", 200_000) is CasePriority.High
    assert cases.derivePriority("settlement_capture_mismatch", 5_000) is CasePriority.Normal
    assert cases.derivePriority("currency_mismatch", 100) is CasePriority.High
    assert cases.derivePriority("settlement_currency_mismatch", 100_000_000) is CasePriority.Urgent
    assert cases.derivePriority("refund_before_capture", 50) is CasePriority.Low
    assert cases.derivePriority("refund_before_capture", 10_000) is CasePriority.Normal
    assert cases.derivePriority("refund_before_capture", 0) is CasePriority.Normal
    assert cases.derivePriority("payment_integrity", 999_999_999) is CasePriority.Normal

    # priorityText / parsePriority round-trip.
    for priority in CasePriority:
        assert cases.parsePriority(cases.priorityText(priority)) is priority
    assert cases.parsePriority("URGENT") is CasePriority.Urgent
    _expect_error(
        lambda: cases.parsePriority("boom"),
        "Case priority must be one of: low, normal, high, urgent",
    )

    # filterByStatusLabel
    assert cases.filterByStatusLabel([], None) == []
    assert cases.filterByStatusLabel([sampleCase()], "open") == [sampleCase()]
    _expect_error(
        lambda: cases.filterByStatusLabel([], "weird"),
        "Case filter must be one of: open, resolved, all",
    )

    # filterByPriority
    normal_case = sampleCase()
    urgent_sample_case = ReviewCase(
        id="case-2",
        key="k2",
        provider="stripe",
        paymentId="pay-1",
        kind="settlement_capture_mismatch",
        detail="d",
        status=CaseStatus.Open,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt=None,
        resolution=None,
        priority=CasePriority.Urgent,
    )
    assert cases.filterByPriority(
        [normal_case, urgent_sample_case], CasePriority.Urgent
    ) == [urgent_sample_case]
    assert cases.filterByPriority(
        [normal_case, urgent_sample_case], CasePriority.Low
    ) == []

    # sortByPriority — most-urgent first, stable inside a band.
    assert cases.sortByPriority([normal_case, urgent_sample_case]) == [
        urgent_sample_case,
        normal_case,
    ]

    # openCasesForPayment
    assert cases.openCasesForPayment([sampleCase()], "pay-1") == [sampleCase()]

    # findCaseById
    assert cases.findCaseById([sampleCase()], "case-1") == sampleCase()
    assert cases.findCaseById([], "case-1") is None

    # auditForOpenedCase — priority encoded in message and structured field.
    assert cases.auditForOpenedCase(sampleCase()) == AuditEntry(
        key="case-opened:k",
        subjectId="payment:pay-1",
        action="case.opened",
        message="[normal] [x] d",
        createdAt="2026-03-10T12:00:00Z",
        priority=CasePriority.Normal,
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
        priority=CasePriority.High,
    )
    assert cases.auditForResolvedCase(resolved_case) == AuditEntry(
        key="case-resolved:k",
        subjectId="payment:pay-1",
        action="case.resolved",
        message="[high] duplicate",
        createdAt="2026-03-10T13:00:00Z",
        priority=CasePriority.High,
    )

    # auditForReprioritizedCase
    reprioritized_case = ReviewCase(
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
        priority=CasePriority.Urgent,
    )
    assert cases.auditForReprioritizedCase(
        reprioritized_case, "2026-03-10T14:00:00Z"
    ) == AuditEntry(
        key="case-reprioritized:k",
        subjectId="payment:pay-1",
        action="case.reprioritized",
        message="[urgent] priority updated",
        createdAt="2026-03-10T14:00:00Z",
        priority=CasePriority.Urgent,
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

    # replayPayment
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

    # emptyState / applyEvent seed check
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

    # withCurrencyCheck no-op when matching
    assert ledger._withCurrencyCheck(empty, "evt-9", "USD") == empty

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

    # reconcileProvider capture mismatch — draft carries the larger side as amount.
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
            1000,
        )
    ]

    # comparePayment settlement without realtime — amount is the settled total.
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
            400,
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
            1000,
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
            1000,
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
            200,
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

    # compareCurrency
    assert reconcile._compareCurrency("stripe", sampleState(), None) == []
    assert reconcile._compareCurrency("stripe", sampleState(), "USD") == []
    assert reconcile._compareCurrency("stripe", sampleState(), "EUR") == [
        CaseDraft(
            "reconcile:currency-mismatch:stripe:pay-1:USD:EUR",
            "stripe",
            "pay-1",
            "settlement_currency_mismatch",
            "Realtime currency is USD but settlement currency is EUR",
            1000,
        )
    ]

    # settlementsForProvider / settlementsForPayment
    assert reconcile.settlementsForProvider(
        [sampleCaptureRow(), sampleRefundRow()], "stripe"
    ) == [sampleCaptureRow(), sampleRefundRow()]
    assert reconcile.settlementsForProvider([sampleCaptureRow()], "adyen") == []
    assert reconcile.settlementsForPayment(
        [sampleCaptureRow(), sampleRefundRow()], "pay-1"
    ) == [sampleCaptureRow(), sampleRefundRow()]
    assert reconcile.settlementsForPayment([sampleCaptureRow()], "missing") == []

    # _totalByKind
    assert reconcile._totalByKind(
        [sampleCaptureRow(), sampleRefundRow()], SettlementKind.Capture
    ) == 1000
    assert reconcile._totalByKind(
        [sampleCaptureRow(), sampleRefundRow()], SettlementKind.Refund
    ) == 200

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

    # casePriorityText
    assert views.casePriorityText(sampleCase()) == "normal"

    # providerSummary
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

    # openCasesByPriority — every level is keyed even when counts are zero.
    urgent = ReviewCase(
        id="case-2",
        key="k2",
        provider="stripe",
        paymentId="pay-1",
        kind="settlement_capture_mismatch",
        detail="d",
        status=CaseStatus.Open,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt=None,
        resolution=None,
        priority=CasePriority.Urgent,
    )
    closed_high = ReviewCase(
        id="case-3",
        key="k3",
        provider="stripe",
        paymentId="pay-2",
        kind="settlement_capture_mismatch",
        detail="d",
        status=CaseStatus.Resolved,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt="2026-03-10T12:30:00Z",
        resolution="fixed",
        priority=CasePriority.High,
    )
    other_provider = ReviewCase(
        id="case-4",
        key="k4",
        provider="adyen",
        paymentId="pay-3",
        kind="settlement_capture_mismatch",
        detail="d",
        status=CaseStatus.Open,
        createdAt="2026-03-10T12:00:00Z",
        resolvedAt=None,
        resolution=None,
        priority=CasePriority.Urgent,
    )
    buckets = views.openCasesByPriority(
        [sampleCase(), urgent, closed_high, other_provider], "stripe"
    )
    assert buckets == {
        CasePriority.Low: 0,
        CasePriority.Normal: 1,
        CasePriority.High: 0,
        CasePriority.Urgent: 1,
    }

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
    print("payment_ops python port (case_priority_levels): all smoke tests passed")
    # Silence linters about unused imports while keeping the public surface explicit.
    _ = models
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
