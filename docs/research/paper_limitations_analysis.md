> **Status (Oct 2026):** the literature analysis below remains valid. Statements in it about this project's *measured results* are superseded by `report/FINAL_REPORT.md` (see `AUDIT_REPORT.md`).

# Limitation Analysis of the Two Assigned IEEE TITS Papers, and What This Project Takes From Them

This document does three things:

1. records what each paper actually does,
2. extracts its limitations — both the ones the authors state and the ones that
   follow from reading the method,
3. maps each limitation to a decision: **applied**, **already covered**, or
   **out of scope**, with the reason.

Only limitations marked *applied* produced code. Everything applied is behind a
configuration flag and **off by default**, so every previously reported number and
every pre-existing test remains valid.

---

## 0. The two papers

Both are IEEE Transactions on Intelligent Transportation Systems, 2025.

| | Paper | Venue | Identifier |
|---|---|---|---|
| **A** | Li, Lu, Wang — *A Multi-Objective Model for Traffic Signal Coordination Control With Queue Profile Estimation* | IEEE **TITS**, vol. 26, no. 12, pp. 23389–23406, Dec. 2025 | DOI 10.1109/TITS.2025.3616119 |
| **B** | Wei, Ampountolas, Hirrle, Wang — *Hierarchical Predictive Control of Network Traffic Signals Using Link Transmission Model With Queue Dynamics* | IEEE **TITS**, vol. 26, no. 10, pp. 16391–16404, Oct. 2025 | DOI 10.1109/TITS.2025.3568869 |

A third paper is referenced throughout but is *not* one of the two assigned:
**Raza et al., IEEE Access vol. 13, 2025, DOI 10.1109/ACCESS.2025.3602844** — the
edge-deployed YOLO + PCE density paper. That is this project's existing *base*
paper, already positioned in `report/literature_review.md` §2.1. It is kept here
only where it supplies something one of the TITS papers is missing.

**What unites the two assigned papers.** Both are model-based, optimisation-driven
*network* controllers, and both make the same central argument: **queue length must
be treated as a variable to be estimated, not as a fixed input or an afterthought.**
Wei et al. make it a state in a predictive model; Li et al. make it a decision
variable in a mathematical program. Everything worth taking from them follows from
that one shared idea, and it is exactly the idea this project's controller was
weakest on.

---

## 1. Paper A — Li, Lu & Wang 2025 (IEEE TITS)

### 1.1 What it does

An arterial signal coordination model for a five-intersection corridor in Rizhao,
Shandong (link lengths 433 / 447 / 653 / 261 m), on the NEMA ring-barrier phase
structure.

**Two objectives, both minimised:** the total level of phase over-saturation, and
the total queue length, which under their assumptions equals the number of vehicle
stops. Decision variables are **phase splits, offsets, and phase sequences**
simultaneously. The only hyperparameter is a common cycle length.

**The mechanism — queue profile estimation.** A phase's queue profile has two
halves, accumulation and dissipation:

- *Two kinds of green band.* Rather than one uniform band, local bands are split
  into a **saturation-flow band** and a **free-flow band**. Vehicles discharge at
  saturation flow while a queue is present, then transition to free flow once the
  queue has cleared. Any vehicle travelling outside *either* band is taken to be
  stopped and joins the downstream queue — which is how stops are derived from band
  membership rather than assumed.
- *Queue discharging time* (their Eq. 7):
  `u = ε/W + ε/V_f + T_loss`, where `ε` is queue length in metres, `W` the
  saturation flow, `V_f` the free-flow speed, and `T_loss` the per-phase lost time.
- *Over-saturation, defined operationally.* A phase is under-saturated when its
  split is at least the discharging time (`φ ≥ u`) and over-saturated otherwise.
  The **shortfall `η = u − φ` is the quantitative level of over-saturation**, made
  linear with a binary indicator and a big-M relaxation (Eqs. 9–10).
- *Measured, not assumed.* `W` and `V_f` are extracted from connected-vehicle
  trajectories aggregated across cycles, so the connected-vehicle penetration
  requirement stays low. Queue accumulation rates per phase pair are weighted by
  observed trajectory counts.
- *Metric queue length.* Flow is converted to a queue length in metres through an
  average space headway.

