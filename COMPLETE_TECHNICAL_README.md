# A Vision-Based Predictive Traffic Signal Controller Using YOLO-Based Vehicle Tracking and Queue Dynamics

## Complete Technical README — deep-dive, with exact code locations

> **Purpose of this file.** One document that explains the project at the depth needed
> to answer *"where exactly is that in the code, and why did you do it that way?"*
> Every claim points at a file and line number. Every number points at a `run_id`.
>
> Verified state at time of writing: **762 tests passing**, **57 run logs**,
> **12 source modules (~9,550 lines)**, **34 test modules (~12,944 lines)**.

---

# TABLE OF CONTENTS

1. [The assignment and how the project answers it](#1)
2. [System architecture and the per-frame data flow](#2)
   — 2.1 layered architecture · 2.2 module dependency DAG · 2.3 per-frame pipeline
   · 2.4 ROI geometry · 2.5 score composition tree · 2.6 signal state machine
   · 2.7 feedback loop · 2.8 ordering · 2.9 one-frame trace
3. [Module-by-module code walkthrough](#3)
4. [The measurement layer — every quantity, with formulae and code](#4)
5. [The scoring function — the mathematical core](#5)
6. [The control layer — selection, timing, safety invariants](#6)
7. [The two IEEE TITS papers: full limitation analysis](#7)
8. [What I built, why, and where](#8)
9. [My own contributions in full detail](#9)
   — 9.1 spatial queue reach · 9.2 spillback risk · 9.3 novelty positioning
   · 9.4 **validating the axis, and a defect I found in my own calibration**
10. [**The dataset — provenance, preprocessing, and why these clips**](#ds)
11. [The staged ablation — experimental design and results](#10)
12. [Testing strategy — how correctness is established](#11)
13. [Reproducibility and the evidence chain](#12)
14. [Honest limitations](#13)
15. [How to run everything](#14)
16. [Deep-dive Q&A — the hard questions](#15)
17. [**NOTATION — every symbol defined**](#0)

---

<a name="0"></a>
# 0. NOTATION — every symbol defined

> Read this before the formula sections. Laid out the way IEEE TITS papers do it
> (Wei et al. have "TABLE I NOMENCLATURE"; Li et al. have "TABLE II NOTATIONS").

## 0.1 Indices and sets

| Symbol | Read as | Means |
|---|---|---|
| $i$ | "eye" | Approach index. Always one of North, East, South, West. A subscript $i$ means *"of this approach"* |
| $j$ | "jay" | A single vehicle (one Track_ID) |
| $k$ | "kay" | Discrete time step / frame index |
| $\Omega_i$ | "omega-i" (capital) | The **ROI polygon** of approach $i$ — the whole visible road area |
| $\Theta_i$ | "theta-i" (capital) | The **Queue_Region polygon** of approach $i$ — the stop-line area, sits inside $\Omega_i$ |
| $\mathcal{S}_i$ | "script-S-i" | The **set of stopped vehicles** on approach $i$ this frame |
| $\mathcal{Q}_i$ | "script-Q-i" | The set of **queueing** vehicles (those inside $\Theta_i$) |

## 0.2 The seven measured quantities (all normalised to 0..1)

These are the inputs to the score. Each is a *fraction*, so they are directly comparable.

| Symbol | Name | Means in plain words | Code field |
|---|---|---|---|
| $D_i$ | **density** | How crowded the whole approach is, PCE-weighted | `vehicle_density` |
| $Q_i$ | **queue** | How full the stop-line area is | `normalized_queue` |
| $A_i$ | **arrival** | Vehicles present but *not yet* queueing (approaching) | `normalized_arrival` |
| $B_i$ | **backed-up / observed spillback** | Vehicles stopped *past* the stop-line area — overflow that **already happened** | `normalized_spillback` |
| $F_i$ | **forecast** | Where the *count-based* queue is heading | `normalized_forecast` |
| $X_i$ | **queue reach (eXtent)** | How far **back** the queue physically stretches | `queue_reach` |
| $S_i$ | **spillback risk** | Where the *spatial* occupancy is heading → about to run out of storage | `spillback_risk` |

**Why $X$ for reach and $B$ for observed spillback:** to keep $R$ free (it collides with
the ROI polygon) and to make the $B$ (already happened) vs $S$ (about to happen)
distinction visible in the notation itself.

## 0.3 The six score weights

Each says *"what share of the decision does this term get?"* All lie in 0..1.

| Symbol | Read as | Weights which term | Config field | Default |
|---|---|---|---|---|
| $\alpha$ | alpha | balance **between** $D$ and $Q$ | `alpha` | 0.5 |
| $\gamma$ | gamma | arrival $A_i$ | `predictive_weight` | 0.0 |
| $\delta$ | delta | observed spillback $B_i$ | `spillback_weight` | 0.0 |
| $\omega$ | omega (lowercase) | forecast $F_i$ | `forecast_weight` | 0.0 |
| $\psi$ | psi | queue reach $X_i$ | `queue_reach_weight` | 0.0 |
| $\rho$ | rho | spillback risk $S_i$ | `spillback_risk_weight` | 0.0 |

$\alpha$ is different in kind from the rest: it *splits* the base term, while the other
five *take share away from* the base term. Constraint:
$\gamma + \delta + \omega + \psi + \rho \le 1$.

## 0.4 Geometry symbols (the `ApproachAxis` construction)

This is the notation for my own contribution. All quantities are in **image pixels**.

| Symbol | Read as | Means | Where it comes from |
|---|---|---|---|
| $\mathbf{c}_i$ | "c-i", bold | **Centroid of the ROI** $\Omega_i$ — the middle of the whole road area | `polygon_centroid(roi)` |
| $\mathbf{o}_i$ | "o-i", bold | **Origin = centroid of the Queue_Region** $\Theta_i$. This *is* the stop line, and it is the zero point of the axis | `polygon_centroid(queue_region)` |
| $\mathbf{u}_i$ | "u-i", bold | **Downstream** unit vector — points from road centre *toward* the stop line, i.e. the direction traffic travels | computed |
| $\hat{\mathbf{u}}_i$ | "u-hat-i" | **Upstream** unit vector $= -\mathbf{u}_i$ — points *back up* the queue. This is the measuring direction | computed |
| $L_i$ | "L-i" | **Length of the approach** upstream of the stop line, in pixels. The normalising divisor | max vertex projection |
| $\mathbf{v}$ | "v", bold | One **vertex** of the ROI polygon | config |
| $\mathbf{p}$ | "p", bold | Any **point** in the image — in practice a vehicle's reference point | tracker |
| $t_i(\mathbf{p})$ | "t-of-p" | **Position fraction**: 0 at the stop line, 1 at the far upstream edge | `ApproachAxis.fraction` |

**Bold means a 2-D vector** $(x, y)$; plain italic means a single number (scalar).
**A hat** ($\hat{\mathbf{u}}$) means a unit vector — length exactly 1, so it carries
direction only, no magnitude.

**Reading $(\mathbf{p} - \mathbf{o}_i)\cdot\hat{\mathbf{u}}_i$ in words:**
*"take the vehicle's position, measure it relative to the stop line, then ask how far
that lies along the upstream direction."* The dot product $(\cdot)$ is what converts a
2-D offset into a single distance along one chosen direction.

## 0.5 Time and prediction symbols

| Symbol | Read as | Means | Config field | Default |
|---|---|---|---|---|
| $H$ | "H" | **Forecast horizon** — how many seconds ahead to project the queue | `forecast_horizon_seconds` | 3.0 s |
| $H_{risk}$ | "H-risk" | **Risk horizon** — how far ahead to project the spatial occupancy | `risk_horizon_seconds` | 5.0 s |
| $W$ | "W" | **Window** — how many recent frames the trend is fitted over | `forecast_window_frames` | 15 |
| $\Delta t$ | "delta-t" | One frame's duration in seconds, $= 1/\text{frame rate}$ | derived | 1/30 s |
| $\dfrac{dX_i}{dt}$ | "dX by dt" | **Rate of change of the reach** — how fast the queue is extending backwards, per second. Positive = growing, negative = clearing | `_slope_per_second` | — |

## 0.6 Green-time and control symbols

| Symbol | Means | Config field |
|---|---|---|
| $g_i$ | Green time granted to approach $i$, in seconds | output |
| $g_{\min}, g_{\max}$ | Green-time bounds every path is clamped to | `min_green_time`, `max_green_time` |
| $q_s$ | Saturation flow rate — PCE discharged per second of green | `saturation_flow_rate` |
| $T_{loss}$ | Lost time per phase (here the yellow duration) | `yellow_duration` |
| $C_i$ | `saturation_count` — PCE that counts as a "full" approach | `saturation_count` |
| $K_i$ | `queue_capacity` — vehicles/PCE that count as a "full" queue region | `queue_capacity` |
| $\eta_i$ | **Over-saturation level** — seconds of green the queue still needed | `oversaturation` |

## 0.7 Symbols borrowed from the two TITS papers

Used only in §7 when describing *their* methods. Kept as the authors wrote them.

**Li et al. (queue profile / over-saturation):**

| Symbol | Means |
|---|---|
| $\varepsilon_{l,k}$ | Queue length in **metres** for phase $l$ at intersection $k$ |
| $W_{l,k}$ | Saturation flow (m/s) |
| $V^f_{l,k}$ | Free-flow speed (m/s) |
| $u_{l,k}$ | Queue **discharging time** — how long the queue needs to clear |
| $\phi_{l,k}$ | Phase split (the green time actually given) |
| $\eta_{l,k}$ | Level of over-saturation $= u - \phi$ |
| $T_{loss}$ | Lost time per phase (4 s in their experiments) |

Their test: **under-saturated iff $\phi \ge u$**, otherwise over-saturated.

**Wei et al. (link transmission model with queue dynamics):**

| Symbol | Means |
|---|---|
| $N^{in}, N^{out}, N^{que}$ | **Cumulative** vehicle counts: link inflow, outflow, queue inflow |
| $\rho^{que}, \rho^{c}, \rho^{jam}$ | Queue density, **critical** density, **jam** density |
| $q^{c}, q^{out}, q^{s}$ | Capacity flow, outflow rate, saturation flow rate |
| $b_i$ | Effective **fraction** of green time (their decision variable, 0..1) |
| $\Delta b$ | Variation in that fraction between steps — what they penalise for stability |
| $L_q, L_f$ | Length of the queued part and the free-flowing part of a link |
| $T_n, T_l$ | Prediction horizon of the network layer and the local layer |

> ⚠️ **Symbol collision, flagged deliberately.** Wei et al. use $\rho$ for traffic
> **density**. In this project $\rho$ is the **spillback-risk weight**. They are unrelated.
> I keep both as written rather than renaming, because renaming a published paper's
> notation makes it harder to check my §7 against the source. Same for $u$: Li et al.
> use it for discharging *time*; I use bold $\mathbf{u}$ for a direction *vector*.

## 0.8 Operators

| Symbol | Means |
|---|---|
| $\mathrm{clamp}(x)$ | Force $x$ into 0..1: below 0 → 0, above 1 → 1, else unchanged |
| $\mathrm{clamp}(x, a, b)$ | Same, into $[a,b]$ |
| $\lVert \mathbf{a} \rVert$ | Length (magnitude) of vector $\mathbf{a}$, $=\sqrt{a_x^2+a_y^2}$ |
| $\mathbf{a}\cdot\mathbf{b}$ | Dot product — projects one vector onto another, giving a single number |
| $\max_{j\in\mathcal{S}} f(j)$ | Largest value of $f$ over every member of set $\mathcal{S}$ |
| $\lvert\mathcal{S}\rvert$ | Number of elements in set $\mathcal{S}$ (its cardinality) |

---

<a name="1"></a>
# 1. The assignment and how the project answers it

## 1.1 What was asked

```
Existing paper → find its limitation → study newer IEEE TITS research
→ pick one meaningful limitation → build your own enhancement
→ experimentally prove whether it helps
```

Two phrases matter: **"your own enhancement"** (not a reimplementation) and
**"experimentally prove whether it helps"** (an ablation, not a demo).

## 1.2 The research chain this project follows

```
              Raza et al. 2025 (IEEE Access)
                          |
              Reactive PCE-density control
                          |
        LIMITATION: reacting to current traffic is
        insufficient when a queue is growing or is
              about to exhaust its storage
                          |
        +-----------------+------------------+
        |  IEEE TITS 2025                    |
        |  Li et al.  - queue profile,       |
        |               over-saturation      |
        |  Wei et al. - predictive queue     |
        |               dynamics, spillback  |
        +-----------------+------------------+
                          |
        Their machinery does NOT transfer:
        network MPC, link transmission model,
        GUROBI/CPLEX, connected-vehicle data
                          |
              TAKE THE IDEA, NOT THE MACHINERY
                          |
                  MY CV ADAPTATION
                          |
        +-----------------+------------------+
        |                                    |
   YOLO + ByteTrack                Spatial queue measure
   trajectories                    (own contribution)
        |                                    |
        +-----------------+------------------+
                          |
          Short-term queue-growth prediction
                          |
             Spillback-risk-aware control
                          |
        STAGED ABLATION: S0 → S1 → S2 → S3 → S4
```

## 1.3 The research question

> *Can short-term queue-growth information extracted from vehicle trajectories improve
> a density-based adaptive traffic signal controller by anticipating queue buildup and
> spillback?*

**Measured answer:** Yes — **but only when the projected quantity does not saturate.**
Projecting the count-based queue changed nothing (S3). Projecting the spatial occupancy
raised throughput 20.6% (S4). That distinction is the project's core finding.

---

<a name="2"></a>
# 2. System architecture and the per-frame data flow

## 2.1 Layered architecture (the 10,000-foot view)

Five layers. Data flows down; nothing flows back up except the **control feedback loop**
marked with `<<<`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  INPUT                                                                   │
│  videos/*.mp4   +   config/*.json   +   models/yolov8n.pt                │
└──────────────────────────────┬───────────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — PERCEPTION           "what objects are in this frame?"        │
│  ┌────────────────┐   ┌───────────────┐   ┌────────────────────┐        │
│  │ VideoIngestor  │──>│ YoloDetector  │──>│ ByteTrackTracker   │        │
│  │ video_io.py    │   │ detection.py  │   │ tracking.py        │        │
│  └────────────────┘   └───────────────┘   └────────────────────┘        │
│   frames               boxes+classes       boxes + PERSISTENT IDs        │
└──────────────────────────────┬───────────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — SPATIAL REASONING    "which road is each vehicle on?"        │
│  ┌──────────────────┐        ┌────────────────────────────────┐         │
│  │ ApproachAssigner │        │ ApproachAxis    ** MY WORK **  │         │
│  │ ROI point-in-poly│        │ upstream axis from 2 polygons  │         │
│  └──────────────────┘        └────────────────────────────────┘         │
│                     lane_analysis.py                                    │
│   -> AssignedTrack(track_id, class, ref_point, approach, is_queueing)   │
└──────────────────────────────┬───────────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — MEASUREMENT          "how bad is each road?"                 │
│  ┌────────────────────────┐   ┌──────────────────────────────────┐      │
│  │ MetricsEngine          │──>│ QueuePredictor                   │      │
│  │ D Q A B X + stops/wait │   │ F (count-based) S (spatial) **   │      │
│  └────────────────────────┘   └──────────────────────────────────┘      │
│                       traffic_metrics.py                                │
│   -> ApproachMetrics x4  (all quantities normalised to 0..1)            │
└──────────────────────────────┬───────────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — DECISION             "who gets green, for how long?"         │
│  ┌──────────────────┐   ┌──────────────┐   ┌────────────────────┐       │
│  │ compute_config_  │──>│ Adaptive /   │──>│ PhaseSequencer     │       │
│  │ scores + demands │   │ FixedTime    │   │ timing + SAFETY    │       │
│  │ traffic_metrics  │   │ controller   │   │ signal_controller  │       │
│  └──────────────────┘   └──────────────┘   └────────────────────┘       │
│   Score_i in 0..1        Selection          SignalState x4              │
└──────────────────────────────┬───────────────────────────────────────────┘
        <<<  feedback: signal state gates the NEXT frame's waiting time  <<<
                               v
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — OUTPUT and EVIDENCE                                          │
│  ┌───────────────┐   ┌─────────────────┐   ┌───────────────────────┐    │
│  │ SignalOverlay │   │ ResultsStore    │   │ evaluation            │    │
│  │ overlay.py    │   │ results_store   │   │ metrics/tables/graphs │    │
│  └───────────────┘   └─────────────────┘   └───────────────────────┘    │
│   annotated video     results/run_logs/*.json   report/*.png, *.md      │
└──────────────────────────────────────────────────────────────────────────┘

  ** = my own contribution (ApproachAxis, spillback risk)
```

## 2.2 Module dependency graph (verified, acyclic)

Generated from the actual `from src.X import` statements. **The graph is a DAG — there
are no circular imports**, and the layering is strict.

```
 L0   errors                       (no internal dependencies)
        │
 L1   config ──────────────────────────────────────────────┐
        │                                                  │
 L2   video_io          signal_controller ◄─────────────────┤
        │                     (depends ONLY on config)      │
 L3   detection ◄──────────────────────────────────────────┤
        │                                                  │
 L4   tracking ◄───────────────────────────────────────────┤
        │                                                  │
 L5   lane_analysis ◄──────────────────────────────────────┤
        │                                                  │
 L6   traffic_metrics ◄────────────────────────────────────┤
        │                                                  │
 L7   overlay      results_store ◄─────────────────────────┤
        │                │                                 │
 L8   main ◄─────────────┴─────────────────────────────────┘
        │
 L9   evaluation
```

| Module | Depends on |
|---|---|
| `errors` | — |
| `config` | `errors` |
| `signal_controller` | **`config` only** |
| `video_io` | `config`, `errors` |
| `detection` | `config`, `errors`, `video_io` |
| `tracking` | `config`, `detection` |
| `lane_analysis` | `config`, `detection`, `errors`, `tracking`, `video_io` |
| `traffic_metrics` | `config`, `errors`, `lane_analysis` |
| `overlay` | `config`, `detection`, `errors`, `lane_analysis`, `tracking`, `traffic_metrics`, `video_io` |
| `results_store` | `config`, `errors`, `traffic_metrics`, `video_io` |
| `main` | everything above |
| `evaluation` | `config`, `errors`, `lane_analysis`, `main`, `results_store` |

### Two dependency facts worth pointing out

**1. `signal_controller` depends on `config` and nothing else.** Not on
`traffic_metrics`, not on `tracking`, not on video. That is deliberate: the entire
control layer — both controllers *and* all the safety invariants — can be tested with
plain dictionaries of numbers, **no video, no YOLO, no GPU**. It is why 
`tests/test_signal_properties.py` can run thousands of generated scenarios in seconds.

**2. `traffic_metrics` does not import `signal_controller`.** The dependency runs one
way only. `MetricsEngine` gates waiting time on signal state, but it receives that state
as **plain strings** (`"GREEN"`, `"YELLOW"`, `"RED"`) rather than importing the
`SignalState` enum. `_check_signal_states` reads `getattr(state, "value", state)`, so it
accepts either. That keeps the two layers independently testable.

## 2.3 The per-frame pipeline, with data types

This is the loop body of `Pipeline.run` (`src/main.py:196`), executed once per frame.

```
                 ┌─────────────────────────────────────────┐
                 │  frame k   (numpy array, 720x1280x3)    │
                 └────────────────────┬────────────────────┘
                                      v
  (1) DETECT            YoloDetector.detect(frame)
      detection.py      ├─ YOLO inference
                        ├─ keep only car / motorcycle / bus / truck
                        ├─ drop conf < confidence_threshold
                        └─ clip boxes to frame bounds
                        ▼  list[Detection(bbox, class, confidence)]

  (2) TRACK             ByteTrackTracker.update(frame, detections)
      tracking.py       ├─ associate detections to existing tracks
                        ├─ assign new Track_IDs, retire stale ones
                        └─ append to bounded trajectory deques
                        ▼  list[Track(track_id, bbox, class, ref_point, trajectory)]

  (3) ASSIGN            ApproachAssigner.assign(tracks, frame_index)
      lane_analysis.py  ├─ ref_point = bottom-centre of box  (Req 4.2)
                        ├─ point_in_polygon vs each ROI  Ω_i
                        ├─ 0 hits -> approach=None (EXCLUDED from measurement)
                        ├─ 1 hit  -> assigned
                        ├─ 2+ hits-> nearest ROI centroid + log OverlapEvent
                        └─ is_queueing = inside Queue_Region Θ_i
                        ▼  list[AssignedTrack], list[OverlapEvent]

  (4) SIGNAL            PhaseSequencer.tick(k, scores, demands)   <-- BEFORE metrics
      signal_controller ├─ E6 gap-out / extend the active green
                        ├─ expire green -> yellow -> red -> next cycle
                        ├─ on new cycle: controller.select(scores, demands)
                        └─ derive 4 SignalStates from ONE internal triple
                        ▼  PhaseInfo,  dict[approach -> SignalState]

  (5) MEASURE           MetricsEngine.update(assigned, states, dt)
      traffic_metrics   ├─ dedupe by Track_ID  -> n_i
                        ├─ PCE-weight          -> D_i
                        ├─ queueing subset     -> m_i, Q_i
                        ├─ stopped test        -> B_i, X_i, stop counts
                        ├─ accrue waiting where state != GREEN
                        └─ count vehicles served where state == GREEN
                        ▼  dict[approach -> ApproachMetrics]

  (6) PREDICT           QueuePredictor.predict(metrics)     [if enabled]
      traffic_metrics   ├─ push Q_i onto its window -> slope -> F_i
                        └─ push X_i onto its window -> slope -> S_i
                        ▼  dict[approach -> ApproachMetrics]  (+F, +S)

  (7) SCORE             compute_config_scores(metrics, config)
      traffic_metrics   └─ convex blend of D Q A B F X S
                        ▼  dict[approach -> float in 0..1]

      DEMAND            compute_config_demands(metrics, config)
                        └─ PCE demand for discharge-based green timing
                        ▼  dict[approach -> float PCE]

  (8) DRAW              SignalOverlay.draw(...)
      overlay.py        └─ ROIs, boxes, panel (n= q= d= s= f= ^), lights, banner
                        ▼  annotated frame -> optional .mp4

  (9) RECORD            ResultsStore.append_frame_record(log, measurement)
      results_store     └─ one JSON object per frame
                        ▼  results/run_logs/<run_id>.json

        └────────► scores, demands carried to frame k+1 ─────────┐
                                                                  │
                              (feedback into step 4 next frame) ◄─┘
```

## 2.4 The geometry each approach is measured against

Two polygons per approach, both hand-drawn once per camera in the config. Validation
enforces that every `queue_region` vertex lies **inside** its parent `roi_polygon`
(`src/config.py:503`).

```
        ┌──────────────── frame 1280 x 720 ─────────────────┐
        │                                                    │
        │            ╔═══════ NORTH Ω ═══════╗               │
        │            ║   ┌──── Θ ────┐        ║               │
        │            ║   │ stop line │        ║               │
        │            ╚═══╧═══════════╧════════╝               │
        │   ╔═══════╗      ┌───────────┐      ╔═══════╗      │
        │   ║ WEST  ║      │           │      ║ EAST  ║      │
        │   ║  Ω    ║ ┌─Θ─┐│ JUNCTION  │┌─Θ─┐ ║  Ω    ║      │
        │   ║       ║ └───┘│  (no ROI  │└───┘ ║       ║      │
        │   ╚═══════╝      │  covers   │      ╚═══════╝      │
        │                  │   this)   │                      │
        │            ╔═════╧═══════════╧══════╗               │
        │            ║   ┌──── Θ ────┐        ║               │
        │            ║   │ stop line │        ║               │
        │            ╚═══╧═══════════╧════════╝               │
        │                 SOUTH Ω                             │
        └────────────────────────────────────────────────────┘

  Ω = roi_polygon    (whole visible approach)
  Θ = queue_region   (stop-line area, must sit inside Ω)
```

**This diagram is also the reason a scope limit exists (§13.3):** all four ROIs are
**inbound**. The junction box itself is in no ROI, and the *outbound* side of each road
is in no ROI. Once a vehicle crosses the stop line it leaves the measured area entirely
— so downstream occupancy is unobservable from this camera. That is why "spillback risk"
here means *this approach running out of its own storage*, not *blocking the road ahead*.

## 2.5 How the score is assembled (the composition tree)

```
                        Score_i   (0..1, feeds the controller)
                            │
        ┌───────────────────┼───────────────────────────────┐
        │                   │                               │
   (1-Σweights)        extension terms                  weights Σ ≤ 1
     × base_i                │                        enforced at load
        │       ┌────────┬───┴────┬─────────┬─────────┐
        │       │        │        │         │         │
        │     γ·A_i    δ·B_i    ω·F_i     ψ·X_i     ρ·S_i
        │    arrival  backed-up forecast   reach     risk
        │      now    ALREADY   FUTURE      now      FUTURE
        │             happened  (count)   (spatial) (spatial)
        │
    base_i = α·D_i + (1-α)·Q_i
             │        │
          density   queue
           now       now
```

Reading the layers of the tree:

| Row | Question it answers |
|---|---|
| `D_i`, `Q_i`, `A_i` | What is happening **right now**? |
| `B_i` | What has **already gone wrong**? |
| `X_i` | How far back does it **physically stretch**? |
| `F_i`, `S_i` | Where is it **heading**? |

## 2.6 The signal state machine

`PhaseSequencer` stores **one** triple `(active_approach, active_state, phase_end_frame)`
and *derives* all four approach states from it. Only three edges exist.

```
                     ┌──────────────────────────────┐
                     │  start of run                │
                     │  active_approach = None      │
                     │  -> all four read RED        │
                     └──────────────┬───────────────┘
                                    │ controller.select()
                                    v
        ┌────────────┐  green frames  ┌────────────┐  yellow frames  ┌───────┐
        │   GREEN    │───── elapse ──>│   YELLOW   │───── elapse ───>│  RED  │
        │ (exactly 1 │                │ (same      │                 │       │
        │  approach) │                │  approach) │                 │       │
        └────────────┘                └────────────┘                 └───┬───┘
              ^                                                          │
              │                    next cycle begins in the SAME tick    │
              └──────────────────────────────────────────────────────────┘
                     (so no frame ever reads all-RED mid-run)

   LEGAL_TRANSITIONS = { RED->GREEN , GREEN->YELLOW , YELLOW->RED }
   Anything else raises in _transition()  (signal_controller.py:1033)
```

Why this shape:

- **One triple, four derived states** → "exactly one approach non-RED" cannot be
  violated, because there is no per-approach state to disagree.
- **Yellow→next green in the same tick** → without it, the frame between two cycles
  would read all-RED and break that invariant.
- **No GREEN→RED edge** → `finalize()` therefore does *not* tidy the signal to RED at
  end of run; inventing that edge would be exactly the hidden transition the invariant
  forbids.

## 2.7 The closed feedback loop

The one genuine cycle in the system. Everything else is a straight line.

```
   ┌──────────────────────────────────────────────────────────────┐
   │                                                              │
   v                                                              │
 Signal state on frame k                                          │
   │                                                              │
   ├──> gates whether waiting time accrues  (state != GREEN)      │
   ├──> gates whether vehicles count as served (state == GREEN)   │
   │                                                              │
   v                                                              │
 ApproachMetrics for frame k                                      │
   │                                                              │
   v                                                              │
 Score_i and demand_i                                             │
   │                                                              │
   └──> consumed by controller.select() when the NEXT cycle begins ┘
```

**Where the loop is broken deliberately:** the vehicles themselves are **not** in this
loop. The footage is recorded, so a green light cannot make a car move. That is the
open-loop limitation of §13.1, and it is why queue length and stop counts are identical
across every controller while waiting time and throughput differ.

## 2.8 The one ordering decision that matters

In `Pipeline.run` (`src/main.py:196`) the loop is:

```python
phase_info = sequencer.tick(index, scores, demands)   # 1. advance the signal
signal_states = sequencer.signal_states()             # 2. read the state
metrics = engine.update(assigned, signal_states, dt)  # 3. measure under that state
if predictor is not None:
    metrics = predictor.predict(metrics)              # 4. project forward
scores = compute_config_scores(metrics, config)       # 5. score for NEXT cycle
demands = compute_config_demands(metrics, config)
```

**Why the sequencer ticks before the metrics update:** waiting time only accrues while
an approach is *not* green. If metrics ran first, frame *n* would be measured against
frame *n−1*'s signal state, and every phase boundary would mis-attribute one frame of
waiting. Ticking first guarantees the gating state is the one in force on that frame.

**Consequence, recorded honestly:** the controller selects using the *previous* frame's
scores, because frame *n*'s scores do not exist yet when the phase begins. The run log
stores `selection_score_frame = frame_index − 1` for exactly this reason
(`src/signal_controller.py:924`, `_start_segment`).

## 2.9 A concrete trace — one frame, end to end

Frame 1,842 of the busy clip, adaptive controller, North currently GREEN. Values are
representative of that run rather than a single logged record.

| Step | What happens | Result |
|---|---|---|
| 1 | YOLO returns 11 boxes above threshold | 11 detections |
| 2 | ByteTrack matches 10 to existing IDs, opens 1 new | 11 tracks |
| 3 | 9 fall inside an ROI, 2 fall in the junction box | 9 assigned, 2 with `approach=None` |
| 4 | North's green has 6 frames left → no transition | North GREEN, others RED |
| 5 | North: n=4, m=3 → D=0.80, Q=0.75; West: n=3, m=3, one stopped at t=0.71 → X=0.71 | 4 × `ApproachMetrics` |
| 6 | West's reach window slope = +0.06/s → S = clamp(0.71 + 0.06×5) = **1.00** | risk fires for West |
| 7 | With ρ=0.3: West's score rises above North's | West leads |
| 8 | Panel shows `West n=3 q=3 d=0.60 s=0.68 f=0.94^` | caret = anticipating |
| 9 | One JSON record appended | log grows by one frame |

**What this trace demonstrates:** West has *fewer vehicles* than North (3 vs 4) and lower
density (0.60 vs 0.80). A density-only controller would never pick it. But its queue is
**physically extending backwards**, so `X` is high and `S` saturates — and the risk term
surfaces it. That is the exact mechanism behind the S3→S4 result in §10.3.

---

<a name="3"></a>
# 3. Module-by-module code walkthrough

| Module | Lines | Responsibility |
|---|---|---|
| `src/config.py` | 632 | Frozen config dataclasses, JSON load/save, **total validation** |
| `src/video_io.py` | 212 | Frame reading, frame-rate substitution, display loop |
| `src/detection.py` | 546 | YOLO wrapper, class filtering, box clipping |
| `src/tracking.py` | 567 | ByteTrack wrapper, Track_ID lifecycle, trajectories |
| `src/lane_analysis.py` | 515 | Polygon geometry, approach assignment, **ApproachAxis** |
| `src/traffic_metrics.py` | 847 | **All measurement + QueuePredictor + scoring** |
| `src/signal_controller.py` | 984 | Controllers + **PhaseSequencer** + safety invariants |
| `src/overlay.py` | 982 | All drawing |
| `src/results_store.py` | 456 | Run_Log schema, serialisation, round-trip |
| `src/evaluation.py` | 985 | Metric computation, tables, graphs, harness |
| `src/main.py` | 739 | Pipeline assembly + CLI |
| `src/errors.py` | 33 | Error hierarchy |

## 3.1 `src/config.py` — why validation is *total*

`load_config` (line 719) parses then calls `_validate` (line 580). Validation is
**total**: a config that loads is guaranteed usable by every downstream component, so
**no downstream component re-validates**. This is a deliberate design rule — it means
`MetricsEngine` can divide by `saturation_count` without a guard, because a
non-positive value cannot reach it.

What `_validate` enforces:
- exactly four approaches named North/East/South/West
- every ROI polygon has ≥3 vertices, all inside `frame_size`
- every queue-region vertex lies **inside its parent ROI** (line 503, `_validate_approaches`)
- `saturation_count > 0`, `queue_capacity > 0`
- `alpha ∈ [0,1]`, `starvation_limit ≥ 1`
- green bands strictly increasing in `min_score`, starting at 0, non-decreasing in
  `green_time`, all inside `[min_green_time, max_green_time]` (line 543)
- **all five extension weights sum to ≤ 1.0** (the convexity guarantee, see §5.2)

Every failure raises `ConfigError` naming the offending field.

**Optional-key design:** extension fields use `_optional_bool` / `_optional_float` /
`_optional_int` (lines 203–230) so a config file written before a feature existed still
loads with that feature off. This is what makes all 44 historical run logs still
readable.

## 3.2 `src/lane_analysis.py` — geometry

- `as_cv_polygon` (57) — tuple-of-tuples → OpenCV contour
- `point_in_polygon` (72) — `cv2.pointPolygonTest(..., measureDist=False) >= 0`, so
  **boundary points count as inside** (a vehicle exactly on an ROI edge is assigned,
  not dropped)
- `polygon_centroid` (83) — **area** centroid from image moments, not a vertex mean.
  A vertex mean would be dragged toward any edge with densely spaced vertices. Falls
  back to the vertex mean only for a degenerate (zero-area) polygon.
- **`ApproachAxis` (100)** — my contribution, see §9
- `ApproachAssigner` (283) — precomputes contours and centroids **once** in
  `__init__`, so per-frame cost is one point-in-polygon test per track per approach

**Assignment rules** (`assign`, line 336):
- 0 candidates → `approach=None`, excluded from all measurement
- 1 candidate → assigned
- ≥2 candidates → **nearest ROI centroid** wins, and an `OverlapEvent` is logged. The
  candidate list is walked in fixed North/East/South/West order with a strict `<`
  comparison, so an exact distance tie resolves deterministically to the earlier
  approach.

## 3.3 `src/tracking.py` — why tracking is load-bearing

Tracking is not cosmetic here. Three measurements are impossible without persistent IDs:

1. **Deduplication** — `vehicle_count` is `len(set_of_track_ids)`, so one vehicle
   detected twice in a frame cannot inflate the count
2. **Waiting time** — accumulated per Track_ID across frames
3. **Stopped detection** — requires comparing a track's position between frames

`ByteTrackTracker` records retired IDs and asserts none is reissued; trajectories live
in a `dict[int, deque]` with `maxlen=trajectory_length` and are deleted on retirement,
so memory stays bounded by the live track set rather than growing over the run.

---

<a name="4"></a>
# 4. The measurement layer — every quantity, with formulae and code

All in `src/traffic_metrics.py`. `ApproachMetrics` (line 80) is a **frozen** dataclass
with `__post_init__` (141) validating every invariant, so an impossible combination
cannot flow downstream even from a hand-built test value.

## 4.1 The ten measured quantities

| Field | Formula | Range | Code |
|---|---|---|---|
| `vehicle_count` $n_i$ | $\lvert\{\text{distinct track IDs in }\Omega_i\}\rvert$ | ≥0 int | `_measure`:419 |
| `vehicle_density` $D_i$ | $\mathrm{clamp}\big(\sum \mathrm{PCE} / C_i\big)$ | 0..1 | 419 |
| `queue_length` $m_i$ | $\lvert\mathcal{Q}_i\rvert$ (distinct IDs in $\Theta_i$) | ≥0 int | 419 |
| `queue_pce` | $\sum_{j\in\mathcal{Q}_i} \mathrm{PCE}(\text{class}_j)$ | ≥0 | 419 |
| `normalized_queue` $Q_i$ | $\mathrm{clamp}(\texttt{queue\_pce} / K_i)$ | 0..1 | 419 |
| `normalized_arrival` $A_i$ | $\mathrm{clamp}\big((n_i - m_i) / C_i\big)$ | 0..1 | 419 |
| `normalized_spillback` $B_i$ | $\mathrm{clamp}\big(\mathrm{PCE}(\mathcal{S}_i \setminus \mathcal{Q}_i) / C_i\big)$ | 0..1 | 419 |
| **`queue_reach`** $X_i$ | $\max_{j\in\mathcal{S}_i} t_i(\mathbf{p}_j)$ | 0..1 | 419 + `ApproachAxis` |
| `normalized_forecast` $F_i$ | $\mathrm{clamp}(Q_i + \tfrac{dQ_i}{dt}\,H)$ | 0..1 | `_project`:665 |
| **`spillback_risk`** $S_i$ | $\mathrm{clamp}(X_i + \tfrac{dX_i}{dt}\,H_{risk})$ | 0..1 | `_project_risk`:640 |

## 4.2 Structural invariants (why they hold by construction, not by check)

**`queue_length ≤ vehicle_count`.** The queueing set is built as a **subset** of the
same track-ID set the count comes from (`update`, line 298):

```python
classes[approach].setdefault(track.track_id, track.vehicle_class)
if track.is_queueing:
    queueing[approach].add(track.track_id)
```

There is no independent counting path, so the inequality cannot be violated.

**Every ratio stays in 0..1.** All pass through the single `clamp` helper (line 65).
One implementation point, so PCE weighting cannot push a ratio out of range however
large the configured weights are — the divisor and the clamp are shared between the
weighted and unweighted paths.

**Waiting time is non-decreasing.** `_accumulate` (389) only ever *adds*. A GREEN
approach's tracks are **skipped entirely** rather than incremented by zero, so
"waiting freezes on green" holds without a separate hold step.

## 4.3 PCE weighting — one routine, three consumers

`_pce_subset` (497) sums the configured weight over a named subset of tracks.
`_pce_numerator` (493) is `_pce_subset(classes, classes.keys())`. Density, queue, and
spillback all call it. **Why one routine:** a class with a missing weight then fails
identically wherever it is first encountered, rather than being silently skipped in one
term and rejected in another.

## 4.4 Stopped detection — the basis of three measures

`_is_stopped` (476):

```python
previous = self._previous_points.get(track_id)
if previous is None:
    return False                     # first sighting -> conservatively NOT stopped
threshold = config.stopped_displacement          # default 2.0 px/frame
return math.hypot(dx, dy) < threshold
```

Two deliberate choices:
- **First sighting is never "stopped."** A vehicle must be observed stationary across
  two frames. This stops newly-detected vehicles from inflating spillback and reach.
- **`_previous_points` is rebuilt each frame** from the current tracks, so it stays
  bounded by the live track set rather than growing over the run.

At 30 fps, 2 px/frame ≈ 60 px/s. On this camera that is roughly a walking-pace
threshold — documented as a calibration parameter, not a magic number.

## 4.5 Stop counting (Li's headline metric)

A stop is a **moving→stopped transition**, not a stopped frame:

```python
if was_stopped and track.track_id not in self._stopped_previous:
    self._stops[approach] += 1
```

So a vehicle standing for 100 frames contributes **one** stop, not 100. `average_stops`
(284) divides by distinct assigned vehicles seen. Stored explicitly in the Run_Log
(`stops`, `seen_vehicles`) because a transition between two frames cannot be
recomputed from the per-frame series alone.

---

<a name="5"></a>
# 5. The scoring function — the mathematical core

## 5.1 The full formula

`compute_score_weighted` (`src/traffic_metrics.py:750`). Full symbol definitions in §0.

**Step 1 — the base term** blends the two things the base paper family measures:

$$
\text{base}_i = \underbrace{\alpha}_{\text{how much to trust density}} D_i
\;+\; \underbrace{(1-\alpha)}_{\text{the rest goes to queue}} Q_i
$$

**Step 2 — the five extension terms** each take a share away from the base:

$$
\text{Score}_i = \underbrace{(1 - \gamma - \delta - \omega - \psi - \rho)}_{\text{share left for the base}}\text{base}_i
+ \gamma A_i + \delta B_i + \omega F_i + \psi X_i + \rho S_i
$$

### Every term, spelled out

| Weight | Multiplies | Which means | Reacts to |
|---|---|---|---|
| $\alpha$ | $D_i$ **density** | how crowded the approach is | **now** |
| $1-\alpha$ | $Q_i$ **queue** | how full the stop-line area is | **now** |
| $\gamma$ | $A_i$ **arrival** | vehicles present but not yet stopped | **now** |
| $\delta$ | $B_i$ **backed-up** | vehicles stopped *past* the stop line | **already happened** |
| $\omega$ | $F_i$ **forecast** | where the *count* queue is heading | **future** |
| $\psi$ | $X_i$ **reach** | how far back the queue stretches | **now, spatially** |
| $\rho$ | $S_i$ **risk** | where the *spatial* occupancy is heading | **future** |

Reading the whole thing in one sentence:

> *"Score each approach on how crowded it is and how queued it is; then hand some of
> that decision over to how far the queue physically stretches, whether it has already
> overflowed, and where it is heading next."*

### The important pairing

| | Reacts to the present | Predicts the future |
|---|---|---|
| **Count-based** (saturates) | $Q_i$ | $F_i$ |
| **Spatial** (does not saturate) | $X_i$ | $S_i$ |

That 2×2 is the whole design. $F_i$ and $S_i$ use the **same prediction machinery**;
they differ only in which row of the table they project. §10.4 shows that this single
difference is worth +20.6% throughput.

## 5.2 Why it is a *convex* combination — and why that matters

Every term lies in 0..1, every coefficient is ≥0, and the coefficients **sum to
exactly 1**. Therefore `Score ∈ [0,1]` for any admissible weights.

This is not cosmetic. The green-time bands are keyed to score thresholds
(0.0→30 s, 0.3→45 s, 0.6→60 s). If a score could exceed 1 or go negative, band lookup
would be undefined. So convexity is enforced at three levels:

1. **`config._validate`** rejects `γ+δ+ω+ψ+ρ > 1` at load time
2. **`compute_score_weighted`** re-checks, because it is also callable directly
3. **`ApproachMetrics.__post_init__`** guarantees each term is already in 0..1

**Endpoint behaviour (why `α` is interpretable):**
- `α = 1` → `Score = D` exactly → the Raza density-only policy
- `α = 0` → `Score = Q` exactly → pure queue priority
- all extension weights 0 → reduces exactly to `compute_score`

That last property is what makes every extension provably inert when off, and it is
what the default-off design rests on.

## 5.3 Monotonicity

Both base coefficients are non-negative, so the score is non-decreasing in density
(when `α>0`) and non-decreasing in queue (when `α<1`). Combined with bands that are
non-decreasing in score, **green time is monotone non-decreasing in demand**. Property-
tested in `tests/test_traffic_metrics_properties.py`.

---

<a name="6"></a>
# 6. The control layer

`src/signal_controller.py` (984 lines). Three parts: the controllers decide *which*
approach; `PhaseSequencer` owns *timing and safety*; the split is what lets one
sequencer serve both controllers and be tested with no video at all.

## 6.1 The safety invariants — and why they are unfalsifiable

`PhaseSequencer` (630) stores **one** triple:
`(active_approach, active_state, phase_end_frame)`, and *derives* the four-element
signal vector from it (`signal_states`, 748):

```python
return {name: self._active_state if name == self._active_approach else SignalState.RED
        for name in APPROACH_ORDER}
```

**There is no per-approach state to fall out of agreement.** So "exactly one approach
green, the other three red" is not *checked* — it is **structurally impossible to
violate**. A run starts with `active_approach = None`, which reads as four REDs.

**Legal transitions only.** `LEGAL_TRANSITIONS` is `{RED→GREEN, GREEN→YELLOW,
YELLOW→RED}`. Every state change routes through `_transition` (1033), which raises on
any other edge. Nothing public sets a state directly.

Note `finalize` (819) deliberately does **not** force the signal to RED at end of run:
GREEN→RED is not a legal edge, and inventing one to tidy up would be exactly the hidden
transition the invariant forbids.

**Durations are frames, not seconds.** `duration_to_frames` (87) is
`max(1, round(seconds × frame_rate))` — the floor of one frame means a green shorter
than a frame interval still produces an observable phase.

**No all-red gap between cycles.** When a yellow expires, the next cycle's green begins
**in the same tick** (`tick`, 762). Without that, the frame between two cycles would read
all-RED and break the one-green invariant.

## 6.2 `FixedTimeController` (259) — the baseline

Round-robin North→East→South→West, constant 30 s. `select` accepts `scores` and
**ignores it entirely** (`del scores`). That is the point: its plan is a function of
cycle number alone, so two runs over the same footage produce identical sequences
whatever the traffic did. `FIXED_GREEN_TIME` is a module constant, not a config field —
letting it vary would make the two controllers' metrics incomparable across runs.

## 6.3 `AdaptiveController` (311) — decision order

`select` (408) resolves in a strict priority order, and the order is a safety property:

```
1. Starvation override   _starved_approach (598)
     any approach with cycles_waited >= starvation_limit wins, ignoring scores
2. Switching margin      _apply_switching_margin (561)
     a challenger must beat the incumbent by `switching_margin` to take green
3. Highest score         _highest_scoring_approach (546)
     ties -> greatest cycles_waited -> APPROACH_ORDER
```

**Why starvation is checked first.** If the switching margin ran first it could pin an
incumbent indefinitely and break the starvation bound. Placing starvation first means
the fairness guarantee holds **regardless of how large the margin is** — there is a
test for exactly this (`test_e4_margin_does_not_defeat_starvation_prevention`) driving
`switching_margin=1.0`, the most adversarial value.

**Counter update happens last** (`_advance_counters`, 621), after the decision. A
counter incremented before selection would let an approach's own wait influence the
cycle it is currently being considered for.

## 6.4 Green-time computation — three paths, one bound

`_green_time_for_selection` (454):

| Path | Formula | Code |
|---|---|---|
| Score bands (default) | band with greatest `min_score ≤ score` | `green_time_for`:505 |
| Discharge-limited | `demand / saturation_flow + lost_time` | `discharge_green_time`:473 |
| Rate-limited (E4) | bounded change vs previous cycle | `_rate_limited_green`:522 |

**All three end in `_bounded_green` (540)**, which clamps to
`[min_green_time, max_green_time]`. So the green-time range guarantee holds on every
path, including under gap-out resizing.

The **lost-time term** in `discharge_green_time` comes from Li et al. Eq. 7. My first
version omitted it and the measurement showed why that was wrong: greens collapsed to
the floor and ~23% of the run went on intergreen, because a short green looked free
when it was not.

## 6.5 Gap-out and extension (`_apply_gap_out`, 963)

Applied *before* the expiry check in `tick`, so it reshapes the active green:

- **Gap out:** queue cleared and minimum green served → bring the end frame forward
- **Extend:** queue persists and green is expiring → push the end frame out, capped at
  maximum green

`_resize_green` (1005) updates the `Phase` record too, so the log stays truthful about
the *realised* duration rather than the assigned one.

## 6.6 Over-saturation measurement (`_finalize_green_metrics`, 1012)

At the GREEN→YELLOW edge, records on the `Phase`:

```
queue_start   PCE present when green began
queue_end     PCE still present when it ended  (-1 = unknown)
cleared       queue_end <= epsilon
oversaturation = 0 if cleared else queue_end / saturation_flow_rate
```

This is Li et al.'s $\eta = u - \phi$ measured from observation rather than predicted
from a model. `queue_end = -1` distinguishes *unknown* from a genuine *zero* — a run
with no demand vector is not the same as a run whose queue cleared.

---

<a name="7"></a>
# 7. The two IEEE TITS papers: full limitation analysis

Complete catalogue in `report/paper_limitations_analysis.md`. Summary here.

## 7.1 Li, Lu & Wang 2025 — DOI 10.1109/TITS.2025.3616119

**Method.** Arterial coordination over 5 intersections on NEMA ring-barrier phasing.
Two objectives: minimise total over-saturation and total stops. Decision variables:
phase splits, offsets, sequences. Queue treated as a **variable**, estimated from a
triangular fundamental diagram. Green bands split into saturation-flow and free-flow
types. MINLP → MILP via piecewise McCormick envelopes, solved with GUROBI 10.0.

**Their central formula (Eq. 7):**
$$u_{l,k} = \frac{\varepsilon_{l,k}}{W_{l,k}} + \frac{\varepsilon_{l,k}}{V^f_{l,k}} + T_{loss}$$

Over-saturated iff $\phi < u$; level of over-saturation $\eta = u - \phi$.

**Limitations extracted:**

| # | Limitation | Type |
|---|---|---|
| A-S1 | Uniform arrival assumed within each stream | stated |
| A-S2 | CV penetration assumed uniform | stated |
| A-S3 | Uni-modal — no vehicle-type differences | stated |
| A-S6 | Queue ≠ stops once over-saturated | stated |
| A-S8 | Beyond ~6 intersections needs partitioning | stated |
| A-S11 | **Adaptive control is future work** | stated |
| A-D1 | **Fixed-time: solved offline, then held** | derived |
| A-D2 | **Needs connected-vehicle trajectories to exist** | derived |
| A-D3 | Needs GUROBI/CPLEX, ~90–190 s per solve | derived |
| A-D4 | **Needs a corridor — meaningless at one junction** | derived |
| A-D5 | Metric queue length assumes constant headway (6 m) and length (3.5 m) | derived |
| A-D6 | Delay is *measured* but never *optimised* | derived |
| A-D7 | One hyperparameter survives — cycle length, from SYNCHRO | derived |
| A-D10 | Simulation only; benchmarks tuned by unattainable sensitivity analysis | derived |

## 7.2 Wei, Ampountolas, Hirrle & Wang 2025 — DOI 10.1109/TITS.2025.3568869

**Method.** Hierarchical MPC (MPC-Q). Network layer: QP over segment green fractions,
minimising travel time **plus a penalty on decision variation** $\Delta b$. Local layer:
NLP per intersection tracking that reference, splitting green by turn-level queue.
Built on a Link Transmission Model with turn-level queue transmission.

**Their key modelling argument:** standard LTM tracks aggregate inflow/outflow, but
*accumulation is not a queue* — under congestion it mixes queued vehicles with vehicles
still moving upstream. And a constant jam density cannot represent a queue whose density
varies between critical and jam. So:

$$\rho^{que}_\mu = \rho^c_\mu + \frac{q^c_\mu - q^{out}_\mu}{q^c_\mu}(\rho^{jam}_\mu - \rho^c_\mu), \qquad L_q = \frac{N^{que} - N^{out}}{\rho^{que}}$$

Discharge constraint (Eq. 16): $N^{out,des} = \min(N^{out} + q^s b \Delta t,\; N^{out,max})$

**Limitations extracted:**

| # | Limitation | Type |
|---|---|---|
| B-S1 | **Multimodal traffic ignored** | stated |
| B-S2 | Robustness to demand-prediction error needs work | stated |
| B-S3 | Incidents / moving bottlenecks not modelled | stated |
| B-S4 | Ineffective under gridlock | stated |
| B-D1 | **Assumes a state estimation and prediction module — never builds it** | derived |
| B-D2 | Turning rates assumed known | derived |
| B-D3 | FD parameters assumed known per movement | derived |
| B-D4 | Simulation only (MATLAB, synthetic networks) | derived |
| B-D5 | Output is green *fractions*, not an implementable plan (no intergreen, no min green) | derived |
| B-D7 | Needs QP + NLP solvers | derived |
| B-D8 | 10 s sampling — sub-cycle surges invisible | derived |
| B-D10 | Measurement error never propagated | derived |

## 7.3 The insight that made the project possible

**Neither paper contains any computer vision.** Both assume the measurement exists:

- Li et al. need saturation flow, free-flow speed, and turning proportions **from CV
  trajectory data**
- Wei et al. §II-A states plainly that the layers receive inflow/outflow and demand
  predictions **from a state estimation and prediction module** — which they never build

**This project is that module.** That is why the correct response was to take their
*ideas*, not their solvers. And note their shared first limitation (multimodal traffic,
A-S3 / B-S1) is answered by the base paper's PCE weighting.

---

<a name="8"></a>
# 8. What I built, why, and where

All **config-gated, default-off**, so the base system is provably unchanged.

| # | Name | Answers | Formula | Code |
|---|---|---|---|---|
| E1 | PCE-weighted queue | A-S3, B-S1 | `Σ PCE over queueing / capacity` | `_measure`:419 |
| E2 | Spillback pressure | Wei's accumulation≠queue critique | `PCE(stopped−queueing) / saturation` | `_measure`:419 |
| E3 | Discharge-limited green | A-D6, Raza's arbitrary bands | `demand/q_s + T_loss` | `discharge_green_time`:473 |
| E4 | Control-plan stability | Wei Eq.19–20 | switching margin + rate limit | `_apply_switching_margin`:561, `_rate_limited_green`:522 |
| E5 | Over-saturation measurement | A-S6, Li Eq.7–10 | `η = queue_end / q_s` | `_finalize_green_metrics`:1012 |
| E6 | Gap-out / extension | A-D1, A-S11 | online `φ ≥ u` test | `_apply_gap_out`:963 |
| E7 | Stop counting | Li's headline MOE | moving→stopped transitions | `update`:298 |
| E8 | Queue forecast | **Wei's predictive thesis** | `clamp(Q + slope·H)` | `_project`:665 |
| **E9** | **Spatial queue reach** | **own — the saturation blind spot** | `max t(p)` over stopped | `ApproachAxis`:100 |
| **S** | **Spillback risk** | **own — forward-looking storage** | `clamp(reach + d(reach)/dt·H)` | `_project_risk`:640 |

## 8.1 Measured outcome per mechanism (busy clip)

| Mechanism | Effect | Verdict |
|---|---|---|
| E1 | identical run | **inert** — fleet is ~all cars |
| E2 | waiting 1.69 → 1.53 s (−9%) | helps |
| E3 (original) | throughput −37% | **harmful** at this scale |
| E4 | waiting −5% **and** throughput +3% | **only both-axis win** |
| E5 | revealed 3/3 greens ended unserved | diagnostic |
| E6 | over-saturation 3/3 → 1/6, throughput cost | mixed |
| E7 | 1066 stops, identical across controllers | **open-loop bound exposed** |
| E8 | see §10 — **inert at ω=0.3** | negative result |
| E9 | throughput +up to 19%, over-sat 3/3→1/3 | helps |
| S | **throughput +20.6%, served +20.6%** | helps |

Reporting E1, E3 and E8 as failures is deliberate. A study with no negative results is
not a study.

---

<a name="9"></a>
# 9. My own contributions in full detail

## 9.1 E9 — Spatial Queue Reach

### The problem, stated precisely

The queue term was:

$$Q_i = \mathrm{clamp}\!\left(\frac{\text{count}_i}{\text{capacity}_i}\right)$$

Three defects:

1. **Saturation.** Once `count ≥ capacity`, $Q_i = 1.0$ and stops responding. The
   measure dies exactly as conditions worsen. **E5 proved this empirically**: the base
   controller ended **3 of 3** green phases with the queue region still occupied, worst
   6.0 s short — while the queue term reported the same value throughout.
2. **Position discarded.** Three vehicles nose-to-tail at the stop line and three strung
   along the approach give the **identical** measurement.
3. **The divisor is a guess.** `capacity` was eyeballed at 3–4 per approach. Li et al.
   name exactly this as their fourth criticism of prior work.

### Why neither paper solves it for me

Li et al. *do* measure queue length — in **metres**, via a fundamental diagram and an
assumed 6 m space headway. That needs pixel-to-metre calibration I don't have, and it
replaces one guessed constant with another. Wei et al. track cumulative counts and never
represent position at all.

### The construction

The information was **already in the config file, unused**. Each approach has an
`roi_polygon` (road area) and a `queue_region` (stop-line area). The vector between
their centroids *is* the direction of travel.

Visually, for one approach:

```
        far edge of ROI                    stop line / junction
    t = 1                                              t = 0
        |                                                  |
        |<----------------- L_i (pixels) ----------------->|
        |                                                  |
        |    [car]        [car]  [car][car][car]           |
        |      ^                                           |
        |      |                                        [ o_i ]  <- axis origin
        |   furthest stopped vehicle                       ^        (Queue_Region
        |   -> this one sets  reach_i                      |         centroid)
        |                                                  |
        +--------------------[ c_i ]-----------------------+
                          ROI centroid
                                |
              u-hat_i  <--------+--------> u_i
              (upstream,        (downstream, direction of travel)
               measuring
               direction)
```

### Step 1 — find the direction of travel (`ApproachAxis.__init__`, `src/lane_analysis.py:133`)

$$\mathbf{u}_i = \frac{\mathbf{o}_i - \mathbf{c}_i}{\lVert\mathbf{o}_i - \mathbf{c}_i\rVert} \;\text{(downstream)}, \qquad \hat{\mathbf{u}}_i = -\mathbf{u}_i \;\text{(upstream)}$$

- $\mathbf{o}_i - \mathbf{c}_i$ — the arrow from the middle of the road area to the
  stop-line area. Since the Queue_Region *is* the stop line, this arrow points the way
  traffic goes.
- Dividing by $\lVert\cdot\rVert$ (its own length) makes it **length 1** — a pure
  direction with no magnitude. That is what the hat means.
- Negating it gives $\hat{\mathbf{u}}_i$, pointing **back up the queue** — the direction
  we want to measure along.

### Step 2 — find how long the approach is (same method)

$$L_i = \max\Big(\max_{\mathbf{v}\in \Omega_i}\big[(\mathbf{v}-\mathbf{o}_i)\cdot\hat{\mathbf{u}}_i\big],\; 0\Big)$$

- For every **corner** $\mathbf{v}$ of the ROI polygon $\Omega_i$, measure how far
  upstream it lies.
- Take the largest → that is the far edge of the visible road, in pixels.
- The outer $\max(\cdot,\,0)$ guards a degenerate polygon: $L_i$ can never go negative.

**$L_i$ is the divisor, and it is computed — not guessed.** This is the whole reason the
measure adds no tunable parameter.

### Step 3 — place any vehicle on that axis (`ApproachAxis.fraction`, line 165)

$$t_i(\mathbf{p}) = \mathrm{clamp}\!\left(\frac{(\mathbf{p}-\mathbf{o}_i)\cdot\hat{\mathbf{u}}_i}{L_i},\,0,\,1\right)$$

- Numerator: how far upstream this vehicle is, in pixels.
- Divide by $L_i$: convert pixels into a **fraction of the visible approach**.
- $\mathrm{clamp}$: a vehicle already in the junction gives a negative numerator → 0;
  anything beyond the ROI → 1.

So $t = 0$ means *at the stop line*, $t = 1$ means *at the far edge of what the camera
can see*.

### Step 4 — the reach is the furthest stopped vehicle

$$\boxed{\;X_i = \max_{j\in \mathcal{S}_i} t_i(\mathbf{p}_j),\qquad X_i = 0 \text{ if } \mathcal{S}_i=\varnothing\;}$$

- $\mathcal{S}_i$ — the set of **stopped** vehicles on this approach (§0.1).
- $\mathbf{p}_j$ — vehicle $j$'s reference point.
- Take the **max**, because the queue reaches as far back as its furthest stopped member.
- Empty set → 0: no stopped traffic means no queue.

**In one sentence:** *"$X_i$ is the fraction of the visible approach that stopped
traffic stretches across."*

### Why *stopped* rather than *queueing* — the key design choice

`queue_count` only sees inside the Queue_Region. The reach is taken over **all stopped
vehicles anywhere in the ROI**. So when a queue grows past the stop-line region, the
count cannot see it but the reach extends. **That is precisely the blind spot.**

### Verified properties

| Property | Evidence |
|---|---|
| Bounded 0..1 by construction | clamp + max over clamped values |
| Stop line reads exactly 0.000 | verified on all four real approaches |
| Furthest ROI vertex reads exactly 1.00 | verified on all four |
| No new tunable | $L_i$ computed from the polygon |
| Calibration-free | fraction is relative to the approach's own extent |
| Degrades safely | coincident centroids → $L=0$ → inert, not an exception |
| Plain floats, not NumPy | so the JSON log stays serialisable |

### The demonstration that captures the whole argument

`test_e9_reach_grows_where_the_count_is_blind`:

| Scenario | `queue_length` | `queue_reach` |
|---|---|---|
| One vehicle stopped at the stop line | 1 | 0.00 |
| Plus one stopped at the far end of the approach | **1 (unchanged)** | **1.00** |

**Measured on real footage:** the reach reported more congestion than the count in
**28.2%** of frame-approach observations on the busy clip (12.6% on the lighter clip).
If it merely restated the count, that figure would be 0%.

## 9.2 Spillback Risk — the forward-looking term

$$\boxed{\;S_i = \mathrm{clamp}\!\left(\underbrace{X_i}_{\text{occupancy now}} + \underbrace{\frac{dX_i}{dt}}_{\text{how fast it grows}} \cdot \underbrace{H_{risk}}_{\text{look-ahead}}\right)\;}$$

`_project_risk` (`src/traffic_metrics.py:640`).

### Every term

| Term | Means | Units |
|---|---|---|
| $X_i$ | queue reach **right now** — fraction of the approach occupied | fraction (0..1) |
| $\dfrac{dX_i}{dt}$ | **rate** the queue is extending backwards. Positive = growing, negative = clearing, zero = steady | fraction **per second** |
| $H_{risk}$ | how many seconds ahead to look (`risk_horizon_seconds`, default 5) | seconds |
| $\mathrm{clamp}$ | keeps the answer a valid fraction | — |

Note the units multiply out correctly: $\text{fraction/second} \times \text{seconds} =
\text{fraction}$, so it can be added to $X_i$.

**This is just "distance = position + speed × time"** applied to a queue's back end
instead of a moving object.

$\dfrac{dX_i}{dt}$ comes from a least-squares straight-line fit over the last $W$ frames
(`_slope_per_second`, line 670) — using a fit rather than a single frame-to-frame
difference so detector jitter does not read as growth.

### Worked examples

| Situation | $X_i$ | $dX_i/dt$ | $S_i = \mathrm{clamp}(X + \text{rate}\times5)$ | Reading |
|---|---|---|---|---|
| Steady queue | 0.40 | 0.00 | $0.40 + 0 = $ **0.40** | no change expected |
| Growing | 0.28 | +0.20 /s | $0.28 + 1.00 = 1.28 \to$ **1.00** | will fill the approach |
| Clearing | 0.20 | −0.10 /s | $0.20 - 0.50 = -0.30 \to$ **0.00** | no risk |
| Nearly full, growing | 0.88 | +0.05 /s | $0.88 + 0.25 = 1.13 \to$ **1.00** | about to spill |

$S_i \to 1.0$ means the queue is projected to fill the **entire visible approach** — the
point at which it spills out of it.

Risk → 1.0 means the queue is projected to fill the **entire visible approach** — the
point at which it spills out of it.

### Why the basis is `reach` and not `normalized_queue`

**This is the single most important design decision in the project.**

`normalized_queue` saturates. Once it reads 1.0 its **derivative is zero**, so a
projection reports a *stable* approach while the queue is physically still growing. The
spatial reach keeps rising, so its derivative stays meaningful **exactly in the regime
the risk term exists to detect**.

Demonstrated directly, with `normalized_queue` pinned at 1.0 throughout:

| Reach sequence | reach | risk |
|---|---|---|
| growing (extending back) | 0.28 | **1.00** |
| steady | 0.40 | 0.40 |
| clearing | 0.20 | **0.00** |
| nearly full, still growing | 0.88 | **1.00** |

A count-based projection cannot separate row 1 from row 3. The risk term reports
1.00 vs 0.00. Pinned as `test_risk_works_where_the_count_based_projection_is_blind`.

## 9.3 Honest novelty positioning

I will not claim this is unpublished worldwide. I checked **four** candidate ideas and
**rejected three**:

| Idea considered | Prior art found → rejected |
|---|---|
| Per-vehicle worst-case delay / fairness | DRL fair-signal-control papers; **FairSCOSCA** |
| Perception-uncertainty-aware control | **UCATSC** (2026), vision-based partial observability |
| Accumulated service deficit / integral control | **Deficit Round Robin**; ALINEA; and **max-pressure — a baseline in Wei's own paper** |

**Honest claim:** vision-based queue-length estimation is established, and Li et al.
estimate queue length in metres. What is mine is the **calibration-free,
parameter-free** formulation deriving spatial extent from the ROI geometry the detector
already requires, using it as a **control input**, and projecting it forward as a risk —
all possible because this system has per-vehicle tracking and theirs do not.

Neither TITS paper meets an "unpublished worldwide" bar either: Wei combined existing
MPC + LTM; Li combined existing green bands + queue estimation. Novelty here is
**specific construction**.

## 9.4 Validating the axis — a defect I found in my own calibration

Everything in 9.1 and 9.2 rests on one vector being correct: $\hat u_i$, the unit vector
pointing **upstream**. If it is reversed, a nearly empty approach reports as almost full and
the risk term is inverted. I therefore tested that assumption instead of trusting it, and it
failed. The full write-up is `report/axis_validation.md`; this is the summary.

### The construction, and its hidden precondition

$$\hat u_i \;=\; -\,\mathrm{normalize}\!\left(\mathrm{centroid}(\text{Queue\_Region}_i) - \mathrm{centroid}(\text{ROI}_i)\right)$$

The reasoning is sound *provided* the Queue_Region is drawn as a **narrow strip across the
stop line**. Drawn instead as a uniformly inset copy of the whole ROI, the two centroids
nearly coincide and the subtraction returns a direction set by a few pixels of drawing
noise. That precondition was never stated, and I had violated it.

### The diagnostic

`ApproachAxis.conditioning` (`src/lane_analysis.py`) measures the centroid separation as a
fraction of the ROI diagonal, with `MIN_CONDITIONING = 0.10`. Measured on
`config/bellevue_116th.json`:

| Approach | Queue_Region as % of ROI | Centroid separation | Conditioning | Verdict |
|---|---|---|---|---|
| North | 25.6% — a proper strip | 132.6 px | 0.182 | usable |
| East | 56.1% — inset copy | 11.2 px | 0.026 | unusable |
| South | 51.7% — inset copy | 18.4 px | 0.029 | unusable |
| West | 48.8% — inset copy | **2.9 px** | **0.006** | unusable |

`config/default.json` is correct on all four (24.6–27.2% area, 94–159 px), which localises
this to one junction file rather than to the formulation. An empirical flow check then found
**East's axis genuinely reversed**: over 1,200 frames, 1 vehicle moved with decreasing $t$
against 9 with increasing $t$.

### The fix: measure the direction instead of inferring it

Redrawing polygons by hand would leave me arguing I drew them better the second time. The
direction is instead made a **measured** quantity, which is also the more computer-vision
answer — the tracker already carries the evidence. `AxisDirectionEstimator` estimates it
from where vehicles **enter** each ROI versus where they **leave** it:

$$\hat d_i = \mathrm{normalize}\big(\overline{p^{\text{last}}} - \overline{p^{\text{first}}}\big), \qquad \hat u_i = -\hat d_i$$

Confidence is reported as directional **agreement** $|2p-1|$, where $p$ is the fraction of
tracks whose displacement projects positively onto $\hat d_i$. Agreement is the correct
measure here rather than the centroid separation, because this footage yields fragmented
tracks (64 on the busiest approach across 3,220 frames), which shrinks the separation
without making the direction less certain.

```bash
python -m src.main calibrate-axes --video videos/bellevue_116th_busy.mp4 \
    --config config/bellevue_116th.json --no-display \
    --write config/bellevue_116th_calibrated.json
```

| Approach | Tracks | Agreement | Measured downstream | vs. centroid geometry |
|---|---|---|---|---|
| North | 64 | 0.531 | (−0.998, +0.065) | agree (cos +0.98) |
| **East** | 35 | 0.543 | (+0.359, −0.933) | **REVERSED (cos −0.74)** |
| South | 29 | 0.103 | — | below threshold, **not written** |
| West | 24 | 0.667 | (−0.950, +0.312) | agree (cos +0.71) |

Two deliberate choices. East's reversal was confirmed **four independent ways** (conditioning
diagnostic, frame-by-frame flow check, per-vehicle heading voting, entry/exit estimator).
And **South was left on the flagged fallback**, because agreement of 0.103 means this clip
cannot determine it — writing a low-confidence direction would swap an inspectable fallback
for a silent guess. Trustworthy axes went from **1 of 4 to 3 of 4**, with the fourth
honestly declined.

### The robustness result — the part that matters

Re-running the ladder on corrected geometry, the calibration demonstrably changed the
measurements:

| Approach | Frames where reach changed | Frames where risk changed | Max Δreach |
|---|---|---|---|
| North | 3,196 / 3,220 | 2,024 | 0.242 |
| East | 148 | 317 | 0.697 |
| South | 0 (fallback, by design) | 0 | 0.000 |
| West | 1,910 | 1,359 | 0.402 |

…and **every stage reproduced its metrics exactly**, including S3 → S4 at +20.6% and
over-saturated greens 3/3 → 2/3. The measure moved on 99% of frames; the conclusion did not
move at all.

The reason is traceable. S3 and S4 differ in one decision, at frame 2430, and S4 selects
**North — the one approach that was correctly calibrated all along**, precisely because its
count-based queue looked mild ($Q = 0.25$) while its queue physically extended far back
($X = 0.71$). The mechanism fired for the claimed reason on the approach where measurement
was valid, which is why repairing the other three changed the inputs without changing the
outcome.

### What this is worth saying out loud

A defect in the foundation of my own contribution, found by my own diagnostic, with the
headline result then shown to survive it, is stronger evidence than never having tested the
assumption. It is also why the metrics engine now raises a `RuntimeWarning` naming any
untrustworthy axis whenever a spatial measure is enabled: an inverted axis produces
perfectly valid-looking numbers, so **silence was the real hazard**.

Pinned by 39 tests in `tests/test_axis_calibration.py`, including an executable statement of
the core claim (spatial reach still separates cases a saturated count cannot) and a test
asserting the current state of the shipped geometry, so a future edit cannot quietly
reintroduce the defect.

---

<a name="ds"></a>
# 10. The dataset — provenance, preprocessing, and why these clips

This section exists because "where is your data from?" is the first question a computer
vision examiner asks, and because the honest answer includes a real constraint on what the
results can claim.

## 10.1 Source

| Property | Value |
|---|---|
| Dataset | **Bellevue Traffic Video Dataset** |
| Publisher | City of Bellevue, Washington, USA |
| URL | https://github.com/City-of-Bellevue/TrafficVideoDataset |
| Licence / terms | released by the City of Bellevue for research use, provided "as is" |
| Junction | 116th Ave NE / NE 12th St |
| Capture | pole-mounted fixed traffic camera, September 2017 |
| Native resolution | 1280 x 720 |

This is a **public, citable, real-world** dataset, not footage I collected or synthesised.
That matters in two directions: the traffic behaviour is genuine rather than simulated,
which is the point of the project; and I cannot re-record it under different conditions,
which is the source of most limitations in section 14.

Provenance and preprocessing are recorded in machine-readable form in
`data/annotations/videos.json`, which is the file the evaluation reads to decide what to
run on.

## 10.2 Preprocessing, and why it was necessary

The raw hourly recordings **drop frames**, so their container frame rate is misleading. The
08:08 recording, for instance, reports 27.6452 fps. That is not a cosmetic problem:
waiting time in this system accumulates as `dt = 1 / frame_rate`, so a variable or wrong
frame rate directly corrupts the primary metric.

The preprocessing was therefore:

1. Measure inter-frame intervals across each hourly recording.
2. Locate the longest **gap-free** run, verified at 33.33 ms ± 0.01 per frame, i.e. true
   30 fps.
3. Cut a window from inside that run and re-encode at a **constant** 30 fps.

**No frames were duplicated and none were interpolated.** The clips are a subset of real
frames, not a resampling.

## 10.3 The evaluation clips

Three clips are registered in `data/annotations/videos.json`, all from the **same fixed
camera** — which is required, because one `Config`, and therefore one set of ROI polygons,
is applied to every video in the set.

| Clip | Role | Duration | Frames | fps | Use |
|---|---|---|---|---|---|
| `bellevue_116th_dev.mp4` | development | 240 s | 7,199 | 30 | tuning, sanity checks |
| `bellevue_116th_final.mp4` | final | 240 s | 7,200 | 30 | held-out reporting |
| `bellevue_116th_busy.mp4` | final | 107 s | 3,220 | 30 | the congested case; headline ablation |

`videos/_demo_short.mp4` (20 s) exists only for rendering a short overlay demo and is not
part of any measurement.

## 10.4 Why the headline ablation uses one clip — and why that is a real limitation

The staged S0–S4 ablation was run on `bellevue_116th_busy.mp4` because it is the only clip
containing **sustained over-saturation**, which is the condition the contribution addresses:
if no approach ever runs short of storage, a storage-risk term has nothing to act on and the
comparison would be vacuous.

That choice has a cost I state plainly. At `min_green_time = 30 s` plus a 3 s yellow, a
107-second clip admits only **three green phases**. S3 and S4 therefore differ in **one
selection decision**. The +20.6% throughput difference is correctly measured and correctly
attributed, but one decision is not evidence of an average effect.

This is addressed rather than excused: `run_multiclip_ablation.py` re-runs the decisive S3
vs S4 comparison on both 240-second clips, and `multiclip_table.py` reports whether the
finding reproduces. Results are in section 11.

## 10.5 Footage that exists but is deliberately unused

`videos/_candidates/` holds five further clips from the same junction. They are **excluded
on methodological grounds**, not overlooked:

| Candidate | Duration | Container fps | Why excluded |
|---|---|---|---|
| `b116_0908.mp4` | 930 s | 28.0 | frame-drop contaminated |
| `b116_1208.mp4` | 109 s | 30.0 | usable; a genuine expansion candidate |
| `b116_1608.mp4` | 81 s | 20.7 | frame-drop contaminated |
| `bellevue_116th_NE12th_0808.mp4` | 603 s | 27.6 | frame-drop contaminated |
| `bellevue_116th_NE12th_1708.mp4` | 427 s | 17.4 | frame-drop contaminated |

Four of the five report a frame rate well below 30 fps because of dropped frames. Feeding
them in directly would silently corrupt waiting time, so each would first need the same
gap-analysis and re-cutting described in 10.2. `b116_1208.mp4` already reads a clean
30.0 fps and is the obvious next clip to add.

Total footage on disk is about 46 minutes; the **validated, constant-rate subset used for
measurement is 587 seconds** across three clips. The gap between those two numbers is
preprocessing cost, and it is the honest reason the evaluation is not larger.

## 10.6 What the dataset does *not* let me claim

The footage is **pre-recorded**, so vehicles cannot respond to the simulated signal. This is
the single most important constraint in the project and is treated at length in section 14.1.
It is why queue length and stop counts are *controller-invariant* here (verified: 0.8276 and
1066 identically across S3 and S4), and why the reported gains are confined to
signal-gated quantities — throughput, vehicles served, waiting, over-saturation — rather
than claimed as real-world queue reduction.

---

<a name="10"></a>
# 11. The staged ablation — experimental design and results

## 11.1 Design

Each stage adds **exactly one** component. Geometry, PCE weights, detector, tracker,
green bands, yellow and starvation limit are **identical** throughout, so the difference
between consecutive rows isolates that component.

| Stage | Score composition | Config |
|---|---|---|
| S0 | *(fixed-time, no measurement used)* | `--controller fixed` |
| S1 | $D$ | `ablation_s1_raza.json` |
| S2 | $0.5D + 0.5Q$ | `ablation_s2_queue.json` |
| S3 | $0.7(0.5D + 0.5Q) + 0.3G$ | `ablation_s3_prediction.json` |
| S4 | $0.4(0.5D + 0.5Q) + 0.3G + 0.3S$ | `ablation_s4_proposed.json` |

Generated by `make_ablation_configs.py`; table rebuilt by `ablation_table.py`.

**Integrity detail:** `ablation_table.py`'s `stage_of` classifies each run by reading
the **resolved config back out of the log**, matching **exact** weights (`abs(w−0.3) <
1e-9`), not just "enabled". This caught and rejected an older run with
`forecast_weight = 0.4` that would otherwise have been mislabelled as S3. A row cannot
be silently wrong.

## 11.2 Results — `bellevue_116th_busy.mp4`, 107 s, 30 fps

| Stage | D | Q | G | S | Waiting (s) | Throughput | Served | Over-sat greens |
|---|---|---|---|---|---|---|---|---|
| **S0** fixed-time | – | – | – | – | 2.36 | 73.8 | 132 | 4/4 |
| **S1** Raza baseline | ✓ | – | – | – | 2.25 | **105.7** | **189** | 2/3 |
| **S2** + queue | ✓ | ✓ | – | – | **1.69** | 73.2 | 131 | 3/3 |
| **S3** + prediction | ✓ | ✓ | ✓ | – | **1.69** | 73.2 | 131 | 3/3 |
| **S4** PROPOSED | ✓ | ✓ | ✓ | ✓ | 2.14 | 88.3 | 158 | **2/3** |

`run_id`s:
```
S0  bellevue_116th_busy__fixed__alpha1p00__20260907-094022
S1  bellevue_116th_busy__adaptive__alpha1p00__20260907-095151
S2  bellevue_116th_busy__adaptive__alpha0p50__20260907-095726
S3  bellevue_116th_busy__adaptive__alpha0p50__20260907-102844
S4  bellevue_116th_busy__adaptive__alpha0p50__20260907-103624
```

Green phase sequences — the mechanism behind each row:

| Stage | GREEN phases |
|---|---|
| S0 | North/30, East/30, South/30, West/30 |
| S1 | North/30, North/45, **South**/45 |
| S2 | North/30, North/45, **West**/45 |
| S3 | North/30, North/45, West/45 *(identical to S2)* |
| S4 | North/30, North/45, **North**/45 |

## 11.3 What each step proves

**S0 → S1.** Adaptive beats fixed on **both** axes: throughput +43%, waiting down,
over-saturated greens 4/4 → 2/3. **Fixed-time is Pareto-dominated** — no argument for it.

**S1 → S2.** The queue term cuts waiting **25%** (2.25 → 1.69 s), the best waiting figure
in the table, at a throughput cost. The mechanism is visible in the sequence: it serves
the standing-queue West that density-only skips in favour of higher-flow South.

**S2 → S3. Prediction alone was INERT — bit-for-bit identical.** Same waiting, same
throughput, same vehicles served, same green sequence. The forecast changed **no
decision**.

**This is the most informative row in the table**, because it is what the risk design
predicted. The forecast projects `normalized_queue`, which saturates; once saturated its
derivative is zero, so the projection reports stability while the queue still grows.

**S3 → S4.** Replacing the saturating basis with the spatial one:

| | S3 | S4 | Change |
|---|---|---|---|
| Throughput | 73.2 | **88.3** | **+20.6%** |
| Served | 131 | **158** | **+20.6%** |
| Over-sat greens | 3/3 | **2/3** | −1 |
| Waiting | 1.69 s | 2.14 s | +0.45 s |

## 11.4 The controlled comparison that justifies the design

S3 and S4 differ in **exactly one** thing — the projected quantity:

| | Projects | Saturates? | Effect on control |
|---|---|---|---|
| S3 | count-based `normalized_queue` | **Yes**, at configured capacity | **none — identical run** |
| S4 | spatial `queue_reach` | **No**, bounded by ROI geometry | +20.6% throughput, +20.6% served |

Same predictor, same window, same horizon machinery, same weight (0.3). **Only the basis
differs.** This is a controlled experiment on one design decision, and it comes out in
favour of the spatial basis. The risk signal exceeded current occupancy in **28.6%** of
observations.

## 11.5 Does it reproduce? Multi-clip test — and the condition it revealed

The single-decision weakness in 11.6 is not something to argue around, so I re-ran the decisive
S3 vs S4 comparison on the 240-second development clip, which carries **7 green phases** instead
of 3. Full write-up: `report/multiclip_reproducibility.md`.

| Clip | Phases | Throughput S3 → S4 | Change | Decisions differing |
|---|---|---|---|---|
| busy (107 s, saturated) | 3 | 73.2 → 88.3 | **+20.6%** | **1** |
| development (240 s) | 7 | 51.5 → 51.5 | **+0.0%** | **0** |
| **final / held-out (240 s)** | 7 | 56.25 → 56.25 | **+0.0%** | **0** |

**On both 240-second clips — including the held-out one — the risk term changed nothing at all.**
Not a smaller gain: zero decisions differed and every metric is identical. The effect exists on
**1 of 3 clips** and rests on **1 changed decision in total**. Reported here prominently for the
same reason S3's inertness is: a mechanism that works only sometimes needs its *sometimes*
characterised, and that characterisation is itself the result.

### Why it was inert — diagnosed, not guessed

The term did **not** simply fail to fire:

| | development | busy |
|---|---|---|
| `spillback_risk > 0` | 25.0% of observations | 50.4% |
| Frames where any score changed S3 → S4 | **83.4%** | — |
| Frames where the **top-ranked** approach changed | **15.3%** | — |
| Green decisions changed | **0** | 1 |

It fired constantly, moved scores on 83% of frames, and flipped the leading approach on 15% —
and still changed no green. The resolution is in **what was actually deciding the greens**:

| | busy | development | final |
|---|---|---|---|
| **Starvation-driven greens** | **0 of 3** | **4 of 7** | **4 of 7** |
| **Score-driven greens** (the term's opportunities) | **3 of 3** | **3 of 7** | **3 of 7** |
| Starvation greens serving a **zero-score** approach | – | 3 of 4 | **4 of 4** |
| Frames where **every** approach scores 0 | **0%** | **15.8%** | **6.3%** |

On the held-out clip **all four** starvation greens served an approach scoring exactly `0.0` — an
empty road, given green solely because the fairness rule forbids indefinite skipping
(`_starved_approach`, `signal_controller.py:598`). Reproduce with `python diagnose_regime.py`.

**The mechanism:** on a lightly loaded junction most approaches are empty, a substantial share of
frames have *every* score at exactly zero, and in **4 of 7** greens selection is therefore driven
by the starvation guarantee rather than by the score — producing near round-robin sequences.
**A score term cannot change a decision the score is not making.** For those four the risk term
was not too weak; it was *structurally bypassed*. The other **3 per clip were score-driven**, so it
could have acted and did not.

### The honest denominator

| Clip | Score-driven greens | Decisions the term changed |
|---|---|---|
| busy (saturated) | 3 | **1** |
| development | 3 | 0 |
| final / held-out | 3 | 0 |
| **total** | **9** | **1** |

**The term changed 1 of 9 decisions where it was capable of acting.** That is the correct way to
state this result. The +20.6% is the *magnitude* of that one decision's effect on a three-phase
clip, where any single decision moves every aggregate substantially — not an average effect.

### What this licenses, and what it does not

**Defensible:** the one decision it changed is fully traced (frame 2430, §9.4) and matched the
design intent exactly — it selected the approach whose count-based queue read mild (Q = 0.25)
while its queue physically extended far back (X = 0.71). Where the term could not act, the reason
is *measured and structural* rather than unexplained. Inertness on empty approaches is arguably
correct: a storage-exhaustion term firing there would be the bug.

**Not claimed:** that the mechanism helps on average across conditions — averaging one real effect
with two structural zeros (+6.9%) would be meaningless, so no mean is quoted. Nor that it fires
reliably when needed: on the 6 score-driven greens of the two long clips it had the opportunity and
changed nothing. Nor **that it generalises** — it was inert on the held-out clip, which is the
single most important qualification in this project.

### So what IS established?

The **diagnosis**, firmly; the **effect size**, not at all:

1. The count-based queue **provably saturates** — S3 is bit-identical to S2 on every clip, so
   projecting a saturated variable is measurably useless. A reproducible negative result about the
   base paper's state representation.
2. The spatial extent **provably does not saturate** — pinned by
   `test_reach_does_not_saturate_where_a_capped_count_would`, and visible in the one decision that
   mattered (Q = 0.25 while X = 0.71).
3. When the spatial variable was allowed to decide, it decided **in the direction theory predicts**,
   with the trace confirming the mechanism rather than a coincidence.

What is unquantified is how often that matters, because this dataset holds only 107 seconds of
saturated footage. A tested measurement with a demonstrated mechanism and an unquantified effect
size is an honest place for a one-semester single-junction study to land.

## 11.6 Honest reading

- S4 is a **throughput-and-completion** improvement, not a waiting-reduction one. If
  minimum waiting is the objective, **S2 is the best row** and I say so.
- S4 does not beat S1 on throughput (88.3 vs 105.7). S1 achieves its throughput by
  concentrating green on high-flow approaches and skipping queued ones — the behaviour
  S2's queue term exists to correct.
- The busy clip holds only **3 green phases**, so one phase changing moves every aggregate.
  These rows show **direction**, not precise effect sizes.
- The mechanism is **conditional on load**, per 11.5. That is the single most important
  qualification on the headline number.

---

<a name="11"></a>
# 12. Testing strategy — how correctness is established

**762 tests across 34 modules (~12,944 lines).** More test code than source code.

## 12.1 Why the suite runs in ~60 s with no GPU

`tests/fixtures.py` (449 lines) provides `FakeDetector` and `FakeTracker` returning
scripted per-frame results, plus a synthetic clip generator writing real MP4s via
`cv2.VideoWriter`. So stages downstream of detection are tested with **no YOLO weights,
no GPU, and no real footage** — which is what makes running the suite on every change
practical.

## 12.2 Property-based tests (Hypothesis)

Generated inputs, not hand-picked examples. These catch the ordering bugs examples miss:

| File | Properties verified |
|---|---|
| `test_signal_properties.py` (440) | exactly one non-RED approach at every frame; every transition legal; green within `[min,max]`; `cycles_waited` bounded; every approach served within the starvation window; clock strictly increasing |
| `test_traffic_metrics_properties.py` (289) | all ratios in 0..1; `0 ≤ queue ≤ count`; waiting non-decreasing; score in 0..1 for any α; α=1→density, α=0→queue; monotonicity in each argument |
| `test_config_properties.py` (181) | `from_json_obj(to_json_obj(c)) == c` for generated configs |
| `test_results_store_properties.py` (164) | Run_Log round-trips within 0.001 for decimals |

The starvation-window bound is the one most worth having under generated inputs: a
hand-written example set would not find the ordering bug where counters update *before*
rather than *after* selection.

## 12.3 Invariants checked against every recorded run, not just test scenarios

Unit and property tests establish the invariants on *constructed* inputs. `verify_invariants.py`
additionally re-checks them on **every frame of every run log ever recorded**, which is a
different and harder standard: it cannot be satisfied by a suite that happens to exercise only
convenient cases.

```powershell
python verify_invariants.py
```

Latest result — **54 runs, 226,972 frames, zero violations**:

| # | Invariant | Result |
|---|---|---|
| I1 | at most one GREEN per frame | PASS |
| I2 | at most one YELLOW, never simultaneously with a GREEN | PASS |
| I3 | all signal states legal | PASS |
| I4 | no GREEN → RED without passing through YELLOW | PASS |
| I5 | realised green duration within configured `[min, max]` | PASS |
| I6 | starvation guarantee honoured (a starved approach is later served) | PASS |
| I7 | every metric finite and within `[0, 1]` — no NaN ever reached a log | PASS |
| I8 | queue length a non-negative integer | PASS |

I7 is the one worth emphasising in a viva: it is a standing guarantee that **no NaN or
out-of-range value has ever been written into a result**, checked across a quarter of a
million frames rather than asserted.

## 12.4 `tests/test_tits_extensions.py` — 1,388 lines

Every enhancement, grouped, with the **first test in each group pinning that the feature
is inert when off**. That is what licenses the claim that the baseline is unchanged.

Highlights:
- `test_e9_reach_grows_where_the_count_is_blind` — the count/reach demonstration
- `test_risk_works_where_the_count_based_projection_is_blind` — the S3/S4 rationale as
  an executable check
- `test_e4_margin_does_not_defeat_starvation_prevention` — safety under the most
  adversarial margin
- `test_e6_preserves_signal_integrity` — randomised gap-out never breaks the one-green
  invariant
- `test_e9_degenerate_geometry_is_inert_not_fatal` — coincident centroids
- 4 tests confirming legacy configs missing new keys still load

## 12.5 A bug the tests actually caught

`test_e4_margin_boundary_is_inclusive` failed because `0.6 − 0.5 = 0.09999999999999998`
in binary, making a documented *inclusive* bound behave *exclusively*. Fixed with
`SWITCHING_MARGIN_TOLERANCE = 1e-9` (`src/signal_controller.py`), sized far above
double-precision noise and far below any configurable margin. Documented in the source —
a latent bug, found by a boundary test rather than by luck.

---

<a name="12"></a>
# 13. Reproducibility and the evidence chain

## 13.1 Every number traces to a run log

`results/run_logs/{video}__{controller}__alpha{a}__{timestamp}.json` (44 files) contains:

- the **fully resolved configuration** (so a run is replayable exactly)
- **one record per frame**: per approach — count, queue length, density,
  normalized queue, arrival, spillback, **forecast**, **reach**, **risk**, queue_pce,
  score, signal state
- **phase history** with green time, selection score, starvation flag, truncation flag,
  and the E5 fields (`queue_start`, `queue_end`, `oversaturation`, `cleared`,
  `extended`, `gapped_out`)
- per-Track_ID waiting times; per-approach stop counts; `seen_vehicles`
- warnings, overlap events, `complete` flag, wall-clock seconds

**Why the extra normalised fields are logged:** without them a score in the log could
not be re-derived from the record it sits in. An unre-derivable number has to be taken
on trust. `evaluate --graphs-only` regenerates every table and graph from logs **without
decoding video**.

## 13.2 The pipeline is deterministic

Repeat runs of identical configurations:

| Metric | Reference (4 runs) | E8 config (3 runs) |
|---|---|---|
| Average waiting | 1.686 s — **identical** | 2.143 s — **identical** |
| Throughput | 73.2 — **identical** | 88.3 — **identical** |
| Vehicles served | 131 — **identical** | 158 — **identical** |
| Stops | 1066 — **identical** | 1066 — **identical** |
| Processing FPS | 9.46 ± 1.92 | 8.94 ± 2.59 |

**Only wall-clock speed varies.** So every reported traffic number is *exactly*
reproducible and needs no error bars — a stronger statement than "mean ± SD". Verify
with `python scripts_variance_analysis.py`.

One honest detail: that script excludes runs predating stop counting from the stops
column, because **absent is not zero** — including them would have dragged the mean.

---

<a name="13"></a>
# 14. Honest limitations

## 14.1 Open-loop evaluation (the most important)

The footage is **recorded**. Vehicles cannot react to the simulated signal. Therefore
**queue length and stop counts are fixed by the video** and are identical across every
controller — which is why every run reports 1066 stops on the busy clip.

Only **signal-gated** metrics respond: waiting, throughput, vehicles served,
over-saturation.

- **Claimable:** "how well each controller allocates green to the observed traffic"
- **Not claimable:** "this reduces real-world waiting time"

A causal claim needs closed-loop microscopic simulation (SUMO) or field deployment —
both deliberately out of scope in the project spec.

## 14.2 Single camera

All footage is 116th Ave NE / NE 12th St, from the public **Bellevue Traffic Video
Dataset** (full provenance in section 10). The dataset provides different *hours* of one
camera, not different junctions. ROI polygons are hand-calibrated to that view.

**Correct wording:** *the method is camera-independent; the calibration is
camera-specific.* A new junction needs one calibration pass, not a code change.
Verified with `check_roi_coverage` that the calibration covers traffic on all three clips
(the development clip has no East traffic in that window — a correct detection, not a
failure).

## 14.3 "Spillback risk" is LOCAL storage, not downstream blocking

- **Measured:** whether *this* approach is about to exhaust *its own* visible storage
- **Not measured:** whether the road a vehicle is *entering* is already full

**Why not:** all four ROIs are **inbound** approaches to one junction. Once a vehicle
crosses it exits into an outbound lane in **no ROI**. A single camera physically cannot
see downstream occupancy. Doing it properly needs a second camera or a network model —
which is exactly what Wei et al. use, and exactly why their method needs infrastructure
this project does not have.

## 14.4 Short busy clip — the single-decision problem

This is the **strongest remaining weakness in the results**, and it deserves a blunt
statement rather than a hedge.

At `min_green_time = 30 s` plus a 3 s yellow, the 107 s busy clip admits only **three green
phases**. S3 and S4 differ in **exactly one selection decision** (frame 2430; traced in 9.4).
So:

- The +20.6% throughput difference is **correctly measured** and **correctly attributed** —
  verified by recomputing from raw frame records, and by confirming the two runs differ in
  only 3 configuration fields, all intended.
- It is **not** evidence of an average effect. It is one decision going the right way for a
  diagnosable reason.

If asked "how many times did your mechanism help?", the honest answer on this clip is
**once**. The defensible claim is about **direction and mechanism**, not effect size.

Why this clip nonetheless: it is the only registered clip with sustained over-saturation,
and a storage-risk term has nothing to act on without it (see 10.4).

What is being done about it rather than argued around it: `run_multiclip_ablation.py` re-runs
the decisive comparison on both 240 s clips, roughly doubling the phases per clip, and
`multiclip_table.py` reports the change per clip plus the spread across clips. More
constant-rate footage is the real fix, and section 10.5 identifies exactly which candidate
clip is ready for it and what preprocessing the rest need.

## 14.5 Hand-chosen weights

α, γ, δ, ω, ψ, ρ were set a priori, not tuned. Li et al. criticise exactly this
("burden of hyperparameters"), and I found a real instance: **E2 and E4 each help alone
but degrade together**, because E2 raises the score of the approach with the largest
spillback reading (North, 0.80) and E4's margin then locks it in. Reported, not hidden.

## 14.6 Mechanisms that did not work

E1 inert (homogeneous fleet); E3/E6 cost throughput (queue-scale mismatch: link-level
vs camera-ROI); **E8 inert at ω=0.3** (saturation). Three negative results out of ten,
each with a diagnosed cause.

---

<a name="14"></a>
# 15. How to run everything

## 15.1 Setup

```powershell
python -m pip install -r requirements.txt      # opencv, ultralytics, numpy, matplotlib
python -m pip install -r requirements-dev.txt  # + pytest, hypothesis
python -m pytest -q                            # 762 tests, ~60 s
```

## 15.2 Calibrate the approach axes (do this first for a new camera)

The spatial measures need to know which way is upstream on each approach. Measure it from
vehicle motion rather than trusting the polygon drawing — see 9.4 for why this exists.

```powershell
# Report only (dry run): compares the measured direction against the drawn geometry
python -m src.main calibrate-axes --video videos/bellevue_116th_busy.mp4 `
    --config config/bellevue_116th.json --no-display

# Write a calibrated copy. Only directions clearing the confidence threshold are written.
python -m src.main calibrate-axes --video videos/bellevue_116th_busy.mp4 `
    --config config/bellevue_116th.json --no-display `
    --write config/bellevue_116th_calibrated.json

# RECOMMENDED: pool every clip from the same camera into one estimate.
# Confidence depends on how many independent vehicles were seen, not on the geometry, so
# pooling raises certainty without changing what is estimated. Track_IDs are namespaced
# per clip automatically. ~50 min on CPU for all three clips.
python -m src.main calibrate-axes --no-display `
    --video videos/bellevue_116th_busy.mp4 videos/bellevue_116th_dev.mp4 `
            videos/bellevue_116th_final.mp4 `
    --config config/bellevue_116th.json `
    --write config/bellevue_116th_calibrated.json
```

Why pooling matters, measured: North's agreement is **0.531** on the 107 s clip but **0.232**
on the 240 s clip, while the *direction verdict is identical on both*. The direction is
stable; the certainty attached to a single short clip is not. Pooling fixes the certainty.

If any axis is untrustworthy and a spatial measure is enabled, every run prints a
`RuntimeWarning` naming the offending approaches. Do not ignore it.

## 15.3 The staged ablation (the headline experiment)

```powershell
# Stage configs on the CALIBRATED geometry (recommended)
python make_ablation_configs.py --base config/bellevue_116th_calibrated.json `
    --suffix _calibrated

python make_ablation_configs.py                # or the uncalibrated originals

# S0 baseline
python -m src.main control --video videos/bellevue_116th_busy.mp4 `
    --controller fixed --config config/ablation_s1_raza.json --no-display --no-video

# S1..S4
foreach ($s in @('s1_raza','s2_queue','s3_prediction','s4_proposed')) {
  python -m src.main control --video videos/bellevue_116th_busy.mp4 `
      --controller adaptive --config "config/ablation_$s.json" --no-display --no-video
}

python ablation_table.py                       # rebuild the table from the logs
python make_ablation_graph.py                  # rebuild the figure
```

## 15.4 Multi-clip reproducibility check (addresses the one-decision weakness)

The headline S3 → S4 comparison rests on a single decision on the 107 s clip (see 10.4).
This re-runs the decisive comparison on both 240 s clips and reports whether it reproduces.

```powershell
python run_multiclip_ablation.py --check-calibration   # ~90 min, CPU-only
python multiclip_table.py                              # summarise across clips
```

`--check-calibration` also re-measures the axis direction on each clip, which tests whether
the calibration is a property of the **camera** (expected) rather than of the clip it was
measured on.

## 15.5 Live demo (drop `--no-display`)

```powershell
python -m src.main control --video videos/bellevue_116th_busy.mp4 `
    --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json
```

Pre-rendered: `results/videos/DEMO_E8_forecast_busy.mp4`

**On screen, point at the per-approach panel:**
`n=` count, `q=` queue, `d=` density, `s=` score, **`f=` forecast**, and a **`^`** when
the forecast exceeds the current queue — that caret is the prediction actively working.
(`approach_panel_row`, `src/overlay.py:344`.)

## 15.6 Staged entry points (each independently runnable)

```powershell
python -m src.main play    --video V   # frame count + rate
python -m src.main detect  --video V   # annotated detections
python -m src.main track   --video V   # + Track_IDs and trajectories
python -m src.main measure --video V   # live counts, queue, density, score
python -m src.main control --video V --controller {fixed,adaptive}
python -m src.main evaluate --graphs-only
```

## 15.7 Configuration files

| File | Purpose |
|---|---|
| `config/default.json` | canonical placeholder the tests pin |
| `config/bellevue_116th.json` | **calibrated reference** for the real camera |
| `config/ablation_s1..s4*.json` | the staged ablation |
| `config/bellevue_116th_e1/e2/e4.json` | single-mechanism ablations |
| `config/bellevue_116th_li.json` | Li stack (E1+E3+E6) |
| `config/bellevue_116th_forecast*.json` | E8 variants |
| `config/bellevue_116th_reach*.json` | E9 ψ sweep (0.2 / 0.4 / 0.6) |

---

<a name="15"></a>
# 16. Deep-dive Q&A — the hard questions

**Q: Why does the sequencer tick before the metrics update?**
> Waiting time accrues only while an approach is not green. If metrics ran first, frame
> *n* would be measured against frame *n−1*'s signal state and every phase boundary
> would mis-attribute a frame of waiting. The cost is that selection uses the previous
> frame's scores, which is why the log records `selection_score_frame = frame_index − 1`.

**Q: How do you guarantee only one approach is green?**
> It's not guaranteed by a check — it's structural. `PhaseSequencer` stores one
> `(approach, state, end_frame)` triple and *derives* the four-element vector from it.
> There is no per-approach state that could disagree. And all transitions route through
> `_transition`, which rejects any edge outside `{RED→GREEN, GREEN→YELLOW, YELLOW→RED}`.

**Q: Doesn't the switching margin break your starvation guarantee?**
> No, because of ordering. `select` checks starvation **first**, then the margin, then
> the score. So the fairness bound holds regardless of margin size. There's a test
> driving `switching_margin=1.0` — the most adversarial value — confirming every
> approach is still served.

**Q: Why did prediction (S3) fail but the risk term (S4) work?**
> Same machinery, different basis. `normalized_queue` is a count over a configured
> capacity, so it saturates; once it reads 1.0 its derivative is zero and a projection
> reports stability while the queue is still growing. `queue_reach` is bounded by road
> geometry instead, so it keeps rising and its derivative stays meaningful. I can show
> this in isolation: with the count pinned at 1.0, the risk term still reports 1.00 for
> a growing queue and 0.00 for a clearing one.

**Q: Why is `queue_reach` taken over *stopped* vehicles, not *queueing* ones?**
> Because the blind spot is outside the Queue_Region. `queue_count` only sees inside it.
> Taking the max over all stopped vehicles anywhere in the ROI means the measure extends
> when a queue grows past the stop-line area — which is exactly the case the count
> cannot see.

**Q: Why no camera calibration for the reach?**
> Because the fraction is relative to the approach's **own** visible extent. $L_i$ is
> computed from the ROI polygon the detector already required. Li et al. measure metres,
> which needs a homography *and* an assumed 6 m headway — replacing one guessed constant
> with another. Mine introduces neither.

**Q: Why is the stop count identical across every controller?**
> Open-loop. The video is a recording, so vehicles cannot react to the signal — when
> they stopped is fixed by the footage. Only signal-gated metrics can move. It's also
> why I don't claim a causal waiting-time reduction.

**Q: Why keep enhancements off by default?**
> So the baseline is *provably* unchanged. The score reduces exactly to
> `compute_score` when all extension weights are zero, and the first test in each group
> pins that inertness. It means an ablation row differs from the reference in exactly
> the intended way and nothing else.

**Q: How do you know your ablation rows aren't mislabelled?**
> `ablation_table.py` classifies each run by reading the resolved config **back out of
> the log** and matching **exact** weights, not just "enabled". It already caught an
> older run with `forecast_weight = 0.4` that would have been reported as S3 (which
> requires 0.3), and excluded it.

**Q: Is `2.0 px/frame` for "stopped" not arbitrary?**
> It's a calibration parameter, exposed as `stopped_displacement`, and I document its
> meaning: at 30 fps it's ≈60 px/s, roughly walking pace on this camera. It's also
> conservative by design — a first sighting is never "stopped", so a vehicle must be
> observed stationary across two frames.

**Q: What's genuinely yours here?**
> Two mechanisms: the spatial queue reach and the forward-looking spillback risk built
> on it. Both are only possible because this system has per-vehicle tracking, which
> neither TITS paper has — both contain no computer vision at all and assume the
> measurement exists. I also checked novelty properly and **rejected three** other ideas
> for prior art (fairness → FairSCOSCA; uncertainty-aware → UCATSC; deficit →
> max-pressure, a baseline in Wei's own paper).

**Q: What would you do next, in priority order?**
> 1. **Closed-loop SUMO** — lets vehicles react, which unlocks causal claims on queue,
>    stops and delay. This is the real unlock.
> 2. **Longer, more varied footage** — so the forecast and the current queue point at
>    *different* approaches and prediction can open a distinct operating point.
> 3. **Weight tuning** — α, ω, ψ, ρ were set a priori; the measured E2/E4 antagonism is
>    the clearest candidate for a joint sweep.

---

# APPENDIX — Document map

| Need | File |
|---|---|
| **This deep-dive** | `COMPLETE_TECHNICAL_README.md` |
| Simple-language explainer + viva script | `MASTER_GUIDE_FOR_PROFESSOR.md` |
| My own contribution, full derivation | `report/my_contribution_E9.md` |
| **Axis defect, its fix, and the robustness check** | `report/axis_validation.md` |
| Dataset provenance and preprocessing (machine-readable) | `data/annotations/videos.json` |
| Slide-ready headlines | `report/RESULTS_HIGHLIGHTS.md` |
| Full TITS limitation catalogue | `report/paper_limitations_analysis.md` |
| Literature positioning | `report/literature_review.md` |
| Every measured number, `run_id`-tagged | `report/results_summary.md` |
| **Headline figure** | `report/graph_staged_ablation.png` |
| Other figures | `report/graph_adaptive_vs_fixed.png`, `graph_alpha_pareto.png`, `graph_enhancement_ablation.png` |
| Demo video | `results/videos/DEMO_E8_forecast_busy.mp4` |
| Reproducibility check (repeat-run variance) | `scripts_variance_analysis.py` |
| Multi-clip reproducibility of S3 → S4 | `run_multiclip_ablation.py`, `multiclip_table.py` |
| Axis calibration tool | `python -m src.main calibrate-axes` |
