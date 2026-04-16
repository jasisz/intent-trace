#!/usr/bin/env python3
"""Run one intent-trace slice end-to-end.

Usage:
    python scripts/run_one.py programs/order_total prompts/order_total/reject_negative_prices.md
"""
import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

from anthropic import Anthropic

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
    # Strip markdown code fences if present.
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
    # Defensively strip code fences if the model added them.
    if out.startswith("```"):
        lines = out.splitlines()
        if lines[-1].startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        out = "\n".join(lines)
    return out


def make_diff(before: str, after: str, filename: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )


def llm_b_guess(lang: str, filename: str, diff: str) -> dict:
    system = (
        "You are reviewing a code change. You see only the diff. "
        "Guess what the author was trying to achieve. "
        "Focus on the PRIMARY intent. Skip secondary refactoring notes. "
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


def run_slice(lang: str, program_dir: Path, prompt: str) -> dict:
    filename, source = load_program(program_dir, lang)
    print(f"  [{lang}] LLM-A applying change request...")
    modified = llm_a_modify(lang, filename, source, prompt)
    diff = make_diff(source, modified, filename)
    print(f"  [{lang}] LLM-B guessing intent from diff...")
    guess = llm_b_guess(lang, filename, diff)
    print(f"  [{lang}] Judge scoring guess vs original...")
    score = judge(prompt, guess["intent"])
    return {
        "lang": lang,
        "filename": filename,
        "source_before": source,
        "source_after": modified,
        "diff": diff,
        "guess": guess,
        "judgment": score,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("program_dir", help="e.g. programs/order_total")
    ap.add_argument("prompt_file", help="e.g. prompts/order_total/reject_negative_prices.md")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    program_dir = Path(args.program_dir)
    prompt = Path(args.prompt_file).read_text()

    print(f"Program:   {program_dir}")
    print(f"Prompt:    {args.prompt_file}")
    print(f"Model A:   {MODEL_A}")
    print(f"Model B:   {MODEL_B}")
    print(f"Judge:     {MODEL_JUDGE}")
    print()

    results = {}
    for lang in ["aver", "python"]:
        print(f"[{lang}]")
        results[lang] = run_slice(lang, program_dir, prompt)
        print()

    ts = time.strftime("%Y%m%d_%H%M%S")
    prompt_name = Path(args.prompt_file).stem
    out = Path("results") / f"{program_dir.name}__{prompt_name}__{ts}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "prompt": prompt,
                "program": program_dir.name,
                "models": {"a": MODEL_A, "b": MODEL_B, "judge": MODEL_JUDGE},
                "runs": results,
            },
            indent=2,
        )
    )

    print("---")
    print(f"Saved:  {out}")
    print()
    print(f"Aver   score: {results['aver']['judgment']['score']}/3")
    print(f"  guess:    {results['aver']['guess']['intent']}")
    print(f"  judge:    {results['aver']['judgment']['reasoning']}")
    print()
    print(f"Python score: {results['python']['judgment']['score']}/3")
    print(f"  guess:    {results['python']['guess']['intent']}")
    print(f"  judge:    {results['python']['judgment']['reasoning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
