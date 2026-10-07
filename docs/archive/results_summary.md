# Measured results

Every value below is derived from the Run_Log named in its row (Requirement 17.6). Aggregate figures over all four approaches.

## Base specification runs

| video | controller | alpha | avg wait (s) | avg queue | max queue | served | throughput (veh/min) | fps | complete | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bellevue_116th_busy.mp4 | adaptive | 0.00 | 1.53 | 0.83 | 4 | 128 | 71.55 | 8.7 | yes | `bellevue_116th_busy__adaptive__alpha0p00__20260819-095543` |
| bellevue_116th_busy.mp4 | adaptive | 0.50 | 1.69 | 0.83 | 4 | 131 | 73.23 | 11.7 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260819-133003` |
| bellevue_116th_busy.mp4 | adaptive | 1.00 | 2.25 | 0.83 | 4 | 189 | 105.65 | 8.8 | yes | `bellevue_116th_busy__adaptive__alpha1p00__20260819-094453` |
| bellevue_116th_busy.mp4 | fixed | 0.50 | 2.36 | 0.83 | 4 | 132 | 73.79 | 8.8 | yes | `bellevue_116th_busy__fixed__alpha0p50__20260819-101026` |
| bellevue_116th_dev.mp4 | adaptive | 0.00 | 1.25 | 0.36 | 5 | 206 | 51.51 | 11.0 | yes | `bellevue_116th_dev__adaptive__alpha0p00__20260818-224219` |
| bellevue_116th_dev.mp4 | adaptive | 0.50 | 1.02 | 0.36 | 5 | 199 | 49.76 | 0.8 | yes | `bellevue_116th_dev__adaptive__alpha0p50__20260818-202042` |
| bellevue_116th_dev.mp4 | adaptive | 1.00 | 1.02 | 0.36 | 5 | 199 | 49.76 | 6.6 | yes | `bellevue_116th_dev__adaptive__alpha1p00__20260818-200233` |
| bellevue_116th_dev.mp4 | fixed | 0.50 | 1.49 | 0.36 | 5 | 158 | 39.51 | 15.0 | yes | `bellevue_116th_dev__fixed__alpha0p50__20260818-195433` |
| bellevue_116th_final.mp4 | adaptive | 0.00 | 0.96 | 0.36 | 4 | 236 | 59.00 | 9.2 | yes | `bellevue_116th_final__adaptive__alpha0p00__20260818-233631` |
| bellevue_116th_final.mp4 | adaptive | 0.50 | 0.96 | 0.36 | 4 | 225 | 56.25 | 8.4 | yes | `bellevue_116th_final__adaptive__alpha0p50__20260818-232211` |
| bellevue_116th_final.mp4 | adaptive | 1.00 | 0.96 | 0.36 | 4 | 225 | 56.25 | 8.7 | yes | `bellevue_116th_final__adaptive__alpha1p00__20260818-230827` |
| bellevue_116th_final.mp4 | fixed | 0.50 | 1.10 | 0.36 | 4 | 137 | 34.25 | 7.9 | yes | `bellevue_116th_final__fixed__alpha0p50__20260818-225315` |
## Paper-derived control extensions (ablation on the busy clip)

All rows below use `alpha = 0.50` on `bellevue_116th_busy.mp4`, so the only thing
varying is which extension is enabled. The `a=0.50 base` row from the table above
(`...20260819-133003`) is the reference. Enhancement labels are defined in
`report/paper_limitations_analysis.md`:

- **E1** PCE-weighted Queue_Length
- **E2** spillback pressure, `spillback_weight = 0.25`
- **E3** discharge-limited Green_Time, `saturation_flow_rate = 0.5` PCE/s, `min_green_time = 10 s`
- **E4** control-plan stability, `switching_margin = 0.05`, `green_rate_limit = 10 s`

| enabled | config file | avg wait (s) | avg queue | max queue | served | throughput (veh/min) | fps | complete | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none (reference) | `bellevue_116th.json` | 1.69 | 0.83 | 4 | 131 | 73.23 | 11.7 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260819-133003` |
| E1 | `bellevue_116th_e1.json` | 1.69 | 0.83 | 4 | 131 | 73.23 | 9.5 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260822-054051` |
| E2 | `bellevue_116th_e2.json` | **1.53** | 0.83 | 4 | 128 | 71.55 | 9.3 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260822-054638` |
| E4 | `bellevue_116th_e4.json` | **1.60** | 0.83 | 4 | **135** | **75.47** | 9.3 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260822-055232` |
| E1+E2+E4 | `bellevue_116th_tits_scoring.json` | 2.13 | 0.83 | 4 | 151 | 84.41 | 9.9 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260822-052726` |
| E1+E2+E3+E4 | `bellevue_116th_tits.json` | 2.34 | 0.83 | 4 | 82 | 45.84 | 9.6 | yes | `bellevue_116th_busy__adaptive__alpha0p50__20260822-053302` |

