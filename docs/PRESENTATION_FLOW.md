# Presentation Flow: what to say, why, how, and where

The whole talk as one story. Each stage gives **what to say**, **what to show**, **why**
(the reasoning the professor will probe), **where** it lives in the code, and the
**bridge sentence** into the next stage. The worked example (stage 4) sits in the middle:
it connects *what the camera measures* to *what the controller does with it*.

Slide numbers refer to the 28-slide deck (`report/slides_src/`).

---

## Stage 1 — The problem (2 min) · slides 1–5

**Say:** "A traffic signal repeatedly decides which road gets green next and for how long.
Fixed-time plans ignore traffic. Adaptive control measures traffic. A loop detector sees
one spot; a camera sees the whole road, so it can measure where the queue ends."

**Show:** the demo frame (slide 3), challenges (slide 4).

**Why it matters:** queues that reach the start of their road (spillback) block upstream
junctions; measuring that needs position, not a count.

**Bridge:** "So the research question is: *what should the camera measure, and does it help
control?*"

---

## Stage 2 — Base paper and its limitation (2 min) · slides 6, 10

**Say:** "Raza et al. 2025 detect vehicles with YOLO, weight them by PCE into a density
D = Σ count × PCE, give green to the densest road, for a fixed band of time. My analysis of
their method: the state is a count. It doesn't know where the queue ends or how long each
road is: 10 cars on a 60 m road and on a 150 m road look the same."

**Show:** problem formulation (slide 6), Raza's equation (slide 10).

**Where:** Raza-style controller = `AdaptiveController` in `src/signal_controller.py`.

**Bridge:** "Newer research says this position information matters."

---

## Stage 3 — Newer research and the gap (2 min) · slides 7–11

**Say:** "Li 2025 estimates the queue *profile* in space. Wei 2025 *predicts* queues to act
before spillback. Mohajerpoor 2023 shows spillback avoidance needs the queue's position
relative to link length, as a constraint `x ≤ β·L` and a penalty `1/(αL − x)`. But all
three need data I don't have, or *estimate* the position with a model. The gap: camera
controllers only count; spillback-aware controllers can't see. My idea: measure the queue
position with the camera."

**Show:** taxonomy (slide 7), Table 1 with limitations (slides 8–9), gaps (slide 11).

**Bridge:** "First I had to measure the queue position reliably from video. Here is exactly
how, on one real frame."

---

## Stage 4 — The measurement, with the worked example (4 min) · slides 15–16

**Say, following one frame (busy clip, frame 2036, West road):**
1. *Detect and track:* "YOLOv8m gives boxes; ByteTrack gives each car an ID across
   frames. The reference point is the bottom-centre of the box, where the wheels are."
2. *Position p:* "I drew a line along each road from the stop line. A car's position is how
   far along that line it is: car #2 is at p = 0.57, 57% of the way back."
3. *Stopped:* "A car is stopped if it moved less than 0.2 of its own box height in one
   second. Dividing by its own size handles perspective; one second averages out jitter;
   tracking tells me which car it is."
4. *X:* "Walk back from the stop line through stopped cars while each gap is within two car
   lengths. The last one is the queue end: X = 0.57."
5. *S:* "Fit a line to X over 2.5 s and project 5 s ahead: S = 0.552 here. S is where the
   queue is heading."

**Show:** `report/explain_frame.png`, the C1 table (4.28 → 0.76 false stops per vehicle).

**Why each choice:** see `docs/WORKED_EXAMPLE.md`, including where each formula comes from.

**Where:** `src/traffic_metrics.py` (`_is_stopped_windowed`, `queue_tail_reach_scaled`,
`QueuePredictor`); `src/lane_analysis.py` (`_polyline_fraction`). Reproduce live:
`python audit/explain_frame.py --frame 2036 --approach West`.

**Limitation to state yourself:** X is a fraction of the *visible* road; far roads
(North, East) are small in the image and some cars are missed.

**Bridge:** "Now I have D, Q, X and S for every road, every frame. Next: how does the
controller use them?"

---

## Stage 5 — How the measurements drive the signal (2 min) · slides 6, 15

**Say:** "Two decisions. *Which road next:* each road gets a score, and the highest wins,
with a starvation guard so no road waits more than three turns. *How long:* either Raza's
bands (30/45/60 s by score) or actuated timing, which ends the green once the stop-line zone
has been empty for 2 s, between 10 and 60 s."

**Show:** method flow (slide 15); on the worked frame, West had the top score (0.488) so it
would get green next.

**Where:** `src/signal_controller.py` (`AdaptiveController.select`,
`PhaseSequencer._apply_gap_out`).

**Bridge:** "To know whether any of this helps, I need a fair test. Recorded video can't
give one."

---

## Stage 6 — Why video can't evaluate control, and the audit (1.5 min) · slide 17 notes

