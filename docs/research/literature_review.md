> **Status (Oct 2026):** the literature analysis below remains valid. Statements in it about this project's *measured results* are superseded by `report/FINAL_REPORT.md` (see `AUDIT_REPORT.md`).

# Literature Review and Positioning

> For the project report. **All result figures below are measured and final** — every
> value is derived from a run log under `results/run_logs/` and is tagged with the
> `run_id` it came from. No number here is hand-entered. Tables are reproducible with
> `python -m src.main evaluate --graphs-only`, which regenerates them from the logs
> without decoding any video.

## 1. Background

Adaptive traffic signal control (ATSC) aims to replace fixed-time signal cycles,
which ignore live demand, with timings that respond to the traffic actually
present at an intersection. Vision-based ATSC uses a camera and an object
detector to estimate per-approach demand, then feeds that estimate to a control
policy that allocates green time. Two broad families of control policy appear in
the recent literature: **learning-based** policies (reinforcement learning, often
with graph or attention models) and **rule-based** policies (interpretable
functions of measured traffic state). This project sits in the rule-based family
and builds directly on the density-based controller of Raza et al. (2025).

**Project direction.** Enhancing a YOLO/PCE-based adaptive traffic-signal system
(Raza 2025) with **vision-based queue dynamics and short-term queue prediction**, to
reduce queue build-up and spillback. The research trio is:

| Role | Paper |
|---|---|
| Base system | Raza et al. 2025 (IEEE Access) — YOLO + PCE density, reactive adaptive control |
| Queue / over-saturation foundation (supporting) | Li, Lu & Wang 2025 (IEEE TITS) — queue-profile estimation, over-saturation |
| **Predictive / spillback direction (primary)** | **Wei et al. 2025 (IEEE TITS) — predictive control with queue dynamics** |

Raza already solves the *reactive* adaptive-control problem: it reacts to the
current density. The identified limitation is that **reacting to the present is not
enough once a queue is growing or about to spill back**. Wei et al. answer that with
predictive, queue-dynamics control (their apparatus is network-scale MPC); this
project takes the *idea* and implements a single-junction, CV-only version —
YOLO + tracking to forecast queue growth, feeding the Raza-style controller so it
grants green *before* the queue spills back. Li et al. supply the queue-profile and
over-saturation foundation that the forecast builds on.

## 2. Reviewed work

### 2.1 Raza et al., 2025 — the base paper
*"An Edge-Deployed Real-Time Adaptive Traffic Light Control System Using
YOLO-Based Vehicle Detection and PCE-Aware Density Estimation,"* IEEE Access,
vol. 13, 2025. DOI: 10.1109/ACCESS.2025.3602844.

Raza et al. present a complete, hardware-deployed vision ATSC system for
undisciplined urban traffic. A lightweight YOLO detector runs on a Jetson Xavier
NX roadside edge node (mAP 90%, 74 FPS). Detections are converted to a traffic
density estimate that weights vehicle classes by **Passenger Car Equivalent
(PCE)** factors rather than counting all vehicles equally. An Adaptive Traffic
Light Control (ATLC) algorithm on a Portenta H7 board selects the approach with
the greatest PCE density and allocates green time in **three discrete bands
(40 s / 60 s / 120 s)** for low / moderate / high density, with a Green-Denial
Counter for **starvation prevention** and an explicit **tie-break** rule.
Reported gains over fixed-time control are up to 33% less congestion and 23%
lower waiting time. Stated limitations and future work include RSEN power
efficiency, static (non-continually-learning) detectors, annotation cost, and
sensitivity to degraded visual conditions.

**Key property for this project:** in Raza's controller, selection and green-time
allocation depend on **PCE density only**. Queue occupancy enters the system
solely as a binary starvation trigger, never as a graded term in the allocation
decision.

