# My Traffic-Light Project, Explained So Simply That a Child Could Follow It

This one file explains the **whole** project from the very beginning, using everyday
examples. Each part has two pieces:

* 🧒 **Simple version** — how you would explain it to a child.
* 🎓 **Say it to your professor** — the same idea in proper words.

Read it top to bottom once. Then read only the 🎓 lines again, and you can explain the
whole project.

---

## Part 1 — What problem are we solving?

### 🧒 Simple version

Imagine a crossroads where four roads meet: North, East, South and West. Cars come
from all four roads, but only **one road can go at a time**, or the cars would crash.
So we need a **traffic light** that decides:

1. **Which road goes next?**
2. **For how long does it stay green?**

A **dumb** traffic light just takes turns: North 30 seconds, East 30 seconds, South 30
seconds, West 30 seconds, again and again. It does this even if a road is empty and
another road has a huge line of cars. That wastes time.

A **smart** traffic light *looks* at the roads and gives green to the road that needs it
most. This project is about making a smart traffic light that **looks with a camera**.

### 🎓 Say it to your professor

> A signalised junction must repeatedly decide which approach gets green and for how
> long. Fixed-time control ignores the traffic that is actually present. My project is
> about camera-based adaptive control, and specifically about *what the camera should
> measure* to make that decision.

---

## Part 2 — The paper I started from (Raza, 2025)

### 🧒 Simple version

Some researchers (Raza and friends) already built a smart traffic light with a camera.
Here is how it works:

1. **The camera counts the vehicles on each road.** A bus or truck counts as about
   **3 cars**, because it is big and slow. A motorbike counts as half a car. This is
   called **PCE**: "how many cars is this vehicle worth".
2. It adds them up to get a **"how crowded is this road"** number. That is the **density**.
3. The **most crowded road gets green**.
4. How long the green lasts depends on how crowded the road is: a little crowded gets
   40 seconds, medium gets 60 seconds, very crowded gets 120 seconds.
5. **Fairness rule:** if a road has been skipped too many times in a row, it gets green
   next, no matter what. They call this the **Green Denial Counter**. It is like a
   teacher making sure every child gets a turn.

They tested it in a **computer traffic game called SUMO** and on real roads. It was
better than the dumb light.

### 🎓 Say it to your professor

> Raza et al., IEEE Access 2025: YOLO detects vehicles; density = Σ count × PCE, times a
> lane-priority weight; the approach with the highest density is served, unless a Green
> Denial Counter forces a starved approach; green time is 120/60/40 s by density band.
> They evaluated in SUMO and on real footage, and report up to 33% less congestion and
> 23% lower waiting than fixed-time control.

---

## Part 3 — What is missing in Raza's idea? (the "limitation")

### 🧒 Simple version

Raza's light **only looks at right now**. It counts cars *this second*. But two roads can
have the same count and be in very different situations:

* Road A: 5 cars waiting, and **more cars keep joining the back** → the line is getting longer.
* Road B: 5 cars waiting, and **cars are leaving** → the line is getting shorter.

Counting says "5 and 5, the same". But Road A is in trouble. Its line might soon reach
back so far that it **blocks the road behind it**.

Raza's light also does not know **how far back the line reaches**. It only knows how many
cars there are.

⚠️ **Important to say honestly:** Raza did not write "my system only looks at right now"
as a weakness. The weaknesses Raza listed were about the computer hardware and the
camera's detector. **I found this one myself** by studying how their method decides.

### 🎓 Say it to your professor

> The limitation I study is my own analysis of Raza's Algorithm 1: it is reactive.
> Selection and green length depend only on current density, so it cannot tell a growing
> queue from a clearing one, or say how far back a queue reaches. Raza's own stated
> limitations concern hardware and detection.

---

## Part 4 — The newer papers I read (Li 2025 and Wei 2025)

The method my professor wants is: *find newer research about the weakness.* I **chose**
two papers from IEEE T-ITS, a top journal for traffic research, both from 2025.

