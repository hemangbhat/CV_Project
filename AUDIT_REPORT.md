# Repository Audit — Is this project ready for the professor?

**Date:** 2026-10-07 · **Scope:** all of `src/`, `tests/`, `config/`, `results/run_logs/`, `report/`, top-level docs.
**Method:** read the source, re-ran the test suite, recomputed every headline number from the raw
run logs, and replayed the real controller code offline over the logged measurements to test
alternative explanations. No file in `src/` was modified for this audit.

Every claim below can be reproduced with the scripts in `audit/`:

| Script | What it shows |
|---|---|
| `python audit/summarise_logs.py "results/run_logs/bellevue_116th_busy*"` | one line per run: stage, metrics, the actual green sequence |
| `python audit/signal_statistics.py` | how often `Q` really saturates; how noisy `X` and `S` are |
| `python audit/replay_ablation.py` | replays the real `AdaptiveController`/`PhaseSequencer` over logged metrics, incl. a **null control** |

---

## 0. Verdict in one paragraph

The engineering is good: the pipeline is clean, well-tested (**762 tests pass**, not 721 as the README
says), deterministic, and honest about many limitations. The **research story is well motivated**
(Raza → Li → Wei → spatial queue reach), and the saturation argument is mathematically correct.
**But the enhancement is not demonstrated by the current evidence.** Four independent findings
(§2) show that the headline S3 → S4 result (+21% throughput) is produced by (a) one controller
decision in a truncated final phase, (b) a weight-renormalisation side-effect that a null control
reproduces exactly, and (c) an evaluation metric that, on recorded footage, measures agreement with
the *real* traffic light rather than control quality. Separately, the claimed mechanism (count queue
saturates → forecast is blind) **almost never occurs on the real footage** (Q = 1 in ≤ 0.6% of
frames). A strict professor who asks the right two questions will find this. It is fixable in the
remaining time, and §8 is the plan.

---

## 1. What is correct

| Area | Status | Evidence |
|---|---|---|
| Video ingestion, fixed 30 fps, simulated clock | ✅ | `src/video_io.py`; clip prep documented in `data/annotations/videos.json` |
| YOLOv8 detection → 4 vehicle classes | ✅ works | `src/detection.py`; stock COCO weights, conf 0.3 |
| ByteTrack via Ultralytics `persist=True` | ✅ deterministic | repeated runs give byte-identical metrics (e.g. three S0 logs all 132 served / 1066 stops) |
| ROI / queue assignment (bottom-centre point, boundary-inclusive, nearest-centroid tiebreak) | ✅ | `src/lane_analysis.py:72-80, 759-813` |
| PCE density `D = clamp(ΣPCE / saturation_count)` | ✅ | `src/traffic_metrics.py:459-494` |
| Signal state machine (one triple, only legal edges, yellow, truncation) | ✅ very solid | `src/signal_controller.py:630-1112` |
| Starvation guarantee (served within `limit+1` cycles) | ✅ | `_starved_approach`, property tests |
| Score is a convex combination, all terms in [0,1], α=1 → density, α=0 → queue, all extra weights 0 → baseline | ✅ | `compute_score_weighted`, `traffic_metrics.py:777-831` |
| `ApproachAxis` geometry + measured-direction calibration | ✅ well-reasoned | `lane_analysis.py:100-308, 345-595` |
| Unit tests | ✅ **762 passed in 28 s** | `python -m pytest -q` |
| Run logs are complete & reproducible | ✅ | every number in the docs traces to a `run_id` |
| Honesty of docs on open-loop, local-vs-downstream spillback, 1-of-3-clips | ✅ mostly | `README.md:228`, `report/multiclip_reproducibility.md` |

### 1.1 The mathematical check you asked for (§23 of your brief) — verified from source

