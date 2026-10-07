# Vision-Measured Spatial Queue Reach for Adaptive Traffic Signal Control: a Raza-Style Controller Extended with Forward-Looking Spillback Risk, Evaluated Open- and Closed-Loop

*Computer Vision course project — final report*

---

## Abstract

Raza et al. (2025) control a traffic signal from YOLO detections weighted into a
Passenger-Car-Equivalent (PCE) density. Their control is *reactive*: it reads only the
current state. Two 2025 IEEE T-ITS papers motivate looking further: Li et al. model the
queue profile and over-saturation, and Wei et al. predict queue dynamics and spillback.
Their solvers (MILP, MPC with a link-transmission model) are out of reach for a single
camera, so this project transfers the *idea* and leaves the machinery. We measure, from
YOLOv8 + ByteTrack tracks, how far the stopped queue physically reaches back along each
approach (**spatial queue reach X**), and project it forward (**local spillback risk S**).
The motivation is a blind spot that we prove from the formulas: a vehicle count inside a
fixed stop-line region (Q) saturates once the region is full, so a forecast of that count
(F) cannot signal further growth, whereas X keeps changing.

The measurement works. After three corrections found during this project — a windowed,
perspective-normalised stopped test; a contiguous queue tail; and recalibrated geometry
with drawn stop-line axes — the measured queue tail matches the visible queue where the
detector sees the vehicles. Stop counts fall from an unphysical 4.3 to 0.76 per vehicle,
and frame-to-frame jumps in X drop from 7% to ≤ 1% of frames.

The control benefit is **not** demonstrated. An audit showed that the earlier headline
(+21% throughput on a 107 s clip) came from re-weighting the score, not from S: a
weight-matched null control reproduced it exactly. It also showed that open-loop
"throughput" on recorded video measures overlap with the *real* recorded signal, so it
cannot rank controllers. In a pre-registered closed-loop SUMO experiment (10 controllers,
9 demand scenarios up to beyond capacity, 20 paired seeds, two timing rules, two density
normalisers, exact and vision-like sensors), adding X or S changes mean delay by no more
than about ±0.5 s in all but one scenario, with no significant improvement anywhere. Adding S
changed which approach got the green in only 1 of 40 inspected runs. The decisive factor
is the green-time rule: actuated timing roughly halves delay against fixed-time, and
cuts it nine-fold in unequal over-saturated demand. Raza-style score bands perform worse
than fixed-time in most scenarios. We explain the null result, and it is the main finding:
when X matters most, X saturates too, at the edge of the camera's view, and at decision
time it ranks the approaches the same way density already does.

---

## 1. Problem

A signalised junction repeatedly decides *which approach gets green next and for how
long*. Fixed-time plans ignore the traffic that is actually present. Vision-based
adaptive control replaces loop detectors with a camera: detect and count vehicles per
approach, turn the counts into a priority score, and serve the highest score. The open
question this project studies is **what state the camera should measure**. Is "how
many vehicles" enough, or does "how far back the queue reaches, and where it is heading"
make better decisions?

## 2. Base paper: Raza et al. (2025)

Raza et al., *IEEE Access* 13, 2025, doi:10.1109/ACCESS.2025.3602844. Their system uses
a lightweight YOLO detector on a Jetson Xavier NX edge node and converts detections into
a **PCE-weighted traffic density** (a bus counts as several cars). The controller serves
the approach with the highest PCE density. Its green time comes from **three discrete
bands** (40 / 60 / 120 s for low / moderate / high density), with a **Green-Denial
Counter** that prevents an approach from being skipped indefinitely. They report up to
33% less congestion and 23% lower waiting time than fixed-time control.

**What we reproduce.** Our S1 baseline is *Raza-style*, not a reproduction. It uses the
same rule (argmax of PCE density, three score bands, a starvation counter), but with
bands scaled to 30 / 45 / 60 s, our own geometry, stock YOLOv8 and our own camera. Raza's
detector, hardware and dataset are not reproduced.

