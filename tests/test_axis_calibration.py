"""Tests for the approach-axis direction and its calibration from observed motion.

Why this module exists. The spatial reach measure X, and the spillback risk S derived
from it, both depend on one thing being right: which way is upstream on each approach.
The original construction inferred that from the offset between the ROI centroid and the
Queue_Region centroid, which is sound only when the Queue_Region is a strip at the stop
line. Drawn instead as an inset copy of the whole ROI, the two centroids nearly coincide
and the inferred direction is decided by a few pixels of drawing noise. That is not a
hypothetical: it happened in this project's own junction configuration, where one
approach's centroid separation was 2.9 px and its direction came out reversed.

These tests pin down three things:

* the conditioning diagnostic actually flags that situation rather than passing silently;
* a measured direction, when supplied, overrides the drawing entirely and orients the
  axis correctly regardless of how the Queue_Region was drawn;
* the estimator that produces the measured direction recovers a known ground-truth
  direction from synthetic tracks, and refuses to answer when the evidence is too weak.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.config import ApproachConfig
from src.errors import ConfigError
from src.lane_analysis import (
    ApproachAxis,
    AxisDirectionEstimator,
    as_cv_polygon,
    principal_axis,
)

# A horizontal approach 400 px long and 100 px tall. Traffic travels in +x, so the stop
# line is the right-hand edge at x = 400 and upstream is -x.
ROI = ((0, 0), (400, 0), (400, 100), (0, 100))

# Drawn correctly: a narrow strip at the downstream (right-hand) end.
STRIP_QUEUE = ((340, 0), (400, 0), (400, 100), (340, 100))

# Drawn badly: a uniformly inset copy of the ROI, so its centroid sits almost exactly on
# the ROI centroid and the direction it implies is meaningless.
INSET_QUEUE = ((20, 10), (380, 10), (380, 90), (20, 90))


def _axis(queue: tuple[tuple[int, int], ...], **kwargs: object) -> ApproachAxis:
    return ApproachAxis(as_cv_polygon(ROI), as_cv_polygon(queue), **kwargs)  # type: ignore[arg-type]


class TestConditioningDiagnostic:
    """The diagnostic must separate a usable drawing from an unusable one."""

    def test_strip_queue_region_is_well_conditioned(self) -> None:
        axis = _axis(STRIP_QUEUE)
        assert axis.well_conditioned
        assert axis.conditioning >= ApproachAxis.MIN_CONDITIONING
        assert axis.direction_source == "geometry"

    def test_inset_queue_region_is_flagged_ill_conditioned(self) -> None:
        axis = _axis(INSET_QUEUE)
        assert not axis.well_conditioned
        assert axis.conditioning < ApproachAxis.MIN_CONDITIONING

    def test_ill_conditioned_axis_still_produces_finite_fractions(self) -> None:
        """Being ill-conditioned means the SIGN is doubtful, not that maths breaks.

        The measure must stay usable and bounded, because the controller reads it every
        frame; the failure mode to guard against is a silent NaN or an out-of-range
        value, not a wrong direction, which the diagnostic reports separately.
        """
        axis = _axis(INSET_QUEUE)
        for point in ((0, 50), (200, 50), (400, 50), (-100, 50), (999, 50)):
            value = axis.fraction(point)
            assert 0.0 <= value <= 1.0
            assert math.isfinite(value)

    def test_strip_queue_region_orients_upstream_away_from_stop_line(self) -> None:
        """Sanity check on the correctly-drawn case: the stop line reads ~0."""
        axis = _axis(STRIP_QUEUE)
        assert axis.fraction((400, 50)) == pytest.approx(0.0, abs=0.05)
        assert axis.fraction((0, 50)) > 0.8


class TestMeasuredDirection:
    """A supplied direction must override the drawing completely."""

    def test_measured_direction_fixes_a_badly_drawn_queue_region(self) -> None:
        """The whole point: correct orientation despite unusable polygon geometry."""
        axis = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
        assert axis.direction_source == "measured"
        assert axis.well_conditioned
        # Downstream end (the stop line) reads 0, far upstream reads 1.
        assert axis.fraction((400, 50)) == pytest.approx(0.0, abs=1e-6)
        assert axis.fraction((0, 50)) == pytest.approx(1.0, abs=1e-6)
        assert axis.fraction((200, 50)) == pytest.approx(0.5, abs=0.01)

    def test_measured_direction_ignores_queue_region_entirely(self) -> None:
        """Two very different Queue_Regions must give the same measured axis.

        This is the property that makes the measured path robust: it removes the
        drawing from the calculation rather than merely tolerating it.
        """
        a = _axis(STRIP_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
        b = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
        assert a.length == pytest.approx(b.length)
        assert a.upstream == pytest.approx(b.upstream)
        for point in ((0, 50), (137, 20), (400, 90)):
            assert a.fraction(point) == pytest.approx(b.fraction(point))

    def test_reversing_the_measured_direction_reverses_the_reach(self) -> None:
        """Guards the sign convention, which is the thing that was wrong in the config."""
        forward = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
        backward = _axis(INSET_QUEUE, direction=(-1.0, 0.0), direction_confidence=0.9)
        assert forward.fraction((400, 50)) == pytest.approx(
            1.0 - backward.fraction((400, 50)), abs=1e-6
        )

    def test_low_confidence_measured_axis_is_flagged(self) -> None:
        axis = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.2)
        assert axis.direction_source == "measured"
        assert not axis.well_conditioned

    def test_measured_axis_length_spans_the_roi(self) -> None:
        axis = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
        assert axis.length == pytest.approx(400.0)

    def test_degenerate_measured_direction_is_inert_not_fatal(self) -> None:
        axis = _axis(STRIP_QUEUE, direction=(0.0, 0.0), direction_confidence=0.9)
        assert axis.length == 0.0
        assert not axis.well_conditioned
        assert axis.fraction((200, 50)) == 0.0

    def test_diagonal_measured_direction_normalises(self) -> None:
        """An unnormalised direction must be accepted and normalised, not scale the axis."""
        unit = _axis(STRIP_QUEUE, direction=(0.6, 0.8), direction_confidence=0.9)
        scaled = _axis(STRIP_QUEUE, direction=(60.0, 80.0), direction_confidence=0.9)
        assert unit.length == pytest.approx(scaled.length)
        assert unit.upstream == pytest.approx(scaled.upstream)
        assert math.hypot(*unit.upstream) == pytest.approx(1.0)


class TestPrincipalAxis:
    """The polygon long axis, and the elongation that says whether to trust it."""

    def test_elongated_roi_has_a_clear_axis(self) -> None:
        direction, elongation = principal_axis(as_cv_polygon(ROI))
        assert elongation == pytest.approx(4.0, rel=0.02)
        # 400x100 box: long axis is horizontal, up to sign.
        assert abs(direction[0]) == pytest.approx(1.0, abs=0.01)
        assert abs(direction[1]) == pytest.approx(0.0, abs=0.01)

    def test_square_roi_has_no_meaningful_axis(self) -> None:
        square = ((0, 0), (200, 0), (200, 200), (0, 200))
        _, elongation = principal_axis(as_cv_polygon(square))
        assert elongation == pytest.approx(1.0, rel=0.02)


class TestAxisDirectionEstimator:
    """Recovering a known direction from tracks, and refusing when evidence is thin."""

    @staticmethod
    def _drive(
        estimator: AxisDirectionEstimator,
        approach: str,
        n_tracks: int,
        step: tuple[float, float],
        start: tuple[float, float] = (0.0, 50.0),
        queue_at: int | None = None,
        id_offset: int = 0,
    ) -> None:
        """Simulate ``n_tracks`` vehicles moving along ``step`` through ``approach``.

        ``id_offset`` keeps two calls on the same approach from colliding on track IDs,
        which would merge the two vehicles' trajectories into one.
        """
        for track_id in range(id_offset, id_offset + n_tracks):
            x, y = start
            for frame in range(10):
                queueing = queue_at is not None and frame >= queue_at
                estimator.observe(approach, track_id, (x, y), queueing=queueing)
                x += step[0]
                y += step[1]

    def test_recovers_a_known_direction(self) -> None:
        est = AxisDirectionEstimator()
        self._drive(est, "North", n_tracks=8, step=(10.0, 0.0), queue_at=8)
        result = est.entry_exit_direction("North", roi_span=400.0)
        assert result is not None
        direction, agreement, tracks = result
        assert direction[0] == pytest.approx(1.0, abs=1e-6)
        assert direction[1] == pytest.approx(0.0, abs=1e-6)
        assert agreement == pytest.approx(1.0)
        assert tracks == 8

    def test_recovers_a_reversed_direction(self) -> None:
        """The case that mattered: traffic travelling the opposite way."""
        est = AxisDirectionEstimator()
        self._drive(est, "East", n_tracks=8, step=(-10.0, 0.0), start=(400.0, 50.0))
        result = est.entry_exit_direction("East", roi_span=400.0)
        assert result is not None
        direction, _, _ = result
        assert direction[0] == pytest.approx(-1.0, abs=1e-6)

    def test_refuses_when_too_few_tracks(self) -> None:
        est = AxisDirectionEstimator()
        self._drive(est, "South", n_tracks=2, step=(10.0, 0.0))
        assert est.entry_exit_direction("South", roi_span=400.0) is None

    def test_stationary_tracks_cast_no_vote(self) -> None:
        """A vehicle queued for its whole appearance carries no heading information."""
        est = AxisDirectionEstimator()
        for track_id in range(20):
            for _ in range(10):
                est.observe("West", track_id, (100.0, 50.0), queueing=True)
        assert est.entry_exit_direction("West", roi_span=400.0) is None

    def test_balanced_opposing_flows_yield_no_confident_direction(self) -> None:
        """A ROI spanning both directions must not report a confident direction.

        Two acceptable outcomes: ``None``, when the entry and exit centroids coincide so
        exactly that no direction exists at all, or a direction whose agreement falls
        below the trust threshold. What must never happen is a confident answer, because
        the approach genuinely has no single travel direction, and the calibration would
        then silently install an arbitrary axis.
        """
        est = AxisDirectionEstimator()
        self._drive(est, "North", n_tracks=6, step=(10.0, 0.0), start=(0.0, 40.0))
        self._drive(
            est, "North", n_tracks=6, step=(-10.0, 0.0), start=(400.0, 60.0),
            id_offset=100,
        )
        result = est.entry_exit_direction("North", roi_span=400.0)
        if result is not None:
            _, agreement, _ = result
            assert agreement < ApproachAxis.MIN_DIRECTION_CONSENSUS, (
                "an ROI carrying both directions must not report a confident "
                f"direction, got agreement {agreement:.3f}"
            )

    def test_predominant_flow_still_wins_against_a_minority(self) -> None:
        """An imbalanced ROI must still resolve, since real approaches are imbalanced."""
        est = AxisDirectionEstimator()
        self._drive(est, "North", n_tracks=10, step=(10.0, 0.0), start=(0.0, 40.0))
        self._drive(
            est, "North", n_tracks=2, step=(-10.0, 0.0), start=(400.0, 60.0),
            id_offset=100,
        )
        result = est.entry_exit_direction("North", roi_span=400.0)
        assert result is not None
        direction, agreement, tracks = result
        assert tracks == 12
        assert direction[0] > 0.0, "the majority +x flow should set the direction"
        assert agreement == pytest.approx(abs(10 - 2) / 12, abs=1e-6)

    def test_approaches_are_measured_independently(self) -> None:
        est = AxisDirectionEstimator()
        self._drive(est, "North", n_tracks=6, step=(10.0, 0.0))
        self._drive(est, "South", n_tracks=6, step=(0.0, 10.0))
        north = est.entry_exit_direction("North", roi_span=400.0)
        south = est.entry_exit_direction("South", roi_span=400.0)
        assert north is not None and south is not None
        assert north[0][0] == pytest.approx(1.0, abs=1e-6)
        assert south[0][1] == pytest.approx(1.0, abs=1e-6)

    def test_inbound_filter_prefers_vehicles_that_reached_the_queue(self) -> None:
        """Inbound votes isolate the direction the queue measure actually cares about."""
        est = AxisDirectionEstimator()
        # Inbound: travels +x and reaches the queue region.
        self._drive(est, "North", n_tracks=6, step=(10.0, 0.0), queue_at=5)
        # Outbound: travels -x, never queues, so must not be counted as inbound.
        for track_id in range(200, 206):
            x = 400.0
            for _ in range(10):
                est.observe("North", track_id, (x, 70.0), queueing=False)
                x -= 10.0
        inbound = est.votes("North", inbound_only=True)
        everything = est.votes("North", inbound_only=False)
        assert len(inbound) == 6
        assert len(everything) == 12
        assert all(v[0] > 0.0 for v in inbound)


class TestConfigAxisFields:
    """The calibrated direction must survive the configuration round trip."""

    @staticmethod
    def _approach(**kwargs: object) -> ApproachConfig:
        base = dict(
            name="North",
            roi_polygon=ROI,
            queue_region=STRIP_QUEUE,
            saturation_count=12.0,
            queue_capacity=8.0,
        )
        base.update(kwargs)
        return ApproachConfig(**base)  # type: ignore[arg-type]

    def test_defaults_to_no_measured_direction(self) -> None:
        approach = self._approach()
        assert approach.axis_direction is None
        assert approach.axis_confidence == 1.0

    def test_round_trips_through_json(self) -> None:
        from src.config import _approach_from_json_obj, _approach_to_json_obj

        original = self._approach(axis_direction=(0.6, -0.8), axis_confidence=0.75)
        restored = _approach_from_json_obj(_approach_to_json_obj(original))
        assert restored.axis_direction == pytest.approx((0.6, -0.8))
        assert restored.axis_confidence == pytest.approx(0.75)

    def test_uncalibrated_approach_omits_the_fields(self) -> None:
        """An uncalibrated configuration must round-trip unchanged, not gain nulls."""
        from src.config import _approach_to_json_obj

        payload = _approach_to_json_obj(self._approach())
        assert "axis_direction" not in payload
        assert "axis_confidence" not in payload

    def test_zero_direction_is_rejected_at_load(self) -> None:
        """A degenerate direction would silently disable the measure, so refuse it."""
        from src.config import _approach_from_json_obj, _approach_to_json_obj

        payload = _approach_to_json_obj(self._approach())
        payload["axis_direction"] = [0.0, 0.0]
        with pytest.raises(ConfigError, match="non-zero"):
            _approach_from_json_obj(payload)

    @pytest.mark.parametrize("bad", [[1.0], [1.0, 2.0, 3.0], "left", 5])
    def test_malformed_direction_is_rejected(self, bad: object) -> None:
        from src.config import _approach_from_json_obj, _approach_to_json_obj

        payload = _approach_to_json_obj(self._approach())
        payload["axis_direction"] = bad
        with pytest.raises(ConfigError):
            _approach_from_json_obj(payload)


class TestRealJunctionConfigurations:
    """Pins the audit's finding so a future edit cannot quietly reintroduce it."""

    def test_bellevue_north_is_the_only_well_drawn_approach(self) -> None:
        """Documents the defect the audit found in the shipped junction geometry.

        North's Queue_Region was drawn as a stop-line strip and measures correctly;
        East, South and West were drawn as roughly half-ROI inset copies and their
        implied directions are unusable. This is asserted rather than merely written
        down so that fixing the geometry forces the test to be updated deliberately.
        """
        from src.config import APPROACH_NAMES, load_config
        from src.lane_analysis import build_approach_axes

        axes = build_approach_axes(load_config("config/bellevue_116th.json"))
        assert axes["North"].well_conditioned
        for name in ("East", "South", "West"):
            assert not axes[name].well_conditioned, (
                f"{name} unexpectedly well-conditioned; if the geometry was fixed, "
                "update this test"
            )
        assert set(axes) == set(APPROACH_NAMES)

    def test_default_config_is_well_drawn_throughout(self) -> None:
        from src.config import APPROACH_NAMES, load_config
        from src.lane_analysis import build_approach_axes

        axes = build_approach_axes(load_config("config/default.json"))
        for name in APPROACH_NAMES:
            assert axes[name].well_conditioned, f"{name} is ill-conditioned"

    def test_calibrated_config_repairs_three_of_four_axes(self) -> None:
        """The calibrated configuration must actually be an improvement."""
        from pathlib import Path

        from src.config import load_config
        from src.lane_analysis import build_approach_axes

        path = Path("config/bellevue_116th_calibrated.json")
        if not path.exists():
            pytest.skip("calibrated configuration not generated in this checkout")

        axes = build_approach_axes(load_config(str(path)))
        measured = [n for n, a in axes.items() if a.direction_source == "measured"]
        assert sorted(measured) == ["East", "North", "West"]
        for name in measured:
            assert axes[name].well_conditioned
        # South carried too little traffic to calibrate and must stay on the fallback,
        # still flagged, rather than being given an invented direction.
        assert axes["South"].direction_source == "geometry"
        assert not axes["South"].well_conditioned

    def test_calibration_reverses_the_east_axis(self) -> None:
        """The specific bug: East's geometric direction pointed the wrong way."""
        from pathlib import Path

        from src.config import load_config
        from src.lane_analysis import build_approach_axes

        path = Path("config/bellevue_116th_calibrated.json")
        if not path.exists():
            pytest.skip("calibrated configuration not generated in this checkout")

        before = build_approach_axes(load_config("config/bellevue_116th.json"))["East"]
        after = build_approach_axes(load_config(str(path)))["East"]
        cosine = (
            before.upstream[0] * after.upstream[0]
            + before.upstream[1] * after.upstream[1]
        )
        assert cosine < 0.0, (
            "East's calibrated axis should oppose the geometric one; "
            f"got cosine {cosine:+.3f}"
        )


