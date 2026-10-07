"""Command-line entry points (Requirement 15).

This module owns argument parsing and the single place a deliberate failure is
turned into a process exit: every error raised by this project derives from
:class:`~src.errors.TrafficSignalError`, so one handler prints the message and
exits with status 1 (Requirements 1.4, 14.2).

Implemented so far:

* ``python -m src.main play --video V`` — plays the video in an OpenCV window and
  reports its frame count and frame rate (Requirements 1.3, 1.6, 15.1).
* ``python -m src.main detect --video V`` — detects the four Vehicle_Classes and
  writes an annotated video to ``results/videos/`` (Requirements 2.7, 15.2).
* ``python -m src.main track --video V`` — tracks vehicles and writes an annotated
  video carrying each track's Track_ID, Vehicle_Class, and trajectory polyline to
  ``results/videos/`` (Requirements 3.6, 15.3).
* ``python -m src.main measure --video V`` — displays the live per-approach
  Vehicle_Count, Queue_Length, Vehicle_Density, and Score with the ROI polygons
  and queue regions drawn (Requirement 15.4).
* ``python -m src.main control --video V --controller {fixed,adaptive}`` — runs the
  full pipeline under the selected controller and renders the simulated signal
  overlay (Requirement 15.5).
* ``python -m src.main evaluate`` — runs the comparison experiment and writes the
  tables and graphs, or regenerates them from existing Run_Logs with
  ``--graphs-only`` (Requirements 13.9, 14.7, 15.6).

The :class:`Pipeline` class here is the per-frame loop those last two commands share
with the Evaluation_Harness, so a run started from the command line and a run started
by the harness are the same code path with the same recorded output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import cv2

from src import evaluation
from src.config import APPROACH_NAMES, Config, load_config, to_json_obj
from src.detection import Detector, YoloDetector, run_detection_video
from src.errors import TrafficSignalError, VideoError
from src.lane_analysis import (
    ApproachAssigner,
    ApproachAxis,
    AxisDirectionEstimator,
    as_cv_polygon,
    build_approach_axes,
    principal_axis,
)
from src.overlay import (
    CONTROL_WINDOW_NAME,
    AnnotatedVideoWriter,
    SignalOverlay,
    approach_panel_row,
    default_control_output_path,
    run_measurement_video,
    run_tracking_video,
    signal_banner_text,
)
from src.results_store import ResultsStore, RunLog
from src.signal_controller import (
    AdaptiveController,
    Controller,
    FixedTimeController,
    PhaseSequencer,
)
from src.track_cache import CachedTracker, RecordingTracker, default_cache_path
from src.tracking import ByteTrackTracker, Tracker
from src.traffic_metrics import (
    ApproachMetrics,
    FrameMeasurement,
    MetricsEngine,
    QueuePredictor,
    compute_config_demands,
    compute_config_scores,
)
from src.video_io import VideoIngestor, VideoInfo, play, simulated_clock

#: Resolved relative to the working directory, as the design specifies.
DEFAULT_CONFIG_PATH = "config/default.json"

#: The controller names the ``control`` command accepts (Requirement 15.5).
CONTROLLER_NAMES = ("fixed", "adaptive")


def make_controller(name: str, config: Config) -> Controller:
    """Build the controller named ``name`` (Requirement 15.5).

    The Fixed_Time_Controller takes no configuration: its Green_Time is the
    definition of the baseline rather than a tunable of this System, so it must not
    move when the configuration is retuned.
    """
    if name == "fixed":
        return FixedTimeController()
    if name == "adaptive":
        return AdaptiveController(config)
    raise TrafficSignalError(
        f"unknown controller {name!r}; choose one of {', '.join(CONTROLLER_NAMES)}"
    )


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of one :meth:`Pipeline.run`.

    ``metrics`` and ``scores`` are the final frame's values, the same convention the
    measurement milestone uses, so a headless run can be asserted on without decoding
    its output video. ``log`` is the written Run_Log, which is where every reported
    number comes from afterwards.
    """

    info: VideoInfo
    log: RunLog
    log_path: str
    out_path: str | None
    controller_name: str
    frames_processed: int
    frames_written: int
    complete: bool
    quit_early: bool
    cycle_count: int
    processing_seconds: float
    metrics: dict[str, ApproachMetrics]
    scores: dict[str, float]

    @property
    def processing_fps(self) -> float:
        """Frames processed per wall-clock second (Requirement 13.3)."""
        if self.processing_seconds <= 0.0:
            return 0.0
        return self.frames_processed / self.processing_seconds


