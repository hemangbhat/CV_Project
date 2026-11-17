# Vision-Measured Queue Tails for Storage-Aware Adaptive Traffic Signal Control

Computer Vision course project, Netaji Subhas University of Technology.

| Team member | Roll number |
|---|---|
| Hemang Bhat | 2024UCA1829 |
| Jatin | 2024UCA1865 |

A single traffic camera (YOLOv8 + ByteTrack) is used to
measure **where each queue ends** (queue tail X) and where it is heading (risk S). The
measurement is then used in an adaptive signal controller built on the density-based
method of **Raza et al. (2025)**, in two ways: as a term in the selection score, and,
following the spillback constraint of **Mohajerpoor et al. (IEEE T-ITS 2023)**, as a
storage limit on green time. Both are evaluated closed-loop in SUMO against fixed-time,
actuated, capacity-aware max pressure and Raza-style control.

**Reports:** [Conference PDF](paper/Vision_Measured_Queue_Tails_Paper.pdf) · [Journal PDF](paper/Vision_Measured_Queue_Tails_Paper_journal.pdf) · [Overleaf ZIP](paper/overleaf_upload.zip)

Both layouts contain the same report and both team members' roll numbers. The final
storage-aware controller is `sim/study2.py` (PROP). The video command below demonstrates
the measurement and Study 1 score; it does not run Study 2's storage-protection policy.

## Improvement over the reference methods

| Reference | What this project adds | Evidence and scope |
|---|---|---|
| Raza et al.: PCE-weighted counts and band-based green timing | A normalised queue-tail measurement, projected storage risk and actuated timing | Separates the timing benefit from the spatial measurement using RAZA-A, PROP-B and PROP-CNT ablations |
| Mohajerpoor et al.: modelled queue position and optimised signal splits | A camera-derived storage state and a simple storage-triggered timing rule | Adapts their constraint idea; does not reproduce or outperform their FASC optimiser |
| Count-based version of the proposed rule | Uses queue position relative to each approach's storage | At 3000 veh/h on the short-minor junction: 115.4 ± 81.2 fewer blocked-entry approach-seconds over 20 paired seeds |
| Actuated round-robin | Score-based selection plus storage protection | At 3000 veh/h: about 26% and 20% less entry blockage for uniform and short-minor storage; fails beyond capacity |

Control benefits are simulated. Video evaluation uses stability proxies and visual
inspection; hand-labelled queue-tail accuracy has not been measured. The virtual
sensor uses instantaneous simulated speeds and a fixed spatial gap, rather than the
video pipeline's temporal stopped test and box-scaled gap. The pressure baseline is
a simplified capacity-normalised implementation, not a reproduction.

[Submission audit](docs/SUBMISSION_AUDIT.md) explains the corrections and limits.
Run `python audit/verify_paper_results.py` to check the paper's numerical tables directly
against the raw seed logs. [Build and test workflow](https://github.com/hemangbhat/CV_Project/actions/workflows/submission-check.yml)
rebuilds both reports and checks representative simulation replays.

```
Existing paper (Raza) → limitation (count-only, storage-blind) → newer research
(Mohajerpoor; Li; Wei) → enhancement (queue tail X, risk S, storage-aware control)
→ ablation against baselines → measured result
```

## Quick start

```bash
git clone https://github.com/hemangbhat/CV_Project.git && cd CV_Project
pip install -r requirements-dev.txt          # OpenCV, Ultralytics, NumPy, Matplotlib, pytest
pip install -r requirements-sim.txt          # SUMO (only for the simulation experiments)
git lfs pull                                 # the three video clips
python -m pytest -q                          # 800+ tests, no GPU or video needed

# every measurement on one real frame, printed step by step (uses the cached tracks)
python audit/explain_frame.py --frame 2036 --approach West

# one closed-loop simulation run of the proposed controller
python -c "from sim.study2 import run, METHODS; r = run('short_minor_3000', METHODS['PROP'], 200); print(r['mean_delay'], r['blocked_seconds'])"
```

## The result in five points

1. **The measurement is more stable.** After three CV corrections (a perspective-normalised
   stopped test, a contiguous queue tail, and recalibrated geometry with drawn stop-line
   axes), visual inspection supports the measured queue tail where the detector sees the
   vehicles; no labelled queue-tail accuracy is claimed (`report/queue_tail_validation.png`).
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
   control without an observed delay penalty; on a junction with a 60 m side street the camera's spatial
   measure beats a stopped-vehicle count. Beyond capacity the protection rule fails
   (delay roughly doubles). `report/RESEARCH_PRESENTATION.md` has the full results.

## Documents

| Document | What it is |
|---|---|
| [`paper/Vision_Measured_Queue_Tails_Paper.pdf`](paper/Vision_Measured_Queue_Tails_Paper.pdf) | **The paper** (IEEE format); LaTeX source in `paper/` (conference and journal layouts) |
| [`docs/TECHNICAL_GUIDE.md`](docs/TECHNICAL_GUIDE.md) | How every stage works: formulas, code locations, design reasons, worked examples |
| [`docs/WORKED_EXAMPLE.md`](docs/WORKED_EXAMPLE.md) | One real frame followed through every formula, reproducible with `audit/explain_frame.py` |
| [`docs/architecture.md`](docs/architecture.md) | System diagram and module map |
| [`report/FINAL_REPORT.md`](report/FINAL_REPORT.md) | Long-form report including the audit of the earlier results and both studies |
| [`report/RESEARCH_PRESENTATION.md`](report/RESEARCH_PRESENTATION.md) | The project in presentation order, with all result tables |
| [`AUDIT_REPORT.md`](AUDIT_REPORT.md) | What was wrong with the earlier results, how it was found, how it was fixed |
| [`sim/PROTOCOL.md`](sim/PROTOCOL.md), [`sim/PROTOCOL_STUDY2.md`](sim/PROTOCOL_STUDY2.md) | The two experiment protocols, fixed before the test runs |
| [`results/sim/study2/tables.md`](results/sim/study2/tables.md) | Full study 2 result tables (regenerate with `python -m sim.study2_figures`) |
| [`docs/research/`](docs/research/), [`docs/papers/`](docs/papers/) | Literature review, limitation analysis, and verified facts from the papers |
| [`docs/README.md`](docs/README.md) | Index of technical documentation and experiment evidence |
| [`docs/archive/`](docs/archive/) | Earlier documents, kept for the record; their headline results are superseded |

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
tests/               800+ unit and property tests (no GPU or video needed)
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
