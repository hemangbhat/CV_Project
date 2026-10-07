"""Tests for the measurement milestone: the region layer, the approach panel, and
``python -m src.main measure --video V``.

Validates: Requirements 12.1, 12.2, 15.4

Every run injects ``FakeDetector`` and ``FakeTracker``, so the suite needs no YOLO
weights and no GPU, and the GUI is never touched except where a test monkeypatches
``cv2`` deliberately.

The configuration used here is scaled to the synthetic clip: the shipped
``config/default.json`` is calibrated for 1280x720, so on a 64x48 clip every
reference point would fall outside every ROI and the measurements would all be
zero for the wrong reason. The four ROIs below tile the clip into quadrants, and
the scripted detections are placed at known points inside three of them.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src import main as cli
from src.config import APPROACH_NAMES, ApproachConfig, Config, load_config, to_json_obj
from src.detection import Detection
from src.errors import ModelError
from src.overlay import (
    ALL_RED_STATES,
    MEASURE_VIDEO_SUFFIX,
    TRACK_VIDEO_DIR,
    approach_color,
    approach_panel_row,
    default_measure_output_path,
    draw_approach_panel_on,
    draw_measurements,
    draw_regions,
    queue_color,
    run_measurement_video,
)
from src.traffic_metrics import RED, ApproachMetrics
from tests.fixtures import (
    DEFAULT_CLIP_HEIGHT,
    DEFAULT_CLIP_WIDTH,
    FakeDetector,
    FakeTracker,
    scripted_detections,
    synthetic_frame,
    write_synthetic_clip,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

CLIP_FRAME_COUNT = 6

#: Reference points of the three scripted vehicles below, one per quadrant.
#: Each is the bottom-edge midpoint of its box, which is what the assigner tests.
NORTH_REF = (15, 8)
EAST_REF = (47, 20)
SOUTH_REF = (47, 40)


def _approach(
    name: str,
    roi: tuple[tuple[int, int], ...],
    queue: tuple[tuple[int, int], ...],
) -> ApproachConfig:
    return ApproachConfig(
        name=name,
        roi_polygon=roi,
        queue_region=queue,
        saturation_count=4.0,
        queue_capacity=2.0,
    )


@pytest.fixture
def config() -> Config:
    """The shipped configuration rescaled to tile the synthetic clip in quadrants.

    Queue regions occupy the lower half of each ROI, so ``NORTH_REF`` sits in
    North's ROI but outside its queue region while ``EAST_REF`` and ``SOUTH_REF``
    are queueing. That gives one Approach with a count above its queue length,
    which is the case worth having in the panel.
    """
    base = load_config(str(DEFAULT_CONFIG_PATH))
    width, height = DEFAULT_CLIP_WIDTH, DEFAULT_CLIP_HEIGHT
    mid_x, mid_y = width // 2, height // 2
    right, bottom = width - 1, height - 1
    return dataclasses.replace(
        base,
        frame_size=(width, height),
        approaches=(
            _approach(
                "North",
                ((0, 0), (mid_x - 1, 0), (mid_x - 1, mid_y - 1), (0, mid_y - 1)),
                ((0, 16), (mid_x - 1, 16), (mid_x - 1, mid_y - 1), (0, mid_y - 1)),
            ),
            _approach(
                "East",
                ((mid_x, 0), (right, 0), (right, mid_y - 1), (mid_x, mid_y - 1)),
                ((mid_x, 16), (right, 16), (right, mid_y - 1), (mid_x, mid_y - 1)),
            ),
            _approach(
                "South",
                ((mid_x, mid_y), (right, mid_y), (right, bottom), (mid_x, bottom)),
                ((mid_x, mid_y), (right, mid_y), (right, 40), (mid_x, 40)),
            ),
            _approach(
                "West",
                ((0, mid_y), (mid_x - 1, mid_y), (mid_x - 1, bottom), (0, bottom)),
                ((0, mid_y), (mid_x - 1, mid_y), (mid_x - 1, 40), (0, 40)),
            ),
        ),
    )


def _detection(ref: tuple[int, int], vehicle_class: str) -> Detection:
    """Return a detection whose bottom-edge midpoint is exactly ``ref``."""
    cx, cy = ref
    return Detection(
        x1=cx - 3,
        y1=cy - 6,
        x2=cx + 3,
        y2=cy,
        vehicle_class=vehicle_class,
        confidence=0.9,
    )


def _static_script(frame_count: int = CLIP_FRAME_COUNT) -> list[list[Detection]]:
    """One vehicle standing still in North, East, and South on every frame.

    Static boxes keep the expected measurements a fixed, stated quantity across
    the run, so the assertions name the numbers rather than recomputing the
    rectangle motion of the synthetic clip.
    """
    frame = [
        _detection(NORTH_REF, "car"),
        _detection(EAST_REF, "bus"),
        _detection(SOUTH_REF, "truck"),
    ]
    return [list(frame) for _ in range(frame_count)]


def _metrics(
    approach: str, count: int, queue: int, density: float, normalized: float
) -> ApproachMetrics:
    return ApproachMetrics(
        approach=approach,
        vehicle_count=count,
        vehicle_density=density,
        queue_length=queue,
        normalized_queue=normalized,
    )


def _decoded_frame_count(path: str | Path) -> int:
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
# Requirement 12.1 — the region layer
# ---------------------------------------------------------------------------


def test_region_layer_draws_every_roi_and_queue_region(config: Config) -> None:
    """Validates: Requirements 12.1"""
    frame = np.zeros((DEFAULT_CLIP_HEIGHT, DEFAULT_CLIP_WIDTH, 3), dtype=np.uint8)

    drawn = draw_regions(frame, config)

    painted = {tuple(int(c) for c in colour) for colour in drawn.reshape(-1, 3)}
    for name in APPROACH_NAMES:
        assert approach_color(name) in painted, f"{name} ROI outline was not drawn"
        assert queue_color(name) in painted, f"{name} queue region was not drawn"


def test_queue_region_colour_differs_from_its_parent_roi() -> None:
    """Validates: Requirements 12.1"""
    for name in APPROACH_NAMES:
        assert queue_color(name) != approach_color(name)


def test_region_layer_does_not_mutate_the_input_frame(config: Config) -> None:
    frame = synthetic_frame(0)
    original = frame.copy()

    drawn = draw_regions(frame, config)

    assert np.array_equal(frame, original)
    assert not np.array_equal(drawn, original)


def test_regions_calibrated_for_a_larger_frame_still_draw() -> None:
    """A configuration for 1280x720 must clip, not raise, on a 64x48 frame."""
    large = load_config(str(DEFAULT_CONFIG_PATH))
    frame = np.zeros((DEFAULT_CLIP_HEIGHT, DEFAULT_CLIP_WIDTH, 3), dtype=np.uint8)

    assert draw_regions(frame, large).shape == frame.shape


# ---------------------------------------------------------------------------
# Requirements 12.2, 15.4 — the approach panel
# ---------------------------------------------------------------------------


def test_panel_row_names_the_approach_and_reports_two_decimals() -> None:
    """Validates: Requirements 12.2, 15.4"""
    row = approach_panel_row("North", _metrics("North", 3, 2, 0.25, 0.5), 0.375)

    assert row.startswith("North")
    assert "n=3" in row
    assert "q=2" in row
    assert "d=0.25" in row
    assert "s=0.38" in row


def test_panel_draws_one_row_per_approach() -> None:
    """Validates: Requirements 12.2

    Drawn on a frame tall enough to hold four rows; the 48-pixel synthetic clip
    is shorter than the panel itself, which no junction video will be.
    """
    frame = np.zeros((120, 240, 3), dtype=np.uint8)
    metrics = {
        name: _metrics(name, 1, 1, 0.25, 0.5) for name in APPROACH_NAMES
    }
    scores = {name: 0.375 for name in APPROACH_NAMES}

    drawn = draw_approach_panel_on(frame.copy(), metrics, scores)

    painted = {tuple(int(c) for c in colour) for colour in drawn.reshape(-1, 3)}
    for name in APPROACH_NAMES:
        assert approach_color(name) in painted, f"{name} panel row was not drawn"


def test_measurement_overlay_leaves_the_callers_frame_untouched(config: Config) -> None:
    """Validates: Requirements 15.4"""
    frame = synthetic_frame(1)
    original = frame.copy()
    metrics = {name: _metrics(name, 0, 0, 0.0, 0.0) for name in APPROACH_NAMES}

    annotated = draw_measurements(
        frame, [], metrics, {name: 0.0 for name in APPROACH_NAMES}, config
    )

    assert np.array_equal(frame, original)
    assert annotated.shape == frame.shape
    assert not np.array_equal(annotated, original), "regions and panel must be drawn"


# ---------------------------------------------------------------------------
# Requirement 15.4 — the measurement run
# ---------------------------------------------------------------------------


def test_measurement_reports_the_expected_per_approach_numbers(
    tmp_path: Path, config: Config
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)

    result = run_measurement_video(
        str(clip),
        config,
        detector=FakeDetector(_static_script()),
        tracker=FakeTracker(config),
        display=False,
    )

    assert result.frames_processed == CLIP_FRAME_COUNT
    assert result.out_path is None and result.frames_written == 0
    assert set(result.metrics) == set(APPROACH_NAMES)

    counts = {name: result.metrics[name].vehicle_count for name in APPROACH_NAMES}
    queues = {name: result.metrics[name].queue_length for name in APPROACH_NAMES}
    assert counts == {"North": 1, "East": 1, "South": 1, "West": 0}
    # North's vehicle stands above its queue region; the other two are queueing.
    assert queues == {"North": 0, "East": 1, "South": 1, "West": 0}

    # saturation_count 4, queue_capacity 2, alpha 0.5 from the fixture config.
    assert result.metrics["East"].vehicle_density == pytest.approx(0.25)
    assert result.metrics["East"].normalized_queue == pytest.approx(0.5)
    assert result.scores["East"] == pytest.approx(0.375)
    assert result.scores["West"] == pytest.approx(0.0)
    assert result.unassigned_tracks == 0


def test_waiting_time_accrues_against_the_all_red_state(
    tmp_path: Path, config: Config
) -> None:
    """Validates: Requirements 15.4"""
    assert ALL_RED_STATES == {name: RED for name in APPROACH_NAMES}
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)

    result = run_measurement_video(
        str(clip),
        config,
        detector=FakeDetector(_static_script()),
        tracker=FakeTracker(config),
        display=False,
    )

    dt = 1.0 / result.info.frame_rate
    # The two queueing vehicles waited every frame; the North one never queued.
    assert len(result.waiting_times) == 2
    for waited in result.waiting_times.values():
        assert waited == pytest.approx(CLIP_FRAME_COUNT * dt)


def test_measurement_writes_one_frame_per_input_frame_when_asked(
    tmp_path: Path, config: Config
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    out_path = tmp_path / "out.mp4"

    result = run_measurement_video(
        str(clip),
        config,
        str(out_path),
        detector=FakeDetector(scripted_detections(CLIP_FRAME_COUNT)),
        tracker=FakeTracker(config),
        display=False,
    )

    assert result.out_path == str(out_path)
    assert result.frames_written == result.frames_processed == CLIP_FRAME_COUNT
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_measurement_display_shows_every_frame_and_honours_the_quit_key(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    shown: list[str] = []
    keys = [0xFF, 0xFF, ord(config.quit_key)]

    monkeypatch.setattr(cv2, "imshow", lambda window, frame: shown.append(window))
    monkeypatch.setattr(cv2, "waitKey", lambda delay: keys[len(shown) - 1])
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)

    result = run_measurement_video(
        str(clip),
        config,
        detector=FakeDetector(_static_script()),
        tracker=FakeTracker(config),
        display=True,
    )

    assert result.quit_early is True
    assert result.frames_processed == len(shown) == len(keys)


def test_default_measure_output_path_lives_under_results_videos() -> None:
    out_path = default_measure_output_path("videos/junction_a.mp4")

    assert Path(out_path).parent == Path(TRACK_VIDEO_DIR)
    assert MEASURE_VIDEO_SUFFIX in Path(out_path).stem


# ---------------------------------------------------------------------------
# Requirement 15.4 — the measure entry point
# ---------------------------------------------------------------------------


def _write_config(path: Path, config: Config) -> Path:
    path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")
    return path


def _patch_tracker(monkeypatch, script: list[list[Detection]]) -> None:
    """Patch ``ByteTrackTracker`` with a fake that detects and tracks in one pass."""
    detector = FakeDetector(script)

    class DetectingFakeTracker(FakeTracker):
        def update(self, frame, detections):
            return super().update(frame, detector.detect(frame))

    monkeypatch.setattr(cli, "ByteTrackTracker", lambda config: DetectingFakeTracker(config))


def test_main_measure_prints_live_per_approach_statistics(
    tmp_path: Path, config: Config, monkeypatch, capsys
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    config_path = _write_config(tmp_path / "config.json", config)
    out_path = tmp_path / "out.mp4"
    _patch_tracker(monkeypatch, _static_script())

    status = cli.main(
        [
            "measure",
            "--video", str(clip),
            "--out", str(out_path),
            "--config", str(config_path),
            "--no-display",
        ]
    )

    assert status == 0
    out = capsys.readouterr().out
    assert f"frames read    : {CLIP_FRAME_COUNT}" in out
    for name in APPROACH_NAMES:
        assert name in out
    assert "n=1" in out and "q=1" in out
    assert "d=0.25" in out and "s=0.38" in out
    assert f"frames written : {CLIP_FRAME_COUNT}" in out
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_main_measure_without_out_writes_no_video(
    tmp_path: Path, config: Config, monkeypatch, capsys
) -> None:
    """Validates: Requirements 15.4"""
    monkeypatch.chdir(tmp_path)
    clip = write_synthetic_clip(tmp_path / "junction.mp4", frame_count=3)
    config_path = _write_config(tmp_path / "config.json", config)
    _patch_tracker(monkeypatch, _static_script(3))

    status = cli.main(
        [
            "measure",
            "--video", str(clip),
            "--config", str(config_path),
            "--no-display",
        ]
    )

    assert status == 0
    assert "output video" not in capsys.readouterr().out
    assert not Path(TRACK_VIDEO_DIR).exists()


def test_main_measure_does_not_touch_the_gui_when_display_is_off(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=3)
    config_path = _write_config(tmp_path / "config.json", config)
    _patch_tracker(monkeypatch, _static_script(3))

    def fail(*args, **kwargs):
        raise AssertionError("--no-display must not touch the GUI")

    monkeypatch.setattr(cv2, "imshow", fail)
    monkeypatch.setattr(cv2, "waitKey", fail)

    assert cli.main(
        ["measure", "--video", str(clip), "--config", str(config_path), "--no-display"]
    ) == 0


def test_main_measure_exits_non_zero_naming_unloadable_weights(
    tmp_path: Path, config: Config, monkeypatch, capsys
) -> None:
    """Validates: Requirements 15.4"""
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=2)
    config_path = _write_config(tmp_path / "config.json", config)

    def unloadable(cfg: Config):
        raise ModelError(f"could not load the YOLO weights configured at {cfg.model_path}")

    monkeypatch.setattr(cli, "ByteTrackTracker", unloadable)

    status = cli.main(
        ["measure", "--video", str(clip), "--config", str(config_path), "--no-display"]
    )

    assert status == 1
    assert "could not load the YOLO weights" in capsys.readouterr().err


def test_main_measure_exits_non_zero_naming_a_missing_video(
    tmp_path: Path, config: Config, monkeypatch, capsys
) -> None:
    """Validates: Requirements 15.4"""
    missing = tmp_path / "absent.mp4"
    config_path = _write_config(tmp_path / "config.json", config)
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda cfg: FakeTracker(cfg))

    status = cli.main(
        ["measure", "--video", str(missing), "--config", str(config_path), "--no-display"]
    )

    assert status == 1
    assert str(missing) in capsys.readouterr().err


def test_demo_overlay_draws_without_changing_the_input() -> None:
    """The research dashboard renders on a real-sized frame and leaves its input intact."""
    from src.config import load_config as _load
    from src.overlay import SignalOverlay, demo_panel_rows
    from src.signal_controller import PhaseInfo, SignalState

    cfg = _load("config/bellevue_116th_v2.json")
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    metrics = {n: _metrics(n, 3, 1, 0.3, 0.25) for n in ("North", "East", "South", "West")}
    scores = {"North": 0.2, "East": 0.1, "South": 0.6, "West": 0.5}
    states = {n: SignalState.GREEN if n == "North" else SignalState.RED for n in scores}
    info = PhaseInfo(10, "North", SignalState.GREEN, 0, 0, 30.0, 30.0, 12.0, 0.2, False, False, False)
    out = SignalOverlay(cfg, "demo").draw(frame, [], [], metrics, scores, states, info, "adaptive", 1.0)
    assert out.shape == frame.shape and out.any() and not frame.any()
    rows = demo_panel_rows(metrics, scores, states)
    assert [r[0] for r in rows] == ["North", "East", "South", "West"]
    assert rows[0][1] == "G" and rows[1][1] == "R"
