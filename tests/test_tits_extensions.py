"""Tests for the four control extensions derived from the two assigned papers.

Each extension answers a published limitation, is configuration-gated, and is off
by default. See ``report/paper_limitations_analysis.md`` for the mapping. The
tests are grouped per enhancement:

* **E1** PCE-weighted Queue_Length  — Wei et al. B-S1 (multimodal traffic ignored)
* **E2** Spillback pressure         — Wei et al.'s critique that accumulation is
  not queue and a constant jam density cannot represent a varying one
* **E3** Discharge-limited green    — Wei et al. Eq. 16, replacing Raza's bands
* **E4** Control-plan stability     — Wei et al. Eqs. 19-20, the ``Δb`` penalty

The first test in every group pins that the extension is inert by default, since
that is what keeps the 576 pre-existing tests and every previously reported
result valid.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from src.config import (
    APPROACH_NAMES,
    Config,
    from_json_obj,
    load_config,
    to_json_obj,
)
from src.errors import ConfigError
from src.lane_analysis import AssignedTrack
from src.signal_controller import AdaptiveController
from src.traffic_metrics import (
    ApproachMetrics,
    MetricsEngine,
    compute_config_demands,
    compute_config_scores,
    compute_score,
    compute_score_weighted,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

ALL_RED = {name: "RED" for name in APPROACH_NAMES}
DT = 1.0 / 25.0
SATURATION = 12.0
QUEUE_CAPACITY = 8.0


#: Writes a JSON object to a temp file and loads it through the public entry point,
#: matching the convention in ``tests/test_config_validation.py``: validation is
#: exercised through ``load_config`` rather than by calling private helpers, so the
#: tests pin the behaviour callers actually get.
Loader = Callable[[dict[str, Any]], Config]


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


@pytest.fixture
def default_obj() -> dict[str, Any]:
    """The default configuration as a mutable JSON object."""
    return to_json_obj(load_config(str(DEFAULT_CONFIG_PATH)))


@pytest.fixture
def load_obj(tmp_path: Path) -> Loader:
    def _load(obj: dict[str, Any]) -> Config:
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(obj), encoding="utf-8")
        return load_config(str(path))

    return _load


def _track(
    track_id: int,
    approach: str,
    *,
    queueing: bool = False,
    vehicle_class: str = "car",
    point: tuple[int, int] = (10, 10),
) -> AssignedTrack:
    return AssignedTrack(
        track_id=track_id,
        vehicle_class=vehicle_class,
        ref_point=point,
        approach=approach,
        is_queueing=queueing,
    )


def _metrics(
    *,
    density: float = 0.0,
    queue: float = 0.0,
    arrival: float = 0.0,
    spillback: float = 0.0,
    approach: str = "North",
) -> ApproachMetrics:
    return ApproachMetrics(
        approach=approach,
        vehicle_count=1,
        vehicle_density=density,
        queue_length=0,
        normalized_queue=queue,
        normalized_arrival=arrival,
        normalized_spillback=spillback,
    )


# ===========================================================================
# E1 — PCE-weighted Queue_Length
# ===========================================================================


def test_e1_off_by_default_leaves_queue_unweighted(config: Config) -> None:
    """A queueing bus counts as one vehicle unless E1 is enabled."""
    assert config.use_pce_queue is False
    engine = MetricsEngine(config)
    tracks = [_track(1, "North", queueing=True, vehicle_class="bus")]

    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    assert metrics.queue_length == 1
    assert metrics.normalized_queue == pytest.approx(1 / QUEUE_CAPACITY)
    assert metrics.queue_pce == pytest.approx(1.0)


def test_e1_weights_the_queue_by_vehicle_class(config: Config) -> None:
    """With E1 on, a bus contributes its PCE weight of 3.0 to the queue."""
    weighted = dataclasses.replace(config, use_pce_queue=True)
    engine = MetricsEngine(weighted)
    tracks = [_track(1, "North", queueing=True, vehicle_class="bus")]

    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    # Queue_Length stays the raw integer count; only the normalized measure and the
    # PCE magnitude change — exactly how density already behaves.
    assert metrics.queue_length == 1
    assert metrics.queue_pce == pytest.approx(3.0)
    assert metrics.normalized_queue == pytest.approx(3.0 / QUEUE_CAPACITY)


def test_e1_mixed_classes_sum_their_weights(config: Config) -> None:
    weighted = dataclasses.replace(config, use_pce_queue=True)
    engine = MetricsEngine(weighted)
    tracks = [
        _track(1, "East", queueing=True, vehicle_class="car"),          # 1.0
        _track(2, "East", queueing=True, vehicle_class="motorcycle"),   # 0.5
        _track(3, "East", queueing=True, vehicle_class="truck"),        # 3.0
    ]

    metrics = engine.update(tracks, ALL_RED, DT)["East"]

    assert metrics.queue_length == 3
    assert metrics.queue_pce == pytest.approx(4.5)
    assert metrics.normalized_queue == pytest.approx(4.5 / QUEUE_CAPACITY)


def test_e1_shares_the_queue_clamp(config: Config) -> None:
    """Weighting cannot take normalized_queue past 1.0 (shared clamp)."""
    weighted = dataclasses.replace(config, use_pce_queue=True)
    engine = MetricsEngine(weighted)
    # 4 buses = 12.0 PCE against a queue_capacity of 8.0.
    tracks = [
        _track(i, "South", queueing=True, vehicle_class="bus") for i in range(4)
    ]

    metrics = engine.update(tracks, ALL_RED, DT)["South"]

    assert metrics.queue_pce == pytest.approx(12.0)
    assert metrics.normalized_queue == 1.0


def test_e1_only_queueing_tracks_are_weighted(config: Config) -> None:
    """A non-queueing bus does not enter the queue term."""
    weighted = dataclasses.replace(config, use_pce_queue=True)
    engine = MetricsEngine(weighted)
    tracks = [_track(1, "West", queueing=False, vehicle_class="bus")]

    metrics = engine.update(tracks, ALL_RED, DT)["West"]

    assert metrics.queue_length == 0
    assert metrics.queue_pce == pytest.approx(0.0)
    assert metrics.normalized_queue == 0.0


# ===========================================================================
# E2 — Spillback pressure
# ===========================================================================


def test_e2_spillback_defaults_to_zero_on_hand_built_metrics() -> None:
    m = ApproachMetrics(
        approach="North",
        vehicle_count=3,
        vehicle_density=0.25,
        queue_length=1,
        normalized_queue=0.125,
    )
    assert m.normalized_spillback == 0.0
    assert m.queue_pce == 0.0


def test_e2_first_sighting_is_never_stopped(config: Config) -> None:
    """A track with no previous position cannot count as spillback."""
    engine = MetricsEngine(config)
    tracks = [_track(1, "North", point=(100, 100))]

    assert engine.update(tracks, ALL_RED, DT)["North"].normalized_spillback == 0.0


def test_e2_stationary_non_queueing_track_is_spillback(config: Config) -> None:
    """Stopped inside the ROI but outside the Queue_Region = backing up."""
    engine = MetricsEngine(config)
    tracks = [_track(1, "North", point=(100, 100))]

    engine.update(tracks, ALL_RED, DT)              # establishes the position
    metrics = engine.update(tracks, ALL_RED, DT)    # same point -> stopped

    assert metrics["North"].queue_length == 0
    assert metrics["North"].normalized_spillback == pytest.approx(1 / SATURATION)


def test_e2_moving_track_is_not_spillback(config: Config) -> None:
    engine = MetricsEngine(config)

    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    # Moved 10 px, well beyond the 2.0 px default threshold.
    metrics = engine.update([_track(1, "North", point=(100, 110))], ALL_RED, DT)

    assert metrics["North"].normalized_spillback == 0.0


def test_e2_stopped_displacement_threshold_is_configurable(config: Config) -> None:
    """Raising the threshold makes a slow crawl count as stopped."""
    tolerant = dataclasses.replace(config, stopped_displacement=20.0)
    engine = MetricsEngine(tolerant)

    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    metrics = engine.update([_track(1, "North", point=(100, 110))], ALL_RED, DT)

    assert metrics["North"].normalized_spillback == pytest.approx(1 / SATURATION)


def test_e2_queueing_tracks_are_excluded_from_spillback(config: Config) -> None:
    """Spillback is the queue *beyond* the Queue_Region, so queueing is excluded."""
    engine = MetricsEngine(config)
    tracks = [_track(1, "North", queueing=True, point=(100, 100))]

    engine.update(tracks, ALL_RED, DT)
    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    assert metrics.queue_length == 1
    assert metrics.normalized_spillback == 0.0


def test_e2_spillback_is_pce_weighted_with_e1(config: Config) -> None:
    weighted = dataclasses.replace(config, use_pce_queue=True)
    engine = MetricsEngine(weighted)
    tracks = [_track(1, "North", vehicle_class="bus", point=(100, 100))]

    engine.update(tracks, ALL_RED, DT)
    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    assert metrics.normalized_spillback == pytest.approx(3.0 / SATURATION)


def test_e2_spillback_shares_the_density_clamp(config: Config) -> None:
    engine = MetricsEngine(config)
    tracks = [_track(i, "North", point=(10 * i, 100)) for i in range(20)]

    engine.update(tracks, ALL_RED, DT)
    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    assert metrics.normalized_spillback == 1.0


def test_e2_score_blends_spillback() -> None:
    """delta shifts weight from the base score onto the spillback term."""
    m = _metrics(density=0.4, queue=0.4, spillback=1.0)
    # base at any alpha = 0.4; delta 0.5 -> 0.5*0.4 + 0.5*1.0 = 0.7.
    assert compute_score_weighted(m, 0.5, 0.0, 0.5) == pytest.approx(0.7)


def test_e2_score_at_zero_weights_reduces_to_base() -> None:
    m = _metrics(density=0.8, queue=0.2, arrival=0.5, spillback=0.9)
    for alpha in (0.0, 0.5, 1.0):
        assert compute_score_weighted(m, alpha, 0.0, 0.0) == pytest.approx(
            compute_score(m, alpha)
        )


def test_e2_score_stays_in_unit_range() -> None:
    for density in (0.0, 0.5, 1.0):
        for queue in (0.0, 1.0):
            for arrival in (0.0, 1.0):
                for spillback in (0.0, 1.0):
                    m = _metrics(
                        density=density,
                        queue=queue,
                        arrival=arrival,
                        spillback=spillback,
                    )
                    for alpha in (0.0, 0.5, 1.0):
                        for gamma, delta in ((0.0, 0.0), (0.3, 0.3), (0.0, 1.0), (0.5, 0.5)):
                            score = compute_score_weighted(m, alpha, gamma, delta)
                            assert 0.0 <= score <= 1.0


def test_e2_score_rejects_weights_summing_past_one() -> None:
    m = _metrics(density=0.5, queue=0.5)
    with pytest.raises(ValueError, match="sum to at most"):
        compute_score_weighted(m, 0.5, 0.6, 0.6)


def test_e2_score_is_nondecreasing_in_spillback() -> None:
    low = _metrics(density=0.3, queue=0.3, spillback=0.1)
    high = _metrics(density=0.3, queue=0.3, spillback=0.9)
    assert compute_score_weighted(high, 0.5, 0.0, 0.4) >= compute_score_weighted(
        low, 0.5, 0.0, 0.4
    )


def test_e2_config_scores_use_spillback_without_predictive(config: Config) -> None:
    """The spillback term is independent of the predictive flag."""
    spill = dataclasses.replace(config, spillback_weight=0.5)
    metrics = {
        name: _metrics(density=0.4, queue=0.4, spillback=1.0, approach=name)
        for name in APPROACH_NAMES
    }

    scores = compute_config_scores(metrics, spill)

    assert spill.use_predictive is False
    for name in APPROACH_NAMES:
        assert scores[name] == pytest.approx(0.7)


# ===========================================================================
# E3 — Discharge-limited Green_Time
# ===========================================================================


def test_e3_off_by_default_uses_the_score_bands(config: Config) -> None:
    assert config.use_discharge_green is False
    controller = AdaptiveController(config)
    scores = {name: 0.0 for name in APPROACH_NAMES}
    scores["North"] = 0.7          # would earn the 60 s band
    demands = {name: 0.0 for name in APPROACH_NAMES}  # would earn min green

    selection = controller.select(scores, demands)

    assert selection.approach == "North"
    assert selection.green_time == pytest.approx(60.0)


def test_e3_discharge_time_is_queue_over_saturation_flow(config: Config) -> None:
    """green = demand / saturation_flow_rate, inside the configured range."""
    discharge = dataclasses.replace(
        config,
        use_discharge_green=True,
        saturation_flow_rate=0.5,
        min_green_time=5.0,
        max_green_time=60.0,
    )
    controller = AdaptiveController(discharge)
    lost = discharge.yellow_duration  # Li et al. Eq. 7 T_loss = 3.0 s by default

    # 10 PCE at 0.5 PCE/s needs 20 s discharge + 3 s lost time = 23 s.
    assert controller.discharge_green_time(10.0) == pytest.approx(20.0 + lost)
    # 0.5 PCE needs 1 s + 3 s = 4 s, raised to the 5 s floor.
    assert controller.discharge_green_time(0.5) == pytest.approx(5.0)
    # 100 PCE would need 200 s + 3 s, capped at the 60 s ceiling.
    assert controller.discharge_green_time(100.0) == pytest.approx(60.0)


def test_e3_empty_queue_earns_minimum_green(config: Config) -> None:
    discharge = dataclasses.replace(config, use_discharge_green=True)
    controller = AdaptiveController(discharge)

    assert controller.discharge_green_time(0.0) == pytest.approx(config.min_green_time)


def test_e3_green_is_nondecreasing_in_demand(config: Config) -> None:
    discharge = dataclasses.replace(
        config, use_discharge_green=True, saturation_flow_rate=0.2, min_green_time=1.0
    )
    controller = AdaptiveController(discharge)

    times = [controller.discharge_green_time(d) for d in (0.0, 1.0, 5.0, 10.0, 50.0)]
    assert times == sorted(times)


def test_e3_green_stays_within_the_configured_range(config: Config) -> None:
    """Requirement 8.8 must hold on the discharge path too."""
    discharge = dataclasses.replace(
        config, use_discharge_green=True, saturation_flow_rate=0.01
    )
    controller = AdaptiveController(discharge)

    for demand in (0.0, 0.001, 1.0, 12.0, 1e6):
        green = controller.discharge_green_time(demand)
        assert config.min_green_time <= green <= config.max_green_time


def test_e3_select_uses_demand_not_score(config: Config) -> None:
    discharge = dataclasses.replace(
        config, use_discharge_green=True, saturation_flow_rate=1.0, min_green_time=10.0
    )
    controller = AdaptiveController(discharge)
    scores = {name: 0.0 for name in APPROACH_NAMES}
    scores["East"] = 0.9           # the band table would say 60 s
    demands = {name: 0.0 for name in APPROACH_NAMES}
    demands["East"] = 25.0         # 25 PCE at 1.0 PCE/s + 3 s lost time -> 28 s

    selection = controller.select(scores, demands)

    assert selection.approach == "East"
    assert selection.green_time == pytest.approx(25.0 + discharge.yellow_duration)


def test_e3_falls_back_to_bands_when_no_demands_supplied(config: Config) -> None:
    """A caller that passes no demand vector still gets a valid Green_Time."""
    discharge = dataclasses.replace(config, use_discharge_green=True)
    controller = AdaptiveController(discharge)
    scores = {name: 0.0 for name in APPROACH_NAMES}
    scores["South"] = 0.45

    selection = controller.select(scores)

    assert selection.approach == "South"
    assert selection.green_time == pytest.approx(45.0)


def test_e3_rejects_negative_demand(config: Config) -> None:
    controller = AdaptiveController(dataclasses.replace(config, use_discharge_green=True))
    with pytest.raises(ValueError, match="demand"):
        controller.discharge_green_time(-1.0)


def test_e3_demand_vector_is_the_pce_queue(config: Config) -> None:
    weighted = dataclasses.replace(config, use_pce_queue=True)
    metrics = {
        name: ApproachMetrics(
            approach=name,
            vehicle_count=2,
            vehicle_density=0.2,
            queue_length=2,
            normalized_queue=0.5,
            queue_pce=4.0,
        )
        for name in APPROACH_NAMES
    }

    demands = compute_config_demands(metrics, weighted)

    for name in APPROACH_NAMES:
        assert demands[name] == pytest.approx(4.0)


def test_e3_demand_includes_spillback_when_weighted(config: Config) -> None:
    """What is scored is what is timed: spillback joins the discharge demand."""
    spill = dataclasses.replace(config, use_pce_queue=True, spillback_weight=0.3)
    metrics = {
        name: ApproachMetrics(
            approach=name,
            vehicle_count=4,
            vehicle_density=0.4,
            queue_length=2,
            normalized_queue=0.5,
            queue_pce=4.0,
            normalized_spillback=0.25,   # 0.25 * saturation 12.0 = 3.0 PCE
        )
        for name in APPROACH_NAMES
    }

    demands = compute_config_demands(metrics, spill)

    for name in APPROACH_NAMES:
        assert demands[name] == pytest.approx(7.0)


# ===========================================================================
# E4 — Control-plan stability
# ===========================================================================


def test_e4_off_by_default_reselects_the_maximum(config: Config) -> None:
    assert config.switching_margin == 0.0
    assert config.green_rate_limit == 0.0
    controller = AdaptiveController(config)

    first = controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    second = controller.select({"North": 0.0, "East": 0.51, "South": 0.5, "West": 0.0})

    assert first.approach == "North"
    # No margin, so the tiny lead is enough to switch.
    assert second.approach == "East"


def test_e4_switching_margin_keeps_the_incumbent_on_a_near_tie(config: Config) -> None:
    stable = dataclasses.replace(config, switching_margin=0.1)
    controller = AdaptiveController(stable)

    controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    # East leads North by 0.05, under the 0.1 margin -> North holds.
    second = controller.select({"North": 0.50, "East": 0.55, "South": 0.0, "West": 0.0})

    assert second.approach == "North"


def test_e4_switching_margin_yields_to_a_clear_lead(config: Config) -> None:
    stable = dataclasses.replace(config, switching_margin=0.1)
    controller = AdaptiveController(stable)

    controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    # East leads North by 0.3, over the margin -> East takes green.
    second = controller.select({"North": 0.50, "East": 0.80, "South": 0.0, "West": 0.0})

    assert second.approach == "East"


def test_e4_margin_boundary_is_inclusive(config: Config) -> None:
    """A lead exactly equal to the margin is enough to switch."""
    stable = dataclasses.replace(config, switching_margin=0.1)
    controller = AdaptiveController(stable)

    controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    second = controller.select({"North": 0.5, "East": 0.6, "South": 0.0, "West": 0.0})

    assert second.approach == "East"


def test_e4_margin_does_not_defeat_starvation_prevention(config: Config) -> None:
    """The safety property: a starving Approach is still served (Reqs 9.3-9.5).

    The margin is applied only after the starvation check, so an incumbent cannot
    hold green past the Starvation_Limit however large the margin is.
    """
    stable = dataclasses.replace(config, switching_margin=1.0, starvation_limit=3)
    controller = AdaptiveController(stable)
    # North always wins on Score, and a margin of 1.0 would otherwise pin it there.
    scores = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}

    served = [controller.select(scores).approach for _ in range(8)]

    assert served[0] == "North"
    # Every Approach must appear within any starvation_limit + 1 window.
    for start in range(len(served) - 3):
        assert len(set(served[start : start + 4])) > 1
    assert set(served) == set(APPROACH_NAMES)


def test_e4_green_rate_limit_bounds_the_change_per_cycle(config: Config) -> None:
    limited = dataclasses.replace(
        config, green_rate_limit=5.0, min_green_time=30.0, max_green_time=60.0
    )
    controller = AdaptiveController(limited)

    # First cycle has no previous Green_Time: the band value applies unbounded.
    first = controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    assert first.green_time == pytest.approx(60.0)

    # The band would drop straight to 30 s; the limit allows only 5 s of movement.
    second = controller.select({"North": 0.0, "East": 0.0, "South": 0.0, "West": 0.0})
    assert second.green_time == pytest.approx(55.0)

    third = controller.select({"North": 0.0, "East": 0.0, "South": 0.0, "West": 0.0})
    assert third.green_time == pytest.approx(50.0)


def test_e4_rate_limited_green_stays_within_the_configured_range(config: Config) -> None:
    limited = dataclasses.replace(config, green_rate_limit=100.0)
    controller = AdaptiveController(limited)
    scores = {name: 0.0 for name in APPROACH_NAMES}

    for _ in range(10):
        green = controller.select(scores).green_time
        assert config.min_green_time <= green <= config.max_green_time


def test_e4_rate_limit_converges_to_the_target(config: Config) -> None:
    """Smoothing delays the target, it does not prevent reaching it."""
    limited = dataclasses.replace(config, green_rate_limit=5.0)
    controller = AdaptiveController(limited)

    controller.select({"North": 0.9, "East": 0.0, "South": 0.0, "West": 0.0})
    scores = {name: 0.0 for name in APPROACH_NAMES}
    greens = [controller.select(scores).green_time for _ in range(10)]

    assert greens[-1] == pytest.approx(config.min_green_time)


def test_e4_combines_with_discharge_green(config: Config) -> None:
    """The rate limit applies to the discharge path as well as the bands."""
    both = dataclasses.replace(
        config,
        use_discharge_green=True,
        saturation_flow_rate=1.0,
        green_rate_limit=5.0,
        min_green_time=10.0,
        max_green_time=60.0,
    )
    controller = AdaptiveController(both)
    scores = {name: 0.0 for name in APPROACH_NAMES}
    demands = {name: 0.0 for name in APPROACH_NAMES}

    # 50 PCE / 1.0 + 3 s lost time = 53 s (first cycle: no rate limit yet).
    demands["North"] = 50.0
    first = controller.select(scores, demands)
    assert first.green_time == pytest.approx(53.0)

    # 10 PCE / 1.0 + 3 s = 13 s target, but the 5 s/cycle limit allows only 48 s.
    demands["North"] = 10.0
    second = controller.select(scores, demands)
    assert second.green_time == pytest.approx(48.0)


# ===========================================================================
# Configuration plumbing
# ===========================================================================


def test_all_extension_fields_default_neutral(config: Config) -> None:
    assert config.use_pce_queue is False
    assert config.spillback_weight == 0.0
    assert config.stopped_displacement == 2.0
    assert config.use_discharge_green is False
    assert config.saturation_flow_rate == 0.5
    assert config.switching_margin == 0.0
    assert config.green_rate_limit == 0.0


def test_extension_config_round_trips(config: Config) -> None:
    tuned = dataclasses.replace(
        config,
        use_pce_queue=True,
        spillback_weight=0.25,
        stopped_displacement=3.5,
        use_discharge_green=True,
        saturation_flow_rate=0.8,
        switching_margin=0.05,
        green_rate_limit=10.0,
    )
    assert from_json_obj(to_json_obj(tuned)) == tuned


def test_legacy_config_without_extension_keys_loads(config: Config) -> None:
    """A file written before these extensions still loads, with them all off."""
    obj = to_json_obj(config)
    for key in (
        "use_pce_queue",
        "spillback_weight",
        "stopped_displacement",
        "use_discharge_green",
        "saturation_flow_rate",
        "switching_margin",
        "green_rate_limit",
    ):
        del obj[key]

    restored = from_json_obj(obj)

    assert restored == config


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("spillback_weight", 1.5, "spillback_weight"),
        ("spillback_weight", -0.1, "spillback_weight"),
        ("switching_margin", 2.0, "switching_margin"),
        ("stopped_displacement", 0.0, "stopped_displacement"),
        ("stopped_displacement", -1.0, "stopped_displacement"),
        ("saturation_flow_rate", 0.0, "saturation_flow_rate"),
        ("green_rate_limit", -1.0, "green_rate_limit"),
        ("use_pce_queue", "yes", "use_pce_queue"),
        ("use_discharge_green", 1, "use_discharge_green"),
    ],
)
def test_validation_rejects_bad_extension_values(
    load_obj: Loader, default_obj: dict[str, object], field: str, value: object, message: str
) -> None:
    default_obj[field] = value

    with pytest.raises(ConfigError, match=message):
        load_obj(default_obj)


def test_validation_rejects_weights_summing_past_one(
    load_obj: Loader, default_obj: dict[str, object]
) -> None:
    """A negative base-score coefficient would take the Score outside 0..1."""
    default_obj["use_predictive"] = True
    default_obj["predictive_weight"] = 0.7
    default_obj["spillback_weight"] = 0.7

    with pytest.raises(ConfigError, match="sum to at most"):
        load_obj(default_obj)


def test_validation_accepts_weights_summing_to_exactly_one(
    load_obj: Loader, default_obj: dict[str, object]
) -> None:
    default_obj["use_predictive"] = True
    default_obj["predictive_weight"] = 0.5
    default_obj["spillback_weight"] = 0.5

    loaded = load_obj(default_obj)   # must not raise

    assert loaded.predictive_weight == 0.5
    assert loaded.spillback_weight == 0.5


# ===========================================================================
# E5 — over-saturation measurement (Li et al. 2025 Eqs. 7-10)
# ===========================================================================

from src.signal_controller import AdaptiveController as _AC, PhaseSequencer, SignalState


def _run_sequencer(config, frame_rate, scores_by_frame, demands_by_frame, n_frames):
    """Drive a PhaseSequencer over n frames, returning it for inspection."""
    seq = PhaseSequencer(_AC(config), config, frame_rate)
    for f in range(n_frames):
        seq.tick(f, scores_by_frame(f), demands_by_frame(f))
    seq.finalize(n_frames - 1)
    return seq


def test_e5_records_cleared_queue_as_not_oversaturated(config: Config) -> None:
    """A green whose queue empties is marked cleared with zero shortfall."""
    fr = 30.0
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    # North has a queue at the start, which clears after 60 frames.
    def demands(f: int) -> dict[str, float]:
        q = 4.0 if f < 60 else 0.0
        return {"North": q, "East": 0.0, "South": 0.0, "West": 0.0}

    seq = _run_sequencer(config, fr, lambda f: hi, demands, 200)
    north_greens = [p for p in seq.phases if p.state is SignalState.GREEN and p.approach == "North"]
    assert north_greens
    first = north_greens[0]
    assert first.cleared is True
    assert first.oversaturation == pytest.approx(0.0)
    assert not first.oversaturated


def test_e5_records_oversaturation_when_queue_persists(config: Config) -> None:
    """A green that ends with queue left records the shortfall in seconds."""
    fr = 30.0
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    # North queue never clears within its green.
    demands = lambda f: {"North": 5.0, "East": 0.0, "South": 0.0, "West": 0.0}

    seq = _run_sequencer(config, fr, lambda f: hi, demands, 200)
    north_greens = [p for p in seq.phases if p.state is SignalState.GREEN and p.approach == "North"]
    assert north_greens
    first = north_greens[0]
    assert first.cleared is False
    assert first.oversaturated
    # shortfall = remaining PCE / saturation_flow_rate = 5.0 / 0.5 = 10 s.
    assert first.oversaturation == pytest.approx(5.0 / config.saturation_flow_rate)


def test_e5_queue_end_stays_unknown_without_demands(config: Config) -> None:
    """With no demand vector, over-saturation is unknown, not falsely zero."""
    fr = 30.0
    seq = PhaseSequencer(_AC(config), config, fr)
    for f in range(120):
        seq.tick(f, {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0})
    seq.finalize(119)
    greens = [p for p in seq.phases if p.state is SignalState.GREEN]
    assert greens
    assert all(p.queue_end == -1.0 for p in greens)


def test_e5_serialises_and_round_trips(config: Config) -> None:
    """The new phase fields survive as_dict()."""
    fr = 30.0
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    demands = lambda f: {"North": 5.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(config, fr, lambda f: hi, demands, 120)
    d = seq.phases[0].as_dict()
    for key in ("queue_start", "queue_end", "oversaturation", "cleared", "extended", "gapped_out"):
        assert key in d


# ===========================================================================
# E6 — queue-clearance gap-out and extension (Li et al. online)
# ===========================================================================


def test_e6_off_by_default_holds_the_assigned_green(config: Config) -> None:
    assert config.use_gap_out is False
    fr = 30.0
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    # Queue clears immediately, but with gap-out off the green must run its full band.
    demands = lambda f: {"North": 0.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(config, fr, lambda f: hi, demands, 300)
    first = next(p for p in seq.phases if p.state is SignalState.GREEN)
    # First North green gets the score-band 60 s (score 1.0) -> 1800 frames intended,
    # so within a 300-frame run it never even reaches yellow.
    assert first.gapped_out is False
    assert first.frames >= int(config.min_green_time * fr)


def test_e6_gaps_out_after_minimum_green_when_queue_clears(config: Config) -> None:
    fr = 30.0
    gap = dataclasses.replace(
        config, use_gap_out=True, min_green_time=2.0, max_green_time=60.0
    )
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    # North starts with a queue, clears at frame 20 (well before min green of 60 fr).
    def demands(f: int) -> dict[str, float]:
        return {"North": 3.0 if f < 20 else 0.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(gap, fr, lambda f: hi, demands, 400)
    first = next(p for p in seq.phases if p.state is SignalState.GREEN)
    assert first.gapped_out is True
    # Ended at or after the minimum green (2 s = 60 frames), not before.
    assert first.frames >= int(gap.min_green_time * fr)
    # And well short of the 60 s band it was assigned.
    assert first.frames < int(gap.max_green_time * fr)


def test_e6_respects_minimum_green_even_if_queue_clears_immediately(config: Config) -> None:
    fr = 30.0
    gap = dataclasses.replace(config, use_gap_out=True, min_green_time=3.0)
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    demands = lambda f: {"North": 0.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(gap, fr, lambda f: hi, demands, 400)
    first = next(p for p in seq.phases if p.state is SignalState.GREEN)
    assert first.frames >= int(gap.min_green_time * fr)


def test_e6_extends_up_to_maximum_when_queue_persists(config: Config) -> None:
    fr = 30.0
    # Discharge-green so the assigned green is short, then extension must lengthen it.
    gap = dataclasses.replace(
        config,
        use_gap_out=True,
        use_discharge_green=True,
        saturation_flow_rate=1.0,
        min_green_time=2.0,
        max_green_time=8.0,
    )
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    # Persistent queue: assigned discharge green is short, extension pushes to max.
    demands = lambda f: {"North": 2.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(gap, fr, lambda f: hi, demands, 600)
    first = next(p for p in seq.phases if p.state is SignalState.GREEN)
    assert first.extended is True
    # Never beyond the maximum green.
    assert first.frames <= int(gap.max_green_time * fr)


def test_e6_never_exceeds_max_green_frames(config: Config) -> None:
    fr = 30.0
    gap = dataclasses.replace(
        config, use_gap_out=True, min_green_time=1.0, max_green_time=5.0
    )
    hi = {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0}
    demands = lambda f: {"North": 9.0, "East": 0.0, "South": 0.0, "West": 0.0}
    seq = _run_sequencer(gap, fr, lambda f: hi, demands, 600)
    for p in seq.phases:
        if p.state is SignalState.GREEN and not p.truncated:
            assert p.frames <= int(gap.max_green_time * fr)


def test_e6_preserves_signal_integrity(config: Config) -> None:
    """Gap-out must not break the one-approach-non-RED invariant."""
    fr = 30.0
    gap = dataclasses.replace(config, use_gap_out=True, min_green_time=1.0)
    import random
    rng = random.Random(7)
    scores = lambda f: {n: rng.random() for n in APPROACH_NAMES}
    demands = lambda f: {n: rng.choice([0.0, 2.0]) for n in APPROACH_NAMES}
    seq = PhaseSequencer(_AC(gap), gap, fr)
    for f in range(600):
        seq.tick(f, scores(f), demands(f))
        states = seq.signal_states()
        non_red = [n for n, s in states.items() if s is not SignalState.RED]
        assert len(non_red) <= 1
    seq.finalize(599)


# ===========================================================================
# E7 — stop counting (Li et al.'s headline MOE)
# ===========================================================================


def test_e7_counts_one_stop_per_moving_to_stopped_transition(config: Config) -> None:
    engine = MetricsEngine(config)
    # Frame 0: appears (no previous point -> not stopped).
    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    assert engine.total_stops == 0
    # Frame 1: same point -> stopped -> one stop.
    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    assert engine.total_stops == 1
    # Frame 2: still stopped -> not counted again.
    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    assert engine.total_stops == 1


def test_e7_moving_vehicle_is_not_a_stop(config: Config) -> None:
    engine = MetricsEngine(config)
    for f in range(5):
        engine.update([_track(1, "North", point=(100, 100 + 10 * f))], ALL_RED, DT)
    assert engine.total_stops == 0


def test_e7_restop_after_moving_counts_again(config: Config) -> None:
    engine = MetricsEngine(config)
    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)
    engine.update([_track(1, "North", point=(100, 100))], ALL_RED, DT)  # stop 1
    engine.update([_track(1, "North", point=(100, 130))], ALL_RED, DT)  # moving
    engine.update([_track(1, "North", point=(100, 130))], ALL_RED, DT)  # stop 2
    assert engine.total_stops == 2


def test_e7_stops_are_per_approach(config: Config) -> None:
    engine = MetricsEngine(config)
    tracks = [_track(1, "North", point=(100, 100)), _track(2, "East", point=(950, 300))]
    engine.update(tracks, ALL_RED, DT)
    engine.update(tracks, ALL_RED, DT)
    stops = engine.stops
    assert stops["North"] == 1
    assert stops["East"] == 1
    assert stops["South"] == 0


def test_e7_average_stops_divides_by_distinct_vehicles(config: Config) -> None:
    engine = MetricsEngine(config)
    # Two vehicles, one stops once, the other never.
    engine.update(
        [_track(1, "North", point=(100, 100)), _track(2, "East", point=(950, 300))],
        ALL_RED, DT,
    )
    engine.update(
        [_track(1, "North", point=(100, 100)), _track(2, "East", point=(950, 340))],
        ALL_RED, DT,
    )
    assert engine.total_stops == 1
    assert engine.seen_vehicles == 2
    assert engine.average_stops == pytest.approx(0.5)


def test_e7_average_stops_zero_on_empty_run(config: Config) -> None:
    engine = MetricsEngine(config)
    for f in range(3):
        engine.update([], ALL_RED, DT)
    assert engine.average_stops == 0.0


def test_e7_config_default_off_leaves_gap_out_and_stops_inert(config: Config) -> None:
    """Stops are always measured; they are a metric, not a gated behaviour."""
    # Stops accumulate regardless of any flag, so there is nothing to switch off;
    # this test simply documents that measuring them does not change control.
    engine = MetricsEngine(config)
    engine.update([_track(1, "North", point=(1, 1))], ALL_RED, DT)
    engine.update([_track(1, "North", point=(1, 1))], ALL_RED, DT)
    assert engine.total_stops == 1


# ===========================================================================
# Run log round-trip for the new fields
# ===========================================================================


def test_run_log_round_trips_stops() -> None:
    from src.results_store import RunLog

    log = RunLog(
        run_id="r", video_path="v", controller_name="adaptive", alpha=0.5,
        config={}, video_info={"frame_rate": 30.0},
        stops={"North": 3, "East": 0, "South": 1, "West": 2}, seen_vehicles=10,
    )
    restored = RunLog.from_dict(log.as_dict())
    assert restored.stops == {"North": 3, "East": 0, "South": 1, "West": 2}
    assert restored.seen_vehicles == 10
    assert restored.total_stops == 6
    assert restored.average_stops == pytest.approx(0.6)


def test_gap_out_config_round_trips(config: Config) -> None:
    tuned = dataclasses.replace(config, use_gap_out=True)
    assert from_json_obj(to_json_obj(tuned)) == tuned


def test_legacy_config_without_gap_out_key_loads(config: Config) -> None:
    obj = to_json_obj(config)
    del obj["use_gap_out"]
    assert from_json_obj(obj).use_gap_out is False


# ===========================================================================
# E8 — short-term queue forecast (primary Wei-derived enhancement)
# ===========================================================================

from src.traffic_metrics import QueuePredictor, compute_config_demands


def _qmetrics(name: str, queue: float) -> ApproachMetrics:
    return ApproachMetrics(
        approach=name,
        vehicle_count=1,
        vehicle_density=0.1,
        queue_length=0,
        normalized_queue=queue,
    )


def _forecast_config(config: Config, **over) -> Config:
    base = dict(
        use_forecast=True,
        forecast_weight=0.4,
        forecast_horizon_seconds=3.0,
        forecast_window_frames=10,
    )
    base.update(over)
    return dataclasses.replace(config, **base)


def test_e8_rising_queue_forecasts_higher(config: Config) -> None:
    """A queue trending up projects above its current value."""
    fc = _forecast_config(config)
    predictor = QueuePredictor(fc, 30.0)
    out = None
    for i in range(10):
        metrics = {n: _qmetrics(n, 0.05 * i if n == "North" else 0.2) for n in APPROACH_NAMES}
        out = predictor.predict(metrics)
    assert out["North"].normalized_forecast > out["North"].normalized_queue


def test_e8_steady_queue_forecasts_itself(config: Config) -> None:
    fc = _forecast_config(config)
    predictor = QueuePredictor(fc, 30.0)
    out = None
    for _ in range(10):
        metrics = {n: _qmetrics(n, 0.3) for n in APPROACH_NAMES}
        out = predictor.predict(metrics)
    assert out["North"].normalized_forecast == pytest.approx(0.3, abs=1e-9)


def test_e8_falling_queue_forecasts_lower(config: Config) -> None:
    """A clearing queue is never inflated by the forecast."""
    fc = _forecast_config(config)
    predictor = QueuePredictor(fc, 30.0)
    out = None
    for i in range(10):
        metrics = {n: _qmetrics(n, max(0.0, 0.9 - 0.05 * i)) for n in APPROACH_NAMES}
        out = predictor.predict(metrics)
    assert out["North"].normalized_forecast <= out["North"].normalized_queue


def test_e8_forecast_clamps_to_unit_range(config: Config) -> None:
    """A steep rise projects past 1.0 but is clamped."""
    fc = _forecast_config(config, forecast_horizon_seconds=30.0)
    predictor = QueuePredictor(fc, 30.0)
    out = None
    for i in range(10):
        metrics = {n: _qmetrics(n, min(1.0, 0.1 * i)) for n in APPROACH_NAMES}
        out = predictor.predict(metrics)
    assert 0.0 <= out["North"].normalized_forecast <= 1.0


def test_e8_first_frame_forecasts_current(config: Config) -> None:
    """With one sample there is no slope, so the forecast equals the current queue."""
    fc = _forecast_config(config)
    predictor = QueuePredictor(fc, 30.0)
    out = predictor.predict({n: _qmetrics(n, 0.5) for n in APPROACH_NAMES})
    assert out["North"].normalized_forecast == pytest.approx(0.5)


def test_e8_window_bounds_the_trend(config: Config) -> None:
    """An old surge outside the window does not bias the current forecast."""
    fc = _forecast_config(config, forecast_window_frames=3)
    predictor = QueuePredictor(fc, 30.0)
    # A spike, then a long flat stretch longer than the window.
    seq = [0.0, 0.9] + [0.3] * 10
    out = None
    for q in seq:
        out = predictor.predict({n: _qmetrics(n, q) for n in APPROACH_NAMES})
    # The window now holds only flat 0.3 samples, so the forecast is ~0.3.
    assert out["North"].normalized_forecast == pytest.approx(0.3, abs=1e-9)


def test_e8_score_blend_uses_forecast(config: Config) -> None:
    """omega shifts weight onto the forecast term."""
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=0.4, queue_length=0,
        normalized_queue=0.4, normalized_forecast=1.0,
    )
    # base at alpha 0.5 = 0.5*0.4 + 0.5*0.4 = 0.4; omega 0.5 -> 0.5*0.4 + 0.5*1.0 = 0.7.
    assert compute_score_weighted(m, 0.5, 0.0, 0.0, 0.5) == pytest.approx(0.7)


def test_e8_score_at_zero_omega_reduces_to_base(config: Config) -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=0.8, queue_length=0,
        normalized_queue=0.2, normalized_forecast=0.9,
    )
    for alpha in (0.0, 0.5, 1.0):
        assert compute_score_weighted(m, alpha, 0.0, 0.0, 0.0) == pytest.approx(
            compute_score(m, alpha)
        )


def test_e8_score_stays_in_unit_range_with_all_terms() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=1.0, queue_length=0,
        normalized_queue=1.0, normalized_arrival=1.0, normalized_spillback=1.0,
        normalized_forecast=1.0,
    )
    for alpha in (0.0, 0.5, 1.0):
        s = compute_score_weighted(m, alpha, 0.3, 0.3, 0.4)
        assert 0.0 <= s <= 1.0


def test_e8_config_scores_blend_forecast_when_on(config: Config) -> None:
    fc = _forecast_config(config, forecast_weight=0.5)
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=1, vehicle_density=0.4, queue_length=0,
            normalized_queue=0.4, normalized_forecast=1.0,
        )
        for n in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, fc)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(0.7)


def test_e8_config_scores_ignore_forecast_when_off(config: Config) -> None:
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=1, vehicle_density=0.4, queue_length=0,
            normalized_queue=0.4, normalized_forecast=1.0,
        )
        for n in APPROACH_NAMES
    }
    assert config.use_forecast is False
    scores = compute_config_scores(metrics, config)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(compute_score(metrics[n], config.alpha))


def test_e8_demand_pre_sizes_for_forecast(config: Config) -> None:
    """With forecasting on, discharge demand takes the larger of now and forecast."""
    fc = _forecast_config(config)
    # queue_capacity for North in default.json is 8.0; forecast 0.5 -> 4.0 PCE.
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.2, queue_length=2,
            normalized_queue=0.25, queue_pce=2.0, normalized_forecast=0.5,
        )
        for n in APPROACH_NAMES
    }
    demands = compute_config_demands(metrics, fc)
    # max(current 2.0 PCE, forecast 0.5*8.0 = 4.0) = 4.0.
    assert demands["North"] == pytest.approx(4.0)


def test_e8_demand_leaves_clearing_queue_alone(config: Config) -> None:
    fc = _forecast_config(config)
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=4, vehicle_density=0.4, queue_length=4,
            normalized_queue=0.5, queue_pce=4.0, normalized_forecast=0.1,
        )
        for n in APPROACH_NAMES
    }
    demands = compute_config_demands(metrics, fc)
    # forecast 0.1*8.0 = 0.8 < current 4.0, so the current demand stands.
    assert demands["North"] == pytest.approx(4.0)


def test_e8_predictor_rejects_bad_frame_rate(config: Config) -> None:
    with pytest.raises(ValueError, match="frame_rate"):
        QueuePredictor(_forecast_config(config), 0.0)


def test_e8_config_round_trips(config: Config) -> None:
    tuned = _forecast_config(config, forecast_weight=0.3, forecast_window_frames=20)
    assert from_json_obj(to_json_obj(tuned)) == tuned


def test_e8_legacy_config_without_forecast_keys_loads(config: Config) -> None:
    obj = to_json_obj(config)
    for key in ("use_forecast", "forecast_weight", "forecast_horizon_seconds", "forecast_window_frames"):
        del obj[key]
    restored = from_json_obj(obj)
    assert restored.use_forecast is False
    assert restored.forecast_weight == 0.0
    assert restored.forecast_window_frames == 15


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("forecast_weight", 1.5, "forecast_weight"),
        ("forecast_weight", -0.1, "forecast_weight"),
        ("forecast_horizon_seconds", 0.0, "forecast_horizon_seconds"),
        ("forecast_horizon_seconds", -1.0, "forecast_horizon_seconds"),
        ("forecast_window_frames", 1, "forecast_window_frames"),
        ("forecast_window_frames", 2.5, "forecast_window_frames"),
        ("use_forecast", "yes", "use_forecast"),
    ],
)
def test_e8_validation_rejects_bad_values(
    load_obj: Loader, default_obj: dict[str, object], field: str, value: object, message: str
) -> None:
    default_obj[field] = value
    with pytest.raises(ConfigError, match=message):
        load_obj(default_obj)


def test_e8_validation_rejects_three_weights_summing_past_one(
    load_obj: Loader, default_obj: dict[str, object]
) -> None:
    default_obj["use_predictive"] = True
    default_obj["predictive_weight"] = 0.4
    default_obj["spillback_weight"] = 0.4
    default_obj["use_forecast"] = True
    default_obj["forecast_weight"] = 0.4
    with pytest.raises(ConfigError, match="sum to at most"):
        load_obj(default_obj)


# ===========================================================================
# E8 overlay display
# ===========================================================================

from src.overlay import approach_panel_row


def _panel_metrics(queue_norm: float, forecast: float) -> ApproachMetrics:
    return ApproachMetrics(
        approach="North",
        vehicle_count=3,
        vehicle_density=0.6,
        queue_length=2,
        normalized_queue=queue_norm,
        normalized_forecast=forecast,
    )


def test_overlay_omits_forecast_when_absent() -> None:
    """A base run's panel row is unchanged (no forecast field)."""
    row = approach_panel_row("North", _panel_metrics(0.5, 0.0), 0.55)
    assert "f=" not in row
    assert row == "North n=3   q=2   d=0.60 s=0.55"


