"""Detector — per-frame vehicle detection contract and its data model.

This module owns the *shape* of a detection: an axis-aligned box in image
coordinates, one of the four Vehicle_Classes, and a confidence in 0..1
(Requirement 2.1). Everything downstream — the Tracker, the Approach_Assigner,
the Metrics_Engine — reads only these fields, so the concrete detector is
interchangeable.

:class:`Detector` is a ``Protocol`` rather than a base class for exactly that
reason: the real Ultralytics YOLO detector needs weights and, in practice, a GPU,
while a scripted fake needs neither. Tests inject the fake and the whole
downstream pipeline runs headless (see ``tests/fixtures.FakeDetector``).

:class:`Detection` validates itself on construction. A detector that produces a
box outside the frame, a zero-width box, an unknown class, or a confidence
outside 0..1 violates Requirements 2.1, 2.2, and 2.5, and the failure is far
cheaper to diagnose here than three stages later as a nonsensical queue count.
Frame-bounds clipping itself belongs to the concrete detector, since only it
knows the frame size.

:class:`YoloDetector` is that concrete detector: it loads pretrained Ultralytics
weights from ``config.model_path`` and applies the class, confidence, and
geometry filters of Requirements 2.2, 2.3, and 2.5 to what the model returns. The
``ultralytics`` import is deliberately local to the loader, so this module — and
therefore every fake and every downstream stage — imports without it installed.

:func:`run_detection_video` is the detection-only milestone entry point
(Requirements 2.7, 15.2): it draws every detection as a labelled box and writes
one output frame per processed input frame to ``results/videos/`` at the input
frame rate.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import cv2
import numpy as np

from src.config import VEHICLE_CLASSES as _CONFIG_VEHICLE_CLASSES
from src.config import Config
from src.errors import ModelError, VideoError
from src.video_io import VideoIngestor, VideoInfo

#: The Vehicle_Classes the Detector may return (Requirement 2.2).
#:
#: Re-exported from :mod:`src.config` rather than redeclared, so the class set the
#: Detector filters to and the set ``pce_weights`` must cover cannot drift apart.
#: The import direction is fixed by ``YoloDetector`` needing ``Config``.
VEHICLE_CLASSES: tuple[str, ...] = _CONFIG_VEHICLE_CLASSES


@dataclass(frozen=True)
class Detection:
    """One detected vehicle in one frame (Requirement 2.1).

    Coordinates are integer pixel positions with ``(x1, y1)`` the top-left and
    ``(x2, y2)`` the bottom-right corner, exclusive on the lower-right so that
    ``width``/``height`` are the pixel extents. Both extents are strictly
    positive (Requirement 2.5).
    """

    x1: int
    y1: int
    x2: int
    y2: int
    vehicle_class: str          # in VEHICLE_CLASSES
    confidence: float           # 0..1

    def __post_init__(self) -> None:
        for field in ("x1", "y1", "x2", "y2"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"Detection.{field} must be an int, got {value!r}"
                )
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(
                "Detection box must have positive width and height, got "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )
        if self.x1 < 0 or self.y1 < 0:
            raise ValueError(
                f"Detection box must lie within the frame, got "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )
        if self.vehicle_class not in VEHICLE_CLASSES:
            raise ValueError(
                f"Detection.vehicle_class must be one of {VEHICLE_CLASSES}, "
                f"got {self.vehicle_class!r}"
            )
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError(
                f"Detection.confidence must be a number, got {self.confidence!r}"
            )
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Detection.confidence must lie in 0..1, got {self.confidence!r}"
            )

    @property
    def width(self) -> int:
        """Box width in pixels, always greater than 0 (Requirement 2.5)."""
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        """Box height in pixels, always greater than 0 (Requirement 2.5)."""
        return self.y2 - self.y1

    @property
    def box(self) -> tuple[int, int, int, int]:
        """The box as ``(x1, y1, x2, y2)``, for the Tracker hand-off."""
        return (self.x1, self.y1, self.x2, self.y2)

    def label(self) -> str:
        """Short caption drawn on annotated frames (Requirement 2.7)."""
        return f"{self.vehicle_class} {self.confidence:.2f}"


@runtime_checkable
class Detector(Protocol):
    """Anything that turns a frame into a list of :class:`Detection` values.

    ``detect`` returns zero or more detections and must not mutate ``frame``:
    the same frame object is handed to the Tracker and the Signal_Overlay after
    detection, so an in-place edit here would corrupt both.
    """

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Return the detections found in ``frame`` (Requirement 2.1)."""
        ...


