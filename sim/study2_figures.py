"""Tables and figures for study 2 (test seeds).

    python -m sim.study2_figures

Reads ``results/sim/study2/test_exact.jsonl`` (+ ``test_vision.jsonl``), writes
``results/sim/study2/summary.json``, ``results/sim/study2/tables.md`` and figures in
``report/figures/study2/``. The queue-trace figure re-runs four simulations on test seed
200 with tracing on; it reproduces the logged runs exactly (same seed, same code).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sim.study2 import GEOMETRIES, LEVELS, METHODS, condition_names, run, summarise

RESULTS = Path("results/sim/study2")
FIGS = Path("report/figures/study2")
ORDER = ["FT", "ACT", "CMP", "RAZA", "RAZA_A", "PROP_B", "PROP", "PROP_CNT"]
LABEL = {
    "FT": "Fixed-time", "ACT": "Actuated", "CMP": "Capacity-aware MP", "RAZA": "Raza-style",
    "RAZA_A": "Raza + actuated", "PROP_B": "+ storage barrier", "PROP": "Proposed",
    "PROP_CNT": "Proposed, count-based S",
}
STYLE = {
    "FT": ("#999999", "-", "o"), "ACT": ("#1f77b4", "-", "s"), "CMP": ("#9467bd", "-", "^"),
    "RAZA": ("#8c564b", "-", "v"), "RAZA_A": ("#17becf", "--", "v"), "PROP_B": ("#ff7f0e", "--", "D"),
    "PROP": ("#d62728", "-", "D"), "PROP_CNT": ("#2ca02c", ":", "x"),
}
GEO_LABEL = {"uniform": "all approaches 150 m", "short_minor": "minor street 60 m, major 150 m"}


def load(name: str) -> list[dict]:
    path = RESULTS / name
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def table(summary: dict, metric: str, title: str, lower_better: bool = True) -> str:
    exact = summary["exact"]
    lines = [f"**{title}** (mean over 20 test seeds; best per column in bold)", ""]
    for geo in GEOMETRIES:
        lines.append(f"*{GEO_LABEL[geo]}*")
        lines.append("")
        lines.append("| Method | " + " | ".join(f"{lvl} veh/h" for lvl in LEVELS) + " |")
        lines.append("|---|" + "---|" * len(LEVELS))
        best = {}
        for lvl in LEVELS:
            c = f"{geo}_{lvl}"
            vals = {a: exact[c][a][metric]["mean"] for a in ORDER if a in exact[c]}
            best[lvl] = min(vals.values()) if lower_better else max(vals.values())
        for a in ORDER:
            cells = []
            for lvl in LEVELS:
                v = exact[f"{geo}_{lvl}"][a][metric]["mean"]
                txt = f"{v:.1f}"
                cells.append(f"**{txt}**" if abs(v - best[lvl]) < 0.05 else txt)
            name = f"{LABEL[a]} ({a})" + (" *" if a == "PROP" else "")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        if "vision" in summary:
            cells = [f"{summary['vision'][f'{geo}_{lvl}']['PROP'][metric]['mean']:.1f}" for lvl in LEVELS]
            lines.append("| Proposed, camera noise (PROP, vision) | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def paired_table(summary: dict, metric: str) -> str:
    exact = summary["exact"]
    refs = ["RAZA", "ACT", "CMP", "PROP_B", "PROP_CNT"]
    lines = [f"**Paired difference PROP − reference, {metric}** (mean ± 95% CI over 20 seeds; "
             "`*` = interval excludes 0; negative = proposed better)", "",
             "| Condition | " + " | ".join(f"vs {r}" for r in refs) + " |", "|---|" + "---|" * len(refs)]
    for c in condition_names():
        cells = []
        for r in refs:
            d = exact[c]["PROP"].get(f"vs_{r}", {}).get(metric)
            if not d:
                cells.append("-")
                continue
            sig = "*" if abs(d["mean"]) > d["ci"] else ""
            cells.append(f"{d['mean']:+.1f} ± {d['ci']:.1f}{sig}")
        lines.append(f"| {c} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def curves(summary: dict, metric: str, ylabel: str, fname: str, log: bool) -> None:
    exact = summary["exact"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for ax, geo in zip(axes, GEOMETRIES):
        for a in ORDER:
            ys = [exact[f"{geo}_{lvl}"][a][metric]["mean"] for lvl in LEVELS]
            es = [exact[f"{geo}_{lvl}"][a][metric]["ci"] for lvl in LEVELS]
            color, ls, mk = STYLE[a]
            ax.errorbar(LEVELS, ys, yerr=es, color=color, linestyle=ls, marker=mk, capsize=3,
                        linewidth=2.4 if a == "PROP" else 1.4, label=LABEL[a])
        ax.set_title(GEO_LABEL[geo])
        ax.set_xlabel("total demand (veh/h)")
        ax.set_xticks(LEVELS)
        ax.grid(alpha=0.3)
        if log:
            ax.set_yscale("symlog" if metric == "blocked_seconds" else "log")
            if metric == "mean_delay":
                ax.set_ylim(10, 600)
    axes[0].set_ylabel(ylabel)
    axes[1].legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIGS / fname, dpi=160)
    plt.close(fig)


def queue_traces(cond: str = "short_minor_3000", seed: int = 200) -> None:
    methods = ["RAZA", "ACT", "CMP", "PROP"]
    fig, axes = plt.subplots(len(methods), 1, figsize=(11, 8.5), sharex=True)
    for ax, name in zip(axes, methods):
        trace: list = []
        result = run(cond, METHODS[name], seed, trace=trace)
        t = [row[0] for row in trace]
        for approach, color in (("North", "#1f77b4"), ("East", "#d62728")):
            ax.plot(t, [row[3][approach] for row in trace], color=color, linewidth=1,
                    label=f"{approach} ({'150' if approach == 'North' else '60'} m)")
        ax.axhline(1.0, color="green", linestyle="-.", linewidth=1)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("queue tail /\nstorage")
        ax.set_title(f"{LABEL[name]}: mean delay {result['mean_delay']:.1f} s, "
                     f"blocked entry {result['blocked_seconds']:.0f} s", fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, loc="upper right")
    axes[-1].set_xlabel("simulation time (s)")
    fig.suptitle(f"True queue tail on a major (North) and a short minor (East) approach, {cond}, test seed {seed}\n"
                 "(dash-dot = end of storage; the tail is the last stopped vehicle's front, so a full 60 m road reads ~0.8;\n"
                 "one seed, fixed in advance - on this seed the proposed method is slightly worse than actuated; see tables for means)",
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGS / "fig_s2_queue_traces.png", dpi=160)
    plt.close(fig)


def main() -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    rows = load("test_exact.jsonl") + load("test_vision.jsonl")
    summary = summarise(rows, refs=("RAZA", "ACT", "CMP", "PROP_B", "PROP_CNT"))
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1))
    parts = [
        "# Study 2 results (test seeds 200-219)\n",
        "Generated by `python -m sim.study2_figures` from `test_exact.jsonl` and `test_vision.jsonl`.\n",
        table(summary, "mean_delay", "Mean delay per vehicle, seconds (lower is better)"),
        table(summary, "blocked_seconds", "Blocked-entry seconds: local spillback (lower is better)"),
        paired_table(summary, "mean_delay"),
        paired_table(summary, "blocked_seconds"),
    ]
    (RESULTS / "tables.md").write_text("\n".join(parts))
    curves(summary, "mean_delay", "mean delay per vehicle (s)", "fig_s2_delay.png", log=True)
    curves(summary, "blocked_seconds", "blocked-entry seconds (local spillback)", "fig_s2_spillback.png", log=True)
    queue_traces()
    print("wrote", RESULTS / "tables.md", "and", *sorted(p.name for p in FIGS.glob("*.png")))


if __name__ == "__main__":
    main()