Average and maximum queue length are identical across every row because the
footage is recorded and open-loop: the controller cannot change what the vehicles
did. Only the signal-gated metrics — waiting time, vehicles served, throughput —
respond to the controller. This is the same property documented in
`literature_review.md` §5.2.

### Per-approach waiting time and vehicles served

The aggregate hides the mechanism, so the per-approach view is recorded too. West
is the approach that carries a standing queue at low instantaneous density.

| configuration | North | East | South | West |
| --- | --- | --- | --- | --- |
| none (reference) | 0.167 s / 122 | 0.315 s / 0 | 0.399 s / 0 | 0.831 s / 9 |
| E4 only | 0.205 s / 119 | 0.315 s / 0 | 0.399 s / 0 | **0.675 s / 16** |
| E1+E2+E4 | 0.060 s / 151 | 0.315 s / 0 | 0.399 s / 0 | **1.502 s / 0** |

### Green phase sequence per run

The whole clip is 107 s, so a run holds only a few cycles; the sequence is short
enough to record in full, which is what makes the aggregate differences traceable.

| configuration | GREEN phases in order |
| --- | --- |
| none (reference) | North/30 s, North/45 s, West/45 s |
| E1 only | North/30 s, North/45 s, West/45 s |
| E2 only | North/30 s, North/30 s, West/45 s |
| E4 only | North/30 s, North/40 s, West/45 s |
| E1+E2+E4 | North/30 s, North/30 s, North/40 s |
| E1+E2+E3+E4 | North/10 s, North/10 s, North/10 s, East/10 s, South/10 s, West/10 s, North/10 s, East/10 s, South/10 s |

### Spillback measure activity

Confirming the E2 term is measuring something rather than sitting at zero
(`bellevue_116th_busy__adaptive__alpha0p50__20260822-054638`): `normalized_spillback`
is non-zero in 3851 frame-approach cells of 12 880, peaking at 0.80 on North and
0.40–0.50 on the other three approaches.
## Li et al. derived mechanisms (E5-E7), busy clip, alpha = 0.50

E5 (over-saturation measurement), E6 (queue-clearance gap-out), E7 (stop counting).
The reference here is re-run so that E5 and E7 populate its log; it reproduces the
`...20260819-133003` control result exactly (1.69 s / 73.2 veh/min / 131 served),
confirming the measurement additions do not change control.

| enabled | config file | avg wait (s) | throughput | served | stops | stops/veh | green phases | over-saturated | worst shortfall (s) | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none (reference) | `bellevue_116th.json` | 1.69 | 73.2 | 131 | 1066 | 4.28 | 3 | **3 / 3** | 6.0 | `bellevue_116th_busy__adaptive__alpha0p50__20260822-103419` |
| E6 (bands) | `bellevue_116th_gapout.json` | 1.75 | 50.9 | 91 | 1066 | 4.28 | 6 | **1 / 6** | 6.0 | `bellevue_116th_busy__adaptive__alpha0p50__20260822-104003` |
| E1+E3+E6 (Li bundle) | `bellevue_116th_li.json` | 1.75 | 50.9 | 91 | 1066 | 4.28 | 6 | 1 / 6 | 6.0 | `bellevue_116th_busy__adaptive__alpha0p50__20260822-104551` |

