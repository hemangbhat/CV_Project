# Vision-Measured Spatial Queue Reach for Adaptive Traffic Signal Control

Computer Vision course project. A camera-based adaptive traffic-signal controller in the
style of **Raza et al. (2025)** (YOLO + PCE density), extended with a vision-measured
**spatial queue reach X** and a forward-looking **local spillback risk S**. The extension
is motivated by two 2025 IEEE T-ITS papers (**Li et al.** — queue profile and
over-saturation; **Wei et al.** — predictive queue dynamics and spillback) and evaluated
open-loop on real footage and closed-loop in SUMO.

```
Existing paper (Raza) → limitation (reactive, count-only) → T-ITS research (Li, Wei)
→ own enhancement (queue axis, queue tail X, spillback risk S) → ablation → measured result
```

## The result in five points

1. **The measurement works.** After three CV corrections (a perspective-normalised
   stopped test, a contiguous queue tail, and recalibrated geometry with drawn stop-line
   axes), the measured queue tail matches the visible queue wherever the detector sees the
   vehicles (`report/queue_tail_validation.png`).
2. **The earlier "+21% throughput" result was an artefact.** A weight-matched null control
   reproduces it exactly. On recorded video, "vehicles served" measures overlap with the
   *real* filmed signal, not control quality (`AUDIT_REPORT.md`).
3. **In a pre-registered closed-loop test** (10 controllers × 9 scenarios × 20 paired
   seeds), adding X or S to the score never measurably reduces delay or spillback. It
   changed which approach got green in 1 of 40 inspected runs. The reason: X also saturates
   at the edge of the view, and at decision time it ranks approaches the way density
   already does.
4. **What does matter is the green-time rule.** Actuated timing roughly halves delay
   against fixed-time, and cuts it about 9× under unequal over-saturated demand. Raza-style
   score bands are worse than fixed-time in most scenarios.
5. **Used in the green timing instead, the measurement helps near capacity.** Study 2
   (pre-registered, `sim/PROTOCOL_STUDY2.md`) adds a Mohajerpoor-style storage barrier and
   storage protection. At 3000 veh/h it cuts local spillback by 20–26% against actuated
   control at no delay cost; on a junction with a 60 m side street the camera's spatial
   measure beats a stopped-vehicle count. Beyond capacity the protection rule fails
   (delay roughly doubles). `report/RESEARCH_PRESENTATION.md` has the full results.

## Read in this order

| Document | What it is for |
|---|---|
| [`docs/EXPLAIN_TO_PROFESSOR.md`](docs/EXPLAIN_TO_PROFESSOR.md) | **Read first before presenting:** the story, what is mine vs borrowed, key numbers, 20-minute talk plan, hard questions |
| [`report/RESEARCH_PRESENTATION.md`](report/RESEARCH_PRESENTATION.md) | **The project in the department's research-presentation format:** problem formulation, literature table with limitations, gaps, objectives, workflow, contributions C1–C3 with result tables, takeaways |
| [`COMPLETE_PROJECT_DOCUMENT.md`](COMPLETE_PROJECT_DOCUMENT.md) | **Everything in one file:** report, architecture, technical guide, audit, protocol, papers, glossary, viva answers and study plan, with an "In simple terms" box after every part |
| [`PROJECT_EXPLAINED.md`](PROJECT_EXPLAINED.md) | Every concept, term and abbreviation (SUMO, YOLO, ByteTrack, PCE, MPC, CI, ...) with its full form, meaning and role in this project |
| [`report/FINAL_REPORT.md`](report/FINAL_REPORT.md) | The research report: problem → Raza → Li/Wei → enhancement → experiments → results → limitations |
| [`docs/TECHNICAL_GUIDE.md`](docs/TECHNICAL_GUIDE.md) | How every stage works: formulas, code locations, design reasons, worked examples |
| [`docs/STUDY_GUIDE.md`](docs/STUDY_GUIDE.md) | How to learn the project in depth: derivations, exercises, presentation and demo plan |
| [`docs/VIVA_QA.md`](docs/VIVA_QA.md) | Exact answers to the 32 expected questions and the hard follow-ups |
| [`AUDIT_REPORT.md`](AUDIT_REPORT.md) | What was wrong with the earlier results, how it was found, how it was fixed |
| [`sim/PROTOCOL.md`](sim/PROTOCOL.md) | The closed-loop experiment, frozen before the test runs |
| [`sim/PROTOCOL_STUDY2.md`](sim/PROTOCOL_STUDY2.md) | Study 2 (storage-aware control), its validation history and selection rule |
| [`docs/architecture.md`](docs/architecture.md) | System diagram and module map |
| [`docs/research/`](docs/research/) | Literature review and limitation analysis of the papers |
| [`docs/papers/`](docs/papers/) | The papers and their verified facts |
| [`docs/archive/`](docs/archive/) | Pre-audit documents, kept for the record; their headline results are superseded |

