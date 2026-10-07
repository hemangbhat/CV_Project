# CODE TOUR — what to open, in what order, and what to say

> **Use this when the professor says "show me the implementation."**
> Every `file:line` below is verified to resolve to the exact function named.
> 10 stops, ~15 minutes. Stops 3, 4 and 5 are your own contribution — spend the
> most time there.

---

## THE 30-SECOND ANSWER

If he only wants one thing, open **two files side by side**:

| File | Why |
|---|---|
| `src/lane_analysis.py` line **100** | `ApproachAxis` — my spatial queue measure |
| `src/traffic_metrics.py` line **640** | `_project_risk` — my spillback risk term |

Those ~90 lines are the contribution. Everything else is supporting infrastructure.

---

## THE FULL TOUR

### Stop 0 — Prove it runs and is tested (30 seconds, do this first)

```powershell
python -m pytest -q
```

> *"762 tests. Every enhancement is off by default, and the first test in each group
> pins that it's inert when off — so I can prove the baseline is unchanged."*

Then show the scale:

```powershell
Get-ChildItem src/*.py | ForEach-Object { "{0,-24} {1,5}" -f $_.Name, (Get-Content $_.FullName | Measure-Object -Line).Lines }
```

> *"About 9,500 lines of source, 12,900 lines of tests. More test code than source."*

---

### Stop 1 — The overall shape

**Open:** `COMPLETE_TECHNICAL_README.md` § 2.1 and § 2.2

> *"Five layers: perception, spatial reasoning, measurement, decision, output. And this
> dependency graph is generated from the actual import statements — it's a DAG, no
> circular imports."*

**The line to say here:**

> *"Notice `signal_controller` depends on `config` and nothing else. Not on metrics, not
> on video. That's deliberate — the whole control layer plus every safety invariant can
> be tested with plain dictionaries of numbers, no GPU. It's why the property tests run
> in seconds."*

---

### Stop 2 — Where a vehicle becomes a measurement

**Open:** `src/lane_analysis.py` line **336** (`ApproachAssigner.assign`)

Point at the three-case logic:

```
0 candidates  -> approach = None   (excluded from ALL measurement)
1 candidate   -> assigned
2+ candidates -> nearest ROI centroid wins + log an OverlapEvent
```

> *"Vehicles outside every ROI are kept for the overlay but excluded from measurement.
> Overlaps are resolved by nearest centroid and logged, so an ambiguous assignment is
> visible in the run log rather than silent."*

**Bonus detail if he probes:** `polygon_centroid` at line **83** uses image moments, not
a vertex mean — because a vertex mean gets dragged toward any polygon edge that happens
to have densely spaced vertices.

---

### Stop 3 — ⭐ MY CONTRIBUTION 1: the spatial queue measure

**Open:** `src/lane_analysis.py` line **100** — `class ApproachAxis`

**First, the problem.** Say this before showing the code:

> *"My queue measure was `count ÷ capacity`, where capacity was a number I guessed. Two
> flaws: it saturates — once the count hits capacity it reads 1.0 and stops responding
> even as the queue keeps growing — and it throws away position, so three cars at the
> stop line looks identical to three cars spread down the road."*

**Then the insight:**

> *"Both are geometry problems, and the geometry was already in my config file, unused.
> Each approach has two polygons — the road area and the stop-line area. The line between
> their centroids is the direction of travel."*

**Walk the three methods:**

| Line | Method | What to say |
|---|---|---|
| **133** | `__init__` | *"Builds the axis: `u` = downstream direction, negate for upstream. `L` = how far the ROI reaches back, taken as the max projection over the polygon's own vertices."* |
| **165** | `fraction` | *"Projects any point onto that axis. 0 = at the stop line, 1 = far edge of the visible road. One dot product per vehicle."* |
| **181** | `build_approach_axes` | *"Built once per run, so per-frame cost is one dot product per vehicle."* |

**The key sentence — the one that shows you understand your own design:**

> *"`L` is **computed** from the polygon, not guessed. That's why this measure adds no
> tunable parameter. And it needs no camera calibration, because the fraction is relative
> to the approach's own visible extent. Li et al. measure queue length in metres, which
> needs a homography plus an assumed 6-metre vehicle spacing — replacing one guessed
> constant with another. Mine introduces neither."*

**Then show where it's consumed:** `src/traffic_metrics.py` line **419** (`_measure`)

