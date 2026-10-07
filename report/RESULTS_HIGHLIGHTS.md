# Results Highlights — slide-ready

> Every number is measured from a run log under `results/run_logs/` and tagged with
> its `run_id`. Graphs are in `report/`. Full detail: `report/results_summary.md`,
> `report/PROJECT_EXPLAINER.md`.

---

## Headline 0 — Staged ablation: which component actually helps?

![Staged ablation](graph_staged_ablation.png)

Each stage adds **exactly one** component; everything else is held identical.

| Stage | D | Q | G | S | Waiting (s) | Throughput | Served | Unserved queues |
|---|---|---|---|---|---|---|---|---|
| S0 fixed-time | – | – | – | – | 2.36 | 73.8 | 132 | 4 / 4 |
| S1 Raza baseline | Y | – | – | – | 2.25 | **105.7** | **189** | 2 / 3 |
| S2 + queue | Y | Y | – | – | **1.69** | 73.2 | 131 | 3 / 3 |
| S3 + prediction | Y | Y | Y | – | **1.69** | 73.2 | 131 | 3 / 3 |
| S4 **proposed** | Y | Y | Y | Y | 2.14 | 88.3 | 158 | **2 / 3** |

**Talking point (the strongest in the project):** S3 is **bit-for-bit identical** to S2 —
the count-based forecast changed no decision, because it saturates at its configured
capacity and its derivative goes to zero. S4 applies the *same* prediction machinery to
the **spatial** occupancy, which does not saturate, and gains **+20.6% throughput and +20.6%
vehicles served**. That is a controlled comparison of one design decision, with a
negative result and a positive result that explain each other.

> **State the scope yourself, before you are asked.** These rows are the **107 s saturated
> clip**, where S3 and S4 differ in **one** selection decision. On both 240 s clips —
> including the held-out one — S4 was **completely inert** (0 decisions changed, identical
> metrics). Counting only greens the score actually decided, the term changed **1 of 9**
> decisions across all three clips. What this establishes is the **mechanism and the
> diagnosis**, not an average effect size. Full analysis: `multiclip_reproducibility.md`.

---

## Headline 1 — Adaptive control beats fixed-time on every clip

![Adaptive vs fixed](graph_adaptive_vs_fixed.png)

| Clip | Waiting: fixed -> adaptive | Throughput: fixed -> adaptive |
|---|---|---|
| development | 1.49 -> **1.02 s** (−32%) | 39.5 -> **49.8** veh/min |
| final | 1.10 -> **0.96 s** (−13%) | 34.2 -> **56.2** veh/min |
| busy | 2.36 -> **1.69 s** (−28%) | 73.8 -> 73.2 veh/min |

**Talking point:** the vision-based adaptive controller cuts waiting on all three
clips and raises throughput on the two clips where there is spare capacity. On the
busy clip it converts spare green into lower waiting at equal throughput.

---

## Headline 2 — The queue-aware parameter alpha traces a Pareto frontier, and fixed-time is dominated

![Alpha Pareto](graph_alpha_pareto.png)

On the busy clip, the three adaptive operating points (density-only, balanced,
queue-only) form a **fairness-vs-throughput trade-off curve**:

| Controller | Throughput (veh/min) | Avg waiting (s) |
|---|---|---|
| adaptive alpha=1.0 (density-only) | **105.7** | 2.25 |
| adaptive alpha=0.5 (balanced) | 73.2 | 1.69 |
| adaptive alpha=0.0 (queue-only) | 71.6 | **1.53** |
| fixed-time | 73.8 | 2.36 |

**Talking point (this is the strong one):** fixed-time is **dominated** — the
density-only adaptive controller delivers *higher throughput (105.7 vs 73.8) and
lower waiting (2.25 vs 2.36) at the same time*. And `alpha` is a single, interpretable
dial that lets an operator choose anywhere on the frontier: maximise throughput
(alpha=1) or minimise waiting/maximise fairness (alpha=0). That is a clean, defensible
research contribution.

---

## Headline 3 — Prediction only works once you change *what* is predicted

![Enhancement ablation](graph_enhancement_ablation.png)

This is Headline 0's mechanism, stated as a lever. **Read the two columns carefully: the
forecast (E8) is ON in both.** The only change is adding the **spatial spillback risk** term.

| | S3 — forecast on *count* | S4 — forecast + *spatial* risk |
|---|---|---|
| Throughput | 73.2 | **88.3** veh/min (+20.6%) |
| Vehicles served | 131 | **158** (+20.6%) |
| Over-saturated green phases | 3 / 3 | **2 / 3** |
| Avg waiting | 1.69 | 2.14 s — **worse by 27.1%** |

The forecast **is** genuinely predictive in the narrow sense — it projected a queue above the
currently-measured one in **~19%** of frame-approach observations on the busy clip (10.6% dev,
14.4% final), and its activity scales with congestion as a queue predictor should.

