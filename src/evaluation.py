"""Evaluation_Harness — measured comparison of adaptive against fixed-time.

Every number this module reports comes out of a Run_Log (Requirement 13.4). Nothing
here reads live pipeline state, and nothing here decodes video except
:func:`run_evaluation`, which is the one function whose job is to *produce* Run_Logs.
That split is what makes ``evaluate --graphs-only`` regenerate every table and graph
from files on disk (Requirement 14.7), and it is why every figure in the report can
name the ``run_id`` it was measured from (Requirement 17.6).

The metrics, and how each is derived from the frame records:

* **average Queue_Length** — the mean of an Approach's per-frame Queue_Length.
* **maximum Queue_Length** — the largest per-frame Queue_Length.
* **Vehicles_Served** — summed drops in Queue_Length on frames where the Approach is
  GREEN. A vehicle leaving a Queue_Region while its Approach is served shows up in
  the records as exactly that drop, so the count is a function of the log rather than
  of pipeline state carried alongside it.
* **average Waiting_Time** — per Approach, the accumulated queue-seconds spent not
  GREEN divided by the number of vehicles that joined that queue. Both terms are
  read off the Queue_Length series: a frame contributes ``queue_length * dt`` of
  waiting when the Approach is not GREEN, which is precisely what the Metrics_Engine
  accumulates per track, and a rise in Queue_Length is a vehicle joining. For the
  aggregate, the mean of the per-Track_ID accumulations recorded in the log is used
  instead, since that is the exact quantity Requirement 13.2 names.
* **Throughput** — Vehicles_Served per minute of simulated duration.
* **Processing_FPS** — frames processed divided by the run's wall-clock seconds,
  reported for adaptive runs (Requirement 13.3). It is a property of the run rather
  than of an Approach, so it appears on the aggregate only.

Phases flagged truncated are excluded from per-phase reporting (Requirement 11.4),
and a run whose ``complete`` flag is false is reported as incomplete and left out of
the controller comparison (Requirement 13.8).

Matplotlib is imported through the ``Agg`` backend, so graph generation works on a
headless machine and in a test run with no display.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")                       # before pyplot: headless by default
import matplotlib.pyplot as plt             # noqa: E402  (backend must be set first)

from src.config import APPROACH_NAMES, Config
from src.errors import EvaluationError
from src.lane_analysis import roi_coverage_report
from src.results_store import RUN_LOG_DIR, ResultsStore, RunLog

#: Where comparison tables and graphs are written, as the design fixes them.
TABLE_DIR = "results/tables"
GRAPH_DIR = "results/graphs"

#: Where the video specification set lives (Requirements 16.1-16.3).
VIDEO_SPEC_PATH = "data/annotations/videos.json"

#: The evaluation input set must hold between these many videos (Requirement 16.1).
MIN_VIDEOS = 2
MAX_VIDEOS = 5

#: The two roles a video may play in the study (Requirement 16.3). Development
#: videos are the ones ROIs and parameters were calibrated on; final videos are the
#: ones the reported comparison is measured on, and the two are reported separately
#: (Requirement 16.4) so a tuned-on-it-then-measured-on-it result cannot be passed
#: off as a held-out one.
VIDEO_ROLES = ("development", "final")

#: The name the aggregate row carries in tables and the metrics mapping.
AGGREGATE = "ALL"

#: Metrics the comparison graphs plot, as ``(field, axis label, title)``
#: (Requirement 13.7).
GRAPH_METRICS: tuple[tuple[str, str, str], ...] = (
    ("avg_waiting_time", "seconds", "Average waiting time"),
    ("avg_queue_length", "vehicles", "Average queue length"),
    ("max_queue_length", "vehicles", "Maximum queue length"),
    ("throughput", "vehicles per minute", "Throughput"),
)


# ---------------------------------------------------------------------------
# Metrics (Requirements 13.2 to 13.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationMetrics:
    """The measured outcome of one run, for one Approach or aggregated."""

    avg_waiting_time: float
    avg_queue_length: float
    max_queue_length: int
    vehicles_served: int
    throughput: float                  # vehicles per minute
    processing_fps: float | None       # adaptive runs only (Requirement 13.3)

    def as_json_obj(self) -> dict[str, Any]:
        """Return the JSON form the Run_Log and the tables carry."""
        return {
            "avg_waiting_time": float(self.avg_waiting_time),
            "avg_queue_length": float(self.avg_queue_length),
            "max_queue_length": int(self.max_queue_length),
            "vehicles_served": int(self.vehicles_served),
            "throughput": float(self.throughput),
            "processing_fps": (
                None if self.processing_fps is None else float(self.processing_fps)
            ),
        }

    @classmethod
    def from_json_obj(cls, obj: Mapping[str, Any]) -> "EvaluationMetrics":
        """Rebuild metrics from the JSON form, so a table can be redrawn from a log."""
        fps = obj.get("processing_fps")
        return cls(
            avg_waiting_time=float(obj.get("avg_waiting_time", 0.0)),
            avg_queue_length=float(obj.get("avg_queue_length", 0.0)),
            max_queue_length=int(obj.get("max_queue_length", 0)),
            vehicles_served=int(obj.get("vehicles_served", 0)),
            throughput=float(obj.get("throughput", 0.0)),
            processing_fps=None if fps is None else float(fps),
        )


def _states(log: RunLog, approach: str) -> list[str]:
    """The Signal_State series of one Approach, in frame order."""
    return [
        str(record.get("approaches", {}).get(approach, {}).get("signal_state", "RED"))
        for record in log.frames
    ]


def _queue_lengths(log: RunLog, approach: str) -> list[int]:
    """The Queue_Length series of one Approach, in frame order."""
    return [
        int(record.get("approaches", {}).get(approach, {}).get("queue_length", 0))
        for record in log.frames
    ]


def _approach_metrics(log: RunLog, approach: str) -> EvaluationMetrics:
    """Derive one Approach's metrics from the frame records alone (Req 13.4)."""
    queues = _queue_lengths(log, approach)
    states = _states(log, approach)
    frame_rate = log.frame_rate
    dt = 1.0 / frame_rate if frame_rate > 0 else 0.0
    duration_minutes = log.simulated_duration / 60.0

    if not queues:
        return EvaluationMetrics(0.0, 0.0, 0, 0, 0.0, None)

    # ``previous`` starts at 0, so the first frame's standing queue counts as that
    # many vehicles joining — they are waiting when the run begins, and pretending
    # they arrived from nowhere would divide the queue-seconds by too small a count
    # and overstate the average wait.
    served = 0
    joined = 0
    waiting_seconds = 0.0
    previous = 0
    for queue, state in zip(queues, states):
        if state != "GREEN":
            waiting_seconds += queue * dt
        if queue > previous:
            joined += queue - previous
        elif queue < previous and state == "GREEN":
            served += previous - queue
        previous = queue

    return EvaluationMetrics(
        avg_waiting_time=waiting_seconds / joined if joined else 0.0,
        avg_queue_length=sum(queues) / len(queues),
        max_queue_length=max(queues),
        vehicles_served=served,
        throughput=served / duration_minutes if duration_minutes > 0 else 0.0,
        processing_fps=None,
    )


