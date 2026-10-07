"""Unit tests for display mode and the ``play`` entry point.

Requirements 1.3 (render every frame, close the window at the end), 1.6 (quit key
stops reading and releases everything), and 15.1 (runnable entry point reporting
frame count and frame rate).

Every GUI call is monkeypatched, so the suite runs on a headless machine and the
assertions can check exactly what display mode did rather than what it drew.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src import main as cli
from src.config import Config, load_config
from src.errors import VideoError
from src.video_io import PLAY_WINDOW_NAME, play

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

CLIP_WIDTH = 64
CLIP_HEIGHT = 48
CLIP_FRAME_COUNT = 6
CLIP_FRAME_RATE = 20.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _write_clip(path: Path, frame_count: int = CLIP_FRAME_COUNT) -> str:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        CLIP_FRAME_RATE,
        (CLIP_WIDTH, CLIP_HEIGHT),
    )
    assert writer.isOpened(), f"no encoder available for {path.name}"
    try:
        for index in range(frame_count):
            frame = np.zeros((CLIP_HEIGHT, CLIP_WIDTH, 3), dtype=np.uint8)
            frame[:, :, 0] = index * 30
            writer.write(frame)
    finally:
        writer.release()
    return str(path)


class _GuiSpy:
    """Records the GUI calls display mode makes and scripts the key presses."""

    def __init__(self, monkeypatch, keys: list[int] | None = None) -> None:
        self.shown: list[str] = []
        self.destroy_all_calls = 0
        self._keys = list(keys or [])
        monkeypatch.setattr(cv2, "imshow", self._imshow)
        monkeypatch.setattr(cv2, "waitKey", self._wait_key)
        monkeypatch.setattr(cv2, "destroyAllWindows", self._destroy_all)

    def _imshow(self, window: str, frame: np.ndarray) -> None:
        assert frame.shape == (CLIP_HEIGHT, CLIP_WIDTH, 3)
        self.shown.append(window)

    def _wait_key(self, delay: int) -> int:
        assert delay >= 1, "a zero delay would block until a key press"
        return self._keys.pop(0) if self._keys else -1

    def _destroy_all(self) -> None:
        self.destroy_all_calls += 1


# ---------------------------------------------------------------------------
# Requirement 1.3 — render each frame, close the window after the final frame
# ---------------------------------------------------------------------------


def test_play_renders_every_frame_once_in_one_window(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    gui = _GuiSpy(monkeypatch)
    result = play(_write_clip(tmp_path / "clip.mp4"), config)

    assert result.frames_shown == CLIP_FRAME_COUNT
    assert gui.shown == [PLAY_WINDOW_NAME] * CLIP_FRAME_COUNT
    assert result.quit_early is False


def test_play_closes_the_window_after_the_final_frame(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    gui = _GuiSpy(monkeypatch)
    play(_write_clip(tmp_path / "clip.mp4"), config)
    assert gui.destroy_all_calls >= 1


def test_play_reports_the_video_properties(tmp_path: Path, config: Config, monkeypatch) -> None:
    _GuiSpy(monkeypatch)
    info = play(_write_clip(tmp_path / "clip.mp4"), config).info
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)
    assert info.frame_count == CLIP_FRAME_COUNT
    assert info.frame_rate == pytest.approx(CLIP_FRAME_RATE)


# ---------------------------------------------------------------------------
# Requirement 1.6 — quit key stops reading and releases everything
# ---------------------------------------------------------------------------


def test_quit_key_stops_early_and_releases_the_capture(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    released: list[bool] = []
    real_capture = cv2.VideoCapture

    class _Capture:
        def __init__(self, path: str, *a, **k):
            self._capture = real_capture(path)

        def isOpened(self) -> bool:  # noqa: N802 - OpenCV's spelling
            return self._capture.isOpened()

        def get(self, prop: int) -> float:
            return self._capture.get(prop)

        def read(self):
            return self._capture.read()

        def release(self) -> None:
            released.append(True)
            self._capture.release()

    monkeypatch.setattr(cv2, "VideoCapture", _Capture)
    # No key on the first frame, the quit key on the second.
    gui = _GuiSpy(monkeypatch, keys=[-1, ord(config.quit_key)])

    result = play(_write_clip(tmp_path / "clip.mp4"), config)

    assert result.quit_early is True
    assert result.frames_shown == 2, "reading must stop on the quit key"
    assert len(gui.shown) == 2
    assert released == [True]
    assert gui.destroy_all_calls >= 1


def test_quit_key_is_case_insensitive(tmp_path: Path, config: Config, monkeypatch) -> None:
    _GuiSpy(monkeypatch, keys=[ord(config.quit_key.upper())])
    result = play(_write_clip(tmp_path / "clip.mp4"), config)
    assert result.quit_early is True
    assert result.frames_shown == 1


def test_other_key_presses_do_not_stop_playback(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    _GuiSpy(monkeypatch, keys=[ord("a"), ord("Z"), 3])
    result = play(_write_clip(tmp_path / "clip.mp4"), config)
    assert result.quit_early is False
    assert result.frames_shown == CLIP_FRAME_COUNT


def test_resources_are_released_when_a_gui_call_fails(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    destroyed: list[bool] = []

    def broken_imshow(window: str, frame: np.ndarray) -> None:
        raise cv2.error("no GUI support in this build")

    monkeypatch.setattr(cv2, "imshow", broken_imshow)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: destroyed.append(True))

    clip = _write_clip(tmp_path / "clip.mp4")
    with pytest.raises(VideoError) as excinfo:
        play(clip, config)
    assert clip in str(excinfo.value)
    assert destroyed == [True]


# ---------------------------------------------------------------------------
# Headless path — the same loop with no GUI call
# ---------------------------------------------------------------------------


def test_display_disabled_makes_no_gui_calls(
    tmp_path: Path, config: Config, monkeypatch
) -> None:
    def fail(*args, **kwargs):  # pragma: no cover - only runs if the guard breaks
        raise AssertionError("display=False must not touch the GUI")

    monkeypatch.setattr(cv2, "imshow", fail)
    monkeypatch.setattr(cv2, "waitKey", fail)

    result = play(_write_clip(tmp_path / "clip.mp4"), config, display=False)
    assert result.frames_shown == CLIP_FRAME_COUNT
    assert result.quit_early is False


# ---------------------------------------------------------------------------
# Requirement 15.1 — the play entry point
# ---------------------------------------------------------------------------


def test_main_play_reports_frame_count_and_frame_rate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _GuiSpy(monkeypatch)
    clip = _write_clip(tmp_path / "clip.mp4")

    status = cli.main(["play", "--video", clip, "--config", str(DEFAULT_CONFIG_PATH)])

    assert status == 0
    out = capsys.readouterr().out
    assert f"frame count : {CLIP_FRAME_COUNT}" in out
    assert f"frame rate  : {CLIP_FRAME_RATE:.2f} fps" in out
    assert f"frames shown: {CLIP_FRAME_COUNT}" in out


def test_main_play_runs_headless_with_no_display(tmp_path: Path, monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):  # pragma: no cover - only runs if the guard breaks
        raise AssertionError("--no-display must not touch the GUI")

    monkeypatch.setattr(cv2, "imshow", fail)
    monkeypatch.setattr(cv2, "waitKey", fail)

    status = cli.main(
        [
            "play",
            "--video", _write_clip(tmp_path / "clip.mp4"),
            "--config", str(DEFAULT_CONFIG_PATH),
            "--no-display",
        ]
    )

    assert status == 0
    assert f"frames shown: {CLIP_FRAME_COUNT}" in capsys.readouterr().out


def test_main_play_exits_non_zero_naming_a_missing_video(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "absent.mp4"
    status = cli.main(
        ["play", "--video", str(missing), "--config", str(DEFAULT_CONFIG_PATH)]
    )
    assert status == 1
    assert str(missing) in capsys.readouterr().err
