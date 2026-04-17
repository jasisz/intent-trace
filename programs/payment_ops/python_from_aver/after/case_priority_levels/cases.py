"""Manual-review case generation and lifecycle management.

Dirty payment operations need stable case keys so re-running ingest or reconciliation
does not keep opening the same human task forever.

This variant adds an operator-facing priority dimension. Every case carries a
`CasePriority` assigned automatically from its kind and amount at materialization
time, and operators can override that priority when resolving or re-prioritizing
a case. The derived priority is preserved in the audit trail.
"""

from __future__ import annotations

from dataclasses import replace

from .models import (
    AuditEntry,
    CaseDraft,
    CasePriority,
    CaseStatus,
    PaymentState,
    ReviewCase,
)


# ---------------------------------------------------------------------------
# Priority derivation.
# ---------------------------------------------------------------------------


# Kinds that represent a genuine money-integrity break — large mismatches on
# these must page as Urgent, moderate amounts still rank as High.
_MONEY_BREAK_KINDS = frozenset(
    {
        "captured_exceeds_authorized",
        "refund_exceeds_capture",
        "settlement_capture_mismatch",
        "settlement_refund_mismatch",
        "settlement_without_capture",
        "settlement_without_refund",
        "settlement_without_realtime",
        "realtime_missing_settlement",
        "refund_missing_settlement",
    }
)

# Kinds that are usually ordering or metadata issues rather than money loss.
# They default to Normal and only downgrade to Low for clearly trivial amounts.
_ORDERING_KINDS = frozenset(
    {
        "refund_before_capture",
        "capture_without_authorize",
    }
)

# Currency-mismatch kinds always warrant attention regardless of the amount,
# because an unreviewed FX break can silently distort every downstream report.
_CURRENCY_KINDS = frozenset(
    {
        "currency_mismatch",
        "settlement_currency_mismatch",
    }
)


# Amount thresholds expressed in minor units (cents). The bands are chosen to
# map the prompt's intuition: a $50 duplicate refund (=5_000) stays Normal or
# Low, a $500k reconciliation break (=50_000_000) pages as Urgent.
_URGENT_AMOUNT_THRESHOLD = 10_000_000  # 100_000 units of currency, e.g. $100k.
_HIGH_AMOUNT_THRESHOLD = 100_000  # 1_000 units of currency, e.g. $1k.
_LOW_AMOUNT_THRESHOLD = 1_000  # 10 units of currency, e.g. $10.


def derivePriority(kind: str, amount: int) -> CasePriority:
    """Derive the priority for a case from its kind and monetary amount.

    The rules are intentionally explicit so operators can reason about them
    without reading the codebase:

    * money-integrity breaks escalate with size (Urgent / High / Normal);
    * currency mismatches start at High because they silently distort reports;
    * ordering-only anomalies stay at Normal, or Low when the amount is tiny;
    * anything unrecognised defaults to Normal so new kinds fail safe.
    """
    sanitizedAmount = abs(amount)
    if kind in _MONEY_BREAK_KINDS:
        return _priorityForMoneyBreak(sanitizedAmount)
    if kind in _CURRENCY_KINDS:
        return _priorityForCurrency(sanitizedAmount)
    if kind in _ORDERING_KINDS:
        return _priorityForOrdering(sanitizedAmount)
    return CasePriority.Normal


def _priorityForMoneyBreak(amount: int) -> CasePriority:
    """Escalate money-integrity breaks by size."""
    if amount >= _URGENT_AMOUNT_THRESHOLD:
        return CasePriority.Urgent
    if amount >= _HIGH_AMOUNT_THRESHOLD:
        return CasePriority.High
    return CasePriority.Normal


def _priorityForCurrency(amount: int) -> CasePriority:
    """Currency mismatches are High by default, Urgent for very large sums."""
    if amount >= _URGENT_AMOUNT_THRESHOLD:
        return CasePriority.Urgent
    return CasePriority.High