**E5 — the diagnostic works.** The base controller ends **all three** of its green
phases with the queue region still occupied, the worst falling 6.0 s short of
clearing. The System previously had no way to see this. E5 changes no control; it
records `η = leftover_queue / saturation_flow` per green phase (Li et al. Eqs. 7-10).

**E7 — stops are controller-invariant on recorded footage.** All three runs record
the identical 1066 stops (4.28 per vehicle). This is the open-loop property again,
now visible on Li et al.'s own headline metric: because the vehicles in the
recording cannot react to the signal, the count of moving-to-stopped transitions is
a property of the video, exactly as average and maximum queue length are. Stops are
reportable now, but on recorded footage they cannot separate controllers — a
genuinely causal stops comparison would need closed-loop simulation.

**E6 — does what Li intends, at a throughput cost on this clip.** Gap-out cut
over-saturated green phases from 3/3 to 1/6: by ending each green once its queue
clears, it starts more cycles and leaves fewer queues unserved, which is precisely
the behaviour Li et al.'s under/over-saturation test is designed to produce. But
6 green phases instead of 3 means twice the intergreen (a 3 s yellow after each), so
throughput fell 73.2 → 50.9 veh/min. Same scale mismatch as E3: the queues here
clear in a few seconds, so ending greens early trades useful green for changeover
overhead. E1+E3+E6 is identical to E6 alone — E1 is inert on the car-only fleet and
gap-out governs the timing regardless of the discharge band.

**E3 lost-time correction (from Li et al. Eq. 7).** The discharge green now charges
the 3 s changeover as well as the discharge, so a run under `bellevue_116th_li.json`
(discharge + gap-out) reaches 50.9 veh/min against the original E3's 45.8
(`...20260822-053302`) — the lost-time accounting removes some of the degenerate
minimum-green thrash, though it does not overcome the scale mismatch on this clip.
## E8 - short-term queue forecast (the primary enhancement), busy clip, alpha = 0.50

E8 forecasts each approach's queue `forecast_horizon_seconds` (3 s) ahead from the
least-squares trend of its measured `normalized_queue` over a 15-frame window, and
feeds the forecast into the score (weight `omega = 0.4`) and the discharge green.
"fc max" is the largest forecast the run produced; "fc ahead" is the share of
frame-approach cells where the forecast exceeded the current queue, i.e. where the
predictor was genuinely anticipating growth rather than echoing the present.

| configuration | config file | avg wait (s) | throughput | served | over-saturated | fc max | fc ahead | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| reference (bands) | `bellevue_116th.json` | 1.69 | 73.2 | 131 | 3 / 3 | – | – | `bellevue_116th_busy__adaptive__alpha0p50__20260822-103419` |
| E8 in score only | `bellevue_116th_forecast_scoreonly.json` | 2.14 | **88.3** | **158** | **2 / 3** | 1.00 | 18.7% | `bellevue_116th_busy__adaptive__alpha0p50__20260822-164349` |
| E1+E3+E6 (forecast off) | `bellevue_116th_li.json` | 1.75 | 50.9 | 91 | 1 / 6 | – | – | `bellevue_116th_busy__adaptive__alpha0p50__20260822-104551` |
| E1+E3+E6+E8 (full) | `bellevue_116th_forecast.json` | 2.20 | 48.6 | 87 | 1 / 9 | 1.00 | 18.3% | `bellevue_116th_busy__adaptive__alpha0p50__20260822-164949` |

**The forecast is genuinely anticipatory.** It reached the maximum forecast of 1.0
(projecting an approach to a full queue before it filled) and, in **~19% of
frame-approach cells, forecast a queue higher than the one currently measured** —
so in nearly a fifth of the run it acted on growth that had not yet arrived, not on
the present state. This is the distinguishing evidence that E8 is a forecast, not a
restatement of the current queue.