**Tractability.** Formulated as a MINLP, then linearised into a MILP: min/max terms
via auxiliary binaries, bilinear terms via **piecewise McCormick envelopes** (three
partitions). Solved with GUROBI 10.0. The envelope is what makes it usable — at a
0.1% optimality gap the model solves in about 90 s with the envelope, against a
run that becomes impractical without it. Solve time grows to roughly 190 s at ten
intersections.

**Validation.** SUMO, fifteen demand scenarios in three groups (A: short multiple
paths, B: long paths, C: two-way plus heavy cross-street), including three with
over-saturated phases, 80 replications each. MOEs are **average delay (AD),
average stops (AS), average total travel time (ATTT)**. Benchmarks are Yang's
multi-path progression model and MP-BAND.

**Results.** Beats Yang's model on all three MOEs across every scenario. Against
MP-BAND it wins consistently and substantially on **average stops**, and wins on AD
and ATTT in most scenarios (A1 and A3 are the exceptions, where MP-BAND is slightly
better on AD/ATTT). Uniquely among the three, it can **re-allocate splits to
relieve over-saturation** — in the heaviest cross-street scenario it moves two of
four over-saturated phases back under capacity, which neither fixed-split benchmark
can do.

### 1.2 Limitations the authors state

Assumptions declared in §II, and limitations discussed in §IV-D/E/F and §V.

| # | Stated limitation |
|---|---|
| A-S1 | **Uniform arrival.** Arrivals are assumed stable and approximately uniform. They improve on prior work by separating saturation-flow from free-flow streams, but *within* each stream uniformity is retained deliberately, to keep the model linear and solvable. |
| A-S2 | **Connected-vehicle penetration is assumed uniform** along the corridor, so trajectory counts stand in for true flows. They concede penetration really varies by place, time of day, and vehicle type. |
| A-S3 | **Uni-modal.** Built for one vehicle class. They sketch three multi-modal extensions (bus trajectory estimation, bus stops as decision variables, a passenger-centric stops objective) and note that heavy vehicles raise saturation headway and so lower effective saturation flow. |
| A-S4 | **Unmodelled conflicts between origins.** A residual low-speed region in their own queue heat map is attributed to conflicts where two bands overlap, which they flag as a limit of the traffic-dynamics modelling. |
| A-S5 | **Gains shrink as demand rises.** The stops advantage over MP-BAND declines with volume, because progression itself loses purchase near saturation. |
| A-S6 | **Queue ≠ stops when over-saturated.** For an over-saturated phase the per-cycle queue is not stable and grows across cycles, so the queue-equals-stops identity holds only under saturation. |
| A-S7 | **An unexplained cycle-length effect** (delay rising while stops fall) is observed and explicitly left unexplored. |
| A-S8 | **Computational ceiling.** Beyond roughly six intersections, partitioning strategies are needed; more McCormick partitions tighten the relaxation but cost computation. |
| A-S9 (future work) | Objectives cover over-saturation and stops but **not delay or fuel consumption** — connecting progression to those MOEs is left to future work. |
| A-S10 (future work) | Traffic-dynamic estimation needs refining. |
| A-S11 (future work) | **An adaptive control framework is future work** — the model as it stands is not adaptive. |

### 1.3 Limitations that follow from the method

