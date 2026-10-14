"""Show, step by step, how D, Q, X and S are computed for one approach on one real frame.

A learning tool: it replays the cached YOLOv8m + ByteTrack tracks through the project's
own MetricsEngine and QueuePredictor (so the numbers are the real ones), and prints every
intermediate value: each vehicle's box, reference point, 1-second displacement, speed in
box heights per second, stopped or not, position along the queue axis, gap allowance,
the chain walk that gives X, the slope that gives S, and the score. It also saves the
frame with each vehicle labelled by its number in the printout.

    python audit/explain_frame.py --frame 2400 --approach West
    python audit/explain_frame.py --find --approach South      # pick a frame with a queue
"""
import argparse
import dataclasses
import math
import sys

import cv2
import numpy as np

sys.path.insert(0, ".")
from src.config import APPROACH_NAMES, load_config
from src.lane_analysis import ApproachAssigner, build_approach_axes
from src.track_cache import CachedTracker
from src.traffic_metrics import MetricsEngine, QueuePredictor, compute_score_weighted

parser = argparse.ArgumentParser()
parser.add_argument("--clip", default="busy")
parser.add_argument("--frame", type=int, default=2400)
parser.add_argument("--approach", default="West", choices=APPROACH_NAMES)
parser.add_argument("--find", action="store_true",
                    help="use the first frame after 300 where the approach has X in 0.3-0.8 and >= 3 stopped cars")
parser.add_argument("--out", default="report/explain_frame.png")
args = parser.parse_args()

cfg = dataclasses.replace(
    load_config("config/bellevue_116th_v2.json"),
    stopped_window_seconds=1.0, stopped_speed_ratio=0.2, queue_tail_gap_boxes=2.0,
    use_forecast=True, forecast_window_frames=75,
)
FPS = 30.0
DT = 1.0 / FPS
axes = build_approach_axes(cfg)
axis = axes[args.approach]
appr = cfg.approach(args.approach)
assigner, engine, predictor = ApproachAssigner(cfg), MetricsEngine(cfg), QueuePredictor(cfg, FPS)
tracker = CachedTracker(f"results/track_cache/bellevue_116th_{args.clip}__yolov8m__c0p30.json.gz", cfg)
cap = cv2.VideoCapture(f"videos/bellevue_116th_{args.clip}.mp4")

x_history: list[float] = []
target = None
index = 0
while True:
    ok, frame = cap.read()
    if not ok:
        sys.exit("ran out of video before a suitable frame")
    tracks = tracker.update(None, [])
    assigned, _ = assigner.assign(tracks, frame_index=index)
    history_before = {k: list(v) for k, v in engine._history.items()}  # positions over the last second
    metrics = predictor.predict(engine.update(assigned, {a: "RED" for a in APPROACH_NAMES}, DT))
    x_history.append(metrics[args.approach].queue_reach)
    m = metrics[args.approach]
    if args.find:
        n_stopped = sum(1 for t in assigned if t.approach == args.approach and t.track_id in engine._stopped_previous)
        hit = index > 300 and 0.3 <= m.queue_reach <= 0.8 and n_stopped >= 3
    else:
        hit = index == args.frame
    if hit:
        target = (index, frame, tracks, assigned, history_before, metrics)
        break
    index += 1

index, frame, tracks, assigned, history_before, metrics = target
m = metrics[args.approach]
print(f"\n=== {args.clip} clip, frame {index} (t = {index / FPS:.2f} s), approach {args.approach} ===\n")

# ---------------------------------------------------------------- the queue axis
pts = appr.queue_axis
print("STEP 1. The queue axis (drawn once, in config/bellevue_116th_v2.json)")
print(f"  polyline points (pixels): {pts}   first point = stop line")
print(f"  total length L_axis = {axis.length:.1f} px   (position 0 = stop line, 1 = far end)\n")