#: COCO class id -> Vehicle_Class, for the four classes this project measures
#: (Requirement 2.2). Every other COCO id is discarded, so a pedestrian, a
#: bicycle, or a traffic light never reaches the Metrics_Engine as a vehicle.
#: The ids are those of the standard COCO 80-class label set the pretrained
#: Ultralytics weights are trained on.
COCO_VEHICLE_CLASS_IDS: dict[int, str] = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


def to_numpy(value: Any) -> np.ndarray:
    """Return ``value`` as a NumPy array, whatever tensor library produced it.

    Ultralytics returns torch tensors that may live on a GPU; a stubbed model
    result in a test returns plain arrays or lists. Detaching and moving to host
    memory when those methods exist keeps both paths working without importing
    torch, which is not a declared dependency of this project.
    """
    for step in ("detach", "cpu", "numpy"):
        method = getattr(value, step, None)
        if callable(method):
            value = method()
    return np.asarray(value)


def iter_results(results: Any) -> list[Any]:
    """Normalize an Ultralytics prediction return value into a list of results.

    ``predict`` returns a list for a single frame but a generator when streaming,
    and a stub may hand back one result object directly.
    """
    if results is None:
        return []
    if hasattr(results, "boxes"):
        return [results]
    return list(results)


class YoloDetector:
    """The Detector backed by pretrained Ultralytics YOLO weights.

    ``__init__`` loads the weights named by ``config.model_path`` and makes no
    training call of any kind (Requirement 2.4); a load failure raises
    :class:`~src.errors.ModelError` naming that path (Requirement 2.6), which
    ``src/main.py`` turns into exit status 1 through its single
    ``TrafficSignalError`` handler.

    ``detect`` applies three filters, in this order, to whatever the model
    returns:

    1. class: only COCO ids in :data:`COCO_VEHICLE_CLASS_IDS` survive
       (Requirement 2.2)
    2. confidence: anything below ``config.confidence_threshold`` is dropped
       (Requirement 2.3)
    3. geometry: coordinates are clipped to the frame bounds, and a box that
       degenerates to zero width or height once clipped is dropped rather than
       returned as an invalid :class:`Detection` (Requirements 2.1, 2.5)

    Filtering happens here in Python rather than being delegated entirely to the
    model call, so the behaviour is the Detector's own and is testable against a
    stubbed model result with no weights and no GPU. The threshold is also passed
    to the model, which lets the backend skip work it would only have discarded.
    """

    def __init__(self, config: Config, *, model: Any | None = None) -> None:
        """Load the configured weights, or adopt an already-built ``model``.

        ``model`` is an injection seam for tests: it accepts anything exposing
        Ultralytics' ``predict`` shape, so the filtering rules can be verified
        without weights or a GPU. Production callers pass ``config`` only.
        """
        self._config = config
        self._model = model if model is not None else load_yolo_model(config.model_path)

    @property
    def model_path(self) -> str:
        """The configured weights path this detector was built from."""
        return self._config.model_path

    @property
    def confidence_threshold(self) -> float:
        """The configured Confidence_Threshold applied by :meth:`detect`."""
        return self._config.confidence_threshold

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Return the vehicle detections found in ``frame`` (Requirement 2.1).

        ``frame`` is never modified: the same array is handed to the Tracker and
        the Signal_Overlay after detection.
        """
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            raise ValueError(
                "YoloDetector.detect expects a NumPy image array, got "
                f"{type(frame).__name__}"
            )
        height, width = int(frame.shape[0]), int(frame.shape[1])

        detections: list[Detection] = []
        for result in iter_results(self._predict(frame)):
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            raw_xyxy = getattr(boxes, "xyxy", None)
            if raw_xyxy is None:
                continue
            xyxy = to_numpy(raw_xyxy).reshape(-1, 4)
            if xyxy.size == 0:
                continue
            raw_class_ids = getattr(boxes, "cls", None)
            raw_confidences = getattr(boxes, "conf", None)
            class_ids = to_numpy([] if raw_class_ids is None else raw_class_ids).reshape(-1)
            confidences = to_numpy([] if raw_confidences is None else raw_confidences).reshape(-1)
            if len(class_ids) != len(xyxy) or len(confidences) != len(xyxy):
                raise ModelError(
                    f"model at {self.model_path} returned {len(xyxy)} boxes but "
                    f"{len(class_ids)} class ids and {len(confidences)} confidences"
                )

            for row, raw_class_id, raw_confidence in zip(xyxy, class_ids, confidences):
                detection = self._to_detection(row, raw_class_id, raw_confidence, width, height)
                if detection is not None:
                    detections.append(detection)
        return detections

    # -- internals ---------------------------------------------------------

    def _predict(self, frame: np.ndarray) -> Any:
        """Run inference on one frame. No training call is ever made (Req 2.4)."""
        return self._model.predict(
            source=frame,
            conf=self._config.confidence_threshold,
            verbose=False,
        )

    def _to_detection(
        self,
        row: np.ndarray,
        raw_class_id: Any,
        raw_confidence: Any,
        width: int,
        height: int,
    ) -> Detection | None:
        """Convert one raw model row into a :class:`Detection`, or drop it.

        Returns ``None`` for every row excluded by Requirements 2.2, 2.3, and
        2.5, so the caller only has to collect what survives.
        """
        vehicle_class = COCO_VEHICLE_CLASS_IDS.get(coco_class_id(raw_class_id))
        if vehicle_class is None:
            return None                                   # Requirement 2.2

        confidence = float(raw_confidence)
        if not math.isfinite(confidence) or confidence < self._config.confidence_threshold:
            return None                                   # Requirement 2.3
        confidence = min(1.0, max(0.0, confidence))

        clipped = clip_box_to_frame(row, width, height)
        if clipped is None:
            return None                                   # Requirement 2.5
        x1, y1, x2, y2 = clipped
        return Detection(
            x1=x1, y1=y1, x2=x2, y2=y2,
            vehicle_class=vehicle_class,
            confidence=confidence,
        )


def coco_class_id(raw_class_id: Any) -> int:
    """Return the COCO class id as an ``int``.

    Ultralytics reports class ids as floats. A non-numeric or non-finite id
    cannot name any class, so it maps to a sentinel that is absent from
    :data:`COCO_VEHICLE_CLASS_IDS` and is therefore discarded.
    """
    try:
        value = float(raw_class_id)
    except (TypeError, ValueError):
        return -1
    if not math.isfinite(value):
        return -1
    return int(round(value))


def clip_box_to_frame(
    row: np.ndarray, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Clip one ``xyxy`` row to the frame, or return ``None`` if it degenerates.

    Coordinates are ordered before clipping, so a model that emits a reversed
    corner pair still produces a positive-extent box rather than being discarded.
    A box lying wholly outside the frame, or collapsing to zero width or height
    once clipped, has no measurable area and is dropped (Requirement 2.5).
    """
    values = [float(value) for value in row[:4]]
    if not all(math.isfinite(value) for value in values):
        return None

    raw_x1, raw_x2 = sorted((values[0], values[2]))
    raw_y1, raw_y2 = sorted((values[1], values[3]))

    x1 = _clip_coordinate(raw_x1, width)
    x2 = _clip_coordinate(raw_x2, width)
    y1 = _clip_coordinate(raw_y1, height)
    y2 = _clip_coordinate(raw_y2, height)

    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _clip_coordinate(value: float, extent: int) -> int:
    """Round ``value`` to a pixel index inside ``0..extent`` inclusive.

    The upper bound is inclusive because coordinates are exclusive on the
    lower-right corner: a box touching the right edge of a 640-wide frame has
    ``x2 == 640`` and a width of the pixels actually covered.
    """
    return max(0, min(extent, int(round(value))))


