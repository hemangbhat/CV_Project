# Project Explained — every concept, term and abbreviation in this project

This single file explains everything the other documents assume you already know.
Every term has its **full form**, **what it means**, and **why it appears in this
project**. Read Part 1 for the overall picture, then use Parts 2–9 as a reference you can
look things up in. Part 10 maps each document in the repository.

---

## Part 1 — The project in one page

**Goal.** Build a camera-based *adaptive traffic signal controller* and test whether a new
measurement of the queue improves it.

**Research chain** (the method the professor requires):

1. **Base paper:** Raza et al. 2025 (*IEEE Access*). A camera with YOLO counts vehicles,
   weights them by PCE into a *density*, and gives green to the densest approach.
2. **Limitation** (my own analysis of their algorithm): it is *reactive*. It uses only the
   current count, so it cannot tell a growing queue from a shrinking one, or how far back
   a queue reaches.
3. **Newer research** (chosen by me, both IEEE T-ITS 2025):
   * Li et al.: estimate the queue *profile* and measure *over-saturation*.
   * Wei et al.: *predict* queue dynamics to prevent *spillback*.
4. **My enhancement:**
   * measure **queue reach X**: how far back along the road the line of stopped vehicles
     extends, measured from tracked vehicles in the image;
   * predict it forward as **spillback risk S**;
   * add both to the controller's score.
5. **Implementation:** a Python + OpenCV + YOLOv8 + ByteTrack pipeline. Six measurement
   problems were found and fixed along the way.
6. **Evaluation:**
   * an audit showed the earlier "+21%" result was an artefact;
   * a fair closed-loop test in SUMO: 10 controllers × 9 traffic scenarios × 20 seeds =
     7,920 runs.
7. **Result of study 1:**
   * the measurement works;
   * adding X/S to the score does **not** measurably improve control (it changed the
     decision in 1 of 40 runs);
   * the reason is explained;
   * the green-time rule (actuated timing) is what reduces delay.
8. **Study 2, the final enhancement** (after Mohajerpoor et al., IEEE T-ITS 2023):
   * use the camera-measured queue as a **storage limit** in the green timing, relative to
     each road's own length: one extra term in Raza's score plus one rule that ends a green
     when a waiting road is about to fill;
   * tested against fixed-time, actuated, capacity-aware max pressure and Raza-style, on
     junctions with equal and unequal road lengths, at five demand levels, 20 seeds;
   * near capacity: **20–26% less spillback** than actuated control at the same delay; on a
     short road the camera measurement beats a simple count;
   * beyond capacity: it **fails** (delay about doubles), and the reason is known.

**What is mine and what is borrowed** is set out in `docs/EXPLAIN_TO_PROFESSOR.md` §2.

---

## Part 2 — Traffic-engineering terms