def processing_fps(log: RunLog) -> float | None:
    """Return the run's Processing_FPS, or ``None`` when it was not measured.

    Reported for adaptive runs (Requirement 13.3). Returned for any run that
    recorded a wall-clock duration, because withholding a measured number would make
    the fixed-time baseline's cost unreportable — the harness decides what to publish,
    not this function.
    """
    if log.processing_seconds <= 0.0 or not log.frames:
        return None
    return log.frame_count / log.processing_seconds


def compute_metrics(log: RunLog) -> tuple[dict[str, EvaluationMetrics], EvaluationMetrics]:
    """Return per-Approach and aggregate metrics for ``log`` (Reqs 13.2, 13.4, 13.5).

    The aggregate's ``avg_waiting_time`` is the mean of the per-Track_ID Waiting_Time
    accumulations the run recorded, which is the exact quantity Requirement 13.2
    names. When a log carries no per-track accumulations — an older log, or a run in
    which nothing ever waited — it falls back to the mean of the per-Approach values,
    and reports 0.0 rather than nothing.
    """
    per_approach = {
        name: _approach_metrics(log, name) for name in log.approach_names
    }

    waits = list(log.waiting_times.values())
    if waits:
        avg_waiting_time = sum(waits) / len(waits)
    elif per_approach:
        avg_waiting_time = sum(m.avg_waiting_time for m in per_approach.values()) / len(
            per_approach
        )
    else:
        avg_waiting_time = 0.0

    served = sum(m.vehicles_served for m in per_approach.values())
    duration_minutes = log.simulated_duration / 60.0
    aggregate = EvaluationMetrics(
        avg_waiting_time=avg_waiting_time,
        avg_queue_length=(
            sum(m.avg_queue_length for m in per_approach.values()) / len(per_approach)
            if per_approach
            else 0.0
        ),
        max_queue_length=max((m.max_queue_length for m in per_approach.values()), default=0),
        vehicles_served=served,
        throughput=served / duration_minutes if duration_minutes > 0 else 0.0,
        processing_fps=processing_fps(log),
    )
    return per_approach, aggregate


def metrics_json(log: RunLog) -> dict[str, Any]:
    """Return the metrics of ``log`` in the form the Run_Log records them.

    Also carries the phase summary, which is where Requirement 11.4's exclusion of a
    truncated phase becomes visible: the phase count the averages were taken over is
    the completed count, and the truncated one is reported separately rather than
    silently dropped.
    """
    per_approach, aggregate = compute_metrics(log)
    completed = log.completed_phases
    green_phases = [phase for phase in completed if phase.get("state") == "GREEN"]
    return {
        "approaches": {name: m.as_json_obj() for name, m in per_approach.items()},
        "aggregate": aggregate.as_json_obj(),
        "phases": {
            "recorded": len(log.phases),
            "completed": len(completed),
            "truncated": len(log.phases) - len(completed),
            "avg_green_time": (
                sum(float(phase.get("green_time", 0.0)) for phase in green_phases)
                / len(green_phases)
                if green_phases
                else 0.0
            ),
            # Counted from the phases rather than from ``log.starvation_overrides``,
            # so the number holds for any log carrying phases — the override list is
            # a projection of the same phases and would only be a second thing able
            # to disagree.
            "starvation_overrides": sum(
                1
                for phase in log.phases
                if phase.get("starvation_override") and phase.get("state") == "GREEN"
            ),
        },
        "frames_processed": log.frame_count,
        "simulated_duration": log.simulated_duration,
    }


