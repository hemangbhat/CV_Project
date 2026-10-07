"""One closed-loop run: a controller from ``src`` driving the SUMO junction.

Per simulation step (``STEP`` seconds) the order is the video pipeline's order:

    sequencer.tick(scores of previous step) -> set the signal -> advance SUMO
    -> virtual camera -> QueuePredictor -> compute_config_scores / demands

so the controller code path is the one the video runs use, with the frame rate
replaced by ``1 / STEP``.
"""

from __future__ import annotations

import dataclasses
import random
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import APPROACH_NAMES, Config, load_config
from src.signal_controller import AdaptiveController, FixedTimeController, PhaseSequencer
from src.traffic_metrics import (
    QueuePredictor,
    compute_config_scores,
)
from sim.scenario import (
    APPROACH_LENGTH,
    LANES,
    Scenario,
    build_network,
    in_edge,
    sumo_binary,
    write_routes,
)
from sim.sensor import NoiseModel, SensorGeometry, VehicleObs, jam_capacity, measure_approach, observe

STEP = 0.5                    # s per simulation step = one "frame"
FRAME_RATE = 1.0 / STEP
DRAIN_LIMIT = 900.0           # s allowed after demand ends for the network to empty
QUEUE_REGION_M = 20.0         # m of Queue_Region at the stop line
TREND_WINDOW_S = 2.5          # s of history the F and S slopes are fitted over

#: The virtual camera's ROI is the whole approach (its visible storage). Two choices of
#: the density normaliser ``saturation_count`` are an experimental factor:
#:   physical   - jam capacity of the whole ROI (40 veh): D is a non-saturating occupancy,
#:                so the count already carries most of the spatial information;
#:   saturating - 10 veh (25% of storage), like the video configs, whose normaliser sits at
#:                the most vehicles the detector ever reports (D = 1 on 0-4% of frames):
#:                D then goes blind exactly where a long queue forms.
#: The Queue_Region normaliser is its jam capacity in both (4 veh in 20 m x 2 lanes).
GEOMETRIES: dict[str, SensorGeometry] = {
    "physical": SensorGeometry(
        roi_length=APPROACH_LENGTH,
        queue_length_m=QUEUE_REGION_M,
        saturation_count=jam_capacity(APPROACH_LENGTH, LANES),
        queue_capacity=jam_capacity(QUEUE_REGION_M, LANES),
    ),
    "saturating": SensorGeometry(
        roi_length=APPROACH_LENGTH,
        queue_length_m=QUEUE_REGION_M,
        saturation_count=10.0,
        queue_capacity=jam_capacity(QUEUE_REGION_M, LANES),
    ),
}
GEOMETRY = GEOMETRIES["physical"]

BASE_CONFIG = "config/ablation_s4_proposed_calibrated.json"


@dataclass(frozen=True)
class Arm:
    """One controller under test: a score composition, or the fixed-time plan."""

    name: str
    label: str
    controller: str                                   # "fixed" | "adaptive"
    overrides: dict[str, Any] = field(default_factory=dict)
    zero_fields: tuple[str, ...] = ()                 # measures forced to 0 (null controls)
    timing: str | None = None                         # force a timing policy for this arm


def _weights(alpha=0.5, F=0.0, X=0.0, S=0.0) -> dict[str, Any]:
    # use_forecast is always on: the predictor runs for every arm (it also fills S), and
    # a zero weight keeps an unweighted measure out of the Score.
    return dict(
        alpha=alpha,
        use_forecast=True,
        forecast_weight=F,
        use_queue_reach=X > 0,
        queue_reach_weight=X,
        use_spillback_risk=S > 0,
        spillback_risk_weight=S,
    )


#: Ablation arms. S0-S4 are the staged ablation of the report; the rest are controls:
#: NULL keeps S4's renormalised base weight with S zeroed (does S itself matter?),
#: S4-X swaps the projected risk for the un-projected reach (does projection matter?),
#: S3-S and S3-X put the spatial measures in place of the count forecast at S3's
#: exact weight (is the spatial state variable what matters?).
ARMS: dict[str, Arm] = {
    "S0": Arm("S0", "fixed-time 30 s", "fixed", timing="bands"),
    "A0": Arm("A0", "actuated round-robin (no score)", "fixed", timing="actuated"),
    "S1": Arm("S1", "Raza-style density (a=1)", "adaptive", _weights(alpha=1.0)),
    "S2": Arm("S2", "+queue (a=0.5)", "adaptive", _weights()),
    "S3": Arm("S3", "+count forecast F (0.7B+0.3F)", "adaptive", _weights(F=0.3)),
    "S4": Arm("S4", "proposed (0.4B+0.3F+0.3S)", "adaptive", _weights(F=0.3, S=0.3)),
    "NULL": Arm("NULL", "control: S4 with S zeroed", "adaptive", _weights(F=0.3, S=0.3),
                zero_fields=("spillback_risk",)),
    "S4X": Arm("S4X", "control: S4 with X instead of S", "adaptive", _weights(F=0.3, X=0.3)),
    "S3S": Arm("S3S", "spatial risk in place of F (0.7B+0.3S)", "adaptive", _weights(S=0.3)),
    "S3X": Arm("S3X", "spatial reach in place of F (0.7B+0.3X)", "adaptive", _weights(X=0.3)),
}


