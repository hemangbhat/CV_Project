"""Visual explanation of E9 (spatial queue reach) drawn on REAL camera frames.

Produces three figures into report/:

  figure_E9_geometry.png   - the axis construction on a real frame: the two config
                             polygons, the centroids, the upstream direction, and the
                             t = 0 .. 1 scale, all computed by the real ApproachAxis.
  figure_E9_measured.png   - real YOLO detections + ByteTrack on one frame, with each
                             stopped vehicle's t value and the resulting reach.
  figure_E9_blindspot.png  - schematic: two situations with the SAME count but
                             different reach. Labelled as a schematic, not real data.

Everything in the first two figures comes from the real config and the real code path
(ApproachAxis, MetricsEngine); nothing is hand-placed.
"""

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from src.config import load_config
from src.lane_analysis import ApproachAssigner, as_cv_polygon, build_approach_axes
from src.traffic_metrics import MetricsEngine, APPROACH_NAMES

import argparse

_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument(
    "--config",
    default="config/bellevue_116th_calibrated.json",
    help="junction configuration to draw the geometry from. Defaults to the CALIBRATED "
         "one so the figures show the same axis the reported ablation used; pass the "
         "uncalibrated file to illustrate the pre-calibration geometry.",
)
_parser.add_argument(
    "--suffix",
    default="",
    help="appended to each output filename, e.g. '_uncalibrated'",
)
_args = _parser.parse_args()

CONFIG = _args.config
SUFFIX = _args.suffix
VIDEO = "videos/bellevue_116th_busy.mp4"
FOCUS = "North"          # the approach with the longest visible extent (540 px)
FRAME_A, FRAME_B = 1500, 1502

ORANGE, CYAN, RED, GREEN, YELLOW = "#e8873a", "#38bdd6", "#e03a3a", "#3ac96b", "#f2d33c"

cfg = load_config(CONFIG)
axes_map = build_approach_axes(cfg)


def grab(idx):
    cap = cv2.VideoCapture(VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"could not read frame {idx}")
    return frame


def rgb(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def draw_poly(ax, poly, colour, label, lw=2.2, ls="-"):
    pts = np.array(poly, dtype=float)
    pts = np.vstack([pts, pts[:1]])
    ax.plot(pts[:, 0], pts[:, 1], color=colour, lw=lw, ls=ls, label=label)


# ---------------------------------------------------------------------------
# FIGURE 1 - the geometry, computed by the real ApproachAxis
# ---------------------------------------------------------------------------
def figure_geometry():
    frame = grab(FRAME_A)
    approach = cfg.approach(FOCUS)
    axis = axes_map[FOCUS]

    roi = np.array(approach.roi_polygon, dtype=float)
    roi_centroid = np.array(
        __import__("src.lane_analysis", fromlist=["polygon_centroid"]).polygon_centroid(
            as_cv_polygon(approach.roi_polygon)
        )
    )
    stop_line = np.array(axis.origin)
    # Recover the upstream unit vector from the axis itself (no re-derivation).
    upstream = np.array([axis._ux, axis._uy])

    fig, ax = plt.subplots(figsize=(15, 8.6))
    ax.imshow(rgb(frame))

    draw_poly(ax, approach.roi_polygon, ORANGE, f"ROI  $\\Omega_i$  ({FOCUS})")
    draw_poly(ax, approach.queue_region, CYAN, "Queue_Region  $\\Theta_i$  (stop line)")

    # centroids
    ax.plot(*roi_centroid, "o", ms=13, color=ORANGE, mec="black", mew=1.4, zorder=6)
    ax.annotate(r"$\mathbf{c}_i$  ROI centroid", roi_centroid,
                xytext=(-140, 52), textcoords="offset points", fontsize=12,
                color="white", fontweight="bold",
                bbox=dict(fc=ORANGE, ec="black", alpha=.95, boxstyle="round,pad=0.35"),
                arrowprops=dict(arrowstyle="->", color=ORANGE, lw=2.2))

    ax.plot(*stop_line, "o", ms=13, color=CYAN, mec="black", mew=1.4, zorder=6)
    ax.annotate(r"$\mathbf{o}_i$  origin = stop line   ($t=0$)", stop_line,
                xytext=(-60, -95), textcoords="offset points", fontsize=12,
                color="black", fontweight="bold",
                bbox=dict(fc=CYAN, ec="black", alpha=.95, boxstyle="round,pad=0.35"),
                arrowprops=dict(arrowstyle="->", color=CYAN, lw=2.2))

    # the axis, from t=0 to t=1
    far = stop_line + upstream * axis.length
    ax.annotate("", xy=far, xytext=stop_line,
                arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=4.5,
                                mutation_scale=32, shrinkA=0, shrinkB=0))

    # tick marks along the axis
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = stop_line + upstream * axis.length * t
        n = np.array([-upstream[1], upstream[0]]) * 17
        ax.plot([p[0] - n[0], p[0] + n[0]], [p[1] - n[1], p[1] + n[1]],
                color=GREEN, lw=3, zorder=7)
        ax.text(p[0] + n[0] * 2.0, p[1] + n[1] * 2.0, f"t={t:g}",
                color="black", fontsize=11.5, fontweight="bold", ha="center", va="center",
                bbox=dict(fc=GREEN, ec="black", alpha=.95, boxstyle="round,pad=0.22"))

    mid = stop_line + upstream * axis.length * 0.62
    ax.annotate(
        f"$\\hat{{\\mathbf{{u}}}}_i$  upstream direction\n"
        f"$L_i$ = {axis.length:.0f} px  (computed from $\\Omega_i$, NOT configured)",
        mid, xytext=(70, 120), textcoords="offset points", fontsize=12.5,
        color="black", fontweight="bold",
        bbox=dict(fc=GREEN, ec="black", alpha=.95, boxstyle="round,pad=0.4"),
        arrowprops=dict(arrowstyle="->", color=GREEN, lw=2.4))

    ax.set_title(
        "E9 — how the spatial queue axis is built (real frame, real config geometry)\n"
        r"$\hat{\mathbf{u}}_i$ points upstream from the stop line; "
        r"$t=0$ at the stop line, $t=1$ at the far edge of the visible approach",
        fontsize=13.5, fontweight="bold")
    ax.legend(loc="lower right", fontsize=11.5, framealpha=.93)
    ax.set_xlim(0, frame.shape[1])
    ax.set_ylim(frame.shape[0], 0)
    ax.axis("off")
    fig.tight_layout()
    out = f"report/figure_E9_geometry{SUFFIX}.png"
    fig.savefig(out, dpi=132, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out, f"(L_i = {axis.length:.0f} px)")


