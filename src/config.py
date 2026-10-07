"""Config_Loader — reads every tunable parameter from a single JSON file.

Parsing uses only the standard-library ``json`` module; geometry validation uses
OpenCV and NumPy, which are already permitted runtime dependencies
(Requirement 15.8).

All three dataclasses are frozen and hold only tuples and scalars for their
sequence-valued fields, so ``from_json_obj(to_json_obj(c)) == c`` is exact
(Requirement 14.5).

Full validation lives in :func:`_validate`, which ``load_config`` calls after
deserializing. Validation is total: a configuration that passes ``load_config``
is guaranteed usable by every downstream component, so no downstream component
re-validates. Every failure raises :class:`~src.errors.ConfigError` naming the
offending field, approach, or value (Requirement 14.2).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from src.errors import ConfigError

#: Vertex as stored on a frozen dataclass.
Vertex = tuple[int, int]
Polygon = tuple[Vertex, ...]

#: The Approach set required by Requirement 4.1, in the fixed service order.
APPROACH_NAMES: tuple[str, ...] = ("North", "East", "South", "West")

#: The Vehicle_Classes that ``pce_weights`` must cover (Requirement 5.9).
VEHICLE_CLASSES: tuple[str, ...] = ("car", "motorcycle", "bus", "truck")


@dataclass(frozen=True)
class ApproachConfig:
    """Geometry and capacity of one Approach."""

    name: str                      # "North" | "East" | "South" | "West"
    roi_polygon: Polygon
    queue_region: Polygon
    saturation_count: float        # > 0   (Req 5.10)
    queue_capacity: float          # > 0   (Req 5.10)
    # Measured downstream travel direction for this Approach, as a 2-D image-space
    # vector, written by the ``calibrate-axes`` command from observed vehicle headings.
    # When present it replaces the Queue_Region centroid geometry as the basis for the
    # spatial reach axis, which removes the reach measure's sensitivity to how the
    # Queue_Region polygon happened to be drawn. ``None`` keeps the geometric fallback.
    axis_direction: tuple[float, float] | None = None
    # Agreement among the per-vehicle headings that produced ``axis_direction`` (the
    # mean resultant length, 0..1). Reported by the calibration and carried here so the
    # axis can expose how much to trust its own direction.
    axis_confidence: float = 1.0
    # Drawn queue axis (audit fix W7): a polyline from the STOP LINE back along the
    # inbound lanes to the far end of the visible storage, in image pixels. When given it
    # defines the reach axis outright - origin at the stop line, arc length along the
    # (possibly curved, fisheye) road - and takes precedence over ``axis_direction`` and
    # the centroid geometry, neither of which knows where the stop line is.
    queue_axis: Polygon | None = None


@dataclass(frozen=True)
class GreenTimeBand:
    """One Score band and the Green_Time it grants."""

    min_score: float               # inclusive lower bound
    green_time: float              # simulated seconds


@dataclass(frozen=True)
class Config:
    """The complete resolved configuration for one run."""

    alpha: float                   # 0..1  (Req 6.7)
    confidence_threshold: float
    model_path: str
    track_buffer: int
    trajectory_length: int
    approaches: tuple[ApproachConfig, ...]     # exactly 4 (Req 4.1)
    green_time_bands: tuple[GreenTimeBand, ...]
    min_green_time: float
    max_green_time: float
    yellow_duration: float
    starvation_limit: int          # >= 1  (Req 9.7)
    default_frame_rate: float
    use_pce_weighting: bool
    pce_weights: dict[str, float]  # per Vehicle_Class (Req 5.9)
    quit_key: str
    frame_size: tuple[int, int]    # for ROI bounds validation (Req 4.8)
    # --- Optional predictive/arrival-aware extension (off by default) ---------
    # Not part of the base specification: an anticipatory term that blends the
    # normalized count of vehicles present but not yet queueing (approaching the
    # stop line) into the Score. Defaults keep every base run and every existing
    # configuration file byte-for-byte unchanged in behaviour.
    use_predictive: bool = False
    predictive_weight: float = 0.0   # gamma in 0..1; share of Score from arrival
    # --- Optional control extensions derived from Wei et al. 2025 (TITS) ------
    # Four additive mechanisms transferred from the hierarchical MPC-Q paper to
    # this single-junction controller. Every default below is the neutral value,
    # so a configuration that omits all of them behaves exactly as the base
    # specification requires. See report/paper_limitations_analysis.md for which
    # published limitation each one answers.
    #
    # E1 — PCE-weighted Queue_Length. Base code PCE-weights density but counts the
    # queue raw; Wei's stated limitation is that vehicle types differ in queue
    # dynamics, and Raza supplies the weights.
    use_pce_queue: bool = False
    # E2 — Spillback pressure: the share of an Approach holding vehicles that are
    # stopped but outside the Queue_Region, i.e. the queue has grown past the
    # stop-line area. ``spillback_weight`` is delta, the share of the Score it
    # takes; ``stopped_displacement`` is the per-frame reference-point movement in
    # pixels below which a track counts as stopped.
    spillback_weight: float = 0.0
    stopped_displacement: float = 2.0
    # E3 — Discharge-limited Green_Time (Wei Eq. 16): serve the queue at the
    # saturation flow rate instead of reading a hand-tuned Score band.
    # ``saturation_flow_rate`` is in PCE per second of green (vehicles per second
    # when ``use_pce_queue`` is false).
    use_discharge_green: bool = False
    saturation_flow_rate: float = 0.5
    # E4 — Control-plan stability (Wei Eqs. 19-20): resist cycle-to-cycle
    # oscillation. ``switching_margin`` is the Score margin a challenger must beat
    # the incumbent Approach by; ``green_rate_limit`` bounds the change in
    # Green_Time between consecutive Cycles in seconds, 0.0 meaning unbounded.
    switching_margin: float = 0.0
    green_rate_limit: float = 0.0
    # --- Optional control extension derived from Li et al. 2025 (TITS) ---------
    # E6 — queue-clearance gap-out and extension. Li et al. classify a phase as
    # under- or over-saturated by comparing its split against the time the queue
    # needs to discharge; applied online, the same comparison ends a GREEN early
    # once the queue has cleared and extends it while the queue has not, bounded by
    # ``min_green_time`` and ``max_green_time``.
    #
    # Deliberately a single boolean with no weight of its own. Li et al.'s fourth
    # criticism of prior work is the burden of hyperparameters that cannot be set
    # without field surveys, so a mechanism that decides by measured comparison is
    # preferred here to one that decides by a tuned weight. Every quantity it needs
    # is already configured: the green bounds, and ``saturation_flow_rate`` for the
    # extension length.
    use_gap_out: bool = False
    # --- E8: short-term queue forecast (primary Wei-derived enhancement) --------
    # A vision-only analogue of Wei et al.'s predictive control: forecast each
    # approach's queue a few seconds ahead from the recent trend of its measured
    # queue, and let the controller grant green before the queue spills back.
    # ``forecast_weight`` (omega) is the share of the Score the forecast takes;
    # ``forecast_horizon_seconds`` how far ahead to project; ``forecast_window_frames``
    # how many recent frames the growth rate is fitted over. All neutral by default.
    use_forecast: bool = False
    forecast_weight: float = 0.0            # omega in 0..1
    forecast_horizon_seconds: float = 3.0   # > 0
    forecast_window_frames: int = 15        # >= 2
    # --- E9: spatial queue reach (own computer-vision contribution) -------------
    # Queue_Length is a count inside a hand-drawn region divided by a hand-set
    # capacity, so it saturates and it discards vehicle position. ``queue_reach``
    # instead measures how far back along the visible approach the stopped traffic
    # extends, normalised by the ROI's own geometry — no capacity guess and no
    # camera calibration. ``queue_reach_weight`` (psi) is the share of the Score it
    # takes. Neutral by default.
    use_queue_reach: bool = False
    queue_reach_weight: float = 0.0         # psi in 0..1
    # --- Spillback risk (the S term of the proposed controller) -----------------
    # Forward-looking storage occupancy: ``queue_reach`` extrapolated at its current
    # rate of spatial growth over ``risk_horizon_seconds``. Answers "is this approach
    # about to run out of storage?", where ``spillback_weight`` only sees overflow that
    # has already happened. ``spillback_risk_weight`` (rho) is its share of the Score.
    use_spillback_risk: bool = False
    spillback_risk_weight: float = 0.0      # rho in 0..1
    risk_horizon_seconds: float = 5.0       # > 0
    # --- Robust queue measurement (audit fix, see AUDIT_REPORT.md W5) ------------
    # ``stopped_window_seconds`` > 0 replaces the per-frame "moved < stopped_displacement
    # px since the previous frame" test with a speed test over a time window, measured
    # in bounding-box heights per second so a distant (small) vehicle and a near
    # (large) one are judged on the same physical scale. 0 keeps the legacy test.
    # ``stopped_speed_ratio`` is that threshold: below it a vehicle counts as stopped.
    # ``queue_tail_gap`` > 0 makes queue reach the tail of the CONTIGUOUS chain of
    # stopped vehicles that starts at the stop line (consecutive stopped vehicles no
    # more than this axis fraction apart), so an isolated stopped box far upstream
    # cannot set the reach. 0 keeps the legacy "furthest stopped vehicle" reach.
    # Passage time for E6 gap-out: the Queue_Region must stay empty this long before a
    # green is ended early. 0 keeps the legacy rule (end on the first empty frame), which
    # gaps out mid-discharge whenever moving vehicles happen to leave a gap in the region.
    gap_out_seconds: float = 0.0            # >= 0
    stopped_window_seconds: float = 0.0     # >= 0, 0 = legacy per-frame test
    stopped_speed_ratio: float = 0.2        # > 0, box heights per second
    queue_tail_gap: float = 0.0             # 0..1, 0 = legacy max reach
    # Perspective-scaled form of the same rule, for video: consecutive stopped vehicles
    # belong to one queue when they are at most this many BOX HEIGHTS apart along the
    # queue axis, and a chain may start at any stopped vehicle inside the Queue_Region.
    # A fixed axis fraction cannot work on a fisheye view, where one car near the camera
    # spans half the axis and one far away a few percent. > 0 overrides queue_tail_gap.
    queue_tail_gap_boxes: float = 0.0       # >= 0, 0 = use queue_tail_gap

    def approach(self, name: str) -> ApproachConfig:
        """Return the Approach configuration named ``name``."""
        for approach in self.approaches:
            if approach.name == name:
                return approach
        raise ConfigError(f"no approach named {name!r} in configuration")


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------


def _require(obj: Mapping[str, Any], key: str, where: str) -> Any:
    """Return ``obj[key]`` or raise ``ConfigError`` naming the absent key."""
    if not isinstance(obj, Mapping):
        raise ConfigError(f"{where} must be a JSON object, got {type(obj).__name__}")
    if key not in obj:
        raise ConfigError(f"missing required configuration key {key!r} in {where}")
    return obj[key]


def _as_float(value: Any, where: str) -> float:
    """Coerce a JSON number to ``float`` or raise ``ConfigError`` naming ``where``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} must be a number, got {value!r}")
    return float(value)


