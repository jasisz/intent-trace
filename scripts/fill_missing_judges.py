#!/usr/bin/env python3
"""Fill missing GPT judges (gpt-4o, gpt-4.1) on merged per-reader JSONL.

Reads results/merged/<reader>.jsonl, for each slice that's missing gpt-4o
or gpt-4.1 in judgment_prompt/judgment_diff.individual, calls only the
missing judges and updates the row. Writes back in place (atomic via tmp).

Usage:
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

NEEDED = ("gpt-4o", "gpt-4.1")
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


def fill_file(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    prompt_rows = [r for r in rows
                   if r.get("view") in ("full", "masked")
                   and r.get("judgment_prompt", {}).get("individual")]
    todo = []
    for r in prompt_rows:
        p_ind = r["judgment_prompt"].get("individual", {})
        d_ind = r["judgment_diff"].get("individual", {})
        missing = set()
        for j in NEEDED:
            if j not in p_ind or j not in d_ind:
                missing.add(j)
        if missing:
            todo.append((r, sorted(missing)))
    print(f"{path.name}: {len(todo)} slices need fill", flush=True)
    if not todo:
        return

    for i, (r, missing) in enumerate(todo, 1):
        key = f"{r['program']}/{r['prompt']}/{r['lang']}/{r['view']}"
        print(f"  [{i}/{len(todo)}] {key}  missing={missing}", flush=True)
        try:
            prompt_text = load_prompt_text(r["program"], r["prompt"])
            for judge in missing:
                p_ind = r["judgment_prompt"].setdefault("individual", {})
                d_ind = r["judgment_diff"].setdefault("individual", {})
                if judge not in p_ind:
                    gp = judge_prompt_axis(None, judge, prompt_text, r["guess"]["intent"])
                    p_ind[judge] = gp
                if judge not in d_ind:
                    gd = judge_diff_axis(None, judge, r["diff"], r["guess"]["intent"])
                    d_ind[judge] = gd
            r["judgment_prompt"] = recompute(r["judgment_prompt"])
            r["judgment_diff"] = recompute(r["judgment_diff"])
        except Exception as e:
            print(f"    ERROR {type(e).__name__}: {e}", flush=True)

    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)
    print(f"{path.name}: written back", flush=True)


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set", file=sys.stderr)
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