**Say:** "The cars in the video obey the real signal that was filmed, not mine, so my
controller can't change their delay. An earlier '+21% throughput' on video turned out to
be an artefact: a control version with the new term switched off gave exactly the same
number. So I test control in a simulator where cars react to my signal, and use the video
only to check the measurement."

**Where:** `AUDIT_REPORT.md` (W1, W2), `audit/replay_ablation.py`.

**Bridge:** "Here is the simulation setup."

---

## Stage 7 — The fair test (1.5 min) · slide 14

**Say:** "SUMO, an open-source traffic simulator. A four-arm junction; a virtual camera
computes D, Q, X, S with the same definitions as the video. Every method sees the same
random traffic (same seeds), so I compare paired differences with 95% confidence
intervals. All settings were fixed on separate validation seeds and written into a
protocol before the final test ran."

**Where:** `sim/closed_loop.py`, `sim/sensor.py`, `sim/study2.py`, `sim/PROTOCOL.md`,
`sim/PROTOCOL_STUDY2.md`.

**Bridge:** "My first attempt put X and S into the score."

---

## Stage 8 — Study 1: the first enhancement, and why it failed (1.5 min) · slide 17

**Say:** "Ablation: fixed-time, Raza-style, + queue, + count forecast, + my S, plus a null
control. Adding X or S changed delay by at most ±0.5 s and changed the chosen road in 1 run
of 40. Why: when a green ends, the road with the longest queue is also the one with most
cars, so X ranks roads exactly as the count does. What did matter was timing: actuated
timing cut delay from 300 to 34 s in the hardest scenario."

**The lesson that designs study 2:** "Spatial information can only matter in the *timing*,
and relative to *each road's own length*."

**Bridge:** "That is exactly how Mohajerpoor uses it, so that is what I built next."

---

## Stage 9 — Study 2: the final enhancement (2 min) · slides 18–19

**Say:** "One extra term in Raza's equation and one timing rule:
`score = D + λ(1/(1.1 − S) − 1/1.1)`, which is near 0 for short queues and rises steeply
near the end of the road; and after 20 s of green, end it early if a waiting road's S
reaches 0.85. Borrowed from Mohajerpoor: the shape of the constraint and penalty. Mine: the
camera measurement instead of their model, the online rule inside Raza's controller, and
the experiments."

**Show:** slide 19 (borrowed vs mine). On the worked frame: West score 1.33, South 1.92,
so the storage term would serve South, the road closer to full.

**Where:** `sim/study2.py` (`Policy.choose`, `Policy.should_end`).

**Bridge:** "I tested it against standard and published baselines over five traffic levels."

---

## Stage 10 — Results and ablation (3 min) · slides 20–25

**Say:**
* "Two junctions (all roads 150 m; side street 60 m), five demand levels, eight methods, 20
  seeds. This table is also my ablation: Raza → + actuated timing → + barrier →
  + protection → camera vs count."
* "Against Raza: 34–87 s less delay in 9 of 10 conditions, mostly from actuated timing."
* "Near capacity (3000 veh/h): 20–26% less spillback than standard actuated control, at the
  same delay. On the short road the camera's measured queue beats a count by 115 s."
* "Beyond capacity (3600 veh/h): it fails, delay about doubles, because every road is near
  full and the rule keeps cutting greens. Mohajerpoor warns about exactly this regime."
* "Capacity-aware max pressure is best at light demand; I report that too."

**Show:** Tables 2–3, curves, queue traces, paired tests.

**Where:** `results/sim/study2/tables.md`, `report/figures/study2/`.

---

## Stage 11 — Limitations, contributions, future work (1.5 min) · slides 24–27

**Say:**
* *Contributions:* the calibration-free queue measurement; the storage-aware rule; the
  evidence of where it helps and where it fails.
* *Limitations:* visible-road-only X and missed far cars; one junction; isolated junction
  under-values spillback; no hand-labelled accuracy number yet.
* *Future:* switch protection off when all roads are full; homography to metres;
  higher-resolution detection on far roads; a two-junction corridor.

**Close with:** "The same camera measurement was useless for choosing the next road and
useful as a storage limit near capacity. Where the information enters the controller
matters more than what is measured."

---

## Timing

| Stages | Minutes |
|---|---|
| 1–3 (problem, base paper, research gap) | 6 |
| 4–5 (measurement + controller) | 6 |
| 6–8 (fair test, study 1) | 4.5 |
| 9–10 (study 2, results) | 5 |
| 11 (close) | 1.5 |
| **Total** | **~23** (trim stages 6–7 to fit 20) |

## How to prepare

1. Write each stage's **bridge sentence** on a card. If you know the bridges, you never lose
   the thread.
2. Practise stage 4 with the frame open, pointing at each car while you say its numbers.
3. For each stage, have one "why" answer ready (the **Why** lines above).
4. Rehearse twice out loud with a timer, then once in front of someone who interrupts with
   questions from `docs/VIVA_QA.md`.
