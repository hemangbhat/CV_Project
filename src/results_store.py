"""Results_Store — the Run_Log is the single source of truth for a run.

Every number this project reports in a table, a graph, or the report is derived
from a Run_Log written here, never from live pipeline state. That is a deliberate
constraint rather than a convenience: it makes ``--graphs-only`` regeneration
without decoding video fall out for free (Requirement 14.7), it makes every
reported figure traceable to one ``run_id`` (Requirement 17.6), and it means a
result can be re-derived months later from a file in ``results/run_logs/`` when the
video, the weights, and the machine are gone.

What one Run_Log holds:

* the resolved configuration, the input video path, and the controller name, all
  recorded at run start (Requirement 14.3)
* one record per processed frame carrying Vehicle_Count, Queue_Length, Score, and
  Signal_State for each of the four Approaches (Requirement 14.6)
* the warnings a run raised, the frame-rate substitution among them (Req 1.5)
* every ROI overlap event (Requirement 4.4) and every starvation override
  (Requirement 9.6)
* every phase with its ``truncated`` flag, so per-phase averages can exclude a
  phase the video cut short (Requirement 11.4)
* the Evaluation_Metrics computed at the end, and whether the run reached the final
  frame at all (Requirement 13.8)

Serialization is plain ``json`` from the standard library, so no dependency is
added (Requirement 15.8). Floats go through Python's own repr, which round-trips
exactly — comfortably inside the 0.001 tolerance Requirement 14.4 asks for, and the
reason :meth:`ResultsStore.read` needs no tolerance handling of its own.

Files are written compactly rather than pretty-printed. A three-minute clip at 25
fps produces about 4,500 frame records of four Approaches each; indentation would
roughly double a file nothing reads by eye. The per-run summary the report quotes
comes from :mod:`src.evaluation`, not from squinting at this JSON.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.config import APPROACH_NAMES, Config, to_json_obj
from src.errors import EvaluationError
from src.traffic_metrics import FrameMeasurement
from src.video_io import VideoInfo

#: Where Run_Logs are written, as the design fixes it.
RUN_LOG_DIR = "results/run_logs"

#: Timestamp format used in a ``run_id``. Sortable, filename-safe on Windows — no
#: colons — and second resolution, which is enough to separate two runs of the same
#: video, controller, and Alpha.
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

#: The Run_Log format this module writes and reads. Recorded in every file so a
#: later reader can tell whether it is looking at a log it understands.
RUN_LOG_VERSION = 1

#: Characters allowed in the parts of a ``run_id``; everything else is replaced, so
#: a video path with spaces or punctuation still produces a usable filename.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(text: str) -> str:
    """Return ``text`` reduced to filename-safe characters."""
    cleaned = _UNSAFE.sub("-", str(text)).strip("-")
    return cleaned or "unknown"


def format_alpha(alpha: float) -> str:
    """Format Alpha for a ``run_id``: ``0.5`` becomes ``0p50``.

    Two decimals with the point replaced, so an Alpha sweep produces filenames that
    sort in Alpha order and carry no character a shell or a filesystem objects to.
    """
    return f"{float(alpha):.2f}".replace(".", "p")


def make_run_id(
    video_path: str, controller_name: str, alpha: float, when: datetime | None = None
) -> str:
    """Build the ``run_id`` that names one run and its file.

    ``{video_stem}__{controller}__alpha{a}__{timestamp}``: the four things that
    distinguish one run of this System from another. The Alpha is in the id even for
    a fixed-time run, where it has no effect on control, because the Scores recorded
    in the frame records were computed with it.
    """
    stem = _safe(Path(video_path).stem or "video")
    moment = (when or datetime.now()).strftime(TIMESTAMP_FORMAT)
    return f"{stem}__{_safe(controller_name)}__alpha{format_alpha(alpha)}__{moment}"


@dataclass
class RunLog:
    """One run's complete machine-readable record.

    Mutable, and built up as the run proceeds: the configuration and video metadata
    are known at the start, the frame records accrue during the loop, and the
    Evaluation_Metrics and the ``complete`` flag are only knowable at the end.

    ``config`` and ``video_info`` are plain dictionaries rather than
    :class:`~src.config.Config` and :class:`~src.video_io.VideoInfo` values, because
    a Run_Log read back from disk must not depend on those classes still having the
    same fields it was written with.
    """

    run_id: str
    video_path: str
    controller_name: str
    alpha: float
    config: dict[str, Any]
    video_info: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    overlap_events: list[dict[str, Any]] = field(default_factory=list)
    starvation_overrides: list[dict[str, Any]] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    waiting_times: dict[str, float] = field(default_factory=dict)
    #: Moving-to-stopped transitions per Approach over the run (E7), and the number
    #: of distinct assigned vehicles they are averaged over. Recorded here rather
    #: than derived from the frame records because a stop is a transition between two
    #: frames, not a property of one — so, like ``waiting_times``, it cannot be
    #: recomputed from the per-frame series alone and has to be carried explicitly.
    stops: dict[str, int] = field(default_factory=dict)
    seen_vehicles: int = 0
    evaluation_metrics: dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    #: Wall-clock seconds the run took, the divisor of Processing_FPS (Req 13.3).
    #: Recorded here rather than computed by the Evaluation_Harness because it is the
    #: one reported quantity that is not derivable from the frame records — and
    #: keeping it in the log is what lets Processing_FPS be regenerated from the log
    #: alone like every other metric (Requirement 13.4).
    processing_seconds: float = 0.0
    version: int = RUN_LOG_VERSION

    # -- convenience readers used by the Evaluation_Harness ----------------

    @property
    def frame_count(self) -> int:
        """How many frame records the run wrote (Requirement 14.6)."""
        return len(self.frames)

    @property
    def frame_rate(self) -> float:
        """The frame rate the Simulated_Clock of this run was derived from."""
        return float(self.video_info.get("frame_rate", 0.0))

    @property
    def simulated_duration(self) -> float:
        """The run's duration in simulated seconds.

        ``frame_count / frame_rate`` rather than the last record's
        ``simulated_time``: the final frame's clock reading is the *start* of that
        frame, so using it would understate the duration by one frame interval and
        overstate Throughput.
        """
        rate = self.frame_rate
        return self.frame_count / rate if rate > 0 else 0.0

    @property
    def completed_phases(self) -> list[dict[str, Any]]:
        """Phases per-phase averages may read, truncated ones excluded (Req 11.4)."""
        return [phase for phase in self.phases if not phase.get("truncated", False)]

    @property
    def approach_names(self) -> tuple[str, ...]:
        """The Approaches this run measured, in the fixed order."""
        return tuple(
            approach["name"] for approach in self.config.get("approaches", [])
        ) or APPROACH_NAMES

    def approach_series(self, approach: str, key: str) -> list[float]:
        """Return one per-frame series for one Approach, in frame order.

        The accessor the Evaluation_Harness averages and maximizes over, so that
        every metric it reports is visibly a function of the frame records
        (Requirement 13.4).
        """
        series: list[float] = []
        for record in self.frames:
            entry = record.get("approaches", {}).get(approach)
            if entry is None or key not in entry:
                continue
            series.append(float(entry[key]))
        return series

    # -- serialization ------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON object form of this Run_Log."""
        return {
            "version": self.version,
            "run_id": self.run_id,
            "video_path": self.video_path,
            "controller_name": self.controller_name,
            "alpha": self.alpha,
            "config": self.config,
            "video_info": self.video_info,
            "warnings": list(self.warnings),
            "overlap_events": list(self.overlap_events),
            "starvation_overrides": list(self.starvation_overrides),
            "phases": list(self.phases),
            "frames": list(self.frames),
            "waiting_times": dict(self.waiting_times),
            "stops": dict(self.stops),
            "seen_vehicles": int(self.seen_vehicles),
            "evaluation_metrics": dict(self.evaluation_metrics),
            "complete": bool(self.complete),
            "processing_seconds": float(self.processing_seconds),
        }

    @property
    def total_stops(self) -> int:
        """Stops summed over the Approaches this run recorded (E7)."""
        return sum(int(v) for v in self.stops.values())

    @property
    def average_stops(self) -> float:
        """Stops per distinct assigned vehicle, Li et al.'s headline MOE (E7)."""
        return self.total_stops / self.seen_vehicles if self.seen_vehicles else 0.0

    @classmethod
    def from_dict(cls, obj: Mapping[str, Any]) -> "RunLog":
        """Rebuild a Run_Log from its JSON object form.

        Missing optional collections default to empty rather than raising: a log
        written by an earlier stage of this project — before overlap events or
        waiting times were recorded — is still readable, and a reader that needs a
        field it did not find will say so itself.
        """
        if not isinstance(obj, Mapping):
            raise EvaluationError(
                f"run log must be a JSON object, got {type(obj).__name__}"
            )
        for key in ("run_id", "video_path", "controller_name"):
            if key not in obj:
                raise EvaluationError(f"run log is missing required key {key!r}")

        return cls(
            run_id=str(obj["run_id"]),
            video_path=str(obj["video_path"]),
            controller_name=str(obj["controller_name"]),
            alpha=float(obj.get("alpha", 0.0)),
            config=dict(obj.get("config", {})),
            video_info=dict(obj.get("video_info", {})),
            warnings=list(obj.get("warnings", [])),
            overlap_events=list(obj.get("overlap_events", [])),
            starvation_overrides=list(obj.get("starvation_overrides", [])),
            phases=list(obj.get("phases", [])),
            frames=list(obj.get("frames", [])),
            waiting_times={
                str(key): float(value)
                for key, value in dict(obj.get("waiting_times", {})).items()
            },
            stops={
                str(key): int(value)
                for key, value in dict(obj.get("stops", {})).items()
            },
            seen_vehicles=int(obj.get("seen_vehicles", 0)),
            evaluation_metrics=dict(obj.get("evaluation_metrics", {})),
            complete=bool(obj.get("complete", False)),
            processing_seconds=float(obj.get("processing_seconds", 0.0)),
            version=int(obj.get("version", RUN_LOG_VERSION)),
        )