def test_overlay_shows_forecast_when_present() -> None:
    row = approach_panel_row("North", _panel_metrics(0.5, 0.85), 0.62)
    assert "f=0.85" in row


def test_overlay_marks_anticipation_with_caret() -> None:
    """A forecast above the current queue is flagged as anticipating."""
    rising = approach_panel_row("North", _panel_metrics(0.5, 0.85), 0.62)
    clearing = approach_panel_row("North", _panel_metrics(0.5, 0.30), 0.45)
    assert rising.rstrip().endswith("^")
    assert not clearing.rstrip().endswith("^")


# ===========================================================================
# E9 — spatial queue reach (own computer-vision contribution)
#
# Queue_Length is a count inside a hand-drawn region divided by a hand-set
# capacity, so it saturates and discards vehicle position. queue_reach measures
# how far back along the visible approach the stopped traffic extends, normalised
# by the ROI's own geometry. These tests pin the geometry, the "count is blind /
# reach is not" property that motivates it, and the score plumbing.
# ===========================================================================

from src.lane_analysis import ApproachAxis, as_cv_polygon, build_approach_axes

BELLEVUE_CONFIG_PATH = PROJECT_ROOT / "config" / "bellevue_116th.json"


@pytest.fixture
def bellevue() -> Config:
    """A real calibrated camera configuration, for the geometry tests."""
    return load_config(str(BELLEVUE_CONFIG_PATH))


