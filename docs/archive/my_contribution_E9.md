# My Own Contribution — E9: Spatial Queue Reach

> This document covers the one enhancement that is **not** derived from any paper.
> E1–E8 are transfers from Raza (base), Li et al. and Wei et al. E9 is my own
> construction, and this file gives its motivation, derivation, formula, properties,
> honest novelty positioning, and measured result.

---

## 1. The problem I found in my own system

My controller scored each approach on a queue term:

```
normalized_queue_i  =  clamp( queue_count_i / queue_capacity_i , 0 , 1 )
```

`queue_count_i` counts tracked vehicles whose reference point falls inside a
hand-drawn Queue_Region polygon. `queue_capacity_i` is a number I chose by eye while
calibrating (3 or 4 vehicles per approach).

Two things are wrong with this, and both are **geometry** problems, not control
problems:

**Problem 1 — it saturates and goes blind.** Once the count reaches `queue_capacity`,
the measure reads exactly 1.0 and stops responding. Every subsequent vehicle joining
that queue changes nothing. The measure dies precisely when the situation is getting
worse. My own E5 over-saturation measurement exposed this concretely: the base
controller ends **3 of 3** green phases with the queue region still occupied, the
worst falling 6.0 s short of clearing — yet the queue term reported the same value
throughout.

**Problem 2 — it discards position entirely.** A count is a scalar. Three vehicles
bunched nose-to-tail at the stop line and three vehicles strung out along the whole
approach produce the **identical** measurement, even though the second case is a much
longer queue and a much worse condition.

**Problem 3 — the divisor is a guess.** `queue_capacity` cannot be derived from
anything; it is eyeballed per approach. Li et al. name exactly this as their fourth
criticism of prior work — the burden of hyperparameters "difficult to determine
theoretically" and needing costly field surveys.

---

## 2. Why the two TITS papers cannot solve it for me

Li et al. *do* measure queue length properly — as a physical distance in metres,
`ε`, derived from a fundamental diagram and an average space headway. But that
requires:

- camera-to-ground calibration to convert pixels to metres, which I do not have
- a constant assumed space headway (6 m) and vehicle length (3.5 m) — which is
  another homogeneous-fleet assumption, i.e. a new hyperparameter to replace the old
- connected-vehicle trajectory data for the flow parameters

Wei et al. track cumulative counts `N(k)` at link boundaries and never represent
individual vehicle positions at all.

**The key observation:** both papers work with aggregate flow variables and contain
**no computer vision whatsoever** — they explicitly assume a state-estimation module
supplies their inputs. So neither paper offers a way to measure queue extent from an
uncalibrated camera. But I have something they don't: **per-vehicle image positions
from ByteTrack, and ROI polygons that already describe the road geometry.**

That is the gap I fill.

---

## 3. My construction

### 3.1 The insight

The information needed to measure how far back a queue extends is **already in my
configuration file** and was never used. Each approach has two polygons:

- `roi_polygon` — the whole visible road area of that approach
- `queue_region` — the stop-line area inside it

The vector between their centroids *is* the direction of travel. If the Queue_Region
is the stop-line area (which it is, by definition), then the direction from the ROI
centre toward the Queue_Region centre points **downstream**, and its negation points
**upstream** — back along the queue.

So I can build a one-dimensional axis per approach and express any image point as a
fraction of how far back along the visible approach it lies. **No calibration, no new
parameter — the normalisation comes from the polygon the user already had to draw for
detection to work at all.**

### 3.2 The formulae

For approach *i* with ROI polygon `R_i` (vertices `v`) and queue region `Q_i`:

**(1) Upstream unit vector.** With `c_i` the ROI centroid and `o_i` the Queue_Region
centroid (the stop line):

$$
\mathbf{u}_i \;=\; \frac{\mathbf{o}_i - \mathbf{c}_i}{\lVert \mathbf{o}_i - \mathbf{c}_i \rVert}
\qquad\text{(downstream)}, \qquad
\hat{\mathbf{u}}_i \;=\; -\,\mathbf{u}_i \qquad\text{(upstream)}
$$

**(2) Approach extent.** How far the ROI itself reaches upstream of the stop line:

$$
L_i \;=\; \max\Big( \max_{\mathbf{v} \in R_i} \big[ (\mathbf{v} - \mathbf{o}_i)\cdot\hat{\mathbf{u}}_i \big] ,\; 0 \Big)
$$

**(3) Position fraction.** For any image point **p**:

$$
t_i(\mathbf{p}) \;=\; \mathrm{clamp}\!\left( \frac{(\mathbf{p} - \mathbf{o}_i)\cdot\hat{\mathbf{u}}_i}{L_i},\; 0,\; 1 \right)
$$

**(4) Queue reach.** With `S_i` the set of vehicles currently **stopped** on approach
*i* (from the frame-to-frame displacement test), and `p_j` vehicle *j*'s reference
point:

