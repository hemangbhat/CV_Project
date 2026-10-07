"""Shared test fixtures: synthetic clips generated in memory.

No footage is checked into the repository. Clips are built with NumPy — moving
rectangles on a static background — and encoded with ``cv2.VideoWriter``, so the
tests that need a readable video (Requirement 1.2 and the integration loop) can
generate exactly the clip they need, of exactly the length they need.

The rectangles move by a fixed per-frame step, which gives the later detection
and tracking stages something with genuine motion to work with rather than flat
frames.

The two fakes here — :class:`FakeDetector` and :class:`FakeTracker` — replay and
associate those same rectangles, so every stage after tracking can be built and
tested with no YOLO weights, no GPU, and no footage.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from src.config import Config
from src.detection import Detection
from src.tracking import Track

#: Small by default: every test here cares about frame bookkeeping, not pixels,
#: and small frames keep generated-input tests fast.
DEFAULT_CLIP_WIDTH = 64
DEFAULT_CLIP_HEIGHT = 48
DEFAULT_CLIP_FRAME_RATE = 20.0
DEFAULT_CLIP_FRAME_COUNT = 8

#: Static background grey, distinct from the rectangle colours below.
BACKGROUND_LEVEL = 40

#: ``(width, height, colour_bgr, x0, y0, dx, dy)`` for each moving rectangle.
_RECTANGLES: tuple[tuple[int, int, tuple[int, int, int], int, int, int, int], ...] = (
    (10, 8, (255, 255, 255), 2, 4, 3, 0),
    (8, 6, (0, 255, 0), 20, 30, 0, -2),
    (12, 6, (0, 0, 255), 40, 10, -2, 1),
)


def synthetic_frame(
    index: int,
    width: int = DEFAULT_CLIP_WIDTH,
    height: int = DEFAULT_CLIP_HEIGHT,
) -> np.ndarray:
    """Return frame ``index`` of the synthetic clip as a BGR ``uint8`` array.

    Rectangles are placed by ``index`` alone, so any frame can be regenerated
    independently of the ones around it.
    """
    frame = np.full((height, width, 3), BACKGROUND_LEVEL, dtype=np.uint8)
    for rect_w, rect_h, colour, x0, y0, dx, dy in _RECTANGLES:
        x = (x0 + dx * index) % width
        y = (y0 + dy * index) % height
        # Clipping instead of wrapping keeps each rectangle a single solid block.
        x1 = min(x + rect_w, width)
        y1 = min(y + rect_h, height)
        frame[y:y1, x:x1] = colour
    return frame


def synthetic_frames(
    frame_count: int = DEFAULT_CLIP_FRAME_COUNT,
    width: int = DEFAULT_CLIP_WIDTH,
    height: int = DEFAULT_CLIP_HEIGHT,
) -> list[np.ndarray]:
    """Return ``frame_count`` synthetic frames in clip order."""
    if frame_count < 1:
        raise ValueError(f"frame_count must be at least 1, got {frame_count}")
    return [synthetic_frame(index, width, height) for index in range(frame_count)]


def write_synthetic_clip(
    path: str | os.PathLike[str],
    frame_count: int = DEFAULT_CLIP_FRAME_COUNT,
    frame_rate: float = DEFAULT_CLIP_FRAME_RATE,
    width: int = DEFAULT_CLIP_WIDTH,
    height: int = DEFAULT_CLIP_HEIGHT,
) -> Path:
    """Write a synthetic clip to ``path`` and return it as a :class:`Path`.

    Raises ``RuntimeError`` when the environment has no usable encoder, rather
    than leaving a caller to debug an empty file: a silent zero-byte clip would
    make every downstream assertion fail for the wrong reason.
    """
    clip_path = Path(path)
    writer = cv2.VideoWriter(
        str(clip_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(frame_rate),
        (width, height),
    )
    if not writer.isOpened():
        writer.release()
        raise RuntimeError(f"no OpenCV encoder available to write {clip_path}")
    try:
        for frame in synthetic_frames(frame_count, width, height):
            writer.write(frame)
    finally:
        writer.release()

    if not clip_path.is_file() or clip_path.stat().st_size == 0:
        raise RuntimeError(f"synthetic clip was not written: {clip_path}")
    return clip_path


# ---------------------------------------------------------------------------
# Scripted Detector fake (Requirement 2.1)
# ---------------------------------------------------------------------------

#: Vehicle_Class and confidence granted to each rectangle of the synthetic clip,
#: in the order the rectangles are declared in ``_RECTANGLES``. Distinct classes
#: keep class-dependent behaviour downstream (PCE weighting, Requirement 5.9)
#: visible in tests driven by this fake.
_RECTANGLE_LABELS: tuple[tuple[str, float], ...] = (
    ("car", 0.90),
    ("motorcycle", 0.75),
    ("bus", 0.60),
)


def scripted_detections(
    frame_count: int = DEFAULT_CLIP_FRAME_COUNT,
    width: int = DEFAULT_CLIP_WIDTH,
    height: int = DEFAULT_CLIP_HEIGHT,
) -> list[list[Detection]]:
    """Return one detection list per frame, matching the synthetic clip.

    The boxes are the exact rectangle positions :func:`synthetic_frame` draws, so
    a downstream stage fed by :class:`FakeDetector` sees detections that move the
    way the pixels move. That matters for tracking and queue tests: a fake that
    emitted static boxes would let an identity-association bug pass.
    """
    if frame_count < 1:
        raise ValueError(f"frame_count must be at least 1, got {frame_count}")

    script: list[list[Detection]] = []
    for index in range(frame_count):
        frame_detections: list[Detection] = []
        for (rect_w, rect_h, _colour, x0, y0, dx, dy), (vehicle_class, confidence) in zip(
            _RECTANGLES, _RECTANGLE_LABELS
        ):
            x = (x0 + dx * index) % width
            y = (y0 + dy * index) % height
            frame_detections.append(
                Detection(
                    x1=x,
                    y1=y,
                    x2=min(x + rect_w, width),
                    y2=min(y + rect_h, height),
                    vehicle_class=vehicle_class,
                    confidence=confidence,
                )
            )
        script.append(frame_detections)
    return script


class FakeDetector:
    """A :class:`~src.detection.Detector` that replays a scripted detection list.

    Frame pixels are ignored entirely: the fake returns ``script[i]`` on its
    ``i``-th ``detect`` call. That is what lets every stage after detection be
    built and tested with no YOLO weights, no GPU, and no real footage, and it
    makes the detections an exact known quantity so a downstream assertion can
    name the count it expects.

    Past the end of the script ``detect`` returns an empty list, which mirrors a
    real detector on a frame holding no vehicles rather than inventing boxes.
    Each returned list is a fresh copy, so a caller that filters in place cannot
    corrupt the script for a later replay.
    """

    def __init__(self, script: Sequence[Sequence[Detection]] | None = None) -> None:
        self._script: tuple[tuple[Detection, ...], ...] = (
            tuple(tuple(frame_detections) for frame_detections in script)
            if script is not None
            else tuple(tuple(f) for f in scripted_detections())
        )
        self.calls = 0

    @property
    def script(self) -> tuple[tuple[Detection, ...], ...]:
        """The scripted detections, one entry per frame."""
        return self._script

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Return the scripted detections for the next frame (Requirement 2.1)."""
        index = self.calls
        self.calls += 1
        if index >= len(self._script):
            return []
        return list(self._script[index])

    def reset(self) -> None:
        """Rewind to the start of the script so one fake can drive two runs.

        The Evaluation_Harness runs the same video under both controllers
        (Requirement 13.1); resetting keeps the second run's detections identical
        to the first without rebuilding the script.
        """
        self.calls = 0


