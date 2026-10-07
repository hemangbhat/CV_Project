"""Write the final video ablation configs to config/final/, one per arm.

Every arm inherits config/bellevue_116th_v2.json (recalibrated geometry + robust
measurement) and changes ONLY the score weights, using the same arm definitions as
the closed-loop experiment (sim/closed_loop.py ARMS), so a stage means the same thing
in the open-loop video runs and in the closed-loop simulation.

    python make_final_configs.py
"""
import json
from pathlib import Path

from sim.closed_loop import ARMS

BASE = Path("config/bellevue_116th_v2.json")
OUT = Path("config/final")

base = json.loads(BASE.read_text())
OUT.mkdir(parents=True, exist_ok=True)
for name, arm in ARMS.items():
    if arm.controller == "fixed":
        continue  # S0 / A0 read no score; run S0 with --controller fixed on the base config
    cfg = dict(base)
    cfg.update(arm.overrides)
    # In the video pipeline use_forecast also adds the forecast to the over-saturation
    # demand, so switch it on only where F is actually weighted; the predictor still
    # runs for S because the pipeline builds it whenever use_spillback_risk is set.
    cfg["use_forecast"] = cfg["forecast_weight"] > 0.0
    cfg["_comment"] = [f"FINAL ARM {name}: {arm.label}", f"Inherits {BASE}; only the score weights differ."]
    if arm.zero_fields:
        cfg["_comment"].append(
            "NULL control: identical to S4 but with S forced to 0. The CLI cannot zero a "
            "measure, so this arm is evaluated by replay (audit/replay_ablation.py) and in sim/."
        )
    (OUT / f"{name}.json").write_text(json.dumps(cfg, indent=2) + "\n")
    print("wrote", OUT / f"{name}.json")
