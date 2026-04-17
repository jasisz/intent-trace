#!/usr/bin/env python3
"""Run intent-trace over a program + one or more prompts, with ablation.

Usage:
    # Single prompt:
    python scripts/run_one.py programs/order_total prompts/order_total/reject_negative_prices.md

    # All prompts in a directory:
    python scripts/run_one.py programs/order_total prompts/order_total
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

from anthropic import Anthropic

from intent_trace.mask import mask

MODEL_A = os.environ.get("INTENT_TRACE_MODEL_A", "claude-sonnet-4-6")
MODEL_B = os.environ.get("INTENT_TRACE_MODEL_B", "claude-sonnet-4-6")
MODEL_JUDGE = os.environ.get("INTENT_TRACE_MODEL_JUDGE", "claude-opus-4-7")

client = Anthropic()


def load_program(program_dir: Path, lang: str) -> tuple[str, str]:
    lang_dir = program_dir / lang
    files = [f for f in lang_dir.iterdir() if f.is_file()]
    if len(files) != 1:
        raise RuntimeError(f"expected 1 file in {lang_dir}, found {len(files)}")
    f = files[0]
    return f.name, f.read_text()


def extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def call(model: str, system: str, user: str, max_tokens: int = 1024) -> str:
    r = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text


def llm_a_modify(lang: str, filename: str, source: str, prompt: str) -> str:
    system = (
        f"You are modifying a {lang} source file to fulfill a change request. "
        "Return ONLY the complete modified file content. No prose, no code fences, no explanations."
    )
    user = (
        f"Original file ({filename}):\n\n{source}\n\n"
        f"---\n\nChange request:\n\n{prompt}\n\n"
        "Return the complete modified file content."
    )
    out = call(MODEL_A, system, user, max_tokens=8192).strip()
    if out.startswith("```"):
        lines = out.splitlines()
        if lines and lines[-1].startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        out = "\n".join(lines)
    return out


def make_diff(before: str, after: str, filename: str, context_label: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{context_label}/{filename}",
            tofile=f"b/{context_label}/{filename}",
            lineterm="",
        )
    )


def llm_b_guess(lang: str, filename: str, diff: str) -> dict:
    system = (
        "You are reviewing a code change. You see only the diff. "
        "Guess what the author was trying to achieve. "
        "Focus on the PRIMARY intent. Skip secondary refactoring. "
        "Be concrete, not vague."
    )
    user = (
        f"Language: {lang}\nFile: {filename}\n\nDiff:\n\n{diff}\n\n"
        "Respond with JSON only:\n"
        '{\n  "intent": "one sentence, primary intent",\n'
        '  "confidence": "low|medium|high",\n'
        '  "unclear": "optional one-line note, or null"\n}'
    )
    return extract_json(call(MODEL_B, system, user))


def judge(original_prompt: str, guess_intent: str) -> dict:
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
        "Respond with JSON only:\n"
        '{\n  "score": 0,\n  "reasoning": "one sentence"\n}'
    )
    return extract_json(call(MODEL_JUDGE, system, user))


def run_prompt(program_dir: Path, prompt_path: Path) -> list[dict]:
    prompt = prompt_path.read_text()
    prompt_name = prompt_path.stem
    results: list[dict] = []

    for lang in ["aver", "python"]:
        print(f"  [{lang}] LLM-A applying '{prompt_name}'...")
        filename, source = load_program(program_dir, lang)
        modified = llm_a_modify(lang, filename, source, prompt)

        for ablation in ["full", "masked"]:
            if ablation == "full":
                before, after = source, modified
            else:
                before, after = mask(lang, source), mask(lang, modified)

            diff = make_diff(before, after, filename, ablation)
            print(f"    [{lang}/{ablation}] LLM-B guessing...")
            guess = llm_b_guess(lang, filename, diff)
            print(f"    [{lang}/{ablation}] judge scoring...")
            judgment = judge(prompt, guess["intent"])
            results.append(
                {
                    "prompt": prompt_name,
                    "lang": lang,
                    "ablation": ablation,
                    "filename": filename,
                    "diff": diff,
                    "guess": guess,
                    "judgment": judgment,
                }
            )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("program_dir", help="e.g. programs/order_total")
    ap.add_argument("prompts", help="prompt file or directory")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    program_dir = Path(args.program_dir)
    prompts_arg = Path(args.prompts)

    if prompts_arg.is_dir():
        prompt_files = sorted(prompts_arg.glob("*.md"))
    else:
        prompt_files = [prompts_arg]

    if not prompt_files:
        print("no prompt files found", file=sys.stderr)
        return 1

    print(f"Program:  {program_dir.name}")
    print(f"Prompts:  {len(prompt_files)}")
    print(f"Model A:  {MODEL_A}")
    print(f"Model B:  {MODEL_B}")
    print(f"Judge:    {MODEL_JUDGE}")
    print()

    all_results: list[dict] = []
    for pf in prompt_files:
        print(f"[{pf.stem}]")
        all_results.extend(run_prompt(program_dir, pf))
        print()

    ts = time.strftime("%Y%m%d_%H%M%S")
    scope = "all" if prompts_arg.is_dir() else prompts_arg.stem
    out = Path("results") / f"{program_dir.name}__{scope}__{ts}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "program": program_dir.name,
                "models": {"a": MODEL_A, "b": MODEL_B, "judge": MODEL_JUDGE},
                "results": all_results,
            },
            indent=2,
        )
    )

    # Summary table
    print("---")
    print(f"Saved: {out}\n")
    rows: dict[tuple[str, str, str], int] = {}
    for r in all_results:
        key = (r["prompt"], r["lang"], r["ablation"])
        rows[key] = r["judgment"]["score"]

    prompts_seen = sorted({r["prompt"] for r in all_results})
    header = f"{'prompt':<30} {'lang':<8} {'full':>6} {'masked':>8}  Δ"
    print(header)
    print("-" * len(header))
    for pn in prompts_seen:
        for lang in ["aver", "python"]:
            full = rows.get((pn, lang, "full"), "-")
            masked = rows.get((pn, lang, "masked"), "-")
            try:
                delta = full - masked  # type: ignore[operator]
                delta_s = f"{delta:+d}"
            except TypeError:
                delta_s = "-"
            print(f"{pn:<30} {lang:<8} {full!s:>6} {masked!s:>8}  {delta_s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