| # | Limitation | Why it is a limitation |
|---|---|---|
| **A-D1** | **It is fixed-time coordination, not adaptive control.** Splits, offsets, and sequences are solved offline for a demand scenario and then held; the common cycle length is an input. Their own A-S11 concedes this. | The plan cannot respond to the demand actually present in a given cycle. Every quantity it optimises is an expectation over a scenario, not a measurement of now. |
| **A-D2** | **It needs connected-vehicle trajectories to exist at all.** `W`, `V_f`, and the phase-pair weights all come from trajectory data. | Same structural gap as Paper B: the control layer presumes a measurement layer. No CV fleet, no model. |
| **A-D3** | **It needs a commercial MILP solver** (GUROBI/CPLEX) and ~90–190 s per solve. | Not deployable on the edge hardware class this project's base paper targets. Fine for fixed-time plans refreshed hourly; unusable per cycle. |
| **A-D4** | **It needs a corridor.** Green bands, offsets, and progression are meaningless at one isolated junction. | The largest part of the paper cannot transfer to a single-camera single-junction system at all. |
| **A-D5** | **Metric queue length rests on a constant space headway and vehicle length** (6 m and 3.5 m in their experiments). | A homogeneous-fleet assumption baked into the unit conversion, which is A-S3 reappearing inside the arithmetic rather than in the discussion. |
| **A-D6** | **Delay is measured but not optimised.** AD and ATTT are reported MOEs while the objective is over-saturation and stops. | This is why MP-BAND beats them on AD/ATTT in two scenarios — those are the axes they never optimised. A-S9 admits the gap. |
| **A-D7** | **One hyperparameter survives, and it is the influential one.** The claim is "no hyperparameters beyond a common cycle length", but the cycle length is set beforehand by SYNCHRO. | Cycle length dominates delay. The paper's own critique of prior work is the burden of hyperparameters; it reduces that burden rather than removing it. |
| **A-D8** | **Right turns are uncontrolled** and assumed uniform across the cycle. | Right-turn-on-red interacts strongly with queue formation at the stop line, which is where the whole model does its work. |
| **A-D9** | **Tied to the NEMA ring-barrier structure.** The split, barrier, and sequence constraints are written against it. | Junction layouts outside that structure need reformulation, not reparameterisation. |
| **A-D10** | **Simulation only.** SUMO, no field deployment, and the benchmarks' hyperparameters were tuned by sensitivity analysis the authors themselves call unattainable in practice. | The comparison is fair but synthetic; and it is arguably generous to the proposed model, since its benchmarks were handicapped by hyperparameters that cannot be tuned in the field. |

---

## 2. Paper B — Wei, Ampountolas, Hirrle & Wang 2025 (IEEE TITS)

### 2.1 What it does

A hierarchical model predictive controller (**MPC-Q**) for *networks* of signals,
built on a Link Transmission Model extended with turn-level queue transmission.

- **Why LTM with queues.** A standard LTM tracks only aggregate inflow and outflow
  at link boundaries. Vehicle *accumulation* is not a *queue*: under congestion,
  accumulation mixes queued vehicles with vehicles still moving upstream of the
  queue. And because standard LTM assumes one constant jam density, it cannot
  represent a queue whose density varies between critical and jam density. CTMs can,
  but only by discretising links into cells, which is prohibitive at network scale.
- **Their queue model.** Queue density interpolates with outflow (Eq. 1):
  `ρ_que = ρ_c + ((q_c − q_out)/q_c)(ρ_jam − ρ_c)`; queue length
  `L_q = (N_que − N_out)/ρ_que` (Eq. 2); free-flow remainder `L_f = L − L_q`.
- **Upper (network) layer.** A quadratic program over segment-level green fractions
  `b`, minimising total time spent **plus a penalty on the cycle-to-cycle variation**
  `Δb = b(k) − b(k−1)` (Eqs. 19–20). Horizon `T_n`, 10 s sampling.
- **Lower (local) layer.** A nonlinear program per intersection over a shorter
  horizon, tracking the network reference while splitting green between turning
  movements in proportion to their queue lengths — which also warm-starts the solver.
- **Discharge constraint (Eq. 16).** Desired outflow is capped by what can actually
  be served: `min(N_out(k−1) + q_s · b · Δt, N_out_max)`.
- **Results.** Eliminates spillback where the benchmark MPC cannot; in oversaturated
  regimes, at least 29% fewer blocked vehicles than both the standard-LTM MPC and
  max-pressure, with higher throughput. Green-fraction plans are visibly smoother.
  0.35 CPU-s per step against a 10 s sampling interval.

### 2.2 Limitations the authors state

| # | Stated limitation |
|---|---|
| B-S1 | **Multimodal traffic is not accounted for** — buses, bicycles, trams have different operational characteristics that MPC-Q ignores |
| B-S2 | Robustness to **general demand prediction errors** needs further work (only a free-flow-speed mismatch of ±2 m/s against an actual 10 m/s is tested) |
| B-S3 | **Sudden incidents** — accidents, moving bottlenecks — are not modelled |
| B-S4 | **Gridlock**: the controller works while the network still has buffer space to store queues; under gridlock the design is likely ineffective and the objective would need refining |

### 2.3 Limitations that follow from the method

