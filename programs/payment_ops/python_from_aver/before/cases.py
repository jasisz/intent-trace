"""Manual-review case generation and lifecycle management.

Dirty payment operations need stable case keys so re-running ingest or reconciliation
does not keep opening the same human task forever.

Design decisions:

* Suspicious payment histories open an explicit manual-review case instead of
  being silently repaired or reduced to a log warning. Backoffice systems
  should not silently fix suspicious payment histories, and opening explicit
  manual-review cases keeps the risk visible and auditable. Alternatives
  considered: silent repair, log-only warnings.
"""

from __future__ import annotations

from dataclasses import replace

from .models import AuditEntry, CaseDraft, CaseStatus, PaymentState, ReviewCase


def makeCaseDraft(
    provider: str, paymentId: str, kind: str, detail: str, key: str
) -> CaseDraft:
    """Convenience constructor for reconcile and replay anomalies."""
    return CaseDraft(
        key=key, provider=provider, paymentId=paymentId, kind=kind, detail=detail
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
    return makeCaseDraft(state.provider, state.paymentId, _kindFromKey(key), note, key)


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


def materializeNewCases(
    existing: list[ReviewCase], drafts: list[CaseDraft], createdAt: str
) -> list[ReviewCase]:
    """Assign deterministic case IDs and skip already-known case keys."""
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
            )
        )
        next_id += 1
    return result


def resolveCase(
    cases: list[ReviewCase], caseId: str, resolution: str, resolvedAt: str
) -> list[ReviewCase]:
    """Mark one case as resolved."""
    if not _hasCaseId(cases, caseId):
        raise ValueError("Unknown case: " + caseId)
    return [
        replace(
            item,
            status=CaseStatus.Resolved,
            resolvedAt=resolvedAt,
            resolution=resolution,
        )
        if item.id == caseId
        else item
        for item in cases
    ]


def _hasCaseId(cases: list[ReviewCase], caseId: str) -> bool:
    """Exact lookup by case ID."""
    return any(item.id == caseId for item in cases)


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


def auditForOpenedCase(item: ReviewCase) -> AuditEntry:
    """Stable audit entry for new manual-review work."""
    return AuditEntry(
        key=f"case-opened:{item.key}",
        subjectId=f"payment:{item.paymentId}",
        action="case.opened",
        message=f"[{item.kind}] {item.detail}",
        createdAt=item.createdAt,
    )


def auditForResolvedCase(item: ReviewCase) -> AuditEntry:
    """Stable audit entry for manual resolution."""
    return AuditEntry(
        key=f"case-resolved:{item.key}",
        subjectId=f"payment:{item.paymentId}",
        action="case.resolved",
        message=item.resolution if item.resolution is not None else "resolved",
        createdAt=item.resolvedAt if item.resolvedAt is not None else item.createdAt,
    )
