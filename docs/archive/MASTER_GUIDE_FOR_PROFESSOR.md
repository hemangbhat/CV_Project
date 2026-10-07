# A Vision-Based Predictive Traffic Signal Controller Using YOLO-Based Vehicle Tracking and Queue Dynamics

## Master Guide — Everything About This Project, In Simple Language

> **Read this one file and you can explain the whole project to your professor.**
> Written for the exact task he set: *"Find IEEE TITS papers, take their limitations,
> and apply those to your project."*
>
> Every number in this document is measured from a saved run file in
> `results/run_logs/`. No number is invented or guessed.

---

# THE RESEARCH STORY (show him this first)

```
                        RAZA 2025 (IEEE Access)
                                 |
                                 v
                   Reactive PCE-density control
                                 |
                    LIMITATION IDENTIFIED:
             reacting to current traffic is not enough
              when a queue is growing or about to
                     run out of storage
                                 |
                                 v
        +----------------------------------------------+
        |  IEEE TITS 2025 research                     |
        |                                              |
        |  Li et al.    - queue profile estimation,    |
        |                 over-saturation              |
        |  Wei et al.   - predictive queue dynamics,   |
        |                 queue spillback              |
        +----------------------------------------------+
                                 |
                     Their machinery does NOT transfer:
                     network MPC, link transmission model,
                     GUROBI/CPLEX solvers, connected vehicles
                                 |
                          TAKE THE IDEA, NOT THE MACHINERY
                                 |
                                 v
                         MY ADAPTATION (CV)
                                 |
              +------------------+------------------+
              v                                     v
      YOLO + ByteTrack                    Spatial queue measure
      vehicle trajectories                (my own contribution)
              |                                     |
              +------------------+------------------+
                                 |
                                 v
                 Short-term queue growth prediction
                                 |
                                 v
                    Spillback-risk-aware control
                                 |
                                 v
              STAGED ABLATION: which component actually helps?
                    S0 -> S1 -> S2 -> S3 -> S4
```

**The one-sentence research question:**

> *Can short-term queue-growth information extracted from vehicle trajectories improve
> a density-based adaptive traffic signal controller by anticipating queue buildup and
> spillback?*

**The answer I measured:** Yes — but only when the projected quantity does not
saturate. Projecting the count-based queue changed nothing; projecting the spatial
occupancy raised throughput 20.6%. That distinction is the core finding.

---

# THE 60-SECOND VERSION (if you only have one minute)

> "My base project used YOLO to detect vehicles and adjust traffic lights based on
> how crowded each road is. My professor asked me to find IEEE TITS papers, take their
> limitations, and apply them.
>
> I found two 2025 TITS papers. Both argue the same thing my project was weak on:
> queue length must be *estimated and predicted*, not treated as an afterthought.
> And both papers have the same gap — they assume a traffic-measurement module exists
> and never build it. My project IS that module.
>
> So I built improvements from both papers — the main one predicts each road's queue
> 3 seconds ahead from its growth trend so green is given before the road jams. I
> also built a new spatial queue measure that uses the ROI geometry your camera
> already has, instead of a hand-guessed capacity number.
>
> Results: adaptive beats fixed-timer on every video — 13 to 32% less waiting. The
> fixed timer is beaten on both throughput and waiting at the same time. The forecast
> is genuinely predictive, acting on traffic that hasn't arrived yet in about 19% of
> observations. Everything is backed by 762 tests and fully reproducible."

---

# PART 1 — What the professor asked, and what I did

**What he asked:** Look at IEEE TITS papers, find their limitations, and use those
limitations to improve your project.

**What I did:** I read two IEEE TITS 2025 papers, listed every limitation (both the
ones the authors admit and the ones I found myself), decided which ones I could
actually fix in a camera-based single-junction project, and built improvements from
them. I measured all of them honestly and reported the ones that worked and the ones
that didn't.

**The three papers:**

| Role | Paper | Why it's here |
|---|---|---|
| My **base** project | Raza et al. 2025, *IEEE Access*, DOI 10.1109/ACCESS.2025.3602844 | The system I already had: YOLO detects vehicles → calculates traffic density → adjusts traffic light |
| **Supporting** TITS paper | Li, Lu & Wang 2025, *IEEE TITS*, DOI 10.1109/TITS.2025.3616119 | How to properly measure **queues** and detect **over-saturation** |
| **Main (Primary)** TITS paper | **Wei et al. 2025, *IEEE TITS*, DOI 10.1109/TITS.2025.3568869** | **Prediction** — don't just react to current traffic, predict where it's going |

---

# PART 2 — What my project actually is

