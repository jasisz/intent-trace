"""Manual-review case generation and lifecycle management.

Dirty payment operations need stable case keys so re-running ingest or
reconciliation does not keep opening the same human task forever. The
``CaseRegistry`` owns the mutable list of review cases and exposes the few
operations the backoffice cares about; ``CaseDraftBuilder`` (via class
methods on ``CaseDraft``) turns replay anomalies into review drafts.
"""

from __future__ import annotations

from models import (
    CaseDraft,
    CaseStatus,
    PaymentState,
    ReviewCase,
    UnknownCaseError,
    UnknownCaseFilterError,
)


class CaseDraftFactory:
    """Builds ``CaseDraft`` instances from replay anomalies.

    Anomaly keys use stable prefixes, so the factory can derive the
    user-facing case kind directly from the key. Keys and notes are
    zipped by position — if notes are shorter than keys, the remaining
    cases fall back to a generic message.
    """

    _KIND_PREFIXES: tuple[tuple[str, str], ...] = (
        ("refund-before-capture:", "refund_before_capture"),
        ("capture-without-authorize:", "capture_without_authorize"),
        ("captured-exceeds-authorized:", "captured_exceeds_authorized"),
        ("refund-exceeds-capture:", "refund_exceeds_capture"),
        ("currency-mismatch:", "currency_mismatch"),
    )
    _FALLBACK_KIND = "payment_integrity"
    _FALLBACK_NOTE = "Suspicious payment history"

    @classmethod
    def kind_from_key(cls, key: str) -> str:
        for prefix, kind in cls._KIND_PREFIXES:
            if key.startswith(prefix):
                return kind
        return cls._FALLBACK_KIND

    @classmethod
    def make(
        cls,
        provider: str,
        payment_id: str,
        kind: str,
        detail: str,
        key: str,
    ) -> CaseDraft:
        return CaseDraft(
            key=key,
            provider=provider,
            payment_id=payment_id,
            kind=kind,
            detail=detail,
        )

    @classmethod
    def drafts_from_state(cls, state: PaymentState) -> list[CaseDraft]:
        """Turn replay anomalies from one state into manual-review drafts."""
        drafts: list[CaseDraft] = []
        remaining_notes = list(state.anomaly_notes)
        for key in state.anomaly_keys:
            note = remaining_notes[0] if remaining_notes else cls._FALLBACK_NOTE
            drafts.append(
                cls.make(
                    state.provider,
                    state.payment_id,
                    cls.kind_from_key(key),
                    note,
                    key,
                )
            )
            if remaining_notes:
                remaining_notes = remaining_notes[1:]
        return drafts

    @classmethod
    def drafts_from_states(cls, states: list[PaymentState]) -> list[CaseDraft]:
        drafts: list[CaseDraft] = []
        for state in states:
            drafts.extend(cls.drafts_from_state(state))
        return drafts


class CaseRegistry:
    """Owns the mutable list of manual-review cases.

    The registry deduplicates by case key so re-running ingest or
    reconciliation does not keep opening the same human task forever.
    """

    def __init__(self, cases: list[ReviewCase] | None = None) -> None:
        self._cases: list[ReviewCase] = list(cases) if cases else []

    @property
    def cases(self) -> list[ReviewCase]:
        return self._cases

    def __len__(self) -> int:
        return len(self._cases)

    def __iter__(self):
        return iter(self._cases)

    def find_by_id(self, case_id: str) -> ReviewCase | None:
        for case in self._cases:
            if case.id == case_id:
                return case
        return None

    def open_for_payment(self, payment_id: str) -> list[ReviewCase]:
        return [
            case
            for case in self._cases
            if case.payment_id == payment_id and case.is_open
        ]

    def filter(self, status: str | None) -> list[ReviewCase]:
        """Support list_cases open|resolved|all with open as the default."""
        if status is None:
            return [case for case in self._cases if case.is_open]
        label = status.lower()
        if label == "open":
            return [case for case in self._cases if case.is_open]
        if label == "resolved":
            return [case for case in self._cases if case.is_resolved]
        if label == "all":
            return list(self._cases)
        raise UnknownCaseFilterError("Case filter must be one of: open, resolved, all")

    def open_cases(self, drafts: list[CaseDraft], created_at: str) -> list[ReviewCase]:
        """Materialize new review cases from drafts, skipping duplicates by key.

        Returns the list of cases that were actually added (in insertion order)
        so callers can turn them into audit entries without re-walking the
        full registry.
        """
        seen_keys = {case.key for case in self._cases}
        added: list[ReviewCase] = []
        next_id = len(self._cases) + 1
        for draft in drafts:
            if draft.key in seen_keys:
                continue
            seen_keys.add(draft.key)
            case = ReviewCase(
                id=f"case-{next_id}",
                key=draft.key,
                provider=draft.provider,
                payment_id=draft.payment_id,
                kind=draft.kind,
                detail=draft.detail,
                status=CaseStatus.Open,
                created_at=created_at,
                resolved_at=None,
                resolution=None,
            )
            self._cases.append(case)
            added.append(case)
            next_id += 1
        return added

    def resolve(self, case_id: str, resolution: str, resolved_at: str) -> ReviewCase:
        case = self.find_by_id(case_id)
        if case is None:
            raise UnknownCaseError("Unknown case: " + case_id)
        case.resolve(resolution, resolved_at)
        return case


__all__ = ["CaseDraftFactory", "CaseRegistry"]
