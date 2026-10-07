import cv2, pathlib, time
SRC = "videos/_candidates/b116_0908.mp4"
DST = pathlib.Path("videos/bellevue_116th_final.mp4")
START, COUNT, FPS = 200, 7200, 30.0
cap = cv2.VideoCapture(SRC)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
vw = cv2.VideoWriter(str(DST), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
cap.set(cv2.CAP_PROP_POS_FRAMES, START)
n, t0 = 0, time.time()
while n < COUNT:
    ok, fr = cap.read()
    if not ok: break
    vw.write(fr); n += 1
cap.release(); vw.release()
print(f"wrote {n} frames -> {DST}  {DST.stat().st_size/1e6:.1f} MB in {time.time()-t0:.0f}s")