| Term | Full form / meaning | Why it matters here |
|---|---|---|
| **Signalised intersection / junction** | A crossroads controlled by traffic lights | The system being controlled |
| **Approach** | One road entering the junction (here North, East, South, West) | Everything is measured per approach |
| **Phase** | A period in which one set of movements has green | Here one approach is green per phase |
| **Cycle** | One full round of phases | The fixed-time plan serves N→E→S→W in each cycle |
| **Green time / split** | How long a phase stays green | The second decision a controller makes |
| **Yellow / intergreen** | The safety period between one green and the next (3 s here) | Lost time on every change; frequent switching costs capacity |
| **Fixed-time control** | Every phase has a preset duration, whatever the traffic | Baseline **S0** (30 s each) |
| **Adaptive control** (ATSC, *Adaptive Traffic Signal Control*; Raza calls it ATLC, *Adaptive Traffic Light Control*) | Green is decided from measured traffic | What Raza and this project build |
| **Actuated control** | Green continues while vehicles are present and ends once the queue has cleared | The "actuated" timing rule; the largest gain in the study |
| **Gap-out** | Ending a green early because no vehicle is present | Part of actuated timing |
| **Passage time** | How long the detector must stay empty before gap-out (2 s here) | Without it, gaps between moving cars ended greens too early (a bug I found) |
| **Min / max green** | Bounds on green length (10–60 s actuated; 30–60 s bands) | Safety and fairness limits |
| **Green-time bands** | Green length chosen from the score: low → short, high → long (Raza: 40/60/120 s; here 30/45/60 s) | The Raza-style timing rule |
| **Starvation** | An approach never getting green because others always score higher | Prevented by a counter |
| **Green Denial Counter (GDC)** | Raza's name for counting how many consecutive cycles an approach was denied green; over a threshold it is served next | Same mechanism as `cycles_waited` / `starvation_limit = 3` here |
| **Queue** | The line of stopped vehicles waiting at the stop line | What the enhancement measures |
| **Stop line** | The white line where the first vehicle stops | The origin (0) of the queue axis |
| **Queue length** | How many vehicles are queued, or how far the queue extends | Count version = Q, spatial version = X |
| **Queue profile** | How queue length evolves over time and space within a cycle | Li et al.'s concept; X over time is a vision version of it |
| **Saturation flow** | The maximum rate a queue discharges on green (~1,800 veh/h/lane) | Sets capacity in the simulation |
| **Capacity** | The most vehicles an approach can serve per hour | Scenarios were set relative to it |
| **Under-/Over-saturation** | Demand below/above capacity; over-saturated = the green cannot clear the queue | Li's focus; the regime where X/S should matter |
| **Spillback** | A queue growing so long it blocks the upstream road or junction | Wei's focus |
| **Local spillback risk** | *This project's* term: the queue is projected to fill the approach's **visible** storage | What S measures. It is **not** downstream spillback, which the camera cannot see |
| **Storage** | How many vehicles a road segment can hold (≈ length ÷ 7.5 m per car per lane) | X = 1 means the visible storage is full |
| **Delay** | Extra travel time caused by the junction compared with free driving | Main closed-loop metric |
| **Waiting time** | Time spent (almost) stopped | Secondary metric |
| **Throughput** | Vehicles passing per hour | Secondary metric; invalid on recorded video (see Part 7) |
| **Stops** | Number of times a vehicle comes to a halt | Li's headline metric (MOE) |
| **MOE** (*Measure of Effectiveness*) | A performance metric in traffic engineering | Delay, stops, throughput |
| **PCE** (*Passenger Car Equivalent*) | How many cars one vehicle "counts as": car 1, motorcycle 0.5, bus/truck 3 | Raza's density weighting, kept here |
| **Density (D)** | Σ (vehicles × PCE) in the approach area ÷ a normaliser | Raza's measure; one term of the score |
| **Platoon / surge** | A bunch of vehicles arriving together | The "surge" scenario |
| **Demand** | Vehicles arriving per hour (veh/h) | Set per scenario; one scenario is calibrated from the video |
| **Turning movement** | Left/through/right flows | Not modelled; all straight through (a stated limitation) |
| **Kinematic wave theory** | A model of how traffic jams form and travel back as waves | Used by Li, Wei and Mohajerpoor; not implemented here |
| **Fundamental diagram** | The relation between flow, density and speed on a road | Background of Wei's model |
| **Max-pressure control** | A controller that serves the movement with the largest (upstream − downstream) queue difference | Established alternative that handles finite storage |
| **Connected vehicles (CVs)** | Vehicles that broadcast their position/speed | Li's data source. ⚠️ **"CV" here means Connected Vehicles, not Computer Vision** |
| **Penetration rate** | The share of vehicles that are connected | A limitation of Li's approach |

---

## Part 3 — Computer-vision terms

