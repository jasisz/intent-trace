#!/usr/bin/env python3
"""Add third-axis judge (change_quality) to an existing results file.

Measures: did LLM-A correctly fulfill the prompt? Independent of whether
LLM-B could read it back. Separates structural-pressure gap from
prompt-misinterpretation noise.

Usage:
    python scripts/judge_quality.py results/...__rejudged_...json
    # or: latest __rejudged__ file if no arg given
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

MODEL_JUDGE_QUALITY = os.environ.get(
    "INTENT_TRACE_MODEL_JUDGE_QUALITY", "claude-opus-4-7"
)

client = Anthropic()


def extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def judge_change_quality(prompt: str, diff: str) -> dict:
    system = (
        "You evaluate whether a code change correctly fulfills a change request. "
        "You see the original request and the diff (before/after). "
        "Score whether the change ADDRESSES the request, independent of any "
        "reviewer's guess. Bonus additions beyond the request are fine as "
        "long as the core request is met."
    )
    rubric = (
        "Rubric (change quality):\n"
        "  0 — the diff goes in the opposite direction, or completely misses the request\n"
        "  1 — addresses something tangential; does not fulfill the core request\n"
        "  2 — partially fulfills the request; misses or misinterprets one important element\n"
        "  3 — correctly fulfills the request (extras beyond the request are ok)"
    )
    user = (
        f"Original change request:\n{prompt}\n\n"
        f"Actual diff:\n{diff}\n\n"
        f"{rubric}\n\n"
        "Respond with JSON only:\n"
        '{\n  "score": 0,\n  "reasoning": "one sentence"\n}'
    )
    r = client.messages.create(
        model=MODEL_JUDGE_QUALITY,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


def latest_rejudged() -> Path:
    candidates = sorted(
        Path("results").glob("*__rejudged_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise SystemExit("no results/*__rejudged_*.json files found")
    return candidates[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="rejudged JSON (default: latest)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    in_path = Path(args.input) if args.input else latest_rejudged()
    data = json.loads(in_path.read_text())
    print(f"Source:        {in_path.name}")
    print(f"Quality judge: {MODEL_JUDGE_QUALITY}")
    print(f"Slices:        {len(data['results'])}")
    print()

    # We need per-prompt text. Load prompts from disk by name.
    prompt_cache: dict[str, str] = {}

    def load_prompt(name: str) -> str:
        if name in prompt_cache:
            return prompt_cache[name]
        # search programs/<prog>/../prompts/<program>/<name>.md
        program = data.get("program", "")
        pf = Path("prompts") / program / f"{name}.md"
        prompt_cache[name] = pf.read_text()
        return prompt_cache[name]

    # change_quality depends only on (prompt, lang, ablation=full -> source, diff).
    # But ablation masking only hides narrative from LLM-B; LLM-A always saw
    # the full file. So change quality is a property of (prompt, lang), not
    # of ablation. We judge once per (prompt, lang) using the FULL diff,
    # and apply the same score to both ablation rows.
    seen: dict[tuple[str, str], dict] = {}
    for r in data["results"]:
        if r["ablation"] != "full":
            continue
        key = (r["prompt"], r["lang"])
        prompt_text = load_prompt(r["prompt"])
        print(f"  judging: {r['prompt']} / {r['lang']}...")
        seen[key] = judge_change_quality(prompt_text, r["diff"])

    for r in data["results"]:
        key = (r["prompt"], r["lang"])
        r["judgment_quality"] = seen[key]

    data.setdefault("models", {})["judge_quality"] = MODEL_JUDGE_QUALITY

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = in_path.with_name(in_path.stem + f"__quality_{ts}.json")
    out.write_text(json.dumps(data, indent=2))

    print()
    print(f"Saved: {out}\n")
    print("---")

    # Three-axis summary
    header = (
        f"{'prompt':<24} {'lang':<8} {'abl':<7} "
        f"{'prompt':>7} {'diff':>5} {'qual':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in data["results"]:
        sp = r["judgment"]["score"]
        sd = r["judgment_diff"]["score"]
        sq = r["judgment_quality"]["score"]
        print(
            f"{r['prompt']:<24} {r['lang']:<8} {r['ablation']:<7} "
            f"{sp:>7} {sd:>5} {sq:>5}"
        )

    print()
    print("Legend:")
    print("  prompt: LLM-B guess vs original request (rewards prompt-matching, not diff-matching)")
    print("  diff:   LLM-B guess vs what actually changed (rewards faithful diff reading)")
    print("  qual:   LLM-A change vs original request (rewards fulfilling the request)")
    print()
    print("  qual<3: LLM-A misinterpreted prompt — treat this row's gap as noisy")

    # Aggregated deltas, quality-filtered
    clean = [r for r in data["results"] if r["judgment_quality"]["score"] == 3]
    dirty = [r for r in data["results"] if r["judgment_quality"]["score"] < 3]

    def avg_gap(rows):
        if not rows:
            return None
        gs = [r["judgment_diff"]["score"] - r["judgment"]["score"] for r in rows]
        return sum(gs) / len(gs)

    print()
    print(f"Quality-clean rows (qual=3): {len(clean)}/{len(data['results'])}")
    print(f"Quality-dirty rows (qual<3): {len(dirty)}/{len(data['results'])}")
    if clean:
        aver_clean = [r for r in clean if r["lang"] == "aver"]
        python_clean = [r for r in clean if r["lang"] == "python"]
        print(f"  Clean avg gap (aver):   {avg_gap(aver_clean):.2f}" if aver_clean else "  (no clean aver rows)")
        print(f"  Clean avg gap (python): {avg_gap(python_clean):.2f}" if python_clean else "  (no clean python rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
