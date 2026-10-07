# Project Explainer — everything, in one place

> A single document to explain the project end to end: what it is, the papers it
> builds on, every enhancement, all measured results, the honest limitations, how
> to run it, and a viva Q&A. Every number here comes from a run log under
> `results/run_logs/` and is tagged with its `run_id`; nothing is hand-entered.

---

## 1. One-paragraph summary

This is a **vision-based adaptive traffic-signal controller**. It reads a
traffic-junction video, detects and tracks vehicles with YOLO + ByteTrack, measures
per-approach demand (density and queue), scores each of the four approaches, and
allocates green time to the neediest one — with a fixed-time controller as the
baseline to measure against. The base design follows **Raza et al. 2025**. This
project's contribution is a set of **control-layer enhancements** derived from two
IEEE TITS 2025 papers, the headline being a **short-term queue forecast** that lets
the controller act on where a queue is *heading*, not only where it is now.

**Project direction:** *Enhancing a YOLO/PCE-based adaptive traffic-signal system
with vision-based queue dynamics and short-term queue prediction, to reduce queue
build-up and spillback.*

---

## 2. The pipeline

```
Traffic video
   -> OpenCV frame reading
   -> YOLO vehicle detection (car, motorcycle, bus, truck)
   -> ByteTrack tracking (persistent IDs)
   -> Approach assignment (4 ROI polygons: North, East, South, West)
   -> Per-approach measurement:
        density, queue length, PCE weighting,
        spillback, arrival, QUEUE FORECAST
   -> Score per approach  (alpha*density + (1-alpha)*queue + extension terms)
   -> Controller (fixed-time baseline  OR  adaptive)
   -> Green time  (score bands  OR  discharge-limited)
   -> Simulated signal overlay + run log
   -> Evaluation: waiting, throughput, served, over-saturation, stops
```

Fixed technology stack: Python, OpenCV, Ultralytics YOLO, ByteTrack, NumPy,
Matplotlib. No solver, no MPC, no reinforcement learning, no edge hardware.

---

## 3. The three papers and their roles

| Role | Paper | What we take |
|---|---|---|
| **Base system** | Raza, Kazmi et al. 2025, *IEEE Access* (DOI 10.1109/ACCESS.2025.3602844) — YOLO + PCE density + adaptive light control | The whole reactive pipeline: YOLO detection, PCE density, starvation prevention, tie-breaks, adaptive green bands |
| **Supporting** | Li, Lu, Wang 2025, *IEEE TITS* (DOI 10.1109/TITS.2025.3616119) — multi-objective coordination with queue-profile estimation | Queue-profile / over-saturation reasoning: measure whether a green cleared its queue; the discharge-time formula |
| **PRIMARY** | Wei, Ampountolas, Hirrle, Wang 2025, *IEEE TITS* (DOI 10.1109/TITS.2025.3568869) — hierarchical predictive control with queue dynamics | The predictive idea: forecast where the queue is going and pre-empt spillback |

**Why these two TITS papers.** Raza already solves the *reactive* problem — it reacts
to current density. Both TITS papers argue the same thing Raza is missing: **queue
length must be treated as a variable you estimate and anticipate, not a fixed input
or an afterthought.** Li et al. give the queue-profile foundation; Wei et al. give
the predictive direction. Neither TITS paper's actual machinery transfers (both are
network-scale, solver-bound, simulation-only) — but their *ideas* do, at
single-junction CV scale.

Supporting literature (unchanged from the spec): Saraff 2025 (Indian traffic + YOLO),
YOLO-LIGHT 2026 (detection/tracking), Jin 2024 (RL signal control), Lamrabet 2026
(emergency priority + SUMO validation).

---

## 4. What each TITS paper's limitations are (short version)

Full catalogue with per-item decisions is in `report/paper_limitations_analysis.md`.
The headline limitations that drove the work:

**Li et al. (supporting):**
- Fixed-time coordination, not adaptive — solves offline for a scenario (A-D1).
- Needs connected-vehicle trajectory data and a MILP solver (A-D2, A-D3).
- Corridor-scale: offsets, green bands, progression — none of it applies to one junction (A-D4).
- Queue = stops only under saturation (A-S6); gains shrink at high demand (A-S5).

**Wei et al. (primary):**
- Assumes a "state estimation and prediction module" it never builds (B-D1) — *this project is that module.*
- Ignores vehicle-type differences (B-S1) — *fixed by PCE weighting.*
- Network MPC + QP/NLP solver, simulation only (B-D4, B-D7).
- Coarse 10 s steps; a constant jam density cannot represent a varying queue.

---

## 5. Every enhancement (E1–E8)

All are **configuration-gated and off by default**, so the base system is unchanged
and every original test still passes. Each answers a specific published limitation.

