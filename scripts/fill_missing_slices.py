#!/usr/bin/env python3
"""Fill completely missing slices in a merged per-reader JSONL.

For slices absent from results/merged/<reader>.jsonl (compared to the
sonnet canonical set), this runs the full pipeline:
  - LLM-B (reader model) produces a guess
  - 6-judge ensemble (Claude Opus/Sonnet/Haiku + gpt-4o + gpt-4.1 + Kimi K2) scores it
  - append to merged jsonl

Also fills missing __quality__ rows (3-Claude ensemble, no GPT) where
a full+masked pair is newly added.

Usage:
    python scripts/fill_missing_slices.py gemini
    python scripts/fill_missing_slices.py gpt4.1
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

from intent_trace.mask import mask
from intent_trace.judges import (
    judge_prompt_axis,
    judge_diff_axis,
    judge_quality_axis,
)
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
JUDGES_Q = {
    "opus":   "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}

MERGED = Path("results/merged")


def list_files(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file())


def combine_diffs(before_dir: Path, after_dir: Path, label: str, transform=None) -> str:
    import difflib
    diffs = []
    b = {p.name: p for p in list_files(before_dir)} if before_dir.exists() else {}
    a = {p.name: p for p in list_files(after_dir)} if after_dir.exists() else {}
    for name in sorted(set(b) | set(a)):
        bp, ap = b.get(name), a.get(name)
        bs = bp.read_text() if bp else ""
        as_ = ap.read_text() if ap else ""
        if transform:
            bs = transform(bp, bs) if bp else ""
            as_ = transform(ap, as_) if ap else ""
        d = "\n".join(difflib.unified_diff(
            bs.splitlines(), as_.splitlines(),
            fromfile=f"a/{label}/{name}", tofile=f"b/{label}/{name}", lineterm=""))
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
    """Return ensemble dict like the existing pipeline."""
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
        if not line.strip(): continue
        r = json.loads(line)
        k = (r["program"], r["prompt"], r["lang"], r["view"])
        out[k] = r
    return out


def canonical_keys(reader: str = "sonnet") -> tuple[set, set]:
    """Return (prompt/diff slice keys, quality triples) present in sonnet reader."""
    path = MERGED / f"{reader}.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    pd = {(r["program"], r["prompt"], r["lang"], r["view"])
          for r in rows if r["view"] in ("full", "masked")}
    q = {(r["program"], r["prompt"], r["lang"])
         for r in rows if r["view"] == "__quality__"}
    return pd, q


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("Need OPENAI_API_KEY and ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    reader = sys.argv[1]
    model_b = READER_MODELS[reader]
    print(f"Reader:  {reader}  (LLM-B = {model_b})")

    current = load_merged(reader)
    can_pd, can_q = canonical_keys("sonnet")
    present_pd = {k for k in current if k[3] in ("full", "masked")}
    present_q = {(k[0], k[1], k[2]) for k in current if k[3] == "__quality__"}
    missing_pd = sorted(can_pd - present_pd)
    missing_q = sorted(can_q - present_q)
    print(f"Missing slices: {len(missing_pd)}  (full/masked)")
    print(f"Missing quality triples: {len(missing_q)}")

    programs = Path("programs")
    prompts = Path("prompts")

    for i, (program, prompt_name, lang, view) in enumerate(missing_pd, 1):
        key_str = f"{program}/{prompt_name}/{lang}/{view}"
        print(f"[{i}/{len(missing_pd)}] {key_str}", flush=True)
        try:
            before_dir = programs / program / lang / "before"
            after_dir = programs / program / lang / "after" / prompt_name
            prompt_text = (prompts / program / f"{prompt_name}.md").read_text()

            if view == "full":
                diff = combine_diffs(before_dir, after_dir, view)
            else:
                diff = combine_diffs(before_dir, after_dir, view,
                                     transform=lambda _p, s: mask(lang, s))
            if not diff.strip():
                print("    empty diff, skip")
                continue

            print("    LLM-B...", flush=True)
            guess = llm_b_guess(model_b, lang, diff)
            print(f"    guess: {guess.get('intent','')[:80]}")
            print("    prompt-axis ensemble (6 judges)...", flush=True)
            jp = run_ensemble(judge_prompt_axis, JUDGES_PD, prompt_text, guess["intent"])
            print("    diff-axis ensemble (6 judges)...", flush=True)
            jd = run_ensemble(judge_diff_axis, JUDGES_PD, diff, guess["intent"])

            slice_obj = {
                "program": program, "prompt": prompt_name, "lang": lang, "view": view,
                "diff": diff, "guess": guess,
                "judgment_prompt": jp, "judgment_diff": jd,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            current[(program, prompt_name, lang, view)] = slice_obj
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}", flush=True)

    for i, (program, prompt_name, lang) in enumerate(missing_q, 1):
        print(f"[Q {i}/{len(missing_q)}] {program}/{prompt_name}/{lang}", flush=True)
        try:
            before_dir = programs / program / lang / "before"
            after_dir = programs / program / lang / "after" / prompt_name
            prompt_text = (prompts / program / f"{prompt_name}.md").read_text()
            full_diff = combine_diffs(before_dir, after_dir, "full")
            if not full_diff.strip():
                continue
            print("    quality-axis ensemble (3 Claude)...", flush=True)
            jq = run_ensemble(judge_quality_axis, JUDGES_Q, prompt_text, full_diff)
            q_obj = {
                "program": program, "prompt": prompt_name, "lang": lang,
                "view": "__quality__", "judgment_quality": jq,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            current[(program, prompt_name, lang, "__quality__")] = q_obj
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}", flush=True)

    out = MERGED / f"{reader}.jsonl"
    tmp = out.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in current.values():
            f.write(json.dumps(r) + "\n")
    tmp.replace(out)
    print(f"\nWritten: {out}  (total rows: {len(current)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