| Term | Full form / meaning | Why it matters here |
|---|---|---|
| **CV** | *Computer Vision*: extracting information from images or video | The course subject; every controller input comes from pixels |
| **OpenCV** | *Open Source Computer Vision Library* | Video reading, polygons (`pointPolygonTest`), drawing the overlay |
| **YOLO** | *You Only Look Once*: a one-pass deep-learning object detector | Detects vehicles in each frame |
| **YOLOv8n / YOLOv8m** | Version 8 of YOLO (by Ultralytics); **n** = nano (smallest, fastest), **m** = medium (more accurate) | Final system uses **v8m**: it finds 2–3× more distant cars |
| **Ultralytics** | The company and Python library that ships YOLOv8 and ByteTrack | Main dependency |
| **COCO** | *Common Objects in Context*: a public dataset with 80 classes that YOLO is pretrained on | Classes 2 = car, 3 = motorcycle, 5 = bus, 7 = truck are kept |
| **Pretrained / fine-tuned** | Pretrained = trained by others on a generic dataset; fine-tuned = retrained on your own data | Pretrained only, no fine-tuning (a limitation) |
| **Bounding box** | The rectangle around a detected object (x1, y1, x2, y2) | Basis of all positions |
| **Confidence threshold** | Minimum detector certainty for a box to be kept (0.3 here) | Trades missed vehicles against false ones |
| **mAP** (*mean Average Precision*) | The standard detection-accuracy score | Raza reports ~90 mAP; not measured here |
| **IoU** (*Intersection over Union*) | Overlap of two boxes ÷ their union | How trackers match boxes between frames |
| **Recall / false positive** | Recall = the share of real objects found; false positive = a box on something that isn't a vehicle | Far-field recall is the main limit; false positives fall outside the ROIs and are ignored |
| **Multi-object tracking (MOT)** | Following many objects across frames with persistent IDs | Needed for any temporal measure |
| **ByteTrack** | A MOT method (Zhang et al., ECCV 2022) that also uses *low-confidence* boxes to continue existing tracks | Keeps vehicle IDs through brief occlusions |
| **Track / Track ID** | One vehicle's sequence of boxes, and its ID number | "Is it stopped?" compares the same ID across 1 s |
| **Kalman filter** | A predictor of the next position from past motion | Used inside ByteTrack to match boxes |
| **Occlusion** | One object hiding another | Causes missed detections and ID switches |
| **ID switch** | The tracker accidentally gives a vehicle a new ID | Inflates vehicle counts; why only *shares* of counts were used for demand |
| **Track buffer** | How long a lost track is kept before deletion (30 frames = 1 s) | Tracking parameter |
| **Track cache** | *This project's* tool: tracker output saved once and replayed in every experiment | Guarantees every controller sees identical vision input |
| **ROI** (*Region of Interest*) | A polygon on the image covering one approach's inbound lanes | Which approach a vehicle belongs to; D |
| **Queue region / queue strip (Θ)** | A small polygon directly behind the stop line | Q = vehicles in it ÷ its capacity |
| **Queue axis** | A drawn polyline from the stop line back along the road | X is measured along it |
| **Polyline** | A line made of connected straight segments | Follows curved roads in the fisheye image |
| **Arc length** | Distance measured along a curved line | Position along the queue axis |
| **Bottom-centre point** | The middle of the box's bottom edge, where wheels meet road | The single point used to locate each vehicle |
| **Point-in-polygon test** | Checks whether a point lies inside a polygon (`cv2.pointPolygonTest`) | Assigns vehicles to ROIs |
| **Perspective** | Far objects look smaller and move fewer pixels | Why speed is divided by vehicle size |
| **Fisheye camera** | A very wide-angle lens that bends straight lines | Why ROIs and axes had to be redrawn carefully |
| **Box jitter** | Boxes wobble a few pixels between frames even for a still car | Caused 4.3 false "stops" per vehicle in the old version |
| **Calibration (camera)** | Mapping pixels to real-world metres | Not needed: X is a fraction of the visible road |
| **FPS** (*frames per second*) | Video frame rate (30 here) or processing speed | All durations are counted in frames, so a constant 30 fps matters |
| **Overlay** | Information drawn on top of the video | The demo dashboard (D, Q, X, S, F, score, signal) |

---

## Part 4 — This project's own measures and symbols

