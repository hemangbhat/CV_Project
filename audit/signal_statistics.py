"""Audit check: how often does the count-based queue actually saturate on the real
footage, and how noisy are the spatial reach (X) and spillback-risk (S) series?

Reads only logged per-frame metrics, which are controller-invariant in open loop.
Usage:  python audit/signal_statistics.py
"""
import json
import statistics as st

RUNS = [
    ("busy", "bellevue_116th_busy__adaptive__alpha0p50__20260913-232912"),
    ("dev", "bellevue_116th_dev__adaptive__alpha0p50__20260914-090322"),
    ("final", "bellevue_116th_final__adaptive__alpha0p50__20260914-095045"),
]
for clip, run in RUNS:
    d = json.load(open(f"results/run_logs_legacy/{run}.json"))
    caps = {a["name"]: a["queue_capacity"] for a in d["config"]["approaches"]}
    fr = d["frames"]
    print(f"== {clip} ({len(fr)} frames)")
    for a in ["North", "East", "South", "West"]:
        Q = [f["approaches"][a]["normalized_queue"] for f in fr]
        ql = [f["approaches"][a]["queue_length"] for f in fr]
        X = [f["approaches"][a]["queue_reach"] for f in fr]
        S = [f["approaches"][a]["spillback_risk"] for f in fr]
        sat = sum(1 for q in Q if q >= 1.0) / len(fr)
        jx = st.mean(abs(X[i] - X[i - 1]) for i in range(1, len(X)))
        js = st.mean(abs(S[i] - S[i - 1]) for i in range(1, len(S)))
        big = sum(1 for i in range(1, len(X)) if abs(X[i] - X[i - 1]) > 0.3) / len(X)
        print(
            f"  {a:6s} cap={caps[a]:.0f} max_queue={max(ql)} Q==1 in {sat:6.2%} of frames | "
            f"mean|dX|/frame={jx:.3f}  X jumps>0.3 in {big:5.1%} of frames  mean|dS|/frame={js:.3f}"
        )
