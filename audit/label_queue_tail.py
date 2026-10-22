"""Hand-label the true queue end on real frames (ground truth for X).

Opens one frame at a time with one approach's queue axis drawn. You click where the
queue really ends: the back of the last stopped vehicle that is part of the queue
starting at the stop line. Keys:

    left click   mark the queue end (click again to move it)
    n            no queue on this approach (true X = 0)
    s            skip (cannot tell, e.g. occluded)
    Enter/Space  accept and go to the next approach / frame
    q            save and quit (labels so far are kept; rerun to continue)

    python audit/label_queue_tail.py --clip busy --every 150 --approaches West South North East

Labels go to results/labels/queue_tail_<clip>.json as pixel points; the scorer converts
them to axis fractions with the same queue axis the pipeline uses. Needs a display, so
run it on your own computer.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, ".")
from src.config import load_config

parser = argparse.ArgumentParser()
parser.add_argument("--clip", default="busy")
parser.add_argument("--every", type=int, default=150, help="label one frame every N frames (150 = 5 s)")
parser.add_argument("--start", type=int, default=300, help="first frame (skip warm-up)")
parser.add_argument("--approaches", nargs="+", default=["West", "South", "North", "East"])
args = parser.parse_args()

cfg = load_config("config/bellevue_116th_v2.json")
out = Path(f"results/labels/queue_tail_{args.clip}.json")
out.parent.mkdir(parents=True, exist_ok=True)
labels = json.loads(out.read_text()) if out.exists() else {"clip": args.clip, "labels": []}
done = {(l["frame"], l["approach"]) for l in labels["labels"]}

cap = cv2.VideoCapture(f"videos/bellevue_116th_{args.clip}.mp4")
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
click = {"pt": None}


def on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        click["pt"] = (x, y)


cv2.namedWindow("label", cv2.WINDOW_NORMAL)
cv2.setMouseCallback("label", on_mouse)
quit_now = False
for frame_index in range(args.start, total, args.every):
    if quit_now:
        break
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    if not ok:
        break
    for name in args.approaches:
        if (frame_index, name) in done:
            continue
        appr = cfg.approach(name)
        click["pt"] = None
        decision = None
        while decision is None:
            view = frame.copy()
            cv2.polylines(view, [np.array(appr.roi_polygon, np.int32)], True, (0, 200, 255), 2)
            cv2.polylines(view, [np.array(appr.queue_axis, np.int32)], False, (255, 255, 255), 2)
            cv2.circle(view, tuple(appr.queue_axis[0]), 6, (0, 0, 255), -1)
            if click["pt"]:
                cv2.drawMarker(view, click["pt"], (0, 0, 255), cv2.MARKER_CROSS, 24, 3)
            cv2.putText(view, f"frame {frame_index}  {name}: click queue end | n = no queue | s = skip | "
                              f"Enter = accept | q = quit", (10, 25), 0, 0.6, (255, 255, 255), 2)
            cv2.imshow("label", view)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                decision, quit_now = "quit", True
            elif key == ord("s"):
                decision = "skip"
            elif key == ord("n"):
                decision = "none"
            elif key in (13, 32) and click["pt"]:
                decision = "point"
        if decision == "quit":
            break
        if decision == "skip":
            continue
        labels["labels"].append({
            "frame": frame_index, "approach": name,
            "point": list(click["pt"]) if decision == "point" else None,
        })
        out.write_text(json.dumps(labels, indent=1))
cv2.destroyAllWindows()
out.write_text(json.dumps(labels, indent=1))
print(f"{len(labels['labels'])} labels saved to {out}")