> *"The reach is taken over **stopped** vehicles anywhere in the ROI — not just queueing
> ones. That's deliberate: the blind spot is *outside* the stop-line region, so the max
> has to range over the whole approach."*

---

### Stop 4 — ⭐ MY CONTRIBUTION 2: the spillback risk term

**Open:** `src/traffic_metrics.py` line **640** — `_project_risk`

The formula on screen is:

```
risk = clamp( reach + slope × risk_horizon )
```

> *"This is just 'position + speed × time', applied to the back end of a queue instead of
> a moving object. Reach is where the queue ends now, the slope is how fast it's
> extending backwards, and the horizon is how far ahead I look."*

**Show line 670** (`_slope_per_second`):

> *"The slope is a least-squares fit over a 15-frame window, not a single frame-to-frame
> difference — otherwise detector jitter reads as growth."*

**Now the payoff. This is the single best moment in the tour:**

> *"Look at which quantity I project. Not the count-based queue — the **spatial** reach.
> Here's why that matters: the count saturates. Once it reads 1.0 its derivative is zero,
> so projecting it forward reports a **stable** approach while the queue is physically
> still growing. The spatial reach keeps rising, so its derivative stays meaningful
> exactly in the regime this term exists to detect."*

**Prove it live** — this takes 10 seconds and it's the strongest evidence you have:

```powershell
python -c @"
import dataclasses
from src.config import load_config
from src.traffic_metrics import QueuePredictor, ApproachMetrics, APPROACH_NAMES
c = load_config('config/bellevue_116th.json')
c = dataclasses.replace(c, use_spillback_risk=True, risk_horizon_seconds=5.0)
def run(seq, label):
    p = QueuePredictor(c, 30.0); out = None
    for r in seq:
        out = p.predict({n: ApproachMetrics(approach=n, vehicle_count=2,
            vehicle_density=0.3, queue_length=1,
            normalized_queue=1.0, queue_reach=r) for n in APPROACH_NAMES})
    m = out['North']
    print(f'{label:22s} count_queue={m.normalized_queue:.2f} (SATURATED)  reach={m.queue_reach:.2f}  risk={m.spillback_risk:.2f}')
run([0.02*i for i in range(15)], 'GROWING queue')
run([0.4]*15,                    'STEADY queue')
run([max(0,0.9-0.05*i) for i in range(15)], 'CLEARING queue')
"@
```

Expected output:

```
GROWING queue          count_queue=1.00 (SATURATED)  reach=0.28  risk=1.00
STEADY queue           count_queue=1.00 (SATURATED)  reach=0.40  risk=0.40
CLEARING queue         count_queue=1.00 (SATURATED)  reach=0.20  risk=0.00
```

> *"The count is pinned at 1.00 in all three — a count-based projection cannot tell these
> apart at all. Mine reports 1.00, 0.40, 0.00."*

**Then point at the test that pins it:**
`tests/test_tits_extensions.py` → `test_risk_works_where_the_count_based_projection_is_blind`

---

### Stop 5 — ⭐ The scoring function (where both contributions enter the decision)

**Open:** `src/traffic_metrics.py` line **750** — `compute_score_weighted`

> *"Seven terms, convex combination. Every term is in 0..1, every coefficient is
> non-negative, and they sum to exactly 1 — so the score is guaranteed in 0..1."*

**Why that guarantee matters (not cosmetic):**

> *"The green-time bands are keyed to score thresholds — 0.3 gives 45 seconds, 0.6 gives
> 60. If a score could exceed 1 or go negative, band lookup would be undefined. So
> convexity is enforced at three levels: config validation at load, a re-check in this
> function because it's callable directly, and `ApproachMetrics.__post_init__` guaranteeing
> each input term is already in range."*

**Show the 2×2 that is the whole design:**