**The limitation we study.** Selection and green time depend on *current* density only.
Density counts vehicles in view. It does not say whether the queue is growing, how far
back it reaches, or whether the approach is about to run out of storage.

## 3. Newer research: the two IEEE T-ITS papers

**Li, Lu & Wang (2025)**, *A Multi-Objective Model for Traffic Signal Coordination
Control With Queue Profile Estimation*, IEEE T-ITS 26(12), doi:10.1109/TITS.2025.3616119.
They estimate the **queue profile** from connected-vehicle trajectories and solve corridor
coordination as a MILP. Their criterion: a phase is *under-saturated* when its green
covers the time its queue needs to discharge, and the shortfall measures
**over-saturation**. Limitations that matter here:
* it is fixed-time and offline (adaptive control is stated as future work);
* it needs connected-vehicle trajectories and a commercial solver;
* it assumes uniform arrivals and a single vehicle class;
* its queue/stops identity breaks under over-saturation.

**Wei, Ampountolas, Hirrle & Wang (2025)**, *Hierarchical Predictive Control of Network
Traffic Signals Using Link Transmission Model With Queue Dynamics*, IEEE T-ITS 26(10),
doi:10.1109/TITS.2025.3568869. They use **MPC over a link-transmission model with explicit
queue dynamics**: predict how queues will evolve and allocate green *before* a queue
spills back. Limitations that matter here:
* the state estimation and prediction module is *assumed*, not built;
* multimodal traffic is ignored;
* robustness to prediction error is only lightly tested;
* it needs a QP/NLP solver at network scale, with coarse 10 s steps.

A detailed limitation table for both is in `docs/research/paper_limitations_analysis.md`.

**What transfers, and what does not.** The *ideas* transfer to a single camera: measure
the queue as a profile in space (Li), and act on where it is heading rather than where it
is (Wei). The *machinery* does not: corridor MILP, network MPC, connected-vehicle data.
Wei's own limitation (state estimation is assumed) is exactly what a camera can supply.

## 4. Our enhancement

### 4.1 The blind spot (proved from the implementation)

The natural first step is a queue count `Q = clamp(count in stop-line strip / capacity)`,
then a forecast `F = clamp(Q + dQ/dt · H)`. But a strip of fixed size holds a bounded
number of vehicles. Once it is full, more queueing vehicles stand *behind* it, `Q = 1`,
and its slope is 0, so `F = 1`, which reads as "steady" no matter how fast the queue
grows. F can signal clearing, never further growth.

### 4.2 Spatial queue reach X and spillback risk S

* **Queue axis.** For each approach, a polyline is drawn from the stop line back along the
  inbound lanes to the far edge of the view. A point's position is its arc length along
  the polyline divided by the total length, from 0 at the stop line to 1 at the far edge.
  No camera calibration is needed.
* **Stopped.** A tracked vehicle is stopped if it moved less than 0.2 of its own box
  height per second over the last second. Tracking (ByteTrack) is what makes this
  possible: it compares the *same* vehicle across frames.
* **X**, the queue tail: the position of the last vehicle in the contiguous chain of
  stopped vehicles that starts at the stop line. Consecutive members may be at most two vehicle lengths apart, a length being the box's
  extent along the local road direction.
* **S**, local spillback risk: `S = clamp(X + dX/dt · 5 s)`, with the slope fitted by least
  squares over 2.5 s. S rises above X for a growing queue, equals X for a steady one, and
  falls below X for a clearing one. It does this even while Q = 1.

S is **local storage-exhaustion risk**: the queue is projected to fill the visible approach.
It is not downstream spillback, which would need to see the road vehicles drive *into*.

### 4.3 The score

```
Score_i = (1 − ω − ρ − ψ)·(α·D_i + (1 − α)·Q_i) + ω·F_i + ψ·X_i + ρ·S_i
```

The proposed controller **S4** uses α = 0.5, ω = 0.3, ρ = 0.3. All terms are in [0, 1].

### 4.4 What is genuinely ours, and what is not

Not ours: YOLO-based signal control, PCE density, queue-aware control,
waiting-time-based optimisation, predictive queue control. These all exist in the
literature.

