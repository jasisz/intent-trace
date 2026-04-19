#!/usr/bin/env python3
"""Post-run sanity check: every scored row has all 5 judges on both axes.

Exits non-zero (and prints per-row details) if any row is missing a judge
or has a failed (-1) score. Use this after any rerun_all / fill_* to
confirm merged/ state is clean before publishing.

Usage: python scripts/coverage_check.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

JUDGES = ("opus", "sonnet", "haiku", "gpt-4o", "gpt-4.1")
VIEWS = ("full", "masked", "masked_spec", "no_verify")
MERGED = Path("results/merged")


def check_reader(path: Path) -> tuple[int, int, Counter]:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    scored = [r for r in rows if r.get("view") in VIEWS
              and r.get("judgment_prompt", {}).get("individual") is not None]
    complete = 0
    missing = Counter()
    for r in scored:
        p_ind = r["judgment_prompt"].get("individual", {})
        d_ind = r["judgment_diff"].get("individual", {})
        row_ok = True
        for j in JUDGES:
            for axis_name, ind in (("P", p_ind), ("D", d_ind)):
                entry = ind.get(j)
                if entry is None or entry.get("score", -1) < 0:
                    missing[(j, axis_name)] += 1
                    row_ok = False
        if row_ok:
            complete += 1
    return len(scored), complete, missing


def main() -> int:
    any_bad = False
    print(f'{"reader":<10} {"scored":>8} {"complete":>10} {"coverage":>10}')
    for path in sorted(MERGED.glob("*.jsonl")):
        name = path.stem
        total, complete, missing = check_reader(path)
        coverage = 100 * complete / total if total else 0
        print(f'{name:<10} {total:>8} {complete:>10} {coverage:>9.1f}%')
        if missing:
            any_bad = True
            for (j, axis), n in sorted(missing.items()):
                print(f'    missing {j} {axis}-axis: {n} rows')
    if any_bad:
        print("\nFAIL: some rows have missing judges. Run fill_missing_judges.py")
        return 1
    print("\nOK: all rows have all 5 judges on both axes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
