# Master Guide — Everything About This Project, In Simple Language
2
+
 
3
+
> **Read this one file and you can explain the whole project.**
4
+
> Written for the exact task the professor set: *"find IEEE TITS papers, take the
5
+
> limitations from them, and apply those to your project."*
6
+
>
7
+
> Every number in this document is measured, not estimated. Each one comes from a
8
+
> saved run file in `results/run_logs/` and carries a `run_id` so it can be checked.
9
+
 
10
+
---
11
+
 
12
+
# PART 1 — What the professor asked, and what I did
13
+
 
14
+
**What he asked:** Look at IEEE TITS papers, find their limitations, and use those
15
+
limitations to improve my project.
16
+
 
17
+
**What I did, in one sentence:**
18
+
I read two IEEE TITS 2025 papers, listed every limitation they have (both the ones
19
+
the authors admit and the ones I found myself), decided which of those limitations I
20
+
could actually fix in a camera-based single-junction project, and then built **8
21
+
improvements** into my system — the main one being a **queue forecast** that predicts
22
+
traffic build-up before it happens. I measured all 8 honestly and reported the ones
23
+
that worked *and* the ones that didn't.
24
+
 
25
+
**The three papers involved:**
26
+
 
27
+
| Role | Paper | Why it's here |
28
+
|---|---|---|
29
+
| My **base** project | Raza et al. 2025, *IEEE Access* | This is the system I already had: YOLO detects vehicles → calculates traffic density → adjusts traffic light |
30
+
| **Supporting** TITS paper | Li, Lu & Wang 2025, *IEEE TITS* | Teaches how to properly measure **queues** and detect **over-saturation** |
31
+
| **Main** TITS paper | **Wei et al. 2025, *IEEE TITS*** | Teaches **prediction** — don't just react to traffic now, predict where it's going |
32
+
 
33
+
---
34
+
 
35
+
# PART 2 — What my project actually is (the basics)
36
+
 
37
+
My project is a **traffic light controller that watches a video and decides which
38
+
road gets the green light.**
39
+
 
40
+
Step by step:
41
+
 
42
+
```
43
+
1. Take a video of a traffic junction (4 roads: North, East, South, West)
44
+
2. YOLO finds every vehicle in every frame (cars, bikes, buses, trucks)
45
+
3. ByteTrack gives each vehicle an ID so we can follow it across frames
46
+
4. We check which of the 4 roads each vehicle is on
47
+
5. For each road we measure:  how crowded is it?  how long is the queue?
48
+
6. We give each road a "score" based on those measurements
49
+
7. The road with the highest score gets the green light
50
+
8. How long the green lasts depends on how bad that road's score is
51
+
9. We draw the traffic light on the screen so you can watch it work
52
+
```
53
+
 
54
+
**Why this is useful:** A normal traffic light is on a fixed timer — it gives 30
55
+
seconds to an empty road while a packed road waits. Mine looks at the actual traffic
56
+
and gives green where it's needed.
57
+
 
58
+
**Important:** There is no real traffic light hardware. The signal is drawn on
59
+
screen as a simulation. This was always the plan and is stated in my project scope.
60
+
 
61
+
---
62
+
 
63
+
# PART 3 — The two TITS papers explained simply
64
+
 
65
+
## Paper A (Supporting): Li, Lu & Wang 2025 — IEEE TITS
66
+
*"A Multi-Objective Model for Traffic Signal Coordination Control With Queue Profile
67
+
Estimation"* — DOI 10.1109/TITS.2025.3616119
68
+
 
69
+
**What they do (simple version):**
70
+
They control a whole *road corridor* (5 traffic lights in a row) and try to
71
+
"green-wave" the traffic. Their clever idea is that **queue length should be
72
+
calculated, not guessed.** They work out how long it takes a queue to empty, and if
73
+
the green light is shorter than that time, they call the road "over-saturated" and
74
+
measure by how much it fell short.
75
+
 
76
+
Their main formula (simplified):
77
+
```
78
+
time needed to empty the queue = queue length ÷ discharge speed + lost time
79
+
if green time < time needed  ->  the road is over-saturated
80
+
shortfall = time needed − green time
81
+
```
82
+
 
83
+
**Their limitations:**
84
+
 
