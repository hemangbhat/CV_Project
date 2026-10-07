"""Figures for the closed-loop results (report/figures/).

    python -m sim.figures [--results results/sim/test_exact.jsonl]

1. fig_timing_vs_score.png  - mean delay per scenario: fixed-time, actuated round-robin,
   Raza-style band timing (S1, S4) and actuated score selection (S4). Shows that the
   timing rule, not the score composition, dominates delay.
2. fig_paired_<timing>_<norm>.png - forest plots of paired differences (95% CI over
   test seeds) for the pre-registered comparisons S4-S3, S4-NULL, S3S-S3, S3X-S3.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sim.experiment import load, summarise  # noqa: E402

OUT = Path("report/figures")
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3dc"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]   # fixed categorical order
MARKERS = ["o", "s", "D", "^", "v"]
SCENARIOS = ["light", "medium", "heavy", "calibrated", "unequal", "surge", "growing",
             "unequal_oversat", "oversat"]


def _style(ax):
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelcolor=INK, length=0)


def timing_figure(summary: dict) -> Path:
    phys = summary["actuated/physical/exact"]
    bands = summary["bands/physical/exact"]
    series = [
        ("S0 fixed-time 30 s", phys, "S0"),
        ("A0 actuated round-robin (no CV score)", phys, "A0"),
        ("S1 Raza-style density, band timing", bands, "S1"),
        ("S4 proposed score, band timing", bands, "S4"),
        ("S4 proposed score, actuated timing", phys, "S4"),
    ]
    scen = [s for s in SCENARIOS if s in phys]
    fig, ax = plt.subplots(figsize=(8.5, 6.4), dpi=150)
    for k, (label, table, arm) in enumerate(series):
        ys = [i + (k - 2) * 0.14 for i in range(len(scen))]
        means = [table[s][arm]["mean_delay"]["mean"] for s in scen]
        cis = [table[s][arm]["mean_delay"]["ci"] for s in scen]
        ax.errorbar(means, ys, xerr=cis, fmt=MARKERS[k], color=SERIES[k], ms=6,
                    elinewidth=1.5, capsize=0, label=label)
    ax.set_yticks(range(len(scen)), scen)
    ax.invert_yaxis()
    ax.set_xscale("log")
    ticks = [20, 30, 50, 100, 200, 300]
    ax.set_xticks(ticks, [str(t) for t in ticks])
    ax.minorticks_off()
    ax.set_xlabel("mean delay per vehicle, s (log scale; bars = 95% CI over 20 test seeds)", color=INK)
    ax.set_title("Closed loop: the green-time rule dominates delay", color=INK, loc="left", fontsize=12)
    _style(ax)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.45, -0.12),
              ncol=2, labelcolor=INK)
    fig.tight_layout()
    path = OUT / "fig_timing_vs_score.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def paired_figure(summary: dict, group: str) -> Path | None:
    table = summary.get(group)
    if not table:
        return None
    comparisons = [
        ("S4 − S3  (add spillback risk S)", "S4", "vs_S3"),
        ("S4 − NULL  (S itself, weight-matched)", "S4", "vs_NULL"),
        ("S3S − S3  (S instead of count forecast F)", "S3S", "vs_S3"),
        ("S3X − S3  (X instead of count forecast F)", "S3X", "vs_S3"),
    ]
    scen = [s for s in SCENARIOS if s in table]
    fig, ax = plt.subplots(figsize=(8.5, 6.4), dpi=150)
    ax.axvline(0.0, color=MUTED, linewidth=1)
    for k, (label, arm, key) in enumerate(comparisons):
        ys, xs, es = [], [], []
        for i, s in enumerate(scen):
            d = table[s].get(arm, {}).get(key, {}).get("mean_delay")
            if d:
                ys.append(i + (k - 1.5) * 0.17)
                xs.append(d["mean"])
                es.append(d["ci"])
        ax.errorbar(xs, ys, xerr=es, fmt=MARKERS[k], color=SERIES[k], ms=6,
                    elinewidth=1.5, capsize=0, label=label)
    ax.set_yticks(range(len(scen)), scen)
    ax.invert_yaxis()
    timing, norm, _ = group.split("/")
    ax.set_xlabel("paired difference in mean delay, s  (< 0 = better; bars = 95% CI)", color=INK)
    ax.set_title(f"Score ablation, {timing} timing, {norm} density normaliser",
                 color=INK, loc="left", fontsize=12)
    _style(ax)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.45, -0.12),
              ncol=2, labelcolor=INK)
    fig.tight_layout()
    path = OUT / f"fig_paired_{timing}_{norm}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results/sim/test_exact.jsonl")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    summary = summarise(load(Path(args.results)))
    print(timing_figure(summary))
    for group in sorted(summary):
        path = paired_figure(summary, group)
        if path:
            print(path)


if __name__ == "__main__":
    main()