### 🧒 Simple version

**Li's paper:** "Don't just count the line. Think about the **shape** of the line: how
long it is and how it grows and shrinks. And check whether a green light was **long enough
to empty the line**. If not, the road is **over-saturated**, meaning more cars arrive than
can leave."

**Wei's paper:** "Don't just look at now. **Guess the future.** Predict how the line will
grow, and give green **before** the line spills back and blocks other roads."

**Spillback** means the line gets so long it reaches back and blocks the road or
crossroads behind it, like a line at an ice-cream shop that grows so long it blocks the
shop door next to it.

But both papers use **very heavy machinery**: special connected cars that send their
positions, giant maths solvers, and models of the whole city. I have **one camera and one
crossroads**. So I took **their idea**, not their machinery.

### 🎓 Say it to your professor

> Li et al. (T-ITS 2025) estimate queue profiles from connected-vehicle trajectories and
> minimise over-saturation with a MILP. Wei et al. (T-ITS 2025) use model-predictive
> control with queue dynamics to prevent spillback. Both depend on queue measurements
> they do not build: Wei assumes the state is measured; Li needs connected vehicles. I
> transferred the ideas, a queue profile and prediction, and supplied the measurement
> with a camera.

---

## Part 5 — My idea (my contribution)

### 🧒 Simple version: the cookie jar problem

Imagine I want to know how many cookies a child has. I give them a **small jar that holds
only 4 cookies**, and I count the cookies in the jar.

* 2 cookies → jar says 2. ✅
* 4 cookies → jar says 4 (full). ✅
* 10 cookies → the jar is full, so the extra 6 are on the table. The jar still says **4**. ❌

Once the jar is full, **it cannot tell me any more**. Counting cars in a small box near the
traffic light works the same way. That box is called the **queue region**. Once it is full,
the count stops growing, even if the line keeps getting longer behind it.