### 2.2 Jin, 2024 — multi-intersection reinforcement learning
*"Automatic Control of Traffic Lights at Multiple Intersections Based on
Artificial Intelligence and ABST Light,"* IEEE Access, vol. 12, 2024.
DOI: 10.1109/ACCESS.2024.3433016.

Jin models multi-intersection control as a Markov decision process solved with
Deep Q-Networks, enhanced by multi-head attention and graph convolution
(ABSTLight) to share information between neighbouring intersections. Results are
strong in simulation (throughput up to 195 veh/min, short queues and delays) but
the approach is heavyweight, requires training, and is validated in simulation
rather than on real footage. It represents the learning-based end of the field
and motivates why a lightweight, interpretable, deployable rule-based controller
remains attractive.

### 2.3 Wang et al., 2026 — lightweight detection and tracking (YOLO-LIGHT)
*"YOLO-LIGHT: A Real-Time Lightweight Model Based on Limited Edge Computing for
Traffic Scene Detection and Tracking,"* IEEE Trans. ITS, vol. 27, no. 6, 2026.
DOI: 10.1109/TITS.2026.3665257.

Wang et al. focus on the perception layer: a pruned, edge-optimised detector
(FastPConv, DySample, SCAM, AMA_SPPF, Soft-NMS) and a ByteTrack-derived tracker
(Ve-Track with an extended Kalman filter), reaching 51 FPS on Jetson Orin NX.
This work is orthogonal to control: it improves *what the controller sees*, not
*how it decides*. This project deliberately uses stock YOLOv8 + ByteTrack and
makes no perception-layer claim, treating detector/tracker quality as a
substitutable input.

### 2.4 Lamrabet et al., 2026 — emergency-aware supervisory control
*"Emergency-Aware Adaptive Traffic Signal Control Using YOLO-Based Light-Bar
Detection and SUMO System-Level Validation,"* IEEE Access, vol. 14, 2026.
DOI: 10.1109/ACCESS.2026.3710015.

Lamrabet et al. add a YOLO light-bar detector as a supervisory layer over a
GA–SGD-optimised controller, granting temporary priority to emergency vehicles,
and validate the closed loop in SUMO. Two ideas are relevant here: (i) a
supervisory override layered on a base controller, and (ii) **closed-loop
system-level validation in a microscopic simulator**, which is the standard way
to make a causal waiting-time claim. This project does neither and is explicit
that its evaluation is open-loop (see Section 4).

### 2.5 Saraff et al., 2025 — traffic video summarization
*"Indian Traffic Surveillance Video Summarization Using YOLO and Multi-Level
Masking,"* IEEE Access, vol. 13, 2025. DOI: 10.1109/ACCESS.2025.3616267.

Saraff et al. address a different task — summarising long surveillance footage by
object-centric frame selection and masking — but share the detection-plus-video
pipeline and the observation that missed/occluded small objects motivate
temporal tracking. It is included as context for the vision pipeline, not as a
control baseline.

## 3. Comparison

| Work | Task | Detector | Tracker | Control policy | Demand signal | Validation | Deployment |
|---|---|---|---|---|---|---|---|
| Raza 2025 (base) | ATSC | tuned lightweight YOLO | — | rule-based, density-only, 3-band green | **PCE density** | real + partial sim | Jetson + Portenta (edge/IoT) |
| Jin 2024 | multi-int. ATSC | — | — | DQN + MHA + GCN (RL) | learned state | simulation | — |
| Wang 2026 | detection/tracking | YOLO-LIGHT (custom) | Ve-Track | — | — | benchmark datasets | Jetson Orin NX |
| Lamrabet 2026 | emergency ATSC | YOLO light-bar | — | GA–SGD + priority override | vehicle + siren cue | **SUMO closed-loop** | pre-deployment |
| Saraff 2025 | summarization | YOLO11 (custom-trained) | — | — | — | custom video | web app |
| **This project** | ATSC (control layer) | stock YOLOv8n | stock ByteTrack | **rule-based, PCE density + queue, α-weighted** | **PCE density + queue length** | real footage, open-loop | offline (CPU) |

