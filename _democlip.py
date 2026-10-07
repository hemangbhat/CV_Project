import cv2, pathlib

SRC = "videos/bellevue_116th_final.mp4"
DST = pathlib.Path("videos/_demo_short.mp4")
START, COUNT, FPS = 0, 600, 30.0  # 20 s

cap = cv2.VideoCapture(SRC)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
vw = cv2.VideoWriter(str(DST), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
cap.set(cv2.CAP_PROP_POS_FRAMES, START)
n = 0
for _ in range(COUNT):
    ok, fr = cap.read()
    if not ok:
        break
    vw.write(fr); n += 1
cap.release(); vw.release()
print(f"wrote {n} frames -> {DST}")
