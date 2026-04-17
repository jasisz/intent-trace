#!/usr/bin/env python3
"""Cross-rater test: add Sonnet judge (same rubric) and Opus judge (paraphrased rubric).

Gives four raters per slice on each axis:
  1. Opus + original rubric     (already in source JSON)
  2. Opus + original rubric (replicated)  — deterministic, see replicate_judges.py
  3. Sonnet + original rubric    (this script: *_sonnet)
  4. Opus + paraphrased rubric   (this script: *_paraphrased)

Reports agreement between 1, 3, 4.

Usage:
    python scripts/cross_judge.py results/...__quality_...json
    # or: latest __quality__ file
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

MODEL_OPUS = os.environ.get("INTENT_TRACE_MODEL_OPUS", "claude-opus-4-7")
MODEL_SONNET = os.environ.get("INTENT_TRACE_MODEL_SONNET", "claude-sonnet-4-6")

client = Anthropic()


def extract_json(text: str) -> dict:
    text = text.strip()
    # Code fence
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # Direct JSON object
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: greedy {...} with "score"
    m = re.search(r"\{[^{}]*?\"score\"[^{}]*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Last-resort regex: extract score + reasoning manually
    sm = re.search(r'"?score"?\s*[:=]\s*(\d)', text)
    rm = re.search(r'"?reasoning"?\s*[:=]\s*"?([^"\n]+)"?', text)
    if sm:
        return {
            "score": int(sm.group(1)),
            "reasoning": rm.group(1).strip().rstrip('"') if rm else "(no reasoning parsed)",
        }
    raise ValueError(f"could not extract score from: {text[:300]!r}")


def call_judge(model: str, system: str, user: str) -> dict:
    # Stronger instruction in the user message to force JSON-only output.
    user = user + "\n\nIMPORTANT: Respond with raw JSON only. No markdown fences, no preamble, no explanation outside the JSON."
    r = client.messages.create(
        model=model, max_tokens=512, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json(r.content[0].text)


# Original rubrics (Sonnet uses these)
ORIG_PROMPT_SYS = (
    "You are scoring how well an LLM reviewer guessed the intent of a code change, "
    "given only the diff. Be strict. Partial matches score 1 or 2, not 3."
)
ORIG_PROMPT_RUBRIC = (
    "Rubric:\n"
    "  0 — unrelated or wrong direction\n"
    "  1 — same general area, wrong specifics\n"
    "  2 — close, missing one key detail\n"
    "  3 — matches: same action, same target, same scope"
)
ORIG_DIFF_SYS = (
    "You are evaluating how faithfully a reviewer's guess describes the "
    "changes visible in a code diff. The reviewer saw only the diff and "
    "produced this one-sentence guess of what the author was trying to do. "
    "You do NOT have access to the author's original request. "
    "Score whether the guess captures what was ACTUALLY changed in the diff, "
    "independent of any task specification."
)
ORIG_DIFF_RUBRIC = (
    "Rubric (fidelity to diff):\n"
    "  0 — guess does not describe the change visible in the diff\n"
    "  1 — captures part of the change, misses substantial elements\n"
    "  2 — captures most elements, misses one material detail OR includes one wrong detail\n"
    "  3 — faithfully describes all material elements of the change"
)
ORIG_QUALITY_SYS = (
    "You evaluate whether a code change correctly fulfills a change request. "
    "You see the original request and the diff (before/after). "
    "Score whether the change ADDRESSES the request, independent of any "
    "reviewer's guess. Bonus additions beyond the request are fine as "
    "long as the core request is met."
)
ORIG_QUALITY_RUBRIC = (
    "Rubric (change quality):\n"
    "  0 — the diff goes in the opposite direction, or completely misses the request\n"
    "  1 — addresses something tangential; does not fulfill the core request\n"
    "  2 — partially fulfills the request; misses or misinterprets one important element\n"
    "  3 — correctly fulfills the request (extras beyond the request are ok)"
)


# Paraphrased rubrics (Opus uses these)
PARA_PROMPT_SYS = (
    "Grade how accurately a reviewer inferred the author's task from a diff alone. "
    "Withhold the top score when the guess is broadly on topic but imprecise."
)
PARA_PROMPT_RUBRIC = (
    "Scoring guide:\n"
    "  0 — the guess points away from what was asked\n"
    "  1 — topic is right, details diverge\n"
    "  2 — close but one meaningful aspect is off or absent\n"
    "  3 — action, target, and scope all align with the original request"
)
PARA_DIFF_SYS = (
    "Assess how thoroughly a reviewer's one-sentence description mirrors the "
    "changes shown in a diff. You have the diff and the description but not "
    "the original task. Judge description accuracy relative to what the diff "
    "actually shows."
)
PARA_DIFF_RUBRIC = (
    "Scoring guide (description accuracy):\n"
    "  0 — the description conflicts with what the diff shows\n"
    "  1 — only some of the changes are captured; significant parts absent\n"
    "  2 — largely accurate; one meaningful element is missing or misstated\n"
    "  3 — all significant changes are reflected faithfully"
)
PARA_QUALITY_SYS = (
    "Decide whether an author's code change delivers what a request asked for. "
    "You will see the request and the diff. Score only whether the request is "
    "fulfilled; unrelated additions do not lower the score if the core ask is met."
)
PARA_QUALITY_RUBRIC = (
    "Scoring guide (request fulfillment):\n"
    "  0 — the change does not satisfy the request at all\n"
    "  1 — the change is off-target; it addresses something adjacent\n"
    "  2 — the change mostly satisfies but has a gap in fulfillment\n"
    "  3 — the change satisfies the request (additional unrelated work is fine)"
)


def user_prompt(original_prompt: str | None, guess: str | None, diff: str | None, rubric: str) -> str:
    parts: list[str] = []
    if original_prompt:
        parts.append(f"Original change request:\n{original_prompt}")
    if diff:
        parts.append(f"Diff:\n{diff}")
    if guess:
        parts.append(f"Reviewer's guess:\n{guess}")
    parts.append(rubric)
    parts.append('Respond with JSON only:\n{"score": 0, "reasoning": "one sentence"}')
    return "\n\n".join(parts)


def latest_quality() -> Path:
    # Prefer most recently modified __quality_*.json (skip __replicated_ duplicates)
    candidates = [
        p for p in Path("results").glob("*__quality_*.json")
        if "__replicated_" not in p.name
    ]
    if not candidates:
        candidates = list(Path("results").glob("*__quality_*.json"))
    if not candidates:
        raise SystemExit("no results/*__quality_*.json files found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    in_path = Path(args.input) if args.input else latest_quality()
    data = json.loads(in_path.read_text())
    print(f"Source:         {in_path.name}")
    print(f"Opus model:     {MODEL_OPUS}")
    print(f"Sonnet model:   {MODEL_SONNET}")
    print(f"Slices:         {len(data['results'])}")
    print()

    # Load prompts
    prompts: dict[str, str] = {}

    def load_prompt(name: str) -> str:
        if name not in prompts:
            program = data.get("program", "")
            prompts[name] = (Path("prompts") / program / f"{name}.md").read_text()
        return prompts[name]

    # Per-slice: prompt-axis + diff-axis (x2 raters = Sonnet + Opus-paraphrased)
    for i, r in enumerate(data["results"], 1):
        ptxt = load_prompt(r["prompt"])
        guess = r["guess"]["intent"]
        diff = r["diff"]
        print(f"  [{i}/{len(data['results'])}] {r['prompt']} / {r['lang']} / {r['ablation']}")

        # Sonnet, same rubric
        r["judgment_sonnet"] = call_judge(
            MODEL_SONNET, ORIG_PROMPT_SYS,
            user_prompt(ptxt, guess, None, ORIG_PROMPT_RUBRIC),
        )
        r["judgment_diff_sonnet"] = call_judge(
            MODEL_SONNET, ORIG_DIFF_SYS,
            user_prompt(None, guess, diff, ORIG_DIFF_RUBRIC),
        )

        # Opus, paraphrased rubric
        r["judgment_paraphrased"] = call_judge(
            MODEL_OPUS, PARA_PROMPT_SYS,
            user_prompt(ptxt, guess, None, PARA_PROMPT_RUBRIC),
        )
        r["judgment_diff_paraphrased"] = call_judge(
            MODEL_OPUS, PARA_DIFF_SYS,
            user_prompt(None, guess, diff, PARA_DIFF_RUBRIC),
        )

    # Per (prompt, lang): quality-axis (x2 raters)
    seen_s: dict[tuple[str, str], dict] = {}
    seen_p: dict[tuple[str, str], dict] = {}
    for r in data["results"]:
        if r["ablation"] != "full":
            continue
        key = (r["prompt"], r["lang"])
        ptxt = load_prompt(r["prompt"])
        print(f"  quality: {r['prompt']} / {r['lang']}")
        seen_s[key] = call_judge(
            MODEL_SONNET, ORIG_QUALITY_SYS,
            user_prompt(ptxt, None, r["diff"], ORIG_QUALITY_RUBRIC),
        )
        seen_p[key] = call_judge(
            MODEL_OPUS, PARA_QUALITY_SYS,
            user_prompt(ptxt, None, r["diff"], PARA_QUALITY_RUBRIC),
        )
    for r in data["results"]:
        key = (r["prompt"], r["lang"])
        r["judgment_quality_sonnet"] = seen_s[key]
        r["judgment_quality_paraphrased"] = seen_p[key]

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = in_path.with_name(in_path.stem + f"__cross_{ts}.json")
    out.write_text(json.dumps(data, indent=2))

    print()
    print(f"Saved: {out}\n")
    print("---")

    # Per-slice table: 3 raters per axis
    header = (
        f"{'prompt':<22} {'lang':<7} {'abl':<7} "
        f"{'Op':>3} {'Sn':>3} {'Pa':>3} | "
        f"{'Op':>3} {'Sn':>3} {'Pa':>3} | "
        f"{'Op':>3} {'Sn':>3} {'Pa':>3}"
    )
    print("               (slice)                prompt-axis   diff-axis    quality-axis")
    print(header)
    print("-" * len(header))
    for r in data["results"]:
        po = r["judgment"]["score"]
        ps = r["judgment_sonnet"]["score"]
        pp = r["judgment_paraphrased"]["score"]
        do = r["judgment_diff"]["score"]
        ds = r["judgment_diff_sonnet"]["score"]
        dp = r["judgment_diff_paraphrased"]["score"]
        qo = r["judgment_quality"]["score"]
        qs = r["judgment_quality_sonnet"]["score"]
        qp = r["judgment_quality_paraphrased"]["score"]
        print(
            f"{r['prompt']:<22} {r['lang']:<7} {r['ablation']:<7} "
            f"{po:>3} {ps:>3} {pp:>3} | "
            f"{do:>3} {ds:>3} {dp:>3} | "
            f"{qo:>3} {qs:>3} {qp:>3}"
        )

    print()
    print("Legend:  Op = Opus original rubric   Sn = Sonnet original rubric   Pa = Opus paraphrased rubric")

    # Pairwise agreement per axis
    print("\nInter-rater agreement (Opus-original vs each alternative):\n")
    header = f"  {'axis':<10}{'comparator':<22}{'exact':>8}{'±1':>8}{'max_Δ':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    def agree(a_key: str, b_key: str, label: str) -> tuple[str, int, int, int, int]:
        scores = [(r[a_key]["score"], r[b_key]["score"]) for r in data["results"]]
        n = len(scores)
        ex = sum(1 for x, y in scores if x == y)
        o1 = sum(1 for x, y in scores if abs(x - y) <= 1)
        md = max(abs(x - y) for x, y in scores)
        return label, ex, o1, md, n

    for axis_label, a_key, s_key, p_key in [
        ("prompt",  "judgment",          "judgment_sonnet",          "judgment_paraphrased"),
        ("diff",    "judgment_diff",     "judgment_diff_sonnet",     "judgment_diff_paraphrased"),
        ("quality", "judgment_quality",  "judgment_quality_sonnet",  "judgment_quality_paraphrased"),
    ]:
        for comp_label, comp_key in [("vs Sonnet (same rubric)", s_key),
                                      ("vs Opus (paraphrased)", p_key)]:
            _, ex, o1, md, n = agree(a_key, comp_key, comp_label)
            print(f"  {axis_label:<10}{comp_label:<22}{f'{ex}/{n}':>8}{f'{o1}/{n}':>8}{md:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