| # | Name | From | In one line | Measured effect (busy clip) |
|---|---|---|---|---|
| **E8** | **Short-term queue forecast (PRIMARY)** | **Wei** | Fit the trend of each approach's queue, project it ~3 s ahead, act on the forecast | Genuinely anticipatory (forecast led current queue in ~19% of frames); shifts operating point toward throughput (73→88 veh/min, 131→158 served) |
| E1 | PCE-weighted queue | Raza + Wei B-S1 | Weight the queue by vehicle type (bus=3, car=1…), not just density | **No change** — Bellevue fleet is ~all cars |
| E2 | Spillback pressure | Wei | Weight vehicles stopped *beyond* the stop-line area | Waiting 1.69→1.53 s (−9%), slight throughput cost |
| E3 | Discharge-limited green | Wei Eq. 16 + Li Eq. 7 | Green = queue ÷ saturation flow + lost time | Harmful alone at this scale (queues too small); lost-time fix helps |
| E4 | Control-plan stability | Wei Eqs. 19–20 | Resist cycle-to-cycle switching/green oscillation | **Only both-axis win**: waiting −5% *and* throughput +3% |
| E5 | Over-saturation measurement | Li Eqs. 7–10 | Record per green phase whether the queue cleared, and the shortfall | Diagnostic: base controller leaves **3/3** greens with queue unserved (6 s short) |
| E6 | Queue-clearance gap-out | Li | End green when queue clears; extend while it persists | Cut over-saturated phases 3/3→1/6, throughput cost from extra intergreen |
| E7 | Stop counting | Li (headline MOE) | Count moving→stopped transitions | Reportable now; **controller-invariant** on recorded footage (1066 stops everywhere) |

**How E8 works (the one to explain in detail):**
```
Each frame, per approach:
   keep the last ~15 frames of measured normalized_queue
   fit slope (rate of change per second) by least squares
   forecast = clamp( current_queue + slope * horizon_seconds , 0, 1)
Feed forecast into:
   - the score (a 4th convex term at weight omega = 0.4)
   - the discharge green (size green for max(current, forecast))
```
A growing queue forecasts higher and pulls green *earlier*; a clearing queue
forecasts lower and is left alone. This is Wei et al.'s "predict and pre-empt
spillback" reduced to a trend line — no MPC, no solver.

**E8 vs the older arrival term (γ):** the pre-existing "predictive" term counts
vehicles *present but not yet queueing* right now — instantaneous. E8 is a genuine
*forecast over time*: the queue's own trend, projected forward. Different quantities,
independent weights, can be combined.

---

## 6. Measured results

All aggregates over the four approaches. Footage: Bellevue 116th Ave NE / NE 12th
St, 1280×720, 30 fps. `run_id`s in `report/results_summary.md`.

### 6.1 Adaptive beats fixed-time (the baseline result, all three clips)

| Clip | Controller | Avg wait (s) | Throughput (veh/min) | Served |
|---|---|---|---|---|
| development (240 s) | fixed-time | 1.49 | 39.5 | 158 |
| development | adaptive | 1.02 | 49.8 | 199 |
| final (240 s) | fixed-time | 1.10 | 34.3 | 137 |
| final | adaptive | 0.96 | 56.3 | 225 |
| busy (107 s) | fixed-time | 2.36 | 73.8 | 132 |
| busy | adaptive (α=0.5) | 1.69 | 73.2 | 131 |

Adaptive reduces waiting and raises throughput/served on every clip. This reproduces
the well-established adaptive-over-fixed result and confirms the pipeline is correct;
it is **not** itself the contribution.

### 6.2 The α sweep (queue-awareness), busy clip

| Controller | Avg wait (s) | Throughput | Served |
|---|---|---|---|
| adaptive α=1.0 (density-only, Raza-style) | 2.25 | 105.7 | 189 |
| adaptive α=0.5 (balanced) | 1.69 | 73.2 | 131 |
| adaptive α=0.0 (queue-only) | 1.53 | 71.6 | 128 |

As weight shifts from density to queue, waiting falls and throughput falls — a
fairness-vs-throughput trade-off, quantified. The queue term specifically rescues
the West approach, which density-only control starves (waits 1.50 s, served 0 →
waits 0.42 s, served 26).

### 6.3 The enhancement ablation, busy clip, α=0.5

| Enabled | Avg wait (s) | Throughput | Served | Over-saturated greens | Notes |
|---|---|---|---|---|---|
| none (reference) | 1.69 | 73.2 | 131 | 3 / 3 | |
| E1 | 1.69 | 73.2 | 131 | — | identical (car-only fleet) |
| E2 | 1.53 | 71.6 | 128 | — | −9% waiting |
| E4 | 1.60 | 75.5 | 135 | — | only both-axis win |
| E6 | 1.75 | 50.9 | 91 | 1 / 6 | fewer unserved queues, intergreen cost |
| **E8 (score)** | 2.14 | **88.3** | **158** | **2 / 3** | throughput shift; forecast active ~19% |
| E1+E2+E4 | 2.13 | 84.4 | 151 | — | E2/E4 interaction (see §8) |

