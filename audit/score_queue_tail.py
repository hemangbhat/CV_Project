"""Score the measured queue tail X against hand labels (audit/label_queue_tail.py).

Replays the cached tracks through the project's MetricsEngine, reads the measured X on
every labelled frame, converts each labelled pixel point to an axis fraction with the same
queue axis, and reports, overall and per approach:

* MAE  - mean absolute error |X_measured - X_true| in axis fraction;
* bias - mean signed error (negative = X underestimates the queue);
* within 0.10 - share of labels where the measurement is within 10% of the axis length;
* queue detection - agreement on "is there a queue at all" (X > 0 vs label != none).

    python audit/score_queue_tail.py --clip busy
"""
import argparse
import dataclasses
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
from src.config import APPROACH_NAMES, load_config
from src.lane_analysis import ApproachAssigner, build_approach_axes
from src.track_cache import CachedTracker
from src.traffic_metrics import MetricsEngine

parser = argparse.ArgumentParser()
parser.add_argument("--clip", default="busy")
parser.add_argument("--labels", default=None)
parser.add_argument("--out", default=None)
args = parser.parse_args()

label_path = Path(args.labels or f"results/labels/queue_tail_{args.clip}.json")
labels = json.loads(label_path.read_text())["labels"]
if not labels:
    sys.exit("no labels yet: run audit/label_queue_tail.py first")
wanted = defaultdict(list)
for l in labels:
    wanted[l["frame"]].append(l)

cfg = dataclasses.replace(
    load_config("config/bellevue_116th_v2.json"),
    stopped_window_seconds=1.0, stopped_speed_ratio=0.2, queue_tail_gap_boxes=2.0,
)
axes = build_approach_axes(cfg)
assigner, engine = ApproachAssigner(cfg), MetricsEngine(cfg)
tracker = CachedTracker(f"results/track_cache/bellevue_116th_{args.clip}__yolov8m__c0p30.json.gz", cfg)

rows = []
last = max(wanted)
for index in range(last + 1):
    tracks = tracker.update(None, [])
    assigned, _ = assigner.assign(tracks, frame_index=index)
    metrics = engine.update(assigned, {a: "RED" for a in APPROACH_NAMES}, 1 / 30)
    for l in wanted.get(index, []):
        name = l["approach"]
        true_x = 0.0 if l["point"] is None else axes[name].fraction(tuple(l["point"]))
        rows.append((name, metrics[name].queue_reach, true_x))


def summary(sub):
    err = [m - t for _, m, t in sub]
    detect = [((m > 0) == (t > 0)) for _, m, t in sub]
    return {
        "n": len(sub),
        "mae": round(statistics.fmean(abs(e) for e in err), 3),
        "bias": round(statistics.fmean(err), 3),
        "within_0.10": round(sum(abs(e) <= 0.10 for e in err) / len(err), 3),
        "queue_detection_agreement": round(sum(detect) / len(detect), 3),
    }


report = {"clip": args.clip, "overall": summary(rows)}
for name in APPROACH_NAMES:
    sub = [r for r in rows if r[0] == name]
    if sub:
        report[name] = summary(sub)
print(json.dumps(report, indent=1))
out = Path(args.out or f"results/labels/queue_tail_{args.clip}_score.json")
out.write_text(json.dumps(report, indent=1))
print("saved", out)
