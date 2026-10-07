"""Tracker — persistent vehicle identities and their trajectory contract.

This module owns the *shape* of a track: a Track_ID, an axis-aligned box, one of
the four Vehicle_Classes, and an ordered trajectory of recent reference points
(Requirements 3.1, 3.5). Everything downstream — the Approach_Assigner, the
Metrics_Engine, the Signal_Overlay — reads only these fields, so the concrete
tracker is interchangeable.

:class:`Tracker` is a ``Protocol`` for the same reason :class:`~src.detection.Detector`
is: the real ByteTrack tracker is reached through Ultralytics and needs weights,
while a pure-Python fake needs neither. Tests inject the fake and every stage
after tracking runs headless (see ``tests/fixtures.FakeTracker``).

The trajectory is a ``deque`` carrying a ``maxlen``, not a list. That is what
makes Requirement 3.5 ("at most Trajectory_Length most recent reference points")
structural rather than something each tracker has to remember to trim: appending
past the bound evicts the oldest point automatically, and memory per live track
is fixed. :class:`Track` rejects an unbounded deque on construction so a tracker
cannot accidentally opt out of the bound.

:class:`Track` also validates its Track_ID, box, and class on construction, for
the same reason :class:`~src.detection.Detection` does: a malformed track is far
cheaper to diagnose here than three stages later as a nonsensical queue count.

:class:`ByteTrackTracker` is the concrete tracker: it reaches ByteTrack through
Ultralytics' persistent tracking, generates a tracker config carrying
``config.track_buffer``, and owns the invariant checks for Requirements 3.2 and
3.4. Its ``ultralytics`` import is local to the weights loader it shares with the
Detector, so this module — the data model and the protocol every fake implements
— stays importable without ultralytics installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

import numpy as np

from src.config import Config
from src.detection import (
    COCO_VEHICLE_CLASS_IDS,
    VEHICLE_CLASSES,
    Detection,
    clip_box_to_frame,
    coco_class_id,
    iter_results,
    load_yolo_model,
    to_numpy,
)

#: One trajectory point: an integer pixel position in image coordinates.
Point = tuple[int, int]


def new_trajectory(
    trajectory_length: int, points: Iterable[Point] | None = None
) -> deque[Point]:
    """Return an empty bounded trajectory deque, optionally pre-filled.

    Centralising construction keeps the ``maxlen`` bound of Requirement 3.5 in
    one place: a tracker asks for a trajectory by length and cannot forget to
    pass the bound. Pre-filling past the bound keeps only the most recent points,
    exactly as appending would.
    """
    if isinstance(trajectory_length, bool) or not isinstance(trajectory_length, int):
        raise ValueError(
            f"trajectory_length must be an int, got {trajectory_length!r}"
        )
    if trajectory_length < 1:
        raise ValueError(
            f"trajectory_length must be at least 1, got {trajectory_length}"
        )
    return deque(points or (), maxlen=trajectory_length)


@dataclass
class Track:
    """One tracked vehicle in one frame (Requirement 3.1).

    Coordinates follow :class:`~src.detection.Detection`: ``(x1, y1)`` top-left,
    ``(x2, y2)`` bottom-right and exclusive, both extents strictly positive.

    Unlike ``Detection`` this dataclass is mutable, because a tracker advances
    the same track object across frames — it rewrites the box and appends to the
    trajectory rather than allocating a new track per frame. ``trajectory`` is
    ordered oldest to newest and is bounded by its ``maxlen`` (Requirement 3.5).
    """

    track_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    vehicle_class: str                                  # in VEHICLE_CLASSES
    trajectory: deque[Point] = field(default_factory=lambda: new_trajectory(1))

    def __post_init__(self) -> None:
        if isinstance(self.track_id, bool) or not isinstance(self.track_id, int):
            raise ValueError(
                f"Track.track_id must be an int, got {self.track_id!r}"
            )
        if self.track_id < 0:
            raise ValueError(
                f"Track.track_id must not be negative, got {self.track_id!r}"
            )
        for name in ("x1", "y1", "x2", "y2"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Track.{name} must be an int, got {value!r}")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(
                "Track box must have positive width and height, got "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )
        if self.x1 < 0 or self.y1 < 0:
            raise ValueError(
                "Track box must lie within the frame, got "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )
        if self.vehicle_class not in VEHICLE_CLASSES:
            raise ValueError(
                f"Track.vehicle_class must be one of {VEHICLE_CLASSES}, "
                f"got {self.vehicle_class!r}"
            )
        if not isinstance(self.trajectory, deque):
            raise ValueError(
                "Track.trajectory must be a deque of reference points, got "
                f"{type(self.trajectory).__name__}"
            )
        if self.trajectory.maxlen is None:
            raise ValueError(
                "Track.trajectory must be bounded by a maxlen so the configured "
                "Trajectory_Length cannot be exceeded"
            )

    # -- geometry ----------------------------------------------------------

    @property
    def width(self) -> int:
        """Box width in pixels, always greater than 0."""
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        """Box height in pixels, always greater than 0."""
        return self.y2 - self.y1

    @property
    def box(self) -> tuple[int, int, int, int]:
        """The box as ``(x1, y1, x2, y2)``, for the Approach_Assigner hand-off."""
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def ref_point(self) -> Point:
        """Midpoint of the bottom edge of the box (Requirement 4.2).

        The reference point lives here because it is what the trajectory records
        (Requirement 3.5), so the tracker needs it before the Approach_Assigner
        exists. ``src/lane_analysis.reference_point`` reads this property rather
        than recomputing the formula, so the point the overlay draws, the point
        the trajectory stores, and the point tested against the ROI polygons are
        the same point by construction.
        """
        return ((self.x1 + self.x2) // 2, self.y2)

    @property
    def trajectory_length(self) -> int:
        """The configured bound on retained reference points (Requirement 3.5)."""
        maxlen = self.trajectory.maxlen
        assert maxlen is not None                       # enforced in __post_init__
        return maxlen

    # -- mutation ----------------------------------------------------------

    def set_box(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Move the track to a new box, re-running construction validation."""
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.__post_init__()

    def record_position(self) -> Point:
        """Append the current reference point to the trajectory and return it.

        Called once per frame the track is reported in. The deque's ``maxlen``
        evicts the oldest point when the trajectory is full, which is what keeps
        Requirement 3.5 satisfied without an explicit trim.
        """
        point = self.ref_point
        self.trajectory.append(point)
        return point

    def label(self) -> str:
        """Short caption drawn on annotated frames (Requirement 3.6)."""
        return f"#{self.track_id} {self.vehicle_class}"

    # -- construction ------------------------------------------------------

    @classmethod
    def from_detection(
        cls, track_id: int, detection: Detection, trajectory_length: int
    ) -> Track:
        """Start a new track from the detection that opened it.

        The reference point of that first detection is recorded immediately, so a
        track reported on its first frame already carries a one-point trajectory
        rather than an empty one.
        """
        track = cls(
            track_id=track_id,
            x1=detection.x1,
            y1=detection.y1,
            x2=detection.x2,
            y2=detection.y2,
            vehicle_class=detection.vehicle_class,
            trajectory=new_trajectory(trajectory_length),
        )
        track.record_position()
        return track

    def update_from_detection(self, detection: Detection) -> None:
        """Advance an existing track onto ``detection`` for the current frame.

        The Vehicle_Class is taken from the new detection: a real tracker can
        associate a box whose class the detector re-read differently between
        frames, and the track must report what was detected this frame
        (Requirement 3.1) rather than a stale class.
        """
        self.vehicle_class = detection.vehicle_class
        self.set_box(detection.x1, detection.y1, detection.x2, detection.y2)
        self.record_position()


