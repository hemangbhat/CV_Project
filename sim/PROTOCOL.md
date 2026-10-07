# Closed-loop experiment protocol (frozen before the test seeds were run)

Committed before `python -m sim.experiment run --seeds 100-119` so that no design
choice can have been tuned on the reported numbers. Design choices were made on
validation seeds 0-3 only (`results/sim/validation/`).

## Question
Does adding the vision-derived **spatial** queue measures (queue reach X, forward-looking
spillback risk S) to the Raza-style score improve signal control **when vehicles respond
to the signal**, compared with the count-based queue (Q) and the count-based forecast (F)?

## Fixed design
* Junction: 4 arms, 2 lanes, 150 m visible storage per approach, straight-through,
  one approach served per phase, 3 s yellow (`sim/scenario.py`).
* Demand: Poisson, 5% heavy vehicles. 9 scenarios: light, medium, heavy, oversat, unequal,
  unequal_oversat, surge, growing, calibrated (per-approach shares measured by the vision
  pipeline, `results/sim/video_demand.json`). 1800 s of demand, first 300 s excluded.
* Controller: the project's own code from `src/`, unchanged across arms except the Score weights.
* Sensor: virtual camera with the video definitions (`sim/sensor.py`); stopped < 0.2 vehicle
  lengths/s; contiguous queue tail with gap 0.25; trend window 2.5 s; F horizon 3 s; S horizon 5 s.

## Factors
| Factor | Levels |
|---|---|
| Arm | S0 fixed 30 s · A0 actuated round-robin · S1 Raza-style (α=1) · S2 +Q (α=0.5) · S3 +F (0.7B+0.3F) · **S4 proposed (0.4B+0.3F+0.3S)** · NULL (S4 with S≡0) · S4X (S4 with X for S) · S3S (0.7B+0.3S) · S3X (0.7B+0.3X) |
| Timing | `bands` (Raza-style 30/45/60 s from the Score) · `actuated` (Score selects only; 10-60 s, 2 s passage-time gap-out) |
| Density normaliser | `physical` (jam capacity, 40 veh) · `saturating` (10 veh, video-like) |
| Sensor | `exact` · `vision` (≤30% far misses, 2 m jitter) — vision only for actuated/saturating |
| Seeds | **100-119 (20 test seeds)**, common random numbers across arms |

## Analysis (pre-specified)
* Primary metric: mean delay per vehicle (time loss + insertion delay, so spillback counts).
* Secondary: blocked-entry seconds (local spillback), throughput, stops, p95 delay, worst-approach delay.
* Comparisons, paired over seeds, 95% t-interval: **S4 − S3**, **S4 − NULL**, **S3S − S3**, **S3X − S3**,
  and S1 − S0, A0 − S0 for context. An effect is reported as present only if the interval excludes 0.
* No arm, weight, horizon or scenario is changed after this file is committed. Any later
  exploration is reported separately and labelled as such.
