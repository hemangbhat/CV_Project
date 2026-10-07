"""Draw the measured queue tail on real frames, for visual validation of X.

For each requested frame: tracked boxes (green = judged stopped, grey = moving), each
approach's drawn queue axis, and a red bar across the axis at the measured queue reach.
If the bar sits at the back of the visible queue, the measurement is right.

    python audit/render_queue_tail.py --clip busy --frames 600 1500 2400 3000
"""
import argparse
import dataclasses
import sys

import cv2
import numpy as np

sys.path.insert(0, ".")
from src.config import APPROACH_NAMES, load_config
from src.lane_analysis import ApproachAssigner, build_approach_axes
from src.track_cache import CachedTracker
from src.traffic_metrics import MetricsEngine

parser = argparse.ArgumentParser()
parser.add_argument("--clip", default="busy")
parser.add_argument("--frames", type=int, nargs="+", default=[600, 1500, 2400, 3000])
parser.add_argument("--out", default="report/queue_tail_validation.png")
args = parser.parse_args()

cfg = dataclasses.replace(
    load_config("config/bellevue_116th_v2.json"),
    stopped_window_seconds=1.0, stopped_speed_ratio=0.2, queue_tail_gap_boxes=2.0,
)
axes = build_approach_axes(cfg)
assigner, engine = ApproachAssigner(cfg), MetricsEngine(cfg)
tracker = CachedTracker(f"results/track_cache/bellevue_116th_{args.clip}__yolov8n__c0p30.json.gz", cfg)
cap = cv2.VideoCapture(f"videos/bellevue_116th_{args.clip}.mp4")
wanted, panels = set(args.frames), []


def axis_point(axis, fraction):
    target = fraction * axis.length
    for ax, ay, ux, uy, seg, start in axis._polyline:
        if target <= start + seg:
            t = target - start
            return (ax + ux * t, ay + uy * t), (-uy, ux)
    ax, ay, ux, uy, seg, _ = axis._polyline[-1]
    return (ax + ux * seg, ay + uy * seg), (-uy, ux)


for index in range(max(wanted) + 1):
    ok, frame = cap.read()
    tracks = tracker.update(None, [])
    assigned, _ = assigner.assign(tracks, frame_index=index)
    before = dict(engine._previous_points)
    metrics = engine.update(assigned, {a: "RED" for a in APPROACH_NAMES}, 1 / 30)
    if index not in wanted:
        continue
    for approach in cfg.approaches:
        cv2.polylines(frame, [np.array(approach.roi_polygon, np.int32)], True, (200, 200, 200), 1)
        cv2.polylines(frame, [np.array(approach.queue_axis, np.int32)], False, (255, 255, 255), 1)
    for t, a in zip(tracks, assigned):
        if a.approach is None:
            continue
        stopped = t.track_id in engine._stopped_previous
        cv2.rectangle(frame, (t.x1, t.y1), (t.x2, t.y2), (0, 255, 0) if stopped else (150, 150, 150), 2)
    for name in APPROACH_NAMES:
        reach = metrics[name].queue_reach
        if reach <= 0:
            continue
        (px, py), (nx, ny) = axis_point(axes[name], reach)
        p1 = (int(px - 25 * nx), int(py - 25 * ny))
        p2 = (int(px + 25 * nx), int(py + 25 * ny))
        cv2.line(frame, p1, p2, (0, 0, 255), 4)
        cv2.putText(frame, f"{name} X={reach:.2f}", (p2[0] + 4, p2[1]), 0, 0.55, (0, 0, 255), 2)
    cv2.putText(frame, f"{args.clip} frame {index}: green box = stopped, red bar = measured queue tail",
                (10, 25), 0, 0.6, (255, 255, 255), 2)
    panels.append(frame)

cv2.imwrite(args.out, np.vstack(panels))
print("wrote", args.out)
