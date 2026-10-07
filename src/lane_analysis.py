"""Approach_Assigner — maps each track to one junction Approach.

Every per-approach number the project reports (Vehicle_Count, Queue_Length,
Vehicle_Density, Normalized_Queue, and therefore Score and Green_Time) is
downstream of one decision made here: which Approach, if any, a track belongs to,
and whether it is queueing (Requirements 4.2 to 4.6).

Three deliberate choices shape this module:

**One point per track.** A track is reduced to the midpoint of the bottom edge of
its box (Requirement 4.2) — roughly where the vehicle meets the road — so a tall
box whose top spills into a neighbouring ROI is still assigned by where the
vehicle actually stands. :func:`reference_point` delegates to
:attr:`~src.tracking.Track.ref_point` rather than recomputing the formula, so the
point the trajectory stores, the point the overlay draws, and the point tested
against the ROI polygons are the same point by construction.

**Boundary counts as inside.** ``cv2.pointPolygonTest`` with
``measureDist=False`` returns +1 inside, 0 on the boundary, and -1 outside, and
this module treats ``>= 0`` as inside — the same rule the Config_Loader uses when
it checks a queue region against its parent ROI, so a vertex accepted at load
time behaves as inside at run time. Vehicles genuinely stop on stop lines, so
"on the edge" has to resolve one way rather than dropping the track.

**Geometry is precomputed.** Polygon ``np.ndarray`` contours and centroids are
built once in :meth:`ApproachAssigner.__init__`. Rebuilding them per frame would
repeat the same eight small allocations on every one of thousands of frames while
Processing_FPS is itself a reported metric (Requirement 13.3).

The same geometry answers one question asked *before* a video enters the
evaluation set: :func:`check_roi_coverage` samples frames and reports the
Approaches whose ROI saw no assigned track, which is how a changed camera
viewpoint is caught and the video excluded (Requirement 16.5).

Assignment is total: every track comes back as an :class:`AssignedTrack`. An
unassigned track carries ``approach=None`` and ``is_queueing=False``, and the
Metrics_Engine skips it (Requirement 4.5) rather than this module dropping it, so
"tracked but outside every ROI" stays visible to the overlay and the Run_Log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from src.config import APPROACH_NAMES, Config, Polygon
from src.detection import Detector
from src.errors import ConfigError
from src.tracking import ByteTrackTracker, Point, Track, Tracker
from src.video_io import VideoIngestor


def as_cv_polygon(polygon: Polygon | Sequence[Sequence[int]]) -> np.ndarray:
    """Return the OpenCV contour representation of ``polygon``.

    ``cv2.pointPolygonTest`` wants an ``int32`` contour shaped ``(n, 1, 2)``;
    building it here keeps the shape in one place.
    """
    array = np.array(polygon, dtype=np.int32)
    if array.ndim != 2 or array.shape[0] < 3 or array.shape[1] != 2:
        raise ConfigError(
            "a polygon must be a list of at least 3 [x, y] vertices, got shape "
            f"{tuple(array.shape)}"
        )
    return array.reshape((-1, 1, 2))


def point_in_polygon(polygon: np.ndarray, point: Point | tuple[float, float]) -> bool:
    """Return whether ``point`` lies inside ``polygon``, boundary included.

    The ``>= 0`` comparison is the whole point: ``cv2.pointPolygonTest`` returns 0
    exactly on the boundary, and a vehicle stopped on a stop line must resolve to
    one Approach rather than to none (Requirements 4.3, 4.6).
    """
    x, y = point
    return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0


def polygon_centroid(polygon: np.ndarray) -> tuple[float, float]:
    """Return the area centroid of ``polygon`` as ``(x, y)`` floats.

    Used only for the overlap tiebreak of Requirement 4.4. The area centroid from
    image moments describes the polygon's shape better than a vertex mean, which
    a run of closely spaced vertices along one edge would drag towards that edge.
    A degenerate polygon (zero area, all vertices collinear) has no area centroid,
    so the vertex mean is the fallback rather than a division by zero.
    """
    moments = cv2.moments(polygon)
    area = moments["m00"]
    if area != 0.0:
        return (moments["m10"] / area, moments["m01"] / area)
    points = polygon.reshape(-1, 2).astype(np.float64)
    return (float(points[:, 0].mean()), float(points[:, 1].mean()))


class ApproachAxis:
    """The upstream axis of one Approach, for measuring how far a queue reaches.

    Motivation. Queue_Length in this System is a *count* of vehicles inside the
    Queue_Region, divided by a hand-set ``queue_capacity``. Two things go wrong with
    that. It saturates: once the count reaches the configured capacity the measure
    reads 1.0 and stops responding, exactly when conditions are worsening. And it
    cannot tell three vehicles bunched at the stop line from three vehicles strung
    out along the approach, because a count discards position entirely.

    Both problems are geometric, and the geometry is already in the configuration.
    This class turns each Approach's two polygons into a one-dimensional axis
    pointing *upstream* — away from the stop line — so any image point can be
    expressed as a fraction of how far back along the visible approach it sits:

    * ``u`` is the unit vector from the ROI centroid towards the Queue_Region
      centroid. The Queue_Region is by definition the stop-line area, so this
      points downstream; ``-u`` therefore points upstream.
    * ``origin`` is the Queue_Region centroid, i.e. the stop line, at fraction 0.
    * ``length`` is how far the ROI itself extends upstream, taken as the largest
      upstream projection over the ROI's own vertices. That is fraction 1.

    :meth:`fraction` then reports where a point lies on that axis, clamped to 0..1.

    The consequence worth stating: the normalisation comes from the ROI polygon the
    user already drew, not from a capacity they guessed, so this measure introduces
    no new tunable. It also needs no camera calibration — the fraction is relative
    to the approach's own visible extent, which is why it works on an uncalibrated
    fixed camera where a queue length in metres would not.
    """

    #: Minimum ratio of centroid separation to ROI diagonal for the axis direction to
    #: be trustworthy. The direction is derived from ``queue_centroid - roi_centroid``;
    #: if the Queue_Region is drawn CONCENTRIC with the ROI (a uniformly inset copy)
    #: rather than as a strip at the stop-line end, those two centroids nearly coincide
    #: and the resulting direction is decided by drawing noise rather than by the road.
    #: 0.10 is a conservative floor: at that separation the sign along the road axis is
    #: unambiguous for any plausibly-shaped approach polygon.
    MIN_CONDITIONING: float = 0.10

    #: Minimum heading consensus (mean resultant length of the per-vehicle heading
    #: votes) for a *measured* axis direction to be trusted. 0.5 corresponds to a
    #: clear dominant flow direction: with votes spread uniformly over a half-plane
    #: the resultant is about 0.64, so 0.5 admits genuinely noisy approaches while
    #: still rejecting the case where opposing movements cancel and leave no
    #: meaningful direction at all.
    MIN_DIRECTION_CONSENSUS: float = 0.50

    __slots__ = ("origin", "_ux", "_uy", "length", "conditioning", "direction_source")

    def __init__(
        self,
        roi: np.ndarray,
        queue_region: np.ndarray,
        direction: tuple[float, float] | None = None,
        direction_confidence: float = 1.0,
    ) -> None:
        """Build the axis for one Approach.

        ``direction``, when given, is a *measured* downstream direction — the mean
        heading of vehicles observed travelling through this Approach, obtained from
        the tracker by :class:`AxisDirectionEstimator`. Supplying it switches the
        class to a formulation that does not depend on the Queue_Region drawing at
        all, which matters because the centroid-difference fallback below is
        ill-conditioned whenever the Queue_Region was drawn as an inset copy of the
        ROI rather than as a strip across the stop line. See :meth:`well_conditioned`.

        ``direction_confidence`` is the agreement among those per-vehicle headings
        (the mean resultant length, 0..1), recorded as :attr:`conditioning` so that a
        measured axis and a geometric one expose the same trust signal.
        """
        if direction is not None:
            self._init_from_measured_direction(roi, direction, direction_confidence)
            return
        self.direction_source = "geometry"
        roi_centroid = polygon_centroid(roi)
        stop_line = polygon_centroid(queue_region)

        # Downstream direction: ROI centre -> stop-line area centre.
        dx = stop_line[0] - roi_centroid[0]
        dy = stop_line[1] - roi_centroid[1]
        norm = math.hypot(dx, dy)

        # How well-conditioned that direction is: the centroid separation as a fraction
        # of the ROI's own extent. Recorded so a caller can tell a confidently-oriented
        # axis from one whose direction is essentially arbitrary.
        vertices_all = roi.reshape(-1, 2).astype(np.float64)
        span = math.hypot(
            float(vertices_all[:, 0].max() - vertices_all[:, 0].min()),
            float(vertices_all[:, 1].max() - vertices_all[:, 1].min()),
        )
        self.conditioning = float(norm / span) if span > 0.0 else 0.0
        if norm <= 0.0:
            # Degenerate geometry (the two centroids coincide). No usable axis, so
            # every fraction reports 0 and the measure is simply inert rather than
            # raising: the configuration is still valid for every other measure.
            self._ux, self._uy = 0.0, 0.0
            self.origin = stop_line
            self.length = 0.0
            return

        # Upstream unit vector is the negation of the downstream direction.
        self._ux, self._uy = -dx / norm, -dy / norm
        self.origin = stop_line

        # How far the ROI reaches upstream of the stop line, in pixels.
        vertices = roi.reshape(-1, 2).astype(np.float64)
        projections = [
            (vx - stop_line[0]) * self._ux + (vy - stop_line[1]) * self._uy
            for vx, vy in vertices
        ]
        # Cast to a plain float: the projections are NumPy scalars, and letting one
        # through would make every derived fraction a NumPy type and put NumPy
        # objects into the Run_Log's JSON.
        self.length = float(max(max(projections), 0.0))

    def _init_from_measured_direction(
        self, roi: np.ndarray, direction: tuple[float, float], confidence: float
    ) -> None:
        """Build the axis from a measured flow direction and the ROI polygon alone.

        The Queue_Region is deliberately not consulted here. Instead the stop line is
        taken to be the ROI's own most-downstream extent along the measured heading:
        projecting every ROI vertex onto the flow direction, the largest projection is
        where the road leaves the field of view in the direction traffic is moving,
        which is the stop line (or the junction edge) by construction. The smallest is
        the far upstream edge. The span between them is the approach's visible storage.

        This removes the last hand-drawn quantity from the reach measure: it now
        depends only on the ROI outline and on where vehicles were actually seen going.
        """
        self.direction_source = "measured"
        dx, dy = float(direction[0]), float(direction[1])
        norm = math.hypot(dx, dy)
        if norm <= 0.0:
            self._ux, self._uy = 0.0, 0.0
            self.origin = polygon_centroid(roi)
            self.length = 0.0
            self.conditioning = 0.0
            return

        # Upstream unit vector: opposite the direction traffic travels.
        self._ux, self._uy = -dx / norm, -dy / norm

        vertices = roi.reshape(-1, 2).astype(np.float64)
        # Projection of every vertex onto the UPSTREAM axis. The minimum is the
        # downstream (stop-line) end, the maximum the far upstream end.
        along = vertices[:, 0] * self._ux + vertices[:, 1] * self._uy
        a_min, a_max = float(along.min()), float(along.max())
        self.length = float(max(a_max - a_min, 0.0))

        # Store the stop-line end as an actual image point, so the overlay can draw
        # the axis, and so ``origin`` keeps the same meaning as in the geometric path.
        downstream_vertex = vertices[int(along.argmin())]
        self.origin = (float(downstream_vertex[0]), float(downstream_vertex[1]))

        # ``fraction`` measures from ``origin`` along the upstream axis, and the
        # downstream-most vertex sits exactly at a_min, so no offset correction is
        # needed: (p . u) - (origin . u) == (p . u) - a_min.
        self.conditioning = float(max(0.0, min(1.0, confidence)))

    @property
    def upstream(self) -> tuple[float, float]:
        """The unit vector pointing upstream, away from the stop line.

        Exposed so the calibration command can compare a measured heading against the
        direction the geometry inferred, and so the overlay can draw the axis.
        """
        return (self._ux, self._uy)

    @property
    def well_conditioned(self) -> bool:
        """Whether this axis's *direction* can be trusted.

        ``False`` means the Queue_Region was drawn roughly concentric with the ROI, so
        the centroid difference that defines upstream-vs-downstream is dominated by
        drawing noise. The magnitudes (``length``, and every ``fraction``) are still
        computed correctly, but the **sign** may be inverted — a vehicle at the stop
        line could read near 1 instead of near 0.

        There are two remedies. Redraw the Queue_Region as a strip across the stop
        line rather than as an inset copy of the whole ROI; or, preferably, supply a
        measured ``direction`` from :class:`AxisDirectionEstimator`, which sidesteps
        the drawing entirely by taking the heading from observed vehicle motion.

        For a measured axis the stored :attr:`conditioning` is heading *consensus*
        rather than a centroid ratio, so it is tested against the stricter
        :attr:`MIN_DIRECTION_CONSENSUS`.
        """
        if self.length <= 0.0:
            return False
        if self.direction_source == "measured":
            return self.conditioning >= self.MIN_DIRECTION_CONSENSUS
        return self.conditioning >= self.MIN_CONDITIONING

    def fraction(self, point: tuple[float, float]) -> float:
        """Return how far upstream ``point`` lies, as a fraction of the ROI, 0..1.

        0 means at the stop line, 1 means at the far upstream edge of the ROI.
        Points downstream of the stop line clamp to 0, so a vehicle already in the
        junction cannot report a negative reach.
        """
        if self.length <= 0.0:
            return 0.0
        along = (float(point[0]) - self.origin[0]) * self._ux + (
            float(point[1]) - self.origin[1]
        ) * self._uy
        ratio = along / self.length
        return 0.0 if ratio < 0.0 else (1.0 if ratio > 1.0 else float(ratio))


def principal_axis(polygon: np.ndarray) -> tuple[tuple[float, float], float]:
    """Return ``(unit_vector, elongation)`` for ``polygon``'s long axis.

    A road approach ROI is an elongated quadrilateral drawn along the carriageway, so
    the direction of its longest extent *is* the direction of the road. That is a far
    more stable thing to measure than the offset between two polygon centroids: it is
    determined by the whole outline rather than by a difference of averages that can
    collapse to a few pixels.

    The axis comes from the minimum-area enclosing rectangle, whose longer side is the
    road direction. ``elongation`` is the ratio of the longer side to the shorter one,
    and is the quality measure that belongs with it: a genuinely elongated ROI
    (elongation well above 1) has an unambiguous long axis, while a roughly square ROI
    has none, and the returned vector would then be arbitrary.

    Note this fixes the axis only up to **sign** — it says which line the road runs
    along, not which end is the stop line. :class:`AxisDirectionEstimator` resolves the
    sign from observed motion, which is a far easier question to answer reliably than
    the full heading.
    """
    points = polygon.reshape(-1, 2).astype(np.float32)
    (_, _), (width, height), angle = cv2.minAreaRect(points)
    theta = math.radians(angle)
    # cv2 returns the angle of the rectangle's *width* side. Take whichever side is
    # longer as the axis.
    if height > width:
        theta += math.pi / 2.0
        long_side, short_side = height, width
    else:
        long_side, short_side = width, height
    elongation = float(long_side / short_side) if short_side > 0.0 else 0.0
    return (math.cos(theta), math.sin(theta)), elongation


class AxisDirectionEstimator:
    """Measures each Approach's downstream direction from observed vehicle motion.

    Why this exists. :class:`ApproachAxis` originally inferred the upstream direction
    from the offset between the ROI centroid and the Queue_Region centroid. That is
    only sound when the Queue_Region is drawn as a narrow strip at the stop line. When
    it is drawn as a uniformly inset copy of the ROI — a natural thing to do, and what
    happened in this project's own junction configuration — the two centroids nearly
    coincide, and the direction that results is decided by a few pixels of drawing
    noise rather than by the road. One approach in that configuration was measured with
    a centroid separation of 2.9 px, and its direction turned out to be reversed.

    The direction is, however, directly observable: vehicles on an approach travel
    *towards* the stop line, so the mean heading of the tracks passing through the ROI
    is the downstream direction, and its negation is upstream. The tracker already
    produces exactly the data needed, so no extra annotation, no camera calibration and
    no new tunable parameter is required.

    Estimator. Each track contributes **one unit-vector vote**, taken from its net
    displacement between the first and last frame it was seen in that Approach:

    .. math::
        v_k = \\frac{p_k^{last} - p_k^{first}}{\\lVert p_k^{last} - p_k^{first}\\rVert},
        \\qquad
        \\hat{d}_i = \\frac{1}{n}\\sum_{k} v_k

    Voting per track rather than per frame keeps a single long-lived or fast vehicle
    from dominating, and normalising each vote removes any speed weighting, so a
    crawling vehicle counts as much as a free-flowing one. The **mean resultant
    length** :math:`R_i = \\lVert \\hat{d}_i \\rVert` then falls out as a natural
    confidence: :math:`R_i \\to 1` means every vehicle agreed on the heading, while
    :math:`R_i \\to 0` means opposing movements cancelled and the Approach has no
    single direction (a shared ROI covering both travel directions, say).

    Tracks whose net displacement is below :attr:`MIN_TRACK_DISPLACEMENT` are ignored:
    a vehicle that was queued for its whole appearance carries no heading information,
    and including it would only add jitter.
    """

    #: Minimum net displacement, in pixels, for a track to cast a heading vote. Below
    #: this a track is either stationary or its motion is within tracker jitter.
    MIN_TRACK_DISPLACEMENT: float = 12.0

    #: Minimum number of voting tracks for an Approach's estimate to be reported. With
    #: fewer than this the mean heading is dominated by individual vehicle behaviour
    #: (a turn, a lane change) rather than by the road geometry.
    MIN_VOTES: int = 5

    __slots__ = ("_first", "_last", "_arrival")

    def __init__(self) -> None:
        # (approach, track_id) -> reference point, first and most recent sighting.
        self._first: dict[tuple[str, int], Point] = {}
        self._last: dict[tuple[str, int], Point] = {}
        # (approach, track_id) -> the point where the track was FIRST seen inside the
        # Queue_Region. Present only for vehicles that actually reached the stop-line
        # area, which is what makes them inbound; see :meth:`votes`.
        self._arrival: dict[tuple[str, int], Point] = {}

    def observe(
        self, approach: str, track_id: int, point: Point, queueing: bool = False
    ) -> None:
        """Record that ``track_id`` was seen at ``point`` while inside ``approach``.

        ``queueing`` is the assigner's verdict that the reference point lies inside
        this Approach's Queue_Region on this frame.
        """
        key = (approach, int(track_id))
        if key not in self._first:
            self._first[key] = point
        self._last[key] = point
        if queueing and key not in self._arrival:
            self._arrival[key] = point

    def votes(
        self, approach: str, inbound_only: bool = True
    ) -> list[tuple[float, float]]:
        """Return the unit heading vote of every qualifying track in ``approach``.

        With ``inbound_only`` (the default) a track votes only if it was seen to reach
        the Queue_Region, and its vote is its displacement from where it was first seen
        to where it first arrived at that region. This matters on a real junction ROI,
        which usually spans **both** directions of travel: including outbound vehicles
        makes the two flows cancel and drives the consensus towards zero, even though
        the inbound direction is perfectly well defined. Vehicles that reach the stop
        line are inbound by construction, so restricting the vote to them isolates the
        only direction the queue measure cares about.
        """
        out: list[tuple[float, float]] = []
        for (name, track_id), start in self._first.items():
            if name != approach:
                continue
            key = (name, track_id)
            if inbound_only:
                end = self._arrival.get(key)
                if end is None:
                    continue
            else:
                end = self._last[key]
            dx, dy = end[0] - start[0], end[1] - start[1]
            norm = math.hypot(dx, dy)
            if norm < self.MIN_TRACK_DISPLACEMENT:
                continue
            out.append((dx / norm, dy / norm))
        return out

    def entry_exit_direction(
        self, approach: str, roi_span: float
    ) -> tuple[tuple[float, float], float, int] | None:
        """Return ``(downstream_unit, confidence, n_tracks)`` from entry/exit geometry.

        This is the most reliable of the three estimators here, and the reason is that
        it measures a *large* separation rather than a small one. Vehicles enter an
        Approach's ROI at its upstream edge and leave at the stop line, so the centroid
        of every track's first sighting sits near one end of the approach and the
        centroid of every track's last sighting sits near the other. The vector between
        them therefore spans a good fraction of the ROI's own length, whereas the
        per-vehicle heading votes of :meth:`resolve_sign` each span only a few tens of
        pixels and the centroid-offset construction in :class:`ApproachAxis` can
        collapse to almost nothing.

        Averaging over every track also means individual turning movements, lane changes
        and dropped tracks contribute noise that shrinks with the number of vehicles,
        instead of biasing the result.

        ``confidence`` is that separation as a fraction of the ROI's own extent, which
        makes it directly comparable with :attr:`ApproachAxis.conditioning` — the same
        quantity, but measured from traffic rather than read off a hand-drawn polygon.
        """
        starts: list[Point] = []
        ends: list[Point] = []
        for (name, track_id), start in self._first.items():
            if name != approach:
                continue
            end = self._last[(name, track_id)]
            dx, dy = end[0] - start[0], end[1] - start[1]
            # A track that never moved says nothing about which end is which.
            if math.hypot(dx, dy) < self.MIN_TRACK_DISPLACEMENT:
                continue
            starts.append(start)
            ends.append(end)

        if len(starts) < self.MIN_VOTES:
            return None

        entry = (
            sum(p[0] for p in starts) / len(starts),
            sum(p[1] for p in starts) / len(starts),
        )
        exit_ = (
            sum(p[0] for p in ends) / len(ends),
            sum(p[1] for p in ends) / len(ends),
        )
        dx, dy = exit_[0] - entry[0], exit_[1] - entry[1]
        norm = math.hypot(dx, dy)
        if norm <= 0.0:
            return None
        direction = (dx / norm, dy / norm)

        # Confidence is how consistently individual vehicles move along the estimated
        # direction, not how far the entry and exit centroids happen to be apart. The
        # separation is a poor measure here because a fragmented tracker produces many
        # short tracks that begin and end mid-ROI, shrinking the separation without
        # making the direction any less certain. Agreement in sign is what actually
        # matters for orienting the axis, and it is what this reports: |2p - 1| for a
        # fraction p of tracks travelling along the estimate, so 1.0 means unanimous
        # and 0.0 an even split.
        along = 0
        against = 0
        for start, end in zip(starts, ends):
            projection = (end[0] - start[0]) * direction[0] + (
                end[1] - start[1]
            ) * direction[1]
            if projection > 0.0:
                along += 1
            elif projection < 0.0:
                against += 1
        total = along + against
        agreement = abs(along - against) / total if total else 0.0
        return direction, float(agreement), len(starts)

    def resolve_sign(
        self, approach: str, axis: tuple[float, float], inbound_only: bool = True
    ) -> tuple[float, float, int] | None:
        """Orient ``axis`` so it points downstream, using observed motion.

        Returns ``(signed_axis_x_component_sign, agreement, vote_count)`` — more
        precisely ``(sign, agreement, n)`` where ``sign`` is ``+1`` if traffic travels
        along ``axis`` and ``-1`` if against it.

        This is the robust half of the direction problem. Rather than asking the votes
        to agree on a heading in two dimensions — which they largely fail to do at a
        real junction, where turning movements and lane changes spread the headings
        widely — each vote is reduced to a single bit: did this vehicle move along the
        road axis, or against it? ``agreement`` is ``|2p - 1|`` for a majority fraction
        ``p``, so it reaches 1 when every vehicle moved the same way along the road and
        0 at an even split. A 75/25 split still yields 0.5, which is decisive for a
        binary choice even though the corresponding 2-D heading consensus would look
        hopeless.

        ``None`` when too few tracks qualified, or when the split is exactly even and
        no sign can be justified.
        """
        votes = self.votes(approach, inbound_only=inbound_only)
        if len(votes) < self.MIN_VOTES:
            return None
        along = 0
        against = 0
        for vx, vy in votes:
            projection = vx * axis[0] + vy * axis[1]
            if projection > 0.0:
                along += 1
            elif projection < 0.0:
                against += 1
        total = along + against
        if total == 0 or along == against:
            return None
        sign = 1.0 if along > against else -1.0
        agreement = abs(along - against) / total
        return sign, float(agreement), total

    def estimate(
        self, approach: str
    ) -> tuple[tuple[float, float], float, int, str] | None:
        """Return ``(downstream_unit, consensus, vote_count, basis)`` for ``approach``.

        Inbound-only votes are preferred. If too few vehicles reached the Queue_Region
        to support an estimate, the method falls back to every moving track and says so
        through ``basis``, because a lower-quality estimate that is labelled as such is
        more useful than none — but a caller can still reject it on the consensus.

        ``None`` when neither basis reaches :attr:`MIN_VOTES`, which is the honest
        answer for an Approach that carried almost no traffic in the clip: no direction
        is reported rather than one invented from two vehicles.
        """
        for inbound_only, basis in ((True, "inbound"), (False, "all-tracks")):
            votes = self.votes(approach, inbound_only=inbound_only)
            if len(votes) < self.MIN_VOTES:
                continue
            mean_x = sum(v[0] for v in votes) / len(votes)
            mean_y = sum(v[1] for v in votes) / len(votes)
            consensus = math.hypot(mean_x, mean_y)
            if consensus <= 0.0:
                continue
            return (
                (mean_x / consensus, mean_y / consensus),
                float(consensus),
                len(votes),
                basis,
            )
        return None


def build_approach_axes(config: Config) -> dict[str, ApproachAxis]:
    """Return one :class:`ApproachAxis` per Approach, keyed by name.

    Built once per run from the configured polygons, so the per-frame cost of the
    reach measure is one dot product per tracked vehicle.

    An Approach carrying a calibrated ``axis_direction`` (written by the
    ``calibrate-axes`` command from measured vehicle headings) gets the measured
    formulation; the rest fall back to the Queue_Region centroid geometry.
    """
    axes: dict[str, ApproachAxis] = {}
    for name in APPROACH_NAMES:
        approach = config.approach(name)
        axes[name] = ApproachAxis(
            as_cv_polygon(approach.roi_polygon),
            as_cv_polygon(approach.queue_region),
            direction=approach.axis_direction,
            direction_confidence=approach.axis_confidence,
        )
    return axes


def reference_point(track: Track) -> Point:
    """Return the midpoint of the bottom edge of ``track``'s box (Req 4.2).

    Delegates to :attr:`~src.tracking.Track.ref_point` so the formula exists once
    in the codebase; see the module docstring.
    """
    return track.ref_point


@dataclass(frozen=True)
class AssignedTrack:
    """One track together with the Approach it was assigned to.

    ``approach`` is ``None`` when the reference point fell outside every ROI
    polygon; such a track is excluded from every per-approach measurement
    (Requirement 4.5). ``is_queueing`` is true only for an assigned track whose
    reference point also lies inside that Approach's Queue_Region
    (Requirement 4.6), so ``is_queueing`` implies ``approach is not None`` and
    Requirement 5.8 (``Queue_Length <= Vehicle_Count``) holds structurally.
    """

    track_id: int
    vehicle_class: str
    ref_point: Point
    approach: str | None
    is_queueing: bool

    def __post_init__(self) -> None:
        if self.approach is None and self.is_queueing:
            raise ValueError(
                f"AssignedTrack {self.track_id} is marked queueing without an "
                "approach; queueing requires assignment (Requirement 4.6)"
            )

    @property
    def is_assigned(self) -> bool:
        """Whether this track counts towards a per-approach measurement."""
        return self.approach is not None


@dataclass(frozen=True)
class OverlapEvent:
    """A track whose reference point fell inside two or more ROI polygons.

    Recorded in the Run_Log (Requirement 4.4) because overlapping ROIs are a
    calibration fault, not a run-time condition: the tiebreak keeps the run going,
    and the event is what tells the operator which Approaches to redraw.
    """

    track_id: int
    frame_index: int
    candidates: tuple[str, ...]
    chosen: str

    def __post_init__(self) -> None:
        if len(self.candidates) < 2:
            raise ValueError(
                "an OverlapEvent describes two or more candidate approaches, got "
                f"{list(self.candidates)}"
            )
        if self.chosen not in self.candidates:
            raise ValueError(
                f"OverlapEvent chosen approach {self.chosen!r} must be one of its "
                f"candidates {list(self.candidates)}"
            )

    def as_json_obj(self) -> dict[str, Any]:
        """Return the JSON-encodable form the Results_Store writes (Req 4.4)."""
        return {
            "track_id": self.track_id,
            "frame_index": self.frame_index,
            "candidates": list(self.candidates),
            "chosen": self.chosen,
        }


@dataclass(frozen=True)
class _ApproachGeometry:
    """Precomputed geometry of one Approach, built once per run."""

    name: str
    roi: np.ndarray
    queue_region: np.ndarray
    centroid: tuple[float, float]


class ApproachAssigner:
    """Assigns tracks to Approaches from the configured ROI polygons (Req 4.2-4.6).

    Geometry is read from ``Config`` once, in Approach order North, East, South,
    West. That order is what makes the two tiebreaks deterministic: the candidate
    list of an :class:`OverlapEvent` is reported in it, and two candidate
    centroids at an exactly equal distance resolve to the earlier Approach in it,
    matching the fixed order the controllers use.

    The Config_Loader's validation is total, so this class re-checks nothing about
    ranges or containment; it only requires that the four Approaches are present,
    which ``config.approach`` reports as a :class:`~src.errors.ConfigError`.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._geometry: tuple[_ApproachGeometry, ...] = tuple(
            self._build_geometry(config, name) for name in APPROACH_NAMES
        )

    @staticmethod
    def _build_geometry(config: Config, name: str) -> _ApproachGeometry:
        approach = config.approach(name)             # ConfigError when absent
        roi = as_cv_polygon(approach.roi_polygon)
        return _ApproachGeometry(
            name=approach.name,
            roi=roi,
            queue_region=as_cv_polygon(approach.queue_region),
            centroid=polygon_centroid(roi),
        )

    # -- introspection -----------------------------------------------------

    @property
    def approach_names(self) -> tuple[str, ...]:
        """The Approach names in the fixed order this assigner iterates."""
        return tuple(geometry.name for geometry in self._geometry)

    @property
    def centroids(self) -> dict[str, tuple[float, float]]:
        """ROI polygon centroids by Approach name, computed once in ``__init__``."""
        return {g.name: g.centroid for g in self._geometry}

    def roi_polygon(self, name: str) -> np.ndarray:
        """Return the precomputed ROI contour of Approach ``name``."""
        return self._geometry_for(name).roi

    def queue_polygon(self, name: str) -> np.ndarray:
        """Return the precomputed Queue_Region contour of Approach ``name``."""
        return self._geometry_for(name).queue_region

    # -- assignment --------------------------------------------------------

    def assign(
        self, tracks: Iterable[Track], frame_index: int = 0
    ) -> tuple[list[AssignedTrack], list[OverlapEvent]]:
        """Assign every track in ``tracks`` (Requirements 4.2 to 4.6).

        Returns one :class:`AssignedTrack` per input track, in input order, plus
        the :class:`OverlapEvent` list for this frame. ``frame_index`` only labels
        those events for the Run_Log; assignment itself is frame-independent.
        """
        assigned: list[AssignedTrack] = []
        overlaps: list[OverlapEvent] = []

        for track in tracks:
            ref = reference_point(track)
            candidates = [g for g in self._geometry if point_in_polygon(g.roi, ref)]

            if not candidates:
                # Requirement 4.5: outside every ROI, so outside every measurement.
                assigned.append(
                    AssignedTrack(
                        track_id=track.track_id,
                        vehicle_class=track.vehicle_class,
                        ref_point=ref,
                        approach=None,
                        is_queueing=False,
                    )
                )
                continue

            if len(candidates) == 1:
                chosen = candidates[0]               # Requirement 4.3
            else:
                chosen = self._nearest_centroid(candidates, ref)   # Requirement 4.4
                overlaps.append(
                    OverlapEvent(
                        track_id=track.track_id,
                        frame_index=frame_index,
                        candidates=tuple(g.name for g in candidates),
                        chosen=chosen.name,
                    )
                )

            assigned.append(
                AssignedTrack(
                    track_id=track.track_id,
                    vehicle_class=track.vehicle_class,
                    ref_point=ref,
                    approach=chosen.name,
                    # Requirement 4.6: queueing iff inside the assigned Approach's
                    # own queue region — never a neighbour's.
                    is_queueing=point_in_polygon(chosen.queue_region, ref),
                )
            )

        return assigned, overlaps

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _nearest_centroid(
        candidates: Sequence[_ApproachGeometry], ref: Point
    ) -> _ApproachGeometry:
        """Return the candidate whose ROI centroid is nearest to ``ref`` (Req 4.4).

        ``candidates`` arrives in the fixed Approach order, and the comparison is
        strict, so an exact distance tie resolves to the earlier Approach.
        """
        best = candidates[0]
        best_distance = math.hypot(
            best.centroid[0] - ref[0], best.centroid[1] - ref[1]
        )
        for candidate in candidates[1:]:
            distance = math.hypot(
                candidate.centroid[0] - ref[0], candidate.centroid[1] - ref[1]
            )
            if distance < best_distance:
                best, best_distance = candidate, distance
        return best

    def _geometry_for(self, name: str) -> _ApproachGeometry:
        for geometry in self._geometry:
            if geometry.name == name:
                return geometry
        raise ConfigError(f"no approach named {name!r} in configuration")


