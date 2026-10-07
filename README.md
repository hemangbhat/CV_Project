# Vision-Based Adaptive Traffic Signal Control Using Vehicle Detection, Tracking and Queue-Aware Traffic Estimation

A video-driven adaptive traffic signal controller. The system reads a traffic-junction
video, detects and tracks vehicles, assigns each track to one of four approaches
(North, East, South, West), estimates per-approach vehicle density and queue length,
combines them into a single queue-aware score, and allocates green time from that score.
A fixed-time controller is included as a baseline so the adaptive controller can be
measured against it on the same videos.

The contribution is the queue-aware scoring function

```
Score_i = alpha * D_i + (1 - alpha) * Q_i
```

where `D_i` is normalized density, `Q_i` is normalized queue length, and `alpha` is a
tunable experimental parameter. Signal state is drawn on screen as a simulated traffic
light; no physical traffic hardware is involved.

## Fixed stack

Python, OpenCV, Ultralytics YOLO, ByteTrack (via Ultralytics), NumPy, Matplotlib.
No other runtime libraries are used. Configuration is JSON, read with the standard
library. pytest and hypothesis are development-only.

## Pipeline

```
Traffic video
  -> OpenCV video processing
  -> Vehicle detection (YOLO)
  -> Vehicle tracking (ByteTrack)
  -> Approach assignment via per-approach ROI polygons
  -> per-approach vehicle density + queue length
  -> score / priority
  -> adaptive signal logic
  -> green time
  -> on-screen simulated traffic-light output
  -> performance comparison (adaptive vs fixed-time)
```

## Research trio and enhancements

The base system follows **Raza et al. 2025 (IEEE Access)** — YOLO + PCE density +
adaptive control. This project extends the control layer with mechanisms derived from
two **IEEE TITS 2025** papers:

| Role | Paper |
|---|---|
| Base system | Raza et al. 2025, IEEE Access (DOI 10.1109/ACCESS.2025.3602844) |
| Supporting — queue / over-saturation | Li, Lu & Wang 2025, IEEE TITS (DOI 10.1109/TITS.2025.3616119) |
| **Primary — predictive / spillback** | **Wei et al. 2025, IEEE TITS (DOI 10.1109/TITS.2025.3568869)** |

Eight enhancements, all **configuration-gated and off by default**, so the base
system and every original test are unchanged:

