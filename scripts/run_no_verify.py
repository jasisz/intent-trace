#!/usr/bin/env python3
"""Architectural-payment_ops Aver rerun with verify blocks stripped from the diff.

Tests the hypothesis: Aver's large gap on payment_ops architectural refactors
might be partly driven by verify-block noise in the diff (30-36% of diff lines
were verify content). If stripping verify while keeping all narrative prose
lifts Aver's score, verify-in-diff was a confound.

Targets: 3 readers × 2 architectural payment_ops prompts (atomic_batch_ingest,
event_sourcing_rebuild) × 1 view (full, but Aver-only with verify stripped)
= 6 slices × 11 API calls = ~$1.

Written to results/merged/<reader>.jsonl with view="no_verify".
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

from intent_trace.mask import strip_verify_only
from intent_trace.judges import judge_prompt_axis, judge_diff_axis
from intent_trace.llm_provider import call_llm


READER_MODELS = {
    "sonnet":  "claude-sonnet-4-6",
    "gpt4.1":  "gpt-4.1",
    "gemini":  "gemini-2.5-flash",
}
JUDGES = {
    "opus":    "claude-opus-4-7",
    "sonnet":  "claude-sonnet-4-6",
    "haiku":   "claude-haiku-4-5-20251001",
    "gpt-4o":  "gpt-4o",
    "gpt-4.1": "gpt-4.1",
}
TARGETS = [
    ("payment_ops", "atomic_batch_ingest"),
    ("payment_ops", "event_sourcing_rebuild"),
]
MERGED = Path("results/merged")


def list_files(d):
    return sorted(p for p in d.iterdir() if p.is_file())


def combine_diff_no_verify(before_dir, after_dir):
    diffs = []
    b = {p.name: p for p in list_files(before_dir)} if before_dir.exists() else {}
    a = {p.name: p for p in list_files(after_dir)} if after_dir.exists() else {}
    for name in sorted(set(b) | set(a)):
        bp, ap = b.get(name), a.get(name)
        bs = strip_verify_only(bp.read_text()) if bp else ""
        as_ = strip_verify_only(ap.read_text()) if ap else ""
        d = "\n".join(difflib.unified_diff(
            bs.splitlines(), as_.splitlines(),
            fromfile=f"a/no_verify/{name}", tofile=f"b/no_verify/{name}", lineterm=""))
        if d.strip():
            diffs.append(d)
    return "\n".join(diffs)


def llm_b(model, lang, diff):
    system = (
        "You are reviewing a code change. You see only the diff. "
        "Guess what the author was trying to achieve. "
        "Focus on the PRIMARY intent. Skip secondary refactoring. Be concrete, not vague."
    )
    user = (
        f"Language: {lang}\n\nDiff:\n\n{diff}\n\n"
        "Respond with JSON only:\n"
        '{"intent": "one sentence, primary intent", "confidence": "low|medium|high", "unclear": "optional one-line note, or null"}'
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


def ensemble(fn, *args):
    indiv = {}
    for name, model in JUDGES.items():
        try:
            indiv[name] = fn(None, model, *args)
        except Exception as e:
            indiv[name] = {"score": -1, "error": f"{type(e).__name__}: {e}"}
    scores = [v["score"] for v in indiv.values() if v.get("score", -1) >= 0]
    out = {"individual": indiv}
    if scores:
        out["median"] = round(statistics.median(scores), 1)
        out["mean"] = round(statistics.mean(scores), 2)
        out["spread"] = max(scores) - min(scores)
    return out


def main():
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("need API keys", file=sys.stderr); return 1

    for reader, model_b in READER_MODELS.items():
        print(f"\n=== reader={reader} ===")
        path = MERGED / f"{reader}.jsonl"
        current = {}
        for line in path.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            current[(r["program"], r["prompt"], r["lang"], r["view"])] = r

        for program, prompt_name in TARGETS:
            prompt_text = (Path("prompts") / program / f"{prompt_name}.md").read_text()
            before_dir = Path("programs") / program / "aver" / "before"
            after_dir = Path("programs") / program / "aver" / "after" / prompt_name
            print(f"  [{program}/{prompt_name}/aver/no_verify]", flush=True)
            try:
                diff = combine_diff_no_verify(before_dir, after_dir)
                print(f"    diff: {len(diff.splitlines())} lines (was ~136-189 with verify)")
                guess = llm_b(model_b, "aver", diff)
                print(f"    guess: {guess.get('intent','')[:80]}")
                jp = ensemble(judge_prompt_axis, prompt_text, guess["intent"])
                jd = ensemble(judge_diff_axis, diff, guess["intent"])
                slice_obj = {
                    "program": program, "prompt": prompt_name,
                    "lang": "aver", "view": "no_verify",
                    "diff": diff, "guess": guess,
                    "judgment_prompt": jp, "judgment_diff": jd,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                current[(program, prompt_name, "aver", "no_verify")] = slice_obj
                print(f"    P={jp.get('median','-')} D={jd.get('median','-')}")
            except Exception as e:
                print(f"    ERROR: {type(e).__name__}: {e}")

        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w") as f:
            for r in current.values():
                f.write(json.dumps(r) + "\n")
        tmp.replace(path)
        print(f"  written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
