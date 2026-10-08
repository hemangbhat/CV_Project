"""SUMO network and demand for the closed-loop experiment.

Network. One four-arm signalised junction. Each approach is a two-lane inbound edge
``APPROACH_LENGTH`` metres long — the *visible storage* the virtual camera sees, the
analogue of an approach ROI. When an approach's queue fills that storage, arriving
vehicles cannot enter and wait upstream (SUMO's insertion backlog): that is local
spillback, measured directly as ``depart_delay`` and as blocked-entry seconds.
All movements are straight through, and the signal serves one approach at a time,
exactly the phase structure of the video controller.

Demand. Poisson arrivals per approach (``period="exp(λ)"``), 5% heavy vehicles.
Scenarios are defined as veh/h per approach, optionally with time-varying surges.
The ``calibrated`` scenario takes its per-approach *shares* from the vision
pipeline's counts on the Bellevue footage (``results/sim/video_demand.json``) and
scales them to a medium-heavy total.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

APPROACHES = ("North", "East", "South", "West")
#: compass offset of each approach's far node, and its outbound (straight-on) edge.
_GEOMETRY = {
    "North": ((0.0, 1.0), "S_out"),
    "East": ((1.0, 0.0), "W_out"),
    "South": ((0.0, -1.0), "N_out"),
    "West": ((-1.0, 0.0), "E_out"),
}
_PREFIX = {"North": "N", "East": "E", "South": "S", "West": "W"}

APPROACH_LENGTH = 150.0   # m of inbound storage per approach (the visible ROI)
LANES = 2
SPEED = 13.89             # m/s (50 km/h)
HEAVY_SHARE = 0.05        # trucks/buses, PCE 3

BUILD_DIR = Path("results/sim/build")
VIDEO_DEMAND_PATH = Path("results/sim/video_demand.json")


def sumo_binary(name: str) -> str:
    import sumo  # the eclipse-sumo wheel

    return os.path.join(sumo.SUMO_HOME, "bin", name)


def in_edge(approach: str) -> str:
    return f"{_PREFIX[approach]}_in"


def build_network(directory: Path = BUILD_DIR, lengths: dict[str, float] | None = None) -> Path:
    """Write and netconvert the junction; return the ``.net.xml`` path (cached).

    ``lengths`` gives each approach's inbound length (its storage) in metres; by
    default every approach is ``APPROACH_LENGTH`` long (the study-1 junction).
    """
    directory.mkdir(parents=True, exist_ok=True)
    if lengths is None or all(lengths[a] == APPROACH_LENGTH for a in APPROACHES):
        stem = "junction"
        lengths = {a: APPROACH_LENGTH for a in APPROACHES}
    else:
        stem = "junction_" + "_".join(f"{lengths[a]:g}" for a in APPROACHES)
    net = directory / f"{stem}.net.xml"
    if net.exists():
        return net
    nodes = ['<nodes>', '  <node id="C" x="0" y="0" type="traffic_light"/>']
    edges = ['<edges>']
    for approach, ((dx, dy), _) in _GEOMETRY.items():
        p = _PREFIX[approach]
        far = lengths[approach]
        nodes.append(f'  <node id="{p}" x="{dx * far}" y="{dy * far}" type="priority"/>')
        edges.append(
            f'  <edge id="{p}_in" from="{p}" to="C" numLanes="{LANES}" speed="{SPEED}"/>'
        )
        edges.append(
            f'  <edge id="{p}_out" from="C" to="{p}" numLanes="{LANES}" speed="{SPEED}"/>'
        )
    nodes.append('</nodes>')
    edges.append('</edges>')
    (directory / f"{stem}.nod.xml").write_text("\n".join(nodes) + "\n")
    (directory / f"{stem}.edg.xml").write_text("\n".join(edges) + "\n")
    subprocess.run(
        [
            sumo_binary("netconvert"),
            "--node-files", str(directory / f"{stem}.nod.xml"),
            "--edge-files", str(directory / f"{stem}.edg.xml"),
            "--no-turnarounds", "true",
            "--junctions.corner-detail", "0",
            "--no-internal-links", "false",
            "--tls.default-type", "static",
            "-o", str(net),
        ],
        check=True,
        capture_output=True,
    )
    return net


@dataclass(frozen=True)
class Surge:
    """A burst of extra demand on one approach: [start, end) seconds, extra veh/h."""

    approach: str
    start: float
    end: float
    extra: float


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    demand: dict[str, float]                 # base veh/h per approach
    surges: tuple[Surge, ...] = field(default_factory=tuple)
    duration: float = 1800.0                 # s of demand
    warmup: float = 300.0                    # s excluded from the statistics


def _periodic_surges(approach: str, extra: float, every: float, length: float, duration: float):
    return tuple(
        Surge(approach, t, t + length, extra)
        for t in range(int(every / 2), int(duration), int(every))
    )


def load_video_shares() -> dict[str, float] | None:
    if not VIDEO_DEMAND_PATH.exists():
        return None
    data = json.loads(VIDEO_DEMAND_PATH.read_text())
    shares = data.get("shares")
    return {a: float(shares[a]) for a in APPROACHES} if shares else None


def scenarios() -> dict[str, Scenario]:
    """The scenario set, spanning from well under to beyond junction capacity.

    Capacity reference: with four single-approach phases, 2 lanes, ~1800 veh/h/lane
    saturation flow, 60 s maximum green and 3 s yellow, one approach can discharge at
    most ~850 veh/h when all four run at maximum green. ``light``-``heavy`` stay under
    that, ``oversat`` and ``unequal_oversat`` exceed it so queues fill the 150 m storage
    and spill back (blocked entry), which is the regime the spillback-risk term
    targets. Levels were fixed on validation seeds before any test seed was run.
    """
    out = {
        "light": Scenario(
            "light", "balanced, well under capacity",
            {a: 200.0 for a in APPROACHES},
        ),
        "medium": Scenario(
            "medium", "balanced, moderate",
            {a: 450.0 for a in APPROACHES},
        ),
        "heavy": Scenario(
            "heavy", "balanced, near capacity",
            {a: 700.0 for a in APPROACHES},
        ),
        "oversat": Scenario(
            "oversat", "balanced, beyond capacity: every approach exhausts its storage",
            {a: 950.0 for a in APPROACHES},
        ),
        "unequal": Scenario(
            "unequal", "major/minor street: N-S heavy, E-W light",
            {"North": 900.0, "East": 250.0, "South": 700.0, "West": 200.0},
        ),
        "unequal_oversat": Scenario(
            "unequal_oversat", "major street beyond its share of capacity, minor street light",
            {"North": 1300.0, "East": 250.0, "South": 900.0, "West": 200.0},
        ),
        "surge": Scenario(
            "surge", "medium base + periodic 2-minute platoons of +1500 veh/h on North",
            {a: 450.0 for a in APPROACHES},
            surges=_periodic_surges("North", 1500.0, every=600.0, length=120.0, duration=1800.0),
        ),
        "growing": Scenario(
            "growing", "North ramps from 400 to 1450 veh/h in 5-minute steps (queue growth into storage)",
            {"North": 400.0, "East": 400.0, "South": 400.0, "West": 400.0},
            surges=tuple(
                Surge("North", float(t), float(t + 300), 210.0 * (k + 1))
                for k, t in enumerate(range(300, 1800, 300))
            ),
        ),
    }
    shares = load_video_shares()
    if shares:
        total = 2600.0  # veh/h over the junction: ~75% of the all-max-green capacity
        out["calibrated"] = Scenario(
            "calibrated", "per-approach shares measured by the vision pipeline on the Bellevue clips",
            {a: round(total * shares[a], 1) for a in APPROACHES},
        )
    return out


def write_routes(scenario: Scenario, seed: int, path: Path) -> Path:
    """Write Poisson flows for ``scenario``; ``seed`` is applied by SUMO's --seed."""
    lines = [
        "<routes>",
        '  <vType id="car" vClass="passenger" length="5" minGap="2.5" maxSpeed="13.89" '
        'accel="2.6" decel="4.5" sigma="0.5"/>',
        '  <vType id="truck" vClass="truck" length="12" minGap="2.5" maxSpeed="13.89" '
        'accel="1.3" decel="4.0" sigma="0.5"/>',
    ]
    for approach, (_, out_edge) in _GEOMETRY.items():
        lines.append(f'  <route id="r_{approach}" edges="{in_edge(approach)} {out_edge}"/>')

    def flow(fid: str, approach: str, begin: float, end: float, vph: float) -> None:
        if vph <= 0.0 or end <= begin:
            return
        for vtype, share in (("car", 1.0 - HEAVY_SHARE), ("truck", HEAVY_SHARE)):
            rate = vph * share / 3600.0
            lines.append(
                f'  <flow id="{fid}_{vtype}" type="{vtype}" route="r_{approach}" '
                f'begin="{begin:.1f}" end="{end:.1f}" period="exp({rate:.6f})" '
                f'departLane="best" departSpeed="max" departPos="base"/>'
            )

    for approach in APPROACHES:
        flow(f"base_{approach}", approach, 0.0, scenario.duration, scenario.demand[approach])
    for k, s in enumerate(scenario.surges):
        flow(f"surge{k}_{s.approach}", s.approach, s.start, min(s.end, scenario.duration), s.extra)
    # SUMO requires flows sorted by begin time.
    header, body = lines[: 3 + len(_GEOMETRY)], lines[3 + len(_GEOMETRY):]
    body.sort(key=lambda line: float(line.split('begin="')[1].split('"')[0]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + body + ["</routes>"]) + "\n")
    return path