| | Reacts to present | Predicts future |
|---|---|---|
| **Count-based** (saturates) | `Q` | `F` |
| **Spatial** (doesn't saturate) | **`X`** ⭐ | **`S`** ⭐ |

> *"`F` and `S` use the same prediction machinery. They differ only in which row they
> project. My ablation shows that single difference is worth +20.6% throughput."*

---

### Stop 6 — The safety invariants (shows engineering maturity)

**Open:** `src/signal_controller.py` line **748** — `signal_states`

```python
return {name: self._active_state if name == self._active_approach else SignalState.RED
        for name in APPROACH_ORDER}
```

> *"'Exactly one approach is green' isn't checked anywhere — it's structurally
> impossible to violate. The sequencer stores **one** triple `(approach, state,
> end_frame)` and derives all four states from it. There's no per-approach state that
> could fall out of agreement."*

**Then line 1033** — `_transition`:

> *"Only three edges exist: RED→GREEN, GREEN→YELLOW, YELLOW→RED. Every state change
> routes through here and anything else raises. Note there's no GREEN→RED edge — so
> `finalize` deliberately does **not** tidy the signal to RED at end of run. Inventing
> that edge to make the ending look neat would be exactly the hidden transition the
> invariant forbids."*

---

### Stop 7 — The decision order (a subtle safety property)

**Open:** `src/signal_controller.py` line **408** — `AdaptiveController.select`

Point at the strict order:

```
1. _starved_approach     (line 598)  -> ignores scores entirely
2. _apply_switching_margin (line 561)
3. _highest_scoring_approach (line 546)
```

> *"Starvation is checked **first**, before the margin and before the score. If the
> switching margin ran first it could pin an incumbent indefinitely and break the
> fairness bound. This ordering means the guarantee holds **regardless of how large the
> margin is**."*

**Show the test that proves it:**
`tests/test_tits_extensions.py` → `test_e4_margin_does_not_defeat_starvation_prevention`
— it drives `switching_margin = 1.0`, the most adversarial possible value.

**One more detail if he's engaged:** `_advance_counters` (line **621**) runs *last*,
after the decision.

> *"A counter incremented before selection would let an approach's own wait influence the
> cycle it's currently being considered for."*

---

### Stop 8 — The ablation: proving each component earns its place

**Open:** `make_ablation_configs.py`

> *"Each stage adds exactly one component. Geometry, PCE weights, detector, tracker,
> green bands — all identical. So the difference between two consecutive rows isolates
> that component."*

**Then open** `ablation_table.py` → `stage_of`:

> *"This classifies each run by reading the resolved config **back out of the log** and
> matching **exact** weights, not just 'enabled'. It already caught an older run with
> forecast weight 0.4 that would have been mislabelled as S3, which requires 0.3. A row
> can't be silently wrong."*

**Run it live:**

```powershell
python ablation_table.py
```

| Stage | Waiting | Throughput | Served | Unserved queues |
|---|---|---|---|---|
| S0 fixed-time | 2.36 | 73.8 | 132 | 4/4 |
| S1 Raza (density) | 2.25 | **105.7** | **189** | 2/3 |
| S2 + queue | **1.69** | 73.2 | 131 | 3/3 |
| S3 + prediction | **1.69** | 73.2 | 131 | 3/3 |
| S4 **proposed** | 2.14 | 88.3 | 158 | **2/3** |

**Lead with the negative result — it's your strongest card:**

> *"S3 came out **bit-for-bit identical** to S2. The count-based forecast changed no
> decision at all. I report that rather than hiding it — and diagnosing why, saturation,
> is what led to S4, which uses the same predictor on the spatial quantity and gains 20.6%
> throughput. That's a controlled comparison of one design decision."*

---

### Stop 9 — The evidence chain

**Open any file in** `results/run_logs/`

> *"57 run logs. Each holds the fully resolved config, one record per frame with every
> per-approach measurement, the phase history, per-vehicle waiting times, and stop
> counts. Every number in my report traces to a `run_id`."*

**Point at why the extra fields are logged:**

> *"I log `normalized_forecast`, `queue_reach`, `spillback_risk` and `queue_pce` too —
> not just the score. Without them a score in the log couldn't be re-derived from the
> record it sits in, and an unre-derivable number has to be taken on trust."*

**Then prove reproducibility:**

```powershell
python scripts_variance_analysis.py
```

> *"I re-ran the key configs 3–4 times. Every traffic metric was **identical** every
> time; only laptop processing speed varied. The pipeline is deterministic, so every
> number is exactly reproducible — that's a stronger claim than mean ± standard
> deviation."*

---

### Stop 10 — The live demo

```powershell
python -m src.main control --video videos/bellevue_116th_busy.mp4 `
    --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json
```

Or use the pre-rendered one (no waiting): `results/videos/DEMO_E8_forecast_busy.mp4`

**What to point at on screen:**

| On screen | Say |
|---|---|
| Coloured polygons | *"The four approach ROIs from the config."* |
| Boxes with numbers | *"ByteTrack IDs — persistent across frames, which is what makes per-vehicle waiting time and stopped-detection possible."* |
| Panel: `n= q= d= s=` | *"Count, queue, density, score per approach."* |
| **`f=0.94^`** | ⭐ *"That's the forecast. **The caret means the forecast exceeds the current queue** — the prediction is actively firing on traffic that hasn't arrived yet."* |
| Signal lights + banner | *"Which approach has green, and how long remains."* |

**The overlay code:** `src/overlay.py` line **344** — `approach_panel_row`

---

## QUICK REFERENCE — every file:line in this tour

| Stop | File | Line | Symbol |
|---|---|---|---|
| 2 | `src/lane_analysis.py` | 336 | `ApproachAssigner.assign` |
| 2 | `src/lane_analysis.py` | 83 | `polygon_centroid` |
| **3** ⭐ | `src/lane_analysis.py` | **100** | **`class ApproachAxis`** |
| **3** ⭐ | `src/lane_analysis.py` | **133** | `ApproachAxis.__init__` |
| **3** ⭐ | `src/lane_analysis.py` | **165** | `ApproachAxis.fraction` |
| 3 | `src/lane_analysis.py` | 181 | `build_approach_axes` |
| 3 | `src/traffic_metrics.py` | 419 | `MetricsEngine._measure` |
| **4** ⭐ | `src/traffic_metrics.py` | **640** | **`_project_risk`** |
| 4 | `src/traffic_metrics.py` | 670 | `_slope_per_second` |
| 4 | `src/traffic_metrics.py` | 563 | `class QueuePredictor` |
| **5** ⭐ | `src/traffic_metrics.py` | **750** | **`compute_score_weighted`** |
| 5 | `src/traffic_metrics.py` | 828 | `compute_config_scores` |
| 6 | `src/signal_controller.py` | 748 | `signal_states` |
| 6 | `src/signal_controller.py` | 1033 | `_transition` |
| 7 | `src/signal_controller.py` | 408 | `AdaptiveController.select` |
| 7 | `src/signal_controller.py` | 598 | `_starved_approach` |
| 7 | `src/signal_controller.py` | 561 | `_apply_switching_margin` |
| 7 | `src/signal_controller.py` | 621 | `_advance_counters` |
| — | `src/signal_controller.py` | 630 | `class PhaseSequencer` |
| — | `src/signal_controller.py` | 762 | `PhaseSequencer.tick` |
| — | `src/signal_controller.py` | 963 | `_apply_gap_out` |
| — | `src/signal_controller.py` | 1012 | `_finalize_green_metrics` |
| — | `src/signal_controller.py` | 473 | `discharge_green_time` |
| — | `src/config.py` | 580 | `_validate` |
| — | `src/main.py` | 196 | `Pipeline.run` |
| 10 | `src/overlay.py` | 344 | `approach_panel_row` |

---

## IF HE ASKS TO SEE SOMETHING SPECIFIC

| He asks about... | Open |
|---|---|
| "Show me your contribution" | `lane_analysis.py:100` + `traffic_metrics.py:640` |
| "How is the score computed?" | `traffic_metrics.py:750` |
| "How do you know only one light is green?" | `signal_controller.py:748` and `:1033` |
| "How is fairness guaranteed?" | `signal_controller.py:408` (ordering) + `:598` |
| "Where does PCE weighting happen?" | `traffic_metrics.py:497` (`_pce_subset`) |
| "How do you detect a stopped vehicle?" | `traffic_metrics.py:476` (`_is_stopped`) |
| "How is over-saturation measured?" | `signal_controller.py:1012` |
| "How is the config validated?" | `config.py:580` (validation is **total**) |
| "Where are the green-time bounds enforced?" | `signal_controller.py:540` (`_bounded_green`) |
| "Show me the tests for X" | `tests/test_tits_extensions.py` (1,388 lines, grouped per feature) |
| "Prove the baseline is unchanged" | first test of each group asserts inertness when off |
| "Show me the numbers" | `report/results_summary.md` (every row `run_id`-tagged) |

---

## THE THREE SENTENCES THAT MATTER MOST

1. **On your contribution:**
   > *"`L` is computed from the ROI polygon, not guessed — that's why this adds no tunable
   > parameter, and no camera calibration."*

2. **On the design decision:**
   > *"The count-based queue saturates, so its derivative goes to zero and a projection
   > reports stability while the queue is still growing. The spatial reach doesn't, so its
   > derivative stays meaningful exactly where it's needed."*

3. **On the science:**
   > *"S3 was identical to S2 — my prediction term did nothing. I report that, and
   > diagnosing why is what produced S4's 20.6% gain."*
