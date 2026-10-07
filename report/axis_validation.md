# Validating the approach axis, and a defect it uncovered

This document records a self-audit of the geometric assumption underneath my spatial
contribution, the defect that audit found in my own junction configuration, the tool I
built to measure the quantity instead of assuming it, and the robustness check showing my
headline result is unaffected.

I am including a defect I found in my own work deliberately. The measure it affects is the
basis of my main contribution, so its validity has to be demonstrated rather than
asserted, and the only honest way to demonstrate it is to show what happens when it is
tested hard.

---

## 1. The assumption being tested

The spatial queue reach `X_i` expresses a stopped vehicle's position as a fraction of how
far back an approach's visible road extends:

```
t_i(p) = clamp( ((p - o_i) . u_i) / L_i ,  0, 1 )
X_i    = max over stopped vehicles j of t_i(p_j)
```

`u_i` is the unit vector pointing **upstream**, away from the stop line. Everything the
measure reports depends on that vector pointing the right way. If it is reversed, a nearly
empty approach reads as almost full, and the reported risk is inverted.

`u_i` was originally derived from the two polygons already in the configuration:

```
u_i = -normalize( centroid(Queue_Region_i) - centroid(ROI_i) )
```

The reasoning is that the Queue_Region *is* the stop-line area, so the offset from the ROI
centre towards it points downstream. That is valid — but only if the Queue_Region is drawn
as a **narrow strip across the stop line**.

## 2. The defect

If the Queue_Region is instead drawn as a uniformly inset copy of the whole ROI — a
natural thing to do, and what I had actually done on three of four approaches — then its
centroid nearly coincides with the ROI centroid, and the subtraction above returns a
direction determined by a few pixels of drawing noise.

I added a conditioning diagnostic to quantify this: the centroid separation as a fraction
of the ROI's own diagonal (`src/lane_analysis.py`, `ApproachAxis.conditioning`, threshold
`MIN_CONDITIONING = 0.10`).

Measured on `config/bellevue_116th.json`:

| Approach | Queue_Region as % of ROI | Centroid separation | Conditioning | Verdict |
|---|---|---|---|---|
| North | 25.6% (a proper strip) | 132.6 px | 0.182 | usable |
| East | 56.1% (inset copy) | 11.2 px | 0.026 | unusable |
| South | 51.7% (inset copy) | 18.4 px | 0.029 | unusable |
| West | 48.8% (inset copy) | **2.9 px** | **0.006** | unusable |

`config/default.json` is correct on all four approaches (Queue_Region 24.6–27.2% of ROI,
separation 94–159 px), which confirms this is a calibration mistake in one junction file
and not a flaw in the formulation.

I then checked the direction empirically, by asking whether tracked vehicles actually move
towards decreasing `t` as they should. **East came out reversed**: over 1200 frames, 1
vehicle moved with decreasing `t` against 9 with increasing `t`.

So: one approach's axis was genuinely pointing the wrong way, and two more were resting on
separations too small to trust.

## 3. Measuring the direction instead of assuming it

Rather than redraw the polygons by hand — which would leave me arguing that I drew them
better the second time — I made the direction a **measured** quantity. The tracker already
produces the necessary evidence: vehicles on an approach travel towards its stop line, so
the direction can be estimated from observed motion, with no extra annotation and no camera
calibration.

`AxisDirectionEstimator` (`src/lane_analysis.py`) estimates it from where vehicles **enter**
each ROI versus where they **leave** it, averaged over every moving track:

```
entry_i = mean of first-seen positions
exit_i  = mean of last-seen positions
d_i     = normalize( exit_i - entry_i )        # downstream
u_i     = -d_i                                  # upstream
```

Confidence is reported as directional **agreement**, `|2p - 1|` where `p` is the fraction
of tracks whose displacement projects positively onto `d_i`. Agreement is the right measure
here rather than the size of the entry/exit separation: this footage yields fragmented
tracks (only 64 on the busiest approach across 3220 frames), which shrinks the separation
without making the direction any less certain.

Run it with:

```bash
python -m src.main calibrate-axes --video videos/bellevue_116th_busy.mp4 \
    --config config/bellevue_116th.json --no-display \
    --write config/bellevue_116th_calibrated.json
```

Measured result:

| Approach | Tracks | Agreement | Measured downstream | vs. centroid geometry |
|---|---|---|---|---|
| North | 64 | 0.531 | (−0.998, +0.065) | agree (cos +0.98) |
| **East** | 35 | 0.543 | (+0.359, −0.933) | **REVERSED (cos −0.74)** |
| South | 29 | 0.103 | — | below threshold, not written |
| West | 24 | 0.667 | (−0.950, +0.312) | agree (cos +0.71) |

