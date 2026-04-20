#!/usr/bin/env python3
"""Generate charts in Aver brand colors from latest JSONL results."""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib as mpl

# Aver brand palette (from averlang.dev style.css + logo SVGs)
AMBER = "#d97706"
AMBER_LIGHT = "#f59e0b"
AMBER_DARK = "#92400e"
SLATE = "#1e293b"
SLATE_MUTED = "#64748b"
SLATE_LIGHT = "#e2e8f0"
BG = "#f8fafc"

LANG_COLORS = {
    "aver": AMBER,
    "python_from_aver": SLATE,
    "python_oop": SLATE_MUTED,
}
LANG_COLORS_MASKED = {
    "aver": "#fbbf24",              # light amber — visibly distinct from full AMBER
    "python_from_aver": "#94a3b8",  # slate-400 — light enough for SLATE hatch to be visible
    "python_oop": "#e2e8f0",         # slate-200 — very light for the muted baseline
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
    """Canonical reader = Sonnet (merged, 6-judge).

    Uses canonical_load: for pfa/oop, masked_spec rows are aliased to
    `masked` (same input, independent sample → doubles N).
    """
    from _load import canonical_load
    merged = results_root / "merged" / "sonnet.jsonl"
    if not merged.exists():
        raise SystemExit("results/merged/sonnet.jsonl missing — run scripts/merge_judge_runs.py first")
    print(f"source: {merged}")
    return canonical_load(merged, keep_views=("full", "masked"))


def _bootstrap_ci(per_slice: list[float], n_resamples: int = 10000,
                  pct_low: float = 2.5, pct_high: float = 97.5,
                  seed: int = 0) -> tuple[float, float]:
    """Return (lo, hi) percentile bootstrap CI of the mean over per-slice scores."""
    rng = random.Random(seed)
    k = len(per_slice)
    if k == 0:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_resamples):
        sample = [per_slice[rng.randrange(k)] for _ in range(k)]
        means.append(sum(sample) / k)
    means.sort()
    lo = means[int(len(means) * pct_low / 100)]
    hi = means[int(len(means) * pct_high / 100)]
    return (lo, hi)


def aggregate(rows: list[dict]) -> dict[tuple[str, str], dict]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r["lang"], r["view"])].append(r)
    out = {}
    for k, rr in buckets.items():
        n = len(rr)
        p_per = [x["judgment_prompt"]["median"] for x in rr]
        d_per = [x["judgment_diff"]["median"] for x in rr]
        avg_per = [(p + d) / 2 for p, d in zip(p_per, d_per)]
        p = sum(p_per) / n
        d = sum(d_per) / n
        lo, hi = _bootstrap_ci(avg_per)
        out[k] = {"n": n, "p": p, "d": d, "avg": (p + d) / 2,
                  "ci_lo": lo, "ci_hi": hi, "per_slice": avg_per}
    return out


def plot_ranking(agg: dict, out_path: Path) -> None:
    items = sorted(agg.items(), key=lambda kv: -kv[1]["avg"])
    labels = [f"{LANG_LABELS[lang]}\n{view}" for (lang, view), _ in items]
    values = [v["avg"] for _, v in items]
    err_lo = [v["avg"] - v["ci_lo"] for _, v in items]
    err_hi = [v["ci_hi"] - v["avg"] for _, v in items]
    colors = [LANG_COLORS[lang] if view == "full" else LANG_COLORS_MASKED[lang]
              for (lang, view), _ in items]
    hatches = [VIEW_HATCH[view] for (_, view), _ in items]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(labels, values, color=colors, edgecolor=SLATE, linewidth=1.2,
                  yerr=[err_lo, err_hi], ecolor=SLATE, capsize=4,
                  error_kw={"linewidth": 1.0})
    for bar, hatch in zip(bars, hatches):
        bar.set_hatch(hatch)
    for bar, value, ehi in zip(bars, values, err_hi):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + ehi + 0.06,
            f"{value:.2f}",
            ha="center", va="bottom", fontweight="bold", color=SLATE,
        )
    ax.set_ylabel("Average score (P+D)/2  (bars = 95% bootstrap CI)", fontweight="bold")
    ax.set_title(
        "Intent-trace: AI reviewer accuracy by language & view (Sonnet reader)",
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


READER_PATHS = {
    "Sonnet": "results/merged/sonnet.jsonl",
    "Opus": "results/merged/opus.jsonl",
    "gpt-4.1": "results/merged/gpt4.1.jsonl",
    "Gemini Flash": "results/merged/gemini.jsonl",
    "Kimi K2": "results/merged/kimi.jsonl",
    "Gemma 4 e4b": "results/merged/gemma.jsonl",
}
READER_COLORS = {
    "Sonnet": AMBER,            # Anthropic — brand amber
    "Opus": AMBER_DARK,         # Anthropic — dark amber
    "gpt-4.1": SLATE,           # OpenAI — slate
    "Gemini Flash": "#3b82f6",  # Google cloud — blue
    "Kimi K2": "#9333ea",       # Moonshot — purple
    "Gemma 4 e4b": "#16a34a",   # Google OSS — green
}


def plot_3way_readers(out_path: Path) -> None:
    from _load import canonical_load
    readers_data: dict[str, dict] = {}
    for name, path in READER_PATHS.items():
        p = Path(path)
        if not p.exists():
            continue
        rows = canonical_load(p, keep_views=("full", "masked"))
        readers_data[name] = aggregate(rows)

    cells = sorted({k for d in readers_data.values() for k in d})
    labels = [f"{lang}\n{view}" for lang, view in cells]

    n_readers = len(readers_data)
    bar_w = 0.18
    fig, ax = plt.subplots(figsize=(15, 6.5))
    positions = range(len(cells))
    for i, (reader, d) in enumerate(readers_data.items()):
        values = [d[k]["avg"] if k in d else 0 for k in cells]
        xs = [p + (i - (n_readers - 1) / 2) * bar_w for p in positions]
        bars = ax.bar(xs, values, bar_w, color=READER_COLORS[reader],
                      edgecolor=SLATE, linewidth=0.8, label=reader)
        for bar, v in zip(bars, values):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, v + 0.04,
                        f"{v:.2f}", ha="center", va="bottom",
                        fontsize=7.5, color=SLATE)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Average score (P+D)/2", fontweight="bold")
    ax.set_title(f"Intent-trace: reader family dependence across {n_readers} vendors",
                 fontweight="bold", pad=15, color=SLATE)
    ax.set_ylim(7.0, 9.3)
    ax.legend(loc="lower right", frameon=True,
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
    plot_3way_readers(out_dir / "three_way_readers.png")

    print(f"\nN per cell: {set(v['n'] for v in agg.values())}")
    print(f"Total measured: {sum(v['n'] for v in agg.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
