"""Unit tests for the cross-frame Metrics_Engine accumulators.

Waiting_Time and Vehicles_Served are the only measures that depend on frame
history and on the Signal_State vector, so these tests drive the engine over
frame sequences rather than single frames: waiting freezes while an Approach is
GREEN, resumes when it goes back to RED, never decreases, and a track leaving a
Queue_Region on GREEN is counted served exactly once.

Signal states are passed as plain strings here; a ``str``-valued enum member from
the Signal_Controller normalizes to the same value, which
``test_enum_like_signal_states_are_accepted`` covers.

Validates: Requirements 5.5, 5.6, 13.2
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, load_config
from src.lane_analysis import AssignedTrack
from src.traffic_metrics import MetricsEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

FRAME_RATE = 25.0
DT = 1.0 / FRAME_RATE


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


@pytest.fixture
def engine(config: Config) -> MetricsEngine:
    return MetricsEngine(config)


def _states(**overrides: str) -> dict[str, str]:
    """The four Signal_States, RED unless named otherwise."""
    states = {name: "RED" for name in APPROACH_NAMES}
    states.update(overrides)
    return states


def _queued(track_id: int, approach: str, *, queueing: bool = True) -> AssignedTrack:
    return AssignedTrack(
        track_id=track_id,
        vehicle_class="car",
        ref_point=(10, 10),
        approach=approach,
        is_queueing=queueing,
    )


# ---------------------------------------------------------------------------
# Waiting_Time accumulation (Requirements 5.5, 5.6)
# ---------------------------------------------------------------------------


def test_waiting_time_accrues_one_frame_per_frame_on_red(
    engine: MetricsEngine,
) -> None:
    """Validates: Requirements 5.5"""
    for _ in range(5):
        engine.update([_queued(1, "North")], _states(), DT)

    assert engine.waiting_times[1] == pytest.approx(5 * DT)


def test_a_track_that_is_not_queueing_never_waits(engine: MetricsEngine) -> None:
    """Only Queue_Region membership accrues Waiting_Time (Requirement 5.5)."""
    for _ in range(4):
        engine.update([_queued(1, "North", queueing=False)], _states(), DT)

    assert engine.waiting_times == {}


def test_waiting_time_freezes_during_green_and_resumes_on_red(
    engine: MetricsEngine,
) -> None:
    """Validates: Requirements 5.5, 5.6"""
    track = [_queued(1, "North")]

    for _ in range(3):
        engine.update(track, _states(), DT)
    after_red = engine.waiting_times[1]

    for _ in range(10):
        engine.update(track, _states(North="GREEN"), DT)
    after_green = engine.waiting_times[1]

    for _ in range(2):
        engine.update(track, _states(), DT)
    after_resume = engine.waiting_times[1]

    assert after_red == pytest.approx(3 * DT)
    assert after_green == after_red          # held unchanged through 10 GREEN frames
    assert after_resume == pytest.approx(5 * DT)


def test_yellow_accrues_waiting_time_like_red(engine: MetricsEngine) -> None:
    """Requirement 5.5 gates on *not* GREEN, so YELLOW still accrues."""
    for _ in range(3):
        engine.update([_queued(1, "East")], _states(East="YELLOW"), DT)

    assert engine.waiting_times[1] == pytest.approx(3 * DT)


def test_waiting_time_never_decreases_over_a_mixed_run(
    engine: MetricsEngine,
) -> None:
    """Validates: Requirements 5.5, 5.6"""
    track = [_queued(7, "South")]
    sequence = ["RED", "GREEN", "RED", "RED", "YELLOW", "GREEN", "GREEN", "RED"]

    history = []
    for state in sequence:
        engine.update(track, _states(South=state), DT)
        history.append(engine.waiting_times.get(7, 0.0))

    assert all(later >= earlier for earlier, later in zip(history, history[1:]))
    # Five non-GREEN frames in the sequence, and only those, accrued.
    assert history[-1] == pytest.approx(5 * DT)


def test_each_track_accumulates_independently(engine: MetricsEngine) -> None:
    """Validates: Requirements 5.5, 5.6"""
    tracks = [_queued(1, "North"), _queued(2, "West")]

    for _ in range(4):
        engine.update(tracks, _states(North="GREEN"), DT)

    assert 1 not in engine.waiting_times
    assert engine.waiting_times[2] == pytest.approx(4 * DT)


def test_a_repeated_track_id_waits_once_per_frame(engine: MetricsEngine) -> None:
    """Deduplication applies to Waiting_Time too (Requirements 5.1, 5.5)."""
    engine.update([_queued(1, "North"), _queued(1, "North")], _states(), DT)

    assert engine.waiting_times[1] == pytest.approx(DT)


def test_waiting_times_property_returns_a_copy(engine: MetricsEngine) -> None:
    """A caller cannot rewrite the run's history through the property."""
    engine.update([_queued(1, "North")], _states(), DT)

    engine.waiting_times[1] = 99.0
    engine.waiting_times.clear()

    assert engine.waiting_times[1] == pytest.approx(DT)


