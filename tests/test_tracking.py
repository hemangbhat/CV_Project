"""Unit tests for the Track data model and the deterministic Tracker fake.

Covers Requirement 3.1: a Tracker turns per-frame detections into tracks, each
carrying a Track_ID, a bounding box, and a Vehicle_Class. The bounded trajectory
contract of Requirement 3.5 is tested here at the data-model level; the concrete
ByteTrack wrapper and its id-persistence and retirement behaviour
(Requirements 3.2-3.5) are tested with task 5.2.
"""

from collections import deque

import numpy as np
import pytest

from src.detection import Detection
from src.tracking import Track, Tracker, new_trajectory
from tests.fixtures import (
    DEFAULT_CLIP_FRAME_COUNT,
    FakeTracker,
    scripted_detections,
    synthetic_frame,
)


def _detection(x1: int = 10, y1: int = 20, x2: int = 30, y2: int = 40) -> Detection:
    return Detection(x1=x1, y1=y1, x2=x2, y2=y2, vehicle_class="car", confidence=0.9)


def _track(track_id: int = 1, trajectory_length: int = 5, **box: int) -> Track:
    return Track.from_detection(track_id, _detection(**box), trajectory_length)


# -- new_trajectory ---------------------------------------------------------


def test_new_trajectory_is_bounded_by_trajectory_length() -> None:
    trajectory = new_trajectory(3)

    assert trajectory.maxlen == 3
    assert list(trajectory) == []


def test_new_trajectory_keeps_only_the_most_recent_prefilled_points() -> None:
    trajectory = new_trajectory(2, [(1, 1), (2, 2), (3, 3)])

    assert list(trajectory) == [(2, 2), (3, 3)]


@pytest.mark.parametrize("length", [0, -1])
def test_new_trajectory_rejects_non_positive_length(length: int) -> None:
    with pytest.raises(ValueError, match="trajectory_length"):
        new_trajectory(length)


def test_new_trajectory_rejects_non_integer_length() -> None:
    with pytest.raises(ValueError, match="must be an int"):
        new_trajectory(2.5)                      # type: ignore[arg-type]


# -- Track data model -------------------------------------------------------


def test_track_carries_id_box_and_vehicle_class() -> None:
    track = _track(track_id=7)

    assert track.track_id == 7
    assert track.box == (10, 20, 30, 40)
    assert track.vehicle_class == "car"
    assert (track.width, track.height) == (20, 20)
    assert track.label() == "#7 car"


def test_track_reference_point_is_the_bottom_edge_midpoint() -> None:
    track = _track(x1=10, y1=20, x2=31, y2=40)

    assert track.ref_point == (20, 40)


def test_track_from_detection_records_the_opening_reference_point() -> None:
    track = _track(trajectory_length=4)

    assert list(track.trajectory) == [track.ref_point]
    assert track.trajectory_length == 4


def test_track_trajectory_is_truncated_at_trajectory_length() -> None:
    track = _track(trajectory_length=2)

    track.set_box(10, 20, 30, 41)
    track.record_position()
    track.set_box(10, 20, 30, 42)
    track.record_position()

    assert list(track.trajectory) == [(20, 41), (20, 42)]


def test_track_update_from_detection_moves_the_box_and_extends_the_trajectory() -> None:
    track = _track(trajectory_length=5)
    first = track.ref_point

    track.update_from_detection(
        Detection(x1=12, y1=22, x2=32, y2=44, vehicle_class="bus", confidence=0.5)
    )

    assert track.box == (12, 22, 32, 44)
    assert track.vehicle_class == "bus"
    assert list(track.trajectory) == [first, (22, 44)]


def test_track_rejects_an_unbounded_trajectory() -> None:
    with pytest.raises(ValueError, match="maxlen"):
        Track(1, 0, 0, 4, 4, "car", deque())


@pytest.mark.parametrize(
    "box",
    [
        (5, 0, 5, 4),      # zero width
        (0, 5, 4, 5),      # zero height
        (6, 0, 2, 4),      # inverted in x
    ],
)
def test_track_rejects_non_positive_extents(box: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="positive width and height"):
        Track(1, *box, vehicle_class="car", trajectory=new_trajectory(2))


def test_track_rejects_a_negative_id() -> None:
    with pytest.raises(ValueError, match="track_id"):
        Track(-1, 0, 0, 4, 4, "car", new_trajectory(2))


def test_track_rejects_an_unknown_vehicle_class() -> None:
    with pytest.raises(ValueError, match="vehicle_class"):
        Track(1, 0, 0, 4, 4, "bicycle", new_trajectory(2))


def test_track_set_box_revalidates() -> None:
    track = _track()

    with pytest.raises(ValueError, match="positive width and height"):
        track.set_box(30, 20, 10, 40)


# -- FakeTracker ------------------------------------------------------------


