"""Signal state model and Phase_Sequencer — timing and state integrity.

This module owns the simulated signal itself: what state each Approach is in on
each frame, how long a phase lasts in frames, and which transitions are even
expressible. The controllers that *choose* an Approach (Fixed_Time_Controller,
Adaptive_Controller) plug into it through the :class:`Controller` protocol, so
the integrity guarantees of Requirement 10 are written once and apply to both
(Requirement 10.6).

Three structural decisions carry most of the requirements:

**One triple, four derived states.** :class:`PhaseSequencer` stores exactly one
``(active_approach, active_state, phase_end_frame)`` triple and derives the
four-element Signal_State vector from it in :meth:`PhaseSequencer.signal_states`.
There is no per-approach state to fall out of agreement, so "exactly one Approach
is GREEN and the other three are RED" (Requirements 10.1, 10.2) and the same for
YELLOW (Requirement 10.3) are unfalsifiable rather than checked. A run starts with
``active_approach is None``, which reads as four REDs (Requirement 10.5).

**No operation produces an illegal edge.** The only state changes are the three
private helpers behind :meth:`PhaseSequencer.tick`, one per legal edge:
RED→GREEN when a Cycle begins, GREEN→YELLOW when the green frames elapse, and
YELLOW→RED when the yellow frames elapse (Requirement 10.4). Every one routes
through :meth:`PhaseSequencer._transition`, which rejects any pair outside
:data:`LEGAL_TRANSITIONS`. Nothing public sets a state directly, so an illegal
edge is unreachable, and the guard is there to keep it unreachable under later
edits.

**Durations are frames, not seconds.** Green_Time and Yellow_Duration are
configured in simulated seconds; the sequencer converts each with
:func:`duration_to_frames` — ``max(1, round(seconds * frame_rate))`` — so a phase
always spans at least one frame even when the configured duration is shorter than
a frame interval (Requirement 11.2). Because a phase is measured in whole frames
and :meth:`tick` requires a strictly increasing frame index, the Simulated_Clock
derived from that index by :func:`~src.video_io.simulated_clock` is strictly
increasing over the run (Requirements 11.1, 11.3).

A run that ends mid-phase leaves that phase incomplete, so
:meth:`PhaseSequencer.finalize` flags it ``truncated``; per-phase averages read
:attr:`PhaseSequencer.completed_phases`, which omits it (Requirement 11.4).
Finalizing does not force the signal to RED: GREEN→RED is not a legal edge, and
inventing one to tidy up the end of a run would be exactly the kind of hidden
transition Requirement 10.4 forbids.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from src.config import APPROACH_NAMES, Config


class SignalState(str, Enum):
    """The state of one Approach at a point of the Simulated_Clock.

    A ``str`` enum so the value serializes straight into the Run_Log and compares
    equal to the plain strings :mod:`src.traffic_metrics` gates Waiting_Time on,
    which is what keeps that module free of any import from this one.
    """

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.value


#: The Approach service order (Requirements 7.1, 8.3). Aliased to the Config_Loader's
#: set rather than restated, so the controllers, the geometry, and the metrics can
#: never disagree about which four Approaches exist or in what order ties break.
APPROACH_ORDER: tuple[str, ...] = APPROACH_NAMES

#: The only Signal_State edges the System may take (Requirement 10.4).
LEGAL_TRANSITIONS: frozenset[tuple[SignalState, SignalState]] = frozenset(
    {
        (SignalState.RED, SignalState.GREEN),
        (SignalState.GREEN, SignalState.YELLOW),
        (SignalState.YELLOW, SignalState.RED),
    }
)


def duration_to_frames(seconds: float, frame_rate: float) -> int:
    """Convert a duration in simulated seconds to a whole number of frames.

    ``max(1, round(seconds * frame_rate))`` (Requirement 11.2). The floor of one
    frame is what makes a Green_Time shorter than a single frame interval still
    produce an observable phase instead of a zero-length one that the sequencer
    would skip past in the same tick it started.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError(f"duration must be a number of seconds, got {seconds!r}")
    if not math.isfinite(seconds) or seconds <= 0.0:
        raise ValueError(f"duration must be a finite number of seconds > 0, got {seconds!r}")
    _check_frame_rate(frame_rate)
    return max(1, round(float(seconds) * float(frame_rate)))


@dataclass(frozen=True)
class Selection:
    """One controller decision: which Approach to serve, and for how long."""

    approach: str
    green_time: float               # simulated seconds
    starvation_override: bool       # Requirement 9.6, recorded in the Run_Log
    selection_score: float          # the Score the decision was read from


