"""Tests for the Results_Store.

Requirements 1.5 (frame-rate substitution recorded as a warning), 4.4 (overlap
events), 9.6 (starvation overrides), 11.4 (phases with their truncated flag),
13.8 (the run's ``complete`` flag), 14.3 (resolved configuration, video path and
controller name at run start), and 14.6 (per-frame Vehicle_Count, Queue_Length,
Score and Signal_State for each Approach).

The Run_Log round trip of Requirement 14.4 is quantified over generated logs in
``test_results_store_properties.py``; what is pinned here is the *content* of a log
written from real pipeline values.

Validates: Requirements 1.5, 4.4, 9.6, 11.4, 13.8, 14.3, 14.6
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, Config, load_config
from src.errors import EvaluationError
from src.lane_analysis import OverlapEvent
from src.results_store import (
    RUN_LOG_VERSION,
    ResultsStore,
    RunLog,
    format_alpha,
    frame_record,
    make_run_id,
)
from src.signal_controller import (
    AdaptiveController,
    PhaseSequencer,
    SignalState,
)
from src.traffic_metrics import ApproachMetrics, FrameMeasurement
from src.video_io import VideoInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

FRAME_RATE = 4.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


@pytest.fixture
def store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(str(tmp_path / "run_logs"))


def _video_info(*, substituted: bool = False, frame_count: int = 20) -> VideoInfo:
    return VideoInfo(
        path="videos/junction_a.mp4",
        width=64,
        height=48,
        frame_count=frame_count,
        frame_rate=FRAME_RATE,
        frame_rate_substituted=substituted,
    )


def _measurement(frame_index: int, *, green: str = "North") -> FrameMeasurement:
    metrics = {
        name: ApproachMetrics(
            approach=name,
            vehicle_count=index + frame_index,
            vehicle_density=min(1.0, 0.1 * (index + frame_index)),
            queue_length=index,
            normalized_queue=min(1.0, 0.2 * index),
        )
        for index, name in enumerate(APPROACH_NAMES)
    }
    return FrameMeasurement(
        frame_index=frame_index,
        simulated_time=frame_index / FRAME_RATE,
        metrics=metrics,
        scores={name: 0.25 * index for index, name in enumerate(APPROACH_NAMES)},
        signal_states={
            name: "GREEN" if name == green else "RED" for name in APPROACH_NAMES
        },
    )


# ---------------------------------------------------------------------------
# Requirement 14.3 — what a run records about itself at the start
# ---------------------------------------------------------------------------


def test_start_run_records_config_video_and_controller(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())

    assert log.video_path == "videos/junction_a.mp4"
    assert log.controller_name == "adaptive"
    assert log.alpha == pytest.approx(config.alpha)
    # The resolved configuration, not the path it was loaded from.
    assert log.config["alpha"] == pytest.approx(config.alpha)
    assert len(log.config["approaches"]) == 4
    assert log.config["green_time_bands"][0]["green_time"] == pytest.approx(
        config.green_time_bands[0].green_time
    )
    assert log.video_info["frame_rate"] == pytest.approx(FRAME_RATE)
    assert log.version == RUN_LOG_VERSION
    assert log.complete is False


def test_run_id_names_video_controller_alpha_and_time(config: Config) -> None:
    run_id = make_run_id(
        "videos/junction a.mp4", "adaptive", 0.5, datetime(2026, 8, 16, 9, 30, 15)
    )

    assert run_id == "junction-a__adaptive__alpha0p50__20260816-093015"
    assert format_alpha(0.0) == "0p00"
    assert format_alpha(1.0) == "1p00"


def test_default_path_is_the_run_id_under_the_run_log_directory(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run("videos/junction_a.mp4", "fixed", config, _video_info())

    path = Path(store.default_path(log))

    assert path.name == f"{log.run_id}.json"
    assert path.parent == store.out_dir


# ---------------------------------------------------------------------------
# Requirement 1.5 — the frame-rate substitution warning
# ---------------------------------------------------------------------------


def test_a_substituted_frame_rate_is_recorded_as_a_warning(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run(
        "videos/junction_a.mp4", "adaptive", config, _video_info(substituted=True)
    )

    assert len(log.warnings) == 1
    assert "frame rate" in log.warnings[0]
    assert str(config.default_frame_rate) in log.warnings[0]


def test_a_usable_frame_rate_records_no_warning(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())

    assert log.warnings == []


# ---------------------------------------------------------------------------
# Requirement 14.6 — one record per frame, all four approaches
# ---------------------------------------------------------------------------


def test_frame_record_carries_every_required_measure() -> None:
    record = frame_record(_measurement(3, green="East"))

    assert record["frame_index"] == 3
    assert record["simulated_time"] == pytest.approx(3 / FRAME_RATE)
    assert set(record["approaches"]) == set(APPROACH_NAMES)
    east = record["approaches"]["East"]
    for key in ("vehicle_count", "queue_length", "score", "signal_state"):
        assert key in east
    assert east["signal_state"] == "GREEN"
    assert record["approaches"]["North"]["signal_state"] == "RED"


def test_append_frame_record_keeps_one_record_per_frame_in_order(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())

    for index in range(5):
        store.append_frame_record(log, _measurement(index))

    assert log.frame_count == 5
    assert [record["frame_index"] for record in log.frames] == [0, 1, 2, 3, 4]
    assert log.simulated_duration == pytest.approx(5 / FRAME_RATE)


def test_approach_series_reads_one_measure_across_the_run(
    store: ResultsStore, config: Config
) -> None:
    """The accessor every Evaluation_Metric is derived through (Requirement 13.4)."""
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    for index in range(4):
        store.append_frame_record(log, _measurement(index))

    counts = log.approach_series("South", "vehicle_count")

    # South is the third Approach, so its count is index + 2 on frame index.
    assert counts == [2.0, 3.0, 4.0, 5.0]


# ---------------------------------------------------------------------------
# Requirements 4.4, 9.6, 11.4 — events, overrides and phases
# ---------------------------------------------------------------------------


def test_overlap_events_reach_the_log(store: ResultsStore, config: Config) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())

    store.append_overlap_events(
        log,
        [OverlapEvent(track_id=7, frame_index=2, candidates=("North", "East"), chosen="East")],
    )

    assert log.overlap_events == [
        {"track_id": 7, "frame_index": 2, "candidates": ["North", "East"], "chosen": "East"}
    ]


def test_phases_and_starvation_overrides_come_from_the_sequencer(
    store: ResultsStore, config: Config
) -> None:
    """Requirements 9.6 and 11.4, recorded from a real run rather than a stand-in."""
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    sequencer = PhaseSequencer(AdaptiveController(config), config, 1.0)
    busy_north = {name: 0.9 if name == "North" else 0.0 for name in APPROACH_NAMES}

    frame = 0
    while sequencer.cycle_count < 4:
        sequencer.tick(frame, busy_north)
        frame += 1
    truncated = sequencer.finalize(frame - 1)
    store.record_phases(log, sequencer.phases)

    assert truncated is not None
    assert log.phases[-1]["truncated"] is True
    assert log.completed_phases == [p for p in log.phases if not p["truncated"]]
    # The fourth Cycle is the one the Starvation_Limit forces, and it is the only
    # override in the log.
    assert [override["approach"] for override in log.starvation_overrides] == ["East"]
    assert log.starvation_overrides[0]["cycle_index"] == 3


def test_record_phases_replaces_rather_than_appends(
    store: ResultsStore, config: Config
) -> None:
    """Called again after finalize, the truncation flag must be picked up."""
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    sequencer = PhaseSequencer(AdaptiveController(config), config, 1.0)
    scores = {name: 0.5 for name in APPROACH_NAMES}
    sequencer.tick(0, scores)

    store.record_phases(log, sequencer.phases)
    assert log.phases[0]["truncated"] is False

    sequencer.finalize(0)
    store.record_phases(log, sequencer.phases)

    assert len(log.phases) == 1
    assert log.phases[0]["truncated"] is True


def test_waiting_times_are_recorded_per_track(store: ResultsStore, config: Config) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())

    store.record_waiting_times(log, {4: 1.5, 9: 0.25})

    assert log.waiting_times == {"4": 1.5, "9": 0.25}


# ---------------------------------------------------------------------------
# Requirement 13.8 — completeness, and persistence
# ---------------------------------------------------------------------------


def test_finish_run_writes_metrics_and_the_complete_flag(
    store: ResultsStore, config: Config
) -> None:
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    store.append_frame_record(log, _measurement(0))

    path = store.finish_run(log, {"aggregate": {"avg_waiting_time": 2.5}}, True)

    assert Path(path).is_file()
    assert log.complete is True
    reread = store.read(path)
    assert reread.complete is True
    assert reread.evaluation_metrics["aggregate"]["avg_waiting_time"] == pytest.approx(2.5)


def test_an_incomplete_run_is_marked_as_such(store: ResultsStore, config: Config) -> None:
    """Requirement 13.8: a run cut short is recorded, not discarded or patched."""
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    store.append_frame_record(log, _measurement(0))

    path = store.finish_run(log, {}, False)

    assert store.read(path).complete is False


def test_write_creates_the_run_log_directory(tmp_path: Path, config: Config) -> None:
    store = ResultsStore(str(tmp_path / "nested" / "run_logs"))
    log = store.start_run("videos/junction_a.mp4", "fixed", config, _video_info())

    path = store.write(log, store.default_path(log))

    assert Path(path).is_file()
    assert json.loads(Path(path).read_text(encoding="utf-8"))["run_id"] == log.run_id


def test_find_logs_returns_written_logs_sorted(store: ResultsStore, config: Config) -> None:
    for controller in ("adaptive", "fixed"):
        log = store.start_run("videos/junction_a.mp4", controller, config, _video_info())
        store.finish_run(log, {}, True)

    paths = store.find_logs()

    assert len(paths) == 2
    assert paths == sorted(paths)


def test_reading_a_missing_log_names_the_path(store: ResultsStore) -> None:
    with pytest.raises(EvaluationError, match="not found"):
        store.read(str(store.out_dir / "absent.json"))


def test_reading_malformed_json_names_the_path(store: ResultsStore, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    with pytest.raises(EvaluationError, match="not valid JSON"):
        store.read(str(bad))


def test_reading_a_log_without_required_keys_is_an_error(tmp_path: Path) -> None:
    store = ResultsStore(str(tmp_path))
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"run_id": "x"}), encoding="utf-8")

    with pytest.raises(EvaluationError, match="video_path"):
        store.read(str(incomplete))


def test_non_finite_values_are_rejected_before_they_reach_the_file(
    store: ResultsStore, config: Config
) -> None:
    """NaN and Infinity are not JSON; failing here names the field."""
    log = store.start_run("videos/junction_a.mp4", "adaptive", config, _video_info())
    log.evaluation_metrics = {"aggregate": {"throughput": float("inf")}}

    with pytest.raises(EvaluationError, match="throughput"):
        store.write(log, store.default_path(log))


def test_signal_state_enum_members_serialize_as_their_values() -> None:
    """The overlay and the sequencer hold enum members; the log holds strings."""
    measurement = FrameMeasurement(
        frame_index=0,
        simulated_time=0.0,
        metrics={
            name: ApproachMetrics(
                approach=name,
                vehicle_count=0,
                vehicle_density=0.0,
                queue_length=0,
                normalized_queue=0.0,
            )
            for name in APPROACH_NAMES
        },
        scores={name: 0.0 for name in APPROACH_NAMES},
        signal_states={
            name: SignalState.GREEN if name == "West" else SignalState.RED
            for name in APPROACH_NAMES
        },
    )

    record = frame_record(measurement)

    assert record["approaches"]["West"]["signal_state"] == "GREEN"
    assert json.dumps(record)          # plain strings, so it serializes


def test_run_log_from_dict_tolerates_absent_optional_collections() -> None:
    log = RunLog.from_dict(
        {"run_id": "r", "video_path": "v.mp4", "controller_name": "fixed"}
    )

    assert log.frames == []
    assert log.phases == []
    assert log.warnings == []
    assert log.complete is False
    assert log.approach_names == APPROACH_NAMES