def _axis(roi, queue) -> ApproachAxis:
    return ApproachAxis(as_cv_polygon(roi), as_cv_polygon(queue))


# -- the geometry -----------------------------------------------------------


def test_e9_stop_line_is_fraction_zero(bellevue: Config) -> None:
    """The Queue_Region centroid is the axis origin, so it reads exactly 0."""
    for name, axis in build_approach_axes(bellevue).items():
        assert axis.fraction(axis.origin) == pytest.approx(0.0), name


def test_e9_far_roi_edge_is_fraction_one(bellevue: Config) -> None:
    """The furthest-upstream ROI vertex defines fraction 1 by construction."""
    axes = build_approach_axes(bellevue)
    for name, axis in axes.items():
        fractions = [
            axis.fraction((float(x), float(y)))
            for x, y in bellevue.approach(name).roi_polygon
        ]
        assert max(fractions) == pytest.approx(1.0), name


def test_e9_fraction_is_monotone_upstream() -> None:
    """Moving away from the stop line increases the fraction."""
    # A simple horizontal approach: stop line on the left, road extending right.
    roi = [(0, 0), (100, 0), (100, 20), (0, 20)]
    queue = [(0, 0), (20, 0), (20, 20), (0, 20)]
    axis = _axis(roi, queue)

    values = [axis.fraction((x, 10.0)) for x in (10, 30, 50, 70, 90)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_e9_fraction_clamps_to_unit_range() -> None:
    roi = [(0, 0), (100, 0), (100, 20), (0, 20)]
    queue = [(0, 0), (20, 0), (20, 20), (0, 20)]
    axis = _axis(roi, queue)

    # Far downstream of the stop line, and far beyond the ROI.
    assert axis.fraction((-500.0, 10.0)) == 0.0
    assert axis.fraction((5000.0, 10.0)) == 1.0
    for x in range(-50, 200, 7):
        assert 0.0 <= axis.fraction((float(x), 10.0)) <= 1.0


def test_e9_degenerate_geometry_is_inert_not_fatal() -> None:
    """Coincident centroids give no usable axis, so the measure reports 0."""
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    axis = _axis(square, square)   # ROI and queue region identical
    assert axis.length == 0.0
    assert axis.fraction((5.0, 5.0)) == 0.0


def test_e9_fraction_returns_plain_floats(bellevue: Config) -> None:
    """No NumPy scalars, so the Run_Log stays JSON-serialisable."""
    axis = build_approach_axes(bellevue)["North"]
    assert type(axis.length) is float
    assert type(axis.fraction((500.0, 160.0))) is float


# -- the property that motivates the measure --------------------------------


def _north_track(track_id: int, point: tuple[float, float], queueing: bool = False):
    return AssignedTrack(
        track_id=track_id,
        vehicle_class="car",
        ref_point=point,
        approach="North",
        is_queueing=queueing,
    )


def test_e9_reach_grows_where_the_count_is_blind(bellevue: Config) -> None:
    """The whole point: a queue extending back moves reach but not the count.

    One vehicle stopped at the stop line, then a second stopped far upstream but
    outside the Queue_Region. Queue_Length cannot see the second vehicle; the reach
    can, and that is the saturation blind spot this measure exists to fix.
    """
    engine = MetricsEngine(bellevue)
    axis = build_approach_axes(bellevue)["North"]
    near = axis.origin                       # fraction 0
    far = (1010.0, 150.0)                    # a far-upstream ROI vertex

    at_line = [_north_track(1, near, queueing=True)]
    engine.update(at_line, ALL_RED, DT)
    only_near = engine.update(at_line, ALL_RED, DT)["North"]

    engine2 = MetricsEngine(bellevue)
    extended = [_north_track(1, near, queueing=True), _north_track(2, far)]
    engine2.update(extended, ALL_RED, DT)
    with_far = engine2.update(extended, ALL_RED, DT)["North"]

    # The count is identical...
    assert only_near.queue_length == with_far.queue_length == 1
    # ...but the reach is not.
    assert only_near.queue_reach == pytest.approx(0.0)
    assert with_far.queue_reach > only_near.queue_reach
    assert with_far.queue_reach == pytest.approx(1.0)


def test_e9_reach_needs_a_stopped_vehicle(bellevue: Config) -> None:
    """A moving vehicle does not extend the queue's reach."""
    engine = MetricsEngine(bellevue)
    far = (1010.0, 150.0)
    engine.update([_north_track(1, far)], ALL_RED, DT)
    # Second frame at a clearly different position -> moving, not stopped.
    moved = engine.update([_north_track(1, (700.0, 200.0))], ALL_RED, DT)["North"]
    assert moved.queue_reach == 0.0


def test_e9_reach_defaults_to_zero_on_hand_built_metrics() -> None:
    m = ApproachMetrics(
        approach="North",
        vehicle_count=2,
        vehicle_density=0.4,
        queue_length=1,
        normalized_queue=0.25,
    )
    assert m.queue_reach == 0.0


def test_e9_reach_is_range_checked() -> None:
    with pytest.raises(ValueError, match="queue_reach"):
        ApproachMetrics(
            approach="North",
            vehicle_count=1,
            vehicle_density=0.2,
            queue_length=0,
            normalized_queue=0.0,
            queue_reach=1.4,
        )


# -- the score term ---------------------------------------------------------


def test_e9_score_blends_queue_reach() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=2, vehicle_density=0.4, queue_length=1,
        normalized_queue=0.4, queue_reach=1.0,
    )
    # base at any alpha = 0.4; psi 0.5 -> 0.5*0.4 + 0.5*1.0 = 0.7
    assert compute_score_weighted(m, 0.5, 0.0, 0.0, 0.0, 0.5) == pytest.approx(0.7)