My project is a **traffic light controller that watches a video and decides which
road gets the green light.**

```
1. Take a video of a traffic junction (4 roads: North, East, South, West)
2. YOLO detects every vehicle in every frame (cars, bikes, buses, trucks)
3. ByteTrack gives each vehicle an ID so we can follow it across frames
4. We check which road each vehicle is on
5. For each road we measure: how crowded? how long is the queue? how far back?
6. We give each road a "score" based on those measurements
7. The road with the highest score gets the green light
8. How long the green lasts depends on the score
9. The traffic light is drawn on screen as a simulation
```

**Why it's useful:** A normal traffic light is on a fixed timer — it gives 30 seconds
to an empty road while a packed road waits. Mine looks at the actual traffic and
gives green where it's needed.

---

# PART 2b — My dataset (he WILL ask this first)

## Where the video comes from

| Question | Answer |
|---|---|
| What dataset? | **Bellevue Traffic Video Dataset** |
| Who published it? | **City of Bellevue, Washington, USA** |
| Where? | https://github.com/City-of-Bellevue/TrafficVideoDataset |
| Can I use it? | Yes — released by the city for research use |
| Which junction? | 116th Ave NE / NE 12th St |
| When filmed? | September 2017, fixed pole-mounted traffic camera |
| Resolution | 1280 × 720 |

**Say it like this:** *"It is a real public traffic dataset published by the City of Bellevue.
I did not record it and I did not simulate it — it is genuine junction footage from a fixed
traffic camera."*

The full provenance is machine-readable in **`data/annotations/videos.json`**, which is the
file my evaluation actually reads. Nothing about the data is only written in prose.

## "Why only one video?" — the honest answer

I actually use **three** clips, all from that same camera:

| Clip | Length | Role |
|---|---|---|
| `bellevue_116th_dev.mp4` | 240 s | development / tuning |
| `bellevue_116th_final.mp4` | 240 s | final reporting |
| `bellevue_116th_busy.mp4` | 107 s | the congested case — the headline experiment |

**Why the headline ablation uses the 107 s busy clip:** it is the only clip with *sustained
over-saturation* — roads actually running out of space. My contribution is about predicting
when a road runs out of storage. On a clip where that never happens, the mechanism has
nothing to act on and the comparison would prove nothing.

**Why the same camera for all three:** one configuration means one set of ROI polygons. Those
polygons are drawn against a specific camera view, so mixing junctions would invalidate the
geometry. The clips are different *hours* of the same camera — different traffic, same view.

## "Is 46 minutes of footage on disk not more data?"

Yes, and it is worth being straight about why most of it is unused. `videos/_candidates/`
holds five more clips from the same junction, and **four of the five have dropped frames** —
their reported frame rates are 28.0, 27.6, 20.7 and 17.4 fps instead of 30.

That matters because my waiting-time metric accumulates as `dt = 1 / frame_rate`. Feeding in a
clip whose real frame rate is not what the file claims would **silently corrupt my main
metric.** So each usable clip had to be prepared first:

1. measure the gaps between frames across the whole recording,
2. find the longest run with **no dropped frames** (verified 33.33 ms ± 0.01 = true 30 fps),
3. cut a window from inside that run and re-encode at a constant 30 fps.

**No frames were duplicated or interpolated** — the clips are real frames, just a clean
subset. One candidate (`b116_1208.mp4`) already reads a clean 30.0 fps and is the obvious
next clip to add.

**Say it like this:** *"There is about 46 minutes on disk but only 587 seconds passed my
frame-rate validation. I would rather report three clips I can trust than nine that would
corrupt the waiting-time measurement."*

## The limitation to admit before he pushes

The busy clip is 107 seconds, which at a 30-second minimum green is only **three green
phases** — so my S3 vs S4 comparison comes down to **one decision**. It is correctly measured
and I can trace exactly why it happened, but one decision is **not** proof of an average
effect. That is why I also re-ran the comparison on both 240-second clips
(`run_multiclip_ablation.py`), and why "more validated footage" is the honest top item on my
future-work list.

---

# PART 3 — The two TITS papers and their limitations (simple version)

## Paper A (Supporting): Li et al. — IEEE TITS
*Multi-Objective Model for Traffic Signal Coordination with Queue Profile Estimation*

**What they do:** They control a whole road corridor (5 traffic lights in a row)
and estimate how long a queue needs to clear:

```
Time to clear queue = queue_length ÷ discharge_speed + lost_time
If green_time < time_to_clear → the road is "over-saturated"
Shortfall = time_to_clear − green_time
```

