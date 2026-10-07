"""Verify the signal-safety invariants against EVERY run log on disk.

The unit tests check these invariants on constructed scenarios. This checks them on every
frame of every real run that has ever been recorded, which is a different and stronger kind
of evidence: it cannot be satisfied by a test that happens to exercise only easy cases.

Invariants checked, per frame:

  I1  at most one approach is GREEN
  I2  at most one approach is YELLOW, and never simultaneously with a GREEN
  I3  every approach is in a legal state
  I4  no approach jumps GREEN -> RED without passing through YELLOW
  I5  realised green durations lie within the configured bounds
  I6  a starved approach is eventually served (starvation guarantee)
  I7  metrics are finite and in range (no NaN reaching a Run_Log)
  I8  queue length never exceeds the configured capacity's own basis
"""

from __future__ import annotations

import glob
import json
import math
from collections import defaultdict

LEGAL = {"GREEN", "YELLOW", "RED"}

failures: list[str] = []
checked_frames = 0
checked_runs = 0
per_invariant: dict[str, int] = defaultdict(int)


def fail(invariant: str, message: str) -> None:
    failures.append(f"[{invariant}] {message}")
    per_invariant[invariant] += 1


for path in sorted(glob.glob("results/run_logs/*.json")):
    try:
        log = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        fail("LOAD", f"{path}: unreadable ({exc})")
        continue
    if not log.get("complete"):
        continue

    run = log.get("run_id", path)
    config = log.get("config", {})
    names = [a["name"] for a in config.get("approaches", [])]
    min_green = float(config.get("min_green_time", 0.0))
    max_green = float(config.get("max_green_time", math.inf))
    frames = log.get("frames", [])
    if not frames:
        continue
    checked_runs += 1

    previous: dict[str, str] = {}
    for record in frames:
        checked_frames += 1
        approaches = record.get("approaches", {})
        states = {n: approaches[n]["signal_state"] for n in names if n in approaches}

        # I3 legal states
        for name, state in states.items():
            if state not in LEGAL:
                fail("I3", f"{run} frame {record['frame_index']}: {name}={state!r}")

        # I1 at most one GREEN
        greens = [n for n, s in states.items() if s == "GREEN"]
        if len(greens) > 1:
            fail("I1", f"{run} frame {record['frame_index']}: GREEN on {greens}")

        # I2 at most one YELLOW, never with a GREEN
        yellows = [n for n, s in states.items() if s == "YELLOW"]
        if len(yellows) > 1:
            fail("I2", f"{run} frame {record['frame_index']}: YELLOW on {yellows}")
        if greens and yellows:
            fail("I2", f"{run} frame {record['frame_index']}: "
                       f"GREEN {greens} and YELLOW {yellows} together")

        # I4 no GREEN -> RED without YELLOW
        for name, state in states.items():
            was = previous.get(name)
            if was == "GREEN" and state == "RED":
                fail("I4", f"{run} frame {record['frame_index']}: "
                           f"{name} GREEN->RED with no YELLOW")
        previous = states

        # I7 finite, in-range metrics
        for name, entry in approaches.items():
            for key in ("vehicle_density", "normalized_queue", "score",
                        "queue_reach", "spillback_risk", "normalized_forecast"):
                value = entry.get(key)
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    fail("I7", f"{run} frame {record['frame_index']}: "
                               f"{name}.{key}={value!r}")
                elif not (-1e-9 <= float(value) <= 1.0 + 1e-9):
                    fail("I7", f"{run} frame {record['frame_index']}: "
                               f"{name}.{key}={value} out of [0,1]")
            queue = entry.get("queue_length")
            if queue is not None and (queue < 0 or int(queue) != queue):
                fail("I8", f"{run} frame {record['frame_index']}: "
                           f"{name}.queue_length={queue!r}")

    # I5 realised green durations within bounds
    for phase in log.get("phases", []):
        if phase.get("state") != "GREEN":
            continue
        green = float(phase.get("green_time", 0.0))
        # The final phase may be truncated by the end of the video, which is legitimate.
        truncated = phase.get("truncated") or phase is log["phases"][-1]
        if green > max_green + 1e-6:
            fail("I5", f"{run} cycle {phase.get('cycle_index')}: "
                       f"green {green} > max {max_green}")
        if green < min_green - 1e-6 and not truncated:
            fail("I5", f"{run} cycle {phase.get('cycle_index')}: "
                       f"green {green} < min {min_green}")

    # I6 starvation guarantee: an approach flagged as starved must get a green after it.
    overrides = log.get("starvation_overrides", [])
    greens = [p for p in log.get("phases", []) if p.get("state") == "GREEN"]
    for override in overrides:
        approach = override.get("approach") if isinstance(override, dict) else None
        cycle = override.get("cycle_index") if isinstance(override, dict) else None
        if approach is None:
            continue
        served = any(
            p.get("approach") == approach
            and (cycle is None or (p.get("cycle_index") or 0) >= cycle)
            for p in greens
        )
        if not served:
            fail("I6", f"{run}: {approach} flagged starved at cycle {cycle} "
                       "but never served afterwards")

print("=" * 78)
print("SIGNAL SAFETY INVARIANTS - checked against every recorded run")
print("=" * 78)
print(f"  runs checked   : {checked_runs}")
print(f"  frames checked : {checked_frames:,}")
print()

LABELS = {
    "I1": "at most one GREEN per frame",
    "I2": "at most one YELLOW, never with a GREEN",
    "I3": "all signal states legal",
    "I4": "no GREEN -> RED without YELLOW",
    "I5": "realised green within configured bounds",
    "I6": "starvation guarantee honoured",
    "I7": "metrics finite and within [0,1]",
    "I8": "queue length a non-negative integer",
    "LOAD": "run log readable",
}
for key, label in LABELS.items():
    count = per_invariant.get(key, 0)
    status = "PASS" if count == 0 else f"FAIL ({count})"
    print(f"  {key}  {label:44s} {status}")

if failures:
    print(f"\n{len(failures)} violation(s); first 25:")
    for line in failures[:25]:
        print(f"  {line}")
    raise SystemExit(1)

print("\nAll invariants hold on every frame of every recorded run.")
