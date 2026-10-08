"""Diagrams for the presentation (taxonomy, workflow, method flow).

    python scripts/make_presentation_figures.py

Writes PNGs to ``report/figures/presentation/``. Result figures come from
``sim/study2_figures.py``.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path("report/figures/presentation")
BOX = "#dbe7f7"
EDGE = "#3b5b8c"
OURS = "#c0392b"


def box(ax, x, y, w, h, text, *, face=BOX, edge=EDGE, dashed=False, size=10, bold=False):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                facecolor=face, edgecolor=edge, linewidth=2 if dashed else 1.2,
                                linestyle="--" if dashed else "-"))
    ax.text(x, y, text, ha="center", va="center", fontsize=size, wrap=True,
            fontweight="bold" if bold else "normal")


def arrow(ax, x0, y0, x1, y1, color="#333333"):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=14,
                                 color=color, linewidth=1.2))


def taxonomy() -> None:
    fig, ax = plt.subplots(figsize=(12, 6.2))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.4)
    ax.axis("off")
    box(ax, 6, 5.9, 3.4, 0.6, "Traffic signal control", bold=True)
    for x, t in ((2, "Fixed-time\n(Webster plans)"), (6, "Actuated\n(detector gap-out)"), (10, "Adaptive")):
        box(ax, x, 4.8, 2.6, 0.75, t)
        arrow(ax, 6, 5.6, x, 5.18)
    box(ax, 7.4, 3.2, 2.9, 1.2, "Model-based optimisation\nFASC (Mohajerpoor 2023)\nMPC-Q (Wei 2025)\nMILP (Li 2025)", size=9)
    box(ax, 10.4, 3.2, 2.7, 1.2, "Feedback / pressure\nMax pressure (Varaiya 2013)\nCapacity-aware MP\n(Gregoire 2015)", size=9)
    arrow(ax, 10, 4.42, 7.4, 3.82)
    arrow(ax, 10, 4.42, 10.4, 3.82)
    box(ax, 4.2, 3.2, 2.9, 1.2, "Vision-based adaptive\n(camera replaces detectors)", size=9)
    arrow(ax, 10, 4.42, 4.2, 3.82)
    box(ax, 2.6, 1.4, 2.8, 1.0, "Count / density state\nRaza 2025 (YOLO + PCE)", size=9)
    box(ax, 6.0, 1.4, 3.4, 1.0, "Spatial queue state\n(queue tail X, risk S)\nthis project", size=9,
        face="#fbe3e0", edge=OURS, dashed=True)
    arrow(ax, 4.2, 2.6, 2.6, 1.9)
    arrow(ax, 4.2, 2.6, 6.0, 1.9)
    ax.text(6, 0.25, "Figure: taxonomy of signal control methods and where this project sits", ha="center",
            fontsize=10, style="italic")
    fig.savefig(OUT / "fig_taxonomy.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def workflow() -> None:
    fig, ax = plt.subplots(figsize=(12, 6.4))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.6)
    ax.axis("off")
    box(ax, 6, 6.15, 4.6, 0.6, "Traffic camera video (Bellevue 116th Ave NE / NE 12th St)", size=9)
    box(ax, 6, 5.2, 4.6, 0.65, "YOLOv8m detection + ByteTrack tracking (cached)", size=9)
    arrow(ax, 6, 5.85, 6, 5.53)
    box(ax, 6, 4.2, 5.2, 0.75, "Per-approach measures: D (PCE density), Q (stop-line queue),\nX (queue tail along the road), S (X projected 5 s ahead)", size=9)
    arrow(ax, 6, 4.87, 6, 4.58)
    # objective branches
    xs = (1.6, 4.6, 7.6, 10.4)
    titles = ("Objective 1\nMeasure the queue\non real footage",
              "Objective 2\nSpatial terms in\nthe selection score\n(study 1)",
              "Objective 3\nStorage-aware score\n+ storage protection\n(study 2)",
              "Objective 4\nRobustness to\ncamera errors\n(misses, jitter)")
    tests = ("compare X with\nvisible queue on\nframes (§6.2)",
             "SUMO, 9 scenarios,\n10 arms, 20 seeds",
             "SUMO, 2 storage\ngeometries x 5 demand\nlevels, 8 methods",
             "vision noise model\n(30% far misses,\n2 m jitter)")
    for x, t, s in zip(xs, titles, tests):
        arrow(ax, 6, 3.82, x, 3.3)
        box(ax, x, 2.75, 2.5, 1.05, t, size=9, face="#fbe3e0" if "3" in t else BOX,
            edge=OURS if "3" in t else EDGE, dashed="3" in t)
        arrow(ax, x, 2.22, x, 1.85)
        box(ax, x, 1.35, 2.5, 0.95, s, size=8.5, face="#f4f4f4")
        arrow(ax, x, 0.87, 6, 0.5)
    box(ax, 6, 0.3, 5.4, 0.42, "Evaluation: delay, local spillback (blocked entry), paired 95% CIs", size=9)
    fig.savefig(OUT / "fig_workflow.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def method_flow() -> None:
    fig, ax = plt.subplots(figsize=(13.5, 4.2))
    ax.set_xlim(0, 13.4)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    box(ax, 1.0, 3.1, 1.7, 0.6, "Camera frame", size=9, face="white")
    box(ax, 1.0, 1.4, 1.7, 0.6, "Approach geometry\n(ROI, queue axis)", size=8.5, face="white")
    steps = (
        (3.6, "1. Detect + track\nYOLOv8m, ByteTrack;\nbottom-centre point\nassigned to an approach"),
        (6.4, "2. Spatial queue\nwindowed stopped test;\ncontiguous queue tail X\nas share of storage;\nS = X + slope x 5 s"),
        (9.2, "3. Storage-aware decision\nscore = D + lam*(1/(a-S) - 1/a)\nactuated green 10-60 s;\nafter 20 s, end green if a\nwaiting S >= 0.85"),
        (11.9, "4. Signal\nGREEN -> YELLOW 3 s\n-> next approach"),
    )
    for k, (x, t) in enumerate(steps):
        ours = k in (1, 2)
        box(ax, x, 2.25, 2.5, 1.9, t, size=8.5, face="#fbe3e0" if ours else BOX,
            edge=OURS if ours else EDGE, dashed=ours)
    arrow(ax, 1.85, 3.1, 2.35, 2.6)
    arrow(ax, 1.85, 1.4, 2.35, 1.9)
    for a, b in ((4.85, 5.15), (7.65, 7.95), (10.45, 10.65)):
        arrow(ax, a, 2.25, b, 2.25)
    ax.annotate("", xy=(3.6, 3.25), xytext=(11.9, 3.25),
                arrowprops=dict(arrowstyle="-|>", linestyle=":", color="#555555", connectionstyle="arc3,rad=0.25"))
    ax.text(7.75, 4.0, "next frame (closed loop)", ha="center", fontsize=8.5, color="#555555")
    ax.text(6.5, 0.35, "Figure: flow of the proposed method (dashed red = this project's contribution)",
            ha="center", fontsize=10, style="italic")
    fig.savefig(OUT / "fig_method_flow.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    taxonomy()
    workflow()
    method_flow()
    print("wrote", *sorted(p.name for p in OUT.glob("*.png")))
