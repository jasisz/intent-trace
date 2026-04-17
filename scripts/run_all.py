#!/usr/bin/env python3
"""Run intent-trace over before/after snapshots with ensemble judges.

Directory layout:
    programs/<program>/<lang>/before/*.<ext>
    programs/<program>/<lang>/after/<prompt>/*.<ext>
    prompts/<program>/<prompt>.md

Pipeline: reads before & after from disk, generates diff, asks LLM-B to guess
intent, scores guess via ensemble (Opus + Sonnet + Haiku) on three axes.

No LLM-A: changes are expected to be produced externally by a real agent loop
(Claude Code, subagent, human, whatever). Pipeline only measures read-side.

Output: results/<run_id>/slices.jsonl (append per slice, crash-safe, resumable).

Usage:
    python scripts/run_all.py
    python scripts/run_all.py --programs inventory
    python scripts/run_all.py --resume results/ensemble_20260416_XXXXXX
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
from intent_trace.judges import (
    ensemble,
    judge_prompt_axis,
    judge_diff_axis,
    judge_quality_axis,
)

MODEL_B = os.environ.get("INTENT_TRACE_MODEL_B", "claude-sonnet-4-6")

JUDGE_MODELS = {
    "opus": os.environ.get("INTENT_TRACE_JUDGE_OPUS", "claude-opus-4-7"),
    "sonnet": os.environ.get("INTENT_TRACE_JUDGE_SONNET", "claude-sonnet-4-6"),
    "haiku": os.environ.get("INTENT_TRACE_JUDGE_HAIKU", "claude-haiku-4-5-20251001"),
}

client = Anthropic()


def list_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file())


def combine_diffs(before_dir: Path, after_dir: Path, label: str, transform=None) -> str:
    """Build a combined unified diff over all files in before_dir vs after_dir.

    Files present only in one side are emitted as full additions or deletions.
    transform(path, source) -> str is applied to both sides if provided.
    """
    diffs: list[str] = []
    before_files = {p.name: p for p in list_files(before_dir)} if before_dir.exists() else {}
    after_files = {p.name: p for p in list_files(after_dir)} if after_dir.exists() else {}
    all_names = sorted(set(before_files) | set(after_files))
    for name in all_names:
        before_path = before_files.get(name)
        after_path = after_files.get(name)
        before = before_path.read_text() if before_path else ""
        after = after_path.read_text() if after_path else ""
        if transform is not None:
            before = transform(before_path, before) if before_path else ""
            after = transform(after_path, after) if after_path else ""
        diff = "\n".join(difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{label}/{name}",
            tofile=f"b/{label}/{name}",
            lineterm="",
        ))
        if diff.strip():
            diffs.append(diff)
    return "\n".join(diffs)


def llm_b_guess(lang: str, diff: str) -> dict:
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
    r = client.messages.create(
        model=MODEL_B, max_tokens=512, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = r.content[0].text.strip()
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


def discover(programs_root: Path, prompts_root: Path, filter_names: list[str] | None) -> list[tuple[str, list[str]]]:
    """Return [(program_name, [prompt_names])] where after/<prompt>/ exists in at least one lang."""
    out: list[tuple[str, list[str]]] = []
    for pd in sorted(programs_root.iterdir()):
        if not pd.is_dir():
            continue
        if filter_names and pd.name not in filter_names:
            continue
        prompts_dir = prompts_root / pd.name
        if not prompts_dir.is_dir():
            continue
        ready_prompts: list[str] = []
        for pf in sorted(prompts_dir.glob("*.md")):
            name = pf.stem
            has_any = any((pd / lang / "after" / name).is_dir() for lang in ("aver", "python"))
            if has_any:
                ready_prompts.append(name)
        if ready_prompts:
            out.append((pd.name, ready_prompts))
    return out


def slice_key(program: str, prompt: str, lang: str, view: str) -> str:
    return f"{program}::{prompt}::{lang}::{view}"


def load_done(jsonl_path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not jsonl_path.exists():
        return done
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = slice_key(r["program"], r["prompt"], r["lang"], r["view"])
            done[k] = r
    return done


def append_slice(jsonl_path: Path, slice_obj: dict) -> None:
    with jsonl_path.open("a") as f:
        f.write(json.dumps(slice_obj) + "\n")


def process_one(
    programs_root: Path,
    prompts_root: Path,
    program: str,
    prompt_name: str,
    lang: str,
    jsonl_path: Path,
    done: dict[str, dict],
) -> None:
    before_dir = programs_root / program / lang / "before"
    after_dir = programs_root / program / lang / "after" / prompt_name
    if not after_dir.is_dir():
        print(f"    [{lang}] skipped (no after dir)", flush=True)
        return

    prompt_text = (prompts_root / program / f"{prompt_name}.md").read_text()

    views = ["full", "masked"]
    for view in views:
        k = slice_key(program, prompt_name, lang, view)
        if k in done:
            print(f"    [{lang}/{view}] skipped (resume)", flush=True)
            continue

        if view == "full":
            diff = combine_diffs(before_dir, after_dir, view, transform=None)
        else:  # masked
            diff = combine_diffs(before_dir, after_dir, view, transform=lambda _p, s: mask(lang, s))

        if not diff.strip():
            print(f"    [{lang}/{view}] empty diff, skipping", flush=True)
            continue

        print(f"    [{lang}/{view}] LLM-B...", flush=True)
        guess = llm_b_guess(lang, diff)

        print(f"    [{lang}/{view}] ensemble prompt-axis...", flush=True)
        j_prompt = ensemble(judge_prompt_axis, client, JUDGE_MODELS, prompt_text, guess["intent"])
        print(f"    [{lang}/{view}] ensemble diff-axis...", flush=True)
        j_diff = ensemble(judge_diff_axis, client, JUDGE_MODELS, diff, guess["intent"])

        slice_obj = {
            "program": program,
            "prompt": prompt_name,
            "lang": lang,
            "view": view,
            "diff": diff,
            "guess": guess,
            "judgment_prompt": j_prompt,
            "judgment_diff": j_diff,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        append_slice(jsonl_path, slice_obj)
        done[k] = slice_obj

    # Quality axis: per (prompt, lang), from full diff only (ablation independent)
    q_key = slice_key(program, prompt_name, lang, "__quality__")
    if q_key not in done:
        full_diff = combine_diffs(before_dir, after_dir, "full", transform=None)
        if full_diff.strip():
            print(f"    [{lang}] ensemble quality-axis...", flush=True)
            j_qual = ensemble(judge_quality_axis, client, JUDGE_MODELS, prompt_text, full_diff)
            q_obj = {
                "program": program,
                "prompt": prompt_name,
                "lang": lang,
                "view": "__quality__",
                "judgment_quality": j_qual,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            append_slice(jsonl_path, q_obj)
            done[q_key] = q_obj

    # Attach quality to full/masked slices for easy aggregation
    q = done.get(q_key)
    if q:
        for view in views:
            k = slice_key(program, prompt_name, lang, view)
            if k in done and "judgment_quality" not in done[k]:
                done[k]["judgment_quality"] = q["judgment_quality"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--programs", nargs="*", help="filter to specific program names")
    ap.add_argument("--resume", help="path to existing run dir")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    programs_root = Path("programs")
    prompts_root = Path("prompts")
    plan = discover(programs_root, prompts_root, args.programs)
    if not plan:
        print("no (program, prompt) pairs with after/ dirs found", file=sys.stderr)
        return 1

    run_dir = Path(args.resume) if args.resume else Path("results") / f"ensemble_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    jsonl_path = run_dir / "slices.jsonl"
    success_marker = run_dir / "SUCCESS"

    meta = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "models": {"b": MODEL_B, "judges": JUDGE_MODELS},
        "plan": [{"program": p, "prompts": prompts} for p, prompts in plan],
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    if success_marker.exists():
        success_marker.unlink()

    done = load_done(jsonl_path)
    n_slices = sum(len(prompts) for _, prompts in plan) * 2 * 2  # lang * view
    print(f"Run dir: {run_dir}")
    print(f"Plan:    {len(plan)} programs, ~{n_slices} slices")
    print(f"Resume:  {sum(1 for k in done if not k.endswith('__quality__'))} slices already done")
    print(f"LLM-B:   {MODEL_B}")
    print(f"Judges:  {', '.join(f'{k}={v}' for k, v in JUDGE_MODELS.items())}")
    print()

    for program, prompts in plan:
        # Auto-detect languages present: aver + any python* variants.
        prog_dir = programs_root / program
        langs = [d.name for d in sorted(prog_dir.iterdir())
                 if d.is_dir() and (d.name == "aver" or d.name.startswith("python"))]
        for prompt_name in prompts:
            print(f"[{program} / {prompt_name}]  langs={langs}", flush=True)
            for lang in langs:
                try:
                    process_one(programs_root, prompts_root, program, prompt_name, lang, jsonl_path, done)
                except Exception as e:
                    print(f"    ERROR ({lang}): {type(e).__name__}: {e}", flush=True)
            print(flush=True)

    success_marker.touch()
    print(f"Done. slices.jsonl: {jsonl_path}")

    # Aggregate (skip __quality__ rows)
    measured_views = ("full", "masked")
    measured = [r for r in done.values() if r["view"] in measured_views]
    all_langs = sorted({r["lang"] for r in measured})
    print()
    print("Aggregate (ensemble medians):")
    for view in measured_views:
        for lang in all_langs:
            rows = [r for r in measured if r["lang"] == lang and r["view"] == view]
            if not rows:
                continue
            avg_p = sum(r["judgment_prompt"]["median"] for r in rows) / len(rows)
            avg_d = sum(r["judgment_diff"]["median"] for r in rows) / len(rows)
            gap = avg_d - avg_p
            q_rows = [r for r in rows if "judgment_quality" in r]
            avg_q = sum(r["judgment_quality"]["median"] for r in q_rows) / len(q_rows) if q_rows else float("nan")
            print(f"  {lang:<7} {view:<7} N={len(rows):3}  prompt={avg_p:.2f}  diff={avg_d:.2f}  "
                  f"quality={avg_q:.2f}  gap(d-p)={gap:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