## 4. Gap addressed and contribution of this work

Across the reviewed rule-based systems, the base paper (Raza 2025) drives green
allocation from **PCE density alone**, treating queue occupancy only as a binary
starvation guard. This project's contribution is a bounded, measurable
**enhancement to the control layer**:

1. **Queue-augmented scoring.** Each approach is scored as
   `Score = α · PCE_density + (1 − α) · normalized_queue`, promoting queue length
   from a starvation flag to a graded, co-equal term in both approach selection
   and green-time allocation. Setting `α = 1` recovers a density-only,
   Raza-style baseline on the identical pipeline, so the queue term's effect is
   isolated rather than confounded with detector or footage differences.

2. **α as a reported design parameter.** An α-sweep (`α ∈ {1.0, 0.5, 0.0}`)
   quantifies the marginal effect of queue-awareness — density-only, balanced,
   and queue-only — against fixed-time control, on the same real footage.

3. **Reproducible, open-loop-honest evaluation.** Fixed-time and adaptive runs
   use one shared configuration (ROIs, queue regions, PCE weights, detector,
   tracker); only the controller and α differ. Every reported number is derived
   from a run log and tagged with its `run_id`.

**What this work does not claim.** It does not improve Raza's detector (stock
YOLOv8n is used), does not perform edge/IoT deployment, and does not contribute a
dataset — these are Raza's contributions and are orthogonal to the control-layer
enhancement studied here. Because the footage is recorded, vehicles cannot react
to the simulated signal; the evaluation therefore measures how each controller
**allocates green to the observed traffic state**, not a causal reduction in
real-world waiting time. Establishing the latter would require closed-loop
microscopic simulation (as in Lamrabet 2026) or field deployment (as in
Raza 2025), both out of scope.

## 5. Measured results

All figures are aggregates over the four approaches, derived from the run logs
(`results/run_logs/`) and tagged with their `run_id` in `report/results_summary.md`.
Footage: Bellevue 116th Ave NE / NE 12th St, 1280×720, 30 fps.

### 5.1 Adaptive vs fixed-time

| Clip | Controller | Avg waiting (s) | Throughput (veh/min) | Vehicles served |
|---|---|---|---|---|
| development (4:00) | fixed-time | 1.49 | 39.5 | 158 |
| development | adaptive (α=1.0) | 1.02 | 49.8 | 199 |
| final (4:00) | fixed-time | 1.10 | 34.3 | 137 |
| final | adaptive (α=1.0) | 0.96 | 56.3 | 225 |

The adaptive controller reduces aggregate waiting time by ~32% (development) and
~12% (final) and raises throughput and vehicles served on both clips. This
reproduces the well-established adaptive-over-fixed-time result (as in Raza 2025)
and confirms the pipeline behaves correctly; it is **not** itself a contribution
over Raza.

### 5.2 Open-loop measurement note

Because the footage is recorded, the controller cannot change what vehicles do:
**average and maximum queue length are identical across every controller** for a
given clip (a property of the video, not the policy). Only signal-gated
quantities — waiting time, vehicles served, throughput — differ between
controllers. Reported comparisons therefore concern how each controller
**allocates green to the observed traffic state**, not a causal change in
real-world queueing (see Section 4).

### 5.3 The queue-augmentation (the contribution over Raza)

On the development and final clips, the queue-augmented controller (α=0.5, 0.0)
was **indistinguishable from the density-only baseline (α=1.0)** — identical
selection and timing. Diagnosis: on this low-density footage the densest approach
is almost always also the most-queued one, so `density` and `normalized_queue`
rank the approaches identically and α cannot change the decision. In addition,
scores rarely exceeded the lowest green-time band, so timing was saturated at the
minimum.