Ours:
* the image-space queue axis and the contiguous, perspective-scaled queue-tail
  measurement of X from tracked vehicles;
* its forward projection as local spillback risk S;
* the analysis showing that count forecasts are blind at saturation while X is not;
* the controlled evaluation of whether any of it helps.

We call this "our practical enhancement", not a globally novel method.

## 5. System

YOLOv8m (COCO weights; car, motorcycle, bus, truck; confidence 0.3) → ByteTrack (Track_IDs,
1 s buffer) → bottom-centre point-in-polygon assignment to four inbound approaches → D, Q,
X → QueuePredictor (F, S) → score → AdaptiveController (argmax with a starvation guard) →
PhaseSequencer (GREEN → 3 s YELLOW → RED) → overlay and JSON run log. Architecture:
`docs/architecture.md`. Every formula with worked examples: `docs/TECHNICAL_GUIDE.md`.
Data: three clips (107 s, 240 s, 240 s) from the City of Bellevue Traffic Video Dataset,
camera at 116th Ave NE / NE 12th St, re-encoded to a constant 30 fps.

## 6. Experiments and results

### 6.1 Audit of the earlier results (what did *not* hold up)

The project's earlier evaluation ran each controller over the recorded clips. It reported
that S4 raised throughput by 21% over S3 on the 107 s busy clip, at a waiting-time cost.
An audit (`AUDIT_REPORT.md`, scripts in `audit/`) found:

| # | finding | evidence |
|---|---|---|
| W1 | Open-loop "vehicles served" = recorded departures that fall inside our *simulated* green. The recorded cars move when the *real* light turns green, so this metric measures agreement with the real signal. | exact for every run: S0 132 = N48+E8+S71+W5; S4 158 = North only (North green for the whole clip) |
| W2 | The S3 → S4 gain is a re-weighting artefact. S4 drops the base weight 0.7 → 0.4 *and* adds S; with S forced to 0 (NULL) the result is identical. | replay of the real controller: NULL = 158 served, same as S4 |
| W3 | The count queue almost never saturated on this footage. | Q = 1 in 0.0–0.6% of frames, on every clip and approach |
| W4 | The gain rested on one decision, in a truncated last phase. On both 240 s clips S3 ≡ S4. | green sequences N30 N45 W45* vs N30 N45 N45* |
| W5 | X was dominated by noise: a per-frame 2 px stopped test, perspective bias, and a 0.5 s slope extrapolated 5 s. | 4.3 stops per vehicle; X jumped > 0.3 in up to 8% of frames |
| W7 | The geometry did not define queues: ROIs mixed legs and directions, there was no stop line, and two calibrated axes pointed the wrong way. | `report/geometry_v2.png` |

Those findings shaped the rest of the work: fix the measurement, then test the control
claim where vehicles respond to the signal.

### 6.2 Measurement on real footage (after the fixes)

`python audit/measurement_compare.py` → `report/measurement_compare.json`; busy clip:

| | legacy | robust test, old geometry | v2 geometry + robust test |
|---|---|---|---|
| stops per vehicle | 4.28 | 0.46 | 0.76 |
| North: X > 0 / X jump > 0.3 | 99% / 7.3% | 0.2% / 0.06% | 71% / 0.06% |
| South: X > 0 / X jump > 0.3 | 58% / 3.0% | 0% / 0% | 48% / 1.1% |
| West: X > 0 / X jump > 0.3 | 59% / 4.8% | 0.2% / 0% | 58% / 0.5% |

The middle column is instructive. Removing the noise on the *old* geometry made X almost
always zero. That is how finding W7 was discovered: the old geometry never defined a
queue in the first place. `report/queue_tail_validation.png` shows the measured tail on
real frames. It sits at the back of the visible queue on South and West. On the far North
and East legs it stops short wherever queued cars are not detected. Detector recall at
distance is therefore the binding limit: on a test frame YOLOv8m found 4 of about 9 queued
North cars and 3 of about 10 East cars, where YOLOv8n found 2 and 0.