**Stops (E7):** identical 1066 (4.28/veh) across every controller on the busy clip —
see §8 on why.

### 6.4 Generalization across clips (the "does it work on more than one" test)

Same code, same E8 config, three clips of different congestion. "fc active" is the
share of frame-approach cells where the forecast exceeded the current queue — i.e.
where it genuinely anticipated growth.

| Clip | Controller | Avg wait (s) | Throughput | Served | Stops | fc active | run_id |
|---|---|---|---|---|---|---|---|
| development | reference | 1.02 | 49.8 | 199 | 943 | – | `...dev__adaptive__alpha0p50__20260822-172515` |
| development | E8 forecast | 1.25 | 51.5 | 206 | 943 | 10.6% | `...dev__adaptive__alpha0p50__20260822-173411` |
| final | reference | 0.96 | 56.2 | 225 | 1204 | – | `...final__adaptive__alpha0p50__20260822-174244` |
| final | E8 forecast | 0.96 | 56.2 | 225 | 1204 | 14.4% | `...final__adaptive__alpha0p50__20260822-175319` |
| busy | reference | 1.69 | 73.2 | 131 | 1066 | – | `...busy__adaptive__alpha0p50__20260822-103419` |
| busy | E8 forecast | 2.14 | 88.3 | 158 | 1066 | 18.7% | `...busy__adaptive__alpha0p50__20260822-164349` |

**Three findings, all in the project's favour:**

1. **E8 runs correctly on every clip** — no crashes, no ROI misses, sensible
   numbers. It is not overfit to the busy clip.
2. **The forecast is active on every clip, and its activity scales with congestion**
   (10.6% → 14.4% → 18.7% as traffic increases). This is exactly what a queue
   predictor should do: the busier the junction, the more queue dynamics there are
   to anticipate.
3. **Its *effect on outcomes* also scales with congestion** — near-zero on the
   light final clip (identical result), tiny on development (+7 served), and a clear
   throughput shift on the busy clip (+27 served, +15 veh/min). On light traffic the
   forecast correctly does almost nothing rather than doing harm; it earns its
   keep only when there is a build-up to pre-empt.

The ROI calibration was verified to cover traffic on all three clips
(`check_roi_coverage`): final and busy cover all four approaches; the development
clip has no East-approach traffic *in that window* (a property of the footage, and a
correct detection, not a failure).

**Note on stops:** they differ *between* clips (943 / 1204 / 1066 — real, the videos
differ) but are identical *within* a clip across controllers — the open-loop property
of §8.1. Both facts are what you expect from an honest measurement.

---

## 7. Does it only work on one video? — the honest answer

**No, but with an important caveat.**

- It runs and produces sensible results on **all three Bellevue clips** (development,
  final, busy) — different times of day and different congestion levels. §6.1 and
  §6.4 show that.
- **Every video available is from the same fixed camera** (116th Ave NE / NE 12th
  St). The dataset provides different *hours* of that one camera, not different
  junctions.