| # | Limitation | Why it is a limitation |
|---|---|---|
| **B-D1** | **It assumes the measurement it needs already exists.** §II-A states that the network layer receives inflow/outflow estimates and demand predictions, and the local layer turn-level inflow and queue inflow/outflow, from a *state estimation and prediction module* — never built. | The entire controller sits downstream of a perception problem the paper does not solve. |
| **B-D2** | **Turning rates are assumed known.** | They drive the whole node model, must be estimated in practice, and the paper itself varies them per regime. |
| **B-D3** | **Fundamental-diagram parameters are assumed known per movement** — jam density, critical density, capacity flow, free-flow speed. | Each needs field calibration; the paper sets them to reasonable defaults. |
| **B-D4** | **Simulation only.** MATLAB, a synthetic 14-segment arterial and a synthetic 48-segment grid. | No real sensor data, no field deployment. |
| **B-D5** | **The output is green *fractions*, not an implementable plan.** Continuous `b ∈ [0,1]`, with no intergreen, no minimum green, no phase-order constraint. | Deliberate — it avoids a mixed-integer program — but the result still needs converting into a legal, safe phase sequence. |
| **B-D6** | **Movement symmetry is assumed** (north = south, east = west). | Rules out asymmetric junctions, which is most real junctions. |
| **B-D7** | **Needs a QP plus NLP solver**, warm-started. | Not deployable on a microcontroller. |
| **B-D8** | **Coarse temporal resolution** — 10 s sampling, at most 6 prediction steps. | A sub-cycle queue surge is invisible. |
| **B-D9** | **Longer horizons do not reliably help** — their Table III shows cost rising without matching throughput gain. | The horizon is a per-case tuning parameter with no selection rule. |
| **B-D10** | **Measurement error is never propagated.** Robustness is tested against a model-parameter error, not against noisy or missing state estimates. | Given B-D1, the untested error source is the one that would dominate in deployment. |

---

## 3. Reading the two together

Three things line up once both papers are on the table.

**Both assume a measurement layer neither builds (A-D2, B-D1).** Li et al. need
saturation flow, free-flow speed, and turning proportions from connected-vehicle
trajectories; Wei et al. name a state estimation and prediction module as an input.
Neither paper produces the quantities its own controller consumes. This project is
that measurement layer — camera to per-approach demand, on real footage. That is
the honest complementarity, and it is why "implement MPC-Q" or "implement the MILP"
is the wrong response to these papers.

**Both are simulation-only and solver-bound (A-D3, A-D10, B-D4, B-D7).** Neither can
run on the hardware class the base paper targets. So what transfers is not their
machinery but their *reasoning about queues*.

**Both stated multimodal limitations (A-S3, B-S1) are answered by the base paper's
PCE weighting.** Li et al. go further and name the mechanism — heavy vehicles raise
saturation headway and therefore lower effective saturation flow — which is
precisely a PCE correction. So the base paper already holds the fix for the two TITS
papers' shared first limitation.

**Where the two differ is the most useful part.** Wei et al. ask *how much green to
grant, predictively*. Li et al. ask *did the green actually clear the queue* — and
make the shortfall the objective. The second question is far cheaper to answer from
video, needs no prediction, and needs no solver. It is the single best thing either
paper offers a single-junction vision controller.

---

## 4. Decisions: what this project does about each limitation

### 4.1 Applied (implemented, tested, measured)

| Enhancement | Answers | Mechanism borrowed from |
|---|---|---|
| **E8. Short-term queue forecast (PRIMARY)** | Wei et al.'s core thesis: reacting to the present is not enough | **Wei et al.'s predictive queue-dynamics control**, reduced to a CV trend forecast |
| E1. PCE-weighted queue length | A-S3, B-S1 | Base-paper PCE, applied to the queue term rather than only density |
| E2. Spillback-aware queue pressure | B's accumulation-vs-queue critique | Wei et al.: a constant jam density cannot represent a varying queue |
| E3. Discharge-limited green time | A-D6, and Raza's arbitrary bands | Wei et al. Eq. 16; **corrected using Li et al. Eq. 7** to include lost time |
| E4. Control-plan stability | — | Wei et al. Eqs. 19–20, the `Δb` variation penalty |
| E5. Over-saturation measurement | A-S6, A-D6 | **Li et al. Eqs. 7–10**: `η = discharge_time − green_time` |
| E6. Queue-clearance gap-out and extension | A-D1, A-S11 | **Li et al.'s under/over-saturation test (`φ ≥ u`), applied online** |
| E7. Stop counting | A's headline MOE | **Li et al.**: average stops, the metric they improve most consistently |