**But activity is not effect.** Despite firing in 19% of observations, it changed **no
decision at all**: stage S3 is bit-identical to S2. The diagnosis is the saturation argument —
the normalised count is pinned at 1.0 exactly when conditions are worst, so its derivative
is zero precisely when a forecast would be useful.

**Talking point:** the Wei-derived idea (predict, don't just react) is sound, but it is inert
on the state variable Raza's density/queue formulation gives you. It becomes a working lever
only on a state variable that keeps responding under saturation — the spatial extent. That is
why the negative result and the positive result belong in the same headline: neither means
much without the other. Implemented as vision-only trend projection: no MPC, no solver.

---

## Headline 4 — I found a defect in my own measurement, and the result survived it

Everything above rests on one vector pointing the right way: the **upstream direction** of each
approach. If it is reversed, an empty road reports as almost full. I built a diagnostic to test
that assumption instead of trusting it, and **it failed**.

| Approach | Queue_Region as % of ROI | Centroid separation | Usable? |
|---|---|---|---|
| North | 25.6% — correctly drawn strip | 132.6 px | yes |
| East | 56.1% — inset copy | 11.2 px | **no — direction REVERSED** |
| South | 51.7% — inset copy | 18.4 px | no |
| West | 48.8% — inset copy | **2.9 px** | no |

East's reversal was confirmed **four independent ways**, and again on a **second clip**. The fix
was to stop inferring the direction from hand-drawn polygons and instead **measure it from
vehicle motion** (`python -m src.main calibrate-axes`), taking each approach's heading from
where vehicles enter its ROI versus where they leave. Trustworthy axes went **1 of 4 → 3 of 4**,
with the fourth honestly declined for insufficient evidence rather than guessed.

Then the test that matters — re-running everything on corrected geometry:

| | Result |
|---|---|
| Frames where the spatial measure changed | **3,196 of 3,220** on North; up to Δ0.70 on East |
| S3 → S4 throughput | **+20.6% — identical** |
| Over-saturated greens | **3/3 → 2/3 — identical** |
| Open-loop invariants | queue 0.8276, stops 1066 — **unchanged** |

**Talking point:** the measurement moved on 99% of frames and the conclusion did not move at
all. The reason is traceable: S4's one differing decision selects **North — the single approach
that was correctly calibrated all along** — precisely because its count-based queue looked mild
(Q = 0.25) while its queue physically extended far back (X = 0.71). Finding a flaw in the
foundation of my own contribution and showing the result survives it is stronger evidence than
never having tested it. Full write-up: `axis_validation.md`.

**Also now guaranteed:** every run raises a `RuntimeWarning` naming any untrustworthy axis, and
`verify_invariants.py` re-checks eight signal-safety invariants across **54 runs / 226,972
frames** — zero violations, including "no NaN or out-of-range value has ever reached a result".

---

## The one honest caveat (say it first, before you're asked)

The footage is **recorded**, so the evaluation is **open-loop**: vehicles cannot
react to the simulated signal, so queue length and stop counts are fixed by the
video and only signal-gated metrics (waiting, throughput, served, over-saturation)
respond. We measure *how well each controller allocates green to the observed
traffic* — a fair, reproducible comparison — not a causal real-world reduction. A
causal claim would need closed-loop simulation (SUMO) or field deployment, both
outside this project's scope. **Framing mixed and negative results honestly, with the
reason why, is the mark of a sound study — and it is exactly what a viva rewards.**

---

## Numbers at a glance (for a summary slide)

- **Dataset:** Bellevue Traffic Video Dataset, City of Bellevue, WA — a **real public**
  dataset, not recorded or simulated by me. 116th Ave NE / NE 12th St, Sept 2017, 1280×720.
- **762** automated tests passing; **54 runs / 226,972 frames** invariant-checked, zero
  violations.
- **9** paper-derived enhancements plus my own spatial risk term, all config-gated and
  default-off.
- **3** validated clips (587 s), **1** camera, **4** approaches, **3** congestion levels.
- Adaptive vs fixed: **−32% / −13% / −28%** waiting across the three clips.
- Fixed-time is **Pareto-dominated** by density-only adaptive control.
- **The core finding:** projecting the queue *count* forward = **inert on all three clips**
  (S3 ≡ S2); projecting the *spatial extent* forward = **+20.6% throughput** on the saturated
  clip, over-saturated greens 3/3 → 2/3, at the cost of **+27.1% waiting**.
- **Scope, stated up front:** that gain is **1 changed decision out of 9** the score actually
  decided; inert on both 240 s clips. **Mechanism demonstrated, effect size not established.**
- Axis calibration: trustworthy axes **1/4 → 3/4**; headline result **unchanged** after the fix.
- Every figure traceable to a `run_id`; zero hand-entered results.
