"""Study 2: vision-measured storage protection (pre-registered in ``sim/PROTOCOL_STUDY2.md``).

Study 1 put the spatial measures X and S into the *selection* score and found they
change nothing. Mohajerpoor et al. (IEEE T-ITS 2023) point at where spatial queue
information does matter: in the *green timing*, as a constraint that a queue must not
outgrow its link (``x_p <= beta_p * link length``) and as a penalty that grows like
``1 / (alpha * link length - queue)`` as the queue nears the end of the link. They have
to *estimate* the queue position with a shockwave model because loop detectors cannot
see it. A camera measures it.

This module tests that idea on a junction whose approaches have unequal storage, over
graded demand levels, against standard and literature baselines:

=========  ==================================================================  ==============================
method     selection (WHICH approach)                                           timing (HOW LONG)
=========  ==================================================================  ==============================
FT         round robin                                                          fixed 30 s
ACT        round robin, skipping empty approaches                               actuated (stop-line gap-out)
CMP        capacity-aware max pressure, argmax n_i / C_i (Gregoire et al.)      re-decided every 5 s
RAZA       Raza-style PCE density score, starvation guard (Raza et al. 2025)    score bands 30/45/60 s
RAZA_A     RAZA's selection                                                     actuated
PROP_B     RAZA's density + storage barrier on the measured risk S              actuated
PROP       PROP_B                                                               actuated + storage protection
PROP_CNT   PROP with S built from the stopped *count*, not the measured reach   as PROP
=========  ==================================================================  ==============================

The proposed equation changes Raza's score in one term (cf. T-DLcR vs LcR):

    Raza:      score_i = D_i
    Proposed:  score_i = D_i + lam * ( 1 / (alpha - S_i) - 1 / alpha )

and adds one timing rule: after the minimum green, the current green ends when a
waiting approach's projected storage occupancy S_j reaches beta (and exceeds the
current approach's own). ``D_i`` is Raza's raw PCE density (one common normaliser,
so it is storage-blind, as in the paper); ``S_i`` is the camera's queue tail as a
fraction of *that approach's* visible storage, projected 5 s ahead, so it is
storage-aware by construction.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path

from src.config import APPROACH_NAMES
from src.signal_controller import AdaptiveController
from src.traffic_metrics import QueuePredictor, clamp, compute_config_scores
from sim.closed_loop import ARMS, FRAME_RATE, QUEUE_REGION_M, STEP, _approach_of, _summarise, arm_config
from sim.experiment import NOISE, _mean_ci, parse_seeds
from sim.scenario import APPROACHES, LANES, Scenario, build_network, in_edge, sumo_binary, write_routes
from sim.sensor import SensorGeometry, VehicleObs, jam_capacity, measure_approach, observe

RESULTS_DIR = Path("results/sim/study2")
DRAIN_LIMIT = 900.0
ORDER = ("North", "East", "South", "West")

YELLOW = 3.0
MIN_GREEN = 10.0
MAX_GREEN = 60.0
FIXED_GREEN = 30.0
PASSAGE = 2.0             # s the stop-line region must stay empty to gap out
CMP_STEP = 5.0            # s between max-pressure decisions (Mohajerpoor et al. use 5 s)
STARVATION_LIMIT = 3      # greens an approach with traffic may be passed over
FULL_FRACTION = 0.85     # true queue tail at or beyond this share of storage counts as "storage full"
COMMON_SATURATION = jam_capacity(150.0, LANES)  # Raza-style density normaliser (storage-blind)

#: Storage geometries. ``uniform`` is the study-1 junction. ``short_minor`` shortens the
#: minor street (East/West) to 60 m: the case where a count says little about how close
#: a queue is to the end of its road.
GEOMETRIES: dict[str, dict[str, float]] = {
    "uniform": {"North": 150.0, "East": 150.0, "South": 150.0, "West": 150.0},
    "short_minor": {"North": 150.0, "East": 60.0, "South": 150.0, "West": 60.0},
}
#: Demand split (major street North/South) and graded total demand, veh/h over the
#: junction. The levels play the role of the noise densities in a super-resolution
#: table: one column per level, from well under to beyond capacity.
SHARES = {"North": 0.35, "East": 0.20, "South": 0.30, "West": 0.15}
LEVELS = (1200, 1800, 2400, 3000, 3600)


def condition_names() -> list[str]:
    return [f"{g}_{lvl}" for g in GEOMETRIES for lvl in LEVELS]


def condition(name: str) -> tuple[Scenario, dict[str, float]]:
    geometry, level = name.rsplit("_", 1)
    demand = {a: round(float(level) * SHARES[a], 1) for a in APPROACHES}
    return Scenario(name, f"{geometry}, {level} veh/h", demand), GEOMETRIES[geometry]


@dataclass(frozen=True)
class Method:
    name: str
    label: str
    select: str                 # "rr" | "rr_skip" | "cmp" | "raza" | "prop"
    timing: str                 # "fixed" | "bands" | "actuated" | "cmp"
    protect: bool = False
    spatial: str = "reach"      # "reach" (camera queue tail) | "count" (stopped count)
    lam: float = 1.0
    alpha: float = 1.1
    beta: float = 0.85
    #: Protection may cut the active green only while the active approach itself has
    #: storage slack (its own S below this). 1.0 disables the guard.
    slack: float = 1.0
    #: Protection may cut the active green only after this much green.
    protect_after: float = MIN_GREEN


#: Frozen after validation (seeds 0-9); see PROTOCOL_STUDY2.md. No guarded variant met
#: the pre-stated criterion; PROTECT_AFTER is the one with the smallest worst-case
#: delay increase, as the protocol prescribes.
LAM = 1.0
BETA = 0.85
PROTECT_AFTER = 20.0
METHODS: dict[str, Method] = {
    "FT": Method("FT", "fixed-time 30 s", "rr", "fixed"),
    "ACT": Method("ACT", "actuated (stop-line gap-out)", "rr_skip", "actuated"),
    "CMP": Method("CMP", "capacity-aware max pressure", "cmp", "cmp"),
    "RAZA": Method("RAZA", "Raza-style density + bands", "raza", "bands"),
    "RAZA_A": Method("RAZA_A", "Raza-style density + actuated", "raza", "actuated"),
    "PROP_B": Method("PROP_B", "+ storage barrier on S", "prop", "actuated", lam=LAM, beta=BETA),
    "PROP": Method("PROP", "proposed: barrier + storage protection", "prop", "actuated",
                   protect=True, lam=LAM, beta=BETA, protect_after=PROTECT_AFTER),
    "PROP_CNT": Method("PROP_CNT", "control: proposed with count-based S", "prop", "actuated",
                       protect=True, spatial="count", lam=LAM, beta=BETA, protect_after=PROTECT_AFTER),
}


def barrier(s: float, alpha: float) -> float:
    """Mohajerpoor-style reciprocal penalty, zero for an empty approach."""
    return 1.0 / (alpha - min(s, 1.0)) - 1.0 / alpha


class Policy:
    """One method's decisions, fed one measurement per simulation step."""

    def __init__(self, method: Method, lengths: dict[str, float]) -> None:
        self.m = method
        self.capacity = {a: jam_capacity(lengths[a], LANES) for a in APPROACHES}
        self._rr = -1
        self._waited = {a: 0 for a in APPROACHES}
        self._empty_since: float | None = None
        self._green_len = FIXED_GREEN
        if method.select == "raza":
            self._raza_cfg = arm_config(ARMS["S1"], "bands")
            self._raza = AdaptiveController(self._raza_cfg)

    # -- selection ------------------------------------------------------------
    def choose(self, obs: dict, previous: str | None) -> str:
        m = self.m
        if m.select == "rr":
            self._rr = (self._rr + 1) % 4
            return ORDER[self._rr]
        if m.select == "rr_skip":
            for _ in range(4):
                self._rr = (self._rr + 1) % 4
                if obs["count"][ORDER[self._rr]] > 0:
                    return ORDER[self._rr]
            return ORDER[self._rr]
        if m.select == "raza":
            sel = self._raza.select(obs["raza_scores"])
            self._green_len = sel.green_time
            return sel.approach
        if m.select == "cmp":
            others = [a for a in APPROACHES if a != previous and obs["count"][a] > 0]
            pool = others or list(APPROACHES)
            return max(pool, key=lambda a: (obs["pressure"][a], -ORDER.index(a)))
        # prop: Raza density + storage barrier, with the same starvation guard as Raza.
        starved = [a for a in ORDER if a != previous and obs["count"][a] > 0
                   and self._waited[a] >= STARVATION_LIMIT]
        if starved:
            choice = max(starved, key=lambda a: self._waited[a])
        else:
            score = {a: obs["density"][a] + m.lam * barrier(obs["risk"][a], m.alpha)
                     for a in APPROACHES}
            others = [a for a in APPROACHES if a != previous and obs["count"][a] > 0]
            pool = others or list(APPROACHES)
            choice = max(pool, key=lambda a: (score[a], -ORDER.index(a)))
        for a in APPROACHES:
            self._waited[a] = 0 if a == choice else self._waited[a] + (obs["count"][a] > 0)
        return choice

    # -- timing ---------------------------------------------------------------
    def should_end(self, active: str, green: float, now: float, obs: dict) -> bool:
        m = self.m
        if m.timing == "fixed":
            return green >= FIXED_GREEN
        if m.timing == "bands":
            return green >= self._green_len
        if green >= MAX_GREEN:
            return True
        if m.timing == "cmp":
            if green < MIN_GREEN or (green - MIN_GREEN) % CMP_STEP > STEP / 2:
                return False
            best = max(APPROACHES, key=lambda a: obs["pressure"][a])
            return best != active and obs["pressure"][best] > obs["pressure"][active]
        # actuated: end once the stop-line region has been empty for the passage time.
        if obs["queue_region"][active] == 0:
            if self._empty_since is None:
                self._empty_since = now
        else:
            self._empty_since = None
        if green >= MIN_GREEN and self._empty_since is not None and now - self._empty_since >= PASSAGE:
            return True
        if m.protect and green >= m.protect_after:
            own = obs["risk"][active]
            if own >= m.slack:
                return False
            return any(obs["risk"][a] >= m.beta and obs["risk"][a] > own
                       for a in APPROACHES if a != active)
        return False

    def on_green(self) -> None:
        self._empty_since = None