To test the queue term where it can actually act, a **higher-demand clip** was cut
from the same camera (noon hour, ~8.4 vehicles/frame vs ~4.7–5.0 on the other
clips; 3220 frames, 107 s, constant 30 fps). On this clip the densest approach and
the most-queued approach **diverge in 28.0% of frames (901 / 3220)**, confirming
that density and queue are decoupled under congestion and that α is decision-
relevant here.

Comparison on the busy clip (density-only vs queue-aware), aggregate over approaches:

| Controller | Avg waiting (s) | Throughput (veh/min) | Vehicles served | run_id |
|---|---|---|---|---|
| fixed-time | 2.36 | 73.8 | 132 | `bellevue_116th_busy__fixed__alpha0p50__20260819-101026` |
| adaptive α=1.0 (density-only, Raza-style) | 2.25 | 105.7 | 189 | `bellevue_116th_busy__adaptive__alpha1p00__20260819-094453` |
| adaptive α=0.5 (balanced) | 1.69 | 73.2 | 131 | `bellevue_116th_busy__adaptive__alpha0p50__20260819-133003` |
| adaptive α=0.0 (queue-only) | **1.53** | 71.6 | 128 | `bellevue_116th_busy__adaptive__alpha0p00__20260819-095543` |
| adaptive α=0.5 + predictive (γ=0.3) | 1.53 | 71.6 | 128 | `bellevue_116th_busy__adaptive__alpha0p50__20260821-082800` |

The α sweep is monotone in the expected direction: as weight shifts from density
(α=1.0) toward queue (α=0.0), aggregate waiting falls (2.25 → 1.69 → 1.53 s) while
throughput and vehicles served fall too (105.7 → 73.2 → 71.6 veh/min) — the
fairness-vs-throughput trade-off, quantified.

Per-approach waiting time (the revealing view):

| Approach | α=1.0 wait (s) / served | α=0.0 wait (s) / served |
|---|---|---|
| North | 0.167 / 122 | 0.305 / 102 |
| East  | 0.315 / 0   | 0.315 / 0   |
| South | 0.280 / 67  | 0.399 / 0   |
| West  | **1.502 / 0** | **0.421 / 26** |

**Interpretation (measured).** Under density-only control (α=1.0, the Raza-style
policy) the West approach waits 1.50 s and is served zero times in the window,
because West carries a **standing queue of stopped vehicles but low instantaneous
density** — so a density-ranked selector never picks it. Introducing the queue
term (α=0.0) serves West and cuts its waiting to 0.42 s, lowering aggregate
waiting time by ~32% (2.25 → 1.53 s). The cost is throughput and total vehicles
served (189 → 128): queue-priority spends green clearing the stopped approach
rather than feeding the highest-flow one. The queue term therefore does not
dominate density control; it **shifts the operating point toward fairness / lower
waiting at the expense of throughput**, and it specifically addresses a failure
mode of density-only selection — starvation of a low-density, high-queue approach.

**Caveats, stated honestly.**
- The busy clip is short (107 s ≈ 3 signal cycles). The controller's own
  starvation-prevention (`starvation_limit = 3` cycles) had little opportunity to
  trigger, so part of West's density-only starvation would be mitigated over a
  longer horizon. The waiting-time differences hold within the observed window.
- Measurement is open-loop (Section 4): queue and density observations are fixed
  by the recording; only signal-gated metrics differ.
- The α=0.5 (balanced) busy run did not complete — it repeatedly stalled because
  the host sleeps during idle background execution. It should be run on an
  always-on machine to fill the midpoint; the fixed / density-only / queue-only
  rows above already establish the effect and its trade-off.

## 6. Predictive / arrival-aware extension (implemented)

Raza and the base controller here both decide on the *instantaneous* stopped and
present state. As a second, optional enhancement this System adds an
**anticipatory arrival term**: the normalized count of vehicles present in an
approach's ROI but not yet queueing — i.e. moving toward the stop line — is
blended into the Score at a weight `gamma`:

```
Score = (1 - gamma) * (alpha * density + (1 - alpha) * queue) + gamma * arrival
```

It is off by default (`use_predictive = false`), so base behaviour and every base
test are unchanged; it is enabled in `config/bellevue_116th_predictive.json`
(`alpha = 0.5`, `gamma = 0.3`) and covered by `tests/test_predictive.py`.

**Measured result (busy clip).** The predictive controller reached 1.53 s average
waiting / 71.6 veh/min / 128 served
(`bellevue_116th_busy__adaptive__alpha0p50__20260821-082800`) — **identical to the
queue-only controller** and better than the balanced (1.69 s) and density-only
(2.25 s) controllers. Honest reading: on this short, single-junction clip the
anticipatory term does not open a new operating point; "vehicles approaching West"
and "vehicles queued at West" identify the same approach, so anticipation collapses
onto queue-priority. The term is implemented, tested, and evaluated, but on this
footage it matches rather than beats the queue term. Demonstrating a distinct
predictive benefit would need footage where arrivals and standing queues point to
*different* approaches (e.g. a longer horizon with platooned arrivals), which this
junction and clip length do not provide.

## 7. Extensions derived from the two IEEE TITS papers

Two 2025 IEEE TITS papers were selected for limitation analysis. Both are
network-scale, optimisation-based, simulation-only controllers, and both argue the
same core point: **queue length must be estimated as a variable, not fixed or
inferred as an afterthought.** That is the axis this project's controller was
weakest on, so it is the axis the transferred mechanisms address. The full
limitation catalogue and the applied/out-of-scope decisions are in
`report/paper_limitations_analysis.md`; this section records what was built and
what it measured. Every mechanism is configuration-gated and off by default, so
Sections 5 and 6 remain valid.

### 7.1 Wei et al. 2025 (IEEE TITS) — PRIMARY: predictive control with queue dynamics

*"Hierarchical Predictive Control of Network Traffic Signals Using Link
Transmission Model With Queue Dynamics,"* IEEE Trans. ITS, vol. 26, no. 10,
pp. 16391–16404, Oct. 2025. DOI: 10.1109/TITS.2025.3568869.

**This is the primary enhancement direction.** Wei et al.'s central point is that
reacting to the *current* state is insufficient — a controller must anticipate
queue build-up and spillback. Their machinery (hierarchical MPC, a link
transmission model, a QP/NLP solver) is network-scale and does not transfer to one
camera at one junction. What transfers is the *idea*: **predict where the queue is
heading and act before it spills back.** This project implements that as **E8**, a
vision-only short-term queue forecast (§7.3), which is the headline contribution of
this extension work. The mechanisms below (E1–E4) are the earlier, instantaneous
transfers from the same paper; E8 is the predictive one it was really pointing at.

Wei et al. propose MPC-Q, a two-layer model predictive controller for *networks* of
signals built on a Link Transmission Model extended with turn-level queue
transmission. A quadratic program at the network layer maximises throughput while
penalising the cycle-to-cycle variation of the green fractions; a nonlinear program
at each intersection tracks that reference while splitting green between turning
movements in proportion to their queue lengths. In oversaturated regimes it reports
at least 29% fewer blocked vehicles than a standard-LTM MPC and than max-pressure,
and it eliminates spillback the benchmark cannot.

The paper is complementary to this project in a specific and useful way. Section
II-A states that the controller receives inflow, outflow, queue inflow and demand
predictions **from a state estimation and prediction module** — and never builds
one. Its first stated limitation is that it **ignores vehicle-type differences**,
which is precisely what Raza's PCE weighting corrects. So Wei et al. supply control
mechanisms with no perception; Raza et al. and this project supply perception with
comparatively naive control.