And if I try to **guess the future** from the jar ("it said 4, then 4, then 4, so it's not
growing"), I guess **wrong**. The jar is stuck at full, so my guess is stuck too.

### 🧒 Simple version: my fix, the measuring tape

Instead of counting cars in a small box, I lay a **measuring tape** along the road,
starting at the stop line (the white line where cars stop) and going backwards.

* Then I ask: **"How far back does the line of stopped cars reach?"**
* 0 means the line is right at the stop line. 1 means the line reaches the end of what the
  camera can see.
* I call this number **X, the queue reach**.

X can keep growing after the small box is full, because the line keeps getting longer
along the tape.

Then I **guess the future** from X. If X went from 0.30 to 0.40 in 2.5 seconds, it is
growing by 0.04 every second, so in 5 seconds it will be about 0.40 + 0.20 = **0.60**.
I call this guess **S, the spillback risk**. S close to 1 means "this line will soon fill
the whole road the camera can see".

### 🎓 Say it to your professor

> A count in a fixed stop-line region saturates: Q = 1 once the region is full, so its
> slope is 0 and the forecast F = Q + slope·H is stuck at 1. It cannot show growth.
> My enhancement measures *where* the stopped vehicles are: X is the tail of the
> contiguous chain of stopped vehicles along a drawn stop-line axis, and S = X + slope(X)·5 s
> projects it forward. S is the local risk of the queue filling the visible approach.

---

## Part 6 — How the camera "sees" (the Computer Vision part)

This is the most important part for a Computer Vision course.

### Step 1: Spotting the vehicles (YOLO)

🧒 **YOLO** is a computer program that looks at a picture and draws a **box around every
car, bus, truck and motorbike**. It is like a child playing "spot the cars" very, very
fast, 30 pictures every second. I use a version called **YOLOv8m**.

### Step 2: Giving each car a name tag (ByteTrack)

🧒 YOLO only says "there is a car here" in each picture. It does not know it is the
**same** car as in the last picture. **ByteTrack** gives every car a **name tag** (an ID
number, like #384) and keeps the same tag on the same car from picture to picture.

Why do I need name tags? To know if a car is **stopped**, I must compare **the same car**
now and one second ago. Without name tags, I can't.

### Step 3: Which road is the car on?

🧒 On the picture I drew **shapes**:

* a big shape over each road's incoming lanes (the **ROI**, "region of interest");
* a small strip just behind the stop line (the **queue region**);
* a line from the stop line backwards along the road (the **measuring tape**, or
  **queue axis**).

For each car I take the **middle of the bottom of its box**. That is where its wheels
touch the road. Then I check which shape that point is inside.

### Step 4: Is the car stopped?

🧒 I look at how far the car moved **in the last 1 second**, and compare it to **the car's
own size**. If it moved less than **a fifth of its own size per second**, it is stopped.

Why use the car's size? Because the camera sees **far cars as tiny** and **near cars as
big**. A far car driving fast only moves a few dots on the screen. If I only counted dots,
I would think it was stopped. Comparing to its own size is fair for near and far cars.

### Step 5: Where does the line end?

🧒 I start at the stop line and walk backwards along the measuring tape, from stopped car
to stopped car. I keep going as long as the next stopped car is **close behind** (about 2
car-lengths). When there is a big gap, the line has ended. Where it ends is **X**.

A car stopped far away **all by itself** (maybe parked) is **not** part of the line, so it
does not fool the measurement.

### 🎓 Say it to your professor

> YOLOv8m detects the four vehicle classes; ByteTrack keeps identities across frames,
> which every temporal measure needs. The bottom-centre point is assigned to an approach
> by point-in-polygon. A vehicle is "stopped" if its displacement over 1 s is below 0.2 of
> its own size per second; normalising by size removes perspective bias. X is the tail of
> the contiguous stopped chain from the stop line, with a gap allowance of two vehicle
> lengths measured along the road direction, so isolated stopped boxes are ignored.

---

## Part 7 — Problems I found and fixed while building it

This is where you show you really **built** the project. Each fix came from a problem I
**saw**.

| # | 🧒 What went wrong | How I noticed | How I fixed it |
|---|---|---|---|
| 1 | Boxes wiggle a little in every picture, even for parked cars | It said each car "stopped" 4 times in 107 seconds, which is silly | Look at movement over **1 second**, not one picture |
| 2 | Far cars look tiny, so fast far cars looked "stopped" | Lines looked long when they weren't | Compare movement to **the car's own size** |
| 3 | One lonely stopped car far away made the line look huge | X near 1 with no real line | The line must be a **connected chain** from the stop line |
| 4 | My road shapes were drawn wrong: one shape covered two roads, and two "directions" were backwards | After fix 1, X became almost always 0 | **Redrew** the shapes, with a real stop line and a measuring tape for each road |
| 5 | Cars seen from the side are long and flat, and my "2 car lengths" used the box height | The line was cut short on the West road | Measure car length **along the road** |
| 6 | The small YOLO missed far-away cars | Lines looked too short on far roads | Used a **bigger YOLO** (YOLOv8m), which finds 2–3 times more far cars |

After the fixes, the "stops per car" went from **4.28** (silly) to about **0.6–0.7**
(sensible), and the red "end of line" marker sits at the back of the real line in the
pictures (`report/queue_tail_validation.png`).

### 🎓 Say it to your professor

> Each correction was driven by an observed failure: box jitter (4.3 stops/vehicle),
> perspective bias, isolated stopped boxes, ROIs mixing legs with reversed axes, side-on
> vehicles, and far-field recall. After the fixes, stops/vehicle fell to 0.56–0.74 and the
> measured tail matches the visible queue where vehicles are detected.

---

## Part 8 — How the smart light decides (the controller)

### 🧒 Simple version

Every road gets a **score** from 0 to 1. A higher score means "I need green more".
The score mixes:

* **D** = how crowded the road is (Raza's idea);
* **Q** = how full the small box at the stop line is;
* **F** = guess of Q in 3 seconds (count-based guess);
* **X** = how far back the line reaches (my tape);
* **S** = guess of X in 5 seconds (my spillback risk).

Then:

1. **Fairness first:** if a road was skipped 3 turns in a row, it goes next.
2. Otherwise the **highest score goes next**.
3. The light goes **green → yellow (3 seconds) → red**, and only one road is ever green.

How long is the green? I tested two rules:

* **Raza-style rule:** higher score gives a longer green (30, 45 or 60 seconds).
* **Actuated rule:** stay green **until the line has emptied** (at least 10 seconds, at
  most 60 seconds). Like a teacher who lets a group leave the room until the room is
  empty, then calls the next group.

### 🎓 Say it to your professor

> Score = weighted mix of D, Q, F, X, S, all in [0, 1]. A starvation guard comes first,
> then argmax. Green length is either score bands (Raza-style) or actuated: a 10–60 s green
> ending after the queue strip has been empty for a 2 s passage time. My proposed
> controller S4 is 0.4·(0.5D + 0.5Q) + 0.3F + 0.3S.

---

## Part 9 — The big mistake I found in the old results (the audit)

### 🧒 Simple version

Before, the project said: "My smart light lets **21% more cars through**!" That sounded
great. But it was **wrong**, for two reasons.

**Reason 1: the video is a movie, not real life.** The video was **recorded in the past**.
The cars in it already did what the **real** traffic light told them, back then. They
**cannot see my pretend light**. It is like shouting "STOP!" at a car in a movie: the car
keeps going, because it's a movie.

So when my pretend light was green and a car drove away, that car **was not obeying me**.
It was obeying the real light from the past. My "21% more cars" only meant "my pretend
light happened to be green at the same time as the real light". That measures **luck**,
not how good my light is.

**Reason 2: a sneaky change.** When I added S, I also turned down the other parts of the
score (from 0.7 to 0.4). So I tested again with **S switched off** but everything else
the same. This is called the **NULL control**. It gave **exactly the same 158 cars**. So
the "improvement" did not come from S at all.

Finding my own mistake and proving it is a **strength**, not a weakness.

### 🎓 Say it to your professor

> On recorded footage, "vehicles served" equals recorded departures that fall inside the
> simulated green, so it measures agreement with the real filmed signal (S4's 158 were
> all North, green the whole clip). And the S3 → S4 change also lowered the base weight
> from 0.7 to 0.4: a weight-matched null control with S = 0 reproduced 158 exactly. So
> the +21% was an artefact.

---

## Part 10 — The fair test (the SUMO computer game)

### 🧒 Simple version

To test fairly, the cars must **obey my light**. So I used **SUMO**, a computer traffic
game, the same kind of tool Raza used. In SUMO:

* I build a crossroads with four roads, each 150 metres long.
* Pretend cars arrive at random times.
* **My own controller code** runs the lights, the exact same code as for the video.
* A **pretend camera** measures D, Q, X and S the same way the real camera does.
* The cars **stop and go because of my light**. Now it's real cause and effect!

To be fair:

* I tested **10 different light controllers**, including fixed time, Raza-style, mine,
  and the NULL control.
* in **9 traffic situations**, from quiet roads to roads overflowing with cars;
* each **20 times**, with different random traffic, and **every controller got the same
  20 traffic patterns** (so it's like the same exam for everyone);
* I **wrote down the rules before** running the final test, so I couldn't cheat by
  changing things after seeing the results (`sim/PROTOCOL.md`).

That is **7,920 test runs** in total.

### 🎓 Say it to your professor

> Closed-loop SUMO: my controller code from `src/` drives the lights; a virtual camera
> computes D, Q, X with the same definitions. Ten arms × nine scenarios × 20 paired seeds,
> two timing rules, two density normalisers, exact and vision-like sensors. The protocol
> was committed before the test seeds ran, and differences are reported with 95% intervals.

---

## Part 11 — What did I find?

### 🧒 Simple version

**Finding 1: the rule for how long the green lasts matters MOST.**
The "stay green until the line is empty" rule was the big winner. It cut waiting time in
**half** compared with the dumb light, and in very busy uneven traffic from about
**300 seconds to about 34 seconds**. Raza-style timing (higher score, longer green) was
often **worse** than the dumb light.

**Finding 2: adding my measuring tape (X) and future-guess (S) did NOT help.**
With the good timing rule, all the score versions gave **almost exactly the same waiting
time**, within about half a second.

**Finding 3: I found WHY.** My S changed **which road went next** in only **1 out of 40**
test runs. Two reasons:

1. **The measuring tape also has an end.** The camera only sees part of the road. When the
   line fills everything the camera sees, X says "1, full" for **every** busy road, so it
   can't tell them apart, just like the cookie jar, only a bigger jar.
2. **When the light has to choose, the longest line is also the most crowded road.** So
   "how far back is the line" and "how many cars are there" **point to the same road**.
   Adding X doesn't change the winner.

**Finding 4: on the real video, it is the same.** My controller picks **the same road** as
the version without S at every single green, on all three video clips.

### 🎓 Say it to your professor

> The green-time rule dominates: actuated timing roughly halves delay against fixed-time
> and cuts it about 9× in unequal over-saturation, while score-banded timing is worse than
> fixed-time in most scenarios. Adding X or S never measurably reduced delay or spillback.
> S changed the green choice in 1 of 40 inspected runs, because X saturates at the edge of
> the camera view and, at decision time, ranks approaches the same way density does.

---

## Part 12 — So is the project a failure? NO.

### 🧒 Simple version

My teacher's rule was: *"Make an improvement and **prove whether** it helps."*
It does **not** say "prove it helps". I:

1. ✅ found a real weakness;
2. ✅ read newer research about it;
3. ✅ built my own improvement, the measuring tape and future guess;
4. ✅ made the camera measurement actually work, fixing six problems;
5. ✅ found that the old "21% better" was a mistake, and proved it;
6. ✅ built a fair test where cars obey the light;
7. ✅ found the honest answer (it doesn't help the choice) **and explained why**;
8. ✅ found what *does* help: the green-time rule.

A scientist who reports "I tested it carefully and it did not work, and here is why" is a
**good** scientist.

### 🎓 Say it to your professor

> The contribution is the vision-based spatial queue measurement, the analysis of the
> count-saturation blind spot, and a controlled, pre-registered test showing the
> measurement does not improve approach selection, with the mechanism explained. The
> timing rule is where the gain lies, which agrees with Mohajerpoor et al. (T-ITS 2023),
> who use prediction to set timing, not selection.

---

## Part 13 — What is mine, and what is not

| ❌ Not mine (I used it and say so) | ✅ Mine |
|---|---|
| YOLO (spotting cars) | the measuring-tape idea on the camera picture (queue axis) |
| ByteTrack (name tags) | the "connected chain of stopped cars" way to find where the line ends (X) |
| SUMO (traffic game) | the future guess of the line, spillback risk (S) |
| The idea of smart lights, PCE, density (Raza) | showing why counting in a small box gets stuck (the cookie jar) |
| Queue and prediction ideas (Li, Wei) | the six measurement fixes |
| The Bellevue traffic videos | finding the old 21% mistake and proving it |
| | the fair test design and explaining the result |

---

## Part 14 — Honest limits (say these before the teacher asks)

1. 🧒 **The real video is a movie.** The cars can't obey my light, so real-video results
   only show measurements and choices, not "my light is better".
2. 🧒 **The fair test is a computer game.** It is not a real street.
3. 🧒 **One camera, one crossroads, three short videos** (about 10 minutes in total).
4. 🧒 **The camera can't see far.** It misses about half of the tiny far-away cars.
5. 🧒 **I can't see where the cars go after the crossroads.** So S is only about *this*
   road filling up, not about blocking roads further away. Never say "I detect spillback";
   say "**local spillback risk**".
6. 🧒 **My Raza version is "Raza-style", not an exact copy.** My greens are 30/45/60 s, not
   40/60/120 s, and I don't use their lane-priority weights.

---

## Part 15 — What I would do next

1. 🧒 **A second camera** that sees where cars go, to measure *real* spillback.
2. 🧒 **Use X to decide how long the green lasts** (stay green while the line is still
   shrinking), because timing is what mattered.
3. 🧒 **Better far-away detection**, or guess the end of the line even when far cars are
   missed (Zhu et al., T-ITS).
4. 🧒 **More videos** from more crossroads.

---

## Part 16 — Tiny dictionary

| Word | 🧒 Simple meaning |
|---|---|
| Approach | one of the 4 roads coming into the crossroads |
| Queue | the line of cars waiting |
| Density (D) | how crowded the road is |
| PCE | how many cars a vehicle is worth (bus = 3) |
| Queue region / Q | the small box at the stop line, and how full it is |
| Queue reach (X) | how far back the line reaches, from 0 to 1 |
| Forecast (F) | a guess of Q a few seconds later |
| Spillback risk (S) | a guess of X a few seconds later |
| Saturate | stuck at "full", like the cookie jar |
| YOLO | the program that draws boxes around cars |
| ByteTrack | the program that gives each car a name tag |
| ROI | the shape on the picture covering one road |
| Open loop | the cars **can't** react to my light (the video is a movie) |
| Closed loop | the cars **do** react to my light (SUMO) |
| NULL control | the same test with S switched off, to check whether S really did anything |
| Seed | one random traffic pattern; the same seed means the same "exam" |
| Actuated | stay green until the line empties |
| Over-saturated | more cars arrive than can leave |

---

## Part 17 — The numbers to remember

| What | Number |
|---|---|
| Stops per car, before → after fix | 4.28 → about 0.6–0.7 |
| Old claimed improvement | +21%, proven to be a mistake (NULL gave the same 158) |
| SUMO test runs | 7,920 |
| Controllers × situations × repeats | 10 × 9 × 20 |
| Dumb light vs. "stay green until empty", busy uneven roads | about 300 s → about 34 s waiting |
| How often S changed the choice | 1 out of 40 runs |
| Difference S made to waiting | within about ±0.5 s (no real effect) |
| Count box Q stuck at full on the real video | only 0–6% of the time |

---

## Part 18 — Quick quiz (answer out loud, then check)

1. Why can't a recorded video prove my light is better?
   → *The cars are a movie; they obey the old real light, not mine.*
2. Why does counting in a small box get stuck?
   → *Once the box is full, extra cars wait behind it; the count stays at full.*
3. What is X?
   → *How far back the line of stopped cars reaches, along a measuring tape from the stop line.*
4. What is S?
   → *A guess of X 5 seconds later, from how fast X is growing.*
5. Why do I need ByteTrack?
   → *To know it's the same car over time, so I can tell if it stopped.*
6. Why compare movement to the car's size?
   → *Far cars look tiny and move few dots even when fast.*
7. What did the NULL control show?
   → *The old +21% came from changing weights, not from S.*
8. What mattered most in the fair test?
   → *How long the green lasts: stay green until the line empties.*
9. Why didn't S help?
   → *X also gets stuck at the edge of the camera's view, and the longest line is usually
   also the most crowded road, so the choice doesn't change.*
10. Is "no improvement" a failure?
   → *No. The task was to prove* whether *it helps, and I proved it carefully and explained why.*

---

**Where to go deeper:** `docs/TECHNICAL_GUIDE.md` (every formula and code location),
`docs/VIVA_QA.md` (exact answers to the professor's questions), `report/FINAL_REPORT.md`
(the full report), `AUDIT_REPORT.md` (the mistakes and how they were found).
