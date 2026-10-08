# Vision-Measured Spatial Queues for Storage-Aware Adaptive Traffic Signal Control

*Computer Vision course project. Laid out in the department's research-presentation order:
introduction → challenges → applications → problem formulation → related literature and
gaps → motivation → objectives → workflow → datasets and metrics → contributions with
results → summary and takeaways → references. Every number is produced by a script in this
repository; the command is given next to it.*

---

## Table of contents

1. Introduction to vision-based signal control
2. Challenges
3. Applications in smart cities
4. Problem formulation
5. Related literature and research gaps
6. Research motivation
7. Objectives
8. Proposed workflow
9. Data, simulation testbed and evaluation measures
10. Research contributions and results
    * C1: robust spatial queue measurement from video
    * C2: spatial terms in the selection score (study 1)
    * C3: storage-aware score and storage protection (study 2)
11. Summary and key takeaways
12. References

---

## 1. Introduction

**What is vision-based adaptive signal control?** A signalised junction repeatedly
decides *which approach gets green next and for how long*. Fixed-time plans repeat the
same timings whatever traffic is present. Adaptive control measures the traffic and
decides from the measurement. Vision-based adaptive control uses a camera as the sensor:
a detector (YOLO) finds vehicles, a tracker (ByteTrack) follows each one across frames,
and the per-approach measurements drive the controller.

**Why a camera, not loop detectors?** A loop detector is a wire coil in the road that
reports whether a vehicle is over it. It sees one spot. A camera sees the whole approach,
so it can measure *where the queue ends*, not only whether a vehicle is at the stop line.

**Example.** On the Bellevue camera (116th Ave NE / NE 12th St), the queue on the South
approach backs up past the stop-line area during red. A count of vehicles in the stop-line
zone saturates at the zone's capacity; the queue tail measured along the road keeps moving
back (`report/queue_tail_validation.png`).

## 2. Challenges

* **Perspective.** Vehicles far from the camera are small; a fixed pixel threshold for
  "stopped" is wrong for near and far vehicles alike.
* **Detection recall at distance.** Small, distant, occluded vehicles are missed; on a
  test frame YOLOv8m found 4 of about 9 queued cars on the far North leg.
* **Tracking noise.** Bounding boxes jitter frame to frame; a parked-looking jitter can
  read as motion, and an ID switch breaks a vehicle's history.
* **Closed-loop evaluation.** Recorded video cannot answer "what if the signal had been
  different?": the filmed vehicles obey the real signal, not ours.
* **Over-saturation.** When demand exceeds capacity, queues reach the end of the road
  (spillback). Counting sensors saturate exactly there.

## 3. Applications in smart cities

* **Adaptive junction control** from existing traffic cameras, without cutting loops
  into the road.
* **Spillback monitoring:** knowing when an approach is about to run out of storage,
  which in a network blocks the junction upstream.
* **Traffic analytics:** queue lengths, stops per vehicle and approach demand for
  planning (the demand shares used in the calibrated simulation scenario come from the
  video counts).

## 4. Problem formulation

Each approach `i` of the junction has visible storage of length `L_i` (metres along the
road). At time `t` its queue tail is at distance `x_i(t)` from the stop line. The camera
image `I_t` is the only sensor:

```
measurement:   X̂_i(t) = h(I_t, I_{t-1}, ...; θ) ≈ x_i(t) / L_i          (1)
```

where `h` is the vision pipeline (detection, tracking, stopped test, queue-tail search)
with parameters `θ`. A controller `π` turns the measurements into a decision:

```
control:       (p_k, g_k) = π( X̂(t), D̂(t), ... )                        (2)
```

`p_k` is the approach served in green `k` and `g_k` its green time. The goal is

```
minimise   total delay  Σ_v ( t_v,actual − t_v,free )
subject to x_i(t) ≤ β_i · L_i          (spillback avoidance, Mohajerpoor et al. 2023, Eq. 9)
           g_min ≤ g_k ≤ g_max
```

`β_i ≥ 1` relaxes the constraint for low-priority movements (Mohajerpoor et al. set
β = 1 for the major and 5 for the minor road). The question this project answers is
whether a camera-measured `X̂_i` can serve this constraint, and whether doing so lowers
delay or spillback.

**The base controller (Raza et al. 2025).** Density is a PCE-weighted count,

```
D_i = Σ_c n_i,c · PCE_c                                                  (3)
p_k = argmax_i D_i  (with a starvation guard),   g_k = band(D_{p_k}) ∈ {low, medium, high}
```

(their Eq. 1, Algorithm 1; 40/60/120 s in the paper, 30/45/60 s here). Its state is a
count. It does not know how long the road is, or where the queue ends.

## 5. Related literature and research gaps

### Taxonomy

![taxonomy](figures/presentation/fig_taxonomy.png)

### Table 1: summary of signal-control methods relevant to this project

