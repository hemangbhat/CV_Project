"""Metrics_Engine — per-approach Vehicle_Count, Queue_Length, and their ratios.

This module turns the :class:`~src.lane_analysis.AssignedTrack` list of one frame
into four :class:`ApproachMetrics` values, the numbers every later stage consumes:
the Score_Calculator reads the two normalized ratios, the controllers read the
Score derived from them, and the Run_Log records the raw counts
(Requirements 5.1 to 5.4, 5.7, 5.9).

Three choices shape the arithmetic:

**Counts are set cardinalities.** Vehicle_Count is ``len`` of a ``set`` of
Track_IDs, so a track reported twice in one frame cannot inflate the count
(Requirement 5.1), and Queue_Length is the cardinality of the queueing subset of
that same set (Requirement 5.3). Because the queueing set is built as a subset
rather than counted independently, ``queue_length <= vehicle_count``
(Requirement 5.8) holds structurally rather than by a check.

**One clamp, one divisor.** Both ratios pass through the single :func:`clamp`
helper, which is what gives Requirement 5.7 one implementation point. PCE
weighting (Requirement 5.9) changes only the density *numerator*: the
``saturation_count`` divisor and the clamp are shared by both code paths, so
enabling weighting cannot take the density outside 0..1 however large the
configured weights are.

**Unassigned tracks are dropped here.** The Approach_Assigner returns every track,
including those outside all four ROIs, so that they stay visible to the overlay
and the Run_Log; this module skips them, which is where Requirement 4.5's
exclusion from measurement actually happens.

**Two measures accumulate across frames.** Waiting_Time and Vehicles_Served are
not readable off a single frame, so they live on the engine rather than in
:class:`ApproachMetrics`. Both are gated by the Signal_State vector in force on
the frame being measured, which is why the pipeline ticks the Phase_Sequencer
*before* calling :meth:`MetricsEngine.update`: waiting accrues only while an
Approach is not GREEN (Requirements 5.5, 5.6), and a vehicle counts as served
only when it leaves the Queue_Region of an Approach that is GREEN
(Requirement 13.2). Because a GREEN Approach's tracks are skipped entirely rather
than incremented by zero, Requirement 5.6 holds without a separate hold step, and
``waiting_times`` is non-decreasing for every Track_ID by construction — nothing
in this module ever subtracts from it.
"""

from __future__ import annotations

import math
import warnings
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from src.config import APPROACH_NAMES, Config
from src.errors import ConfigError
from src.lane_analysis import AssignedTrack, build_approach_axes


#: The Signal_State vocabulary this module gates on. Held here as strings so the
#: engine does not import the Signal_Controller (the dependency runs the other
#: way): any value equal to one of these, including a ``str``-valued enum member,
#: is accepted.
GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
SIGNAL_STATES = (GREEN, YELLOW, RED)