@dataclass
class Phase:
    """One recorded GREEN or YELLOW segment of the simulated signal.

    One record per segment rather than per Cycle, so a run that ends mid-YELLOW
    marks only that segment truncated and leaves the GREEN it followed intact as a
    complete measurement. ``cycle_index`` links the two segments of one Cycle.

    Mutable because ``truncated`` is only knowable at the end of the run, when
    :meth:`PhaseSequencer.finalize` learns the final frame index.
    """

    index: int
    cycle_index: int
    approach: str
    state: SignalState
    start_frame: int
    end_frame: int                  # exclusive: the first frame after this segment
    frames: int
    green_time: float               # the Cycle's assigned Green_Time in seconds
    selection_score: float
    selection_score_frame: int      # frame whose Scores produced the selection, -1 if none
    starvation_override: bool
    truncated: bool = False         # Requirement 11.4
    # --- Over-saturation measurement (E5, Li et al. 2025 Eqs. 7-10) -----------
    # Li et al. define a phase as under-saturated exactly when its split is at least
    # the time the queue needs to discharge, and make the shortfall the quantitative
    # level of over-saturation. Recorded here per GREEN segment so a run log can
    # answer "did this green clear its queue, and if not by how much did it fall
    # short" — a question the System previously had no way to answer.
    #
    # ``queue_end`` is -1.0 when unknown (no demand vector was supplied, or this is a
    # YELLOW segment), which is distinguishable from a genuine cleared queue at 0.0.
    queue_start: float = 0.0        # PCE present when the segment began
    queue_end: float = -1.0         # PCE still present when it ended, -1 if unknown
    oversaturation: float = 0.0     # seconds of green the queue still needed
    cleared: bool = False           # the queue emptied within this green
    extended: bool = False          # E6 lengthened this green
    gapped_out: bool = False        # E6 ended this green early

    @property
    def include_in_averages(self) -> bool:
        """Whether per-phase averages may read this segment (Requirement 11.4)."""
        return not self.truncated

    @property
    def oversaturated(self) -> bool:
        """Whether this green ended with its queue not yet discharged (E5)."""
        return self.oversaturation > 0.0

    def as_dict(self) -> dict[str, Any]:
        """The Run_Log representation of this segment (Requirements 9.6, 11.4)."""
        return {
            "index": self.index,
            "cycle_index": self.cycle_index,
            "approach": self.approach,
            "state": self.state.value,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frames": self.frames,
            "green_time": self.green_time,
            "selection_score": self.selection_score,
            "selection_score_frame": self.selection_score_frame,
            "starvation_override": self.starvation_override,
            "truncated": self.truncated,
            "queue_start": self.queue_start,
            "queue_end": self.queue_end,
            "oversaturation": self.oversaturation,
            "cleared": self.cleared,
            "extended": self.extended,
            "gapped_out": self.gapped_out,
        }


@dataclass(frozen=True)
class Transition:
    """One observed Signal_State edge, kept so a run stays auditable."""

    frame_index: int
    approach: str
    from_state: SignalState
    to_state: SignalState


@dataclass(frozen=True)
class PhaseInfo:
    """The signal situation on one frame, as the overlay and Run_Log read it.

    ``approach`` is ``None`` only before the first Cycle has begun; after that
    exactly one Approach is non-RED (Requirements 10.1-10.3).
    """

    frame_index: int
    approach: str | None
    state: SignalState
    phase_index: int                # -1 before the first Cycle
    cycle_index: int                # -1 before the first Cycle
    green_time: float               # the Cycle's assigned Green_Time (Requirement 12.4)
    phase_seconds: float            # full duration of the current segment
    remaining_seconds: float        # of the current segment (Requirement 12.4)
    selection_score: float
    starvation_override: bool
    selection_changed: bool         # selected Approach differs from the previous Cycle (Req 12.7)
    phase_started: bool             # a segment began on this frame


@runtime_checkable
class Controller(Protocol):
    """A signal controller: turns the four Scores into one :class:`Selection`.

    Deliberately narrow. A controller sees Scores and nothing else — no frame
    index, no Signal_State, no timing — because timing and state integrity belong
    to :class:`PhaseSequencer`. That is what lets one sequencer serve both the
    Fixed_Time_Controller and the Adaptive_Controller (Requirement 10.6) and lets
    both be tested with no video, weights, or GPU.
    """

    name: str

    def select(self, scores: Mapping[str, float]) -> Selection:
        """Choose the Approach to serve next and the Green_Time to grant it."""
        ...


#: The Green_Time the baseline grants every Cycle, in simulated seconds
#: (Requirement 7.2). A literal here rather than a Config field on purpose: this is
#: not a tunable of the proposed System but the definition of the baseline it is
#: measured against, and letting it vary would make the two controllers'
#: Evaluation_Metrics incomparable across runs. The Adaptive_Controller's Green_Time
#: comes from ``config.green_time_bands`` instead, so Requirement 8.9 is unaffected.
FIXED_GREEN_TIME: float = 30.0

#: Recorded as the ``selection_score`` of every Fixed_Time selection. The baseline
#: reads no Score at all (Requirement 7.3), so the Run_Log shows a constant rather
#: than a value that would suggest the decision depended on it.
FIXED_SELECTION_SCORE: float = 0.0

#: Slack allowed when comparing a Score lead against the E4 switching margin.
#: Scores are sums of ratios, so a lead that is exactly the margin in decimal terms
#: can come out fractionally under it in binary — ``0.6 - 0.5`` is 0.09999999999999998,
#: which would silently make a documented inclusive bound exclusive. Sized far above
#: double-precision noise on values in 0..1 and far below any margin worth
#: configuring, so it settles representation error without affecting a real decision.
SWITCHING_MARGIN_TOLERANCE: float = 1e-9


class FixedTimeController:
    """The round-robin baseline: North, East, South, West, forever (Req 7.1-7.5).

    Holds one index into :data:`APPROACH_ORDER` and advances it modulo four on each
    :meth:`select`, so the fourth Cycle's West is followed by North (Requirements
    7.1, 7.5). Every Cycle is granted :data:`FIXED_GREEN_TIME` (Requirement 7.2).

    ``scores`` is accepted to satisfy the :class:`Controller` protocol and then
    ignored entirely — not read, not stored, not validated (Requirement 7.3). That
    is the whole point of the baseline: its phase plan is a function of the Cycle
    number alone, so two runs over the same video length produce identical selection
    and Green_Time sequences whatever the traffic did, which is what makes it a
    fair fixed reference for the Adaptive_Controller to be compared against.

    Requirement 7.4 — YELLOW for the configured Yellow_Duration once the Green_Time
    elapses, then RED — needs nothing here: :class:`PhaseSequencer` owns every
    transition and applies it to both controllers (Requirement 10.6).
    """

    name = "fixed"

    def __init__(self) -> None:
        # Starts before the first Approach so the first select yields APPROACH_ORDER[0],
        # keeping "advance, then read" as the single rule for every Cycle.
        self._index: int = -1

    @property
    def cycles_selected(self) -> int:
        """Cycles this controller has decided so far."""
        return self._index + 1

    @property
    def next_approach(self) -> str:
        """The Approach the next :meth:`select` will choose (Requirements 7.1, 7.5)."""
        return APPROACH_ORDER[(self._index + 1) % len(APPROACH_ORDER)]

    def select(self, scores: Mapping[str, float] | None = None) -> Selection:
        """Serve the next Approach in the fixed order for a constant Green_Time.

        ``scores`` is unused (Requirement 7.3); it exists only so this class is
        interchangeable with the Adaptive_Controller behind the same protocol.
        """
        del scores  # Requirement 7.3: the baseline is score-independent.
        self._index += 1
        return Selection(
            approach=APPROACH_ORDER[self._index % len(APPROACH_ORDER)],
            green_time=FIXED_GREEN_TIME,
            starvation_override=False,  # round-robin cannot starve an Approach
            selection_score=FIXED_SELECTION_SCORE,
        )