| Symbol | Name | Definition (short) | Range |
|---|---|---|---|
| **D** | density | Σ PCE in ROI ÷ saturation_count, clamped | 0–1 |
| **Q** | normalised queue | vehicles in queue strip ÷ queue_capacity, clamped | 0–1 |
| **A** | arrival | vehicles in ROI but not in the strip, normalised | 0–1 |
| **F** | count forecast | Q + slope(Q) × 3 s | 0–1 |
| **X** | queue reach | tail position of the contiguous chain of stopped vehicles along the queue axis | 0 = stop line, 1 = far end of view |
| **S** | spillback risk | X + slope(X) × 5 s | 0–1 |
| **α** (alpha) | density/queue balance | base = α·D + (1−α)·Q | 0–1 |
| **Score** | priority of an approach | weighted mix of base, F, X, S | 0–1 |
| **Stopped** | a vehicle is stopped if it moved < 0.2 of its own length per second over the last 1 s | | |
| **Clamp** | limit a value to [0, 1] | | |
| **Saturate** | a measure hits its maximum and stops responding | why Q (and then F) goes blind | |
| **Slope** | rate of change, fitted by *least squares* over the last 2.5 s | used by F and S | |

**Least squares** is the standard method for fitting the best straight line through
noisy points. Its slope is the trend, and fitting over 2.5 s averages out jitter.

**The blind-spot argument in one line:** if the strip is full, Q = 1 every frame, the slope
is 0, so F = 1 + 0 = 1. F cannot show the queue still growing; X can.

---

## Part 5 — Controllers (the "arms" of the experiment)

**Arm** = one version of the controller in the comparison. **Ablation** = removing or adding
one component at a time to see what each contributes.

| Arm | What it is |
|---|---|
| **S0** | Fixed-time, 30 s each, round robin |
| **A0** | Actuated round-robin: fixed order, but each green ends when the queue clears (no camera score) |
| **S1** | Raza-style: score = D only |
| **S2** | + queue: 0.5·D + 0.5·Q |
| **S3** | + count forecast: 0.7·base + 0.3·F |
| **S4** | **Proposed:** 0.4·base + 0.3·F + 0.3·S |
| **NULL** | S4 with S forced to 0: tests whether S itself matters, or only the re-weighting |
| **S4X** | S4 with X instead of S: does *predicting* X matter? |
| **S3S / S3X** | S or X *instead of* F, at the same weight: is the spatial measure better than the count? |

**Argmax** means "pick the option with the highest value": the approach with the highest
score gets green.

### Study 2 methods (`sim/study2.py`)

| Method | What it is |
|---|---|
| **FT** | Fixed-time, 30 s each, round robin |
| **ACT** | Actuated: round robin, skips empty roads, green 10–60 s, ends when the stop-line zone is empty for 2 s |
| **CMP** | Capacity-aware max pressure (Gregoire et al. 2015): every 5 s, serve the road with the largest vehicles ÷ capacity |
| **RAZA** | Raza-style density + 30/45/60 s bands |
| **RAZA_A** | Raza-style density, actuated timing (ablation) |
| **PROP_B** | + storage barrier on S in the score (ablation) |
| **PROP** | **Proposed:** barrier + storage protection (after 20 s of green, end it if a waiting road has S ≥ 0.85) |
| **PROP_CNT** | Control: PROP with S built from a stopped-vehicle count instead of the camera's queue tail |

| Term | Meaning |
|---|---|
| **Storage** | The length of road an approach has for queueing (150 m or 60 m here) |
| **Storage-aware / storage-blind** | Whether a measure accounts for how long each road is. Raza's count is storage-blind; S is a share of each road's own length |
| **Storage barrier** | The term λ(1/(α − S) − 1/α): close to 0 for short queues, very large near the end of the road |
| **Storage protection** | The timing rule that ends a green early to stop a waiting road from filling up |
| **β (beta)** | The share of storage at which protection acts (0.85) |
| **Guard** | Protection may act only after 20 s of green, so greens are not cut too short |

---

## Part 6 — The research papers and publishing terms

