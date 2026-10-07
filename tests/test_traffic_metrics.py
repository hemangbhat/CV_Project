"""Unit tests for the Metrics_Engine per-frame measures.

Covers the counts, both density modes, and the clamp at and beyond saturation —
the three places where the arithmetic of Requirement 5 can go wrong without any
downstream stage noticing.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.7, 5.9
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, load_config
from src.lane_analysis import AssignedTrack
from src.traffic_metrics import ApproachMetrics, MetricsEngine, clamp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: Every default Approach uses these, so the expected ratios stay readable.
SATURATION = 12.0
QUEUE_CAPACITY = 8.0

ALL_RED = {name: "RED" for name in APPROACH_NAMES}
DT = 1.0 / 25.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _assigned(
    track_id: int,
    approach: str | None,
    *,
    queueing: bool = False,
    vehicle_class: str = "car",
) -> AssignedTrack:
    """An assigned track with the geometry already resolved.

    The reference point is irrelevant to the Metrics_Engine — assignment happened
    upstream — so it is fixed rather than varied per test.
    """
    return AssignedTrack(
        track_id=track_id,
        vehicle_class=vehicle_class,
        ref_point=(10, 10),
        approach=approach,
        is_queueing=queueing,
    )


def _tracks(approach: str, count: int, *, queueing: int = 0) -> list[AssignedTrack]:
    """``count`` distinct tracks on ``approach``, the first ``queueing`` queueing."""
    return [
        _assigned(index, approach, queueing=index < queueing) for index in range(count)
    ]


# ---------------------------------------------------------------------------
# Counts (Requirements 5.1, 5.3)
# ---------------------------------------------------------------------------


def test_vehicle_count_is_the_number_of_distinct_track_ids(config: Config) -> None:
    """Validates: Requirements 5.1"""
    engine = MetricsEngine(config)

    metrics = engine.update(_tracks("North", 3) + _tracks("East", 1), ALL_RED, DT)

    assert metrics["North"].vehicle_count == 3
    assert metrics["East"].vehicle_count == 1
    assert metrics["South"].vehicle_count == 0
    assert metrics["West"].vehicle_count == 0


def test_a_repeated_track_id_is_counted_once(config: Config) -> None:
    """Distinctness is set cardinality, not list length (Requirement 5.1)."""
    engine = MetricsEngine(config)

    metrics = engine.update(
        [_assigned(7, "North"), _assigned(7, "North"), _assigned(8, "North")],
        ALL_RED,
        DT,
    )

    assert metrics["North"].vehicle_count == 2


def test_unassigned_tracks_are_excluded_from_every_measurement(config: Config) -> None:
    """Validates: Requirements 4.5"""
    engine = MetricsEngine(config)

    metrics = engine.update(
        [_assigned(1, None), _assigned(2, None), _assigned(3, "South")], ALL_RED, DT
    )

    assert metrics["South"].vehicle_count == 1
    assert sum(m.vehicle_count for m in metrics.values()) == 1


def test_queue_length_counts_only_the_queueing_subset(config: Config) -> None:
    """Validates: Requirements 5.3, 5.8"""
    engine = MetricsEngine(config)

    metrics = engine.update(_tracks("West", 5, queueing=2), ALL_RED, DT)

    assert metrics["West"].vehicle_count == 5
    assert metrics["West"].queue_length == 2
    assert metrics["West"].queue_length <= metrics["West"].vehicle_count


# ---------------------------------------------------------------------------
# Ratios (Requirements 5.2, 5.4)
# ---------------------------------------------------------------------------


def test_density_and_normalized_queue_are_the_configured_ratios(
    config: Config,
) -> None:
    """Validates: Requirements 5.2, 5.4"""
    engine = MetricsEngine(config)

    metrics = engine.update(_tracks("North", 6, queueing=2), ALL_RED, DT)

    assert metrics["North"].vehicle_density == pytest.approx(6 / SATURATION)
    assert metrics["North"].normalized_queue == pytest.approx(2 / QUEUE_CAPACITY)


def test_an_empty_frame_measures_zero_everywhere(config: Config) -> None:
    """Validates: Requirements 5.7"""
    engine = MetricsEngine(config)

    metrics = engine.update([], ALL_RED, DT)

    assert set(metrics) == set(APPROACH_NAMES)
    for measured in metrics.values():
        assert measured.vehicle_count == 0
        assert measured.queue_length == 0
        assert measured.vehicle_density == 0.0
        assert measured.normalized_queue == 0.0


# ---------------------------------------------------------------------------
# Clamping at and beyond saturation (Requirement 5.7)
# ---------------------------------------------------------------------------


def test_density_is_exactly_one_at_saturation(config: Config) -> None:
    """Validates: Requirements 5.2, 5.7"""
    engine = MetricsEngine(config)

    metrics = engine.update(_tracks("East", int(SATURATION)), ALL_RED, DT)

    assert metrics["East"].vehicle_density == 1.0


def test_density_and_queue_clamp_beyond_their_capacities(config: Config) -> None:
    """Validates: Requirements 5.2, 5.4, 5.7"""
    engine = MetricsEngine(config)

    metrics = engine.update(
        _tracks("South", int(SATURATION) + 9, queueing=int(QUEUE_CAPACITY) + 5),
        ALL_RED,
        DT,
    )

    assert metrics["South"].vehicle_count == 21
    assert metrics["South"].queue_length == 13
    assert metrics["South"].vehicle_density == 1.0
    assert metrics["South"].normalized_queue == 1.0


def test_clamp_helper_confines_values_to_the_unit_range() -> None:
    """Validates: Requirements 5.7"""
    assert clamp(-0.5) == 0.0
    assert clamp(0.0) == 0.0
    assert clamp(0.42) == pytest.approx(0.42)
    assert clamp(1.0) == 1.0
    assert clamp(4.0) == 1.0


# ---------------------------------------------------------------------------
# PCE weighting (Requirement 5.9)
# ---------------------------------------------------------------------------


def _weighted(config: Config) -> Config:
    return dataclasses.replace(config, use_pce_weighting=True)


def test_pce_weighting_sums_per_class_weights_as_the_density_numerator(
    config: Config,
) -> None:
    """Validates: Requirements 5.9"""
    tracks = [
        _assigned(1, "North", vehicle_class="car"),          # 1.0
        _assigned(2, "North", vehicle_class="bus"),          # 3.0
        _assigned(3, "North", vehicle_class="motorcycle"),   # 0.5
    ]

    unweighted = MetricsEngine(config).update(tracks, ALL_RED, DT)["North"]
    weighted = MetricsEngine(_weighted(config)).update(tracks, ALL_RED, DT)["North"]

    assert unweighted.vehicle_density == pytest.approx(3 / SATURATION)
    assert weighted.vehicle_density == pytest.approx(4.5 / SATURATION)
    # Only the density numerator changes (Requirement 5.9).
    assert weighted.vehicle_count == unweighted.vehicle_count == 3
    assert weighted.queue_length == unweighted.queue_length
    assert weighted.normalized_queue == unweighted.normalized_queue


def test_pce_weighting_shares_the_same_clamp(config: Config) -> None:
    """Heavy vehicles cannot push density past 1 (Requirements 5.7, 5.9)."""
    engine = MetricsEngine(_weighted(config))

    # 5 buses at weight 3.0 sum to 15.0 against a saturation count of 12.
    tracks = [_assigned(i, "East", vehicle_class="bus") for i in range(5)]

    assert engine.update(tracks, ALL_RED, DT)["East"].vehicle_density == 1.0


# ---------------------------------------------------------------------------
# Reported types (Requirement 5.7)
# ---------------------------------------------------------------------------


def test_metrics_reject_a_queue_longer_than_the_count() -> None:
    """Validates: Requirements 5.8"""
    with pytest.raises(ValueError, match="exceeds vehicle_count"):
        ApproachMetrics(
            approach="North",
            vehicle_count=1,
            vehicle_density=0.1,
            queue_length=2,
            normalized_queue=0.2,
        )


def test_metrics_reject_a_ratio_outside_the_unit_range() -> None:
    """Validates: Requirements 5.7"""
    with pytest.raises(ValueError, match="vehicle_density"):
        ApproachMetrics(
            approach="North",
            vehicle_count=20,
            vehicle_density=1.7,
            queue_length=0,
            normalized_queue=0.0,
        )
