"""Unit tests for :class:`~src.tracking.ByteTrackTracker` (Requirements 3.1-3.5).

The Ultralytics model is stubbed, so these tests run with no weights, no GPU, and
no network: what is under test is the wrapper's own bookkeeping — id persistence
across consecutive frames (Requirement 3.3), retirement after ``track_buffer``
absent frames (Requirement 3.4), trajectory truncation at ``trajectory_length``
(Requirement 3.5), and the distinctness and no-reissue checks (Requirements 3.2,
3.4) — not ByteTrack's association mathematics.

The stub mimics the Ultralytics tracking return shape: ``track`` yields a list of
result objects, each carrying ``boxes.xyxy``, ``boxes.cls``, and ``boxes.id``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pytest

from src.config import Config, load_config
from src.errors import ModelError
from src.tracking import (
    REMAPPED_ID_BASE,
    TRACKER_CONFIG_NAME,
    ByteTrackTracker,
    Track,
    Tracker,
    render_tracker_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

FRAME_WIDTH = 60
FRAME_HEIGHT = 40

#: ``(track_id, x1, y1, x2, y2, coco_class_id)`` per stubbed tracker row. A
#: ``track_id`` of ``None`` is a detection ByteTrack has not confirmed.
Row = tuple[Any, float, float, float, float, float]

CAR = 2.0
BUS = 5.0
PERSON = 0.0


class _StubBoxes:
    def __init__(self, rows: Sequence[Row]) -> None:
        self.xyxy = np.array([row[1:5] for row in rows], dtype=np.float32).reshape(-1, 4)
        self.cls = np.array([row[5] for row in rows], dtype=np.float32)
        self.id = None if not rows else [row[0] for row in rows]


class _StubResult:
    def __init__(self, rows: Sequence[Row]) -> None:
        self.boxes = _StubBoxes(rows)


class _StubModel:
    """Stands in for ``ultralytics.YOLO``, replaying one row list per frame.

    ``train`` fails loudly: no tracker or detector of this project may train
    (Requirement 2.4), and a silent stub would let a regression through.
    """

    def __init__(self, script: Sequence[Sequence[Row]]) -> None:
        self.script = [list(rows) for rows in script]
        self.calls: list[dict[str, Any]] = []

    def track(self, **kwargs: Any) -> list[_StubResult]:
        index = len(self.calls)
        self.calls.append(kwargs)
        rows = self.script[index] if index < len(self.script) else []
        return [_StubResult(rows)]

    def train(self, *args: Any, **kwargs: Any) -> None:      # pragma: no cover
        raise AssertionError("the Tracker must never train (Requirement 2.4)")


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _frame() -> np.ndarray:
    return np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)


def _tracker(
    config: Config,
    script: Sequence[Sequence[Row]],
    tmp_path: Path,
    *,
    track_buffer: int | None = None,
    trajectory_length: int | None = None,
) -> tuple[ByteTrackTracker, _StubModel]:
    if track_buffer is not None:
        config = dataclasses.replace(config, track_buffer=track_buffer)
    if trajectory_length is not None:
        config = dataclasses.replace(config, trajectory_length=trajectory_length)
    model = _StubModel(script)
    tracker = ByteTrackTracker(
        config, model=model, tracker_config_dir=str(tmp_path / "tracker_cfg")
    )
    return tracker, model


def _run(tracker: ByteTrackTracker, frames: int) -> list[list[Track]]:
    return [tracker.update(_frame(), []) for _ in range(frames)]


# ---------------------------------------------------------------------------
# Contract, weights, and the generated tracker config
# ---------------------------------------------------------------------------


def test_bytetrack_tracker_satisfies_the_tracker_protocol(
    config: Config, tmp_path: Path
) -> None:
    tracker, _ = _tracker(config, [], tmp_path)

    assert isinstance(tracker, Tracker)


def test_unloadable_model_path_raises_model_error_naming_the_path(
    config: Config, tmp_path: Path
) -> None:
    missing = tmp_path / "not_a_model.xyz"
    broken = dataclasses.replace(config, model_path=str(missing))

    with pytest.raises(ModelError) as excinfo:
        ByteTrackTracker(broken, tracker_config_dir=str(tmp_path / "cfg"))

    assert str(missing) in str(excinfo.value)


def test_generated_tracker_config_carries_the_configured_track_buffer(
    config: Config,
) -> None:
    text = render_tracker_config(dataclasses.replace(config, track_buffer=17))

    assert "tracker_type: bytetrack" in text
    assert "track_buffer: 17" in text


def test_tracker_config_is_written_and_passed_to_the_backend(
    config: Config, tmp_path: Path
) -> None:
    tracker, model = _tracker(config, [[(1, 0.0, 0.0, 10.0, 10.0, CAR)]], tmp_path)

    tracker.update(_frame(), [])

    written = Path(tracker.tracker_config_path)
    assert written.name == TRACKER_CONFIG_NAME
    assert f"track_buffer: {config.track_buffer}" in written.read_text(encoding="utf-8")
    assert model.calls[0]["tracker"] == str(written)
    assert model.calls[0]["persist"] is True


def test_update_does_not_mutate_the_frame(config: Config, tmp_path: Path) -> None:
    tracker, _ = _tracker(config, [[(1, 2.0, 3.0, 12.0, 13.0, CAR)]], tmp_path)
    frame = _frame()
    before = frame.copy()

    tracker.update(frame, [])

    assert np.array_equal(frame, before)


def test_close_removes_an_owned_tracker_config_directory(config: Config) -> None:
    tracker = ByteTrackTracker(config, model=_StubModel([]))
    directory = Path(tracker.tracker_config_path).parent

    assert directory.is_dir()
    tracker.close()

    assert not directory.exists()


# ---------------------------------------------------------------------------
# Identity persistence across consecutive frames (Requirements 3.1, 3.3)
# ---------------------------------------------------------------------------


def test_same_vehicle_keeps_its_track_id_across_consecutive_frames(
    config: Config, tmp_path: Path
) -> None:
    script = [
        [(7, 0.0, 0.0, 10.0, 10.0, CAR)],
        [(7, 3.0, 0.0, 13.0, 10.0, CAR)],
        [(7, 6.0, 0.0, 16.0, 10.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path)

    frames = _run(tracker, 3)

    assert [[track.track_id for track in tracks] for tracks in frames] == [[7], [7], [7]]
    assert tracker.active_ids == (7,)
    assert tracker.retired_ids == frozenset()


def test_a_persistent_track_is_one_object_carrying_its_own_history(
    config: Config, tmp_path: Path
) -> None:
    script = [
        [(7, 0.0, 0.0, 10.0, 10.0, CAR)],
        [(7, 4.0, 0.0, 14.0, 10.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path)

    first, second = _run(tracker, 2)

    assert first[0] is second[0]
    assert second[0].box == (4, 0, 14, 10)
    assert list(second[0].trajectory) == [(5, 10), (9, 10)]


def test_tracks_carry_a_track_id_a_box_and_a_vehicle_class(
    config: Config, tmp_path: Path
) -> None:
    """Requirement 3.1."""
    tracker, _ = _tracker(
        config, [[(3, 1.0, 2.0, 11.0, 12.0, BUS)]], tmp_path
    )

    (track,) = tracker.update(_frame(), [])

    assert track.track_id == 3
    assert track.box == (1, 2, 11, 12)
    assert track.vehicle_class == "bus"


def test_distinct_vehicles_receive_distinct_ids_within_a_frame(
    config: Config, tmp_path: Path
) -> None:
    """Requirement 3.2."""
    rows: list[Row] = [
        (1, 0.0, 0.0, 10.0, 10.0, CAR),
        (2, 20.0, 0.0, 30.0, 10.0, CAR),
        (3, 40.0, 20.0, 55.0, 35.0, BUS),
    ]
    tracker, _ = _tracker(config, [rows], tmp_path)

    tracks = tracker.update(_frame(), [])

    ids = [track.track_id for track in tracks]
    assert ids == [1, 2, 3]
    assert len(set(ids)) == len(ids)


def test_duplicate_id_within_one_frame_is_rejected(config: Config, tmp_path: Path) -> None:
    """Requirement 3.2 is checked, not assumed."""
    rows: list[Row] = [
        (4, 0.0, 0.0, 10.0, 10.0, CAR),
        (4, 20.0, 0.0, 30.0, 10.0, CAR),
    ]
    tracker, _ = _tracker(config, [rows], tmp_path)

    with pytest.raises(AssertionError, match="twice in one frame"):
        tracker.update(_frame(), [])


def test_rows_without_an_id_are_not_tracks(config: Config, tmp_path: Path) -> None:
    rows: list[Row] = [
        (None, 0.0, 0.0, 10.0, 10.0, CAR),
        (9, 20.0, 0.0, 30.0, 10.0, CAR),
    ]
    tracker, _ = _tracker(config, [rows], tmp_path)

    assert [track.track_id for track in tracker.update(_frame(), [])] == [9]


def test_non_vehicle_classes_never_occupy_a_track_id(
    config: Config, tmp_path: Path
) -> None:
    rows: list[Row] = [
        (1, 0.0, 0.0, 10.0, 10.0, PERSON),
        (2, 20.0, 0.0, 30.0, 10.0, CAR),
    ]
    tracker, _ = _tracker(config, [rows], tmp_path)

    tracks = tracker.update(_frame(), [])

    assert [(t.track_id, t.vehicle_class) for t in tracks] == [(2, "car")]


def test_boxes_are_clipped_to_the_frame_bounds(config: Config, tmp_path: Path) -> None:
    tracker, _ = _tracker(
        config,
        [[(1, -6.0, -4.0, FRAME_WIDTH + 9.0, FRAME_HEIGHT + 5.0, CAR)]],
        tmp_path,
    )

    (track,) = tracker.update(_frame(), [])

    assert track.box == (0, 0, FRAME_WIDTH, FRAME_HEIGHT)


# ---------------------------------------------------------------------------
# Retirement after track_buffer absent frames (Requirement 3.4)
# ---------------------------------------------------------------------------


def test_track_survives_absence_up_to_the_track_buffer(
    config: Config, tmp_path: Path
) -> None:
    script: list[list[Row]] = [
        [(5, 0.0, 0.0, 10.0, 10.0, CAR)],
        [],
        [],
        [(5, 4.0, 0.0, 14.0, 10.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=2)

    frames = _run(tracker, 4)

    assert tracker.retired_ids == frozenset()
    assert [track.track_id for track in frames[3]] == [5]
    assert len(frames[3][0].trajectory) == 2


def test_track_is_retired_after_more_than_track_buffer_absent_frames(
    config: Config, tmp_path: Path
) -> None:
    script: list[list[Row]] = [
        [(5, 0.0, 0.0, 10.0, 10.0, CAR)],
        [],
        [],
        [],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=2)

    _run(tracker, 3)
    assert tracker.active_ids == (5,)                # 2 absent frames: still live

    _run(tracker, 1)

    assert tracker.active_ids == ()                  # 3 absent frames: retired
    assert tracker.retired_ids == frozenset({5})


def test_retirement_deletes_the_trajectory_so_memory_tracks_live_vehicles(
    config: Config, tmp_path: Path
) -> None:
    """Requirement 3.5: history is bounded by the number of live tracks."""
    script: list[list[Row]] = [
        [(1, 0.0, 0.0, 10.0, 10.0, CAR), (2, 20.0, 0.0, 30.0, 10.0, CAR)],
        [(2, 22.0, 0.0, 32.0, 10.0, CAR)],
        [(2, 24.0, 0.0, 34.0, 10.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=1)

    _run(tracker, 3)

    assert set(tracker.trajectories) == {2}
    assert tracker.retired_ids == frozenset({1})


def test_a_retired_track_id_is_never_reissued(config: Config, tmp_path: Path) -> None:
    """Requirement 3.4: a retired id never labels a later track.

    ByteTrack accounts its lost-track buffer independently of this wrapper's
    absence counter, so it can re-associate an id the wrapper already retired.
    The requirement is that the retired id stays unassigned, so the reappearance
    is relabelled with a fresh id — the run continues rather than aborting, which
    is also what ``FakeTracker`` does.
    """
    script: list[list[Row]] = [
        [(5, 0.0, 0.0, 10.0, 10.0, CAR)],
        [],
        [],
        [(5, 30.0, 20.0, 40.0, 30.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=1)

    _run(tracker, 3)
    assert tracker.retired_ids == frozenset({5})

    reappeared = tracker.update(_frame(), [])

    assert [track.track_id for track in reappeared] == [REMAPPED_ID_BASE]
    assert 5 not in {track.track_id for track in reappeared}
    assert tracker.retired_ids == frozenset({5})


def test_a_relabelled_track_keeps_its_new_id_across_frames(
    config: Config, tmp_path: Path
) -> None:
    """A relabelled reappearance is tracked under the new id, not re-relabelled."""
    script: list[list[Row]] = [
        [(5, 0.0, 0.0, 10.0, 10.0, CAR)],
        [],
        [],
        [(5, 30.0, 20.0, 40.0, 30.0, CAR)],
        [(5, 32.0, 22.0, 42.0, 32.0, CAR)],
        [(5, 34.0, 24.0, 44.0, 34.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=1)

    frames = _run(tracker, 6)

    assert [track.track_id for track in frames[3]] == [REMAPPED_ID_BASE]
    assert [track.track_id for track in frames[4]] == [REMAPPED_ID_BASE]
    assert [track.track_id for track in frames[5]] == [REMAPPED_ID_BASE]
    assert len(frames[5][0].trajectory) == 3


def test_reset_clears_tracks_trajectories_and_retired_ids(
    config: Config, tmp_path: Path
) -> None:
    script: list[list[Row]] = [
        [(1, 0.0, 0.0, 10.0, 10.0, CAR)],
        [],
        [],
        [(2, 20.0, 0.0, 30.0, 10.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, track_buffer=1)

    _run(tracker, 3)
    tracker.reset()

    assert tracker.active_ids == ()
    assert tracker.retired_ids == frozenset()
    assert tracker.trajectories == {}
    assert tracker.frames_seen == 0


# ---------------------------------------------------------------------------
# Trajectory maintenance (Requirement 3.5)
# ---------------------------------------------------------------------------


def test_trajectory_records_one_reference_point_per_reported_frame(
    config: Config, tmp_path: Path
) -> None:
    script = [
        [(1, 0.0, 0.0, 10.0, 20.0, CAR)],
        [(1, 2.0, 0.0, 12.0, 20.0, CAR)],
        [(1, 4.0, 0.0, 14.0, 20.0, CAR)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, trajectory_length=10)

    frames = _run(tracker, 3)

    assert list(frames[2][0].trajectory) == [(5, 20), (7, 20), (9, 20)]
    assert list(tracker.trajectories[1]) == [(5, 20), (7, 20), (9, 20)]


def test_trajectory_is_truncated_at_trajectory_length(
    config: Config, tmp_path: Path
) -> None:
    frames_count = 8
    script = [
        [(1, float(2 * i), 0.0, float(2 * i + 10), 20.0, CAR)]
        for i in range(frames_count)
    ]
    tracker, _ = _tracker(config, script, tmp_path, trajectory_length=3)

    frames = _run(tracker, frames_count)
    trajectory = frames[-1][0].trajectory

    assert trajectory.maxlen == 3
    assert len(trajectory) == 3
    # Only the three most recent reference points survive, oldest first.
    assert list(trajectory) == [(15, 20), (17, 20), (19, 20)]


def test_each_track_owns_its_own_bounded_trajectory(
    config: Config, tmp_path: Path
) -> None:
    script: list[list[Row]] = [
        [(1, 0.0, 0.0, 10.0, 20.0, CAR), (2, 30.0, 0.0, 40.0, 30.0, BUS)],
        [(1, 2.0, 0.0, 12.0, 20.0, CAR), (2, 32.0, 0.0, 42.0, 30.0, BUS)],
    ]
    tracker, _ = _tracker(config, script, tmp_path, trajectory_length=4)

    _run(tracker, 2)

    assert list(tracker.trajectories[1]) == [(5, 20), (7, 20)]
    assert list(tracker.trajectories[2]) == [(35, 30), (37, 30)]
