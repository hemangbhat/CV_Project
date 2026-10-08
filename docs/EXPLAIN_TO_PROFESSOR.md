# Explaining the Project to the Professor

This is the document to study before the presentation. It gives the story in the order
the professor expects (existing paper → limitation → newer research → enhancement →
implementation → comparison → conclusion), states exactly **what is mine and what is
borrowed**, lists the numbers to know by heart, and gives a 20-minute talk plan with the
slide for each minute. Details behind every statement are in
`report/RESEARCH_PRESENTATION.md` (the full write-up in presentation order) and
`docs/TECHNICAL_GUIDE.md` (how each part works).

---

## 1. The project in five sentences

1. **Base paper:** Raza et al. (IEEE Access 2025) control a traffic signal from a camera:
   YOLO counts vehicles, the counts are weighted by PCE into a density, and the densest
   approach gets green for a fixed band of time.
2. **Limitation (my analysis):** the state is a *count*. It does not know where the queue
   ends, or how much road each approach has, so 10 cars on a 60 m road and 10 cars on a
   150 m road look the same, though the first road is almost full.
3. **Newer research:** Mohajerpoor et al. (IEEE T-ITS 2023) show that avoiding spillback
   (a queue reaching the start of its road) needs the queue's *position*, and they use it
   as a constraint on green timing. But they cannot see the queue; they *estimate* its
   position with a traffic-flow model, predicted demand and loop detectors.
4. **My enhancement:** measure the queue position directly from video (YOLO + ByteTrack +
   my queue-tail measurement), and use it as a storage limit inside Raza's real-time
   controller.
5. **Result:** near capacity it cuts local spillback by 20–26% against standard actuated
   control at the same delay, and on a short road the camera measurement beats a simple
   count. Beyond capacity it fails. I show both.

---

## 2. What is mine, and what is borrowed

This is the question a strict examiner asks first. Answer it before it is asked.

| Part | Source | What I did |
|---|---|---|
| YOLOv8 detection, ByteTrack tracking | Existing tools (Ultralytics, Zhang et al. 2022) | Used them; added a track cache so every experiment replays identical tracks |
| PCE density score, argmax selection, starvation guard | Raza et al. 2025 | Re-implemented as the baseline ("Raza-style", not a reproduction) |
| **Queue axis** drawn along each road | **Mine** | Positions measured as a fraction of the road from the stop line, no camera calibration |
| **Perspective-normalised stopped test** | **Mine** | "Stopped" = moved less than 0.2 of its own box length in 1 s, so near and far cars are judged the same |
| **Contiguous queue tail X, risk S** | **Mine** | X = last car of the chain of stopped cars starting at the stop line; S = X projected 5 s ahead |
| Spillback constraint `x ≤ β·L` and the reciprocal penalty `1/(α·L − x)` | Mohajerpoor et al. 2023 | Borrowed the idea and the shape |
| **Online rule instead of an optimiser** | **Mine** | FASC solves a non-convex optimisation over whole cycles with predicted demand; I turned the idea into a per-step rule (no forecast, no solver) inside Raza's controller |
| **Camera measurement in place of their model** | **Mine** | The queue position they estimate, I measure |
| Actuated green with 2 s passage time; 20 s protection guard | Standard practice / **mine** | The passage-time fix and the guard came from my own experiments |
| **Experiments** | **Mine** | Audit of the earlier result; study 1 (10 controllers × 9 scenarios × 20 seeds); study 2 (8 methods × 10 conditions × 20 seeds); count-based control; camera-noise test; pre-registered protocols |

**The sentence to say:**
> "Raza controls signals from a camera but only counts vehicles. Mohajerpoor shows spillback
> avoidance needs the queue's position but has to estimate it with a model. My enhancement
> measures the queue position directly from video and uses it as a storage limit inside
> Raza's real-time controller. I test it against standard and published baselines, show
> that the camera measurement beats counting on short roads, and find where it stops
> working."

**Why this is a legitimate enhancement:** it is built the same way as the department's own
work. T-DLcR is LcR with a borrowed noise filter (AMF) built into a new dictionary step.
The contribution is the combination, the adaptation, and the evidence that it helps.