class Pipeline:
    """The per-frame loop: vision, measurement, control, overlay, Run_Log.

    One instance runs one video under one controller once. Stage order per frame,
    which is the order the design fixes:

    read frame → detect → track → assign → **sequencer tick** → metrics update →
    score → overlay draw → append frame record.

    Two ordering points carry requirements:

    * The sequencer ticks **before** the Metrics_Engine update, so the Signal_State
      that gates Waiting_Time on frame ``n`` is the state in force during frame ``n``
      (Requirements 5.5, 5.6).
    * Controller selection at a phase boundary therefore reads the Scores computed on
      frame ``n - 1`` — frame ``n``'s do not exist yet at that point in the loop. The
      Run_Log records that as each phase's ``selection_score_frame``, so a decision
      stays auditable rather than appearing to have used data it could not have had
      (Requirement 11.1's ordering, recorded per Requirement 14.6).

    ``detector`` and ``tracker`` are injection seams: with fakes the whole loop runs
    with no weights, no GPU, and no real footage, which is what makes the integration
    test of task 11 possible. Left as ``None``, a :class:`~src.tracking.ByteTrackTracker`
    is built, and Ultralytics' persistent ByteTrack detects and associates in one
    pass, so no separate Detector is constructed.
    """

    def __init__(
        self,
        video_path: str,
        config: Config,
        controller: str | Controller = "adaptive",
        *,
        detector: Detector | None = None,
        tracker: Tracker | None = None,
        store: ResultsStore | None = None,
        out_path: str | None = None,
        write_video: bool = False,
        display: bool = False,
        window_name: str = CONTROL_WINDOW_NAME,
        log_path: str | None = None,
    ) -> None:
        self._video_path = str(video_path)
        self._config = config
        self._controller = (
            make_controller(controller, config) if isinstance(controller, str) else controller
        )
        self._controller_name = getattr(
            self._controller, "name", type(self._controller).__name__
        )
        self._detector = detector
        self._tracker = tracker
        self._owns_tracker = tracker is None
        self._store = store or ResultsStore()
        self._write_video = write_video or out_path is not None
        self._out_path = out_path
        self._display = display
        self._window_name = window_name
        self._log_path = log_path

    @property
    def controller_name(self) -> str:
        """The active controller's name, as the overlay and Run_Log carry it."""
        return self._controller_name

    def run(self) -> PipelineResult:
        """Process the whole video and write the Run_Log, returning the outcome."""
        config = self._config
        ingestor = VideoIngestor(self._video_path, config)
        info = ingestor.info

        tracker = self._tracker if self._tracker is not None else ByteTrackTracker(config)
        assigner = ApproachAssigner(config)
        engine = MetricsEngine(config)
        overlay = SignalOverlay(config)
        sequencer = PhaseSequencer(self._controller, config, info.frame_rate)
        # E8: the short-term queue forecaster, built only when enabled so a base run
        # constructs nothing extra. When present it augments each frame's metrics
        # with a projected queue before scoring and demand computation.
        # Built when either projected measure is wanted: the forecast (count-based
        # queue projected forward) or the spillback risk (spatial occupancy projected
        # forward). Both come from the same trend machinery, so one predictor serves
        # both and a base run constructs nothing extra.
        predictor = (
            QueuePredictor(config, info.frame_rate)
            if getattr(config, "use_forecast", False)
            or getattr(config, "use_spillback_risk", False)
            else None
        )

        log = self._store.start_run(self._video_path, self._controller_name, config, info)
        out_path = self._resolve_out_path(log)
        writer = AnnotatedVideoWriter(out_path, info.frame_rate) if out_path else None

        # One frame's simulated duration: Waiting_Time is measured in video time, not
        # wall-clock time (Requirement 5.5).
        dt = 1.0 / info.frame_rate
        delay_ms = max(1, int(round(1000.0 / info.frame_rate)))
        quit_codes = {ord(config.quit_key.lower()), ord(config.quit_key.upper())}

        # The Scores the first Cycle is selected from. Zero for every Approach: on
        # frame 0 no previous frame has been measured, and a zero vector makes the
        # first selection fall to the fixed Approach order rather than to whichever
        # Approach a stale reading happened to favour.
        scores: dict[str, float] = {name: 0.0 for name in APPROACH_NAMES}
        # The queue demand in PCE units the discharge-limited Green_Time reads (E3).
        # Carried alongside the Scores and for the same reason: selection happens
        # before this frame is measured, so both describe the previous frame. Zero on
        # frame 0, where the bounded Green_Time falls to min_green_time.
        demands: dict[str, float] = {name: 0.0 for name in APPROACH_NAMES}
        metrics: dict[str, ApproachMetrics] = {}
        frames_processed = 0
        last_index = -1
        quit_early = False
        started = time.perf_counter()

        try:
            for index, frame in ingestor.frames():
                detections = self._detector.detect(frame) if self._detector else []
                tracks = tracker.update(frame, detections)
                assigned, overlaps = assigner.assign(tracks, frame_index=index)

                phase_info = sequencer.tick(index, scores, demands)
                signal_states = sequencer.signal_states()

                metrics = engine.update(assigned, signal_states, dt)
                if predictor is not None:
                    # Fill each Approach's normalized_forecast from its recent trend,
                    # so the scores and demands below act on where the queue is
                    # heading, not only where it stands (E8).
                    metrics = predictor.predict(metrics)
                scores = compute_config_scores(metrics, config)
                demands = compute_config_demands(metrics, config)

                measurement = FrameMeasurement(
                    frame_index=index,
                    simulated_time=simulated_clock(index, info.frame_rate),
                    metrics=metrics,
                    scores=scores,
                    signal_states={
                        name: state.value for name, state in signal_states.items()
                    },
                )
                self._store.append_frame_record(log, measurement)
                self._store.append_overlap_events(log, overlaps)

                annotated = overlay.draw(
                    frame,
                    tracks,
                    assigned,
                    metrics,
                    scores,
                    signal_states,
                    phase_info,
                    self._controller_name,
                    measurement.simulated_time,
                )
                if writer is not None:
                    writer.write(annotated)

                frames_processed += 1
                last_index = index

                if self._display and self._show(annotated, delay_ms) in quit_codes:
                    quit_early = True
                    break
        finally:
            if writer is not None:
                writer.close()
            ingestor.close()
            if self._owns_tracker:
                close = getattr(tracker, "close", None)
                if callable(close):
                    close()

        processing_seconds = time.perf_counter() - started

        if frames_processed == 0:                # pragma: no cover - first frame validated
            raise VideoError(f"video yielded no frames to process: {self._video_path}")

        # A run that stopped on the quit key did not reach the final frame, so it is
        # incomplete and the harness excludes it from the comparison (Requirement 13.8).
        complete = not quit_early
        sequencer.finalize(last_index)
        self._store.record_phases(log, sequencer.phases)
        self._store.record_waiting_times(log, engine.waiting_times)
        self._store.record_stops(log, engine.stops, engine.seen_vehicles)
        self._store.record_processing_time(log, processing_seconds)
        if quit_early:
            self._store.append_warning(
                log,
                f"run stopped on the quit key {config.quit_key!r} after "
                f"{frames_processed} of {info.frame_count} frames; excluded from the "
                f"controller comparison",
            )
        elif info.frame_count > 0 and frames_processed != info.frame_count:
            # Container metadata and decodable frames disagree often enough that this
            # is a note rather than an error, but it belongs in the log: the
            # simulated duration is derived from the frames actually processed.
            self._store.append_warning(
                log,
                f"processed {frames_processed} frames; the container reported "
                f"{info.frame_count}",
            )

        log_path = self._store.finish_run(
            log, evaluation.metrics_json(log), complete, path=self._log_path
        )

        return PipelineResult(
            info=info,
            log=log,
            log_path=log_path,
            out_path=out_path,
            controller_name=self._controller_name,
            frames_processed=frames_processed,
            frames_written=writer.frames_written if writer is not None else 0,
            complete=complete,
            quit_early=quit_early,
            cycle_count=sequencer.cycle_count,
            processing_seconds=processing_seconds,
            metrics=metrics,
            scores=scores,
        )

    # -- internals ---------------------------------------------------------

    def _resolve_out_path(self, log: RunLog) -> str | None:
        """Return where the annotated video goes, or ``None`` to write none.

        Named by ``run_id`` when the caller asked for a video without naming one, so
        two runs of the same clip under different controllers cannot overwrite each
        other's output — which is exactly what the Evaluation_Harness does.
        """
        if self._out_path:
            return self._out_path
        if not self._write_video:
            return None
        return str(Path(default_control_output_path(self._video_path)).with_name(f"{log.run_id}.mp4"))

    def _show(self, frame: Any, delay_ms: int) -> int:
        """Display one frame and return the key pressed, or -1."""
        try:
            cv2.imshow(self._window_name, frame)
            return cv2.waitKey(delay_ms) & 0xFF
        except cv2.error as exc:
            raise VideoError(
                f"display mode is unavailable in this OpenCV build, rerun with "
                f"--no-display: {self._video_path}: {exc}"
            ) from exc


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments every command shares."""
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help=f"configuration JSON file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--no-display",
        dest="display",
        action="store_false",
        help="run without opening any OpenCV window (headless)",
    )
    parser.set_defaults(display=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="Queue-aware adaptive traffic signal control.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    play_parser = subparsers.add_parser(
        "play", help="play a junction video and report its properties"
    )
    play_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    _add_common_arguments(play_parser)

    detect_parser = subparsers.add_parser(
        "detect",
        help="detect vehicles and write an annotated video to results/videos/",
    )
    detect_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    detect_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="annotated output video path (default: results/videos/<video>__detections.mp4)",
    )
    _add_common_arguments(detect_parser)

    track_parser = subparsers.add_parser(
        "track",
        help="track vehicles and write an annotated video to results/videos/",
    )
    track_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    track_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="annotated output video path (default: results/videos/<video>__tracks.mp4)",
    )
    _add_common_arguments(track_parser)

    measure_parser = subparsers.add_parser(
        "measure",
        help="display live per-approach counts, queue lengths, density, and score",
    )
    measure_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    measure_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help=(
            "also write the drawn frames to this video file "
            "(default: display only, no video written)"
        ),
    )
    _add_common_arguments(measure_parser)

    control_parser = subparsers.add_parser(
        "control",
        help="run a controller over a video and render the simulated signal overlay",
    )
    control_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    control_parser.add_argument(
        "--controller",
        default="adaptive",
        choices=list(CONTROLLER_NAMES),
        help="which controller drives the signal (default: adaptive)",
    )
    control_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="annotated output video path (default: results/videos/<run_id>.mp4)",
    )
    control_parser.add_argument(
        "--no-video",
        dest="write_video",
        action="store_false",
        help="do not write an annotated video, only the run log",
    )
    control_parser.add_argument(
        "--track-cache",
        default=None,
        metavar="PATH",
        help=(
            "replay recorded YOLO+ByteTrack output from this cache instead of running "
            "the detector (see the cache-tracks command); guarantees identical vision "
            "input across ablation stages"
        ),
    )
    control_parser.set_defaults(write_video=True)
    _add_common_arguments(control_parser)

    cache_parser = subparsers.add_parser(
        "cache-tracks",
        help="run YOLO+ByteTrack once over a video and record its tracks for replay",
    )
    cache_parser.add_argument("--video", required=True, metavar="PATH", help="input video file")
    cache_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="cache file (default: results/track_cache/<video>__<model>__c<conf>.json.gz)",
    )
    _add_common_arguments(cache_parser)

    calibrate_parser = subparsers.add_parser(
        "calibrate-axes",
        help="measure each approach's travel direction from observed vehicle motion",
    )
    calibrate_parser.add_argument(
        "--video",
        required=True,
        nargs="+",
        metavar="PATH",
        help="input video file(s). Pass several clips from the SAME camera to pool them "
             "into one estimate, which raises confidence without changing the geometry",
    )
    calibrate_parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="stop after this many frames (0 = whole video)",
    )
    calibrate_parser.add_argument(
        "--write",
        default=None,
        metavar="PATH",
        help="write a calibrated copy of the configuration to PATH",
    )
    _add_common_arguments(calibrate_parser)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="run the comparison experiment and write tables and graphs",
    )
    evaluate_parser.add_argument(
        "--videos",
        default=evaluation.VIDEO_SPEC_PATH,
        metavar="PATH",
        help=f"video specification set (default: {evaluation.VIDEO_SPEC_PATH})",
    )
    evaluate_parser.add_argument(
        "--alpha-sweep",
        default=None,
        metavar="A,B,C",
        help="run the adaptive controller once per comma-separated alpha value",
    )
    evaluate_parser.add_argument(
        "--graphs-only",
        action="store_true",
        help="regenerate tables and graphs from existing run logs, decoding no video",
    )
    evaluate_parser.add_argument(
        "--report",
        action="store_true",
        help=f"also build the report artefacts under {evaluation.REPORT_DIR}/",
    )
    evaluate_parser.add_argument(
        "--videos-out",
        dest="write_videos",
        action="store_true",
        help="write an annotated video per run (slower; off by default)",
    )
    evaluate_parser.add_argument(
        "--no-roi-check",
        dest="check_roi",
        action="store_false",
        help=(
            "skip the sampled check that each configured ROI still covers road this "
            "video uses; by default a video whose ROIs see no traffic is excluded"
        ),
    )
    evaluate_parser.set_defaults(check_roi=True)
    _add_common_arguments(evaluate_parser)

    return parser


def _run_play(args: argparse.Namespace) -> int:
    """Play the video and report frame count and frame rate (Requirement 15.1)."""
    config = load_config(args.config)
    result = play(args.video, config, display=args.display)
    info = result.info

    print(f"video       : {info.path}")
    print(f"resolution  : {info.width}x{info.height}")
    print(f"frame count : {info.frame_count}")
    print(f"frame rate  : {info.frame_rate:.2f} fps")
    print(f"frames shown: {result.frames_shown}")
    if info.frame_rate_substituted:
        print(
            f"warning     : reported frame rate was unusable, substituted the "
            f"configured default of {config.default_frame_rate:.2f} fps"
        )
    if result.quit_early:
        print(f"note        : stopped early on the quit key {config.quit_key!r}")
    return 0


def _run_detect(args: argparse.Namespace) -> int:
    """Write an annotated detection video (Requirements 2.7, 15.2).

    The detector is built here rather than inside ``run_detection_video`` so a
    weights-loading failure raises :class:`~src.errors.ModelError` before the
    video is opened, and so tests can substitute a scripted fake by patching
    ``YoloDetector`` in this module.
    """
    config = load_config(args.config)
    detector = YoloDetector(config)
    result = run_detection_video(
        args.video, config, args.out, detector=detector, display=args.display
    )
    info = result.info

    print(f"video          : {info.path}")
    print(f"resolution     : {info.width}x{info.height}")
    print(f"frame rate     : {info.frame_rate:.2f} fps")
    print(f"frames read    : {result.frames_processed}")
    print(f"frames written : {result.frames_written}")
    print(f"detections     : {result.detections_drawn}")
    print(f"output video   : {result.out_path}")
    if info.frame_rate_substituted:
        print(
            f"warning        : reported frame rate was unusable, substituted the "
            f"configured default of {config.default_frame_rate:.2f} fps"
        )
    if result.quit_early:
        print(f"note           : stopped early on the quit key {config.quit_key!r}")
    return 0


def _run_track(args: argparse.Namespace) -> int:
    """Write a tracked annotated video (Requirements 3.6, 15.3).

    The tracker is built here rather than inside ``run_tracking_video`` so a
    weights-loading failure raises :class:`~src.errors.ModelError` before the
    video is opened, and so tests can substitute a fake by patching
    ``ByteTrackTracker`` in this module. No separate Detector is constructed:
    Ultralytics' persistent ByteTrack detects and associates in one pass.
    """
    config = load_config(args.config)
    tracker = ByteTrackTracker(config)
    result = run_tracking_video(
        args.video, config, args.out, tracker=tracker, display=args.display
    )
    info = result.info

    print(f"video          : {info.path}")
    print(f"resolution     : {info.width}x{info.height}")
    print(f"frame rate     : {info.frame_rate:.2f} fps")
    print(f"frames read    : {result.frames_processed}")
    print(f"frames written : {result.frames_written}")
    print(f"tracks drawn   : {result.tracks_drawn}")
    print(f"unique ids     : {result.unique_track_count}")
    print(f"output video   : {result.out_path}")
    if info.frame_rate_substituted:
        print(
            f"warning        : reported frame rate was unusable, substituted the "
            f"configured default of {config.default_frame_rate:.2f} fps"
        )
    if result.quit_early:
        print(f"note           : stopped early on the quit key {config.quit_key!r}")
    return 0


def _run_measure(args: argparse.Namespace) -> int:
    """Display the live per-approach measurements (Requirement 15.4).

    The tracker is built here for the same reasons as in :func:`_run_track`: a
    weights failure is raised before the video is opened, and tests substitute a
    fake by patching ``ByteTrackTracker`` in this module.

    The per-approach lines printed at the end are the same strings the on-screen
    panel carries (:func:`~src.overlay.approach_panel_row`), so a headless run
    reports exactly what a displayed run showed on its final frame.
    """
    config = load_config(args.config)
    tracker = ByteTrackTracker(config)
    result = run_measurement_video(
        args.video, config, args.out, tracker=tracker, display=args.display
    )
    info = result.info

    print(f"video          : {info.path}")
    print(f"resolution     : {info.width}x{info.height}")
    print(f"frame rate     : {info.frame_rate:.2f} fps")
    print(f"frames read    : {result.frames_processed}")
    print(f"tracks drawn   : {result.tracks_drawn}")
    print(f"unassigned     : {result.unassigned_tracks}")
    print(f"alpha          : {config.alpha:.2f}")
    print("signal states  : all RED (no controller at this milestone)")
    for name in APPROACH_NAMES:
        metrics = result.metrics.get(name)
        if metrics is None:                  # pragma: no cover - every frame measures all four
            continue
        print(f"approach       : {approach_panel_row(name, metrics, result.scores[name])}")
    if result.overlap_events:
        print(f"roi overlaps   : {len(result.overlap_events)} (see the run log stage)")
    if result.out_path is not None:
        print(f"frames written : {result.frames_written}")
        print(f"output video   : {result.out_path}")
    if info.frame_rate_substituted:
        print(
            f"warning        : reported frame rate was unusable, substituted the "
            f"configured default of {config.default_frame_rate:.2f} fps"
        )
    if result.quit_early:
        print(f"note           : stopped early on the quit key {config.quit_key!r}")
    return 0


def _run_control(args: argparse.Namespace) -> int:
    """Run the full pipeline under the selected controller (Requirement 15.5).

    The tracker is built here for the same reason as in :func:`_run_track`: a weights
    failure is raised before the video is opened, and tests substitute a fake by
    patching ``ByteTrackTracker`` in this module.
    """
    config = load_config(args.config)
    tracker: Tracker = (
        CachedTracker(args.track_cache, config)
        if args.track_cache
        else ByteTrackTracker(config)
    )
    pipeline = Pipeline(
        args.video,
        config,
        args.controller,
        tracker=tracker,
        out_path=args.out,
        write_video=args.write_video,
        display=args.display,
    )
    result = pipeline.run()
    info = result.info

    print(f"video          : {info.path}")
    print(f"resolution     : {info.width}x{info.height}")
    print(f"frame rate     : {info.frame_rate:.2f} fps")
    print(f"controller     : {result.controller_name}")
    print(f"alpha          : {config.alpha:.2f}")
    print(f"frames read    : {result.frames_processed}")
    print(f"cycles served  : {result.cycle_count}")
    print(f"processing fps : {result.processing_fps:.2f}")
    print(f"complete       : {'yes' if result.complete else 'no'}")
    for name in APPROACH_NAMES:
        metrics = result.metrics.get(name)
        if metrics is None:                  # pragma: no cover - every frame measures all four
            continue
        print(f"approach       : {approach_panel_row(name, metrics, result.scores[name])}")
    aggregate = result.log.evaluation_metrics.get("aggregate", {})
    if aggregate:
        print(f"avg wait       : {aggregate.get('avg_waiting_time', 0.0):.2f} s")
        print(f"avg queue      : {aggregate.get('avg_queue_length', 0.0):.2f} vehicles")
        print(f"vehicles served: {aggregate.get('vehicles_served', 0)}")
        print(f"throughput     : {aggregate.get('throughput', 0.0):.2f} veh/min")
    # E7: stops, Li et al.'s headline MOE. E5: how many GREEN phases ended with
    # their queue not yet discharged, and the worst shortfall in seconds.
    log = result.log
    print(f"total stops    : {log.total_stops}")
    print(f"avg stops/veh  : {log.average_stops:.3f}")
    green_phases = [p for p in log.phases if p.get("state") == "GREEN"]
    oversat = [p for p in green_phases if p.get("oversaturation", 0.0) > 0.0]
    if green_phases:
        worst = max((p.get("oversaturation", 0.0) for p in green_phases), default=0.0)
        print(
            f"oversaturated  : {len(oversat)}/{len(green_phases)} green phases "
            f"(worst shortfall {worst:.1f} s)"
        )
    print(f"run log        : {result.log_path}")
    if result.out_path is not None:
        print(f"frames written : {result.frames_written}")
        print(f"output video   : {result.out_path}")
    if info.frame_rate_substituted:
        print(
            f"warning        : reported frame rate was unusable, substituted the "
            f"configured default of {config.default_frame_rate:.2f} fps"
        )
    if result.quit_early:
        print(f"note           : stopped early on the quit key {config.quit_key!r}")
    return 0


def _run_cache_tracks(args: argparse.Namespace) -> int:
    """Run the real tracker once over a video and save its per-frame output."""
    config = load_config(args.config)
    recorder = RecordingTracker(ByteTrackTracker(config), config, args.video)
    ingestor = VideoIngestor(args.video, config)
    started = time.perf_counter()
    try:
        for _index, frame in ingestor.frames():
            recorder.update(frame, [])
    finally:
        ingestor.close()
        recorder.close()
    out = args.out or default_cache_path(args.video, config)
    recorder.save(out)
    elapsed = time.perf_counter() - started
    print(f"video          : {args.video}")
    print(f"frames cached  : {recorder.frames_recorded}")
    print(f"tracker fps    : {recorder.frames_recorded / elapsed:.2f}")
    print(f"track cache    : {out}")
    return 0


def _parse_alpha_sweep(raw: str | None) -> list[float] | None:
    """Parse ``--alpha-sweep A,B,C`` into Alpha values (Requirement 13.9)."""
    if not raw:
        return None
    values: list[float] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            values.append(float(text))
        except ValueError as exc:
            raise TrafficSignalError(
                f"--alpha-sweep expects comma-separated numbers, got {part!r}"
            ) from exc
    if not values:
        raise TrafficSignalError("--alpha-sweep was given no values")
    return values


def _run_calibrate_axes(args: argparse.Namespace) -> int:
    """Measure each Approach's travel direction from the video and report it.

    The spatial reach measure needs to know which way is upstream on each Approach.
    Inferring that from the offset between the ROI and Queue_Region centroids only
    works when the Queue_Region is a strip at the stop line; drawn as an inset copy of
    the ROI the two centroids nearly coincide and the inferred direction is noise. This
    command replaces that inference with a measurement: it tracks the clip, takes one
    unit heading vote per vehicle per Approach, and reports the mean heading together
    with the consensus among those votes.

    With ``--write`` the result is written back into a copy of the configuration as
    ``axis_direction``/``axis_confidence``, which :func:`~src.lane_analysis.build_approach_axes`
    then prefers over the centroid geometry. Nothing is modified in place.

    Several clips may be passed at once. They must come from the same fixed camera, since
    one set of ROI polygons is applied to all of them, and pooling is worthwhile because
    confidence depends on how many independent vehicles were seen rather than on the
    geometry: measured on this dataset, North's agreement is 0.531 on the 107 s clip and
    0.232 on the 240 s clip while the direction verdict is identical on both. Pooling
    raises the certainty without changing what is being estimated.
    """
    config = load_config(args.config)
    assigner = ApproachAssigner(config)
    # One estimator across every clip. Agreement rises with the number of independent
    # vehicles observed, and a single short clip can leave an approach below the
    # confidence threshold even when its direction is perfectly consistent — measured
    # here: North reads 0.531 on the 107 s clip but 0.232 on the 240 s one, while the
    # direction verdict is identical. Pooling clips from the same fixed camera is
    # therefore the right way to calibrate, since the direction is a property of the
    # camera rather than of any one recording.
    estimator = AxisDirectionEstimator()

    limit = args.max_frames if args.max_frames and args.max_frames > 0 else None
    videos = list(args.video)
    per_video: list[tuple[str, int, float]] = []

    for video_number, video_path in enumerate(videos):
        ingestor = VideoIngestor(video_path, config)
        info = ingestor.info
        # A fresh tracker per clip, because Track_IDs are only unique within one run.
        tracker = ByteTrackTracker(config)
        # Track_IDs restart at 1 for each clip, so they must be namespaced or two
        # different vehicles in different clips would be merged into one trajectory.
        offset = video_number * 1_000_000

        processed = 0
        for index, frame in ingestor.frames():
            assigned, _ = assigner.assign(tracker.update(frame, []), frame_index=index)
            for track in assigned:
                if track.approach is not None:
                    estimator.observe(
                        track.approach,
                        track.track_id + offset,
                        track.ref_point,
                        queueing=track.is_queueing,
                    )
            processed += 1
            if limit is not None and processed >= limit:
                break
        per_video.append((video_path, processed, info.frame_rate))

    existing = build_approach_axes(config)
    total_frames = sum(n for _, n, _ in per_video)
    if len(per_video) == 1:
        path, frames, rate = per_video[0]
        print(f"\nAxis calibration from {path}  ({frames} frames @ {rate:g} fps)")
    else:
        print(f"\nAxis calibration pooled over {len(per_video)} clips "
              f"({total_frames} frames total)")
        for path, frames, rate in per_video:
            print(f"    {path}  ({frames} frames @ {rate:g} fps)")
    print("\n  Direction is measured from where vehicles ENTER each ROI versus where")
    print("  they LEAVE it, averaged over every moving track.\n")
    print(
        f"  {'approach':9s} {'tracks':>7s} {'confidence':>10s} "
        f"{'downstream heading':>20s}   {'vs centroid guess':>20s}"
    )

    calibrated: dict[str, tuple[tuple[float, float], float]] = {}
    for name in APPROACH_NAMES:
        approach = config.approach(name)
        pts = as_cv_polygon(approach.roi_polygon).reshape(-1, 2).astype(float)
        span = float(
            ((pts[:, 0].max() - pts[:, 0].min()) ** 2
             + (pts[:, 1].max() - pts[:, 1].min()) ** 2) ** 0.5
        )
        old = existing[name]

        result = estimator.entry_exit_direction(name, span)
        if result is None:
            print(
                f"  {name:9s} {0:7d} {'-':>10s} {'insufficient traffic':>20s}"
                f"   {'-':>20s}"
            )
            continue

        direction, confidence, tracks = result
        calibrated[name] = (direction, confidence)

        # Compare against what the old centroid geometry believed, since that
        # disagreement is the whole reason this command exists.
        if old.length > 0.0:
            dot = -(direction[0] * old.upstream[0] + direction[1] * old.upstream[1])
            verdict = (
                "agree" if dot > 0.34
                else ("REVERSED" if dot < -0.34 else "orthogonal")
            )
            comparison = f"{verdict} ({dot:+.2f})"
        else:
            comparison = "degenerate"

        heading = f"({direction[0]:+.3f}, {direction[1]:+.3f})"
        flag = "" if confidence >= ApproachAxis.MIN_DIRECTION_CONSENSUS else "  LOW"
        print(
            f"  {name:9s} {tracks:7d} {confidence:10.3f} {heading:>20s}"
            f"   {comparison:>20s}{flag}"
        )

    # Only directions that clear the confidence threshold are written. A low-agreement
    # estimate is worse than no estimate: the geometric fallback is at least stable and
    # inspectable, whereas an unreliable measurement would silently replace it.
    trusted = {
        name: value
        for name, value in calibrated.items()
        if value[1] >= ApproachAxis.MIN_DIRECTION_CONSENSUS
    }
    rejected = sorted(set(calibrated) - set(trusted))
    if rejected:
        print(
            f"\n  below the {ApproachAxis.MIN_DIRECTION_CONSENSUS:.2f} confidence "
            f"threshold, left on geometric fallback: {rejected}"
        )

    if args.write:
        approaches = tuple(
            (
                replace(
                    approach,
                    axis_direction=trusted[approach.name][0],
                    axis_confidence=trusted[approach.name][1],
                )
                if approach.name in trusted
                else approach
            )
            for approach in config.approaches
        )
        updated = replace(config, approaches=approaches)
        destination = Path(args.write)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(to_json_obj(updated), indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n  wrote calibrated configuration to {destination}")
        print(f"  calibrated approaches: {sorted(trusted)}")
    else:
        print("\n  (dry run - pass --write PATH to save a calibrated configuration)")

    return 0


def _run_evaluate(args: argparse.Namespace) -> int:
    """Run the evaluation experiment or regenerate its artefacts (Req 15.6).

    ``--graphs-only`` reads Run_Logs and never opens a video (Requirement 14.7), so it
    is also the command to use after retuning how a table or a graph is drawn.
    """
    config = load_config(args.config)
    store = ResultsStore()

    if args.graphs_only:
        specs = (
            evaluation.load_video_specs(args.videos)
            if Path(args.videos).is_file()
            else None
        )
        table, graphs = evaluation.regenerate_artifacts(store=store, video_specs=specs)
        logs = store.read_all(store.find_logs())
        print(f"run logs read  : {len(logs)}")
    else:
        specs = evaluation.load_video_specs(args.videos)
        alphas = _parse_alpha_sweep(args.alpha_sweep)
        print(f"videos         : {len(specs)}")
        print(f"alphas         : {alphas or [config.alpha]}")
        excluded: list[str] = []
        logs = evaluation.run_evaluation(
            specs,
            config,
            alphas=alphas,
            store=store,
            write_videos=args.write_videos,
            display=args.display,
            check_roi=args.check_roi,
            excluded=excluded,
        )
        for message in excluded:
            print(f"excluded       : {message}")
        table = evaluation.write_comparison_table(logs, video_specs=specs)
        graphs = evaluation.write_comparison_graphs(logs)

    complete = evaluation.comparable_logs(logs)
    print(f"runs           : {len(logs)} ({len(complete)} complete)")
    print(f"table          : {table}")
    for graph in graphs:
        print(f"graph          : {graph}")

    if args.report:
        written = evaluation.build_report_artifacts(logs, video_specs=specs)
        for kind, paths in written.items():
            print(f"report {kind:<8}: {len(paths)} file(s)")

    if len(complete) < len(logs):
        print(
            f"note           : {len(logs) - len(complete)} incomplete run(s) are "
            f"reported in the table but excluded from the comparison"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the requested command, returning an exit status."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "play":
            return _run_play(args)
        if args.command == "detect":
            return _run_detect(args)
        if args.command == "track":
            return _run_track(args)
        if args.command == "measure":
            return _run_measure(args)
        if args.command == "control":
            return _run_control(args)
        if args.command == "cache-tracks":
            return _run_cache_tracks(args)
        if args.command == "calibrate-axes":
            return _run_calibrate_axes(args)
        if args.command == "evaluate":
            return _run_evaluate(args)
    except TrafficSignalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command {args.command!r}")   # pragma: no cover


if __name__ == "__main__":       # pragma: no cover - exercised as a subprocess
    sys.exit(main())