| # | Name | From | One line |
|---|---|---|---|
| **E8** | **Short-term queue forecast (PRIMARY)** | Wei | Forecast each approach's queue ~3 s ahead from its trend; act before it spills back |
| E1 | PCE-weighted queue | Raza + Wei | Weight the queue by vehicle type, not just density |
| E2 | Spillback pressure | Wei | Weight vehicles stopped beyond the stop-line area |
| E3 | Discharge-limited green | Wei + Li | Green = queue ÷ saturation flow + lost time |
| E4 | Control-plan stability | Wei | Resist cycle-to-cycle switching/green oscillation |
| E5 | Over-saturation measurement | Li | Record whether each green cleared its queue |
| E6 | Queue-clearance gap-out | Li | End green when the queue clears; extend while it persists |
| E7 | Stop counting | Li | Count moving→stopped transitions (Li's headline metric) |

E8 is the vision-only realisation of Wei et al.'s predictive control: a least-squares
trend of the measured queue projected forward, feeding the score and the green time —
no MPC, no solver. Full derivation, limitation mapping, and measured results are in
`report/` (see the Documentation map below).

## Documentation map (start here for the report / viva)

| File | Read it for |
|---|---|
| `report/PROJECT_EXPLAINER.md` | **Everything in one place** — papers, all enhancements, results, limitations, run commands, and a viva Q&A |
| `report/RESULTS_HIGHLIGHTS.md` | Slide-ready headlines with the three key graphs |
| `report/paper_limitations_analysis.md` | Full limitation catalogue of both TITS papers and the applied/out-of-scope decisions |
| `report/literature_review.md` | Positioning, the research trio, and per-enhancement measured results |
| `report/results_summary.md` | Every measured number, each tagged with its `run_id` |
| `report/graph_adaptive_vs_fixed.png` | Adaptive beats fixed-time on all three clips |
| `report/graph_alpha_pareto.png` | `alpha` traces a fairness-vs-throughput frontier; fixed-time is dominated |
| `report/graph_enhancement_ablation.png` | Per-enhancement waiting/throughput on the busy clip |

## Layout

| Path | Contents |
|---|---|
| `config/` | JSON configuration, including `default.json` |
| `data/raw/`, `data/processed/`, `data/annotations/` | source data, derived data, video metadata |
| `videos/` | input junction videos |
| `models/` | pretrained YOLO weights |
| `src/` | application modules |
| `tests/` | pytest unit and property-based tests |
| `notebooks/` | exploratory work |
| `report/` | report material, screenshots, diagrams |
| `results/videos/`, `results/graphs/`, `results/tables/`, `results/run_logs/` | run outputs |

## Setup

```bash
python -m pip install -r requirements.txt      # runtime
python -m pip install -r requirements-dev.txt  # runtime + test tooling
pytest                                         # run the test suite (762 tests)
```

Place pretrained YOLO weights at the path named by `model_path` in
`config/default.json` (default `models/yolov8n.pt`) and junction videos under
`videos/`.

## Entry points

Every command takes `--config PATH` (default `config/default.json`) and
`--no-display` for a headless run.

```bash
python -m src.main play    --video videos/junction_a.mp4
python -m src.main detect  --video videos/junction_a.mp4
python -m src.main track   --video videos/junction_a.mp4
python -m src.main measure --video videos/junction_a.mp4
python -m src.main control --video videos/junction_a.mp4 --controller adaptive
python -m src.main control --video videos/junction_a.mp4 --controller fixed
python -m src.main evaluate --alpha-sweep 0.0,0.25,0.5,0.75,1.0 --report
python -m src.main evaluate --graphs-only
```

To run the enhancements, point `--config` at one of the calibrated Bellevue configs:

```bash
# Calibrated reference (no extensions)
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th.json
# Primary enhancement: E8 short-term queue forecast, in the score
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json
# Full enhancement stack (E1+E3+E6+E8)
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast.json
```

Calibrated / experiment configs in `config/`: `bellevue_116th.json` (reference),
`bellevue_116th_e1/e2/e4.json` (single-mechanism ablations), `bellevue_116th_li.json`
(Li stack E1+E3+E6), `bellevue_116th_forecast_scoreonly.json` and
`bellevue_116th_forecast.json` (E8), `bellevue_116th_predictive.json` (arrival term).
`config/default.json` remains the canonical placeholder the automated tests pin.

| Command | What it produces |
|---|---|
| `play` | the video in a window, plus its frame count and frame rate |
| `detect` | annotated detection video in `results/videos/` |
| `track` | annotated video with Track_IDs and trajectories |
| `measure` | live per-approach count, queue length, density, and score |
| `control` | the simulated signal overlay, an annotated video, and a run log |
| `evaluate` | one run per video and controller, then tables and graphs |

`evaluate --graphs-only` rebuilds every table and graph from the run logs in
`results/run_logs/` without decoding any video, so results can be re-rendered long
after the footage is gone. `evaluate --report` additionally writes the report
artefacts described below.

## Calibration

1. Record 2 to 5 junction videos in `data/annotations/videos.json`; copy
   `data/annotations/videos.example.json` and edit it. Each entry carries the video
   path, its role (`development` or `final`), frame rate, resolution, duration, and the
   approaches visible in frame.
2. Draw the four ROI polygons and the four stop-line queue regions in
   `config/default.json` against a representative frame of each video. Run
   `python -m src.main measure --video V` and confirm each outlined region covers the
   intended road area and that each queue region sits at its stop line.
3. Tune `saturation_count`, `queue_capacity`, `alpha`, and `green_time_bands` from the
   counts you observe, and record the chosen values in `config/default.json`.
4. Run `python -m src.main evaluate --report`. A video whose configured ROIs see no
   traffic at all is reported and excluded from the input set; pass `--no-roi-check`
   only when an approach is legitimately empty for the whole sample.

## Results and reproducibility

Every run writes a run log to `results/run_logs/{video}__{controller}__alpha{a}__{timestamp}.json`
holding the resolved configuration, one record per frame with each approach's vehicle
count, queue length, score, and signal state, the phase history with truncation flags,
and the computed evaluation metrics. Tables, graphs, and every number quoted in
`report/` are derived from those logs and are tagged with the `run_id` they came from.
A run that ends before the video's final frame is marked incomplete and excluded from
the controller comparison rather than patched.

## Contribution

The contribution of this project is a **practical queue-aware extension** to
vision-based adaptive signal allocation, together with its **measured evaluation**
against a fixed-time baseline on the same junction videos. It is not a globally novel
algorithm, and no claim of one is made.

Setting `alpha = 1` reduces the score to density alone, which is what makes the queue
term's effect measurable rather than assumed: the same pipeline, the same videos, and
the same detector and tracker configuration are used for every run, and only the
controller and `alpha` differ. This `alpha = 1` case is a faithful proxy for the
density-only allocation of the base paper (Raza et al., 2025); the queue term is the
enhancement studied here. What this project does **not** claim over Raza: no improved
detector (stock YOLOv8n is used), no edge/IoT deployment, and no dataset contribution.

### Measured findings (see `report/literature_review.md` for full tables)

- **Adaptive vs fixed-time:** adaptive lowers aggregate waiting time on every clip
  (development −32%, final −12%), reproducing the expected result; this is not itself
  a contribution over Raza.
- **Queue term vs density-only:** on the low-density development/final clips the queue
  term is inert (density and queue rank the approaches identically), but on a
  higher-demand clip — where the densest and most-queued approaches diverge in 28% of
  frames — the queue term cuts aggregate waiting from 2.25 s to 1.53 s (−32% vs the
  density-only baseline) by serving a standing-queue approach that density-only control
  starves, at the cost of throughput. The effect is a fairness/throughput trade-off,
  reported honestly rather than as a uniform win.
- **Predictive extension (optional):** an anticipatory arrival term
  (`use_predictive`, off by default) is implemented and evaluated; on this footage it
  matches the queue-only operating point rather than beating it. See
  `config/bellevue_116th_predictive.json` and `tests/test_predictive.py`.

### Fairness bound, as implemented

The adaptive controller serves the maximal-score approach and forces the
longest-waiting approach once any approach has waited `starvation_limit` cycles. With
four approaches, greedy score selection and a hard bound of `starvation_limit` cycles
cannot both hold: if three approaches reach the limit together, only one can be served
next. The guarantee this System does hold, and property-tests, is that no approach
waits more than `starvation_limit + 2` cycles, so every approach is served within any
window of `starvation_limit + 3` consecutive cycles. See
`tests/test_signal_properties.py` for the argument and the tests.

### Measurement limitation: the comparison is open-loop

Vehicles in a recorded video cannot respond to the simulated signal. Both controllers
are replayed over identical footage, so the arrival pattern, the queue build-up, and
the moment each vehicle moves off are fixed by the recording and are the same under
fixed-time and adaptive control.

What the reported metrics therefore measure is how each controller **allocates green
time to the traffic state the video presents** — whether the adaptive controller directs
green to the approaches carrying the density and queue, and how long vehicles are held
on red as a result. They do not measure the downstream effect of that allocation on
driver behaviour.

Accordingly, this project does not claim that adaptive control reduces real waiting
time at the junction. Establishing that would require either a closed-loop microscopic
simulator or a deployed installation, both listed under Out of Scope. Waiting-time
figures in `report/` are stated as open-loop measurements over fixed footage, and the
`run_id` tag on every number identifies the run and video they came from.

## Out of Scope

The following are explicitly excluded. This project does not implement, depend on, or
claim any of them:

- Reinforcement learning of any kind, including DQN, multi-agent RL, and
  policy-gradient control
- Graph convolutional networks and other graph-neural traffic prediction
- Jetson or other edge-hardware deployment, and IoT sensor nodes
- SUMO or any microscopic traffic simulator as the primary system; the primary system
  is video-driven
- GA-SGD or other evolutionary hyperparameter optimization
- A custom YOLO-LIGHT style architecture, or any custom detector training
- Emergency-vehicle detection and priority pre-emption
- License-plate recognition
- Drone or UAV based monitoring
- Control of real traffic-signal hardware
- Large-scale dataset construction or public dataset release

## Report artefacts

`python -m src.main evaluate --report` writes into `report/`:

| File | Contents |
|---|---|
| `comparison__*.csv` | one row per video, controller, alpha, and approach |
| `avg_waiting_time.png`, `avg_queue_length.png`, `max_queue_length.png`, `throughput.png` | grouped bar charts, adaptive against fixed-time |
| `screenshots/` | demonstration frames exported from the annotated videos |
| `architecture.md` | pipeline architecture diagram (Mermaid source) |
| `literature_comparison.csv` | Raza 2025, Saraff 2025, YOLO-LIGHT 2026, Jin 2024, Lamrabet 2026, with how this system differs from each |
| `contribution.md` | the contribution framing and the Out of Scope list |
| `results_summary.md` | headline numbers, each row naming its `run_id` |
