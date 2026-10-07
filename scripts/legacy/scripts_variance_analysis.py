"""Run-to-run variance / reproducibility analysis over repeated runs.

Groups run logs by (video, controller, resolved-config-signature) so that only
genuinely identical experiments are compared, then reports mean and spread for each
metric. Wall-clock processing FPS is reported separately because it is an environment
measurement, not a control result.
"""

import glob
import json
import os
import statistics as st

from src.results_store import ResultsStore
from src.evaluation import compute_metrics

store = ResultsStore()

# The config keys that define an experiment. Anything outside this set cannot change
# the control decisions, so two runs agreeing on these are the same experiment.
KEYS = [
    "alpha", "use_pce_queue", "spillback_weight", "use_discharge_green",
    "saturation_flow_rate", "switching_margin", "green_rate_limit", "use_gap_out",
    "use_forecast", "forecast_weight", "forecast_horizon_seconds",
    "forecast_window_frames", "use_predictive", "predictive_weight",
    "min_green_time", "max_green_time", "stopped_displacement",
]


#: Defaults so a log written before a key existed compares equal to a newer log that
#: carries the key at its neutral value — otherwise identical experiments look different.
DEFAULTS = {
    "use_pce_queue": False, "spillback_weight": 0.0, "use_discharge_green": False,
    "saturation_flow_rate": 0.5, "switching_margin": 0.0, "green_rate_limit": 0.0,
    "use_gap_out": False, "use_forecast": False, "forecast_weight": 0.0,
    "forecast_horizon_seconds": 3.0, "forecast_window_frames": 15,
    "use_predictive": False, "predictive_weight": 0.0, "stopped_displacement": 2.0,
}


def signature(log):
    c = log.config
    return json.dumps(
        {k: (c[k] if c.get(k) is not None else DEFAULTS.get(k)) for k in KEYS},
        sort_keys=True,
    )


groups = {}
for p in glob.glob("results/run_logs/bellevue_116th_busy__adaptive__*.json"):
    log = store.read(p)
    if not log.complete:
        continue
    key = (os.path.basename(p).split("__")[0], log.controller_name, signature(log))
    groups.setdefault(key, []).append((p, log))

print("Repeat-run analysis (busy clip, complete runs only)\n")
for (video, ctrl, sig) in sorted(groups, key=lambda k: k[2]):
    runs = groups[(video, ctrl, sig)]
    if len(runs) < 2:
        continue
    cfg = json.loads(sig)
    label = "forecast(w=%.1f)" % cfg["forecast_weight"] if cfg["use_forecast"] else "reference"
    extras = []
    if cfg["use_discharge_green"]:
        extras.append("E3")
    if cfg["use_gap_out"]:
        extras.append("E6")
    if cfg["spillback_weight"]:
        extras.append("E2")
    if cfg["switching_margin"] or cfg["green_rate_limit"]:
        extras.append("E4")
    if cfg["use_pce_queue"]:
        extras.append("E1")
    if extras:
        label += "+" + "+".join(extras)

    waits, thrus, srvs, stops, fps = [], [], [], [], []
    for p, log in runs:
        _, a = compute_metrics(log)
        waits.append(a.avg_waiting_time)
        thrus.append(a.throughput)
        srvs.append(a.vehicles_served)
        fps.append(a.processing_fps or 0.0)
        # Stop counting (E7) was added later; a log written before it has no stop
        # record at all. Absent is not zero, so those runs are skipped for this
        # metric rather than dragging the mean toward 0.
        if log.seen_vehicles > 0:
            stops.append(log.total_stops)

    def fmt(vals, prec=3):
        if len(set(vals)) == 1:
            return f"{vals[0]:.{prec}f}  (identical)"
        return f"{st.mean(vals):.{prec}f} +/- {st.pstdev(vals):.{prec}f}  (min {min(vals):.{prec}f}, max {max(vals):.{prec}f})"

    print(f"--- {label}   n={len(runs)} runs")
    print(f"    avg waiting (s) : {fmt(waits)}")
    print(f"    throughput      : {fmt(thrus, 1)}")
    print(f"    vehicles served : {fmt([float(v) for v in srvs], 0)}")
    if stops:
        note = "" if len(stops) == len(runs) else f"  [{len(stops)}/{len(runs)} runs recorded stops]"
        print(f"    total stops     : {fmt([float(v) for v in stops], 0)}{note}")
    print(f"    processing fps  : {fmt(fps, 2)}   <- wall-clock, environment-dependent")
    for p, _ in sorted(runs):
        print(f"      run_id: {os.path.basename(p)[:-5]}")
    print()
