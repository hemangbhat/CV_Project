"""Unit tests for the Detection data model and the scripted Detector fake.

Covers Requirement 2.1: every detection carries an axis-aligned box in image
coordinates, one of the four Vehicle_Classes, and a confidence in 0..1, and a
Detector returns zero or more of them per frame. The concrete YOLO detector and
its filtering rules (Requirements 2.2–2.6) are tested with task 4.2.
"""

import numpy as np
import pytest

from src.config import VEHICLE_CLASSES as CONFIG_VEHICLE_CLASSES
from src.detection import VEHICLE_CLASSES, Detection, Detector
from tests.fixtures import (
    DEFAULT_CLIP_HEIGHT,
    DEFAULT_CLIP_WIDTH,
    FakeDetector,
    scripted_detections,
    synthetic_frame,
)


def test_vehicle_classes_are_the_four_required_classes() -> None:
    assert VEHICLE_CLASSES == ("car", "motorcycle", "bus", "truck")
    # One source of truth, so the Detector filter and pce_weights cannot drift.
    assert VEHICLE_CLASSES is CONFIG_VEHICLE_CLASSES


def test_detection_exposes_box_extents_and_label() -> None:
    detection = Detection(x1=10, y1=20, x2=40, y2=60, vehicle_class="car", confidence=0.75)

    assert detection.box == (10, 20, 40, 60)
    assert detection.width == 30
    assert detection.height == 40
    assert detection.label() == "car 0.75"


def test_detection_is_frozen_and_hashable() -> None:
    detection = Detection(x1=0, y1=0, x2=2, y2=2, vehicle_class="bus", confidence=1.0)

    with pytest.raises(Exception):
        detection.x1 = 5           # type: ignore[misc]
    assert detection in {detection}


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_detection_accepts_confidence_endpoints(confidence: float) -> None:
    assert Detection(0, 0, 1, 1, "truck", confidence).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan")])
def test_detection_rejects_confidence_outside_unit_range(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        Detection(0, 0, 1, 1, "car", confidence)


@pytest.mark.parametrize(
    "box",
    [
        (5, 0, 5, 4),      # zero width
        (0, 5, 4, 5),      # zero height
        (6, 0, 2, 4),      # inverted in x
        (0, 6, 4, 2),      # inverted in y
    ],
)
def test_detection_rejects_non_positive_extents(box: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="positive width and height"):
        Detection(*box, vehicle_class="car", confidence=0.5)


@pytest.mark.parametrize("box", [(-1, 0, 4, 4), (0, -3, 4, 4)])
def test_detection_rejects_negative_origin(box: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="within the frame"):
        Detection(*box, vehicle_class="car", confidence=0.5)


def test_detection_rejects_unknown_vehicle_class() -> None:
    with pytest.raises(ValueError, match="vehicle_class"):
        Detection(0, 0, 4, 4, "bicycle", 0.5)


def test_detection_rejects_non_integer_coordinates() -> None:
    with pytest.raises(ValueError, match="must be an int"):
        Detection(0.5, 0, 4, 4, "car", 0.5)      # type: ignore[arg-type]


def test_fake_detector_satisfies_the_detector_protocol() -> None:
    assert isinstance(FakeDetector(), Detector)


def test_fake_detector_replays_the_script_in_frame_order() -> None:
    script = [
        [Detection(0, 0, 4, 4, "car", 0.9)],
        [],
        [
            Detection(1, 1, 5, 5, "bus", 0.8),
            Detection(6, 6, 9, 9, "truck", 0.7),
        ],
    ]
    detector = FakeDetector(script)
    frame = synthetic_frame(0)

    assert detector.detect(frame) == script[0]
    assert detector.detect(frame) == []
    assert detector.detect(frame) == script[2]
    assert detector.calls == 3


def test_fake_detector_returns_empty_past_the_end_of_the_script() -> None:
    detector = FakeDetector([[Detection(0, 0, 4, 4, "car", 0.9)]])
    frame = synthetic_frame(0)

    detector.detect(frame)
    assert detector.detect(frame) == []
    assert detector.detect(frame) == []


def test_fake_detector_reset_replays_identical_detections() -> None:
    detector = FakeDetector()
    frame = synthetic_frame(0)

    first_run = [detector.detect(frame) for _ in range(3)]
    detector.reset()
    second_run = [detector.detect(frame) for _ in range(3)]

    assert first_run == second_run


def test_fake_detector_hands_out_copies_so_callers_cannot_corrupt_the_script() -> None:
    detector = FakeDetector()
    frame = synthetic_frame(0)

    returned = detector.detect(frame)
    returned.clear()
    detector.reset()

    assert detector.detect(frame) == list(detector.script[0])


def test_fake_detector_does_not_mutate_the_frame() -> None:
    detector = FakeDetector()
    frame = synthetic_frame(0)
    before = frame.copy()

    detector.detect(frame)

    assert np.array_equal(frame, before)


def test_scripted_detections_track_the_synthetic_clip_rectangles() -> None:
    frame_count = 6
    script = scripted_detections(frame_count)

    assert len(script) == frame_count
    for frame_detections in script:
        assert len(frame_detections) == 3
        for detection in frame_detections:
            assert detection.vehicle_class in VEHICLE_CLASSES
            assert 0.0 <= detection.confidence <= 1.0
            assert detection.width > 0 and detection.height > 0
            assert 0 <= detection.x1 < detection.x2 <= DEFAULT_CLIP_WIDTH
            assert 0 <= detection.y1 < detection.y2 <= DEFAULT_CLIP_HEIGHT

    # The boxes move, which is what makes the fake usable for tracking tests.
    assert script[0][0].box != script[1][0].box


def test_scripted_detections_rejects_an_empty_clip() -> None:
    with pytest.raises(ValueError, match="frame_count"):
        scripted_detections(0)
