"""Manual-review case generation and lifecycle management.

Dirty payment operations need stable case keys so re-running ingest or
reconciliation does not keep opening the same human task forever. The
``CaseRegistry`` owns the mutable list of review cases and exposes the few
operations the backoffice cares about; ``CaseDraftFactory`` turns replay
anomalies into review drafts. ``PriorityPolicy`` derives an initial
priority from the case kind and amount so operators can triage without
reading every detail.
"""

from __future__ import annotations

from .models import (
    CaseDraft,
    CasePriority,
    CaseStatus,
    DEFAULT_PRIORITY,
    PaymentState,
    ReviewCase,
    UnknownCaseError,
    UnknownCaseFilterError,
    UnknownCasePriorityError,
)


class PriorityPolicy:
    """Maps ``(kind, amount)`` pairs to an initial :class:`CasePriority`.

    Severity starts from a kind-specific base tier; large amounts then
    promote the result so a $500k reconciliation break eclipses a routine
    $50 mismatch. Amounts use the same minor-unit convention as the rest
    of the ledger (integer cents).
    """

    _URGENT_KINDS: frozenset[str] = frozenset(
        {
            "captured_exceeds_authorized",
            "refund_exceeds_capture",
            "currency_mismatch",
            "settlement_currency_mismatch",
            "settlement_without_realtime",
            "payment_integrity",
        }
    )
    _HIGH_KINDS: frozenset[str] = frozenset(
        {
            "capture_without_authorize",
            "refund_before_capture",
            "settlement_without_capture",
            "realtime_missing_settlement",
            "settlement_without_refund",
            "refund_missing_settlement",
        }
    )
    _NORMAL_KINDS: frozenset[str] = frozenset(
        {
            "settlement_capture_mismatch",
            "settlement_refund_mismatch",
        }
    )

    # Amount thresholds in minor units (cents). A $1k mismatch bumps one
    # tier; a $100k mismatch is Urgent regardless of base kind.
    _LARGE_AMOUNT_CENTS: int = 10_000_000
    _MEDIUM_AMOUNT_CENTS: int = 100_000
    _TINY_AMOUNT_CENTS: int = 10_000

    @classmethod
    def base_for_kind(cls, kind: str) -> CasePriority:
        """Initial priority tier implied by the case kind alone."""
        if kind in cls._URGENT_KINDS:
            return CasePriority.Urgent
        if kind in cls._HIGH_KINDS:
            return CasePriority.High
        if kind in cls._NORMAL_KINDS:
            return CasePriority.Normal
        return DEFAULT_PRIORITY

    @classmethod
    def derive(cls, kind: str, amount: int) -> CasePriority:
        """Combine kind severity with amount magnitude into one priority."""
        base = cls.base_for_kind(kind)
        if amount >= cls._LARGE_AMOUNT_CENTS:
            return CasePriority.Urgent
        if amount >= cls._MEDIUM_AMOUNT_CENTS:
            return base.bumped_up(1)
        if amount < cls._TINY_AMOUNT_CENTS and amount > 0:
            if base.rank > CasePriority.Normal.rank:
                return CasePriority.Normal
        return base