def test_e9_score_at_zero_psi_reduces_to_base() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=2, vehicle_density=0.8, queue_length=1,
        normalized_queue=0.2, queue_reach=0.9,
    )
    for alpha in (0.0, 0.5, 1.0):
        assert compute_score_weighted(m, alpha, 0.0, 0.0, 0.0, 0.0) == pytest.approx(
            compute_score(m, alpha)
        )


def test_e9_score_stays_in_unit_range_with_every_term() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=1.0, queue_length=0,
        normalized_queue=1.0, normalized_arrival=1.0, normalized_spillback=1.0,
        normalized_forecast=1.0, queue_reach=1.0,
    )
    for alpha in (0.0, 0.5, 1.0):
        for weights in ((0.25, 0.25, 0.25, 0.25), (0.0, 0.0, 0.0, 1.0), (0.1, 0.2, 0.3, 0.4)):
            score = compute_score_weighted(m, alpha, *weights)
            assert 0.0 <= score <= 1.0


def test_e9_score_rejects_weights_summing_past_one() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=0.5, queue_length=0,
        normalized_queue=0.5,
    )
    with pytest.raises(ValueError, match="sum to at most"):
        compute_score_weighted(m, 0.5, 0.3, 0.3, 0.3, 0.3)


def test_e9_config_scores_use_reach_when_enabled(config: Config) -> None:
    reach_cfg = dataclasses.replace(
        config, use_queue_reach=True, queue_reach_weight=0.5
    )
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.4, queue_length=1,
            normalized_queue=0.4, queue_reach=1.0,
        )
        for n in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, reach_cfg)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(0.7)