def test_fake_tracker_satisfies_the_tracker_protocol() -> None:
    assert isinstance(FakeTracker(), Tracker)


def test_fake_tracker_assigns_distinct_sequential_ids_within_a_frame() -> None:
    tracker = FakeTracker()
    detections = [
        Detection(0, 0, 4, 4, "car", 0.9),
        Detection(50, 50, 54, 54, "car", 0.8),
    ]

    tracks = tracker.update(synthetic_frame(0), detections)

    assert [track.track_id for track in tracks] == [1, 2]
    assert len({track.track_id for track in tracks}) == len(tracks)


def test_fake_tracker_holds_ids_while_a_vehicle_keeps_moving() -> None:
    tracker = FakeTracker()
    frame = synthetic_frame(0)
    script = scripted_detections(DEFAULT_CLIP_FRAME_COUNT)

    first = tracker.update(frame, script[0])
    ids_per_frame = [[track.track_id for track in first]]
    for frame_detections in script[1:4]:
        tracks = tracker.update(frame, frame_detections)
        ids_per_frame.append([track.track_id for track in tracks])

    assert all(ids == ids_per_frame[0] for ids in ids_per_frame)


def test_fake_tracker_extends_the_trajectory_once_per_reported_frame() -> None:
    tracker = FakeTracker(trajectory_length=10)
    frame = synthetic_frame(0)
    script = scripted_detections(4)

    for frame_detections in script:
        tracks = tracker.update(frame, frame_detections)

    assert [len(track.trajectory) for track in tracks] == [4] * len(tracks)


def test_fake_tracker_bounds_the_trajectory_at_trajectory_length() -> None:
    tracker = FakeTracker(trajectory_length=2)
    frame = synthetic_frame(0)

    for frame_detections in scripted_detections(5):
        tracks = tracker.update(frame, frame_detections)

    assert all(len(track.trajectory) == 2 for track in tracks)


def test_fake_tracker_retires_an_id_after_track_buffer_absent_frames() -> None:
    tracker = FakeTracker(track_buffer=2)
    frame = synthetic_frame(0)
    detection = Detection(0, 0, 4, 4, "car", 0.9)

    tracker.update(frame, [detection])
    assert tracker.active_ids == (1,)

    tracker.update(frame, [])
    tracker.update(frame, [])
    assert tracker.active_ids == (1,)          # still within the buffer

    tracker.update(frame, [])
    assert tracker.active_ids == ()
    assert tracker.retired_ids == {1}


def test_fake_tracker_never_reissues_a_retired_id() -> None:
    tracker = FakeTracker(track_buffer=1)
    frame = synthetic_frame(0)
    detection = Detection(0, 0, 4, 4, "car", 0.9)

    tracker.update(frame, [detection])
    for _ in range(3):
        tracker.update(frame, [])
    reappeared = tracker.update(frame, [detection])

    assert tracker.retired_ids == {1}
    assert [track.track_id for track in reappeared] == [2]


def test_fake_tracker_separates_vehicles_that_are_far_apart() -> None:
    tracker = FakeTracker(max_match_distance=5.0)
    frame = synthetic_frame(0)

    first = tracker.update(frame, [Detection(0, 0, 4, 4, "car", 0.9)])
    second = tracker.update(frame, [Detection(40, 40, 44, 44, "car", 0.9)])

    assert first[0].track_id != second[0].track_id


def test_fake_tracker_does_not_associate_across_vehicle_classes() -> None:
    tracker = FakeTracker()
    frame = synthetic_frame(0)

    first = tracker.update(frame, [Detection(0, 0, 4, 4, "car", 0.9)])
    second = tracker.update(frame, [Detection(0, 0, 4, 4, "truck", 0.9)])

    assert first[0].track_id != second[0].track_id


def test_fake_tracker_reset_replays_identical_ids() -> None:
    tracker = FakeTracker()
    frame = synthetic_frame(0)
    script = scripted_detections(4)

    first_run = [
        [track.track_id for track in tracker.update(frame, d)] for d in script
    ]
    tracker.reset()
    second_run = [
        [track.track_id for track in tracker.update(frame, d)] for d in script
    ]

    assert first_run == second_run
    assert tracker.retired_ids == set()


def test_fake_tracker_returns_no_tracks_for_an_empty_detection_list() -> None:
    assert FakeTracker().update(synthetic_frame(0), []) == []


def test_fake_tracker_does_not_mutate_the_frame() -> None:
    tracker = FakeTracker()
    frame = synthetic_frame(0)
    before = frame.copy()

    tracker.update(frame, scripted_detections(1)[0])

    assert np.array_equal(frame, before)


def test_fake_tracker_reads_bounds_from_config() -> None:
    from src.config import load_config

    config = load_config("config/default.json")
    tracker = FakeTracker(config)

    assert tracker.trajectory_length == config.trajectory_length
    assert tracker.track_buffer == config.track_buffer