85
+
| Limitation | Simple explanation |
86
+
|---|---|
87
+
| It is not adaptive | They calculate the timings once, offline, and then just run them. They admit adaptive control is "future work." |
88
+
| Needs connected vehicles | They need GPS data from cars on the road. I don't have that. |
89
+
| Needs expensive software | Needs GUROBI/CPLEX (commercial optimisation software) and takes ~90 seconds per calculation. |
90
+
| Only works on a corridor | Green waves need multiple junctions. Useless for one junction. |
91
+
| Only tested in simulation | SUMO simulator only, never on a real road. |
92
+
| Assumes traffic arrives evenly | Real traffic arrives in bunches. |
93
+
| Only one vehicle type | Doesn't handle buses/trucks differently from cars. |
94
+
| Too many tuning knobs | They criticise other papers for needing hand-tuned settings that can't be worked out theoretically. |
95
+
 
96
+
## Paper B (MAIN): Wei et al. 2025 — IEEE TITS
97
+
*"Hierarchical Predictive Control of Network Traffic Signals Using Link Transmission
98
+
Model With Queue Dynamics"* — DOI 10.1109/TITS.2025.3568869
99
+
 
100
+
**What they do (simple version):**
101
+
They control a whole *network* of traffic lights and **predict the future**. Their
102
+
main point: if you only react to traffic that's already there, you're too late — the
103
+
queue may already be spilling backwards and blocking the junction behind it. So they
104
+
predict where the queues are heading and give green *early* to prevent that spillback.
105
+
 
106
+
**Their limitations:**
107
+
 
108
+
| Limitation | Simple explanation |
109
+
|---|---|
110
+
| **They assume someone else measures the traffic** | This is the big one. Their paper literally says it receives traffic data "from a state estimation and prediction module" — and they never build it. **My project IS that missing module.** |
111
+
| Ignores vehicle types | They admit buses, bikes and trams behave differently and they don't model it. |
112
+
| Needs a solver | Quadratic + nonlinear programming, MATLAB/CPLEX. Can't run on a small computer. |
113
+
| Simulation only | Made-up road networks in MATLAB. No real cameras, no real roads. |
114
+
| Only reacts every 10 seconds | Too slow to see a sudden queue build-up. |
115
+
| Assumes turning rates are known | Assumes you already know what % of cars turn left. |
116
+
| Doesn't handle gridlock or accidents | They state both as limitations. |
117
+
 
118
+
## The key insight I found by reading both together
119
+
 
120
+
Both papers argue the **same thing**: *queue length must be estimated and predicted,
121
+
not treated as an afterthought.* And both papers have the **same hole**: they assume
122
+
the traffic measurement already exists.
123
+
 