$$
\boxed{\;\mathrm{reach}_i \;=\; \max_{j \in S_i} \; t_i(\mathbf{p}_j), \qquad \mathrm{reach}_i = 0 \text{ if } S_i = \varnothing \;}
$$

**(5) Entry into the controller.** The reach joins the score as a convex term at
weight `ψ`:

$$
\mathrm{Score}_i \;=\; (1-\psi)\big[\alpha D_i + (1-\alpha)Q_i\big] \;+\; \psi\,\mathrm{reach}_i
$$

### 3.3 Why *stopped* vehicles and not *queueing* vehicles

This is a deliberate design choice with a consequence. `queue_count` only sees
vehicles inside the Queue_Region. The reach is taken over all **stopped** vehicles
anywhere in the ROI. So when a queue grows past the stop-line region, the count
cannot see it but the reach extends — which is exactly the blind spot from §1.

---

## 4. Properties I can state and defend

| Property | Why it holds |
|---|---|
| **Bounded by construction** | `t ∈ [0,1]` by the clamp, and `reach` is a max over such values, so `reach ∈ [0,1]`. The score stays a convex combination and the Green_Time bands remain valid. |
| **Never saturates prematurely** | The upper bound is *geometric* (the far edge of the ROI), not a guessed capacity. The measure keeps responding until the queue fills the entire visible approach. |
| **Position-sensitive** | Two configurations with equal counts but different extents give different values. Verified in `test_e9_reach_grows_where_the_count_is_blind`: count stays 1, reach moves 0.0 → 1.0. |
| **Adds no tunable** | `L_i` is derived from the ROI polygon; the only new number is the weight `ψ`, which is a blend share, not a physical quantity requiring a field survey. |
| **Calibration-free** | The fraction is relative to the approach's own visible extent, so no pixel-to-metre homography is needed. This is why it works where Li et al.'s metric queue length would not. |
| **Degrades safely** | If the two centroids coincide (degenerate geometry), `L_i = 0`, every fraction returns 0, and the measure is inert rather than raising. Verified in `test_e9_degenerate_geometry_is_inert_not_fatal`. |
| **Exact at the boundaries** | Stop line reads exactly 0.000; the furthest ROI vertex reads exactly 1.00. Verified on all four real approaches. |

---

## 5. Honest novelty positioning

I will not claim this is unpublished worldwide — traffic engineering is a large,
old field and I checked the literature before building it.

**What exists:** Vision-based queue length estimation is well studied, and Li et al.
themselves estimate queue length (in metres, via a fundamental diagram and assumed
space headway). Spatial queue measurement is not a new *goal*.

**What I searched:** per-vehicle delay fairness, perception-uncertainty-aware signal
control, self-calibrating saturation flow, and accumulated-deficit selection. Each
turned out to have close prior art (FairSCOSCA and DRL fair-signal work; the UCATSC
preprint; video SFR estimation studies; Deficit Round Robin and max-pressure
respectively). I rejected all four rather than present them as novel.

**What is mine in E9:**
1. Deriving the approach's flow axis from the **two polygons the configuration already
   contains**, so the measure is calibration-free and parameter-free.
2. Normalising queue extent by the **ROI's own upstream extent**, which is what
   removes the `queue_capacity` guess rather than replacing it with a headway guess.
3. Taking the max over **stopped** vehicles rather than queueing ones, so the measure
   deliberately extends past the Queue_Region — targeting the saturation blind spot I
   measured in my own system with E5.
4. Using it as a **control input** in the scoring function, not only as a reported
   statistic.

**The honest one-line claim:**
> *Vision-based queue length estimation is established, and Li et al. estimate queue
> length in metres using a calibrated fundamental diagram. My contribution is a
> calibration-free, parameter-free formulation that derives the queue's spatial extent
> from the ROI geometry the detector already requires, and feeds it to the controller —
> which is possible because my system has per-vehicle tracking and theirs does not.*

That is defensible, checkable, and does not overclaim.

---

## 6. What it fixes, traced to my own measurements

| Measured problem in my system | How E9 addresses it |
|---|---|
| E5 found **3/3** green phases ended with queue unserved, worst 6.0 s short | The reach keeps rising as the queue extends, so a backing-up approach keeps gaining score instead of plateauing |
| E2 (spillback) helped, showing vehicles **do** stop outside the Queue_Region here | E9 measures *how far* they extend, not just *that* they exist — a graded version of the same signal |
| `queue_capacity` was eyeballed at 3–4 per approach | E9's divisor is computed from the ROI polygon |
| Density-only control starved West (1.50 s wait, 0 served) | An approach with a long standing queue reads high reach even at low instantaneous density |

---

## 7. Measured result

Measured on `bellevue_116th_busy.mp4`, `α = 0.50`, `ψ = 0.40`, against the reference
config with everything else identical. Figures and `run_id`s in
`report/results_summary.md`.

Verified geometry on the real camera (all four approaches):

| Approach | Stop-line fraction | Max ROI-vertex fraction | Upstream extent |
|---|---|---|---|
| North | 0.000 | 1.00 | 540 px |
| East | 0.000 | 1.00 | 190 px |
| South | 0.000 | 1.00 | 299 px |
| West | 0.000 | 1.00 | 245 px |