def test_e9_config_scores_ignore_reach_when_disabled(config: Config) -> None:
    assert config.use_queue_reach is False
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.4, queue_length=1,
            normalized_queue=0.4, queue_reach=1.0,
        )
        for n in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, config)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(compute_score(metrics[n], config.alpha))


# -- config plumbing --------------------------------------------------------


def test_e9_config_defaults_neutral(config: Config) -> None:
    assert config.use_queue_reach is False
    assert config.queue_reach_weight == 0.0


def test_e9_config_round_trips(config: Config) -> None:
    tuned = dataclasses.replace(config, use_queue_reach=True, queue_reach_weight=0.35)
    assert from_json_obj(to_json_obj(tuned)) == tuned


def test_e9_legacy_config_without_reach_keys_loads(config: Config) -> None:
    obj = to_json_obj(config)
    del obj["use_queue_reach"]
    del obj["queue_reach_weight"]
    restored = from_json_obj(obj)
    assert restored.use_queue_reach is False
    assert restored.queue_reach_weight == 0.0


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("queue_reach_weight", 1.5, "queue_reach_weight"),
        ("queue_reach_weight", -0.2, "queue_reach_weight"),
        ("use_queue_reach", "yes", "use_queue_reach"),
    ],
)
def test_e9_validation_rejects_bad_values(
    load_obj: Loader, default_obj: dict[str, object], field: str, value: object, message: str
) -> None:
    default_obj[field] = value
    with pytest.raises(ConfigError, match=message):
        load_obj(default_obj)