def read_metrics(log: RunLog) -> tuple[dict[str, EvaluationMetrics], EvaluationMetrics]:
    """Return ``log``'s metrics, preferring the ones it already recorded.

    A finished Run_Log carries its own Evaluation_Metrics, and a table drawn from it
    must show those rather than a freshly computed set that could differ if this
    module's derivation changes. A log that has none — one still being written — is
    computed from its frame records.
    """
    recorded = log.evaluation_metrics
    if isinstance(recorded, Mapping) and "aggregate" in recorded:
        approaches = {
            name: EvaluationMetrics.from_json_obj(obj)
            for name, obj in dict(recorded.get("approaches", {})).items()
        }
        return approaches, EvaluationMetrics.from_json_obj(recorded["aggregate"])
    return compute_metrics(log)


# ---------------------------------------------------------------------------
# The video specification set (Requirements 16.1 to 16.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoSpec:
    """One junction video and its role in the study."""

    path: str
    role: str                          # "development" | "final"
    frame_rate: float
    resolution: tuple[int, int]
    duration_seconds: float
    visible_approaches: tuple[str, ...]

    @property
    def stem(self) -> str:
        """The video's filename stem, as tables and Run_Log ids use it."""
        return Path(self.path).stem or "video"

    def as_json_obj(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "frame_rate": float(self.frame_rate),
            "resolution": [int(self.resolution[0]), int(self.resolution[1])],
            "duration_seconds": float(self.duration_seconds),
            "visible_approaches": list(self.visible_approaches),
        }

    @classmethod
    def from_json_obj(cls, obj: Mapping[str, Any]) -> "VideoSpec":
        """Build one spec, naming any absent or unusable field (Requirement 16.2)."""
        if not isinstance(obj, Mapping):
            raise EvaluationError(
                f"each video specification must be a JSON object, got {type(obj).__name__}"
            )
        for key in ("path", "role", "frame_rate", "resolution", "duration_seconds"):
            if key not in obj:
                raise EvaluationError(
                    f"video specification is missing required key {key!r}: {dict(obj)}"
                )

        role = str(obj["role"])
        if role not in VIDEO_ROLES:
            raise EvaluationError(
                f"video {obj['path']!r} has role {role!r}; expected one of "
                f"{', '.join(VIDEO_ROLES)}"
            )

        resolution = obj["resolution"]
        if not isinstance(resolution, Sequence) or len(resolution) != 2:
            raise EvaluationError(
                f"video {obj['path']!r} resolution must be [width, height], got {resolution!r}"
            )

        approaches = tuple(str(name) for name in obj.get("visible_approaches", APPROACH_NAMES))
        unknown = [name for name in approaches if name not in APPROACH_NAMES]
        if unknown:
            raise EvaluationError(
                f"video {obj['path']!r} names unknown approach(es) {', '.join(unknown)}"
            )

        frame_rate = float(obj["frame_rate"])
        duration = float(obj["duration_seconds"])
        for label, value in (("frame_rate", frame_rate), ("duration_seconds", duration)):
            if not math.isfinite(value) or value <= 0.0:
                raise EvaluationError(
                    f"video {obj['path']!r} {label} must be a finite value > 0, got {value!r}"
                )

        return cls(
            path=str(obj["path"]),
            role=role,
            frame_rate=frame_rate,
            resolution=(int(resolution[0]), int(resolution[1])),
            duration_seconds=duration,
            visible_approaches=approaches,
        )


def load_video_specs(path: str = VIDEO_SPEC_PATH) -> list[VideoSpec]:
    """Load and validate the evaluation input set (Requirements 16.1 to 16.3).

    The set must hold between :data:`MIN_VIDEOS` and :data:`MAX_VIDEOS` entries, and
    the error names the count found (Requirement 16.1) — a study run over one video
    cannot support a controller comparison, and one run over twenty is not what this
    project's timeline allows, so both ends are a configuration mistake worth
    stopping for.
    """
    spec_path = Path(path)
    try:
        with spec_path.open("r", encoding="utf-8") as handle:
            obj = json.load(handle)
    except FileNotFoundError as exc:
        raise EvaluationError(
            f"video specification set not found: {spec_path}; record 2 to 5 junction "
            f"videos there before running an evaluation"
        ) from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(
            f"video specification set is not valid JSON: {spec_path}: {exc}"
        ) from exc

    entries = obj.get("videos") if isinstance(obj, Mapping) else obj
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise EvaluationError(
            f"video specification set {spec_path} must hold a list of videos under "
            f"the key 'videos'"
        )

    specs = [VideoSpec.from_json_obj(entry) for entry in entries]
    if not MIN_VIDEOS <= len(specs) <= MAX_VIDEOS:
        raise EvaluationError(
            f"video specification set {spec_path} holds {len(specs)} video(s); this "
            f"study requires between {MIN_VIDEOS} and {MAX_VIDEOS}"
        )

    duplicates = {spec.path for spec in specs if [s.path for s in specs].count(spec.path) > 1}
    if duplicates:
        raise EvaluationError(
            f"video specification set {spec_path} lists {', '.join(sorted(duplicates))} "
            f"more than once"
        )
    return specs