class CaseDraftFactory:
    """Builds ``CaseDraft`` instances from replay anomalies.

    Anomaly keys use stable prefixes, so the factory can derive the
    user-facing case kind directly from the key. Keys and notes are
    zipped by position; priority is attached by routing every draft
    through :class:`PriorityPolicy`.
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
        """Normalize anomaly key prefixes into user-facing case kinds."""
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
        amount: int = 0,
        priority: CasePriority | None = None,
    ) -> CaseDraft:
        """Convenience constructor for reconcile and replay anomalies.

        Passing ``priority=None`` derives one from ``(kind, amount)`` via
        :class:`PriorityPolicy`; an explicit value is honored verbatim.
        """
        resolved_priority = (
            priority if priority is not None else PriorityPolicy.derive(kind, amount)
        )
        return CaseDraft(
            key=key,
            provider=provider,
            payment_id=payment_id,
            kind=kind,
            detail=detail,
            amount=amount,
            priority=resolved_priority,
        )

    @classmethod
    def _amount_for_state(cls, state: PaymentState) -> int:
        """Pick the most meaningful single number out of a replayed state."""
        return max(
            state.captured_amount,
            state.refunded_amount,
            state.authorized_amount,
        )

    @classmethod
    def drafts_from_state(cls, state: PaymentState) -> list[CaseDraft]:
        """Turn replay anomalies from one state into manual-review drafts."""
        drafts: list[CaseDraft] = []
        remaining_notes = list(state.anomaly_notes)
        amount = cls._amount_for_state(state)
        for key in state.anomaly_keys:
            note = remaining_notes[0] if remaining_notes else cls._FALLBACK_NOTE
            drafts.append(
                cls.make(
                    state.provider,
                    state.payment_id,
                    cls.kind_from_key(key),
                    note,
                    key,
                    amount,
                )
            )
            if remaining_notes:
                remaining_notes = remaining_notes[1:]
        return drafts

    @classmethod
    def drafts_from_states(cls, states: list[PaymentState]) -> list[CaseDraft]:
        """Flatten replay anomalies from every payment state."""
        drafts: list[CaseDraft] = []
        for state in states:
            drafts.extend(cls.drafts_from_state(state))
        return drafts


class CaseRegistry:
    """Owns the mutable list of manual-review cases.

    The registry deduplicates by case key so re-running ingest or
    reconciliation does not keep opening the same human task forever.
    Priority lives on each :class:`ReviewCase` and is settable via
    :meth:`reprioritize` or on resolution.
    """

    def __init__(self, cases: list[ReviewCase] | None = None) -> None:
        self._cases: list[ReviewCase] = list(cases) if cases else []

    # ------------------------------------------------------------------
    # Read-only views.
    # ------------------------------------------------------------------

    @property
    def cases(self) -> list[ReviewCase]:
        """All cases in insertion order. Mutation is intentional: callers may
        treat this as the live list."""
        return self._cases

    def __len__(self) -> int:
        return len(self._cases)

    def __iter__(self):
        return iter(self._cases)

    def find_by_id(self, case_id: str) -> ReviewCase | None:
        """Convenience lookup after mutation."""
        for case in self._cases:
            if case.id == case_id:
                return case
        return None

    def open_for_payment(self, payment_id: str) -> list[ReviewCase]:
        """Keep only open cases for one payment."""
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

    def filter_by_priority(self, priority: CasePriority) -> list[ReviewCase]:
        """Filter every stored case (open or resolved) by one priority tier."""
        return [case for case in self._cases if case.priority is priority]

    def sorted_by_priority(
        self, status: str | None = None, descending: bool = True
    ) -> list[ReviewCase]:
        """Return cases ordered by priority rank, keeping insertion order as tiebreaker.

        Consumers that just want ``[urgent..low, urgent..low]`` across the
        whole list can call this with no arguments.
        """
        subset = self.filter(status) if status is not None else list(self._cases)
        sign = -1 if descending else 1
        return sorted(subset, key=lambda case: sign * case.priority.rank)

    # ------------------------------------------------------------------
    # Mutations.
    # ------------------------------------------------------------------

    def open_cases(self, drafts: list[CaseDraft], created_at: str) -> list[ReviewCase]:
        """Materialize new review cases from drafts, skipping duplicates by key.

        Returns the list of cases that were actually added (in insertion order)
        so callers can turn them into audit entries without re-walking the
        full registry. Each case inherits the priority carried by its draft.
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
                priority=draft.priority,
                created_at=created_at,
                resolved_at=None,
                resolution=None,
            )
            self._cases.append(case)
            added.append(case)
            next_id += 1
        return added

    def resolve(
        self,
        case_id: str,
        resolution: str,
        resolved_at: str,
        priority: CasePriority | None = None,
    ) -> ReviewCase:
        """Mark one case as resolved; optional ``priority`` finalizes the tier."""
        case = self.find_by_id(case_id)
        if case is None:
            raise UnknownCaseError("Unknown case: " + case_id)
        case.resolve(resolution, resolved_at, priority)
        return case

    def reprioritize(self, case_id: str, priority: CasePriority) -> ReviewCase:
        """Operator override outside the resolve flow (e.g. during triage)."""
        case = self.find_by_id(case_id)
        if case is None:
            raise UnknownCaseError("Unknown case: " + case_id)
        if not isinstance(priority, CasePriority):
            raise UnknownCasePriorityError(f"Unknown priority: {priority!r}")
        case.reprioritize(priority)
        return case


__all__ = ["CaseDraftFactory", "CaseRegistry", "PriorityPolicy"]