def _priorityForOrdering(amount: int) -> CasePriority:
    """Ordering-only anomalies stay Normal except for clearly trivial sums."""
    if amount > 0 and amount < _LOW_AMOUNT_THRESHOLD:
        return CasePriority.Low
    return CasePriority.Normal


def priorityText(priority: CasePriority) -> str:
    """Stable on-disk and CLI spelling for case priority."""
    if priority is CasePriority.Low:
        return "low"
    if priority is CasePriority.Normal:
        return "normal"
    if priority is CasePriority.High:
        return "high"
    if priority is CasePriority.Urgent:
        return "urgent"
    raise ValueError(f"Unknown priority: {priority!r}")


def parsePriority(raw: str) -> CasePriority:
    """Parse one stored case priority (case-insensitive)."""
    lowered = raw.strip().lower()
    if lowered == "low":
        return CasePriority.Low
    if lowered == "normal":
        return CasePriority.Normal
    if lowered == "high":
        return CasePriority.High
    if lowered == "urgent":
        return CasePriority.Urgent
    raise ValueError("Case priority must be one of: low, normal, high, urgent")


# ---------------------------------------------------------------------------
# Draft construction.
# ---------------------------------------------------------------------------


def makeCaseDraft(
    provider: str,
    paymentId: str,
    kind: str,
    detail: str,
    key: str,
    amount: int = 0,
) -> CaseDraft:
    """Convenience constructor for reconcile and replay anomalies.

    `amount` is the money signal used at materialization to pick the priority.
    Callers that do not have a concrete amount available (for example
    replay-order anomalies on an empty stream) can omit it and the case will
    default to Normal.
    """
    return CaseDraft(
        key=key,
        provider=provider,
        paymentId=paymentId,
        kind=kind,
        detail=detail,
        amount=amount,
    )


def draftsFromState(state: PaymentState) -> list[CaseDraft]:
    """Turn replay anomalies into manual-review drafts."""
    return _draftsFromPairs(state, list(state.anomalyKeys), list(state.anomalyNotes))


def draftsFromStates(states: list[PaymentState]) -> list[CaseDraft]:
    """Flatten replay anomalies from every payment state."""
    acc: list[CaseDraft] = []
    for state in states:
        acc.extend(draftsFromState(state))
    return acc


def _draftsFromPairs(
    state: PaymentState, keys: list[str], notes: list[str]
) -> list[CaseDraft]:
    """Zip anomaly keys and notes into review cases.

    Mirrors the Aver implementation: each key is paired with the first note in
    the remaining notes list (fallback to a generic message when notes are
    shorter than keys).
    """
    drafts: list[CaseDraft] = []
    remaining_notes = list(notes)
    for key in keys:
        note = remaining_notes[0] if remaining_notes else "Suspicious payment history"
        drafts.append(_draftFromPair(state, key, note))
        if remaining_notes:
            remaining_notes = remaining_notes[1:]
    return drafts


def _draftFromPair(state: PaymentState, key: str, note: str) -> CaseDraft:
    """Build one case draft from a replay anomaly."""
    kind = _kindFromKey(key)
    return makeCaseDraft(
        state.provider,
        state.paymentId,
        kind,
        note,
        key,
        _amountForReplayAnomaly(state, kind),
    )


def _amountForReplayAnomaly(state: PaymentState, kind: str) -> int:
    """Pick the most meaningful amount signal for a replay-anomaly draft.

    The replayed `PaymentState` doesn't keep per-event amounts, so we use the
    rolling totals that the anomaly actually relates to. For everything else
    we fall back to the largest observed total so priority still reflects the
    size of the stream.
    """
    if kind == "refund_before_capture" or kind == "refund_exceeds_capture":
        return state.refundedAmount
    if kind == "capture_without_authorize" or kind == "captured_exceeds_authorized":
        return state.capturedAmount
    return max(
        state.authorizedAmount,
        state.capturedAmount,
        state.refundedAmount,
    )