# ---------------------------------------------------------------------------
# The comparison run (Requirements 13.1, 13.8, 13.9)
# ---------------------------------------------------------------------------


def run_evaluation(
    video_specs: Sequence[VideoSpec],
    config: Config,
    *,
    alphas: Sequence[float] | None = None,
    detector_factory: Callable[[Config], Any] | None = None,
    tracker_factory: Callable[[Config], Any] | None = None,
    store: ResultsStore | None = None,
    write_videos: bool = False,
    display: bool = False,
    check_roi: bool = True,
    excluded: list[str] | None = None,
) -> list[RunLog]:
    """Run both controllers over every video and return the Run_Logs (Req 13.1).

    Per video: one Fixed_Time run, then one Adaptive run per Alpha in ``alphas``
    (defaulting to the configured Alpha alone). Every run reuses the *same*
    :class:`~src.config.Config` for ROI polygons, queue regions, detector, and
    tracker — only ``alpha`` is varied, and only for the adaptive runs — so the
    controller is the one thing that differs between the two runs being compared
    (Requirement 13.1). An Alpha sweep produces one Run_Log per Alpha value
    (Requirement 13.9).

    Detection and tracking are re-run per controller rather than cached. Given the
    same frames they produce the same tracks, so the runs stay comparable, and a
    cache would be one more thing able to make them silently incomparable.

    ``detector_factory`` and ``tracker_factory`` are the injection seams the tests
    use to run the whole harness with no weights and no GPU; each is called once per
    run so the two runs do not share tracker state.

    A run that ends before the video's final frame is marked incomplete in its own
    Run_Log (Requirement 13.8); it is still returned, because the table reports it as
    incomplete rather than pretending it did not happen.

    ``check_roi`` samples each video first and drops it from the input set when a
    configured ROI sees no traffic at all, reporting why (Requirement 16.5). The
    exclusion messages are appended to ``excluded`` when one is supplied, so the
    caller can print them; the check is skipped for a video only by passing
    ``check_roi=False``, which is what a caller does when it has already calibrated
    and verified the geometry by hand.
    """
    # Imported here rather than at module scope: the Pipeline lives in ``src.main``
    # with the CLI it serves (Requirement 15.7), and ``src.main`` imports this module
    # for its ``evaluate`` command. One of the two directions has to be deferred, and
    # this is the one that runs once per evaluation rather than once per frame.
    from src.main import Pipeline

    if not video_specs:
        raise EvaluationError("no videos to evaluate")

    alpha_values = tuple(alphas) if alphas else (config.alpha,)
    for alpha in alpha_values:
        if not 0.0 <= float(alpha) <= 1.0:
            raise EvaluationError(f"alpha must lie in 0..1, got {alpha!r}")

    results_store = store or ResultsStore()
    logs: list[RunLog] = []
    exclusions = excluded if excluded is not None else []

    for spec in video_specs:
        if check_roi:
            report = roi_coverage_report(
                spec.path,
                config,
                detector=detector_factory(config) if detector_factory else None,
                tracker=tracker_factory(config) if tracker_factory else None,
            )
            message = report.mismatch_message()
            if message is not None:
                exclusions.append(message)
                continue

        plan: list[tuple[str, float]] = [("fixed", config.alpha)]
        plan.extend(("adaptive", float(alpha)) for alpha in alpha_values)

        for controller_name, alpha in plan:
            run_config = config if alpha == config.alpha else _with_alpha(config, alpha)
            pipeline = Pipeline(
                spec.path,
                run_config,
                controller_name,
                detector=detector_factory(run_config) if detector_factory else None,
                tracker=tracker_factory(run_config) if tracker_factory else None,
                store=results_store,
                write_video=write_videos,
                display=display,
            )
            logs.append(pipeline.run().log)

    if not logs:
        raise EvaluationError(
            "every video was excluded because the configured ROI polygons did not "
            "match it (Requirement 16.5): "
            + "; ".join(exclusions)
        )
    return logs


def _with_alpha(config: Config, alpha: float) -> Config:
    """Return ``config`` with a different Alpha, everything else identical.

    Used by the sweep so that the geometry, the detector, and the tracker of a swept
    run are provably the same objects as the baseline's (Requirement 13.9).
    """
    import dataclasses

    return dataclasses.replace(config, alpha=float(alpha))


def comparable_logs(logs: Iterable[RunLog]) -> list[RunLog]:
    """Return the logs the controller comparison may use (Requirement 13.8)."""
    return [log for log in logs if log.complete]


# ---------------------------------------------------------------------------
# Comparison table (Requirements 13.6, 16.4, 17.6)
# ---------------------------------------------------------------------------

#: Columns of the comparison table, in order. ``run_id`` is first after the
#: identifying columns so every numeric cell on the row is traceable to the run it
#: was measured from (Requirement 17.6).
TABLE_COLUMNS: tuple[str, ...] = (
    "section",
    "video",
    "controller",
    "alpha",
    "approach",
    "run_id",
    "complete",
    "avg_waiting_time",
    "avg_queue_length",
    "max_queue_length",
    "vehicles_served",
    "throughput",
    "processing_fps",
    "frames_processed",
    "simulated_duration",
)