# ---------------------------------------------------------------------------
# Viewpoint mismatch check (Requirement 16.5)
# ---------------------------------------------------------------------------

#: How many frames the coverage check looks at by default. Enough to span a
#: whole clip at a coarse stride without paying for a full decode-and-track pass,
#: since this check runs once per candidate video before it enters the set.
DEFAULT_COVERAGE_SAMPLE_COUNT = 60


@dataclass(frozen=True)
class RoiCoverageReport:
    """What a sampled pass over one video saw in each configured ROI (Req 16.5).

    ``observations`` counts assigned-track sightings per Approach over the sampled
    frames, not distinct vehicles: the question this check answers is only whether
    the configured ROI covers road that traffic actually uses under this
    viewpoint, so one sighting is enough to clear an Approach.

    ``unassigned`` counts sightings that fell outside every ROI. A video whose
    ROIs are stale typically shows both symptoms at once — an empty Approach and a
    pile of unassigned tracks — so keeping the second number makes the report
    diagnostic rather than just a verdict.
    """

    video_path: str
    frames_sampled: int
    observations: dict[str, int]
    unassigned: int

    @property
    def uncovered(self) -> tuple[str, ...]:
        """Approaches that saw no assigned track, in fixed Approach order."""
        return tuple(
            name for name in APPROACH_NAMES if self.observations.get(name, 0) == 0
        )

    @property
    def matches(self) -> bool:
        """Whether every configured ROI saw traffic, so the video may be used."""
        return not self.uncovered

    def mismatch_message(self) -> str | None:
        """Return the operator-facing mismatch text, or ``None`` when it matches.

        Requirement 16.5 asks the System to *report* that the configuration does
        not match the video; the message is built here so the caller that excludes
        the video and the Run_Log that records the exclusion state it identically.
        """
        uncovered = self.uncovered
        if not uncovered:
            return None
        return (
            f"configuration does not match video {self.video_path}: no assigned "
            f"track appeared in the ROI of {', '.join(uncovered)} across "
            f"{self.frames_sampled} sampled frames "
            f"({self.unassigned} track sighting(s) fell outside every ROI); "
            "excluding this video from the evaluation input set"
        )