| Term | Meaning |
|---|---|
| **IEEE** | *Institute of Electrical and Electronics Engineers*: the largest engineering publisher |
| **IEEE Access** | IEEE's broad, open-access journal (where Raza published) |
| **T-ITS** | *IEEE Transactions on Intelligent Transportation Systems*: the leading journal in this field (Li, Wei, Mohajerpoor, Zhu) |
| **ITS** | *Intelligent Transportation Systems*: using sensing, computing and communication in transport |
| **DOI** | *Digital Object Identifier*: a permanent link to a paper, e.g. 10.1109/TITS.2025.3616119 |
| **Raza et al. 2025** | Base paper: edge YOLO + PCE density + Algorithm 1 (GDC, density argmax, 120/60/40 s bands); evaluated in SUMO and on real footage |
| **Li et al. 2025 (T-ITS)** | Corridor signal coordination with queue-profile estimation; minimises over-saturation and stops |
| **Wei et al. 2025 (T-ITS)** | Hierarchical predictive control of a network with queue dynamics; prevents spillback |
| **Mohajerpoor, Cai & Ramezani 2023 (T-ITS)** | Single over-saturated junction. FASC algorithm: sets cycle lengths and splits from *predicted* demand, a shockwave queue model, a spillback constraint (queue ≤ β × link length) and a penalty that grows near the end of the link. Source of study 2's storage term and rule |
| **FASC** | Mohajerpoor's algorithm name (from the paper): optimal signal control of an isolated over-saturated junction |
| **Shockwave / kinematic-wave model** | A traffic-flow model of how the back of a queue moves; Mohajerpoor uses it to *estimate* queue position. This project *measures* it instead |
| **Queue formation (QF) / queue discharging (QD)** | Mohajerpoor's two over-saturated regimes: demand above capacity (queues grow) and afterwards (queues clear). Study 2 fails in the QF-like regime |
| **Max pressure / capacity-aware max pressure** | Varaiya 2013 / Gregoire et al. 2015: serve the road with the largest queue pressure; the capacity-aware version divides by road capacity |
| **Zhu et al. 2024/25 (T-ITS)** | Queue length from *sparse* vehicle trajectories; relevant to missed far-away detections |
| **"et al."** | "and others" (co-authors) |
| **Edge computing / edge node** | Running the AI on a small device next to the road instead of in the cloud |
| **Jetson (Nano / Xavier NX)** | NVIDIA's small AI computers; Raza's hardware |
| **Portenta H7** | A microcontroller board Raza used for signal logic |
| **RSEN** (*Road-Side Edge Node*) | Raza's term for a camera + edge computer unit per approach |

### Optimisation and control terms used in Li and Wei

| Term | Full form / meaning |
|---|---|
| **MILP** | *Mixed-Integer Linear Programming*: optimisation with some whole-number variables and linear equations (Li) |
| **MINLP** | *Mixed-Integer Non-Linear Programming*: the same with non-linear equations; Li converts MINLP into MILP |
| **GUROBI / CPLEX** | Commercial optimisation solvers (Li uses GUROBI) |
| **MPC** | *Model Predictive Control*: repeatedly predict the future with a model, choose the best plan, apply the first step, repeat (Wei) |
| **LTM** | *Link Transmission Model*: a model of how traffic flows from road link to road link (Wei) |
| **QP / NLP** | *Quadratic Programming* / *Non-Linear Programming*: the solver types inside Wei's MPC |
| **Hierarchical control** | A network layer that sets targets and a local layer per junction (Wei) |
| **NEMA ring-barrier** | A standard US signal phase structure (*National Electrical Manufacturers Association*) assumed by Li |
| **Corridor / arterial coordination** | Timing consecutive junctions so vehicles meet "green waves" (Li) |
| **RL / DRL / DQN** | *Reinforcement Learning* / *Deep RL* / *Deep Q-Network*: learning a control policy by trial and error. Not used, because it would not isolate the effect of one measurement |

Why their machinery was not used: it needs connected-vehicle data, commercial solvers
and network models. A single camera at one junction provides none of those. The
*ideas* (queue profile, prediction) were transferred instead.

---

## Part 7 — Experiment and statistics terms

