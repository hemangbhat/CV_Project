"""Summarise the S3-vs-S4 comparison across every clip it has been run on.

The purpose is to test whether the S3 -> S4 finding reproduces. On the 107 s busy clip the
two stages differ in a single selection decision, which is a correctly measured difference
but not evidence of an average effect. Running the same comparison on the 240 s clips gives
more phases, and therefore more opportunities for the mechanism to act or fail to act.

Stage identity is read back out of each Run_Log's resolved configuration by reusing
``ablation_table.stage_of``, so a row cannot be mislabelled by a filename. Metrics are
recomputed from the raw frame records by ``evaluation.compute_metrics`` rather than read
from stored aggregates.
"""

from __future__ import annotations

import glob
import os

from ablation_table import stage_of
from src.evaluation import compute_metrics
from src.results_store import ResultsStore

store = ResultsStore()

# clip stem -> stage index -> (mtime, label, path, log)
per_clip: dict[str, dict[int, tuple]] = {}

for path in glob.glob("results/run_logs/*.json"):
    try:
        log = store.read(path)
    except Exception:
        continue
    if not log.complete:
        continue
    index, label = stage_of(log)
    if index is None:
        continue
    clip = os.path.basename(log.video_path).replace(".mp4", "")
    mtime = os.path.getmtime(path)
    bucket = per_clip.setdefault(clip, {})
    if index not in bucket or mtime > bucket[index][0]:
        bucket[index] = (mtime, label, path, log)

ROLES = {
    "bellevue_116th_busy": "busy (107 s)",
    "bellevue_116th_dev": "development (240 s)",
    "bellevue_116th_final": "final / held-out (240 s)",
}

print("=" * 86)
print("S3 vs S4 ACROSS CLIPS  -  does the spatial-risk finding reproduce?")
print("=" * 86)

summary = []
for clip in sorted(per_clip, key=lambda c: (c not in ROLES, c)):
    stages = per_clip[clip]
    if 3 not in stages or 4 not in stages:
        continue
    _, _, p3, log3 = stages[3]
    _, _, p4, log4 = stages[4]
    _, m3 = compute_metrics(log3)
    _, m4 = compute_metrics(log4)

    g3 = [p for p in log3.phases if p["state"] == "GREEN"]
    g4 = [p for p in log4.phases if p["state"] == "GREEN"]
    ov3 = sum(1 for p in g3 if p.get("oversaturation", 0.0) > 0.0)
    ov4 = sum(1 for p in g4 if p.get("oversaturation", 0.0) > 0.0)

    seq3 = [p["approach"] for p in g3]
    seq4 = [p["approach"] for p in g4]
    differing = sum(1 for x, y in zip(seq3, seq4) if x != y) + abs(len(seq3) - len(seq4))

    def pct(new: float, old: float) -> str:
        return f"{100.0 * (new - old) / old:+.1f}%" if old else "n/a"

    print(f"\n{ROLES.get(clip, clip)}   [{clip}]")
    print(f"  {'metric':22s} {'S3':>10s} {'S4':>10s} {'change':>10s}")
    print(f"  {'throughput (veh/min)':22s} {m3.throughput:10.2f} {m4.throughput:10.2f} "
          f"{pct(m4.throughput, m3.throughput):>10s}")
    print(f"  {'vehicles served':22s} {m3.vehicles_served:10d} {m4.vehicles_served:10d} "
          f"{pct(m4.vehicles_served, m3.vehicles_served):>10s}")
    print(f"  {'avg waiting (s)':22s} {m3.avg_waiting_time:10.3f} "
          f"{m4.avg_waiting_time:10.3f} {pct(m4.avg_waiting_time, m3.avg_waiting_time):>10s}")
    print(f"  {'over-saturated greens':22s} {f'{ov3}/{len(g3)}':>10s} "
          f"{f'{ov4}/{len(g4)}':>10s}")
    print(f"  {'green phases':22s} {len(g3):10d} {len(g4):10d}")
    print(f"  {'decisions differing':22s} {differing:10d}")
    print(f"  S3 sequence: {' -> '.join(seq3)}")
    print(f"  S4 sequence: {' -> '.join(seq4)}")

    # Open-loop invariants must hold within each clip regardless of controller.
    inv = (
        abs(m3.avg_queue_length - m4.avg_queue_length) < 1e-9
        and m3.max_queue_length == m4.max_queue_length
    )
    print(f"  open-loop invariance (queue identical S3 vs S4): {inv}")
    if not inv:
        print(f"    !! avg_queue {m3.avg_queue_length:.4f} vs {m4.avg_queue_length:.4f}"
              f"  max {m3.max_queue_length} vs {m4.max_queue_length}")

    summary.append((
        ROLES.get(clip, clip), m3.throughput, m4.throughput,
        m3.vehicles_served, m4.vehicles_served,
        m3.avg_waiting_time, m4.avg_waiting_time,
        f"{ov3}/{len(g3)}", f"{ov4}/{len(g4)}", differing,
        os.path.basename(p3)[:-5], os.path.basename(p4)[:-5],
    ))

if not summary:
    print("\nNo clip yet has BOTH S3 and S4 runs. Run: python run_multiclip_ablation.py")
    raise SystemExit(0)

print("\n" + "=" * 86)
print("REPRODUCIBILITY SUMMARY")
print("=" * 86)
print(f"  {'clip':26s} {'thru S3':>8s} {'thru S4':>8s} {'change':>8s} "
      f"{'wait S3':>8s} {'wait S4':>8s} {'ovsat':>10s} {'diff dec':>8s}")
for (label, t3, t4, s3, s4, w3, w4, o3, o4, diff, _, _) in summary:
    change = f"{100.0 * (t4 - t3) / t3:+.1f}%" if t3 else "n/a"
    print(f"  {label:26s} {t3:8.1f} {t4:8.1f} {change:>8s} {w3:8.2f} {w4:8.2f} "
          f"{o3 + ' -> ' + o4:>10s} {diff:8d}")

gains = [100.0 * (t4 - t3) / t3 for (_, t3, t4, *_r) in summary if t3]
if len(gains) > 1:
    mean = sum(gains) / len(gains)
    spread = max(gains) - min(gains)
    print(f"\n  throughput change across {len(gains)} clips: "
          f"mean {mean:+.1f}%, range {min(gains):+.1f}% to {max(gains):+.1f}% "
          f"(spread {spread:.1f} points)")
    helped = sum(1 for g in gains if g > 0)
    print(f"  clips where S4 improved throughput: {helped}/{len(gains)}")
else:
    print("\n  only one clip available: this is NOT yet evidence of reproducibility")

print("\nrun_ids:")
for (label, *_rest, r3, r4) in summary:
    print(f"  {label:26s} S3 {r3}")
    print(f"  {'':26s} S4 {r4}")
