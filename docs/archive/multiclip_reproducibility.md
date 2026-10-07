# Does the S3 → S4 finding reproduce on more footage?

This is the honest follow-up to the headline result, and the answer is **partly — with a
condition I can now state precisely.** The condition turns out to be more interesting than an
unqualified win would have been, but it does narrow what may be claimed.

---

## 1. Why this test was necessary

The headline S3 → S4 comparison was measured on `bellevue_116th_busy.mp4`: 107 seconds, which at
a 30-second minimum green admits only **three green phases**. The two stages therefore differ in
**exactly one selection decision**. That is a correctly measured difference, but one decision is
not evidence of an average effect, and presenting it as though it were would not survive
scrutiny.

So I re-ran the same comparison on the 240-second clips, which carry roughly twice the phases
each.

## 2. Result

| Clip | Phases | Throughput S3 → S4 | Change | Waiting S3 → S4 | Over-sat | Decisions differing |
|---|---|---|---|---|---|---|
| busy (107 s) | 3 | 73.2 → 88.3 | **+20.6%** | 1.69 → 2.14 (worse) | 3/3 → 2/3 | **1** |
| development (240 s) | 7 | 51.5 → 51.5 | **+0.0%** | 1.25 → 1.25 | 4/7 → 4/7 | **0** |
| **final / held-out (240 s)** | 7 | 56.25 → 56.25 | **+0.0%** | 0.96 → 0.96 | 1/7 → 1/7 | **0** |

Run IDs:

| Clip | S3 | S4 |
|---|---|---|
| busy | `...busy__adaptive__alpha0p50__20260913-232105` | `...busy__adaptive__alpha0p50__20260913-232912` |
| dev | `...dev__adaptive__alpha0p50__20260914-083911` | `...dev__adaptive__alpha0p50__20260914-090322` |
| final | `...final__adaptive__alpha0p50__20260914-093459` | `...final__adaptive__alpha0p50__20260914-095045` |

**On both 240-second clips — including the held-out one — the spillback-risk term changed nothing
whatsoever.** Not a smaller gain: zero decisions differed and every metric is identical to three
decimal places.

So the effect exists on **1 of 3 clips**, and rests on **1 changed decision in total**.

I am reporting this prominently rather than burying it, for the same reason S3's inertness is
reported prominently: a mechanism that only works sometimes needs its *sometimes* characterised,
and that characterisation is itself the result.

## 3. Why it was inert — diagnosed, not guessed

The obvious worry is that the risk term simply never fired on the lighter clip. That is **not**
what happened:

| | development | busy |
|---|---|---|
| `spillback_risk > 0` | 25.0% of frame-approach observations | 50.4% |
| Frames where **any** score changed S3 → S4 | **83.4%** | — |
| Frames where the **top-ranked** approach changed | **15.3%** | — |
| Green decisions changed | **0** | 1 |

So the term fired constantly, altered scores on 83% of frames, and flipped the leading approach
on 15% of frames — and still changed no green. That combination looked contradictory until I
checked *what was actually deciding the greens*:

| | busy | development | final |
|---|---|---|---|
| Green phases | 3 | 7 | 7 |
| **Starvation-driven greens** | **0** | **4 of 7** | **4 of 7** |
| **Score-driven greens** | **3 of 3** | **3 of 7** | **3 of 7** |
| Starvation greens serving a **zero-score** approach | – | 3 of 4 | **4 of 4** |
| Frames where **every** approach scores 0 | **0%** | **15.8%** | **6.3%** |

On both long clips, 4 of 7 greens were forced by the starvation guarantee
(`_starved_approach`, `signal_controller.py:598`), and on the held-out clip **all four** of those
served an approach whose score was exactly `0.0` — an empty road, given green solely because the
fairness rule forbids indefinite skipping. Produced by `python diagnose_regime.py`.

**The mechanism is this.** On the two longer clips the junction is lightly loaded, so most
approaches are empty at any given moment and 15.8% / 6.3% of frames have *every* approach scoring
exactly zero. In **4 of 7 greens** the score is therefore not what selects the green — the
starvation guarantee is — and the resulting sequences are close to round-robin (dev: North → West →
East → South → North → West → East). **A score term cannot change a decision the score is not
making.** For those greens the risk term was not too weak; it was *structurally bypassed*.

That accounts for 4 of 7 on each long clip. The remaining **3 per clip were score-driven**, so the
term could have acted there and did not: it left the top-ranked approach unchanged in all of them.

### The honest denominator

Combining all three clips gives the number that actually matters:

| Clip | Score-driven greens (the term's opportunities) | Decisions it changed |
|---|---|---|
| busy (107 s, saturated) | 3 | **1** |
| development (240 s) | 3 | 0 |
| final / held-out (240 s) | 3 | 0 |
| **total** | **9** | **1** |

**The spatial risk term changed 1 of 9 green decisions where it was structurally capable of
acting.** That is the correct way to report this result, and it is a far more modest claim than an
unqualified "+20.6% throughput".

When it did act, the effect on that clip's aggregates was large — +20.6% throughput, one fewer
over-saturated green — but that magnitude is partly an artefact of the clip being only three
phases long, where any single decision moves every aggregate substantially.

On the busy clip there were **zero** starvation overrides and **no** all-zero frames, which is why
all three of its greens were available to the term, and it is the only clip with sustained
saturation — the condition the term was designed for.

## 4. What may and may not be claimed

**Defensible:**

- The term changed **1 of 9** green decisions where it was structurally capable of acting, across
  three clips.
- On the decision it did change, under saturated conditions, the measured effect was +20.6%
  throughput, +20.6% vehicles served and one fewer over-saturated green, at the cost of 27.1%
  worse average waiting. That decision is fully traced (frame 2430; see `axis_validation.md` §5)
  and the mechanism matched the design intent — it selected the approach whose count-based queue
  read mild (Q = 0.25) while its queue physically extended far back (X = 0.71).
- In **4 of 7** greens on the light clip the term could not have acted at all, because selection
  was starvation-driven. That is a property of the *regime*, not a defect in the term, and it is
  measured rather than assumed.
- The inertness on empty approaches is arguably **correct behaviour**. A storage-exhaustion risk
  term has nothing legitimate to do on an empty approach; firing there would be the bug.

**Not defensible, and not claimed:**

- That the mechanism improves throughput on average across conditions. Averaging one real effect
  with two structural zeros (+6.9%) would be meaningless, so no mean is quoted.
- That the effect size is established. It rests on **one decision**, on a clip where three phases
  mean any single decision moves every aggregate.
- That the term reliably fires when needed. On the 6 score-driven greens of the two long clips it
  had the opportunity and changed nothing.
- **That it generalises.** It was inert on the held-out clip. This is the single most important
  qualification in the project, and it should be stated before anyone asks.

The honest one-line summary: *the spatial risk term acted on 1 of 9 decisions available to it;
where it acted — on the only saturated clip — the effect was large and fully traceable; where it
did not, the reason is measured and structural rather than unexplained.*

### Is the contribution therefore worthless?

No, but its value has to be stated correctly. What is solidly established is **the diagnosis**, not
the effect size:

1. The count-based queue **provably saturates** — S3 is bit-identical to S2 across every clip, so
   projecting a saturated variable is measurably useless. That is a real, reproducible negative
   result about the base paper's state representation.
2. The spatial extent **provably does not saturate** — pinned by an executable test, and visible in
   the one decision where it mattered (Q = 0.25 while X = 0.71).
3. When the spatial variable was allowed to decide, it decided **in the direction the theory
   predicts**, and the trace confirms the mechanism rather than a coincidence.

What is *not* established is how often that matters in practice, because this dataset contains only
107 seconds of saturated footage. The contribution is a correct and tested measurement with a
demonstrated mechanism and an unquantified effect size — which is an honest place for a
one-semester single-junction study to land.

## 5. What would settle it

The blocker is not the mechanism, it is the amount of **saturated** footage. Of about 46 minutes
on disk, only 587 seconds passed frame-rate validation, and only 107 of those are congested.

1. **More saturated footage.** `videos/_candidates/b116_1208.mp4` already reads a clean 30.0 fps
   and is the immediate candidate; the other four need the gap-analysis and re-cutting described
   in the dataset section before their frame rates can be trusted.
2. **Report conditional on regime.** Split results by whether the score or the starvation
   guarantee selected each green. That partition is already recorded in every Run_Log
   (`starvation_overrides`), so it needs no new experiment — only more phases to partition.
3. **Lower `min_green_time` for the study.** At 30 s a 240 s clip yields 7 phases. A shorter
   minimum green would yield more decisions per second of footage, at the cost of realism. Worth
   doing as a sensitivity study, not as the headline configuration.

## 6. Reproducing this

```powershell
python run_multiclip_ablation.py --check-calibration   # ~90 min, CPU-only
python multiclip_table.py                              # the table in section 2
```

`multiclip_table.py` recomputes every metric from raw frame records via
`evaluation.compute_metrics` and identifies each stage by reading the resolved configuration back
out of the Run_Log, so no row can be mislabelled by a filename.
