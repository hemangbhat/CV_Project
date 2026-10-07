"""Unit tests for Adaptive_Controller starvation prevention.

The Cycles_Waited counters and the override that reads them are pinned here by
example: when the override does *not* fire, when it does, which Approach it picks
when several are starving, what Green_Time an overridden Cycle receives, and that
the override reaches the Run_Log through the recorded Phase. The bound on the
counters (Requirement 9.4) and the service window (Requirement 9.5) are quantified
over all Score sequences and live in the property tests instead.

Validates: Requirements 9.1, 9.2, 9.3, 9.6
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import Config, load_config
from src.signal_controller import (
    APPROACH_ORDER,
    AdaptiveController,
    PhaseSequencer,
    SignalState,
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


def _starve_west(controller: AdaptiveController) -> None:
    """Serve North, East and South once each, so only West reaches a wait of 3.

    Leaves the counters at ``North 2, East 1, South 0, West 3`` — one Approach at
    the default Starvation_Limit and no other, which is the situation the override
    of Requirement 9.3 is written for.
    """
    controller.select(_scores(0.9, 0.0, 0.0, 0.0))
    controller.select(_scores(0.0, 0.9, 0.0, 0.0))
    controller.select(_scores(0.0, 0.0, 0.9, 0.0))


# ---------------------------------------------------------------------------
# The counters themselves (Requirements 9.1, 9.2)
# ---------------------------------------------------------------------------


def test_counters_start_at_zero_for_every_approach(config: Config) -> None:
    """Requirement 9.1: a run begins with nobody waiting, so no Cycle is forced."""
    controller = AdaptiveController(config)

    assert controller.cycles_waited == {name: 0 for name in APPROACH_ORDER}
    assert controller.starvation_limit == 3


def test_selection_resets_its_own_counter_and_increments_the_others(config: Config) -> None:
    """Requirement 9.2: one reset, three increments, once per Cycle."""
    controller = AdaptiveController(config)

    controller.select(_scores(0.0, 0.0, 0.9, 0.0))
    assert controller.cycles_waited == {"North": 1, "East": 1, "South": 0, "West": 1}
    controller.select(_scores(0.0, 0.0, 0.9, 0.0))
    assert controller.cycles_waited == {"North": 2, "East": 2, "South": 0, "West": 2}


def test_counters_are_updated_after_the_decision_not_before(config: Config) -> None:
    """Requirement 9.2: the reached limit forces the *next* Cycle, not this one."""
    controller = AdaptiveController(config)
    _starve_west(controller)

    # West's counter reached the limit as the third Cycle closed, so the third Cycle
    # itself was still decided on Score.
    assert controller.cycles_waited == {"North": 2, "East": 1, "South": 0, "West": 3}
    assert controller.cycles_selected == 3


# ---------------------------------------------------------------------------
# The override (Requirements 9.3, 9.6)
# ---------------------------------------------------------------------------


def test_no_override_while_every_counter_is_below_the_limit(config: Config) -> None:
    """Requirement 9.3 fires on reaching the limit, not before it (Req 8.2 holds)."""
    controller = AdaptiveController(config)

    for _ in range(3):
        selection = controller.select(_scores(0.9, 0.0, 0.0, 0.0))
        assert selection.approach == "North"
        assert selection.starvation_override is False


def test_a_zero_score_approach_is_served_once_its_counter_reaches_the_limit(
    config: Config,
) -> None:
    """Requirement 9.3: the longest waiter is served irrespective of every Score."""
    controller = AdaptiveController(config)
    _starve_west(controller)
    assert controller.cycles_waited["West"] == controller.starvation_limit

    # West holds the lowest Score of the four and would never win on Score.
    selection = controller.select(_scores(0.9, 0.8, 0.7, 0.0))

    assert selection.approach == "West"
    assert selection.starvation_override is True
    # Requirement 9.6: the Green_Time is the one West's own Score earns, which for a
    # Score of 0 is the lowest band, not the 60 s the Score-based winner would have had.
    assert selection.selection_score == pytest.approx(0.0)
    assert selection.green_time == pytest.approx(controller.green_time_for(0.0))
    assert selection.green_time == pytest.approx(30.0)
    # Serving it resets its counter, so the next Cycle is decided on Score again.
    assert controller.cycles_waited["West"] == 0


@pytest.mark.parametrize(
    ("west_score", "expected_green"),
    [(0.0, 30.0), (0.29, 30.0), (0.3, 45.0), (0.65, 60.0)],
)
def test_an_overridden_cycle_gets_the_green_time_its_own_score_earns(
    west_score: float, expected_green: float, config: Config
) -> None:
    """Requirement 9.6: the override changes who is served, not the band lookup."""
    controller = AdaptiveController(config)
    _starve_west(controller)

    selection = controller.select(_scores(0.9, 0.9, 0.9, west_score))

    assert (selection.approach, selection.starvation_override) == ("West", True)
    assert selection.green_time == pytest.approx(expected_green)


def test_override_picks_the_greatest_counter_when_several_are_starving(
    config: Config,
) -> None:
    """Requirement 9.3: greatest Cycles_Waited first, then APPROACH_ORDER."""
    controller = AdaptiveController(config)
    controller.select(_scores(0.9, 0.0, 0.0, 0.0))   # North; E, S, W wait 1
    controller.select(_scores(0.0, 0.9, 0.0, 0.0))   # East;  N 1, S 2, W 2
    controller.select(_scores(0.0, 0.9, 0.0, 0.0))   # East;  N 2, S 3, W 3

    # South and West are both at the limit and tie on wait; North has waited less.
    assert controller.cycles_waited == {"North": 2, "East": 0, "South": 3, "West": 3}
    assert controller.select(_scores(0.9, 0.9, 0.0, 0.0)).approach == "South"


def test_order_breaks_a_four_way_tie_at_the_limit(config: Config) -> None:
    """Ties among equally starved Approaches resolve to North, East, South, West."""
    controller = AdaptiveController(config)
    # Three Cycles of North leave East, South and West equally starved at the limit.
    for _ in range(3):
        controller.select(_scores(0.9, 0.0, 0.0, 0.0))
    assert controller.cycles_waited == {"North": 0, "East": 3, "South": 3, "West": 3}

    selection = controller.select(_scores(0.9, 0.0, 0.0, 0.0))

    assert (selection.approach, selection.starvation_override) == ("East", True)


def test_the_limit_comes_from_configuration(config: Config) -> None:
    """Requirement 9.7: a retuned Starvation_Limit moves when the override fires."""
    controller = AdaptiveController(dataclasses.replace(config, starvation_limit=2))

    controller.select(_scores(0.9, 0.0, 0.0, 0.0))   # North; E, S, W wait 1
    second = controller.select(_scores(0.9, 0.0, 0.0, 0.0))   # North again; E, S, W wait 2
    third = controller.select(_scores(0.9, 0.0, 0.0, 0.0))

    assert second.starvation_override is False
    # With a limit of 2 the override arrives a Cycle earlier than the default would.
    assert (third.approach, third.starvation_override) == ("East", True)


def test_a_limit_of_one_degenerates_into_round_robin(config: Config) -> None:
    """Every Approach starves after one skipped Cycle, so order alone decides."""
    controller = AdaptiveController(dataclasses.replace(config, starvation_limit=1))
    busy_north = _scores(0.9, 0.0, 0.0, 0.0)

    selections = [controller.select(busy_north) for _ in range(4)]

    assert [s.approach for s in selections] == list(APPROACH_ORDER)
    assert [s.starvation_override for s in selections] == [False, True, True, True]


# ---------------------------------------------------------------------------
# Reaching the Run_Log through the sequencer (Requirement 9.6)
# ---------------------------------------------------------------------------


def test_the_override_is_recorded_on_the_phase(config: Config) -> None:
    """Requirement 9.6: the flag reaches the Run_Log through the recorded Phase."""
    controller = AdaptiveController(config)
    sequencer = PhaseSequencer(controller, config, FRAME_RATE)
    busy_north = _scores(0.9, 0.0, 0.0, 0.0)
    frame = 0

    # Four Cycles: three on Score, the fourth forced by the limit.
    while sequencer.cycle_count < 4:
        info = sequencer.tick(frame, busy_north)
        frame += 1
        assert info.approach is not None

    greens = [p for p in sequencer.phases if p.state is SignalState.GREEN]
    assert [(p.approach, p.starvation_override) for p in greens] == [
        ("North", False),
        ("North", False),
        ("North", False),
        ("East", True),
    ]
    assert greens[-1].as_dict()["starvation_override"] is True
    # The forced Cycle still gets the Green_Time East's own Score of 0 earns.
    assert greens[-1].green_time == pytest.approx(30.0)
