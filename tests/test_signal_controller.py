"""Unit tests for the signal state model and the Phase_Sequencer.

The sequencer is driven by a stub controller here, not by the real
Fixed_Time_Controller or Adaptive_Controller: those are separate tasks, and the
integrity and timing guarantees under test belong to the sequencer for *any*
controller (Requirement 10.6). The generated-input versions of these invariants
live in the property tests for this module.

Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 11.1, 11.2, 11.3, 11.4
"""

from __future__ import annotations

import dataclasses
import itertools
from pathlib import Path

import pytest

from src.config import Config, load_config
from src.signal_controller import (
    APPROACH_ORDER,
    LEGAL_TRANSITIONS,
    Controller,
    PhaseSequencer,
    Selection,
    SignalState,
    duration_to_frames,
)
from src.video_io import simulated_clock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: Deliberately small so a whole run of several Cycles fits in a few dozen frames.
FRAME_RATE = 4.0
ZERO_SCORES = {name: 0.0 for name in APPROACH_ORDER}


class StubController:
    """Cycles through a scripted list of selections, ignoring Scores.

    Standing in for the real controllers so these tests exercise only the
    sequencer. Records the Scores it was handed so the read-on-Cycle-boundary
    behaviour stays visible.
    """

    name = "stub"

    def __init__(self, selections: list[Selection]) -> None:
        self._selections = itertools.cycle(selections)
        self.seen_scores: list[dict[str, float]] = []

    def select(self, scores):  # noqa: ANN001 - protocol signature
        self.seen_scores.append(dict(scores))
        return next(self._selections)


def _selection(approach: str, green_time: float) -> Selection:
    return Selection(
        approach=approach,
        green_time=green_time,
        starvation_override=False,
        selection_score=0.5,
    )


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _sequencer(config: Config, selections: list[Selection], **overrides) -> PhaseSequencer:
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return PhaseSequencer(StubController(selections), config, FRAME_RATE)


# ---------------------------------------------------------------------------
# Frame conversion (Requirement 11.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "frame_rate", "expected"),
    [
        (30.0, 25.0, 750),
        (45.0, 30.0, 1350),
        (3.0, 25.0, 75),
        (1.0, 1.0, 1),
        (0.01, 25.0, 1),        # sub-one-frame duration still spans a frame
        (0.019, 25.0, 1),       # rounds to 0 before the floor applies
        (0.5, 3.0, 2),          # 1.5 rounds to even, per the design's round()
    ],
)
def test_duration_to_frames(seconds: float, frame_rate: float, expected: int) -> None:
    """``max(1, round(seconds * frame_rate))``, never below one frame (Req 11.2)."""
    assert duration_to_frames(seconds, frame_rate) == expected


@pytest.mark.parametrize("seconds", [0.0, -1.0, float("nan"), float("inf")])
def test_duration_to_frames_rejects_unusable_durations(seconds: float) -> None:
    with pytest.raises(ValueError):
        duration_to_frames(seconds, FRAME_RATE)


@pytest.mark.parametrize("frame_rate", [0.0, -25.0, float("nan")])
def test_duration_to_frames_rejects_unusable_frame_rates(frame_rate: float) -> None:
    with pytest.raises(ValueError):
        duration_to_frames(30.0, frame_rate)


# ---------------------------------------------------------------------------
# The all-red start (Requirement 10.5)
# ---------------------------------------------------------------------------


def test_run_starts_all_red(config: Config) -> None:
    """Before the first Cycle every Approach is RED (Requirement 10.5)."""
    sequencer = _sequencer(config, [_selection("North", 30.0)])

    assert sequencer.active_approach is None
    assert sequencer.signal_states() == {name: SignalState.RED for name in APPROACH_ORDER}
    assert sequencer.phases == ()
    assert sequencer.cycle_count == 0


