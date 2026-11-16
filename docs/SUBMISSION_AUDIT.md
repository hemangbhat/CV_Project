# Submission audit

Team: Hemang Bhat (2024UCA1829), Jatin (2024UCA1865).

This review corrects the submission documents against the implementation and stored
per-seed results. It preserves experiment code, frozen protocols, raw results and
development history. It does not establish global novelty or guarantee a mark.

## Numerical verification

`python audit/verify_paper_results.py` reads raw JSONL logs rather than copying values
from generated summary tables. It verifies:

- All 176 values in Tables III-V, including the displayed paired confidence intervals.
- The 25 representative Study 1 delay means in Table II.
- All 21 stop-event and percentage values in Table I.
- 1,600 exact-sensor and 200 noise runs for Study 2, with complete unique paired seeds.
- Identical report bodies in the conference and journal sources and both roll numbers.

Two rounding corrections were necessary: ACT, uniform 3000 veh/h, Table IV is **120**
approach-seconds when rounded directly from 120.475. The earlier 121 appears consistent
with rounding twice (120.475 → 120.5 → 121). Other Study 2 table entries agree with the
raw results to their displayed precision. Table I's North nonzero share with the final geometry is 70% (70.4658%), rather than 71%.

## Corrections to the paper

| Issue | Correction |
|---|---|
| Single author and missing roll number | Added both supplied names and roll numbers to both layouts |
| Abstract said the Study 1 difference was always within ±0.5 s | Replaced with the supported conclusion: no significant delay improvement |
| Table I numbers were presented next to a YOLOv8m description | Labelled the audit as YOLOv8n; separately reported the final YOLOv8m busy value of 0.74 |
| Stop-event reduction implied labelled accuracy | Described it as a stability proxy; no queue-end ground truth exists |
| Polyline equation omitted preceding segment lengths | Added the nearest segment's starting arc length and clamped segment projection |
| Box-normalised threshold was interpreted as a calibrated road speed | Described it as an image-space heuristic |
| Final video and simulated sensors were described as identical | Disclosed fixed simulation gap 0.25 and instantaneous simulated speed, versus box-scaled video gaps and temporal tracking |
| Noise model sounded like a complete camera model | Disclosed that speeds remain exact and tracking errors are absent |
| Warm-up exclusion sounded universal | Delay filters scheduled arrivals at 300 s; blocked-entry approach-seconds include warm-up and draining |
| Entry blockage was equated with network spillback | Identified pending SUMO insertions as a local proxy, including brief insertion conflicts |
| Near-capacity delay was described as insignificant everywhere | Uniform: −1.3 ± 0.9 s; short-minor: +0.8 ± 1.6 s |
| Count variant was described as equivalent elsewhere | Disclosed its lower delay than PROP at 3600 veh/h on both geometries |
| CMP sounded like a full literature reproduction | Described the simplified capacity-normalised baseline and its timing bounds |
| Unfinished fixed-time trips were omitted | Disclosed eight 3600 veh/h runs with censored losses at the drain limit |
| References lacked some details | Added available DOI, page and author details checked against the source papers |

## What is the useful contribution?

The implementation measures a camera-visible queue tail relative to an approach's
storage, then tests where that information affects control. The first score-only design
does not improve delay. The timing-constraint design reduces local entry blockage near
capacity, including a paired advantage over its count-based counterpart on a short road.
This is a bounded extension of reference ideas, not a new detector or a reproduction of
the cited optimisation methods.

Most delay improvement against Raza-style band timing comes from actuated timing.
The spatial contribution is supported most directly by PROP versus PROP-CNT on
short-minor 3000 veh/h: −115.4 ± 81.2 blocked-entry approach-seconds. Protection fails
beyond capacity; noisy observations weaken the near-capacity benefit.

## Reproducibility and authorship

The submission workflow runs the existing test suite, the independent table checker,
four representative replay checks (ACT and PROP at short-minor 3000 and 3600 veh/h,
seed 200), and compiles both reports before updating generated files. Its run logs are
the evidence of successful execution; the workflow file alone is not a passing result.
The replay check does not rerun the complete experimental grid.

The AI-assistance acknowledgment and repository history remain intact. No contribution
split, email address, unperformed experiment or human-only authorship is invented.
Study notes remain available for learning and are not evidence that a result was
independently reproduced. The authors should read both reports and be able to explain
the implementation, ablations and limitations before submission.
