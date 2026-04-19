#!/usr/bin/env python3
"""Stratify the 18 prompts into diff-type categories, reanalyze by category.

Category encoding (read once from prompts, hand-classified, reproducible):
  architectural — extraction / decomposition / new transactional abstraction
  additive      — adds a new field/state/feature without restructuring
  data-model    — restructures the shape of existing data
  vague         — directive without a specific form (requires reviewer inference)

Question: is Aver's payment_ops loss uniform across types, or concentrated?
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

PROMPT_CATEGORY = {
    # inventory
    ("inventory", "atomic_batch_operations"):        "architectural",
    ("inventory", "extract_reservation_module"):     "architectural",
    ("inventory", "make_harder_to_lose_stock"):      "vague",
    ("inventory", "per_warehouse_reorder_points"):   "data-model",
    ("inventory", "track_expiration_dates"):         "data-model",
    # payment_ops
    ("payment_ops", "add_multi_currency_support"):   "additive",
    ("payment_ops", "atomic_batch_ingest"):          "architectural",
    ("payment_ops", "case_priority_levels"):         "additive",
    ("payment_ops", "event_sourcing_rebuild"):       "architectural",
    # taskmanager
    ("taskmanager", "add_priority"):                 "additive",
    ("taskmanager", "delegate_permissions"):         "additive",
    ("taskmanager", "project_archival"):             "additive",
    ("taskmanager", "task_dependencies"):            "additive",
    # workflow
    ("workflow", "add_withdraw_action"):             "additive",
    ("workflow", "approval_expiration"):             "additive",
    ("workflow", "categorize_rejection_reasons"):    "additive",    # string → enum, still additive
    ("workflow", "multi_approver_chain"):            "architectural",
    ("workflow", "separate_audit_from_state"):       "architectural",
}

CATEGORIES = ["architectural", "additive", "data-model", "vague"]

READERS = {
    "Sonnet":  "results/merged/sonnet.jsonl",
    "gpt-4.1": "results/merged/gpt4.1.jsonl",
    "Gemini":  "results/merged/gemini.jsonl",
    "Kimi K2": "results/merged/kimi.jsonl",
}
LANGS = ["aver", "python_from_aver", "python_oop"]


def load_full(path: str) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("view") == "full"]


def cell_score(r: dict) -> float:
    return (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2


def main() -> None:
    print("\n=== Prompt distribution per category ===\n")
    cat_counts = defaultdict(lambda: defaultdict(int))
    for (prog, _), cat in PROMPT_CATEGORY.items():
        cat_counts[prog][cat] += 1
    print(f'{"program":<14} {"architectural":>14} {"additive":>10} {"data-model":>12} {"vague":>8}')
    for prog in ("inventory", "workflow", "taskmanager", "payment_ops"):
        c = cat_counts[prog]
        print(f'{prog:<14} {c["architectural"]:>14} {c["additive"]:>10} {c["data-model"]:>12} {c["vague"]:>8}')

    print("\n=== Per (category, lang) scores — pooled across readers, full view ===\n")
    print(f'{"category":<16} {"n_prompts":>10}  ' + "  ".join(f'{l:>16}' for l in LANGS))
    agg_all = defaultdict(lambda: defaultdict(list))
    for reader_path in READERS.values():
        for r in load_full(reader_path):
            key = (r["program"], r["prompt"])
            cat = PROMPT_CATEGORY.get(key)
            if cat is None:
                continue
            agg_all[cat][r["lang"]].append(cell_score(r))

    for cat in CATEGORIES:
        n = sum(1 for k, v in PROMPT_CATEGORY.items() if v == cat)
        lang_means = []
        for lang in LANGS:
            vals = agg_all[cat][lang]
            lang_means.append(f'{mean(vals):.2f} (n={len(vals):>3})' if vals else "  —")
        print(f'{cat:<16} {n:>10}  ' + "  ".join(f'{m:>16}' for m in lang_means))

    print("\n=== Per (program, category, lang) — focus on payment_ops split ===\n")
    print(f'{"program":<14} {"category":<16} {"lang":<18} {"n":>4} {"mean":>6}')
    per_prog = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for path in READERS.values():
        for r in load_full(path):
            key = (r["program"], r["prompt"])
            cat = PROMPT_CATEGORY.get(key)
            if cat is None:
                continue
            per_prog[r["program"]][cat][r["lang"]].append(cell_score(r))
    for prog in ("inventory", "workflow", "taskmanager", "payment_ops"):
        for cat in CATEGORIES:
            for lang in LANGS:
                vals = per_prog[prog][cat][lang]
                if vals:
                    print(f'{prog:<14} {cat:<16} {lang:<18} {len(vals):>4} {mean(vals):>6.2f}')
            if any(per_prog[prog][cat][lang] for lang in LANGS):
                print()

    print("\n=== Key question: Aver's payment_ops loss by category ===\n")
    print("Δ (python_from_aver − aver) on payment_ops, full view, pooled across readers:\n")
    print(f'{"category":<16} {"aver":>8} {"pfa":>8} {"python_oop":>12} {"Δ pfa−aver":>12} {"Δ oop−aver":>12}')
    for cat in CATEGORIES:
        a_vals = per_prog["payment_ops"][cat]["aver"]
        pfa_vals = per_prog["payment_ops"][cat]["python_from_aver"]
        oop_vals = per_prog["payment_ops"][cat]["python_oop"]
        if not (a_vals and pfa_vals):
            continue
        a = mean(a_vals)
        pfa = mean(pfa_vals)
        oop = mean(oop_vals) if oop_vals else float("nan")
        print(f'{cat:<16} {a:>8.2f} {pfa:>8.2f} {oop:>12.2f} {pfa-a:>+12.2f} '
              f'{(oop-a) if oop_vals else float("nan"):>+12.2f}')


if __name__ == "__main__":
    main()
