# Report folder — index

This folder holds everything needed to explain and defend the project. Read in this
order.

## Start here

1. **`RESULTS_HIGHLIGHTS.md`** — slide-ready. The headline results with graphs and the
   honest caveats. Use this to build the presentation.
2. **`PROJECT_EXPLAINER.md`** — the complete walkthrough: what the project is, the three
   papers, every enhancement, all measured results, honest limitations, how to run the
   demo, and a **viva Q&A**. If you read one file in this folder, read this.
3. **`../COMPLETE_TECHNICAL_README.md`** — the deep technical dive, with exact code
   locations, every formula, the dataset section, and the axis validation.

## Supporting documents

| File | Contents |
|---|---|
| `axis_validation.md` | **The defect I found in my own axis calibration**, the measured fix, the three-clip cross-check, and proof the headline result survives it. Read this before defending the spatial contribution. |
| `multiclip_reproducibility.md` | **Does the S3 → S4 result reproduce?** Honest answer: it is inert on both 240 s clips including the held-out one. The term changed **1 of 9** decisions where it could act, and the reason is diagnosed, not guessed. Read this before quoting +20.6%. |
| `my_contribution_E9.md` | Spatial queue reach and spillback risk, full derivation. |
| `paper_limitations_analysis.md` | Full limitation catalogue of both IEEE TITS papers (Li et al.; Wei et al.), with a per-limitation decision: applied / already covered / out of scope, and why. |
| `literature_review.md` | Literature positioning, the research trio (Raza base / Li supporting / Wei primary), and per-enhancement measured results with `run_id`s. |
| `results_summary.md` | Every measured number in one place, each row tagged with the `run_id` it came from. Nothing here is hand-entered. |
| `architecture.md` | Pipeline architecture diagram (Mermaid source). |
| `contribution.md` | Contribution framing and the Out-of-Scope list. |
| `literature_comparison.csv` | Comparison table across the reviewed papers. |
| `../data/annotations/videos.json` | Dataset provenance, source URL, and the frame-rate preprocessing, machine-readable. |

## Graphs (generated from run logs)

| File | Shows |
|---|---|
| `graph_staged_ablation.png` | **The headline figure.** S0 → S4, one component added per stage. |
| `figure_E9_geometry.png` | The approach axis and the `t = 0..1` scale, drawn by the real `ApproachAxis`. |
| `figure_E9_measured.png` | Spatial reach measured on real frames. |
| `figure_E9_blindspot.png` | The case a count-based queue cannot see but spatial reach can. |
| `graph_adaptive_vs_fixed.png` | Adaptive beats fixed-time on waiting across all three clips, and on throughput where capacity allows. |
| `graph_alpha_pareto.png` | The `alpha` parameter traces a fairness-vs-throughput frontier; fixed-time is Pareto-dominated by density-only adaptive control. |
| `graph_enhancement_ablation.png` | Waiting and throughput per enhancement on the busy clip. |
| `avg_waiting_time.png`, `avg_queue_length.png`, `max_queue_length.png`, `throughput.png` | Earlier auto-generated comparison charts (`evaluate --report`). |
| `screenshots/` | Demonstration frames from the annotated videos. |
| `comparison__*.csv` | Auto-generated comparison tables (one row per video, controller, alpha). |

## The four things to say in a viva

1. **Adaptive control improves on fixed-time.** Waiting drops on every clip
   (−32% / −13% / −28%), and fixed-time is Pareto-dominated by the density-only Raza-style
   baseline — higher throughput *and* lower waiting.

2. **The count-based prediction failed, and that is the finding.** Stage S3 projects the
   *normalised queue count* forward. It produced metrics **bit-identical to S2** — completely
   inert — because the count is already saturated at 1.0 when conditions are worst, so its
   derivative is zero exactly when a forecast would be useful. This negative result is the
   evidence for why a spatial representation was needed. Do not present E8 as a success.

3. **The spatial representation is what worked — on the saturated clip.** Stage S4 applies the
   *same* prediction machinery to spatial queue reach instead of the count: **+20.6% throughput,
   +20.6% vehicles served, over-saturated greens 3/3 → 2/3**. Same predictor, different state
   variable. Now the caveats, in the same breath, because they are the difference between a
   defensible claim and an overclaim: **average waiting rose 27.1%**; the two stages differ in
   **one** selection decision; and the term was **completely inert on both 240 s clips, including
   the held-out one**. Counting only greens the score actually decided, it changed **1 of 9**
   decisions. What is established is the *mechanism*, not an average effect size — see
   `multiclip_reproducibility.md`.

4. **The evaluation is honest and open-loop.** The footage is recorded, so vehicles cannot
   react to the simulated signal. Queue length and stop counts are therefore
   *controller-invariant* (verified: 0.8276 and 1066 identical across S3 and S4) and only
   signal-gated metrics move. No real-world queue reduction is claimed.

## If asked "how do you know your own measure is correct?"

Point at `axis_validation.md`. The spatial measure depends on one vector pointing the right
way; I built a conditioning diagnostic, found **three of four approaches unusable and one
genuinely reversed** in my own configuration, replaced the inferred direction with one
measured from vehicle motion, and then showed the headline result reproduces exactly on
corrected geometry. Confirmed four independent ways, and consistent across two clips.

Every number traces to a `run_id` under `../results/run_logs/`.
