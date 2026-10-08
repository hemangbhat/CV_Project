# Viva preparation — questions and exact answers

Each answer is short enough to say aloud and points to where the evidence is. Numbers
match `report/FINAL_REPORT.md`. If you are asked something not listed here, the two
documents to know cold are `docs/TECHNICAL_GUIDE.md` (how it works) and
`AUDIT_REPORT.md` (what went wrong and how it was found).

---

## A. Problem and research chain

**1. What is the problem?**
Deciding which approach of a junction gets green next and for how long, using a camera
instead of loop detectors. My specific question is *what the camera should measure*:
counting vehicles, or measuring how far the queue reaches back and where it is heading.

**2. Why is fixed-time control insufficient?**
It ignores the traffic actually present: it gives green to empty roads and too little to
busy ones. In my simulation, fixed-time 30 s greens give 43.5 s mean delay in light
traffic, against 20.3 s for an actuated controller that ends a green once its queue has
cleared. Under unequal over-saturated demand it is 300.6 s against 33.8 s.

**3. Why Raza?**
It is the closest recent paper to a camera-only course project: YOLO detection, PCE
density, a rule-based controller, real deployment. It is also simple enough to extend,
and its control rule has a clear, nameable limitation.

**4. What exactly does Raza do?**
A YOLO detector (YOLOv7-tiny / YOLOv8-nano on Jetson edge nodes) counts vehicles per
approach. Density = Σ count × PCE (Eq. 1), multiplied by a lane-priority weight (left 3,
right 2, through 1; Eq. 2). Algorithm 1:
1. If any approach's Green Denial Counter (cycles denied green) exceeds a threshold, serve
   it.
2. Otherwise serve the highest weighted density.
3. Give 120, 60 or 40 s of green for high, moderate or low density.
4. Reset the served counter and increment the others.

They evaluate in SUMO via TraCI and on real footage: up to 33% less congestion and 23% lower
waiting time than fixed-time.

**5. What is Raza's relevant limitation?**
Be precise here. The limitations Raza *state* (§VI) are hardware and detection: power-aware
edge nodes, online learning, label noise, multimodal fusion. The limitation I study is my own
analysis of their Algorithm 1: it is reactive, because both the choice and the green length
depend only on current density. It cannot tell a queue that is growing from one that is
clearing, nor how far back the queue reaches. The T-ITS papers are what make that a
recognised gap: Li models queue profiles and over-saturation, Wei predicts queue dynamics.

**5b. How did Raza evaluate their controller, and how does yours compare?**
In SUMO with TraCI on a four-way intersection, plus real footage, against fixed-time. I also
evaluate closed-loop in SUMO, but with more controls: 10 controller variants, 9 demand
scenarios, 20 paired seeds, a null control, and a pre-registered protocol. My S1 copies their
rule (denial counter, density argmax, three bands) but not their lane-priority weights or
their 40/60/120 s bands.

**6. Why did you study T-ITS, and why did you choose these two papers?**
The course method asks for newer research on the base paper's limitation, preferably IEEE
T-ITS, which is the leading journal for traffic-control research. I chose the two papers
myself, because each attacks one half of the limitation I found in Raza:
* Li et al. (2025) treat the queue as a *profile* and measure *over-saturation*. That
  addresses "density does not describe the queue".
* Wei et al. (2025) *predict* queue dynamics to prevent spillback. That addresses "reacting
  only to the present is not enough".

Both are 2025, both are in T-ITS, and both state limitations that a camera can address
(Wei assumes the queue state is measured; Li needs connected-vehicle trajectories, which a
tracker can provide). I also checked newer and nearby work (Mohajerpoor et al. 2023, Zhu et
al. 2024/25) and kept them as supporting references.