# ---------------------------------------------------------------------------
# FIGURE 2 - real detections, real reach
# ---------------------------------------------------------------------------
def figure_measured():
    from src.detection import YoloDetector
    from src.tracking import ByteTrackTracker

    detector = YoloDetector(cfg)
    tracker = ByteTrackTracker(cfg)
    assigner = ApproachAssigner(cfg)
    engine = MetricsEngine(cfg)
    all_red = {n: "RED" for n in APPROACH_NAMES}

    # Two consecutive frames: the engine needs a previous position to judge "stopped".
    frames = []
    cap = cv2.VideoCapture(VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, FRAME_A)
    for _ in range(FRAME_B - FRAME_A + 1):
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()

    metrics = assigned = None
    for i, f in enumerate(frames):
        tracks = tracker.update(f, detector.detect(f))
        assigned, _ = assigner.assign(tracks, frame_index=FRAME_A + i)
        metrics = engine.update(assigned, all_red, 1 / 30.0)
    frame = frames[-1]

    axis = axes_map[FOCUS]
    upstream = np.array([axis._ux, axis._uy])
    stop_line = np.array(axis.origin)

    fig, ax = plt.subplots(figsize=(15, 8.6))
    ax.imshow(rgb(frame))
    for name in APPROACH_NAMES:
        a = cfg.approach(name)
        draw_poly(ax, a.roi_polygon, ORANGE if name == FOCUS else "#8a8a8a",
                  f"ROI {name}" if name == FOCUS else None,
                  lw=2.4 if name == FOCUS else 1.2,
                  ls="-" if name == FOCUS else ":")
        if name == FOCUS:
            draw_poly(ax, a.queue_region, CYAN, "Queue_Region")

    # the axis
    far = stop_line + upstream * axis.length
    ax.annotate("", xy=far, xytext=stop_line,
                arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=3.2,
                                mutation_scale=26, shrinkA=0, shrinkB=0, alpha=.85))

    m = metrics[FOCUS]
    best_t, best_p = -1.0, None
    for tr in assigned:
        if tr.approach != FOCUS:
            continue
        p = (float(tr.ref_point[0]), float(tr.ref_point[1]))
        t = axis.fraction(p)
        col = YELLOW if tr.is_queueing else "#ffffff"
        ax.plot(*p, "o", ms=11, color=col, mec="black", mew=1.5, zorder=8)
        ax.text(p[0] + 13, p[1] - 13, f"t={t:.2f}", fontsize=10.5, fontweight="bold",
                color="black",
                bbox=dict(fc="white", ec="black", alpha=.9, boxstyle="round,pad=0.2"))
        if t > best_t:
            best_t, best_p = t, p

    if best_p is not None and m.queue_reach > 0:
        ax.plot(*best_p, "o", ms=24, mfc="none", mec=RED, mew=4, zorder=9)
        ax.annotate(
            f"FURTHEST STOPPED VEHICLE\n"
            f"sets  $X_i$ = reach = {m.queue_reach:.2f}",
            best_p, xytext=(95, -110), textcoords="offset points",
            fontsize=13, color="white", fontweight="bold",
            bbox=dict(fc=RED, ec="black", alpha=.95, boxstyle="round,pad=0.45"),
            arrowprops=dict(arrowstyle="->", color=RED, lw=3))

    box = (
        f"{FOCUS} approach — measured by the real code\n"
        f"  vehicle_count  n = {m.vehicle_count}\n"
        f"  queue_length   m = {m.queue_length}      <- a COUNT, saturates\n"
        f"  normalized_queue Q = {m.normalized_queue:.2f}\n"
        f"  queue_reach      X = {m.queue_reach:.2f}   <- SPATIAL, does not saturate"
    )
    ax.text(0.012, 0.985, box, transform=ax.transAxes, va="top", ha="left",
            fontsize=12, family="monospace", fontweight="bold", color="white",
            bbox=dict(fc="black", ec=GREEN, lw=2.2, alpha=.86,
                      boxstyle="round,pad=0.55"))

    ax.legend(handles=[
        mpatches.Patch(color=YELLOW, label="queueing (inside Queue_Region)"),
        mpatches.Patch(color="white", label="in ROI, not queueing"),
        mpatches.Patch(color=RED, label="furthest stopped -> sets the reach"),
    ], loc="lower right", fontsize=11, framealpha=.93)

    ax.set_title(
        f"E9 — measured on a real frame (#{FRAME_B}) with real YOLO + ByteTrack\n"
        "each vehicle's position expressed as $t$ along the approach; "
        "the reach is the maximum over stopped vehicles",
        fontsize=13.5, fontweight="bold")
    ax.set_xlim(0, frame.shape[1])
    ax.set_ylim(frame.shape[0], 0)
    ax.axis("off")
    fig.tight_layout()
    out = f"report/figure_E9_measured{SUFFIX}.png"
    fig.savefig(out, dpi=132, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out, f"(reach={m.queue_reach:.2f}, count={m.queue_length})")