def queue_tail_reach(positions: Iterable[float], max_gap: float) -> float:
    """Return the tail of the contiguous queue that starts at the stop line.

    ``positions`` are the upstream axis fractions (0 = stop line, 1 = far edge of the
    visible approach) of the vehicles judged stopped on one Approach. A queue is a
    chain of stopped vehicles beginning at the stop line, so walking upstream from 0
    the chain continues while consecutive stopped vehicles are at most ``max_gap``
    apart, and the reach is the position of its last member. A stopped vehicle beyond
    a larger gap is not part of the queue (a parked car, a detection far upstream,
    a slow vehicle misjudged as stopped) and cannot set the reach.

    With ``max_gap <= 0`` the legacy definition is returned: the furthest stopped
    vehicle, contiguous or not. Shared by the vision Metrics_Engine and the closed-loop
    simulation sensor so both measure reach with one definition.
    """
    ordered = sorted(float(p) for p in positions)
    if not ordered:
        return 0.0
    if max_gap <= 0.0:
        return clamp(ordered[-1])
    tail = 0.0
    previous = 0.0
    for position in ordered:
        if position - previous > max_gap:
            break
        tail = previous = position
    return clamp(tail)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Return ``value`` confined to ``[low, high]``.

    The single clamping point for both normalized measures (Requirement 5.7).
    ``low`` and ``high`` default to the unit range because that is the range every
    normalized measure in this project reports.
    """
    if low > high:
        raise ValueError(f"clamp bounds are inverted: low={low!r} exceeds high={high!r}")
    if not math.isfinite(value):
        raise ValueError(f"clamp received a non-finite value: {value!r}")
    return low if value < low else high if value > high else value


@dataclass(frozen=True)
class ApproachMetrics:
    """The measured traffic condition of one Approach on one frame.

    ``vehicle_count`` and ``queue_length`` are the raw integer counts and the two
    ratios are their normalized forms in 0..1 (Requirement 5.7). The invariants
    are checked on construction: they hold by construction in
    :meth:`MetricsEngine.update`, and the check is what stops a hand-built value
    in a test or a Run_Log replay from carrying an impossible combination
    downstream.
    """

    approach: str
    vehicle_count: int          # >= 0
    vehicle_density: float      # 0..1
    queue_length: int           # >= 0, <= vehicle_count
    normalized_queue: float     # 0..1
    # Optional anticipatory measure (predictive extension): the normalized count
    # of vehicles present in the ROI but not yet queueing, i.e. approaching the
    # stop line. Defaults to 0.0 so base metrics and existing constructions are
    # unaffected; only the predictive scoring path reads it.
    normalized_arrival: float = 0.0   # 0..1
    # --- Optional measures for the Wei-derived control extensions -------------
    # ``queue_pce`` is the Queue_Length in PCE units before normalization: the
    # summed PCE weight of the queueing tracks when ``use_pce_queue`` is enabled,
    # and plain ``float(queue_length)`` otherwise. It is the *magnitude* the
    # discharge-limited Green_Time divides by the saturation flow rate (E3), which
    # normalized_queue cannot supply because it saturates at 1.0.
    #
    # ``normalized_spillback`` is the share of the Approach holding vehicles that
    # are stopped but *outside* the Queue_Region — evidence the queue has grown
    # past the stop-line area (E2). It is a subset of the vehicles counted by
    # ``normalized_arrival``, so a configuration giving both terms weight counts
    # those vehicles twice; use one or the other (see
    # report/paper_limitations_analysis.md).
    #
    # Both default to 0.0, so every hand-built value and every base run is
    # unchanged. The engine always sets them.
    queue_pce: float = 0.0            # >= 0, in PCE units
    normalized_spillback: float = 0.0  # 0..1
    # E8 (short-term queue forecast, the primary Wei-derived enhancement): the
    # queue this Approach is projected to hold ``forecast_horizon`` seconds from now,
    # normalized like ``normalized_queue``. Filled by :class:`QueuePredictor` from
    # the recent trend of ``normalized_queue``; 0.0 on a metrics value the predictor
    # never touched, so base scoring is unaffected.
    normalized_forecast: float = 0.0   # 0..1
    # E9 (queue reach): how far back along the visible approach the stopped traffic
    # extends, as a fraction of the ROI's own upstream extent — 0 at the stop line,
    # 1 at the far edge of the ROI. A *spatial* queue measure rather than a count,
    # so it neither saturates at a hand-set capacity nor discards vehicle position.
    # See :class:`~src.lane_analysis.ApproachAxis` for the geometry.
    queue_reach: float = 0.0          # 0..1
    # Spillback risk (S): the storage occupancy this Approach is *projected* to reach,
    # obtained by extrapolating ``queue_reach`` forward at its current rate of spatial
    # growth. 1.0 means the queue is projected to fill the entire visible approach,
    # i.e. to spill back out of it. Distinct from the two neighbouring measures:
    # ``normalized_spillback`` is overflow that has *already happened*, and
    # ``normalized_forecast`` projects the count-based queue *inside* the Queue_Region
    # (so it saturates); this projects the non-saturating spatial occupancy instead.
    # Filled by :class:`QueuePredictor`; 0.0 when it never ran.
    spillback_risk: float = 0.0       # 0..1

    def __post_init__(self) -> None:
        for field, value in (
            ("vehicle_count", self.vehicle_count),
            ("queue_length", self.queue_length),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{self.approach} {field} must be an integer, got {value!r}"
                )
            if value < 0:
                raise ValueError(f"{self.approach} {field} must be >= 0, got {value!r}")

        if self.queue_length > self.vehicle_count:
            raise ValueError(
                f"{self.approach} queue_length {self.queue_length} exceeds "
                f"vehicle_count {self.vehicle_count} (Requirement 5.8)"
            )

        for field, value in (
            ("vehicle_density", self.vehicle_density),
            ("normalized_queue", self.normalized_queue),
            ("normalized_arrival", self.normalized_arrival),
            ("normalized_spillback", self.normalized_spillback),
            ("normalized_forecast", self.normalized_forecast),
            ("queue_reach", self.queue_reach),
            ("spillback_risk", self.spillback_risk),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{self.approach} {field} must lie in 0..1, got {value!r}"
                )

        # A PCE magnitude, so unbounded above but never negative.
        if not math.isfinite(self.queue_pce) or self.queue_pce < 0.0:
            raise ValueError(
                f"{self.approach} queue_pce must be a finite value >= 0, got "
                f"{self.queue_pce!r}"
            )


@dataclass(frozen=True)
class FrameMeasurement:
    """Everything measured on one frame, as the Results_Store records it.

    Held as one value rather than four parallel dictionaries so a frame record in
    the Run_Log cannot be assembled from mismatched frames.
    """

    frame_index: int
    simulated_time: float
    metrics: dict[str, ApproachMetrics]
    scores: dict[str, float]
    signal_states: dict[str, str]


class MetricsEngine:
    """Computes per-approach measurements frame by frame (Requirements 5.1-5.9).

    Holds the configuration it needs plus the cross-frame state Waiting_Time and
    Vehicles_Served accumulate into; the per-frame measures themselves are a pure
    function of one frame's assigned tracks. One engine belongs to one run,
    because the accumulators carry that run's history.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        # Resolved once: every frame divides by these, and the validated Approach
        # set means the lookup cannot fail later in the run.
        self._approaches = tuple(config.approach(name) for name in APPROACH_NAMES)
        # E9: the upstream axis of each Approach, built once from the configured
        # polygons. Used to express a stopped vehicle's position as a fraction of how
        # far back the visible approach extends.
        self._axes = build_approach_axes(config)
        # An ill-conditioned axis still produces bounded, finite numbers, so nothing
        # downstream fails — which is exactly why it needs to be said out loud. A
        # reversed axis would make a nearly-empty approach read as almost full, and the
        # only symptom would be an odd green-time pattern buried in a Run_Log. Warned
        # about only when a measure that depends on the axis direction is switched on,
        # so a density-only baseline run stays quiet.
        if getattr(config, "use_queue_reach", False) or getattr(
            config, "use_spillback_risk", False
        ):
            suspect = [
                f"{name} (conditioning {axis.conditioning:.3f}, "
                f"{axis.direction_source})"
                for name, axis in self._axes.items()
                if not axis.well_conditioned
            ]
            if suspect:
                warnings.warn(
                    "approach axis direction is unreliable for: "
                    + "; ".join(suspect)
                    + ". Spatial queue reach and spillback risk may be inverted on "
                    "these approaches. Draw each queue_region as a strip across the "
                    "stop line, or run `python -m src.main calibrate-axes --write ...` "
                    "to measure the direction from vehicle motion.",
                    RuntimeWarning,
                    stacklevel=2,
                )
        # Cross-frame state. Waiting_Time is keyed by Track_ID across the whole
        # run, not per Approach, because a Track_ID is unique for the run.
        self._waiting_times: dict[int, float] = {}
        # Frame n-1's queueing Track_IDs per Approach: departures from these sets
        # while the Approach is GREEN are what Vehicles_Served counts.
        self._previous_queueing: dict[str, set[int]] = {
            name: set() for name in self.approach_names
        }
        self._vehicles_served = 0
        # Frame n-1's reference point per Track_ID, used to tell a stopped track
        # from a moving one for the spillback measure (E2). Rebuilt from the
        # current frame's tracks on every update, so it stays bounded by the live
        # track set rather than growing over the run.
        self._previous_points: dict[int, tuple[float, float]] = {}
        # Windowed stopped test (audit fix W5): recent reference points per Track_ID,
        # oldest first, bounded by the window. Rebuilt from the live track set each
        # frame like ``_previous_points``. Empty and unused in legacy mode.
        self._window_seconds = float(getattr(config, "stopped_window_seconds", 0.0))
        self._speed_ratio = float(getattr(config, "stopped_speed_ratio", 0.2))
        self._tail_gap = float(getattr(config, "queue_tail_gap", 0.0))
        self._history: dict[int, deque[tuple[float, float]]] = {}
        # Stop counting (E7). Average stops is the headline measure of effectiveness
        # in Li et al. (2025) and the one their model improves most consistently, and
        # this project could not report it at all. A stop is a moving-to-stopped
        # transition, so it needs the previous frame's stopped set as well as the
        # current one; both come from the same displacement test the spillback
        # measure uses, so the two measures cannot disagree about what "stopped"
        # means. Counted per Approach, and only for assigned tracks
        # (Requirement 4.5).
        self._stopped_previous: set[int] = set()
        self._stops: dict[str, int] = {name: 0 for name in self.approach_names}
        # Distinct assigned Track_IDs seen over the run, the denominator of
        # average stops per vehicle. Bounded by the number of vehicles in the run.
        self._seen_tracks: set[int] = set()

    @property
    def approach_names(self) -> tuple[str, ...]:
        """The Approach names in the fixed order every result dict is keyed in."""
        return tuple(approach.name for approach in self._approaches)

    @property
    def waiting_times(self) -> dict[int, float]:
        """Accumulated Waiting_Time in simulated seconds per Track_ID (Req 5.5).

        A copy, so a caller holding the result — the Run_Log writer, or the
        Evaluation_Harness averaging over it — cannot mutate the run's state. A
        Track_ID appears only once it has waited at all: tracks that were never
        queueing on a non-GREEN Approach are absent rather than present at 0.
        """
        return dict(self._waiting_times)

    @property
    def vehicles_served(self) -> int:
        """Distinct Track_IDs that left a Queue_Region on GREEN (Req 13.2)."""
        return self._vehicles_served

    @property
    def stops(self) -> dict[str, int]:
        """Moving-to-stopped transitions per Approach over the run so far (E7).

        Li et al. (2025) minimise total stops as one of their two objectives and
        report average stops as a measure of effectiveness; a copy is returned so a
        caller cannot mutate the run's state.
        """
        return dict(self._stops)

    @property
    def total_stops(self) -> int:
        """Stops summed over the four Approaches (E7)."""
        return sum(self._stops.values())

    @property
    def seen_vehicles(self) -> int:
        """Distinct assigned Track_IDs seen over the run (the average-stops base)."""
        return len(self._seen_tracks)

    @property
    def average_stops(self) -> float:
        """Stops per distinct assigned vehicle, Li et al.'s headline MOE (E7).

        Zero when no assigned track has been seen, rather than undefined: a run over
        empty footage produced no stops, which is a measurement rather than a gap.
        """
        if not self._seen_tracks:
            return 0.0
        return self._total_stops_float / float(len(self._seen_tracks))

    @property
    def _total_stops_float(self) -> float:
        return float(sum(self._stops.values()))

    def update(
        self,
        assigned: Iterable[AssignedTrack],
        signal_states: Mapping[str, Any],
        dt: float,
    ) -> dict[str, ApproachMetrics]:
        """Measure one frame and return its four :class:`ApproachMetrics`.

        ``assigned`` is the Approach_Assigner's output for this frame; tracks with
        ``approach is None`` are excluded (Requirement 4.5). ``signal_states`` is
        the Signal_State in force on this frame, and ``dt`` its simulated duration
        in seconds (``1 / frame_rate``) — together they gate the Waiting_Time and
        Vehicles_Served accumulation, which this call also advances.
        """
        states = self._check_signal_states(signal_states)
        self._check_dt(dt)

        # track_id -> vehicle_class per approach: the mapping deduplicates ids
        # (Requirement 5.1) while keeping the class each id needs for PCE
        # weighting (Requirement 5.9).
        classes: dict[str, dict[int, str]] = {name: {} for name in self.approach_names}
        queueing: dict[str, set[int]] = {name: set() for name in self.approach_names}
        # Tracks whose reference point barely moved since the previous frame (E2).
        stopped: dict[str, set[int]] = {name: set() for name in self.approach_names}
        current_points: dict[int, tuple[float, float]] = {}
        # E9: the furthest-upstream fraction reached by any stopped vehicle on each
        # Approach. Taken over *stopped* vehicles rather than queueing ones, so a
        # queue that has backed up beyond the Queue_Region still extends the reach —
        # which is precisely the case a count inside that region cannot see.
        stopped_positions: dict[str, list[float]] = {
            name: [] for name in self.approach_names
        }
        window_frames = self._window_frames(dt)
        current_history: dict[int, deque[tuple[float, float]]] = {}

        # Every track that is stopped on this frame, assigned or not. Kept whole so
        # the moving-to-stopped test on the next frame is not confused by a track
        # that merely moved between ROIs.
        stopped_now: set[int] = set()

        for track in assigned:
            # Recorded before the assignment check so a track that crosses into an
            # ROI still has a usable previous position on the frame it arrives.
            point = (float(track.ref_point[0]), float(track.ref_point[1]))
            if window_frames:
                was_stopped = self._is_stopped_windowed(
                    track.track_id, point, float(getattr(track, "box_height", 0.0)),
                    dt, window_frames, current_history,
                )
            else:
                was_stopped = self._is_stopped(track.track_id, point)
            current_points[track.track_id] = point
            if was_stopped:
                stopped_now.add(track.track_id)

            approach = track.approach
            if approach is None:
                continue
            if approach not in classes:
                raise ConfigError(
                    f"track {track.track_id} is assigned to unknown approach "
                    f"{approach!r}; configured approaches are "
                    f"{', '.join(self.approach_names)}"
                )
            classes[approach].setdefault(track.track_id, track.vehicle_class)
            if track.is_queueing:
                queueing[approach].add(track.track_id)
            if was_stopped:
                stopped[approach].add(track.track_id)
                # E9: record where this stopped vehicle sits along the upstream
                # axis; the reach is derived from all of them after the loop.
                axis = self._axes.get(approach)
                if axis is not None:
                    stopped_positions[approach].append(axis.fraction(point))

            # E7: one stop per moving-to-stopped transition. A track already stopped
            # on the previous frame is not counted again, so a vehicle standing for
            # a hundred frames contributes one stop, not a hundred.
            self._seen_tracks.add(track.track_id)
            if was_stopped and track.track_id not in self._stopped_previous:
                self._stops[approach] += 1

        self._accumulate(queueing, states, dt)
        self._previous_points = current_points
        self._stopped_previous = stopped_now
        self._history = current_history
        reach = {
            name: queue_tail_reach(stopped_positions[name], self._tail_gap)
            for name in self.approach_names
        }

        return {
            approach.name: self._measure(
                approach.name,
                classes[approach.name],
                queueing[approach.name],
                stopped[approach.name],
                reach[approach.name],
            )
            for approach in self._approaches
        }

    # -- internals ---------------------------------------------------------

    def _accumulate(
        self,
        queueing: Mapping[str, set[int]],
        states: Mapping[str, str],
        dt: float,
    ) -> None:
        """Advance Waiting_Time and Vehicles_Served for one frame.

        Both measures read the same deduplicated queueing sets the counts are
        taken from, so a track cannot wait twice or be served twice in one frame.
        """
        for name, queued in queueing.items():
            is_green = states[name] == GREEN

            if is_green:
                # Requirement 5.6: no track of a GREEN Approach is touched, so
                # every waiting time on it is held exactly as it was.
                # Requirement 13.2: a track that was queueing here last frame and
                # is not queueing here now has left the Queue_Region on GREEN.
                self._vehicles_served += len(self._previous_queueing[name] - queued)
            else:
                for track_id in queued:
                    # Requirement 5.5: one frame's simulated duration per frame
                    # spent queueing on a non-GREEN Approach.
                    self._waiting_times[track_id] = (
                        self._waiting_times.get(track_id, 0.0) + dt
                    )

            self._previous_queueing[name] = set(queued)

    def _measure(
        self,
        name: str,
        classes: Mapping[int, str],
        queueing: set[int],
        stopped: set[int],
        queue_reach: float = 0.0,
    ) -> ApproachMetrics:
        """Build one Approach's metrics from its deduplicated track sets."""
        approach = self._config.approach(name)
        vehicle_count = len(classes)
        queue_length = len(queueing)

        numerator = (
            self._pce_numerator(classes)
            if self._config.use_pce_weighting
            else float(vehicle_count)
        )

        # Approaching vehicles: present in the ROI but not yet queueing. Scaled by
        # the same saturation_count and passed through the same clamp as density,
        # so it stays in 0..1 by the shared rule (predictive extension).
        approaching = max(vehicle_count - queue_length, 0)

        # E1: the Queue_Length numerator, PCE-weighted when configured. The
        # ``queue_capacity`` divisor and the clamp are shared with the unweighted
        # path exactly as they are for density, so enabling weighting cannot take
        # normalized_queue outside 0..1 however large the configured weights are.
        queue_pce = (
            self._pce_subset(classes, queueing)
            if getattr(self._config, "use_pce_queue", False)
            else float(queue_length)
        )

        # E2: stopped but not queueing — the queue has grown beyond the
        # Queue_Region. Scaled by saturation_count and clamped like density, since
        # it measures occupancy of the approach rather than of the stop-line area.
        spilled = stopped - queueing
        spillback = (
            self._pce_subset(classes, spilled)
            if getattr(self._config, "use_pce_queue", False)
            else float(len(spilled))
        )

        return ApproachMetrics(
            approach=name,
            vehicle_count=vehicle_count,
            # Same divisor and same clamp in both density modes (Reqs 5.2, 5.9).
            vehicle_density=clamp(numerator / approach.saturation_count),
            queue_length=queue_length,
            normalized_queue=clamp(queue_pce / approach.queue_capacity),
            normalized_arrival=clamp(approaching / approach.saturation_count),
            queue_pce=queue_pce,
            normalized_spillback=clamp(spillback / approach.saturation_count),
            queue_reach=clamp(queue_reach),
        )

    def _is_stopped(self, track_id: int, point: tuple[float, float]) -> bool:
        """Whether ``track_id`` barely moved since the previous frame (E2).

        A track seen for the first time has no previous position, so it is
        reported as *not* stopped. That is the conservative direction: a vehicle
        entering the ROI is counted as spillback only once it has been observed
        standing still across two frames, so newly-detected vehicles cannot inflate
        the measure.
        """
        previous = self._previous_points.get(track_id)
        if previous is None:
            return False
        threshold = float(getattr(self._config, "stopped_displacement", 2.0))
        dx = point[0] - previous[0]
        dy = point[1] - previous[1]
        return math.hypot(dx, dy) < threshold

    def _window_frames(self, dt: float) -> int:
        """Frames in the stopped-test window, or 0 in legacy per-frame mode."""
        if self._window_seconds <= 0.0 or dt <= 0.0:
            return 0
        return max(2, int(round(self._window_seconds / dt)))

    def _is_stopped_windowed(
        self,
        track_id: int,
        point: tuple[float, float],
        box_height: float,
        dt: float,
        window_frames: int,
        current_history: dict[int, deque[tuple[float, float]]],
    ) -> bool:
        """Whether ``track_id`` moved slower than the threshold over the window.

        Speed is net displacement across the window divided by the elapsed time, in
        box heights per second. Net displacement over ~1 s averages out the
        frame-to-frame box jitter that made the per-frame test flip, and dividing by
        the box height puts a distant (small) vehicle and a near (large) one on the
        same physical scale, so a far vehicle moving at speed is no longer read as
        stopped merely because it covers few pixels per frame.

        A track needs at least half a window of history before it can be judged
        stopped; until then it is reported as moving, the same conservative
        direction the legacy test takes for a track's first frame.
        """
        previous = self._history.get(track_id)
        history: deque[tuple[float, float]] = deque(
            previous if previous is not None else (), maxlen=window_frames
        )
        stopped = False
        if len(history) >= max(1, window_frames // 2) and box_height > 0.0:
            oldest = history[0]
            elapsed = len(history) * dt
            displacement = math.hypot(point[0] - oldest[0], point[1] - oldest[1])
            speed = displacement / elapsed / box_height
            stopped = speed < self._speed_ratio
        history.append(point)
        current_history[track_id] = history
        return stopped

    def _pce_numerator(self, classes: Mapping[int, str]) -> float:
        """Sum the configured PCE weight of each distinct track (Req 5.9)."""
        return self._pce_subset(classes, classes.keys())

    def _pce_subset(self, classes: Mapping[int, str], ids: Iterable[int]) -> float:
        """Sum the configured PCE weight of the named subset of ``classes``.

        One weighting routine for density, queue, and spillback, so a class whose
        weight is missing fails the same way wherever it is first met rather than
        being silently skipped in one term and rejected in another.
        """
        weights = self._config.pce_weights
        total = 0.0
        for track_id in ids:
            vehicle_class = classes[track_id]
            if vehicle_class not in weights:
                raise ConfigError(
                    f"no pce_weights entry for vehicle class {vehicle_class!r} "
                    f"(track {track_id}); configured classes are "
                    f"{', '.join(sorted(weights))}"
                )
            total += weights[vehicle_class]
        return total

    def _check_signal_states(self, signal_states: Mapping[str, Any]) -> dict[str, str]:
        """Normalize the frame's Signal_States to plain strings.

        Gating Waiting_Time on a missing or misspelled state would silently
        accumulate against the wrong Signal_State, so both are errors rather than
        defaults. Enum members are accepted by reading ``value``, which keeps this
        module free of any import from the Signal_Controller.
        """
        if not isinstance(signal_states, Mapping):
            raise ValueError(
                "signal_states must map approach names to Signal_States, got "
                f"{type(signal_states).__name__}"
            )
        missing = [name for name in self.approach_names if name not in signal_states]
        if missing:
            raise ValueError(
                f"signal_states is missing approach(es) {', '.join(missing)}"
            )

        normalized: dict[str, str] = {}
        for name in self.approach_names:
            state = signal_states[name]
            value = getattr(state, "value", state)
            if not isinstance(value, str) or value not in SIGNAL_STATES:
                raise ValueError(
                    f"{name} signal state {state!r} is not one of "
                    f"{', '.join(SIGNAL_STATES)}"
                )
            normalized[name] = value
        return normalized

    @staticmethod
    def _check_dt(dt: float) -> None:
        """Require a finite, non-negative frame duration in simulated seconds."""
        if isinstance(dt, bool) or not isinstance(dt, (int, float)):
            raise ValueError(f"dt must be a number of seconds, got {dt!r}")
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError(f"dt must be a finite duration >= 0, got {dt!r}")


# ---------------------------------------------------------------------------
# Queue_Predictor (E8) — short-term queue forecast, the primary Wei-derived
# enhancement.
# ---------------------------------------------------------------------------


class QueuePredictor:
    """Forecasts each Approach's queue a few seconds ahead from its recent trend.

    This is the vision-only, single-junction analogue of the *predictive* half of
    Wei et al. (2025). Their MPC-Q predicts network queue propagation over a horizon
    to grant green before a queue spills back; it needs a link transmission model, a
    demand-prediction module, and a solver. None of that is available or appropriate
    here. What *is* available is the per-frame ``normalized_queue`` this System
    already measures from YOLO + ByteTrack, and a queue that is growing shows it in
    that series before it saturates.

    So the forecast is deliberately simple: keep a sliding window of each Approach's
    recent ``normalized_queue``, fit its rate of change (slope per second) by least
    squares, and project the current value forward by ``horizon`` seconds, clamped to
    0..1:

        forecast = clamp(current + slope_per_second * horizon)

    A growing queue forecasts higher than it stands now, so the controller can be
    given green *before* the stop-line area fills — the spillback-avoidance intent of
    Wei et al., reduced to a trend line. A shrinking or steady queue forecasts at or
    below its current value, so the predictor never inflates a clearing Approach.

    Stateful across frames and owned by one run, like :class:`MetricsEngine`. It is
    a no-op unless ``config.use_forecast`` is set; the pipeline only constructs it
    then, and even if constructed its output is inert until ``forecast_weight`` (the
    Score) or the forecast demand path is enabled.
    """

    def __init__(self, config: Config, frame_rate: float) -> None:
        if not math.isfinite(frame_rate) or frame_rate <= 0.0:
            raise ValueError(f"frame_rate must be a positive number, got {frame_rate!r}")
        self._config = config
        self._dt = 1.0 / float(frame_rate)
        self._horizon = float(getattr(config, "forecast_horizon_seconds", 3.0))
        window = int(getattr(config, "forecast_window_frames", 15))
        # At least two samples are needed for a slope; the window bounds how far back
        # the trend is measured, so an old surge does not bias a present estimate.
        self._window = max(2, window)
        self._history: dict[str, deque[float]] = {
            name: deque(maxlen=self._window) for name in APPROACH_NAMES
        }
        # Spillback risk projects the *spatial* occupancy instead of the count-based
        # queue, so it needs its own history of ``queue_reach`` and its own horizon.
        self._risk_horizon = float(getattr(config, "risk_horizon_seconds", 5.0))
        self._reach_history: dict[str, deque[float]] = {
            name: deque(maxlen=self._window) for name in APPROACH_NAMES
        }

    def predict(
        self, metrics: Mapping[str, ApproachMetrics]
    ) -> dict[str, ApproachMetrics]:
        """Return ``metrics`` with each Approach's ``normalized_forecast`` filled.

        Appends this frame's ``normalized_queue`` to the per-Approach window, then
        projects it forward. Returns new :class:`ApproachMetrics` values (the inputs
        are frozen), so the caller scores and times against the forecast without the
        base measurement being altered.
        """
        _check_metrics_complete(metrics)
        out: dict[str, ApproachMetrics] = {}
        for name in APPROACH_NAMES:
            m = metrics[name]

            history = self._history[name]
            history.append(float(m.normalized_queue))
            forecast = self._project(history, m.normalized_queue)

            reach_history = self._reach_history[name]
            reach_history.append(float(m.queue_reach))
            risk = self._project_risk(reach_history, m.queue_reach)

            out[name] = replace(
                m, normalized_forecast=forecast, spillback_risk=risk
            )
        return out

    def _project_risk(self, reach_history: "deque[float]", current: float) -> float:
        """Return the projected storage occupancy of one Approach (spillback risk).

        ``risk = clamp( reach + d(reach)/dt * risk_horizon )``.

        ``queue_reach`` is the fraction of the visible approach that stopped traffic
        currently occupies, so its time derivative is the speed at which the queue is
        *extending backwards*. Extrapolating it over ``risk_horizon_seconds`` answers
        the question the base controller cannot ask: *is this approach on course to run
        out of storage?* A risk of 1.0 means the queue is projected to fill the whole
        visible approach — the point at which it spills out of it.

        Choosing ``queue_reach`` as the occupancy basis rather than
        ``normalized_queue`` is deliberate. The count-based queue saturates at its
        configured capacity, so once it reads 1.0 its derivative is 0 and a
        still-worsening approach would be projected as stable. The spatial reach keeps
        rising while the queue physically extends, so its derivative stays meaningful
        exactly in the regime the risk term is meant to detect.

        A steady or shrinking queue projects at or below its current occupancy, so the
        term never inflates an approach that is holding or clearing.
        """
        slope = self._slope_per_second(reach_history)
        return clamp(float(current) + slope * self._risk_horizon)

    def _project(self, history: "deque[float]", current: float) -> float:
        """Project ``current`` forward by the horizon using the window's slope."""
        slope = self._slope_per_second(history)
        return clamp(float(current) + slope * self._horizon)

    def _slope_per_second(self, history: "deque[float]") -> float:
        """Least-squares slope of the windowed samples, in units per second.

        Zero until at least two samples exist, and zero when the window is flat, so
        a steady queue is projected as itself. Time is measured in seconds via the
        frame interval, so the slope is comparable across frame rates.
        """
        n = len(history)
        if n < 2:
            return 0.0
        # x in seconds relative to the window start; y the normalized queue samples.
        xs = [i * self._dt for i in range(n)]
        ys = list(history)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom <= 0.0:
            return 0.0
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        return num / denom

    def forecast_pce(self, name: str, metrics: ApproachMetrics) -> float:
        """The forecast queue of ``name`` in PCE units, for the discharge demand.

        ``normalized_forecast`` scaled back by the Approach's ``queue_capacity``, so
        the discharge-limited Green_Time (E3) can be timed for where the queue is
        heading rather than only where it stands.
        """
        return float(metrics.normalized_forecast) * self._config.approach(name).queue_capacity


# ---------------------------------------------------------------------------
# Score_Calculator (Requirements 6.1, 6.3, 6.4, 6.8)
# ---------------------------------------------------------------------------


def compute_score(metrics: ApproachMetrics, alpha: float) -> float:
    """Return the queue-aware Score of one Approach (Requirement 6.1).

    ``Score = alpha * vehicle_density + (1 - alpha) * normalized_queue``: a convex
    combination of two values :class:`ApproachMetrics` already confines to 0..1,
    with a weight :func:`~src.config.validate_config` already confines to 0..1, so
    the result lies in 0..1 without a clamp of its own (Requirement 6.2). The two
    endpoints fall out of the same expression rather than out of special cases —
    ``alpha == 1`` leaves the density and ``alpha == 0`` leaves the normalized
    queue (Requirements 6.3, 6.4) — and monotonicity in each argument follows from
    both coefficients being non-negative (Requirements 6.5, 6.6).

    ``alpha`` is a caller-supplied experimental parameter, always
    :attr:`Config.alpha` in the running System (Requirement 6.8): no tunable of
    this calculation is written as a literal here. It is validated on the way in
    because a Score computed from an out-of-range weight would leave 0..1 and be
    silently comparable against Scores that had not.
    """
    weight = _check_alpha(alpha)
    return weight * metrics.vehicle_density + (1.0 - weight) * metrics.normalized_queue


def compute_score_predictive(
    metrics: ApproachMetrics, alpha: float, gamma: float
) -> float:
    """Return the anticipatory Score of one Approach (predictive extension).

    ``Score = (1 - gamma) * base + gamma * normalized_arrival`` where ``base`` is
    the queue-aware Score of :func:`compute_score` and ``normalized_arrival`` is
    the share of the Approach carrying vehicles that are present but not yet
    queueing. ``gamma`` is the predictive weight in 0..1: at ``gamma == 0`` this
    reduces exactly to :func:`compute_score`, so enabling the extension with a
    zero weight changes nothing. Because ``base`` and ``normalized_arrival`` both
    lie in 0..1 and ``gamma`` is a convex weight, the result stays in 0..1 and the
    Green_Time bands remain valid.

    Rationale: the base controller (and Raza's density scheme) react to the
    *current* stopped/present state. The arrival term gives weight to approaches
    with vehicles still moving toward the stop line, so green can be granted in
    anticipation rather than only after a queue has formed.
    """
    return compute_score_weighted(metrics, alpha, gamma, 0.0)


def compute_score_weighted(
    metrics: ApproachMetrics,
    alpha: float,
    gamma: float,
    delta: float,
    omega: float = 0.0,
    psi: float = 0.0,
    rho: float = 0.0,
) -> float:
    """Return the Score with the arrival, spillback, and forecast terms blended in.

    ``Score = (1 - gamma - delta - omega) * base
              + gamma * arrival + delta * spillback + omega * forecast``
    where ``base`` is the queue-aware Score of :func:`compute_score`. Every term
    lies in 0..1 and the coefficients are non-negative and sum to 1, so the result
    is a convex combination and stays in 0..1 — which is what keeps the Green_Time
    bands applicable (Requirement 6.2). At ``gamma == delta == omega == 0`` this is
    exactly :func:`compute_score`, so all three extensions are inert when unweighted.

    ``delta`` is the spillback weight (E2): the share of the Score given to
    vehicles stopped inside the Approach but outside its Queue_Region — a queue
    already backing up past the stop line.

    ``omega`` is the forecast weight (E8, the primary Wei-derived enhancement): the
    share of the Score given to the queue this Approach is *projected* to hold a few
    seconds ahead (:class:`QueuePredictor`). Where ``delta`` reacts to a queue that
    has *already* overflowed, ``omega`` acts on one that is *about to* — the
    spillback-avoidance intent of Wei et al.'s predictive control, reduced to a
    measured trend rather than a link-transmission model.

    ``gamma + delta + omega <= 1`` is enforced by :func:`~src.config.load_config`;
    it is re-checked here because this function is also called directly.
    """
    weight = _check_alpha(alpha)
    share = _check_gamma(gamma)
    spill = _check_delta(delta)
    fore = _check_omega(omega)
    reach = _check_psi(psi)
    risk = _check_rho(rho)
    total = share + spill + fore + reach + risk
    if total > 1.0:
        raise ValueError(
            f"predictive weight {share!r}, spillback weight {spill!r}, forecast "
            f"weight {fore!r}, queue-reach weight {reach!r} and spillback-risk weight "
            f"{risk!r} must sum to at most 1.0, got {total!r}"
        )
    base = weight * metrics.vehicle_density + (1.0 - weight) * metrics.normalized_queue
    return (
        (1.0 - total) * base
        + share * metrics.normalized_arrival
        + spill * metrics.normalized_spillback
        + fore * metrics.normalized_forecast
        + reach * metrics.queue_reach
        + risk * metrics.spillback_risk
    )


def compute_scores(
    metrics: Mapping[str, ApproachMetrics], alpha: float
) -> dict[str, float]:
    """Score every Approach in ``metrics`` under the same ``alpha``.

    Keyed by the same Approach names :meth:`MetricsEngine.update` returns, which is
    what the controllers index by. The full Approach set is required: a controller
    selecting a maximum over three Scores would silently never serve the fourth.
    """
    if not isinstance(metrics, Mapping):
        raise ValueError(
            "metrics must map approach names to ApproachMetrics, got "
            f"{type(metrics).__name__}"
        )
    missing = [name for name in APPROACH_NAMES if name not in metrics]
    if missing:
        raise ValueError(f"metrics is missing approach(es) {', '.join(missing)}")

    return {name: compute_score(metrics[name], alpha) for name in APPROACH_NAMES}


def compute_config_scores(
    metrics: Mapping[str, ApproachMetrics], config: Config
) -> dict[str, float]:
    """Score every Approach using the run's configured Alpha (Requirement 6.8).

    The form the Pipeline calls, so the weight reaching the controllers is the one
    recorded in the Run_Log and nothing else. When the optional predictive
    extension is enabled (``config.use_predictive``), the anticipatory arrival
    term is blended in at ``config.predictive_weight``; otherwise the base
    queue-aware Score is used unchanged.
    """
    gamma = config.predictive_weight if getattr(config, "use_predictive", False) else 0.0
    delta = float(getattr(config, "spillback_weight", 0.0))
    omega = config.forecast_weight if getattr(config, "use_forecast", False) else 0.0
    psi = (
        config.queue_reach_weight if getattr(config, "use_queue_reach", False) else 0.0
    )
    rho = (
        config.spillback_risk_weight
        if getattr(config, "use_spillback_risk", False)
        else 0.0
    )

    if gamma <= 0.0 and delta <= 0.0 and omega <= 0.0 and psi <= 0.0 and rho <= 0.0:
        return compute_scores(metrics, config.alpha)

    _check_metrics_complete(metrics)
    return {
        name: compute_score_weighted(
            metrics[name], config.alpha, gamma, delta, omega, psi, rho
        )
        for name in APPROACH_NAMES
    }


def compute_config_demands(
    metrics: Mapping[str, ApproachMetrics], config: Config
) -> dict[str, float]:
    """Return each Approach's queue demand in PCE units, for the discharge model.

    The magnitude the discharge-limited Green_Time (E3) divides by the saturation
    flow rate. ``normalized_queue`` cannot serve this purpose: it is clamped to 1.0,
    so every queue at or beyond capacity would ask for the same green.

    When the spillback term is weighted, the vehicles standing upstream of the
    Queue_Region are part of what the green has to discharge, so they are added to
    the demand. That keeps the quantity being timed equal to the quantity being
    scored (Wei et al. Eq. 16 times the traffic actually present to serve, not a
    band read off a threshold).
    """
    _check_metrics_complete(metrics)
    include_spillback = float(getattr(config, "spillback_weight", 0.0)) > 0.0
    include_forecast = bool(getattr(config, "use_forecast", False))

    demands: dict[str, float] = {}
    for name in APPROACH_NAMES:
        approach_metrics = metrics[name]
        demand = float(approach_metrics.queue_pce)
        if include_spillback:
            # normalized_spillback is a share of saturation_count, so scale it back
            # into the same PCE units queue_pce is expressed in.
            demand += approach_metrics.normalized_spillback * config.approach(
                name
            ).saturation_count
        if include_forecast:
            # E8: size the green for where the queue is heading, not only where it
            # stands. normalized_forecast is a share of queue_capacity, so scale it
            # back into PCE and take the larger of the current and forecast demand —
            # a growing queue asks for more green now, a clearing one is left alone.
            forecast_pce = (
                approach_metrics.normalized_forecast
                * config.approach(name).queue_capacity
            )
            demand = max(demand, forecast_pce)
        demands[name] = demand
    return demands


def _check_metrics_complete(metrics: Mapping[str, ApproachMetrics]) -> None:
    """Require a metrics mapping covering every Approach."""
    if not isinstance(metrics, Mapping):
        raise ValueError(
            "metrics must map approach names to ApproachMetrics, got "
            f"{type(metrics).__name__}"
        )
    missing = [name for name in APPROACH_NAMES if name not in metrics]
    if missing:
        raise ValueError(f"metrics is missing approach(es) {', '.join(missing)}")


def _check_alpha(alpha: float) -> float:
    """Return ``alpha`` as a float, rejecting anything outside 0..1 (Req 6.7)."""
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError(f"alpha must be a number in 0..1, got {alpha!r}")
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must lie in 0..1, got {alpha!r}")
    return float(alpha)


def _check_gamma(gamma: float) -> float:
    """Return ``gamma`` (predictive weight) as a float in 0..1."""
    if isinstance(gamma, bool) or not isinstance(gamma, (int, float)):
        raise ValueError(f"predictive weight must be a number in 0..1, got {gamma!r}")
    if not math.isfinite(gamma) or not 0.0 <= gamma <= 1.0:
        raise ValueError(f"predictive weight must lie in 0..1, got {gamma!r}")
    return float(gamma)


def _check_delta(delta: float) -> float:
    """Return ``delta`` (spillback weight) as a float in 0..1."""
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        raise ValueError(f"spillback weight must be a number in 0..1, got {delta!r}")
    if not math.isfinite(delta) or not 0.0 <= delta <= 1.0:
        raise ValueError(f"spillback weight must lie in 0..1, got {delta!r}")
    return float(delta)


def _check_omega(omega: float) -> float:
    """Return ``omega`` (forecast weight, E8) as a float in 0..1."""
    if isinstance(omega, bool) or not isinstance(omega, (int, float)):
        raise ValueError(f"forecast weight must be a number in 0..1, got {omega!r}")
    if not math.isfinite(omega) or not 0.0 <= omega <= 1.0:
        raise ValueError(f"forecast weight must lie in 0..1, got {omega!r}")
    return float(omega)


def _check_psi(psi: float) -> float:
    """Return ``psi`` (queue-reach weight, E9) as a float in 0..1."""
    if isinstance(psi, bool) or not isinstance(psi, (int, float)):
        raise ValueError(f"queue-reach weight must be a number in 0..1, got {psi!r}")
    if not math.isfinite(psi) or not 0.0 <= psi <= 1.0:
        raise ValueError(f"queue-reach weight must lie in 0..1, got {psi!r}")
    return float(psi)


def _check_rho(rho: float) -> float:
    """Return ``rho`` (spillback-risk weight) as a float in 0..1."""
    if isinstance(rho, bool) or not isinstance(rho, (int, float)):
        raise ValueError(f"spillback-risk weight must be a number in 0..1, got {rho!r}")
    if not math.isfinite(rho) or not 0.0 <= rho <= 1.0:
        raise ValueError(f"spillback-risk weight must lie in 0..1, got {rho!r}")
    return float(rho)


__all__ = [
    "GREEN",
    "RED",
    "SIGNAL_STATES",
    "YELLOW",
    "ApproachMetrics",
    "FrameMeasurement",
    "MetricsEngine",
    "QueuePredictor",
    "clamp",
    "queue_tail_reach",
    "compute_config_demands",
    "compute_config_scores",
    "compute_score",
    "compute_score_predictive",
    "compute_score_weighted",
    "compute_scores",
]