def _as_str(value: Any, where: str) -> str:
    """Return ``value`` when it is a string, else raise ``ConfigError``."""
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string, got {value!r}")
    return value


def _as_bool(value: Any, where: str) -> bool:
    """Return ``value`` when it is a JSON boolean, else raise ``ConfigError``."""
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false, got {value!r}")
    return value


def _optional_bool(obj: Mapping[str, Any], key: str, default: bool) -> bool:
    """Read an optional boolean key, falling back to ``default`` when absent.

    Optional rather than required so a configuration file written before an
    extension existed still loads, with the extension off (Requirement 14.1's
    required-key set is unchanged; these keys are additive).
    """
    if key not in obj:
        return default
    return _as_bool(obj[key], f"configuration field {key!r}")


def _optional_float(obj: Mapping[str, Any], key: str, default: float) -> float:
    """Read an optional numeric key, falling back to ``default`` when absent."""
    if key not in obj:
        return default
    return _as_float(obj[key], f"configuration field {key!r}")


def _optional_int(obj: Mapping[str, Any], key: str, default: int) -> int:
    """Read an optional integer key, falling back to ``default`` when absent."""
    if key not in obj:
        return default
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"configuration field {key!r} must be an integer, got {value!r}")
    return value


def _as_polygon(value: Any, where: str) -> Polygon:
    """Convert a JSON list of ``[x, y]`` pairs into a tuple of vertex tuples."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ConfigError(f"{where} must be a list of [x, y] vertices")
    vertices: list[Vertex] = []
    for index, vertex in enumerate(value):
        if isinstance(vertex, str) or not isinstance(vertex, Sequence) or len(vertex) != 2:
            raise ConfigError(f"{where} vertex {index} must be a pair [x, y]")
        vertices.append((vertex[0], vertex[1]))
    return tuple(vertices)


def _polygon_to_json(polygon: Polygon) -> list[list[int]]:
    return [[x, y] for x, y in polygon]


def _approach_from_json_obj(obj: Mapping[str, Any]) -> ApproachConfig:
    name = _as_str(_require(obj, "name", "approach entry"), "approach entry 'name'")
    where = f"approach {name!r}"
    return ApproachConfig(
        name=name,
        roi_polygon=_as_polygon(_require(obj, "roi_polygon", where), f"{where} roi_polygon"),
        queue_region=_as_polygon(_require(obj, "queue_region", where), f"{where} queue_region"),
        saturation_count=_as_float(
            _require(obj, "saturation_count", where), f"{where} saturation_count"
        ),
        queue_capacity=_as_float(
            _require(obj, "queue_capacity", where), f"{where} queue_capacity"
        ),
        axis_direction=_as_optional_direction(
            obj.get("axis_direction"), f"{where} axis_direction"
        ),
        axis_confidence=_as_float(
            obj.get("axis_confidence", 1.0), f"{where} axis_confidence"
        ),
        queue_axis=(
            _as_polygon(obj["queue_axis"], f"{where} queue_axis")
            if obj.get("queue_axis") is not None
            else None
        ),
    )


def _as_optional_direction(
    value: Any, where: str
) -> tuple[float, float] | None:
    """Coerce an optional ``[dx, dy]`` axis direction, rejecting a degenerate vector.

    A zero-length direction is refused rather than silently ignored: it would leave the
    reach measure permanently reading 0 for that Approach, which is far harder to
    notice in a Run_Log than a load-time error.
    """
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ConfigError(f"{where} must be a two-element [dx, dy] list")
    items = list(value)
    if len(items) != 2:
        raise ConfigError(f"{where} must have exactly two elements, got {len(items)}")
    dx = _as_float(items[0], f"{where} dx")
    dy = _as_float(items[1], f"{where} dy")
    if math.hypot(dx, dy) <= 0.0:
        raise ConfigError(f"{where} must be a non-zero vector, got [{dx}, {dy}]")
    return (dx, dy)


def _approach_to_json_obj(approach: ApproachConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": approach.name,
        "roi_polygon": _polygon_to_json(approach.roi_polygon),
        "queue_region": _polygon_to_json(approach.queue_region),
        "saturation_count": approach.saturation_count,
        "queue_capacity": approach.queue_capacity,
    }
    # Only emitted when calibrated, so an uncalibrated config round-trips unchanged.
    if approach.axis_direction is not None:
        payload["axis_direction"] = [
            float(approach.axis_direction[0]),
            float(approach.axis_direction[1]),
        ]
        payload["axis_confidence"] = float(approach.axis_confidence)
    if approach.queue_axis is not None:
        payload["queue_axis"] = _polygon_to_json(approach.queue_axis)
    return payload


def _band_from_json_obj(obj: Mapping[str, Any]) -> GreenTimeBand:
    where = "green_time_bands entry"
    return GreenTimeBand(
        min_score=_as_float(_require(obj, "min_score", where), f"{where} min_score"),
        green_time=_as_float(_require(obj, "green_time", where), f"{where} green_time"),
    )


def _band_to_json_obj(band: GreenTimeBand) -> dict[str, Any]:
    return {"min_score": band.min_score, "green_time": band.green_time}


def from_json_obj(obj: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from a decoded JSON object.

    Sequences become tuples and ``pce_weights`` keys are sorted, so the result
    compares equal to any other configuration carrying the same values
    regardless of the order they appeared in the file.
    """
    if not isinstance(obj, Mapping):
        raise ConfigError(f"configuration must be a JSON object, got {type(obj).__name__}")

    root = "configuration"

    raw_approaches = _require(obj, "approaches", root)
    if isinstance(raw_approaches, str) or not isinstance(raw_approaches, Sequence):
        raise ConfigError("configuration key 'approaches' must be a list")

    raw_bands = _require(obj, "green_time_bands", root)
    if isinstance(raw_bands, str) or not isinstance(raw_bands, Sequence):
        raise ConfigError("configuration key 'green_time_bands' must be a list")

    raw_weights = _require(obj, "pce_weights", root)
    if not isinstance(raw_weights, Mapping):
        raise ConfigError("configuration key 'pce_weights' must be a JSON object")

    raw_frame_size = _require(obj, "frame_size", root)
    if (
        isinstance(raw_frame_size, str)
        or not isinstance(raw_frame_size, Sequence)
        or len(raw_frame_size) != 2
    ):
        raise ConfigError("configuration key 'frame_size' must be a pair [width, height]")

    return Config(
        alpha=_as_float(_require(obj, "alpha", root), "configuration field 'alpha'"),
        confidence_threshold=_as_float(
            _require(obj, "confidence_threshold", root),
            "configuration field 'confidence_threshold'",
        ),
        model_path=_as_str(
            _require(obj, "model_path", root), "configuration field 'model_path'"
        ),
        track_buffer=_require(obj, "track_buffer", root),
        trajectory_length=_require(obj, "trajectory_length", root),
        approaches=tuple(_approach_from_json_obj(entry) for entry in raw_approaches),
        green_time_bands=tuple(_band_from_json_obj(entry) for entry in raw_bands),
        min_green_time=_as_float(
            _require(obj, "min_green_time", root), "configuration field 'min_green_time'"
        ),
        max_green_time=_as_float(
            _require(obj, "max_green_time", root), "configuration field 'max_green_time'"
        ),
        yellow_duration=_as_float(
            _require(obj, "yellow_duration", root), "configuration field 'yellow_duration'"
        ),
        starvation_limit=_require(obj, "starvation_limit", root),
        default_frame_rate=_as_float(
            _require(obj, "default_frame_rate", root),
            "configuration field 'default_frame_rate'",
        ),
        use_pce_weighting=_as_bool(
            _require(obj, "use_pce_weighting", root),
            "configuration field 'use_pce_weighting'",
        ),
        pce_weights={
            _as_str(k, "pce_weights key"): _as_float(v, f"pce_weights[{k!r}]")
            for k, v in sorted(raw_weights.items())
        },
        quit_key=_as_str(_require(obj, "quit_key", root), "configuration field 'quit_key'"),
        frame_size=(raw_frame_size[0], raw_frame_size[1]),
        # Optional keys: absent means the extension is off, so base configs load
        # unchanged (Requirement 14.1 keys stay required; these are additive).
        use_predictive=(
            _as_bool(obj["use_predictive"], "configuration field 'use_predictive'")
            if "use_predictive" in obj
            else False
        ),
        predictive_weight=(
            _as_float(obj["predictive_weight"], "configuration field 'predictive_weight'")
            if "predictive_weight" in obj
            else 0.0
        ),
        use_pce_queue=_optional_bool(obj, "use_pce_queue", False),
        spillback_weight=_optional_float(obj, "spillback_weight", 0.0),
        stopped_displacement=_optional_float(obj, "stopped_displacement", 2.0),
        use_discharge_green=_optional_bool(obj, "use_discharge_green", False),
        saturation_flow_rate=_optional_float(obj, "saturation_flow_rate", 0.5),
        switching_margin=_optional_float(obj, "switching_margin", 0.0),
        green_rate_limit=_optional_float(obj, "green_rate_limit", 0.0),
        use_gap_out=_optional_bool(obj, "use_gap_out", False),
        use_forecast=_optional_bool(obj, "use_forecast", False),
        forecast_weight=_optional_float(obj, "forecast_weight", 0.0),
        forecast_horizon_seconds=_optional_float(obj, "forecast_horizon_seconds", 3.0),
        forecast_window_frames=_optional_int(obj, "forecast_window_frames", 15),
        use_queue_reach=_optional_bool(obj, "use_queue_reach", False),
        queue_reach_weight=_optional_float(obj, "queue_reach_weight", 0.0),
        use_spillback_risk=_optional_bool(obj, "use_spillback_risk", False),
        spillback_risk_weight=_optional_float(obj, "spillback_risk_weight", 0.0),
        risk_horizon_seconds=_optional_float(obj, "risk_horizon_seconds", 5.0),
        gap_out_seconds=_optional_float(obj, "gap_out_seconds", 0.0),
        stopped_window_seconds=_optional_float(obj, "stopped_window_seconds", 0.0),
        stopped_speed_ratio=_optional_float(obj, "stopped_speed_ratio", 0.2),
        queue_tail_gap=_optional_float(obj, "queue_tail_gap", 0.0),
        queue_tail_gap_boxes=_optional_float(obj, "queue_tail_gap_boxes", 0.0),
    )