## Repository map

```
src/                 the system (vision pipeline + controller)
  video_io.py          constant-fps frame reader and simulated clock
  tracking.py          YOLOv8 + ByteTrack (Ultralytics)
  track_cache.py       record tracker output once, replay it for every experiment
  lane_analysis.py     ROI / queue strip / drawn queue axis; assignment of vehicles
  traffic_metrics.py   D, Q, A, X; windowed stopped test; queue tail; F and S; the score
  signal_controller.py fixed-time and adaptive controllers; signal state machine; timing
  overlay.py           classic overlay and the research demo dashboard
  main.py              command line (play, detect, track, measure, control, cache-tracks, ...)
sim/                 closed-loop SUMO evaluation using the controller code from src/
audit/               scripts that reproduce every audit finding
config/              bellevue_116th_v2.json (final geometry) and final/ (one file per ablation arm)
results/             track_cache/, sim/ (closed-loop results), run_logs_legacy/ (pre-audit)
report/              FINAL_REPORT.md, figures, validation images
tests/               790+ unit and property tests (no GPU or video needed)
```

## Setup

```bash
pip install -r requirements-dev.txt     # OpenCV, Ultralytics (YOLOv8 + ByteTrack), NumPy, Matplotlib, pytest
pip install -r requirements-sim.txt     # SUMO, only for the closed-loop evaluation
git lfs pull                            # the videos
python -m pytest -q                     # ~30 s
```

## Run it

```bash
# 1. Detect + track once per clip (YOLOv8m), then everything replays the cache
python -m src.main cache-tracks --video videos/bellevue_116th_busy.mp4 --config config/bellevue_116th_v2.json --no-display

# 2. Demo video: boxes, IDs, approaches, queue axes, measured queue tail, D Q X S F, score, signal
python -m src.main control --video videos/bellevue_116th_busy.mp4 --config config/final/S4.json \
    --track-cache results/track_cache/bellevue_116th_busy__yolov8m__c0p30.json.gz --overlay demo

# 3. Measurement checks on real footage
python audit/measurement_compare.py
python audit/render_queue_tail.py --clip busy

# 4. Closed-loop ablation (about 2 h on 4 cores), tables, figures, decision analysis
python -m sim.experiment run --seeds 100-119 --out results/sim/test_exact.jsonl
python -m sim.experiment table --results results/sim/test_exact.jsonl
python -m sim.figures
python -m sim.decision_analysis

# 5. Study 2: storage-aware control (about 1.5 h on 4 cores), tables and figures
python -m sim.study2 run --seeds 200-219 --out results/sim/study2/test_exact.jsonl
python -m sim.study2 run --seeds 200-219 --methods PROP --noise vision --out results/sim/study2/test_vision.jsonl
python -m sim.study2_figures
```

## Data

City of Bellevue Traffic Video Dataset (camera at 116th Ave NE / NE 12th St, released for
research), three clips of 107 s, 240 s and 240 s re-encoded to a constant 30 fps
(`data/annotations/videos.json`).

## What this project does not claim

* It does not claim to reproduce Raza, Li or Wei. S1 is a *Raza-style* baseline.
* It does not claim that recorded video shows a real-world reduction in delay. Recorded
  vehicles cannot react to a simulated signal.
* It does not claim to detect downstream spillback. S is the local risk of an approach
  filling its *visible* storage.
* It does not claim that the spatial score terms improve control. The closed-loop test
  shows they do not, and explains why.
* It does not claim that storage protection helps in general. It helps near capacity and
  fails beyond it; both are reported.
