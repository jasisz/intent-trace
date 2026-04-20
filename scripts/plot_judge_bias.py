#!/usr/bin/env python3
"""Extra plots focused on the 5-judge cross-vendor findings:

1. judge_reader_heatmap.png — 5 judges × 3 readers grid, showing per-cell means
2. judges_3v5.png           — 3-judge (Claude-only) vs 5-judge per-reader ranking shift
3. judge_family_x_lang.png  — Claude-3 vs OpenAI-2 judge family means per language
4. ablation_all_readers.png — full→masked drop per language, all 3 readers side-by-side
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

AMBER = "#d97706"
AMBER_LIGHT = "#f59e0b"
AMBER_DARK = "#92400e"
SLATE = "#1e293b"
SLATE_MUTED = "#64748b"
SLATE_LIGHT = "#e2e8f0"
BG = "#f8fafc"

mpl.rcParams.update({
    "font.family": ["Helvetica Neue", "Arial", "sans-serif"],
    "axes.edgecolor": SLATE,
    "axes.labelcolor": SLATE,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "text.color": SLATE,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
})

READERS = {
    "Sonnet": "results/merged/sonnet.jsonl",
    "Opus": "results/merged/opus.jsonl",
    "gpt-4.1": "results/merged/gpt4.1.jsonl",
    "Gemini": "results/merged/gemini.jsonl",
    "Kimi K2": "results/merged/kimi.jsonl",
}
JUDGES = ["opus", "sonnet", "haiku", "gpt-4o", "gpt-4.1", "kimi"]
JUDGE_FAMILY = {
    "opus": "Claude", "sonnet": "Claude", "haiku": "Claude",
    "gpt-4o": "OpenAI", "gpt-4.1": "OpenAI",
    "kimi": "Moonshot",
}
CLAUDE = ("opus", "sonnet", "haiku")
GPT = ("gpt-4o", "gpt-4.1")
LANGS = ["aver", "python_from_aver", "python_oop"]
LANG_COLORS = {"aver": AMBER, "python_from_aver": SLATE, "python_oop": SLATE_MUTED}
# Masked variants use lighter shades so full/masked are visually distinct
# (hatching alone is invisible on darker colors)
LANG_COLORS_MASKED = {
    "aver": "#fbbf24",            # light amber (pale orange, clearly distinct from AMBER)
    "python_from_aver": "#94a3b8",  # slate-400 — light enough for SLATE hatch to be visible
    "python_oop": "#e2e8f0",        # slate-200 — very light for the muted baseline
}
LANG_LABELS = {"aver": "Aver", "python_from_aver": "Python (from Aver)", "python_oop": "Python (OOP)"}


def load_rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def judge_reader_heatmap(out_path: Path) -> None:
    """5 judges × 3 readers heatmap; cells are mean (P+D)/2 over all full+masked slices."""
    matrix = np.zeros((len(JUDGES), len(READERS)))
    for i, judge in enumerate(JUDGES):
        for j, (_, path) in enumerate(READERS.items()):
            rows = [r for r in load_rows(path) if r.get("view") in ("full", "masked")]
            vals = []
            for r in rows:
                p = r["judgment_prompt"]["individual"].get(judge, {}).get("score", -1)
                d = r["judgment_diff"]["individual"].get(judge, {}).get("score", -1)
                if p >= 0 and d >= 0:
                    vals.append((p + d) / 2)
            matrix[i, j] = mean(vals) if vals else np.nan

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    vmin, vmax = 7.5, 9.0
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "aver", ["#f5f5f4", "#fef3c7", AMBER_LIGHT, AMBER, "#92400e"],
    )
    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)

    ax.set_xticks(range(len(READERS)))
    ax.set_xticklabels(READERS.keys(), fontsize=11, fontweight="bold")
    ax.set_yticks(range(len(JUDGES)))
    ax.set_yticklabels(JUDGES, fontsize=10, family="monospace")
    ax.set_xlabel("Reader (LLM-B)", fontweight="bold")
    ax.set_ylabel("Judge", fontweight="bold")

    # Family separators: Claude (top 3) vs OpenAI (bottom 2)
    ax.axhline(2.5, color=SLATE, linewidth=1.2)
    # Subtle own-family markers
    for (i, judge) in enumerate(JUDGES):
        for j, reader in enumerate(READERS):
            v = matrix[i, j]
            if np.isnan(v):
                continue
            txt_color = "white" if v > 8.5 else SLATE
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color=txt_color, fontsize=11, fontweight="bold")

    # Annotate family groups
    ax.text(-0.85, 1.0, "Claude\njudges", rotation=90, va="center", ha="center",
            fontsize=9, color=SLATE_MUTED, style="italic")
    ax.text(-0.85, 3.5, "OpenAI\njudges", rotation=90, va="center", ha="center",
            fontsize=9, color=SLATE_MUTED, style="italic")

    ax.set_title("Judge × Reader — per-cell mean (P+D)/2\n"
                 "Own-family lift is small (~0.05), well below the ~0.3 noise band",
                 fontweight="bold", fontsize=11, pad=12)
    cb = plt.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    cb.set_label("mean score", fontweight="bold")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def judges_3v5_comparison(out_path: Path) -> None:
    """For each reader, show per-language full-view ranking under 3-judge (Claude-only) vs 5-judge."""
    n = len(READERS)
    fig, axes = plt.subplots(1, n, figsize=(4.7 * n, 5.5), sharey=True)

    for ax, (reader, path) in zip(axes, READERS.items()):
        rows = [r for r in load_rows(path) if r.get("view") == "full"]
        three = defaultdict(list)
        five = defaultdict(list)
        # Match the benchmark's aggregation: median per axis across the judge panel,
        # then (P+D)/2 per slice. That's what the run_all.py ensemble writes and what
        # the README / other plots cite.
        for r in rows:
            def axis_scores(panel, axis):
                vals = [r[axis]["individual"].get(j, {}).get("score", -1) for j in panel]
                return [v for v in vals if v >= 0]

            p_c = axis_scores(CLAUDE, "judgment_prompt")
            d_c = axis_scores(CLAUDE, "judgment_diff")
            if p_c and d_c:
                three[r["lang"]].append((median(p_c) + median(d_c)) / 2)

            p_all = axis_scores(CLAUDE + GPT, "judgment_prompt")
            d_all = axis_scores(CLAUDE + GPT, "judgment_diff")
            if p_all and d_all:
                five[r["lang"]].append((median(p_all) + median(d_all)) / 2)

        langs = LANGS
        x = np.arange(len(langs))
        w = 0.36
        three_vals = [mean(three[l]) for l in langs]
        five_vals = [mean(five[l]) for l in langs]

        b1 = ax.bar(x - w / 2, three_vals, w, color=SLATE_MUTED, edgecolor=SLATE,
                    linewidth=1.0, label="3-judge (Claude only)")
        b2 = ax.bar(x + w / 2, five_vals, w, color=AMBER, edgecolor=SLATE,
                    linewidth=1.0, label="5-judge (cross-vendor)")
        for bars, vals in ((b1, three_vals), (b2, five_vals)):
            for bar, v in zip(bars, vals):
                ax.text(bar.get_x() + bar.get_width() / 2, v + 0.04, f"{v:.2f}",
                        ha="center", va="bottom", fontsize=8, color=SLATE)

        # Callout only on a real reversal: different winner AND the new winner's
        # score jumped by ≥0.10 between 3-judge and 5-judge (i.e. GPT judges
        # meaningfully lifted it, not just tiny noise shuffling).
        top_five = int(np.argmax(five_vals))
        top_three = int(np.argmax(three_vals))
        winner_lift = five_vals[top_five] - three_vals[top_five]
        if top_five != top_three and winner_lift >= 0.10:
            ax.annotate("ranking reversal", xy=(top_five + w / 2, five_vals[top_five]),
                        xytext=(top_five, max(five_vals) + 0.25),
                        fontsize=9, fontweight="bold", color=AMBER_DARK, ha="center",
                        arrowprops=dict(arrowstyle="->", color=AMBER_DARK, lw=1.2))

        ax.set_xticks(x)
        ax.set_xticklabels([LANG_LABELS[l] for l in langs], rotation=12, ha="right", fontsize=9)
        ax.set_title(f"{reader} reader", fontweight="bold", fontsize=11)
        ax.set_ylim(7.8, 9.0)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    axes[0].set_ylabel("Average full-view score (P+D)/2", fontweight="bold")
    axes[0].legend(loc="lower left", fontsize=9, frameon=True,
                   facecolor=BG, edgecolor=SLATE_LIGHT)
    fig.suptitle("Adding GPT judges flips the gpt-4.1 reader's winner (python_oop → aver)",
                 fontweight="bold", fontsize=12, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def judge_family_x_lang(out_path: Path) -> None:
    """Claude-3 vs OpenAI-2 family mean per (lang, view) — shows GPT kinder to Aver."""
    data = {}  # (family, lang, view) → mean
    for family_name, family in [("Claude-3", CLAUDE), ("OpenAI-2", GPT)]:
        for lang in LANGS:
            for view in ("full", "masked"):
                vals = []
                for path in READERS.values():
                    rows = [r for r in load_rows(path)
                            if r.get("view") == view and r["lang"] == lang]
                    for r in rows:
                        for j in family:
                            p = r["judgment_prompt"]["individual"].get(j, {}).get("score", -1)
                            d = r["judgment_diff"]["individual"].get(j, {}).get("score", -1)
                            if p >= 0 and d >= 0:
                                vals.append((p + d) / 2)
                data[(family_name, lang, view)] = mean(vals) if vals else float("nan")

    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(LANGS) * 2)  # per (lang, view)
    labels = []
    for lang in LANGS:
        for view in ("full", "masked"):
            labels.append(f"{LANG_LABELS[lang]}\n{view}")

    claude_vals = []
    gpt_vals = []
    for lang in LANGS:
        for view in ("full", "masked"):
            claude_vals.append(data[("Claude-3", lang, view)])
            gpt_vals.append(data[("OpenAI-2", lang, view)])

    w = 0.4
    b1 = ax.bar(x - w / 2, claude_vals, w, color=SLATE, edgecolor=SLATE, linewidth=1.0,
                label="Claude-3 judges mean")
    b2 = ax.bar(x + w / 2, gpt_vals, w, color=AMBER, edgecolor=SLATE, linewidth=1.0,
                label="OpenAI-2 judges mean")
    for bars, vals in ((b1, claude_vals), (b2, gpt_vals)):
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.04, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)

    # Δ annotations per pair (amber intensity scales with effect size)
    for i, (c, g) in enumerate(zip(claude_vals, gpt_vals)):
        delta = g - c
        color = AMBER_DARK if delta > 0.15 else (AMBER if delta > 0.05 else SLATE_MUTED)
        ax.annotate(f"Δ {delta:+.2f}", xy=(i, max(c, g) + 0.22), ha="center",
                    fontsize=9, fontweight="bold", color=color)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel(f"Mean score (P+D)/2 across all {len(READERS)} readers", fontweight="bold")
    ax.set_title("GPT judges are most lenient on Aver (+0.22), similar on both Python variants (+0.07)",
                 fontweight="bold", fontsize=11, pad=12)
    ax.set_ylim(7.2, 9.2)
    ax.legend(loc="lower right", frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def ablation_all_readers(out_path: Path) -> None:
    """Full → masked drop per language, grouped per reader."""
    n = len(READERS)
    fig, axes = plt.subplots(1, n, figsize=(4.7 * n, 5.5), sharey=True)

    for ax, (reader, path) in zip(axes, READERS.items()):
        rows = load_rows(path)
        full_map = defaultdict(list)
        mask_map = defaultdict(list)
        for r in rows:
            if r.get("view") not in ("full", "masked"):
                continue
            score = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
            if r["view"] == "full":
                full_map[r["lang"]].append(score)
            else:
                mask_map[r["lang"]].append(score)

        langs = LANGS
        x = np.arange(len(langs))
        w = 0.36
        full_vals = [mean(full_map[l]) for l in langs]
        mask_vals = [mean(mask_map[l]) for l in langs]
        deltas = [m - f for f, m in zip(full_vals, mask_vals)]

        b1 = ax.bar(x - w / 2, full_vals, w, color=[LANG_COLORS[l] for l in langs],
                    edgecolor=SLATE, linewidth=1.0, label="full")
        b2 = ax.bar(x + w / 2, mask_vals, w, color=[LANG_COLORS_MASKED[l] for l in langs],
                    edgecolor=SLATE, linewidth=1.0, hatch="////", label="masked")
        for bar, v in zip(b1, full_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.05, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)
        for bar, v in zip(b2, mask_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.05, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)
        # Delta below each pair (amber intensity = drop size)
        for i, d in enumerate(deltas):
            color = AMBER_DARK if d < -0.3 else (AMBER if d < -0.1 else SLATE_MUTED)
            ax.text(i, 7.05, f"Δ {d:+.2f}", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color=color)

        ax.set_xticks(x)
        ax.set_xticklabels([LANG_LABELS[l] for l in langs], rotation=12, ha="right", fontsize=9)
        ax.set_title(f"{reader} reader", fontweight="bold", fontsize=11)
        ax.set_ylim(7.0, 9.0)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    axes[0].set_ylabel("Average score (P+D)/2", fontweight="bold")
    # Custom legend for hatch meaning
    from matplotlib.patches import Patch
    legend_items = [
        Patch(facecolor=SLATE_LIGHT, edgecolor=SLATE, label="full (prose included)"),
        Patch(facecolor=SLATE_LIGHT, edgecolor=SLATE, hatch="////", label="masked (prose stripped)"),
    ]
    axes[-1].legend(handles=legend_items, loc="lower left", fontsize=9,
                    frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
    fig.suptitle("Ablation: masking prose always hurts Aver most, OOP Python never",
                 fontweight="bold", fontsize=12, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


PROGRAMS_ORDERED = ["inventory", "workflow", "taskmanager", "payment_ops"]
PROGRAM_LINES = {"inventory": 250, "workflow": 200, "taskmanager": 400, "payment_ops": 1300}


def program_lang_ranking(out_path: Path) -> None:
    """Per program, (full view) avg across 3 readers per language. Shows which programs favor which style."""
    fig, ax = plt.subplots(figsize=(12.5, 6))

    # per program × lang: mean across readers of (P+D)/2 for full view
    matrix = {prog: {lang: [] for lang in LANGS} for prog in PROGRAMS_ORDERED}
    for reader, path in READERS.items():
        rows = [r for r in load_rows(path) if r.get("view") == "full"]
        for r in rows:
            prog = r["program"]
            if prog not in matrix:
                continue
            score = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
            matrix[prog][r["lang"]].append(score)

    x = np.arange(len(PROGRAMS_ORDERED))
    w = 0.26
    for i, lang in enumerate(LANGS):
        vals = [mean(matrix[p][lang]) for p in PROGRAMS_ORDERED]
        offset = (i - 1) * w
        bars = ax.bar(x + offset, vals, w, color=LANG_COLORS[lang],
                      edgecolor=SLATE, linewidth=1.0, label=LANG_LABELS[lang])
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.03, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)

    ax.set_xticks(x)
    ax.set_xticklabels(
        [f"{p}\n(~{PROGRAM_LINES[p]} lines)" for p in PROGRAMS_ORDERED],
        fontsize=10, fontweight="bold",
    )
    ax.set_ylabel("Full-view score (P+D)/2, averaged across 3 readers", fontweight="bold")
    ax.set_title("Per program: language ranking (higher = more legible to reviewers)",
                 fontweight="bold", fontsize=12, pad=12)
    ax.set_ylim(7.0, 9.2)
    ax.legend(loc="lower left", frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color=SLATE_LIGHT, linewidth=0.5)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def program_complexity_advantage(out_path: Path) -> None:
    """Aver's advantage over python_oop per program (full view), sized by program complexity.

    Shows whether Aver's legibility gap over idiomatic Python grows with program size.
    """
    # gather per (program, lang) mean across readers
    data = defaultdict(lambda: {"aver": [], "python_from_aver": [], "python_oop": []})
    for path in READERS.values():
        rows = [r for r in load_rows(path) if r.get("view") == "full"]
        for r in rows:
            score = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
            data[r["program"]][r["lang"]].append(score)

    fig, ax = plt.subplots(figsize=(10, 6))
    aver_advantage_vs_oop = []
    aver_advantage_vs_pfa = []
    sizes = []
    progs = []
    for prog in PROGRAMS_ORDERED:
        d = data[prog]
        a = mean(d["aver"])
        pfa = mean(d["python_from_aver"])
        oop = mean(d["python_oop"])
        aver_advantage_vs_oop.append(a - oop)
        aver_advantage_vs_pfa.append(a - pfa)
        sizes.append(PROGRAM_LINES[prog])
        progs.append(prog)

    x = np.arange(len(progs))
    w = 0.36
    b1 = ax.bar(x - w / 2, aver_advantage_vs_oop, w, color=AMBER, edgecolor=SLATE,
                linewidth=1.0, label="Aver − Python (OOP)")
    b2 = ax.bar(x + w / 2, aver_advantage_vs_pfa, w, color=SLATE_MUTED, edgecolor=SLATE,
                linewidth=1.0, label="Aver − Python (from Aver)")
    # Amber = Aver wins, Slate = Python wins, Slate-muted = tie
    for bars, vals in ((b1, aver_advantage_vs_oop), (b2, aver_advantage_vs_pfa)):
        for bar, v in zip(bars, vals):
            y = v + (0.03 if v >= 0 else -0.08)
            if v > 0.1:
                color = AMBER_DARK
            elif v < -0.1:
                color = SLATE
            else:
                color = SLATE_MUTED
            ax.text(bar.get_x() + bar.get_width() / 2, y, f"{v:+.2f}",
                    ha="center", va="bottom" if v >= 0 else "top",
                    fontsize=9, fontweight="bold", color=color)

    ax.axhline(0, color=SLATE, linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [f"{p}\n~{PROGRAM_LINES[p]} lines" for p in progs],
        fontsize=10, fontweight="bold",
    )
    ax.set_ylabel("Aver advantage (Δ score, full view, averaged over readers)",
                  fontweight="bold")
    ax.set_title("Does Aver's legibility edge grow with program complexity?\n"
                 "(positive bars = Aver reads better than the Python variant)",
                 fontweight="bold", fontsize=11, pad=12)
    ax.set_ylim(-0.7, 0.9)
    ax.legend(loc="upper left", frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color=SLATE_LIGHT, linewidth=0.5)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def program_reader_heatmap(out_path: Path) -> None:
    """12 rows (program × lang) × 3 readers, full view only. More compact than the
    existing 24-row heatmap that also includes masked."""
    row_keys = []
    row_labels = []
    for prog in PROGRAMS_ORDERED:
        for lang in LANGS:
            row_keys.append((prog, lang))
            row_labels.append(f"{prog:<12} /  {LANG_LABELS[lang]}")

    matrix = np.full((len(row_keys), len(READERS)), np.nan)
    for j, (reader, path) in enumerate(READERS.items()):
        rows = [r for r in load_rows(path) if r.get("view") == "full"]
        bucket = defaultdict(list)
        for r in rows:
            bucket[(r["program"], r["lang"])].append(
                (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2,
            )
        for i, key in enumerate(row_keys):
            vals = bucket.get(key)
            if vals:
                matrix[i, j] = mean(vals)

    fig, ax = plt.subplots(figsize=(7.5, 8))
    vmin, vmax = 7.0, 9.2
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "aver", ["#f5f5f4", "#fef3c7", AMBER_LIGHT, AMBER, "#92400e"],
    )
    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(READERS)))
    ax.set_xticklabels(READERS.keys(), fontweight="bold", fontsize=10)
    ax.set_yticks(range(len(row_keys)))
    ax.set_yticklabels(row_labels, fontsize=9, family="monospace")

    for i in range(len(row_keys)):
        for j in range(len(READERS)):
            v = matrix[i, j]
            if not np.isnan(v):
                txt_color = "white" if v > 8.5 else SLATE
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        color=txt_color, fontsize=9, fontweight="bold")
    # Separators between programs (every 3 langs)
    for p in range(1, len(PROGRAMS_ORDERED)):
        ax.axhline(p * len(LANGS) - 0.5, color=SLATE, linewidth=1.0)

    ax.set_title("Per-program × language × reader (full view only)\n"
                 "Row groups = programs, ordered by size",
                 fontweight="bold", fontsize=11, pad=10)
    cb = plt.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label("avg score", fontweight="bold")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def masked_vs_masked_spec(out_path: Path) -> None:
    """N-reader × 3-lang: masked vs masked_spec, with replication-noise floor
    from Python variants making the Aver delta interpretable."""
    n = len(READERS)
    fig, axes = plt.subplots(1, n, figsize=(4.7 * n, 5.5), sharey=True)

    for ax, (reader, path) in zip(axes, READERS.items()):
        rows = load_rows(path)
        masked = defaultdict(list)
        mspec = defaultdict(list)
        for r in rows:
            if r.get("view") not in ("masked", "masked_spec"):
                continue
            s = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
            if r["view"] == "masked":
                masked[r["lang"]].append(s)
            else:
                mspec[r["lang"]].append(s)

        langs = LANGS
        x = np.arange(len(langs))
        w = 0.38
        # Some readers (e.g. opus) have no masked_spec for pfa/oop because the
        # skip-fix correctly avoids duplicate runs — fall back to masked mean
        # (alias per canonical_load semantics) so the plot stays consistent.
        m_vals = [mean(masked[l]) if masked[l] else 0 for l in langs]
        s_vals = [mean(mspec[l]) if mspec[l] else (mean(masked[l]) if masked[l] else 0) for l in langs]
        deltas = [s - m for m, s in zip(m_vals, s_vals)]

        b1 = ax.bar(x - w / 2, m_vals, w,
                    color=[LANG_COLORS_MASKED[l] for l in langs],
                    edgecolor=SLATE, linewidth=1.0, hatch="////", label="masked")
        b2 = ax.bar(x + w / 2, s_vals, w,
                    color=[LANG_COLORS[l] for l in langs],
                    edgecolor=SLATE, linewidth=1.0, label="masked_spec (keep verify/assert)")
        for bar, v in zip(b1, m_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.04, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)
        for bar, v in zip(b2, s_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.04, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, color=SLATE)
        for i, d in enumerate(deltas):
            color = AMBER_DARK if abs(d) >= 0.25 else (AMBER if abs(d) >= 0.1 else SLATE_MUTED)
            ax.text(i, 7.05, f"Δ {d:+.2f}", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color=color)

        ax.set_xticks(x)
        ax.set_xticklabels([LANG_LABELS[l] for l in langs], rotation=12, ha="right", fontsize=9)
        ax.set_title(f"{reader} reader", fontweight="bold", fontsize=11)
        ax.set_ylim(7.0, 8.8)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    axes[0].set_ylabel("Average score (P+D)/2", fontweight="bold")
    axes[-1].legend(loc="lower right", fontsize=8, frameon=True,
                    facecolor=BG, edgecolor=SLATE_LIGHT)
    fig.suptitle("Verify-preserving ablation: Python Δ = replication-noise floor (±0.2).\n"
                 "Aver Δ sits inside that floor on every reader — verify does not carry legibility.",
                 fontweight="bold", fontsize=11, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def ranking_all_readers_ci(out_path: Path) -> None:
    """Per-reader ranking with 95% bootstrap CI — one subplot per reader."""
    import random
    n = len(READERS)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5.5), sharey=True)
    rng = random.Random(0)

    def ci(vals, n=10000):
        k = len(vals)
        means = []
        for _ in range(n):
            s = [vals[rng.randrange(k)] for _ in range(k)]
            means.append(sum(s) / k)
        means.sort()
        return means[int(n * 0.025)], means[int(n * 0.975)]

    for ax, (reader, path) in zip(axes, READERS.items()):
        rows = [r for r in load_rows(path) if r.get("view") in ("full", "masked")]
        buckets = defaultdict(list)
        for r in rows:
            buckets[(r["lang"], r["view"])].append(
                (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2,
            )
        items = sorted(buckets.items(), key=lambda kv: -mean(kv[1]))
        # Compact labels: short lang + view on separate lines
        short = {"aver": "Aver", "python_from_aver": "py_from_aver", "python_oop": "py_oop"}
        labels = [f"{short[lang]}\n{view}" for (lang, view), _ in items]
        values = [mean(v) for _, v in items]
        cis = [ci(v) for _, v in items]
        err_lo = [m - lo for m, (lo, _) in zip(values, cis)]
        err_hi = [hi - m for m, (_, hi) in zip(values, cis)]
        # Different hatch per lang so masked variants stay distinguishable
        # even when two masked bars sit adjacent.
        HATCH_BY_LANG = {"aver": "////", "python_from_aver": "xxxx", "python_oop": "...."}
        colors = [LANG_COLORS[lang] if view == "full" else LANG_COLORS_MASKED[lang]
                  for (lang, view), _ in items]
        hatches = ["" if view == "full" else HATCH_BY_LANG[lang]
                   for (lang, view), _ in items]

        x_pos = np.arange(len(items))
        bars = ax.bar(x_pos, values, color=colors, edgecolor=SLATE, linewidth=1.0,
                      yerr=[err_lo, err_hi], ecolor=SLATE, capsize=3,
                      error_kw={"linewidth": 0.9})
        for bar, h in zip(bars, hatches):
            bar.set_hatch(h)
        for bar, v, ehi in zip(bars, values, err_hi):
            ax.text(bar.get_x() + bar.get_width() / 2, v + ehi + 0.04, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8, fontweight="bold", color=SLATE)
        ax.set_title(f"{reader} reader", fontweight="bold", fontsize=11)
        ax.set_ylim(7.0, 9.3)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=8)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    axes[0].set_ylabel("Average (P+D)/2 — bars = 95% bootstrap CI", fontweight="bold")
    fig.suptitle("Per-reader ranking with 95% bootstrap CIs (N=18 per cell, 10k resamples).\n"
                 "Top-of-ranking intervals overlap on every reader — visual form of 'parity within noise'.",
                 fontweight="bold", fontsize=11, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


DIFF_TYPE = {
    ("inventory", "atomic_batch_operations"): "architectural",
    ("inventory", "extract_reservation_module"): "architectural",
    ("inventory", "make_harder_to_lose_stock"): "vague",
    ("inventory", "per_warehouse_reorder_points"): "data-model",
    ("inventory", "track_expiration_dates"): "data-model",
    ("payment_ops", "add_multi_currency_support"): "additive",
    ("payment_ops", "atomic_batch_ingest"): "architectural",
    ("payment_ops", "case_priority_levels"): "additive",
    ("payment_ops", "event_sourcing_rebuild"): "architectural",
    ("taskmanager", "add_priority"): "additive",
    ("taskmanager", "delegate_permissions"): "additive",
    ("taskmanager", "project_archival"): "additive",
    ("taskmanager", "task_dependencies"): "additive",
    ("workflow", "add_withdraw_action"): "additive",
    ("workflow", "approval_expiration"): "additive",
    ("workflow", "categorize_rejection_reasons"): "additive",
    ("workflow", "multi_approver_chain"): "architectural",
    ("workflow", "separate_audit_from_state"): "architectural",
}


def diff_type_stratification(out_path: Path) -> None:
    """Pooled category × lang (left) and payment_ops category × lang (right)."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    def collect(filter_prog=None):
        d = defaultdict(lambda: defaultdict(list))
        for path in READERS.values():
            for r in load_rows(path):
                if r.get("view") != "full":
                    continue
                if filter_prog and r["program"] != filter_prog:
                    continue
                cat = DIFF_TYPE.get((r["program"], r["prompt"]))
                if not cat:
                    continue
                s = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
                d[cat][r["lang"]].append(s)
        return d

    for ax, (title, data, cats) in zip(
        axes,
        [("Pooled across all 4 programs", collect(), ["architectural", "additive", "data-model", "vague"]),
         ("payment_ops only (the large-program split)", collect("payment_ops"), ["architectural", "additive"])],
    ):
        x = np.arange(len(cats))
        w = 0.26
        for i, lang in enumerate(LANGS):
            vals = [mean(data[c][lang]) if data[c][lang] else np.nan for c in cats]
            ns = [len(data[c][lang]) for c in cats]
            offset = (i - 1) * w
            bars = ax.bar(x + offset, vals, w, color=LANG_COLORS[lang],
                          edgecolor=SLATE, linewidth=1.0, label=LANG_LABELS[lang])
            for bar, v, n in zip(bars, vals, ns):
                if not np.isnan(v):
                    ax.text(bar.get_x() + bar.get_width() / 2, v + 0.03,
                            f"{v:.2f}\n(n={n})", ha="center", va="bottom",
                            fontsize=7, color=SLATE)
        ax.set_xticks(x)
        ax.set_xticklabels(cats, fontsize=9, fontweight="bold")
        ax.set_title(title, fontweight="bold", fontsize=11, pad=8)
        ax.set_ylim(7.0, 9.4)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    axes[0].set_ylabel("Average (P+D)/2, full view", fontweight="bold")
    axes[0].legend(loc="lower left", fontsize=8, frameon=True,
                   facecolor=BG, edgecolor=SLATE_LIGHT)
    fig.suptitle("Aver's payment_ops gap concentrates on architectural refactors (Δ+0.79),\n"
                 "is much smaller on additive (Δ+0.42). python_oop also drops on architectural — "
                 "it's docstring volume, not language.",
                 fontweight="bold", fontsize=11, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def interjudge_heatmap(out_path: Path) -> None:
    """Two 5×5 Spearman heatmaps (P-axis, D-axis). Shows which judge disagrees
    with which, pooled across all readers and views."""
    from itertools import product
    from scipy.stats import spearmanr

    def pooled_matrix(axis_key):
        """(n_judges × n_items) matrix pooled across readers+views."""
        cols = []
        for path in READERS.values():
            rows = [r for r in load_rows(path)
                    if r.get("view") in ("full", "masked", "masked_spec")
                    and r.get(axis_key, {}).get("individual")]
            mat = np.full((len(JUDGES), len(rows)), np.nan)
            for j, judge in enumerate(JUDGES):
                for i, r in enumerate(rows):
                    sc = r[axis_key]["individual"].get(judge, {}).get("score", -1)
                    if sc is not None and sc >= 0:
                        mat[j, i] = sc
            cols.append(mat)
        return np.concatenate(cols, axis=1)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "aver_corr", ["#f5f5f4", "#fef3c7", AMBER_LIGHT, AMBER, AMBER_DARK],
    )

    for ax, axis_key, title in [
        (axes[0], "judgment_prompt", "P-axis (prompt match)"),
        (axes[1], "judgment_diff", "D-axis (diff fidelity)"),
    ]:
        m = pooled_matrix(axis_key)
        corr = np.full((len(JUDGES), len(JUDGES)), np.nan)
        for a, b in product(range(len(JUDGES)), repeat=2):
            mask = ~(np.isnan(m[a]) | np.isnan(m[b]))
            if mask.sum() >= 3:
                r, _ = spearmanr(m[a][mask], m[b][mask])
                corr[a, b] = r
        im = ax.imshow(corr, cmap=cmap, vmin=0.2, vmax=0.8, aspect="equal")
        ax.set_xticks(range(len(JUDGES)))
        ax.set_yticks(range(len(JUDGES)))
        ax.set_xticklabels(JUDGES, rotation=30, ha="right", fontsize=9)
        ax.set_yticklabels(JUDGES, fontsize=9)
        for i in range(len(JUDGES)):
            for j in range(len(JUDGES)):
                v = corr[i, j]
                if not np.isnan(v):
                    color = "white" if v > 0.6 else SLATE
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=9, fontweight="bold", color=color)
        # Family separators: Claude (0..2) vs OpenAI (3..4)
        ax.axhline(2.5, color=SLATE, linewidth=1.0)
        ax.axvline(2.5, color=SLATE, linewidth=1.0)
        ax.set_title(title, fontweight="bold", fontsize=11, pad=8)
        plt.colorbar(im, ax=ax, shrink=0.7, pad=0.02)

    fig.suptitle("Pairwise Spearman rank correlation between judges (pooled over all readers + views).\n"
                 "D-axis correlations are visibly weaker — the diff-fidelity rubric is the main "
                 "source of judge disagreement.",
                 fontweight="bold", fontsize=11, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def thinking_probe(out_path: Path) -> None:
    """Thinking vs non-thinking readers, same-vendor pairs where possible.

    Current pairs:
      - Kimi K2-0905 (non-thinking) vs Kimi K2.5 (thinking; newer generation)
      - Kimi K2-0905 (non-thinking) vs Kimi K2-thinking (same generation, clean)
      - Gemini 2.5 Flash (non-thinking) vs Gemini 2.5 Pro (thinking; tier up)

    Only draws pairs where both files exist. X-axis = lang/view cells.
    """
    PAIRS = [
        ("Kimi K2 (non-thinking)", "results/merged/kimi.jsonl",
         "Kimi K2.5 (thinking)",    "results/merged/kimi2.5.jsonl"),
        ("Kimi K2 (non-thinking)", "results/merged/kimi.jsonl",
         "Kimi K2-thinking",        "results/merged/kimi-thinking.jsonl"),
        ("Gemini Flash (non-thinking)", "results/merged/gemini.jsonl",
         "Gemini 2.5 Pro (thinking)",   "results/merged/gemini-pro.jsonl"),
    ]
    available = [(n1, p1, n2, p2) for (n1, p1, n2, p2) in PAIRS
                 if Path(p1).exists() and Path(p2).exists()
                 and sum(1 for _ in open(p2)) >= 18]
    if not available:
        print("thinking_probe: no thinking-reader data yet, skipping")
        return
    n = len(available)
    fig, axes = plt.subplots(n, 1, figsize=(13, 4.5 * n), sharex=True)
    if n == 1:
        axes = [axes]
    CELLS = [("aver","full"),("aver","masked"),("aver","masked_spec"),
             ("python_from_aver","full"),("python_from_aver","masked"),
             ("python_oop","full"),("python_oop","masked")]
    labels = [f"{l}\n{v}" for l, v in CELLS]

    for ax, (name_nt, path_nt, name_t, path_t) in zip(axes, available):
        rows_nt = [json.loads(l) for l in open(path_nt) if l.strip()]
        rows_t = [json.loads(l) for l in open(path_t) if l.strip()]
        def bucket(rows):
            bk = defaultdict(list)
            for r in rows:
                p = r.get("judgment_prompt",{}).get("median")
                d = r.get("judgment_diff",{}).get("median")
                if p is None or d is None: continue
                bk[(r["lang"], r["view"])].append((p+d)/2)
            return bk
        bk_nt, bk_t = bucket(rows_nt), bucket(rows_t)
        vals_nt = [mean(bk_nt[k]) if bk_nt[k] else 0 for k in CELLS]
        vals_t  = [mean(bk_t[k])  if bk_t[k]  else 0 for k in CELLS]
        deltas  = [t - nt for nt, t in zip(vals_nt, vals_t)]

        x = np.arange(len(CELLS))
        w = 0.4
        b1 = ax.bar(x - w/2, vals_nt, w, color=SLATE_MUTED, edgecolor=SLATE,
                    linewidth=1.0, label=name_nt, hatch="////")
        b2 = ax.bar(x + w/2, vals_t, w, color=AMBER, edgecolor=SLATE,
                    linewidth=1.0, label=name_t)
        for bars, vals in ((b1, vals_nt), (b2, vals_t)):
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(bar.get_x() + bar.get_width()/2, v + 0.04,
                            f"{v:.2f}", ha="center", va="bottom",
                            fontsize=8, color=SLATE)
        # Delta badges: bounding-box labels below each cell, visible regardless of bar height
        for i, d in enumerate(deltas):
            if abs(d) >= 0.20:
                bg, fg = AMBER, "white"
            elif abs(d) >= 0.10:
                bg, fg = AMBER_LIGHT, SLATE
            else:
                bg, fg = SLATE_LIGHT, SLATE
            ax.text(i, 7.12, f"Δ {d:+.2f}", ha="center", va="bottom",
                    fontsize=10, fontweight="bold", color=fg,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=bg,
                              edgecolor=SLATE, linewidth=0.8))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylim(7.0, 9.3)
        ax.set_ylabel("Average (P+D)/2", fontweight="bold")
        ax.legend(loc="upper right", frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
        ax.set_title(f"{name_t} vs {name_nt}", fontweight="bold", fontsize=11)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)

    fig.suptitle("Thinking-tier probe: thinking reader vs non-thinking counterpart.\n"
                 "Δ = (thinking − non-thinking); positive = thinking scores higher.",
                 fontweight="bold", fontsize=11, y=1.00)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def main() -> None:
    out_dir = Path("results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    judge_reader_heatmap(out_dir / "judge_reader_heatmap.png")
    judges_3v5_comparison(out_dir / "judges_3v5.png")
    judge_family_x_lang(out_dir / "judge_family_x_lang.png")
    ablation_all_readers(out_dir / "ablation_all_readers.png")
    program_lang_ranking(out_dir / "program_lang_ranking.png")
    program_complexity_advantage(out_dir / "program_complexity_advantage.png")
    program_reader_heatmap(out_dir / "program_reader_heatmap.png")
    masked_vs_masked_spec(out_dir / "masked_vs_masked_spec.png")
    ranking_all_readers_ci(out_dir / "ranking_all_readers_ci.png")
    diff_type_stratification(out_dir / "diff_type_stratification.png")
    interjudge_heatmap(out_dir / "interjudge_heatmap.png")
    thinking_probe(out_dir / "thinking_probe.png")


if __name__ == "__main__":
    main()