# ---------------------------------------------------------------------------
# FIGURE 3 - the blind spot, as a schematic
# ---------------------------------------------------------------------------
def figure_blindspot():
    fig, axs = plt.subplots(1, 2, figsize=(15, 5.6))
    scenarios = [
        ("Case A — bunched at the stop line", [0.05, 0.11, 0.17], 0.17),
        ("Case B — strung back along the approach", [0.05, 0.45, 0.92], 0.92),
    ]
    for ax, (title, positions, reach) in zip(axs, scenarios):
        ax.add_patch(mpatches.Rectangle((0, 0.3), 1, 0.4, fc="#e9edf1",
                                        ec=ORANGE, lw=2.4))
        ax.add_patch(mpatches.Rectangle((0, 0.3), 0.2, 0.4, fc="#d5f2f8",
                                       ec=CYAN, lw=2.2))
        ax.text(0.10, 0.755, "Queue_Region", ha="center", fontsize=10.5,
                color=CYAN, fontweight="bold")
        ax.text(0.60, 0.755, "ROI (visible approach)", ha="center", fontsize=10.5,
                color=ORANGE, fontweight="bold")

        for p in positions:
            ax.add_patch(mpatches.FancyBboxPatch(
                (p - 0.035, 0.42), 0.07, 0.16, boxstyle="round,pad=0.008",
                fc=YELLOW, ec="black", lw=1.5, zorder=5))

        ax.annotate("", xy=(1.0, 0.19), xytext=(0.0, 0.19),
                    arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=3,
                                    mutation_scale=24))
        for t in (0, 0.25, 0.5, 0.75, 1.0):
            ax.plot([t, t], [0.16, 0.22], color=GREEN, lw=2.4)
            ax.text(t, 0.10, f"t={t:g}", ha="center", fontsize=10, fontweight="bold")

        ax.plot([reach, reach], [0.30, 0.70], color=RED, lw=3, ls="--", zorder=6)
        ax.text(reach, 0.79, f"reach\n$X$ = {reach:.2f}", ha="center", fontsize=12,
                color="white", fontweight="bold",
                bbox=dict(fc=RED, ec="black", boxstyle="round,pad=0.3"))

        ax.text(0.5, -0.10,
                f"queue_length (count) = {len(positions)}          "
                f"queue_reach = {reach:.2f}",
                transform=ax.transAxes, ha="center", fontsize=12.5,
                family="monospace", fontweight="bold",
                bbox=dict(fc="#fff6d6", ec="black", boxstyle="round,pad=0.4"))

        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_xlim(-0.05, 1.08)
        ax.set_ylim(-0.02, 0.95)
        ax.axis("off")

    fig.suptitle(
        "E9 — the blind spot the count cannot see (schematic)\n"
        "Both cases hold THREE stopped vehicles, so queue_length is identical. "
        "Only the spatial reach distinguishes them.",
        fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0.05, 1, 0.99))
    out = f"report/figure_E9_blindspot{SUFFIX}.png"
    fig.savefig(out, dpi=132)
    plt.close(fig)
    print("wrote", out)


if __name__ == "__main__":
    figure_geometry()
    figure_blindspot()
    figure_measured()      # slowest (loads YOLO), so last
