"""Tests for the robust queue measurement (audit fix W5).

Two changes, both opt-in through the configuration so every legacy run log keeps its
meaning:

* a windowed, perspective-normalised stopped test (``stopped_window_seconds``,
  ``stopped_speed_ratio``) in place of the per-frame 2-pixel test, and
* a contiguous queue tail (``queue_tail_gap``) in place of "furthest stopped vehicle".
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, load_config, to_json_obj
from src.errors import ConfigError
from src.lane_analysis import AssignedTrack, build_approach_axes
from src.traffic_metrics import MetricsEngine, queue_tail_reach

CALIBRATED = Path(__file__).resolve().parents[1] / "config" / "bellevue_116th_calibrated.json"
ALL_RED = {name: "RED" for name in APPROACH_NAMES}
DT = 1.0 / 30.0


@pytest.fixture
def base() -> Config:
    return load_config(str(CALIBRATED))


def robust(config: Config, **over) -> Config:
    values = dict(stopped_window_seconds=1.0, stopped_speed_ratio=0.2, queue_tail_gap=0.25)
    values.update(over)
    return dataclasses.replace(config, **values)


def north_point(config: Config, fraction: float) -> tuple[float, float]:
    """The image point lying ``fraction`` of the way up North's approach axis."""
    axis = build_approach_axes(config)["North"]
    ux, uy = axis.upstream
    return (axis.origin[0] + ux * axis.length * fraction, axis.origin[1] + uy * axis.length * fraction)


def track(track_id, point, height=40.0, queueing=False):
    return AssignedTrack(
        track_id=track_id, vehicle_class="car", ref_point=point,
        approach="North", is_queueing=queueing, box_height=height,
    )


# -- queue_tail_reach --------------------------------------------------------


def test_tail_is_zero_without_stopped_vehicles() -> None:
    assert queue_tail_reach([], 0.25) == 0.0


def test_tail_follows_a_contiguous_chain_from_the_stop_line() -> None:
    assert queue_tail_reach([0.05, 0.2, 0.4, 0.6], 0.25) == pytest.approx(0.6)


def test_isolated_far_vehicle_does_not_set_the_tail() -> None:
    # Chain 0.05 -> 0.2, then a gap of 0.7 to a lone stopped box.
    assert queue_tail_reach([0.05, 0.2, 0.9], 0.25) == pytest.approx(0.2)


def test_queue_not_starting_at_the_stop_line_has_no_tail() -> None:
    assert queue_tail_reach([0.5, 0.6], 0.25) == 0.0


def test_order_of_positions_does_not_matter() -> None:
    assert queue_tail_reach([0.4, 0.05, 0.2], 0.25) == queue_tail_reach([0.05, 0.2, 0.4], 0.25)


def test_zero_gap_is_the_legacy_furthest_vehicle() -> None:
    assert queue_tail_reach([0.05, 0.2, 0.9], 0.0) == pytest.approx(0.9)


# -- windowed stopped test ---------------------------------------------------


def _run(engine, frames):
    out = None
    for tracks in frames:
        out = engine.update(tracks, ALL_RED, DT)
    return out["North"]


def test_jittering_stationary_vehicle_is_stopped(base: Config) -> None:
    """+-3 px box jitter every frame: the legacy 2 px test flips, the window does not."""
    cfg = robust(base)
    p = north_point(cfg, 0.1)
    frames = [[track(1, (p[0] + (3 if i % 2 else -3), p[1]))] for i in range(45)]
    metrics = _run(MetricsEngine(cfg), frames)
    assert metrics.queue_reach == pytest.approx(0.1, abs=0.03)

    legacy = _run(MetricsEngine(base), frames)
    assert legacy.queue_reach == 0.0  # 6 px per frame reads as moving every frame


def test_distant_slow_mover_is_not_stopped(base: Config) -> None:
    """A small (distant) box moving 1.5 px/frame is 2.25 box-heights/s: moving.

    The legacy test (< 2 px between frames) calls it stopped, which is the
    perspective bias that inflated queue reach far upstream.
    """
    cfg = robust(base)
    start = north_point(cfg, 0.1)
    frames = [[track(1, (start[0] - 1.5 * i, start[1]), height=20.0)] for i in range(45)]
    assert _run(MetricsEngine(cfg), frames).queue_reach == 0.0
    assert _run(MetricsEngine(base), frames).queue_reach > 0.0


def test_new_track_needs_half_a_window_before_it_can_be_stopped(base: Config) -> None:
    cfg = robust(base)
    p = north_point(cfg, 0.1)
    engine = MetricsEngine(cfg)
    early = None
    for _ in range(10):  # 10 frames < 15 = half of the 30-frame window
        early = engine.update([track(1, p)], ALL_RED, DT)["North"]
    assert early.queue_reach == 0.0
    later = None
    for _ in range(10):
        later = engine.update([track(1, p)], ALL_RED, DT)["North"]
    assert later.queue_reach > 0.0


def test_reach_grows_with_a_physically_extending_queue(base: Config) -> None:
    """Vehicles join the back of a stationary queue: the tail moves upstream."""
    cfg = robust(base)
    engine = MetricsEngine(cfg)
    reaches = []
    queue = []
    for k, fraction in enumerate([0.05, 0.2, 0.35, 0.5]):
        queue.append(track(k + 1, north_point(cfg, fraction)))
        for _ in range(30):
            reaches.append(engine.update(list(queue), ALL_RED, DT)["North"].queue_reach)
    assert reaches[-1] == pytest.approx(0.5, abs=0.02)
    assert reaches[-1] > reaches[60] > reaches[29]


def test_robust_mode_is_off_by_default(base: Config) -> None:
    assert base.stopped_window_seconds == 0.0
    assert base.queue_tail_gap == 0.0