def to_json_obj(config: Config) -> dict[str, Any]:
    """Serialize a :class:`Config` into a JSON-encodable object.

    Field coverage is total in both directions, which is what makes the
    round-trip of Requirement 14.5 hold.
    """
    return {
        "alpha": config.alpha,
        "confidence_threshold": config.confidence_threshold,
        "model_path": config.model_path,
        "track_buffer": config.track_buffer,
        "trajectory_length": config.trajectory_length,
        "approaches": [_approach_to_json_obj(a) for a in config.approaches],
        "green_time_bands": [_band_to_json_obj(b) for b in config.green_time_bands],
        "min_green_time": config.min_green_time,
        "max_green_time": config.max_green_time,
        "yellow_duration": config.yellow_duration,
        "starvation_limit": config.starvation_limit,
        "default_frame_rate": config.default_frame_rate,
        "use_pce_weighting": config.use_pce_weighting,
        "pce_weights": {k: config.pce_weights[k] for k in sorted(config.pce_weights)},
        "quit_key": config.quit_key,
        "frame_size": [config.frame_size[0], config.frame_size[1]],
        "use_predictive": config.use_predictive,
        "predictive_weight": config.predictive_weight,
        "use_pce_queue": config.use_pce_queue,
        "spillback_weight": config.spillback_weight,
        "stopped_displacement": config.stopped_displacement,
        "use_discharge_green": config.use_discharge_green,
        "saturation_flow_rate": config.saturation_flow_rate,
        "switching_margin": config.switching_margin,
        "green_rate_limit": config.green_rate_limit,
        "use_gap_out": config.use_gap_out,
        "use_forecast": config.use_forecast,
        "forecast_weight": config.forecast_weight,
        "forecast_horizon_seconds": config.forecast_horizon_seconds,
        "forecast_window_frames": config.forecast_window_frames,
        "use_queue_reach": config.use_queue_reach,
        "queue_reach_weight": config.queue_reach_weight,
        "use_spillback_risk": config.use_spillback_risk,
        "spillback_risk_weight": config.spillback_risk_weight,
        "risk_horizon_seconds": config.risk_horizon_seconds,
        "gap_out_seconds": config.gap_out_seconds,
        "stopped_window_seconds": config.stopped_window_seconds,
        "stopped_speed_ratio": config.stopped_speed_ratio,
        "queue_tail_gap": config.queue_tail_gap,
        "queue_tail_gap_boxes": config.queue_tail_gap_boxes,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_finite(value: float, field: str) -> None:
    """Reject NaN and infinity, which no downstream arithmetic can tolerate."""
    if not math.isfinite(value):
        raise ConfigError(f"configuration field {field!r} must be a finite number, got {value!r}")


def _check_int_at_least(value: Any, field: str, minimum: int) -> None:
    """Require an ``int`` (never a ``bool``) no smaller than ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"configuration field {field!r} must be an integer, got {value!r}"
        )
    if value < minimum:
        raise ConfigError(
            f"configuration field {field!r} must be an integer >= {minimum}, got {value!r}"
        )


def _check_in_unit_range(value: float, field: str) -> None:
    """Require ``0 <= value <= 1``, naming the supplied value on failure."""
    _check_finite(value, field)
    if not 0.0 <= value <= 1.0:
        raise ConfigError(
            f"configuration field {field!r} must lie in the range 0 to 1 inclusive, "
            f"got {value!r}"
        )


def _check_positive(value: float, field: str) -> None:
    """Require ``value > 0``, naming the supplied value on failure."""
    _check_finite(value, field)
    if value <= 0.0:
        raise ConfigError(f"configuration field {field!r} must be > 0, got {value!r}")


def _as_cv_polygon(polygon: Polygon) -> np.ndarray:
    """Return the OpenCV contour representation of ``polygon``."""
    return np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))


def _polygon_contains(polygon: np.ndarray, vertex: Vertex) -> bool:
    """Point-in-polygon test matching the Approach_Assigner exactly.

    ``cv2.pointPolygonTest`` with ``measureDist=False`` returns +1 inside, 0 on
    the boundary, and -1 outside, so ``>= 0`` treats boundary points as inside
    just as the Approach_Assigner does (design decision on geometry).
    """
    x, y = vertex
    return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0


def _validate_polygon_shape(polygon: Polygon, where: str) -> None:
    """Require at least 3 integer vertices, naming the offending vertex."""
    if len(polygon) < 3:
        raise ConfigError(
            f"{where} must have at least 3 vertices, got {len(polygon)}"
        )
    for index, vertex in enumerate(polygon):
        for coordinate, label in zip(vertex, ("x", "y")):
            if isinstance(coordinate, bool) or not isinstance(coordinate, int):
                raise ConfigError(
                    f"{where} vertex {index} {label} coordinate must be an integer, "
                    f"got {coordinate!r}"
                )


def _validate_approaches(config: Config) -> None:
    """Validate the Approach set and every Approach's geometry and capacities."""
    names = [approach.name for approach in config.approaches]
    if sorted(names) != sorted(APPROACH_NAMES):
        raise ConfigError(
            "configuration field 'approaches' must define exactly the approaches "
            f"{', '.join(APPROACH_NAMES)}, got {', '.join(names) if names else '(none)'}"
        )

    width, height = config.frame_size
    for approach in config.approaches:
        where = f"approach {approach.name!r}"

        _validate_polygon_shape(approach.roi_polygon, f"{where} roi_polygon")
        _validate_polygon_shape(approach.queue_region, f"{where} queue_region")

        for index, (x, y) in enumerate(approach.roi_polygon):
            if not (0 <= x < width and 0 <= y < height):
                raise ConfigError(
                    f"{where} roi_polygon vertex {index} ({x}, {y}) lies outside the "
                    f"frame bounds {width}x{height}"
                )

        roi = _as_cv_polygon(approach.roi_polygon)
        for index, vertex in enumerate(approach.queue_region):
            if not _polygon_contains(roi, vertex):
                raise ConfigError(
                    f"{where} queue_region vertex {index} "
                    f"({vertex[0]}, {vertex[1]}) lies outside its parent roi_polygon"
                )

        if approach.queue_axis is not None:
            if len(approach.queue_axis) < 2:
                raise ConfigError(f"{where} queue_axis needs at least 2 points (stop line, far end)")
            for index, (x, y) in enumerate(approach.queue_axis):
                if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y)):
                    raise ConfigError(f"{where} queue_axis point {index} must be integer pixels")
            total = sum(
                math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(approach.queue_axis, approach.queue_axis[1:])
            )
            if total <= 0.0:
                raise ConfigError(f"{where} queue_axis has zero length")
        _check_finite(approach.axis_confidence, f"{where} axis_confidence")
        if not (0.0 <= approach.axis_confidence <= 1.0):
            raise ConfigError(
                f"{where} axis_confidence must lie in [0, 1], got "
                f"{approach.axis_confidence}"
            )
        if approach.axis_direction is not None:
            for index, component in enumerate(approach.axis_direction):
                _check_finite(component, f"{where} axis_direction[{index}]")

        for parameter, value in (
            ("saturation_count", approach.saturation_count),
            ("queue_capacity", approach.queue_capacity),
        ):
            _check_finite(value, f"{where} {parameter}")
            if value <= 0.0:
                raise ConfigError(f"{where} {parameter} must be > 0, got {value!r}")


