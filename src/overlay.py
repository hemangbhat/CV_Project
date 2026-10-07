"""Signal_Overlay — everything this System draws onto an output frame.

At this stage the module owns three of the draw stack's layers:

* the *region layer* — the four ROI_Polygons and the four Queue_Regions with a
  per-Approach label (Requirement 12.1);
* the *track layer* — each active track's box, its Track_ID, its Vehicle_Class,
  and its trajectory polyline (Requirement 3.6);
* the *approach panel* — per Approach the name, Vehicle_Count, Queue_Length,
  Vehicle_Density, and Score at two decimals (Requirements 12.2, 15.4).

plus the two milestone entry points that consume them: the tracking run that
writes the annotated video (Requirement 15.3) and the measurement run that
displays the live per-Approach statistics (Requirement 15.4). The traffic-light
glyphs, the phase banner, and the header of Requirements 12.3-12.5 and 12.7 are
added by the full ``SignalOverlay`` draw stack once the Phase_Sequencer exists;
they stack on top of these layers without changing them.

The layering convention that makes that extension mechanical:

* ``draw_*_on(frame, ...)`` functions mutate ``frame`` in place and return it.
  They are the *layers*, meant to be called in back-to-front order on one canvas.
* ``draw_*(frame, ...)`` functions copy first and delegate to the ``_on`` layer,
  so a caller that only wants one layer cannot accidentally annotate the frame
  the Tracker and the Metrics_Engine are still reading.

Copying matters: the frame handed to :func:`draw_tracks` is the same array the
Detector and the Tracker were given, and an in-place edit here would feed drawn
pixels back into the next stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import cv2
import numpy as np

from src.config import APPROACH_NAMES, Config
from src.detection import Detector, open_annotated_writer
from src.errors import VideoError
from src.lane_analysis import (
    ApproachAssigner,
    AssignedTrack,
    OverlapEvent,
    as_cv_polygon,
)
from src.tracking import ByteTrackTracker, Point, Track, Tracker
from src.traffic_metrics import (
    RED,
    ApproachMetrics,
    MetricsEngine,
    compute_config_scores,
)
from src.video_io import VideoIngestor, VideoInfo

# ---------------------------------------------------------------------------
# Drawing constants
# ---------------------------------------------------------------------------

#: Palette indexed by Track_ID, so one vehicle keeps one colour for as long as it
#: keeps its Track_ID. That is what makes an identity switch visible by eye in the
#: annotated video, which is the whole point of the tracking milestone
#: (Requirements 3.3, 3.6). BGR, as OpenCV expects.
TRACK_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 255, 0),
    (0, 200, 255),
    (255, 128, 0),
    (255, 0, 255),
    (0, 0, 255),
    (255, 255, 0),
    (128, 255, 128),
    (200, 120, 255),
)

#: One colour per Approach, so an ROI polygon, its Queue_Region, its label, and
#: its panel row are all recognisably the same Approach in a screenshot
#: (Requirements 12.1, 12.2). BGR, as OpenCV expects.
APPROACH_COLORS: dict[str, tuple[int, int, int]] = {
    "North": (255, 200, 0),
    "East": (0, 200, 255),
    "South": (120, 255, 120),
    "West": (255, 120, 255),
}

#: Fallback for a name absent from :data:`APPROACH_COLORS`. Unreachable while the
#: configuration is validated against :data:`~src.config.APPROACH_NAMES`, but
#: drawing must never raise.
APPROACH_FALLBACK_COLOR: tuple[int, int, int] = (200, 200, 200)

#: How much a Queue_Region is darkened relative to its parent ROI. The design
#: asks for a *distinct* colour per region so the report screenshots show the two
#: separately; darkening rather than a second free palette keeps the pairing
#: obvious — a Queue_Region is visibly the deeper shade of its own Approach.
QUEUE_COLOR_SCALE = 0.5

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.4
_FONT_THICKNESS = 1
_TEXT_COLOR: tuple[int, int, int] = (0, 0, 0)
_BOX_THICKNESS = 2
_TRAJECTORY_THICKNESS = 2
_REF_POINT_RADIUS = 3
_ROI_THICKNESS = 2
_QUEUE_THICKNESS = 2

#: Where the per-approach panel's first row sits, and how far apart rows are.
#: The panel is anchored top-left because the ROI polygons of a junction view
#: meet in the middle of the frame, which is the area worth leaving clear.
PANEL_ORIGIN: tuple[int, int] = (4, 16)
PANEL_ROW_HEIGHT = 15

#: Title of the window opened when :func:`run_tracking_video` displays frames.
TRACK_WINDOW_NAME = "Adaptive Traffic Signal - track"

#: Where annotated output videos are written (Requirements 12.6, 15.3).
TRACK_VIDEO_DIR = "results/videos"

#: Suffix appended to the input stem when no output path is supplied, so a
#: tracking run cannot overwrite a detection run's output for the same video.
TRACK_VIDEO_SUFFIX = "__tracks"


def track_color(track_id: int) -> tuple[int, int, int]:
    """Return the stable colour of ``track_id``.

    A pure function of the id, so the box, the label strip, and the trajectory
    polyline of one track always agree, and two frames of the same run colour the
    same vehicle identically without any per-run state.
    """
    return TRACK_COLORS[int(track_id) % len(TRACK_COLORS)]


def draw_label_on(
    frame: np.ndarray,
    text: str,
    x: int,
    y: int,
    colour: tuple[int, int, int],
    *,
    above: bool = True,
) -> np.ndarray:
    """Draw ``text`` on a filled strip anchored near ``(x, y)``.

    The strip keeps the caption readable over both light and dark road surfaces.
    By default it is placed above ``y`` where there is room and inside the frame
    otherwise, so a track touching an edge still carries a visible Track_ID rather
    than one clipped away (Requirement 3.6).

    ``above=False`` puts the strip's *top* at ``y`` instead, which is what a region
    label wants: a label hanging above its ROI polygon would sit on top of
    whatever the neighbouring Approach has drawn there, and on a tight junction
    view that is another Approach's Queue_Region boundary.

    Public because every later layer — the approach panel, the phase banner, the
    header — needs exactly this primitive.
    """
    height, width = int(frame.shape[0]), int(frame.shape[1])
    (text_w, text_h), baseline = cv2.getTextSize(text, _FONT, _FONT_SCALE, _FONT_THICKNESS)
    strip_h = text_h + baseline

    top = y if not above else y - strip_h
    if top < 0:                                  # no room above: place it inside
        top = min(max(0, y), max(0, height - strip_h))
    left = max(0, min(x, max(0, width - text_w)))

    cv2.rectangle(
        frame,
        (left, top),
        (min(left + text_w, width), min(top + strip_h, height)),
        colour,
        cv2.FILLED,
    )
    cv2.putText(
        frame,
        text,
        (left, top + text_h),
        _FONT,
        _FONT_SCALE,
        _TEXT_COLOR,
        _FONT_THICKNESS,
        cv2.LINE_AA,
    )
    return frame


def draw_trajectory_on(
    frame: np.ndarray, points: Iterable[Point], colour: tuple[int, int, int]
) -> np.ndarray:
    """Draw one trajectory polyline, oldest point to newest (Requirement 3.6).

    A single point draws nothing but the reference marker the caller adds: a
    polyline needs two vertices, and a track on its first frame legitimately has
    only one recorded point.
    """
    height, width = int(frame.shape[0]), int(frame.shape[1])
    clipped = [
        (max(0, min(int(x), width - 1)), max(0, min(int(y), height - 1)))
        for x, y in points
    ]
    if len(clipped) >= 2:
        cv2.polylines(
            frame,
            [np.asarray(clipped, dtype=np.int32).reshape(-1, 1, 2)],
            False,
            colour,
            _TRAJECTORY_THICKNESS,
            cv2.LINE_AA,
        )
    return frame


def draw_tracks_on(
    frame: np.ndarray, tracks: Sequence[Track], *, trajectories: bool = True
) -> np.ndarray:
    """Draw the track layer onto ``frame`` in place and return it.

    Per active track: the bounding box, a label carrying the Track_ID and the
    Vehicle_Class, the trajectory polyline, and a marker at the current reference
    point (Requirement 3.6). The reference point is marked because it, not the
    box, is what the Approach_Assigner tests against a ROI polygon, so the
    annotated video shows the position that actually drives assignment.

    Trajectories are drawn before boxes so a box is never hidden under a line.
    """
    height, width = int(frame.shape[0]), int(frame.shape[1])

    if trajectories:
        for track in tracks:
            draw_trajectory_on(frame, track.trajectory, track_color(track.track_id))

    for track in tracks:
        colour = track_color(track.track_id)
        cv2.rectangle(
            frame,
            (track.x1, track.y1),
            (min(track.x2, width - 1), min(track.y2, height - 1)),
            colour,
            _BOX_THICKNESS,
        )
        ref_x, ref_y = track.ref_point
        cv2.circle(
            frame,
            (max(0, min(ref_x, width - 1)), max(0, min(ref_y, height - 1))),
            _REF_POINT_RADIUS,
            colour,
            cv2.FILLED,
        )
        draw_label_on(frame, track.label(), track.x1, track.y1, colour)

    return frame


def draw_tracks(
    frame: np.ndarray, tracks: Sequence[Track], *, trajectories: bool = True
) -> np.ndarray:
    """Return a copy of ``frame`` with the track layer drawn (Requirement 3.6).

    A copy rather than an in-place edit: the caller's frame is also the frame the
    Tracker and the later measurement stages read.
    """
    return draw_tracks_on(frame.copy(), tracks, trajectories=trajectories)


# ---------------------------------------------------------------------------
# Region layer: ROI polygons and queue regions (Requirement 12.1)
# ---------------------------------------------------------------------------


def approach_color(name: str) -> tuple[int, int, int]:
    """Return the colour of Approach ``name``, shared by all of its drawing."""
    return APPROACH_COLORS.get(name, APPROACH_FALLBACK_COLOR)


def queue_color(name: str) -> tuple[int, int, int]:
    """Return the Queue_Region colour of Approach ``name``.

    A darkened form of :func:`approach_color`, so the Queue_Region reads as part
    of its parent ROI while staying separable from it in a screenshot.
    """
    return tuple(int(round(channel * QUEUE_COLOR_SCALE))
                 for channel in approach_color(name))            # type: ignore[return-value]


def draw_regions_on(
    frame: np.ndarray, config: Config, *, labels: bool = True
) -> np.ndarray:
    """Draw the region layer onto ``frame`` in place and return it (Req 12.1).

    Per Approach: the ROI polygon outline in that Approach's colour, its
    Queue_Region outline in the darker queue colour, and the Approach name as a
    label. This is the *back* layer of the draw stack, so tracks and the panel
    drawn afterwards stay legible over it.

    Outlines rather than filled regions: a fill would hide the road surface and
    the vehicles the demo is meant to show. ``cv2.polylines`` clips coordinates
    outside the frame itself, so a configuration calibrated for a larger frame
    still draws what falls inside this one instead of raising.

    Drawn without anti-aliasing, unlike the trajectory polylines: these outlines
    mark where the measurement boundaries actually are, and a blended edge pixel
    would misreport a boundary by half a pixel in a screenshot.
    """
    for name in APPROACH_NAMES:
        approach = config.approach(name)
        colour = approach_color(name)
        cv2.polylines(
            frame,
            [as_cv_polygon(approach.roi_polygon)],
            True,
            colour,
            _ROI_THICKNESS,
            cv2.LINE_8,
        )
        cv2.polylines(
            frame,
            [as_cv_polygon(approach.queue_region)],
            True,
            queue_color(name),
            _QUEUE_THICKNESS,
            cv2.LINE_8,
        )
        if labels:
            # Anchored just inside the polygon's top-left extent, so the label
            # sits with its own region rather than over a neighbouring one.
            x = min(vertex[0] for vertex in approach.roi_polygon)
            y = min(vertex[1] for vertex in approach.roi_polygon)
            draw_label_on(frame, name, int(x), int(y), colour, above=False)
    return frame


def draw_regions(frame: np.ndarray, config: Config, *, labels: bool = True) -> np.ndarray:
    """Return a copy of ``frame`` with the region layer drawn (Requirement 12.1)."""
    return draw_regions_on(frame.copy(), config, labels=labels)


# ---------------------------------------------------------------------------
# Approach panel: the live per-approach statistics (Requirements 12.2, 15.4)
# ---------------------------------------------------------------------------


def approach_panel_row(name: str, metrics: ApproachMetrics, score: float) -> str:
    """Return the panel row of one Approach.

    Carries the Approach name, Vehicle_Count, Queue_Length, Vehicle_Density, and
    Score, the last two at two decimal places (Requirements 12.2, 15.4). Built as
    a plain string so the same text can go to the overlay and to a terminal
    summary without the two drifting apart.

    When the E8 queue forecast is populated, the projected queue is appended as
    ``f=<value>``, with a trailing ``^`` when the forecast exceeds the currently
    measured queue — i.e. when the predictor is actively anticipating growth rather
    than restating the present. Nothing is appended when the forecast is zero, so a
    base run's panel is unchanged.
    """
    row = (
        f"{name:<5} n={metrics.vehicle_count:<3d} q={metrics.queue_length:<3d} "
        f"d={metrics.vehicle_density:.2f} s={score:.2f}"
    )
    if metrics.normalized_forecast > 0.0:
        rising = "^" if metrics.normalized_forecast > metrics.normalized_queue else " "
        row += f" f={metrics.normalized_forecast:.2f}{rising}"
    return row


def draw_approach_panel_on(
    frame: np.ndarray,
    metrics: Mapping[str, ApproachMetrics],
    scores: Mapping[str, float],
    *,
    origin: tuple[int, int] = PANEL_ORIGIN,
    row_height: int = PANEL_ROW_HEIGHT,
) -> np.ndarray:
    """Draw one row per Approach onto ``frame`` in place (Requirements 12.2, 15.4).

    Rows are drawn in the fixed Approach order and each in its own Approach
    colour, so a row can be matched to its ROI polygon by colour alone. An
    Approach missing from ``metrics`` is skipped rather than defaulted to zero: a
    zero row would be indistinguishable from a genuinely empty Approach.

    ``origin`` and ``row_height`` are parameters so the full draw stack can place
    this panel alongside the banner and header it adds later without this layer
    knowing about them.
    """
    x, y = origin
    row = 0
    for name in APPROACH_NAMES:
        approach_metrics = metrics.get(name)
        if approach_metrics is None:
            continue
        draw_label_on(
            frame,
            approach_panel_row(name, approach_metrics, float(scores.get(name, 0.0))),
            x,
            y + row * row_height,
            approach_color(name),
        )
        row += 1
    return frame


def draw_measurements(
    frame: np.ndarray,
    tracks: Sequence[Track],
    metrics: Mapping[str, ApproachMetrics],
    scores: Mapping[str, float],
    config: Config,
    *,
    trajectories: bool = True,
) -> np.ndarray:
    """Return a copy of ``frame`` carrying the measurement overlay (Req 15.4).

    The three layers this module owns, drawn back to front on one canvas: regions,
    then tracks, then the approach panel. One copy is made for the whole stack, so
    the caller's frame — still the Tracker's and the Metrics_Engine's input — is
    untouched while no layer pays for a copy of its own.
    """
    annotated = frame.copy()
    draw_regions_on(annotated, config)
    draw_tracks_on(annotated, tracks, trajectories=trajectories)
    draw_approach_panel_on(annotated, metrics, scores)
    return annotated


# ---------------------------------------------------------------------------
# Tracking milestone entry point (Requirements 3.6, 15.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackingRunResult:
    """Outcome of one :func:`run_tracking_video` call.

    ``frames_written`` is reported separately from ``frames_processed`` so the
    one-output-frame-per-input-frame guarantee is an observable fact rather than
    an assumption; the two are equal on every successful run. ``track_ids`` is the
    set of distinct Track_IDs the run reported, which is what makes id
    persistence visible without decoding the output video.
    """

    info: VideoInfo
    out_path: str
    frames_processed: int
    frames_written: int
    tracks_drawn: int
    track_ids: frozenset[int]
    quit_early: bool

    @property
    def unique_track_count(self) -> int:
        """How many distinct Track_IDs the run reported."""
        return len(self.track_ids)


def default_track_output_path(
    video_path: str, out_dir: str = TRACK_VIDEO_DIR
) -> str:
    """Return the ``results/videos/`` path for a tracking run over ``video_path``.

    Derived from the input stem, so a run's output is traceable to its input
    without consulting a log (Requirement 15.3).
    """
    stem = Path(video_path).stem or "video"
    return str(Path(out_dir) / f"{stem}{TRACK_VIDEO_SUFFIX}.mp4")


def run_tracking_video(
    video_path: str,
    config: Config,
    out_path: str | None = None,
    *,
    detector: Detector | None = None,
    tracker: Tracker | None = None,
    display: bool = False,
) -> TrackingRunResult:
    """Track vehicles in ``video_path`` and write the annotated video.

    One output frame is written per processed input frame, each carrying every
    active track's box, Track_ID, Vehicle_Class, and trajectory polyline, at the
    input frame rate, under ``results/videos/`` by default (Requirements 3.6,
    15.3).

    ``detector`` and ``tracker`` are injection seams: passing fakes runs this
    entry point with no weights and no GPU, which is how the tests check the
    frame-count guarantee and the drawing. Passing ``tracker=None`` builds a
    :class:`~src.tracking.ByteTrackTracker`, so a weights failure surfaces as
    :class:`~src.errors.ModelError` naming the configured path (Requirement 2.6).

    ``detector`` may stay ``None``: Ultralytics' persistent ByteTrack detects and
    associates in one pass and ignores the ``detections`` argument entirely, so a
    separate detection pass would only load the same weights twice and run
    inference twice per frame. A tracker that *does* need detections — the
    deterministic fake, or any future association-only tracker — is driven by
    passing a detector alongside it.

    ``display=True`` additionally shows each annotated frame and honours
    ``config.quit_key``; the default is headless, since this entry point's
    product is the file on disk.

    The writer is opened from the first drawn frame's own shape rather than from
    container metadata, because ``cv2.VideoWriter`` silently discards a frame
    whose size differs from the one it was opened with — which would break the
    one-frame-per-frame guarantee without raising anything.
    """
    resolved_out_path = out_path or default_track_output_path(video_path)

    ingestor = VideoIngestor(video_path, config)
    info = ingestor.info
    owns_tracker = tracker is None
    if tracker is None:
        tracker = ByteTrackTracker(config)

    delay_ms = max(1, int(round(1000.0 / info.frame_rate)))
    quit_codes = {ord(config.quit_key.lower()), ord(config.quit_key.upper())}

    writer: cv2.VideoWriter | None = None
    writer_size: tuple[int, int] = (0, 0)
    frames_processed = 0
    frames_written = 0
    tracks_drawn = 0
    track_ids: set[int] = set()
    quit_early = False
    try:
        for _index, frame in ingestor.frames():
            detections = detector.detect(frame) if detector is not None else []
            tracks = tracker.update(frame, detections)
            annotated = draw_tracks(frame, tracks)

            frames_processed += 1
            tracks_drawn += len(tracks)
            track_ids.update(track.track_id for track in tracks)

            if writer is None:
                writer_size = (annotated.shape[1], annotated.shape[0])
                writer = open_annotated_writer(
                    resolved_out_path, info.frame_rate, writer_size
                )
            elif (annotated.shape[1], annotated.shape[0]) != writer_size:
                # A mid-video resolution change would otherwise be dropped
                # silently; resizing keeps the frame counts equal.
                annotated = cv2.resize(annotated, writer_size)
            writer.write(annotated)
            frames_written += 1

            if display:
                try:
                    cv2.imshow(TRACK_WINDOW_NAME, annotated)
                    key = cv2.waitKey(delay_ms) & 0xFF
                except cv2.error as exc:
                    raise VideoError(
                        f"display mode is unavailable in this OpenCV build, "
                        f"rerun with display disabled: {video_path}: {exc}"
                    ) from exc
                if key in quit_codes:
                    quit_early = True
                    break
    finally:
        if writer is not None:
            writer.release()
        ingestor.close()
        if owns_tracker:
            close = getattr(tracker, "close", None)
            if callable(close):
                close()

    if writer is None:
        raise VideoError(f"video yielded no frames to annotate: {video_path}")

    return TrackingRunResult(
        info=info,
        out_path=resolved_out_path,
        frames_processed=frames_processed,
        frames_written=frames_written,
        tracks_drawn=tracks_drawn,
        track_ids=frozenset(track_ids),
        quit_early=quit_early,
    )


# ---------------------------------------------------------------------------
# Measurement milestone entry point (Requirement 15.4)
# ---------------------------------------------------------------------------

#: Title of the window opened when :func:`run_measurement_video` displays frames.
MEASURE_WINDOW_NAME = "Adaptive Traffic Signal - measure"

#: Suffix appended to the input stem when a measurement run is asked to write a
#: video, so it cannot overwrite a detection or tracking run's output.
MEASURE_VIDEO_SUFFIX = "__measurements"

#: The Signal_State vector this milestone measures against. No controller exists
#: yet, and the Metrics_Engine gates Waiting_Time on the state in force
#: (Requirements 5.5, 5.6), so every Approach reads RED — exactly the state the
#: Phase_Sequencer itself reports before its first Cycle (Requirement 10.5). The
#: ``control`` entry point replaces this with the sequencer's own vector.
ALL_RED_STATES: dict[str, str] = {name: RED for name in APPROACH_NAMES}


@dataclass(frozen=True)
class MeasurementRunResult:
    """Outcome of one :func:`run_measurement_video` call.

    ``metrics`` and ``scores`` are the *final* frame's values, which is what a
    caller can assert about without decoding the output video. ``out_path`` is
    ``None`` when the run was asked to draw and display only, since this
    milestone's product is the live display rather than a file.
    """

    info: VideoInfo
    out_path: str | None
    frames_processed: int
    frames_written: int
    tracks_drawn: int
    unassigned_tracks: int
    metrics: dict[str, ApproachMetrics]
    scores: dict[str, float]
    waiting_times: dict[int, float]
    overlap_events: tuple[OverlapEvent, ...]
    quit_early: bool


def default_measure_output_path(
    video_path: str, out_dir: str = TRACK_VIDEO_DIR
) -> str:
    """Return the ``results/videos/`` path for a measurement run over ``video_path``."""
    stem = Path(video_path).stem or "video"
    return str(Path(out_dir) / f"{stem}{MEASURE_VIDEO_SUFFIX}.mp4")


def run_measurement_video(
    video_path: str,
    config: Config,
    out_path: str | None = None,
    *,
    detector: Detector | None = None,
    tracker: Tracker | None = None,
    display: bool = True,
) -> MeasurementRunResult:
    """Measure ``video_path`` per Approach and draw the result (Requirement 15.4).

    Per frame: track, assign each track to an Approach, measure the four
    :class:`~src.traffic_metrics.ApproachMetrics`, score them under the configured
    Alpha, and draw regions, tracks, and the approach panel. This is the pipeline
    of task 11 minus the controller — the stage order is the same, which is what
    makes this milestone a real rehearsal of it rather than a separate code path.

    ``display`` defaults to ``True`` because the deliverable here is the live
    view; ``out_path`` defaults to ``None`` and no video is written unless one is
    supplied. ``detector`` and ``tracker`` are the same injection seams as in
    :func:`run_tracking_video`, so this entry point runs with no weights and no
    GPU in tests.

    Waiting_Time accumulates against an all-RED Signal_State vector
    (:data:`ALL_RED_STATES`): no controller has been built at this milestone, and
    RED is what the Phase_Sequencer reports before its first Cycle anyway, so the
    numbers here are the "nobody is being served" baseline.
    """
    ingestor = VideoIngestor(video_path, config)
    info = ingestor.info
    assigner = ApproachAssigner(config)
    engine = MetricsEngine(config)

    owns_tracker = tracker is None
    if tracker is None:
        tracker = ByteTrackTracker(config)

    # The simulated duration of one frame, so Waiting_Time is measured in video
    # time rather than wall-clock time (Requirement 5.5).
    dt = 1.0 / info.frame_rate
    delay_ms = max(1, int(round(1000.0 / info.frame_rate)))
    quit_codes = {ord(config.quit_key.lower()), ord(config.quit_key.upper())}

    writer: cv2.VideoWriter | None = None
    writer_size: tuple[int, int] = (0, 0)
    frames_processed = 0
    frames_written = 0
    tracks_drawn = 0
    unassigned_tracks = 0
    metrics: dict[str, ApproachMetrics] = {}
    scores: dict[str, float] = {}
    overlap_events: list[OverlapEvent] = []
    quit_early = False
    try:
        for index, frame in ingestor.frames():
            detections = detector.detect(frame) if detector is not None else []
            tracks = tracker.update(frame, detections)
            assigned, overlaps = assigner.assign(tracks, frame_index=index)
            metrics = engine.update(assigned, ALL_RED_STATES, dt)
            scores = compute_config_scores(metrics, config)
            annotated = draw_measurements(frame, tracks, metrics, scores, config)

            frames_processed += 1
            tracks_drawn += len(tracks)
            unassigned_tracks += sum(1 for track in assigned if track.approach is None)
            overlap_events.extend(overlaps)

            if out_path is not None:
                if writer is None:
                    writer_size = (annotated.shape[1], annotated.shape[0])
                    writer = open_annotated_writer(
                        out_path, info.frame_rate, writer_size
                    )
                elif (annotated.shape[1], annotated.shape[0]) != writer_size:
                    # A mid-video resolution change would otherwise be dropped
                    # silently; resizing keeps the frame counts equal.
                    annotated = cv2.resize(annotated, writer_size)
                writer.write(annotated)
                frames_written += 1

            if display:
                try:
                    cv2.imshow(MEASURE_WINDOW_NAME, annotated)
                    key = cv2.waitKey(delay_ms) & 0xFF
                except cv2.error as exc:
                    raise VideoError(
                        f"display mode is unavailable in this OpenCV build, "
                        f"rerun with display disabled: {video_path}: {exc}"
                    ) from exc
                if key in quit_codes:
                    quit_early = True
                    break
    finally:
        if writer is not None:
            writer.release()
        ingestor.close()
        if owns_tracker:
            close = getattr(tracker, "close", None)
            if callable(close):
                close()

    if frames_processed == 0:                # pragma: no cover - first frame is validated
        raise VideoError(f"video yielded no frames to measure: {video_path}")

    return MeasurementRunResult(
        info=info,
        out_path=out_path,
        frames_processed=frames_processed,
        frames_written=frames_written,
        tracks_drawn=tracks_drawn,
        unassigned_tracks=unassigned_tracks,
        metrics=metrics,
        scores=scores,
        waiting_times=engine.waiting_times,
        overlap_events=tuple(overlap_events),
        quit_early=quit_early,
    )


# ---------------------------------------------------------------------------
# Signal layers: lights, banner, header (Requirements 12.3, 12.4, 12.5, 12.7)
# ---------------------------------------------------------------------------

#: Lamp colour per Signal_State, BGR. The lit lamp of an Approach's glyph takes
#: its state's colour here; the other two are darkened by
#: :data:`UNLIT_LAMP_SCALE`, so a glyph reads as a traffic light with one lamp on
#: rather than as three unrelated dots (Requirement 12.3).
SIGNAL_LAMP_COLORS: dict[str, tuple[int, int, int]] = {
    "RED": (0, 0, 255),
    "YELLOW": (0, 255, 255),
    "GREEN": (0, 255, 0),
}

#: The order lamps are drawn in, top of the light to bottom — the order a real
#: three-aspect signal head uses.
LAMP_ORDER: tuple[str, ...] = ("RED", "YELLOW", "GREEN")

#: How much an unlit lamp is darkened relative to its lit colour.
UNLIT_LAMP_SCALE = 0.25

_LAMP_RADIUS = 4
_LAMP_SPACING = 11
_LAMP_OUTLINE = (30, 30, 30)

#: Where the header, the phase banner, and the per-approach panel sit in the full
#: draw stack. The header and banner take the top two rows and the four panel rows
#: follow, so the whole textual overlay is one block in the top-left corner and the
#: middle of the frame — where a junction's ROI polygons meet — stays clear.
HEADER_ORIGIN: tuple[int, int] = (4, 16)
BANNER_ORIGIN: tuple[int, int] = (4, 16 + PANEL_ROW_HEIGHT)
SIGNAL_PANEL_ORIGIN: tuple[int, int] = (4, 16 + 2 * PANEL_ROW_HEIGHT)

#: Horizontal offset of the traffic-light glyph column from the panel's left edge,
#: wide enough to clear the longest panel row at the overlay's font scale. OpenCV
#: clips drawing that falls outside the frame, so a frame narrower than this still
#: renders the panel and simply loses the glyphs rather than raising.
LIGHT_COLUMN_OFFSET = 232

#: Colour of the header and banner strips. Neutral rather than per-Approach: both
#: describe the run as a whole, and an Approach colour would suggest otherwise.
HEADER_COLOR: tuple[int, int, int] = (235, 235, 235)
BANNER_COLOR: tuple[int, int, int] = (255, 255, 255)

#: Radius of the ring drawn around a queueing track's reference point. Larger than
#: the reference marker itself so it reads as an annotation of that marker.
QUEUE_RING_RADIUS = 7

#: Title of the window opened by the control entry point (Requirement 15.5).
CONTROL_WINDOW_NAME = "Adaptive Traffic Signal - control"

#: Suffix appended to the input stem for a control run's annotated video, so it
#: cannot overwrite a detection, tracking, or measurement run's output.
CONTROL_VIDEO_SUFFIX = "__control"


def default_control_output_path(
    video_path: str, out_dir: str = TRACK_VIDEO_DIR
) -> str:
    """Return the ``results/videos/`` path for a control run over ``video_path``."""
    stem = Path(video_path).stem or "video"
    return str(Path(out_dir) / f"{stem}{CONTROL_VIDEO_SUFFIX}.mp4")


def lamp_color(state: str, *, lit: bool) -> tuple[int, int, int]:
    """Return the colour of one lamp of a traffic-light glyph.

    ``lit`` selects between the state's own colour and a darkened form of it, so an
    unlit lamp still shows *which* lamp it is instead of vanishing into the frame.
    """
    colour = SIGNAL_LAMP_COLORS.get(str(state), APPROACH_FALLBACK_COLOR)
    if lit:
        return colour
    return tuple(int(round(channel * UNLIT_LAMP_SCALE)) for channel in colour)  # type: ignore[return-value]


def draw_signal_light_on(
    frame: np.ndarray, x: int, y: int, state: str
) -> np.ndarray:
    """Draw one three-aspect traffic-light glyph with ``state`` lit (Req 12.3).

    ``(x, y)`` is the centre of the first lamp; the three lamps run left to right in
    :data:`LAMP_ORDER`. Horizontal rather than vertical because the glyph sits at
    the end of its Approach's panel row, and a vertical head would span three rows
    and pair with the wrong Approach.
    """
    for index, lamp in enumerate(LAMP_ORDER):
        centre = (int(x + index * _LAMP_SPACING), int(y))
        cv2.circle(frame, centre, _LAMP_RADIUS, lamp_color(lamp, lit=lamp == str(state)),
                   cv2.FILLED, cv2.LINE_AA)
        cv2.circle(frame, centre, _LAMP_RADIUS, _LAMP_OUTLINE, 1, cv2.LINE_AA)
    return frame


def draw_signal_lights_on(
    frame: np.ndarray,
    signal_states: Mapping[str, object],
    *,
    origin: tuple[int, int] = SIGNAL_PANEL_ORIGIN,
    row_height: int = PANEL_ROW_HEIGHT,
    column_offset: int = LIGHT_COLUMN_OFFSET,
) -> np.ndarray:
    """Draw one traffic-light glyph per Approach in place (Requirement 12.3).

    Rows follow the same order and spacing as :func:`draw_approach_panel_on`, so
    each glyph lands at the end of its own Approach's row. ``signal_states`` values
    may be :class:`~src.signal_controller.SignalState` members or the plain strings
    the Run_Log carries; both are read through ``str``, so the overlay never
    depends on which of the two a caller holds.
    """
    x, y = origin
    for row, name in enumerate(APPROACH_NAMES):
        state = signal_states.get(name, "RED")
        state_name = getattr(state, "value", str(state))
        draw_signal_light_on(
            frame,
            x + column_offset,
            y + row * row_height - _LAMP_RADIUS,
            state_name,
        )
    return frame


def signal_banner_text(phase_info: object) -> str:
    """Return the phase banner's text (Requirements 12.4, 12.7).

    While an Approach holds GREEN the banner names it with the Green_Time assigned
    to the Cycle and the seconds still to run (Requirement 12.4). On the Cycle where
    the served Approach changes, the Score that produced the selection is appended,
    which is what makes an adaptive decision legible in a screenshot
    (Requirement 12.7). A starvation override is called out by name, since the
    Score alone would not explain that choice (Requirement 9.6).

    Built as a plain string, like :func:`approach_panel_row`, so a headless run can
    report exactly what the displayed run drew.
    """
    approach = getattr(phase_info, "approach", None)
    if approach is None:
        return "no cycle yet - all approaches RED"

    state = getattr(phase_info, "state", "RED")
    state_name = getattr(state, "value", str(state))
    text = (
        f"{approach} {state_name} "
        f"{getattr(phase_info, 'remaining_seconds', 0.0):.1f}s left "
        f"of {getattr(phase_info, 'green_time', 0.0):.0f}s green"
    )
    if getattr(phase_info, "selection_changed", False):
        text += f" | selected on score {getattr(phase_info, 'selection_score', 0.0):.2f}"
    if getattr(phase_info, "starvation_override", False):
        text += " | starvation override"
    return text


def signal_header_text(
    controller_name: str, simulated_time: float, phase_info: object | None = None
) -> str:
    """Return the header's text: controller name and Simulated_Clock (Req 12.5).

    The Cycle number is included when a :class:`~src.signal_controller.PhaseInfo` is
    supplied. It is not required by Requirement 12.5, but it is what makes two
    screenshots of the same run orderable by eye.
    """
    text = f"controller: {controller_name} | t={float(simulated_time):.2f}s"
    if phase_info is not None:
        cycle_index = int(getattr(phase_info, "cycle_index", -1))
        if cycle_index >= 0:
            text += f" | cycle {cycle_index + 1}"
    return text


def draw_banner_on(
    frame: np.ndarray,
    phase_info: object,
    *,
    origin: tuple[int, int] = BANNER_ORIGIN,
) -> np.ndarray:
    """Draw the phase banner in place (Requirements 12.4, 12.7)."""
    return draw_label_on(frame, signal_banner_text(phase_info), origin[0], origin[1],
                         BANNER_COLOR)


def draw_header_on(
    frame: np.ndarray,
    controller_name: str,
    simulated_time: float,
    phase_info: object | None = None,
    *,
    origin: tuple[int, int] = HEADER_ORIGIN,
) -> np.ndarray:
    """Draw the run header in place (Requirement 12.5)."""
    return draw_label_on(
        frame,
        signal_header_text(controller_name, simulated_time, phase_info),
        origin[0],
        origin[1],
        HEADER_COLOR,
    )


def draw_queue_markers_on(
    frame: np.ndarray, assigned: Sequence[AssignedTrack]
) -> np.ndarray:
    """Ring the reference point of every queueing track in place.

    Not required by Requirement 12, but the Queue_Length in the panel is only
    checkable against the picture if the tracks it counted are identifiable. The
    ring is drawn in the assigned Approach's queue colour, so a miscalibrated
    Queue_Region shows up as rings in the wrong place — which is exactly the
    calibration check task 14 performs by eye.
    """
    for track in assigned:
        if track.approach is None or not track.is_queueing:
            continue
        cv2.circle(
            frame,
            (int(track.ref_point[0]), int(track.ref_point[1])),
            QUEUE_RING_RADIUS,
            queue_color(track.approach),
            1,
            cv2.LINE_AA,
        )
    return frame


# ---------------------------------------------------------------------------
# Research demo layer: the measurements the controller actually reads
# ---------------------------------------------------------------------------

#: Top-left corner of the demo dashboard. On the Bellevue camera this is trees and a
#: building roof, so the panel hides no road.
DEMO_PANEL_ORIGIN = (8, 8)
DEMO_ROW_HEIGHT = 22
DEMO_COLUMNS = ("leg", "sig", "n", "D", "Q", "X", "S", "F", "score")
_DEMO_COLUMN_X = (8, 70, 96, 120, 160, 200, 270, 340, 380)
_SIGNAL_GLYPH = {"GREEN": ("G", (0, 200, 0)), "YELLOW": ("Y", (0, 215, 255)), "RED": ("R", (0, 0, 220))}


def _axis_point(axis, fraction: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Image point at ``fraction`` along a drawn queue axis, and the axis normal there."""
    segments = getattr(axis, "_polyline", None)
    if not segments:
        ux, uy = axis.upstream
        d = fraction * axis.length
        return (axis.origin[0] + ux * d, axis.origin[1] + uy * d), (-uy, ux)
    target = fraction * axis.length
    for ax, ay, ux, uy, seg, start in segments:
        if target <= start + seg:
            t = target - start
            return (ax + ux * t, ay + uy * t), (-uy, ux)
    ax, ay, ux, uy, seg, _ = segments[-1]
    return (ax + ux * seg, ay + uy * seg), (-uy, ux)


