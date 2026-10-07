"""Property-based tests for control and timing (Properties 8 to 17).

Both controllers are driven through a real :class:`~src.signal_controller.PhaseSequencer`
over generated Score sequences and generated configurations — generated band sets,
Starvation_Limits, Yellow_Durations, and frame rates — so the invariants are checked
against the timing code that actually runs rather than against a simplified stand-in.
No video, weights, or GPU are involved: the control path is a function of Scores and
configuration alone, which is exactly why it can be quantified over like this.

A note on Requirements 9.4 and 9.5, because the bound this module asserts is weaker
than the one those requirements state, and the difference is a finding rather than a
shortcut.

    Requirement 8.2 obliges the Adaptive_Controller to serve a maximal-Score Approach
    whenever no Approach has reached the Starvation_Limit ``L``. Requirement 9.2 makes
    every unselected Approach's counter grow by one per Cycle. Take Scores that hold
    North at 1.0 and the other three at 0.0 forever. Requirement 8.2 forces North for
    ``L`` Cycles, at the end of which East, South and West all sit at exactly ``L`` —
    still within Requirement 9.4's bound. The next Cycle can serve only one of them
    (Requirement 10.1 permits one GREEN), so the two it does not serve reach ``L + 1``,
    and the last of them reaches ``L + 2`` before its turn arrives. Requirement 9.4's
    bound is therefore unreachable for four Approaches under Requirement 8.2, for any
    ``L`` whatsoever — it is not an artefact of this implementation.

    What *is* attainable, and what these tests assert, is ``L + A - 2`` with ``A = 4``
    Approaches: an Approach's counter exceeds ``L`` only while it waits in the starved
    set, that set holds at most ``A - 1`` Approaches, and the override drains it in
    counter order, so no Approach waits more than ``A - 2`` Cycles beyond the limit.
    Equivalently, every Approach is served within any window of ``L + A - 1``
    consecutive Cycles. Requirement 9.4's exact bound *is* asserted, separately, for
    the states it is feasible in: those where at most one Approach sits at or above
    the limit at a Cycle end.

    The practical guarantee Requirement 9 exists for — no Approach waits unboundedly,
    and the bound is a configured constant plus a fixed three Cycles — holds either
    way.

Validates: Requirements 7.3, 8.7, 8.8, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.5, 11.2, 11.3
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Sequence

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.config import APPROACH_NAMES, Config, GreenTimeBand, load_config
from src.signal_controller import (
    APPROACH_ORDER,
    LEGAL_TRANSITIONS,
    AdaptiveController,
    FixedTimeController,
    PhaseSequencer,
    Selection,
    SignalState,
    duration_to_frames,
)
from src.video_io import simulated_clock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = load_config(str(PROJECT_ROOT / "config" / "default.json"))

#: How many Approaches the System controls. Named because the attainable starvation
#: bound is stated in terms of it rather than as the bare number 4.
APPROACH_COUNT = len(APPROACH_ORDER)

#: Frames a generated run may take before it is cut short. A generous ceiling: it
#: exists so a pathological band set cannot turn one example into a long loop, not
#: as a limit the assertions rely on.
MAX_FRAMES = 4000

_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def _timing_configs(draw: Any) -> Config:
    """Vary every configured value the control path reads.

    Durations are drawn small — a few simulated seconds, at a few frames per second
    — so one generated example covers a dozen Cycles in a few hundred ticks. The
    band set is drawn to satisfy exactly what the Config_Loader validates: a first
    band at ``min_score`` 0, strictly increasing ``min_score``, non-decreasing
    ``green_time``, every ``green_time`` inside ``[min_green_time, max_green_time]``.
    Generating that shape here rather than filtering invalid ones keeps the
    generated space the *accepted* configuration space, which is what Requirements
    8.7 and 8.8 are quantified over.
    """
    min_green = draw(st.floats(min_value=1.0, max_value=3.0))
    max_green = min_green + draw(st.floats(min_value=0.0, max_value=4.0))

    band_count = draw(st.integers(min_value=1, max_value=4))
    thresholds = draw(
        st.lists(
            st.floats(min_value=0.05, max_value=1.0),
            min_size=band_count - 1,
            max_size=band_count - 1,
            unique=True,
        )
    )
    green_times = sorted(
        draw(
            st.lists(
                st.floats(min_value=min_green, max_value=max_green),
                min_size=band_count,
                max_size=band_count,
            )
        )
    )
    bands = tuple(
        GreenTimeBand(min_score=min_score, green_time=green_time)
        for min_score, green_time in zip([0.0, *sorted(thresholds)], green_times)
    )

    return dataclasses.replace(
        DEFAULT_CONFIG,
        green_time_bands=bands,
        min_green_time=min_green,
        max_green_time=max_green,
        yellow_duration=draw(st.floats(min_value=0.5, max_value=2.0)),
        starvation_limit=draw(st.integers(min_value=1, max_value=5)),
    )


_frame_rates = st.floats(min_value=1.0, max_value=4.0)

_score_vectors = st.fixed_dictionaries({name: _unit for name in APPROACH_NAMES})

_score_sequences = st.lists(_score_vectors, min_size=1, max_size=8)


# ---------------------------------------------------------------------------
# Run harness
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Run:
    """One recorded pass of a controller through the sequencer."""

    sequencer: PhaseSequencer
    frames: tuple[int, ...]
    states: tuple[dict[str, SignalState], ...]
    cycle_starts: tuple[int, ...]     # frame index at which each Cycle's GREEN began


def _drive(
    controller: Any,
    config: Config,
    frame_rate: float,
    scores: Sequence[dict[str, float]],
    *,
    cycles: int,
) -> Run:
    """Tick ``controller`` through a sequencer until ``cycles`` Cycles have begun.

    The Score vector handed to each tick cycles through ``scores``, so a short
    generated sequence still varies the Scores the controller reads at successive
    Cycle boundaries rather than pinning them to one vector for the whole run.

    The all-RED vector every run starts in is recorded *before* the first tick, so
    Property 10 is checked against the state the System is actually in at frame 0
    rather than against a claim about the constructor.
    """
    sequencer = PhaseSequencer(controller, config, frame_rate)
    frames: list[int] = []
    states: list[dict[str, SignalState]] = [sequencer.signal_states()]
    cycle_starts: list[int] = []

    frame = 0
    while sequencer.cycle_count < cycles and frame < MAX_FRAMES:
        before = sequencer.cycle_count
        sequencer.tick(frame, scores[frame % len(scores)])
        if sequencer.cycle_count > before:
            cycle_starts.append(frame)
        frames.append(frame)
        states.append(sequencer.signal_states())
        frame += 1

    return Run(
        sequencer=sequencer,
        frames=tuple(frames),
        states=tuple(states),
        cycle_starts=tuple(cycle_starts),
    )


def _adaptive_selections(
    config: Config, scores: Sequence[dict[str, float]], *, cycles: int
) -> tuple[list[Selection], list[dict[str, int]]]:
    """Run the Adaptive_Controller alone and record its decisions and counters.

    The counter snapshot is taken after each :meth:`select`, which is the "end of
    every Cycle" Requirements 9.4 and 9.5 are stated at. Driving the controller
    directly rather than through the sequencer keeps the starvation properties
    independent of timing: they are claims about the Cycle sequence, and a Cycle is
    a decision, not a frame count.
    """
    controller = AdaptiveController(config)
    selections: list[Selection] = []
    counters: list[dict[str, int]] = []
    for index in range(cycles):
        selections.append(controller.select(scores[index % len(scores)]))
        counters.append(controller.cycles_waited)
    return selections, counters


# ---------------------------------------------------------------------------
# Property 8: exactly one approach is non-red (Requirements 10.1, 10.2, 10.3)
# ---------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(config=_timing_configs(), frame_rate=_frame_rates, scores=_score_sequences)
def test_property_8_exactly_one_approach_is_non_red_after_the_first_cycle(
    config: Config, frame_rate: float, scores: Sequence[dict[str, float]]
) -> None:
    """One GREEN or YELLOW, three RED, on every frame of every Cycle."""
    for controller in (AdaptiveController(config), FixedTimeController()):
        run = _drive(controller, config, frame_rate, scores, cycles=4)
        assert run.frames, "the run produced no frames to check"

        # states[0] is the pre-run all-RED vector; states[i + 1] belongs to frames[i].
        for frame, state in zip(run.frames, run.states[1:]):
            non_red = [name for name, value in state.items() if value is not SignalState.RED]
            assert len(non_red) == 1, (
                f"frame {frame} of the {run.sequencer.controller_name} run holds "
                f"{len(non_red)} non-RED approaches: {state}"
            )
            assert state[non_red[0]] in (SignalState.GREEN, SignalState.YELLOW)
            assert all(
                state[name] is SignalState.RED
                for name in APPROACH_ORDER
                if name != non_red[0]
            )


# ---------------------------------------------------------------------------
# Property 9: transitions follow the legal cycle (Requirement 10.4)
# ---------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(config=_timing_configs(), frame_rate=_frame_rates, scores=_score_sequences)
def test_property_9_every_transition_is_legal(
    config: Config, frame_rate: float, scores: Sequence[dict[str, float]]
) -> None:
    """Only RED→GREEN, GREEN→YELLOW and YELLOW→RED ever occur."""
    for controller in (AdaptiveController(config), FixedTimeController()):
        run = _drive(controller, config, frame_rate, scores, cycles=4)
        transitions = run.sequencer.transitions
        assert transitions, "the run recorded no transitions"

        for transition in transitions:
            edge = (transition.from_state, transition.to_state)
            assert edge in LEGAL_TRANSITIONS, (
                f"illegal edge {edge} for {transition.approach} at frame "
                f"{transition.frame_index}"
            )

        # The per-approach edge sequences are the same claim read the other way: each
        # Approach's own history must also be a walk through the legal cycle.
        for name in APPROACH_ORDER:
            state = SignalState.RED
            for transition in transitions:
                if transition.approach != name:
                    continue
                assert transition.from_state is state
                assert (state, transition.to_state) in LEGAL_TRANSITIONS
                state = transition.to_state


# ---------------------------------------------------------------------------
# Property 10: the run starts all-red (Requirement 10.5)
# ---------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(config=_timing_configs(), frame_rate=_frame_rates, scores=_score_sequences)
def test_property_10_the_run_starts_all_red(
    config: Config, frame_rate: float, scores: Sequence[dict[str, float]]
) -> None:
    """Before the first Cycle begins, all four Approaches read RED."""
    for controller in (AdaptiveController(config), FixedTimeController()):
        run = _drive(controller, config, frame_rate, scores, cycles=2)

        assert run.states[0] == {name: SignalState.RED for name in APPROACH_ORDER}
        # And the first Cycle begins on the very first ticked frame, so the all-RED
        # vector is never observed by a frame of the run itself.
        assert run.cycle_starts[0] == run.frames[0]


# ---------------------------------------------------------------------------
# Properties 11 and 12: green time bounds and monotonicity (Reqs 8.7, 8.8)
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(config=_timing_configs(), scores=_score_sequences)
def test_property_11_green_time_stays_within_the_configured_bounds(
    config: Config, scores: Sequence[dict[str, float]]
) -> None:
    """Every assigned Green_Time lies in ``[min_green_time, max_green_time]``."""
    selections, _counters = _adaptive_selections(config, scores, cycles=12)

    for selection in selections:
        assert config.min_green_time <= selection.green_time <= config.max_green_time


@settings(max_examples=50, deadline=None)
@given(config=_timing_configs(), lower=_unit, upper=_unit)
def test_property_12_green_time_is_non_decreasing_in_score(
    config: Config, lower: float, upper: float
) -> None:
    """A higher Score never earns a shorter Green_Time."""
    assume(lower <= upper)
    controller = AdaptiveController(config)

    assert controller.green_time_for(lower) <= controller.green_time_for(upper)


@settings(max_examples=30, deadline=None)
@given(config=_timing_configs(), scores=st.lists(_unit, min_size=2, max_size=12))
def test_property_12_band_lookup_is_monotone_across_a_sorted_score_sweep(
    config: Config, scores: list[float]
) -> None:
    """Sorting Scores sorts their Green_Times, for any accepted band set."""
    controller = AdaptiveController(config)
    green_times = [controller.green_time_for(score) for score in sorted(scores)]

    assert green_times == sorted(green_times)


# ---------------------------------------------------------------------------
# Properties 13 and 14: starvation bounds (Requirements 9.4, 9.5)
# ---------------------------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(config=_timing_configs(), scores=_score_sequences)
def test_property_13_counters_stay_within_the_attainable_bound(
    config: Config, scores: Sequence[dict[str, float]]
) -> None:
    """No counter exceeds ``starvation_limit + APPROACH_COUNT - 2``.

    The attainable form of Requirement 9.4; the module docstring proves why the
    stated bound cannot hold jointly with Requirement 8.2 for four Approaches.
    """
    bound = config.starvation_limit + APPROACH_COUNT - 2
    _selections, counters = _adaptive_selections(config, scores, cycles=24)

    for cycle, snapshot in enumerate(counters):
        for name, waited in snapshot.items():
            assert waited <= bound, (
                f"{name} waited {waited} Cycles at the end of Cycle {cycle}, above the "
                f"attainable bound {bound} for starvation_limit {config.starvation_limit}"
            )


@settings(max_examples=60, deadline=None)
@given(config=_timing_configs(), scores=_score_sequences)
def test_property_13_counters_respect_the_stated_limit_while_one_approach_starves(
    config: Config, scores: Sequence[dict[str, float]]
) -> None:
    """Requirement 9.4 exactly, over the Cycles where it is feasible.

    A Cycle end at which at most one Approach sits at or above the Starvation_Limit
    is one the override can fully discharge on the next Cycle, so the stated bound
    must hold at the end of that next Cycle. This is the check that would fail if
    counters were updated before the decision instead of after it.
    """
    limit = config.starvation_limit
    _selections, counters = _adaptive_selections(config, scores, cycles=24)

    for previous, current in zip(counters, counters[1:]):
        at_limit = [name for name, waited in previous.items() if waited >= limit]
        if len(at_limit) > 1:
            continue
        assert max(current.values()) <= limit, (
            f"counters {current} exceed the limit {limit} although only {at_limit} "
            f"was starving at the previous Cycle end ({previous})"
        )


@settings(max_examples=60, deadline=None)
@given(config=_timing_configs(), scores=_score_sequences)
def test_property_14_every_approach_is_served_within_the_attainable_window(
    config: Config, scores: Sequence[dict[str, float]]
) -> None:
    """Every Approach appears in every window of ``limit + APPROACH_COUNT - 1`` Cycles.

    The attainable form of Requirement 9.5, and the guarantee that matters: the wait
    an Approach can suffer is bounded by a configured constant plus a fixed three
    Cycles, whatever the Scores do.
    """
    window = config.starvation_limit + APPROACH_COUNT - 1
    selections, _counters = _adaptive_selections(config, scores, cycles=window * 3)
    served = [selection.approach for selection in selections]

    for start in range(0, len(served) - window + 1):
        seen = set(served[start : start + window])
        assert seen == set(APPROACH_ORDER), (
            f"window {served[start : start + window]} starting at Cycle {start} misses "
            f"{sorted(set(APPROACH_ORDER) - seen)} for starvation_limit "
            f"{config.starvation_limit}"
        )


@settings(max_examples=40, deadline=None)
@given(config=_timing_configs(), scores=_score_sequences)
def test_property_14_a_starved_approach_is_served_before_any_new_score_choice(
    config: Config, scores: Sequence[dict[str, float]]
) -> None:
    """Once the limit is reached, every Cycle is an override until the set drains.

    This is the mechanism the window bound rests on: while any Approach sits at or
    above the limit, Score cannot win a Cycle, so the starved set can only shrink.
    """
    limit = config.starvation_limit
    selections, counters = _adaptive_selections(config, scores, cycles=24)

    for snapshot, selection in zip(counters, selections[1:]):
        if any(waited >= limit for waited in snapshot.values()):
            assert selection.starvation_override is True, (
                f"counters {snapshot} reached the limit {limit} but the next Cycle "
                f"served {selection.approach} on Score"
            )


# ---------------------------------------------------------------------------
# Property 15: fixed-time control is score-independent (Requirement 7.3)
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    config=_timing_configs(),
    frame_rate=_frame_rates,
    first=_score_sequences,
    second=_score_sequences,
)
def test_property_15_fixed_time_control_ignores_scores(
    config: Config,
    frame_rate: float,
    first: Sequence[dict[str, float]],
    second: Sequence[dict[str, float]],
) -> None:
    """Two different Score sequences produce one identical fixed-time phase plan."""
    cycles = 6
    plans = []
    for scores in (first, second):
        controller = FixedTimeController()
        plans.append(
            [
                (selection.approach, selection.green_time)
                for selection in (controller.select(scores[i % len(scores)]) for i in range(cycles))
            ]
        )

    assert plans[0] == plans[1]
    # The plan is the fixed order at a constant Green_Time, so it is also independent
    # of the configuration's band set.
    assert [approach for approach, _green in plans[0]] == [
        APPROACH_ORDER[index % APPROACH_COUNT] for index in range(cycles)
    ]
    assert len({green for _approach, green in plans[0]}) == 1

    # And the same holds of the frames the sequencer derives from that plan.
    runs = [
        _drive(FixedTimeController(), config, frame_rate, scores, cycles=3)
        for scores in (first, second)
    ]
    assert [phase.as_dict() for phase in runs[0].sequencer.phases] == [
        phase.as_dict() for phase in runs[1].sequencer.phases
    ]


# ---------------------------------------------------------------------------
# Properties 16 and 17: clock and phase length (Requirements 11.2, 11.3)
# ---------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(config=_timing_configs(), frame_rate=_frame_rates, scores=_score_sequences)
def test_property_16_the_simulated_clock_strictly_increases(
    config: Config, frame_rate: float, scores: Sequence[dict[str, float]]
) -> None:
    """Video time advances on every processed frame and never repeats or rewinds."""
    for controller in (AdaptiveController(config), FixedTimeController()):
        run = _drive(controller, config, frame_rate, scores, cycles=3)
        clock = [simulated_clock(frame, frame_rate) for frame in run.frames]

        assert clock[0] == 0.0
        assert all(later > earlier for earlier, later in zip(clock, clock[1:]))


@settings(max_examples=50, deadline=None)
@given(config=_timing_configs(), frame_rate=_frame_rates, scores=_score_sequences)
def test_property_17_every_phase_spans_at_least_one_frame(
    config: Config, frame_rate: float, scores: Sequence[dict[str, float]]
) -> None:
    """No segment is zero-length, however short its configured duration."""
    for controller in (AdaptiveController(config), FixedTimeController()):
        run = _drive(controller, config, frame_rate, scores, cycles=4)
        phases = run.sequencer.phases
        assert phases, "the run recorded no phases"

        for phase in phases:
            assert phase.frames >= 1
            assert phase.end_frame == phase.start_frame + phase.frames
            expected = duration_to_frames(
                phase.green_time if phase.state is SignalState.GREEN else config.yellow_duration,
                frame_rate,
            )
            assert phase.frames == expected

        # Segments tile the run without gaps or overlaps, which is what makes "one
        # non-RED Approach per frame" (Property 8) a statement about every frame.
        for earlier, later in zip(phases, phases[1:]):
            assert later.start_frame == earlier.end_frame


@settings(max_examples=50, deadline=None)
@given(
    seconds=st.floats(min_value=0.0001, max_value=120.0),
    frame_rate=st.floats(min_value=0.1, max_value=120.0),
)
def test_property_17_duration_conversion_never_yields_a_zero_length_phase(
    seconds: float, frame_rate: float
) -> None:
    """``max(1, round(g * fps))`` for any positive duration and frame rate."""
    frames = duration_to_frames(seconds, frame_rate)

    assert frames >= 1
    assert frames == max(1, round(seconds * frame_rate))
