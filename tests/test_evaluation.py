"""Unit tests for Evaluation_Metrics and the video specification set.

Requirements 11.4 (truncated phases excluded), 13.2 to 13.5 (what is measured and
how it is reported), and 16.1 to 16.3 (the evaluation input set and its metadata).

Metrics are checked against hand-built Run_Logs with known values, so a derivation
error shows up as a wrong number here rather than as an unexplained difference between
two controllers later. Nothing in this module decodes video — that is the point of
deriving every metric from the Run_Log (Requirement 13.4).

Validates: Requirements 11.4, 13.2, 13.3, 13.4, 13.5, 16.1, 16.2, 16.3
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from src.config import APPROACH_NAMES
from src.errors import EvaluationError
from src.evaluation import (
    AGGREGATE,
    MAX_VIDEOS,
    MIN_VIDEOS,
    EvaluationMetrics,
    VideoSpec,
    compute_metrics,
    load_video_specs,
    metrics_json,
    processing_fps,
    read_metrics,
)
from src.results_store import RunLog

FRAME_RATE = 2.0

#: North's Queue_Length and Signal_State over four frames: two vehicles join, are
#: held on RED for one frame, then are released on GREEN.
NORTH_QUEUES = (0, 2, 2, 0)
NORTH_STATES = ("RED", "RED", "GREEN", "GREEN")


def _frame(index: int) -> dict[str, object]:
    approaches = {}
    for name in APPROACH_NAMES:
        queue = NORTH_QUEUES[index] if name == "North" else 0
        state = NORTH_STATES[index] if name == "North" else "RED"
        approaches[name] = {
            "vehicle_count": queue,
            "queue_length": queue,
            "vehicle_density": queue / 4.0,
            "normalized_queue": queue / 4.0,
            "score": queue / 8.0,
            "signal_state": state,
        }
    return {
        "frame_index": index,
        "simulated_time": index / FRAME_RATE,
        "approaches": approaches,
    }


def _log(
    *,
    controller: str = "adaptive",
    complete: bool = True,
    processing_seconds: float = 2.0,
    waiting_times: dict[str, float] | None = None,
    phases: Sequence[dict[str, object]] | None = None,
) -> RunLog:
    return RunLog(
        run_id=f"junction__{controller}__alpha0p50__20260816-090000",
        video_path="videos/junction_a.mp4",
        controller_name=controller,
        alpha=0.5,
        config={"approaches": [{"name": name} for name in APPROACH_NAMES]},
        video_info={"frame_rate": FRAME_RATE, "frame_count": len(NORTH_QUEUES)},
        frames=[_frame(index) for index in range(len(NORTH_QUEUES))],
        waiting_times=waiting_times if waiting_times is not None else {"1": 1.0, "2": 0.5},
        phases=list(phases or ()),
        complete=complete,
        processing_seconds=processing_seconds,
    )


# ---------------------------------------------------------------------------
# Requirement 13.2 — the per-approach metrics
# ---------------------------------------------------------------------------


def test_per_approach_metrics_are_derived_from_the_frame_records() -> None:
    per_approach, _aggregate = compute_metrics(_log())
    north = per_approach["North"]

    # Average queue length over four frames of 0, 2, 2, 0.
    assert north.avg_queue_length == pytest.approx(1.0)
    assert north.max_queue_length == 2
    # Two vehicles left the queue region while North was GREEN.
    assert north.vehicles_served == 2
    # One frame of two vehicles waiting on RED, at half a second per frame, shared
    # between the two vehicles that joined.
    assert north.avg_waiting_time == pytest.approx(0.5)
    # Two vehicles over two simulated seconds is sixty per minute.
    assert north.throughput == pytest.approx(60.0)
    # Processing_FPS is a property of the run, not of an approach.
    assert north.processing_fps is None


def test_an_approach_that_never_queued_reports_zeroes() -> None:
    per_approach, _aggregate = compute_metrics(_log())

    for name in APPROACH_NAMES:
        if name == "North":
            continue
        metrics = per_approach[name]
        assert metrics.avg_queue_length == 0.0
        assert metrics.max_queue_length == 0
        assert metrics.vehicles_served == 0
        assert metrics.avg_waiting_time == 0.0
        assert metrics.throughput == 0.0


def test_vehicles_leaving_on_red_are_not_counted_as_served() -> None:
    """Vehicles_Served counts departures while the Approach is GREEN (Req 13.2)."""
    log = _log()
    for record in log.frames:
        record["approaches"]["North"]["signal_state"] = "RED"

    per_approach, aggregate = compute_metrics(log)

    assert per_approach["North"].vehicles_served == 0
    assert aggregate.vehicles_served == 0
    assert aggregate.throughput == 0.0


# ---------------------------------------------------------------------------
# Requirements 13.3, 13.5 — the aggregate and the processing rate
# ---------------------------------------------------------------------------


def test_aggregate_metrics_summarize_all_four_approaches() -> None:
    _per_approach, aggregate = compute_metrics(_log())

    # The mean of the per-track accumulations the run recorded (Requirement 13.2).
    assert aggregate.avg_waiting_time == pytest.approx(0.75)
    # The mean of the four per-approach averages.
    assert aggregate.avg_queue_length == pytest.approx(0.25)
    assert aggregate.max_queue_length == 2
    assert aggregate.vehicles_served == 2
    assert aggregate.throughput == pytest.approx(60.0)


def test_processing_fps_is_frames_over_wall_clock_seconds() -> None:
    """Requirement 13.3."""
    log = _log(processing_seconds=2.0)

    assert processing_fps(log) == pytest.approx(2.0)
    assert compute_metrics(log)[1].processing_fps == pytest.approx(2.0)


def test_processing_fps_is_absent_when_it_was_not_measured() -> None:
    log = _log(processing_seconds=0.0)

    assert processing_fps(log) is None
    assert compute_metrics(log)[1].processing_fps is None


def test_aggregate_waiting_time_falls_back_to_the_approach_values() -> None:
    """A log with no per-track accumulations still reports a waiting time."""
    log = _log(waiting_times={})

    _per_approach, aggregate = compute_metrics(log)

    # One approach waited 0.5 s per vehicle and three waited nothing.
    assert aggregate.avg_waiting_time == pytest.approx(0.125)


def test_an_empty_log_reports_zeroes_rather_than_failing() -> None:
    log = _log()
    log.frames = []

    per_approach, aggregate = compute_metrics(log)

    assert all(metrics.max_queue_length == 0 for metrics in per_approach.values())
    assert aggregate.vehicles_served == 0
    assert aggregate.throughput == 0.0
    assert aggregate.processing_fps is None


# ---------------------------------------------------------------------------
# Requirement 11.4 — truncated phases
# ---------------------------------------------------------------------------


def test_metrics_json_excludes_a_truncated_phase_from_the_averages() -> None:
    phases = [
        {
            "state": "GREEN",
            "approach": "North",
            "green_time": 30.0,
            "truncated": False,
            "starvation_override": False,
        },
        {
            "state": "YELLOW",
            "approach": "North",
            "green_time": 30.0,
            "truncated": False,
            "starvation_override": False,
        },
        {
            "state": "GREEN",
            "approach": "East",
            "green_time": 60.0,
            "truncated": True,
            "starvation_override": True,
        },
    ]

    summary = metrics_json(_log(phases=phases))["phases"]

    assert summary == {
        "recorded": 3,
        "completed": 2,
        "truncated": 1,
        # Only the completed GREEN phase counts, so 30 s rather than the mean of 30
        # and the 60 s phase the video cut short.
        "avg_green_time": pytest.approx(30.0),
        "starvation_overrides": 1,
    }


def test_metrics_json_has_the_shape_the_run_log_records() -> None:
    obj = metrics_json(_log())

    assert set(obj) == {
        "approaches",
        "aggregate",
        "phases",
        "frames_processed",
        "simulated_duration",
    }
    assert set(obj["approaches"]) == set(APPROACH_NAMES)
    assert obj["frames_processed"] == 4
    assert obj["simulated_duration"] == pytest.approx(2.0)
    # Round-trips through the JSON form the Run_Log stores.
    assert json.loads(json.dumps(obj))["aggregate"]["vehicles_served"] == 2


def test_read_metrics_prefers_what_the_log_already_recorded() -> None:
    """A table must show the numbers the run recorded, not freshly derived ones."""
    log = _log()
    log.evaluation_metrics = metrics_json(log)
    log.evaluation_metrics["aggregate"]["vehicles_served"] = 99

    _approaches, aggregate = read_metrics(log)

    assert aggregate.vehicles_served == 99


def test_read_metrics_computes_when_the_log_recorded_none() -> None:
    log = _log()
    log.evaluation_metrics = {}

    _approaches, aggregate = read_metrics(log)

    assert aggregate.vehicles_served == 2


def test_evaluation_metrics_round_trip_through_json() -> None:
    metrics = EvaluationMetrics(1.5, 2.5, 3, 4, 5.5, None)

    assert EvaluationMetrics.from_json_obj(metrics.as_json_obj()) == metrics
    assert AGGREGATE == "ALL"


# ---------------------------------------------------------------------------
# Requirements 16.1 to 16.3 — the video specification set
# ---------------------------------------------------------------------------


def _spec_obj(path: str, role: str = "final") -> dict[str, object]:
    return {
        "path": f"videos/{path}",
        "role": role,
        "frame_rate": 25.0,
        "resolution": [1280, 720],
        "duration_seconds": 120.0,
        "visible_approaches": list(APPROACH_NAMES),
    }


def _write_specs(tmp_path: Path, videos: list[dict[str, object]]) -> str:
    path = tmp_path / "videos.json"
    path.write_text(json.dumps({"videos": videos}), encoding="utf-8")
    return str(path)


def test_a_valid_set_loads_with_its_metadata_and_roles(tmp_path: Path) -> None:
    """Requirements 16.2, 16.3."""
    path = _write_specs(
        tmp_path, [_spec_obj("a.mp4", "development"), _spec_obj("b.mp4", "final")]
    )

    specs = load_video_specs(path)

    assert [spec.stem for spec in specs] == ["a", "b"]
    assert [spec.role for spec in specs] == ["development", "final"]
    assert specs[0].frame_rate == pytest.approx(25.0)
    assert specs[0].resolution == (1280, 720)
    assert specs[0].duration_seconds == pytest.approx(120.0)
    assert specs[0].visible_approaches == APPROACH_NAMES
    assert VideoSpec.from_json_obj(specs[0].as_json_obj()) == specs[0]


def test_a_bare_list_is_accepted_as_well_as_a_videos_key(tmp_path: Path) -> None:
    path = tmp_path / "videos.json"
    path.write_text(json.dumps([_spec_obj("a.mp4"), _spec_obj("b.mp4")]), encoding="utf-8")

    assert len(load_video_specs(str(path))) == 2


@pytest.mark.parametrize("count", [0, 1, MAX_VIDEOS + 1])
def test_a_set_outside_two_to_five_videos_is_rejected_naming_the_count(
    count: int, tmp_path: Path
) -> None:
    """Requirement 16.1."""
    path = _write_specs(tmp_path, [_spec_obj(f"v{index}.mp4") for index in range(count)])

    with pytest.raises(EvaluationError, match=rf"{count} video"):
        load_video_specs(path)


def test_the_error_names_the_allowed_range(tmp_path: Path) -> None:
    path = _write_specs(tmp_path, [_spec_obj("a.mp4")])

    with pytest.raises(EvaluationError, match=f"between {MIN_VIDEOS} and {MAX_VIDEOS}"):
        load_video_specs(path)


def test_an_unknown_role_is_rejected(tmp_path: Path) -> None:
    path = _write_specs(tmp_path, [_spec_obj("a.mp4", "training"), _spec_obj("b.mp4")])

    with pytest.raises(EvaluationError, match="role 'training'"):
        load_video_specs(path)


def test_a_missing_field_is_named(tmp_path: Path) -> None:
    incomplete = _spec_obj("a.mp4")
    del incomplete["frame_rate"]
    path = _write_specs(tmp_path, [incomplete, _spec_obj("b.mp4")])

    with pytest.raises(EvaluationError, match="'frame_rate'"):
        load_video_specs(path)


def test_an_unknown_approach_name_is_named(tmp_path: Path) -> None:
    spec = _spec_obj("a.mp4")
    spec["visible_approaches"] = ["North", "Northwest"]
    path = _write_specs(tmp_path, [spec, _spec_obj("b.mp4")])

    with pytest.raises(EvaluationError, match="Northwest"):
        load_video_specs(path)


@pytest.mark.parametrize("field", ["frame_rate", "duration_seconds"])
def test_a_non_positive_measure_is_rejected(field: str, tmp_path: Path) -> None:
    spec = _spec_obj("a.mp4")
    spec[field] = 0.0
    path = _write_specs(tmp_path, [spec, _spec_obj("b.mp4")])

    with pytest.raises(EvaluationError, match=field):
        load_video_specs(path)


def test_a_duplicated_video_is_rejected(tmp_path: Path) -> None:
    path = _write_specs(tmp_path, [_spec_obj("a.mp4"), _spec_obj("a.mp4")])

    with pytest.raises(EvaluationError, match="more than once"):
        load_video_specs(path)


def test_a_missing_specification_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="2 to 5 junction videos"):
        load_video_specs(str(tmp_path / "absent.json"))


def test_malformed_specification_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "videos.json"
    path.write_text("{oops", encoding="utf-8")

    with pytest.raises(EvaluationError, match="not valid JSON"):
        load_video_specs(str(path))
