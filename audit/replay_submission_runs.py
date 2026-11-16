"""Replay representative stored Study 2 runs without replacing the test dataset."""
from pathlib import Path
import json
import tempfile
from sim.study2 import METHODS, run

rows = [json.loads(x) for x in Path("results/sim/study2/test_exact.jsonl").read_text().splitlines()]
with tempfile.TemporaryDirectory() as directory:
    for condition in ("short_minor_3000", "short_minor_3600"):
        for arm in ("ACT", "PROP"):
            expected = next(r for r in rows if r["condition"] == condition and
                            r["arm"] == arm and r["seed"] == 200)
            actual = run(condition, METHODS[arm], 200, workdir=Path(directory))
            for metric in ("mean_delay", "blocked_seconds"):
                assert abs(actual[metric] - expected[metric]) < 1e-6, (
                    condition, arm, metric, actual[metric], expected[metric])
            assert actual["green_sequence"] == expected["green_sequence"]
            print(condition, arm, "seed 200: delay, blockage and green sequence reproduced")