124
+
**My project fills that hole.** I have the camera, the detection, and the tracking —
125
+
which is exactly what they lack. So instead of copying their heavy mathematics
126
+
(which needs data and software I don't have), I took their **ideas** and implemented
127
+
them using computer vision at one junction.
128
+
 
129
+
> **This is the sentence to say to the professor:**
130
+
> *"Both TITS papers assume a traffic-measurement module exists and never build it.
131
+
> My project is that module. So I didn't reproduce their solvers — I took their ideas
132
+
> and implemented them with YOLO and tracking."*
133
+
 
134
+
---
135
+
 
136
+
# PART 4 — The 8 improvements I built
137
+
 
138
+
All 8 are **switchable and OFF by default**. That means my original project still
139
+
behaves exactly as before, and I can turn each improvement on one at a time to prove
140
+
what it does. Nothing is mixed together and guessed at.
141
+
 
142
+
| # | Name | From which paper | What it does (simple) | Did it help? |
143
+
|---|---|---|---|---|
144
+
| **E8** | **Queue forecast (MAIN)** | **Wei** | Watches how fast a queue is growing and predicts it 3 seconds into the future, so green is given *before* the road jams | **Not on its own** — stage S3 was inert. The projection only pays off in S4, applied to the spatial reach: +20.6% throughput, though waiting rose |
145
+
| E1 | Weighted queue | Raza + Wei | Counts a bus as 3 cars in the queue, not 1 | No change (my video has only cars) |
146
+
| E2 | Spillback detection | Wei | Notices vehicles stopped *past* the stop-line area, meaning the queue is overflowing | **Yes** — waiting down 9% |
147
+
| E3 | Smarter green time | Wei + Li | Green length = how long the queue needs to clear, instead of fixed 30/45/60s | No — made throughput worse here |
148
+
| E4 | Stability | Wei | Stops the light flip-flopping between roads every cycle | **Yes** — the only one that improved BOTH waiting and throughput |
149
+
| E5 | Over-saturation check | Li | Records whether each green light actually managed to clear its queue | **Yes** (as a diagnostic — see below) |
150
+
| E6 | Early cut-off | Li | Ends the green early if the queue is already empty; extends it if not | Partly — fewer unserved queues, but wasted time on light changes |
151
+
| E7 | Stop counting | Li | Counts how many times vehicles have to stop (Li's main measurement) | Measured, but see Part 6 |
152
+
 
153
+
## E8 — the main one, explained properly
154
+
 
155
+
This is the improvement to talk about most, because it's the one that answers
156
+
Wei et al.'s main point.
157
+
 
158
+
**The problem:** My original system (and Raza's) only looks at traffic *right now*.
159
+
If a queue is building fast, by the time it's big enough to win the score, it's
160
+
already too late — that's Wei et al.'s exact criticism.
161
+
 
162
+
**My solution, in plain steps:**
163
+
```
164
+
For each road, every frame:
165
+
   1. Remember the queue size from the last 15 frames (about half a second)
166
+
   2. Draw a straight line through those points to see the trend
167
+
      (is the queue growing? shrinking? steady?)
168
+
   3. Project that line 3 seconds into the future
169
+
   4. Use that predicted queue in the scoring decision
170
+
```
171
+
 
172
+
- If a queue is **growing** → the prediction is *higher* than now → that road gets
173
+
  green sooner. **This prevents the jam.**
174
+
- If a queue is **shrinking** → the prediction is *lower* → the system correctly
175
+
  leaves it alone.
176
+
 
177
+
**Why this is legitimate and not fake:** I can prove it's actually predicting, not
178
+
just repeating the current queue. **In about 19% of all measurements, the forecast was
179
+
higher than the actual current queue** — meaning one time in five it was acting on
180
+
traffic that hadn't arrived yet. If it were fake, that number would be zero.
181
+
 
182
+
**What makes it a real contribution:** Wei et al. need an MPC controller, a link
183
+
transmission model, and a commercial solver. I did the same *idea* with a straight
184
+
line through 15 numbers. It runs on every frame, on a normal laptop, with no extra
185
+
software.
186
+
 
187
+
---
188
+
 
189
+
# PART 5 — The results (all measured, all real)
190
+
 
191
+
## Result 1: My adaptive controller beats a normal fixed-timer light — on all 3 videos
192
+
 
193
+
*(graph: `report/graph_adaptive_vs_fixed.png`)*
194
+
 
195
+
| Video | Waiting time | Improvement | Throughput |
196
+
|---|---|---|---|
197
+
| development | 1.49s → **1.02s** | **32% less waiting** | 39.5 → 49.8 veh/min |
198
+
| final | 1.10s → **0.96s** | **13% less waiting** | 34.2 → 56.2 veh/min |
199
+
| busy | 2.36s → **1.69s** | **28% less waiting** | 73.8 → 73.2 veh/min |
200
+
 
201
+
**Say this:** *"The adaptive controller reduces waiting on every video I tested."*
202
+
 
203
+
## Result 2 (STRONGEST): The fixed-timer light is "dominated"
204
+
 
205
+
*(graph: `report/graph_alpha_pareto.png`)*
206
+
 
207
+
This is my best result. On the busy video:
208
+
 
209
+
| Controller | Throughput | Waiting |
210
+
|---|---|---|
211
+
| Fixed timer | 73.8 | 2.36s |
212
+
| My adaptive (density mode) | **105.7** | **2.25s** |
213
+
 
214
+
The adaptive controller is **better on both measurements at the same time**. In
215
+
research terms the fixed timer is *Pareto-dominated* — there is no argument for using
216
+
it. That's a clean, undeniable win.
217
+
 
218
+
**Bonus:** my `alpha` setting is a single dial that lets an operator choose:
219
+
 
220
+
| alpha setting | Throughput | Waiting | Best for |
221
+
|---|---|---|---|
222
+
| 1.0 (density only) | 105.7 | 2.25s | Maximum vehicles through |
223
+
| 0.5 (balanced) | 73.2 | 1.69s | Middle ground |
224
+
| 0.0 (queue only) | 71.6 | **1.53s** | Fairest — shortest waiting |
225
+
 
226
+
**Say this:** *"Alpha is one understandable dial that trades throughput against
227
+
fairness, and the operator can pick their point on the curve."*
228
+
 
229
+
## Result 3: The queue idea fixes a real failure
230
+
 
231
+
On the busy video, with density-only control, the **West road never got a green light
232
+
at all** (waited 1.50s, served 0 vehicles). Why? It had a queue of *stopped* cars —
233
+
so its density looked low, and a density-only system never picks it.
234
+
 
235
+
Turning on my queue term **served West and cut its waiting to 0.42s**.
236
+
 
237
+
**Say this:** *"I found a specific failure case of the base paper's approach and my
238
+
queue term fixes it."*
239
+
 
240
+
## Result 4: E8 forecast — a real lever, honestly reported
241
+
 
242
+
*(graph: `report/graph_enhancement_ablation.png`)*
243
+
 
244
+
| | Before | With E8 forecast |
245
+
|---|---|---|
246
+
| Throughput | 73.2 | **88.3 veh/min (+20.6%)** |
247
+
| Vehicles served | 131 | **158 (+20.6%)** |
248
+
| Roads left with unfinished queues | 3 of 3 | **2 of 3** |
249
+
| Waiting | 1.69s | 2.14s (worse) |
250
+
 
251
+
**Be honest here:** E8 pushes the system toward throughput. It gets 27 more vehicles
252
+
through and leaves fewer queues unserved, but average waiting goes up. It's a
253
+
**trade-off**, not a free win — and saying so is what makes the work credible.
254
+
 
255
+
## Result 5: E8 works on ALL videos, and gets more useful as traffic gets heavier
256
+
 
257
+
| Video | How often the forecast predicted growth | Effect |
258
+
|---|---|---|
259
+
| development (light) | 10.6% | +7 vehicles served |
260
+
| final (light-medium) | 14.4% | no change |
261
+
| busy (heavy) | 18.7% | **+27 vehicles, +15 veh/min** |
262
+
 
263
+
**This is a really good point to make:** the forecast does more work when there's
264
+
more congestion to predict — exactly what a queue predictor *should* do. And on light
265
+
traffic it correctly does almost nothing rather than causing harm.
266
+
 
267
+
## Result 6: My results are 100% reproducible
268
+
 
269
+
I ran the same settings **3 times each** to check for randomness:
270
+
 
271
+
| Measurement | Reference config (4 runs) | E8 config (3 runs) |
272
+
|---|---|---|
273
+
| Average waiting | 1.686s — **identical every time** | 2.143s — **identical every time** |
274
+
| Throughput | 73.2 — **identical** | 88.3 — **identical** |
275
+
| Vehicles served | 131 — **identical** | 158 — **identical** |
276
+
| Stops | 1066 — **identical** | 1066 — **identical** |
277
+
| Processing speed | 9.46 ± 1.92 fps | 8.94 ± 2.59 fps |
278
+
 
279
+
**Say this:** *"My pipeline is fully deterministic. Repeating a run gives byte-for-byte
280
+
identical results — the only thing that varies is how fast my laptop was that day.
281
+
So every number I report is exactly reproducible, and I don't need error bars on the
282
+
traffic measurements."*
283
+
 
284
+
That's a **stronger** statement than "mean ± standard deviation."
285
+
 
286
+
---
287
+
 
288
+
# PART 6 — The honest limitations (say these BEFORE he asks)
289
+
 
290
+
Being upfront about these is what separates a real study from an overclaimed one.
291
+
Professors test exactly here.
292
+
 
293
+
## Limitation 1: The evaluation is "open-loop" (most important)
294
+
 
295
+
**Simple explanation:** My video is a recording. The cars in the recording cannot
296
+
see my traffic light and cannot react to it. So no matter what my controller does,
297
+
the cars queue and stop at exactly the same moments.
298
+
 
299
+
**What this means:** Queue length and stop count are **fixed by the video** and are
300
+
identical for every controller I test. That's why my stop count is 1066 for
301
+
everything. Only the *signal-related* measurements (waiting time, throughput, vehicles
302
+
served) can change.
303
+
 
304
+
**What I can honestly claim:** *"I measure how well each controller allocates green
305
+
time to the traffic the video shows."*
306
+
**What I cannot claim:** *"My controller reduces real-world waiting time."*
307
+
 
308
+
**To prove that** I'd need a closed-loop simulator (SUMO) where cars react, or a real
309
+
road installation. Both are listed as out of scope in my project plan, so this is a
310
+
deliberate boundary, not something I forgot.
311
+
 
312
+
## Limitation 2: One camera
313
+
 
314
+
All my videos come from the same camera (Bellevue 116th Ave). The dataset only gives
315
+
different *times of day* of that one junction, not different junctions.
316
+
 
317
+
The 4 detection zones (ROIs) are hand-drawn for that camera's view. Point it at a
318
+
different junction and you must redraw them — otherwise vehicles fall outside the
319
+
zones and nothing is measured. **This is true of any fixed-camera system, including
320
+
Raza's.**
321
+
 
322
+
**Correct wording:** *"The method is camera-independent; the calibration is
323
+
camera-specific. A new junction needs one calibration pass, not a code change."*
324
+
 
325
+
## Limitation 3: Short busy clip
326
+
The busy video is only 107 seconds ≈ 3 signal cycles. One cycle changing can move the
327
+
averages a lot. So my results show the **direction** of an effect, not a precise size.
328
+
 
329
+
## Limitation 4: Hand-picked settings
330
+
Values like alpha, the forecast weight, and the spillback weight were chosen by me
331
+
beforehand, not tuned. Interestingly, **Li et al. criticise exactly this** ("burden of
332
+
hyperparameters"), and I found a real example: E2 and E4 each help alone, but together
333
+
they made things worse because their weights interact badly. I report that rather than
334
+
hiding it.
335
+
 
336
+
## Limitation 5: Not everything worked
337
+
E1 did nothing (my video has only cars). E3 and E6 hurt throughput because my queues
338
+
are small (3–4 vehicles) and the papers' methods assume tens of vehicles. **Reporting
339
+
failures is a strength.**
340
+
 
341
+
---
342
+
 
343
+
# PART 7 — Live demo script
344
+
 
345
+
```powershell
346
+
# 1. Normal fixed-timer light (the baseline)
347
+
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller fixed --config config/bellevue_116th.json
348
+
 
349
+
# 2. My adaptive controller
350
+
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th.json
351
+
 
352
+
# 3. THE MAIN ONE — with the E8 queue forecast
353
+
python -m src.main control --video videos/bellevue_116th_busy.mp4 --controller adaptive --config config/bellevue_116th_forecast_scoreonly.json
354
+
 
355
+
# 4. Prove it's all tested
356
+
python -m pytest -q
357
+
```
358
+
 
359
+
**Ready-made demo video (no waiting):**
360
+
`results/videos/DEMO_E8_forecast_busy.mp4` — shows the whole thing running with the
361
+
forecast visible on screen.
362
+
 
363
+
**What to point at on screen:**
364
+
- The 4 coloured zones = the four roads being watched
365
+
- Boxes with numbers = vehicles being tracked with IDs
366
+
- The side panel per road: `n=` vehicles, `q=` queue, `d=` density, `s=` score,
367
+
  **`f=` the forecast**
368
+
- **A `^` next to `f=` means the forecast is HIGHER than the current queue — that's
369
+
  the prediction actively working.** This is the single best thing to point at.
370
+
- The traffic light glyphs and the banner showing which road has green and for how long
371
+
 
372
+
---
373
+
 
374
+
# PART 8 — Questions he might ask, and your answers
375
+
 
376
+
**Q: What is your actual contribution?**
377
+
> The base paper reacts to current traffic density. I read two IEEE TITS papers,
378
+
> extracted their limitations, and built 8 improvements — the main one being a
379
+
> queue forecast that predicts build-up ~3 seconds ahead so green is given before a
380
+
> road jams. I measured each one separately and reported which helped and which
381
+
> didn't.
382
+
 
383
+
**Q: Why didn't you implement their actual method (the MPC / MILP)?**
384
+
> Because both need things I don't have: connected-vehicle GPS data, commercial
385
+
> solvers like GUROBI, and multiple junctions. Their solvers also take ~90 seconds per
386
+
> decision, which can't run per video frame. So I took the transferable idea — predict
387
+
> queue growth — and implemented it as a trend line that runs every frame.
388
+
 
389
+
**Q: How do you know your forecast really predicts?**
390
+
> Measured proof: in about 19% of all observations the forecast was *higher* than the
391
+
> currently measured queue, so it was acting on traffic that hadn't arrived yet. If it
392
+
> were simply copying the present, that number would be 0%.
393
+
 
394
+
**Q: Your enhancement made waiting worse. Isn't that a failure?**
395
+
> It's a trade-off, and I report it as one. E8 pushes toward throughput: 27 more
396
+
> vehicles served and fewer unfinished queues, at the cost of higher average waiting.
397
+
> Which is "better" depends on the objective. What matters is that I measured it and
398
+
> didn't hide it.
399
+
 
400
+
**Q: Why is the stop count identical for every controller?**
401
+
> Because the video is a recording — the cars can't react to my signal, so when they
402
+
> stopped is fixed by the footage. Only signal-gated measurements can change. That's
403
+
> the open-loop limitation, and it's why a causal claim needs SUMO or a real road.
404
+
 
405
+
**Q: Does it work on other videos?**
406
+
> Yes — all three clips, at three different congestion levels, and the forecast is
407
+
> active on all three (10.6% / 14.4% / 18.7%), doing more when traffic is heavier.
408
+
> All footage is from one camera though, so the detection zones are calibrated for
409
+
> that view. A new junction needs one calibration pass.
410
+
 
411
+
**Q: Did you check your results are reliable?**
412
+
> Yes — I re-ran the key configurations 3 times each. Every traffic measurement was
413
+
> **identical** every time; only the laptop's processing speed varied. The pipeline is
414
+
> deterministic, so every number is exactly reproducible.
415
+
 
416
+
**Q: What would you do next?**
417
+
> Three things: (1) closed-loop SUMO so cars react and I can make causal claims;
418
+
> (2) longer, more varied footage so the forecast and the current queue point to
419
+
> different roads and the prediction can show a distinct benefit; (3) tune the weights
420
+
> instead of choosing them beforehand.
421
+
 
422
+
---
423
+
 
424
+
# PART 9 — Numbers for a summary slide
425
+
 
426
+
- **762** automated tests, all passing
427
+
- **8** improvements taken from **2 IEEE TITS papers**
428
+
- **28+** saved run files — every number traceable
429
+
- **3** videos, **3** congestion levels, **4** roads
430
+
- Adaptive vs fixed timer: **32% / 13% / 28%** less waiting
431
+
- Fixed timer is **Pareto-dominated** (worse on both measures)
432
+
- E8 forecast alone: **no measurable effect** (stage S3 is bit-identical to S2). The **+20.6%** belongs to S4's spatial spillback risk.
433
+
- Repeat runs: **100% identical** results (deterministic)
434
+
- **0** hand-entered numbers
435
+
 
436
+
---
437
+
 
438
+
# PART 10 — Where to find everything
439
+
 
440
+
| I want to... | Open this |
441
+
|---|---|
442
+
| Explain the whole project | **This file** |
443
+
| Show slides | `report/RESULTS_HIGHLIGHTS.md` + the 3 graphs |
444
+
| Show the paper limitation analysis | `report/paper_limitations_analysis.md` |
445
+
| Show the literature review | `report/literature_review.md` |
446
+
| Show every measured number | `report/results_summary.md` |
447
+
| Show the demo | `results/videos/DEMO_E8_forecast_busy.mp4` |
448
+
| Show the code | `src/` (esp. `traffic_metrics.py` = forecast, `signal_controller.py` = control) |
449
+
| Show the tests | `tests/` — run `python -m pytest -q` |
450
+
| Show reproducibility | `python scripts_variance_analysis.py` |
451
+
 
452
+
---
453
+
 
454
+
# The 60-second version (if you only get one minute)
455
+
 
456
+
> "My base project used YOLO to detect vehicles and adjust traffic lights based on how
457
+
> crowded each road is. My professor asked me to find IEEE TITS papers, take their
458
+
> limitations, and apply them.
459
+
>
460
+
> I found two 2025 TITS papers. Both argue the same thing my project was weak on:
461
+
> queue length must be *estimated and predicted*, not treated as an afterthought. And
462
+
> both papers have the same hole — they assume a traffic-measurement module exists and
463
+
> never build it. My project *is* that module.
464
+
>
465
+
> So I built 8 improvements. The main one predicts each road's queue 3 seconds into
466
+
> the future from its growth trend, so green is given *before* the road jams — that's
467
+
> Wei et al.'s predictive idea, done with computer vision instead of their solver.
468
+
>
469
+
> Results: my adaptive controller beats a fixed timer on all three videos — 13 to 32%
470
+
> less waiting — and I proved the fixed timer is Pareto-dominated. The forecast is
471
+
> genuinely predictive: in 19% of observations it acted on traffic that hadn't arrived
472
+
> yet, and it raised throughput by 20.6%.
473
+
>
474
+
> I'm honest about the limits: the video is a recording, so cars can't react to my
475
+
> light — that means I measure green-time allocation, not real-world waiting. Proving
476
+
> that needs SUMO or a real road. And not every improvement worked — two made things
477
+
> worse, and I explain exactly why. Everything is backed by 762 tests and fully
478
+
> reproducible run logs."
479
+
 