def coverage_sample_stride(frame_count: int, sample_count: int) -> int:
    """Return the frame stride that spreads ``sample_count`` samples over a video.

    Sampling is strided rather than seeked because :class:`VideoIngestor` owns
    frame indexing and reads strictly forward (Requirement 1.2); a seek is
    codec-dependent and would put the index sequence in doubt.
    """
    if sample_count < 1:
        raise ValueError(f"sample_count must be at least 1, got {sample_count}")
    if frame_count <= sample_count:
        return 1
    return max(1, frame_count // sample_count)


def roi_coverage_report(
    video_path: str,
    config: Config,
    *,
    detector: Detector | None = None,
    tracker: Tracker | None = None,
    sample_count: int = DEFAULT_COVERAGE_SAMPLE_COUNT,
    stride: int | None = None,
) -> RoiCoverageReport:
    """Sample ``video_path`` and report which ROIs saw no traffic (Req 16.5).

    Frames are read in order and every ``stride``-th one is detected, tracked, and
    assigned; ``stride`` defaults to spreading ``sample_count`` samples across the
    whole clip. When the container reports no frame count — some codecs do not —
    the stride falls back to 1 and the pass stops after ``sample_count`` frames,
    which inspects the opening of the clip rather than nothing at all.

    ``detector`` and ``tracker`` are injection seams, exactly as in
    :func:`~src.overlay.run_tracking_video`: fakes run this check with no weights
    and no GPU. With ``tracker=None`` a :class:`~src.tracking.ByteTrackTracker` is
    built and closed here, and a detector is then unnecessary because Ultralytics'
    persistent tracking detects and associates in one pass.

    Tracks are used rather than raw detections so the check answers the same
    question the pipeline does — which Approach a *tracked* vehicle is assigned to
    — and so a stale ROI cannot be cleared by a detection the tracker discards.
    Identity churn across a strided sample is harmless here: only presence counts.
    """
    if sample_count < 1:
        raise ValueError(f"sample_count must be at least 1, got {sample_count}")
    if stride is not None and stride < 1:
        raise ValueError(f"stride must be at least 1, got {stride}")

    assigner = ApproachAssigner(config)
    ingestor = VideoIngestor(video_path, config)
    info = ingestor.info

    resolved_stride = (
        stride
        if stride is not None
        else coverage_sample_stride(info.frame_count, sample_count)
    )
    # An unknown frame count leaves no basis for spreading the samples out, so the
    # pass is capped instead of striding blindly over an unknown length.
    max_samples = sample_count if info.frame_count <= 0 else None

    owns_tracker = tracker is None
    if tracker is None:
        tracker = ByteTrackTracker(config)

    observations = {name: 0 for name in APPROACH_NAMES}
    unassigned = 0
    frames_sampled = 0
    try:
        for index, frame in ingestor.frames():
            if index % resolved_stride != 0:
                continue
            detections = detector.detect(frame) if detector is not None else []
            tracks = tracker.update(frame, detections)
            assigned, _overlaps = assigner.assign(tracks, frame_index=index)

            frames_sampled += 1
            for track in assigned:
                if track.approach is None:
                    unassigned += 1
                else:
                    observations[track.approach] += 1

            if max_samples is not None and frames_sampled >= max_samples:
                break
    finally:
        ingestor.close()
        if owns_tracker:
            close = getattr(tracker, "close", None)
            if callable(close):
                close()

    return RoiCoverageReport(
        video_path=video_path,
        frames_sampled=frames_sampled,
        observations=observations,
        unassigned=unassigned,
    )


def check_roi_coverage(
    video_path: str,
    config: Config,
    *,
    detector: Detector | None = None,
    tracker: Tracker | None = None,
    sample_count: int = DEFAULT_COVERAGE_SAMPLE_COUNT,
    stride: int | None = None,
) -> list[str]:
    """Return the Approaches whose ROI saw no assigned track (Requirement 16.5).

    An empty list means the configured geometry still matches this video; a
    non-empty list is the mismatch the caller reports before excluding the video
    from the evaluation input set. :func:`roi_coverage_report` returns the same
    pass with its counts and its ready-made message when the caller wants to say
    *why*.
    """
    report = roi_coverage_report(
        video_path,
        config,
        detector=detector,
        tracker=tracker,
        sample_count=sample_count,
        stride=stride,
    )
    return list(report.uncovered)


__all__ = [
    "DEFAULT_COVERAGE_SAMPLE_COUNT",
    "ApproachAssigner",
    "ApproachAxis",
    "AssignedTrack",
    "OverlapEvent",
    "RoiCoverageReport",
    "as_cv_polygon",
    "build_approach_axes",
    "check_roi_coverage",
    "coverage_sample_stride",
    "point_in_polygon",
    "polygon_centroid",
    "reference_point",
    "roi_coverage_report",
]
