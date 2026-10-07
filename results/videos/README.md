# Demo videos

`DEMO_final_S4_busy.mp4` (the final demo: S4 configuration, YOLOv8m track cache, research
overlay) is **not committed**, because it is too large for this repository's LFS upload
access. Regenerate it in about a minute from the committed track cache:

```bash
python -m src.main control --video videos/bellevue_116th_busy.mp4 --config config/final/S4.json \
  --track-cache results/track_cache/bellevue_116th_busy__yolov8m__c0p30.json.gz \
  --overlay demo --no-display --out results/videos/DEMO_final_S4_busy.mp4
```

A still from it is in `report/demo_frame.png`. The other two `.mp4` files here are pre-audit
demos (legacy overlay and measurement).