def test_fraction_is_monotonic_along_the_measured_axis() -> None:
    """Reach must increase steadily with distance upstream, with no plateau or jump.

    Monotonicity is what makes X informative where the count-based queue saturates: the
    count stops responding once capacity is reached, whereas the fraction keeps rising
    as the queue physically extends further back.
    """
    axis = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
    values = [axis.fraction((x, 50)) for x in range(400, -1, -20)]
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)
    for earlier, later in zip(values, values[1:]):
        assert later >= earlier
    assert len(set(values)) > 15, "fraction should vary smoothly, not sit in steps"


def test_reach_does_not_saturate_where_a_capped_count_would() -> None:
    """The core claim behind the S3 -> S4 result, as an executable statement.

    Eight vehicles against a queue_capacity of 5 pin the normalised count at 1.0. The
    same eight vehicles spread further and further back keep raising the reach, so the
    spatial measure still carries information after the count has stopped responding.
    """
    axis = _axis(INSET_QUEUE, direction=(1.0, 0.0), direction_confidence=0.9)
    capacity = 5.0

    compact = [(400 - 10 * i, 50) for i in range(8)]
    spread = [(400 - 40 * i, 50) for i in range(8)]

    count_compact = min(len(compact) / capacity, 1.0)
    count_spread = min(len(spread) / capacity, 1.0)
    assert count_compact == count_spread == 1.0, "count must be saturated in both cases"

    reach_compact = max(axis.fraction(p) for p in compact)
    reach_spread = max(axis.fraction(p) for p in spread)
    assert reach_spread > reach_compact, (
        "spatial reach must still distinguish the two situations that the "
        "saturated count cannot"
    )