class AdaptiveController:
    """Queue-aware selection: serve the highest Score, for a Score-derived time.

    Two decisions per Cycle, both driven by the four Scores handed to
    :meth:`select` (Requirement 8.1): *which* Approach to serve, and *how long*.

    **Which.** The Approach holding the maximal Score (Requirement 8.2). Ties are
    resolved by the greatest ``cycles_waited``, then by :data:`APPROACH_ORDER`
    (Requirement 8.3). Selection walks :data:`APPROACH_ORDER` once and keeps a
    strictly better ``(score, cycles_waited)`` key, so the fixed order breaks a
    remaining tie without a separate pass.

    **Unless an Approach is starving.** Before Score is consulted at all,
    :meth:`_starved_approach` checks the ``cycles_waited`` counters
    (Requirement 9.1): once any has reached ``config.starvation_limit``, the
    longest-waiting Approach is served irrespective of every Score, ties again by
    :data:`APPROACH_ORDER` (Requirement 9.3). Counters are updated at the *end* of
    :meth:`select` (Requirement 9.2), so the largest value any counter can hold when
    a Cycle finishes is the limit itself, and the override then serves that Approach
    on the very next Cycle. That ordering is what bounds every counter by the limit
    (Requirement 9.4) and serves every Approach within any window of
    ``starvation_limit + 1`` Cycles (Requirement 9.5). An overridden Cycle still
    receives the Green_Time its own Score earns and is flagged
    ``starvation_override`` so the Run_Log records it (Requirement 9.6).

    **How long.** :meth:`green_time_for` reads ``config.green_time_bands`` — the band
    with the greatest ``min_score`` not exceeding the Score. With the default bands
    that is 30 s below 0.3, 45 s in [0.3, 0.6), and 60 s at or above 0.6
    (Requirements 8.4-8.6). Every threshold and duration comes from configuration
    (Requirement 8.9); there is no Green_Time literal in this class. The bands are
    validated at load as strictly increasing in ``min_score``, non-decreasing in
    ``green_time``, and inside ``[min_green_time, max_green_time]``, so monotonicity
    in Score (Requirement 8.7) and the range bound (Requirement 8.8) hold for any
    tuned band set rather than only the default one.

    Requirement 8.10 — YELLOW for the Yellow_Duration once the Green_Time elapses,
    then RED — needs nothing here: :class:`PhaseSequencer` owns every transition and
    applies it to both controllers (Requirement 10.6).
    """

    name = "adaptive"

    #: Signals to :class:`PhaseSequencer` that :meth:`select` accepts the optional
    #: per-Approach demand vector the discharge-limited Green_Time needs (E3).
    #: Absent on any other controller, including test doubles, so the sequencer
    #: keeps calling those with Scores alone.
    accepts_demands = True

    def __init__(self, config: Config) -> None:
        if not config.green_time_bands:
            raise ValueError(
                "config.green_time_bands is empty; the adaptive controller has no "
                "Green_Time to assign (Requirement 8.9)"
            )
        self._config = config
        self._bands = tuple(config.green_time_bands)
        self._starvation_limit = int(config.starvation_limit)
        # Requirement 9.1: consecutive Cycles since each Approach was last served.
        # All zero at the start of a run, so the first Cycle is decided on Score alone.
        self._cycles_waited: dict[str, int] = {name: 0 for name in APPROACH_ORDER}
        self._cycles_selected: int = 0

        # --- Wei-derived extensions, all neutral by default -------------------
        self._min_green = float(config.min_green_time)
        self._max_green = float(config.max_green_time)
        self._use_discharge_green = bool(getattr(config, "use_discharge_green", False))
        self._saturation_flow_rate = float(getattr(config, "saturation_flow_rate", 0.5))
        self._switching_margin = float(getattr(config, "switching_margin", 0.0))
        self._green_rate_limit = float(getattr(config, "green_rate_limit", 0.0))
        # Li et al. (2025) Eq. 7's T_loss. The Yellow_Duration is this System's only
        # changeover, so it is the lost time a phase must pay for.
        self._lost_time = float(config.yellow_duration)
        # E4 state: the Approach served last Cycle (the incumbent the switching
        # margin protects) and the Green_Time it was granted (what the rate limit
        # measures the next Green_Time against).
        self._last_selected: str | None = None
        self._last_green: float | None = None

    # -- read-only state ---------------------------------------------------

    @property
    def cycles_waited(self) -> dict[str, int]:
        """The Cycles_Waited counter of every Approach (Requirement 9.1)."""
        return dict(self._cycles_waited)

    @property
    def cycles_selected(self) -> int:
        """Cycles this controller has decided so far."""
        return self._cycles_selected

    @property
    def starvation_limit(self) -> int:
        """The configured Starvation_Limit this controller enforces (Req 9.7)."""
        return self._starvation_limit

    # -- the decision ------------------------------------------------------

    def select(
        self,
        scores: Mapping[str, float],
        demands: Mapping[str, float] | None = None,
    ) -> Selection:
        """Choose the Approach to serve next and the Green_Time to grant it.

        Reads the Score of all four Approaches (Requirement 8.1) and validates them
        through the same check the sequencer uses, so a malformed Score map is
        rejected at the point of use rather than silently ranked.

        ``demands`` is the optional per-Approach queue demand in PCE units used by
        the discharge-limited Green_Time (E3). It is ignored unless
        ``config.use_discharge_green`` is set, so the default path is the configured
        Score bands exactly as before.

        Counter maintenance happens last, after the decision is made
        (Requirement 9.2): a counter incremented before selection would let an
        Approach's own wait influence the Cycle it is already being considered for.
        """
        _check_scores(scores)

        approach = self._starved_approach()
        starvation_override = approach is not None
        if approach is None:
            approach = self._highest_scoring_approach(scores)

        score = float(scores[approach])
        # The same Green_Time rule runs whether or not the choice was overridden, so
        # an overridden Approach still receives the Green_Time its own state earns
        # (Requirement 9.6).
        green_time = self._green_time_for_selection(approach, score, demands)

        self._advance_counters(approach)
        self._last_selected = approach
        self._last_green = green_time
        self._cycles_selected += 1
        return Selection(
            approach=approach,
            green_time=green_time,
            starvation_override=starvation_override,
            selection_score=score,
        )

    # -- Green_Time -------------------------------------------------------

    def _green_time_for_selection(
        self,
        approach: str,
        score: float,
        demands: Mapping[str, float] | None,
    ) -> float:
        """The Green_Time to grant ``approach`` this Cycle.

        Either the configured Score band (the base rule) or the discharge time the
        measured queue needs (E3), then bounded by the E4 rate limit. Both
        extensions end in :meth:`_bounded_green`, so Requirement 8.8's
        ``[min_green_time, max_green_time]`` range holds on every path.
        """
        if self._use_discharge_green and demands is not None and approach in demands:
            green_time = self.discharge_green_time(demands[approach])
        else:
            green_time = self.green_time_for(score)
        return self._rate_limited_green(green_time)

    def discharge_green_time(self, demand: float) -> float:
        """The Green_Time needed to discharge ``demand`` PCE of queue (E3).

        ``demand / saturation_flow_rate + lost_time``, bounded by the configured
        Green_Time range. Adapted from Wei et al. (2025) Eq. 16, where the desired
        cumulative outflow is capped at ``N_out + q_s · b · Δt`` — green is granted in
        proportion to what can actually be discharged at the saturation flow rate,
        rather than read from a threshold band.

        The point of this over the bands: a band table encodes an assumption about
        how much traffic a "high" Score represents, which does not transfer between
        cameras or approaches. A discharge time is in physical units on both sides —
        PCE divided by PCE per second — so it transfers wherever
        ``saturation_flow_rate`` has been measured.

        **The lost-time term comes from Li et al. (2025) Eq. 7**, where the queue
        discharging time is ``ε/W + ε/V_f + T_loss``. The first version of this
        method charged only the discharge and omitted the changeover entirely, which
        is why it settled on the minimum green every cycle and spent close to a
        quarter of the run on intergreen: a short green looked free when it was not.
        Charging the phase for its own lost time is the correct accounting and makes
        very short greens compare unfavourably against fewer, longer ones.
        ``yellow_duration`` is used as the lost time because it is the only
        changeover this System models.
        """
        if isinstance(demand, bool) or not isinstance(demand, (int, float)):
            raise ValueError(f"demand must be a number of PCE, got {demand!r}")
        if not math.isfinite(demand) or demand < 0.0:
            raise ValueError(f"demand must be a finite value >= 0, got {demand!r}")
        discharge = float(demand) / self._saturation_flow_rate
        return self._bounded_green(discharge + self._lost_time)

    def green_time_for(self, score: float) -> float:
        """The configured Green_Time for ``score`` (Requirements 8.4-8.6, 8.9).

        The band with the greatest ``min_score`` not exceeding ``score``. Boundaries
        are inclusive lower bounds, so a Score of exactly 0.3 earns the 0.3 band's
        45 s, not the band below it. Bands are strictly increasing in ``min_score``,
        so the first band that does not apply ends the search; the first band's
        ``min_score`` is validated to be 0, so some band always applies to a Score in
        0..1.
        """
        green_time = self._bands[0].green_time
        for band in self._bands:
            if band.min_score > score:
                break
            green_time = band.green_time
        return float(green_time)

    def _rate_limited_green(self, green_time: float) -> float:
        """Bound the change in Green_Time between consecutive Cycles (E4).

        The discrete analogue of the ``Δb`` penalty in Wei et al. (2025) Eqs. 19-20,
        which adds the variation of the green-time decision to the MPC objective so
        the control plan does not oscillate between steps. They report visibly
        smoother plans than the benchmark and argue the smoothness itself benefits
        safety and fuel consumption.

        Inert when ``green_rate_limit`` is 0 (the default) or on the first Cycle,
        which has no previous Green_Time to move away from.
        """
        if self._green_rate_limit <= 0.0 or self._last_green is None:
            return self._bounded_green(green_time)
        low = self._last_green - self._green_rate_limit
        high = self._last_green + self._green_rate_limit
        return self._bounded_green(min(max(green_time, low), high))

    def _bounded_green(self, green_time: float) -> float:
        """Confine a Green_Time to the configured range (Requirement 8.8)."""
        return float(min(max(green_time, self._min_green), self._max_green))

    # -- selection helpers -------------------------------------------------

    def _highest_scoring_approach(self, scores: Mapping[str, float]) -> str:
        """The maximal-Score Approach, ties by wait then order (Reqs 8.2, 8.3).

        Iterating in :data:`APPROACH_ORDER` and replacing the incumbent only on a
        strictly greater ``(score, cycles_waited)`` key makes the fixed order the
        final tiebreak by construction (Requirement 8.3).
        """
        best_approach = APPROACH_ORDER[0]
        best_key = (float(scores[best_approach]), self._cycles_waited[best_approach])
        for name in APPROACH_ORDER[1:]:
            key = (float(scores[name]), self._cycles_waited[name])
            if key > best_key:
                best_approach, best_key = name, key
        return self._apply_switching_margin(best_approach, scores)

    def _apply_switching_margin(
        self, challenger: str, scores: Mapping[str, float]
    ) -> str:
        """Keep serving the incumbent unless ``challenger`` beats it by the margin.

        The selection-side half of E4. Wei et al. (2025) penalize the *variation* of
        the control decision between steps (Eqs. 19-20) because optimizing each step
        independently produces oscillating plans; the base controller here re-runs
        ``argmax(score)`` every Cycle with no memory, so two Approaches with nearly
        equal Scores can trade green back and forth. Requiring a challenger to lead
        by ``switching_margin`` makes near-ties hold their assignment instead.

        Inert when ``switching_margin`` is 0 (the default) or on the first Cycle.

        This deliberately sits *after* the starvation check in :meth:`select`, never
        before it, so it cannot delay a starvation override: the counters still
        advance every Cycle, and an Approach at the Starvation_Limit is chosen
        before Scores or margins are consulted at all. Requirements 9.4 and 9.5
        therefore hold unchanged with the margin enabled.
        """
        incumbent = self._last_selected
        if (
            self._switching_margin <= 0.0
            or incumbent is None
            or incumbent == challenger
            or incumbent not in scores
        ):
            return challenger
        lead = float(scores[challenger]) - float(scores[incumbent])
        # Inclusive bound: a lead of exactly the margin switches. The tolerance is
        # what makes that true of the floating-point values actually compared.
        return (
            challenger
            if lead >= self._switching_margin - SWITCHING_MARGIN_TOLERANCE
            else incumbent
        )

    def _starved_approach(self) -> str | None:
        """The Approach that must be served irrespective of Score, if any.

        ``None`` while every counter is below the Starvation_Limit, in which case the
        Cycle is decided on Score alone (Requirement 9.2's precondition). Once one or
        more counters have reached the limit, the Approach holding the greatest
        ``cycles_waited`` — ties broken by :data:`APPROACH_ORDER` — is returned and
        :meth:`select` serves it whatever the Scores say (Requirement 9.3).

        The maximum is taken over all four Approaches rather than only the ones at or
        past the limit, which is the same Approach: nothing can exceed a counter that
        already reached the limit by more than the limit itself, and the maximal
        counter is by definition at least as large as any other that reached it.
        Walking :data:`APPROACH_ORDER` and replacing the incumbent only on a strictly
        greater counter makes the fixed order the tiebreak by construction.
        """
        starved = APPROACH_ORDER[0]
        longest_wait = self._cycles_waited[starved]
        for name in APPROACH_ORDER[1:]:
            if self._cycles_waited[name] > longest_wait:
                starved, longest_wait = name, self._cycles_waited[name]
        return starved if longest_wait >= self._starvation_limit else None

    def _advance_counters(self, selected: str) -> None:
        """Reset the served Approach's counter and increment the rest (Req 9.2)."""
        for name in APPROACH_ORDER:
            if name == selected:
                self._cycles_waited[name] = 0
            else:
                self._cycles_waited[name] += 1