# ---------------------------------------------------------------------------
# Vehicles_Served (Requirement 13.2)
# ---------------------------------------------------------------------------


def test_a_track_leaving_the_queue_on_green_is_served(engine: MetricsEngine) -> None:
    """Validates: Requirements 13.2"""
    engine.update([_queued(1, "North")], _states(North="GREEN"), DT)
    assert engine.vehicles_served == 0

    # Frame n: still assigned to North, no longer inside the Queue_Region.
    engine.update([_queued(1, "North", queueing=False)], _states(North="GREEN"), DT)

    assert engine.vehicles_served == 1


def test_a_track_absent_on_green_is_served(engine: MetricsEngine) -> None:
    """Leaving the frame entirely counts as leaving the queue (Req 13.2)."""
    engine.update([_queued(1, "North")], _states(North="GREEN"), DT)
    engine.update([], _states(North="GREEN"), DT)

    assert engine.vehicles_served == 1


def test_a_track_leaving_the_queue_on_red_is_not_served(
    engine: MetricsEngine,
) -> None:
    """Validates: Requirements 13.2"""
    engine.update([_queued(1, "North")], _states(), DT)
    engine.update([], _states(), DT)

    assert engine.vehicles_served == 0


def test_a_served_track_is_counted_once(engine: MetricsEngine) -> None:
    """Validates: Requirements 13.2"""
    green = _states(North="GREEN")
    engine.update([_queued(1, "North")], green, DT)
    engine.update([], green, DT)
    engine.update([], green, DT)
    engine.update([], green, DT)

    assert engine.vehicles_served == 1


def test_every_departure_from_the_green_queue_is_counted(
    engine: MetricsEngine,
) -> None:
    """Validates: Requirements 13.2"""
    green = _states(North="GREEN")
    queue = [_queued(index, "North") for index in range(3)]

    engine.update(queue, green, DT)
    engine.update(queue[1:], green, DT)   # track 0 departs
    engine.update(queue[2:], green, DT)   # track 1 departs

    assert engine.vehicles_served == 2


def test_departures_from_a_non_green_approach_are_not_served(
    engine: MetricsEngine,
) -> None:
    """Only the GREEN Approach discharges (Requirement 13.2)."""
    states = _states(North="GREEN")
    engine.update([_queued(1, "North"), _queued(2, "West")], states, DT)
    engine.update([], states, DT)

    assert engine.vehicles_served == 1


# ---------------------------------------------------------------------------
# Signal_State handling
# ---------------------------------------------------------------------------


class _SignalState(str, Enum):
    """Stands in for the Signal_Controller enum, which lands in a later task."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


def test_enum_like_signal_states_are_accepted(engine: MetricsEngine) -> None:
    """Validates: Requirements 5.5, 5.6"""
    states = {name: _SignalState.RED for name in APPROACH_NAMES}
    states["North"] = _SignalState.GREEN
    tracks = [_queued(1, "North"), _queued(2, "East")]

    engine.update(tracks, states, DT)

    assert 1 not in engine.waiting_times
    assert engine.waiting_times[2] == pytest.approx(DT)


def test_an_unknown_signal_state_is_rejected(engine: MetricsEngine) -> None:
    """A misspelled state would silently accrue against the wrong phase."""
    with pytest.raises(ValueError, match="North signal state"):
        engine.update([], _states(North="green"), DT)