def test_first_tick_opens_a_green_phase(config: Config) -> None:
    """The first tick begins a Cycle, so one Approach is GREEN (Reqs 10.1, 10.2)."""
    sequencer = _sequencer(config, [_selection("East", 30.0)])

    info = sequencer.tick(0, ZERO_SCORES)

    assert info.approach == "East"
    assert info.state is SignalState.GREEN
    assert sequencer.signal_states() == {
        "North": SignalState.RED,
        "East": SignalState.GREEN,
        "South": SignalState.RED,
        "West": SignalState.RED,
    }
    assert info.green_time == 30.0
    assert info.remaining_seconds == pytest.approx(30.0)
    assert info.phase_started is True
    assert info.selection_changed is True       # nothing was selected before
    assert sequencer.phases[0].selection_score_frame == -1


# ---------------------------------------------------------------------------
# Phase timing and the legal cycle (Requirements 10.1-10.4, 11.2)
# ---------------------------------------------------------------------------


def test_green_then_yellow_then_next_green_span_exact_frame_counts(config: Config) -> None:
    """Each segment lasts exactly its converted frame count (Requirement 11.2)."""
    # 2 s green at 4 fps = 8 frames; 3 s yellow at 4 fps = 12 frames.
    sequencer = _sequencer(
        config,
        [_selection("North", 2.0), _selection("South", 2.0)],
        min_green_time=2.0,
        max_green_time=60.0,
    )
    green_frames = duration_to_frames(2.0, FRAME_RATE)
    yellow_frames = duration_to_frames(config.yellow_duration, FRAME_RATE)

    states = [sequencer.tick(i, ZERO_SCORES).state for i in range(green_frames + yellow_frames + 2)]

    assert states[:green_frames] == [SignalState.GREEN] * green_frames
    assert states[green_frames : green_frames + yellow_frames] == [SignalState.YELLOW] * yellow_frames
    # The frame after YELLOW expires opens the next Cycle's GREEN in the same tick,
    # so no frame reads all-RED once the first Cycle has started (Requirement 10.1).
    assert states[green_frames + yellow_frames] is SignalState.GREEN


def test_exactly_one_approach_is_non_red_on_every_frame(config: Config) -> None:
    """Requirements 10.1, 10.2, 10.3 over a multi-Cycle run."""
    sequencer = _sequencer(
        config,
        [_selection(name, 2.0) for name in APPROACH_ORDER],
        min_green_time=2.0,
    )

    for frame in range(60):
        sequencer.tick(frame, ZERO_SCORES)
        states = sequencer.signal_states()
        assert set(states) == set(APPROACH_ORDER)
        non_red = [name for name, state in states.items() if state is not SignalState.RED]
        assert len(non_red) == 1
        assert states[non_red[0]] in (SignalState.GREEN, SignalState.YELLOW)


def test_every_transition_is_legal(config: Config) -> None:
    """Only RED→GREEN, GREEN→YELLOW, YELLOW→RED occur (Requirement 10.4)."""
    sequencer = _sequencer(
        config,
        [_selection(name, 2.0) for name in APPROACH_ORDER],
        min_green_time=2.0,
    )

    for frame in range(60):
        sequencer.tick(frame, ZERO_SCORES)

    assert sequencer.transitions
    for transition in sequencer.transitions:
        assert (transition.from_state, transition.to_state) in LEGAL_TRANSITIONS

    # And the per-approach sequence of edges follows the cycle in order.
    for name in APPROACH_ORDER:
        edges = [t.to_state for t in sequencer.transitions if t.approach == name]
        expected = [SignalState.GREEN, SignalState.YELLOW, SignalState.RED]
        for index, state in enumerate(edges):
            assert state is expected[index % 3]


def test_cycles_are_recorded_as_green_and_yellow_segments(config: Config) -> None:
    """One Cycle produces one GREEN and one YELLOW segment on the same Approach."""
    sequencer = _sequencer(
        config,
        [_selection("North", 2.0), _selection("West", 2.0)],
        min_green_time=2.0,
    )

    for frame in range(duration_to_frames(2.0, FRAME_RATE) + sequencer.yellow_frames + 1):
        sequencer.tick(frame, ZERO_SCORES)

    first_cycle = [p for p in sequencer.phases if p.cycle_index == 0]
    assert [p.state for p in first_cycle] == [SignalState.GREEN, SignalState.YELLOW]
    assert {p.approach for p in first_cycle} == {"North"}
    assert first_cycle[0].end_frame == first_cycle[1].start_frame
    assert sequencer.cycle_count == 2