### 6.3 Closed-loop evaluation (SUMO)

Protocol frozen before the test seeds were run: `sim/PROTOCOL.md`. The controller is this
project's own code. The simulated vehicles stop and go because of it, so delay,
queues and spillback depend on the controller. A virtual camera measures D, Q, X with the
same definitions as the video pipeline (§4.2). All results are means over 20 paired
seeds ± 95% CI. Delay = SUMO time loss + insertion delay, so vehicles blocked by a full
approach count too.

**Result 1 — the green-time rule dominates.** Mean delay per vehicle (s), physical normaliser:

| scenario | S0 fixed 30 s | S1 Raza-style, bands | S4 proposed, bands | A0 actuated round-robin | S1 actuated | S4 actuated |
|---|---|---|---|---|---|---|
| light | 43.5 ± 0.9 | 45.5 ± 1.0 | 76.9 ± 2.0 | 20.3 ± 0.3 | 20.3 ± 0.3 | 20.2 ± 0.3 |
| medium | 47.3 ± 0.6 | 77.5 ± 1.6 | 86.0 ± 1.1 | 23.1 ± 0.4 | 23.0 ± 0.4 | 23.0 ± 0.3 |
| heavy | 52.4 ± 0.8 | 92.2 ± 0.9 | 92.8 ± 1.0 | 36.3 ± 1.6 | 36.3 ± 0.9 | 36.5 ± 1.1 |
| calibrated (video shares) | 57.3 ± 2.9 | 90.7 ± 2.3 | 93.1 ± 2.6 | 31.8 ± 0.8 | 32.1 ± 0.9 | 31.9 ± 0.9 |
| unequal | 77.7 ± 8.9 | 73.3 ± 1.5 | 98.9 ± 5.2 | 27.0 ± 0.5 | 26.8 ± 0.7 | 27.0 ± 0.6 |
| surge | 68.1 ± 2.6 | 87.7 ± 2.9 | 100.1 ± 2.3 | 29.4 ± 0.9 | 27.8 ± 0.5 | 27.7 ± 0.4 |
| growing | 103.5 ± 5.1 | 100.9 ± 5.4 | 128.0 ± 4.8 | 28.2 ± 1.1 | 27.5 ± 0.7 | 27.3 ± 0.7 |
| unequal_oversat | 300.6 ± 16.5 | 147.4 ± 14.0 | 254.0 ± 12.9 | 40.0 ± 4.2 | 33.8 ± 0.7 | 33.7 ± 0.6 |
| oversat | 147.6 ± 10.7 | 148.2 ± 9.2 | 149.6 ± 9.6 | 140.0 ± 12.4 | 116.8 ± 9.4 | 120.1 ± 10.1 |

(`report/figures/fig_timing_vs_score.png`.) Three observations:

* **Raza-style band timing is worse than fixed-time in most scenarios.** Every green is at
  least 30 s, it never ends when the queue has cleared, and higher scores make greens even
  longer. It only beats fixed-time under unequal over-saturated demand.
* **Actuated timing, which ends the green once the queue strip has been empty for 2 s,
  halves delay or better** in every under-capacity scenario.
* **Choosing the approach by score beats round-robin only beyond capacity:** 140 → 117 s
  oversat, 40 → 34 s unequal-oversat.

**Result 2 — the score composition does not matter.** Paired differences in mean delay (s)
under actuated timing. These are the pre-registered comparisons, saturating normaliser
(the video-like one, where count terms saturate):