Four mechanisms were transferred from it, reduced to a single junction, and driven
from the existing vision measurement. Each is configuration-gated and off by
default, so every result in Sections 5 and 6 above remains valid. Full derivation,
the limitation each one answers, and what was deliberately left out are in
**`report/paper_limitations_analysis.md`**; measured tables are in
`report/results_summary.md`.

| | Mechanism | Wei et al. source | Measured effect on the busy clip |
|---|---|---|---|
| E1 | PCE-weighted Queue_Length | limitation B-S1 (multimodal traffic ignored) | **none** — identical run; the Bellevue fleet is nearly all cars |
| E2 | Spillback pressure term | the critique that accumulation ≠ queue, and that a constant jam density cannot represent a varying one | waiting **1.69 → 1.53 s** (−9.3%), throughput 73.2 → 71.6 |
| E3 | Discharge-limited Green_Time | Eq. 16, `min(N_out + q_s·b·Δt, N_out_max)` | **harmful** — throughput 73.2 → 45.8 (−37%); see below |
| E4 | Control-plan stability | Eqs. 19–20, the `Δb` variation penalty | waiting **1.69 → 1.60 s** *and* throughput **73.2 → 75.5** |

**E4 is the clean win.** It is the only change that improved waiting *and*
throughput simultaneously, and it improved the standing-queue West approach from
0.831 s / 9 served to 0.675 s / 16 served
(`bellevue_116th_busy__adaptive__alpha0p50__20260822-055232`). The green-time rate
limit also produced a 40 s phase, a duration no Score band offers.

**E3 does not transfer at this spatial scale, and that is the finding.** Wei et
al.'s discharge constraint acts on link-level queues of tens of vehicles. Applied
to a camera ROI holding 3–4 vehicles, `queue / 0.5 PCE·s⁻¹` falls below any sane
minimum green on essentially every cycle, so the model degenerated into a fixed
10 s green: 9 cycles instead of 3, ~23% of the run spent on intergreen, and 37%
fewer vehicles served. Transferring it properly needs metric queue length from a
calibrated camera, which this project does not measure.

**The four together are worse than E2 or E4 alone (2.13 s), and the run logs show
why.** With E1+E2+E4 the third cycle goes to North rather than West, so West is
never served and its waiting rises to 1.502 s with 0 served — the same failure mode
this project documented for density-only control at α=1.0 in Section 5.3. North
carries the largest spillback reading (0.80 of 1.0), so E2 raises *North's* score
rather than West's, and E4's 0.05 switching margin then requires West to beat that
inflated score. Either mechanism alone leaves cycle 3 to West; both together flip
it. At this junction **spillback correlates with density rather than with standing
queue**, so it works against the queue-fairness term instead of reinforcing it.

**Caveats.** The clip is 107 s ≈ 3 cycles for the band-based configurations, so one
cycle's difference moves the aggregate substantially; these results indicate
direction, not effect size. The weights (`spillback_weight = 0.25`,
`switching_margin = 0.05`) were chosen a priori and not tuned, so this is an
untuned operating point. Measurement remains open-loop (Section 4).

The `E3` discharge-limited green above was later corrected using **Li et al. Eq. 7**
(§7.2) to charge the changeover as lost time, which the first version omitted; that
correction is reflected in the E3 rows of §7.2's runs.

### 7.2 Li, Lu & Wang 2025 (IEEE TITS) — SUPPORTING: queue-profile and over-saturation foundation

*"A Multi-Objective Model for Traffic Signal Coordination Control With Queue
Profile Estimation,"* IEEE Trans. ITS, vol. 26, no. 12, pp. 23389–23406, Dec. 2025.
DOI: 10.1109/TITS.2025.3616119.