def test_e9_validation_rejects_four_weights_summing_past_one(
    load_obj: Loader, default_obj: dict[str, object]
) -> None:
    default_obj["use_predictive"] = True
    default_obj["predictive_weight"] = 0.3
    default_obj["spillback_weight"] = 0.3
    default_obj["use_forecast"] = True
    default_obj["forecast_weight"] = 0.3
    default_obj["use_queue_reach"] = True
    default_obj["queue_reach_weight"] = 0.3
    with pytest.raises(ConfigError, match="sum to at most"):
        load_obj(default_obj)


# ===========================================================================
# Spillback risk (the S term of the proposed controller)
#
# risk = clamp(queue_reach + d(queue_reach)/dt * risk_horizon)
#
# Distinct from its two neighbours: normalized_spillback is overflow that has
# ALREADY happened, and normalized_forecast projects the count-based queue inside
# the Queue_Region (which saturates). This projects the non-saturating spatial
# occupancy, so it still carries a trend when the count has pinned at 1.0 — the
# regime the risk term exists to detect.
# ===========================================================================


def _risk_config(config: Config, **over) -> Config:
    base = dict(
        use_spillback_risk=True,
        spillback_risk_weight=0.3,
        risk_horizon_seconds=5.0,
        forecast_window_frames=15,
    )
    base.update(over)
    return dataclasses.replace(config, **base)