| scenario | S4 − S3 | S4 − NULL | S3S − S3 (S instead of F) | S3X − S3 (X instead of F) |
|---|---|---|---|---|
| light | +0.0 ± 0.0 | +0.0 ± 0.0 | +0.0 ± 0.1 | +0.1 ± 0.1 |
| medium | +0.0 ± 0.0 | +0.0 ± 0.1 | +0.1 ± 0.2 | +0.1 ± 0.2 |
| heavy | +0.0 ± 0.0 | −0.1 ± 0.1 | +0.0 ± 0.2 | +0.0 ± 0.2 |
| calibrated | +0.0 ± 0.0 | +0.0 ± 0.0 | −0.2 ± 0.2 | −0.2 ± 0.2 |
| unequal | −0.0 ± 0.0 | +0.0 ± 0.0 | −0.1 ± 0.1 | −0.1 ± 0.1 |
| surge | +0.0 ± 0.1 | −0.1 ± 0.3 | +0.1 ± 0.1 | +0.0 ± 0.1 |
| growing | +0.1 ± 0.2 | +0.1 ± 0.2 | +0.0 ± 0.2 | +0.0 ± 0.2 |
| unequal_oversat | −0.0 ± 0.0 | +0.0 ± 0.0 | +0.0 ± 0.3 | +0.0 ± 0.3 |
| oversat | +1.5 ± 3.1 | +0.5 ± 2.1 | −1.3 ± 2.4 | −1.6 ± 2.3 |

(`report/figures/fig_paired_actuated_saturating.png`; the physical normaliser gives the
same picture.) No interval excludes zero in an improving direction. The same holds for
spillback: in `oversat`, S0 4143 blocked-entry seconds, A0 3677, S1 3138, S4 3251 ± 317;
S does not reduce spillback.

With the **vision-like sensor** (up to 30% of far vehicles missed, 2 m jitter), the
conclusions are unchanged. One cell, S3S − S3 in `oversat` (−2.3 ± 2.2 s), excludes zero
by a hair. With about 36 comparisons per table, one or two such cells are expected by
chance at the 5% level, so we do not report it as an effect.

**Result 3 — why.** `python -m sim.decision_analysis` compares the green sequences of two
arms, decision by decision, on identical random traffic. Adding S (S3 → S4) changed which
approach got the green in **1 of 40** runs (heavy, oversat, unequal_oversat and growing, 5
seeds each, both normalisers). Replacing F by S (S2 vs S3S) changed it in 2 of 40. Two
mechanisms explain this:

1. **X saturates too.** It extends the measurable range from the ~4 vehicles of the stop-line
   strip to the whole visible approach, but when an approach's queue fills its storage,
   X = 1 and S = 1. In over-saturation every queued approach reaches that state together,
   so S ties exactly where it was meant to discriminate.
2. **At decision time, X ranks approaches the way density already does.** A decision is
   made when a green ends. The other approaches have all been red and are all building
   queues, and the longest queue (largest X) is also the most vehicles (largest D). A term
   that is monotone with the existing ranking cannot change an argmax.

**Result 4 — under band timing, the score composition changes *timing*, not *choice*.**
Under bands, NULL (61.9 s, light) and S3S (61.8 s) look much better than S4 (76.9 s). That
is not because they choose better: their scores are smaller, so they earn shorter greens.
This is the same artefact as W2, and it is why the actuated rule is the valid test of
the state measure.

### 6.4 Open-loop decision analysis on the footage

*(Filled in from the final v2 / YOLOv8m runs; see `results/final_video/`.)*

### 6.5 Earlier negative results (kept; detail in `docs/archive/`)

| # | extension | outcome |
|---|---|---|
| E1 | PCE-weighted queue | no effect: the footage is almost all cars |
| E2 | spillback (stopped beyond the strip) as a score term | no consistent effect |
| E3 | discharge-limited green (Wei Eq. 16, Li Eq. 7) | hurt at this short ROI scale |
| E4 | switching margin and green-rate limit | mixed |
| E5 | over-saturation measurement (Li Eqs. 7–10) | diagnostic only |
| E6 | gap-out/extension | mixed open-loop. In closed loop, with the 2 s passage-time fix, actuated timing is the largest improvement in the study |
| E7 | stop counting | identical across controllers on recorded video (open loop) |
| E8 | count forecast F | inert on the busy clip, active on the dev clip; no closed-loop effect |
| E9/S | spatial reach X and risk S | measurement works after the fixes; no closed-loop control effect |

## 7. Discussion