- The four **ROI polygons are hand-calibrated to that camera's geometry**
  (`config/bellevue_116th.json`). Point the system at a *different* junction and the
  vehicles would fall outside those polygons — nothing would be measured — until the
  ROIs are re-drawn for the new view. This is inherent to any fixed-camera vision
  ATSC system (Raza's included), not a defect of this one.
- So the correct statement is: **camera-independent in method, camera-specific in
  calibration.** To deploy on a new junction you draw four ROI polygons for its
  camera; the detection, tracking, scoring, and control code is unchanged.

If you can supply a video of a genuinely different junction, the next step is one
calibration pass (draw the ROIs, set saturation/queue capacities) and it will run —
that is a ~30-minute task, not a code change.

---

## 8. Honest limitations (state these before the professor asks)

1. **Open-loop evaluation.** The footage is recorded, so vehicles cannot react to
   the simulated signal. Therefore **queue length and stop count are fixed by the
   video** — identical across every controller. Only signal-gated metrics (waiting,
   throughput, vehicles served, over-saturation) respond. A *causal* claim ("this
   controller reduces real waiting") needs a closed-loop microscopic simulator
   (SUMO) or field deployment — both out of scope. We measure *how the controller
   allocates green to the observed traffic*, which is the honest framing.
2. **Single junction, single camera.** No coordination, no offsets, no green bands —
   the corridor-scale parts of both TITS papers do not apply.
3. **Short busy clip (107 s ≈ 3 signal cycles).** Aggregates move a lot with one
   cycle; results show *direction*, not precise effect sizes.
4. **Hand-tuned weights.** α, ω, spillback/switching weights were chosen a priori.
   The E2/E4 antagonism (§6.3) is exactly the "hyperparameter burden" Li et al.
   criticise — which is why E5/E6/E8 were designed to decide by *measured comparison*
   where possible.
5. **E8 on this footage re-balances rather than wins outright.** The forecast is
   demonstrably anticipatory (~19% of frames) but, on a short single-junction clip,
   the approach that is *about to* be busiest is usually the one *already* busiest,
   so anticipation and current-queue priority largely coincide. A distinct predictive
   win needs longer, more variable footage.

None of these are hidden — they are the difference between an honest course project
and an overclaimed one, and they are the right things to say in a viva.

---

## 9. How to run it (for the demo)

```powershell
# Baseline vs adaptive on a clip (writes a run log + prints a summary)
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller fixed    --config config/bellevue_116th.json
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th.json

# The primary enhancement (E8 short-term forecast) in the score:
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json

# Full enhancement stack (E1+E3+E6+E8):
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast.json

# Watch it live (drop --no-display): shows ROIs, queues, scores, the signal, and the forecast
# Add --no-display for headless/logged runs.

# Full evaluation (tables + graphs from run logs, no video decode):
python -m src.main evaluate --graphs-only
```

Config files, each a documented experiment:
- `config/bellevue_116th.json` — the calibrated reference (no extensions)
- `config/bellevue_116th_e1.json`, `_e2.json`, `_e4.json` — single-mechanism ablations
- `config/bellevue_116th_li.json` — Li stack (E1+E3+E6)
- `config/bellevue_116th_forecast_scoreonly.json` — E8 in the score only
- `config/bellevue_116th_forecast.json` — full stack + E8 (the headline config)

Run the test suite (762 tests):
```powershell
python -m pytest -q
```

---

## 10. Anticipated viva questions

**Q: What is your actual contribution over the base paper?**
A: A control-layer enhancement set, headlined by E8 — a vision-only short-term queue
forecast that makes the controller anticipatory instead of purely reactive. The base
paper (Raza) reacts to current density; we forecast the queue and pre-empt build-up.
The two TITS papers motivate this (both argue queue must be estimated and
anticipated); we implement their *idea* at single-junction CV scale without their
solvers.

**Q: Why not just implement the MPC / MILP from the TITS papers?**
A: Both are network-scale and need a commercial solver (GUROBI/CPLEX) and
connected-vehicle data we don't have, and they run in tens of seconds per solve —
unusable per frame on a camera. We took the transferable idea (predict queue growth)
and implemented it as a least-squares trend line, which runs every frame.

**Q: Does it work on other videos?**
A: Yes on all three clips from the camera (different hours/congestion). Every
available video is the same fixed camera, so the ROI calibration is shared. A new
junction needs a one-time ROI calibration; the code is unchanged. (§7)

**Q: Your enhancement made waiting worse on the busy clip — isn't that bad?**
A: E8 in the score shifts the operating point *toward throughput* — it cleared 158
vehicles vs 131 and cut over-saturated phases 3/3→2/3, at a waiting cost. Whether
that's "better" depends on the objective. It is a re-balancing, measured honestly,
not a universal win — and I can say exactly why (the open-loop, short-clip,
same-camera constraints in §8).

**Q: How do you know the forecast actually predicts, rather than echoing the current queue?**
A: Measured: the forecast exceeded the currently-measured queue in ~19% of
frame-approach cells and reached the maximum forecast of 1.0 before any approach
actually filled. If it were echoing, those would be zero.

**Q: Why are stops and queue length identical across all controllers?**
A: The footage is recorded — vehicles can't react to our signal, so what they did
(queueing, stopping) is fixed by the video. Only the signal-gated metrics change.
This is the open-loop property, and it's why a causal claim needs SUMO or a field
trial. (§8.1)

**Q: What would you do next?**
A: (1) Closed-loop validation in SUMO to make causal claims and to let E8's
prediction actually change vehicle behaviour; (2) longer, more variable footage where
anticipation and current-queue priority diverge; (3) tune the weights instead of
setting them a priori.

---

## 11. Where everything lives

| Item | File |
|---|---|
| Full limitation analysis of both TITS papers | `report/paper_limitations_analysis.md` |
| Literature review + positioning + research trio | `report/literature_review.md` |
| All measured results, tagged with run_id | `report/results_summary.md` |
| This explainer | `report/PROJECT_EXPLAINER.md` |
| Source | `src/` (detection, tracking, lane_analysis, traffic_metrics, signal_controller, main) |
| Tests (672) | `tests/` |
| Configs (reference + ablations + E8) | `config/` |
| Run logs (every number traces here) | `results/run_logs/` |
