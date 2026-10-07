import http.cookiejar, urllib.request, pathlib, time, cv2, numpy as np

FILE_ID = "1Uo2AsIXVeAu-2QoUvk8c1OW65rKuBlIE"   # 2017-09-11 17:08, evening rush
RAW = pathlib.Path("videos/_candidates/bellevue_116th_NE12th_1708.mp4")

if not RAW.exists():
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA)]
    r = op.open(f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download&confirm=t", timeout=120)
    total = int(r.headers.get("Content-Length", 0)); t0 = time.time()
    with open(RAW, "wb") as f:
        while True:
            ch = r.read(1 << 20)
            if not ch: break
            f.write(ch)
    r.close()
    print(f"downloaded {RAW.stat().st_size/1e6:.1f} MB in {time.time()-t0:.0f}s")
else:
    print("already have raw clip")

# timestamp sweep -> find the longest gap-free stretch
cap = cv2.VideoCapture(str(RAW)); ts = []
while True:
    ok, _ = cap.read()
    if not ok: break
    ts.append(cap.get(cv2.CAP_PROP_POS_MSEC))
cap.release()
t = np.array(ts); d = np.diff(t)
bad = np.where(~np.isfinite(d) | (d > 100) | (d <= 0))[0]
print(f"frames={len(t)}  span={(t[-1]-t[0])/1000:.0f}s  bad intervals={len(bad)}")

bounds = np.concatenate(([0], bad, [len(t) - 1]))
runs = [(bounds[i] + 1, bounds[i+1]) for i in range(len(bounds) - 1)]
runs = [(a, b) for a, b in runs if b - a > 100]
best = max(runs, key=lambda ab: ab[1] - ab[0])
print(f"longest clean run: frames {best[0]}..{best[1]}  ({best[1]-best[0]} frames, {(best[1]-best[0])/30:.0f}s)")
np.save("results/_run1708.npy", np.array(best))