def frame_record(measurement: FrameMeasurement) -> dict[str, Any]:
    """Return the Run_Log record of one frame (Requirement 14.6).

    Carries Vehicle_Count, Queue_Length, Score, and Signal_State per Approach, plus
    every normalized measure the Score could have been computed from. The normalized
    values are not required by Requirement 14.6, but without them a Score in the log
    cannot be re-derived from the record it sits in, and an unre-derivable number is
    one that has to be taken on trust.

    That is why the arrival, spillback, and PCE-queue measures are recorded too, not
    only the base two: once the predictive or spillback weight is non-zero the Score
    is a blend of four terms, and a record holding just density and queue would no
    longer be enough to reproduce it. ``queue_pce`` is recorded for the same reason on
    the timing side — it is the quantity the discharge-limited Green_Time divides by
    the saturation flow rate, so a Green_Time in the log stays checkable as well.
    """
    approaches: dict[str, Any] = {}
    for name, metrics in measurement.metrics.items():
        state = measurement.signal_states.get(name, "RED")
        approaches[name] = {
            "vehicle_count": int(metrics.vehicle_count),
            "queue_length": int(metrics.queue_length),
            "vehicle_density": float(metrics.vehicle_density),
            "normalized_queue": float(metrics.normalized_queue),
            "normalized_arrival": float(metrics.normalized_arrival),
            "normalized_spillback": float(metrics.normalized_spillback),
            "normalized_forecast": float(metrics.normalized_forecast),
            "queue_reach": float(metrics.queue_reach),
            "spillback_risk": float(metrics.spillback_risk),
            "queue_pce": float(metrics.queue_pce),
            "score": float(measurement.scores.get(name, 0.0)),
            "signal_state": getattr(state, "value", str(state)),
        }
    return {
        "frame_index": int(measurement.frame_index),
        "simulated_time": float(measurement.simulated_time),
        "approaches": approaches,
    }


