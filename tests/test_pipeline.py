"""Integration tests for the Pipeline and ``python -m src.main control``.

Requirements 10.6 (both controllers held to the same state integrity), 11.1 (the
Simulated_Clock as frame index over frame rate), 15.5 (the control entry point), and
14.6 (one frame record per frame covering all four Approaches).

The whole loop runs on a synthetic clip with a scripted tracker, so these tests need
no YOLO weights, no GPU, and no footage. Each run writes its Run_Log into ``tmp_path``
rather than into the repository's ``results/`` tree.

Validates: Requirements 5.5, 5.6, 10.1, 10.2, 10.6, 11.1, 11.4, 13.8, 14.6, 15.5
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src import main as cli
from src.config import APPROACH_NAMES, Config, load_config, to_json_obj
from src.detection import Detection
from src.main import Pipeline, PipelineResult, make_controller
from src.results_store import ResultsStore
from src.signal_controller import AdaptiveController, FixedTimeController
from tests.fixtures import (
    DEFAULT_CLIP_HEIGHT,
    DEFAULT_CLIP_WIDTH,
    ScriptedTracker,
    quadrant_config,
    quick_signal_config,
    write_synthetic_clip,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

#: Four frames per second against a one-to-three second green keeps a Cycle six to
#: ten frames long, so this clip covers three Cycles and ends part-way through a
#: fourth — which is what makes the truncated-phase case of Requirement 11.4
#: observable here rather than needing a separate clip.
CLIP_FRAME_RATE = 4.0
CLIP_FRAME_COUNT = 25

#: Reference points placed inside three of the four quadrant ROIs. North's sits in
#: its ROI but outside its Queue_Region; East's and South's are queueing.
NORTH_REF = (15, 8)
EAST_REF = (47, 20)
SOUTH_REF = (47, 40)


@pytest.fixture
def config() -> Config:
    """The shipped configuration scaled to the clip, with short phase durations."""
    base = load_config(str(DEFAULT_CONFIG_PATH))
    return quick_signal_config(
        quadrant_config(base, DEFAULT_CLIP_WIDTH, DEFAULT_CLIP_HEIGHT)
    )


@pytest.fixture
def clip(tmp_path: Path) -> str:
    return str(
        write_synthetic_clip(
            tmp_path / "junction.mp4",
            frame_count=CLIP_FRAME_COUNT,
            frame_rate=CLIP_FRAME_RATE,
        )
    )


@pytest.fixture
def store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(str(tmp_path / "run_logs"))


def _detection(ref: tuple[int, int], vehicle_class: str) -> Detection:
    """Return a detection whose bottom-edge midpoint is exactly ``ref``."""
    cx, cy = ref
    return Detection(
        x1=cx - 3, y1=cy - 6, x2=cx + 3, y2=cy, vehicle_class=vehicle_class, confidence=0.9
    )


def _script(frame_count: int = CLIP_FRAME_COUNT) -> list[list[Detection]]:
    """Three standing vehicles, one per quadrant, present on every frame.

    Standing rather than moving: the queue measures a vehicle that stays put, and a
    fixed script makes the per-frame counts a known constant, so a frame record can be
    asserted exactly rather than approximately.
    """
    return [
        [
            _detection(NORTH_REF, "car"),
            _detection(EAST_REF, "car"),
            _detection(SOUTH_REF, "bus"),
        ]
        for _ in range(frame_count)
    ]


def _tracker(config: Config) -> ScriptedTracker:
    return ScriptedTracker(config, _script())


def _run(
    clip: str, config: Config, controller: str, store: ResultsStore, **kwargs: object
) -> PipelineResult:
    return Pipeline(
        clip, config, controller, tracker=_tracker(config), store=store, **kwargs
    ).run()


# ---------------------------------------------------------------------------
# The loop over both controllers (Requirements 10.6, 13.1, 14.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("controller", ["fixed", "adaptive"])
def test_the_loop_runs_over_the_clip_for_both_controllers(
    controller: str, clip: str, config: Config, store: ResultsStore
) -> None:
    """One frame record per frame, all four Approaches, for either controller."""
    result = _run(clip, config, controller, store)
    log = result.log

    assert result.frames_processed == CLIP_FRAME_COUNT
    assert result.complete is True
    assert log.frame_count == CLIP_FRAME_COUNT
    assert [record["frame_index"] for record in log.frames] == list(range(CLIP_FRAME_COUNT))
    for record in log.frames:
        assert set(record["approaches"]) == set(APPROACH_NAMES)
        for entry in record["approaches"].values():
            for key in ("vehicle_count", "queue_length", "score", "signal_state"):
                assert key in entry

    assert Path(result.log_path).is_file()
    assert result.controller_name == controller
    assert log.controller_name == controller


def test_the_written_log_is_readable_and_carries_its_metrics(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """The Run_Log is what every later number is read from (Requirement 13.4)."""
    result = _run(clip, config, "adaptive", store)

    reread = store.read(result.log_path)

    assert reread.run_id == result.log.run_id
    assert reread.frame_count == CLIP_FRAME_COUNT
    assert reread.complete is True
    assert set(reread.evaluation_metrics) >= {"approaches", "aggregate", "phases"}
    assert reread.evaluation_metrics["aggregate"]["avg_queue_length"] >= 0.0
    assert reread.processing_seconds > 0.0
    # Requirement 13.3: Processing_FPS is derivable from the log alone.
    assert reread.frame_count / reread.processing_seconds > 0.0


@pytest.mark.parametrize("controller", ["fixed", "adaptive"])
def test_exactly_one_approach_is_non_red_on_every_recorded_frame(
    controller: str, clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirements 10.1, 10.2, 10.6, read back off the Run_Log."""
    log = _run(clip, config, controller, store).log

    for record in log.frames:
        states = [entry["signal_state"] for entry in record["approaches"].values()]
        assert states.count("RED") == 3
        assert len([state for state in states if state != "RED"]) == 1