def draw_queue_tails_on(frame: np.ndarray, axes: Mapping[str, object], metrics: Mapping[str, ApproachMetrics]) -> np.ndarray:
    """Draw each queue axis and a red bar at its measured queue reach X, in place."""
    for name, axis in axes.items():
        segments = getattr(axis, "_polyline", None)
        if segments:
            points = [(int(a), int(b)) for a, b, *_ in segments]
            last = segments[-1]
            points.append((int(last[0] + last[2] * last[4]), int(last[1] + last[3] * last[4])))
            cv2.polylines(frame, [np.array(points, np.int32)], False, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.circle(frame, points[0], 4, (0, 0, 255), -1, cv2.LINE_AA)
        m = metrics.get(name)
        if m is None or m.queue_reach <= 0.0:
            continue
        (px, py), (nx, ny) = _axis_point(axis, m.queue_reach)
        cv2.line(frame, (int(px - 22 * nx), int(py - 22 * ny)), (int(px + 22 * nx), int(py + 22 * ny)),
                 (0, 0, 255), 4, cv2.LINE_AA)
    return frame


def demo_panel_rows(
    metrics: Mapping[str, ApproachMetrics],
    scores: Mapping[str, float],
    signal_states: Mapping[str, object],
) -> list[tuple[str, ...]]:
    """The dashboard rows as text cells, in Approach order (also used by tests)."""
    rows = []
    for name in APPROACH_NAMES:
        m = metrics.get(name)
        if m is None:
            continue
        state = getattr(signal_states.get(name), "value", signal_states.get(name, "RED"))
        rows.append((
            name, str(state)[0], str(m.vehicle_count), f"{m.vehicle_density:.2f}",
            f"{m.normalized_queue:.2f}", f"{m.queue_reach:.2f}", f"{m.spillback_risk:.2f}",
            f"{m.normalized_forecast:.2f}", f"{float(scores.get(name, 0.0)):.2f}",
        ))
    return rows


def draw_demo_panel_on(
    frame: np.ndarray,
    metrics: Mapping[str, ApproachMetrics],
    scores: Mapping[str, float],
    signal_states: Mapping[str, object],
    phase_info: object,
    controller_name: str,
    simulated_time: float,
) -> np.ndarray:
    """Draw the research dashboard in place.

    Shows, per Approach, exactly the measurements the Score is built from (D density,
    Q queue, X queue reach, S spillback risk, F count forecast) with bars for the two
    spatial terms, the Score itself, and the signal; the served Approach is
    highlighted and the header states the phase and its remaining green.
    """
    x0, y0 = DEMO_PANEL_ORIGIN
    rows = demo_panel_rows(metrics, scores, signal_states)
    width, height = 420, DEMO_ROW_HEIGHT * (len(rows) + 4) + 8
    roi = frame[y0:y0 + height, x0:x0 + width]
    roi[:] = (roi * 0.35).astype(roi.dtype)

    active = getattr(phase_info, "approach", None)
    state = getattr(getattr(phase_info, "state", None), "value", "")
    remaining = float(getattr(phase_info, "remaining_seconds", 0.0))
    green = float(getattr(phase_info, "green_time", 0.0))
    font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.45
    cv2.putText(frame, f"{controller_name}   t = {simulated_time:6.1f} s", (x0 + 8, y0 + 18),
                font, scale, (255, 255, 255), 1, cv2.LINE_AA)
    if active:
        cv2.putText(frame, f"{state} {active}: {remaining:4.1f} s left of {green:.0f} s",
                    (x0 + 8, y0 + 18 + DEMO_ROW_HEIGHT), font, scale, (0, 255, 255), 1, cv2.LINE_AA)

    y = y0 + 18 + 2 * DEMO_ROW_HEIGHT
    for cx, title in zip(_DEMO_COLUMN_X, DEMO_COLUMNS):
        cv2.putText(frame, title, (x0 + cx, y), font, scale, (200, 200, 200), 1, cv2.LINE_AA)
    best = max(scores, key=lambda n: scores[n]) if scores else None
    for row in rows:
        y += DEMO_ROW_HEIGHT
        name = row[0]
        if name == active:
            cv2.rectangle(frame, (x0 + 2, y - 15), (x0 + width - 4, y + 6), (60, 90, 60), -1)
        state_value = getattr(signal_states.get(name), "value", str(signal_states.get(name)))
        cell_colors = [approach_color(name), _SIGNAL_GLYPH.get(state_value, ("", (255, 255, 255)))[1]]
        for i, (cx, cell) in enumerate(zip(_DEMO_COLUMN_X, row)):
            text_color = cell_colors[i] if i < 2 else (255, 255, 255)
            cv2.putText(frame, cell, (x0 + cx, y), font, scale, text_color, 1, cv2.LINE_AA)
        # bars for X and S (the spatial measures)
        for col, value in ((5, float(row[5])), (6, float(row[6]))):
            bx = x0 + _DEMO_COLUMN_X[col] + 36
            cv2.rectangle(frame, (bx, y - 10), (bx + 28, y - 2), (90, 90, 90), 1)
            cv2.rectangle(frame, (bx, y - 10), (bx + int(28 * value), y - 2),
                          (0, 0, 255) if col == 6 else (0, 165, 255), -1)
        if name == best:
            cv2.putText(frame, "*", (x0 + width - 12, y), font, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    y += DEMO_ROW_HEIGHT
    cv2.putText(frame, "D density  Q queue  X queue reach (spatial)", (x0 + 8, y),
                font, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.putText(frame, "S spillback risk  F count forecast  * highest score", (x0 + 8, y + 16),
                font, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
    return frame


class SignalOverlay:
    """The full draw stack of one run (Requirements 3.6, 12.1-12.5, 12.7).

    One instance per run, holding the configuration the region layer reads so the
    polygons are not re-resolved per frame. :meth:`draw` returns a new frame and
    never mutates its argument, because the frame it is handed is the one the
    Detector and the Tracker were given.

    Layers, back to front, each on the same canvas:

    1. ROI polygons and Queue_Regions with per-Approach labels (Requirement 12.1)
    2. track boxes, Track_IDs, Vehicle_Classes, trajectory polylines (Req 3.6)
    3. rings on the tracks counted as queueing
    4. the per-Approach panel: name, Vehicle_Count, Queue_Length, Score (Req 12.2)
    5. a traffic-light glyph per Approach, coloured by Signal_State (Req 12.3)
    6. the phase banner (Requirements 12.4, 12.7)
    7. the header: controller name and Simulated_Clock (Requirement 12.5)

    The order matters in one direction only: text must come after the geometry it
    would otherwise be hidden by. Requirement 12.6 — writing the drawn frames to
    ``results/videos/`` — belongs to :class:`AnnotatedVideoWriter` rather than here,
    so drawing stays a pure frame transformation.
    """

    def __init__(self, config: Config, style: str = "classic") -> None:
        if style not in ("classic", "demo"):
            raise ValueError(f"overlay style must be 'classic' or 'demo', got {style!r}")
        self._config = config
        self._style = style
        self._axes = None
        if style == "demo":
            from src.lane_analysis import build_approach_axes

            self._axes = build_approach_axes(config)

    @property
    def config(self) -> Config:
        """The configuration whose geometry this overlay draws."""
        return self._config

    def draw(
        self,
        frame: np.ndarray,
        tracks: Sequence[Track],
        assigned: Sequence[AssignedTrack],
        metrics: Mapping[str, ApproachMetrics],
        scores: Mapping[str, float],
        signal_states: Mapping[str, object],
        phase_info: object,
        controller_name: str,
        simulated_time: float,
        *,
        trajectories: bool = True,
    ) -> np.ndarray:
        """Return a new frame carrying the complete overlay for one frame."""
        annotated = frame.copy()
        if self._style == "demo":
            draw_regions_on(annotated, self._config, labels=False)
            draw_tracks_on(annotated, tracks, trajectories=trajectories)
            draw_queue_tails_on(annotated, self._axes or {}, metrics)
            draw_demo_panel_on(annotated, metrics, scores, signal_states, phase_info,
                               controller_name, simulated_time)
            return annotated
        draw_regions_on(annotated, self._config)
        draw_tracks_on(annotated, tracks, trajectories=trajectories)
        draw_queue_markers_on(annotated, assigned)
        draw_approach_panel_on(
            annotated, metrics, scores, origin=SIGNAL_PANEL_ORIGIN
        )
        draw_signal_lights_on(annotated, signal_states, origin=SIGNAL_PANEL_ORIGIN)
        draw_banner_on(annotated, phase_info)
        draw_header_on(annotated, controller_name, simulated_time, phase_info)
        return annotated


# ---------------------------------------------------------------------------
# Annotated video writing (Requirement 12.6)
# ---------------------------------------------------------------------------


class AnnotatedVideoWriter:
    """Writes drawn frames to a video under ``results/videos/`` (Req 12.6).

    Opened lazily from the first frame's own shape rather than from container
    metadata: ``cv2.VideoWriter`` silently discards a frame whose size differs from
    the size it was opened with, which would break the one-output-frame-per-input-frame
    guarantee without raising anything. A later frame of a different size is resized
    to match, for the same reason.

    Usable as a context manager, so the capture and the writer are both released
    even when the run raises — the pattern the tracking and measurement entry points
    already follow with a ``finally`` block.
    """

    def __init__(self, out_path: str, frame_rate: float) -> None:
        self._out_path = str(out_path)
        self._frame_rate = float(frame_rate)
        self._writer: cv2.VideoWriter | None = None
        self._size: tuple[int, int] = (0, 0)
        self._frames_written = 0
        self._closed = False

    @property
    def out_path(self) -> str:
        """Where the annotated video is being written."""
        return self._out_path

    @property
    def frames_written(self) -> int:
        """How many frames have been written so far (Requirement 12.6)."""
        return self._frames_written

    @property
    def size(self) -> tuple[int, int]:
        """The ``(width, height)`` the writer was opened with, ``(0, 0)`` if unopened."""
        return self._size

    @property
    def is_open(self) -> bool:
        """Whether a frame has been written and the writer is not yet released."""
        return self._writer is not None and not self._closed

    def write(self, frame: np.ndarray) -> None:
        """Write one drawn frame, opening the writer on the first call."""
        if self._closed:
            raise VideoError(f"annotated video writer is already closed: {self._out_path}")

        if self._writer is None:
            self._size = (int(frame.shape[1]), int(frame.shape[0]))
            self._writer = open_annotated_writer(
                self._out_path, self._frame_rate, self._size
            )
        elif (int(frame.shape[1]), int(frame.shape[0])) != self._size:
            frame = cv2.resize(frame, self._size)

        self._writer.write(frame)
        self._frames_written += 1

    def close(self) -> None:
        """Release the writer. Idempotent, so a ``finally`` block may always call it."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        self._closed = True

    def __enter__(self) -> "AnnotatedVideoWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = [
    "ALL_RED_STATES",
    "APPROACH_COLORS",
    "APPROACH_FALLBACK_COLOR",
    "AnnotatedVideoWriter",
    "BANNER_COLOR",
    "BANNER_ORIGIN",
    "CONTROL_VIDEO_SUFFIX",
    "CONTROL_WINDOW_NAME",
    "HEADER_COLOR",
    "HEADER_ORIGIN",
    "LAMP_ORDER",
    "LIGHT_COLUMN_OFFSET",
    "MEASURE_VIDEO_SUFFIX",
    "MEASURE_WINDOW_NAME",
    "PANEL_ORIGIN",
    "PANEL_ROW_HEIGHT",
    "QUEUE_COLOR_SCALE",
    "QUEUE_RING_RADIUS",
    "SIGNAL_LAMP_COLORS",
    "SIGNAL_PANEL_ORIGIN",
    "UNLIT_LAMP_SCALE",
    "MeasurementRunResult",
    "SignalOverlay",
    "default_control_output_path",
    "draw_banner_on",
    "draw_header_on",
    "draw_queue_markers_on",
    "draw_signal_light_on",
    "draw_signal_lights_on",
    "lamp_color",
    "signal_banner_text",
    "signal_header_text",
    "TRACK_COLORS",
    "TRACK_VIDEO_DIR",
    "TRACK_VIDEO_SUFFIX",
    "TRACK_WINDOW_NAME",
    "TrackingRunResult",
    "approach_color",
    "approach_panel_row",
    "default_measure_output_path",
    "default_track_output_path",
    "draw_approach_panel_on",
    "draw_label_on",
    "draw_measurements",
    "draw_regions",
    "draw_regions_on",
    "draw_tracks",
    "draw_tracks_on",
    "draw_trajectory_on",
    "queue_color",
    "run_measurement_video",
    "run_tracking_video",
    "track_color",
]
