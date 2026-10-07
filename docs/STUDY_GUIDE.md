# Study Guide — how to know this project well enough to defend it

The other documents tell you *what* the project is. This one tells you *how to learn it*,
so that in front of your teacher you can explain any part from first principles, change
a number and predict what happens, and point to the line of code that does it.

Work through it in order. Each stage has: what to read, what to run, what you must be
able to say without notes, and a self-test. Allow about a week.

---

## Stage 1 — The story (day 1)

**Read:** `README.md`, then `report/FINAL_REPORT.md` §1–4.

**Say without notes (two minutes):**
> Raza controls signals from YOLO counts weighted by PCE. That is reactive: it uses only
> current density. Li models queue profiles and over-saturation; Wei predicts queue
> dynamics to prevent spillback. Their machinery (MILP, network MPC) needs connected
> vehicles and solvers I do not have, so I took the idea: measure the queue *in space*
> and project it forward. A count inside a fixed stop-line strip saturates, so forecasting
> the count is blind to growth. Measuring how far the stopped chain reaches back is not.
> I built that measurement, fixed it until it was correct, and tested whether it improves
> control in a closed-loop simulation. It does not: here is why.

**Self-test:** What exactly is Raza's limitation, in one sentence? What did you take from
Li, and what from Wei? What did you *not* take, and why?

## Stage 2 — The computer-vision pipeline (days 2–3)

**Read:** `docs/TECHNICAL_GUIDE.md` §2–9.

**Run:**
```bash
python -m pytest -q tests/test_robust_queue_measure.py -v      # read each test name aloud and explain it
python audit/render_queue_tail.py --clip busy --frames 600 2400 # open report/queue_tail_validation.png
python audit/measurement_compare.py                              # legacy vs robust vs v2
```

**Be able to draw on a whiteboard:**
1. A frame with one approach: ROI polygon, queue strip at the stop line, the queue axis
   from the stop line backwards, five cars, the bottom-centre point of each box.
2. Which cars are "stopped" and why: speed in box heights per second < 0.2 over 1 s.
3. The contiguous chain and where X ends. Include an isolated far car that is *not*
   counted.

**Explain each design choice as a problem you hit and fixed** (this is what proves you
built it):

| problem observed | how it showed up | fix |
|---|---|---|
| box jitter | 4.3 "stops" per vehicle in 107 s | stopped = displacement over 1 s, not per frame |
| perspective | far cars moving at speed read as stopped | divide speed by box height |
| isolated stopped boxes | X near 1 with no queue | contiguous chain from the stop line |
| fisheye + mixed ROIs | X ≈ 0 everywhere after the noise fix; queued cars at 0.2–0.9 along the axis | v2 geometry: inbound lanes only, drawn stop-line polyline |
| distant cars missed | tail stops short on North/East | YOLOv8m instead of YOLOv8n (2–3× more far cars) |

**Self-test:**
- Why the *bottom-centre* of the box and not its centre?
- Why does tracking matter for "stopped"? What would break with detection only?
- A car with a 50 px box moves 8 px in one second. Stopped? (8/50 = 0.16 < 0.2 → yes.)
- Why a polyline axis rather than a straight line?
- What does X = 0.5 mean physically?

## Stage 3 — Prediction and the score (day 3)

**Read:** `docs/TECHNICAL_GUIDE.md` §10–11.

**Whiteboard derivation you must be able to do (it will be asked):**
1. `Q = clamp(n_strip / capacity)`. The strip holds at most `capacity` cars, so once
   full, `Q = 1` for every larger queue.
2. Least-squares slope over the window: `slope = Σ(t−t̄)(y−ȳ) / Σ(t−t̄)²`.
   If every y = 1, then y − ȳ = 0, so slope = 0.
3. `F = clamp(Q + slope·3) = 1`: "steady", whatever happens behind the strip.
4. X grows as cars join the back of the chain, so slope(X) > 0 and `S = X + slope·5 > X`.
5. Worked numbers: X 0.30 → 0.40 over 2.5 s gives slope 0.04/s and S = 0.60.

**Compute by hand, then check with Python:**
```python
from src.traffic_metrics import ApproachMetrics, compute_score_weighted
m = ApproachMetrics("North", vehicle_count=5, vehicle_density=0.5, queue_length=4,
                    normalized_queue=1.0, normalized_forecast=1.0, spillback_risk=0.8)
compute_score_weighted(m, alpha=0.5, gamma=0, delta=0, omega=0.3, rho=0.3)   # S4: 0.84
compute_score_weighted(m, alpha=0.5, gamma=0, delta=0, omega=0.3)            # S3: 0.825
```

**Self-test:** Why must the weights sum to at most 1? What does NULL control for? Why is
S "local" spillback risk and not spillback detection?

## Stage 4 — The controller and timing (day 4)

**Read:** `docs/TECHNICAL_GUIDE.md` §12–14; `src/signal_controller.py`: `select`,
`_starved_approach`, `green_time_for`, `_apply_gap_out`, `_transition`.

**Be able to explain:**
- Starvation guard: why serving the longest-waiting approach at limit 3 guarantees service
  within 4 cycles.
- Band timing vs actuated timing, and why bands confound the ablation. A bigger score
  means a longer green, so a score term changes *timing*, not just *choice*.
