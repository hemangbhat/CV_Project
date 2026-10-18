# Worked Example: from one camera frame to X, S and the score

Every number below comes from a real frame of the busy Bellevue clip (frame 2036,
t = 67.87 s, West approach), produced by the project's own code. Reproduce it, or do
the same on any other frame or approach:

```bash
python audit/explain_frame.py --frame 2036 --approach West      # this example
python audit/explain_frame.py --find --approach South           # finds another frame with a queue
```
The picture is saved to `report/explain_frame.png`: green boxes = stopped, grey =
moving, red dot = reference point, white line = queue axis, orange = stop-line strip,
red bar = measured queue tail X.

![frame 2036](../report/explain_frame.png)

---

## Step 0. What comes in from YOLO + ByteTrack

For each vehicle, every frame:

| Symbol | Meaning | Example (car #3) |
|---|---|---|
| `(x1, y1, x2, y2)` | Bounding box corners in pixels (top-left, bottom-right) | (227, 467, 374, 559) |
| `class` | car / truck / bus / motorcycle | car |
| `id` | ByteTrack's identity, the same for this car in every frame | 503 |

The **reference point** is the bottom-centre of the box, `((x1+x2)/2, y2)`: where the
wheels touch the road. For car #3: ((227+374)/2, 559) = **(300, 559)**. We use the
bottom, not the centre, because the bottom is on the road surface; the centre of a tall
truck floats above it.

The box **height** `h = y2 − y1` = 559 − 467 = **92 px**; width `w = x2 − x1` = 147 px.

---

## Step 1. The queue axis (drawn once per camera)

In `config/bellevue_116th_v2.json` the West approach has
`queue_axis: [[365, 530], [0, 640]]`: a line from the stop line (365, 530) back along
the road to the edge of the image (0, 640).

| Symbol | Meaning | Value |
|---|---|---|
| `A` | First point of the axis = the stop line | (365, 530) |
| `L_axis` | Total length of the axis in pixels | √(365² + 110²) = **381.2 px** |
| `u` | Unit vector along the axis, pointing upstream | (−365, 110) / 381.2 = (−0.957, 0.289) |

**Position of a car on the axis.** Project its reference point onto the line and measure
how far along it is:

```
t = (ref − A) · u                 (the dot product: how far along the line, in pixels)
p = t / L_axis                    (as a fraction: 0 = stop line, 1 = far end)
```

**Car #3, by hand:** ref − A = (300 − 365, 559 − 530) = (−65, 29).
t = (−65)(−0.957) + (29)(0.289) = 62.2 + 8.4 = 70.6 px → p = 70.6 / 381.2 = **0.185**. ✓

**Car #2, by hand:** ref = (133, 513); ref − A = (−232, −17).
t = 222.0 − 4.9 = 217.1 px → p = 217.1 / 381.2 = **0.570**. ✓

So "p = 0.57" literally means: *this car is 57% of the way from the stop line to the far
end of the visible road, measured along the road.* No camera calibration is needed,
because everything is a fraction of a line drawn on the image.

Code: `src/lane_analysis.py`, `_polyline_fraction` (projects onto the nearest segment if
the axis has several, so curved roads work too).

---

## Step 2. Is each car stopped?

```
speed = (distance the ref point moved over the last 1 s) / (1 s) / h
stopped  ⇔  speed < 0.2
```

| Symbol | Meaning |
|---|---|
| distance moved | Straight-line pixel distance between the ref point now and 1 s ago (30 frames). ByteTrack's `id` is what tells us which point was "this car" 1 s ago |
| `h` | The car's own box height in pixels: its size in the image |
| `speed` | In "box heights per second": how many of its own lengths it moved per second |
| 0.2 | The threshold: less than 0.2 of its own size per second = stopped (≈ 1 m/s for a 5 m car) |

| # | moved in 1 s | h | speed = moved / 1 / h | stopped? |
|---|---|---|---|---|
| 1 | 1.4 px | 74 | 1.4 / 74 = **0.019** | YES |
| 2 | 1.4 px | 58 | **0.024** | YES |
| 3 | 16.5 px | 92 | **0.179** | YES (creeping, still under 0.2) |
| 4 | 105.7 px | 80 | **1.321** | no (driving) |
| 5 | new track | 75 | — | no (needs half a second of history first) |

**Why divide by h?** A far car is small in the image. Moving 1 m covers few pixels, so a
fixed pixel threshold would call far moving cars "stopped". Dividing by the car's own
size puts near and far cars on the same scale. **Why over 1 s?** Boxes jitter by a few
pixels every frame; over a full second the jitter averages out. These two choices took
false stops from 4.28 to 0.76 per vehicle.

Code: `src/traffic_metrics.py`, `_is_stopped_windowed`.

---

## Step 3. The queue tail X

Take only the **stopped** cars, sort them by p, and walk upstream from the stop line:

```
start:    the first stopped car that is inside the stop-line strip (or within its allowance of p = 0)
continue: while  p_next − p_previous  ≤  allowance of the next car
X       = p of the last car in the chain        (0 if there is no chain)
```

| Symbol | Meaning |
|---|---|
| `allowance` | The biggest gap allowed between two cars that are still "one queue": `2 × (car's length along the road) / L_axis` |
| car's length along the road | `|w·ux| + |h·uy|`: how long the box is in the road's direction. Car #3: 147×0.957 + 92×0.289 = 140.7 + 26.6 = **167.3 px** |
| allowance of car #3 | 2 × 167.3 / 381.2 = **0.878** |

**The walk, on this frame:**

| Car | p | Check | Result |
|---|---|---|---|
| #3 | 0.185 | inside the stop-line strip | chain **starts**, X = 0.185 |
| #1 | 0.250 | gap 0.250 − 0.185 = 0.065 ≤ 0.710 | joins, X = 0.250 |
| #2 | 0.570 | gap 0.570 − 0.250 = 0.320 ≤ 0.540 | joins, **X = 0.570** |

**X = 0.57: the queue reaches 57% of the way back along the West road.** The red bar in the
picture sits at the back of car #2.

**Why "contiguous"?** If a parked or misjudged car stood alone at p = 0.95, a gap of
0.95 − 0.57 = 0.38 bigger than its allowance would stop the chain, so it could not
falsely push X to 0.95.

**Two honest observations from this frame:**
* All lanes are projected onto one axis. Car #4 (p = 0.48, moving, in another lane) lies
  between queue members but is ignored because it is moving.
* On the West leg the cars are seen side-on and the axis is short (381 px), so the
  allowance is large (0.5–0.9). The chain rule is loose here; on longer axes it is tighter.

Code: `queue_tail_reach_scaled` and `_extent_along` in `src/traffic_metrics.py`.

---

## Step 4. The spillback risk S

```
S = clamp( X + slope × H )          H = 5 s
slope = least-squares slope of X over the last 2.5 s (75 frames)
clamp keeps S between 0 and 1
```

| Symbol | Meaning | Value here |
|---|---|---|
| `X` | Queue tail now | 0.570 |
| `slope` | How fast X is changing, in "fraction of the road per second". Positive = queue growing backward | −0.0035 /s (essentially steady) |
| `H` | How far ahead we look | 5 s |
| `S` | Where the queue tail is projected to be in 5 s | 0.570 + (−0.0035)(5) = **0.552** |

**What "least squares" means:** fit the best straight line through the 75 recent X values
(one per frame) and take its slope. Using many points instead of two stops one noisy
frame from swinging S.

**Contrasting example (made up for practice):** if X had gone from 0.40 to 0.50 over 2.5 s,
the slope would be 0.04 /s and S = 0.50 + 0.04 × 5 = **0.70**: the queue is at half the road
and heading to 70%. If S reaches 1, the queue is projected to fill the visible road.

Code: `QueuePredictor._project_risk` and `_slope_per_second`.

---

## Step 5. The count measures and the score

| Symbol | Formula | Meaning | Value (West) |
|---|---|---|---|
| `D` | PCE-weighted count / saturation_count | How full the approach is by count. Car = 1, truck/bus = 3, motorcycle = 0.5. `saturation_count` = 12 for West, the most it holds | 5 cars / 12 = **0.417** |
| `Q` | cars in the stop-line strip / queue_capacity | How full the small zone at the stop line is | 1 / 4 = **0.250** |
| `F` | clamp(Q + slope_Q × 3 s) | Count forecast 3 s ahead | **0.630** (Q was rising) |
| `α` | weight between D and Q | | 0.5 |
| `ω`, `ρ` | weights of F and S in the proposed S4 score | | 0.3, 0.3 |

```
Score = (1 − ω − ρ) · (α·D + (1 − α)·Q) + ω·F + ρ·S
      = 0.4 × (0.5 × 0.417 + 0.5 × 0.250) + 0.3 × 0.630 + 0.3 × 0.552
      = 0.4 × 0.333 + 0.189 + 0.166 = 0.488
```

All four approaches on this frame:

| Approach | D | Q | X | S | Score |
|---|---|---|---|---|---|
| North | 0.42 | 0.50 | 0.35 | 0.16 | 0.253 |
| East | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 |
| South | 0.33 | 0.00 | **0.77** | 0.70 | 0.275 |
| West | 0.42 | 0.25 | 0.57 | 0.55 | **0.488** |

West has the highest score, so West would get the next green.

**Something to notice:** South has the longest queue on this frame (X = 0.77), but its Q is
0 and its F is 0, so its count terms are low and its score is only 0.275. That is the
spatial information X adds: by counts alone South looks empty at the stop line, but its
queue reaches 77% of the way back. Study 1 found that over whole simulations this rarely
changes which approach is chosen; study 2 uses S relative to each road's own length in
the green timing instead (`report/RESEARCH_PRESENTATION.md` §10 C3).

---

## Study 2's formula with the same numbers

```
score_i = D_i + λ ( 1/(α_b − S_i) − 1/α_b )      λ = 1, α_b = 1.1
```
| Symbol | Meaning |
|---|---|
| `λ` | How strongly the storage term counts |
| `α_b` | Slightly above 1, so the term stays finite when S = 1 |
| `1/(α_b − S) − 1/α_b` | Zero when the road is empty; grows slowly at first, then very fast as S nears 1 |

West: 0.417 + (1/(1.1 − 0.552) − 1/1.1) = 0.417 + (1.825 − 0.909) = **1.333**.
South: 0.333 + (1/(1.1 − 0.70) − 0.909) = 0.333 + (2.5 − 0.909) = **1.924**.
With the storage term, **South** (the road closer to filling up) would be served first.
(In study 2's simulation, D uses one common normaliser for every road; the numbers here use
the video config's per-approach values, so they illustrate the formula rather than reproduce
a simulation decision.)

And the timing rule: after 20 s of green on another road, the green ends early if some
waiting road has S ≥ 0.85. On this frame no road is at 0.85, so no green would be cut.

---

## Practise

1. Run `python audit/explain_frame.py --find --approach South` and redo Steps 1–4 by hand
   for that frame. Check your numbers against the printout.
2. In Step 3, what would X be if car #2 were moving? (Answer: 0.250.)
3. A car has h = 40 px and moved 10 px in 1 s. Stopped? (10/40 = 0.25 → no.)
4. X went 0.30 → 0.45 over 2.5 s. What is S? (slope 0.06/s → S = 0.45 + 0.30 = 0.75.)
5. Why is the reference point the bottom of the box and not its centre?

---

## Where each formula comes from

| Formula | Source | Borrowed vs chosen |
|---|---|---|
| D = Σ count × PCE | Raza et al. 2025, Eq. 1; PCE is a standard traffic-engineering unit | Normalising by the road's `saturation_count` (to 0–1) is this project's choice |
| Q = cars in stop-line strip / capacity | Queue-length-based control is common in the literature | Strip and capacity set by hand for this camera |
| α·D + (1−α)·Q | The original codebase's weighted average | α = 0.5 is a design choice |
| F = Q + slope × 3 s | Idea from Wei et al. 2025 (act on where the queue is heading); least-squares line extension is standard statistics | 3 s horizon is a choice |
| Stopped: moved < 0.2 box heights in 1 s | This project; dividing speed by object size is a common way to cancel perspective | 0.2 and 1 s fixed during the audit |
| X = end of the contiguous stopped chain | This project; inspired by Li et al. 2025's queue profile | "2 car lengths" gap is a choice |
| S = X + slope × 5 s | Same line extension as F, applied to X | 5 s horizon, 2.5 s window are choices |
| Score = 0.4·(αD+(1−α)Q) + 0.3F + 0.3S | Project design (weighted average) | Weights picked by hand and deliberately **not tuned** |
| Barrier 1/(α−S) − 1/α | Mohajerpoor et al. 2023, Eq. 17: Λ/(αΛ−δ) divided by Λ gives 1/(α − S) | The −1/α shift, λ = 1, α = 1.1 are choices |
| Protection: S ≥ 0.85 | Mohajerpoor et al. 2023, Eq. 9: queue ≤ β × link length | β = 0.85 and the 20 s guard chosen on validation seeds by a pre-written rule |
| Actuated gap-out | Standard practice (FHWA Signal Timing Manual) | 2 s passage time came from the project's own testing |
| Max pressure n/C | Gregoire et al. 2015 | Used as a baseline |
| Paired 95% t-interval | Standard statistics | — |