# ---------------------------------------------------------------------------
# Deterministic Tracker fake (Requirement 3.1)
# ---------------------------------------------------------------------------

#: Trajectory bound and absence tolerance used when no ``Config`` is supplied.
#: Both are small so a test can exhaust them in a handful of frames.
DEFAULT_FAKE_TRAJECTORY_LENGTH = 30
DEFAULT_FAKE_TRACK_BUFFER = 3

#: How far a box centre may move between frames and still be considered the same
#: vehicle. The synthetic clip's rectangles step by at most 3 px per frame, so
#: this is generous enough to hold identities while staying tight enough that two
#: vehicles on opposite sides of the frame never associate.
DEFAULT_FAKE_MATCH_DISTANCE = 24.0


def _box_centre(x1: int, y1: int, x2: int, y2: int) -> tuple[float, float]:
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


class FakeTracker:
    """A :class:`~src.tracking.Tracker` that assigns ids by nearest-centre match.

    Association is deterministic and pure Python: for each detection, in the
    order the detector returned it, the fake claims the closest unclaimed active
    track of the same Vehicle_Class whose centre lies within
    ``max_match_distance``; anything unmatched opens a new track with the next
    sequential id. Ids come from a monotonic counter, so a retired id is never
    reissued (Requirement 3.4) and two runs over the same detections produce the
    same ids — which is what lets a downstream test name the ids it expects.

    Nearest-centre matching rather than "one id per detection index" is
    deliberate: index-keyed ids would hold across frames even if the boxes jumped
    arbitrarily, letting an association bug in a downstream stage pass unnoticed.
    Matching on movement means the fake behaves like a tracker on the moving
    rectangles of the synthetic clip.

    Absence is tolerated for ``track_buffer`` frames, after which the id is
    retired and its trajectory dropped, mirroring the ByteTrack behaviour the
    real wrapper relies on. Frame pixels are ignored entirely, so no weights, GPU,
    or footage are needed.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        trajectory_length: int = DEFAULT_FAKE_TRAJECTORY_LENGTH,
        track_buffer: int = DEFAULT_FAKE_TRACK_BUFFER,
        max_match_distance: float = DEFAULT_FAKE_MATCH_DISTANCE,
        first_track_id: int = 1,
    ) -> None:
        if config is not None:
            trajectory_length = config.trajectory_length
            track_buffer = config.track_buffer
        self.trajectory_length = trajectory_length
        self.track_buffer = track_buffer
        self.max_match_distance = float(max_match_distance)
        self._first_track_id = first_track_id
        self.calls = 0
        self.retired_ids: set[int] = set()
        self._next_id = first_track_id
        self._tracks: dict[int, Track] = {}
        self._missing: dict[int, int] = {}

    @property
    def active_ids(self) -> tuple[int, ...]:
        """Currently live Track_IDs, in ascending order."""
        return tuple(sorted(self._tracks))

    def update(self, frame: np.ndarray, detections: list[Detection]) -> list[Track]:
        """Return the tracks active on this frame (Requirement 3.1)."""
        self.calls += 1

        claimed: set[int] = set()
        updated: list[Track] = []
        for detection in detections:
            track_id = self._match(detection, claimed)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = Track.from_detection(
                    track_id, detection, self.trajectory_length
                )
            else:
                self._tracks[track_id].update_from_detection(detection)
            claimed.add(track_id)
            self._missing[track_id] = 0
            updated.append(self._tracks[track_id])

        for track_id in list(self._tracks):
            if track_id in claimed:
                continue
            self._missing[track_id] += 1
            if self._missing[track_id] > self.track_buffer:
                del self._tracks[track_id]
                del self._missing[track_id]
                self.retired_ids.add(track_id)

        return updated

    def reset(self) -> None:
        """Clear all state so one fake can drive two runs identically.

        The Evaluation_Harness runs the same video under both controllers
        (Requirement 13.1); resetting makes the second run's ids match the first.
        """
        self.calls = 0
        self.retired_ids.clear()
        self._next_id = self._first_track_id
        self._tracks.clear()
        self._missing.clear()

    # -- internals ---------------------------------------------------------

    def _match(self, detection: Detection, claimed: set[int]) -> int | None:
        """Return the id of the closest eligible active track, or ``None``."""
        target = _box_centre(*detection.box)
        best_id: int | None = None
        best_distance = float("inf")
        for track_id in sorted(self._tracks):          # ascending: ties take the older id
            if track_id in claimed:
                continue
            track = self._tracks[track_id]
            if track.vehicle_class != detection.vehicle_class:
                continue
            centre = _box_centre(*track.box)
            distance = float(np.hypot(centre[0] - target[0], centre[1] - target[1]))
            if distance > self.max_match_distance:
                continue
            if best_id is None or distance < best_distance:
                best_id, best_distance = track_id, distance
        return best_id


class ScriptedTracker:
    """A :class:`~src.tracking.Tracker` that detects *and* tracks from a script.

    Wraps :class:`FakeDetector` and :class:`FakeTracker` behind the tracker
    interface, so it can stand in for :class:`~src.tracking.ByteTrackTracker` — which
    also detects and associates in one pass and ignores the ``detections`` argument.
    That is what lets a command-line entry point be tested end to end by patching one
    name, with no weights, no GPU, and no footage.
    """

    def __init__(
        self,
        config: Config | None = None,
        script: Sequence[Sequence[Detection]] | None = None,
        **tracker_kwargs: object,
    ) -> None:
        self._detector = FakeDetector(script)
        self._tracker = FakeTracker(config, **tracker_kwargs)  # type: ignore[arg-type]

    @property
    def detector(self) -> FakeDetector:
        return self._detector

    @property
    def tracker(self) -> FakeTracker:
        return self._tracker

    def update(self, frame: np.ndarray, detections: list[Detection]) -> list[Track]:
        """Ignore ``detections`` and track this frame's scripted ones instead."""
        del detections
        return self._tracker.update(frame, self._detector.detect(frame))

    def reset(self) -> None:
        self._detector.reset()
        self._tracker.reset()