| Term | Meaning | Why it matters here |
|---|---|---|
| **SUMO** | *Simulation of Urban MObility*: free, open-source traffic simulator from the German Aerospace Center (DLR). It simulates every vehicle (*microscopic*), and the vehicles obey the signals | The fair test environment; Raza also used it |
| **TraCI** | *Traffic Control Interface*: lets a Python program read and control a running SUMO simulation | How the controller sets the lights each 0.5 s |
| **libsumo** | SUMO linked directly into Python (faster than TraCI over a network socket) | Made 7,920 runs feasible |
| **netconvert** | SUMO tool that builds the road network file | Builds the 4-arm junction |
| **tripinfo** | SUMO output per vehicle: delay, waiting, stops | Source of the metrics |
| **Time loss** | SUMO's delay: travel time minus free-flow time | Main metric |
| **Depart / insertion delay** | Time a vehicle waits to *enter* because the road is full | Counts spillback inside delay |
| **Blocked-entry seconds** | Time during which arriving vehicles cannot enter an approach | Direct spillback measure |
| **Open loop** | The system's output cannot affect its input: recorded cars cannot react to my simulated light | Why recorded-video "throughput" is invalid |
| **Closed loop** | Output feeds back: simulated cars stop and go because of my light | Required for a causal comparison |
| **Causal claim** | "X *caused* the improvement" | Possible only in closed loop |
| **Scenario** | One traffic situation (light, medium, heavy, oversat, unequal, unequal_oversat, surge, growing, calibrated) | Tests many conditions |
| **Calibrated scenario** | Per-approach demand shares measured by the vision pipeline on the Bellevue clips | Links the simulation to the real camera |
| **Poisson arrivals** | Vehicles arrive at random times with a fixed average rate | Realistic random demand |
| **Seed** | The number that fixes a random sequence; the same seed gives the same traffic | Lets every arm face identical traffic |
| **Common random numbers** | Giving every arm the same seeds | Removes "lucky traffic" from comparisons |
| **Paired difference** | Arm A − arm B on the same seed, then averaged | Much more sensitive than comparing averages |
| **95% CI** (*Confidence Interval*) | A range that contains the true mean difference with 95% confidence; if it excludes 0, the effect is real | How "no effect" was established (intervals ~±0.5 s) |
| **t-interval** | A CI computed with the Student-t distribution (for small samples like 20 seeds) | Method used |
| **Statistical significance** | The CI excludes 0 | Only one marginal cell, consistent with chance |
| **Multiple comparisons** | Testing many things means some look significant by luck (~1 in 20 at 95%) | Why one marginal cell is not reported as an effect |
| **Validation vs test seeds** | Study 1: seeds 0–3 for design, 100–119 reported. Study 2: seeds 0–9 for design, 200–219 reported | Prevents tuning to the results |
| **Selection rule** | A rule, written before seeing results, for choosing among design variants | Study 2's guard was chosen this way (`sim/PROTOCOL_STUDY2.md`) |
| **Demand level** | Total vehicles per hour arriving at the junction (1200–3600 in study 2) | The graded condition, like noise density in a super-resolution table |
| **Under / near / beyond capacity** | Demand well below, close to, or above what the junction can serve | The proposed method helps near capacity and fails beyond it |
| **Pre-registration / frozen protocol** | Writing the experiment plan down and committing it *before* running the test | `sim/PROTOCOL.md`; the git history proves the order |
| **Warm-up** | The first 300 s, excluded so the junction starts filled | Standard simulation practice |
| **Confound** | A hidden second change that could explain a result | S3 → S4 also changed the base weight 0.7 → 0.4 |
| **Null control** | The same change with the tested ingredient switched off | NULL reproduced the old +21%, exposing the confound |
| **Artefact** | A result produced by the method of measurement, not by the thing studied | The +21% |
| **Decision analysis** | Comparing which approach each arm chose, green by green, on identical traffic | S changed 1 choice in 40 runs |
| **Density normaliser: physical vs saturating** | D divided by full road capacity (never saturates) vs by 10 vehicles (saturates, like the video setup) | Tests the method where count terms go blind |
| **Vision-noise sensor** | Simulated camera errors: up to 30% of far vehicles missed, 2 m position jitter | Checks robustness to real CV errors |
| **Ground truth** | The true answer, e.g. hand-labelled queue ends | Not yet done; listed as future work |

---

## Part 8 — Software and tooling terms

