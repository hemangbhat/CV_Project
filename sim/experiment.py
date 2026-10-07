"""Run the closed-loop ablation grid and summarise it with paired statistics.

    python -m sim.experiment run   --seeds 100-119 [--scenarios a,b] [--arms S0,S4] [--noise exact,vision]
    python -m sim.experiment table [--results PATH]

Seeds 0-19 are the *validation* set (used for any design or weight choice); seeds
100-119 are the *test* set and are run once, after every choice is frozen. Every arm
in a scenario sees the same seeds, so arms are compared pairwise on identical demand
realisations (common random numbers), and differences are reported with a 95%
t-interval over seeds.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

from sim.closed_loop import ARMS, run
from sim.scenario import scenarios
from sim.sensor import NoiseModel

RESULTS_DIR = Path("results/sim")
NOISE = {
    "exact": NoiseModel(),
    # Vision-like camera: up to 30% of vehicles missed at the far end of the ROI,
    # 2 m position jitter per frame (AUDIT_REPORT.md W5).
    "vision": NoiseModel(far_miss=0.3, position_sigma=2.0),
}
METRICS = ("mean_delay", "p95_delay", "mean_waiting", "mean_stops", "throughput_vph",
           "blocked_seconds", "worst_approach_delay")
#: t(0.975, n-1) for small n, enough for the seed counts used here.
_T975 = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26,
         12: 2.20, 15: 2.14, 20: 2.09, 25: 2.06, 30: 2.05}


def t975(n: int) -> float:
    keys = sorted(_T975)
    for k in keys:
        if n <= k:
            return _T975[k]
    return 1.96


def _job(args):
    scenario_name, arm_name, seed, noise_name, timing, norm = args
    result = run(scenarios()[scenario_name], ARMS[arm_name], seed,
                 noise=NOISE[noise_name], noise_name=noise_name, timing=timing, norm=norm)
    return result.as_dict()


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        elif part:
            out.append(int(part))
    return out


def run_grid(scenario_names, arm_names, seeds, noise_names, timings, norms, out: Path, workers: int) -> None:
    jobs = []
    for timing, norm in [(t, n) for t in timings for n in norms]:
        for a in arm_names:
            # Arms with a forced timing (S0, A0) run once, under their own policy.
            if ARMS[a].timing and ARMS[a].timing != timing and len(timings) > 1:
                continue
            # S0/A0 read no Score, so the normaliser cannot affect them: run once.
            if ARMS[a].controller == "fixed" and norm != norms[0]:
                continue
            jobs += [(s, a, seed, n, timing, norm) for s in scenario_names
                     for seed in seeds for n in noise_names]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle, Pool(workers) as pool:
        for k, row in enumerate(pool.imap_unordered(_job, jobs), 1):
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} runs", flush=True)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarise(rows: list[dict], reference: str = "S0") -> dict:
    """Mean ± CI per (noise, scenario, arm), and paired differences vs ``reference`` and S3."""
    grouped: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        key = f'{r.get("timing", "bands")}/{r.get("norm", "physical")}/{r["noise"]}'
        grouped[(key, r["scenario"], r["arm"])][r["seed"]] = r
    # S0 and A0 read no Score, so one run serves as the reference in every table.
    groups = {g for g, _, _ in grouped}
    for (group, scenario, arm), by_seed in list(grouped.items()):
        if arm in ("S0", "A0"):
            noise = group.rsplit("/", 1)[1]
            for other in groups:
                if other.endswith("/" + noise):
                    grouped.setdefault((other, scenario, arm), by_seed)
    summary: dict = {}
    for (noise, scenario, arm), by_seed in grouped.items():
        entry = {}
        for metric in METRICS:
            values = [by_seed[s][metric] for s in sorted(by_seed)]
            entry[metric] = _mean_ci(values)
        for other in (reference, "A0", "S3", "NULL", "S2"):
            ref = grouped.get((noise, scenario, other))
            if not ref or other == arm:
                continue
            common = sorted(set(ref) & set(by_seed))
            entry[f"vs_{other}"] = {
                metric: _mean_ci([by_seed[s][metric] - ref[s][metric] for s in common])
                for metric in METRICS
            }
        entry["n"] = len(by_seed)
        summary.setdefault(noise, {}).setdefault(scenario, {})[arm] = entry
    return summary


def _mean_ci(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.fmean(values) if values else 0.0
    half = t975(n) * statistics.stdev(values) / math.sqrt(n) if n > 1 else float("nan")
    return {"mean": mean, "ci": half, "n": n}


def print_table(summary: dict, metric: str = "mean_delay") -> None:
    arms = [a for a in ARMS]
    for noise, by_scenario in summary.items():
        print(f"\n=== {metric}  (timing/normaliser/sensor: {noise})  mean ± 95% CI over seeds; Δ = paired vs S3 / vs NULL")
        print(f"{'scenario':11s}" + "".join(f"{a:>17s}" for a in arms))
        for scenario, by_arm in by_scenario.items():
            line = f"{scenario:11s}"
            for a in arms:
                e = by_arm.get(a)
                line += f"{e[metric]['mean']:9.1f}±{e[metric]['ci']:<6.1f}" + " " if e else f"{'-':>17s}"
            print(line)
        for comparison in ("vs_S3", "vs_NULL", "vs_S2"):
            print(f"  paired Δ{metric} {comparison}:")
            for scenario, by_arm in by_scenario.items():
                cells = []
                for a in ("S4", "S3S", "S3X", "S4X"):
                    d = by_arm.get(a, {}).get(comparison, {}).get(metric)
                    if d:
                        sig = "*" if abs(d["mean"]) > d["ci"] else " "
                        cells.append(f"{a}:{d['mean']:+7.1f}±{d['ci']:<5.1f}{sig}")
                print(f"    {scenario:11s} " + "  ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seeds", required=True)
    r.add_argument("--scenarios", default=",".join(scenarios()))
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--noise", default="exact")
    r.add_argument("--timing", default="bands,actuated")
    r.add_argument("--norm", default="physical,saturating")
    r.add_argument("--out", default=str(RESULTS_DIR / "results.jsonl"))
    r.add_argument("--workers", type=int, default=4)
    t = sub.add_parser("table")
    t.add_argument("--results", default=str(RESULTS_DIR / "results.jsonl"))
    t.add_argument("--metric", default="mean_delay")
    t.add_argument("--json", default=None, help="also write the summary JSON here")
    args = parser.parse_args()

    if args.cmd == "run":
        run_grid(args.scenarios.split(","), args.arms.split(","), parse_seeds(args.seeds),
                 args.noise.split(","), args.timing.split(","), args.norm.split(","), Path(args.out), args.workers)
    else:
        summary = summarise(load(Path(args.results)))
        print_table(summary, args.metric)
        if args.json:
            Path(args.json).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
