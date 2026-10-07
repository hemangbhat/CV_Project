"""Build the staged ablation table from the run logs.

Identifies each run by its resolved score composition, so a row cannot be
mislabelled: the stage is read back out of the log, not assumed from a filename.
"""

import glob
import os

from src.results_store import ResultsStore
from src.evaluation import compute_metrics

store = ResultsStore()


def stage_of(log):
    """Classify a run into an ablation stage from its recorded configuration."""
    c = log.config
    if log.controller_name == "fixed":
        return 0, "S0  fixed-time"

    alpha = c.get("alpha")
    fc_w = float(c.get("forecast_weight") or 0) if c.get("use_forecast") else 0.0
    risk_w = (
        float(c.get("spillback_risk_weight") or 0)
        if c.get("use_spillback_risk")
        else 0.0
    )
    # Exact weights, not just "enabled": a run with a different forecast weight is a
    # different experiment and must not be reported in this stage's row.
    fc = abs(fc_w - 0.3) < 1e-9
    risk = abs(risk_w - 0.3) < 1e-9
    no_fc = fc_w == 0.0
    no_risk = risk_w == 0.0

    # Anything carrying the other extensions is not part of this ablation.
    others = (
        float(c.get("spillback_weight") or 0) > 0
        or float(c.get("queue_reach_weight") or 0) > 0
        or bool(c.get("use_predictive"))
        or bool(c.get("use_gap_out"))
        or bool(c.get("use_discharge_green"))
        or bool(c.get("use_pce_queue"))
    )
    if others:
        return None, None

    if alpha == 0.5 and fc and risk:
        return 4, "S4  proposed (+spillback risk)"
    if alpha == 0.5 and fc and no_risk:
        return 3, "S3  + prediction"
    if alpha == 0.5 and no_fc and no_risk:
        return 2, "S2  + queue"
    if alpha == 1.0 and no_fc and no_risk:
        return 1, "S1  Raza baseline (density)"
    return None, None


def risk_activity(log):
    """Max risk, share active, and share where risk exceeded current occupancy."""
    mx = 0.0
    active = ahead = total = 0
    for fr in log.frames:
        for a in fr["approaches"].values():
            r = a.get("spillback_risk")
            reach = a.get("queue_reach")
            if r is None:
                continue
            total += 1
            mx = max(mx, r)
            if r > 0.0:
                active += 1
            if reach is not None and r > reach + 1e-9:
                ahead += 1
    pct = (lambda n: 100.0 * n / total if total else 0.0)
    return mx, pct(active), pct(ahead)


# Keep only the newest log per stage.
best: dict[int, tuple] = {}
for path in glob.glob("results/run_logs/bellevue_116th_busy__*.json"):
    log = store.read(path)
    if not log.complete:
        continue
    idx, label = stage_of(log)
    if idx is None:
        continue
    mtime = os.path.getmtime(path)
    if idx not in best or mtime > best[idx][0]:
        best[idx] = (mtime, label, path, log)

print("STAGED ABLATION - bellevue_116th_busy.mp4 (107 s, 30 fps)\n")
print("%-30s %6s %7s %6s %8s %9s" % (
    "stage", "wait", "thru", "srv", "ovsat", "risk>reach"))
print("-" * 74)
rows = []
for idx in sorted(best):
    _, label, path, log = best[idx]
    _, a = compute_metrics(log)
    greens = [p for p in log.phases if p.get("state") == "GREEN"]
    ov = sum(1 for p in greens if p.get("oversaturation", 0.0) > 0.0)
    mx, active, ahead = risk_activity(log)
    print("%-30s %6.2f %7.1f %6d %8s %8.1f%%" % (
        label, a.avg_waiting_time, a.throughput, a.vehicles_served,
        f"{ov}/{len(greens)}", ahead))
    rows.append((idx, label, a, f"{ov}/{len(greens)}", mx, active, ahead,
                 os.path.basename(path)[:-5]))

print("\nGREEN phase sequences:")
for idx in sorted(best):
    _, label, _, log = best[idx]
    seq = [(p["approach"], p["green_time"]) for p in log.phases if p["state"] == "GREEN"]
    print(f"  {label:30s}", " -> ".join(f"{ap}/{g:g}s" for ap, g in seq))

print("\nrun_ids:")
for idx, label, *_rest, rid in rows:
    print(f"  {label:30s} {rid}")

missing = [i for i in range(5) if i not in best]
if missing:
    print(f"\n(stages not yet available: {missing})")
