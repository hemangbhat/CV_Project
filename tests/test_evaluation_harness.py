"""Integration tests for the comparison run, the tables, the graphs, and the report.

Requirements 13.1 (both controllers over the same video with the same configuration),
13.6 and 13.7 (comparison table and graphs), 13.8 (incomplete runs reported but
excluded), 13.9 (an Alpha sweep), 14.7 (regeneration from Run_Logs with no video
decode), 15.6 (the evaluate entry point), 16.4 (final videos reported separately from
development ones), and 17.1 to 17.6 (report artefacts).

Runs use a synthetic clip and a scripted tracker, so no weights, GPU, or footage are
needed, and everything is written under ``tmp_path``.

Validates: Requirements 13.1, 13.6, 13.7, 13.8, 13.9, 14.7, 15.6, 16.4, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src import evaluation
from src import main as cli
from src.config import APPROACH_NAMES, Config, load_config, to_json_obj
from src.detection import Detection
from src.errors import EvaluationError
from src.evaluation import (
    TABLE_COLUMNS,
    VideoSpec,
    build_report_artifacts,
    comparable_logs,
    export_screenshots,
    regenerate_artifacts,
    run_evaluation,
    write_comparison_graphs,
    write_comparison_table,
    write_literature_table,
    write_results_summary,
)
from src.results_store import ResultsStore
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

CLIP_FRAME_RATE = 4.0
CLIP_FRAME_COUNT = 20

#: One standing vehicle per quadrant. All four Approaches are occupied because the
#: harness samples each video first and excludes one whose ROI sees no traffic at all
#: (Requirement 16.5) — an empty quadrant here would be read as stale geometry.
NORTH_REF = (15, 8)
EAST_REF = (47, 20)
SOUTH_REF = (47, 40)
WEST_REF = (15, 40)


@pytest.fixture
def config() -> Config:
    base = load_config(str(DEFAULT_CONFIG_PATH))
    return quick_signal_config(
        quadrant_config(base, DEFAULT_CLIP_WIDTH, DEFAULT_CLIP_HEIGHT)
    )


def _detection(ref: tuple[int, int], vehicle_class: str) -> Detection:
    cx, cy = ref
    return Detection(
        x1=cx - 3, y1=cy - 6, x2=cx + 3, y2=cy, vehicle_class=vehicle_class, confidence=0.9
    )


def _script() -> list[list[Detection]]:
    return [
        [
            _detection(NORTH_REF, "car"),
            _detection(EAST_REF, "car"),
            _detection(SOUTH_REF, "bus"),
            _detection(WEST_REF, "motorcycle"),
        ]
        for _ in range(CLIP_FRAME_COUNT)
    ]


def _tracker_factory(config: Config) -> ScriptedTracker:
    return ScriptedTracker(config, _script())


def _patch_trackers(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    """Substitute the scripted tracker everywhere a real one would be built.

    Two places build a :class:`~src.tracking.ByteTrackTracker` during an evaluation
    started from the command line: each run's Pipeline, and the sampled ROI coverage
    check that runs before it (Requirement 16.5). Both are patched, so the CLI path is
    exercised end to end without weights.
    """
    del config                       # the scripted tracker carries its own script
    monkeypatch.setattr(cli, "ByteTrackTracker", lambda cfg: ScriptedTracker(cfg, _script()))
    monkeypatch.setattr(
        "src.lane_analysis.ByteTrackTracker", lambda cfg: ScriptedTracker(cfg, _script())
    )


@pytest.fixture
def specs(tmp_path: Path) -> list[VideoSpec]:
    """Two clips, one development and one final, so section separation is testable."""
    written: list[VideoSpec] = []
    for name, role in (("junction_dev.mp4", "development"), ("junction_final.mp4", "final")):
        path = write_synthetic_clip(
            tmp_path / name, frame_count=CLIP_FRAME_COUNT, frame_rate=CLIP_FRAME_RATE
        )
        written.append(
            VideoSpec(
                path=str(path),
                role=role,
                frame_rate=CLIP_FRAME_RATE,
                resolution=(DEFAULT_CLIP_WIDTH, DEFAULT_CLIP_HEIGHT),
                duration_seconds=CLIP_FRAME_COUNT / CLIP_FRAME_RATE,
                visible_approaches=APPROACH_NAMES,
            )
        )
    return written


@pytest.fixture
def store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(str(tmp_path / "run_logs"))


def _evaluate(
    specs: list[VideoSpec], config: Config, store: ResultsStore, **kwargs: object
) -> list:
    return run_evaluation(
        specs, config, store=store, tracker_factory=_tracker_factory, **kwargs
    )


# ---------------------------------------------------------------------------
# Requirement 13.1 — one fixed run and one adaptive run per video
# ---------------------------------------------------------------------------


def test_each_video_is_run_under_both_controllers(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    logs = _evaluate(specs, config, store)

    assert len(logs) == 2 * len(specs)
    by_video: dict[str, list[str]] = {}
    for log in logs:
        by_video.setdefault(Path(log.video_path).name, []).append(log.controller_name)
    assert {tuple(sorted(names)) for names in by_video.values()} == {("adaptive", "fixed")}
    assert all(log.complete for log in logs)
    assert len(store.find_logs()) == len(logs)


def test_both_runs_of_a_video_share_the_same_geometry_and_frame_count(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    """Requirement 13.1: only the controller differs between the two runs."""
    logs = _evaluate(specs, config, store)
    for spec in specs:
        pair = [log for log in logs if log.video_path == spec.path]
        assert len(pair) == 2
        first, second = pair
        assert first.config["approaches"] == second.config["approaches"]
        assert first.config["alpha"] == second.config["alpha"]
        assert first.frame_count == second.frame_count
        assert first.video_info == second.video_info


def test_an_alpha_sweep_produces_one_adaptive_run_per_alpha(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    """Requirement 13.9."""
    logs = _evaluate(specs, config, store, alphas=[0.0, 0.5, 1.0])

    adaptive = [log for log in logs if log.controller_name == "adaptive"]
    fixed = [log for log in logs if log.controller_name == "fixed"]

    assert len(fixed) == len(specs)
    assert len(adaptive) == 3 * len(specs)
    assert sorted({round(log.alpha, 2) for log in adaptive}) == [0.0, 0.5, 1.0]
    # The sweep changes alpha and nothing else.
    for log in adaptive:
        assert log.config["alpha"] == pytest.approx(log.alpha)
        assert log.config["approaches"] == logs[0].config["approaches"]


def test_alpha_endpoints_change_the_scores_the_controller_read(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    """Alpha 1 scores on density alone, Alpha 0 on queue alone (Reqs 6.3, 6.4)."""
    logs = _evaluate(specs[:1], config, store, alphas=[0.0, 1.0])
    by_alpha = {round(log.alpha, 2): log for log in logs if log.controller_name == "adaptive"}

    density_only = by_alpha[1.0].frames[-1]["approaches"]["East"]
    queue_only = by_alpha[0.0].frames[-1]["approaches"]["East"]

    assert density_only["score"] == pytest.approx(density_only["vehicle_density"])
    assert queue_only["score"] == pytest.approx(queue_only["normalized_queue"])


def test_an_alpha_outside_the_unit_range_is_rejected(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    with pytest.raises(EvaluationError, match="alpha"):
        _evaluate(specs, config, store, alphas=[1.5])


def test_an_empty_video_set_is_rejected(config: Config, store: ResultsStore) -> None:
    with pytest.raises(EvaluationError, match="no videos"):
        run_evaluation([], config, store=store)


# ---------------------------------------------------------------------------
# Requirement 16.5 — a video whose ROIs no longer match is excluded
# ---------------------------------------------------------------------------


def test_a_video_whose_rois_see_no_traffic_is_excluded_with_a_reason(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    """Requirement 16.5: report the mismatch and leave the video out."""
    excluded: list[str] = []

    # The shipped 1280x720 geometry cannot match a 64x48 clip, so every ROI is empty.
    with pytest.raises(EvaluationError, match="every video was excluded"):
        run_evaluation(
            specs,
            load_config(str(DEFAULT_CONFIG_PATH)),
            store=store,
            tracker_factory=_tracker_factory,
            check_roi=True,
            excluded=excluded,
        )

    assert len(excluded) == len(specs)
    assert "configuration does not match video" in excluded[0]
    assert store.find_logs() == []


def test_the_roi_check_passes_for_matching_geometry(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    excluded: list[str] = []

    logs = run_evaluation(
        specs,
        config,
        store=store,
        tracker_factory=_tracker_factory,
        check_roi=True,
        excluded=excluded,
    )

    assert excluded == []
    assert len(logs) == 2 * len(specs)


def test_the_roi_check_can_be_skipped(
    specs: list[VideoSpec], config: Config, store: ResultsStore
) -> None:
    """A hand-calibrated set does not need the sampled pass paid for again."""
    logs = run_evaluation(
        specs,
        load_config(str(DEFAULT_CONFIG_PATH)),
        store=store,
        tracker_factory=_tracker_factory,
        check_roi=False,
    )

    assert len(logs) == 2 * len(specs)
    # With mismatched geometry nothing is ever assigned, which is exactly why the
    # check exists: the runs succeed and measure nothing.
    assert all(
        entry["vehicle_count"] == 0
        for log in logs
        for record in log.frames
        for entry in record["approaches"].values()
    )


# ---------------------------------------------------------------------------
# Requirements 13.6, 16.4, 17.6 — the comparison table
# ---------------------------------------------------------------------------


def _read_table(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_table_has_one_row_per_video_controller_and_approach(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    """Requirements 13.5, 13.6."""
    logs = _evaluate(specs, config, store)

    path = write_comparison_table(logs, str(tmp_path / "tables"), video_specs=specs)
    rows = _read_table(path)

    assert list(rows[0]) == list(TABLE_COLUMNS)
    # Four approaches plus the aggregate row, per run.
    assert len(rows) == len(logs) * (len(APPROACH_NAMES) + 1)
    aggregates = [row for row in rows if row["approach"] == "ALL"]
    assert len(aggregates) == len(logs)
    for row in aggregates:
        assert row["run_id"]                       # Requirement 17.6
        assert row["complete"] == "yes"
        assert float(row["avg_queue_length"]) >= 0.0


def test_final_videos_are_reported_before_development_videos(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    """Requirement 16.4."""
    logs = _evaluate(specs, config, store)

    rows = _read_table(
        write_comparison_table(logs, str(tmp_path / "tables"), video_specs=specs)
    )

    sections = [row["section"] for row in rows]
    assert set(sections) == {"final", "development"}
    assert sections.index("final") < sections.index("development")
    assert sections == sorted(sections, key=lambda s: 0 if s == "final" else 1)


def test_a_video_without_a_spec_is_reported_as_unspecified(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    logs = _evaluate(specs[:1], config, store)

    rows = _read_table(write_comparison_table(logs, str(tmp_path / "tables")))

    assert {row["section"] for row in rows} == {"unspecified"}


def test_an_incomplete_run_is_marked_in_the_table_and_excluded_from_the_graphs(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    """Requirement 13.8."""
    logs = _evaluate(specs, config, store)
    logs[0].complete = False

    rows = _read_table(
        write_comparison_table(logs, str(tmp_path / "tables"), video_specs=specs)
    )

    marked = [row for row in rows if row["run_id"] == logs[0].run_id]
    assert marked and all(row["complete"] == "INCOMPLETE" for row in marked)
    assert len(comparable_logs(logs)) == len(logs) - 1
    # The graphs still draw, from the remaining complete runs.
    assert write_comparison_graphs(logs, str(tmp_path / "graphs"))


def test_tabulating_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="no run logs"):
        write_comparison_table([], str(tmp_path / "tables"))


# ---------------------------------------------------------------------------
# Requirement 13.7 — the comparison graphs
# ---------------------------------------------------------------------------


def test_one_graph_is_written_per_compared_metric(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    logs = _evaluate(specs, config, store)

    paths = write_comparison_graphs(logs, str(tmp_path / "graphs"))

    assert [Path(path).name for path in paths] == [
        "avg_waiting_time.png",
        "avg_queue_length.png",
        "max_queue_length.png",
        "throughput.png",
    ]
    for path in paths:
        assert Path(path).stat().st_size > 0


def test_graphing_only_incomplete_runs_is_an_error(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    logs = _evaluate(specs[:1], config, store)
    for log in logs:
        log.complete = False

    with pytest.raises(EvaluationError, match="no complete run logs"):
        write_comparison_graphs(logs, str(tmp_path / "graphs"))


# ---------------------------------------------------------------------------
# Requirement 14.7 — regeneration without decoding video
# ---------------------------------------------------------------------------


def test_artifacts_regenerate_from_run_logs_with_no_video_present(
    specs: list[VideoSpec],
    config: Config,
    store: ResultsStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 14.7: the Run_Log is sufficient, so the clips can be deleted."""
    _evaluate(specs, config, store)
    for spec in specs:
        Path(spec.path).unlink()

    # Any attempt to open a video from here on is a failure of the requirement.
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("regeneration decoded a video")

    monkeypatch.setattr(evaluation, "export_screenshots", forbidden)
    monkeypatch.setattr("cv2.VideoCapture", forbidden)

    table, graphs = regenerate_artifacts(
        store=store,
        table_dir=str(tmp_path / "tables"),
        graph_dir=str(tmp_path / "graphs"),
        video_specs=specs,
    )

    assert Path(table).is_file()
    assert len(graphs) == 4
    assert len(_read_table(table)) == 4 * (len(APPROACH_NAMES) + 1)