def _reach_metrics(reach: float, *, queue: float = 1.0) -> dict[str, ApproachMetrics]:
    """Metrics with the count-based queue deliberately SATURATED at 1.0."""
    return {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.3, queue_length=1,
            normalized_queue=queue, queue_reach=reach,
        )
        for n in APPROACH_NAMES
    }


def _drive_risk(config: Config, reach_sequence) -> ApproachMetrics:
    predictor = QueuePredictor(config, 30.0)
    out = None
    for reach in reach_sequence:
        out = predictor.predict(_reach_metrics(reach))
    return out["North"]


def test_risk_exceeds_occupancy_when_queue_extends_back(config: Config) -> None:
    """A queue extending backwards projects a higher storage occupancy."""
    m = _drive_risk(_risk_config(config), [0.02 * i for i in range(15)])
    assert m.spillback_risk > m.queue_reach


def test_risk_equals_occupancy_when_steady(config: Config) -> None:
    m = _drive_risk(_risk_config(config), [0.4] * 15)
    assert m.spillback_risk == pytest.approx(0.4, abs=1e-9)


def test_risk_does_not_inflate_a_clearing_queue(config: Config) -> None:
    m = _drive_risk(_risk_config(config), [max(0.0, 0.9 - 0.05 * i) for i in range(15)])
    assert m.spillback_risk <= m.queue_reach