#: Green-time policies. ``bands``: the Raza-style rule of the video runs, Green_Time read
#: from the Score bands (30/45/60 s), so the Score sets both WHICH approach and HOW LONG.
#: ``actuated``: the Score only selects the approach; every arm then times its green the
#: same way (at least 10 s, held while the Queue_Region is occupied, at most 60 s,
#: ended once it has stayed empty for a 2 s passage time,
#: as a standard actuated controller does), so score composition cannot change green length through
#: the band thresholds. That is what makes the ablation a test of the state measure.
TIMINGS: dict[str, dict[str, Any]] = {
    "bands": dict(use_gap_out=False),
    "actuated": dict(use_gap_out=True, min_green_time=10.0, gap_out_seconds=2.0),
}


def arm_config(
    arm: Arm, timing: str = "bands", base: Config | None = None, norm: str = "physical", **extra: Any
) -> Config:
    """The run Config for ``arm``: base geometry/timing, the arm's weights, sim normalisers."""
    base = base or load_config(BASE_CONFIG)
    geometry = GEOMETRIES[norm]
    approaches = tuple(
        dataclasses.replace(
            a,
            saturation_count=float(geometry.saturation_count),
            queue_capacity=float(geometry.queue_capacity),
        )
        for a in base.approaches
    )
    values: dict[str, Any] = dict(
        approaches=approaches,
        forecast_window_frames=max(2, int(round(TREND_WINDOW_S * FRAME_RATE))),
        stopped_speed_ratio=0.2,
        queue_tail_gap=0.25,
    )
    values.update(TIMINGS[arm.timing or timing])
    values.update(arm.overrides)
    values.update(extra)
    return dataclasses.replace(base, **values)


@dataclass
class RunResult:
    scenario: str
    arm: str
    seed: int
    noise: str
    vehicles: int
    mean_delay: float          # s, timeLoss + departDelay (spillback wait included)
    p95_delay: float
    mean_waiting: float        # s, SUMO waitingTime (speed < 0.1 m/s) + departDelay
    mean_stops: float          # waitingCount per vehicle
    throughput_vph: float      # arrivals per hour inside the measured window
    blocked_seconds: float     # s summed over approaches with arrivals unable to enter
    worst_approach_delay: float
    unfinished: int
    greens: int
    max_tail_fraction: float   # true (noise-free) max queue tail / storage
    approach_delay: dict[str, float]
    timing: str = "bands"
    norm: str = "physical"

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _approach_of(vehicle_id: str) -> str:
    for name in APPROACH_NAMES:
        if f"_{name}_" in vehicle_id:
            return name
    raise ValueError(f"cannot tell the approach of vehicle {vehicle_id!r}")


