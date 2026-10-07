"""Measure per-approach inbound demand from the vision pipeline's track caches.

A vehicle counts as inbound demand on an approach when its track is seen inside that
approach's Queue_Region (it reached the stop line), which excludes outbound traffic
crossing the ROI. Counted per distinct Track_ID across the cached clips; ByteTrack
identity switches inflate absolute counts, so only the per-approach *shares* are used
to shape the ``calibrated`` SUMO scenario, with the absolute level set separately.

    python -m sim.calibrate_demand
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import APPROACH_NAMES, load_config
from src.lane_analysis import ApproachAssigner
from src.track_cache import CachedTracker
from sim.scenario import VIDEO_DEMAND_PATH

CONFIG = "config/bellevue_116th_calibrated.json"
CACHES = sorted(Path("results/track_cache").glob("bellevue_116th_*__yolov8n__c0p30.json.gz"))


def main() -> None:
    config = load_config(CONFIG)
    assigner = ApproachAssigner(config)
    totals = {a: 0 for a in APPROACH_NAMES}
    seconds = 0.0
    per_clip = {}
    for cache in CACHES:
        tracker = CachedTracker(str(cache), config)
        inbound: dict[str, set[int]] = {a: set() for a in APPROACH_NAMES}
        for index in range(tracker.frame_count):
            assigned, _ = assigner.assign(tracker.update(None, []), frame_index=index)
            for t in assigned:
                if t.is_queueing:
                    inbound[t.approach].add(t.track_id)
        clip_seconds = tracker.frame_count / 30.0
        seconds += clip_seconds
        per_clip[cache.name] = {a: len(v) for a, v in inbound.items()}
        for a in APPROACH_NAMES:
            totals[a] += len(inbound[a])
    total = sum(totals.values())
    shares = {a: totals[a] / total for a in APPROACH_NAMES}
    out = {
        "source": [c.name for c in CACHES],
        "seconds": seconds,
        "inbound_tracks": totals,
        "per_clip": per_clip,
        "tracks_per_hour": {a: totals[a] / seconds * 3600.0 for a in APPROACH_NAMES},
        "shares": shares,
        "note": "Track counts include ByteTrack identity switches; only the shares are used.",
    }
    VIDEO_DEMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    VIDEO_DEMAND_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
