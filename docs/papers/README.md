# Papers

| File | Paper | Role |
|---|---|---|
| `Raza2025_IEEEAccess_edge_ATLC_YOLO_PCE.pdf` | M. Raza et al., "An Edge-Deployed Real-Time Adaptive Traffic Light Control System Using YOLO-Based Vehicle Detection and PCE-Aware Density Estimation", *IEEE Access* 13, 2025, doi:10.1109/ACCESS.2025.3602844 | base paper |
| `Li2025_TITS_multiobjective_queue_profile.pdf` | C. Li, Y. Lu, H. Wang, "A Multi-Objective Model for Traffic Signal Coordination Control With Queue Profile Estimation", *IEEE T-ITS* 26(12), 2025, doi:10.1109/TITS.2025.3616119 | T-ITS paper 1 (selected for this project: queue profile, over-saturation) |
| `Wei2025_TITS_hierarchical_predictive_control.pdf` | Wei, Ampountolas, Hirrle, Wang, "Hierarchical Predictive Control of Network Traffic Signals Using Link Transmission Model With Queue Dynamics", *IEEE T-ITS* 26(10), 2025, doi:10.1109/TITS.2025.3568869 | T-ITS paper 2 (selected for this project: predictive queue dynamics, spillback) |

Facts about Raza used in the report, checked against the PDF (§III, Algorithm 1, §IV, §VI):

* density = Σ count × PCE (Eq. 1), then × lane-priority weight (left 3, right 2, through 1; Eq. 2);
* green time 120 / 60 / 40 s for high / moderate / low density;
* starvation prevention by a **Green Denial Counter** (consecutive cycles denied green, with a threshold),
  the same mechanism as this project's `cycles_waited` / `starvation_limit`;
* the controller was evaluated **in SUMO via TraCI** on a four-way intersection, as well as with real
  footage, against fixed-time control (up to 33% less congestion, 23% lower waiting time);
* the limitations the authors state (§VI) are power-aware edge nodes, online/continual learning,
  label noise, and multimodal sensor fusion. "Reactive, current-density-only allocation" is this
  project's own analysis of the method, not a limitation the authors list.

**Study 2 reference (not stored here: the copy we have is a licensed IEEE Xplore download):**
R. Mohajerpoor, C. Cai, M. Ramezani, "Optimal Traffic Signal Control of Isolated Oversaturated
Intersections Using Predicted Demand," *IEEE T-ITS* 24(1), 815–826, 2023,
doi:10.1109/TITS.2022.3209606. Facts used, checked against the PDF: the FASC algorithm sets
dynamic cycle lengths and splits from predicted demand and a shockwave queue model; the
spillback-avoidance constraint `x_p(k) ≤ β_p Λ_p` (Eq. 9, β = 1 major and 5 minor in their
experiment); the mixed delay + spillback-probability objective with the reciprocal term
`Λ_p / (α_p Λ_p − δ_p(k))` (Eq. 17); queue-formation and queue-discharging regimes; the remark
that spillback is often unavoidable in the queue-formation period; Aimsun experiment against
fixed, actuated and capacity-aware max pressure (5 s decisions), 63/55/40% less delay.

Further T-ITS work considered for this project: `docs/research/additional_tits_papers.md`.