---

## 3. The story, step by step

### 3.1 The problem
A junction decides, again and again, which approach gets green and for how long. Fixed-time
plans ignore traffic. Adaptive control measures traffic. A camera can measure more than a
loop detector: a loop sees one spot, a camera sees the whole road.

### 3.2 The base paper and its limitation
Raza: `D_i = Σ count × PCE`, green to `argmax D_i`, green time from a band (40/60/120 s in
the paper; 30/45/60 s here). Limitation: count-only and storage-blind; fixed bands never
end a green early.

### 3.3 First attempt (study 1), and why it failed
I measured X and S and added them to the score. Pre-registered closed-loop test in SUMO:
no effect (±0.5 s; 1 decision in 40 changed). **Reason:** when a green ends, the longest
queue is also the one with most cars, so X ranks approaches exactly like the count.
**Lesson:** spatial information can only matter in the *timing*, and relative to *each
road's own length*. This is what Mohajerpoor's formulation does, so it shaped study 2.

The same test showed what does matter: the green-time rule. Actuated timing (end the green
once the stop-line zone has been empty for 2 s) roughly halves delay against fixed-time.

### 3.4 The enhancement (study 2)
```
Raza:      score_i = D_i
Proposed:  score_i = D_i + λ (1/(α − S_i) − 1/α)          λ = 1, α = 1.1
Timing:    actuated green, 10–60 s; after 20 s, end it if a waiting road has S ≥ 0.85
           and a higher S than the road that is green
```
`D_i` is storage-blind (one normaliser for all roads, as in Raza). `S_i` is the camera's
queue tail as a share of *that road's own* length, so it is storage-aware.

### 3.5 How it was tested
* SUMO (Simulation of Urban MObility): simulated cars stop and go because of *my* signal,
  which recorded video cannot do.
* A virtual camera computes D, Q, X, S with the same definitions as the video pipeline,
  with an optional noise mode (30% of far cars missed, 2 m jitter).
* Two junctions: all roads 150 m; or the side street shortened to 60 m.
* Five demand levels: 1200, 1800, 2400, 3000, 3600 veh/h (well under to beyond capacity).
* Eight methods: fixed-time, actuated, capacity-aware max pressure, Raza-style, Raza with
  actuated timing, + barrier, **proposed**, proposed with a count instead of the camera.
* Settings chosen on validation seeds 0–9 by a rule written down beforehand; the final
  test ran once on seeds 200–219.

### 3.6 The results
| Comparison | Result |
|---|---|
| vs Raza-style | 34–87 s less delay per vehicle in 9 of 10 conditions; most of this is actuated timing |
| vs actuated, under capacity | 0.9–1.7 s less delay (small, significant) |
| vs actuated, near capacity (3000 veh/h) | **26% and 20% less spillback**, delay unchanged |
| camera vs count, short road, 3000 veh/h | camera: 115 s less spillback |
| beyond capacity (3600 veh/h) | **fails**: delay about doubles |
| capacity-aware max pressure | best at light demand (about 2 s less delay), collapses under heavy demand |
| with camera errors | holds up to 2400 veh/h; near capacity on the short road most of the benefit is lost |

### 3.7 Why it fails beyond capacity
When every road is near full, some waiting road always has S ≥ 0.85, so the rule keeps
cutting greens. Each switch costs a 3 s yellow; capacity falls; queues grow. Mohajerpoor
says the same: in the queue-formation period spillback is often unavoidable and the
constraint must be relaxed. **Next step:** detect that regime from the camera (all roads
near full at once) and switch protection off.

---

## 4. Numbers to know by heart

| Number | Meaning |
|---|---|
| 4.28 → 0.76 | false stops per vehicle, before → after the robust stopped test |
| ±0.5 s, 1 in 40 | study 1: effect of X/S in the score; decisions changed |
| 300.6 → 33.8 s | study 1: fixed-time vs actuated, unequal over-saturated demand |
| 53.6 → 19.2 s | study 2: Raza-style vs proposed, uniform 1200 veh/h |
| 121 → 89 s | study 2: blocked entry, actuated vs proposed, uniform 3000 veh/h (−26%) |
| 945 → 753 s | study 2: same, short-road junction (−20%) |
| −115 s | camera measurement vs count, short road, 3000 veh/h |
| 99.9 → 190.8 s | study 2: actuated vs proposed beyond capacity (uniform 3600): the failure |
| 20 seeds, 95% CI | every comparison is paired over 20 random demand realisations |

