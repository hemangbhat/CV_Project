# Technical Guide — how every part of the system works, and why

This is the in-depth companion to `report/FINAL_REPORT.md`. It walks the pipeline from
pixels to signal decision. For each stage it gives the formula, where it lives in the
code, why it was designed that way, and a worked example you can redo by hand. Line
numbers refer to the current branch; search for the function name if they drift.

Contents

1. [The problem in one paragraph](#1-the-problem-in-one-paragraph)
2. [Video input and the simulated clock](#2-video-input-and-the-simulated-clock)
3. [Detection: YOLOv8](#3-detection-yolov8)
4. [Tracking: ByteTrack and the track cache](#4-tracking-bytetrack-and-the-track-cache)
5. [Geometry: ROI, queue strip, queue axis](#5-geometry-roi-queue-strip-queue-axis)
6. [Assigning a vehicle to an approach](#6-assigning-a-vehicle-to-an-approach)
7. [Measurements D, Q, A](#7-measurements-d-q-a)
8. [Is a vehicle stopped? (the windowed test)](#8-is-a-vehicle-stopped-the-windowed-test)
9. [Spatial queue reach X](#9-spatial-queue-reach-x)
10. [Forecasts: count forecast F and spillback risk S](#10-forecasts-count-forecast-f-and-spillback-risk-s)
11. [The score](#11-the-score)
12. [Choosing the approach: the adaptive controller](#12-choosing-the-approach-the-adaptive-controller)
13. [Timing the green: bands vs actuated](#13-timing-the-green-bands-vs-actuated)
14. [The signal state machine](#14-the-signal-state-machine)
15. [Open-loop metrics, and why they cannot rank controllers](#15-open-loop-metrics-and-why-they-cannot-rank-controllers)
16. [The closed-loop simulation](#16-the-closed-loop-simulation)
17. [Statistics: how a difference is judged real](#17-statistics-how-a-difference-is-judged-real)
18. [Testing](#18-testing)
19. [Reproducing every number](#19-reproducing-every-number)

---

## 1. The problem in one paragraph

A signalised junction must decide, again and again, *which approach gets green next and
for how long*. A fixed-time plan ignores traffic. Raza et al. (2025) count vehicles with
YOLO, weight them by Passenger Car Equivalent (PCE), and give green in proportion to
density. That is *reactive*: it uses only the state right now. Li et al. (2025, T-ITS)
model the *queue profile* and over-saturation. Wei et al. (2025, T-ITS) *predict* queue
dynamics and spillback with MPC. This project asks a narrow question that a single
camera can address: **can a vision-measured, forward-looking measure of how far a queue
reaches back along the road (X, S) improve the Raza-style controller, compared with
counting vehicles in a stop-line region (Q) and forecasting that count (F)?**

## 2. Video input and the simulated clock

`src/video_io.py`. Frames are read in order; frame *n* happens at simulated time
`t = n / fps`. The Bellevue clips were re-encoded to a **constant 30 fps**: the original
hourly recordings drop frames, and their container rate (27.6 fps for one hour) is wrong.
This matters because every duration in the system (waiting time, green time, the 1 s
stopped window, the 2.5 s trend window) is counted in frames. A wrong frame rate would
silently rescale all of them. Details are in `data/annotations/videos.json`.

## 3. Detection: YOLOv8

`src/tracking.py` (ByteTrackTracker._track, ~line 618), `src/detection.py:144`.

* A pretrained COCO YOLOv8 is used without fine-tuning. Only the four vehicle classes are
  kept: COCO ids 2 = car, 3 = motorcycle, 5 = bus, 7 = truck. Confidence threshold 0.3.
* The final configuration uses **YOLOv8m** (`config/bellevue_116th_v2.json`). The
  evidence for that choice, on frame 600 of the busy clip (by eye: about 9 cars queued
  on North, 10 on East, 3 on South):

| detector | North | East | South | CPU s/frame |
|---|---|---|---|---|
| YOLOv8n @ 640 px (original) | 2 | 0 | 0 | 0.60 |
| YOLOv8m @ 640 px (final) | 4 | 3 | 3 | 0.77 |
| YOLOv8m @ 1280 px | 5 | 4 | 3 | 2.26 |

  The far end of an approach is exactly where queue reach lives, and distant cars are
  small. So detector recall at distance limits the spatial measurement directly. Even
  YOLOv8m misses about half of the most distant cars; this is stated as a limitation.

## 4. Tracking: ByteTrack and the track cache

**ByteTrack** (`src/tracking.py`, `ByteTrackTracker`) links detections across frames so
each vehicle keeps a **Track_ID**. Its key idea is to use low-confidence detections
(here down to 0.1) to *continue* existing tracks, so a vehicle that is briefly occluded
or blurred keeps its identity instead of starting a new one. `track_buffer = 30` frames
(1 s) is how long a lost track is kept before it is retired.

**Why tracking is essential here, not optional.** Every temporal measurement needs to know
that the box in this frame is the *same vehicle* as a box in earlier frames:
"is it stopped?" compares its position now with its position one second ago; waiting time
accumulates per vehicle; "served" means a specific vehicle left the queue strip. Detection
alone gives counts, and counts cannot do any of these.

**Track cache** (`src/track_cache.py`, `python -m src.main cache-tracks`). The tracker runs
once per clip and its output (id, box, class per frame) is saved. Every ablation arm then
replays it (`control --track-cache ...`). Two reasons:

1. **Fairness by construction.** All arms see byte-identical vision input, so a
   difference between arms cannot come from the detector or tracker.
2. **Speed.** A replay takes ~12 s instead of ~8 min. Replaying the cached busy clip under
   the original S4 configuration reproduced the logged run *exactly* (158 served, 2.14 s,
   1066 stops), which verifies the cache.

The cache stores the detector settings (model, confidence, buffer) and refuses to replay
under different settings.

## 5. Geometry: ROI, queue strip, queue axis

`config/bellevue_116th_v2.json`, drawn on `report/geometry_v2.png`.

Each approach has three hand-drawn objects, in image pixels:

| object | meaning | used for |
|---|---|---|
| **ROI polygon Ω** | the *inbound lanes* of that leg that the camera can see | which approach a vehicle belongs to; D |
| **Queue strip Θ** | a strip directly behind the stop line | Q ("queueing" = in Θ); anchors a queue |
| **Queue axis** | a polyline from the stop line back along the road to the far edge of Ω | X, the spatial queue reach |

**Why v2 exists (audit finding W7).** The original geometry had ROIs spanning two legs or
both travel directions. Its queue regions were inset copies of the ROIs, so nothing marked
a stop line. Its motion-calibrated axes for North and West pointed the wrong way, because
outbound and cross traffic dominated the votes. Measured reach was therefore "where in the
box some stopped car is", not a queue length. v2 fixes this by drawing the stop line and
the road direction explicitly. On this rotated fisheye view the labels are: North =
top-left leg, East = top-right, South = bottom-right, West = bottom-left.

**Why a polyline, not a straight line.** The camera is a fisheye, so roads curve in the
image. A point is projected onto the nearest polyline segment, and its position is the
*arc length* from the stop line (`ApproachAxis._polyline_fraction`,
`src/lane_analysis.py:292`), divided by the total length:

```
fraction(p) = (arc length from stop line to the projection of p) / (total polyline length)   in [0, 1]
```

0 = at the stop line, 1 = at the far visible end. Being a *fraction of the visible
storage*, it needs no camera calibration and no metres.

## 6. Assigning a vehicle to an approach

`ApproachAssigner.assign`, `src/lane_analysis.py:815`.

1. Reduce each box to its **bottom-centre point** `((x1+x2)/2, y2)`. This is roughly where
   the vehicle touches the road. A tall box whose roof overlaps a neighbouring polygon is
   still assigned by where it stands.
2. `cv2.pointPolygonTest(Ω, p) >= 0` decides membership (the boundary counts as inside).
3. If a point lies in two ROIs (a calibration fault), the nearest ROI centroid wins and the
   event is logged.
4. `is_queueing = p` lies in that approach's queue strip Θ.
5. The box height is carried along. The stopped test and the queue-tail gap need it.

## 7. Measurements D, Q, A

`MetricsEngine._measure`, `src/traffic_metrics.py:544`. For approach *i* on one frame:

```
D_i = clamp( Σ PCE(vehicles in Ω_i) / saturation_count_i )     PCE: car 1, motorcycle 0.5, bus 3, truck 3
Q_i = clamp( count(vehicles in Θ_i) / queue_capacity_i )
A_i = clamp( (count in Ω_i − count in Θ_i) / saturation_count_i )   (arriving, not yet queued)
clamp(v) = min(max(v, 0), 1)
```

Counts are sizes of *sets of Track_IDs*, so a duplicated box cannot be counted twice.

**Why Q saturates.** Θ is a fixed strip that physically holds about `queue_capacity`
vehicles (4 on North). Once the strip is full, adding cars to the back of the queue adds
them *behind* Θ, so the count inside Θ cannot grow: `Q = 1` and stays there. That is a
property of counting in a fixed region, not of the normaliser; raising `queue_capacity`
only changes where the ceiling sits. *Worked example:* North, capacity 4. Queues of 4, 8 and
15 cars all give 4 cars in Θ, so `Q = 1.0` for all three.

## 8. Is a vehicle stopped? (the windowed test)

`MetricsEngine._is_stopped_windowed`, `src/traffic_metrics.py:624`.

```
speed = | p(now) − p(1 s ago) | / 1 s / box_height        (in box heights per second)
stopped  ⇔  speed < 0.2                                    (needs ≥ 0.5 s of track history)
```

**Why not the original test?** The original test called a vehicle stopped if its point
moved < 2 px since the *previous frame*. That has two flaws:

* **Jitter.** Box edges wobble by a few pixels every frame, so a standing car flickered
  between stopped and moving. On the busy clip this produced 1066 "stops" from 249
  vehicles in 107 s (4.3 stops per vehicle, which is not physical).
* **Perspective.** A distant car is small and moves few pixels per frame even at speed.
  *Worked example:* a far car with a 20 px box moving 1.5 px/frame (45 px/s). Old test:
  1.5 < 2, so "stopped". New test: 45 / 20 = 2.25 box heights/s ≥ 0.2, so moving. A near
  car with a 40 px box that drifts 5 px in one second: 5 / 40 = 0.125 < 0.2, so stopped.

Dividing by box height makes the threshold "a fifth of the vehicle's own length per
second". For a 5 m car that is about 1 m/s, which matches the traffic-engineering notion
of stopped. Measured effect: stops per vehicle on the busy clip fell from 4.28 to 0.76.

## 9. Spatial queue reach X

`queue_tail_reach_scaled`, `src/traffic_metrics.py:95`; collected in `MetricsEngine.update`.

**Definition.** A queue is a chain of stopped vehicles that starts at the stop line. For
each stopped vehicle on approach *i*, take its axis fraction `f`, its gap allowance
`g = 2 × box_height / axis_length`, and whether it is in Θ. Sort by `f`, then walk upstream:

```
start the chain at the first stopped vehicle that is in Θ (or within its g of the line)
extend while  f_next − f_prev ≤ g_next
X_i = f of the last vehicle in the chain   (0 if there is no chain)
```

**Why contiguous, and why scaled by box height.**

* *Contiguous:* the original reach took the *furthest stopped vehicle anywhere*. One parked
  car, or one slow car misread as stopped, far up the road set X near 1 with no queue at
  all.
* *Scaled:* on a fisheye view one car near the camera spans about half the axis, and one
  far away a few percent. A fixed gap either breaks every near queue or bridges every far
  gap. "Two vehicle lengths" is the same physical rule everywhere in the image.

*Worked example:* stopped cars at f = 0.10 (in Θ), 0.30 and 0.48, then a lone stopped car
at 0.95, each with g = 0.25. The chain runs 0.10 → 0.30 (gap 0.20) → 0.48 (gap 0.18).
0.95 is 0.47 past 0.48, so the chain stops and **X = 0.48**. The legacy rule would have
said 0.95.

**Why X does not saturate like Q.** As cars join the back of a queue, the chain gets longer
and X increases, even after Θ is full and Q is pinned at 1. X *does* saturate at 1.0 when the
queue reaches the end of the visible road, because the camera cannot see further. Spatial
reach therefore extends the measurable range from about 4 vehicles (Θ) to the whole visible
approach. It does not remove saturation. That fact explains the closed-loop result (§16).

**Validation.** `report/queue_tail_validation.png` draws the measured tail (red bar) on real
frames: it sits at the back of the visible queue on South and West. On North and East it
falls short wherever distant queued cars are not detected (§3). Statistics are in
`report/measurement_compare.json` (`python audit/measurement_compare.py`). On the busy clip,
frames with a > 0.3 jump in X between consecutive frames fell from 7.3% (legacy) to ≤ 1.1%.

## 10. Forecasts: count forecast F and spillback risk S

`QueuePredictor`, `src/traffic_metrics.py:731`. Both forecasts use the same machinery: keep
the last N = 75 samples (2.5 s at 30 fps), fit a least-squares slope per second, and
project it forward.

```
slope = Σ (t_k − t̄)(y_k − ȳ) / Σ (t_k − t̄)²          t_k = k / fps
F_i = clamp( Q_i + slope(Q_i) × 3 s )      count forecast  (E8, from Wei's predictive idea)
S_i = clamp( X_i + slope(X_i) × 5 s )      spillback risk  (this project's enhancement)
```

**The saturation blind spot, proven from the formulas.** If Θ is full for the whole window,
every Q sample is 1.0. Then `y_k = ȳ`, the slope is 0, and `F = 1 + 0 = 1`: F says "steady"
however fast the real queue is growing behind Θ. F can only ever drop below 1 (a clearing
queue), never signal further growth. X keeps moving, so S keeps its information.

*Worked example*, with Q = 1 throughout:

| case | X over 2.5 s | slope(X) | S = X + slope × 5 |
|---|---|---|---|
| growing | 0.30 → 0.40 | +0.04 / s | 0.40 + 0.20 = **0.60** |
| steady | 0.40 → 0.40 | 0 | **0.40** |
| clearing | 0.40 → 0.30 | −0.04 / s | 0.30 − 0.20 = **0.10** |

F = 1.0 in all three rows; S separates them. This is executable in
`tests/test_tits_extensions.py::test_risk_works_where_the_count_based_projection_is_blind`.

**What S is and is not.** S → 1 means the queue is *projected to fill this approach's
visible storage*. It is **local** storage-exhaustion risk. The camera sees only inbound
legs, so it cannot see whether the road a vehicle drives *into* is full. That would be true
downstream spillback, which Wei et al. model with a network and which this project does
**not** claim.

**Why the window is 2.5 s, not 0.5 s.** The original window was 15 frames (0.5 s), with a
5 s horizon, so any noise in the slope was multiplied by 10. A 2.5 s window averages the
noise down before the projection.

## 11. The score

`compute_score_weighted`, `src/traffic_metrics.py:918`.

```
base_i  = α·D_i + (1 − α)·Q_i
Score_i = (1 − γ − δ − ω − ψ − ρ)·base_i + γ·A_i + δ·B_i + ω·F_i + ψ·X_i + ρ·S_i
```

All terms are in [0, 1] and the weights are non-negative and sum to at most 1, so the score
is a convex combination and stays in [0, 1]. Setting all extra weights to 0 gives the base
score exactly. The arms used in the final ablation are:

| arm | weights | meaning |
|---|---|---|
| S1 | α = 1 | Raza-style: density only |
| S2 | α = 0.5 | + queue |
| S3 | 0.7·base + 0.3·F | + count forecast |
| **S4** | 0.4·base + 0.3·F + 0.3·S | **proposed: + spillback risk** |
| NULL | as S4 but S forced to 0 | does S itself matter, or only the re-weighting? |
| S4X | 0.4·base + 0.3·F + 0.3·X | does *projecting* X matter? |
| S3S | 0.7·base + 0.3·S | spatial S *instead of* count F, at equal weight |
| S3X | 0.7·base + 0.3·X | spatial X instead of F, at equal weight |

*Worked example:* D = 0.5, Q = 1.0, F = 1.0, S = 0.8. Then base = 0.75,
S3 = 0.7·0.75 + 0.3·1.0 = 0.825, and S4 = 0.4·0.75 + 0.3·1.0 + 0.3·0.8 = 0.84.

**Why NULL is needed.** Going from S3 to S4 changes *two* things: S is added, *and* base drops
from 0.7 to 0.4. Any effect could come from either. NULL keeps the 0.4 weighting but feeds
S = 0. On the busy video clip NULL reproduced the original "+21%" S4 result exactly, so that
result came from the re-weighting, not from S (audit W2).

## 12. Choosing the approach: the adaptive controller

`AdaptiveController.select`, `src/signal_controller.py:408`.

1. **Starvation guard first** (`_starved_approach`, line 598). Each approach counts the
   cycles since it was last served. If any count reaches `starvation_limit = 3`, the
   longest-waiting approach is served regardless of score. This guarantees every approach
   is served within 4 cycles.
2. Otherwise **argmax of the score**. Ties go to the longer wait, then the fixed order
   N, E, S, W.
3. Counters update *after* the decision.

**The fixed-time baseline** (`FixedTimeController`) serves N → E → S → W with 30 s each and
reads no score.

## 13. Timing the green: bands vs actuated

Choosing *which* approach and *how long* are separate decisions. The project has two rules
for *how long*:

* **Bands (Raza-style).** `green_time_for`, line 505. The score picks the green length:
  below 0.3 gives 30 s, 0.3–0.6 gives 45 s, 0.6 and above gives 60 s.
  **Consequence:** any added positive term raises the score and *lengthens greens*, so a
  score term changes timing as well as ordering. In closed loop this confound dominates:
  under band timing, arms with smaller scores look "better" only because their greens are
  shorter.
* **Actuated (common to all arms).** `_apply_gap_out`, line 970. The green lasts at least
  10 s and at most 60 s, and ends once the queue strip has stayed *empty for a 2 s passage
  time*. The score then only chooses *which* approach goes next, so it is the clean test of
  the state measure.
  *Why the passage time:* without it the green ended on the first empty frame. During
  discharge, moving cars leave momentary gaps in the strip, so greens were cut short while
  a long queue was still flowing. In one validation scenario that gave a 75 ± 43 s mean
  delay. With the 2 s passage time it was about 25 s. Real actuated controllers use exactly
  this rule.

## 14. The signal state machine

`PhaseSequencer`, `src/signal_controller.py:630`. It stores one triple (active approach,
its state, the frame the phase ends) and *derives* all four lights from it. "Exactly one
approach is non-red" is therefore true by construction, not by checking. The only legal
edges are RED → GREEN, GREEN → YELLOW and YELLOW → RED, all routed through one function
(`_transition`, line 1047) that rejects anything else. Yellow is 3 s. Durations are whole
frames: `max(1, round(seconds × fps))`. A phase still running when the video ends is marked
*truncated* and excluded from per-phase averages.

## 15. Open-loop metrics, and why they cannot rank controllers

On recorded video the system reports waiting time, vehicles served and throughput
(`MetricsEngine._accumulate`, line 514):

```
waiting(v) += 1/fps   for every frame v is in Θ while its approach is not GREEN (simulated)
served     += 1       when v leaves Θ while its approach is GREEN (simulated)
```

The recorded cars move when the **real** light that was filmed turns green, not ours. So
"served" = (departures recorded in the footage) ∩ (our simulated green). The audit verified
this exactly for every busy-clip run:

```
S0 132 = N48 + E8 + S71 + W5      S1 189 = N122 + S67      S4 158 = N158 (East, South, West never green)
```

S4's "best throughput" came from keeping North green for the whole clip, which happened to
coincide with the real signal. **Queue length and stop counts are identical for every
controller** on the same clip, because the vehicles do not react. Recorded footage can
therefore show that the *measurements* are right and *what each controller decides*, but it
cannot show which controller reduces delay. That needs vehicles that respond to the signal
(§16).

## 16. The closed-loop simulation

`sim/`. Protocol frozen before test runs: `sim/PROTOCOL.md`.

* **Junction** (`sim/scenario.py`): four arms, 2 lanes, 150 m of inbound storage each, all
  straight-through, one approach served per phase. This is the same phase structure as the
  video controller. A queue that fills the 150 m blocks new arrivals, which wait upstream:
  that is local spillback, measured as *blocked-entry seconds* and included in delay.
* **Demand:** Poisson arrivals, 5% heavy vehicles, 9 scenarios from light to beyond
  capacity. One scenario, *calibrated*, uses the per-approach shares measured by the vision
  pipeline on the three clips (22% / 30% / 17% / 31%; `sim/calibrate_demand.py`).
* **Virtual camera** (`sim/sensor.py`): computes D, Q, A and X from simulated vehicles with
  the *same definitions*. The ROI is the 150 m approach, Θ is the last 20 m, "stopped" means
  below 0.2 vehicle lengths per second, and X is the contiguous queue tail. F and S come from
  the *same* `QueuePredictor` class. The controller is the *same* `AdaptiveController` and
  `PhaseSequencer`.
* **Factors:** 10 arms × 2 timing rules × 2 density normalisers (*physical* = jam capacity,
  never saturates; *saturating* = 10 vehicles, like the video configs) × 9 scenarios ×
  20 test seeds. A vision-noise sensor (up to 30% of far vehicles missed, 2 m jitter)
  repeats the actuated/saturating cell.
* **Metric:** mean delay per vehicle = SUMO time loss + insertion delay, so vehicles blocked
  by spillback count too. Also blocked-entry seconds, throughput and stops.

## 17. Statistics: how a difference is judged real

Every arm in a scenario runs the *same* 20 seeds, i.e. the same random arrivals (common
random numbers). For each seed we take the difference between two arms, then report the
mean difference ± a 95% t-interval (`sim/experiment.py`). A difference counts as real only
if the interval excludes 0. Pairing removes the large seed-to-seed variation in demand, so
small effects become detectable. That makes the "no effect" result for S a strong negative:
the intervals are typically ±0.1–0.5 s.

## 18. Testing

`python -m pytest -q` runs 790+ tests in about 30 s, with no GPU, weights or video needed
(detector and tracker are replaced by fakes). They include property-based tests
(Hypothesis) for the invariants: exactly one non-red approach, legal transitions only,
starvation bound, score in [0, 1], Q ≤ count. Tests specific to this project's measurements:

* `tests/test_robust_queue_measure.py`: windowed stopped test (jitter, distant mover),
  contiguous and scaled queue tail, drawn polyline axis, v2 geometry.
* `tests/test_tits_extensions.py`: forecast and risk behaviour, including the saturation
  blind spot.
* `tests/test_sim_sensor.py`: the virtual camera, plus one end-to-end SUMO run.

## 19. Reproducing every number

```bash
pip install -r requirements-dev.txt -r requirements-sim.txt
git lfs pull                                              # the videos

# audit evidence (reads the pre-audit logs in results/run_logs_legacy/)
python audit/summarise_logs.py "results/run_logs_legacy/bellevue_116th_busy*"
python audit/replay_ablation.py            # NULL control reproduces S4
python audit/signal_statistics.py          # Q saturation share, X noise

# measurement on real footage
python -m src.main cache-tracks --video videos/bellevue_116th_busy.mp4 --config config/bellevue_116th_v2.json --no-display
python audit/measurement_compare.py        # legacy vs robust vs v2
python audit/render_queue_tail.py --clip busy

# demo video
python -m src.main control --video videos/bellevue_116th_busy.mp4 --config config/final/S4.json \
       --track-cache results/track_cache/bellevue_116th_busy__yolov8m__c0p30.json.gz --overlay demo --no-display

# closed loop (about 2 h on 4 cores)
python -m sim.experiment run --seeds 100-119 --out results/sim/test_exact.jsonl
python -m sim.experiment table --results results/sim/test_exact.jsonl
python -m sim.figures
python -m sim.decision_analysis
```
