#!/usr/bin/env python3
"""One-off helper: run opus reader on python_oop slices.

rerun_all.py has LANGS=(aver, python_from_aver) — python_oop is preserved
from v1 for other readers. But opus is a NEW reader (no v1 data), so it
needs its own python_oop pass. This script fills that gap.

Delete after opus/python_oop rows are populated.

Usage: python scripts/run_opus_oop.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Reuse everything from rerun_all
sys.path.insert(0, str(Path(__file__).parent))
from rerun_all import (
    READER_MODELS, JUDGES, VIEWS, MERGED,
    combine_diffs, llm_b, ensemble,
)
from intent_trace.judges import judge_prompt_axis, judge_diff_axis


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("need API keys", file=sys.stderr); return 1
    reader = "opus"
    model_b = READER_MODELS[reader]
    # Only python_oop — discover locally (rerun_all.discover_plan excludes python_oop)
    plan = []
    for prog_dir in sorted(Path("programs").iterdir()):
        if not prog_dir.is_dir(): continue
        prog = prog_dir.name
        prompts_dir = Path("prompts") / prog
        if not prompts_dir.is_dir(): continue
        for prompt_md in sorted(prompts_dir.glob("*.md")):
            prompt_name = prompt_md.stem
            after_dir = prog_dir / "python_oop" / "after" / prompt_name
            if after_dir.is_dir():
                plan.append((prog, prompt_name, "python_oop"))
    print(f"Reader: {reader}  Lang: python_oop only")
    print(f"Plan: {len(plan)} triples × {len(VIEWS)} views = {len(plan) * len(VIEWS)} slices")

    out_path = MERGED / f"{reader}.jsonl"
    merged = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            merged[(r["program"], r["prompt"], r["lang"], r["view"])] = r

    done_keys = set(merged.keys())
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
                print(f"[{i}/{total}] {key_s}  SKIP", flush=True)
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
