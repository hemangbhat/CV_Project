"""Generate the staged ablation configurations.

Each stage adds exactly ONE component to the previous one, so the measured
difference between two consecutive rows isolates that component's contribution:

    S0  fixed-time            no measurement used at all (controller flag only)
    S1  Raza baseline         D           density only
    S2  + queue               D + Q
    S3  + prediction          D + Q + G   (queue growth / forecast)
    S4  proposed              D + Q + G + S  (+ spillback risk)

Everything else - geometry, PCE weights, detector, tracker, green bands, yellow,
starvation limit - is copied unchanged from config/bellevue_116th.json, so the only
thing varying between stages is the score composition.
"""

import argparse
import json

HORIZON = 3.0
RISK_HORIZON = 5.0
WINDOW = 15

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--base",
    default="config/bellevue_116th.json",
    help="junction configuration the stages inherit geometry and tuning from",
)
parser.add_argument(
    "--suffix",
    default="",
    help="appended to each output filename, e.g. '_calibrated' to write "
         "config/ablation_s4_proposed_calibrated.json",
)
args = parser.parse_args()
BASE_PATH = args.base

with open(BASE_PATH, encoding="utf-8") as handle:
    base = json.load(handle)

STAGES = {
    "s1_raza": {
        "doc": [
            "STAGE 1 - Raza baseline: DENSITY ONLY.",
            "alpha = 1.0 collapses the score to PCE density, which is the allocation",
            "rule of the base paper (Raza et al. 2025). Every extension is off.",
            "Score_i = D_i",
        ],
        "alpha": 1.0,
    },
    "s2_queue": {
        "doc": [
            "STAGE 2 - add QUEUE. alpha = 0.5 gives density and queue equal share.",
            "Isolates what queue-awareness alone buys over Raza.",
            "Score_i = 0.5*D_i + 0.5*Q_i",
        ],
        "alpha": 0.5,
    },
    "s3_prediction": {
        "doc": [
            "STAGE 3 - add PREDICTION (queue growth). The forecast term projects each",
            "approach's queue forward from its recent trend, so green can be granted",
            "before a queue forms rather than after.",
            "Score_i = 0.7*(0.5*D_i + 0.5*Q_i) + 0.3*G_i",
        ],
        "alpha": 0.5,
        "use_forecast": True,
        "forecast_weight": 0.3,
        "forecast_horizon_seconds": HORIZON,
        "forecast_window_frames": WINDOW,
    },
    "s4_proposed": {
        "doc": [
            "STAGE 4 - PROPOSED: add SPILLBACK RISK.",
            "The risk term projects each approach's spatial storage occupancy forward,",
            "so an approach on course to run out of storage is prioritised before it",
            "overflows. This is the full proposed controller.",
            "Score_i = 0.4*(0.5*D_i + 0.5*Q_i) + 0.3*G_i + 0.3*S_i",
        ],
        "alpha": 0.5,
        "use_forecast": True,
        "forecast_weight": 0.3,
        "forecast_horizon_seconds": HORIZON,
        "forecast_window_frames": WINDOW,
        "use_queue_reach": True,
        "queue_reach_weight": 0.0,          # reach is the risk BASIS, not a score term here
        "use_spillback_risk": True,
        "spillback_risk_weight": 0.3,
        "risk_horizon_seconds": RISK_HORIZON,
    },
}

for name, spec in STAGES.items():
    cfg = dict(base)
    doc = spec.pop("doc")
    cfg.update(spec)
    cfg = {"_comment": [*doc, f"Base geometry: {BASE_PATH}"], **cfg}
    path = f"config/ablation_{name}{args.suffix}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2)
    print("wrote", path)

print("\nStage 0 (fixed-time) needs no config: it is `--controller fixed` on any of them.")
