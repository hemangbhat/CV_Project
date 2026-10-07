"""Track_Cache — record YOLO + ByteTrack output once, replay it for every experiment.

Why this exists. Every ablation stage (S0 to S4 and the controls) differs only in how
the Score is composed; the detector and tracker are supposed to be identical across
stages. Re-running YOLO for each stage makes that true only if inference is
deterministic, and it makes each stage cost minutes of CPU. Recording the tracker's
per-frame output once and replaying it makes the vision input **identical by
construction** for every stage, and turns a re-run into a few seconds of arithmetic.

The cache stores exactly what :class:`~src.tracking.ByteTrackTracker` reported on each
frame — Track_ID, box, Vehicle_Class — and nothing derived from it, so every
measurement (assignment, density, queue, reach, risk) is still recomputed by the
normal pipeline from the replayed tracks. It also stores the three configuration
values that determine the tracker output (``model_path``, ``confidence_threshold``,
``track_buffer``) and :class:`CachedTracker` refuses to replay a cache under a
configuration that disagrees with them, so a cache can never silently stand in for a
different detector setting.

Format: gzip-compressed JSON ``{"version", "video_path", "frame_count", "tracker",
"frames": [[[id, x1, y1, x2, y2, class], ...], ...]}``.
"""

from __future__ import annotations

import gzip
import json
import os
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from src.config import Config
from src.errors import ConfigError
from src.tracking import Track, Tracker, new_trajectory

CACHE_VERSION = 1
DEFAULT_CACHE_DIR = "results/track_cache"


def tracker_fingerprint(config: Config) -> dict[str, Any]:
    """The configuration values that determine what the tracker reports."""
    return {
        "model_path": str(config.model_path),
        "confidence_threshold": float(config.confidence_threshold),
        "track_buffer": int(config.track_buffer),
    }


def default_cache_path(video_path: str, config: Config) -> str:
    """``results/track_cache/<video stem>__<model stem>__c<conf>.json.gz``."""
    stem = Path(video_path).stem
    model = Path(str(config.model_path)).stem
    conf = f"{float(config.confidence_threshold):.2f}".replace(".", "p")
    return os.path.join(DEFAULT_CACHE_DIR, f"{stem}__{model}__c{conf}.json.gz")


class RecordingTracker:
    """Wraps a real tracker and records every frame's tracks for later replay."""

    def __init__(self, inner: Tracker, config: Config, video_path: str) -> None:
        self._inner = inner
        self._config = config
        self._video_path = str(video_path)
        self._frames: list[list[list[Any]]] = []

    def update(self, frame: np.ndarray, detections: list) -> list[Track]:
        tracks = self._inner.update(frame, detections)
        self._frames.append(
            [[t.track_id, t.x1, t.y1, t.x2, t.y2, t.vehicle_class] for t in tracks]
        )
        return tracks

    @property
    def frames_recorded(self) -> int:
        return len(self._frames)

    def save(self, path: str) -> str:
        """Write the recorded frames to ``path`` (gzip JSON) and return it."""
        payload = {
            "version": CACHE_VERSION,
            "video_path": self._video_path,
            "frame_count": len(self._frames),
            "tracker": tracker_fingerprint(self._config),
            "frames": self._frames,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        return path

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def load_cache(path: str) -> dict[str, Any]:
    """Read a cache file written by :meth:`RecordingTracker.save`."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("version") != CACHE_VERSION:
        raise ConfigError(
            f"track cache {path} has version {payload.get('version')!r}, "
            f"expected {CACHE_VERSION}"
        )
    return payload


class CachedTracker:
    """A :class:`~src.tracking.Tracker` that replays a recorded cache frame by frame.

    Tracks keep their identity and trajectory across frames exactly as a live tracker
    would: a Track_ID absent for more than ``track_buffer`` frames is dropped, so the
    overlay draws the same trajectories it would have drawn live. ``frame`` and
    ``detections`` are ignored; only the call order matters.
    """

    def __init__(self, path: str, config: Config) -> None:
        payload = load_cache(path)
        recorded = payload.get("tracker", {})
        expected = tracker_fingerprint(config)
        mismatched = {
            key: (recorded.get(key), value)
            for key, value in expected.items()
            if recorded.get(key) != value
        }
        if mismatched:
            details = ", ".join(
                f"{key}: cache {old!r} vs config {new!r}"
                for key, (old, new) in mismatched.items()
            )
            raise ConfigError(
                f"track cache {path} was recorded with different tracker settings "
                f"({details}); re-record it with `python -m src.main cache-tracks`"
            )
        self._path = path
        self._frames: list[list[list[Any]]] = payload["frames"]
        self._trajectory_length = int(config.trajectory_length)
        self._track_buffer = int(config.track_buffer)
        self._tracks: dict[int, Track] = {}
        self._missing: dict[int, int] = {}
        self._index = 0

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def video_path(self) -> str:
        return str(load_cache(self._path).get("video_path", ""))

    def update(self, frame: np.ndarray, detections: list) -> list[Track]:
        if self._index >= len(self._frames):
            raise ConfigError(
                f"track cache {self._path} holds {len(self._frames)} frames but the "
                f"video yielded more; the cache does not belong to this video"
            )
        rows = self._frames[self._index]
        self._index += 1

        seen: set[int] = set()
        out: list[Track] = []
        for track_id, x1, y1, x2, y2, vehicle_class in rows:
            track = self._tracks.get(track_id)
            if track is None:
                track = Track(
                    track_id=int(track_id),
                    x1=int(x1), y1=int(y1), x2=int(x2), y2=int(y2),
                    vehicle_class=str(vehicle_class),
                    trajectory=new_trajectory(self._trajectory_length),
                )
                self._tracks[track_id] = track
            else:
                track.vehicle_class = str(vehicle_class)
                track.set_box(int(x1), int(y1), int(x2), int(y2))
            track.record_position()
            seen.add(track_id)
            out.append(track)

        for track_id in list(self._tracks):
            if track_id in seen:
                self._missing.pop(track_id, None)
                continue
            self._missing[track_id] = self._missing.get(track_id, 0) + 1
            if self._missing[track_id] > self._track_buffer:
                del self._tracks[track_id]
                del self._missing[track_id]
        return out

    def close(self) -> None:
        """Nothing to release; present for the Tracker lifecycle the pipeline uses."""


__all__ = [
    "CACHE_VERSION",
    "DEFAULT_CACHE_DIR",
    "CachedTracker",
    "RecordingTracker",
    "default_cache_path",
    "load_cache",
    "tracker_fingerprint",
]