def test_risk_saturates_at_one_when_projected_to_overflow(config: Config) -> None:
    """Nearly full and still growing -> risk 1.0, i.e. projected spillback."""
    m = _drive_risk(_risk_config(config), [0.6 + 0.02 * i for i in range(15)])
    assert m.spillback_risk == pytest.approx(1.0)


def test_risk_works_where_the_count_based_projection_is_blind(config: Config) -> None:
    """The design rationale, as an executable check.

    ``normalized_queue`` is held saturated at 1.0 throughout, so its own derivative is
    zero and a count-based projection would report a stable approach. The spatial reach
    still carries the trend, so the risk term still fires.
    """
    cfg = _risk_config(config)
    growing = _drive_risk(cfg, [0.02 * i for i in range(15)])
    clearing = _drive_risk(cfg, [max(0.0, 0.9 - 0.05 * i) for i in range(15)])

    # The count-based measure cannot separate these two cases at all...
    assert growing.normalized_queue == clearing.normalized_queue == 1.0
    # ...but the risk term does.
    assert growing.spillback_risk > clearing.spillback_risk


def test_risk_stays_in_unit_range_for_arbitrary_sequences(config: Config) -> None:
    cfg = _risk_config(config, risk_horizon_seconds=30.0)
    for sequence in (
        [0.0] * 15,
        [1.0] * 15,
        [0.1 * i for i in range(15)],
        [1.0 - 0.07 * i for i in range(15)],
    ):
        m = _drive_risk(cfg, [max(0.0, min(1.0, v)) for v in sequence])
        assert 0.0 <= m.spillback_risk <= 1.0


def test_risk_defaults_to_zero_without_the_predictor() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=0.2,
        queue_length=0, normalized_queue=0.0,
    )
    assert m.spillback_risk == 0.0


def test_risk_is_range_checked() -> None:
    with pytest.raises(ValueError, match="spillback_risk"):
        ApproachMetrics(
            approach="North", vehicle_count=1, vehicle_density=0.2,
            queue_length=0, normalized_queue=0.0, spillback_risk=1.3,
        )


# -- the score term ---------------------------------------------------------


def test_risk_blends_into_the_score() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=2, vehicle_density=0.4, queue_length=1,
        normalized_queue=0.4, spillback_risk=1.0,
    )
    # base = 0.4; rho 0.5 -> 0.5*0.4 + 0.5*1.0 = 0.7
    assert compute_score_weighted(m, 0.5, 0.0, 0.0, 0.0, 0.0, 0.5) == pytest.approx(0.7)


def test_risk_at_zero_rho_reduces_to_base() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=2, vehicle_density=0.8, queue_length=1,
        normalized_queue=0.2, spillback_risk=0.9,
    )
    for alpha in (0.0, 0.5, 1.0):
        assert compute_score_weighted(m, alpha, 0.0, 0.0, 0.0, 0.0, 0.0) == pytest.approx(
            compute_score(m, alpha)
        )


def test_score_stays_in_unit_range_with_all_five_terms() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=1.0, queue_length=0,
        normalized_queue=1.0, normalized_arrival=1.0, normalized_spillback=1.0,
        normalized_forecast=1.0, queue_reach=1.0, spillback_risk=1.0,
    )
    for alpha in (0.0, 0.5, 1.0):
        for w in ((0.2, 0.2, 0.2, 0.2, 0.2), (0.0, 0.0, 0.0, 0.0, 1.0), (0.1, 0.1, 0.3, 0.2, 0.3)):
            assert 0.0 <= compute_score_weighted(m, alpha, *w) <= 1.0


def test_score_rejects_five_weights_summing_past_one() -> None:
    m = ApproachMetrics(
        approach="North", vehicle_count=1, vehicle_density=0.5,
        queue_length=0, normalized_queue=0.5,
    )
    with pytest.raises(ValueError, match="sum to at most"):
        compute_score_weighted(m, 0.5, 0.25, 0.25, 0.25, 0.25, 0.25)


def test_config_scores_use_risk_when_enabled(config: Config) -> None:
    cfg = _risk_config(config, spillback_risk_weight=0.5)
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.4, queue_length=1,
            normalized_queue=0.4, spillback_risk=1.0,
        )
        for n in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, cfg)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(0.7)


def test_config_scores_ignore_risk_when_disabled(config: Config) -> None:
    assert config.use_spillback_risk is False
    metrics = {
        n: ApproachMetrics(
            approach=n, vehicle_count=2, vehicle_density=0.4, queue_length=1,
            normalized_queue=0.4, spillback_risk=1.0,
        )
        for n in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, config)
    for n in APPROACH_NAMES:
        assert scores[n] == pytest.approx(compute_score(metrics[n], config.alpha))


# -- config plumbing --------------------------------------------------------


def test_risk_config_defaults_neutral(config: Config) -> None:
    assert config.use_spillback_risk is False
    assert config.spillback_risk_weight == 0.0
    assert config.risk_horizon_seconds == 5.0


def test_risk_config_round_trips(config: Config) -> None:
    tuned = _risk_config(config, spillback_risk_weight=0.25, risk_horizon_seconds=7.5)
    assert from_json_obj(to_json_obj(tuned)) == tuned


def test_legacy_config_without_risk_keys_loads(config: Config) -> None:
    obj = to_json_obj(config)
    for key in ("use_spillback_risk", "spillback_risk_weight", "risk_horizon_seconds"):
        del obj[key]
    restored = from_json_obj(obj)
    assert restored.use_spillback_risk is False
    assert restored.spillback_risk_weight == 0.0
    assert restored.risk_horizon_seconds == 5.0


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("spillback_risk_weight", 1.5, "spillback_risk_weight"),
        ("spillback_risk_weight", -0.1, "spillback_risk_weight"),
        ("risk_horizon_seconds", 0.0, "risk_horizon_seconds"),
        ("risk_horizon_seconds", -2.0, "risk_horizon_seconds"),
        ("use_spillback_risk", "yes", "use_spillback_risk"),
    ],
)
def test_risk_validation_rejects_bad_values(
    load_obj: Loader, default_obj: dict[str, object], field: str, value: object, message: str
) -> None:
    default_obj[field] = value
    with pytest.raises(ConfigError, match=message):
        load_obj(default_obj)


def test_risk_validation_rejects_five_weights_summing_past_one(
    load_obj: Loader, default_obj: dict[str, object]
) -> None:
    default_obj["use_predictive"] = True
    default_obj["predictive_weight"] = 0.25
    default_obj["spillback_weight"] = 0.25
    default_obj["use_forecast"] = True
    default_obj["forecast_weight"] = 0.25
    default_obj["use_queue_reach"] = True
    default_obj["queue_reach_weight"] = 0.25
    default_obj["use_spillback_risk"] = True
    default_obj["spillback_risk_weight"] = 0.25
    with pytest.raises(ConfigError, match="sum to at most"):
        load_obj(default_obj)
