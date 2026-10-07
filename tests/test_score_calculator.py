"""Unit tests for the Score_Calculator.

Three things are worth pinning with examples: the two endpoints where the Score
collapses onto a single measure, and one mixed case carried all the way from
tracks through the Metrics_Engine to the Score, so a change to either the divisors
or the combination shows up here.

Validates: Requirements 6.1, 6.3, 6.4, 6.8
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, load_config
from src.lane_analysis import AssignedTrack
from src.traffic_metrics import (
    ApproachMetrics,
    MetricsEngine,
    compute_config_scores,
    compute_score,
    compute_scores,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

ALL_RED = {name: "RED" for name in APPROACH_NAMES}
DT = 1.0 / 25.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _metrics(density: float, queue: float, *, approach: str = "North") -> ApproachMetrics:
    """Metrics carrying the two ratios directly, counts kept consistent with them.

    The Score depends on the ratios alone; the counts are present only because
    :class:`ApproachMetrics` validates ``queue_length <= vehicle_count``.
    """
    return ApproachMetrics(
        approach=approach,
        vehicle_count=1,
        vehicle_density=density,
        queue_length=0,
        normalized_queue=queue,
    )


def _assigned(track_id: int, approach: str, *, queueing: bool) -> AssignedTrack:
    return AssignedTrack(
        track_id=track_id,
        vehicle_class="car",
        ref_point=(10, 10),
        approach=approach,
        is_queueing=queueing,
    )


# ---------------------------------------------------------------------------
# Endpoints (Requirements 6.3, 6.4)
# ---------------------------------------------------------------------------


def test_alpha_one_reduces_the_score_to_vehicle_density() -> None:
    """Validates: Requirements 6.3"""
    metrics = _metrics(density=0.75, queue=0.125)

    assert compute_score(metrics, 1.0) == pytest.approx(metrics.vehicle_density)


def test_alpha_zero_reduces_the_score_to_normalized_queue() -> None:
    """Validates: Requirements 6.4"""
    metrics = _metrics(density=0.75, queue=0.125)

    assert compute_score(metrics, 0.0) == pytest.approx(metrics.normalized_queue)


# ---------------------------------------------------------------------------
# The mixed case (Requirement 6.1)
# ---------------------------------------------------------------------------


def test_mixed_alpha_weights_both_measures(config: Config) -> None:
    """The design's worked case, measured from tracks rather than hand-fed.

    Six distinct tracks on North with two of them queueing, against the default
    ``saturation_count`` 12 and ``queue_capacity`` 8, give
    ``density = 6/12 = 0.5`` and ``normalized_queue = 2/8 = 0.25``. At
    ``alpha = 0.6`` the Score is ``0.6 * 0.5 + 0.4 * 0.25 = 0.4``, which sits in
    the design's ``[0.3, 0.6)`` band and lies strictly between the two endpoint
    values, so neither coefficient can be dropped without failing this.

    Validates: Requirements 6.1
    """
    engine = MetricsEngine(config)
    tracks = [_assigned(index, "North", queueing=index < 2) for index in range(6)]

    metrics = engine.update(tracks, ALL_RED, DT)["North"]
    assert metrics.vehicle_density == pytest.approx(0.5)
    assert metrics.normalized_queue == pytest.approx(0.25)

    score = compute_score(metrics, 0.6)

    assert score == pytest.approx(0.4)
    assert compute_score(metrics, 0.0) < score < compute_score(metrics, 1.0)


# ---------------------------------------------------------------------------
# All four approaches, and Alpha as configuration (Requirement 6.8)
# ---------------------------------------------------------------------------


def test_compute_scores_covers_every_approach(config: Config) -> None:
    """Validates: Requirements 6.1"""
    metrics = {
        name: _metrics(density=0.5, queue=0.25, approach=name)
        for name in APPROACH_NAMES
    }

    scores = compute_scores(metrics, config.alpha)

    assert tuple(scores) == APPROACH_NAMES
    # alpha 0.5 in config/default.json: 0.5 * 0.5 + 0.5 * 0.25
    assert all(score == pytest.approx(0.375) for score in scores.values())


def test_compute_config_scores_reads_alpha_from_configuration(config: Config) -> None:
    """A retuned Alpha changes the Scores with no code change (Requirement 6.8)."""
    metrics = {
        name: _metrics(density=1.0, queue=0.0, approach=name) for name in APPROACH_NAMES
    }

    assert compute_config_scores(metrics, config)["North"] == pytest.approx(config.alpha)

    retuned = dataclasses.replace(config, alpha=0.25)
    assert compute_config_scores(metrics, retuned)["North"] == pytest.approx(0.25)


def test_a_missing_approach_is_rejected(config: Config) -> None:
    """A partial Score set would leave one Approach permanently unselectable."""
    metrics = {
        name: _metrics(density=0.5, queue=0.5, approach=name)
        for name in APPROACH_NAMES
        if name != "West"
    }

    with pytest.raises(ValueError, match="West"):
        compute_scores(metrics, config.alpha)


@pytest.mark.parametrize("alpha", [-0.1, 1.5, float("nan"), "0.5", None])
def test_an_out_of_range_alpha_is_rejected(alpha: object) -> None:
    """Validates: Requirements 6.8"""
    with pytest.raises(ValueError, match="alpha"):
        compute_score(_metrics(density=0.5, queue=0.5), alpha)  # type: ignore[arg-type]
