"""Shared loader for results/merged/<reader>.jsonl.

Handles the masked_spec quirk: `mask_for_view` in rerun_all.py applies the
same mask to python_from_aver and python_oop for both `masked` and
`masked_spec` (verify ablation only meaningful for aver). So pfa/oop
masked_spec rows are independent samples of the SAME masked input — we
treat them as additional N for the `masked` bucket (effective N=36
instead of N=18, noise floor √2 lower).

For aver: masked_spec = verify ablation → keep as separate view.
"""
from __future__ import annotations

import json
from pathlib import Path


def canonical_view(lang: str, view: str) -> str | None:
    """Map raw view → canonical view name; None drops the row.

    pfa/oop: masked_spec is identical input to masked → alias to 'masked'.
    aver: masked_spec preserves verify blocks → distinct view.
    """
    if view == "masked_spec" and lang in ("python_from_aver", "python_oop"):
        return "masked"
    return view


def canonical_load(path: Path,
                   keep_views: tuple[str, ...] = ("full", "masked", "masked_spec")
                   ) -> list[dict]:
    """Load rows with view names canonicalized. Drops views not in keep_views."""
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        cv = canonical_view(r.get("lang", ""), r.get("view", ""))
        if cv is None or cv not in keep_views:
            continue
        r2 = dict(r)
        r2["view"] = cv
        out.append(r2)
    return out