def load_yolo_model(model_path: str) -> Any:
    """Load pretrained Ultralytics weights, with no training call (Req 2.4).

    Public because the Tracker loads the same weights for Ultralytics' persistent
    ByteTrack pass (Requirement 3.1) and must report a load failure the same way.

    Both an unavailable ``ultralytics`` package and an unloadable weights file
    raise :class:`~src.errors.ModelError` naming ``model_path`` (Requirement 2.6):
    from the operator's point of view these are the same failure, and the message
    has to name the path they configured either way. The import is local so the
    rest of this module — the ``Detection`` model and the ``Detector`` protocol
    every fake implements — stays importable without ultralytics installed.
    """
    try:
        from ultralytics import YOLO
    except Exception as exc:
        raise ModelError(
            f"could not load the YOLO weights configured at {model_path}: "
            f"the ultralytics package is unavailable ({exc})"
        ) from exc

    try:
        return YOLO(model_path)
    except Exception as exc:
        raise ModelError(
            f"could not load the YOLO weights configured at {model_path}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Annotated detection video (Requirements 2.7, 15.2)
# ---------------------------------------------------------------------------

#: Where annotated output videos are written, as fixed by Requirement 2.7.
DETECTION_VIDEO_DIR = "results/videos"

#: Suffix appended to the input video stem when no output path is supplied, so a
#: detection run cannot silently overwrite a tracked or controlled run's output.
DETECTION_VIDEO_SUFFIX = "__detections"

#: One colour per Vehicle_Class, so the four classes stay distinguishable in the
#: screenshots that go into ``report/``. BGR, as OpenCV expects.
DETECTION_COLORS: dict[str, tuple[int, int, int]] = {
    "car": (0, 255, 0),
    "motorcycle": (0, 200, 255),
    "bus": (255, 128, 0),
    "truck": (255, 0, 255),
}

#: Fallback colour for a class absent from :data:`DETECTION_COLORS`. Unreachable
#: while ``Detection`` validates its class, but drawing must never raise.
DETECTION_FALLBACK_COLOR: tuple[int, int, int] = (200, 200, 200)

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.4
_FONT_THICKNESS = 1
_BOX_THICKNESS = 2

#: Title of the window opened when :func:`run_detection_video` displays frames.
DETECT_WINDOW_NAME = "Adaptive Traffic Signal - detect"


@dataclass(frozen=True)
class DetectionRunResult:
    """Outcome of one :func:`run_detection_video` call.

    ``frames_written`` is reported separately from ``frames_processed`` so the
    one-output-frame-per-input-frame guarantee of Requirement 2.7 is an
    observable fact rather than an assumption; the two are equal on every
    successful run.
    """

    info: VideoInfo
    out_path: str
    frames_processed: int
    frames_written: int
    detections_drawn: int
    quit_early: bool


def default_detection_output_path(
    video_path: str, out_dir: str = DETECTION_VIDEO_DIR
) -> str:
    """Return the ``results/videos/`` path for a detection run over ``video_path``.

    Naming is derived from the input stem so a run's output is traceable to its
    input without consulting a log (Requirement 2.7).
    """
    stem = Path(video_path).stem or "video"
    return str(Path(out_dir) / f"{stem}{DETECTION_VIDEO_SUFFIX}.mp4")


def draw_detections(
    frame: np.ndarray, detections: list[Detection] | tuple[Detection, ...]
) -> np.ndarray:
    """Return a copy of ``frame`` with each detection drawn as a labelled box.

    A copy rather than an in-place edit: the caller's frame is also the frame the
    Tracker and the Signal_Overlay read, and mutating it here would leak
    annotations into their input.

    The label carries the Vehicle_Class and the confidence
    (:meth:`Detection.label`), drawn on a filled strip so it stays readable over
    both light and dark road surfaces. The strip sits above the box, or inside
    the top edge when the box touches the top of the frame, so it is never
    clipped away.
    """
    annotated = frame.copy()
    height, width = annotated.shape[0], annotated.shape[1]

    for detection in detections:
        colour = DETECTION_COLORS.get(detection.vehicle_class, DETECTION_FALLBACK_COLOR)
        cv2.rectangle(
            annotated,
            (detection.x1, detection.y1),
            (min(detection.x2, width - 1), min(detection.y2, height - 1)),
            colour,
            _BOX_THICKNESS,
        )

        text = detection.label()
        (text_w, text_h), baseline = cv2.getTextSize(
            text, _FONT, _FONT_SCALE, _FONT_THICKNESS
        )
        strip_h = text_h + baseline
        top = detection.y1 - strip_h
        if top < 0:                      # box touches the top edge: label inside
            top = min(detection.y1, max(0, height - strip_h))
        left = max(0, min(detection.x1, max(0, width - text_w)))

        cv2.rectangle(
            annotated,
            (left, top),
            (min(left + text_w, width), min(top + strip_h, height)),
            colour,
            cv2.FILLED,
        )
        cv2.putText(
            annotated,
            text,
            (left, top + text_h),
            _FONT,
            _FONT_SCALE,
            (0, 0, 0),
            _FONT_THICKNESS,
            cv2.LINE_AA,
        )
    return annotated


def open_annotated_writer(
    out_path: str, frame_rate: float, size: tuple[int, int]
) -> cv2.VideoWriter:
    """Open a ``cv2.VideoWriter`` at ``out_path``, or raise naming that path.

    The writer is opened at the *input* frame rate, so the annotated video plays
    back over the same wall-clock duration as its source (Requirements 2.7, 12.6).

    Public because every annotated-output entry point — detection here, tracking
    in :mod:`src.overlay`, and the controlled run later — writes under
    ``results/videos/`` at the input frame rate and must report a writer failure
    the same way. It lives in this module rather than in :mod:`src.overlay`
    because ``overlay`` imports from ``detection``, not the other way round.
    """
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    writer = cv2.VideoWriter(
        out_path, cv2.VideoWriter_fourcc(*"mp4v"), float(frame_rate), size
    )
    if not writer.isOpened():
        writer.release()
        raise VideoError(
            f"annotated output video could not be opened for writing: {out_path}"
        )
    return writer


def run_detection_video(
    video_path: str,
    config: Config,
    out_path: str | None = None,
    *,
    detector: Detector | None = None,
    display: bool = False,
) -> DetectionRunResult:
    """Detect vehicles in ``video_path`` and write an annotated video.

    One output frame is written for every processed input frame, each detection
    drawn as a labelled bounding box, at the input frame rate, under
    ``results/videos/`` by default (Requirements 2.7, 15.2).

    ``detector`` is an injection seam: passing a scripted fake runs this entry
    point with no weights and no GPU, which is how the integration test checks
    the frame-count guarantee. Passing ``None`` builds a :class:`YoloDetector`
    from ``config``, so a load failure surfaces as :class:`ModelError` naming the
    configured path (Requirement 2.6).

    ``display=True`` additionally shows each annotated frame and honours
    ``config.quit_key``; the default is headless, since this entry point's
    product is the file on disk.

    The writer is opened from the first frame's own shape rather than from
    container metadata, because ``cv2.VideoWriter`` silently discards a frame
    whose size differs from the one it was opened with — which would break the
    one-frame-per-frame guarantee without raising anything.
    """
    resolved_out_path = out_path or default_detection_output_path(video_path)

    ingestor = VideoIngestor(video_path, config)
    info = ingestor.info
    if detector is None:
        detector = YoloDetector(config)

    delay_ms = max(1, int(round(1000.0 / info.frame_rate)))
    quit_codes = {ord(config.quit_key.lower()), ord(config.quit_key.upper())}

    writer: cv2.VideoWriter | None = None
    writer_size: tuple[int, int] = (0, 0)
    frames_processed = 0
    frames_written = 0
    detections_drawn = 0
    quit_early = False
    try:
        for _index, frame in ingestor.frames():
            detections = detector.detect(frame)
            annotated = draw_detections(frame, detections)
            frames_processed += 1
            detections_drawn += len(detections)

            if writer is None:
                writer = open_annotated_writer(
                    resolved_out_path,
                    info.frame_rate,
                    (annotated.shape[1], annotated.shape[0]),
                )
                writer_size = (annotated.shape[1], annotated.shape[0])
            elif (annotated.shape[1], annotated.shape[0]) != writer_size:
                # A mid-video resolution change would otherwise be dropped
                # silently; resizing keeps the frame counts equal.
                annotated = cv2.resize(annotated, writer_size)
            writer.write(annotated)
            frames_written += 1

            if display:
                try:
                    cv2.imshow(DETECT_WINDOW_NAME, annotated)
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

    if writer is None:
        raise VideoError(f"video yielded no frames to annotate: {video_path}")

    return DetectionRunResult(
        info=info,
        out_path=resolved_out_path,
        frames_processed=frames_processed,
        frames_written=frames_written,
        detections_drawn=detections_drawn,
        quit_early=quit_early,
    )


__all__ = [
    "COCO_VEHICLE_CLASS_IDS",
    "DETECTION_COLORS",
    "DETECTION_VIDEO_DIR",
    "DETECT_WINDOW_NAME",
    "VEHICLE_CLASSES",
    "Detection",
    "DetectionRunResult",
    "Detector",
    "YoloDetector",
    "clip_box_to_frame",
    "coco_class_id",
    "default_detection_output_path",
    "draw_detections",
    "iter_results",
    "load_yolo_model",
    "open_annotated_writer",
    "run_detection_video",
    "to_numpy",
]