# ---------------------------------------------------------------------------
# Configurations scaled to the synthetic clip
# ---------------------------------------------------------------------------


def quadrant_config(
    base: Config,
    width: int = DEFAULT_CLIP_WIDTH,
    height: int = DEFAULT_CLIP_HEIGHT,
    *,
    saturation_count: float = 4.0,
    queue_capacity: float = 2.0,
) -> Config:
    """Return ``base`` with its four ROIs tiling a ``width`` x ``height`` frame.

    The shipped ``config/default.json`` is calibrated for 1280x720. On a 64x48
    synthetic clip every reference point would fall outside every ROI and all
    measurements would read zero for the wrong reason, so a test that needs vehicles
    to be *counted* needs geometry scaled to its clip.

    Each Approach takes one quadrant, and its Queue_Region takes the far half of that
    quadrant, so a clip can hold vehicles that are inside an ROI without queueing —
    the case where Queue_Length is below Vehicle_Count.
    """
    import dataclasses

    from src.config import ApproachConfig

    mid_x, mid_y = width // 2, height // 2
    right, bottom = width - 1, height - 1
    quarter_y = mid_y + (bottom - mid_y) // 2
    upper_queue_y = mid_y // 2

    def approach(
        name: str,
        roi: tuple[tuple[int, int], ...],
        queue: tuple[tuple[int, int], ...],
    ) -> ApproachConfig:
        return ApproachConfig(
            name=name,
            roi_polygon=roi,
            queue_region=queue,
            saturation_count=saturation_count,
            queue_capacity=queue_capacity,
        )

    return dataclasses.replace(
        base,
        frame_size=(width, height),
        approaches=(
            approach(
                "North",
                ((0, 0), (mid_x - 1, 0), (mid_x - 1, mid_y - 1), (0, mid_y - 1)),
                (
                    (0, upper_queue_y),
                    (mid_x - 1, upper_queue_y),
                    (mid_x - 1, mid_y - 1),
                    (0, mid_y - 1),
                ),
            ),
            approach(
                "East",
                ((mid_x, 0), (right, 0), (right, mid_y - 1), (mid_x, mid_y - 1)),
                (
                    (mid_x, upper_queue_y),
                    (right, upper_queue_y),
                    (right, mid_y - 1),
                    (mid_x, mid_y - 1),
                ),
            ),
            approach(
                "South",
                ((mid_x, mid_y), (right, mid_y), (right, bottom), (mid_x, bottom)),
                (
                    (mid_x, mid_y),
                    (right, mid_y),
                    (right, quarter_y),
                    (mid_x, quarter_y),
                ),
            ),
            approach(
                "West",
                ((0, mid_y), (mid_x - 1, mid_y), (mid_x - 1, bottom), (0, bottom)),
                (
                    (0, mid_y),
                    (mid_x - 1, mid_y),
                    (mid_x - 1, quarter_y),
                    (0, quarter_y),
                ),
            ),
        ),
    )


