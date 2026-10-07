# Additional recent IEEE T-ITS papers relevant to this project

Searched October 2026. Each entry was confirmed by title and venue in search results
(full text was not accessible from the work environment, so read the paper itself before
quoting details beyond what is stated here). Neither replaces the two papers this project selected: Li
(queue profile) and Wei (predictive queue dynamics) remain the right pair for the research
chain. These two strengthen specific points.

## 1. Mohajerpoor, Cai & Ramezani (2023) — the closest match to this project's findings

R. Mohajerpoor, C. Cai, M. Ramezani, "Optimal Traffic Signal Control of Isolated
Oversaturated Intersections Using Predicted Demand," *IEEE Trans. Intell. Transp. Syst.*,
vol. 24, no. 1, pp. 815–826, 2023.

* **What it does:** an analytical controller for a *single, isolated* junction under
  over-saturation. It chooses dynamic cycle lengths and phase splits from *predicted demand*,
  with queue dynamics from kinematic-wave theory, and an objective that mixes delay with
  the *probability of spillback*. It reports large delay reductions against optimal
  fixed-time, actuated and capacity-aware max-pressure control.
* **Why it matters here:** it is the same setting as this project's closed-loop experiment
  (one junction, over-saturation, spillback). It differs from this project in exactly the
  place our results point to: it uses prediction to set **timing** (cycle length and splits),
  whereas we used it to **rank** approaches. Our decision analysis found that ranking almost
  never changes (S changed 1 of 40 runs), while timing dominated delay.
* **How to use it:** cite it in the discussion and future work as evidence that
  "prediction + spillback should enter the green-time decision, not the selection
  score". It also gives a fourth baseline idea: capacity-aware max pressure.

## 2. Zhu et al. (T-ITS, 2024/2025) — queue length from sparse vehicle trajectories

J. Zhu et al., "Cycle-by-Cycle Estimation of Queue Length at Signalized Intersections
Using Spatially Sparse Connected Vehicle Trajectories," *IEEE Trans. Intell. Transp. Syst.*,
doi:10.1109/TITS.2024.3498012 (vol. 26, no. 2, Feb. 2025).

* **What it does:** estimates queue length each cycle when only some vehicles report
  trajectories.
* **Why it matters here:** this project's camera produces per-vehicle trajectories
  (ByteTrack), and detection misses about half the distant cars, which makes them *spatially
  sparse* trajectories. That is the same problem this paper addresses. Its method is the
  natural next step for making X robust to missed far detections (limitation 3 in the
  report).
* **How to use it:** cite it in limitations and future work: "the vision trajectories are
  spatially sparse at distance; trajectory-based queue estimation (Zhu et al.) could
  infer the queue tail beyond the last detected stopped vehicle."

## Background works that come up in the same searches (older or not T-ITS)

* Capacity-aware / finite-storage **max pressure** (e.g. Gregoire et al.; Xiao et al.,
  "Pressure releasing policy in traffic signal control with finite queue capacities",
  IEEE 2014). This is the standard way the literature handles finite storage, i.e. spillback,
  in selection. Useful if you are asked "is there an established alternative to your score?"
* Mohajerpoor, Saberi, Ramezani, "Analytical derivation of the optimal traffic signal
  timing: minimizing delay variability and spillback probability for undersaturated
  intersections", *Transportation Research Part B*, 2019. The under-saturated
  predecessor of entry 1.

## Should the project be re-based on one of these?

No. The selected pair already gives the professor's chain
(Raza → limitation → Li & Wei → enhancement), and the project is complete on it. Adding
Mohajerpoor et al. (2023) as a *third* T-ITS reference strengthens the discussion of the
main finding, and Zhu et al. strengthens future work. Neither requires new experiments.
