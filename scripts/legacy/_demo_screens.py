"""Produce the demonstration screenshots for report/ (Requirement 17.1).

Builds a short clip from the final video, runs the adaptive controller with the
annotated overlay, and saves a few annotated frames into report/.
Self-contained so it can run in its own terminal.
"""
from __future__ import annotations
import pathlib
import cv2

from src.config import load_config
from src.main import Pipeline

SRC = "videos/bellevue_116th_final.mp4"
SHORT = pathlib.Path("videos/_demo_short.mp4")
REPORT = pathlib.Path("report")


def build_short(count: int = 900, fps: float = 30.0) -> None:
    cap = cv2.VideoCapture(SRC)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(str(SHORT), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = 0
    while n < count:
        ok, fr = cap.read()
        if not ok:
            break
        vw.write(fr)
        n += 1
    cap.release()
    vw.release()
    print(f"short clip: {n} frames -> {SHORT}", flush=True)


def main() -> None:
    cfg = load_config("config/default.json")
    if not SHORT.exists():
        build_short()

    out_video = pathlib.Path("results/videos/_demo_adaptive.mp4")
    pipe = Pipeline(str(SHORT), cfg, "adaptive", write_video=True,
                    display=False, out_path=str(out_video))
    pipe.run()
    print(f"annotated video -> {out_video}", flush=True)

    # extract three spread frames from the annotated video into report/
    cap = cv2.VideoCapture(str(out_video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    grabs = {int(total * f): name for f, name in
             ((0.35, "demo_overlay_1.png"),
              (0.6, "demo_overlay_2.png"),
              (0.85, "demo_overlay_3.png"))}
    idx = 0
    saved = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if idx in grabs:
            cv2.imwrite(str(REPORT / grabs[idx]), fr)
            saved += 1
        idx += 1
    cap.release()
    print(f"saved {saved} screenshots into {REPORT}", flush=True)


if __name__ == "__main__":
    main()
