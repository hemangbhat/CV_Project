"""Unit tests for the Approach_Assigner.

One test per branch of the assignment algorithm, plus the two geometry edge cases
that are easy to get wrong and expensive to notice later: a reference point lying
exactly on a polygon edge, and a reference point lying in a zone two ROI polygons
share.

Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, ApproachConfig, Config, load_config
from src.errors import ConfigError
from src.lane_analysis import (
    ApproachAssigner,
    AssignedTrack,
    OverlapEvent,
    point_in_polygon,
    polygon_centroid,
    reference_point,
)
from src.tracking import Track, new_trajectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _track(
    ref: tuple[int, int], track_id: int = 1, vehicle_class: str = "car"
) -> Track:
    """Return a track whose bottom-edge midpoint is exactly ``ref``.

    The box is built backwards from the reference point so each test names the
    geometry it cares about — the point tested against the polygons — rather than
    a box whose midpoint the reader has to compute.
    """
    cx, cy = ref
    track = Track(
        track_id=track_id,
        x1=cx - 2,
        y1=cy - 10,
        x2=cx + 2,
        y2=cy,
        vehicle_class=vehicle_class,
        trajectory=new_trajectory(4),
    )
    assert track.ref_point == ref
    return track


def _approach(
    name: str,
    roi: tuple[tuple[int, int], ...],
    queue: tuple[tuple[int, int], ...],
) -> ApproachConfig:
    return ApproachConfig(
        name=name,
        roi_polygon=roi,
        queue_region=queue,
        saturation_count=12.0,
        queue_capacity=8.0,
    )


def _overlapping_config(config: Config) -> Config:
    """Return ``config`` with North and East ROIs sharing the band x in 100..200.

    North's centroid sits at (100, 100) and East's at (200, 100), so a point in
    the shared band resolves by which centroid it is nearer to (Requirement 4.4).
    South and West are moved well clear so they never enter a candidate list.
    """
    return dataclasses.replace(
        config,
        approaches=(
            _approach(
                "North",
                ((0, 0), (200, 0), (200, 200), (0, 200)),
                ((10, 150), (190, 150), (190, 190), (10, 190)),
            ),
            _approach(
                "East",
                ((100, 0), (300, 0), (300, 200), (100, 200)),
                ((210, 10), (290, 10), (290, 190), (210, 190)),
            ),
            _approach(
                "South",
                ((0, 400), (200, 400), (200, 600), (0, 600)),
                ((10, 410), (190, 410), (190, 450), (10, 450)),
            ),
            _approach(
                "West",
                ((400, 400), (600, 400), (600, 600), (400, 600)),
                ((410, 410), (590, 410), (590, 450), (410, 450)),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Requirement 4.2 — the reference point
# ---------------------------------------------------------------------------


def test_reference_point_is_the_bottom_edge_midpoint() -> None:
    """Validates: Requirements 4.2"""
    track = _track((440, 150))
    track.set_box(10, 20, 31, 40)

    assert reference_point(track) == (20, 40)


def test_reference_point_delegates_to_the_track_property() -> None:
    """The assigner and the trajectory must test the same point (Req 4.2)."""
    track = _track((300, 120))

    assert reference_point(track) == track.ref_point


# ---------------------------------------------------------------------------
# Requirement 4.3 — exactly one match
# ---------------------------------------------------------------------------


def test_single_roi_match_assigns_that_approach(config: Config) -> None:
    """Validates: Requirements 4.3"""
    assigner = ApproachAssigner(config)
    track = _track((640, 100))                       # inside North only

    assigned, overlaps = assigner.assign([track])

    assert overlaps == []
    assert len(assigned) == 1
    assert assigned[0].approach == "North"
    assert assigned[0].track_id == track.track_id
    assert assigned[0].vehicle_class == "car"
    assert assigned[0].ref_point == (640, 100)
    assert assigned[0].is_assigned is True


def test_point_on_the_roi_edge_counts_as_inside(config: Config) -> None:
    """A vehicle stopped on the ROI boundary is assigned, not dropped (Req 4.3)."""
    assigner = ApproachAssigner(config)
    track = _track((440, 150))                       # x = 440 is North's left edge

    assigned, overlaps = assigner.assign([track])

    assert overlaps == []
    assert assigned[0].approach == "North"


def test_every_approach_can_be_assigned(config: Config) -> None:
    """Validates: Requirements 4.3"""
    assigner = ApproachAssigner(config)
    points = {"North": (640, 100), "East": (1000, 300), "South": (640, 600), "West": (200, 300)}

    tracks = [
        _track(point, track_id=index + 1)
        for index, point in enumerate(points[name] for name in APPROACH_NAMES)
    ]
    assigned, overlaps = assigner.assign(tracks)

    assert overlaps == []
    assert [entry.approach for entry in assigned] == list(APPROACH_NAMES)


# ---------------------------------------------------------------------------
# Requirement 4.5 — zero matches
# ---------------------------------------------------------------------------


def test_point_outside_every_roi_is_unassigned(config: Config) -> None:
    """Validates: Requirements 4.5"""
    assigner = ApproachAssigner(config)
    track = _track((100, 100))                       # gap above the West ROI

    assigned, overlaps = assigner.assign([track])

    assert overlaps == []
    assert assigned[0].approach is None
    assert assigned[0].is_queueing is False
    assert assigned[0].is_assigned is False


def test_unassigned_tracks_are_still_reported_in_input_order(config: Config) -> None:
    """Excluded from measurement, not from the result list (Requirement 4.5)."""
    assigner = ApproachAssigner(config)
    tracks = [
        _track((100, 100), track_id=7),              # outside every ROI
        _track((640, 100), track_id=8),              # North
    ]

    assigned, _ = assigner.assign(tracks)

    assert [entry.track_id for entry in assigned] == [7, 8]
    assert [entry.approach for entry in assigned] == [None, "North"]


def test_assigned_track_rejects_queueing_without_an_approach() -> None:
    """``is_queueing`` implies assignment (Requirements 4.5, 4.6)."""
    with pytest.raises(ValueError, match="queueing"):
        AssignedTrack(
            track_id=1,
            vehicle_class="car",
            ref_point=(1, 1),
            approach=None,
            is_queueing=True,
        )


# ---------------------------------------------------------------------------
# Requirement 4.4 — two or more matches
# ---------------------------------------------------------------------------


def test_overlap_assigns_the_nearest_centroid_and_logs_the_event(config: Config) -> None:
    """Validates: Requirements 4.4"""
    assigner = ApproachAssigner(_overlapping_config(config))
    track = _track((120, 100), track_id=5)           # in both, nearer North

    assigned, overlaps = assigner.assign([track], frame_index=42)

    assert assigned[0].approach == "North"
    assert overlaps == [
        OverlapEvent(track_id=5, frame_index=42, candidates=("North", "East"), chosen="North")
    ]


def test_overlap_can_resolve_to_the_later_approach(config: Config) -> None:
    """The tiebreak is distance, not Approach order (Requirement 4.4)."""
    assigner = ApproachAssigner(_overlapping_config(config))
    track = _track((190, 100))                       # in both, nearer East

    assigned, overlaps = assigner.assign([track])

    assert assigned[0].approach == "East"
    assert overlaps[0].chosen == "East"
    assert overlaps[0].candidates == ("North", "East")


def test_equidistant_overlap_resolves_to_the_earlier_approach(config: Config) -> None:
    """An exact distance tie resolves deterministically (Requirement 4.4)."""
    assigner = ApproachAssigner(_overlapping_config(config))
    track = _track((150, 100))                       # 50 px from either centroid

    assigned, overlaps = assigner.assign([track])

    assert assigned[0].approach == "North"
    assert overlaps[0].chosen == "North"


def test_overlap_event_serializes_for_the_run_log() -> None:
    """The Run_Log stores overlap events as plain JSON objects (Requirement 4.4)."""
    event = OverlapEvent(
        track_id=3, frame_index=9, candidates=("North", "East"), chosen="East"
    )

    assert event.as_json_obj() == {
        "track_id": 3,
        "frame_index": 9,
        "candidates": ["North", "East"],
        "chosen": "East",
    }


def test_overlap_event_rejects_a_single_candidate() -> None:
    with pytest.raises(ValueError, match="two or more"):
        OverlapEvent(track_id=1, frame_index=0, candidates=("North",), chosen="North")


def test_overlap_event_rejects_a_chosen_approach_outside_its_candidates() -> None:
    with pytest.raises(ValueError, match="candidates"):
        OverlapEvent(
            track_id=1, frame_index=0, candidates=("North", "East"), chosen="South"
        )


# ---------------------------------------------------------------------------
# Requirement 4.6 — queueing
# ---------------------------------------------------------------------------


def test_point_inside_the_queue_region_is_queueing(config: Config) -> None:
    """Validates: Requirements 4.6"""
    assigner = ApproachAssigner(config)
    track = _track((640, 250))                       # inside North's queue region

    assigned, _ = assigner.assign([track])

    assert assigned[0].approach == "North"
    assert assigned[0].is_queueing is True


def test_point_inside_the_roi_but_outside_the_queue_region_is_not_queueing(
    config: Config,
) -> None:
    """Validates: Requirements 4.6"""
    assigner = ApproachAssigner(config)
    track = _track((640, 100))                       # North ROI, above the queue

    assigned, _ = assigner.assign([track])

    assert assigned[0].approach == "North"
    assert assigned[0].is_queueing is False


def test_point_on_the_queue_region_edge_is_queueing(config: Config) -> None:
    """Boundary counts as inside for the queue region too (Requirement 4.6)."""
    assigner = ApproachAssigner(config)
    track = _track((640, 200))                       # North queue region's top edge

    assigned, _ = assigner.assign([track])

    assert assigned[0].is_queueing is True


def test_queueing_is_tested_against_the_assigned_approach_only(config: Config) -> None:
    """A neighbour's queue region must never mark a track queueing (Req 4.6)."""
    assigner = ApproachAssigner(_overlapping_config(config))
    # (120, 170) lies in East's ROI as well, and in North's queue region; it is
    # assigned to North, so it queues. East's queue region is far to the right.
    track = _track((120, 170))

    assigned, overlaps = assigner.assign([track])

    assert assigned[0].approach == "North"
    assert assigned[0].is_queueing is True
    assert overlaps[0].candidates == ("North", "East")


