import cv2, numpy as np
from src.config import load_config
from src.detection import YoloDetector

P = "videos/bellevue_116th_dev.mp4"
cfg = load_config("config/default.json")
det = YoloDetector(cfg)

CELL = 40                      # 1280/40 = 32 cols, 720/40 = 18 rows
grid = np.zeros((720 // CELL, 1280 // CELL), dtype=int)
pts = []

cap = cv2.VideoCapture(P)
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
step = 10                       # every 10th frame -> ~720 samples over 4 min
i = 0
while True:
    ok, fr = cap.read()
    if not ok: break
    if i % step == 0:
        for d in det.detect(fr):
            # reference point = bottom-edge midpoint, same as ApproachAssigner
            rx, ry = (d.x1 + d.x2) // 2, d.y2
            pts.append((rx, ry))
            grid[min(ry // CELL, grid.shape[0]-1), min(rx // CELL, grid.shape[1]-1)] += 1
    i += 1
cap.release()

print(f"sampled {i//step} frames, {len(pts)} vehicle reference points\n")
print("traffic heatmap (rows = y/40, cols = x/40).  . = 0  digits = log scale")
print("      " + "".join(f"{c//10 if c%10==0 else ' '}" for c in range(grid.shape[1])))
print("      " + "".join(str(c % 10) for c in range(grid.shape[1])))
for r in range(grid.shape[0]):
    row = ""
    for c in range(grid.shape[1]):
        v = grid[r, c]
        row += "." if v == 0 else ("#" if v >= 100 else str(min(9, 1 + v // 12)))
    print(f"y{r*CELL:4d} {row}")
np.save("results/_refpoints.npy", np.array(pts))
print("\nsaved reference points -> results/_refpoints.npy")
