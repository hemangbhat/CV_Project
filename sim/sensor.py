"""Virtual camera: per-approach measurements from simulated vehicles.

Mirrors :class:`src.traffic_metrics.MetricsEngine` definition by definition, so the
controller receives the same kind of :class:`~src.traffic_metrics.ApproachMetrics`
it receives from video:

=========================  ============================================  ==========================================
measure                    video (``src/traffic_metrics.py``)            simulation (this module)
=========================  ============================================  ==========================================
ROI                        hand-drawn polygon                            last ``roi_length`` m of the approach edge
Queue_Region               hand-drawn polygon at the stop line           last ``queue_length_m`` m of the edge
D  density                 ΣPCE in ROI / saturation_count                same
Q  normalized queue        count in Queue_Region / queue_capacity        same
A  arrival                 (count - queued) / saturation_count           same
stopped                    < ``stopped_speed_ratio`` box heights / s     < ``stopped_speed_ratio`` vehicle lengths / s
X  queue reach             ``queue_tail_reach`` on axis fractions        ``queue_tail_reach`` on distance / roi_length
F, S                       :class:`QueuePredictor`                       the same class
=========================  ============================================  ==========================================

Box height in the image and vehicle length on the road play the same role: the speed
threshold is "fraction of the vehicle's own size per second", so 0.2 means about
1 m/s for a 5 m car in both domains.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from src.traffic_metrics import ApproachMetrics, clamp, queue_tail_reach


@dataclass(frozen=True)
class VehicleObs:
    """One simulated vehicle on an approach, as the virtual camera sees it."""

    distance: float      # metres upstream of the stop line (0 = at the line)
    speed: float         # m/s
    length: float        # m
    vehicle_class: str   # "car" | "truck" | "bus" | "motorcycle"


@dataclass(frozen=True)
class SensorGeometry:
    """The virtual ROI and Queue_Region of one approach, plus its normalisers."""

    roi_length: float         # m of approach visible to the camera
    queue_length_m: float     # m of the Queue_Region at the stop line
    saturation_count: float   # vehicles (PCE) that saturate density
    queue_capacity: float     # vehicles that saturate the normalized queue


@dataclass(frozen=True)
class NoiseModel:
    """Vision-like degradation of the virtual camera.

    ``far_miss`` is the miss probability at the far end of the ROI, rising linearly
    from 0 at the stop line (small, distant vehicles are what YOLO misses; see
    AUDIT_REPORT.md W5). ``position_sigma`` is Gaussian position jitter in metres,
    applied independently per frame like bounding-box jitter.
    """

    far_miss: float = 0.0
    position_sigma: float = 0.0

    @property
    def active(self) -> bool:
        return self.far_miss > 0.0 or self.position_sigma > 0.0


def observe(
    vehicles: list[VehicleObs], geometry: SensorGeometry, noise: NoiseModel, rng: random.Random
) -> list[VehicleObs]:
    """Apply the noise model: drop and jitter vehicles as a camera would."""
    if not noise.active:
        return [v for v in vehicles if v.distance <= geometry.roi_length]
    seen: list[VehicleObs] = []
    for v in vehicles:
        if v.distance > geometry.roi_length:
            continue
        miss = noise.far_miss * (v.distance / geometry.roi_length)
        if rng.random() < miss:
            continue
        d = max(0.0, v.distance + rng.gauss(0.0, noise.position_sigma))
        seen.append(VehicleObs(d, v.speed, v.length, v.vehicle_class))
    return seen


def measure_approach(
    name: str,
    vehicles: list[VehicleObs],
    geometry: SensorGeometry,
    *,
    pce_weights: dict[str, float],
    use_pce_weighting: bool,
    stopped_speed_ratio: float,
    queue_tail_gap: float,
) -> ApproachMetrics:
    """Build one approach's :class:`ApproachMetrics` from its visible vehicles."""
    in_roi = [v for v in vehicles if v.distance <= geometry.roi_length]
    count = len(in_roi)
    queued = [v for v in in_roi if v.distance <= geometry.queue_length_m]
    stopped = [v for v in in_roi if v.speed < stopped_speed_ratio * v.length]

    numerator = (
        sum(pce_weights.get(v.vehicle_class, 1.0) for v in in_roi)
        if use_pce_weighting
        else float(count)
    )
    queue_count = len(queued)
    spilled = [v for v in stopped if v.distance > geometry.queue_length_m]
    reach = queue_tail_reach(
        (v.distance / geometry.roi_length for v in stopped), queue_tail_gap
    )
    return ApproachMetrics(
        approach=name,
        vehicle_count=count,
        vehicle_density=clamp(numerator / geometry.saturation_count),
        queue_length=queue_count,
        normalized_queue=clamp(queue_count / geometry.queue_capacity),
        normalized_arrival=clamp(max(count - queue_count, 0) / geometry.saturation_count),
        queue_pce=float(queue_count),
        normalized_spillback=clamp(len(spilled) / geometry.saturation_count),
        queue_reach=reach,
    )


def jam_capacity(length_m: float, lanes: int, spacing_m: float = 7.5) -> float:
    """Vehicles a stretch of road holds at jam density (5 m car + 2.5 m gap)."""
    return max(1.0, math.floor(length_m / spacing_m) * lanes)