def test_track_in_the_overlap_band_outside_both_queue_regions_is_not_queueing(
    config: Config,
) -> None:
    """Validates: Requirements 4.6"""
    assigner = ApproachAssigner(_overlapping_config(config))
    track = _track((120, 100))

    assigned, _ = assigner.assign([track])

    assert assigned[0].approach == "North"
    assert assigned[0].is_queueing is False


# ---------------------------------------------------------------------------
# Precomputed geometry
# ---------------------------------------------------------------------------


def test_geometry_is_precomputed_once_not_per_frame(config: Config) -> None:
    """Polygon arrays and centroids are built in ``__init__`` (design decision)."""
    assigner = ApproachAssigner(config)
    track = _track((640, 250))

    before = assigner.roi_polygon("North")
    centroids = assigner.centroids
    assigner.assign([track])
    assigner.assign([track])

    assert assigner.roi_polygon("North") is before
    assert assigner.queue_polygon("North") is assigner.queue_polygon("North")
    assert assigner.centroids == centroids


def test_centroid_of_a_rectangle_is_its_middle(config: Config) -> None:
    assigner = ApproachAssigner(_overlapping_config(config))

    assert assigner.centroids["North"] == pytest.approx((100.0, 100.0))
    assert assigner.centroids["East"] == pytest.approx((200.0, 100.0))