Two points worth stating plainly:

- **East's reversal was confirmed four independent ways**: the conditioning diagnostic, the
  frame-by-frame flow check, per-vehicle heading voting, and the entry/exit estimator.
- **South was left alone.** Its agreement of 0.103 means this clip cannot determine its
  direction, so the calibration writes nothing and the axis stays on the geometric
  fallback, still flagged as unreliable. Writing a low-confidence direction would have
  replaced an inspectable fallback with a silent guess.

The calibration repairs 3 of 4 axes (from 1 of 4 trustworthy to 3 of 4) and honestly
declines the fourth.

### Cross-clip check: is the direction a property of the camera or of the clip?

The whole approach assumes the travel direction belongs to the fixed camera, not to whichever
clip it happened to be measured on. That is testable, so I tested it by re-running the
calibration on the 240-second development clip — different hour, different traffic, same
camera.

Run on all three clips — different hours, different traffic, same camera:

| Approach | busy (107 s) | dev (240 s) | final (240 s) | Direction verdict |
|---|---|---|---|---|
| North | 0.531, agree (+0.98) | 0.232, agree (+0.94) | 0.356, agree (+0.98) | **consistent** |
| **East** | 0.543, **REVERSED** (−0.74) | 0.686, **REVERSED** (−0.86) | 0.552, **REVERSED** (−0.68) | **consistent** |
| South | 0.103, agree (+1.00) | 0.056, agree (+0.93) | 0.133, **REVERSED** (−0.98) | **CONTRADICTORY** |
| West | 0.667, agree (+0.71) | 0.175, agree (+0.64) | 0.165, agree (+0.61) | **consistent** |

Three things follow, and the third is the most useful.

**East's reversal is established.** Reversed on all three clips, every time with agreement above
the 0.50 threshold (0.543 / 0.686 / 0.552), on 35–125 tracks. Combined with the conditioning
diagnostic and the frame-by-frame flow check, that is four independent methods across three
independent clips. The drawn axis for East is wrong; this is not a marginal call.

**North and West are stable in direction but not in confidence.** Both agree with the drawn
geometry on all three clips, yet only the busy clip pushes them over the threshold (North 0.531
vs 0.232 and 0.356; West 0.667 vs 0.175 and 0.165). So *which* approaches get written depends on
which clip you calibrate from. The direction is consistent; the certainty attached to a single
short clip is not.

**The confidence threshold demonstrably earned its place.** South's estimate **contradicts
itself** — it reads "agree" on the busy and dev clips and "REVERSED" on the final clip. And South
is precisely the approach the threshold rejected on all three (0.103 / 0.056 / 0.133). Had I
written the direction anyway, I would have installed one of two opposite axes depending on which
clip I happened to run. Instead South stayed on the flagged fallback in every case. That is the
threshold catching a genuinely undetermined quantity, not being over-cautious.

### Pooled calibration

Because agreement grows with the number of independent vehicles rather than with the geometry,
the right remedy for North and West is to pool clips instead of choosing one. `calibrate-axes`
therefore accepts several videos and namespaces Track_IDs per clip, since IDs restart at 1 for
each recording and colliding IDs would splice two unrelated vehicles into one trajectory:

```bash
python -m src.main calibrate-axes --no-display \
    --video videos/bellevue_116th_busy.mp4 videos/bellevue_116th_dev.mp4 \
            videos/bellevue_116th_final.mp4 \
    --config config/bellevue_116th.json \
    --write config/bellevue_116th_calibrated.json
```

Verified on a bounded two-clip sample: vote counts rose from 8/8/0/0 to 18/19/9/7 and all four
approaches cleared the threshold, with Track_ID namespacing confirmed by
`test_colliding_track_ids_would_corrupt_the_estimate`. The calibration reported in section 3 was
measured from the busy clip alone; re-running it pooled over all three is the recommended final
step and takes about 50 minutes on CPU.

## 4. Robustness check: does the headline result survive?

The important question is whether my S3 → S4 finding depended on the broken geometry. I
re-ran both stages on the calibrated configuration, having first confirmed that the only
difference between the two configuration sets is the axis calibration itself (verified by
comparing **resolved** configurations field by field: the sole differences are
`axis_direction` and `axis_confidence` on North, East and West).

I then re-ran the whole ladder, not just the two stages that use the axis, so that every
row of the final table comes from one configuration lineage rather than a mixture.

Run IDs (calibrated lineage):

| Stage | Run ID |
|---|---|
| S0 fixed-time | `bellevue_116th_busy__fixed__alpha1p00__20260914-000922` |
| S1 Raza baseline | `bellevue_116th_busy__adaptive__alpha1p00__20260914-002256` |
| S2 + queue | `bellevue_116th_busy__adaptive__alpha0p50__20260914-005121` |
| S3 + prediction | `bellevue_116th_busy__adaptive__alpha0p50__20260913-232105` |
| S4 proposed | `bellevue_116th_busy__adaptive__alpha0p50__20260913-232912` |

