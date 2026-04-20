#!/usr/bin/env python3
"""Heatmaps: reader × (program × lang × view)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np

AMBER = "#d97706"
AMBER_LIGHT = "#f59e0b"
SLATE = "#1e293b"
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
PROGRAMS = ["inventory", "workflow", "taskmanager", "payment_ops"]
LANGS = ["aver", "python_from_aver", "python_oop"]
VIEWS = ["full", "masked"]


def load(path_dict):
    from _load import canonical_load
    out = {}
    for name, p in path_dict.items():
        rows = canonical_load(Path(p), keep_views=tuple(VIEWS))
        rows = [r for r in rows if r.get("judgment_prompt")]
        d = defaultdict(list)
        for r in rows:
            d[(r["program"], r["lang"], r["view"])].append(
                (r["judgment_prompt"]["median"] + r["judgment_diff"]["median"]) / 2
            )
        out[name] = {k: sum(v) / len(v) for k, v in d.items()}
    return out


def plot_main_heatmap(data: dict, out_path: Path) -> None:
    """Rows: program × lang × view (24). Cols: reader (3)."""
    row_keys = []
    row_labels = []
    for prog in PROGRAMS:
        for lang in LANGS:
            for view in VIEWS:
                row_keys.append((prog, lang, view))
                lang_short = {"aver": "aver", "python_from_aver": "py_from_aver", "python_oop": "py_oop"}[lang]
                row_labels.append(f"{prog:<12}/{lang_short:<12}/{view}")

    col_labels = list(READERS)
    matrix = np.full((len(row_keys), len(col_labels)), np.nan)
    for i, rk in enumerate(row_keys):
        for j, reader in enumerate(col_labels):
            v = data[reader].get(rk)
            if v is not None:
                matrix[i, j] = v

    fig, ax = plt.subplots(figsize=(8, 13))
    vmin, vmax = 7.0, 9.2
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "aver", ["#f5f5f4", "#fef3c7", AMBER_LIGHT, AMBER, "#92400e"],
    )
    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)

    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, fontsize=10, fontweight="bold")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=8, family="monospace")

    for i in range(len(row_keys)):
        for j in range(len(col_labels)):
            v = matrix[i, j]
            if not np.isnan(v):
                txt_color = "white" if v > 8.5 else SLATE
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        color=txt_color, fontsize=8, fontweight="bold")

    # Separators between programs
    for i in range(1, len(PROGRAMS)):
        ax.axhline(i * len(LANGS) * len(VIEWS) - 0.5, color=SLATE, linewidth=0.8)
    # Separators between langs within a program
    for p in range(len(PROGRAMS)):
        for l in range(1, len(LANGS)):
            ax.axhline(p * len(LANGS) * len(VIEWS) + l * len(VIEWS) - 0.5,
                       color=SLATE_LIGHT, linewidth=0.4)

    ax.set_title("Intent-trace: reader × program × language × view\n"
                 "(avg (P+D)/2 per cell, 5-judge cross-vendor ensemble)",
                 fontweight="bold", pad=15, fontsize=12)
    cb = plt.colorbar(im, ax=ax, shrink=0.5, pad=0.02)
    cb.set_label("avg score", fontweight="bold")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def plot_per_program_grid(data: dict, out_path: Path) -> None:
    """2x2 grid of heatmaps, one per program. Rows = lang+view, cols = reader."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    axes = axes.flatten()

    vmin, vmax = 7.0, 9.2
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "aver", ["#f5f5f4", "#fef3c7", AMBER_LIGHT, AMBER, "#92400e"],
    )

    for idx, prog in enumerate(PROGRAMS):
        ax = axes[idx]
        row_keys = [(lang, view) for lang in LANGS for view in VIEWS]
        row_labels = [f"{lang}/{view}" for lang, view in row_keys]
        col_labels = list(READERS)
        matrix = np.full((len(row_keys), len(col_labels)), np.nan)
        for i, (lang, view) in enumerate(row_keys):
            for j, reader in enumerate(col_labels):
                v = data[reader].get((prog, lang, view))
                if v is not None:
                    matrix[i, j] = v
        im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        for i in range(len(row_keys)):
            for j in range(len(col_labels)):
                v = matrix[i, j]
                if not np.isnan(v):
                    txt_color = "white" if v > 8.5 else SLATE
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            color=txt_color, fontsize=9, fontweight="bold")
        ax.set_xticks(range(len(col_labels)))
        ax.set_xticklabels(col_labels, fontsize=9, fontweight="bold")
        ax.set_yticks(range(len(row_labels)))
        ax.set_yticklabels(row_labels, fontsize=9, family="monospace")
        ax.set_title(prog, fontweight="bold", fontsize=11, pad=8)

    fig.suptitle("Per-program: reader family × language style\n"
                 "(avg score; brighter = better legibility)",
                 fontweight="bold", fontsize=13, y=1.00)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=BG, bbox_inches="tight")
    print(f"wrote: {out_path}")


def main():
    data = load(READERS)
    out_dir = Path("results/plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_main_heatmap(data, out_dir / "heatmap_main.png")
    plot_per_program_grid(data, out_dir / "heatmap_per_program.png")


if __name__ == "__main__":
    main()