def test_selection_changed_tracks_the_served_approach(config: Config) -> None:
    """Requirement 12.7's trigger: the flag is set only when the Approach changes."""
    sequencer = _sequencer(
        config,
        [_selection("North", 2.0), _selection("North", 2.0), _selection("East", 2.0)],
        min_green_time=2.0,
    )
    cycle_frames = duration_to_frames(2.0, FRAME_RATE) + sequencer.yellow_frames

    changed_by_cycle: dict[int, bool] = {}
    for frame in range(cycle_frames * 3):
        info = sequencer.tick(frame, ZERO_SCORES)
        changed_by_cycle.setdefault(info.cycle_index, info.selection_changed)

    assert changed_by_cycle[0] is True     # first selection
    assert changed_by_cycle[1] is False    # North again
    assert changed_by_cycle[2] is True     # North -> East


def test_remaining_seconds_counts_down_within_a_phase(config: Config) -> None:
    """The overlay's countdown decreases by one frame interval per frame (Req 12.4)."""
    sequencer = _sequencer(config, [_selection("North", 2.0)], min_green_time=2.0)
    green_frames = duration_to_frames(2.0, FRAME_RATE)

    remaining = [sequencer.tick(i, ZERO_SCORES).remaining_seconds for i in range(green_frames)]

    assert remaining == pytest.approx(
        [(green_frames - i) / FRAME_RATE for i in range(green_frames)]
    )


def test_scores_are_read_only_at_cycle_boundaries(config: Config) -> None:
    """Requirement 8.1: the controller sees the Scores once per Cycle, not per frame."""
    controller = StubController([_selection("North", 2.0), _selection("East", 2.0)])
    sequencer = PhaseSequencer(
        controller, dataclasses.replace(config, min_green_time=2.0), FRAME_RATE
    )
    cycle_frames = duration_to_frames(2.0, FRAME_RATE) + sequencer.yellow_frames

    for frame in range(cycle_frames + 1):
        sequencer.tick(frame, ZERO_SCORES)

    assert len(controller.seen_scores) == sequencer.cycle_count == 2


# ---------------------------------------------------------------------------
# The Simulated_Clock (Requirements 11.1, 11.3)
# ---------------------------------------------------------------------------


def test_tick_requires_a_strictly_increasing_frame_index(config: Config) -> None:
    """A repeated or rewound frame index is rejected (Requirement 11.3)."""
    sequencer = _sequencer(config, [_selection("North", 30.0)])
    sequencer.tick(0, ZERO_SCORES)
    sequencer.tick(1, ZERO_SCORES)

    with pytest.raises(ValueError, match="strictly increase"):
        sequencer.tick(1, ZERO_SCORES)
    with pytest.raises(ValueError, match="strictly increase"):
        sequencer.tick(0, ZERO_SCORES)


def test_simulated_clock_increases_across_ticked_frames(config: Config) -> None:
    """Requirement 11.1: video time is the frame index over the frame rate."""
    sequencer = _sequencer(config, [_selection("North", 2.0)], min_green_time=2.0)

    times = []
    for frame in range(10):
        sequencer.tick(frame, ZERO_SCORES)
        times.append(simulated_clock(frame, sequencer.frame_rate))

    assert times == sorted(times) and len(set(times)) == len(times)
    assert times[1] - times[0] == pytest.approx(1.0 / FRAME_RATE)


# ---------------------------------------------------------------------------
# Truncation at the end of a run (Requirement 11.4)
# ---------------------------------------------------------------------------


def test_finalize_marks_an_in_progress_phase_truncated(config: Config) -> None:
    """A phase cut short by the final frame is flagged and excluded (Req 11.4)."""
    sequencer = _sequencer(config, [_selection("North", 30.0)])
    for frame in range(5):
        sequencer.tick(frame, ZERO_SCORES)

    truncated = sequencer.finalize(4)

    assert truncated is not None
    assert truncated.truncated is True
    assert truncated.include_in_averages is False
    assert truncated is sequencer.phases[-1]
    assert truncated not in sequencer.completed_phases
    assert truncated.as_dict()["truncated"] is True