# ---------------------------------------------------------------- per vehicle
print("STEP 2. Every tracked vehicle assigned to this approach on this frame")
print("  ref point = bottom-centre of the box (where the wheels touch the road)")
print("  speed = (distance moved over the last 1 s) / (1 s) / box height   -> stopped if < 0.2")
print("  position p = arc length of the ref point's projection on the axis / L_axis")
print("  allowance = 2 x (box extent along the road) / L_axis  (max gap to the car in front)\n")
hdr = f"  {'#':>2} {'id':>4} {'class':>6} {'box (x1,y1,x2,y2)':>22} {'ref pt':>12} {'h px':>5} " \
      f"{'moved px':>8} {'speed':>6} {'stopped':>7} {'p':>6} {'extent':>6} {'allow':>6} {'in strip':>8}"
print(hdr)
rows = []
k = 0
by_id = {t.track_id: t for t in tracks}
for a in assigned:
    if a.approach != args.approach:
        continue
    k += 1
    t = by_id[a.track_id]
    ref = (float(a.ref_point[0]), float(a.ref_point[1]))
    h = float(a.box_height)
    hist = history_before.get(a.track_id, [])
    window = max(2, int(round(cfg.stopped_window_seconds / DT)))
    if len(hist) >= window // 2 and h > 0:
        oldest = hist[0]
        elapsed = len(hist) * DT
        moved = math.hypot(ref[0] - oldest[0], ref[1] - oldest[1])
        speed = moved / elapsed / h
        stopped = speed < cfg.stopped_speed_ratio
        moved_s, speed_s = f"{moved:8.1f}", f"{speed:6.3f}"
    else:
        moved_s, speed_s, stopped = "   (new)", "   n/a", False
    p = axis.fraction(ref)
    extent = MetricsEngine._extent_along(axis, a, ref)
    allow = cfg.queue_tail_gap_boxes * extent / axis.length
    rows.append((k, a, p, allow, stopped, bool(a.is_queueing)))
    print(f"  {k:>2} {a.track_id:>4} {a.vehicle_class:>6} {str((t.x1, t.y1, t.x2, t.y2)):>22} "
          f"{f'({ref[0]:.0f},{ref[1]:.0f})':>12} {h:5.0f} {moved_s} {speed_s} {('YES' if stopped else 'no'):>7} "
          f"{p:6.3f} {extent:6.1f} {allow:6.3f} {('yes' if a.is_queueing else 'no'):>8}")

# ---------------------------------------------------------------- the chain walk
print("\nSTEP 3. Queue tail X: walk upstream through the STOPPED vehicles, sorted by p")
print("  start: the first stopped car inside the stop-line strip, or within its allowance of the line")
print("  continue while (p_next - p_previous) <= allowance of the next car; X = p of the last member")
chain = sorted((p, allow, inside, kk) for kk, a, p, allow, st, inside in rows if st)
tail, prev, started = 0.0, 0.0, False
for p, allow, inside, kk in chain:
    if not started:
        if inside or p <= allow:
            started, tail, prev = True, p, p
            print(f"  car #{kk}: p={p:.3f} {'in strip' if inside else f'<= allowance {allow:.3f}'} -> chain STARTS, X = {p:.3f}")
        else:
            print(f"  car #{kk}: p={p:.3f} not in strip and > allowance {allow:.3f} -> skipped (no chain yet)")
        continue
    gap = p - prev
    if gap > allow:
        print(f"  car #{kk}: p={p:.3f}, gap {gap:.3f} > allowance {allow:.3f} -> chain BREAKS here")
        break
    tail = prev = p
    print(f"  car #{kk}: p={p:.3f}, gap {gap:.3f} <= allowance {allow:.3f} -> joins, X = {p:.3f}")
print(f"  => X = {tail:.3f}   (engine reports {m.queue_reach:.3f})")
print(f"     meaning: the queue reaches {tail * 100:.0f}% of the way from the stop line to the far end of the axis\n")

