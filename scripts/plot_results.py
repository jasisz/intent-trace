#!/usr/bin/env python3
"""Generate charts in Aver brand colors from latest JSONL results."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib as mpl

# Aver brand palette (from averlang.dev style.css + logo SVGs)
AMBER = "#d97706"
AMBER_LIGHT = "#f59e0b"
SLATE = "#1e293b"
SLATE_MUTED = "#64748b"
SLATE_LIGHT = "#e2e8f0"
BG = "#f8fafc"
GREEN = "#16a34a"
RED = "#dc2626"

LANG_COLORS = {
    "aver": AMBER,
    "python_from_aver": SLATE,
    "python_oop": SLATE_MUTED,
}
LANG_LABELS = {
    "aver": "Aver",
    "python_from_aver": "Python (from Aver)",
    "python_oop": "Python (OOP)",
}
VIEW_HATCH = {"full": "", "masked": "////"}

mpl.rcParams.update({
    "font.family": ["SF Pro Text", "Helvetica Neue", "Arial", "sans-serif"],
    "axes.edgecolor": SLATE,
    "axes.labelcolor": SLATE,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "text.color": SLATE,
    "axes.grid": True,
    "grid.color": SLATE_LIGHT,
    "grid.linestyle": "-",
    "grid.linewidth": 0.5,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
})


def load_latest(results_root: Path) -> list[dict]:
    candidates = list(results_root.glob("ensemble_*/slices.jsonl")) + list(
        results_root.glob("gpt_added_*/slices.jsonl")
    )
    if not candidates:
        raise SystemExit("no slices.jsonl found in results/")
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"source: {latest}")
    rows: list[dict] = []
    for line in latest.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("view") in ("full", "masked"):
            rows.append(r)
    return rows


def aggregate(rows: list[dict]) -> dict[tuple[str, str], dict]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r["lang"], r["view"])].append(r)
    out = {}
    for k, rr in buckets.items():
        n = len(rr)
        p = sum(x["judgment_prompt"]["median"] for x in rr) / n
        d = sum(x["judgment_diff"]["median"] for x in rr) / n
        out[k] = {"n": n, "p": p, "d": d, "avg": (p + d) / 2}
    return out


def plot_ranking(agg: dict, out_path: Path) -> None:
    items = sorted(agg.items(), key=lambda kv: -kv[1]["avg"])
    labels = [f"{LANG_LABELS[lang]}\n{view}" for (lang, view), _ in items]
    values = [v["avg"] for _, v in items]
    colors = [LANG_COLORS[lang] for (lang, _), _ in items]
    hatches = [VIEW_HATCH[view] for (_, view), _ in items]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(labels, values, color=colors, edgecolor=SLATE, linewidth=1.2)
    for bar, hatch in zip(bars, hatches):
        bar.set_hatch(hatch)
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.05,
            f"{value:.2f}",
            ha="center", va="bottom", fontweight="bold", color=SLATE,
        )
    ax.set_ylabel("Average score (P+D)/2", fontweight="bold")
    ax.set_title(
        "Intent-trace: AI reviewer accuracy by language & view",
        fontweight="bold", pad=15, color=SLATE,
    )
    ax.set_ylim(7.0, 9.2)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def plot_axes(agg: dict, out_path: Path) -> None:
    """Grouped bars: prompt-axis vs diff-axis side by side per cell."""
    items = sorted(agg.keys(), key=lambda k: (list(LANG_COLORS).index(k[0]), k[1]))
    labels = [f"{LANG_LABELS[lang]}\n{view}" for lang, view in items]
    ps = [agg[k]["p"] for k in items]
    ds = [agg[k]["d"] for k in items]

    x = range(len(items))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11, 6))
    bars_p = ax.bar([i - width / 2 for i in x], ps, width,
                    color=AMBER, edgecolor=SLATE, linewidth=1.0, label="Prompt-axis (P)")
    bars_d = ax.bar([i + width / 2 for i in x], ds, width,
                    color=SLATE, edgecolor=SLATE, linewidth=1.0, label="Diff-axis (D)")
    for bars, vals in ((bars_p, ps), (bars_d, ds)):
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.05,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=9, color=SLATE)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("Median ensemble score (0–10)", fontweight="bold")
    ax.set_title("Prompt-fidelity vs diff-fidelity per cell", fontweight="bold",
                 pad=15, color=SLATE)
    ax.set_ylim(6.5, 9.5)
    ax.legend(loc="lower right", frameon=True, facecolor=BG, edgecolor=SLATE_LIGHT)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def plot_ablation(agg: dict, out_path: Path) -> None:
    """Full→masked delta per language (what ablation reveals)."""
    langs = list(LANG_COLORS)
    fig, ax = plt.subplots(figsize=(9, 5.5))

    x_positions = range(len(langs))
    full_ps = [agg[(lang, "full")]["p"] for lang in langs]
    mask_ps = [agg[(lang, "masked")]["p"] for lang in langs]
    full_ds = [agg[(lang, "full")]["d"] for lang in langs]
    mask_ds = [agg[(lang, "masked")]["d"] for lang in langs]

    width = 0.18
    xs = list(x_positions)
    bars_fp = ax.bar([x - 1.5 * width for x in xs], full_ps, width,
                     color=AMBER, edgecolor=SLATE, linewidth=1.0, label="Full P")
    bars_mp = ax.bar([x - 0.5 * width for x in xs], mask_ps, width,
                     color=AMBER_LIGHT, edgecolor=SLATE, linewidth=1.0,
                     hatch="////", label="Masked P")
    bars_fd = ax.bar([x + 0.5 * width for x in xs], full_ds, width,
                     color=SLATE, edgecolor=SLATE, linewidth=1.0, label="Full D")
    bars_md = ax.bar([x + 1.5 * width for x in xs], mask_ds, width,
                     color=SLATE_MUTED, edgecolor=SLATE, linewidth=1.0,
                     hatch="////", label="Masked D")
    for bars, vals in ((bars_fp, full_ps), (bars_mp, mask_ps),
                       (bars_fd, full_ds), (bars_md, mask_ds)):
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 0.03,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=8, color=SLATE)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([LANG_LABELS[l] for l in langs], fontweight="bold")
    ax.set_ylabel("Median score", fontweight="bold")
    ax.set_title("Ablation: what does each style lose when prose is stripped?",
                 fontweight="bold", pad=15, color=SLATE)
    ax.set_ylim(6.5, 9.5)
    ax.legend(loc="lower right", ncol=2, frameon=True,
              facecolor=BG, edgecolor=SLATE_LIGHT)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def main() -> int:
    results_root = Path("results")
    rows = load_latest(results_root)
    agg = aggregate(rows)

    out_dir = Path("results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_ranking(agg, out_dir / "ranking.png")
    plot_axes(agg, out_dir / "axes.png")
    plot_ablation(agg, out_dir / "ablation.png")

    print(f"\nN per cell: {set(v['n'] for v in agg.values())}")
    print(f"Total measured: {sum(v['n'] for v in agg.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
