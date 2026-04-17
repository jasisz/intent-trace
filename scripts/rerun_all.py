#!/usr/bin/env python3
"""Rerun the full benchmark for one reader (v2 canonical baselines).

Iterates every (program, prompt, lang ∈ {aver, python_from_aver, python_oop},
view ∈ {full, masked, masked_spec}), regenerates diff + LLM-B guess + 5-judge
ensemble (Opus + Sonnet + Haiku + gpt-4o + gpt-4.1), and rewrites
results/merged/<reader>.jsonl from scratch. Skips quality axis.

Run all three readers in parallel (different providers, no rate-limit
contention):

    uv run --env-file .env python scripts/rerun_all.py sonnet &
    uv run --env-file .env python scripts/rerun_all.py gpt4.1 &
    uv run --env-file .env python scripts/rerun_all.py gemini &
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
JUDGES = {
    "opus":    "claude-opus-4-7",
    "sonnet":  "claude-sonnet-4-6",
    "haiku":   "claude-haiku-4-5-20251001",
    "gpt-4o":  "gpt-4o",
    "gpt-4.1": "gpt-4.1",
}
VIEWS = ["full", "masked", "masked_spec"]
LANGS = ("aver", "python_from_aver")  # python_oop unchanged by canonical fix — preserve v1 rows
MERGED = Path("results/merged")


def list_files(d):
    return sorted(p for p in d.iterdir() if p.is_file())


def mask_for_view(lang, src, view):
    if view == "full":
        return src
    if view == "masked":
        return mask(lang, src)
    if view == "masked_spec":
        if lang == "aver":
            return mask("aver_keep_verify", src)
        return mask(lang, src)
    raise ValueError(view)


def combine_diffs(before_dir, after_dir, lang, view):
    diffs = []
    b = {p.name: p for p in list_files(before_dir)} if before_dir.exists() else {}
    a = {p.name: p for p in list_files(after_dir)} if after_dir.exists() else {}
    for name in sorted(set(b) | set(a)):
        bp, ap = b.get(name), a.get(name)
        bs = mask_for_view(lang, bp.read_text(), view) if bp else ""
        as_ = mask_for_view(lang, ap.read_text(), view) if ap else ""
        d = "\n".join(difflib.unified_diff(
            bs.splitlines(), as_.splitlines(),
            fromfile=f"a/{view}/{name}", tofile=f"b/{view}/{name}", lineterm=""))
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


def discover_plan():
    """Return list of (program, prompt, lang) for every after dir that exists."""
    plan = []
    programs_root = Path("programs")
    prompts_root = Path("prompts")
    for prog_dir in sorted(programs_root.iterdir()):
        if not prog_dir.is_dir():
            continue
        prog = prog_dir.name
        prompts_dir = prompts_root / prog
        if not prompts_dir.is_dir():
            continue
        for prompt_md in sorted(prompts_dir.glob("*.md")):
            prompt_name = prompt_md.stem
            for lang in LANGS:
                after_dir = prog_dir / lang / "after" / prompt_name
                if after_dir.is_dir():
                    plan.append((prog, prompt_name, lang))
    return plan


def main():
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("need API keys", file=sys.stderr); return 1
    reader = sys.argv[1]
    model_b = READER_MODELS[reader]
    plan = discover_plan()
    print(f"Reader: {reader}  LLM-B: {model_b}")
    print(f"Plan: {len(plan)} (program, prompt, lang) triples × {len(VIEWS)} views = {len(plan) * len(VIEWS)} slices")

    # Resume support: load existing merged, skip keys already present
    out_path = MERGED / f"{reader}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Start with empty merged — v2 rerun, fresh
    merged = {}
    done_keys = set()
    if "--resume" in sys.argv and out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            k = (r["program"], r["prompt"], r["lang"], r["view"])
            merged[k] = r
            done_keys.add(k)
        print(f"Resume: {len(done_keys)} slices already in merged")

    i = 0
    total = len(plan) * len(VIEWS)
    for (prog, prompt_name, lang) in plan:
        prompt_text = (Path("prompts") / prog / f"{prompt_name}.md").read_text()
        before_dir = Path("programs") / prog / lang / "before"
        after_dir = Path("programs") / prog / lang / "after" / prompt_name
        for view in VIEWS:
            i += 1
            key = (prog, prompt_name, lang, view)
            key_s = f"{prog}/{prompt_name}/{lang}/{view}"
            if key in done_keys:
                print(f"[{i}/{total}] {key_s}  SKIP (resumed)", flush=True)
                continue
            print(f"[{i}/{total}] {key_s}", flush=True)
            try:
                diff = combine_diffs(before_dir, after_dir, lang, view)
                if not diff.strip():
                    print("    empty diff — skip"); continue
                guess = llm_b(model_b, lang, diff)
                print(f"    guess: {guess.get('intent','')[:80]}")
                jp = ensemble(judge_prompt_axis, prompt_text, guess["intent"])
                jd = ensemble(judge_diff_axis, diff, guess["intent"])
                slice_obj = {
                    "program": prog, "prompt": prompt_name,
                    "lang": lang, "view": view,
                    "diff": diff, "guess": guess,
                    "judgment_prompt": jp, "judgment_diff": jd,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                merged[key] = slice_obj
                # Write incrementally so crashes preserve progress
                tmp = out_path.with_suffix(".jsonl.tmp")
                with tmp.open("w") as f:
                    for r in merged.values():
                        f.write(json.dumps(r) + "\n")
                tmp.replace(out_path)
                print(f"    P={jp.get('median','-')} D={jd.get('median','-')}")
            except Exception as e:
                print(f"    ERROR: {type(e).__name__}: {e}", flush=True)

    print(f"\nDone. {len(merged)} slices in {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