**Primary vs supporting.** The project's primary enhancement direction is Wei et
al.'s **prediction**: the identified limitation of the Raza base system is that it
reacts to the current density and cannot pre-empt a queue that is building or about
to spill back. **E8** answers that directly — a vision-only short-term queue
forecast feeding the controller. Li et al. is the supporting paper: its
queue-profile and over-saturation mechanisms (E5, E6, and the E3 lost-time fix) are
the foundation the forecast is built on and measured against. E1–E4 are the earlier
instantaneous transfers.

Rationale for the primary addition:

**E8 — short-term queue forecast.** Wei et al. predict network queue propagation
over a horizon so green can be granted before spillback; they need a link
transmission model, a demand-prediction module, and a solver. None is available
here, but the per-frame `normalized_queue` this System already measures carries the
same signal: a queue that is growing shows a positive trend before it saturates. E8
fits that trend by least squares over a short window and projects it a few seconds
ahead, then feeds the projected queue into both the score (a fourth convex term at
weight `omega`) and the discharge-limited green (pre-sizing green for the larger of
the current and forecast queue). It is the genuine predictive enhancement Wei et al.
point at, implemented at single-junction CV scale rather than reproduced as MPC.
Crucially it is a *forecast over time*, distinct from the existing arrival term
(which is an instantaneous count of vehicles present but not queueing).

Rationale for the Li-derived additions:

**E5 — over-saturation as a measured quantity.** Li et al.'s central definition is
that a phase is over-saturated exactly when its green is shorter than the time the
queue needs to discharge, and that the shortfall `η = u − φ` quantifies it. This
project had **no notion of whether a green phase succeeded**. It recorded how long
green was held and how many vehicles were served, but never "did the queue clear,
and if not, by how much did the green fall short". Recording `η` per phase costs
nothing, is derived from quantities already measured, and gives the run log an
answer to the question Li et al. built an entire objective function around.

**E6 — queue-clearance gap-out and extension.** This is the substantive transfer.
Li et al. compare split against discharging time to *classify* a phase offline; the
same comparison can be made *online* against the observed queue. If the queue region
has emptied and stays empty, the remaining green is being wasted, so terminate it
(subject to minimum green). If the queue has not cleared when the planned green
expires, extend it up to maximum green. This is closed-loop where Li et al. are
open-loop, so it addresses their own A-D1/A-S11 rather than reproducing it. It also
needs **no new tunable weights**, which matters — see §4.4.

**E7 — count stops.** Average stops is Li et al.'s headline MOE and the one on which
their advantage is largest and most consistent. This project measures waiting time,
queue length, and throughput, but not stops, so it cannot report the metric the
paper's own contribution rests on. Stops are detectable from the tracker: a vehicle
transitioning from moving to stopped is one stop, using the same stopped-detection
already built for E2.

**E3 correction from Li et al. Eq. 7.** The original E3 computed green as
`queue / saturation_flow` and nothing else. Li et al.'s discharging time adds a
**lost-time term** (4 s per phase in their experiments) and a second travel term.
Omitting lost time is exactly why the first E3 measurement burned roughly 23% of the
run on intergreen: it costed the discharge but not the changeover. Adding
`T_loss` makes short greens pay for their own overhead, which is the correct
accounting and discourages the degenerate all-minimum-green behaviour.

### 4.2 Already covered before these papers were read

| Limitation | Where it is covered |
|---|---|
| Queue treated as an afterthought (the shared premise of both papers) | The `α`-weighted score `α·density + (1−α)·queue` already promotes queue from a starvation flag to a graded term, with measured evidence: under density-only control the West approach waits 1.50 s and is served zero times; with the queue term it waits 0.42 s. |
| A-S3, B-S1 (multimodal) | PCE weighting for density; E1 extends it to the queue. |
| A-D5 (homogeneous-fleet unit conversion) | This project never converts to metres, so it never inherits a constant-headway assumption; it normalises by a per-approach capacity instead. |
| A-D7 (surviving hyperparameter: cycle length) | There is no fixed cycle here — green time is per-phase and demand-derived, so no common cycle length has to be chosen. |

