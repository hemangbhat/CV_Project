"""How often does adding the spatial term change WHICH approach gets green?

Re-runs a few seeds of S3 and S4 (and S2 vs S3S) under actuated timing and compares
their green sequences decision by decision. Up to the first divergence the two runs are
identical (same seed, same history), so "first divergence" is the first decision the
score term actually changed; after it, the traffic differs and later decisions are not
comparable one-to-one.

    python -m sim.decision_analysis
"""
from sim.closed_loop import ARMS, run
from sim.scenario import scenarios

PAIRS = [("S3", "S4"), ("S2", "S3S")]
SCEN = ["heavy", "oversat", "unequal_oversat", "growing"]
SEEDS = range(100, 105)


def first_divergence(a: str, b: str) -> int | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def main() -> None:
    sc = scenarios()
    for norm in ("saturating", "physical"):
        print(f"== actuated timing, {norm} normaliser")
        for left, right in PAIRS:
            for name in SCEN:
                cells = []
                for seed in SEEDS:
                    a = run(sc[name], ARMS[left], seed, timing="actuated", norm=norm).green_sequence
                    b = run(sc[name], ARMS[right], seed, timing="actuated", norm=norm).green_sequence
                    d = first_divergence(a, b)
                    cells.append(f"{'never' if d is None else d}/{len(a)}")
                print(f"  {left} vs {right:4s} {name:16s} first differing green (of total): {'  '.join(cells)}")


if __name__ == "__main__":
    main()