| Term | Meaning |
|---|---|
| **Python** | The programming language of the whole project |
| **NumPy / Matplotlib** | Numerical arrays / plotting library (figures in `report/figures/`) |
| **pytest** | Python testing framework: 793 automated tests |
| **Hypothesis** | Property-based testing: generates many random inputs to check rules always hold (e.g. only one green at a time) |
| **JSON run log** | A machine-readable record of every frame of a run (`results/run_logs/`) |
| **Config file** | A JSON file with all settings (geometry, weights, thresholds); `config/final/S4.json` etc. |
| **Git / commit / branch** | Version control: a commit is a saved snapshot, a branch is a line of work |
| **main** | The primary branch on GitHub |
| **PR** (*Pull Request*) | A request to merge one branch into another |
| **Git LFS** (*Large File Storage*) | Git extension for big files like videos |
| **CLI** (*Command-Line Interface*) | `python -m src.main control ...` etc. |
| **CPU / GPU** | Processor / graphics processor; everything here runs on CPU |

---

## Part 9 — Data

| Term | Meaning |
|---|---|
| **Bellevue Traffic Video Dataset** | Public traffic-camera recordings from the City of Bellevue, Washington, USA, released for research |
| **Clips** | busy (107 s), dev (240 s), final (240 s); same fisheye camera at 116th Ave NE / NE 12th St |
| **Re-encoding to constant 30 fps** | The originals dropped frames; a constant frame rate keeps every duration correct |
| **dev / final** | A development clip used while building, and a held-out clip for checking |

---

## Part 10 — What each document in the repository contains

| File | Contents in short |
|---|---|
| `README.md` | Entry point: one-paragraph result, reading order, how to run |
| `PROJECT_EXPLAINED.md` | **This file:** all concepts and terms |
| `report/FINAL_REPORT.md` | The research report: problem, papers, method, experiments, results, limitations, references |
| `docs/TECHNICAL_GUIDE.md` | Every pipeline stage with formulas, code locations, design reasons and worked examples |
| `docs/VIVA_QA.md` | Rehearsable answers to the expected viva questions and hard follow-ups |
| `docs/STUDY_GUIDE.md` | A 7-day plan to learn the project, exercises, a 10-minute talk outline and a demo checklist |
| `AUDIT_REPORT.md` | What was wrong in the earlier results (W1–W7), the evidence, and how each was fixed |
| `docs/architecture.md` | System diagram and module map |
| `docs/EXPLAIN_TO_PROFESSOR.md` | **Start here before the presentation:** the story, what is mine vs borrowed, numbers, 20-minute talk plan, hard questions |
| `report/RESEARCH_PRESENTATION.md` | The project in the department's presentation format, with all result tables |
| `sim/PROTOCOL.md` | The closed-loop experiment plan, frozen before the test runs |
| `sim/PROTOCOL_STUDY2.md` | Study 2's plan, validation history and selection rule |
| `docs/research/` | Literature review, limitation analysis of the papers, additional T-ITS papers |
| `docs/papers/` | The PDFs of Raza, Li and Wei, plus verified facts about Raza |
| `docs/archive/` | Old documents whose results are superseded, kept for the record |
| `results/sim/` | All 7,920 simulation results, tables, decision analysis |
| `results/final_video/` | Final real-video runs of every controller |
| `report/*.png` | Geometry, queue-tail validation, demo frame, result figures |

---

## Part 11 — Sentences you must be able to say precisely

* "S1 is a **Raza-style** baseline, not a reproduction: same algorithm structure, but scaled
  bands and no lane-priority weights."
* "The limitation is **my analysis** of Raza's algorithm; Raza's stated limitations are about
  hardware and detection."
* "I **chose** Li and Wei because one addresses the queue profile and the other prediction."
* "S is **local** spillback risk: the queue filling the camera's visible road. I do not
  detect downstream spillback."
* "Recorded video is **open loop**, so it cannot rank controllers; SUMO is **closed loop**."
* "The earlier +21% was an **artefact**; a **null control** reproduced it exactly."
* "The spatial measurement **works**; its control benefit was **tested and not found**, because
  X also saturates at the edge of the view and ranks approaches like density does; the
  **green-time rule** is what reduces delay."
* "In study 2 I **measure** the queue position that Mohajerpoor **estimates** with a model, and
  use it as a storage limit inside Raza's controller."
* "Near capacity it reduces spillback by **20–26%** against actuated control at the same delay;
  **beyond capacity it fails**, because protection keeps cutting greens when every road is full."
* "On a short road the camera measurement beats a stopped-vehicle count: that is where
  measuring **where** the queue is pays off."