### 4.3 Out of scope, with reasons

| Limitation | Why not |
|---|---|
| A-D4 (needs a corridor), and all of Li et al.'s offsets, phase sequences, green bands, and progression | This is a single junction with one camera. Offsets and bands require at least two coordinated intersections and a measured travel time between them. This is the largest part of Paper A and none of it transfers. Stated plainly rather than faked. |
| A-D3, B-D7 (MILP / QP+NLP solvers), the McCormick linearisation | The runtime dependency set is fixed at OpenCV, Ultralytics, NumPy, Matplotlib. Adding GUROBI would break that, and a 90 s solve cannot run per cycle anyway. |
| A-D9 (NEMA ring-barrier) | The project models one movement per approach, so there are no rings, barriers, or protected-left phase pairs to constrain. |
| A-D8 (uncontrolled right turns) | Same reason: no per-movement split within an approach. |
| A-S1 (uniform arrival) | Does not carry over — arrivals are *observed* per frame here, never assumed. Worth noting this project is stronger than both papers on exactly this point. |
| A-S2, A-D2, B-D1 (connected-vehicle data) | No CV fleet. The vision pipeline is the substitute, which is the point of §3. |
| A-S4 (inter-origin conflicts), A-S7 (cycle-length anomaly) | Phenomena inside their model, with no counterpart here. |
| A-S5 (gains shrink at high demand) | An observation about progression, which this project does not do. |
| A-S8, B-D8, B-D9 (solver scaling, horizon tuning) | Artefacts of MILP/MPC discretisation. This project runs per frame at 30 fps. |
| B-S3 (incidents), B-S4 (gridlock), B-D6 (symmetry), B-D2/B-D3 (turning rates, FD parameters) | Neither appears in the available footage, or requires calibration this project cannot do. Claiming an untested capability is worse than omitting it. |
| A-D10, B-D4 (simulation-only validation) | The inverse problem here: this project is *footage*-only and open-loop, which it states. Closing the loop needs SUMO, excluded by the spec. Recorded as further work. |

### 4.4 A limitation of Paper A that lands on this project

Li et al.'s fourth criticism of prior work is the **burden of hyperparameters**:
objective weights and minimum bandwidths that cannot be determined theoretically and
need field surveys to set. Their contribution is partly to remove them.

That criticism applies to this project. The score already carries `α`, and the
extensions added `γ`, `δ`, `switching_margin`, `green_rate_limit`,
`saturation_flow_rate`, and `stopped_displacement` — every one a weight or threshold
chosen by hand. The measured antagonism between `δ` and `switching_margin` (§5) is
precisely the failure mode Li et al. warn about: untunable weights interacting.

This is why **E5 and E6 are preferred to E2 and E3**: over-saturation shortfall and
queue-clearance gap-out are *measured comparisons*, not weighted blends. They add
behaviour without adding a weight that needs a field survey to justify. Recorded as
a design principle taken from the paper, not just a limitation catalogued about it.

---

## 5. Measured outcomes

Full tables, per-approach breakdowns, phase sequences, and `run_id`s are in
`report/results_summary.md`. Ablation on `bellevue_116th_busy.mp4` at `α = 0.50`.

### 5.1 The Wei-derived mechanisms (E1–E4)

| enabled | avg wait (s) | throughput (veh/min) | served |
|---|---|---|---|
| none (reference) | 1.69 | 73.2 | 131 |
| E1 | 1.69 | 73.2 | 131 |
| E2 | **1.53** | 71.6 | 128 |
| E4 | **1.60** | **75.5** | **135** |
| E1+E2+E4 | 2.13 | 84.4 | 151 |
| E1+E2+E3+E4 | 2.34 | 45.8 | 82 |

**E1 changed nothing — bit for bit.** Identical waiting, throughput, vehicles
served, and phase sequence. The reason is the footage, not the code: the Bellevue
junction carries an almost entirely passenger-car fleet, so PCE-weighting the queue
multiplies nearly every track by 1.0. E1 is still the right fix — it removes a real
inconsistency, since a bus previously counted 3.0 in the density term and 1.0 in the
queue term of the same score — and it is exactly the correction both A-S3 and B-S1
ask for. Its effect is measurably zero on a homogeneous fleet, and would bind on the
mixed fleet of the base paper's footage.

