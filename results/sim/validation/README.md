# Validation-seed exploration (seeds 0-3)

These runs were used to make design choices **before** any test seed was run.
They are kept so the choices are auditable; they are not the reported results.

| File | What it showed | Decision taken |
|---|---|---|
| `01_bands_vs_actuated_original_scenarios.jsonl` | With Raza-style band timing (Score sets green length), every adaptive arm is worse than fixed-time, and adding any Score term lengthens greens (S4 worse than NULL by 4-15 s). Under the first actuated policy one `unequal` seed blew up. | Add a common **actuated** timing policy so the Score only selects the approach; keep **bands** as the faithful Raza-style policy. Diagnosed the blow-up as gap-out mid-discharge; added a 2 s passage time (`gap_out_seconds`). |
| `02_actuated_widened_scenarios.jsonl` | Scenarios sized for fixed-time capacity never stressed storage under actuated timing. | Widened the scenario set to and beyond actuated capacity (`oversat`, `unequal_oversat`, `growing` to 1450 veh/h). |

Note that file 01 predates the passage-time fix and the widened scenario set, so its
`actuated` rows and scenario names do not match the final configuration.
