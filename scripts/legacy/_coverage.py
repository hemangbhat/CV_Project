import os
import sys
import json
import numpy as np


def in_poly(pts, poly):
    """Vectorised even-odd point-in-polygon; poly is (n,2)."""
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi
        )
        inside ^= cond
        j = i
    return inside


cfg = json.load(open("config/bellevue_116th.json"))
pts = np.load("results/_refpoints.npy").astype(np.float64)
total = len(pts)

lines = [f"{total} reference points", ""]
assigned = np.zeros(total, dtype=bool)
for ap in cfg["approaches"]:
    roi = np.array(ap["roi_polygon"], dtype=np.float64)
    q = np.array(ap["queue_region"], dtype=np.float64)
    m_roi = in_poly(pts, roi)
    m_q = in_poly(pts, q)
    assigned |= m_roi
    lines.append(
        f"{ap['name']:6s}  ROI {m_roi.sum():5d} pts ({100*m_roi.mean():4.1f}%)   "
        f"queue {m_q.sum():5d} pts   sat={ap['saturation_count']} qcap={ap['queue_capacity']}"
    )
lines.append("")
lines.append(f"assigned to some ROI: {assigned.sum()}/{total} ({100*assigned.mean():.1f}%)")
lines.append(f"unassigned: {total-assigned.sum()} ({100*(1-assigned.mean()):.1f}%)")

sys.stdout.write("\n".join(lines) + "\n")
sys.stdout.flush()
os._exit(0)
