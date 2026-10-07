"""Legacy vs robust queue measurement on the real footage (audit fix W5).

Replays the cached YOLO+ByteTrack tracks of each clip through the Approach_Assigner,
Metrics_Engine and Queue_Predictor twice — once with the legacy measurement (per-frame
2 px stopped test, furthest-stopped-vehicle reach, 0.5 s trend window) and once with the
robust one (1 s window in box heights/s, contiguous queue tail, 2.5 s trend window) —
and reports the statistics the audit used. Measurements do not depend on the signal in
open loop, so no controller is needed.

    python audit/measurement_compare.py            # prints a table, writes report/measurement_compare.json
"""
import dataclasses
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, ".")
from src.config import APPROACH_NAMES, load_config
from src.lane_analysis import ApproachAssigner
from src.track_cache import CachedTracker
from src.traffic_metrics import MetricsEngine, QueuePredictor

BASE = load_config("config/bellevue_116th_calibrated.json")
V2 = load_config("config/bellevue_116th_v2.json")
ROBUST = dict(stopped_window_seconds=1.0, stopped_speed_ratio=0.2,
              queue_tail_gap=0.25, forecast_window_frames=75)
MODES = {
    # The geometry and measurement every reported video result used until the audit.
    "legacy": dataclasses.replace(BASE, forecast_window_frames=15),
    # Robust measurement on the original geometry (W5 fixed, W7 not).
    "robust": dataclasses.replace(BASE, **ROBUST),
    # Robust measurement on the recalibrated v2 geometry (W5 and W7 fixed), with the
    # queue-tail gap scaled by box height (perspective) and anchored in the Queue_Region.
    "v2": dataclasses.replace(V2, **ROBUST, queue_tail_gap_boxes=2.0),
}
CLIPS = ["busy", "dev", "final"]
ALL_RED = {a: "RED" for a in APPROACH_NAMES}
DT = 1.0 / 30.0


def series(config, cache):
    assigner, engine = ApproachAssigner(config), MetricsEngine(config)
    predictor = QueuePredictor(config, 30.0)
    tracker = CachedTracker(cache, config)
    out = {a: {"Q": [], "X": [], "S": []} for a in APPROACH_NAMES}
    for i in range(tracker.frame_count):
        assigned, _ = assigner.assign(tracker.update(None, []), frame_index=i)
        metrics = predictor.predict(engine.update(assigned, ALL_RED, DT))
        for a in APPROACH_NAMES:
            out[a]["Q"].append(metrics[a].normalized_queue)
            out[a]["X"].append(metrics[a].queue_reach)
            out[a]["S"].append(metrics[a].spillback_risk)
    return out, engine.total_stops, engine.seen_vehicles


def stats(xs):
    jumps = [abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))]
    return {
        "mean": st.fmean(xs),
        "nonzero": sum(1 for x in xs if x > 0) / len(xs),
        "mean_abs_step": st.fmean(jumps),
        "jumps_gt_0p3": sum(1 for j in jumps if j > 0.3) / len(xs),
    }


def main():
    report = {}
    for clip in CLIPS:
        cache = f"results/track_cache/bellevue_116th_{clip}__yolov8n__c0p30.json.gz"
        for mode, config in MODES.items():
            data, stops, seen = series(config, cache)
            entry = {"stops_per_vehicle": stops / seen if seen else 0.0, "approaches": {}}
            for a in APPROACH_NAMES:
                entry["approaches"][a] = {
                    "Q_saturated": sum(1 for q in data[a]["Q"] if q >= 1.0) / len(data[a]["Q"]),
                    "X": stats(data[a]["X"]),
                    "S": stats(data[a]["S"]),
                }
            report.setdefault(clip, {})[mode] = entry
    Path("report").mkdir(exist_ok=True)
    Path("report/measurement_compare.json").write_text(json.dumps(report, indent=1))

    names = list(MODES)
    print("Per mode: Q=1 share | X>0 share | mean X | mean |dX| per frame | X jumps > 0.3 | mean |dS| per frame")
    for clip, modes in report.items():
        print(f"== {clip}: stops/vehicle " + "  ".join(
            f"{m} {modes[m]['stops_per_vehicle']:.2f}" for m in names))
        for a in APPROACH_NAMES:
            cells = []
            for m in names:
                e = modes[m]["approaches"][a]
                cells.append(f"{m:6s} {e['Q_saturated']:5.1%} {e['X']['nonzero']:6.1%} {e['X']['mean']:.3f} "
                             f"{e['X']['mean_abs_step']:.4f} {e['X']['jumps_gt_0p3']:5.2%} {e['S']['mean_abs_step']:.4f}")
            print(f"  {a:6s} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