**7. What did Li contribute?**
A queue *profile* estimated from connected-vehicle trajectories, a test for
over-saturation (green shorter than the queue's discharge time), and a multi-objective
MILP for corridor coordination that minimises over-saturation and stops.

**8. What did Wei contribute?**
Hierarchical model-predictive control on a link-transmission model with explicit queue
dynamics. It predicts how queues will evolve and allocates green before a queue spills
back into the upstream link.

**9. What limitations of those papers matter to you?**
* Li: fixed-time and offline (adaptive is their future work); needs connected-vehicle
  trajectories and a commercial solver; corridor-only; single vehicle class.
* Wei: the state estimation and prediction module is *assumed*, not built; multimodal
  traffic ignored; network MPC with QP/NLP solvers.

The key one for me is Wei's: a camera is a way to *supply* the queue state they assume.

## B. The enhancement

**10. What exactly is YOUR enhancement?**
* A spatial queue measure from tracked vehicles: the **queue tail X**, i.e. how far
  along a drawn stop-line axis the contiguous chain of stopped vehicles reaches.
* Its forward projection **S = X + (dX/dt)·5 s**, a local spillback (storage-exhaustion)
  risk.
* Both added to a Raza-style score.
* The proof that a count forecast is blind at saturation while X is not.
* A controlled evaluation of whether any of it helps.

**11. Why isn't adding queue alone enough?**
A queue *count* inside a fixed stop-line strip saturates: once the strip is full,
further vehicles queue behind it and Q stays at 1.0. It cannot show the queue still
growing.

**12. Why did count-based prediction fail?**
F = Q + slope(Q)·3 s. If Q has been 1.0 for the whole 2.5 s window, the least-squares
slope is exactly 0, so F = 1: "steady", however fast the queue grows behind the strip.
F can only drop (clearing), never signal growth. On the real footage, though, I must be
honest: Q was saturated in under 1% of frames, so the earlier claim "S3 was inert
*because* Q saturated" was not what happened there. The blind spot is real in
principle and in the simulation, not on those clips.

**13. Why does spatial queue reach work?**
It measures *where* stopped vehicles are, not how many are in a box. As cars join the
back of a queue the chain gets longer and X grows, even when Q is pinned at 1. Limit:
X itself saturates at 1 when the queue reaches the edge of the camera's view.

**14. What is ApproachAxis?**
The class that turns image points into "how far upstream from the stop line, as a
fraction of the visible approach". In the final geometry it is a polyline I drew from the
stop line back along the inbound lanes. A point is projected onto its nearest segment,
and its arc length is divided by the total length. Polyline because the fisheye curves
the road; fraction because it needs no camera calibration or metres.

**15. How is queue reach calculated?**
1. Each tracked vehicle is tested for "stopped": it moved less than 0.2 of its own box
   height per second over the last second.
2. Each stopped vehicle gets its axis fraction.
3. Sort them. Start at the first stopped vehicle inside the stop-line strip.
4. Walk upstream while each next vehicle is within two of its own vehicle lengths of the
   previous one (length = its box's extent along the road direction).
5. X is the position of the last vehicle in that chain.

Code: `queue_tail_reach_scaled`, `src/traffic_metrics.py`.

**16. How is risk calculated?**
S = clamp(X + slope·5 s), where slope is the least-squares slope of X over the last
2.5 s (75 frames). Growing queue: S > X. Steady: S = X. Clearing: S < X.
Worked example: X goes 0.30 → 0.40 in 2.5 s, so slope = 0.04/s and S = 0.40 + 0.20 = 0.60.

**17. Why is this Computer Vision?**
Every input to the controller is extracted from pixels:
* detection (YOLOv8);
* multi-object tracking (ByteTrack) to keep identities across frames;
* geometric reasoning in image space (point-in-polygon, projection onto a drawn road
  axis);
* a perspective-normalised motion test (speed in box heights per second).

The main technical problems I solved are CV problems: box jitter, perspective, fisheye
geometry, and detection recall at distance.

**18. Why ByteTrack?**
"Stopped", waiting time and "served" all require knowing the *same* vehicle across
frames. ByteTrack also uses low-confidence detections to continue existing tracks, which
keeps identities through brief occlusion in traffic. It is built into Ultralytics, so
there is no extra dependency.

**19. Why PCE?**
A bus or truck occupies more road and discharges more slowly than a car, so density
should weight it more (bus/truck 3, car 1, motorcycle 0.5). It is Raza's design, which I
keep. On these clips it barely matters: the traffic is almost all cars (finding E1).

**20. Why didn't you implement MPC?**
Wei's MPC needs a network traffic model, calibrated fundamental diagrams, demand
prediction and a QP/NLP solver. For a single camera at one junction none of the network
quantities are observable. I transferred the *idea* (act on predicted queue state) and
not the machinery, which the assignment explicitly allows.

**21. Why didn't you use reinforcement learning?**
RL needs a training environment, many episodes and careful reward design, and it gives
an opaque policy. My question is whether a specific *measurement* adds information,
which needs a controller where only that measurement changes. A rule-based score does
that cleanly, and RL would confound it. RL is also a rejected direction in the
literature review.

## C. Spillback and the open-loop limitation

**22. What is actual spillback in your project?**
In the simulation: a queue filling the 150 m approach so that arriving vehicles cannot
enter. I measure it as blocked-entry seconds, and it is included in delay. On video:
nothing is measured as actual spillback. The camera does not see the roads vehicles
drive into.

**23. What is only spillback risk?**
S: a *projection* that this approach's queue will fill its *visible* storage within 5 s.
It is local storage-exhaustion risk, not detected downstream spillback.

**24. Why are queue lengths identical across controllers?**
On recorded video the cars cannot see my simulated signal. They queue and move according
to the real light that was filmed, so the queue is a property of the footage, not of the
controller.

**25. Why are stop counts identical?**
Same reason: stops are moving-to-stopped transitions of the recorded vehicles, which no
simulated controller can change.

**26. What improved?**
* **The measurement:**
  * stops per vehicle 4.28 → 0.74 on the busy clip;
  * frames where X jumps by more than 0.3 fell from 7.3% to ≤ 1.1%;
  * the measured tail matches the visible queue where vehicles are detected
    (`report/queue_tail_validation.png`).
* **The evaluation:** a closed-loop test with 20 paired seeds per cell, replacing an
  invalid open-loop metric.
* **The controller:** actuated timing with a 2 s passage time is the largest improvement
  in the study. It roughly halves delay against fixed-time, which my score enhancement
  does not.

**27. What did not improve?**
Adding X or S to the score: no reduction in delay, spillback or throughput in any of 9
scenarios, under either timing rule, with either density normaliser, and with exact or
noisy sensing.

**28. Is your enhancement better in every metric?**
No, and it is not better in *any* closed-loop metric. Its contribution is the
measurement and the explanation of why it does not change decisions.

**29. What is your strongest experimental result?**
The decision analysis: on identical random traffic, adding S changed which approach got
the green in 1 of 40 runs. Together with the null control, that turns "no difference" into
an explained result rather than a missing one. The second strongest: the null control
that reproduced the old +21% exactly.

**30. What is your biggest limitation?**
The closed loop is a simulated junction with straight-through traffic. The real footage
can only be evaluated open-loop. The camera cannot see downstream roads, and the detector
misses about half the most distant cars.

**31. What is genuinely your contribution?**
* The image-space queue axis, the perspective-scaled contiguous queue tail, and S.
* The three measurement corrections (windowed stopped test, contiguous tail, v2
  geometry).
* The demonstration that open-loop throughput on recorded video measures agreement with
  the real signal.
* The pre-registered closed-loop ablation with null controls, and the explanation of the
  null result.

**32. What would you do next?**
Make S see downstream (a second camera or the receiving link). Use X for *timing*
(end a green when the queue has discharged) rather than ranking. Improve far-field
detection and validate X against hand-labelled frames. Calibrate demand and turning
movements from longer footage.

---

## D. Harder follow-ups a strict examiner may ask

**"Your result is negative. Why should this get marks?"**
The assignment asks me to *experimentally prove whether* the enhancement helps. I built
the enhancement, found and fixed three measurement problems, discovered that the original
headline was an artefact, designed a fair test (null control, common timing,
pre-registered protocol, paired statistics), and explained *why* it does not help. A
claimed win built on the invalid metric would have been the weaker project.

**"How do I know the simulation tests *your* controller and not a re-implementation?"**
`sim/closed_loop.py` imports `AdaptiveController`, `PhaseSequencer`, `QueuePredictor`
and `compute_config_scores` directly from `src/`. Only the sensor differs, and it uses
the same definitions: the same `queue_tail_reach` function, the same 0.2
size-per-second stopped rule.

**"Didn't you just tune the simulation until your method lost/won?"**
Design choices were made on validation seeds 0–3, and each one is logged with its reason
in `results/sim/validation/README.md`. The protocol (`sim/PROTOCOL.md`) was committed
before test seeds 100–119 ran. Git history shows the order.

**"Why two density normalisers?"**
To test the hypothesis fairly. With the *physical* normaliser D never saturates, so
it already carries spatial information. With the *saturating* one (like the video
configs) D goes blind, which is the case where X should help most. It does not help in
either.

**"Why is Raza-style band timing worse than fixed-time? Doesn't that contradict Raza?"**
In my junction every band green is at least 30 s and never ends early. Higher scores mean
even longer greens and longer cycles, which raises delay for everyone else. Raza reports
gains against their fixed-time plan, on their junction and hardware. My S1 is
"Raza-style" with scaled bands, so I state it as a result for this rule on this junction,
not a refutation of the paper.

**"What was the +21% then?"**
On the 107 s clip the proposed score dropped the D+Q weight from 0.7 to 0.4. That changed
one late decision: North got a third green instead of West. North happened to be where the
*real* light was discharging cars, so more cars "left on our green". With S forced to 0 the
same thing happens (158 served). It was neither caused by S nor a valid performance measure.

**"How did you find the geometry was wrong?"**
After making the stopped test robust, X on the old geometry dropped to about 0 on every
approach. I then plotted where queued vehicles sat along each axis: 0.2–0.9 instead of
near 0. Overlaying the ROIs showed one polygon spanning two legs, and the calibrated axes
for North and West reversed (outbound traffic dominated the motion votes).

**"Why is the queue gap measured along the road and not in box heights?"**
Cars on the West leg are seen side-on, so their box is twice as wide as it is tall. Using
box height cut real queues after the first row (X = 0.25 where the visible queue reached
0.95). The vehicle's length on the road is its box projected onto the road direction:
|w·ux| + |h·uy|. That is the same "two vehicle lengths" rule the simulation uses in metres.

**"Why a stopped threshold of 0.2 box heights per second?"**
About 1 m/s for a 5 m car, a usual meaning of "stopped" in traffic engineering. Dividing
by box height makes it the same physical speed for near and far vehicles. The 1 s window
averages out box jitter.

**"Why least squares and not a difference of two samples for the slope?"**
A two-point difference amplifies jitter. Least squares over 75 frames averages it. The
original 0.5 s window with a 5 s horizon multiplied noise by 10.

**"Are there newer T-ITS papers that do what you tried?"**
Mohajerpoor, Cai & Ramezani (T-ITS 24(1), 2023) control an isolated over-saturated junction
using *predicted* demand and spillback probability, and they use the prediction to set the
**cycle and splits**, i.e. timing. That matches my finding: in my test the green-time rule
dominated, while adding prediction to the *selection score* changed almost nothing. For the
measurement side, Zhu et al. (T-ITS, doi:10.1109/TITS.2024.3498012) estimate queue length from
spatially sparse trajectories, which is exactly my far-field detection problem.

**"Is S just X with extra noise?"**
At decision time in the simulation, effectively yes: S4X (unprojected X) and S4 (projected
S) are indistinguishable. Projection adds nothing because decisions happen when queues on
red approaches are all growing at similar rates.

**"What if you tuned the weights?"**
S changed 1 decision in 40 runs at weight 0.3. A weight large enough to flip decisions
would make the score mostly S, but S saturates at 1 together for all queued approaches
in over-saturation, so it cannot separate them. I did not tune, to avoid fitting the
test, and I say so.

**"Show me it works on the real video."**
`python -m src.main control --video videos/bellevue_116th_busy.mp4 --config config/final/S4.json --track-cache results/track_cache/bellevue_116th_busy__yolov8m__c0p30.json.gz --overlay demo`
shows boxes, IDs, the four approaches, each queue axis with a red bar at the measured
queue tail, and a panel with D, Q, X, S, F, score, signal and remaining green.

## E. Study 2: storage-aware control (Mohajerpoor-style)

**"So does your enhancement improve anything?"**
Near capacity, yes. At 3000 veh/h, storage protection reduces local spillback (blocked-entry
seconds) by 26% on the uniform junction and 20% on the junction with a 60 m side street,
against standard actuated control, with no significant change in delay (20 paired test
seeds, 95% CIs exclude zero for spillback). Against the Raza-style base it cuts delay by
34–87 s in 9 of 10 conditions, but the ablation shows most of that is actuated timing.
Beyond capacity (3600 veh/h) it makes delay about twice as bad, and I report that.

**"Why did X/S work in study 2 but not in study 1?"**
In study 1, X entered the *ranking*. When a green ends, the longest queue is also the one
with most vehicles, so X ranks approaches exactly as density does and the argmax never
changes. In study 2, S enters the *timing*, relative to each approach's *own* storage: a
10-vehicle queue on a 60 m road is nearly full, on a 150 m road it is not. A count with one
normaliser cannot tell those apart. That is Mohajerpoor et al.'s formulation: a
spillback constraint `x ≤ β·link length` and a penalty `1/(α·link length − queue)`.

**"What exactly is your equation?"**
`score_i = D_i + λ (1/(α − S_i) − 1/α)`, λ = 1, α = 1.1. Raza's equation is `score_i = D_i`;
mine differs by one term. Timing: actuated, 10–60 s, 2 s passage time; after 20 s of green,
end it if a waiting approach has S ≥ 0.85 and more than the active one.

**"Why does it fail beyond capacity?"**
When every approach is near full, some waiting approach is always at S ≥ 0.85, so the rule
keeps cutting greens. Each switch costs a 3 s yellow, capacity drops, and queues grow
further. Mohajerpoor et al. say exactly this: in the queue-formation period spillback is
often unavoidable and the constraint must be relaxed (they let the minor road queue to 5×
its length). My 20 s guard reduced the damage on validation but did not remove it. The fix
I would build next is a regime switch: turn protection off when all approaches are near
full at once.

**"Is the camera actually needed, or would counting do?"**
I tested that with a control (PROP_CNT) that builds S from a stopped-vehicle count instead
of the measured queue tail. In most conditions the two are the same. On the short-road
junction near capacity, the camera's measured tail gives 115 s less blocked entry
(significant). So the spatial measurement matters exactly where queues are long relative
to the road, and not elsewhere.

**"Did you tune it on the test data?"**
No. λ, β and the guard were chosen on validation seeds 0–9 by a selection rule I wrote down
before seeing the guard results (`sim/PROTOCOL_STUDY2.md`). No guard met my criterion, and
the protocol says so; the frozen setting is the least bad one. The test seeds 200–219 were
run once, after that file was committed.

**"Capacity-aware max pressure beats you at low demand. Why not use it?"**
It does, by about 2 s at ≤ 1800 veh/h. It re-decides every 5 s, which is ideal when queues
are short, but under heavy demand it switches constantly and loses capacity to yellows
(157 s delay at 3000 veh/h versus 44 s for mine). I report it as the best method at light
demand.
