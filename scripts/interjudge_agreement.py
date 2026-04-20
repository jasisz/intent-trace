#!/usr/bin/env python3
"""Inter-judge agreement on the existing 6-judge ensemble.

For each (reader, axis), build a judges × items matrix of individual scores
and compute:
  - Krippendorff's α (ordinal) — primary reliability metric
  - Krippendorff's α (interval) — secondary, treats 0–10 as a continuous scale
  - mean pairwise Spearman correlation across the 10 judge pairs — sanity check

Guideline for α on an 11-point ordinal rubric: ≥0.80 is high agreement,
0.67–0.80 is acceptable for tentative conclusions, <0.67 means the rubric
itself is not reliable enough to draw conclusions from.

No API calls. Reads results/merged/{sonnet,gpt4.1,gemini}.jsonl.
"""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import krippendorff
import numpy as np

READERS = {
    "Sonnet":  "results/merged/sonnet.jsonl",
    "Opus":    "results/merged/opus.jsonl",
    "gpt-4.1": "results/merged/gpt4.1.jsonl",
    "Gemini":  "results/merged/gemini.jsonl",
    "Kimi K2": "results/merged/kimi.jsonl",
}
JUDGES_PD = ["opus", "sonnet", "haiku", "gpt-4o", "gpt-4.1", "kimi"]
JUDGES_Q  = ["opus", "sonnet", "haiku"]
AXES_PD = [
    ("judgment_prompt", "P-axis (prompt match)"),
    ("judgment_diff",   "D-axis (diff fidelity)"),
]


def build_matrix(path: str, axis_key: str, judges: list[str],
                 views: tuple[str, ...]) -> np.ndarray:
    """Return a (len(judges), n_items) float matrix; NaN for missing scores."""
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("view") in views and r.get(axis_key, {}).get("individual")]
    matrix = np.full((len(judges), len(rows)), np.nan)
    for j, judge in enumerate(judges):
        for i, r in enumerate(rows):
            sc = r[axis_key]["individual"].get(judge, {}).get("score", -1)
            if sc is not None and sc >= 0:
                matrix[j, i] = sc
    return matrix


def pairwise_spearman(m: np.ndarray) -> float:
    """Mean Spearman rank correlation across all judge pairs, NaN-tolerant."""
    from scipy.stats import spearmanr
    n_judges = m.shape[0]
    rs = []
    for a, b in combinations(range(n_judges), 2):
        mask = ~(np.isnan(m[a]) | np.isnan(m[b]))
        if mask.sum() < 3:
            continue
        r, _ = spearmanr(m[a][mask], m[b][mask])
        if not np.isnan(r):
            rs.append(r)
    return float(np.mean(rs)) if rs else float("nan")


def fmt(v: float) -> str:
    return f"{v:+.3f}" if not np.isnan(v) else "  nan"


def main() -> None:
    print("\n=== Inter-judge agreement (prompt/diff axes, 6-judge panel) ===\n")
    print(f'{"reader":<10} {"axis":<24} {"n_items":>8} {"α_ord":>8} {"α_int":>8} {"ρ̄ pairs":>10}')
    for reader, path in READERS.items():
        for axis_key, axis_label in AXES_PD:
            m = build_matrix(path, axis_key, JUDGES_PD, ("full", "masked", "masked_spec"))
            n_items = m.shape[1]
            a_ord = krippendorff.alpha(reliability_data=m, level_of_measurement="ordinal")
            a_int = krippendorff.alpha(reliability_data=m, level_of_measurement="interval")
            rho = pairwise_spearman(m)
            print(f'{reader:<10} {axis_label:<24} {n_items:>8d} {fmt(a_ord):>8} {fmt(a_int):>8} {fmt(rho):>10}')

    # Pooled across all 3 readers — gives the headline number
    print("\n=== Pooled across readers (P and D combined per axis) ===\n")
    print(f'{"axis":<24} {"n_items":>8} {"α_ord":>8} {"α_int":>8}')
    for axis_key, axis_label in AXES_PD:
        cols = []
        for path in READERS.values():
            m = build_matrix(path, axis_key, JUDGES_PD, ("full", "masked", "masked_spec"))
            cols.append(m)
        pooled = np.concatenate(cols, axis=1)
        a_ord = krippendorff.alpha(reliability_data=pooled, level_of_measurement="ordinal")
        a_int = krippendorff.alpha(reliability_data=pooled, level_of_measurement="interval")
        print(f'{axis_label:<24} {pooled.shape[1]:>8d} {fmt(a_ord):>8} {fmt(a_int):>8}')


if __name__ == "__main__":
    main()