- The passage-time bug: gaps between moving cars emptied the strip for one frame and ended
  the green mid-discharge. 2 s passage time fixed it. One validation scenario went from
  75 ± 43 s to ~25 s.

**Self-test:** Why can no two approaches ever be green at once? (One stored triple; all
four lights are derived from it.)

## Stage 5 — Why the open-loop result was invalid (day 5)

**Read:** `AUDIT_REPORT.md` W1–W4 and W7; `docs/TECHNICAL_GUIDE.md` §15.

**Run:**
```bash
python audit/summarise_logs.py "results/run_logs_legacy/bellevue_116th_busy*"
python audit/replay_ablation.py
```

**Be able to explain, with these numbers:**
- served = recorded departures ∩ simulated green: S0 132 = N48 + E8 + S71 + W5; S4 158 =
  North only.
- Why that measures agreement with the real filmed signal.
- NULL (S forced to 0) gives 158 = S4, so the +21% came from re-weighting.
- Why queue length and stops are identical across controllers on video.

This is the part a strict teacher respects most: you found your own result was wrong and
proved it.

## Stage 6 — The closed-loop experiment (day 6)

**Read:** `sim/PROTOCOL.md`, `docs/TECHNICAL_GUIDE.md` §16–17,
`report/FINAL_REPORT.md` §6.3.

**Run a tiny version yourself and look at it:**
```bash
python - <<'EOF'
from sim.closed_loop import run, ARMS
from sim.scenario import scenarios
sc = scenarios()["unequal_oversat"]
for arm in ["S0", "A0", "S1", "S4"]:
    r = run(sc, ARMS[arm], 100, timing="actuated", norm="saturating")
    print(arm, round(r.mean_delay, 1), "s delay,", round(r.blocked_seconds), "s blocked,", r.green_sequence[:20])
EOF
```
Optionally watch it on a machine with a display: `$(python -c "import sumo,os;print(os.path.join(sumo.SUMO_HOME,'bin','sumo-gui'))") -n results/sim/build/junction.net.xml -r results/sim/runs/unequal_oversat__s100.rou.xml` (the signal is then uncontrolled; the controlled run needs TraCI).

**Be able to explain:**
- Why closed loop is necessary, and what makes it closed (lights set in SUMO every 0.5 s,
  and cars react).
- That the controller is imported from `src/` unchanged; only the sensor differs.
- Paired seeds and the 95% CI, and why pairing makes small effects detectable.
- The four results: timing dominates; score composition does not matter; S changed 1 of
  40 decisions; band timing is confounded.
- The two reasons S does not help: X saturates at the edge of the view, and at decision
  time X ranks approaches the same way D does.

**Self-test:** What would you have to change for S to matter? (Downstream visibility, or
use X for timing rather than ranking.)

## Stage 7 — Rehearse (day 7)

**Read:** `docs/VIVA_QA.md`. Cover each answer, say it aloud, then compare.

### 10-minute presentation outline

| min | slide | content |
|---|---|---|
| 0–1 | Problem | fixed-time vs adaptive; "what should the camera measure?" |
| 1–2 | Base paper | Raza: YOLO + PCE density + bands; limitation: reactive, count-only |
| 2–3 | T-ITS | Li: queue profile, over-saturation. Wei: predictive queues, spillback. What transfers. |
| 3–4 | Enhancement | saturation proof (Q, F); queue axis, X, S; the score |
| 4–5 | CV pipeline | YOLOv8m → ByteTrack → geometry → stopped test → queue tail (`report/geometry_v2.png`, `report/queue_tail_validation.png`) |
| 5–6 | **Live demo** | demo video: boxes, IDs, queue axes, red tail bar, D Q X S F panel |
| 6–7 | Audit | open-loop throughput is invalid; NULL reproduces +21%; geometry fixed |
| 7–9 | Closed loop | protocol; `fig_timing_vs_score.png`; `fig_paired_actuated_saturating.png`; 1 of 40 decisions |
| 9–10 | Conclusion | measurement works; control benefit tested and not found, with the reason; timing matters; future work |

### Live-demo checklist

```bash
python -m src.main control --video videos/bellevue_116th_busy.mp4 --config config/final/S4.json \
  --track-cache results/track_cache/bellevue_116th_busy__yolov8m__c0p30.json.gz --overlay demo
```
Point at:
1. a tracked car's ID staying constant;
2. a queue forming on West, with its red tail bar moving back;
3. the X and S bars in the panel rising;
4. the highlighted row when a green starts;
5. the countdown.

If the laptop cannot run it live, play `results/videos/DEMO_final_S4_busy.mp4`.

### Things to *never* say

- "We detect spillback" → say "local storage-exhaustion risk on the visible approach".
- "S4 improves throughput by 21%" → say "that was an artefact; here is how I proved it".
- "We reproduce Raza" → say "a Raza-style baseline".
- "Our controller reduced waiting in the real video" → say "recorded vehicles cannot react".
- "This is a novel method" → say "my practical enhancement, tested fairly".

## A note on ownership

A teacher who cross-questions is checking whether you can *reason* about the system, not
whether you can recite it. The fastest way to get there is Stage 2's exercises: change a
threshold (`stopped_speed_ratio`, `queue_tail_gap_boxes`), re-run
`audit/render_queue_tail.py`, and predict the change before you look. If you can predict
it, you understand it.