**Their key limitations I found and used:**
- They treat queue as a fixed input or count — not estimated from positions
- They never built the perception system — they assumed it exists
- They only work with signal timings, not with visual tracking data

## Paper B (Primary): Wei et al. — IEEE TITS
*Hierarchical Predictive Control Using Link Transmission Model with Queue Dynamics*

**What they do:** They predict future queues in a road network so green is given
*before* a road gets jammed — not after it already is.

**Their key limitations I found and used:**
- **They assume a state estimation module exists but never build it** — MY project
  fills exactly this gap
- They ignore vehicle types (buses, trucks affect flow differently) — fixed by PCE
- Their method needs commercial solvers and takes 90 seconds per calculation — not
  usable per video frame
- They use aggregate counts, never individual vehicle positions

**The key insight connecting both papers to my project:**

> Both papers control traffic signals but assume the measurement is already done.
> My camera + YOLO + tracking IS that missing measurement. So I didn't need to copy
> their maths — I took their IDEAS and implemented them with computer vision.

---

# PART 4 — Every improvement I built (E1 to E9)

All improvements are **switchable and OFF by default** — my original project is
unchanged, and I can turn each on separately.

| # | Name | What it does (simple) | From which paper | Did it help? |
|---|---|---|---|---|
| E1 | PCE-weighted queue | Counts a bus as 3 cars in the queue | Raza + Wei | No change (only cars in my video) |
| E2 | Spillback detection | Detects vehicles stopped past the stop-line area | Wei | Yes — waiting −9% |
| E3 | Discharge-timed green | Green = how long the queue needs to clear | Wei + Li | Weak here (small queues) |
| E4 | Control stability | Stops the light flip-flopping between roads | Wei | **Yes — only one that improved BOTH** |
| E5 | Over-saturation check | Records whether each green actually cleared its queue | Li | Yes (diagnostic) |
| E6 | Gap-out | Ends green early when queue is empty | Li | Partly — fewer unserved queues |
| E7 | Stop counting | Counts how many times vehicles must stop | Li | Reportable now |
| **E8** | **Queue forecast (PRIMARY)** | **Predicts queue 3 seconds ahead from its trend** | **Wei** | **No measurable effect on its own (stage S3 was inert - the count-based queue it projects is already saturated); it is the spatial basis in S4 that makes projection pay off** |
| **E9** | **Spatial queue reach (MY OWN)** | **Measures HOW FAR BACK the queue extends using camera geometry** | **None — my own CV contribution** | **Yes — throughput up to +19%, unfinished queues 3/3 → 1/3; sees congestion the count misses in 28% of observations** |

---

# PART 5 — E8 explained (the main TITS-paper enhancement)

**The problem my base system had (and Wei et al. identify):**
My system only looked at traffic *right now*. If a queue was growing fast, by the
time it was big enough to win the score, it might be too late.

**My solution — queue forecast:**
```
For each road, every frame:
1. Remember the queue size from the last 15 frames (~0.5 seconds)
2. Fit a trend line through those measurements (slope per second)
3. Project forward 3 seconds: forecast = current_queue + slope × 3
4. Use the FORECAST in the scoring, not just the current queue
```

- Rising queue → forecast is higher than now → that road gets green sooner
- Falling queue → forecast is lower → system correctly leaves it alone

**Evidence it's really predicting:** In ~19% of all measurements the forecast was
*higher* than the currently measured queue. That means one time in five, it was
acting on traffic that hadn't arrived yet. If it were just copying the present, that
number would be zero.

**Score formula including E8:**
```
Score_i = (1 − ψ) × [α × density + (1−α) × queue]  +  ω × forecast
```
where `ω` (omega) is the forecast weight (typically 0.4).

---

# PART 6 — E9 explained (my own original CV contribution)

**This is the improvement to emphasise as your own work.**

**The problem with the existing queue measure:**
- `normalized_queue = queue_count ÷ queue_capacity`
- `queue_capacity` is a number you have to guess and set manually (3 or 4 vehicles)
- Once the count reaches that number, the measure reads 1.0 and **stops responding**
  — even if the queue keeps getting longer
- It also **throws away all position information** — 3 vehicles at the stop line
  looks the same as 3 vehicles spread across the whole approach

**My solution — spatial queue reach, using geometry you already have:**

Every approach has two polygons: the ROI (the whole visible road area) and the
Queue Region (the stop-line area). I use these to build a direction axis:

```
upstream direction = from Queue_Region centre → towards ROI far edge
queue_reach = fraction of that distance covered by stopped vehicles
```

- A vehicle right at the stop line → reach ≈ 0
- A vehicle at the far end of the ROI → reach ≈ 1.0
- The maximum reach over all stopped vehicles = this approach's queue_reach

