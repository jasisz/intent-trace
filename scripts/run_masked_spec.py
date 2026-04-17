#!/usr/bin/env python3
"""Add `view="masked_spec"` slices to an existing per-reader merged JSONL.

Rationale: the current `view="masked"` strips Aver's `verify` blocks, which are
executable spec (closer to Python `assert` than to a docstring). Python's
masked view already leaves `assert` intact, so this ablation is asymmetric.
`masked_spec` keeps `verify` in Aver and is identical to `masked` in Python —
it rerun Python anyway so we get replication-noise numbers for free.

Usage:
    python scripts/run_masked_spec.py sonnet      # single reader
    python scripts/run_masked_spec.py all         # all three in sequence

Per reader: 3 langs × 18 prompts × 1 view = 54 slices, ~600 API calls.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

from intent_trace.mask import mask
from intent_trace.judges import judge_prompt_axis, judge_diff_axis
from intent_trace.llm_provider import call_llm


READER_MODELS = {
    "sonnet":  "claude-sonnet-4-6",
    "gpt4.1":  "gpt-4.1",
    "gemini":  "gemini-2.5-flash",
}
JUDGES_PD = {
    "opus":    "claude-opus-4-7",
    "sonnet":  "claude-sonnet-4-6",
    "haiku":   "claude-haiku-4-5-20251001",
    "gpt-4o":  "gpt-4o",
    "gpt-4.1": "gpt-4.1",
}
MERGED = Path("results/merged")


def list_files(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file())


def mask_for_spec_view(lang: str, source: str) -> str:
    if lang == "aver":
        return mask("aver_keep_verify", source)
    return mask(lang, source)


def combine_diffs(before_dir: Path, after_dir: Path, lang: str) -> str:
    diffs = []
    b = {p.name: p for p in list_files(before_dir)} if before_dir.exists() else {}
    a = {p.name: p for p in list_files(after_dir)} if after_dir.exists() else {}
    for name in sorted(set(b) | set(a)):
        bp, ap = b.get(name), a.get(name)
        bs = mask_for_spec_view(lang, bp.read_text()) if bp else ""
        as_ = mask_for_spec_view(lang, ap.read_text()) if ap else ""
        d = "\n".join(difflib.unified_diff(
            bs.splitlines(), as_.splitlines(),
            fromfile=f"a/masked_spec/{name}", tofile=f"b/masked_spec/{name}",
            lineterm=""))
        if d.strip():
            diffs.append(d)
    return "\n".join(diffs)


def llm_b_guess(model: str, lang: str, diff: str) -> dict:
    system = (
        "You are reviewing a code change. You see only the diff. "
        "Guess what the author was trying to achieve. "
        "Focus on the PRIMARY intent. Skip secondary refactoring. "
        "Be concrete, not vague."
    )
    user = (
        f"Language: {lang}\n\nDiff:\n\n{diff}\n\n"
        "Respond with JSON only:\n"
        '{\n  "intent": "one sentence, primary intent",\n'
        '  "confidence": "low|medium|high",\n'
        '  "unclear": "optional one-line note, or null"\n}'
        "\n\nIMPORTANT: raw JSON only, no markdown, no preamble."
    )
    text = call_llm(model, system, user, max_tokens=512).strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            return json.loads(m2.group(0))
        raise


def run_ensemble(judge_fn, judges: dict, *args) -> dict:
    indiv = {}
    for name, model in judges.items():
        try:
            indiv[name] = judge_fn(None, model, *args)
        except Exception as e:
            indiv[name] = {"score": -1, "error": f"{type(e).__name__}: {e}"}
    scores = [v["score"] for v in indiv.values() if v.get("score", -1) >= 0]
    out = {"individual": indiv}
    if scores:
        out["median"] = round(statistics.median(scores), 1)
        out["mean"] = round(statistics.mean(scores), 2)
        out["spread"] = max(scores) - min(scores)
    return out


def load_merged(reader: str) -> dict:
    path = MERGED / f"{reader}.jsonl"
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["program"], r["prompt"], r["lang"], r["view"])
        out[k] = r
    return out


def run_reader(reader: str) -> None:
    model_b = READER_MODELS[reader]
    print(f"\n=== {reader} reader  (LLM-B = {model_b}) ===")
    current = load_merged(reader)
    # Canonical slice set: every (program, prompt, lang) present as view="full"
    full_triples = sorted({
        (k[0], k[1], k[2]) for k in current if k[3] == "full"
    })
    # Skip triples already scored with masked_spec
    todo = [t for t in full_triples if (t[0], t[1], t[2], "masked_spec") not in current]
    print(f"Canonical triples: {len(full_triples)}, todo: {len(todo)}")

    programs = Path("programs")
    prompts = Path("prompts")

    for i, (program, prompt_name, lang) in enumerate(todo, 1):
        key = f"{program}/{prompt_name}/{lang}"
        print(f"[{i}/{len(todo)}] {key}", flush=True)
        try:
            before_dir = programs / program / lang / "before"
            after_dir = programs / program / lang / "after" / prompt_name
            prompt_text = (prompts / program / f"{prompt_name}.md").read_text()
            diff = combine_diffs(before_dir, after_dir, lang)
            if not diff.strip():
                print("    empty diff, skip")
                continue
            print("    LLM-B...", flush=True)
            guess = llm_b_guess(model_b, lang, diff)
            print(f"    guess: {guess.get('intent', '')[:80]}")
            print("    prompt-axis ensemble (5 judges)...", flush=True)
            jp = run_ensemble(judge_prompt_axis, JUDGES_PD, prompt_text, guess["intent"])
            print("    diff-axis ensemble (5 judges)...", flush=True)
            jd = run_ensemble(judge_diff_axis, JUDGES_PD, diff, guess["intent"])

            slice_obj = {
                "program": program, "prompt": prompt_name, "lang": lang,
                "view": "masked_spec",
                "diff": diff, "guess": guess,
                "judgment_prompt": jp, "judgment_diff": jd,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            current[(program, prompt_name, lang, "masked_spec")] = slice_obj
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}", flush=True)

    out = MERGED / f"{reader}.jsonl"
    tmp = out.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in current.values():
            f.write(json.dumps(r) + "\n")
    tmp.replace(out)
    print(f"{reader}: written back, total rows={len(current)}")


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("Need OPENAI_API_KEY and ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all":
        for r in READER_MODELS:
            run_reader(r)
    else:
        run_reader(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