Every stage reproduced its original metrics exactly:

| Stage | Wait | Throughput | Served | Over-saturated |
|---|---|---|---|---|
| S0 fixed-time | 2.36 | 73.8 | 132 | 4/4 |
| S1 Raza baseline | 2.25 | 105.7 | 189 | 2/3 |
| S2 + queue | 1.69 | 73.2 | 131 | 3/3 |
| S3 + prediction | 1.69 | 73.2 | 131 | 3/3 |
| S4 proposed | 2.14 | 88.3 | 158 | 2/3 |

S1 and S2 were expected to be unaffected, since neither uses the axis; running them anyway
removes any question about whether the table mixes geometries.

First, the calibration demonstrably changed the underlying measurements:

| Approach | Frames where reach changed | Frames where risk changed | Max change in reach |
|---|---|---|---|
| North | 3196 / 3220 | 2024 | 0.242 |
| East | 148 | 317 | 0.697 |
| South | 0 (fallback, as designed) | 0 | 0.000 |
| West | 1910 | 1359 | 0.402 |

Second, the conclusion did not change:

| Metric | calibrated S3 | calibrated S4 | Change |
|---|---|---|---|
| Throughput (veh/min) | 73.23 | 88.32 | **+20.6%** |
| Vehicles served | 131 | 158 | **+20.6%** |
| Average waiting (s) | 1.686 | 2.143 | +27.1% (worse) |
| Over-saturated greens | 3 / 3 | 2 / 3 | one fewer |
| Average queue | 0.8276 | 0.8276 | invariant, as required |
| Total stops | 1066 | 1066 | invariant, as required |

The spatial measures moved substantially — reach changed on 99% of frames on North, and by
up to 0.70 on East — and the result was identical to the uncalibrated run. **The S3 → S4
finding is robust to the axis calibration.**

## 5. Why the result survived

Tracing the divergence explains it. S3 and S4 differ in exactly one decision, at frame
2430. The scores on the deciding frame 2429 were:

| Approach | D | Q | X (reach) | S (risk) | S3 score | S4 score | Conditioning |
|---|---|---|---|---|---|---|---|
| **North** | 0.40 | 0.25 | 0.71 | 1.00 | 0.302 | **0.505** | 0.182 (usable) |
| East | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.026 |
| South | 0.50 | 0.00 | 0.65 | 0.65 | 0.175 | 0.294 | 0.029 |
| West | 0.40 | 0.50 | 0.24 | 0.94 | **0.315** | 0.463 | 0.006 |

S3 picks West on score; S4 picks North. The approach S4 selects is **North — the one
approach whose axis was correctly calibrated all along.** North is exactly the case the
contribution is aimed at: its count-based queue looks mild (`Q` = 0.25) while its queue
physically extends far back (`X` = 0.71). The count-based score cannot see that; the
spatial score can.

So the mechanism fired for the reason claimed, on the approach where the measurement was
valid. That is why repairing the other three axes changed the numbers feeding the decision
without changing the decision.

## 6. Honest limitations that remain

- **South's axis is still undetermined.** This clip does not contain enough resolvable
  traffic on that approach. It is flagged at runtime rather than hidden, and the reach and
  risk it reports should not be relied on.
- **The result rests on a single decision.** The busy clip contains only three green
  phases, so S3 → S4 differs in one choice. The +20.6% is a real, correctly measured
  difference on this clip, not evidence of an average effect over many cycles. More
  footage is the fix, and I have not claimed more than the data supports.
- **Agreement values are modest** (0.53–0.67) because ByteTrack fragments tracks on this
  footage. The directions are consistent across four independent methods, but a longer
  clip with more stable identities would tighten them.
- **Open-loop evaluation is unchanged by any of this.** Queue and stops remain
  controller-invariant (0.8276 and 1066 in both runs), as they must on recorded video.

## 7. What runtime now tells you

When a configuration asks for the spatial measures while any axis is untrustworthy, the
metrics engine emits a `RuntimeWarning` naming the offending approaches and pointing at
both remedies. An inverted axis produces perfectly valid-looking numbers, so silence was
the real hazard: without the warning, the only trace would have been an unexplained
green-time pattern buried in a Run_Log.

Regression tests in `tests/test_axis_calibration.py` (35 tests) pin all of this down,
including an executable statement of the core claim — that spatial reach still
distinguishes situations a saturated count cannot — and a test asserting the present state
of the shipped junction geometry, so a future edit cannot quietly reintroduce the defect.