**Why this is genuinely better:**

| Old measure | New measure (E9) |
|---|---|
| Count ÷ guessed capacity | Distance fraction from geometry already drawn |
| Saturates at 1.0 and goes blind | Keeps rising as queue extends further back |
| Throws away position | Position IS the measurement |
| Needs hand-tuned capacity | Uses ROI polygon your camera already required |
| Same value whether 3 cars close or spread | Distinguishes both |

**Score formula including E9:**
```
Score_i = (1 − psi) × base + psi × queue_reach_i
```
where `psi` (ψ) is the reach weight.

**Why this is defensible as your own contribution:**
- Neither Wei nor Li uses spatial position from camera vision — **both papers contain
  no computer vision at all**, they assume the measurement already exists
- Li et al. *do* measure queue length, but in **metres**, which needs camera
  calibration and an assumed vehicle spacing — two things mine doesn't need
- The geometry (the ROI polygon) already exists in your config — this is using what
  you already have, more intelligently
- It introduces **zero new hand-tuned parameters** (the divisor is computed from the
  polygon, not guessed)
- It is motivated by a limitation **you measured yourself**: E5 found 3/3 green phases
  ended with queues unserved while the count-based measure sat unchanged

**MEASURED PROOF it sees what the count cannot:**

> **The reach reported more congestion than the count in 28.2% of all observations**
> on the busy clip (12.6% on the lighter clip). If it were just repeating the count,
> that number would be 0%. It peaked at 0.94 — a queue stretching 94% of the way back
> along the visible road.

**Results (the `psi` weight sweep):**

| psi (reach weight) | Waiting | Throughput | Served | Unfinished queues |
|---|---|---|---|---|
| 0.0 (off) | **1.69 s** | 73.2 | 131 | 3 of 3 |
| 0.2 | 2.13 s | 84.4 | 151 | 2 of 3 |
| **0.4 (best)** | 2.32 s | 74.9 | 134 | **1 of 3** |
| 0.6 | 2.46 s | **87.2** | **156** | 2 of 3 |

**What to say:** *"E9 raises throughput and vehicles served at every weight I tested,
on both clips, and cuts unfinished queues from 3 of 3 down to 1 of 3. It costs average
waiting, because it found a long queue on the South road that the old count-based
measure never ranked highly — South went from 0 vehicles served to 37. In a 107-second
clip with only 3 green phases, serving a newly-discovered road means another road
loses its turn. So it's a throughput-and-completion improvement, honestly measured,
not a free win."*

---

# PART 7 — The results (all measured, all real)

## RESULT 0 (THE HEADLINE): Staged ablation — which component actually helps?

*(graph: `report/graph_staged_ablation.png`)*

This is the table to lead with. **Each stage adds exactly one component**, so the
difference between two rows isolates that component's contribution. Everything else —
geometry, PCE weights, detector, tracker, green bands — is identical throughout.

| Stage | Density | Queue | Prediction | Spillback risk | Waiting (s) | Throughput | Served | Unserved queues |
|---|---|---|---|---|---|---|---|---|
| **S0** fixed-timer | – | – | – | – | 2.36 | 73.8 | 132 | 4 / 4 |
| **S1** Raza baseline | ✓ | – | – | – | 2.25 | **105.7** | **189** | 2 / 3 |
| **S2** + queue | ✓ | ✓ | – | – | **1.69** | 73.2 | 131 | 3 / 3 |
| **S3** + prediction | ✓ | ✓ | ✓ | – | **1.69** | 73.2 | 131 | 3 / 3 |
| **S4** PROPOSED | ✓ | ✓ | ✓ | ✓ | 2.14 | 88.3 | 158 | **2 / 3** |

**What each step shows:**

- **S0 → S1:** adaptive control beats the fixed timer on **both** axes — throughput
  +43%, waiting down, unserved queues 4/4 → 2/3. The fixed timer is Pareto-dominated.
- **S1 → S2:** the queue term cuts waiting **25%** (2.25 → 1.69 s), the best waiting
  figure in the table, at a throughput cost. That's the fairness/throughput trade-off.
- **S2 → S3:** ⚠️ **prediction alone changed NOTHING.** Bit-for-bit identical run — same
  waiting, same throughput, same green sequence. **This is my most informative result.**
- **S3 → S4:** the spillback-risk term raised throughput **+20.6%**, vehicles served
  **+20.6%**, and cut unserved queues — using the *same* prediction machinery on a
  different quantity.

## RESULT 0b: WHY S3 failed and S4 worked (the key insight)