@runtime_checkable
class Tracker(Protocol):
    """Anything that turns per-frame detections into identified tracks.

    ``update`` receives the frame the detections came from — the real ByteTrack
    backend re-reads pixels for appearance association — and returns the tracks
    active on that frame, each with a Track_ID that persists while the vehicle
    remains detected (Requirements 3.1, 3.3). It must not mutate ``frame``: the
    same array is handed to the Signal_Overlay after tracking.
    """

    def update(self, frame: np.ndarray, detections: list[Detection]) -> list[Track]:
        """Return the tracks active on this frame (Requirement 3.1)."""
        ...


# ---------------------------------------------------------------------------
# ByteTrack through Ultralytics (Requirements 3.1-3.5)
# ---------------------------------------------------------------------------

#: Name of the generated tracker config file. Ultralytics resolves tracker
#: settings from a YAML path, so the suffix matters.
TRACKER_CONFIG_NAME = "bytetrack.yaml"

#: Association threshold handed to ByteTrack. Ultralytics' shipped default; it is
#: not a tunable of this project, so it is not read from ``Config``.
BYTETRACK_MATCH_THRESHOLD = 0.8

#: Floor for ByteTrack's low-confidence detection pool. ByteTrack's whole premise
#: is that a box below the confidence threshold can still continue an existing
#: track, so this stays below ``confidence_threshold`` rather than tracking it.
BYTETRACK_LOW_THRESHOLD = 0.1