class PhaseSequencer:
    """Owns phase timing and Signal_State integrity for one run.

    Drives one :class:`Controller`, converts its Green_Time decisions into whole
    frame counts, and advances a single active phase through the legal cycle
    RED→GREEN→YELLOW→RED (Requirements 10.1-10.5, 11.2, 11.4).

    A YELLOW that expires is followed by the next Cycle's GREEN *within the same
    tick*. Without that, the frame between two Cycles would read all-RED and
    Requirement 10.1's "exactly one Approach GREEN at every point after the first
    Cycle has started" would fail on it. The outgoing Approach still passes through
    RED on that frame — the edge is recorded — it just holds it for no frames.
    """

    def __init__(self, controller: Controller, config: Config, frame_rate: float) -> None:
        _check_frame_rate(frame_rate)
        if not hasattr(controller, "select"):
            raise ValueError(
                f"controller {controller!r} does not implement select(scores) -> Selection"
            )

        self._controller = controller
        self._config = config
        self._frame_rate = float(frame_rate)
        # Resolved once: the Yellow_Duration never varies within a run (Req 8.10).
        self._yellow_frames = duration_to_frames(config.yellow_duration, self._frame_rate)

        # --- E5/E6: over-saturation measurement and queue-clearance gap-out ----
        # Green bounds in whole frames, so gap-out cannot end a green below the
        # minimum and extension cannot carry it past the maximum (Requirement 8.8
        # holds for the adjusted green as well as the assigned one).
        self._min_green_frames = duration_to_frames(config.min_green_time, self._frame_rate)
        self._max_green_frames = duration_to_frames(config.max_green_time, self._frame_rate)
        self._use_gap_out = bool(getattr(config, "use_gap_out", False))
        self._saturation_flow_rate = float(getattr(config, "saturation_flow_rate", 0.5))
        # A queue at or below this PCE is treated as cleared. Small rather than zero
        # so floating-point queue magnitudes that ought to be empty read as empty.
        self._clear_epsilon = 1e-9
        # The per-Approach queue demand (PCE) from the most recent tick, i.e. the
        # queue as it stood one frame before now. Read to time gap-out/extension and
        # to record the queue at the moment a green ends.
        self._latest_demands: dict[str, float] | None = None

        # The one triple every Signal_State is derived from.
        self._active_approach: str | None = None
        self._active_state: SignalState = SignalState.RED
        self._phase_end_frame: int = 0

        # Current Cycle bookkeeping, all set together in _begin_cycle.
        self._selection: Selection | None = None
        self._cycle_index: int = -1
        self._selection_changed: bool = False
        self._previous_approach: str | None = None
        self._phase_frames: int = 0
        self._phase_started_on: int | None = None

        self._phases: list[Phase] = []
        self._transitions: list[Transition] = []
        self._last_frame_index: int | None = None
        self._finalized: bool = False

    # -- read-only state ---------------------------------------------------

    @property
    def controller_name(self) -> str:
        """The active controller's name, as the overlay header shows it (Req 12.5)."""
        return getattr(self._controller, "name", type(self._controller).__name__)

    @property
    def frame_rate(self) -> float:
        """The video frame rate every duration in this run is converted against."""
        return self._frame_rate

    @property
    def yellow_frames(self) -> int:
        """The Yellow_Duration of this run in frames (Requirement 11.2)."""
        return self._yellow_frames

    @property
    def active_approach(self) -> str | None:
        """The one non-RED Approach, or ``None`` before the first Cycle (Req 10.5)."""
        return self._active_approach

    @property
    def active_state(self) -> SignalState:
        """The state of :attr:`active_approach`; RED while there is none."""
        return self._active_state

    @property
    def phase_end_frame(self) -> int:
        """First frame index at which the active segment is over (exclusive)."""
        return self._phase_end_frame

    @property
    def cycle_count(self) -> int:
        """Cycles begun so far."""
        return self._cycle_index + 1

    @property
    def phases(self) -> tuple[Phase, ...]:
        """Every recorded segment, including any truncated one (Requirement 11.4)."""
        return tuple(self._phases)

    @property
    def completed_phases(self) -> tuple[Phase, ...]:
        """The segments per-phase averages may read (Requirement 11.4)."""
        return tuple(phase for phase in self._phases if phase.include_in_averages)

    @property
    def transitions(self) -> tuple[Transition, ...]:
        """Every Signal_State edge taken so far, in order (Requirement 10.4)."""
        return tuple(self._transitions)

    @property
    def is_finalized(self) -> bool:
        """Whether :meth:`finalize` has run, after which ticking is an error."""
        return self._finalized

    def signal_states(self) -> dict[str, SignalState]:
        """The Signal_State of all four Approaches on the current frame.

        Derived from the single active triple, so exactly one Approach can be
        non-RED (Requirements 10.1-10.3) and all four read RED before the first
        Cycle (Requirement 10.5).
        """
        return {
            name: self._active_state if name == self._active_approach else SignalState.RED
            for name in APPROACH_ORDER
        }

    # -- the per-frame step ------------------------------------------------

    def tick(
        self,
        frame_index: int,
        scores: Mapping[str, float],
        demands: Mapping[str, float] | None = None,
    ) -> PhaseInfo:
        """Advance the signal to ``frame_index`` and describe the resulting phase.

        Called once per processed frame, before the Metrics_Engine update, so the
        Signal_State gating Waiting_Time on frame ``n`` is the state in force on
        frame ``n``. ``scores`` are the Scores available at that point, which is
        frame ``n-1``'s set — frame ``n``'s do not exist yet — and are read only
        when a Cycle begins (Requirement 8.1).

        ``frame_index`` must be strictly greater than the previous call's, which is
        what makes the Simulated_Clock derived from it strictly increasing
        (Requirement 11.3).
        """
        if self._finalized:
            raise ValueError(
                f"sequencer was finalized at frame {self._last_frame_index}; "
                f"cannot tick frame {frame_index}"
            )
        self._check_frame_index(frame_index)
        _check_scores(scores)

        if demands is not None:
            self._latest_demands = dict(demands)

        # E6: before the expiry check, let the live queue reshape the active green —
        # end it early once its queue has cleared, or hold it open while the queue
        # remains, within the configured green bounds. Measurement-only runs
        # (use_gap_out off) skip this and keep the assigned duration.
        if (
            self._use_gap_out
            and self._active_approach is not None
            and self._active_state is SignalState.GREEN
            and self._latest_demands is not None
        ):
            self._apply_gap_out(frame_index)

        self._phase_started_on = None
        # At most one segment can expire per frame, since every segment spans at
        # least one frame; the loop is written generally so that stays true of the
        # code rather than of a comment.
        while self._active_approach is None or frame_index >= self._phase_end_frame:
            if self._active_approach is None:
                self._begin_cycle(frame_index, scores, demands)
            elif self._active_state is SignalState.GREEN:
                self._begin_yellow(frame_index)
            else:
                self._end_yellow(frame_index)
                self._begin_cycle(frame_index, scores, demands)

        self._last_frame_index = frame_index
        return self._phase_info(frame_index)

    def finalize(self, last_frame_index: int) -> Phase | None:
        """Close the run at ``last_frame_index``, truncating any open segment.

        A segment whose ``end_frame`` lies beyond ``last_frame_index + 1`` never
        ran to its assigned duration, so it is flagged ``truncated`` and dropped
        from :attr:`completed_phases` and therefore from every per-phase average
        (Requirement 11.4). A segment that ends exactly at ``last_frame_index + 1``
        completed and is kept.

        The Signal_State is left as it was: GREEN→RED is not a legal edge
        (Requirement 10.4), and the run is over, so there is no frame for a
        tidied-up RED to apply to. Idempotent, and returns the truncated segment or
        ``None``.
        """
        if self._finalized:
            return next((p for p in reversed(self._phases) if p.truncated), None)

        if isinstance(last_frame_index, bool) or not isinstance(last_frame_index, int):
            raise ValueError(f"last_frame_index must be an integer, got {last_frame_index!r}")
        if self._last_frame_index is not None and last_frame_index < self._last_frame_index:
            raise ValueError(
                f"last_frame_index {last_frame_index} precedes the last ticked frame "
                f"{self._last_frame_index}"
            )

        self._finalized = True
        if self._last_frame_index is None:
            # Finalized without ever ticking: no phase ever began, nothing to mark.
            return None
        self._last_frame_index = last_frame_index

        # E5: a green that was still running at the end of the run never had its
        # ending queue recorded by _begin_yellow, so record it here before the phase
        # is (possibly) flagged truncated. A truncated phase is excluded from
        # per-phase averages, but its over-saturation is still worth having in the
        # log as the state the run ended in.
        if self._phases and self._phases[-1].state is SignalState.GREEN:
            self._finalize_green_metrics()

        truncated: Phase | None = None
        if self._phases and self._phases[-1].end_frame > last_frame_index + 1:
            truncated = self._phases[-1]
            truncated.truncated = True
        return truncated

    # -- transitions, one private helper per legal edge --------------------

    def _begin_cycle(
        self,
        frame_index: int,
        scores: Mapping[str, float],
        demands: Mapping[str, float] | None = None,
    ) -> None:
        """Select the next Approach and take its RED→GREEN edge.

        ``demands`` is forwarded only to a controller that advertises
        ``accepts_demands``, so controllers written against the two-argument
        :class:`Controller` protocol — including the fixed-time baseline and every
        test double — keep being called with Scores alone.
        """
        if demands is not None and getattr(self._controller, "accepts_demands", False):
            selection = self._controller.select(dict(scores), dict(demands))
        else:
            selection = self._controller.select(dict(scores))
        self._check_selection(selection)

        self._cycle_index += 1
        self._selection = selection
        self._selection_changed = selection.approach != self._previous_approach
        self._previous_approach = selection.approach

        self._start_segment(
            approach=selection.approach,
            to_state=SignalState.GREEN,
            frame_index=frame_index,
            frames=duration_to_frames(selection.green_time, self._frame_rate),
        )
        # E5: the queue this green is meant to discharge, in PCE, at the moment it
        # begins. Recorded whenever a demand vector is available, independently of
        # whether gap-out is controlling the green (measurement is not control).
        if self._latest_demands is not None:
            self._phases[-1].queue_start = float(
                self._latest_demands.get(selection.approach, 0.0)
            )

    def _begin_yellow(self, frame_index: int) -> None:
        """Take the GREEN→YELLOW edge of the Approach being served (Req 8.10)."""
        assert self._active_approach is not None
        # E5: close out the green's over-saturation measurement before it becomes
        # the previous segment, using the queue as it stood on this frame.
        self._finalize_green_metrics()
        self._start_segment(
            approach=self._active_approach,
            to_state=SignalState.YELLOW,
            frame_index=frame_index,
            frames=self._yellow_frames,
        )

    def _end_yellow(self, frame_index: int) -> None:
        """Take the YELLOW→RED edge, leaving no Approach active."""
        assert self._active_approach is not None
        self._transition(self._active_approach, SignalState.RED, frame_index)
        self._active_approach = None
        self._active_state = SignalState.RED

    def _start_segment(
        self,
        *,
        approach: str,
        to_state: SignalState,
        frame_index: int,
        frames: int,
    ) -> None:
        """Record and enter one GREEN or YELLOW segment of ``frames`` frames.

        The edge is taken *before* ``approach`` becomes active, so
        :meth:`_transition` reads the true outgoing state — RED for an Approach that
        is not the active one — instead of one this method supplied.
        """
        assert self._selection is not None
        self._transition(approach, to_state, frame_index)
        self._active_approach = approach

        self._phase_frames = frames
        self._phase_end_frame = frame_index + frames
        self._phase_started_on = frame_index
        self._phases.append(
            Phase(
                index=len(self._phases),
                cycle_index=self._cycle_index,
                approach=approach,
                state=to_state,
                start_frame=frame_index,
                end_frame=self._phase_end_frame,
                frames=frames,
                green_time=self._selection.green_time,
                selection_score=self._selection.selection_score,
                # The Scores that produced the selection are the previous frame's;
                # the first Cycle has no previous frame, recorded as -1.
                selection_score_frame=frame_index - 1,
                starvation_override=self._selection.starvation_override,
            )
        )

    def _apply_gap_out(self, frame_index: int) -> None:
        """Reshape the active green from its live queue (E6, Li et al. 2025).

        Li et al. classify a phase as under- or over-saturated by comparing its split
        against the time its queue needs to discharge. The same comparison, made
        online against the observed queue rather than offline against a predicted
        one, gives an adaptive green:

        * **Gap-out.** Once the Approach's Queue_Region has emptied, further green is
          wasted, so the green ends as soon as the minimum green has been served —
          the end frame is brought forward to the current frame, bounded below by
          ``start + min_green_frames``.
        * **Extension.** While the queue has not cleared and the assigned green is
          about to expire, the end frame is pushed one frame ahead, up to
          ``start + max_green_frames``.

        Both directions stay inside the configured green range, so Requirement 8.8
        holds for the reshaped green exactly as for the assigned one. Assumes the
        caller has checked there is an active GREEN and a demand vector.
        """
        assert self._latest_demands is not None and self._active_approach is not None
        green = self._phases[-1]
        start = green.start_frame
        queue = float(self._latest_demands.get(self._active_approach, 0.0))
        min_end = start + self._min_green_frames
        max_end = start + self._max_green_frames

        if queue <= self._clear_epsilon:
            # Cleared: end at the current frame, but never before the minimum green.
            new_end = max(min_end, frame_index)
            if new_end < self._phase_end_frame:
                self._resize_green(green, start, new_end)
                green.gapped_out = True
        elif self._phase_end_frame < max_end:
            # Not cleared and near the assigned end: hold it open one more frame,
            # capped at the maximum green. The guard keeps this inert early in the
            # green, where frame_index + 1 is still well before the assigned end.
            new_end = min(max_end, frame_index + 1)
            if new_end > self._phase_end_frame:
                self._resize_green(green, start, new_end)
                green.extended = True

    def _resize_green(self, green: Phase, start: int, new_end: int) -> None:
        """Move the active green's end frame, keeping the record consistent."""
        self._phase_end_frame = new_end
        self._phase_frames = new_end - start
        green.end_frame = new_end
        green.frames = new_end - start

    def _finalize_green_metrics(self) -> None:
        """Record the ending queue and over-saturation of the active green (E5).

        ``oversaturation`` is the leftover queue expressed as the extra green it
        would have needed at the saturation flow rate — Li et al.'s ``η = u − φ`` in
        seconds. ``queue_end`` stays -1 when no demand vector was ever supplied, so a
        measurement-free run is distinguishable from one whose queue genuinely
        cleared to 0.
        """
        green = self._phases[-1]
        if green.state is not SignalState.GREEN:
            return
        if self._latest_demands is None or green.approach not in self._latest_demands:
            return
        remaining = float(self._latest_demands[green.approach])
        green.queue_end = remaining
        green.cleared = remaining <= self._clear_epsilon
        green.oversaturation = (
            0.0 if green.cleared else remaining / self._saturation_flow_rate
        )

    def _transition(self, approach: str, to_state: SignalState, frame_index: int) -> None:
        """Move ``approach`` to ``to_state``, rejecting any illegal edge (Req 10.4).

        The single write point for :attr:`_active_state`. Every edge the System can
        take passes through here, so :data:`LEGAL_TRANSITIONS` is the complete
        description of the state machine rather than a claim about it.
        """
        from_state = self._active_state if approach == self._active_approach else SignalState.RED
        edge = (from_state, to_state)
        if edge not in LEGAL_TRANSITIONS:
            raise AssertionError(
                f"illegal signal transition for {approach} at frame {frame_index}: "
                f"{from_state.value} to {to_state.value}"
            )
        self._active_state = to_state
        self._transitions.append(
            Transition(
                frame_index=frame_index,
                approach=approach,
                from_state=from_state,
                to_state=to_state,
            )
        )

    # -- reporting and validation -----------------------------------------

    def _phase_info(self, frame_index: int) -> PhaseInfo:
        """Describe the active phase on ``frame_index`` for overlay and Run_Log."""
        assert self._selection is not None and self._active_approach is not None
        remaining_frames = max(0, self._phase_end_frame - frame_index)
        return PhaseInfo(
            frame_index=frame_index,
            approach=self._active_approach,
            state=self._active_state,
            phase_index=len(self._phases) - 1,
            cycle_index=self._cycle_index,
            green_time=self._selection.green_time,
            phase_seconds=self._phase_frames / self._frame_rate,
            remaining_seconds=remaining_frames / self._frame_rate,
            selection_score=self._selection.selection_score,
            starvation_override=self._selection.starvation_override,
            selection_changed=self._selection_changed,
            phase_started=self._phase_started_on == frame_index,
        )

    def _check_frame_index(self, frame_index: int) -> None:
        """Require a non-negative, strictly increasing frame index (Req 11.3)."""
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise ValueError(f"frame_index must be an integer, got {frame_index!r}")
        if frame_index < 0:
            raise ValueError(f"frame_index must be >= 0, got {frame_index}")
        if self._last_frame_index is not None and frame_index <= self._last_frame_index:
            raise ValueError(
                f"frame_index must strictly increase over a run: got {frame_index} "
                f"after {self._last_frame_index}"
            )

    def _check_selection(self, selection: Any) -> None:
        """Reject a controller decision the sequencer cannot act on."""
        if not isinstance(selection, Selection):
            raise ValueError(
                f"{self.controller_name}.select must return a Selection, got {selection!r}"
            )
        if selection.approach not in APPROACH_ORDER:
            raise ValueError(
                f"{self.controller_name} selected unknown approach "
                f"{selection.approach!r}; configured approaches are "
                f"{', '.join(APPROACH_ORDER)}"
            )
        green_time = selection.green_time
        if isinstance(green_time, bool) or not isinstance(green_time, (int, float)):
            raise ValueError(
                f"{self.controller_name} green_time must be a number of seconds, "
                f"got {green_time!r}"
            )
        if not math.isfinite(green_time) or green_time <= 0.0:
            raise ValueError(
                f"{self.controller_name} green_time must be a finite duration > 0, "
                f"got {green_time!r}"
            )