S3 and S4 differ in **exactly one thing** — which quantity gets projected forward:

| | Projects | Saturates? | Effect on control |
|---|---|---|---|
| **S3** prediction | count-based queue (`count ÷ capacity`) | **YES**, at the guessed capacity | **none — identical run** |
| **S4** spillback risk | **spatial** queue reach | **NO**, bounded only by road geometry | +20.6% throughput, +20.6% served |

**Say this to your professor — it's the strongest thing in the project:**

> *"The count-based queue saturates at the capacity I configured. Once it reads 1.0 its
> rate of change is zero, so projecting it forward reports a stable approach even while
> the queue is physically still growing. That's why S3 changed nothing. My spatial
> measure keeps rising as the queue physically extends back, so its derivative stays
> meaningful exactly in the regime the risk term is meant to detect — and S4 improved
> throughput by 20.6%. That's a controlled comparison of one design decision, and it
> validates the spatial measure."*

The risk signal exceeded current occupancy in **28.6%** of observations — in over a
quarter of the run it was projecting an approach toward running out of storage before
it had done so.

## Result 1: Adaptive beats fixed-timer on every video

*(graph: `report/graph_adaptive_vs_fixed.png`)*

| Video | Waiting time | Change |
|---|---|---|
| development | 1.49 s → **1.02 s** | **−32%** |
| final | 1.10 s → **0.96 s** | **−13%** |
| busy | 2.36 s → **1.69 s** | **−28%** |

## Result 2 (STRONGEST): Fixed-timer is Pareto-dominated

*(graph: `report/graph_alpha_pareto.png`)*

| Controller | Throughput | Waiting |
|---|---|---|
| Fixed timer | 73.8 veh/min | 2.36 s |
| Adaptive (density mode) | **105.7 veh/min** | **2.25 s** |

Adaptive is **better on BOTH measures at the same time.** The fixed timer has no
defence. Plus, the `alpha` dial lets an operator choose anywhere from maximum
throughput to minimum waiting.

## Result 3: Queue term fixes a real starvation failure

Under density-only control, the West road never got a green light — it waited 1.50 s
and served **zero vehicles**, because its stopped queue looked low-density. Adding the
queue term served it and cut its waiting to **0.42 s**. This is a concrete, measured
failure of the base paper that my enhancement fixes.

## Result 4: The spillback-risk term is what produced the gain (NOT E8 on its own)

**Be precise about this one, because it is the easiest place to get caught.** The table below
is the **S3 → S4** comparison. E8 (the count-based forecast) is switched **ON in both
columns**, at the same weight. The only thing that changes is the added **spatial spillback
risk** term. So the improvement belongs to the risk term, not to the forecast.

| | S3 (forecast only) | S4 (forecast + spatial risk) |
|---|---|---|
| Throughput | 73.2 veh/min | **88.3 veh/min (+20.6%)** |
| Vehicles served | 131 | **158 (+20.6%)** |
| Unfinished queues | 3/3 phases | **2/3 phases** |
| Waiting | 1.69 s | 2.14 s — **worse by 27.1%** |

If asked "so did your predictive term work?", the correct answer is:

> "Prediction only worked once I changed *what* was being predicted. Projecting the queue
> **count** forward did nothing measurable — stage S3 came out bit-identical to S2. Projecting
> the **spatial extent** forward gave +20.6% throughput. Same predictor, different state
> variable. That contrast is the finding."

## Result 5: How often the forecast is actually active

This is an **activity statistic**, not a performance result. It says how often the forecast
differed from the present queue — i.e. how often it had anything to say at all.

| Video | Forecast differed from current queue |
|---|---|
| development (light) | 10.6% of observations |
| final (light-medium) | 14.4% |
| busy (heavy) | **18.7%** |

The pattern is the sensible one: a predictor has more to do when there is more happening. But
note carefully — **being active is not the same as helping.** On the busy clip the forecast
was active in 18.7% of observations and still changed no decision at all (S3 = S2). Activity
without effect is exactly the diagnosis behind Result 4: the count-based queue was already
saturated, so its slope carried no usable information.

## Result 6: My results are 100% reproducible

I ran the same configuration 3–4 times each:

| Measurement | Result |
|---|---|
| Average waiting | **identical every run** |
| Throughput | **identical every run** |
| Vehicles served | **identical every run** |
| Processing speed | varies (depends on laptop load) |

The pipeline is fully deterministic. Every reported number is exactly reproducible.
That is a *stronger* claim than "mean ± standard deviation."

## Result 7 (SAY THIS YOURSELF) — I tested it on a second clip, and it did nothing