**In the score, E8 shifts the operating point toward throughput.** Against the band
reference it raised throughput 73.2 → 88.3 veh/min and vehicles served 131 → 158,
and cut over-saturated green phases 3/3 → 2/3, at the cost of higher waiting
(1.69 → 2.14 s). Reading: by giving green to approaches whose queues are about to
grow, the controller clears more vehicles overall but spends less green on the
single standing-queue approach that low waiting depends on — the same
throughput-vs-fairness trade-off seen across the α sweep, reached here by
anticipation rather than by weighting.

**On the full stack, the E3/E6 timing dominates.** Adding E8 on top of discharge +
gap-out (48.6 veh/min) does not recover the throughput the short greens and extra
intergreen cost; the forecast changes selection but not the intergreen overhead that
governs this clip. Same scale mismatch documented for E3/E6.

**Open-loop bound (unchanged).** Stops stay 1066 and queue length is fixed across
every configuration, because the footage is recorded (Section 5.2). E8 changes which
approach is served and when, so it moves the signal-gated metrics (waiting,
throughput, served, over-saturation), but it cannot change the underlying queue or
stop counts. A forecast that opens a *distinct* operating point — anticipating a
build-up on one approach while another is momentarily denser — needs footage longer
and more variable than this 107 s clip, where the forecast and the current queue
mostly point to the same approach.
## E8 generalization across clips (same code, same config, three congestion levels)

Answering "does it only work on one video": the E8 forecast score-only config
(`bellevue_116th_forecast_scoreonly.json`) run on all three clips, against the
plain-reference config (`bellevue_116th.json`) on the same clip. "fc active" is the
share of frame-approach cells where the forecast exceeded the current queue.

| Clip | Controller | Avg wait (s) | Throughput | Served | Stops | over-sat greens | fc active | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| development (240 s) | reference | 1.02 | 49.8 | 199 | 943 | 4 / 6 | – | `bellevue_116th_dev__adaptive__alpha0p50__20260822-172515` |
| development | E8 forecast | 1.25 | 51.5 | 206 | 943 | 4 / 7 | 10.6% | `bellevue_116th_dev__adaptive__alpha0p50__20260822-173411` |
| final (240 s) | reference | 0.96 | 56.2 | 225 | 1204 | 0 / 7 | – | `bellevue_116th_final__adaptive__alpha0p50__20260822-174244` |
| final | E8 forecast | 0.96 | 56.2 | 225 | 1204 | 1 / 7 | 14.4% | `bellevue_116th_final__adaptive__alpha0p50__20260822-175319` |
| busy (107 s) | reference | 1.69 | 73.2 | 131 | 1066 | 3 / 3 | – | `bellevue_116th_busy__adaptive__alpha0p50__20260822-103419` |
| busy | E8 forecast | 2.14 | 88.3 | 158 | 1066 | 2 / 3 | 18.7% | `bellevue_116th_busy__adaptive__alpha0p50__20260822-164349` |

**Findings:**
1. E8 runs correctly on all three clips — it is not specific to the busy clip.
2. The forecast is **active on every clip**, and its activity **scales with
   congestion** (10.6% → 14.4% → 18.7%). A queue predictor should have more to do
   when there is more queueing, and it does.
3. Its **effect on outcomes scales the same way**: identical on the light final
   clip, +7 served on development, +27 served / +15 veh/min on the busy clip. On
   light traffic it correctly does almost nothing rather than harm; it acts only
   when there is a build-up to pre-empt.
4. Stops differ between clips (943 / 1204 / 1066 — the videos genuinely differ) but
   are identical within a clip across controllers — the open-loop property.
## E9 - spatial queue reach (own contribution), busy clip, alpha = 0.50

E9 replaces "count inside a hand-drawn box divided by a guessed capacity" with a
*geometric* measure: how far back along the visible approach the stopped traffic
extends, as a fraction of the ROI polygon's own upstream extent. Full derivation and
formulae in `report/my_contribution_E9.md`.

Columns specific to E9:
- **reach max** - the largest reach value the run produced (1.0 = queue filled the
  whole visible approach)
- **reach active** - share of frame-approach cells where reach > 0
- **reach > count** - share of cells where the reach reported *more* congestion than
  the count-based `normalized_queue`, i.e. where the count was blind

