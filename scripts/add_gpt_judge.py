#!/usr/bin/env python3
"""Add GPT-4o as a 4th judge to an existing completed JSONL run.

Reuses diffs + guesses from a previous run (already scored by Claude ensemble).
Calls GPT only, appends its score to each slice's individual map, recomputes
median/mean/spread over the new 4-model set, writes a fresh JSONL.

Usage:
    python scripts/add_gpt_judge.py results/ensemble_YYYYMMDD_HHMMSS/slices.jsonl
    # or: latest completed run with SUCCESS marker if no arg
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from intent_trace.judges import (
    judge_prompt_axis,
    judge_diff_axis,
    judge_quality_axis,
)

GPT_MODEL = os.environ.get("INTENT_TRACE_JUDGE_GPT", "gpt-4o")


def find_latest_complete() -> Path:
    cands = sorted(
        (p for p in Path("results").glob("ensemble_*/SUCCESS")),
        key=lambda p: p.stat().st_mtime,
    )
    if not cands:
        raise SystemExit("no completed runs with SUCCESS marker")
    return cands[-1].parent / "slices.jsonl"


def merge_judgment(existing: dict, gpt_result: dict, key: str) -> dict:
    """Append GPT score under `key` to an existing judgment dict, recompute aggregates."""
    indiv = dict(existing.get("individual", {}))
    indiv[key] = gpt_result
    scores = [v["score"] for v in indiv.values() if v.get("score", -1) >= 0]
    out = dict(existing)
    out["individual"] = indiv
    if scores:
        out["median"] = round(statistics.median(scores), 1)
        out["mean"] = round(statistics.mean(scores), 2)
        out["spread"] = max(scores) - min(scores)
    return out


def load_prompt_text(program: str, prompt_name: str) -> str:
    return (Path("prompts") / program / f"{prompt_name}.md").read_text()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="slices.jsonl (default: latest completed)")
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    src = Path(args.input) if args.input else find_latest_complete()
    print(f"Source:  {src}")
    print(f"Judge:   {GPT_MODEL}")

    rows = []
    for line in src.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    print(f"Slices:  {len(rows)}")

    run_dir = Path("results") / f"gpt_added_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "slices.jsonl"
    print(f"Output:  {out_path}\n")

    # Cache quality judgments by (program, prompt, lang) since they reuse across views.
    quality_cache: dict[tuple, dict] = {}

    for i, r in enumerate(rows, 1):
        key = f"{r.get('program','?')}/{r.get('prompt','?')}/{r.get('lang','?')}/{r.get('view','?')}"
        print(f"  [{i}/{len(rows)}] {key}", flush=True)
        try:
            prompt_text = load_prompt_text(r["program"], r["prompt"])

            # prompt-axis + diff-axis per slice
            if "judgment_prompt" in r and "guess" in r:
                gp = judge_prompt_axis(None, GPT_MODEL, prompt_text, r["guess"]["intent"])
                r["judgment_prompt"] = merge_judgment(r["judgment_prompt"], gp, GPT_MODEL)
            if "judgment_diff" in r and "guess" in r and "diff" in r:
                gd = judge_diff_axis(None, GPT_MODEL, r["diff"], r["guess"]["intent"])
                r["judgment_diff"] = merge_judgment(r["judgment_diff"], gd, GPT_MODEL)

            # quality-axis: same per (program, prompt, lang); judge once
            qk = (r["program"], r["prompt"], r["lang"])
            if "judgment_quality" in r and "diff" in r:
                if qk not in quality_cache:
                    quality_cache[qk] = judge_quality_axis(None, GPT_MODEL, prompt_text, r["diff"])
                r["judgment_quality"] = merge_judgment(r["judgment_quality"], quality_cache[qk], GPT_MODEL)
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}", flush=True)

        with out_path.open("a") as f:
            f.write(json.dumps(r) + "\n")

    (run_dir / "SUCCESS").touch()
    print(f"\nDone. {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