The research chain held up as a *question* and failed as a *claim*, and the reason is
informative. The saturation argument is correct: a count in a fixed region is blind to
growth beyond it, and a forecast of that count inherits the blindness. Spatial reach does
recover that information. But a camera's view is itself a fixed region, so X inherits the
same ceiling one level up. And in a one-approach-at-a-time junction the decision is a
ranking that density already gets right in these scenarios. The predictive idea of Wei et
al. pays off in their setting because the network model sees *downstream* storage. Local
storage risk measured on the inbound leg carries little information that inbound density
does not. What *does* matter is timing: serving a queue until it has cleared and no longer
(actuated), instead of fixed or score-banded durations. That points future work at
timing and at downstream visibility, not at more score terms.

## 8. Limitations

1. **Simulated junction, not a field test.** The closed loop is causal but synthetic: one
   junction, straight-through movements, Poisson demand. Only the calibrated scenario's
   per-approach shares come from the footage.
2. **One camera, one junction, three short clips** (587 s in total). The recorded footage is
   open-loop, so it supports measurement and decision analysis only.
3. **Detection recall at distance.** YOLOv8m misses about half of the most distant queued
   cars, so X is underestimated on the far legs. No fine-tuning was done.
4. **Hand-drawn geometry.** The v2 polygons and axes were drawn on two frames and validated
   visually. A different camera needs re-drawing.
5. **Local, not downstream, spillback.** Outbound roads are not in any ROI.
6. **Raza-style, not Raza.** Bands are scaled (30/45/60 s rather than 40/60/120 s), and
   detector and hardware differ.
7. **Fixed weights.** α = 0.5 and the 0.3 weights are a-priori and were not tuned. Given the
   decision analysis (S almost never changes a decision), tuning them is unlikely to change
   the conclusion.

## 9. Future work

* **Downstream visibility.** A second camera, or the receiving link's ROI, so that S
  measures true spillback, which is what Wei et al.'s results depend on.
* **Use X for timing, not ranking.** For example, hold a green while the served approach's
  X is still shrinking at saturation flow. This is where the measurement could actually
  enter the decision.
* **Better far-field detection.** Higher input resolution or tiling, and fine-tuning on
  this camera; then repeat the queue-tail validation with hand-labelled ground truth.
* **Field-calibrated simulation.** Turning movements and arrival rates per approach from
  longer footage; more than one junction.

## 10. Reproducibility

Every number above comes from a file in the repository and a command in
`docs/TECHNICAL_GUIDE.md` §19. The closed-loop protocol was committed before the test
seeds ran. Validation-seed explorations are kept in `results/sim/validation/` with the
design decision each one led to.

## References

1. A. Raza et al., "An Edge-Deployed Real-Time Adaptive Traffic Light Control System Using YOLO-Based Vehicle Detection and PCE-Aware Density Estimation," *IEEE Access*, vol. 13, 2025, doi:10.1109/ACCESS.2025.3602844.
2. Li, Lu, Wang, "A Multi-Objective Model for Traffic Signal Coordination Control With Queue Profile Estimation," *IEEE Trans. Intell. Transp. Syst.*, vol. 26, no. 12, pp. 23389–23406, 2025, doi:10.1109/TITS.2025.3616119.
3. Wei, Ampountolas, Hirrle, Wang, "Hierarchical Predictive Control of Network Traffic Signals Using Link Transmission Model With Queue Dynamics," *IEEE Trans. Intell. Transp. Syst.*, vol. 26, no. 10, pp. 16391–16404, 2025, doi:10.1109/TITS.2025.3568869.
4. Y. Zhang et al., "ByteTrack: Multi-Object Tracking by Associating Every Detection Box," *ECCV*, 2022.
5. G. Jocher et al., Ultralytics YOLOv8, 2023, https://github.com/ultralytics/ultralytics.
6. P. A. Lopez et al., "Microscopic Traffic Simulation using SUMO," *IEEE ITSC*, 2018.
7. City of Bellevue, Traffic Video Dataset, https://github.com/City-of-Bellevue/TrafficVideoDataset.