Li et al. present an arterial coordination model that treats **queue length as a
decision variable** and minimises two objectives, the level of phase
**over-saturation** and the number of **stops**, by estimating each phase's queue
profile (accumulation plus dissipation). A phase is under-saturated exactly when
its split is at least the time the queue needs to discharge, `u = ε/W + ε/V_f +
T_loss` (their Eq. 7); the shortfall `η = u − φ` is the level of over-saturation.
The MINLP is linearised to a MILP with piecewise McCormick envelopes and solved in
SUMO across fifteen demand scenarios, beating Yang's model and MP-BAND — most
consistently on **average stops**, and uniquely able to relieve over-saturation by
re-allocating splits.

Almost all of the paper is corridor-scale (offsets, phase sequences, green bands,
progression) and solver-bound, none of which transfers to one camera at one
junction. What does transfer is its *queue reasoning*, and it transfers cheaply,
because measuring whether a green cleared its queue needs no prediction and no
solver — only the queue this project already estimates. Three mechanisms plus one
correction were taken from it, and a deliberate design choice: Li et al.'s fourth
criticism of prior work is the **burden of untunable hyperparameters**, so these
mechanisms decide by *measured comparison*, adding no new weight.

| | Mechanism | Li et al. source | Measured effect on the busy clip |
|---|---|---|---|
| E5 | Over-saturation measurement | Eqs. 7–10, `η = discharge_time − green` | **diagnostic works** — the base controller ends **3/3** green phases with queue left, worst 6.0 s short |
| E6 | Queue-clearance gap-out / extension | the under/over-saturation test `φ ≥ u`, applied online | cut over-saturated phases **3/3 → 1/6**, but throughput 73.2 → 50.9 (more intergreen) |
| E7 | Stop counting | average stops, their headline MOE | now reportable; **1066 stops on every controller** — stops are controller-invariant on recorded footage |
| E3 fix | Lost time in discharge green | `+ T_loss` in Eq. 7 | discharge+gap-out reaches 50.9 vs the original E3's 45.8 veh/min |

**E5 gives the System a question it could not previously answer:** did this green
clear its queue, and if not by how much did it fall short. On the busy clip the base
adaptive controller ends **all three** of its greens with the queue region still
occupied, the worst 6.0 s short of clearing
(`bellevue_116th_busy__adaptive__alpha0p50__20260822-103419`). It changes no control
— it is measurement — but it is the single most useful thing taken from either paper,
because it turns "the controller seems to work" into a checkable per-phase fact.

**E7 makes Li et al.'s headline metric reportable, and immediately exposes the
open-loop limit on it.** All three runs record the identical **1066 stops (4.28 per
vehicle)**. Because the recorded vehicles cannot react to the signal, the count of
moving-to-stopped transitions is a property of the video, exactly as average and
maximum queue length are (Section 5.2). Stops can now be reported, but on recorded
footage they cannot distinguish controllers; a causal stops comparison — the one
Li et al. make — would need closed-loop simulation.

**E6 does what Li et al.'s test is designed to do, at a throughput cost here.**
Ending each green once its queue clears cut over-saturated green phases from 3/3 to
1/6, leaving far fewer queues unserved. But six greens instead of three doubles the
intergreen, so throughput fell 73.2 → 50.9 veh/min. This is the same scale mismatch
that defeated E3: the queues at this junction clear in a few seconds, so ending
greens early trades useful green for changeover overhead. On a corridor with the
larger queues Li et al. model, the trade would favour gap-out; on a 107 s
single-junction clip it does not. Reported as a directional finding, not a win.

**Why E5/E6/E7 are preferred to E2/E3/E4 as a design.** They decide by measured
comparison — has the queue cleared, how far short did the green fall — rather than
by a tuned weight. That is Li et al.'s own remedy for the hyperparameter burden, and
it is why the E2/E4 antagonism in §7.1 (two untuned weights interacting badly)
cannot arise for E5/E6: there is no weight to mis-set.

### 7.3 E8 — short-term queue forecast (the primary enhancement)

This is the vision-only realisation of Wei et al.'s predictive direction, and the
headline of the extension work. The controllers above all act on the queue as it
stands *now*; E8 acts on where the queue is *heading*.