class TestIllConditionedAxisWarning:
    """The engine must say so when it is asked to use an axis it cannot trust.

    Silence is the dangerous outcome here: an inverted axis yields perfectly valid-looking
    numbers, so without a warning the only trace would be an unexplained green-time
    pattern inside a Run_Log.
    """

    @staticmethod
    def _config(**overrides: object):
        import dataclasses

        from src.config import load_config

        config = load_config("config/bellevue_116th.json")
        return dataclasses.replace(config, **overrides)  # type: ignore[arg-type]

    def test_warns_when_spatial_measures_use_a_bad_axis(self) -> None:
        from src.traffic_metrics import MetricsEngine

        config = self._config(use_spillback_risk=True, spillback_risk_weight=0.3)
        with pytest.warns(RuntimeWarning, match="axis direction is unreliable"):
            MetricsEngine(config)

    def test_warning_names_the_offending_approaches(self) -> None:
        from src.traffic_metrics import MetricsEngine

        config = self._config(use_queue_reach=True, queue_reach_weight=0.2)
        with pytest.warns(RuntimeWarning) as record:
            MetricsEngine(config)
        message = str(record[0].message)
        for name in ("East", "South", "West"):
            assert name in message
        assert "North" not in message, "North is well drawn and must not be flagged"

    def test_silent_when_the_spatial_measures_are_off(self) -> None:
        """A density-only baseline does not touch the axis, so it must not warn."""
        import warnings as _warnings

        from src.traffic_metrics import MetricsEngine

        config = self._config(use_queue_reach=False, use_spillback_risk=False)
        with _warnings.catch_warnings():
            _warnings.simplefilter("error", RuntimeWarning)
            MetricsEngine(config)

    def test_silent_on_a_well_drawn_configuration(self) -> None:
        import dataclasses
        import warnings as _warnings

        from src.config import load_config
        from src.traffic_metrics import MetricsEngine

        config = dataclasses.replace(
            load_config("config/default.json"),
            use_spillback_risk=True,
            spillback_risk_weight=0.3,
        )
        with _warnings.catch_warnings():
            _warnings.simplefilter("error", RuntimeWarning)
            MetricsEngine(config)