def _kindFromKey(key: str) -> str:
    """Normalize anomaly key prefixes into user-facing case kinds."""
    if key.startswith("refund-before-capture:"):
        return "refund_before_capture"
    if key.startswith("capture-without-authorize:"):
        return "capture_without_authorize"
    if key.startswith("captured-exceeds-authorized:"):
        return "captured_exceeds_authorized"
    if key.startswith("refund-exceeds-capture:"):
        return "refund_exceeds_capture"
    if key.startswith("currency-mismatch:"):
        return "currency_mismatch"
    return "payment_integrity"


# ---------------------------------------------------------------------------
# Status text/parse.
# ---------------------------------------------------------------------------


def statusText(status: CaseStatus) -> str:
    """Stable on-disk and CLI spelling for case status."""
    if status is CaseStatus.Open:
        return "open"
    if status is CaseStatus.Resolved:
        return "resolved"
    raise ValueError(f"Unknown status: {status!r}")


def parseStatus(raw: str) -> CaseStatus:
    """Parse one stored case status."""
    lowered = raw.lower()
    if lowered == "open":
        return CaseStatus.Open
    if lowered == "resolved":
        return CaseStatus.Resolved
    raise ValueError("Case status must be one of: open, resolved")


# ---------------------------------------------------------------------------
# Materialization and lifecycle transitions.
# ---------------------------------------------------------------------------


def materializeNewCases(
    existing: list[ReviewCase], drafts: list[CaseDraft], createdAt: str
) -> list[ReviewCase]:
    """Assign deterministic case IDs, derived priorities, and skip known keys."""
    next_id = len(existing) + 1
    seen_keys = {item.key for item in existing}
    result: list[ReviewCase] = []
    for draft in drafts:
        if draft.key in seen_keys:
            continue
        seen_keys.add(draft.key)
        result.append(
            ReviewCase(
                id=f"case-{next_id}",
                key=draft.key,
                provider=draft.provider,
                paymentId=draft.paymentId,
                kind=draft.kind,
                detail=draft.detail,
                status=CaseStatus.Open,
                createdAt=createdAt,
                resolvedAt=None,
                resolution=None,
                priority=derivePriority(draft.kind, draft.amount),
            )
        )
        next_id += 1
    return result


def resolveCase(
    cases: list[ReviewCase],
    caseId: str,
    resolution: str,
    resolvedAt: str,
    priority: CasePriority | None = None,
) -> list[ReviewCase]:
    """Mark one case as resolved.

    Operators may optionally pass a `priority` override that captures the
    severity at the moment of resolution (for example when they discover a
    low-priority case actually masked a critical issue). When omitted the
    priority remains as derived at materialization time.
    """
    if not _hasCaseId(cases, caseId):
        raise ValueError("Unknown case: " + caseId)
    return [
        _resolveOne(item, resolution, resolvedAt, priority)
        if item.id == caseId
        else item
        for item in cases
    ]


def _resolveOne(
    item: ReviewCase,
    resolution: str,
    resolvedAt: str,
    priority: CasePriority | None,
) -> ReviewCase:
    """Apply a resolution transition to one case, optionally overriding priority."""
    if priority is None:
        return replace(
            item,
            status=CaseStatus.Resolved,
            resolvedAt=resolvedAt,
            resolution=resolution,
        )
    return replace(
        item,
        status=CaseStatus.Resolved,
        resolvedAt=resolvedAt,
        resolution=resolution,
        priority=priority,
    )


def reprioritizeCase(
    cases: list[ReviewCase], caseId: str, priority: CasePriority
) -> list[ReviewCase]:
    """Let an operator override priority on an existing case.

    Keeps status and audit timestamps intact; the override is visible via the
    returned case and any subsequent audit entries.
    """
    if not _hasCaseId(cases, caseId):
        raise ValueError("Unknown case: " + caseId)
    return [
        replace(item, priority=priority) if item.id == caseId else item
        for item in cases
    ]


