"""Tests for the track layer and the tracking milestone entry point.

Requirements 3.6 (each active track's box, Track_ID, Vehicle_Class, and
trajectory polyline drawn on the output frame) and 15.3
(``python -m src.main track --video V`` writing the tracked annotated video to
``results/videos/``).

Every run injects ``FakeDetector`` and ``FakeTracker``, so the suite needs no
YOLO weights and no GPU. The frame-count assertion decodes the written file
rather than trusting container metadata, because a size-mismatched frame is
discarded by ``cv2.VideoWriter`` without raising.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src import main as cli
from src.config import Config, load_config
from src.errors import ModelError, VideoError
from src.overlay import (
    TRACK_VIDEO_DIR,
    default_track_output_path,
    draw_label_on,
    draw_tracks,
    draw_trajectory_on,
    run_tracking_video,
    track_color,
)
from src.tracking import Track, new_trajectory
from tests.fixtures import (
    DEFAULT_CLIP_FRAME_RATE,
    FakeDetector,
    FakeTracker,
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


def _track(track_id: int, points: list[tuple[int, int]] | None = None) -> Track:
    track = Track(
        track_id=track_id,
        x1=4,
        y1=6,
        x2=16,
        y2=18,
        vehicle_class="car",
        trajectory=new_trajectory(30, points or []),
    )
    if points is None:
        track.record_position()
    return track


# ---------------------------------------------------------------------------
# Requirement 3.6 — the track layer
# ---------------------------------------------------------------------------


def test_draw_tracks_does_not_mutate_the_input_frame() -> None:
    frame = synthetic_frame(0)
    original = frame.copy()

    annotated = draw_tracks(frame, [_track(1)])

    assert np.array_equal(frame, original), "the caller's frame must be untouched"
    assert not np.array_equal(annotated, original), "tracks must be drawn"
    assert annotated.shape == frame.shape


def test_draw_tracks_leaves_a_frame_without_tracks_unchanged() -> None:
    frame = synthetic_frame(2)
    assert np.array_equal(draw_tracks(frame, []), frame)


def test_track_label_names_the_track_id_and_vehicle_class() -> None:
    assert _track(12).label() == "#12 car"


def test_track_colour_is_stable_per_id_and_differs_between_ids() -> None:
    assert track_color(3) == track_color(3)
    assert track_color(0) != track_color(1)


def test_the_trajectory_polyline_is_drawn_between_its_points() -> None:
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    points = [(5, 30), (5, 20), (5, 10)]

    drawn = draw_trajectory_on(frame.copy(), points, (0, 0, 255))

    # A point midway along the line must have been painted; without a polyline
    # only the endpoints would ever be touched.
    assert drawn[25, 5].tolist() != [0, 0, 0]
    assert drawn[15, 5].tolist() != [0, 0, 0]


def test_a_single_point_trajectory_draws_no_polyline() -> None:
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    assert np.array_equal(draw_trajectory_on(frame.copy(), [(5, 5)], (0, 255, 0)), frame)


def test_a_track_at_the_frame_edges_is_drawn_without_raising() -> None:
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    edge_tracks = [
        Track(
            track_id=1, x1=0, y1=0, x2=30, y2=20, vehicle_class="bus",
            trajectory=new_trajectory(5, [(0, 0), (29, 19)]),
        ),
        Track(
            track_id=2, x1=28, y1=18, x2=30, y2=20, vehicle_class="truck",
            trajectory=new_trajectory(5, [(29, 19)]),
        ),
    ]
    annotated = draw_tracks(frame, edge_tracks)
    assert annotated.shape == frame.shape


def test_draw_label_on_a_zero_sized_margin_stays_inside_the_frame() -> None:
    frame = np.zeros((12, 12, 3), dtype=np.uint8)
    drawn = draw_label_on(frame.copy(), "#1 car", 11, 0, (0, 255, 0))
    assert drawn.shape == frame.shape
    assert not np.array_equal(drawn, frame)


# ---------------------------------------------------------------------------
# Requirement 15.3 — the tracked annotated video
# ---------------------------------------------------------------------------


def test_output_video_has_one_frame_per_processed_input_frame(
    tmp_path: Path, config: Config
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    out_path = tmp_path / "clip__tracks.mp4"

    result = run_tracking_video(
        str(clip),
        config,
        str(out_path),
        detector=FakeDetector(scripted_detections(CLIP_FRAME_COUNT)),
        tracker=FakeTracker(config),
    )

    assert result.frames_processed == CLIP_FRAME_COUNT
    assert result.frames_written == result.frames_processed
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_every_scripted_vehicle_keeps_one_track_id_across_the_run(
    tmp_path: Path, config: Config
) -> None:
    script = scripted_detections(CLIP_FRAME_COUNT)
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)

    result = run_tracking_video(
        str(clip),
        config,
        str(tmp_path / "out.mp4"),
        detector=FakeDetector(script),
        tracker=FakeTracker(config),
    )

    assert result.tracks_drawn == sum(len(frame) for frame in script)
    # Three moving rectangles, tracked throughout: three ids, not one per frame.
    assert result.unique_track_count == len(script[0])


def test_output_video_uses_the_input_frame_rate_and_resolution(
    tmp_path: Path, config: Config
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=4)
    out_path = tmp_path / "out.mp4"

    result = run_tracking_video(
        str(clip),
        config,
        str(out_path),
        detector=FakeDetector(scripted_detections(4)),
        tracker=FakeTracker(config),
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


def test_default_output_path_lives_under_results_videos() -> None:
    out_path = default_track_output_path("videos/junction_a.mp4")
    assert Path(out_path).parent == Path(TRACK_VIDEO_DIR)
    assert Path(out_path).stem.startswith("junction_a")
    assert out_path != "videos/junction_a.mp4"


def test_default_output_path_is_used_and_created(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    clip = write_synthetic_clip(tmp_path / "junction.mp4", frame_count=3)

    result = run_tracking_video(
        str(clip),
        config,
        detector=FakeDetector(scripted_detections(3)),
        tracker=FakeTracker(config),
    )

    assert Path(result.out_path).parent == Path(TRACK_VIDEO_DIR)
    assert Path(result.out_path).is_file()
    assert _decoded_frame_count(result.out_path) == 3


def test_a_missing_video_raises_naming_the_path(tmp_path: Path, config: Config) -> None:
    missing = tmp_path / "absent.mp4"
    with pytest.raises(VideoError, match=str(missing.name)):
        run_tracking_video(
            str(missing), config, str(tmp_path / "out.mp4"), tracker=FakeTracker(config)
        )


# ---------------------------------------------------------------------------
# Requirement 15.3 — the track entry point
# ---------------------------------------------------------------------------


def test_main_track_writes_the_annotated_video(tmp_path: Path, monkeypatch, capsys) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=CLIP_FRAME_COUNT)
    out_path = tmp_path / "out.mp4"
    script = scripted_detections(CLIP_FRAME_COUNT)
    detector = FakeDetector(script)

    class DetectingFakeTracker(FakeTracker):
        """A fake standing in for ByteTrack, which detects and tracks in one pass."""

        def update(self, frame, detections):
            return super().update(frame, detector.detect(frame))

    monkeypatch.setattr(cli, "ByteTrackTracker", lambda config: DetectingFakeTracker(config))

    status = cli.main(
        [
            "track",
            "--video", str(clip),
            "--out", str(out_path),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 0
    out = capsys.readouterr().out
    assert f"frames written : {CLIP_FRAME_COUNT}" in out
    assert f"unique ids     : {len(script[0])}" in out
    assert str(out_path) in out
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_main_track_exits_non_zero_naming_unloadable_weights(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    clip = write_synthetic_clip(tmp_path / "clip.mp4", frame_count=2)

    def unloadable(config: Config):
        raise ModelError(f"could not load the YOLO weights configured at {config.model_path}")

    monkeypatch.setattr(cli, "ByteTrackTracker", unloadable)

    status = cli.main(
        [
            "track",
            "--video", str(clip),
            "--out", str(tmp_path / "out.mp4"),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 1
    assert "could not load the YOLO weights" in capsys.readouterr().err


def test_main_track_exits_non_zero_naming_a_missing_video(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    missing = tmp_path / "absent.mp4"
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda config: FakeTracker(config))

    status = cli.main(
        [
            "track",
            "--video", str(missing),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 1
    assert str(missing) in capsys.readouterr().err