def test_regenerating_without_any_run_log_is_an_error(tmp_path: Path) -> None:
    empty = ResultsStore(str(tmp_path / "empty"))

    with pytest.raises(EvaluationError, match="no run logs found"):
        regenerate_artifacts(store=empty)


# ---------------------------------------------------------------------------
# Requirement 17 — report artefacts
# ---------------------------------------------------------------------------


def test_report_artifacts_cover_every_deliverable(
    specs: list[VideoSpec],
    config: Config,
    store: ResultsStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirements 17.1 to 17.6."""
    # Annotated videos are written relative to the working directory, so the run
    # happens inside tmp_path rather than in the repository's results/ tree.
    monkeypatch.chdir(tmp_path)
    logs = _evaluate(specs, config, store, write_videos=True)
    report_dir = tmp_path / "report"

    written = build_report_artifacts(
        logs,
        str(report_dir),
        table_dir=str(tmp_path / "tables"),
        graph_dir=str(tmp_path / "graphs"),
        video_dir="results/videos",
        video_specs=specs,
    )

    # 17.1: graphs and tables under report/.
    assert len(written["graphs"]) == 4
    assert len(written["tables"]) == 1
    assert all(Path(path).is_file() for paths in written.values() for path in paths)

    # 17.2: the architecture diagram naming every pipeline stage.
    architecture = (report_dir / "architecture.md").read_text(encoding="utf-8")
    for stage in (
        "Video_Ingestor",
        "Detector",
        "Tracker",
        "Approach_Assigner",
        "Metrics_Engine",
        "Score_Calculator",
        "Phase_Sequencer",
        "Signal_Overlay",
        "Evaluation_Harness",
    ):
        assert stage in architecture

    # 17.3: the literature table with a differs column for all five works.
    with (report_dir / "literature_comparison.csv").open(encoding="utf-8", newline="") as h:
        rows = list(csv.DictReader(h))
    assert [row["work"] for row in rows] == [
        "Raza 2025",
        "Saraff 2025",
        "YOLO-LIGHT 2026",
        "Jin 2024",
        "Lamrabet 2026",
    ]
    assert all(row["how this System differs"].strip() for row in rows)

    # 17.4 and 17.5: the contribution framing and the exclusions.
    contribution = (report_dir / "contribution.md").read_text(encoding="utf-8")
    assert "practical queue-aware extension" in contribution
    assert "measured evaluation" in contribution
    assert "Out of Scope" in contribution
    for excluded in ("Reinforcement learning", "SUMO", "License-plate", "GA-SGD"):
        assert excluded in contribution

    # 17.6: every numeric row names the run it was measured from.
    summary = (report_dir / "results_summary.md").read_text(encoding="utf-8")
    for log in logs:
        assert f"`{log.run_id}`" in summary


def test_report_screenshots_come_from_annotated_frames(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 17.1: screenshots are exported from the annotated videos."""
    monkeypatch.chdir(tmp_path)
    clips = [
        VideoSpec(
            path=str(
                write_synthetic_clip(
                    tmp_path / Path(spec.path).name,
                    frame_count=CLIP_FRAME_COUNT,
                    frame_rate=CLIP_FRAME_RATE,
                )
            ),
            role=spec.role,
            frame_rate=spec.frame_rate,
            resolution=spec.resolution,
            duration_seconds=spec.duration_seconds,
            visible_approaches=spec.visible_approaches,
        )
        for spec in specs
    ]
    logs = _evaluate(clips, config, store, write_videos=True)

    written = build_report_artifacts(
        logs,
        str(tmp_path / "report"),
        table_dir=str(tmp_path / "tables"),
        graph_dir=str(tmp_path / "graphs"),
        video_specs=clips,
    )

    assert written["screenshots"], "no screenshots were exported"
    assert all(path.endswith(".png") for path in written["screenshots"])


def test_screenshot_export_skips_an_absent_video(tmp_path: Path) -> None:
    assert export_screenshots(str(tmp_path / "absent.mp4"), str(tmp_path / "shots")) == []


def test_report_artifacts_need_at_least_one_log(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="no run logs"):
        build_report_artifacts([], str(tmp_path / "report"))


def test_the_results_summary_flags_an_excluded_run(
    specs: list[VideoSpec], config: Config, store: ResultsStore, tmp_path: Path
) -> None:
    logs = _evaluate(specs[:1], config, store)
    logs[0].complete = False

    summary = Path(write_results_summary(logs, str(tmp_path / "report"))).read_text(
        encoding="utf-8"
    )

    assert "INCOMPLETE" in summary
    assert "Excluded from the controller comparison" in summary


def test_the_literature_table_can_be_written_on_its_own(tmp_path: Path) -> None:
    path = write_literature_table(str(tmp_path / "report"))

    assert Path(path).name == "literature_comparison.csv"


# ---------------------------------------------------------------------------
# Requirement 15.6 — the evaluate entry point
# ---------------------------------------------------------------------------


def _write_cli_inputs(tmp_path: Path, config: Config) -> tuple[Path, Path]:
    """Write a two-video specification set and a configuration under ``tmp_path``."""
    videos = []
    for name, role in (("junction_dev.mp4", "development"), ("junction_final.mp4", "final")):
        clip = write_synthetic_clip(
            tmp_path / name, frame_count=CLIP_FRAME_COUNT, frame_rate=CLIP_FRAME_RATE
        )
        videos.append(
            {
                "path": str(clip),
                "role": role,
                "frame_rate": CLIP_FRAME_RATE,
                "resolution": [DEFAULT_CLIP_WIDTH, DEFAULT_CLIP_HEIGHT],
                "duration_seconds": CLIP_FRAME_COUNT / CLIP_FRAME_RATE,
                "visible_approaches": list(APPROACH_NAMES),
            }
        )
    specs_path = tmp_path / "videos.json"
    specs_path.write_text(json.dumps({"videos": videos}), encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")
    return specs_path, config_path


def test_evaluate_command_runs_the_experiment_and_writes_artifacts(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``python -m src.main evaluate`` (Requirements 15.6, 13.6, 13.7)."""
    monkeypatch.chdir(tmp_path)
    specs_path, config_path = _write_cli_inputs(tmp_path, config)
    _patch_trackers(monkeypatch, config)

    status = cli.main(
        [
            "evaluate",
            "--videos",
            str(specs_path),
            "--config",
            str(config_path),
            "--no-display",
            "--alpha-sweep",
            "0.0,1.0",
            "--report",
        ]
    )

    assert status == 0
    out = capsys.readouterr().out
    assert "runs           : 6 (6 complete)" in out
    assert list((tmp_path / "results" / "tables").glob("*.csv"))
    assert len(list((tmp_path / "results" / "graphs").glob("*.png"))) == 4
    assert len(list((tmp_path / "results" / "run_logs").glob("*.json"))) == 6
    assert (tmp_path / "report" / "architecture.md").is_file()
    assert (tmp_path / "report" / "results_summary.md").is_file()


def test_evaluate_graphs_only_regenerates_from_existing_logs(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 14.7 through the command line."""
    monkeypatch.chdir(tmp_path)
    specs_path, config_path = _write_cli_inputs(tmp_path, config)
    _patch_trackers(monkeypatch, config)
    assert (
        cli.main(
            [
                "evaluate",
                "--videos",
                str(specs_path),
                "--config",
                str(config_path),
                "--no-display",
            ]
        )
        == 0
    )
    capsys.readouterr()
    for clip in tmp_path.glob("*.mp4"):
        clip.unlink()

    status = cli.main(
        [
            "evaluate",
            "--graphs-only",
            "--videos",
            str(specs_path),
            "--config",
            str(config_path),
        ]
    )

    out = capsys.readouterr().out
    assert status == 0
    assert "run logs read  : 4" in out
    assert len(list((tmp_path / "results" / "graphs").glob("*.png"))) == 4


def test_evaluate_reports_a_missing_video_set_and_exits_nonzero(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 16.1's failure path, through the one top-level handler."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(to_json_obj(config)), encoding="utf-8")

    status = cli.main(
        ["evaluate", "--videos", str(tmp_path / "absent.json"), "--config", str(config_path)]
    )

    assert status == 1
    assert "video specification set not found" in capsys.readouterr().err


def test_a_malformed_alpha_sweep_is_reported(
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    specs_path, config_path = _write_cli_inputs(tmp_path, config)

    status = cli.main(
        [
            "evaluate",
            "--videos",
            str(specs_path),
            "--config",
            str(config_path),
            "--alpha-sweep",
            "0.2,high",
        ]
    )

    assert status == 1
    assert "--alpha-sweep" in capsys.readouterr().err