@dataclass
class _Phase:
    approach: str
    state: object


def run(cond: str, method: Method, seed: int, noise_name: str = "exact",
        workdir: Path = Path("results/sim/runs2"), trace: list | None = None) -> dict:
    """One run. If ``trace`` is a list, one row per second is appended to it:
    (time, active approach, signal state, true queue tail per approach as a share of storage)."""
    import libsumo as traci

    scenario, lengths = condition(cond)
    net = build_network(lengths=lengths)
    noise = NOISE[noise_name]
    geometry = {
        a: SensorGeometry(roi_length=lengths[a], queue_length_m=QUEUE_REGION_M,
                          saturation_count=COMMON_SATURATION,
                          queue_capacity=jam_capacity(QUEUE_REGION_M, LANES))
        for a in APPROACHES
    }
    tag = f"{cond}__{method.name}__{noise_name}__s{seed}"
    routes = write_routes(scenario, seed, workdir / f"{cond}__s{seed}.rou.xml")
    tripinfo = workdir / f"{tag}.tripinfo.xml"
    cfg = arm_config(ARMS["S1"], "bands")
    cfg = dataclasses.replace(cfg, forecast_window_frames=max(2, int(round(2.5 * FRAME_RATE))))
    predictor = QueuePredictor(cfg, FRAME_RATE)
    rng = random.Random(seed * 7919 + 17)
    policy = Policy(method, lengths)

    traci.start([
        sumo_binary("sumo"), "-n", str(net), "-r", str(routes),
        "--seed", str(seed), "--step-length", str(STEP),
        "--tripinfo-output", str(tripinfo), "--tripinfo-output.write-unfinished", "true",
        "--time-to-teleport", "-1", "--no-step-log", "true", "--no-warnings", "true",
        "--duration-log.disable", "true",
    ])
    greens: list[str] = []
    blocked = 0.0
    full_seconds = {a: 0.0 for a in APPROACHES}   # true queue tail >= FULL_FRACTION of storage
    max_tail = {a: 0.0 for a in APPROACHES}
    try:
        links = traci.trafficlight.getControlledLinks("C")
        link_approach = [next(a for a in APPROACHES if g[0][0].startswith(in_edge(a))) for g in links]
        length_cache: dict[str, float] = {}
        active: str | None = None
        state = "Y"
        phase_start = -YELLOW
        step = 0
        horizon = scenario.duration + DRAIN_LIMIT
        obs = None
        while True:
            now = step * STEP
            if now >= horizon or (now >= scenario.duration and traci.simulation.getMinExpectedNumber() == 0):
                break
            # -- signal decision on the previous step's measurements
            if obs is not None or active is None:
                if state == "G" and policy.should_end(active, now - phase_start, now, obs):
                    state, phase_start = "Y", now
                elif state == "Y" and now - phase_start >= YELLOW:
                    active = policy.choose(obs or _empty_obs(), active)
                    greens.append(active)
                    policy.on_green()
                    state, phase_start = "G", now
            sig = "".join(("G" if state == "G" else "y") if a == active else "r" for a in link_approach)
            traci.trafficlight.setRedYellowGreenState("C", sig)
            traci.simulationStep()

            pending = traci.simulation.getPendingVehicles()
            if pending:
                blocked += STEP * len({_approach_of(v) for v in pending})

            metrics, counts, qregion, pressure, tails = {}, {}, {}, {}, {}
            for a in APPROACHES:
                vehicles = []
                for vid in traci.edge.getLastStepVehicleIDs(in_edge(a)):
                    vtype = traci.vehicle.getTypeID(vid)
                    if vtype not in length_cache:
                        length_cache[vtype] = traci.vehicletype.getLength(vtype)
                    vehicles.append(VehicleObs(lengths[a] - traci.vehicle.getLanePosition(vid),
                                               traci.vehicle.getSpeed(vid), length_cache[vtype], vtype))
                kw = dict(pce_weights=cfg.pce_weights, use_pce_weighting=cfg.use_pce_weighting,
                          stopped_speed_ratio=cfg.stopped_speed_ratio, queue_tail_gap=cfg.queue_tail_gap)
                truth = measure_approach(a, vehicles, geometry[a], **kw)
                max_tail[a] = max(max_tail[a], truth.queue_reach)
                if truth.queue_reach >= FULL_FRACTION:
                    full_seconds[a] += STEP
                if trace is not None and step % int(FRAME_RATE) == 0:
                    tails[a] = truth.queue_reach
                seen = observe(vehicles, geometry[a], noise, rng) if noise.active else vehicles
                m = measure_approach(a, seen, geometry[a], **kw) if noise.active else truth
                stopped = [v for v in seen if v.speed < cfg.stopped_speed_ratio * v.length]
                if method.spatial == "count":
                    # What a counting sensor gives: stopped vehicles x jam spacing / storage.
                    m = dataclasses.replace(m, queue_reach=clamp(len(stopped) / policy.capacity[a]))
                metrics[a] = m
                counts[a] = m.vehicle_count
                qregion[a] = m.queue_length
                pce = sum(cfg.pce_weights.get(v.vehicle_class, 1.0) for v in seen)
                pressure[a] = pce / policy.capacity[a]
            if trace is not None and tails:
                trace.append((now, active, state, dict(tails)))
            metrics = predictor.predict(metrics)
            obs = {
                "count": counts,
                "queue_region": qregion,
                "pressure": pressure,
                "density": {a: metrics[a].vehicle_density for a in APPROACHES},
                "risk": {a: metrics[a].spillback_risk for a in APPROACHES},
                "raza_scores": compute_config_scores(metrics, cfg),
            }
            step += 1
    finally:
        traci.close()

    class _Seq:
        phases = ()

    result = _summarise(scenario, method, seed, noise_name, tripinfo, blocked, 0.0, _Seq).as_dict()
    tripinfo.unlink(missing_ok=True)
    result.update(
        arm=method.name, condition=cond, greens=len(greens),
        green_sequence="".join(g[0] for g in greens),
        max_tail_fraction=max(max_tail.values()), max_tail=max_tail,
        full_seconds=full_seconds, full_seconds_total=sum(full_seconds.values()),
        lam=method.lam, beta=method.beta,
    )
    return result


