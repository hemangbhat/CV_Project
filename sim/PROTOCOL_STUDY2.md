# Study 2 protocol: vision-measured storage protection (frozen before the test seeds)

Study 1 (`sim/PROTOCOL.md`) found that the spatial measures X and S change nothing when
they only enter the *selection* score. Mohajerpoor et al. (IEEE T-ITS 24(1), 2023,
doi:10.1109/TITS.2022.3209606) use spatial queue information differently: as a
spillback-avoidance constraint on queue length relative to link length, and as a
reciprocal penalty that grows as a queue approaches the end of its link. They estimate
queue position with a shockwave model because loop detectors cannot observe it. Study 2
asks whether the camera's *measured* queue position, used that way, helps.

## Question
On a junction whose approaches have unequal storage, over graded demand, does a
Raza-style controller extended with (a) a storage barrier on the camera-measured
spillback risk S and (b) storage protection in the green timing reduce delay or local
spillback compared with the Raza-style baseline, standard actuated control and
capacity-aware max pressure? And is the *camera's spatial measurement* what matters, or
would a stopped-vehicle count do the same?

## Fixed design (`sim/study2.py`)
* Junction: 4 arms, 2 lanes, straight-through, one approach per phase, 3 s yellow,
  min green 10 s, max green 60 s for every actuated method.
* Geometries: `uniform` (all 150 m) and `short_minor` (North/South 150 m, East/West 60 m).
* Demand: Poisson, 5% heavy vehicles, shares N 35% / S 30% / E 20% / W 15%, total
  1200, 1800, 2400, 3000, 3600 veh/h (well under to beyond capacity). 1800 s of demand,
  first 300 s excluded.
* Sensor: the virtual camera of study 1; `exact`, and `vision` (≤30% far misses, 2 m jitter)
  for the proposed method.
* Methods: FT, ACT, CMP (Gregoire et al. 2015; 5 s decisions as in Mohajerpoor et al.),
  RAZA (bands), RAZA_A, PROP_B, **PROP**, PROP_CNT (see the table in `sim/study2.py`).

## Validation (seeds 0-9) and how the free choices were made
Recorded in `results/sim/study2/val_*.jsonl`.

1. All methods with λ = 1, α = 1.1, β = 0.85 (`val_main.jsonl`). Finding: protection
   halves blocked-entry time near capacity (uniform 3000: 88 → 43 s) but, beyond
   capacity, cuts greens so often that delay rises about fourfold (uniform 3600: 90 → 353 s).
2. λ ∈ {0.5, 2}, β = 0.95 (`val_l05_b95.jsonl`, `val_l2_b95.jsonl`): no material change
   to either finding. λ = 1, β = 0.85 kept.
3. Guards, in line with Mohajerpoor et al.'s remark that spillback avoidance must be
   relaxed when it is unavoidable: `slack` (protection may only cut a green whose own
   approach has S < 0.5), `after20` (only after 20 s of green), and both.
   Heavy conditions only (2400, 3000, 3600 veh/h, both geometries).

**Selection rule, written before the guard results were seen:** among the three guarded
variants, choose the one with the lowest mean blocked-entry seconds over the six heavy
validation conditions, among those whose mean delay is no more than 5% above PROP_B's in
*every* heavy condition. If none qualifies, PROP is frozen as the variant with the
smallest worst-case delay increase, and reported as failing the criterion.

**Outcome of the rule (validation, heavy conditions, delay relative to PROP_B):**

| variant | worst delay increase | mean blocked-entry s | qualifies |
|---|---|---|---|
| slack (S_active < 0.5) | +293% | 2193 | no (the guard never fired: an approach that is discharging always has S < 0.5) |
| after20 | +94% | 1365 | no |
| slack + after20 | +94% | 1365 | no (identical to after20) |

No variant qualifies. **PROP is frozen with `protect_after = 20 s`** (smallest worst-case
increase) and is reported as failing the criterion beyond capacity. On validation it
reduces blocked-entry time near capacity (short_minor 3000 veh/h: 861 → 650 s at +3%
delay) and increases delay beyond capacity (3600 veh/h: +94% uniform, +76% short_minor).

## Test (seeds 200-219, 20 seeds, run once after this file is committed)
* All 8 methods × 10 conditions, `exact`; PROP also under `vision`.
* Primary metric: mean delay per vehicle (time loss + insertion delay).
* Secondary: blocked-entry seconds (local spillback: the queue reaches the upstream end
  of the approach so arriving vehicles cannot enter), p95 delay, worst-approach delay.
* Pre-specified paired comparisons (95% t-interval over seeds): PROP − RAZA,
  PROP − ACT, PROP − CMP, PROP − PROP_B (effect of protection), PROP − PROP_CNT
  (effect of measuring the queue spatially rather than counting it).
* An effect is called present only if its interval excludes 0. Results are reported in
  full, including conditions where the proposed method is worse.
