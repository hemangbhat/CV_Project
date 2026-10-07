"""Unit tests for the Fixed_Time_Controller baseline.

The baseline's whole contract is that its phase plan is a function of the Cycle
number alone: North, East, South, West, wrapping back to North, each granted a
constant 30 simulated seconds, whatever the Scores say. These tests pin the order,
the wrap, the constant, and the score-independence; the generated-input version of
the score-independence claim is Property 15 in the module's property tests.

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import Config, load_config
from src.signal_controller import (
    APPROACH_ORDER,
    FIXED_GREEN_TIME,
    Controller,
    FixedTimeController,
    PhaseSequencer,
    SignalState,
    duration_to_frames,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: One frame per simulated second, so a 30 s green is 30 frames and a whole
#: four-Cycle run fits in a couple of hundred cheap ticks.
FRAME_RATE = 1.0
ZERO_SCORES = {name: 0.0 for name in APPROACH_ORDER}


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _selections(controller: FixedTimeController, count: int):
    return [controller.select(ZERO_SCORES) for _ in range(count)]


# ---------------------------------------------------------------------------
# Service order and the West -> North wrap (Requirements 7.1, 7.5)
# ---------------------------------------------------------------------------


def test_serves_the_four_approaches_in_order(config: Config) -> None:
    """North, East, South, West on the first four Cycles (Requirement 7.1)."""
    controller = FixedTimeController()

    approaches = [selection.approach for selection in _selections(controller, 4)]

    assert approaches == ["North", "East", "South", "West"]
    assert approaches == list(APPROACH_ORDER)


def test_west_is_followed_by_north(config: Config) -> None:
    """The Cycle after West serves North (Requirement 7.5)."""
    controller = FixedTimeController()
    _selections(controller, 4)  # ... through West

    assert controller.next_approach == "North"
    assert controller.select(ZERO_SCORES).approach == "North"


def test_order_repeats_across_three_full_rounds(config: Config) -> None:
    """The order is a repeating cycle, not a one-off sequence (Requirement 7.1)."""
    controller = FixedTimeController()

    approaches = [selection.approach for selection in _selections(controller, 12)]

    assert approaches == list(APPROACH_ORDER) * 3
    assert controller.cycles_selected == 12


def test_next_approach_reports_the_upcoming_selection(config: Config) -> None:
    controller = FixedTimeController()

    for expected in list(APPROACH_ORDER) * 2:
        assert controller.next_approach == expected
        assert controller.select(ZERO_SCORES).approach == expected


# ---------------------------------------------------------------------------
# The constant Green_Time (Requirements 7.2, 7.3)
# ---------------------------------------------------------------------------


def test_every_cycle_gets_thirty_seconds(config: Config) -> None:
    """Requirement 7.2: 30 simulated seconds, every Cycle."""
    controller = FixedTimeController()

    green_times = [selection.green_time for selection in _selections(controller, 9)]

    assert green_times == [30.0] * 9
    assert FIXED_GREEN_TIME == 30.0


def test_selections_never_flag_a_starvation_override(config: Config) -> None:
    """Round-robin cannot starve an Approach, so it never overrides."""
    controller = FixedTimeController()

    assert all(not s.starvation_override for s in _selections(controller, 8))


@pytest.mark.parametrize(
    "scores",
    [
        {"North": 1.0, "East": 0.0, "South": 0.0, "West": 0.0},
        {"North": 0.0, "East": 0.0, "South": 0.0, "West": 1.0},
        {name: 0.42 for name in APPROACH_ORDER},
        {},                                   # not even read, so not validated
        None,                                 # omitted entirely
    ],
)
def test_scores_do_not_affect_the_selection(scores, config: Config) -> None:
    """Requirement 7.3: the Score argument is ignored entirely."""
    controller = FixedTimeController()

    selections = [controller.select(scores) for _ in range(4)]

    assert [s.approach for s in selections] == list(APPROACH_ORDER)
    assert [s.green_time for s in selections] == [30.0] * 4


def test_differing_score_sequences_produce_identical_cycles(config: Config) -> None:
    """Two controllers fed opposite Scores agree Cycle for Cycle (Requirement 7.3)."""
    length = 10
    heavy_north = [{"North": 1.0, "East": 0.1, "South": 0.2, "West": 0.0}] * length
    heavy_west = [{"North": 0.0, "East": 0.9, "South": 0.8, "West": 1.0}] * length

    first = FixedTimeController()
    second = FixedTimeController()
    left = [first.select(s) for s in heavy_north]
    right = [second.select(s) for s in heavy_west]

    assert [s.approach for s in left] == [s.approach for s in right]
    assert [s.green_time for s in left] == [s.green_time for s in right]
    assert left == right


# ---------------------------------------------------------------------------
# Driven through the Phase_Sequencer (Requirements 7.4, 10.6)
# ---------------------------------------------------------------------------


def test_green_then_yellow_then_red_through_the_sequencer(config: Config) -> None:
    """Requirement 7.4: YELLOW for the Yellow_Duration once the Green_Time elapses."""
    sequencer = PhaseSequencer(FixedTimeController(), config, FRAME_RATE)
    green_frames = duration_to_frames(FIXED_GREEN_TIME, FRAME_RATE)
    yellow_frames = duration_to_frames(config.yellow_duration, FRAME_RATE)

    states = [
        sequencer.tick(frame, ZERO_SCORES).state
        for frame in range(green_frames + yellow_frames)
    ]

    assert states[:green_frames] == [SignalState.GREEN] * green_frames
    assert states[green_frames:] == [SignalState.YELLOW] * yellow_frames
    # The served Approach is RED again once the YELLOW expires.
    sequencer.tick(green_frames + yellow_frames, ZERO_SCORES)
    assert sequencer.signal_states()["North"] is SignalState.RED


def test_four_cycles_wrap_from_west_back_to_north(config: Config) -> None:
    """The full round trip, as the sequencer records it (Requirements 7.1, 7.5)."""
    sequencer = PhaseSequencer(FixedTimeController(), config, FRAME_RATE)
    cycle_frames = (
        duration_to_frames(FIXED_GREEN_TIME, FRAME_RATE) + sequencer.yellow_frames
    )

    for frame in range(cycle_frames * 4 + 1):
        sequencer.tick(frame, ZERO_SCORES)

    greens = [p for p in sequencer.phases if p.state is SignalState.GREEN]
    assert [p.approach for p in greens] == ["North", "East", "South", "West", "North"]
    assert {p.green_time for p in greens} == {30.0}
    assert sequencer.controller_name == "fixed"


def test_sequencer_run_is_score_independent(config: Config) -> None:
    """Identical phase plans from two different Score streams (Requirement 7.3)."""
    frames = 80

    def run(score: float) -> list[tuple[str, SignalState, float]]:
        sequencer = PhaseSequencer(FixedTimeController(), config, FRAME_RATE)
        scores = {name: score for name in APPROACH_ORDER}
        observed = []
        for frame in range(frames):
            info = sequencer.tick(frame, scores)
            assert info.approach is not None
            observed.append((info.approach, info.state, info.green_time))
        return observed

    assert run(0.0) == run(1.0)


def test_satisfies_the_controller_protocol(config: Config) -> None:
    """Interchangeable with the Adaptive_Controller behind one protocol (Req 10.6)."""
    assert isinstance(FixedTimeController(), Controller)


def test_green_time_stays_within_the_configured_bounds(config: Config) -> None:
    """The 30 s baseline sits inside the configured Green_Time range."""
    bounded = dataclasses.replace(config)

    assert bounded.min_green_time <= FIXED_GREEN_TIME <= bounded.max_green_time