class ResultsStore:
    """Creates, fills, and persists Run_Logs under ``results/run_logs/``."""

    def __init__(self, out_dir: str = RUN_LOG_DIR) -> None:
        self._out_dir = Path(out_dir)

    @property
    def out_dir(self) -> Path:
        """Directory Run_Logs are written to."""
        return self._out_dir

    # -- run lifecycle ------------------------------------------------------

    def start_run(
        self,
        video_path: str,
        controller_name: str,
        config: Config,
        video_info: VideoInfo,
        *,
        when: datetime | None = None,
    ) -> RunLog:
        """Open a Run_Log recording the run's inputs (Requirement 14.3).

        The *resolved* configuration is stored, not the path it came from, so the
        log describes the run even if the configuration file is later retuned —
        which task 14 does exactly once per video.

        A substituted frame rate becomes a warning here rather than at the point of
        substitution, because :class:`~src.video_io.VideoIngestor` has no Run_Log to
        write to and should not acquire one (Requirement 1.5).
        """
        log = RunLog(
            run_id=make_run_id(video_path, controller_name, config.alpha, when),
            video_path=str(video_path),
            controller_name=str(controller_name),
            alpha=float(config.alpha),
            config=to_json_obj(config),
            video_info=self._video_info_obj(video_info),
        )
        if video_info.frame_rate_substituted:
            log.warnings.append(
                f"reported frame rate was unusable; substituted the configured default "
                f"of {config.default_frame_rate} fps for {video_path}"
            )
        return log

    def append_frame_record(self, log: RunLog, measurement: FrameMeasurement) -> None:
        """Append one frame's measurements to ``log`` (Requirement 14.6)."""
        log.frames.append(frame_record(measurement))

    def append_overlap_events(self, log: RunLog, events: Iterable[Any]) -> None:
        """Record ROI overlap events for the Run_Log (Requirement 4.4)."""
        for event in events:
            to_json = getattr(event, "as_json_obj", None)
            log.overlap_events.append(to_json() if callable(to_json) else dict(event))

    def append_warning(self, log: RunLog, message: str) -> None:
        """Record a warning that does not stop the run (Requirement 1.5)."""
        log.warnings.append(str(message))

    def record_phases(self, log: RunLog, phases: Sequence[Any]) -> None:
        """Record every phase and derive the starvation overrides from them.

        Phases carry their own ``truncated`` flag (Requirement 11.4) and their own
        ``starvation_override`` flag (Requirement 9.6), so the override list is a
        projection of the phase list rather than a second record that could disagree
        with it. Replaces rather than appends, so calling it again after
        :meth:`~src.signal_controller.PhaseSequencer.finalize` picks up the
        truncation flag it set.
        """
        log.phases = [
            phase.as_dict() if hasattr(phase, "as_dict") else dict(phase)
            for phase in phases
        ]
        log.starvation_overrides = [
            {
                "cycle_index": phase.get("cycle_index"),
                "approach": phase.get("approach"),
                "start_frame": phase.get("start_frame"),
                "selection_score": phase.get("selection_score"),
                "green_time": phase.get("green_time"),
            }
            for phase in log.phases
            if phase.get("starvation_override") and phase.get("state") == "GREEN"
        ]

    def record_processing_time(self, log: RunLog, seconds: float) -> None:
        """Record the wall-clock duration of the run (Requirement 13.3)."""
        log.processing_seconds = float(seconds)

    def record_stops(
        self, log: RunLog, stops: Mapping[str, int], seen_vehicles: int
    ) -> None:
        """Record per-Approach stop counts and the vehicle count they average over.

        The stop count (E7) is Li et al.'s headline measure of effectiveness. Like
        the waiting times, it is a cross-frame accumulation rather than a per-frame
        series, so it is stored explicitly to keep it recoverable from the log.
        """
        log.stops = {str(name): int(count) for name, count in stops.items()}
        log.seen_vehicles = int(seen_vehicles)

    def record_waiting_times(self, log: RunLog, waiting_times: Mapping[int, float]) -> None:
        """Record the per-Track_ID Waiting_Time accumulations (Requirement 13.2).

        Keys become strings because JSON object keys are strings; the Track_ID is
        recoverable from them, and the Evaluation_Harness averages over the values.
        """
        log.waiting_times = {
            str(track_id): float(seconds) for track_id, seconds in waiting_times.items()
        }

    def finish_run(
        self,
        log: RunLog,
        metrics: Mapping[str, Any],
        complete: bool,
        *,
        path: str | None = None,
    ) -> str:
        """Close ``log`` with its Evaluation_Metrics and write it to disk.

        ``complete`` is False when the run ended before the video's final frame —
        a quit key, an early break, or an error — and the Evaluation_Harness excludes
        such a run from the controller comparison while still reporting it as
        incomplete (Requirement 13.8). Returns the path written.
        """
        log.evaluation_metrics = dict(metrics)
        log.complete = bool(complete)
        out_path = path or self.default_path(log)
        self.write(log, out_path)
        return out_path

    # -- persistence --------------------------------------------------------

    def default_path(self, log: RunLog) -> str:
        """Return the ``results/run_logs/`` path of ``log``, named by its ``run_id``."""
        return str(self._out_dir / f"{log.run_id}.json")

    def write(self, log: RunLog, path: str) -> str:
        """Write ``log`` to ``path`` as JSON, creating the directory if needed."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        obj = log.as_dict()
        _check_finite(obj)
        with out_path.open("w", encoding="utf-8") as handle:
            # No indentation, and separators without padding: see the module
            # docstring on why a Run_Log is compact rather than pretty.
            json.dump(obj, handle, separators=(",", ":"), allow_nan=False)
        return str(out_path)

    def read(self, path: str) -> RunLog:
        """Read a Run_Log back from ``path`` (Requirement 14.4)."""
        in_path = Path(path)
        try:
            with in_path.open("r", encoding="utf-8") as handle:
                obj = json.load(handle)
        except FileNotFoundError as exc:
            raise EvaluationError(f"run log not found: {in_path}") from exc
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"run log is not valid JSON: {in_path}: {exc}") from exc
        return RunLog.from_dict(obj)

    def read_all(self, paths: Iterable[str]) -> list[RunLog]:
        """Read several Run_Logs, in the order given."""
        return [self.read(path) for path in paths]

    def find_logs(self, pattern: str = "*.json") -> list[str]:
        """Return the Run_Log paths under :attr:`out_dir`, sorted by name.

        Sorted rather than in directory order, so ``--graphs-only`` regenerates the
        same table from the same set of files on every machine (Requirement 14.7).
        """
        if not self._out_dir.is_dir():
            return []
        return sorted(str(path) for path in self._out_dir.glob(pattern))

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _video_info_obj(video_info: VideoInfo) -> dict[str, Any]:
        """Return the Run_Log form of the input video's metadata."""
        return {
            "path": video_info.path,
            "width": int(video_info.width),
            "height": int(video_info.height),
            "frame_count": int(video_info.frame_count),
            "frame_rate": float(video_info.frame_rate),
            "frame_rate_substituted": bool(video_info.frame_rate_substituted),
        }


def _check_finite(obj: Any, where: str = "run log") -> None:
    """Reject NaN and infinity before they reach the file.

    ``json.dump`` writes them as ``NaN`` and ``Infinity``, which are not valid JSON
    and would make the round trip of Requirement 14.4 depend on a Python-specific
    extension. Failing here names the field instead of producing a file that only
    this interpreter can read.
    """
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise EvaluationError(f"{where} holds a non-finite value: {obj!r}")
        return
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            _check_finite(value, f"{where}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            _check_finite(value, f"{where}[{index}]")


__all__ = [
    "RUN_LOG_DIR",
    "RUN_LOG_VERSION",
    "TIMESTAMP_FORMAT",
    "ResultsStore",
    "RunLog",
    "format_alpha",
    "frame_record",
    "make_run_id",
]