def _empty_obs() -> dict:
    zero = {a: 0.0 for a in APPROACHES}
    return {"count": {a: 0 for a in APPROACHES}, "queue_region": dict(zero), "pressure": dict(zero),
            "density": dict(zero), "risk": dict(zero), "raza_scores": dict(zero)}


# -- grid ---------------------------------------------------------------------

def _job(args):
    cond, method, seed, noise_name = args
    return run(cond, method, seed, noise_name)


def run_grid(conds, methods: list[Method], seeds, noises, out: Path, workers: int) -> None:
    jobs = [(c, m, s, n) for c in conds for m in methods for s in seeds for n in noises]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle, Pool(workers) as pool:
        for k, row in enumerate(pool.imap_unordered(_job, jobs), 1):
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} runs", flush=True)


METRICS = ("mean_delay", "p95_delay", "blocked_seconds", "full_seconds_total", "throughput_vph",
           "worst_approach_delay")


def summarise(rows: list[dict], refs=("ACT", "CMP", "RAZA", "RAZA_A", "PROP_B", "PROP_CNT")) -> dict:
    grouped: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        grouped[(r["noise"], r["condition"], r["arm"])][r["seed"]] = r
    out: dict = {}
    for (noise, cond, arm), by_seed in grouped.items():
        entry = {metric: _mean_ci([by_seed[s][metric] for s in sorted(by_seed)]) for metric in METRICS}
        for ref in refs:
            other = grouped.get(("exact", cond, ref)) if noise != "exact" else grouped.get((noise, cond, ref))
            if not other or ref == arm:
                continue
            common = sorted(set(other) & set(by_seed))
            entry[f"vs_{ref}"] = {metric: _mean_ci([by_seed[s][metric] - other[s][metric] for s in common])
                                  for metric in METRICS}
        out.setdefault(noise, {}).setdefault(cond, {})[arm] = entry
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seeds", required=True)
    r.add_argument("--conditions", default=",".join(condition_names()))
    r.add_argument("--methods", default=",".join(METHODS))
    r.add_argument("--noise", default="exact")
    r.add_argument("--lam", type=float, default=None)
    r.add_argument("--beta", type=float, default=None)
    r.add_argument("--slack", type=float, default=None)
    r.add_argument("--protect-after", type=float, default=None)
    r.add_argument("--out", required=True)
    r.add_argument("--workers", type=int, default=4)
    t = sub.add_parser("table")
    t.add_argument("--results", required=True)
    t.add_argument("--json", default=None)
    args = parser.parse_args()
    if args.cmd == "run":
        methods = []
        for name in args.methods.split(","):
            m = METHODS[name]
            changes = {k: v for k, v in (("lam", args.lam), ("beta", args.beta), ("slack", args.slack),
                                         ("protect_after", args.protect_after)) if v is not None}
            if changes:
                m = dataclasses.replace(m, **changes)
            methods.append(m)
        run_grid(args.conditions.split(","), methods, parse_seeds(args.seeds), args.noise.split(","),
                 Path(args.out), args.workers)
    else:
        rows = [json.loads(x) for x in Path(args.results).read_text().splitlines() if x.strip()]
        summary = summarise(rows)
        for noise, by_cond in summary.items():
            for metric in ("mean_delay", "blocked_seconds"):
                print(f"\n=== {metric} ({noise})")
                arms = [a for a in METHODS if any(a in v for v in by_cond.values())]
                print(f"{'condition':18s}" + "".join(f"{a:>12s}" for a in arms))
                for cond in condition_names():
                    if cond not in by_cond:
                        continue
                    print(f"{cond:18s}" + "".join(
                        f"{by_cond[cond][a][metric]['mean']:12.1f}" if a in by_cond[cond] else f"{'-':>12s}"
                        for a in arms))
        if args.json:
            Path(args.json).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