def test_degenerate_polygon_centroid_falls_back_to_the_vertex_mean() -> None:
    """A zero-area polygon has no area centroid, so the mean stands in."""
    from src.lane_analysis import as_cv_polygon

    collinear = as_cv_polygon(((0, 0), (10, 0), (20, 0)))

    assert polygon_centroid(collinear) == pytest.approx((10.0, 0.0))


def test_approach_order_is_fixed(config: Config) -> None:
    shuffled = dataclasses.replace(config, approaches=tuple(reversed(config.approaches)))

    assert ApproachAssigner(shuffled).approach_names == APPROACH_NAMES


def test_missing_approach_is_reported(config: Config) -> None:
    incomplete = dataclasses.replace(config, approaches=config.approaches[:3])

    with pytest.raises(ConfigError, match="West"):
        ApproachAssigner(incomplete)


def test_point_in_polygon_treats_the_boundary_as_inside() -> None:
    from src.lane_analysis import as_cv_polygon

    square = as_cv_polygon(((0, 0), (10, 0), (10, 10), (0, 10)))

    assert point_in_polygon(square, (5, 5)) is True
    assert point_in_polygon(square, (0, 5)) is True
    assert point_in_polygon(square, (11, 5)) is False


def test_assign_returns_no_events_for_an_empty_frame(config: Config) -> None:
    assigned, overlaps = ApproachAssigner(config).assign([])

    assert assigned == []
    assert overlaps == []