#: First Track_ID used when relabelling a backend id this wrapper has already
#: retired (Requirement 3.4). ByteTrack allocates ids sequentially from 1, so
#: starting far above that keeps a relabelled track from ever colliding with an id
#: the backend later issues itself. See ``ByteTrackTracker._resolve_track_id``.
REMAPPED_ID_BASE = 1_000_000


def render_tracker_config(config: Config) -> str:
    """Return the ByteTrack tracker config text for ``config``.

    ``track_buffer`` is the tunable that matters here: it is how many frames of
    absence ByteTrack tolerates before dropping a track, which is exactly the
    retirement bound of Requirement 3.4. It comes from ``Config`` rather than
    from Ultralytics' shipped default so the retirement behaviour the wrapper
    checks and the behaviour the backend implements are driven by one value.

    Written as plain text rather than through a YAML library because Requirement
    15.8 fixes the runtime dependency set and PyYAML is not in it; Ultralytics
    parses the file itself.
    """
    high = float(config.confidence_threshold)
    low = min(BYTETRACK_LOW_THRESHOLD, high)
    return "\n".join(
        (
            "tracker_type: bytetrack",
            f"track_high_thresh: {high}",
            f"track_low_thresh: {low}",
            f"new_track_thresh: {high}",
            f"track_buffer: {int(config.track_buffer)}",
            f"match_thresh: {BYTETRACK_MATCH_THRESHOLD}",
            "fuse_score: true",
            "",
        )
    )


