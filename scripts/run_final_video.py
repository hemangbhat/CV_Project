"""Final open-loop video runs: decision analysis on the real footage.

For each clip with a YOLOv8m track cache, run fixed-time and every adaptive arm of
config/final/ over the IDENTICAL cached tracks, and report:

* each arm's green sequence and how many decisions differ from S3 (the decision
  analysis the open-loop footage can validly support), and
* the open-loop book-keeping metrics, labelled as such. They measure overlap with the
  real filmed signal, not control quality (AUDIT_REPORT.md W1).

NULL (S4 with S forced to 0) cannot be expressed as a config, so it is evaluated by
replaying S4's logged per-frame metrics through the real controller with S zeroed.

    python scripts/run_final_video.py            # writes results/final_video/
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from src.config import APPROACH_NAMES, load_config  # noqa: E402
from src.main import Pipeline  # noqa: E402
from src.results_store import ResultsStore  # noqa: E402
from src.signal_controller import AdaptiveController, PhaseSequencer  # noqa: E402
from src.track_cache import CachedTracker  # noqa: E402
from src.traffic_metrics import ApproachMetrics, compute_config_scores  # noqa: E402

OUT = Path("results/final_video")
CLIPS = ["busy", "dev", "final"]
ARMS = ["S1", "S2", "S3", "S4", "S4X", "S3S", "S3X"]
FIELDS = ["vehicle_count", "vehicle_density", "queue_length", "normalized_queue",
          "normalized_arrival", "queue_pce", "normalized_spillback", "normalized_forecast",
          "queue_reach", "spillback_risk"]


def greens(phases) -> list[str]:
    return [f"{p['approach'][0]}{int(p['green_time'])}{'*' if p['truncated'] else ''}"
            for p in phases if p["state"] == "GREEN"]


def null_replay(log: dict, config) -> list[str]:
    """Replay S4's per-frame metrics through the real controller with S forced to 0."""
    seq = PhaseSequencer(AdaptiveController(config), config, 30.0)
    scores = {a: 0.0 for a in APPROACH_NAMES}
    for i, frame in enumerate(log["frames"]):
        seq.tick(i, scores)
        metrics = {
            a: ApproachMetrics(approach=a, **{**{k: frame["approaches"][a][k] for k in FIELDS},
                                              "spillback_risk": 0.0})
            for a in APPROACH_NAMES
        }
        scores = compute_config_scores(metrics, config)
    seq.finalize(len(log["frames"]) - 1)
    return [f"{p.approach[0]}{int(p.green_time)}{'*' if p.truncated else ''}"
            for p in seq.phases if p.state.value == "GREEN"]


def differing(a: list[str], b: list[str]) -> int:
    n = sum(1 for x, y in zip(a, b) if x[0] != y[0])
    return n + abs(len(a) - len(b))


def run(clip: str, store: ResultsStore) -> dict:
    video = f"videos/bellevue_116th_{clip}.mp4"
    cache = f"results/track_cache/bellevue_116th_{clip}__yolov8m__c0p30.json.gz"
    rows = {}
    plans = [("S0", "config/bellevue_116th_v2.json", "fixed")] + [
        (arm, f"config/final/{arm}.json", "adaptive") for arm in ARMS
    ]
    logs = {}
    for name, cfg_path, controller in plans:
        config = load_config(cfg_path)
        result = Pipeline(video, config, controller, tracker=CachedTracker(cache, config),
                          store=store).run()
        log = json.loads(Path(result.log_path).read_text())
        logs[name] = log
        agg = log["evaluation_metrics"]["aggregate"]
        rows[name] = {
            "greens": greens(log["phases"]),
            "open_loop_served": agg["vehicles_served"],
            "open_loop_wait_s": round(agg["avg_waiting_time"], 3),
            "run_id": log["run_id"],
        }
        print(f"  {clip:5s} {name:4s} {' '.join(rows[name]['greens'])}")
    rows["NULL"] = {"greens": null_replay(logs["S4"], load_config("config/final/S4.json")),
                    "note": "replay of S4 metrics with S = 0"}
    for name in rows:
        rows[name]["decisions_differing_from_S3"] = differing(rows[name]["greens"], rows["S3"]["greens"])
    # Measurement statistics on the final geometry + detector (controller-invariant).
    frames = logs["S4"]["frames"]
    stats = {}
    for a in APPROACH_NAMES:
        X = [f["approaches"][a]["queue_reach"] for f in frames]
        Q = [f["approaches"][a]["normalized_queue"] for f in frames]
        jumps = [abs(X[i] - X[i - 1]) for i in range(1, len(X))]
        stats[a] = {
            "Q_saturated_share": sum(1 for q in Q if q >= 1) / len(Q),
            "X_nonzero_share": sum(1 for x in X if x > 0) / len(X),
            "X_mean": sum(X) / len(X),
            "X_jump_gt_0p3_share": sum(1 for j in jumps if j > 0.3) / len(X),
        }
    return {"arms": rows, "measurement": stats,
            "stops_per_vehicle": sum(logs["S4"]["stops"].values()) / max(1, logs["S4"]["seen_vehicles"])}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = ResultsStore(str(OUT / "run_logs"))
    summary = {}
    for clip in CLIPS:
        if not Path(f"results/track_cache/bellevue_116th_{clip}__yolov8m__c0p30.json.gz").exists():
            print(f"  {clip}: no YOLOv8m cache yet, skipped")
            continue
        summary[clip] = run(clip, store)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"wrote {OUT / 'summary.json'}")


if __name__ == "__main__":
    main()