**Mechanism.** A `QueuePredictor` keeps a sliding window (default 15 frames ≈ 0.5 s
at 30 fps) of each approach's measured `normalized_queue`, fits its rate of change
by least squares, and projects it `forecast_horizon_seconds` (default 3 s) ahead:

```
forecast = clamp(current_queue + slope_per_second * horizon, 0, 1)
```

A rising queue forecasts higher than it stands, a clearing one at or below — so the
predictor never inflates an emptying approach. The forecast enters the score as a
fourth convex term at weight `omega` (`forecast_weight`), alongside density, queue,
arrival and spillback, and it pre-sizes the discharge-limited green (E3) by the
larger of the current and forecast queue. The intent is Wei et al.'s exactly:
**grant green before the queue spills back, not after.**

No MPC, no link-transmission model, no solver — a trend line over the queue series
this System already measures from YOLO + ByteTrack. It is off by default
(`use_forecast = false`), enabled in `config/bellevue_116th_forecast.json`, and
covered by the E8 tests in `tests/test_tits_extensions.py`.

**Distinction from the existing arrival term (γ).** The pre-existing predictive
extension (§6) weights vehicles *present but not yet queueing* — an instantaneous
count of who is in the ROI now. E8 is a genuine *forecast over time*: the trend of
the queue itself, projected forward. The two are independent convex terms (γ and ω)
and can be combined.

**Measured result (busy clip).** Full table and `run_id`s in
`report/results_summary.md`. The forecast is genuinely anticipatory: it reached the
maximum forecast of 1.0 and, in **~19% of frame-approach cells, projected a queue
higher than the one currently measured** — so in nearly a fifth of the run it acted
on growth that had not yet arrived. Placed in the score (weight 0.4) on the band
controller it shifted the operating point **toward throughput**: throughput rose
73.2 → 88.3 veh/min and vehicles served 131 → 158, and over-saturated green phases
fell 3/3 → 2/3, at the cost of higher waiting (1.69 → 2.14 s)
(`bellevue_116th_busy__adaptive__alpha0p50__20260822-164349`). On the full
discharge + gap-out stack the intergreen overhead of E3/E6 dominates and throughput
does not recover. And the open-loop property (§5.2, §7.2) still bounds everything —
stops and queue length are fixed by the recording, so E8 moves only the
signal-gated metrics. A forecast that opens a *distinct* operating point — a
build-up visible on one approach while another is momentarily denser — needs footage
longer and more variable than this 107 s clip, where forecast and current queue
mostly point to the same approach. The mechanism is implemented, tested, measured,
and demonstrably anticipatory; its headline effect here is a throughput/waiting
re-balancing rather than a free win.

## 8. Further work

**Closed-loop validation.** The open-loop limitation (Section 4) can only be
removed by driving the controller inside a microscopic simulator (SUMO, as in
Lamrabet 2026) calibrated to the observed arrival pattern, or by field deployment
(as in Raza 2025). Either would allow a causal waiting-time claim rather than the
green-allocation comparison reported here.

**Longer and mixed-fleet footage.** Several findings in Section 7 are limited by the
available footage rather than by the mechanisms: E1 needs a mixed vehicle fleet to
bind at all, E3 and E6 need queues long enough that a discharge time exceeds a
minimum green (otherwise ending greens early only adds intergreen), and the E2/E4
interaction needs more than three cycles to be estimated rather than merely observed.

**Closed-loop is required for the stops metric (E7).** On recorded footage stops are
controller-invariant (§7.2), so the metric Li et al. lead on cannot separate
controllers here. Only driving the controller inside a microscopic simulator would
let vehicles react and make a causal stops comparison possible.

**Weight tuning and interaction.** `spillback_weight` and `switching_margin` were
set a priori. The measured antagonism between them is the clearest candidate for a
follow-up sweep, since each is beneficial alone.