* `Q_i = clamp(queue_pce / queue_capacity)` (`traffic_metrics.py:496`). `queue_pce ≥ queue_capacity ⇒ Q_i = 1`. ✔ can saturate.
* `F_i = clamp(Q_i + slope(Q)·H)` with least-squares slope over 15 frames (`traffic_metrics.py:692-716`).
  If `Q` has been 1.0 over the window, slope = 0 ⇒ `F = 1`. If it is about to *fall*, slope < 0 ⇒ `F < 1`.
  **So at saturation F can signal "clearing" but can never signal "still growing".** ✔ blind to growth.
* `X_i = max over STOPPED vehicles of ApproachAxis.fraction(p)` (`traffic_metrics.py:382-390`). It is
  not a count and is not inside the queue region, so it keeps increasing as stopped vehicles appear
  further upstream. ✔
* `S_i = clamp(X_i + slope(X)·H_risk)` (`traffic_metrics.py:667-690`): growing X ⇒ S > X; steady ⇒ S = X;
  clearing ⇒ S < X — independent of Q. ✔
* Executable proof: `tests/test_tits_extensions.py::test_risk_works_where_the_count_based_projection_is_blind`
  holds Q = 1.0 and shows growing vs clearing are separated by S. ✔ passes.

**The theory is right.** The problems are all in whether the real data and the evaluation exercise it.

---

## 2. What is wrong (critical — these break the headline claim)

### W1. Open-loop "throughput / vehicles served" measures agreement with the REAL signal  ★ most important

`vehicles_served` counts a track leaving a Queue_Region while the *simulated* approach is GREEN
(`traffic_metrics.py:430-435`). In a recording, vehicles leave the queue only when the **real**
traffic light lets them. So served = (recorded departures) ∩ (simulated green). Verified exactly:

```
departures per approach are fixed by the footage; served = departures that fall in simulated green
S0 served 132 = N48 + E8 + S71 + W5        S1 served 189 = N122 + S67
S3 served 131 = N122 + W9                  S4 served 158 = N158   (East/South/West never green)
```

S4 "wins" by keeping **North green for the entire clip** (`N30 N45 N45*`) because North is where the
real signal happened to discharge traffic. A controller that ignored the score and always served North
would beat it. Waiting time has the mirror problem (vehicles held by the real red accrue no waiting if
the simulator shows green). The README's open-loop paragraph says "no real-world claim", but it still
treats throughput as a measure of *allocation quality* — it isn't one. **This metric cannot rank
controllers on recorded footage.** This is not a wording issue; it is a validity issue.

### W2. The S3 → S4 gain is a weight-renormalisation artefact, not the risk signal

S3 = `0.7·base + 0.3·F`; S4 = `0.4·base + 0.3·F + 0.3·S`. Two things changed: S was added **and** the
base (D+Q) weight dropped 0.7 → 0.4. Replaying the real controller with **S replaced by zeros**:

| busy clip (replay of the real controller) | served | green sequence |
|---|---|---|
| S3 `0.7B + 0.3F` | 131 | N30 N45 W45* |
| **S4** `0.4B + 0.3F + 0.3S` | **158** | N30 N45 N45* |
| **NULL** `0.4B + 0.3F + 0.3·0` | **158** | N30 N45 N30* |
| S3 with F weight 0.4 | 158 | N30 N45 N30* |

The null control reproduces S4 exactly. The spillback-risk value contributes nothing to the busy-clip
result. (The replay reproduces every logged result exactly, so it is faithful.)

### W3. The real footage almost never enters the saturation regime the story depends on

From `audit/signal_statistics.py` (all clips, all approaches): **Q = 1.0 in 0.00–0.6% of frames**, and
`queue_length` never exceeds `queue_capacity`. So "S3 was inert *because Q saturated*" is **not** what
happened on this footage. It is also contradicted by the dev clip, where S3 *did* change decisions
(S2 199 → S3 206 served). The saturation argument is correct but **untested by the data**.

### W4. One decision, one clip

On busy (107 s) S3 and S4 differ in **one** decision, in a **truncated** final phase. On dev and final
(240 s each) S3 ≡ S4 to every decimal (the docs already admit this). With n = 1 decision there is no
statistical evidence of any effect.

