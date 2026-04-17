#!/usr/bin/env python3
"""Rerun a single (program, prompt) slice for all readers, langs, and views.

Usage:
    python scripts/rerun_slice.py <program> <prompt>
Example:
    python scripts/rerun_slice.py workflow add_withdraw_action

Drops matching rows from results/merged/{sonnet,gpt4.1,gemini}.jsonl and
regenerates diff + LLM-B guess + 5-judge ensemble for each (lang, view)
combination, then writes back in place.
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


def load_merged(reader):
    path = MERGED / f"{reader}.jsonl"
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["program"], r["prompt"], r["lang"], r["view"])
        out[k] = r
    return out


def write_merged(reader, rows_dict):
    path = MERGED / f"{reader}.jsonl"
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in rows_dict.values():
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def main():
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("need API keys", file=sys.stderr); return 1
    program = sys.argv[1]
    prompt_name = sys.argv[2]
    print(f"Rerunning {program}/{prompt_name} for all readers × langs × views")

    prompt_text = (Path("prompts") / program / f"{prompt_name}.md").read_text()
    prog_dir = Path("programs") / program
    langs = [d.name for d in sorted(prog_dir.iterdir())
             if d.is_dir() and (d.name == "aver" or d.name.startswith("python"))]
    # Only aver and python_from_aver for this pilot (python_oop unchanged)
    langs = [l for l in langs if l in ("aver", "python_from_aver")]
    print(f"Langs: {langs}")

    for reader, model_b in READER_MODELS.items():
        print(f"\n=== reader={reader} (LLM-B={model_b}) ===")
        current = load_merged(reader)
        # Drop old rows for this (program, prompt) but keep others
        for k in list(current.keys()):
            if k[0] == program and k[1] == prompt_name and k[2] in langs:
                print(f"  dropping old: {'/'.join(k)}")
                del current[k]

        for lang in langs:
            before_dir = prog_dir / lang / "before"
            after_dir = prog_dir / lang / "after" / prompt_name
            for view in VIEWS:
                print(f"  [{lang}/{view}] diff + LLM-B + ensemble", flush=True)
                try:
                    diff = combine_diffs(before_dir, after_dir, lang, view)
                    if not diff.strip():
                        print(f"    empty diff — skip"); continue
                    guess = llm_b(model_b, lang, diff)
                    print(f"    guess: {guess.get('intent','')[:90]}")
                    jp = ensemble(judge_prompt_axis, prompt_text, guess["intent"])
                    jd = ensemble(judge_diff_axis, diff, guess["intent"])
                    slice_obj = {
                        "program": program, "prompt": prompt_name,
                        "lang": lang, "view": view,
                        "diff": diff, "guess": guess,
                        "judgment_prompt": jp, "judgment_diff": jd,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    current[(program, prompt_name, lang, view)] = slice_obj
                    print(f"    P={jp.get('median','-')} D={jd.get('median','-')}")
                except Exception as e:
                    print(f"    ERROR: {type(e).__name__}: {e}")

        write_merged(reader, current)
        print(f"  written: {MERGED/f'{reader}.jsonl'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