| S.No. | Method | Contribution | Salient features | Limitation |
|---|---|---|---|---|
| 1 | 1958, Webster fixed-time [1] | Optimal cycle length and splits from average flows | Simple, no sensors | Cannot react to fluctuating demand; wastes green on empty approaches |
| 2 | Actuated control (gap-out / max-out) [2] | Green ends when the detector sees no vehicle for a passage time | Responds to presence; industry standard | Sees only the detector zone; cannot tell how far back the queue reaches |
| 3 | 2013, Max pressure [3] | Throughput-optimal decentralised feedback | Serves the phase with the largest queue difference | Assumes unlimited link storage, so ignores spillback |
| 4 | 2015, Capacity-aware backpressure [4] | Normalises pressure by link capacity | Accounts for finite storage | Needs vehicle counts on every link; frequent switching |
| 5 | 2023, FASC, Mohajerpoor et al. [5] | Optimal dynamic cycles and splits for over-saturated isolated junctions | Shockwave (kinematic-wave) queue model; spillback-avoidance constraint; mixed delay + spillback-probability objective; 63/55/40% less delay than fixed/actuated/capacity-aware MP | Needs demand prediction; queue position is *estimated* from a model and loop detectors; non-convex optimisation |
| 6 | 2025, Li et al. [6] | Multi-objective corridor coordination with queue-profile estimation | Under/over-saturation test from queue discharge time | Offline fixed-time; needs connected-vehicle trajectories and a MILP solver |
| 7 | 2025, Wei et al. [7] | Hierarchical MPC with link-transmission queue dynamics | Predicts queue growth, acts before spillback | State estimation is assumed, not built; network-scale solver |
| 8 | 2025, Raza et al. [8] (base paper) | Edge-deployed YOLO + PCE density controller | PCE weighting, lane priority, green-denial counter; SUMO and real footage | State is a count: storage-blind; fixed green bands |

### Relevant methods, with their equations and limitations

**Raza et al. (base).** Eq. (3) above. *Limitation:* the count `D_i` treats 10 vehicles
on a 60 m road and 10 on a 150 m road alike, though the first is nearly full; the green
time is a fixed band, so a green never ends early when its queue has cleared.

**Capacity-aware max pressure (Gregoire et al.).** For an isolated junction, serve

```
p = argmax_i  n_i / C_i                                                   (4)
```

with `n_i` the vehicles on approach `i` and `C_i` its storage capacity, re-decided every
few seconds. *Limitation:* it re-decides on every small change in the ratio, so under
heavy demand it switches often and loses time to yellow phases (seen in §10.3).

**FASC (Mohajerpoor et al.).** Minimises delay subject to the spillback-avoidance
constraint `x_p ≤ β_p Λ_p`, and in the queue-formation period adds a penalty

```
0.5 ω₂ Σ_k Σ_p  Λ_p / (α_p Λ_p − δ_p(k))                                 (5)
```

that grows without bound as the queue `δ_p` approaches the scaled link length `α_p Λ_p`.
*Limitation:* `δ_p` comes from a shockwave model fed by predicted demand and loop
detectors, because the detectors cannot see the queue position.

### Research gaps

* Vision-based controllers (Raza) measure **counts**. They are blind to *where* the queue
  is relative to the end of the road, which is exactly what spillback avoidance needs.
* Spillback-aware methods (FASC, Wei) need the queue position but **estimate** it from
  models, predicted demand or assumed state estimators. None measures it directly.
* Counting sensors **saturate** at the stop-line zone's capacity: once it is full, a
  count forecast reads "steady" however fast the queue grows (§10.1).

## 6. Research motivation

* A camera already sees the whole approach. If the queue position can be measured
  robustly from detections and tracks, it supplies the quantity that FASC and Wei must
  model.
* If a controller uses that measured position *in the way the literature says it matters*
  (as a storage constraint, not just one more term in a ranking), it may reduce spillback
  on short approaches without costing delay.
* Whether it does is an empirical question, and recorded video cannot answer it (§2), so
  it needs a closed-loop testbed and a pre-registered comparison.

## 7. Objectives

1. Design a robust **spatial queue measurement** from YOLO + ByteTrack: the queue tail `X`
   along the road and its 5-second projection `S` (local spillback risk).
2. Test whether `X`/`S` improve a Raza-style controller when added to the **selection
   score** (study 1).
3. Design a **storage-aware** extension in the spirit of Mohajerpoor et al.: a reciprocal
   storage barrier on `S` in the score and **storage protection** in the green timing,
   and test it against standard and literature baselines over graded demand (study 2).
4. Measure **robustness** to camera errors (missed far vehicles, position jitter).

## 8. Proposed workflow

![workflow](figures/presentation/fig_workflow.png)

## 9. Data, simulation testbed and evaluation measures

**Video.** City of Bellevue Traffic Video Dataset (released for research), camera at
116th Ave NE / NE 12th St, three clips of 107 s, 240 s and 240 s, constant 30 fps. Used for
measurement (Objective 1) and to calibrate the demand shares of one simulation scenario.

**Closed-loop testbed.** SUMO (Simulation of Urban MObility, an open-source traffic
simulator) through `libsumo`. A four-arm junction, two lanes per approach, one approach
served per phase, 3 s yellow. A *virtual camera* measures `D`, `Q`, `X`, `S` from the
simulated vehicles with the same definitions as the video pipeline (`sim/sensor.py`), and
a *vision noise* mode drops up to 30% of far vehicles and jitters positions by 2 m.

**Measures.**
* **Mean delay per vehicle** (s): SUMO time loss plus the time a vehicle waited to enter
  a full road. Primary metric.
* **Blocked-entry seconds** (local spillback): seconds, summed over approaches, during
  which arriving vehicles could not enter because the queue reached the upstream end of
  the approach.
* 95% confidence intervals from paired seeds (the same random demand for every method).
* **Pre-registration.** Every free choice is made on validation seeds; the protocol is
  committed before the test seeds run (`sim/PROTOCOL.md`, `sim/PROTOCOL_STUDY2.md`).

<!-- RESULTS SECTIONS BELOW ARE FILLED FROM results/ -->
