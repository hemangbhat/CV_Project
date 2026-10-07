import cv2, numpy as np
from src.config import load_config
from src.detection import YoloDetector

cfg = load_config("config/bellevue_116th.json")
det = YoloDetector(cfg)
CELL = 24
cols, rows = 1280 // CELL, 720 // CELL
occ = np.zeros((rows, cols), dtype=int)      # frames in which cell was occupied

cap = cv2.VideoCapture("videos/bellevue_116th_dev.mp4")
samples, i, step = 0, 0, 15
while True:
    ok, fr = cap.read()
    if not ok: break
    if i % step == 0:
        seen = set()
        for d in det.detect(fr):
            rx, ry = (d.x1 + d.x2) // 2, d.y2
            seen.add((min(ry // CELL, rows-1), min(rx // CELL, cols-1)))
        for r, c in seen: occ[r, c] += 1
        samples += 1
    i += 1
cap.release()

frac = occ / max(samples, 1)
static = np.argwhere(frac > 0.5)
print(f"sampled {samples} frames\n")
print(f"cells occupied in >50% of frames (likely PARKED/static): {len(static)}")
for r, c in static:
    print(f"  x={c*CELL:4d}-{c*CELL+CELL:4d}  y={r*CELL:4d}-{r*CELL+CELL:4d}   occupancy={frac[r,c]*100:.0f}%")
