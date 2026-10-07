"""Run the S3-vs-S4 comparison on the two 240-second clips.

Why. The headline S3 -> S4 result was measured on the 107 s busy clip, which contains only
three green phases, so the two stages differ in exactly ONE selection decision. A single
decision is a correctly measured difference but it is not evidence of an average effect.
This script re-runs the decisive comparison on the two 240 s clips, which carry roughly
twice the number of phases each, so the finding can be checked for reproducibility on
footage it was not derived from.

Role separation follows data/annotations/videos.json: bellevue_116th_dev.mp4 is the
development clip and bellevue_116th_final.mp4 the final one. The mechanism and its weights
were settled on the busy and dev clips, so the final clip is the closest thing this project
has to held-out footage.

Both stages use the calibrated geometry. The axis calibration is a property of the fixed
camera rather than of any one clip, so a direction measured on one clip is expected to hold
on another from the same camera; ``--check-calibration`` verifies that rather than assuming
it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

CLIPS = [
    ("videos/bellevue_116th_dev.mp4", "development"),
    ("videos/bellevue_116th_final.mp4", "final"),
]

STAGES = [
    ("S3", "config/ablation_s3_prediction_calibrated.json"),
    ("S4", "config/ablation_s4_proposed_calibrated.json"),
]


def run(command: list[str], label: str) -> bool:
    print(f"\n{'=' * 74}\n{label}\n{'=' * 74}", flush=True)
    print("  " + " ".join(command), flush=True)
    started = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started

    tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-9:]
    for line in tail:
        print("  " + line, flush=True)
    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode})", flush=True)
        print("  " + "\n  ".join(proc.stderr.splitlines()[-15:]), flush=True)
        return False
    print(f"  done in {elapsed / 60:.1f} min", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-calibration",
        action="store_true",
        help="also measure the axis direction on each clip, to confirm the calibration "
             "is a property of the camera rather than of the clip it was measured on",
    )
    args = parser.parse_args()

    ok = True
    for video, role in CLIPS:
        if args.check_calibration:
            ok &= run(
                [sys.executable, "-m", "src.main", "calibrate-axes",
                 "--video", video, "--config", "config/bellevue_116th.json",
                 "--no-display"],
                f"axis calibration cross-check on {video} ({role})",
            )
        for stage, config in STAGES:
            ok &= run(
                [sys.executable, "-m", "src.main", "control",
                 "--video", video, "--controller", "adaptive",
                 "--config", config, "--no-display", "--no-video"],
                f"{stage} on {video} ({role} clip)",
            )

    print(f"\n{'=' * 74}")
    print("ALL RUNS COMPLETE" if ok else "SOME RUNS FAILED - see above")
    print("Summarise with: python multiclip_table.py")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