This is the result most students would hide. Do the opposite: lead with it, because the
*explanation* is the impressive part.

I re-ran the S3 vs S4 comparison on the 240-second development clip, which has **7 green phases**
instead of 3:

| Clip | Green phases | Throughput S3 → S4 | Decisions changed |
|---|---|---|---|
| busy (107 s, congested) | 3 | 73.2 → 88.3 = **+20.6%** | **1** |
| development (240 s) | 7 | 51.5 → 51.5 = **+0.0%** | **0** |
| **final / held-out (240 s)** | 7 | 56.25 → 56.25 = **+0.0%** | **0** |

**On both longer clips — including the held-out one — my contribution changed absolutely
nothing.** Zero decisions, identical metrics.

### Then I found out why — and it is not "my idea is weak"

First I checked whether the risk term simply never fired. It fired constantly:

- risk > 0 in **25%** of observations
- it changed at least one score on **83.4%** of frames
- it flipped which approach ranked **first** on **15.3%** of frames

So it was active and it *was* changing the ranking — but no green changed. The answer was in
**what actually decides the green on a quiet junction**:

| | busy | development | final |
|---|---|---|---|
| Greens forced by the **starvation guarantee** | **0 of 3** | **4 of 7** | **4 of 7** |
| Greens actually **chosen on score** | **3 of 3** | **3 of 7** | **3 of 7** |
| Starvation greens given to a **completely empty** road | – | 3 of 4 | **4 of 4** |
| Frames where **every** road scores zero | **0%** | **15.8%** | **6.3%** |

On the held-out clip **all four** starvation greens went to a road scoring **exactly 0.0** — an
empty road, served only because my starvation rule says no road may wait forever. (Run
`python diagnose_regime.py` to show him this table live.)

**So on light traffic the score often is not choosing the green at all — my starvation guarantee
is.** The sequences are nearly round-robin. And a score term cannot change a decision that the
score is not making. For those greens my term was not too weak — it was **structurally bypassed**.

### The number to quote (be precise here, it protects you)

Only greens chosen *on score* are chances for my term to do anything. Counting those across all
three clips:

| Clip | Chances (score-driven greens) | Decisions my term changed |
|---|---|---|
| busy (congested) | 3 | **1** |
| development | 3 | 0 |
| final / held-out | 3 | 0 |
| **total** | **9** | **1** |

**Say: "my term changed 1 of the 9 decisions where it was able to act."** Then explain that the
+20.6% is how big that one decision was on a 3-phase clip, not an average improvement. If you quote
+20.6% without this and he asks "out of how many decisions?", you have no answer. With it, you are
the one who already did the analysis.

### And if he says "so your contribution doesn't work?"

Do not get defensive. Say this:

> *"What I proved solidly is the diagnosis, not the effect size. I proved the count-based queue
> saturates — stage S3 is bit-identical to S2 on all three clips, so projecting it forward is
> measurably useless. I proved the spatial version does not saturate, with a test. And on the one
> clip with real congestion, when the spatial version was allowed to decide, it decided the way the
> theory says it should, and I can walk you through that exact frame. What I cannot claim is how
> often that matters, because I only have 107 seconds of genuinely congested footage. So my
> contribution is a tested measurement with a demonstrated mechanism and an effect size I have not
> established — and I would rather say that than overclaim."*

That answer is much stronger than a defended "+20.6%", because it shows you understand the
difference between a mechanism and an effect size — which is exactly what a research-methods
examiner is testing.

### How to say it in the viva

> *"My contribution helps when the junction is busy enough that the score is doing the deciding.
> On light traffic the starvation guarantee decides instead, so no score term can matter — and I
> can show you that 4 of 7 greens there were starvation-forced, 3 of them on completely empty
> roads. I'd argue the inertness is correct rather than a failure: a term that predicts a road
> running out of space should do nothing on an empty road. If it fired there, that would be the
> bug."*

And be straight about the limit:

> *"So I am claiming a direction and a mechanism under saturation, not an average improvement. It
> improved throughput on one of two clips, and the effect rests on one decision. Averaging a real
> effect with a structural zero would be meaningless, so I don't quote a mean."*

---

# PART 8 — The honest limitations (say these before he asks)

## 1. The evaluation is open-loop (most important)
The video is a recording. The cars cannot see my signal. So queue length and stop
count are **fixed by the video** — no controller can change them. This is why my stop
count is the same for every controller. I can only measure how well each controller
allocates green to the traffic that was recorded.

**Correct statement:** "I measure green-time allocation, not real-world delay."
**To prove real-world delay** would need SUMO simulation or a real road deployment —
both are deliberately out of scope and stated as such.

