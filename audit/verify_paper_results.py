"""Verify paper table values directly from raw seed logs; no extra dependencies.

Run from the repository root: python audit/verify_paper_results.py
Does not run simulations or overwrite experiment results.
"""
from collections import defaultdict
from pathlib import Path
import json
import math
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]


def read_rows(name):
    return [json.loads(x) for x in (ROOT / name).read_text().splitlines() if x.strip()]


def numbers(text):
    return [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]


def table(text, label):
    start = text.index(r"\label{" + label + "}")
    end = text.index(r"\end{table", start)
    return [line for line in text[start:end].splitlines() if " & " in line]


def check(paper, raw, decimals, context):
    if abs(paper - raw) > 0.5 * 10 ** -decimals + 1e-8:
        raise AssertionError(f"{context}: paper={paper}, raw={raw}")


def main():
    groups = defaultdict(dict)
    exact = read_rows("results/sim/study2/test_exact.jsonl")
    vision = read_rows("results/sim/study2/test_vision.jsonl")
    assert len(exact) == 1600 and len(vision) == 200
    for row in exact + vision:
        key = (row["noise"], row["condition"], row["arm"])
        assert row["seed"] not in groups[key], f"Duplicate run: {key}, {row['seed']}"
        groups[key][row["seed"]] = row
    assert len(groups) == 90
    for key, values in groups.items():
        assert set(values) == set(range(200, 220)), f"Incomplete paired seeds: {key}"
    conditions = [f"{g}_{n}" for g in ("uniform", "short_minor")
                  for n in (1200, 1800, 2400, 3000, 3600)]
    methods = ["FT", "ACT", "CMP", "RAZA", "RAZA_A", "PROP_B", "PROP", "PROP_CNT"]
    study1 = read_rows("results/sim/test_exact.jsonl")
    measurement = json.loads((ROOT / "report/measurement_compare.json").read_text())["busy"]
    for source in ("main.tex", "main_journal.tex"):
        text = (ROOT / "paper" / source).read_text()
        checked = 0
        for label, metric, decimals, selected in (
            ("tab:delay", "mean_delay", 1, conditions),
            ("tab:spill", "blocked_seconds", 0,
             [c for c in conditions if c.endswith(("2400", "3000", "3600"))]),
        ):
            lines = table(text, label)
            for arm in methods + ["vision"]:
                prefix = "PROP, " if arm == "vision" else arm.replace("_", "-")
                line = next(x for x in lines if
                            x.split(" & ")[0].split("~")[0] == prefix or
                            (arm == "vision" and x.startswith(prefix)))
                values = [numbers(x)[0] for x in line.split(" & ")[1:]]
                assert len(values) == len(selected)
                for condition, value in zip(selected, values):
                    data = groups[("vision" if arm == "vision" else "exact",
                                   condition, "PROP" if arm == "vision" else arm)]
                    raw = statistics.fmean(r[metric] for r in data.values())
                    check(value, raw, decimals, f"{source} {label} {arm} {condition}")
                    checked += 1
        for line in table(text, "tab:paired"):
            if not line.startswith(("RAZA &", "ACT &", "PROP-B &", "PROP-CNT &")):
                continue
            ref, condition, delay, blocked = line.split(" & ")
            condition = condition.replace("short-minor", "short_minor").replace(" ", "_")
            ref = ref.replace("-", "_")
            prop, other = groups["exact", condition, "PROP"], groups["exact", condition, ref]
            for metric, string in (("mean_delay", delay), ("blocked_seconds", blocked)):
                values = [prop[s][metric] - other[s][metric] for s in sorted(prop)]
                paper = numbers(string)
                decimals = 1 if re.search(r"\d\.\d", string) else 0
                check(paper[0], statistics.fmean(values), decimals, f"{source} paired {condition} {ref}")
                # The stored analysis uses a tabulated t critical value of 2.09 for n=20.
                ci = 2.09 * statistics.stdev(values) / math.sqrt(20)
                check(paper[1], ci, decimals, f"{source} paired CI {condition} {ref}")
                checked += 2
        assert checked == 176
        names = {"Light": "light", "Medium": "medium", "Heavy": "heavy",
                 "Unequal, over-sat.": "unequal_oversat", "Over-saturated": "oversat"}
        n_study1 = 0
        for line in table(text, "tab:study1"):
            name = line.split(" & ")[0]
            if name not in names:
                continue
            for value, (arm, timing) in zip(
                [numbers(x)[0] for x in line.split(" & ")[1:]],
                [("S0", "bands"), ("S1", "bands"), ("A0", "actuated"),
                 ("S1", "actuated"), ("S4", "actuated")],
            ):
                data = [r["mean_delay"] for r in study1 if r["arm"] == arm and
                        r["timing"] == timing and r["norm"] == "saturating" and
                        r["scenario"] == names[name]]
                assert len(data) == 20
                check(value, statistics.fmean(data), 1, f"{source} Table II {name} {arm}")
                n_study1 += 1
        assert n_study1 == 25
        stops = next(x for x in table(text, "tab:measure") if x.startswith("Stops per vehicle"))
        for value, mode in zip([numbers(x)[0] for x in stops.split(" & ")[1:]],
                               ["legacy", "robust", "v2"]):
            check(value, measurement[mode]["stops_per_vehicle"], 2, f"{source} Table I {mode}")
        print(f"{source}: 176 Study 2 values, 25 Study 1 means and 3 stop-event values match raw data.")
    a = (ROOT / "paper/main.tex").read_text().split(r"\maketitle", 1)[1]
    b = (ROOT / "paper/main_journal.tex").read_text().split(r"\maketitle", 1)[1]
    assert a == b, "The two layouts have different report bodies"
    for roll in ("2024UCA1829", "2024UCA1865"):
        for source in ("main.tex", "main_journal.tex"):
            assert roll in (ROOT / "paper" / source).read_text()
    unfinished = [r for r in exact if r["unfinished"]]
    assert len(unfinished) == 8
    assert all(r["arm"] == "FT" and r["condition"].endswith("3600") for r in unfinished)
    print("Study 2: 1,600 exact + 200 noise runs, unique keys and complete paired seeds 200-219.")
    print("Eight fixed-time runs at 3600 veh/h have unfinished vehicles: censoring is disclosed.")
    print("Identical report bodies and both author roll numbers verified.")


if __name__ == "__main__":
    main()