def _hasCaseId(cases: list[ReviewCase], caseId: str) -> bool:
    """Exact lookup by case ID."""
    return any(item.id == caseId for item in cases)


# ---------------------------------------------------------------------------
# Filtering and lookup views.
# ---------------------------------------------------------------------------


def filterByStatusLabel(
    cases: list[ReviewCase], status: str | None
) -> list[ReviewCase]:
    """Support list_cases open|resolved|all with open as the default."""
    if status is None:
        return _filterOpen(cases)
    label = status.lower()
    if label == "open":
        return _filterOpen(cases)
    if label == "resolved":
        return _filterResolved(cases)
    if label == "all":
        return list(cases)
    raise ValueError("Case filter must be one of: open, resolved, all")


def _filterOpen(cases: list[ReviewCase]) -> list[ReviewCase]:
    """Keep only open cases."""
    return [item for item in cases if item.status is CaseStatus.Open]


def _filterResolved(cases: list[ReviewCase]) -> list[ReviewCase]:
    """Keep only resolved cases."""
    return [item for item in cases if item.status is CaseStatus.Resolved]


def filterByPriority(
    cases: list[ReviewCase], priority: CasePriority
) -> list[ReviewCase]:
    """Keep only cases at the given priority level."""
    return [item for item in cases if item.priority is priority]


def sortByPriority(cases: list[ReviewCase]) -> list[ReviewCase]:
    """Sort cases most-urgent first while preserving input order inside a band."""
    rank = {
        CasePriority.Urgent: 0,
        CasePriority.High: 1,
        CasePriority.Normal: 2,
        CasePriority.Low: 3,
    }
    return sorted(cases, key=lambda item: rank[item.priority])


def openCasesForPayment(
    cases: list[ReviewCase], paymentId: str
) -> list[ReviewCase]:
    """Keep only open cases for one payment."""
    return [
        item
        for item in cases
        if item.paymentId == paymentId and item.status is CaseStatus.Open
    ]


def findCaseById(cases: list[ReviewCase], caseId: str) -> ReviewCase | None:
    """Convenience lookup after mutation."""
    for item in cases:
        if item.id == caseId:
            return item
    return None


# ---------------------------------------------------------------------------
# Audit trail.
# ---------------------------------------------------------------------------


def auditForOpenedCase(item: ReviewCase) -> AuditEntry:
    """Stable audit entry for new manual-review work.

    Priority is encoded both as a structured field (for programmatic filters)
    and as a suffix in the message text (for human-readable exports).
    """
    return AuditEntry(
        key=f"case-opened:{item.key}",
        subjectId=f"payment:{item.paymentId}",
        action="case.opened",
        message=f"[{priorityText(item.priority)}] [{item.kind}] {item.detail}",
        createdAt=item.createdAt,
        priority=item.priority,
    )


def auditForResolvedCase(item: ReviewCase) -> AuditEntry:
    """Stable audit entry for manual resolution, preserving the priority."""
    resolutionText = item.resolution if item.resolution is not None else "resolved"
    return AuditEntry(
        key=f"case-resolved:{item.key}",
        subjectId=f"payment:{item.paymentId}",
        action="case.resolved",
        message=f"[{priorityText(item.priority)}] {resolutionText}",
        createdAt=item.resolvedAt if item.resolvedAt is not None else item.createdAt,
        priority=item.priority,
    )


def auditForReprioritizedCase(item: ReviewCase, at: str) -> AuditEntry:
    """Audit trail entry for an operator-driven priority change."""
    return AuditEntry(
        key=f"case-reprioritized:{item.key}",
        subjectId=f"payment:{item.paymentId}",
        action="case.reprioritized",
        message=f"[{priorityText(item.priority)}] priority updated",
        createdAt=at,
        priority=item.priority,
    )