| psi | config file | avg wait (s) | throughput | served | over-saturated greens | reach max | reach active | reach > count | run_id |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 (reference) | `bellevue_116th.json` | 1.69 | 73.2 | 131 | **3 / 3** | - | - | - | `bellevue_116th_busy__adaptive__alpha0p50__20260822-103419` |
| 0.4 | `bellevue_116th_reach.json` | 2.32 | **74.9** | **134** | **1 / 3** | 0.94 | 54.5% | **28.2%** | `bellevue_116th_busy__adaptive__alpha0p50__20260906-214121` |

### The headline evidence for the measure

**The reach reported more congestion than the count in 28.2% of all frame-approach
observations.** That is the direct, quantified measurement of the blind spot E9 was
built to fix: in more than a quarter of the run, vehicles were stopped further back
along the approach than the count inside the Queue_Region could see. If the reach were
merely a restatement of the count, this figure would be 0%.

The reach was active (non-zero) in 54.5% of cells and peaked at 0.94 - a queue
extending 94% of the way back along the visible approach.

### Effect on control

**Over-saturated green phases fell from 3 of 3 to 1 of 3.** This is the metric E9
targets directly: a green phase is over-saturated when it ends with its queue not yet
discharged (Li et al.'s definition, measured by E5). Cutting that by two thirds means
the controller is now finishing the queues it starts.

Throughput and vehicles served both rose slightly (73.2 -> 74.9 veh/min, 131 -> 134).

Aggregate waiting rose (1.69 -> 2.32 s), and the per-approach view shows why - the
reach term changed *which* approach was served:

| Approach | reference: wait / served | E9 (psi=0.4): wait / served |
|---|---|---|
| North | 0.167 s / 122 | 0.373 s / 97 |
| East | 0.315 s / 0 | 0.315 s / 0 |
| South | 0.399 s / **0** | 0.313 s / **37** |
| West | 0.831 s / 9 | 1.502 s / 0 |

| configuration | GREEN phases in order |
|---|---|
| reference | North/30 s, North/45 s, **West/45 s** |
| E9 (psi=0.4) | North/30 s, **South/30 s**, North/45 s |

**Honest reading.** The reach term detected a long *spatial* queue on South that the
count-based measure never ranked highly, and served it - South goes from 0 vehicles
served to 37. The cost is that West, which the reference served once, is not reached
inside this 3-cycle window, so aggregate waiting rises. So at psi=0.4 E9 does exactly
what it was designed to do (find spatially-extended queues the count misses, and cut
over-saturation) but at this weight it displaces service rather than adding it. On a
107 s clip with only 3 green phases, serving a newly-detected approach necessarily
means not serving another.
### E9 psi sweep (busy clip) and cross-clip check

The reach weight `psi` was swept to find its operating point rather than reporting a
single hand-picked value.

| psi | avg wait (s) | throughput | served | over-saturated greens | run_id |
| --- | --- | --- | --- | --- | --- |
| 0.0 (reference) | **1.69** | 73.2 | 131 | 3 / 3 | `bellevue_116th_busy__adaptive__alpha0p50__20260822-103419` |
| 0.2 | 2.13 | 84.4 | 151 | 2 / 3 | `bellevue_116th_busy__adaptive__alpha0p50__20260906-220018` |
| 0.4 | 2.32 | 74.9 | 134 | **1 / 3** | `bellevue_116th_busy__adaptive__alpha0p50__20260906-214121` |
| 0.6 | 2.46 | **87.2** | **156** | 2 / 3 | `bellevue_116th_busy__adaptive__alpha0p50__20260906-220606` |

| configuration | GREEN phases in order |
|---|---|
| psi = 0.0 | North/30 s, North/45 s, West/45 s |
| psi = 0.2 | North/30 s, North/30 s, North/45 s |
| psi = 0.4 | North/30 s, **South**/30 s, North/45 s |
| psi = 0.6 | North/30 s, **South**/45 s, North/45 s |

Cross-clip check, same config, lighter footage:

| Clip | Controller | avg wait (s) | throughput | served | over-saturated | reach active | reach > count |
|---|---|---|---|---|---|---|---|
| development | reference | 1.02 | 49.8 | 199 | 4 / 6 | - | - |
| development | E9 (psi=0.4) | 1.17 | **52.3** | **209** | 4 / 7 | 23.6% | 12.6% |
| busy | reference | 1.69 | 73.2 | 131 | 3 / 3 | - | - |
| busy | E9 (psi=0.4) | 2.32 | 74.9 | 134 | **1 / 3** | 54.5% | 28.2% |

**Consistent findings across every psi and both clips:**

1. **Throughput and vehicles served rise at every weight tested** - busy: 73.2 -> 84.4 /
   74.9 / 87.2 veh/min (up to +19%), served 131 -> 151 / 134 / 156; development:
   49.8 -> 52.3 veh/min, served 199 -> 209. The measure consistently gets more traffic
   through.
2. **Over-saturation falls** - busy 3/3 -> 1/3 at the best weight. The controller
   finishes more of the queues it starts, which is the metric E9 targets by design.
3. **Average waiting rises** at every weight (1.69 -> 2.13 / 2.32 / 2.46). E9 is a
   throughput-and-completion mechanism, not a waiting-reduction mechanism, and the
   trade-off is monotone in `psi`.
4. **The measure's informativeness scales with congestion**, exactly as E8's did -
   reach exceeded the count-based measure in **12.6%** of observations on the light
   development clip and **28.2%** on the busy clip. Under heavier traffic the count is
   blind more often, and the reach fills in more.

**Recommended operating point:** `psi = 0.4`, which gives the lowest over-saturation
(1/3) while still raising throughput and vehicles served. `psi = 0.6` maximises
throughput if that is the objective.

**Why waiting rises (honest mechanism, from the phase sequences).** The reach term
detects a spatially long queue on South that the count-based measure never ranks
highly, and serves it - South goes from 0 vehicles served to 37. Within a 107 s window
holding only three green phases, serving a newly-detected approach necessarily means
not serving another, so West loses its single green and aggregate waiting rises. This
is a property of the short clip, not evidence the measure is wrong: it found real
traffic that the previous measure could not see.
## HEADLINE RESULT - Staged ablation of the proposed controller

`bellevue_116th_busy.mp4` (107 s, 30 fps). Each stage adds **exactly one** component to
the previous one, so the difference between two consecutive rows isolates that
component's contribution. Geometry, PCE weights, detector, tracker, green bands, yellow
and starvation limit are identical throughout - only the score composition varies.
Configs are `config/ablation_s1_raza.json` ... `config/ablation_s4_proposed.json`;
regenerate with `python make_ablation_configs.py`, rebuild the table with
`python ablation_table.py`.

| Stage | D | Q | G | S | Avg wait (s) | Throughput (veh/min) | Served | Over-saturated greens | run_id |
|---|---|---|---|---|---|---|---|---|---|
| **S0** fixed-time | - | - | - | - | 2.36 | 73.8 | 132 | 4 / 4 | `bellevue_116th_busy__fixed__alpha1p00__20260907-094022` |
| **S1** Raza baseline | Y | - | - | - | 2.25 | **105.7** | **189** | 2 / 3 | `bellevue_116th_busy__adaptive__alpha1p00__20260907-095151` |
| **S2** + queue | Y | Y | - | - | **1.69** | 73.2 | 131 | 3 / 3 | `bellevue_116th_busy__adaptive__alpha0p50__20260907-095726` |
| **S3** + prediction | Y | Y | Y | - | **1.69** | 73.2 | 131 | 3 / 3 | `bellevue_116th_busy__adaptive__alpha0p50__20260907-102844` |
| **S4** proposed (+ spillback risk) | Y | Y | Y | Y | 2.14 | 88.3 | 158 | **2 / 3** | `bellevue_116th_busy__adaptive__alpha0p50__20260907-103624` |

Score composition per stage:

| Stage | Score |
|---|---|
| S1 | `D` |
| S2 | `0.5*D + 0.5*Q` |
| S3 | `0.7*(0.5*D + 0.5*Q) + 0.3*G` |
| S4 | `0.4*(0.5*D + 0.5*Q) + 0.3*G + 0.3*S` |

Green phase sequences (the mechanism behind each row):

| Stage | GREEN phases in order |
|---|---|
| S0 | North/30 s, East/30 s, South/30 s, West/30 s |
| S1 | North/30 s, North/45 s, **South**/45 s |
| S2 | North/30 s, North/45 s, **West**/45 s |
| S3 | North/30 s, North/45 s, West/45 s *(identical to S2)* |
| S4 | North/30 s, North/45 s, **North**/45 s |

### What each step contributes

**S0 -> S1 (adaptive control helps).** Density-based adaptive control beats the fixed
timer on **both** axes: throughput 73.8 -> 105.7 veh/min (+43%), vehicles served
132 -> 189 (+43%), waiting 2.36 -> 2.25 s, and over-saturated green phases 4/4 -> 2/3.
The fixed timer is Pareto-dominated - there is no argument for it.

**S1 -> S2 (the queue term buys fairness).** Adding queue-awareness cuts waiting by
**25%** (2.25 -> 1.69 s), the best waiting figure in the whole table, at the cost of
throughput (105.7 -> 73.2). This is the fairness/throughput trade-off: the queue term
serves a standing-queue approach (West) that density-only control skips in favour of
the higher-flow South.

**S2 -> S3 (prediction alone was INERT - an honest negative result).** Adding the
count-based forecast at weight 0.3 produced a **bit-for-bit identical run**: same
waiting, same throughput, same vehicles served, same green phase sequence. The forecast
changed no decision at all.

**This negative result is the most informative row in the table**, because it is exactly
what the spillback-risk design predicted. The forecast projects `normalized_queue`,
which is a count divided by a configured capacity and therefore **saturates at 1.0**.
Once saturated its time-derivative is zero, so the projection reports a stable approach
even while the queue is physically still growing. The forecast was measured as active
(it fired in 10-19% of observations on other clips), but at this weight, on this clip,
it could not move a decision.

**S3 -> S4 (the spillback-risk term does what the forecast could not).** Replacing the
saturating basis with the **spatial** one - projecting `queue_reach`, which keeps rising
as the queue physically extends back - changed the third green phase and improved three
of the four measures:

| | S3 | S4 (proposed) | Change |
|---|---|---|---|
| Throughput | 73.2 | **88.3** veh/min | **+20.6%** |
| Vehicles served | 131 | **158** | **+20.6%** |
| Over-saturated greens | 3 / 3 | **2 / 3** | **-1** |
| Avg waiting | 1.69 s | 2.14 s | +0.45 s |

The risk signal exceeded current occupancy in **28.6%** of frame-approach observations -
in more than a quarter of the run it was projecting an approach toward running out of
storage before it had done so.

### The comparison that justifies the design

S3 and S4 differ **only** in which quantity is projected forward:

| | Projected quantity | Saturates? | Effect on control |
|---|---|---|---|
| S3 forecast (`G`) | count-based `normalized_queue` | **Yes**, at the configured capacity | **none - identical run** |
| S4 risk (`S`) | spatial `queue_reach` | **No**, bounded only by ROI geometry | +20.6% throughput, +20.6% served, one fewer unserved queue |

That is a controlled comparison of one design decision, and it comes out in favour of
the spatial basis. It is the strongest single piece of evidence in the project that the
spatial queue measure is worth having.

### Honest reading

- The proposed controller (S4) is a **throughput-and-completion** improvement, not a
  waiting-reduction one. If minimum waiting is the objective, S2 is the best row.
- S4 does not beat S1 on throughput (88.3 vs 105.7). S1 achieves its throughput by
  concentrating green on the highest-flow approaches and skipping queued ones - which is
  the behaviour S2's queue term was introduced to correct.
- The clip holds only 3 green phases, so one phase changing moves every aggregate
  substantially. These rows show **direction**, not precise effect sizes.
- Measurement remains open-loop: the recorded vehicles cannot react, so queue length and
  stop counts are fixed by the footage and only signal-gated metrics respond.