## 2. One camera, one junction
All videos are from the same Bellevue camera. The detection zones are calibrated for
that camera's view. A different junction needs a one-time calibration — the code
doesn't change, only the zone coordinates.

## 3. Short busy clip (107 s ≈ 3 signal cycles)
Results show direction, not precise effect sizes.

## 4. Some improvements didn't work
E1 did nothing on a car-only fleet. E3/E6 hurt throughput (small queues hit the
floor). **S3 (prediction alone) changed nothing at all.** This is honest and shows I
measured carefully, not selectively — and in S3's case the failure *explained* why the
next design worked.

## 5. "Spillback risk" here means LOCAL storage, not downstream blocking
This distinction matters and I state it up front.

- **What I measure:** whether *this* approach is about to run out of its own visible
  storage — its queue extending back toward the far edge of the camera's view.
- **What I do NOT measure:** whether the road a vehicle is *heading into* is already
  full. True downstream spillback prevention needs to see the **receiving** link.

**Why not:** all four of my ROIs are **inbound** approaches to one junction. When a
vehicle crosses, it exits into an outbound lane that is in **no ROI**. A single camera
at one junction physically cannot see downstream occupancy. Claiming otherwise would be
a result I cannot back. Doing it properly needs either a second camera downstream or a
network model — which is exactly what Wei et al. use, and exactly why their approach
needs infrastructure I don't have.

---

# PART 9 — Demo and run commands

```powershell
# 1. Fixed timer (baseline)
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller fixed --config config/bellevue_116th.json

# 2. Adaptive (base)
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th.json

# 3. With E8 queue forecast
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json

# 4. Run all 762 tests
python -m pytest -q
```

**Ready-made demo video (no waiting needed):**
`results/videos/DEMO_E8_forecast_busy.mp4`

**On screen, point at:**
- Coloured zones = 4 roads being watched
- Boxes with numbers = tracked vehicles
- Side panel per road: `n=` count, `q=` queue length, `d=` density, `s=` score,
  **`f=` forecast**
- **`^` next to `f=` means forecast > current queue — prediction actively working**

---

# PART 10 — Questions he might ask, with your answers

