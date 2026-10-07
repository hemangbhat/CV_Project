"""Video_Ingestor — the single owner of frame reading and frame indexing.

The video is the clock for this System: every timing decision downstream is
derived from a frame index and the video frame rate, never from wall-clock time.
That makes frame-index integrity a correctness concern rather than a detail, so
this module is the *only* place a frame is read from a capture (Requirement 1.2).

Responsibilities kept here:

* open a video and report its width, height, frame count, and frame rate
  (Requirement 1.1);
* raise :class:`~src.errors.VideoError` naming the supplied path when the file is
  missing, unopenable, or its first frame cannot be decoded (Requirement 1.4);
* substitute ``config.default_frame_rate`` and flag the substitution when the
  reported FPS is non-positive or NaN (Requirement 1.5);
* release the capture and destroy any windows in :meth:`VideoIngestor.close`
  (Requirements 1.3, 1.6).

:meth:`VideoIngestor.frames` performs no GUI calls at all. Display mode lives in
the separate :func:`play` wrapper so the headless evaluation path never touches a
window.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from src.config import Config
from src.errors import VideoError


@dataclass(frozen=True)
class VideoInfo:
    """Immutable description of an opened video (Requirement 1.1)."""

    path: str
    width: int
    height: int
    frame_count: int
    frame_rate: float
    frame_rate_substituted: bool     # True when the Req 1.5 fallback applied


def simulated_clock(frame_index: int, frame_rate: float) -> float:
    """Return the Simulated_Clock in seconds for ``frame_index``.

    A pure function of the index and the frame rate, with no capture or run
    state involved, so every component that needs video time agrees exactly
    (Requirements 11.1, 11.3). Strictly increasing in ``frame_index`` for any
    positive ``frame_rate``, which is what Requirement 11.3 needs; the frame rate
    is guaranteed positive by :class:`VideoIngestor` and by config validation.
    """
    return frame_index / frame_rate


class VideoIngestor:
    """Opens one video file and yields its frames in order, exactly once.

    ``__init__`` validates the file eagerly by decoding its first frame, so a
    broken input fails before any pipeline state is built (Requirement 1.4).
    That first frame is then *buffered* and later handed out by
    :meth:`frames` as index 0 rather than being re-read after a seek: seeking is
    codec- and container-dependent and is not guaranteed to land back exactly on
    frame 0, whereas buffering keeps the yielded index sequence trivially
    consecutive from 0 with a single owner (Requirement 1.2).
    """

    def __init__(self, path: str, config: Config) -> None:
        if not os.path.isfile(path):
            raise VideoError(f"video file not found: {path}")

        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            capture.release()
            raise VideoError(f"video file could not be opened by OpenCV: {path}")

        try:
            ok, first_frame = capture.read()
        except cv2.error as exc:                      # pragma: no cover - codec dependent
            capture.release()
            raise VideoError(f"video file could not be decoded by OpenCV: {path}: {exc}") from exc
        if not ok or first_frame is None:
            capture.release()
            raise VideoError(f"video file could not be decoded by OpenCV: {path}")

        self._capture: cv2.VideoCapture | None = capture
        self._first_frame: np.ndarray | None = first_frame
        self._frames_called = False
        self._closed = False

        reported_rate = capture.get(cv2.CAP_PROP_FPS)
        frame_rate_substituted = not math.isfinite(reported_rate) or reported_rate <= 0.0
        frame_rate = (
            float(config.default_frame_rate) if frame_rate_substituted else float(reported_rate)
        )

        # Container metadata is not always trustworthy, so fall back to the shape
        # of the frame that was just decoded, which always exists.
        height, width = first_frame.shape[0], first_frame.shape[1]
        reported_width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        reported_height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if math.isfinite(reported_width) and reported_width > 0:
            width = int(round(reported_width))
        if math.isfinite(reported_height) and reported_height > 0:
            height = int(round(reported_height))

        reported_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        frame_count = (
            int(round(reported_count))
            if math.isfinite(reported_count) and reported_count > 0
            else 0
        )

        self._info = VideoInfo(
            path=path,
            width=width,
            height=height,
            frame_count=frame_count,
            frame_rate=frame_rate,
            frame_rate_substituted=frame_rate_substituted,
        )

    @property
    def info(self) -> VideoInfo:
        """Description of the opened video, available before any frame is read."""
        return self._info

    def frames(self) -> Iterator[tuple[int, np.ndarray]]:
        """Yield ``(index, frame)`` pairs with indices 0, 1, 2, ... in order.

        No index is skipped or repeated, and the first frame decoded during
        ``__init__`` is yielded as index 0 (Requirement 1.2). The generator can
        be started only once per ingestor: a second start would resume from
        wherever the capture happens to sit and hand out an index sequence that
        no longer begins at 0, so it raises :class:`VideoError` instead.

        Performs no GUI calls, so the headless evaluation path never opens a
        window.
        """
        if self._frames_called:
            raise VideoError(f"frames() may be iterated only once per ingestor: {self._info.path}")
        self._frames_called = True

        if self._closed or self._capture is None:
            raise VideoError(f"video capture is already closed: {self._info.path}")

        first_frame, self._first_frame = self._first_frame, None
        if first_frame is not None:
            yield 0, first_frame

        index = 1
        while True:
            capture = self._capture
            if capture is None:          # closed while the caller was iterating
                return
            ok, frame = capture.read()
            if not ok or frame is None:
                return
            yield index, frame
            index += 1

    def close(self) -> None:
        """Release the capture and destroy any OpenCV windows.

        Safe to call more than once, so callers can invoke it from a ``finally``
        block regardless of whether the run ended normally, hit the quit key, or
        raised (Requirements 1.3, 1.6).
        """
        capture, self._capture = self._capture, None
        self._first_frame = None
        self._closed = True
        if capture is not None:
            capture.release()
        try:
            cv2.destroyAllWindows()
        except cv2.error:                # pragma: no cover - only on GUI-less builds
            pass


#: Title of the window opened by :func:`play`.
PLAY_WINDOW_NAME = "Adaptive Traffic Signal - play"


@dataclass(frozen=True)
class PlaybackResult:
    """Outcome of one :func:`play` call.

    The design sketch has ``play`` return a :class:`VideoInfo`; it is returned
    here as ``info`` alongside two facts only the playback loop knows:
    ``frames_shown`` (container metadata reports 0 frames for some codecs, and a
    quit stops early) and ``quit_early``, which is what lets the ``play`` entry
    point report honestly what it actually did.
    """

    info: VideoInfo
    frames_shown: int
    quit_early: bool


def play(path: str, config: Config, display: bool = True) -> PlaybackResult:
    """Play ``path`` in an OpenCV window and return what was shown.

    Each frame read is rendered in a single reused window, the window is closed
    once the final frame has been shown (Requirement 1.3), and pressing
    ``config.quit_key`` stops reading immediately (Requirement 1.6). Cleanup runs
    from a ``finally`` block, so the capture is released and the windows are
    destroyed on the normal end, on a quit, and on an exception alike.

    ``display=False`` runs the same loop with no GUI call at all, which is the
    ``--no-display`` path; it is otherwise identical, so headless runs exercise
    the same frame handling.
    """
    ingestor = VideoIngestor(path, config)
    info = ingestor.info

    # info.frame_rate is guaranteed positive: a non-positive or NaN reported rate
    # has already been replaced by the configured default (Requirement 1.5).
    delay_ms = max(1, int(round(1000.0 / info.frame_rate)))
    # Config validation guarantees a single-character quit key; accept it in
    # either case so a stray Caps Lock still quits.
    quit_codes = {ord(config.quit_key.lower()), ord(config.quit_key.upper())}

    frames_shown = 0
    quit_early = False
    try:
        for _index, frame in ingestor.frames():
            if display:
                try:
                    cv2.imshow(PLAY_WINDOW_NAME, frame)
                    key = cv2.waitKey(delay_ms) & 0xFF
                except cv2.error as exc:
                    raise VideoError(
                        f"display mode is unavailable in this OpenCV build, "
                        f"rerun with display disabled: {path}: {exc}"
                    ) from exc
            else:
                key = 0xFF                       # no key, no GUI touched
            frames_shown += 1
            if key in quit_codes:
                quit_early = True
                break
    finally:
        ingestor.close()

    return PlaybackResult(info=info, frames_shown=frames_shown, quit_early=quit_early)


__all__ = [
    "PLAY_WINDOW_NAME",
    "PlaybackResult",
    "VideoInfo",
    "VideoIngestor",
    "play",
    "simulated_clock",
]