---

## 5. Twenty-minute talk, slide by slide

The deck follows this order (`report/slides_src/`, 28 slides).

| Minutes | Slides | What to say |
|---|---|---|
| 0–1 | Cover, contents | One-line summary of the project |
| 1–3 | Introduction, challenges, applications | What a signal decides; camera vs loop; why queues from video are hard |
| 3–4 | Problem formulation | Eq. 1 measurement, Eq. 2 control, delay objective with the storage constraint |
| 4–7 | Taxonomy, Table 1 (two slides), relevant methods | Each method's limitation; the gap between Raza (camera, counts) and Mohajerpoor (position, but modelled) |
| 7–8 | Gaps, objectives | Four objectives; objective 3 is the main enhancement |
| 8–9 | Workflow, data | Video for measurement, SUMO for control, delay and spillback as measures |
| 9–11 | Method flow, C1 | How X is measured; 4.28 → 0.76; the validation image |
| 11–12 | C2 | First attempt failed, and why: this designed C3 |
| 12–17 | C3: method, borrowed vs mine, Tables 2–3, curves, traces, paired tests | The one-term change; results by demand level; near capacity it works, beyond capacity it fails |
| 17–19 | Contributions, summary, takeaways | Mine vs borrowed; regime switch as next step |
| 19–20 | References, questions | |

Have the demo video ready (`results/videos/README.md` has the command) in case the
professor asks to see the measurement on real footage.

---

## 6. Hard questions and short answers

**"Isn't this just Mohajerpoor's method?"**
No. Mohajerpoor *models* the queue position and optimises whole cycles with predicted
demand. I *measure* it from video and use it in a per-step rule inside Raza's vision
controller. I borrowed the constraint's form; the measurement, the controller design and
the evidence are mine. The count-based control shows the measurement itself matters on
short roads.

**"Your method loses beyond capacity. Why present it?"**
Because the comparison is fair and the failure has a known cause. Near capacity it reduces
spillback by a fifth at no delay cost, which is where spillback prevention is needed
before it becomes unavoidable. The failure tells me exactly what to build next.

**"Most of the gain over Raza is actuated timing. What is left for you?"**
Against Raza, yes. Against actuated control, which is the stronger baseline, my
contribution is the 20–26% spillback reduction near capacity, and the camera-vs-count
result. I report both.

**"Why simulation and not the video?"**
Recorded cars obey the real signal, not mine, so video cannot show what my signal would
have done. Video is used to validate the measurement; SUMO to test control. The earlier
"+21% throughput" on video turned out to be an artefact for exactly this reason.

**"Did you tune on the test data?"**
No. λ, β and the guard were chosen on validation seeds 0–9 by a rule written before the
guard results were seen (`sim/PROTOCOL_STUDY2.md`). No guard met the criterion, and the
protocol says so. The test seeds ran once.

**"What is spillback here?"**
Local spillback: the queue reaches the upstream end of the visible approach, so arriving
cars cannot enter (blocked-entry seconds). I do not detect downstream spillback.

More questions with full answers: `docs/VIVA_QA.md` (sections A–E).

---

## 7. Things not to say

| Do not say | Say instead |
|---|---|
| "I invented the spillback constraint" | "I measured what Mohajerpoor's constraint needs, from video" |
| "It improves traffic by 60%" | "Against the Raza-style base, mostly through actuated timing; against actuated, 20–26% less spillback near capacity" |
| "It always helps" | "It helps near capacity and fails beyond it" |
| "We detect spillback" | "Local storage-exhaustion on the visible approach" |
| "We reproduced Raza / Mohajerpoor" | "A Raza-style baseline; a Mohajerpoor-inspired rule" |
| "The video shows less waiting" | "Recorded vehicles cannot react; control is tested in SUMO" |