def run(
    scenario: Scenario,
    arm: Arm,
    seed: int,
    *,
    noise: NoiseModel = NoiseModel(),
    noise_name: str = "exact",
    timing: str = "bands",
    norm: str = "physical",
    workdir: Path = Path("results/sim/runs"),
    config: Config | None = None,
) -> RunResult:
    """Run ``arm`` on ``scenario`` with ``seed`` and return its measures."""
    import libsumo as traci

    cfg = config or arm_config(arm, timing, norm=norm)
    geometry = GEOMETRIES[norm]
    net = build_network()
    tag = f"{scenario.name}__{arm.name}__{timing}__{norm}__{noise_name}__s{seed}"
    routes = write_routes(scenario, seed, workdir / f"{scenario.name}__s{seed}.rou.xml")
    tripinfo = workdir / f"{tag}.tripinfo.xml"

    controller = AdaptiveController(cfg) if arm.controller == "adaptive" else FixedTimeController()
    sequencer = PhaseSequencer(controller, cfg, FRAME_RATE)
    predictor = QueuePredictor(cfg, FRAME_RATE)
    rng = random.Random(seed * 7919 + 17)

    traci.start([
        sumo_binary("sumo"), "-n", str(net), "-r", str(routes),
        "--seed", str(seed), "--step-length", str(STEP),
        "--tripinfo-output", str(tripinfo), "--tripinfo-output.write-unfinished", "true",
        "--time-to-teleport", "-1", "--no-step-log", "true", "--no-warnings", "true",
        "--duration-log.disable", "true",
    ])
    try:
        links = traci.trafficlight.getControlledLinks("C")
        link_approach = []
        for group in links:
            in_lane = group[0][0]
            link_approach.append(
                next(a for a in APPROACH_NAMES if in_lane.startswith(in_edge(a)))
            )
        letter = {"GREEN": "G", "YELLOW": "y", "RED": "r"}
        length_cache: dict[str, float] = {}

        scores = {a: 0.0 for a in APPROACH_NAMES}
        demands = {a: 0.0 for a in APPROACH_NAMES}
        blocked = 0.0
        max_tail = 0.0
        step = 0
        horizon = scenario.duration + DRAIN_LIMIT
        while True:
            now = step * STEP
            if now >= horizon or (now >= scenario.duration and traci.simulation.getMinExpectedNumber() == 0):
                break
            sequencer.tick(step, scores, demands)
            states = sequencer.signal_states()
            traci.trafficlight.setRedYellowGreenState(
                "C", "".join(letter[states[a].value] for a in link_approach)
            )
            traci.simulationStep()

            pending = traci.simulation.getPendingVehicles()
            if pending:
                blocked += STEP * len({_approach_of(v) for v in pending})

            metrics = {}
            for name in APPROACH_NAMES:
                obs = []
                for vid in traci.edge.getLastStepVehicleIDs(in_edge(name)):
                    vtype = traci.vehicle.getTypeID(vid)
                    if vtype not in length_cache:
                        length_cache[vtype] = traci.vehicletype.getLength(vtype)
                    distance = APPROACH_LENGTH - traci.vehicle.getLanePosition(vid)
                    obs.append(VehicleObs(distance, traci.vehicle.getSpeed(vid),
                                          length_cache[vtype], vtype))
                true_metrics = measure_approach(
                    name, obs, geometry, pce_weights=cfg.pce_weights,
                    use_pce_weighting=cfg.use_pce_weighting,
                    stopped_speed_ratio=cfg.stopped_speed_ratio,
                    queue_tail_gap=cfg.queue_tail_gap,
                )
                max_tail = max(max_tail, true_metrics.queue_reach)
                metrics[name] = (
                    measure_approach(
                        name, observe(obs, geometry, noise, rng), geometry,
                        pce_weights=cfg.pce_weights, use_pce_weighting=cfg.use_pce_weighting,
                        stopped_speed_ratio=cfg.stopped_speed_ratio,
                        queue_tail_gap=cfg.queue_tail_gap,
                    )
                    if noise.active
                    else true_metrics
                )
            metrics = predictor.predict(metrics)
            if arm.zero_fields:
                metrics = {
                    n: dataclasses.replace(m, **{f: 0.0 for f in arm.zero_fields})
                    for n, m in metrics.items()
                }
            scores = compute_config_scores(metrics, cfg)
            # One demand rule for every arm: the vehicles in the Queue_Region now. The
            # config-driven rule adds forecast/spillback terms per arm, which would let
            # the score composition change gap-out timing too.
            demands = {n: float(m.queue_pce) for n, m in metrics.items()}
            step += 1
        sequencer.finalize(max(step - 1, 0))
    finally:
        traci.close()

    tripinfo_result = _summarise(scenario, arm, seed, noise_name, tripinfo, blocked, max_tail, sequencer)
    tripinfo_result.timing = arm.timing or timing
    tripinfo_result.norm = norm
    tripinfo.unlink(missing_ok=True)
    return tripinfo_result


def _summarise(scenario, arm, seed, noise_name, tripinfo: Path, blocked, max_tail, sequencer) -> RunResult:
    delays, waits, stops = [], [], []
    per_approach: dict[str, list[float]] = {a: [] for a in APPROACH_NAMES}
    arrived_in_window = 0
    unfinished = 0
    for trip in ET.parse(tripinfo).getroot().iter("tripinfo"):
        depart = float(trip.get("depart"))
        depart_delay = float(trip.get("departDelay"))
        scheduled = depart - depart_delay if depart >= 0 else float("nan")
        arrival = float(trip.get("arrival"))
        if arrival < 0:
            unfinished += 1
        if not (scheduled >= scenario.warmup):
            continue
        delay = float(trip.get("timeLoss")) + depart_delay
        delays.append(delay)
        waits.append(float(trip.get("waitingTime")) + depart_delay)
        stops.append(float(trip.get("waitingCount")))
        per_approach[_approach_of(trip.get("id"))].append(delay)
        if scenario.warmup <= arrival <= scenario.duration:
            arrived_in_window += 1
    hours = (scenario.duration - scenario.warmup) / 3600.0
    approach_delay = {a: (statistics.fmean(v) if v else 0.0) for a, v in per_approach.items()}
    delays_sorted = sorted(delays)
    return RunResult(
        scenario=scenario.name,
        arm=arm.name,
        seed=seed,
        noise=noise_name,
        vehicles=len(delays),
        mean_delay=statistics.fmean(delays) if delays else 0.0,
        p95_delay=delays_sorted[int(0.95 * (len(delays_sorted) - 1))] if delays else 0.0,
        mean_waiting=statistics.fmean(waits) if waits else 0.0,
        mean_stops=statistics.fmean(stops) if stops else 0.0,
        throughput_vph=arrived_in_window / hours,
        blocked_seconds=blocked,
        worst_approach_delay=max(approach_delay.values()),
        unfinished=unfinished,
        greens=sum(1 for p in sequencer.phases if p.state.value == "GREEN"),
        max_tail_fraction=max_tail,
        approach_delay=approach_delay,
    )