# -- configuration ------------------------------------------------------------


def test_new_fields_round_trip(base: Config, tmp_path: Path) -> None:
    cfg = robust(base, stopped_speed_ratio=0.3)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(to_json_obj(cfg)))
    loaded = load_config(str(path))
    assert loaded.stopped_window_seconds == 1.0
    assert loaded.stopped_speed_ratio == 0.3
    assert loaded.queue_tail_gap == 0.25


@pytest.mark.parametrize(
    "field,value",
    [("stopped_window_seconds", -1.0), ("stopped_speed_ratio", 0.0), ("queue_tail_gap", 1.5)],
)
def test_invalid_values_are_rejected(base: Config, tmp_path: Path, field, value) -> None:
    obj = to_json_obj(base)
    obj[field] = value
    path = tmp_path / "c.json"
    path.write_text(json.dumps(obj))
    with pytest.raises(ConfigError, match=field):
        load_config(str(path))


# -- drawn queue axis (W7) and perspective-scaled tail ------------------------

from src.lane_analysis import ApproachAxis, as_cv_polygon  # noqa: E402
from src.traffic_metrics import queue_tail_reach_scaled  # noqa: E402

SQUARE = as_cv_polygon([[0, 0], [400, 0], [400, 400], [0, 400]])


def test_drawn_axis_measures_from_the_stop_line() -> None:
    axis = ApproachAxis(SQUARE, SQUARE, polyline=[(100, 300), (100, 100)])
    assert axis.direction_source == "drawn" and axis.well_conditioned
    assert axis.fraction((100, 300)) == pytest.approx(0.0)
    assert axis.fraction((100, 200)) == pytest.approx(0.5)
    assert axis.fraction((130, 100)) == pytest.approx(1.0)     # beside the far end
    assert axis.fraction((100, 380)) == pytest.approx(0.0)     # downstream of the line


def test_drawn_axis_follows_a_bend_by_arc_length() -> None:
    # An L-shaped road: 100 px up, then 100 px right.
    axis = ApproachAxis(SQUARE, SQUARE, polyline=[(0, 200), (0, 100), (100, 100)])
    assert axis.length == pytest.approx(200.0)
    assert axis.fraction((0, 150)) == pytest.approx(0.25)
    assert axis.fraction((50, 100)) == pytest.approx(0.75)


def test_drawn_axis_overrides_measured_direction() -> None:
    axis = ApproachAxis(SQUARE, SQUARE, direction=(1.0, 0.0), polyline=[(0, 0), (0, 100)])
    assert axis.direction_source == "drawn"


def test_scaled_tail_bridges_large_near_vehicles() -> None:
    # Near the camera one car spans ~0.4 of the axis: a fixed 0.25 gap breaks the chain,
    # a gap of two box heights (0.5 here) does not.
    stopped = [(0.1, 0.5, True), (0.5, 0.5, False), (0.9, 0.5, False)]
    assert queue_tail_reach_scaled(stopped) == pytest.approx(0.9)
    assert queue_tail_reach([0.1, 0.5, 0.9], 0.25) == pytest.approx(0.1)


def test_scaled_tail_starts_in_the_queue_region() -> None:
    # First stopped vehicle sits 0.3 along the axis but inside the Queue_Region.
    assert queue_tail_reach_scaled([(0.3, 0.1, True), (0.38, 0.1, False)]) == pytest.approx(0.38)
    # The same vehicle outside the Queue_Region does not start a queue.
    assert queue_tail_reach_scaled([(0.3, 0.1, False), (0.38, 0.1, False)]) == 0.0


def test_scaled_tail_ignores_an_isolated_far_vehicle() -> None:
    assert queue_tail_reach_scaled([(0.05, 0.1, True), (0.12, 0.1, False), (0.8, 0.1, False)]) == pytest.approx(0.12)


def test_v2_geometry_loads_with_drawn_axes() -> None:
    cfg = load_config(str(CALIBRATED.parent / "bellevue_116th_v2.json"))
    axes = build_approach_axes(cfg)
    assert all(axis.direction_source == "drawn" for axis in axes.values())
    for approach in cfg.approaches:
        # The first queue_axis point is the stop line: fraction 0.
        assert axes[approach.name].fraction(approach.queue_axis[0]) == pytest.approx(0.0)


def test_gap_scales_with_extent_along_the_road_not_box_height(base: Config) -> None:
    """A side-on car (wide, short box) on a horizontal road: its length along the road is
    the box WIDTH. A queue of such cars spaced 1.5 car lengths apart must stay one queue."""
    cfg = robust(load_config(str(CALIBRATED.parent / "bellevue_116th_v2.json")),
                 queue_tail_gap=0.0, queue_tail_gap_boxes=2.0)
    axis = build_approach_axes(cfg)["West"]          # roughly horizontal, ~380 px long
    ux, uy = axis.upstream
    engine = MetricsEngine(cfg)
    spacing = 1.5 * 110.0                            # 110 px wide, 55 px tall cars
    cars = []
    for k in range(3):
        d = 10.0 + k * spacing
        p = (axis.origin[0] + ux * d, axis.origin[1] + uy * d)
        cars.append(AssignedTrack(track_id=k + 1, vehicle_class="car", ref_point=p, approach="West",
                                  is_queueing=(k == 0), box_height=55.0, box_width=110.0))
    out = None
    for _ in range(40):
        out = engine.update(cars, ALL_RED, DT)["West"]
    # Box height alone (2 x 55 = 110 px) is less than the 165 px spacing and would cut the
    # chain after the first car; the extent along the road (about 2 x 110) keeps it whole.
    assert out.queue_reach == pytest.approx((10.0 + 2 * spacing) / axis.length, abs=0.03)
