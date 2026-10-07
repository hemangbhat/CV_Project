"""Diagnose WHICH mechanism selected each green, and whether a score term could matter.

Why this exists. The spatial spillback-risk term gave +20.6% throughput on the busy clip and
changed nothing at all on the development clip. Before concluding anything from that, it has to
be established whether the term failed to fire, fired but lost, or was never consulted. This
script answers that from the Run_Logs.

The key distinction it draws is between two selection regimes:

  score-driven      the green went to the approach the score ranked highest
  starvation-driven the green was forced by the starvation guarantee, because approaches were
                    being skipped for too long

A score term can only ever influence a *score-driven* green. On a lightly loaded junction most
approaches are empty, so many frames have every score at exactly zero and selection falls to the
starvation guarantee. In that regime no score term — however well designed — can change a
decision, and reporting it as "the enhancement did not work" would be a misdiagnosis.

Usage:
    python diagnose_regime.py                      # every clip with an S4 run
    python diagnose_regime.py --clip bellevue_116th_dev
"""

from __future__ import annotations

import argparse
import glob
import json
import os

NAMES = ["North", "East", "South", "West"]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--clip", default=None, help="restrict to one clip stem")
args = parser.parse_args()


def newest_s4_per_clip() -> dict[str, str]:
    """Newest run per clip that has the spillback-risk term enabled."""
    best: dict[str, tuple[float, str]] = {}
    for path in glob.glob("results/run_logs/*.json"):
        try:
            log = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if not log.get("complete"):
            continue
        config = log.get("config", {})
        if not config.get("use_spillback_risk"):
            continue
        clip = os.path.basename(log.get("video_path", "")).replace(".mp4", "")
        mtime = os.path.getmtime(path)
        if clip not in best or mtime > best[clip][0]:
            best[clip] = (mtime, path)
    return {clip: path for clip, (_, path) in best.items()}


runs = newest_s4_per_clip()
if args.clip:
    runs = {k: v for k, v in runs.items() if k == args.clip}
if not runs:
    raise SystemExit("no completed run with use_spillback_risk found")

print("=" * 82)
print("SELECTION REGIME DIAGNOSIS - what actually chose each green?")
print("=" * 82)

for clip in sorted(runs):
    log = json.load(open(runs[clip], encoding="utf-8"))
    frames = log["frames"]
    greens = [p for p in log["phases"] if p["state"] == "GREEN"]
    overrides = log.get("starvation_overrides", [])
    override_cycles = {
        o.get("cycle_index") for o in overrides if isinstance(o, dict)
    }

    # How often can the score discriminate at all?
    all_zero = sum(
        1 for f in frames
        if all(f["approaches"][n]["score"] <= 1e-9 for n in NAMES)
    )

    # Did the green go to the top-scoring approach?
    matched = 0
    considered = 0
    rows = []
    for phase in greens:
        selection_frame = max(phase["start_frame"] - 1, 0)
        record = next(
            (f for f in frames if f["frame_index"] == selection_frame), None
        )
        if record is None:
            continue
        considered += 1
        scores = {n: record["approaches"][n]["score"] for n in NAMES}
        risks = {
            n: record["approaches"][n].get("spillback_risk", 0.0) for n in NAMES
        }
        top = max(NAMES, key=lambda n: scores[n])
        is_match = top == phase["approach"]
        matched += is_match
        nonzero = sum(1 for v in scores.values() if v > 1e-9)
        regime = (
            "starvation" if phase.get("cycle_index") in override_cycles
            else ("score" if nonzero else "score (all zero)")
        )
        rows.append((phase.get("cycle_index"), phase["approach"], top, is_match,
                     nonzero, regime, max(risks.values())))

    starvation_greens = sum(1 for r in rows if r[5] == "starvation")
    score_greens = considered - starvation_greens

    print(f"\n{clip}")
    print(f"  run_id : {log.get('run_id')}")
    print(f"  greens : {considered}   score-driven: {score_greens}   "
          f"starvation-driven: {starvation_greens}")
    print(f"  green went to the top-scoring approach: {matched}/{considered}")
    print(f"  frames where EVERY approach scores 0  : {all_zero}/{len(frames)} "
          f"({100.0 * all_zero / len(frames):.1f}%)")

    print(f"\n  {'cycle':>5s} {'chosen':8s} {'top-scored':11s} {'match':6s} "
          f"{'roads scoring>0':>15s} {'regime':>16s} {'max risk':>9s}")
    for cycle, chosen, top, is_match, nonzero, regime, max_risk in rows:
        print(f"  {cycle if cycle is not None else -1:5d} {chosen:8s} {top:11s} "
              f"{str(is_match):6s} {nonzero:15d} {regime:>16s} {max_risk:9.2f}")

    if overrides:
        empty = [
            o for o in overrides
            if isinstance(o, dict) and float(o.get("selection_score") or 0) <= 1e-9
        ]
        print(f"\n  starvation overrides: {len(overrides)}, of which "
              f"{len(empty)} served an approach scoring EXACTLY zero")

    verdict = (
        "score-driven: a score term CAN influence selection here"
        if score_greens > starvation_greens
        else "starvation-driven: a score term CANNOT influence most selections here"
    )
    print(f"\n  VERDICT: {verdict}")

print("\n" + "=" * 82)
print("How to read this")
print("=" * 82)
print("""  A green marked 'starvation' was forced by the fairness guarantee, not chosen on
  score. Where those dominate, an inert score term is structurally bypassed rather
  than ineffective, and the honest conclusion is about the REGIME, not the term.
  See report/multiclip_reproducibility.md.""")
