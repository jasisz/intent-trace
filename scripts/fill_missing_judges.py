#!/usr/bin/env python3
"""Fill any missing / failed judge scores on merged per-reader JSONL.

Reads results/merged/<reader>.jsonl, and for each scored row (views full /
masked / masked_spec / no_verify), retries any of the 6 judges whose score
is missing or -1 (failure). Only re-calls the judges that need it — does
not re-run LLM-B or re-ensemble from scratch.

Atomic write via tmp file.

Usage:
    python scripts/fill_missing_judges.py sonnet
    python scripts/fill_missing_judges.py gpt4.1
    python scripts/fill_missing_judges.py gemini
    python scripts/fill_missing_judges.py all
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

from intent_trace.judges import judge_prompt_axis, judge_diff_axis

JUDGES = {
    "opus":    "claude-opus-4-7",
    "sonnet":  "claude-sonnet-4-6",
    "haiku":   "claude-haiku-4-5-20251001",
    "gpt-4o":  "gpt-4o",
    "gpt-4.1": "gpt-4.1",
    "kimi":    "kimi-k2-0905-preview",
}
VIEWS = ("full", "masked", "masked_spec", "no_verify")
MERGED = Path("results/merged")


def recompute(axis: dict) -> dict:
    ind = axis.get("individual", {})
    scores = [v["score"] for v in ind.values() if v.get("score", -1) >= 0]
    out = dict(axis)
    if scores:
        out["median"] = round(statistics.median(scores), 1)
        out["mean"] = round(statistics.mean(scores), 2)
        out["spread"] = max(scores) - min(scores)
    return out


def load_prompt_text(program: str, prompt_name: str) -> str:
    return (Path("prompts") / program / f"{prompt_name}.md").read_text()


def needs_fill(axis_ind: dict, judge: str) -> bool:
    """True if this judge is missing from the axis OR its score is -1 (failed)."""
    entry = axis_ind.get(judge)
    if entry is None:
        return True
    return entry.get("score", -1) < 0


def fill_file(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    scored_rows = [r for r in rows
                   if r.get("view") in VIEWS
                   and r.get("judgment_prompt", {}).get("individual") is not None]
    todo = []
    for r in scored_rows:
        p_ind = r["judgment_prompt"].get("individual", {})
        d_ind = r["judgment_diff"].get("individual", {})
        missing_p = [j for j in JUDGES if needs_fill(p_ind, j)]
        missing_d = [j for j in JUDGES if needs_fill(d_ind, j)]
        if missing_p or missing_d:
            todo.append((r, missing_p, missing_d))

    print(f"{path.name}: {len(todo)} rows need fill (of {len(scored_rows)} scored rows)", flush=True)
    if not todo:
        return

    for i, (r, missing_p, missing_d) in enumerate(todo, 1):
        key = f"{r['program']}/{r['prompt']}/{r['lang']}/{r['view']}"
        print(f"  [{i}/{len(todo)}] {key}  P={missing_p}  D={missing_d}", flush=True)
        prompt_text = None
        for judge in missing_p:
            try:
                if prompt_text is None:
                    prompt_text = load_prompt_text(r["program"], r["prompt"])
                model_id = JUDGES[judge]
                gp = judge_prompt_axis(None, model_id, prompt_text, r["guess"]["intent"])
                r["judgment_prompt"].setdefault("individual", {})[judge] = gp
            except Exception as e:
                print(f"    ERROR P/{judge}: {type(e).__name__}: {e}", flush=True)
        for judge in missing_d:
            try:
                model_id = JUDGES[judge]
                gd = judge_diff_axis(None, model_id, r["diff"], r["guess"]["intent"])
                r["judgment_diff"].setdefault("individual", {})[judge] = gd
            except Exception as e:
                print(f"    ERROR D/{judge}: {type(e).__name__}: {e}", flush=True)
        r["judgment_prompt"] = recompute(r["judgment_prompt"])
        r["judgment_diff"] = recompute(r["judgment_diff"])

        # Write incrementally so a mid-run crash preserves progress
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        tmp.replace(path)

    print(f"{path.name}: written back", flush=True)


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("need both OPENAI_API_KEY and ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all":
        for p in sorted(MERGED.glob("*.jsonl")):
            fill_file(p)
    else:
        fill_file(MERGED / f"{target}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