# ---------------------------------------------------------------- S
n = min(len(x_history), cfg.forecast_window_frames)
ys = np.array(x_history[-n:])
ts = np.arange(n) * DT
slope = float(np.polyfit(ts, ys, 1)[0]) if n >= 2 and np.ptp(ys) > 0 else 0.0
s_hand = min(max(tail + slope * cfg.risk_horizon_seconds, 0.0), 1.0)
print(f"STEP 4. Spillback risk S = clamp(X + slope x {cfg.risk_horizon_seconds:.0f} s)")
print(f"  X over the last {n} frames ({n * DT:.1f} s): first {ys[0]:.3f}, last {ys[-1]:.3f}")
print(f"  least-squares slope = {slope:+.4f} per second")
print(f"  S = clamp({tail:.3f} + ({slope:+.4f}) x {cfg.risk_horizon_seconds:.0f}) = {s_hand:.3f}   (predictor reports {m.spillback_risk:.3f})\n")

# ---------------------------------------------------------------- D, Q, score
print("STEP 5. The count measures and the score")
count = m.vehicle_count
print(f"  D = PCE-weighted count / saturation_count = ... / {appr.saturation_count:.0f} = {m.vehicle_density:.3f}"
      f"   ({count} vehicles in the ROI)")
print(f"  Q = vehicles in the stop-line strip / queue_capacity = {m.queue_length} / {appr.queue_capacity:.0f}"
      f" = {m.normalized_queue:.3f}")
print(f"  F = clamp(Q + slope_Q x 3 s) = {m.normalized_forecast:.3f}")
s4 = compute_score_weighted(m, 0.5, 0.0, 0.0, omega=0.3, psi=0.0, rho=0.3)
base = 0.5 * m.vehicle_density + 0.5 * m.normalized_queue
print(f"  S4 score = 0.4 x (0.5 D + 0.5 Q) + 0.3 F + 0.3 S")
print(f"           = 0.4 x {base:.3f} + 0.3 x {m.normalized_forecast:.3f} + 0.3 x {m.spillback_risk:.3f} = {s4:.3f}")
print("\n  All four approaches on this frame:")
for name in APPROACH_NAMES:
    mm = metrics[name]
    sc = compute_score_weighted(mm, 0.5, 0.0, 0.0, omega=0.3, psi=0.0, rho=0.3)
    print(f"    {name:>5}: D={mm.vehicle_density:.2f} Q={mm.normalized_queue:.2f} X={mm.queue_reach:.2f} "
          f"S={mm.spillback_risk:.2f}  score={sc:.3f}")

# ---------------------------------------------------------------- picture
cv2.polylines(frame, [np.array(appr.roi_polygon, np.int32)], True, (200, 200, 200), 1)
cv2.polylines(frame, [np.array(appr.queue_region, np.int32)], True, (0, 200, 255), 2)
cv2.polylines(frame, [np.array(pts, np.int32)], False, (255, 255, 255), 2)
for kk, a, p, allow, st, inside in rows:
    t = by_id[a.track_id]
    colour = (0, 255, 0) if st else (160, 160, 160)
    cv2.rectangle(frame, (t.x1, t.y1), (t.x2, t.y2), colour, 2)
    cv2.circle(frame, (int(a.ref_point[0]), int(a.ref_point[1])), 4, (0, 0, 255), -1)
    cv2.putText(frame, f"#{kk} p={p:.2f}", (t.x1, t.y1 - 4), 0, 0.5, colour, 2)
if tail > 0:
    target_len = tail * axis.length
    for ax, ay, ux, uy, seg, start in axis._polyline:
        if target_len <= start + seg:
            px, py = ax + ux * (target_len - start), ay + uy * (target_len - start)
            break
    cv2.line(frame, (int(px - 25 * uy), int(py + 25 * ux)), (int(px + 25 * uy), int(py - 25 * ux)), (0, 0, 255), 4)
    cv2.putText(frame, f"X = {tail:.2f}", (int(px) + 10, int(py) + 25), 0, 0.8, (0, 0, 255), 2)
cv2.putText(frame, f"frame {index}, {args.approach}: green = stopped, grey = moving, red dot = ref point, "
                   f"white = queue axis, orange = stop-line strip", (10, 25), 0, 0.55, (255, 255, 255), 2)
cv2.imwrite(args.out, frame)
print(f"\nsaved {args.out}")
