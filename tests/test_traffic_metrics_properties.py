"""Property-based tests for measurement and scoring (Properties 1 to 7).

The generated inputs are the two things the Metrics_Engine and the
Score_Calculator are functions of: an arbitrary set of assigned tracks, and an
arbitrary Approach configuration (saturation counts, queue capacities, and PCE
weights). Geometry is not generated here — the Approach_Assigner has already run
by the time these values exist — except in
``test_property_2_queueing_implies_assignment_through_the_assigner``, which drives
the real polygons so the structural claim behind Property 2 is not taken on trust.

Validates: Requirements 5.5, 5.6, 5.7, 5.8, 5.9, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import APPROACH_NAMES, VEHICLE_CLASSES, Config, load_config
from src.lane_analysis import ApproachAssigner, AssignedTrack
from src.tracking import Track, new_trajectory
from src.traffic_metrics import (
    GREEN,
    RED,
    SIGNAL_STATES,
    ApproachMetrics,
    MetricsEngine,
    compute_score,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = load_config(str(PROJECT_ROOT / "config" / "default.json"))

ALL_RED = {name: RED for name in APPROACH_NAMES}

_positive = st.floats(
    min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False
)
_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_alpha = st.one_of(st.just(0.0), st.just(1.0), _unit)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def _configs(draw: Any, *, use_pce_weighting: bool | None = None) -> Config:
    """Vary every configured value the Metrics_Engine divides or weights by.

    Geometry is left at the validated defaults because this module never reads it;
    what matters is that ``saturation_count`` and ``queue_capacity`` range over
    values far smaller and far larger than any generated count, so the clamp of
    Requirement 5.7 is exercised from both sides.
    """
    approaches = tuple(
        dataclasses.replace(
            DEFAULT_CONFIG.approach(name),
            saturation_count=draw(_positive),
            queue_capacity=draw(_positive),
        )
        for name in APPROACH_NAMES
    )
    weighting = (
        draw(st.booleans()) if use_pce_weighting is None else use_pce_weighting
    )
    return dataclasses.replace(
        DEFAULT_CONFIG,
        approaches=approaches,
        use_pce_weighting=weighting,
        pce_weights={cls: draw(_positive) for cls in VEHICLE_CLASSES},
    )


@st.composite
def _track_sets(draw: Any, *, max_size: int = 12) -> list[AssignedTrack]:
    """Build one frame's assigned tracks with distinct Track_IDs.

    ``approach=None`` is drawn alongside the four Approach names so the exclusion
    of unassigned tracks (Requirement 4.5) is part of the generated space. Ids are
    unique because a Track_ID identifies one vehicle in one frame, so an id on two
    Approaches at once is not a state the pipeline can produce.
    """
    track_ids = draw(
        st.lists(
            st.integers(min_value=0, max_value=999),
            min_size=0,
            max_size=max_size,
            unique=True,
        )
    )
    tracks: list[AssignedTrack] = []
    for track_id in track_ids:
        approach = draw(st.sampled_from((*APPROACH_NAMES, None)))
        tracks.append(
            AssignedTrack(
                track_id=track_id,
                vehicle_class=draw(st.sampled_from(VEHICLE_CLASSES)),
                ref_point=(0, 0),
                approach=approach,
                is_queueing=approach is not None and draw(st.booleans()),
            )
        )
    return tracks


_signal_state_vectors = st.fixed_dictionaries(
    {name: st.sampled_from(SIGNAL_STATES) for name in APPROACH_NAMES}
)


@st.composite
def _approach_metrics(draw: Any, approach: str = "North") -> ApproachMetrics:
    """An arbitrary :class:`ApproachMetrics` respecting its own invariants."""
    vehicle_count = draw(st.integers(min_value=0, max_value=50))
    return ApproachMetrics(
        approach=approach,
        vehicle_count=vehicle_count,
        vehicle_density=draw(_unit),
        queue_length=draw(st.integers(min_value=0, max_value=vehicle_count)),
        normalized_queue=draw(_unit),
    )


# ---------------------------------------------------------------------------
# Property 1: normalized measures stay in range
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(config=_configs(), tracks=_track_sets())
def test_property_1_normalized_measures_stay_in_range(
    config: Config, tracks: list[AssignedTrack]
) -> None:
    """Density and normalized queue lie in 0..1 for any tracks and any capacities.

    **Validates: Requirements 5.7**
    """
    metrics = MetricsEngine(config).update(tracks, ALL_RED, 1.0 / 25.0)

    assert set(metrics) == set(APPROACH_NAMES)
    for measured in metrics.values():
        assert 0.0 <= measured.vehicle_density <= 1.0
        assert 0.0 <= measured.normalized_queue <= 1.0


# ---------------------------------------------------------------------------
# Property 2: queue length never exceeds vehicle count
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(config=_configs(), tracks=_track_sets())
def test_property_2_queue_length_never_exceeds_vehicle_count(
    config: Config, tracks: list[AssignedTrack]
) -> None:
    """``0 <= queue_length <= vehicle_count`` on every Approach of every frame.

    **Validates: Requirements 5.8**
    """
    metrics = MetricsEngine(config).update(tracks, ALL_RED, 1.0 / 25.0)

    for measured in metrics.values():
        assert 0 <= measured.queue_length <= measured.vehicle_count


@settings(max_examples=100, deadline=None)
@given(
    points=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=1279),
            st.integers(min_value=1, max_value=719),
        ),
        min_size=0,
        max_size=10,
    ),
    vehicle_classes=st.lists(st.sampled_from(VEHICLE_CLASSES), min_size=0, max_size=10),
)
def test_property_2_queueing_implies_assignment_through_the_assigner(
    points: list[tuple[int, int]], vehicle_classes: list[str]
) -> None:
    """The same bound holds when the real polygons decide who is queueing.

    Without this the property could hold only because :class:`AssignedTrack`
    refuses to be queueing without an Approach; here the queue membership comes
    from ``cv2.pointPolygonTest`` against the configured Queue_Regions, which is
    the mechanism Property 2 actually claims.

    **Validates: Requirements 5.8**
    """
    tracks = [
        Track(
            track_id=index,
            x1=x - 1,
            y1=y - 1,
            x2=x + 1,
            y2=y,
            vehicle_class=vehicle_classes[index % len(vehicle_classes)]
            if vehicle_classes
            else "car",
            trajectory=new_trajectory(1),
        )
        for index, (x, y) in enumerate(points)
    ]

    assigned, _overlaps = ApproachAssigner(DEFAULT_CONFIG).assign(tracks)
    metrics = MetricsEngine(DEFAULT_CONFIG).update(assigned, ALL_RED, 1.0 / 25.0)

    for measured in metrics.values():
        assert 0 <= measured.queue_length <= measured.vehicle_count


# ---------------------------------------------------------------------------
# Property 3: PCE weighting changes only the density numerator
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(config=_configs(use_pce_weighting=False), tracks=_track_sets())
def test_property_3_pce_weighting_preserves_the_density_range(
    config: Config, tracks: list[AssignedTrack]
) -> None:
    """Arbitrary positive weights leave density in 0..1 and the counts untouched.

    **Validates: Requirements 5.9**
    """
    unweighted = MetricsEngine(config).update(tracks, ALL_RED, 1.0 / 25.0)
    weighted = MetricsEngine(
        dataclasses.replace(config, use_pce_weighting=True)
    ).update(tracks, ALL_RED, 1.0 / 25.0)

    for name in APPROACH_NAMES:
        assert 0.0 <= weighted[name].vehicle_density <= 1.0
        assert weighted[name].vehicle_count == unweighted[name].vehicle_count
        assert weighted[name].queue_length == unweighted[name].queue_length
        assert weighted[name].normalized_queue == unweighted[name].normalized_queue


# ---------------------------------------------------------------------------
# Property 4: waiting time accumulates only while queueing and not green
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    config=_configs(),
    frames=st.lists(
        st.tuples(_track_sets(max_size=6), _signal_state_vectors),
        min_size=1,
        max_size=12,
    ),
    dt=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property_4_waiting_time_accumulates_only_while_queueing_and_not_green(
    config: Config,
    frames: list[tuple[list[AssignedTrack], dict[str, str]]],
    dt: float,
) -> None:
    """Waiting_Time never decreases, and rises exactly when queueing and not GREEN.

    **Validates: Requirements 5.5, 5.6**
    """
    engine = MetricsEngine(config)
    previous = engine.waiting_times

    for tracks, states in frames:
        engine.update(tracks, states, dt)
        current = engine.waiting_times

        for track_id, waited in previous.items():
            assert current[track_id] >= waited          # Requirement 5.6

        should_wait = {
            track.track_id
            for track in tracks
            if track.is_queueing and states[str(track.approach)] != GREEN
        }
        for track_id in set(current) | set(previous):
            delta = current[track_id] - previous.get(track_id, 0.0)
            if track_id in should_wait:
                assert delta == pytest.approx(dt)       # Requirement 5.5
            else:
                assert delta == 0.0                     # Requirement 5.6

        previous = current


# ---------------------------------------------------------------------------
# Property 5: score stays in range
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(metrics=_approach_metrics(), alpha=_alpha)
def test_property_5_score_stays_in_range(
    metrics: ApproachMetrics, alpha: float
) -> None:
    """``0 <= score <= 1`` for any metrics and any Alpha in 0..1.

    **Validates: Requirements 6.2**
    """
    assert 0.0 <= compute_score(metrics, alpha) <= 1.0


# ---------------------------------------------------------------------------
# Property 6: score degenerates to its endpoints
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(metrics=_approach_metrics())
def test_property_6_score_degenerates_to_its_endpoints(
    metrics: ApproachMetrics,
) -> None:
    """Alpha 1 leaves the density, Alpha 0 leaves the normalized queue.

    **Validates: Requirements 6.3, 6.4**
    """
    assert compute_score(metrics, 1.0) == metrics.vehicle_density
    assert compute_score(metrics, 0.0) == metrics.normalized_queue


# ---------------------------------------------------------------------------
# Property 7: score is monotone in each measure
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(metrics=_approach_metrics(), queues=st.tuples(_unit, _unit), alpha=_unit)
def test_property_7_score_is_non_decreasing_in_normalized_queue(
    metrics: ApproachMetrics, queues: tuple[float, float], alpha: float
) -> None:
    """With density fixed, a longer normalized queue never lowers the Score.

    **Validates: Requirements 6.5**
    """
    low, high = sorted(queues)
    lower = dataclasses.replace(metrics, normalized_queue=low)
    higher = dataclasses.replace(metrics, normalized_queue=high)

    assert compute_score(higher, alpha) >= compute_score(lower, alpha)


@settings(max_examples=200, deadline=None)
@given(metrics=_approach_metrics(), densities=st.tuples(_unit, _unit), alpha=_unit)
def test_property_7_score_is_non_decreasing_in_vehicle_density(
    metrics: ApproachMetrics, densities: tuple[float, float], alpha: float
) -> None:
    """With the queue fixed, a higher density never lowers the Score.

    **Validates: Requirements 6.6**
    """
    low, high = sorted(densities)
    lower = dataclasses.replace(metrics, vehicle_density=low)
    higher = dataclasses.replace(metrics, vehicle_density=high)

    assert compute_score(higher, alpha) >= compute_score(lower, alpha)
