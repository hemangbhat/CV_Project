"""Unit tests for Adaptive_Controller selection and the Green_Time bands.

Two things are pinned here, both by example: the Approach the controller picks from
a set of Scores, including every tiebreak path, and the Green_Time it reads out of
``config.green_time_bands`` at the exact band boundaries. Starvation prevention has
its own tests; this file only asserts that nothing is overridden while no counter
has reached the limit.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.9, 8.10
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import Config, GreenTimeBand, load_config
from src.signal_controller import (
    APPROACH_ORDER,
    AdaptiveController,
    Controller,
    PhaseSequencer,
    SignalState,
    duration_to_frames,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: One frame per simulated second keeps a whole Cycle a few dozen cheap ticks.
FRAME_RATE = 1.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _scores(north: float, east: float, south: float, west: float) -> dict[str, float]:
    return {"North": north, "East": east, "South": south, "West": west}


def _flat(value: float) -> dict[str, float]:
    return {name: value for name in APPROACH_ORDER}


# ---------------------------------------------------------------------------
# Selection by Score (Requirements 8.1, 8.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("winner", APPROACH_ORDER)
def test_serves_the_single_highest_scoring_approach(winner: str, config: Config) -> None:
    """Requirement 8.2: the Approach whose Score dominates the other three."""
    controller = AdaptiveController(config)
    scores = {name: (0.9 if name == winner else 0.1) for name in APPROACH_ORDER}

    selection = controller.select(scores)

    assert selection.approach == winner
    assert selection.selection_score == pytest.approx(0.9)
    assert selection.starvation_override is False


def test_selection_follows_the_scores_from_cycle_to_cycle(config: Config) -> None:
    """Requirement 8.1: each Cycle reads the Scores current at that Cycle."""
    controller = AdaptiveController(config)

    first = controller.select(_scores(0.8, 0.1, 0.0, 0.0))
    second = controller.select(_scores(0.0, 0.0, 0.7, 0.1))

    assert (first.approach, second.approach) == ("North", "South")


def test_rejects_a_score_map_missing_an_approach(config: Config) -> None:
    """A partial Score map is a programming error, not a silent zero (Req 8.1)."""
    controller = AdaptiveController(config)

    with pytest.raises(ValueError, match="missing approach"):
        controller.select({"North": 0.5, "East": 0.4, "South": 0.3})


# ---------------------------------------------------------------------------
# Every tiebreak path (Requirement 8.3)
# ---------------------------------------------------------------------------


def test_tie_on_score_breaks_to_the_longest_waiting_approach(config: Config) -> None:
    """Requirement 8.3: the greatest Cycles_Waited wins a Score tie."""
    controller = AdaptiveController(config)
    # Serve North first, so East, South and West have each waited one Cycle and
    # North has waited none.
    controller.select(_scores(0.9, 0.1, 0.1, 0.1))
    assert controller.cycles_waited == {"North": 0, "East": 1, "South": 1, "West": 1}

    # North ties with West on Score; West has waited a Cycle, North has not.
    selection = controller.select(_scores(0.5, 0.1, 0.1, 0.5))

    assert selection.approach == "West"


def test_remaining_tie_breaks_on_the_fixed_approach_order(config: Config) -> None:
    """Requirement 8.3: equal Score and equal wait resolve to APPROACH_ORDER."""
    controller = AdaptiveController(config)

    # All four counters are 0 on the first Cycle, so a four-way Score tie is decided
    # by the fixed order alone.
    assert controller.select(_flat(0.5)).approach == "North"


@pytest.mark.parametrize(
    ("tied", "expected"),
    [
        (("East", "South", "West"), "East"),
        (("South", "West"), "South"),
        (("North", "West"), "North"),
        (("West",), "West"),
    ],
)
def test_order_tiebreak_picks_the_earliest_tied_approach(
    tied: tuple[str, ...], expected: str, config: Config
) -> None:
    """The earliest Approach in North, East, South, West wins (Requirement 8.3)."""
    controller = AdaptiveController(config)
    scores = {name: (0.7 if name in tied else 0.2) for name in APPROACH_ORDER}

    assert controller.select(scores).approach == expected


def test_wait_tiebreak_outranks_the_order_tiebreak(config: Config) -> None:
    """Wait is consulted before order, not after (Requirement 8.3)."""
    controller = AdaptiveController(config)
    controller.select(_scores(0.9, 0.0, 0.0, 0.0))   # North served, others wait 1
    controller.select(_scores(0.0, 0.9, 0.0, 0.0))   # East served: North 1, South 2, West 2

    assert controller.cycles_waited == {"North": 1, "East": 0, "South": 2, "West": 2}
    # North, South and West tie on Score. South and West have waited longer than
    # North, so order alone would have said North.
    assert controller.select(_scores(0.4, 0.0, 0.4, 0.4)).approach == "South"


def test_a_lower_score_never_wins_on_a_longer_wait(config: Config) -> None:
    """Wait breaks ties only; it does not outrank Score (Requirements 8.2, 8.3)."""
    controller = AdaptiveController(config)
    controller.select(_scores(0.9, 0.0, 0.0, 0.0))
    controller.select(_scores(0.9, 0.0, 0.0, 0.0))

    # West has waited two Cycles, North none, but North's Score is strictly greater.
    assert controller.cycles_waited["West"] == 2
    assert controller.select(_scores(0.6, 0.0, 0.0, 0.5)).approach == "North"


def test_counters_reset_on_selection_and_increment_elsewhere(config: Config) -> None:
    """Requirement 9.2, needed here because the tiebreak reads the counters."""
    controller = AdaptiveController(config)

    controller.select(_scores(0.9, 0.0, 0.0, 0.0))
    assert controller.cycles_waited == {"North": 0, "East": 1, "South": 1, "West": 1}
    controller.select(_scores(0.0, 0.0, 0.9, 0.0))
    assert controller.cycles_waited == {"North": 1, "East": 2, "South": 0, "West": 2}
    assert controller.cycles_selected == 2


# ---------------------------------------------------------------------------
# Green_Time bands, including the exact boundaries (Requirements 8.4-8.6, 8.9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, 30.0),      # Requirement 8.4: below 0.3
        (0.29, 30.0),
        (0.2999999, 30.0),
        (0.3, 45.0),      # Requirement 8.5: the 0.3 boundary is inclusive
        (0.45, 45.0),
        (0.5999999, 45.0),
        (0.6, 60.0),      # Requirement 8.6: the 0.6 boundary is inclusive
        (0.99, 60.0),
        (1.0, 60.0),
    ],
)
def test_green_time_band_lookup(score: float, expected: float, config: Config) -> None:
    """The band with the greatest min_score not exceeding the Score."""
    controller = AdaptiveController(config)

    assert controller.green_time_for(score) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.1, 30.0), (0.3, 45.0), (0.6, 60.0)],
)
def test_selection_carries_the_banded_green_time(
    score: float, expected: float, config: Config
) -> None:
    """The Selection's Green_Time is the selected Approach's band (Reqs 8.4-8.6)."""
    controller = AdaptiveController(config)

    selection = controller.select(_scores(0.0, score, 0.0, 0.0))

    assert selection.approach == "East"
    assert selection.selection_score == pytest.approx(score)
    assert selection.green_time == pytest.approx(expected)


def test_band_thresholds_come_from_configuration(config: Config) -> None:
    """Requirement 8.9: retuned bands change the outcome, no literals in the class."""
    retuned = dataclasses.replace(
        config,
        green_time_bands=(
            GreenTimeBand(min_score=0.0, green_time=30.0),
            GreenTimeBand(min_score=0.5, green_time=50.0),
        ),
        min_green_time=30.0,
        max_green_time=50.0,
    )
    controller = AdaptiveController(retuned)

    # 0.3 and 0.6 mean nothing to this band set; 0.5 is the only threshold.
    assert controller.green_time_for(0.3) == pytest.approx(30.0)
    assert controller.green_time_for(0.49) == pytest.approx(30.0)
    assert controller.green_time_for(0.5) == pytest.approx(50.0)
    assert controller.green_time_for(0.6) == pytest.approx(50.0)


def test_a_single_band_applies_to_every_score(config: Config) -> None:
    """A degenerate one-band set is a constant Green_Time, not a lookup failure."""
    flat_band = dataclasses.replace(
        config,
        green_time_bands=(GreenTimeBand(min_score=0.0, green_time=40.0),),
        min_green_time=40.0,
        max_green_time=40.0,
    )
    controller = AdaptiveController(flat_band)

    assert {controller.green_time_for(s) for s in (0.0, 0.3, 0.6, 1.0)} == {40.0}


def test_empty_bands_are_rejected_at_construction(config: Config) -> None:
    """Without a band there is no Green_Time to assign (Requirement 8.9)."""
    with pytest.raises(ValueError, match="green_time_bands"):
        AdaptiveController(dataclasses.replace(config, green_time_bands=()))


def test_green_time_stays_within_the_configured_bounds(config: Config) -> None:
    """Requirement 8.8, by way of the validated band set."""
    controller = AdaptiveController(config)

    for score in (0.0, 0.29, 0.3, 0.59, 0.6, 1.0):
        assert config.min_green_time <= controller.green_time_for(score)
        assert controller.green_time_for(score) <= config.max_green_time


def test_green_time_is_non_decreasing_in_score(config: Config) -> None:
    """Requirement 8.7, by example; the generated version is a property test."""
    controller = AdaptiveController(config)
    steps = [i / 20 for i in range(21)]

    granted = [controller.green_time_for(score) for score in steps]

    assert all(b >= a for a, b in zip(granted, granted[1:]))


# ---------------------------------------------------------------------------
# Driven through the Phase_Sequencer (Requirements 8.10, 10.6, 11.2)
# ---------------------------------------------------------------------------


def test_satisfies_the_controller_protocol(config: Config) -> None:
    """Interchangeable with the baseline behind one protocol (Requirement 10.6)."""
    assert isinstance(AdaptiveController(config), Controller)


def test_green_then_yellow_then_red_through_the_sequencer(config: Config) -> None:
    """Requirement 8.10: YELLOW for the Yellow_Duration, then RED, then next Cycle."""
    sequencer = PhaseSequencer(AdaptiveController(config), config, FRAME_RATE)
    scores = _scores(0.0, 0.7, 0.0, 0.0)     # East, in the 60 s band
    green_frames = duration_to_frames(60.0, FRAME_RATE)
    yellow_frames = duration_to_frames(config.yellow_duration, FRAME_RATE)

    states = [
        sequencer.tick(frame, scores).state
        for frame in range(green_frames + yellow_frames)
    ]

    assert states[:green_frames] == [SignalState.GREEN] * green_frames
    assert states[green_frames:] == [SignalState.YELLOW] * yellow_frames
    assert sequencer.signal_states()["East"] is SignalState.YELLOW
    # The Cycle after the YELLOW expires begins on the same frame the East phase ends.
    info = sequencer.tick(green_frames + yellow_frames, scores)
    assert info.state is SignalState.GREEN
    assert sequencer.cycle_count == 2


def test_a_congested_approach_gets_the_longer_green(config: Config) -> None:
    """A high Score buys more frames than a low one (Requirements 8.4, 8.6, 11.2)."""
    sequencer = PhaseSequencer(AdaptiveController(config), config, FRAME_RATE)
    busy = _scores(0.0, 0.9, 0.0, 0.0)
    quiet = _scores(0.1, 0.0, 0.0, 0.0)

    sequencer.tick(0, busy)
    cycle_frames = sequencer.phase_end_frame + sequencer.yellow_frames
    for frame in range(1, cycle_frames):
        sequencer.tick(frame, busy)
    # The next Cycle begins on this frame, reading the quiet Scores.
    sequencer.tick(cycle_frames, quiet)

    greens = [p for p in sequencer.phases if p.state is SignalState.GREEN]
    assert [(p.approach, p.green_time) for p in greens] == [
        ("East", 60.0),
        ("North", 30.0),
    ]
    assert greens[0].frames == duration_to_frames(60.0, FRAME_RATE)
    assert greens[1].frames == duration_to_frames(30.0, FRAME_RATE)


def test_a_sub_one_frame_green_time_still_spans_one_frame(config: Config) -> None:
    """Requirement 11.2: max(1, round(g * fps)) floors a tiny band at one frame."""
    tiny = dataclasses.replace(
        config,
        green_time_bands=(GreenTimeBand(min_score=0.0, green_time=0.01),),
        min_green_time=0.01,
        max_green_time=0.01,
        yellow_duration=0.01,
    )
    controller = AdaptiveController(tiny)
    sequencer = PhaseSequencer(controller, tiny, FRAME_RATE)
    scores = _scores(0.9, 0.0, 0.0, 0.0)

    # 0.01 s at 1 fps rounds to 0 frames; the floor makes it 1.
    assert controller.green_time_for(0.9) == pytest.approx(0.01)
    assert duration_to_frames(0.01, FRAME_RATE) == 1

    info = sequencer.tick(0, scores)

    assert info.state is SignalState.GREEN
    assert info.approach == "North"
    assert sequencer.phases[0].frames == 1
    # One frame of GREEN, one of YELLOW, then the next Cycle begins.
    assert sequencer.tick(1, scores).state is SignalState.YELLOW
    assert sequencer.tick(2, scores).state is SignalState.GREEN
    assert all(phase.frames >= 1 for phase in sequencer.phases)
