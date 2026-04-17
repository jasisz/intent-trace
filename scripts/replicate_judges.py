#!/usr/bin/env python3
"""Replicate all three judges independently on an existing results file.

Re-runs prompt-judge, diff-judge, and quality-judge with a fresh API session
(no context, no cache), storing results under `judgment_2`, `judgment_diff_2`,
`judgment_quality_2`. Reports inter-rater agreement per axis.

Usage:
    python scripts/replicate_judges.py results/...__quality_...json
    # or: latest __quality__ file if no arg given
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

MODEL_JUDGE = os.environ.get("INTENT_TRACE_MODEL_JUDGE", "claude-opus-4-7")

client = Anthropic()


def extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def judge_prompt_axis(original_prompt: str, guess_intent: str) -> dict:
    system = (
        "You are scoring how well an LLM reviewer guessed the intent of a code change, "
        "given only the diff. Be strict. Partial matches score 1 or 2, not 3."
    )
    rubric = (
        "Rubric:\n"
        "  0 — unrelated or wrong direction\n"
        "  1 — same general area, wrong specifics\n"
        "  2 — close, missing one key detail\n"
        "  3 — matches: same action, same target, same scope"
    )
    user = (
        f"Original change request:\n{original_prompt}\n\n"
        f"Reviewer's guess:\n{guess_intent}\n\n"
        f"{rubric}\n\n"
        'Respond with JSON only:\n{"score": 0, "reasoning": "one sentence"}'
    )
    r = client.messages.create(
        model=MODEL_JUDGE, max_tokens=512, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


def judge_diff_axis(diff: str, guess_intent: str) -> dict:
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
        'Respond with JSON only:\n{"score": 0, "reasoning": "one sentence"}'
    )
    r = client.messages.create(
        model=MODEL_JUDGE, max_tokens=512, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


def judge_quality_axis(prompt: str, diff: str) -> dict:
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
        'Respond with JSON only:\n{"score": 0, "reasoning": "one sentence"}'
    )
    r = client.messages.create(
        model=MODEL_JUDGE, max_tokens=512, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


def latest_quality() -> Path:
    candidates = sorted(
        Path("results").glob("*__quality_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise SystemExit("no results/*__quality_*.json files found")
    return candidates[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="quality JSON (default: latest)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    in_path = Path(args.input) if args.input else latest_quality()
    data = json.loads(in_path.read_text())
    print(f"Source:  {in_path.name}")
    print(f"Judge:   {MODEL_JUDGE}")
    print(f"Slices:  {len(data['results'])}")
    print()

    # Load prompts
    prompt_cache: dict[str, str] = {}

    def load_prompt(name: str) -> str:
        if name not in prompt_cache:
            program = data.get("program", "")
            prompt_cache[name] = (Path("prompts") / program / f"{name}.md").read_text()
        return prompt_cache[name]

    # Replicate prompt & diff axes per slice (12)
    for i, r in enumerate(data["results"], 1):
        print(f"  [{i}/{len(data['results'])}] {r['prompt']} / {r['lang']} / {r['ablation']}")
        prompt_text = load_prompt(r["prompt"])
        print(f"    prompt-axis replicate...")
        r["judgment_2"] = judge_prompt_axis(prompt_text, r["guess"]["intent"])
        print(f"    diff-axis replicate...")
        r["judgment_diff_2"] = judge_diff_axis(r["diff"], r["guess"]["intent"])

    # Replicate quality once per (prompt, lang) (6)
    quality2: dict[tuple[str, str], dict] = {}
    for r in data["results"]:
        if r["ablation"] != "full":
            continue
        key = (r["prompt"], r["lang"])
        print(f"  quality replicate: {r['prompt']} / {r['lang']}")
        prompt_text = load_prompt(r["prompt"])
        quality2[key] = judge_quality_axis(prompt_text, r["diff"])

    for r in data["results"]:
        r["judgment_quality_2"] = quality2[(r["prompt"], r["lang"])]

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = in_path.with_name(in_path.stem + f"__replicated_{ts}.json")
    out.write_text(json.dumps(data, indent=2))

    print()
    print(f"Saved: {out}\n")
    print("---")

    # Side-by-side per slice
    header = (
        f"{'prompt':<22} {'lang':<7} {'abl':<7} "
        f"{'P1':>3} {'P2':>3} | {'D1':>3} {'D2':>3} | {'Q1':>3} {'Q2':>3}"
    )
    print(header)
    print("-" * len(header))
    for r in data["results"]:
        p1 = r["judgment"]["score"]
        p2 = r["judgment_2"]["score"]
        d1 = r["judgment_diff"]["score"]
        d2 = r["judgment_diff_2"]["score"]
        q1 = r["judgment_quality"]["score"]
        q2 = r["judgment_quality_2"]["score"]
        print(
            f"{r['prompt']:<22} {r['lang']:<7} {r['ablation']:<7} "
            f"{p1:>3} {p2:>3} | {d1:>3} {d2:>3} | {q1:>3} {q2:>3}"
        )

    # Agreement stats per axis
    print()
    print("Inter-rater agreement (run 1 vs run 2):")
    for label, a, b in [
        ("prompt", "judgment", "judgment_2"),
        ("diff", "judgment_diff", "judgment_diff_2"),
        ("quality", "judgment_quality", "judgment_quality_2"),
    ]:
        scores = [(r[a]["score"], r[b]["score"]) for r in data["results"]]
        exact = sum(1 for x, y in scores if x == y)
        off1 = sum(1 for x, y in scores if abs(x - y) <= 1)
        maxd = max(abs(x - y) for x, y in scores)
        n = len(scores)
        print(
            f"  {label:<8}  exact={exact}/{n} ({100*exact//n}%)  "
            f"off-by-≤1={off1}/{n} ({100*off1//n}%)  max_disagreement={maxd}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