def quick_signal_config(
    base: Config,
    *,
    min_green: float = 1.0,
    max_green: float = 3.0,
    yellow: float = 0.5,
    starvation_limit: int = 3,
) -> Config:
    """Return ``base`` with phase durations short enough for a few-second clip.

    The shipped bands are 30, 45 and 60 simulated seconds, so a test clip would have
    to be minutes long for a second Cycle to begin. Scaling the durations down keeps
    the *structure* the requirements fix — three bands, thresholds at 0.3 and 0.6,
    monotone Green_Time — while letting a 24-frame clip cover four Cycles.
    """
    import dataclasses

    from src.config import GreenTimeBand

    middle = (min_green + max_green) / 2.0
    return dataclasses.replace(
        base,
        green_time_bands=(
            GreenTimeBand(min_score=0.0, green_time=min_green),
            GreenTimeBand(min_score=0.3, green_time=middle),
            GreenTimeBand(min_score=0.6, green_time=max_green),
        ),
        min_green_time=min_green,
        max_green_time=max_green,
        yellow_duration=yellow,
        starvation_limit=starvation_limit,
    )


__all__ = [
    "BACKGROUND_LEVEL",
    "DEFAULT_CLIP_FRAME_COUNT",
    "DEFAULT_CLIP_FRAME_RATE",
    "DEFAULT_CLIP_HEIGHT",
    "DEFAULT_CLIP_WIDTH",
    "DEFAULT_FAKE_MATCH_DISTANCE",
    "DEFAULT_FAKE_TRACK_BUFFER",
    "DEFAULT_FAKE_TRAJECTORY_LENGTH",
    "FakeDetector",
    "FakeTracker",
    "ScriptedTracker",
    "quadrant_config",
    "quick_signal_config",
    "scripted_detections",
    "synthetic_frame",
    "synthetic_frames",
    "write_synthetic_clip",
]