def test_the_simulated_clock_is_the_frame_index_over_the_frame_rate(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirement 11.1."""
    log = _run(clip, config, "adaptive", store).log

    for record in log.frames:
        assert record["simulated_time"] == pytest.approx(
            record["frame_index"] / CLIP_FRAME_RATE
        )


def test_phases_record_the_frame_whose_scores_selected_them(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """The ordering point of the loop, recorded rather than assumed.

    The sequencer ticks before the metrics update, so a Cycle beginning on frame ``n``
    was selected from frame ``n - 1``'s Scores. The Run_Log says so, which is what
    keeps a decision auditable.
    """
    log = _run(clip, config, "adaptive", store).log

    assert log.phases
    for phase in log.phases:
        assert phase["selection_score_frame"] == phase["start_frame"] - 1
    # The first Cycle has no previous frame, recorded as -1.
    assert log.phases[0]["selection_score_frame"] == -1


def test_the_run_covers_several_cycles_and_marks_the_truncated_one(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirement 11.4: the phase the clip cut short is flagged and excluded."""
    result = _run(clip, config, "adaptive", store)
    log = result.log

    assert result.cycle_count >= 2, "the clip was too short to exercise more than one cycle"
    assert log.phases[-1]["truncated"] is True
    assert len(log.completed_phases) == len(log.phases) - 1
    assert log.evaluation_metrics["phases"]["truncated"] == 1


def test_waiting_time_accrues_only_where_an_approach_is_not_green(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirements 5.5 and 5.6, checked through the whole loop.

    The scripted vehicles stand still, so East and South are queueing on every frame.
    Their accumulated Waiting_Time must therefore be the number of frames their own
    Approach was not GREEN, times one frame interval.
    """
    log = _run(clip, config, "adaptive", store).log

    non_green_frames = {name: 0 for name in APPROACH_NAMES}
    for record in log.frames:
        for name, entry in record["approaches"].items():
            if entry["queue_length"] > 0 and entry["signal_state"] != "GREEN":
                non_green_frames[name] += 1

    expected = max(non_green_frames.values()) / CLIP_FRAME_RATE
    assert log.waiting_times, "no track ever waited, so the gating cannot be observed"
    assert max(log.waiting_times.values()) == pytest.approx(expected)


def test_the_fixed_controller_serves_the_approaches_in_order(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirement 7.1, observed through the pipeline rather than in isolation."""
    log = _run(clip, config, "fixed", store).log

    greens = [phase["approach"] for phase in log.phases if phase["state"] == "GREEN"]

    assert greens == [APPROACH_NAMES[index % 4] for index in range(len(greens))]
    assert {phase["green_time"] for phase in log.phases} == {30.0}


def test_the_adaptive_controller_uses_the_configured_bands(
    clip: str, config: Config, store: ResultsStore
) -> None:
    """Requirement 8.9: Green_Times come from configuration, not from literals."""
    log = _run(clip, config, "adaptive", store).log

    assigned = {phase["green_time"] for phase in log.phases}
    allowed = {band.green_time for band in config.green_time_bands}

    assert assigned <= allowed
    assert all(
        config.min_green_time <= green <= config.max_green_time for green in assigned
    )


# ---------------------------------------------------------------------------
# Annotated video output (Requirement 12.6)
# ---------------------------------------------------------------------------


def _decoded_frame_count(path: str | Path) -> int:
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened(), f"output video could not be reopened: {path}"
    try:
        count = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                return count
            count += 1
    finally:
        capture.release()


def test_the_annotated_video_has_one_frame_per_processed_frame(
    clip: str, config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    out_path = tmp_path / "control.mp4"

    result = _run(clip, config, "adaptive", store, out_path=str(out_path))

    assert result.out_path == str(out_path)
    assert result.frames_written == CLIP_FRAME_COUNT
    assert _decoded_frame_count(out_path) == CLIP_FRAME_COUNT


def test_no_video_is_written_unless_asked_for(
    clip: str, config: Config, store: ResultsStore
) -> None:
    result = _run(clip, config, "adaptive", store)

    assert result.out_path is None
    assert result.frames_written == 0


def test_a_default_video_path_is_named_by_the_run_id(
    clip: str, config: Config, store: ResultsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs of one clip must not overwrite each other's output."""
    monkeypatch.chdir(tmp_path)

    result = _run(clip, config, "fixed", store, write_video=True)

    assert result.out_path is not None
    assert Path(result.out_path).name == f"{result.log.run_id}.mp4"
    assert Path(result.out_path).is_file()


# ---------------------------------------------------------------------------
# Early exit (Requirements 1.6, 13.8)
# ---------------------------------------------------------------------------


def test_the_quit_key_ends_the_run_and_marks_it_incomplete(
    clip: str, config: Config, store: ResultsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 13.8: a run cut short is recorded as incomplete, not discarded."""
    shown: list[int] = []

    def fake_imshow(name: str, frame: np.ndarray) -> None:
        shown.append(len(shown))

    monkeypatch.setattr(cli.cv2, "imshow", fake_imshow)
    monkeypatch.setattr(cli.cv2, "waitKey", lambda delay: ord(config.quit_key))
    monkeypatch.setattr(cli.cv2, "destroyAllWindows", lambda: None)

    result = _run(clip, config, "adaptive", store, display=True)

    assert result.quit_early is True
    assert result.complete is False
    assert result.frames_processed == 1
    assert store.read(result.log_path).complete is False
    assert any("quit key" in warning for warning in result.log.warnings)


def test_an_unknown_controller_name_is_rejected(config: Config) -> None:
    with pytest.raises(Exception, match="unknown controller"):
        make_controller("random", config)


def test_make_controller_builds_the_two_supported_controllers(config: Config) -> None:
    assert isinstance(make_controller("fixed", config), FixedTimeController)
    assert isinstance(make_controller("adaptive", config), AdaptiveController)


# ---------------------------------------------------------------------------
# Requirement 15.5 — the control entry point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("controller", ["fixed", "adaptive"])
def test_control_command_runs_headless_and_reports_its_run(
    controller: str,
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``python -m src.main control --video V --controller {fixed,adaptive}``."""
    monkeypatch.chdir(tmp_path)
    clip_path = write_synthetic_clip(
        tmp_path / "junction.mp4", frame_count=CLIP_FRAME_COUNT, frame_rate=CLIP_FRAME_RATE
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda cfg: ScriptedTracker(cfg, _script()))

    status = cli.main(
        [
            "control",
            "--video",
            str(clip_path),
            "--controller",
            controller,
            "--config",
            str(config_path),
            "--no-display",
        ]
    )

    assert status == 0
    out = capsys.readouterr().out
    assert f"controller     : {controller}" in out
    assert "run log        :" in out
    assert "output video   :" in out
    # The run log and the annotated video both landed under results/ in the cwd.
    assert list((tmp_path / "results" / "run_logs").glob("*.json"))
    assert list((tmp_path / "results" / "videos").glob("*.mp4"))


def test_control_command_can_skip_the_annotated_video(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    clip_path = write_synthetic_clip(
        tmp_path / "junction.mp4", frame_count=8, frame_rate=CLIP_FRAME_RATE
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda cfg: ScriptedTracker(cfg, _script()))

    status = cli.main(
        [
            "control",
            "--video",
            str(clip_path),
            "--config",
            str(config_path),
            "--no-display",
            "--no-video",
        ]
    )

    assert status == 0
    assert "output video" not in capsys.readouterr().out
    assert not (tmp_path / "results" / "videos").exists()


def test_control_command_reports_a_missing_video_and_exits_nonzero(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 1.4: one handler prints the message and exits 1."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda cfg: ScriptedTracker(cfg, _script()))

    status = cli.main(
        [
            "control",
            "--video",
            str(tmp_path / "absent.mp4"),
            "--config",
            str(config_path),
            "--no-display",
        ]
    )

    assert status == 1
    assert "absent.mp4" in capsys.readouterr().err