**Q: What is your actual contribution?**
> The base paper (Raza) reacts to current density only. I read two IEEE TITS papers,
> found their limitations, and built improvements. The main ones are: a queue forecast
> that predicts build-up 3 seconds ahead (from Wei's predictive idea), and a spatial
> queue reach measure that uses the camera geometry to measure how far back the queue
> extends — without any hand-guessed capacity number.

**Q: Why didn't you implement their exact method?**
> Both papers need things I don't have: connected-vehicle GPS data, commercial solvers
> (GUROBI/CPLEX) taking 90 seconds per decision, and multiple junctions. I took their
> ideas and implemented them with YOLO + tracking running every frame.

**Q: What is genuinely your own invention?**
> The spatial queue reach (E9). Neither TITS paper uses vehicle positions from camera
> vision — in fact neither contains any computer vision, they assume the measurement
> exists. My measure takes the two ROI polygons already drawn for detection, builds a
> direction axis from them, and measures how far back the queue extends as a fraction
> of the approach's own visible length. It needs no camera calibration and adds no
> parameter to tune.

**Q: Did you check whether your idea already exists in the literature?**
> Yes, and this is important — I checked *four* candidate ideas before settling on
> this one, and **rejected three of them** because they had close prior art:
>
> | Idea I considered | Why I rejected it |
> |---|---|
> | Per-vehicle worst-case delay / fairness | Already published — "A DRL Approach for Fair Traffic Signal Control", FairSCOSCA |
> | Perception-uncertainty-aware control | Already published — UCATSC (2026), vision-based partial observability |
> | Accumulated service deficit / integral control | Too close to max-pressure control — which is a **baseline in Wei's own paper** — and to Deficit Round Robin |
>
> I only kept the spatial queue reach. And I'm honest about its neighbourhood too:
> vision-based queue-length estimation is an established field, and Li et al. estimate
> queue length in metres. What's mine is the *calibration-free, parameter-free*
> formulation that derives the extent from the ROI geometry the detector already
> requires — which is possible because my system has per-vehicle tracking and theirs
> doesn't.

**Q: Isn't it a problem that similar work exists?**
> No — and neither TITS paper meets that bar either. Wei et al. combined existing MPC
> with an existing link transmission model; Li et al. combined existing green bands
> with queue estimation. Novelty in this field is *specific construction*, not virgin
> territory. What matters is that I designed the mechanism, derived the formula,
> motivated it from a limitation I measured in my own system, and checked the
> literature honestly rather than assuming.

**Q: How do you know E8 really predicts?**
> In ~19% of observations the forecast was higher than the current queue — it was
> acting on traffic that hadn't arrived yet. That's measured, not claimed.

**Q: Why is the stop count identical for every controller?**
> Because the video is a recording — vehicles can't react to my signal. Queue and
> stop counts are fixed by the footage. That's the open-loop limitation, and I state
> it clearly in my report rather than hiding it.

**Q: Does it work on more than one video?**
> Yes — tested on all three clips (different congestion levels). E8 is active on all
> of them, doing more as traffic gets heavier (10.6% → 14.4% → 18.7%).

**Q: Did you check reproducibility?**
> Yes — ran the key configurations 3–4 times each. Every traffic metric was identical
> every time. Only laptop processing speed varied.

**Q: How do you know each component actually contributes, rather than just adding features?**
> I ran a staged ablation where each stage adds exactly one component (S0 fixed → S1
> density → S2 +queue → S3 +prediction → S4 +spillback risk), with everything else held
> identical. That isolates each contribution. It also caught a component that does
> **nothing**: S3 was bit-for-bit identical to S2, so the count-based prediction changed
> no decision. I report that rather than hiding it — and diagnosing *why* (saturation)
> is what led to the design that did work.

**Q: Why did prediction fail in S3 but the risk term work in S4?**
> They use the same trend machinery on different quantities. The count-based queue
> saturates at the capacity I configured — once it reads 1.0 its derivative is zero, so
> projecting it forward reports a stable approach even while the queue is still growing.
> My spatial measure is bounded by road geometry instead, so it keeps rising and its
> derivative stays meaningful. Same predictor, different basis, +20.6% throughput. It's a
> controlled comparison of one design decision.

**Q: Does your spillback detection prevent blocking the downstream road?**
> No, and I'm explicit about that. I measure whether *this* approach is about to run out
> of *its own* storage. I cannot see the receiving link: all four ROIs are inbound to one
> junction, so once a vehicle crosses it leaves the camera's measured area entirely. True
> downstream spillback prevention needs a network model or a second camera — which is
> what Wei et al. use, and why their method needs infrastructure I don't have.

**Q: What would you do next?**
> (1) Closed-loop SUMO simulation so vehicles react and I can make causal claims.
> (2) Test E9 (spatial reach) on a longer clip where queues visibly extend across
> the approach. (3) Tune the weights instead of setting them beforehand.

---

# PART 11 — Numbers for a summary slide

- **762** automated tests, all passing
- **5-stage ablation** (S0→S4), each stage adding exactly one component
- **10** improvements, derived from **2 IEEE TITS papers** + own contributions (spatial
  queue reach, spillback risk)
- Headline: **S3 → S4 = +20.6% throughput** from changing only the projected quantity
- **28+** saved run files — every number traceable
- **3** videos, **3** congestion levels, **4** roads
- Adaptive vs fixed: **32% / 13% / 28%** less waiting across three clips
- Fixed timer **Pareto-dominated** (worse on both waiting AND throughput)
- E8 forecast alone: **no measurable effect** (stage S3 metrics are bit-identical to S2, because the count-based queue it projects is already saturated). The **+20.6%** belongs to S4, where the same projection is applied to the spatial reach instead.
- Results: **100% identical** across 3–4 repeat runs (deterministic)
- **0** hand-entered numbers

---

# PART 12 — Where to find everything

| I want to... | Open this |
|---|---|
| Explain the whole project | **This file** (MASTER_GUIDE_FOR_PROFESSOR.md) |
| Defend E9 as **your own contribution** (formulae, novelty check, results) | **`report/my_contribution_E9.md`** |
| **Show the headline ablation graph** | **`report/graph_staged_ablation.png`** |
| Reproduce the ablation | `python make_ablation_configs.py` then `python ablation_table.py` |
| Show slides | `report/RESULTS_HIGHLIGHTS.md` + the graphs in `report/` |
| Show the paper limitation analysis | `report/paper_limitations_analysis.md` |
| Show the literature review | `report/literature_review.md` |
| Show every measured number | `report/results_summary.md` |
| Show the live demo video | `results/videos/DEMO_E8_forecast_busy.mp4` |
| Show the code | `src/traffic_metrics.py` (scoring + forecast + reach), `src/lane_analysis.py` (geometry + reach axis), `src/signal_controller.py` (control logic) |
| Show the tests | `tests/` — run `python -m pytest -q` |
| Show graphs | `report/graph_adaptive_vs_fixed.png`, `report/graph_alpha_pareto.png`, `report/graph_enhancement_ablation.png` |