def _validate_green_time_bands(config: Config) -> None:
    """Validate band monotonicity, coverage from Score 0, and Green_Time bounds."""
    bands = config.green_time_bands
    if not bands:
        raise ConfigError("configuration field 'green_time_bands' must contain at least one band")

    if bands[0].min_score != 0.0:
        raise ConfigError(
            "configuration field 'green_time_bands' must start at min_score 0, got "
            f"{bands[0].min_score!r}"
        )

    for index, band in enumerate(bands):
        _check_in_unit_range(band.min_score, f"green_time_bands[{index}] min_score")
        _check_finite(band.green_time, f"green_time_bands[{index}] green_time")
        if not config.min_green_time <= band.green_time <= config.max_green_time:
            raise ConfigError(
                f"green_time_bands[{index}] green_time {band.green_time!r} lies outside "
                f"the configured range [{config.min_green_time}, {config.max_green_time}]"
            )

    for index in range(1, len(bands)):
        previous, current = bands[index - 1], bands[index]
        if current.min_score <= previous.min_score:
            raise ConfigError(
                f"configuration field 'green_time_bands' must be strictly increasing in "
                f"min_score, but band {index} min_score {current.min_score!r} does not "
                f"exceed band {index - 1} min_score {previous.min_score!r}"
            )
        if current.green_time < previous.green_time:
            raise ConfigError(
                f"configuration field 'green_time_bands' must be non-decreasing in "
                f"green_time, but band {index} green_time {current.green_time!r} is below "
                f"band {index - 1} green_time {previous.green_time!r}"
            )


