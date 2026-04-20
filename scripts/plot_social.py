#!/usr/bin/env python3
"""Social-media-first chart: full-diff intent recovery, 3 language variants × 6 readers.

Optimized for X/Twitter mobile: large labels, minimal legend, clean spacing,
no CI in main version. Writes two PNGs:
  - results/plots/social_main.png     (no CI, priority for tweet image)
  - results/plots/social_with_ci.png  (with CI bars, for methodology reply)
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib as mpl
import matplotlib.pyplot as plt

AMBER = "#d97706"
TEAL = "#0d9488"
GRAY = "#94a3b8"
SLATE = "#1e293b"
SLATE_MUTED = "#64748b"
SLATE_LIGHT = "#e2e8f0"
BG = "#f8fafc"

READERS = [
    ("Sonnet 4.6",       "results/merged/sonnet.jsonl"),
    ("Opus 4.7",         "results/merged/opus.jsonl"),
    ("gpt-4.1",          "results/merged/gpt4.1.jsonl"),
    ("Gemini 2.5 Flash", "results/merged/gemini.jsonl"),
    ("Kimi K2",          "results/merged/kimi.jsonl"),
    ("Gemma 4 e4b",      "results/merged/gemma.jsonl"),
]

SERIES = [
    ("aver",             "Aver",                       AMBER),
    ("python_from_aver", "Python (faithful translit.)", TEAL),
    ("python_oop",       "Python (idiomatic OOP)",     GRAY),
]

mpl.rcParams.update({
    "font.family": ["Helvetica Neue", "Arial", "sans-serif"],
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


def load_full_scores(path: str) -> dict[str, list[float]]:
    """For one reader: {lang: [per-slice (P+D)/2 scores for full view]}."""
    out: dict[str, list[float]] = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("view") != "full":
            continue
        score = (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
        out[r["lang"]].append(score)
    return out


def bootstrap_ci(vals: list[float], n_resamples: int = 10000) -> tuple[float, float]:
    rng = random.Random(0)
    k = len(vals)
    if k == 0:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_resamples):
        s = [vals[rng.randrange(k)] for _ in range(k)]
        means.append(sum(s) / k)
    means.sort()
    return means[int(n_resamples * 0.025)], means[int(n_resamples * 0.975)]


def build_plot(out_path: Path, *, with_ci: bool) -> None:
    # Reader × series → mean, and optionally (lo, hi)
    data = {name: load_full_scores(path) for name, path in READERS}
    n_readers = len(READERS)

    fig, ax = plt.subplots(figsize=(15, 8))
    x_positions = list(range(n_readers))
    bar_w = 0.26

    for i, (lang, label, color) in enumerate(SERIES):
        values = [mean(data[reader][lang]) for reader, _ in READERS]
        xs = [p + (i - 1) * bar_w for p in x_positions]

        err_args = {}
        if with_ci:
            cis = [bootstrap_ci(data[reader][lang]) for reader, _ in READERS]
            err_lo = [m - lo for m, (lo, _) in zip(values, cis)]
            err_hi = [hi - m for m, (_, hi) in zip(values, cis)]
            err_args = dict(
                yerr=[err_lo, err_hi],
                ecolor="#dc2626",
                capsize=6,
                error_kw={"linewidth": 2.0, "zorder": 10},
            )

        bars = ax.bar(
            xs, values, bar_w,
            color=color, edgecolor=SLATE, linewidth=1.8,
            label=label, **err_args,
        )
        for bar, v in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + 0.06,
                f"{v:.2f}",
                ha="center", va="bottom",
                fontsize=12, fontweight="bold", color=SLATE,
            )

    ax.set_xticks(x_positions)
    ax.set_xticklabels([r for r, _ in READERS], fontsize=14, fontweight="bold")
    ax.set_ylabel("Intent recovery score (0–10, higher = better)",
                  fontsize=14, fontweight="bold")
    ax.set_ylim(7.0, 9.4)

    # Title + subtitle (left-aligned, social-media style)
    fig.suptitle(
        "Full diffs: intent recovery by reader",
        fontsize=22, fontweight="bold", color=SLATE,
        x=0.02, y=0.97, ha="left",
    )
    fig.text(
        0.02, 0.925,
        "Raw unified diff only  •  no system prompt  •  no language hint  •  no primer",
        fontsize=13, color=SLATE_MUTED, style="italic", ha="left",
    )

    ax.legend(
        loc="lower center", ncol=3,
        fontsize=13, frameon=True,
        facecolor=BG, edgecolor=SLATE_LIGHT,
        bbox_to_anchor=(0.5, -0.17),
    )

    # Light annotation: Aver tracks Python-transliteration, above idiomatic OOP
    ax.text(
        0.98, 0.97,
        "Aver  ≈  Python (faithful translit.)  >  Python (OOP)",
        transform=ax.transAxes,
        fontsize=12.5, fontweight="bold", color=SLATE,
        ha="right", va="top",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="white",
                  edgecolor=AMBER, linewidth=1.8),
    )

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.set_axisbelow(True)

    plt.subplots_adjust(top=0.86, bottom=0.16, left=0.06, right=0.98)
    plt.savefig(out_path, dpi=180, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote: {out_path}")


def main() -> None:
    out_dir = Path("results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    build_plot(out_dir / "social_main.png",    with_ci=False)
    build_plot(out_dir / "social_with_ci.png", with_ci=True)


if __name__ == "__main__":
    main()
