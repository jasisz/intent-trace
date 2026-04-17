#!/usr/bin/env python3
"""Re-judge an existing results file with a second-axis judge (fidelity-to-diff).

Usage:
    python scripts/rejudge.py results/order_total__all__20260416_221555.json
    # or: latest 'all' file if no arg given
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from anthropic import Anthropic

MODEL_JUDGE_DIFF = os.environ.get("INTENT_TRACE_MODEL_JUDGE_DIFF", "claude-opus-4-7")

client = Anthropic()


def extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def judge_diff_fidelity(diff: str, guess_intent: str) -> dict:
    system = (
        "You are evaluating how faithfully a reviewer's guess describes the "
        "changes visible in a code diff. The reviewer saw only the diff and "
        "produced this one-sentence guess of what the author was trying to do. "
        "You do NOT have access to the author's original request. "
        "Score whether the guess captures what was ACTUALLY changed in the diff, "
        "independent of any task specification."
    )
    rubric = (
        "Rubric (fidelity to diff):\n"
        "  0 — guess does not describe the change visible in the diff\n"
        "  1 — captures part of the change, misses substantial elements\n"
        "  2 — captures most elements, misses one material detail OR includes one wrong detail\n"
        "  3 — faithfully describes all material elements of the change"
    )
    user = (
        f"Diff:\n{diff}\n\n"
        f"Reviewer's guess:\n{guess_intent}\n\n"
        f"{rubric}\n\n"
        "Respond with JSON only:\n"
        '{\n  "score": 0,\n  "reasoning": "one sentence"\n}'
    )
    r = client.messages.create(
        model=MODEL_JUDGE_DIFF,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


def latest_all_file() -> Path:
    candidates = sorted(
        Path("results").glob("*__all__*.json"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        raise SystemExit("no results/*__all__*.json files found")
    return candidates[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="results JSON (default: latest __all__)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    in_path = Path(args.input) if args.input else latest_all_file()
    data = json.loads(in_path.read_text())
    print(f"Source:      {in_path.name}")
    print(f"Diff judge:  {MODEL_JUDGE_DIFF}")
    print(f"Slices:      {len(data['results'])}")
    print()

    for i, r in enumerate(data["results"], 1):
        print(
            f"  [{i}/{len(data['results'])}] {r['prompt']} / {r['lang']} / {r['ablation']}..."
        )
        r["judgment_diff"] = judge_diff_fidelity(r["diff"], r["guess"]["intent"])

    data.setdefault("models", {})["judge_diff"] = MODEL_JUDGE_DIFF

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = in_path.with_name(in_path.stem + f"__rejudged_{ts}.json")
    out.write_text(json.dumps(data, indent=2))

    print()
    print(f"Saved: {out}\n")
    print("---")

    # Two-axis summary
    header = (
        f"{'prompt':<26} {'lang':<8} {'ablation':<8} "
        f"{'prompt':>7} {'diff':>6}  gap"
    )
    print(header)
    print("-" * len(header))
    for r in data["results"]:
        sp = r["judgment"]["score"]
        sd = r["judgment_diff"]["score"]
        gap = sd - sp
        gap_s = f"{gap:+d}" if gap != 0 else " 0"
        print(
            f"{r['prompt']:<26} {r['lang']:<8} {r['ablation']:<8} "
            f"{sp:>7} {sd:>6}  {gap_s}"
        )

    print()
    print("Legend: gap = diff_score - prompt_score.")
    print("  positive gap = reviewer caught MORE than the vague prompt asked for")
    print("               (author exceeded brief; diff carries extra intent)")
    print("  negative gap = reviewer restated the prompt but missed actual changes")
    print("              (bad review or sparse diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
