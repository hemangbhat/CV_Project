"""Unit tests for the optional predictive / arrival-aware extension.

This extension is additive and off by default: with ``use_predictive`` false the
System behaves exactly as the base specification requires. These tests pin the
new arrival metric, the anticipatory Score, the config-driven branch, and the
0..1 range the Green_Time bands depend on.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, from_json_obj, load_config, to_json_obj
from src.lane_analysis import AssignedTrack
from src.traffic_metrics import (
    ApproachMetrics,
    MetricsEngine,
    compute_config_scores,
    compute_score,
    compute_score_predictive,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

ALL_RED = {name: "RED" for name in APPROACH_NAMES}
DT = 1.0 / 25.0
SATURATION = 12.0
QUEUE_CAPACITY = 8.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _assigned(track_id: int, approach: str, *, queueing: bool = False) -> AssignedTrack:
    return AssignedTrack(
        track_id=track_id,
        vehicle_class="car",
        ref_point=(10, 10),
        approach=approach,
        is_queueing=queueing,
    )


def _metrics(
    density: float, queue: float, arrival: float, *, approach: str = "North"
) -> ApproachMetrics:
    return ApproachMetrics(
        approach=approach,
        vehicle_count=1,
        vehicle_density=density,
        queue_length=0,
        normalized_queue=queue,
        normalized_arrival=arrival,
    )


# ---------------------------------------------------------------------------
# The arrival metric
# ---------------------------------------------------------------------------


def test_normalized_arrival_defaults_to_zero() -> None:
    """Base constructions omit the field; it must default to 0.0."""
    m = ApproachMetrics(
        approach="North",
        vehicle_count=3,
        vehicle_density=0.25,
        queue_length=1,
        normalized_queue=0.125,
    )
    assert m.normalized_arrival == 0.0


def test_arrival_is_the_non_queueing_share(config: Config) -> None:
    """Approaching = vehicles present but not queueing, scaled by saturation."""
    engine = MetricsEngine(config)
    # 6 tracks on North, 2 of them queueing -> 4 approaching -> 4/12.
    tracks = [_assigned(i, "North", queueing=i < 2) for i in range(6)]

    metrics = engine.update(tracks, ALL_RED, DT)["North"]

    assert metrics.vehicle_count == 6
    assert metrics.queue_length == 2
    assert metrics.normalized_arrival == pytest.approx(4 / SATURATION)
    # The base measures are unchanged by the extension.
    assert metrics.vehicle_density == pytest.approx(6 / SATURATION)
    assert metrics.normalized_queue == pytest.approx(2 / QUEUE_CAPACITY)


def test_arrival_is_zero_when_all_present_vehicles_queue(config: Config) -> None:
    engine = MetricsEngine(config)
    tracks = [_assigned(i, "East", queueing=True) for i in range(3)]

    assert engine.update(tracks, ALL_RED, DT)["East"].normalized_arrival == 0.0


def test_arrival_shares_the_density_clamp(config: Config) -> None:
    """Approaching beyond saturation still clamps to 1 (shared clamp)."""
    engine = MetricsEngine(config)
    tracks = [_assigned(i, "South") for i in range(int(SATURATION) + 5)]

    assert engine.update(tracks, ALL_RED, DT)["South"].normalized_arrival == 1.0


# ---------------------------------------------------------------------------
# The anticipatory Score
# ---------------------------------------------------------------------------


def test_predictive_score_at_gamma_zero_reduces_to_base() -> None:
    """gamma = 0 must equal the base queue-aware Score exactly."""
    m = _metrics(density=0.8, queue=0.2, arrival=0.5)
    for alpha in (0.0, 0.5, 1.0):
        assert compute_score_predictive(m, alpha, 0.0) == pytest.approx(
            compute_score(m, alpha)
        )


def test_predictive_score_at_gamma_one_is_pure_arrival() -> None:
    m = _metrics(density=0.8, queue=0.2, arrival=0.5)
    assert compute_score_predictive(m, 0.5, 1.0) == pytest.approx(0.5)


def test_predictive_score_blends_base_and_arrival() -> None:
    """A worked mixed case, so neither term can be dropped."""
    m = _metrics(density=0.6, queue=0.1, arrival=0.9)
    # base at alpha 1.0 = density = 0.6; gamma 0.5 -> 0.5*0.6 + 0.5*0.9 = 0.75.
    assert compute_score_predictive(m, 1.0, 0.5) == pytest.approx(0.75)


def test_predictive_score_stays_in_unit_range() -> None:
    for d in (0.0, 0.5, 1.0):
        for q in (0.0, 1.0):
            for a in (0.0, 1.0):
                for alpha in (0.0, 0.5, 1.0):
                    for gamma in (0.0, 0.4, 1.0):
                        s = compute_score_predictive(_metrics(d, q, a), alpha, gamma)
                        assert 0.0 <= s <= 1.0


def test_predictive_score_rejects_out_of_range_gamma() -> None:
    m = _metrics(density=0.5, queue=0.5, arrival=0.5)
    with pytest.raises(ValueError, match="predictive weight"):
        compute_score_predictive(m, 0.5, 1.5)


# ---------------------------------------------------------------------------
# The config-driven branch
# ---------------------------------------------------------------------------


def test_compute_config_scores_uses_base_when_predictive_off(config: Config) -> None:
    metrics = {
        name: _metrics(0.5, 0.25, 0.4, approach=name) for name in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, config)
    for name in APPROACH_NAMES:
        assert scores[name] == pytest.approx(compute_score(metrics[name], config.alpha))


def test_compute_config_scores_blends_when_predictive_on(config: Config) -> None:
    predictive = dataclasses.replace(
        config, use_predictive=True, predictive_weight=0.5
    )
    metrics = {
        name: _metrics(0.6, 0.1, 0.9, approach=name) for name in APPROACH_NAMES
    }
    scores = compute_config_scores(metrics, predictive)
    for name in APPROACH_NAMES:
        assert scores[name] == pytest.approx(
            compute_score_predictive(metrics[name], predictive.alpha, 0.5)
        )


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_predictive_fields_default_off(config: Config) -> None:
    assert config.use_predictive is False
    assert config.predictive_weight == 0.0


def test_predictive_config_round_trips() -> None:
    base = load_config(str(DEFAULT_CONFIG_PATH))
    predictive = dataclasses.replace(base, use_predictive=True, predictive_weight=0.4)
    assert from_json_obj(to_json_obj(predictive)) == predictive


def test_legacy_config_without_predictive_keys_loads() -> None:
    """A config file predating the extension still loads, with the flag off."""
    obj = to_json_obj(load_config(str(DEFAULT_CONFIG_PATH)))
    del obj["use_predictive"]
    del obj["predictive_weight"]
    restored = from_json_obj(obj)
    assert restored.use_predictive is False
    assert restored.predictive_weight == 0.0