def _validate(config: Config) -> None:
    """Validate a configuration, raising ``ConfigError`` on the first failure.

    Validated rules, each naming the offending field, approach, or value:

    * exactly four approaches named North, East, South, West (Req 4.1)
    * every ``roi_polygon`` has >= 3 vertices, all inside ``frame_size`` (Req 4.8)
    * every ``queue_region`` vertex lies inside its parent ROI polygon (Req 4.7)
    * ``saturation_count > 0`` and ``queue_capacity > 0`` (Req 5.10)
    * ``0 <= alpha <= 1`` (Req 6.7)
    * ``starvation_limit`` is an integer >= 1 (Req 9.7)
    * ``green_time_bands`` non-empty, strictly increasing ``min_score`` starting at
      0, non-decreasing ``green_time`` (Reqs 8.7, 8.9)
    * every band ``green_time`` inside ``[min_green_time, max_green_time]`` (Req 8.8)
    * the remaining scalars every downstream component divides by, indexes, or
      loops over are present and in range (Reqs 14.1, 14.2)
    """
    _check_in_unit_range(config.alpha, "alpha")
    _check_in_unit_range(config.confidence_threshold, "confidence_threshold")

    # Optional predictive extension: weight is a share in 0..1 (default 0.0).
    if not isinstance(config.use_predictive, bool):
        raise ConfigError(
            f"configuration field 'use_predictive' must be true or false, got "
            f"{config.use_predictive!r}"
        )
    _check_in_unit_range(config.predictive_weight, "predictive_weight")

    # Optional Wei-derived control extensions. Each neutral default passes these
    # checks, so a base configuration is validated by exactly the same code path.
    for field in (
        "use_pce_queue", "use_discharge_green", "use_gap_out", "use_forecast",
        "use_queue_reach", "use_spillback_risk",
    ):
        value = getattr(config, field)
        if not isinstance(value, bool):
            raise ConfigError(
                f"configuration field {field!r} must be true or false, got {value!r}"
            )

    _check_in_unit_range(config.spillback_weight, "spillback_weight")
    _check_in_unit_range(config.switching_margin, "switching_margin")
    _check_in_unit_range(config.forecast_weight, "forecast_weight")
    _check_in_unit_range(config.queue_reach_weight, "queue_reach_weight")
    _check_in_unit_range(config.spillback_risk_weight, "spillback_risk_weight")
    _check_positive(config.risk_horizon_seconds, "risk_horizon_seconds")
    # The Score is a convex combination of the base score and the extension terms
    # (arrival, spillback, forecast, queue reach, spillback risk), so their weights
    # cannot together exceed 1 without giving the base term a negative coefficient and
    # taking the Score outside 0..1 (which the Green_Time bands assume).
    weight_sum = (
        config.predictive_weight
        + config.spillback_weight
        + config.forecast_weight
        + config.queue_reach_weight
        + config.spillback_risk_weight
    )
    if weight_sum > 1.0:
        raise ConfigError(
            f"configuration fields 'predictive_weight' ({config.predictive_weight!r}), "
            f"'spillback_weight' ({config.spillback_weight!r}), 'forecast_weight' "
            f"({config.forecast_weight!r}), 'queue_reach_weight' "
            f"({config.queue_reach_weight!r}) and 'spillback_risk_weight' "
            f"({config.spillback_risk_weight!r}) must sum to at most 1.0, "
            f"got {weight_sum!r}"
        )

    _check_positive(config.forecast_horizon_seconds, "forecast_horizon_seconds")
    _check_int_at_least(config.forecast_window_frames, "forecast_window_frames", 2)

    _check_positive(config.stopped_displacement, "stopped_displacement")
    _check_positive(config.stopped_speed_ratio, "stopped_speed_ratio")
    if (
        isinstance(config.gap_out_seconds, bool)
        or not isinstance(config.gap_out_seconds, (int, float))
        or not math.isfinite(config.gap_out_seconds)
        or config.gap_out_seconds < 0.0
    ):
        raise ConfigError(
            f"configuration field 'gap_out_seconds' must be a finite number >= 0, "
            f"got {config.gap_out_seconds!r}"
        )
    _check_in_unit_range(config.queue_tail_gap, "queue_tail_gap")
    if (
        isinstance(config.queue_tail_gap_boxes, bool)
        or not isinstance(config.queue_tail_gap_boxes, (int, float))
        or not math.isfinite(config.queue_tail_gap_boxes)
        or config.queue_tail_gap_boxes < 0.0
    ):
        raise ConfigError(
            f"configuration field 'queue_tail_gap_boxes' must be a finite number >= 0, "
            f"got {config.queue_tail_gap_boxes!r}"
        )
    if (
        isinstance(config.stopped_window_seconds, bool)
        or not isinstance(config.stopped_window_seconds, (int, float))
        or not math.isfinite(config.stopped_window_seconds)
        or config.stopped_window_seconds < 0.0
    ):
        raise ConfigError(
            f"configuration field 'stopped_window_seconds' must be a finite number >= 0 "
            f"(0 meaning the legacy per-frame test), got {config.stopped_window_seconds!r}"
        )
    _check_positive(config.saturation_flow_rate, "saturation_flow_rate")
    # 0.0 means unbounded, so this is a floor of 0 rather than a positivity check.
    if (
        isinstance(config.green_rate_limit, bool)
        or not isinstance(config.green_rate_limit, (int, float))
        or not math.isfinite(config.green_rate_limit)
        or config.green_rate_limit < 0.0
    ):
        raise ConfigError(
            f"configuration field 'green_rate_limit' must be a finite number >= 0 "
            f"(0 meaning unbounded), got {config.green_rate_limit!r}"
        )

    if not config.model_path:
        raise ConfigError("configuration field 'model_path' must not be empty")

    _check_int_at_least(config.track_buffer, "track_buffer", 1)
    _check_int_at_least(config.trajectory_length, "trajectory_length", 1)
    _check_int_at_least(config.starvation_limit, "starvation_limit", 1)

    _check_positive(config.default_frame_rate, "default_frame_rate")
    _check_positive(config.yellow_duration, "yellow_duration")
    _check_positive(config.min_green_time, "min_green_time")
    _check_positive(config.max_green_time, "max_green_time")
    if config.min_green_time > config.max_green_time:
        raise ConfigError(
            f"configuration field 'min_green_time' ({config.min_green_time!r}) must not "
            f"exceed 'max_green_time' ({config.max_green_time!r})"
        )

    if len(config.quit_key) != 1:
        raise ConfigError(
            f"configuration field 'quit_key' must be a single character, got {config.quit_key!r}"
        )

    for index, extent in enumerate(config.frame_size):
        label = "width" if index == 0 else "height"
        if isinstance(extent, bool) or not isinstance(extent, int):
            raise ConfigError(
                f"configuration field 'frame_size' {label} must be an integer, got {extent!r}"
            )
        if extent <= 0:
            raise ConfigError(
                f"configuration field 'frame_size' {label} must be > 0, got {extent!r}"
            )

    for vehicle_class in VEHICLE_CLASSES:
        if vehicle_class not in config.pce_weights:
            raise ConfigError(
                f"configuration field 'pce_weights' is missing vehicle class {vehicle_class!r}"
            )
    for vehicle_class, weight in config.pce_weights.items():
        if vehicle_class not in VEHICLE_CLASSES:
            raise ConfigError(
                f"configuration field 'pce_weights' names unknown vehicle class "
                f"{vehicle_class!r}"
            )
        _check_positive(weight, f"pce_weights[{vehicle_class!r}]")

    _validate_approaches(config)
    _validate_green_time_bands(config)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def load_config(path: str) -> Config:
    """Read, deserialize, and validate the configuration file at ``path``."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"configuration file could not be read: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration file is not valid JSON: {path}: {exc}") from exc

    config = from_json_obj(raw)
    _validate(config)
    return config


__all__ = [
    "APPROACH_NAMES",
    "VEHICLE_CLASSES",
    "ApproachConfig",
    "GreenTimeBand",
    "Config",
    "load_config",
    "to_json_obj",
    "from_json_obj",
]