class TestPooledCalibration:
    """Pooling clips must raise confidence without changing what is estimated.

    Confidence depends on how many independent vehicles were observed, not on the geometry,
    so a short clip can leave an approach below the trust threshold even when its direction
    is perfectly consistent. Measured on this dataset: North's agreement is 0.531 on the
    107 s clip and 0.232 on the 240 s clip, with an identical direction verdict. Pooling is
    the principled remedy, and it only works if track identities from different clips are
    kept apart.
    """

    @staticmethod
    def _clip(
        estimator: AxisDirectionEstimator,
        approach: str,
        n_tracks: int,
        step: tuple[float, float],
        id_offset: int,
    ) -> None:
        for track_id in range(id_offset, id_offset + n_tracks):
            x, y = 0.0, 50.0
            for _ in range(10):
                estimator.observe(approach, track_id, (x, y))
                x += step[0]
                y += step[1]

    def test_pooling_accumulates_votes_across_clips(self) -> None:
        pooled = AxisDirectionEstimator()
        # Two clips, each contributing 4 vehicles: below MIN_VOTES alone, enough together.
        self._clip(pooled, "North", 4, (10.0, 0.0), id_offset=0)
        self._clip(pooled, "North", 4, (10.0, 0.0), id_offset=1_000_000)

        single = AxisDirectionEstimator()
        self._clip(single, "North", 4, (10.0, 0.0), id_offset=0)

        assert single.entry_exit_direction("North", roi_span=400.0) is None, (
            "four vehicles alone should be below the vote floor"
        )
        result = pooled.entry_exit_direction("North", roi_span=400.0)
        assert result is not None, "pooling two clips should clear the vote floor"
        direction, agreement, tracks = result
        assert tracks == 8
        assert direction[0] == pytest.approx(1.0, abs=1e-6)
        assert agreement == pytest.approx(1.0)

    def test_colliding_track_ids_would_corrupt_the_estimate(self) -> None:
        """Documents why the CLI namespaces Track_IDs per clip.

        Track_IDs restart at 1 for every clip. Without an offset, vehicle 1 of clip B
        would extend vehicle 1 of clip A's trajectory, fabricating a displacement between
        two unrelated vehicles. This test pins the failure so the offset is not removed as
        redundant.
        """
        colliding = AxisDirectionEstimator()
        # Clip A: 6 vehicles travelling +x, ending near x = 90.
        self._clip(colliding, "North", 6, (10.0, 0.0), id_offset=0)
        # Clip B reuses the SAME ids but travels -x from a far-right start.
        for track_id in range(6):
            x = 400.0
            for _ in range(10):
                colliding.observe("North", track_id, (x, 60.0))
                x -= 10.0

        namespaced = AxisDirectionEstimator()
        self._clip(namespaced, "North", 6, (10.0, 0.0), id_offset=0)
        for track_id in range(1_000_000, 1_000_006):
            x = 400.0
            for _ in range(10):
                namespaced.observe("North", track_id, (x, 60.0))
                x -= 10.0

        assert len(colliding.votes("North", inbound_only=False)) == 6
        assert len(namespaced.votes("North", inbound_only=False)) == 12, (
            "namespacing must keep the two clips' vehicles distinct"
        )