def _role_of(log: RunLog, specs: Mapping[str, str]) -> str:
    """Return the section a log belongs to: its video's role, or ``unspecified``."""
    stem = Path(log.video_path).stem
    return specs.get(stem, specs.get(log.video_path, "unspecified"))


def _table_rows(
    logs: Sequence[RunLog], specs: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Build the table's rows: one per video, controller, alpha, and Approach.

    Requirement 13.6 asks for one row per video and controller pair; Requirement 13.5
    asks for metrics per Approach *and* aggregated. Both are served by carrying an
    ``approach`` column whose ``ALL`` value is the aggregate row — the row a reader
    comparing two controllers wants — with the four per-Approach rows beside it.
    """
    rows: list[dict[str, Any]] = []
    for log in logs:
        per_approach, aggregate = read_metrics(log)
        section = _role_of(log, specs)
        for approach, metrics in (
            *((name, per_approach[name]) for name in log.approach_names if name in per_approach),
            (AGGREGATE, aggregate),
        ):
            row: dict[str, Any] = {
                "section": section,
                "video": Path(log.video_path).name,
                "controller": log.controller_name,
                "alpha": f"{log.alpha:.2f}",
                "approach": approach,
                "run_id": log.run_id,
                "complete": "yes" if log.complete else "INCOMPLETE",
                "frames_processed": log.frame_count,
                "simulated_duration": f"{log.simulated_duration:.3f}",
            }
            values = metrics.as_json_obj()
            row["avg_waiting_time"] = f"{values['avg_waiting_time']:.3f}"
            row["avg_queue_length"] = f"{values['avg_queue_length']:.3f}"
            row["max_queue_length"] = values["max_queue_length"]
            row["vehicles_served"] = values["vehicles_served"]
            row["throughput"] = f"{values['throughput']:.3f}"
            row["processing_fps"] = (
                "" if values["processing_fps"] is None else f"{values['processing_fps']:.2f}"
            )
            rows.append(row)
    return rows


def _section_key(row: Mapping[str, Any]) -> tuple[int, str, str, str, int]:
    """Sort key placing final-evaluation videos before development ones (Req 16.4)."""
    section_rank = {"final": 0, "development": 1}.get(str(row["section"]), 2)
    approach_rank = (
        len(APPROACH_NAMES)
        if row["approach"] == AGGREGATE
        else APPROACH_NAMES.index(str(row["approach"]))
        if row["approach"] in APPROACH_NAMES
        else len(APPROACH_NAMES) + 1
    )
    return (
        section_rank,
        str(row["video"]),
        str(row["controller"]),
        str(row["alpha"]),
        approach_rank,
    )


def write_comparison_table(
    logs: Sequence[RunLog],
    out_dir: str = TABLE_DIR,
    *,
    video_specs: Sequence[VideoSpec] | None = None,
    filename: str | None = None,
) -> str:
    """Write the comparison table as CSV and return its path (Requirement 13.6).

    Rows are grouped so the final-evaluation videos are reported before the
    development ones (Requirement 16.4), and an incomplete run's rows carry
    ``INCOMPLETE`` in the ``complete`` column so it is visible in the table while
    being excluded from the comparison (Requirement 13.8).

    CSV rather than a formatted table: the report needs the numbers pasted into a
    document, and a spreadsheet-readable file survives that trip better than
    pre-rendered column alignment.
    """
    if not logs:
        raise EvaluationError("no run logs to tabulate")

    specs = {spec.stem: spec.role for spec in (video_specs or ())}
    rows = sorted(_table_rows(logs, specs), key=_section_key)

    out_path = Path(out_dir) / (
        filename or f"comparison__{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TABLE_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return str(out_path)


# ---------------------------------------------------------------------------
# Comparison graphs (Requirement 13.7)
# ---------------------------------------------------------------------------


def _series_label(log: RunLog) -> str:
    """Legend label of one run: the controller, with its Alpha when it matters."""
    if log.controller_name == "fixed":
        return "fixed"
    return f"adaptive a={log.alpha:.2f}"


def write_comparison_graphs(
    logs: Sequence[RunLog], out_dir: str = GRAPH_DIR
) -> list[str]:
    """Write one grouped bar chart per metric and return the paths (Req 13.7).

    Grouped by video along the x axis with one bar per controller — and one per Alpha
    when a sweep was run — which is the comparison the report makes. Only complete
    runs are plotted (Requirement 13.8); an incomplete run is reported in the table
    instead, because a bar drawn from a partial run is indistinguishable from a bar
    drawn from a full one.

    Reads Run_Logs only, so this runs with no video present (Requirement 14.7).
    """
    complete = comparable_logs(logs)
    if not complete:
        raise EvaluationError(
            "no complete run logs to plot; every run either failed or was cut short"
        )

    videos = sorted({Path(log.video_path).name for log in complete})
    labels = sorted({_series_label(log) for log in complete})
    metrics_by_run = {log.run_id: read_metrics(log)[1] for log in complete}

    out_paths: list[str] = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    for field_name, axis_label, title in GRAPH_METRICS:
        figure, axes = plt.subplots(figsize=(2.5 + 1.6 * len(videos), 4.2))
        width = 0.8 / max(1, len(labels))

        for index, label in enumerate(labels):
            heights: list[float] = []
            for video in videos:
                matching = [
                    metrics_by_run[log.run_id]
                    for log in complete
                    if Path(log.video_path).name == video and _series_label(log) == label
                ]
                value = (
                    sum(getattr(m, field_name) for m in matching) / len(matching)
                    if matching
                    else 0.0
                )
                heights.append(float(value))
            positions = [
                position + index * width - 0.4 + width / 2
                for position in range(len(videos))
            ]
            axes.bar(positions, heights, width=width, label=label)

        axes.set_xticks(range(len(videos)))
        axes.set_xticklabels(videos, rotation=15, ha="right")
        axes.set_ylabel(axis_label)
        axes.set_title(f"{title}: adaptive against fixed-time")
        axes.legend()
        axes.grid(axis="y", alpha=0.3)
        figure.tight_layout()

        out_path = Path(out_dir) / f"{field_name}.png"
        figure.savefig(out_path, dpi=150)
        plt.close(figure)
        out_paths.append(str(out_path))

    return out_paths


def regenerate_artifacts(
    log_paths: Sequence[str] | None = None,
    *,
    store: ResultsStore | None = None,
    table_dir: str = TABLE_DIR,
    graph_dir: str = GRAPH_DIR,
    video_specs: Sequence[VideoSpec] | None = None,
) -> tuple[str, list[str]]:
    """Rebuild the table and graphs from Run_Logs on disk (Requirement 14.7).

    No video is opened and no frame is decoded: this is what ``--graphs-only`` runs,
    and it is the check that the Run_Log really is sufficient to reproduce every
    reported number.
    """
    results_store = store or ResultsStore(RUN_LOG_DIR)
    paths = list(log_paths) if log_paths else results_store.find_logs()
    if not paths:
        raise EvaluationError(
            f"no run logs found under {results_store.out_dir}; run an evaluation before "
            f"regenerating tables and graphs"
        )
    logs = results_store.read_all(paths)
    table = write_comparison_table(logs, table_dir, video_specs=video_specs)
    graphs = write_comparison_graphs(logs, graph_dir)
    return table, graphs


# ---------------------------------------------------------------------------
# Report artefacts (Requirement 17)
# ---------------------------------------------------------------------------

#: Where report artefacts are written.
REPORT_DIR = "report"

#: How many demonstration screenshots are exported per annotated video (Req 17.1).
SCREENSHOTS_PER_VIDEO = 3

#: The literature this project positions itself against (Requirement 17.3). The
#: "differs" column is the point of the table: each row states what this System does
#: *instead*, which is also the list of things this project deliberately does not
#: claim.
LITERATURE: tuple[dict[str, str], ...] = (
    {
        "work": "Raza 2025",
        "focus": "Reinforcement-learning signal control trained in simulation",
        "differs": (
            "This System uses no learning of any kind: allocation is a deterministic "
            "function of measured density and queue length, so a decision can be "
            "explained from the frame it was made on. It is driven by junction video "
            "rather than a simulator."
        ),
    },
    {
        "work": "Saraff 2025",
        "focus": "Density-only adaptive control from vehicle counts",
        "differs": (
            "This System adds tracked Queue_Length as a second measured term and "
            "weights the two with a tunable Alpha, which is the queue-aware extension "
            "under study; Alpha = 1 reproduces the density-only baseline exactly, so "
            "the comparison is measurable rather than asserted."
        ),
    },
    {
        "work": "YOLO-LIGHT 2026",
        "focus": "A custom lightweight detector architecture for edge deployment",
        "differs": (
            "This System trains no detector and proposes no architecture: it consumes "
            "pretrained Ultralytics YOLO weights unchanged. No edge-hardware or "
            "latency claim is made; Processing_FPS is reported as measured on a "
            "desktop CPU or GPU."
        ),
    },
    {
        "work": "Jin 2024",
        "focus": "Graph-neural network traffic-state prediction across a network",
        "differs": (
            "This System predicts nothing and models a single isolated junction. Its "
            "control input is the current frame's measurement, not a forecast, and no "
            "network-level coordination is attempted."
        ),
    },
    {
        "work": "Lamrabet 2026",
        "focus": "Evolutionary (GA-SGD) optimization of controller hyperparameters",
        "differs": (
            "This System's parameters are calibrated by hand against observed footage "
            "and recorded in config/default.json; the only sweep performed is over "
            "Alpha, reported per value, so each result stays traceable to the "
            "configuration that produced it."
        ),
    },
)

#: The framing Requirement 17.4 asks for, shared verbatim by ``report/`` and the
#: README so the two cannot drift.
CONTRIBUTION_TEXT = """\
## Contribution

The contribution of this project is a **practical queue-aware extension** to
vision-based adaptive signal allocation, together with its **measured evaluation**
against a fixed-time baseline on the same junction videos. It is not a globally
novel algorithm, and no claim of one is made.

Concretely, the per-approach priority is

    Score_i = alpha * D_i + (1 - alpha) * Q_i

where `D_i` is normalized vehicle density and `Q_i` is normalized queue length,
both measured from tracked vehicles, and `alpha` is a configured experimental
weight. Setting `alpha = 1` reduces the System to density-only allocation, which is
what makes the queue term's effect measurable rather than assumed: the same
pipeline, the same videos, and the same detector and tracker configuration are used
for every run, and only the controller and `alpha` differ.

Every number reported in this directory is derived from a Run_Log written under
`results/run_logs/` and is tagged with the `run_id` it was measured from. Nothing is
estimated, extrapolated, or carried over from a run that did not complete.
"""

#: The exclusions Requirement 17.5 asks for, taken from the Out of Scope section of
#: the requirements document.
OUT_OF_SCOPE_TEXT = """\
## Out of Scope

The following are explicitly excluded. This project does not implement, depend on,
or claim any of them:

- Reinforcement learning of any kind, including DQN, multi-agent RL, and
  policy-gradient control
- Graph convolutional networks and other graph-neural traffic prediction
- Jetson or other edge-hardware deployment, and IoT sensor nodes
- SUMO or any microscopic traffic simulator as the primary system; the primary
  system is video-driven
- GA-SGD or other evolutionary hyperparameter optimization
- A custom YOLO-LIGHT style architecture, or any custom detector training
- Emergency-vehicle detection and priority pre-emption
- License-plate recognition
- Drone or UAV based monitoring
- Control of real traffic-signal hardware
- Large-scale dataset construction or public dataset release
"""

#: The architecture diagram of Requirement 17.2, emitted as Mermaid source so it
#: renders in the report document and stays diffable in version control.
ARCHITECTURE_DIAGRAM = """\
# Architecture

Pipeline stages, from video input to evaluation (Requirement 17.2).

```mermaid
flowchart TD
    A[Junction video<br/>videos/*.mp4] --> B[Video_Ingestor<br/>src/video_io.py]
    C[Configuration<br/>config/default.json] --> D[Config_Loader<br/>src/config.py]
    B -->|frame, frame_index| E[Detector<br/>YOLO, src/detection.py]
    E -->|detections| F[Tracker<br/>ByteTrack, src/tracking.py]
    F -->|tracks with Track_IDs| G[Approach_Assigner<br/>src/lane_analysis.py]
    D -->|ROI polygons, queue regions| G
    G -->|assigned tracks| H[Metrics_Engine<br/>src/traffic_metrics.py]
    H -->|density, queue length| I[Score_Calculator<br/>Score = a*D + '1-a'*Q]
    D -->|alpha| I
    I -->|four scores| J[Controller<br/>fixed-time or adaptive]
    J -->|approach, green time| K[Phase_Sequencer<br/>src/signal_controller.py]
    K -->|signal states| H
    K --> L[Signal_Overlay<br/>src/overlay.py]
    H --> L
    L --> M[Annotated video<br/>results/videos/]
    H --> N[Results_Store<br/>src/results_store.py]
    K --> N
    N --> O[Run_Logs<br/>results/run_logs/]
    O --> P[Evaluation_Harness<br/>src/evaluation.py]
    P --> Q[Tables<br/>results/tables/]
    P --> R[Graphs<br/>results/graphs/]
    Q --> S[report/]
    R --> S
    M --> S
```

The only feedback edge is Phase_Sequencer back into Metrics_Engine: Waiting_Time is
held while an approach is GREEN, so the measurement stage needs the signal state in
force on the frame it is measuring. Everything else flows forward.
"""


def export_screenshots(
    video_path: str,
    out_dir: str,
    *,
    count: int = SCREENSHOTS_PER_VIDEO,
    prefix: str | None = None,
) -> list[str]:
    """Export evenly spaced frames of an annotated video as PNGs (Req 17.1).

    Reads an *annotated* video from ``results/videos/`` — a frame that already
    carries the overlay — so a screenshot in the report shows exactly what the demo
    shows. Returns the paths written, and an empty list when the video is absent or
    unreadable, because a missing demonstration video is a reason to skip
    screenshots rather than to fail an evaluation that has already produced its
    numbers.
    """
    import cv2                       # local: report building is not a per-frame path

    source = Path(video_path)
    if not source.is_file():
        return []

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        return []

    try:
        frames: list[Any] = []
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            frames.append(frame)
    finally:
        capture.release()

    if not frames:
        return []

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = prefix or source.stem
    step = max(1, len(frames) // max(1, count))
    written: list[str] = []
    for index in range(min(count, len(frames))):
        frame = frames[min(index * step, len(frames) - 1)]
        out_path = Path(out_dir) / f"{stem}__frame{index + 1}.png"
        if cv2.imwrite(str(out_path), frame):
            written.append(str(out_path))
    return written


def write_literature_table(out_dir: str = REPORT_DIR) -> str:
    """Write the literature-comparison table as CSV (Requirement 17.3)."""
    out_path = Path(out_dir) / "literature_comparison.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["work", "focus", "how this System differs"]
        )
        writer.writeheader()
        for entry in LITERATURE:
            writer.writerow(
                {
                    "work": entry["work"],
                    "focus": entry["focus"],
                    "how this System differs": entry["differs"],
                }
            )
    return str(out_path)


def write_results_summary(logs: Sequence[RunLog], out_dir: str = REPORT_DIR) -> str:
    """Write the report's headline numbers, each tagged with its ``run_id``.

    Requirement 17.6 in its strictest reading: every numeric cell here sits on a row
    that names the Run_Log it came from, so a figure quoted in the written report can
    be traced back to one file under ``results/run_logs/``. Incomplete runs appear
    with their flag rather than being dropped, which is what stops an excluded run
    from quietly becoming an unexplained gap in the table (Requirement 13.8).
    """
    out_path = Path(out_dir) / "results_summary.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Measured results",
        "",
        "Every value below is derived from the Run_Log named in its row "
        "(Requirement 17.6). Aggregate figures over all four approaches.",
        "",
        "| video | controller | alpha | avg wait (s) | avg queue | max queue "
        "| served | throughput (veh/min) | fps | complete | run_id |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for log in logs:
        _approaches, aggregate = read_metrics(log)
        fps = "-" if aggregate.processing_fps is None else f"{aggregate.processing_fps:.1f}"
        lines.append(
            f"| {Path(log.video_path).name} | {log.controller_name} | {log.alpha:.2f} "
            f"| {aggregate.avg_waiting_time:.2f} | {aggregate.avg_queue_length:.2f} "
            f"| {aggregate.max_queue_length} | {aggregate.vehicles_served} "
            f"| {aggregate.throughput:.2f} | {fps} "
            f"| {'yes' if log.complete else 'INCOMPLETE'} | `{log.run_id}` |"
        )

    incomplete = [log for log in logs if not log.complete]
    if incomplete:
        lines += [
            "",
            "## Excluded from the controller comparison",
            "",
            "These runs ended before the final frame and are reported but not compared "
            "(Requirement 13.8):",
            "",
        ]
        lines += [f"- `{log.run_id}` ({Path(log.video_path).name})" for log in incomplete]

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def build_report_artifacts(
    logs: Sequence[RunLog],
    report_dir: str = REPORT_DIR,
    *,
    table_dir: str = TABLE_DIR,
    graph_dir: str = GRAPH_DIR,
    video_dir: str = "results/videos",
    video_specs: Sequence[VideoSpec] | None = None,
) -> dict[str, list[str]]:
    """Assemble everything Requirement 17 asks for under ``report/``.

    Copies the comparison graphs and tables in, exports demonstration screenshots
    from the annotated videos of the runs (Requirement 17.1), writes the architecture
    diagram source (Requirement 17.2), the literature-comparison table
    (Requirement 17.3), the shared contribution and Out of Scope text
    (Requirements 17.4, 17.5), and the run-tagged results summary
    (Requirement 17.6).

    Returns what was written, grouped by kind, so a caller can report it rather than
    inspecting the directory afterwards.
    """
    if not logs:
        raise EvaluationError("no run logs to build report artefacts from")

    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, list[str]] = {
        "tables": [],
        "graphs": [],
        "screenshots": [],
        "documents": [],
    }

    # Tables and graphs are regenerated into results/ first, so the copies in report/
    # are never staler than the Run_Logs they came from.
    table = write_comparison_table(logs, table_dir, video_specs=video_specs)
    graphs = write_comparison_graphs(logs, graph_dir)

    for source, kind in ((table, "tables"), *((graph, "graphs") for graph in graphs)):
        target = destination / Path(source).name
        shutil.copyfile(source, target)
        written[kind].append(str(target))

    for log in logs:
        stem = Path(log.video_path).stem
        for candidate in sorted(Path(video_dir).glob(f"{stem}*.mp4")) if Path(video_dir).is_dir() else []:
            written["screenshots"].extend(
                export_screenshots(str(candidate), str(destination / "screenshots"))
            )

    architecture = destination / "architecture.md"
    architecture.write_text(ARCHITECTURE_DIAGRAM, encoding="utf-8")
    written["documents"].append(str(architecture))

    contribution = destination / "contribution.md"
    contribution.write_text(
        f"{CONTRIBUTION_TEXT}\n{OUT_OF_SCOPE_TEXT}", encoding="utf-8"
    )
    written["documents"].append(str(contribution))

    written["documents"].append(write_literature_table(str(destination)))
    written["documents"].append(write_results_summary(logs, str(destination)))

    return written


__all__ = [
    "AGGREGATE",
    "ARCHITECTURE_DIAGRAM",
    "CONTRIBUTION_TEXT",
    "GRAPH_DIR",
    "GRAPH_METRICS",
    "LITERATURE",
    "MAX_VIDEOS",
    "MIN_VIDEOS",
    "OUT_OF_SCOPE_TEXT",
    "REPORT_DIR",
    "SCREENSHOTS_PER_VIDEO",
    "TABLE_COLUMNS",
    "TABLE_DIR",
    "VIDEO_ROLES",
    "VIDEO_SPEC_PATH",
    "EvaluationMetrics",
    "VideoSpec",
    "build_report_artifacts",
    "comparable_logs",
    "compute_metrics",
    "export_screenshots",
    "load_video_specs",
    "metrics_json",
    "processing_fps",
    "read_metrics",
    "regenerate_artifacts",
    "run_evaluation",
    "write_comparison_graphs",
    "write_comparison_table",
    "write_literature_table",
    "write_results_summary",
]