### W5. `X` and `S` are dominated by measurement noise

* "Stopped" = moved < 2 px **since the previous frame** (`traffic_metrics.py:503-518`). At 30 fps this
  flags slow-moving and **distant** vehicles as stopped (perspective: far vehicles move few px/frame),
  and box jitter flips it constantly. Evidence: **1066 "stops" for 249 vehicles in 107 s (4.3/vehicle)**.
* `X` jumps by > 30% of the approach length between consecutive frames in up to **8%** of frames;
  North-busy `X > 0` in 99.3% of frames.
* The slope window is 15 frames (0.5 s) but the risk horizon is 5 s — noise is **amplified ×10**.
* The controller reads the score on **one frame** per cycle, so the decision samples this noise.
* Detector recall is poor for distant vehicles (see `report/screenshots/*frame2.png`: North ROI shows
  10+ cars, panel says n = 3) — and the far end of the approach is exactly where queue reach lives.

### W6. Raza fidelity is a "Raza-style" baseline, and the base paper PDF is corrupt

* S1 = PCE density with 30/45/60 s bands. Starvation here is a cycle counter; the repo's own literature
  review says Raza's starvation guard is queue-based. Call S1 "Raza-style", never "reproduction".
* `An_Edge-Deployed_...(1).pdf` in the repo **does not render** (broken flate streams; every page blank).
  The Li et al. paper is not in the repo at all. Both must be fixed before submission.

---

## 3. What is missing

1. **A valid causal evaluation** (closed loop) — W1 makes this necessary, not optional.
2. A **null/weight-matched control** and an **X-without-projection** arm in the ablation (W2).
3. **Repeated trials** with variance (seeds/clips) and a paired comparison — currently n = 1.
4. Scenarios that actually **reach saturation / storage exhaustion** (W3) — growing queues, unequal
   demand, platoons. The current clips are lightly loaded (on dev/final, 4 of 7 greens are starvation-forced to empty roads).
5. A robust **stopped-vehicle** test and a **contiguous queue-tail** definition of X (W5).
6. A small **measurement validation** of X against hand-labelled frames (is the CV measurement right?).
7. Demo overlay showing X, S, F, score, selected approach (current panel shows only n, q, d, s).
8. Train/validation/test separation for weight choice (weights are a-priori; fine if stated, better if tuned on validation only).

## 4. What should be removed / cleaned

* Root scratch scripts: `_coverage.py _cut2.py _demo_screens.py _democlip.py _get1708.py _heatmap.py
  _probe_hours.py _static.py` (move to `scripts/` or delete), `scripts_variance_analysis.py`.
* 50 committed `__pycache__` files; `.kiro/` spec folder (tool artefact).
* Five overlapping top-level docs (`README.md`, `readme_final.md`, `COMPLETE_TECHNICAL_README.md` 115 KB,
  `MASTER_GUIDE_FOR_PROFESSOR.md`, `CODE_TOUR.md`) + 10 files in `report/` that repeat each other and
  currently contain the claims W1–W4 invalidate. Consolidate to: README, final report, viva Q&A.
* Stale run logs produced by older code versions (e.g. `stops = 0`, no over-saturation fields) — archive,
  don't mix with final results. Duplicate `comparison__*.csv` in `results/tables` and `report/`.
* The E1–E7 extensions can stay in code (they are switched off by default) but should move to an
  appendix "explored and rejected", not the main story.

## 5. What should be improved

| Priority | Item | Why |
|---|---|---|
| P0 | Closed-loop evaluation of S0–S4 + null + X-only, multiple seeds/scenarios | only valid way to rank controllers (W1, W2, W4) |
| P0 | Correct every doc that states the S4 +21% result as evidence | academic honesty (W1–W3) |
| P1 | Robust stopped test (displacement over ~1 s, normalised by box height) + contiguous queue tail for X + 2–3 s slope window | makes the *CV contribution* real (W5) |
| P1 | Validate X on ~30 hand-labelled frames | proves the measurement, which is the CV part the professor grades |
| P2 | Demo overlay: per-approach D, Q, X, S, F, score, chosen approach, green countdown, queue-tail marker | demo requirements |
| P2 | Detector: try `imgsz=1280` or YOLOv8s for distant vehicles; report recall difference | X depends on far vehicles |
| P3 | Repo cleanup, one README, one report, one Q&A sheet | professor-readiness |

