#!/usr/bin/env python3
"""Merge resume-run outputs into a single clean slices.jsonl per reader.

The add_gpt_judge.py --resume pipeline appends rows; when a slice got judged
by gpt-4o in one run and gpt-4.1 in another, both rows exist with partial
individual maps. This merges them into one row per (program, prompt, lang, view)
with the union of individual judgments and recomputed median/mean/spread.

Also normalizes the legacy judge key "gpt" → "gpt-4o".
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

READERS = {
    "sonnet":  ["results/gpt_added_20260417_122850/slices.jsonl"],
    "gpt4.1":  ["results/gpt_added_20260417_135834/slices.jsonl",
                "results/gpt_added_20260417_141601/slices.jsonl"],
    "gemini":  ["results/gpt_added_20260417_135925/slices.jsonl",
                "results/gpt_added_20260417_141603/slices.jsonl"],
}

OUT_DIR = Path("results/merged")


def normalize(ind: dict) -> dict:
    if "gpt" in ind and "gpt-4o" not in ind:
        ind = dict(ind)
        ind["gpt-4o"] = ind.pop("gpt")
    return ind


def recompute(axis: dict) -> dict:
    ind = axis.get("individual", {})
    scores = [v["score"] for v in ind.values() if v.get("score", -1) >= 0]
    out = dict(axis)
    if scores:
        out["median"] = round(statistics.median(scores), 1)
        out["mean"] = round(statistics.mean(scores), 2)
        out["spread"] = max(scores) - min(scores)
    return out


def merge_paths(paths: list[str]) -> dict:
    merged: dict = {}
    for p in paths:
        for line in Path(p).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            k = (r["program"], r["prompt"], r["lang"], r["view"])
            if k not in merged:
                r2 = dict(r)
                for ax in ("judgment_prompt", "judgment_diff", "judgment_quality"):
                    if ax in r2 and r2[ax].get("individual"):
                        r2[ax] = dict(r2[ax])
                        r2[ax]["individual"] = normalize(r2[ax]["individual"])
                        r2[ax] = recompute(r2[ax])
                merged[k] = r2
            else:
                old = merged[k]
                for ax in ("judgment_prompt", "judgment_diff", "judgment_quality"):
                    if ax in r and ax in old:
                        old_ind = normalize(old[ax].get("individual", {}))
                        new_ind = normalize(r[ax].get("individual", {}))
                        old_ind.update(new_ind)
                        old[ax] = dict(old[ax])
                        old[ax]["individual"] = old_ind
                        old[ax] = recompute(old[ax])
                merged[k] = old
    return merged


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for reader, paths in READERS.items():
        merged = merge_paths(paths)
        out_path = OUT_DIR / f"{reader}.jsonl"
        with out_path.open("w") as f:
            for r in merged.values():
                f.write(json.dumps(r) + "\n")
        prompt_rows = [r for r in merged.values()
                       if r["view"] in ("full", "masked")
                       and r.get("judgment_prompt", {}).get("individual")]
        full_6 = sum(1 for r in prompt_rows
                     if len(r["judgment_prompt"]["individual"]) >= 6)
        print(f"{reader:8}  total={len(merged)}  prompt/diff-rows={len(prompt_rows)}  "
              f"with-all-6-judges={full_6}  →  {out_path}")


if __name__ == "__main__":
    main()
