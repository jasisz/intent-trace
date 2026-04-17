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
from statistics import mean

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
    "gpt-4.1": "results/merged/gpt4.1.jsonl",
    "Gemini": "results/merged/gemini.jsonl",
}
JUDGES = ["opus", "sonnet", "haiku", "gpt-4o", "gpt-4.1"]
JUDGE_FAMILY = {
    "opus": "Claude", "sonnet": "Claude", "haiku": "Claude",
    "gpt-4o": "OpenAI", "gpt-4.1": "OpenAI",
}
CLAUDE = ("opus", "sonnet", "haiku")
GPT = ("gpt-4o", "gpt-4.1")
LANGS = ["aver", "python_from_aver", "python_oop"]
LANG_COLORS = {"aver": AMBER, "python_from_aver": SLATE, "python_oop": SLATE_MUTED}
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
    fig, axes = plt.subplots(1, 3, figsize=(14, 5.5), sharey=True)

    for ax, (reader, path) in zip(axes, READERS.items()):
        rows = [r for r in load_rows(path) if r.get("view") == "full"]
        three = defaultdict(list)
        five = defaultdict(list)
        for r in rows:
            claude_scores = []
            for j in CLAUDE:
                p = r["judgment_prompt"]["individual"].get(j, {}).get("score", -1)
                d = r["judgment_diff"]["individual"].get(j, {}).get("score", -1)
                if p >= 0 and d >= 0:
                    claude_scores.append((p + d) / 2)
            all_scores = list(claude_scores)
            for j in GPT:
                p = r["judgment_prompt"]["individual"].get(j, {}).get("score", -1)
                d = r["judgment_diff"]["individual"].get(j, {}).get("score", -1)
                if p >= 0 and d >= 0:
                    all_scores.append((p + d) / 2)
            if claude_scores:
                three[r["lang"]].append(mean(claude_scores))
            if all_scores:
                five[r["lang"]].append(mean(all_scores))

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

        # Highlight ranking change: circle the new winner
        top_five = int(np.argmax(five_vals))
        top_three = int(np.argmax(three_vals))
        if top_five != top_three:
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
    ax.set_ylabel("Mean score (P+D)/2 across all 3 readers", fontweight="bold")
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
    fig, axes = plt.subplots(1, 3, figsize=(14, 5.5), sharey=True)

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
        b2 = ax.bar(x + w / 2, mask_vals, w, color=[LANG_COLORS[l] for l in langs],
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


if __name__ == "__main__":
    main()