def _check_scores(scores: Mapping[str, float]) -> None:
    """Require a finite Score for all four Approaches (Requirement 8.1).

    Shared by the sequencer and the Adaptive_Controller so a malformed Score map is
    rejected with the same message wherever it enters the control path.
    """
    if not isinstance(scores, Mapping):
        raise ValueError(
            f"scores must map approach names to Scores, got {type(scores).__name__}"
        )
    missing = [name for name in APPROACH_ORDER if name not in scores]
    if missing:
        raise ValueError(f"scores is missing approach(es) {', '.join(missing)}")
    for name in APPROACH_ORDER:
        value = scores[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} score must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"{name} score must be finite, got {value!r}")


def _check_frame_rate(frame_rate: float) -> None:
    """Require a positive finite frame rate, the divisor of the Simulated_Clock."""
    if isinstance(frame_rate, bool) or not isinstance(frame_rate, (int, float)):
        raise ValueError(f"frame_rate must be a number, got {frame_rate!r}")
    if not math.isfinite(frame_rate) or frame_rate <= 0.0:
        raise ValueError(f"frame_rate must be a finite value > 0, got {frame_rate!r}")


__all__ = [
    "APPROACH_ORDER",
    "FIXED_GREEN_TIME",
    "FIXED_SELECTION_SCORE",
    "LEGAL_TRANSITIONS",
    "AdaptiveController",
    "Controller",
    "FixedTimeController",
    "Phase",
    "PhaseInfo",
    "PhaseSequencer",
    "Selection",
    "SignalState",
    "Transition",
    "duration_to_frames",
]