def test_finalize_keeps_a_phase_that_ended_on_the_final_frame(config: Config) -> None:
    """A phase whose last frame is the run's last frame completed (Req 11.4)."""
    sequencer = _sequencer(config, [_selection("North", 2.0)], min_green_time=2.0)
    green_frames = duration_to_frames(2.0, FRAME_RATE)
    for frame in range(green_frames):
        sequencer.tick(frame, ZERO_SCORES)

    truncated = sequencer.finalize(green_frames - 1)

    assert truncated is None
    assert sequencer.completed_phases == sequencer.phases
    assert all(not phase.truncated for phase in sequencer.phases)


def test_finalize_is_idempotent_and_closes_the_run(config: Config) -> None:
    """Ticking after finalize is an error, and finalizing twice changes nothing."""
    sequencer = _sequencer(config, [_selection("North", 30.0)])
    sequencer.tick(0, ZERO_SCORES)

    first = sequencer.finalize(0)
    assert sequencer.is_finalized is True
    assert sequencer.finalize(0) is first

    with pytest.raises(ValueError, match="finalized"):
        sequencer.tick(1, ZERO_SCORES)


def test_finalize_leaves_the_signal_state_untouched(config: Config) -> None:
    """No GREEN→RED shortcut is taken to tidy up the end of a run (Req 10.4)."""
    sequencer = _sequencer(config, [_selection("South", 30.0)])
    sequencer.tick(0, ZERO_SCORES)

    sequencer.finalize(0)

    assert sequencer.signal_states()["South"] is SignalState.GREEN
    assert all(
        (t.from_state, t.to_state) in LEGAL_TRANSITIONS for t in sequencer.transitions
    )


def test_finalize_without_any_tick_records_nothing(config: Config) -> None:
    sequencer = _sequencer(config, [_selection("North", 30.0)])

    assert sequencer.finalize(0) is None
    assert sequencer.phases == ()


# ---------------------------------------------------------------------------
# Interface guards
# ---------------------------------------------------------------------------


def test_sequencer_rejects_incomplete_scores(config: Config) -> None:
    sequencer = _sequencer(config, [_selection("North", 30.0)])

    with pytest.raises(ValueError, match="missing approach"):
        sequencer.tick(0, {"North": 0.5, "East": 0.5, "South": 0.5})


def test_sequencer_rejects_an_unknown_selected_approach(config: Config) -> None:
    sequencer = _sequencer(config, [_selection("Northwest", 30.0)])

    with pytest.raises(ValueError, match="unknown approach"):
        sequencer.tick(0, ZERO_SCORES)


def test_sequencer_rejects_a_non_positive_green_time(config: Config) -> None:
    sequencer = _sequencer(config, [_selection("North", 0.0)])

    with pytest.raises(ValueError, match="green_time"):
        sequencer.tick(0, ZERO_SCORES)


@pytest.mark.parametrize("frame_rate", [0.0, -1.0, float("inf")])
def test_sequencer_rejects_an_unusable_frame_rate(config: Config, frame_rate: float) -> None:
    with pytest.raises(ValueError, match="frame_rate"):
        PhaseSequencer(StubController([_selection("North", 30.0)]), config, frame_rate)


def test_stub_controller_satisfies_the_controller_protocol() -> None:
    """The protocol is the only contract the sequencer needs (Requirement 10.6)."""
    assert isinstance(StubController([_selection("North", 30.0)]), Controller)


def test_signal_state_values_match_the_metrics_engine_vocabulary() -> None:
    """The Metrics_Engine gates Waiting_Time on these strings (Requirements 5.5, 5.6)."""
    from src.traffic_metrics import GREEN, RED, SIGNAL_STATES, YELLOW

    assert SignalState.GREEN == GREEN
    assert SignalState.YELLOW == YELLOW
    assert SignalState.RED == RED
    assert tuple(state.value for state in SignalState) == SIGNAL_STATES