def write_tracker_config(config: Config, directory: str) -> str:
    """Write the generated tracker config into ``directory`` and return its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, TRACKER_CONFIG_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_tracker_config(config))
    return path


class ByteTrackTracker:
    """The Tracker backed by Ultralytics' persistent ByteTrack (Req 3.1-3.5).

    Association is ByteTrack's: ``model.track(..., persist=True)`` keeps tracker
    state across calls, so a vehicle detected on consecutive frames keeps one
    Track_ID (Requirement 3.3) and ids advance monotonically. ``track_buffer``
    reaches it through the generated tracker config of :func:`write_tracker_config`.

    What the wrapper adds is the bookkeeping the requirements need to be
    *checkable* rather than assumed:

    - **Trajectories** (Requirement 3.5) live here, in ``dict[int, deque]`` with
      ``maxlen=config.trajectory_length``. The reference point is appended once
      per frame the track is reported in, and the entry is deleted on retirement,
      so memory is bounded by the number of live tracks rather than by the number
      of vehicles the video ever contained.
    - **Distinctness** (Requirement 3.2) is checked per frame: two rows carrying
      the same id in one frame is a backend fault, and failing here beats
      reporting a queue count that double-counts a vehicle.
    - **Retirement** (Requirement 3.4) is counted here too. An id absent for more
      than ``track_buffer`` frames is retired and recorded. ByteTrack accounts its
      own lost-track buffer independently, so it may still re-associate a retired
      id; the requirement is that the retired id never labels a later track, so
      such a reappearance is relabelled with a fresh id rather than aborting the
      run. See :meth:`_resolve_track_id`.

    The ``detections`` argument of :meth:`update` is accepted for the
    :class:`Tracker` protocol and deliberately unused: Ultralytics runs detection
    and association in one pass, from the same weights and the same
    Confidence_Threshold the Detector uses, so re-associating the Detector's boxes
    here would only be a second, divergent implementation of ByteTrack.
    """

    def __init__(
        self,
        config: Config,
        *,
        model: Any | None = None,
        tracker_config_dir: str | None = None,
    ) -> None:
        """Load the configured weights and generate the tracker config.

        ``model`` is an injection seam for tests, accepting anything with
        Ultralytics' ``track`` shape, so every rule this class owns is testable
        with no weights and no GPU. Production callers pass ``config`` only.
        """
        self._config = config
        self._model = model if model is not None else load_yolo_model(config.model_path)

        self._owns_config_dir = tracker_config_dir is None
        directory = tracker_config_dir or tempfile.mkdtemp(prefix="bytetrack_cfg_")
        self._tracker_config_dir = directory
        self._tracker_config_path = write_tracker_config(config, directory)

        self._tracks: dict[int, Track] = {}
        self._trajectories: dict[int, deque[Point]] = {}
        self._missing: dict[int, int] = {}
        self._retired_ids: set[int] = set()
        self._frames_seen = 0
        # Backend Track_ID -> the Track_ID this wrapper reports for it. Populated
        # only when the backend reissues an id this wrapper has already retired;
        # see :meth:`_resolve_track_id`.
        self._remapped_ids: dict[int, int] = {}
        self._next_remap_id = REMAPPED_ID_BASE

    # -- introspection -----------------------------------------------------

    @property
    def tracker_config_path(self) -> str:
        """Path of the generated ByteTrack config passed to Ultralytics."""
        return self._tracker_config_path

    @property
    def track_buffer(self) -> int:
        """Frames of absence tolerated before retirement (Requirement 3.4)."""
        return self._config.track_buffer

    @property
    def trajectory_length(self) -> int:
        """Bound on retained reference points per track (Requirement 3.5)."""
        return self._config.trajectory_length

    @property
    def frames_seen(self) -> int:
        """Number of :meth:`update` calls made so far."""
        return self._frames_seen

    @property
    def active_ids(self) -> tuple[int, ...]:
        """Currently live Track_IDs, in ascending order."""
        return tuple(sorted(self._tracks))

    @property
    def retired_ids(self) -> frozenset[int]:
        """Track_IDs retired so far; none of them may be reissued (Req 3.4)."""
        return frozenset(self._retired_ids)

    @property
    def trajectories(self) -> Mapping[int, deque[Point]]:
        """Live trajectories by Track_ID (Requirement 3.5).

        Only live tracks appear: a retired id's entry is deleted, which is what
        bounds memory by the number of vehicles on screen.
        """
        return dict(self._trajectories)

    # -- tracking ----------------------------------------------------------

    def update(self, frame: np.ndarray, detections: list[Detection]) -> list[Track]:
        """Return the tracks active on ``frame`` (Requirements 3.1-3.5).

        ``detections`` is unused; see the class docstring. ``frame`` is never
        modified: the same array is handed to the Signal_Overlay after tracking.
        """
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            raise ValueError(
                "ByteTrackTracker.update expects a NumPy image array, got "
                f"{type(frame).__name__}"
            )
        self._frames_seen += 1
        height, width = int(frame.shape[0]), int(frame.shape[1])

        observations = self._observe(frame, width, height)

        active: list[Track] = []
        reported: set[int] = set()          # backend ids seen this frame
        present: set[int] = set()           # wrapper ids seen this frame
        for backend_id, detection in observations:
            if backend_id in reported:
                raise AssertionError(
                    f"tracker reported Track_ID {backend_id} twice in one frame; "
                    "Track_IDs must be distinct within a frame (Requirement 3.2)"
                )
            reported.add(backend_id)
            track_id = self._resolve_track_id(backend_id)
            present.add(track_id)

            track = self._tracks.get(track_id)
            if track is None:
                track = Track.from_detection(
                    track_id, detection, self.trajectory_length
                )
                self._tracks[track_id] = track
                self._trajectories[track_id] = track.trajectory
            else:
                track.update_from_detection(detection)
            self._missing[track_id] = 0
            active.append(track)

        self._retire_absent(present)
        return active

    def reset(self) -> None:
        """Drop all tracking state so one tracker can drive two runs.

        The Evaluation_Harness runs the same video under both controllers with an
        identical Tracker configuration (Requirement 13.1); resetting clears the
        wrapper's tracks, trajectories, and retired ids, and clears the backend's
        persistent state too when it exposes a way to do so, so the second run
        reproduces the first.
        """
        self._tracks.clear()
        self._trajectories.clear()
        self._missing.clear()
        self._retired_ids.clear()
        self._frames_seen = 0
        self._remapped_ids.clear()
        self._next_remap_id = REMAPPED_ID_BASE
        self._reset_backend()

    def close(self) -> None:
        """Delete the generated tracker config directory, if this tracker made it."""
        if self._owns_config_dir and os.path.isdir(self._tracker_config_dir):
            shutil.rmtree(self._tracker_config_dir, ignore_errors=True)

    def __enter__(self) -> ByteTrackTracker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    def _observe(
        self, frame: np.ndarray, width: int, height: int
    ) -> list[tuple[int, Detection]]:
        """Return ``(track_id, detection)`` pairs read from the backend result.

        Rows are filtered exactly as the Detector filters its own
        (Requirements 2.2, 2.3, 2.5), reusing the same helpers so the boxes the
        Approach_Assigner tests against are clipped by one implementation. A row
        carrying no id is a detection ByteTrack has not confirmed into a track,
        so it is not a track and is dropped.
        """
        observations: list[tuple[int, Detection]] = []
        for result in iter_results(self._track(frame)):
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            raw_xyxy = getattr(boxes, "xyxy", None)
            raw_ids = getattr(boxes, "id", None)
            if raw_xyxy is None or raw_ids is None:
                continue
            xyxy = to_numpy(raw_xyxy).reshape(-1, 4)
            ids = to_numpy(raw_ids).reshape(-1)
            if xyxy.size == 0 or ids.size == 0:
                continue

            raw_class_ids = getattr(boxes, "cls", None)
            class_ids = to_numpy([] if raw_class_ids is None else raw_class_ids).reshape(-1)
            if len(ids) != len(xyxy) or len(class_ids) != len(xyxy):
                raise AssertionError(
                    f"tracker returned {len(xyxy)} boxes but {len(ids)} ids and "
                    f"{len(class_ids)} class ids"
                )

            for row, raw_id, raw_class_id in zip(xyxy, ids, class_ids):
                observation = self._to_observation(row, raw_id, raw_class_id, width, height)
                if observation is not None:
                    observations.append(observation)
        return observations

    def _to_observation(
        self,
        row: np.ndarray,
        raw_id: Any,
        raw_class_id: Any,
        width: int,
        height: int,
    ) -> tuple[int, Detection] | None:
        """Convert one backend row into ``(track_id, detection)``, or drop it."""
        track_id = _as_track_id(raw_id)
        if track_id is None:
            return None                                   # unconfirmed detection

        vehicle_class = COCO_VEHICLE_CLASS_IDS.get(coco_class_id(raw_class_id))
        if vehicle_class is None:
            return None                                   # Requirement 2.2

        clipped = clip_box_to_frame(row, width, height)
        if clipped is None:
            return None                                   # Requirement 2.5
        x1, y1, x2, y2 = clipped

        # The confidence a track carries is not itself measured by any
        # requirement — Requirement 3.1 asks for a box and a Vehicle_Class — and
        # ByteTrack reports a predicted box for a frame in which the detector
        # scored nothing. A fixed in-range value keeps the Detection contract
        # satisfied without inventing a detector score.
        return track_id, Detection(
            x1=x1, y1=y1, x2=x2, y2=y2,
            vehicle_class=vehicle_class,
            confidence=1.0,
        )

    def _resolve_track_id(self, backend_id: int) -> int:
        """Map a backend Track_ID to the Track_ID this wrapper reports (Req 3.4).

        Requirement 3.4 obliges this Tracker to leave a retired Track_ID
        "unassigned to any later track". ByteTrack's own lost-track buffer is
        accounted independently of this wrapper's absence counter, so the backend
        can legitimately re-associate an id that this wrapper has already retired.
        Treating that as a fatal invariant breach would abort a run over real
        footage; what the requirement actually calls for is that the retired id
        never labels a later track, so the reappearance is relabelled with a fresh
        id and thereafter tracked under it. This matches ``FakeTracker``, which
        already relabels rather than raising.

        Fresh ids are drawn from :data:`REMAPPED_ID_BASE` upward, well above the
        sequential ids ByteTrack issues, so a relabelled track can never collide
        with an id the backend goes on to allocate itself.
        """
        track_id = self._remapped_ids.get(backend_id, backend_id)
        if track_id not in self._retired_ids:
            return track_id

        # Either the backend reissued a retired id, or a previously relabelled id
        # has itself since retired. Both need a new label.
        while self._next_remap_id in self._retired_ids or self._next_remap_id in self._tracks:
            self._next_remap_id += 1
        track_id = self._next_remap_id
        self._next_remap_id += 1
        self._remapped_ids[backend_id] = track_id
        return track_id

    def _retire_absent(self, seen: set[int]) -> None:
        """Retire every track absent for more than ``track_buffer`` frames.

        Retirement drops the track, its absence counter, and its trajectory
        entry, and records the id so a later reissue is caught in :meth:`update`
        (Requirements 3.4, 3.5).
        """
        for track_id in list(self._tracks):
            if track_id in seen:
                continue
            self._missing[track_id] = self._missing.get(track_id, 0) + 1
            if self._missing[track_id] > self.track_buffer:
                del self._tracks[track_id]
                del self._missing[track_id]
                self._trajectories.pop(track_id, None)
                self._retired_ids.add(track_id)

    def _track(self, frame: np.ndarray) -> Any:
        """Run one persistent ByteTrack step. No training call is ever made.

        ``persist=True`` is what carries tracker state from one call to the next,
        so ids survive across frames (Requirement 3.3). ``classes`` restricts
        association to the four Vehicle_Classes, so a pedestrian never occupies a
        Track_ID (Requirement 2.2).
        """
        return self._model.track(
            source=frame,
            persist=True,
            tracker=self._tracker_config_path,
            conf=self._config.confidence_threshold,
            classes=sorted(COCO_VEHICLE_CLASS_IDS),
            verbose=False,
        )

    def _reset_backend(self) -> None:
        """Clear the backend's persistent tracker state where that is possible."""
        predictor = getattr(self._model, "predictor", None)
        for tracker in getattr(predictor, "trackers", None) or ():
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()


def _as_track_id(raw_id: Any) -> int | None:
    """Return the backend's id as a non-negative ``int``, or ``None`` if absent.

    Ultralytics reports ids as floats and leaves them unset for detections it has
    not confirmed into tracks, which surfaces as ``None`` or ``NaN``.
    """
    if raw_id is None:
        return None
    try:
        value = float(raw_id)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    track_id = int(round(value))
    return track_id if track_id >= 0 else None


__all__ = [
    "BYTETRACK_LOW_THRESHOLD",
    "BYTETRACK_MATCH_THRESHOLD",
    "REMAPPED_ID_BASE",
    "TRACKER_CONFIG_NAME",
    "ByteTrackTracker",
    "Point",
    "Track",
    "Tracker",
    "new_trajectory",
    "render_tracker_config",
    "write_tracker_config",
]
