"""Unit tests for the Video_Ingestor (Requirements 1.1, 1.2, 1.4, 1.5).

Clips come from :mod:`tests.fixtures`, which generates them with NumPy plus
``cv2.VideoWriter``, so the tests need no checked-in footage and every test in the
suite reads the same kind of clip. The generated-length version of the frame-index
invariant lives in ``test_video_io_properties.py`` (Property 18).
"""

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config import Config, load_config
from src.errors import VideoError
from src.video_io import VideoIngestor, simulated_clock
from tests.fixtures import (
    DEFAULT_CLIP_FRAME_RATE,
    DEFAULT_CLIP_HEIGHT,
    DEFAULT_CLIP_WIDTH,
    synthetic_frames,
    write_synthetic_clip,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

CLIP_WIDTH = DEFAULT_CLIP_WIDTH
CLIP_HEIGHT = DEFAULT_CLIP_HEIGHT
CLIP_FRAME_COUNT = 7
CLIP_FRAME_RATE = DEFAULT_CLIP_FRAME_RATE


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _write_clip(
    path: Path,
    frame_count: int = CLIP_FRAME_COUNT,
    frame_rate: float = CLIP_FRAME_RATE,
) -> Path:
    """Write a short synthetic clip and return its path."""
    return write_synthetic_clip(path, frame_count=frame_count, frame_rate=frame_rate)


#: Bound before any test patches ``cv2.VideoCapture``, so the spy below wraps the
#: genuine capture rather than itself.
_REAL_VIDEO_CAPTURE = cv2.VideoCapture


class _SpyCapture:
    """Real capture with one reported value or one read overridden.

    Used only where a genuine file cannot exercise the branch: OpenCV reports a
    positive FPS for every container this environment can write, so the
    Requirement 1.5 fallback and the mid-open decode failure of Requirement 1.4
    are unreachable through file contents alone. Everything else delegates to the
    real capture reading the real clip.
    """

    def __init__(self, path: str, fps: float | None = None, fail_first_read: bool = False):
        self._capture = _REAL_VIDEO_CAPTURE(path)
        self._fps = fps
        self._fail_first_read = fail_first_read

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV's spelling
        return self._capture.isOpened()

    def get(self, prop: int) -> float:
        if self._fps is not None and prop == cv2.CAP_PROP_FPS:
            return self._fps
        return self._capture.get(prop)

    def read(self):
        if self._fail_first_read:
            return False, None
        return self._capture.read()

    def release(self) -> None:
        self._capture.release()


# ---------------------------------------------------------------------------
# Requirement 1.1 — reported video properties
# ---------------------------------------------------------------------------


def test_info_reports_dimensions_frame_count_and_frame_rate(
    tmp_path: Path, config: Config
) -> None:
    ingestor = VideoIngestor(str(_write_clip(tmp_path / "clip.mp4")), config)
    try:
        info = ingestor.info
        assert info.width == CLIP_WIDTH
        assert info.height == CLIP_HEIGHT
        assert info.frame_count == CLIP_FRAME_COUNT
        assert info.frame_rate == pytest.approx(CLIP_FRAME_RATE)
        assert info.frame_rate_substituted is False
        assert info.path == str(tmp_path / "clip.mp4")
    finally:
        ingestor.close()


# ---------------------------------------------------------------------------
# Requirement 1.2 — frame indexing
# ---------------------------------------------------------------------------


def test_frames_yield_consecutive_indices_from_zero_including_the_first_frame(
    tmp_path: Path, config: Config
) -> None:
    ingestor = VideoIngestor(str(_write_clip(tmp_path / "clip.mp4")), config)
    try:
        pairs = list(ingestor.frames())
    finally:
        ingestor.close()

    assert [index for index, _ in pairs] == list(range(CLIP_FRAME_COUNT))
    # The frame decoded during __init__ to validate the file must still be handed
    # out as index 0, and every frame must arrive in its own slot. Compression is
    # lossy, so each decoded frame is matched to the written frame it resembles
    # most rather than compared exactly.
    expected = synthetic_frames(CLIP_FRAME_COUNT)
    for index, frame in pairs:
        assert frame.shape == (CLIP_HEIGHT, CLIP_WIDTH, 3)
        distances = [
            float(np.abs(frame.astype(np.int16) - candidate.astype(np.int16)).mean())
            for candidate in expected
        ]
        assert int(np.argmin(distances)) == index, f"frame {index} decoded out of place"


def test_frames_may_be_iterated_only_once(tmp_path: Path, config: Config) -> None:
    ingestor = VideoIngestor(str(_write_clip(tmp_path / "clip.mp4")), config)
    try:
        assert len(list(ingestor.frames())) == CLIP_FRAME_COUNT
        with pytest.raises(VideoError) as excinfo:
            list(ingestor.frames())
        assert str(tmp_path / "clip.mp4") in str(excinfo.value)
    finally:
        ingestor.close()


def test_frames_performs_no_gui_calls(tmp_path: Path, config: Config, monkeypatch) -> None:
    def fail(*args, **kwargs):  # pragma: no cover - only runs if the guard breaks
        raise AssertionError("frames() must not touch the GUI")

    monkeypatch.setattr(cv2, "imshow", fail)
    monkeypatch.setattr(cv2, "waitKey", fail)

    ingestor = VideoIngestor(str(_write_clip(tmp_path / "clip.mp4")), config)
    try:
        assert len(list(ingestor.frames())) == CLIP_FRAME_COUNT
    finally:
        ingestor.close()


# ---------------------------------------------------------------------------
# Requirement 1.4 — unusable input names the supplied path
# ---------------------------------------------------------------------------


def test_missing_file_raises_video_error_naming_the_path(tmp_path: Path, config: Config) -> None:
    missing = tmp_path / "absent.mp4"
    with pytest.raises(VideoError) as excinfo:
        VideoIngestor(str(missing), config)
    assert str(missing) in str(excinfo.value)


def test_undecodable_file_raises_video_error_naming_the_path(
    tmp_path: Path, config: Config
) -> None:
    not_a_video = tmp_path / "broken.mp4"
    not_a_video.write_bytes(b"this is not video data" * 32)
    with pytest.raises(VideoError) as excinfo:
        VideoIngestor(str(not_a_video), config)
    assert str(not_a_video) in str(excinfo.value)


def test_first_frame_decode_failure_raises_video_error_naming_the_path(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    clip = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        cv2, "VideoCapture", lambda path, *a, **k: _SpyCapture(path, fail_first_read=True)
    )
    with pytest.raises(VideoError) as excinfo:
        VideoIngestor(str(clip), config)
    assert str(clip) in str(excinfo.value)


# ---------------------------------------------------------------------------
# Requirement 1.5 — frame-rate substitution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reported", [0.0, -1.0, float("nan")])
def test_non_positive_or_nan_frame_rate_substitutes_the_configured_default(
    tmp_path: Path, config: Config, monkeypatch, reported: float
) -> None:
    clip = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(cv2, "VideoCapture", lambda path, *a, **k: _SpyCapture(path, fps=reported))

    ingestor = VideoIngestor(str(clip), config)
    try:
        assert ingestor.info.frame_rate_substituted is True
        assert ingestor.info.frame_rate == config.default_frame_rate
        # Substitution must not disturb frame reading.
        assert [i for i, _ in ingestor.frames()] == list(range(CLIP_FRAME_COUNT))
    finally:
        ingestor.close()


def test_positive_reported_frame_rate_is_kept(tmp_path: Path, config: Config) -> None:
    clip = _write_clip(tmp_path / "clip.mp4", frame_rate=12.0)
    ingestor = VideoIngestor(str(clip), config)
    try:
        assert ingestor.info.frame_rate_substituted is False
        assert ingestor.info.frame_rate == pytest.approx(12.0)
        assert ingestor.info.frame_rate != config.default_frame_rate
    finally:
        ingestor.close()


# ---------------------------------------------------------------------------
# Requirements 1.3, 1.6 — cleanup
# ---------------------------------------------------------------------------


def test_close_is_idempotent(tmp_path: Path, config: Config) -> None:
    ingestor = VideoIngestor(str(_write_clip(tmp_path / "clip.mp4")), config)
    ingestor.close()
    ingestor.close()
    ingestor.close()
    # info survives close, since the Results_Store records it after the run ends.
    assert ingestor.info.frame_count == CLIP_FRAME_COUNT
    with pytest.raises(VideoError):
        list(ingestor.frames())


# ---------------------------------------------------------------------------
# Requirements 11.1, 11.3 — Simulated_Clock helper
# ---------------------------------------------------------------------------


def test_simulated_clock_is_index_over_frame_rate_and_strictly_increasing() -> None:
    assert simulated_clock(0, 25.0) == 0.0
    assert simulated_clock(50, 25.0) == pytest.approx(2.0)
    values = [simulated_clock(i, 20.0) for i in range(10)]
    assert all(b > a for a, b in zip(values, values[1:]))
    assert all(math.isfinite(v) for v in values)