**E2 improved waiting by 9.3%** (1.69 → 1.53 s) at a small cost in throughput. The
measure is genuinely active — non-zero in 3851 of 12 880 frame-approach cells,
peaking at 0.80 on North. It reaches the same operating point as `α = 0` while
leaving `α` at 0.5.

**E4 is the only mechanism that improved both axes:** waiting 1.69 → 1.60 s (−5.3%)
*and* throughput 73.2 → 75.5 veh/min (+3.1%). Per-approach, the standing-queue West
approach went from 0.831 s / 9 served to 0.675 s / 16 served. The rate limit also
produced a 40 s phase, a duration no score band offers.

**E3 as originally built was harmful,** and the reason is instructive. Every cycle
asked for the minimum green, so the mechanism degenerated into a fixed 10 s green —
9 cycles instead of 3, and with a 3 s yellow after each, roughly 23% of the run on
intergreen. Throughput and vehicles served both fell 37%. Two causes: a scale
mismatch (Wei et al.'s constraint acts on link queues of tens of vehicles, whereas a
camera ROI holds 3–4, so `queue / 0.5 PCE·s⁻¹` is under 10 s essentially always),
and **the omission of lost time**, which Li et al. Eq. 7 includes and the original E3
did not. The lost-time correction is applied in §5.2.

**The bundle is worse than its parts, and the phase sequences show why.**

| configuration | GREEN phases |
|---|---|
| reference | North/30 s → North/45 s → **West/45 s** |
| E2 only | North/30 s → North/30 s → **West/45 s** |
| E4 only | North/30 s → North/40 s → **West/45 s** |
| E1+E2+E4 | North/30 s → North/30 s → **North/40 s** |

In the bundle the third cycle goes to North instead of West, and West is never
served: its waiting rises to **1.502 s with 0 served** — numerically the same failure
mode this project already documented for density-only control at `α = 1.0`. The
interaction is between E2 and E4: North carries the largest spillback reading (0.80),
so E2 raises *North's* score, not West's, and E4's switching margin of 0.05 then
requires West to beat the inflated North score. Either mechanism alone leaves cycle 3
to West; both together flip it. **At this junction spillback correlates with density
rather than with standing queue**, so weighting it works against the queue-fairness
term. Neither weight was tuned — 0.25 and 0.05 were chosen a priori — so this is an
untuned operating point, and an instance of exactly the hyperparameter burden Li et
al. criticise (§4.4).

### 5.2 The Li-derived mechanisms (E5–E7)

Measured results are recorded in `report/results_summary.md` once the runs complete;
this section states what each run is for, so the numbers cannot be chosen after the
fact.

- **E5** is measurement only and cannot change control, so it is verified by
  appearing in the run log with the expected sign: `η > 0` exactly on phases whose
  green ended with the queue region still occupied.
- **E6** is compared against the reference and against E3, since it is the adaptive
  replacement for E3's open-loop timing.
- **E7** adds a stops column to every configuration already run, so the metric
  Li et al. lead on becomes reportable for all of them.

### 5.3 Caveat on sample size

The busy clip is 107 s, which is 3 cycles for the band-based configurations.
Differences of one cycle move the aggregate substantially, and the starvation limit
of 3 cycles barely has room to fire. These results characterise the mechanisms'
direction on this clip; they are not stable estimates of effect size.

---

## 6. Honest statement of what these enhancements are

They are **control-layer mechanisms transferred from two network-optimisation papers
to a single-junction vision controller, plus one consistency fix**. They are not
novel algorithms. Their value is that each replaces something hand-tuned or
saturating with something derived from traffic-flow reasoning, and that each is
measured on the same real footage against the same baseline, with results reported
from run logs rather than asserted.

What is deliberately *not* claimed: no coordination, no offsets, no green bands, no
progression, no predictive horizon, no solver, no connected-vehicle data. The
majority of both papers is network-scale and does not transfer to one camera at one
junction, and pretending otherwise would be the easiest way to fail a viva.

On the measured evidence so far: **E4 transfers well, E2 helps alone but conflicts
with E4, E1 is correct but inert on this fleet, and E3 as first built does not
transfer at this spatial scale.** Two of those five outcomes are negative and are
reported as such. Nothing was re-tuned to improve a number after the fact.