## 6. Is the enhancement genuinely demonstrated?

**No — not yet.** The *measurement* idea (spatial queue reach + forward-projected storage risk) is
sound, original in its construction, well tested in synthetic unit tests, and correctly motivated by
Li & Wei. But on the current evidence: the reported gain is reproduced without the risk signal (W2),
measured with a metric that cannot rank controllers on recorded video (W1), rests on one decision (W4),
and the saturation regime it is designed for barely occurs in the footage (W3).

## 7. Is the research story defensible?

**The chain is defensible; the conclusion is not (yet).**
Raza (reactive PCE density) → Li (queue profile, over-saturation) → Wei (predictive queue dynamics,
spillback) → "a count inside a fixed region saturates, so predict *spatial* extent instead" is a clean,
narrow, honest research question that fits the professor's workflow exactly. What must change is the
**evidence**: the answer has to come from an experiment in which (i) vehicles respond to the signal,
(ii) queues actually exceed the queue region, and (iii) the risk term is tested against a null control
over many trials. If that experiment shows no benefit, that is still a legitimate, gradeable result —
but it must be reported as such.

---

## 8. Exact final build plan (≈ 5 weeks of student time, ≈ 6 phases)

**Phase A — Measurement fix (CV core, week 1).**
A1 stopped test over a 1 s window, normalised by bounding-box height (perspective-invariant), with
hysteresis. A2 X = tail of the **contiguous** stopped chain starting at the stop line (gap ≤ k vehicle
lengths), so isolated far "stopped" boxes cannot set the reach. A3 slope window 2–3 s.
A4 tests for each. A5 re-run `signal_statistics.py` to show the noise reduction (before/after table).

**Phase B — Measurement validation (week 1–2).** Hand-label the queue tail on ~30 frames across the
three clips; report X error (mean abs error in axis fraction) before/after Phase A, and detector recall
on the far half of each ROI for n/s/imgsz-1280.

**Phase C — Closed-loop evaluation (weeks 2–3).** Single 4-arm junction with finite approach storage,
in which vehicles respond to the signal. Demand per approach **calibrated from the CV counts** of the
real clips, plus stress scenarios (balanced-light, heavy, unequal, platoon surge, ramp to over-saturation).
Same `AdaptiveController`, `PhaseSequencer` and `compute_config_scores` code; D, Q, X, S computed from
simulated vehicle positions with the **same definitions**. ≥ 10 seeds per scenario; metrics: mean delay,
throughput, max queue, **storage-exhaustion events** (real spillback in the sim), stops.
*Decision needed — see below: SUMO vs. a small pure-Python simulator.*

**Phase D — Fair ablation (week 3).** S0, S1, S2, S3, S4, **NULL (weight-matched)**, **S4-X (reach,
no projection)**. Weights fixed before looking at test seeds (tune on validation seeds only). Paired
differences with 95% CI. Key questions: does S beat NULL? does projection (S) beat reach alone (X)?
does the gain appear specifically in the over-saturation scenarios, as the theory predicts?

**Phase E — Open-loop footage, reframed (week 4).** Keep the real-video runs as (1) measurement
demonstration, (2) "decision analysis": when does each controller choose differently and why. Stop
presenting open-loop throughput as a performance ranking. Upgrade the overlay for the demo video.

**Phase F — Report, slides, viva sheet, cleanup (weeks 4–5).** One README, one report with the
Raza → Li → Wei → contribution chain, the negative results kept, the corrected open-loop analysis (W1),
the closed-loop result whatever it is, and exact answers to the 32 viva questions.
