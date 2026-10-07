"""Unit tests for :class:`~src.detection.YoloDetector` (Requirements 2.1–2.6).

The model is stubbed, so these tests run with no weights, no GPU, and no
network: what is under test is the Detector's own filtering — class mapping
(Requirement 2.2), the Confidence_Threshold (Requirement 2.3), and frame-bounds
clipping (Requirements 2.1, 2.5) — not the neural network behind it.

The stub mimics the Ultralytics return shape: ``predict`` yields a list of result
objects, each carrying ``boxes.xyxy``, ``boxes.cls``, and ``boxes.conf``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pytest

from src.config import Config, load_config
from src.detection import COCO_VEHICLE_CLASS_IDS, Detection, Detector, YoloDetector
from src.errors import ModelError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: ``(x1, y1, x2, y2, coco_class_id, confidence)`` per stubbed model row.
Row = tuple[float, float, float, float, float, float]

FRAME_WIDTH = 40
FRAME_HEIGHT = 30


class _StubBoxes:
    """The ``boxes`` member of one stubbed Ultralytics result."""

    def __init__(self, rows: Sequence[Row]) -> None:
        self.xyxy = np.array([row[:4] for row in rows], dtype=np.float32).reshape(-1, 4)
        self.cls = np.array([row[4] for row in rows], dtype=np.float32)
        self.conf = np.array([row[5] for row in rows], dtype=np.float32)


class _StubResult:
    def __init__(self, rows: Sequence[Row]) -> None:
        self.boxes = _StubBoxes(rows)


class _StubModel:
    """Stands in for ``ultralytics.YOLO``, recording how it was called.

    ``train`` fails loudly: Requirement 2.4 forbids any training call, and a
    silent stub would let a regression through.
    """

    def __init__(self, rows: Sequence[Row]) -> None:
        self.rows = list(rows)
        self.calls: list[dict[str, Any]] = []

    def predict(self, **kwargs: Any) -> list[_StubResult]:
        self.calls.append(kwargs)
        return [_StubResult(self.rows)]

    def train(self, *args: Any, **kwargs: Any) -> None:      # pragma: no cover
        raise AssertionError("the Detector must never train (Requirement 2.4)")


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _frame() -> np.ndarray:
    return np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)


def _detector(config: Config, rows: Sequence[Row], threshold: float | None = None) -> tuple[
    YoloDetector, _StubModel
]:
    if threshold is not None:
        config = dataclasses.replace(config, confidence_threshold=threshold)
    model = _StubModel(rows)
    return YoloDetector(config, model=model), model


# ---------------------------------------------------------------------------
# Contract and model loading (Requirements 2.1, 2.4, 2.6)
# ---------------------------------------------------------------------------


def test_yolo_detector_satisfies_the_detector_protocol(config: Config) -> None:
    detector, _ = _detector(config, [])

    assert isinstance(detector, Detector)


def test_unloadable_model_path_raises_model_error_naming_the_path(
    config: Config, tmp_path: Path
) -> None:
    """Requirement 2.6: the message names the configured Model_Path."""
    missing = tmp_path / "not_a_model.xyz"
    broken_config = dataclasses.replace(config, model_path=str(missing))

    with pytest.raises(ModelError) as excinfo:
        YoloDetector(broken_config)

    assert str(missing) in str(excinfo.value)


def test_detect_makes_no_training_call(config: Config) -> None:
    """Requirement 2.4: weights are loaded and used, never trained."""
    detector, model = _detector(config, [(0.0, 0.0, 10.0, 10.0, 2.0, 0.9)])

    detector.detect(_frame())

    assert len(model.calls) == 1


def test_detect_passes_the_configured_threshold_to_the_model(config: Config) -> None:
    detector, model = _detector(config, [], threshold=0.42)

    detector.detect(_frame())

    assert model.calls[0]["conf"] == pytest.approx(0.42)


def test_detect_does_not_mutate_the_frame(config: Config) -> None:
    detector, _ = _detector(config, [(1.0, 2.0, 9.0, 8.0, 2.0, 0.9)])
    frame = _frame()
    before = frame.copy()

    detector.detect(frame)

    assert np.array_equal(frame, before)


def test_detect_returns_an_empty_list_when_the_model_finds_nothing(config: Config) -> None:
    detector, _ = _detector(config, [])

    assert detector.detect(_frame()) == []


def test_detect_rejects_a_non_image_argument(config: Config) -> None:
    detector, _ = _detector(config, [])

    with pytest.raises(ValueError, match="NumPy image array"):
        detector.detect("not a frame")       # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Class filtering (Requirement 2.2)
# ---------------------------------------------------------------------------


def test_only_the_four_vehicle_classes_survive(config: Config) -> None:
    rows: list[Row] = [
        (0.0, 0.0, 5.0, 5.0, 0.0, 0.95),      # person
        (1.0, 1.0, 6.0, 6.0, 1.0, 0.95),      # bicycle
        (2.0, 2.0, 7.0, 7.0, 2.0, 0.95),      # car
        (3.0, 3.0, 8.0, 8.0, 3.0, 0.95),      # motorcycle
        (4.0, 4.0, 9.0, 9.0, 5.0, 0.95),      # bus
        (5.0, 5.0, 10.0, 10.0, 7.0, 0.95),    # truck
        (6.0, 6.0, 11.0, 11.0, 9.0, 0.95),    # traffic light
        (7.0, 7.0, 12.0, 12.0, 15.0, 0.95),   # cat
    ]
    detector, _ = _detector(config, rows)

    detections = detector.detect(_frame())

    assert [d.vehicle_class for d in detections] == ["car", "motorcycle", "bus", "truck"]


@pytest.mark.parametrize("class_id, expected", sorted(COCO_VEHICLE_CLASS_IDS.items()))
def test_each_mapped_coco_id_yields_its_vehicle_class(
    config: Config, class_id: int, expected: str
) -> None:
    detector, _ = _detector(config, [(0.0, 0.0, 10.0, 10.0, float(class_id), 0.8)])

    assert [d.vehicle_class for d in detector.detect(_frame())] == [expected]


def test_unusable_class_id_is_discarded(config: Config) -> None:
    """A NaN class id names no class, so the row cannot become a Detection."""
    detector, _ = _detector(config, [(0.0, 0.0, 10.0, 10.0, float("nan"), 0.9)])

    assert detector.detect(_frame()) == []


# ---------------------------------------------------------------------------
# Threshold filtering (Requirement 2.3)
# ---------------------------------------------------------------------------


def test_detections_below_the_threshold_are_excluded(config: Config) -> None:
    rows: list[Row] = [
        (0.0, 0.0, 5.0, 5.0, 2.0, 0.10),
        (1.0, 1.0, 6.0, 6.0, 2.0, 0.49),
        (2.0, 2.0, 7.0, 7.0, 2.0, 0.51),
        (3.0, 3.0, 8.0, 8.0, 2.0, 0.90),
    ]
    detector, _ = _detector(config, rows, threshold=0.5)

    detections = detector.detect(_frame())

    assert [pytest.approx(d.confidence, abs=1e-6) for d in detections] == [0.51, 0.90]


def test_confidence_exactly_at_the_threshold_is_kept(config: Config) -> None:
    """The threshold is exclusive only below it, per Requirement 2.3."""
    detector, _ = _detector(config, [(0.0, 0.0, 5.0, 5.0, 2.0, 0.5)], threshold=0.5)

    assert len(detector.detect(_frame())) == 1


def test_a_zero_threshold_keeps_a_zero_confidence_detection(config: Config) -> None:
    detector, _ = _detector(config, [(0.0, 0.0, 5.0, 5.0, 2.0, 0.0)], threshold=0.0)

    detections = detector.detect(_frame())

    assert [d.confidence for d in detections] == [0.0]


def test_every_returned_confidence_lies_in_the_unit_range(config: Config) -> None:
    """Requirement 2.1: confidence is reported in 0..1 even if the model overshoots."""
    detector, _ = _detector(config, [(0.0, 0.0, 5.0, 5.0, 2.0, 1.0000001)], threshold=0.5)

    detections = detector.detect(_frame())

    assert len(detections) == 1
    assert 0.0 <= detections[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# Frame-bounds clipping (Requirements 2.1, 2.5)
# ---------------------------------------------------------------------------


def test_box_overhanging_the_frame_is_clipped_to_the_bounds(config: Config) -> None:
    detector, _ = _detector(config, [(-8.0, -5.0, FRAME_WIDTH + 12.0, FRAME_HEIGHT + 7.0, 2.0, 0.9)])

    detections = detector.detect(_frame())

    assert detections[0].box == (0, 0, FRAME_WIDTH, FRAME_HEIGHT)


def test_clipped_boxes_stay_inside_the_frame_with_positive_extents(config: Config) -> None:
    rows: list[Row] = [
        (-4.0, 6.0, 12.0, 20.0, 2.0, 0.9),
        (30.0, 20.0, 60.0, 44.0, 5.0, 0.9),
        (2.0, -3.0, 18.0, 9.0, 7.0, 0.9),
    ]
    detector, _ = _detector(config, rows)

    detections = detector.detect(_frame())

    assert len(detections) == len(rows)
    for detection in detections:
        assert 0 <= detection.x1 < detection.x2 <= FRAME_WIDTH
        assert 0 <= detection.y1 < detection.y2 <= FRAME_HEIGHT
        assert detection.width > 0 and detection.height > 0


@pytest.mark.parametrize(
    "row",
    [
        (-30.0, 5.0, -2.0, 20.0, 2.0, 0.9),                      # wholly left of the frame
        (5.0, -40.0, 20.0, -1.0, 2.0, 0.9),                      # wholly above the frame
        (FRAME_WIDTH + 3.0, 5.0, FRAME_WIDTH + 20.0, 20.0, 2.0, 0.9),   # wholly right
        (5.0, FRAME_HEIGHT + 2.0, 20.0, FRAME_HEIGHT + 9.0, 2.0, 0.9),  # wholly below
    ],
)
def test_box_outside_the_frame_is_discarded(config: Config, row: Row) -> None:
    """Requirement 2.5: a box with no area inside the frame is not returned."""
    detector, _ = _detector(config, [row])

    assert detector.detect(_frame()) == []


@pytest.mark.parametrize(
    "row",
    [
        (10.0, 5.0, 10.0, 20.0, 2.0, 0.9),      # zero width as given
        (10.0, 5.0, 20.0, 5.0, 2.0, 0.9),       # zero height as given
        (10.2, 5.0, 10.4, 20.0, 2.0, 0.9),      # zero width after rounding
    ],
)
def test_degenerate_box_is_discarded(config: Config, row: Row) -> None:
    detector, _ = _detector(config, [row])

    assert detector.detect(_frame()) == []


def test_reversed_corner_pair_is_normalized_rather_than_dropped(config: Config) -> None:
    detector, _ = _detector(config, [(18.0, 22.0, 6.0, 4.0, 2.0, 0.9)])

    detections = detector.detect(_frame())

    assert detections[0].box == (6, 4, 18, 22)


def test_non_finite_coordinates_are_discarded(config: Config) -> None:
    detector, _ = _detector(config, [(0.0, 0.0, float("inf"), 10.0, 2.0, 0.9)])

    assert detector.detect(_frame()) == []


def test_returned_values_are_detection_instances(config: Config) -> None:
    detector, _ = _detector(config, [(1.0, 2.0, 9.0, 8.0, 2.0, 0.9)])

    detections = detector.detect(_frame())

    assert all(isinstance(detection, Detection) for detection in detections)


# ---------------------------------------------------------------------------
# Model output shapes
# ---------------------------------------------------------------------------


class _TensorLike:
    """Mimics a torch tensor: detach/cpu/numpy before it is a NumPy array."""

    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def detach(self) -> "_TensorLike":
        return self

    def cpu(self) -> "_TensorLike":
        return self

    def numpy(self) -> np.ndarray:
        return self._array


def test_tensor_like_model_output_is_read_without_torch(config: Config) -> None:
    class _Boxes:
        xyxy = _TensorLike(np.array([[1.0, 2.0, 9.0, 8.0]], dtype=np.float32))
        cls = _TensorLike(np.array([2.0], dtype=np.float32))
        conf = _TensorLike(np.array([0.9], dtype=np.float32))

    class _Result:
        boxes = _Boxes()

    class _Model:
        def predict(self, **kwargs: Any) -> list[_Result]:
            return [_Result()]

    detector = YoloDetector(config, model=_Model())

    detections = detector.detect(_frame())

    assert len(detections) == 1
    assert detections[0].box == (1, 2, 9, 8)
    assert detections[0].vehicle_class == "car"
    assert detections[0].confidence == pytest.approx(0.9)


def test_result_without_boxes_yields_no_detections(config: Config) -> None:
    class _Result:
        boxes = None

    class _Model:
        def predict(self, **kwargs: Any) -> list[_Result]:
            return [_Result()]

    assert YoloDetector(config, model=_Model()).detect(_frame()) == []


def test_mismatched_model_output_lengths_raise_model_error(config: Config) -> None:
    class _Boxes:
        xyxy = np.array([[1.0, 2.0, 9.0, 8.0], [2.0, 3.0, 10.0, 9.0]], dtype=np.float32)
        cls = np.array([2.0], dtype=np.float32)
        conf = np.array([0.9, 0.8], dtype=np.float32)

    class _Result:
        boxes = _Boxes()

    class _Model:
        def predict(self, **kwargs: Any) -> list[_Result]:
            return [_Result()]

    with pytest.raises(ModelError, match="class ids"):
        YoloDetector(config, model=_Model()).detect(_frame())
