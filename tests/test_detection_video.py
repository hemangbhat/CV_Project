"""Tests for the annotated detection video entry point.

Requirements 2.7 (one output frame per processed input frame, each detection
drawn as a labelled box, written under ``results/videos/`` at the input frame
rate) and 15.2 (``python -m src.main detect --video V``).

Every run here injects ``FakeDetector``, so the suite needs no YOLO weights and
no GPU. The central assertion decodes the written file and counts its frames
rather than trusting container metadata, because a size-mismatched frame is
discarded by ``cv2.VideoWriter`` without raising.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src import main as cli
from src.config import Config, load_config
from src.detection import (
    DETECTION_VIDEO_DIR,
    Detection,
    default_detection_output_path,
    draw_detections,
    run_detection_video,
)
from src.errors import ModelError
from tests.fixtures import (
    DEFAULT_CLIP_FRAME_RATE,
    FakeDetector,
    scripted_detections,
    synthetic_frame,
    write_synthetic_clip,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

CLIP_FRAME_COUNT = 7


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _decoded_frame_count(path: str | Path) -> int:
    """Count the frames that actually decode out of ``path``."""
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened(), f"output video could not be reopened: {path}"
    try:
        count = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                return count
            count += 1
    finally:
        capture.release()


# ---------------------------------------------------------------------------
# Requirement 2.7 — one output frame per processed input frame
# ---------------------------------------------------------------------------


def test_output_video_has_one_frame_per_processed_input_frame(
    tmp_path: Path, config: Config
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    out_path = tmp_path / "clip__detections.mp4"

    result = run_detection_video(
        str(clip),
        config,
        str(out_path),
        detector=FakeDetector(scripted_detections(CLIP_FRAME_COUNT)),
    )

    assert result.frames_processed == CLIP_FRAME_COUNT
    assert result.frames_written == result.frames_processed
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_output_video_uses_the_input_frame_rate_and_resolution(
    tmp_path: Path, config: Config
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=4)
    out_path = tmp_path / "out.mp4"

    result = run_detection_video(
        str(clip), config, str(out_path), detector=FakeDetector(scripted_detections(4))
    )

    capture = cv2.VideoCapture(str(out_path))
    try:
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(
            DEFAULT_CLIP_FRAME_RATE, abs=0.5
        )
        assert int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))) == result.info.width
        assert int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))) == result.info.height
    finally:
        capture.release()


def test_every_scripted_detection_is_drawn(tmp_path: Path, config: Config) -> None:
    script = scripted_detections(CLIP_FRAME_COUNT)
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)

    result = run_detection_video(
        str(clip), config, str(tmp_path / "out.mp4"), detector=FakeDetector(script)
    )

    assert result.detections_drawn == sum(len(frame) for frame in script)


def test_default_output_path_lives_under_results_videos() -> None:
    out_path = default_detection_output_path("videos/junction_a.mp4")
    assert Path(out_path).parent == Path(DETECTION_VIDEO_DIR)
    assert Path(out_path).stem.startswith("junction_a")


def test_default_output_path_is_used_and_created(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    clip = write_synthetic_clip(tmp_path / "junction.mp4", frame_count=3)

    result = run_detection_video(
        str(clip), config, detector=FakeDetector(scripted_detections(3))
    )

    assert Path(result.out_path).parent == Path(DETECTION_VIDEO_DIR)
    assert Path(result.out_path).is_file()
    assert _decoded_frame_count(result.out_path) == 3


# ---------------------------------------------------------------------------
# Requirement 2.7 — each detection drawn as a labelled box
# ---------------------------------------------------------------------------


def test_draw_detections_does_not_mutate_the_input_frame() -> None:
    frame = synthetic_frame(0)
    original = frame.copy()
    detections = scripted_detections(1)[0]

    annotated = draw_detections(frame, detections)

    assert np.array_equal(frame, original), "the caller's frame must be untouched"
    assert not np.array_equal(annotated, original), "detections must be drawn"
    assert annotated.shape == frame.shape


def test_draw_detections_leaves_a_frame_without_detections_unchanged() -> None:
    frame = synthetic_frame(2)
    assert np.array_equal(draw_detections(frame, []), frame)


def test_label_names_the_class_and_confidence() -> None:
    detection = Detection(x1=1, y1=2, x2=9, y2=8, vehicle_class="bus", confidence=0.5)
    assert detection.label() == "bus 0.50"


def test_a_box_at_the_frame_edges_is_drawn_without_raising() -> None:
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    edge_boxes = [
        Detection(x1=0, y1=0, x2=30, y2=20, vehicle_class="car", confidence=0.9),
        Detection(x1=28, y1=18, x2=30, y2=20, vehicle_class="truck", confidence=0.4),
    ]
    annotated = draw_detections(frame, edge_boxes)
    assert annotated.shape == frame.shape


# ---------------------------------------------------------------------------
# Requirement 15.2 — the detect entry point
# ---------------------------------------------------------------------------


def test_main_detect_writes_the_annotated_video(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    out_path = tmp_path / "out.mp4"
    monkeypatch.setattr(
        cli, "YoloDetector", lambda config: FakeDetector(scripted_detections(CLIP_FRAME_COUNT))
    )

    status = cli.main(
        [
            "detect",
            "--video", str(clip),
            "--out", str(out_path),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 0
    out = capsys.readouterr().out
    assert f"frames written : {CLIP_FRAME_COUNT}" in out
    assert str(out_path) in out
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_main_detect_exits_non_zero_naming_unloadable_weights(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=2)

    def unloadable(config: Config):
        raise ModelError(f"could not load the YOLO weights configured at {config.model_path}")

    monkeypatch.setattr(cli, "YoloDetector", unloadable)

    status = cli.main(
        [
            "detect",
            "--video", str(clip),
            "--out", str(tmp_path / "out.mp4"),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 1
    assert "could not load the YOLO weights" in capsys.readouterr().err


def test_main_detect_exits_non_zero_naming_a_missing_video(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    missing = tmp_path / "absent.mp4"
    monkeypatch.setattr(cli, "YoloDetector", lambda config: FakeDetector([]))

    status = cli.main(
        [
            "detect",
            "--video", str(missing),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 1
    assert str(missing) in capsys.readouterr().err