The count-vs-reach demonstration (from `tests/test_tits_extensions.py`):

| Scenario | `queue_length` | `queue_reach` |
|---|---|---|
| One vehicle stopped at the stop line | 1 | 0.00 |
| Plus one stopped at the far end of the approach | **1 (unchanged)** | **1.00** |

That single comparison is the argument for the measure: the count cannot see the
second vehicle at all.

### 7.1 The headline evidence — the measure sees what the count cannot

**The reach reported more congestion than the count-based `normalized_queue` in
28.2% of all frame-approach observations** on the busy clip, and **12.6%** on the
lighter development clip. In more than a quarter of the busy run, vehicles were
stopped further back along the approach than the count inside the Queue_Region could
see. If the reach were merely restating the count, this figure would be 0%.

It was active (non-zero) in 54.5% of busy-clip cells and peaked at **0.94** — a queue
extending 94% of the way back along the visible approach.

Note the same scaling with congestion that E8 showed: the count is blind more often
under heavier traffic (12.6% → 28.2%), and the reach fills in more.

### 7.2 Effect on control — the `psi` sweep

| `psi` | avg wait (s) | throughput | served | over-saturated greens |
|---|---|---|---|---|
| 0.0 (reference) | **1.69** | 73.2 | 131 | 3 / 3 |
| 0.2 | 2.13 | 84.4 | 151 | 2 / 3 |
| 0.4 | 2.32 | 74.9 | 134 | **1 / 3** |
| 0.6 | 2.46 | **87.2** | **156** | 2 / 3 |

And on the development clip at `psi = 0.4`: throughput 49.8 → **52.3** veh/min,
served 199 → **209**, waiting 1.02 → 1.17 s.

**What holds at every weight and on both clips:**

- **Throughput and vehicles served rise** — up to +19% throughput and +25 vehicles on
  the busy clip, +5% and +10 vehicles on development.
- **Over-saturation falls** — 3/3 → 1/3 at the best weight. The controller finishes
  more of the queues it starts, which is precisely what E9 targets.
- **Average waiting rises** — monotonically in `psi`. E9 is a throughput-and-completion
  mechanism, not a waiting-reduction one.

**Recommended operating point:** `psi = 0.4` — lowest over-saturation while still
improving throughput and vehicles served.

### 7.3 Why waiting rises — the honest mechanism

The green-phase sequences show exactly what changed:

| configuration | GREEN phases |
|---|---|
| reference | North/30 s, North/45 s, **West**/45 s |
| E9 (`psi=0.4`) | North/30 s, **South**/30 s, North/45 s |

The reach term found a spatially long queue on **South** that the count-based measure
never ranked highly, and served it — South goes from **0 vehicles served to 37**. But
a 107 s clip holds only three green phases, so serving a newly-detected approach
necessarily means not serving another; West loses its single green and aggregate
waiting rises.

That is a property of the short clip, not a fault in the measure. E9 found real
stopped traffic that the previous measure could not see, and the controller acted on
it. Whether that is "better" depends on the objective: it is better on throughput,
vehicles served, and queue completion, and worse on average waiting.

Full tables and every `run_id` are in `report/results_summary.md`.

---

## 8. Where the code is

| Part | File |
|---|---|
| Geometry: `ApproachAxis`, `build_approach_axes` | `src/lane_analysis.py` |
| Measurement: `queue_reach` on `ApproachMetrics`, computed in `MetricsEngine.update` | `src/traffic_metrics.py` |
| Score term (`ψ`): `compute_score_weighted`, `compute_config_scores` | `src/traffic_metrics.py` |
| Config: `use_queue_reach`, `queue_reach_weight` + validation | `src/config.py` |
| Run log field | `src/results_store.py` |
| Tests (24 for E9) | `tests/test_tits_extensions.py` |
| Experiment config | `config/bellevue_116th_reach.json` |

---

## 9. How to explain it in one minute

> "My queue measure was a count of vehicles inside a hand-drawn box, divided by a
> capacity number I guessed. That has two flaws: it saturates — once it hits the
> capacity it reads 1.0 and stops responding even as the queue keeps growing — and it
> throws away position, so three cars at the stop line looks the same as three cars
> spread down the road.
>
> Both are geometry problems, and the geometry was already sitting in my config file
> unused. Each approach has two polygons: the road area and the stop-line area. The
> line between their centres gives me the direction of travel. So I project each
> stopped vehicle onto that axis and measure how far back the queue reaches, as a
> fraction of the approach's own visible length — 0 at the stop line, 1 at the far end.
>
> It needs no camera calibration and adds no parameter to tune, because the
> normalisation comes from the polygon the detector already needed. Li et al. measure
> queue length in metres, but that needs camera calibration and an assumed vehicle
> spacing. Neither TITS paper has any computer vision at all — they assume the
> measurement exists. Mine produces it."
