import http.cookiejar, urllib.request, pathlib, cv2, numpy as np

CANDS = {
    "0908": "1eRujpQWU7Q5F9yfPMuaH4rcXga_l7VOn",
    "1608": "1QFcCPPBHSXVXUzkSHrNaF7dZJvF3UhR8",
    "1208": "1c3lvwz7FfTnTEVyLaopPg6NjpJ2pAn7Q",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

def fetch(fid, dest):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA)]
    r = op.open(f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t", timeout=120)
    with open(dest, "wb") as f:
        while True:
            ch = r.read(1 << 20)
            if not ch: break
            f.write(ch)
    r.close()

for tag, fid in CANDS.items():
    p = pathlib.Path(f"videos/_candidates/b116_{tag}.mp4")
    if not p.exists(): fetch(fid, p)
    cap = cv2.VideoCapture(str(p)); ts = []
    while True:
        ok, _ = cap.read()
        if not ok: break
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC))
    cap.release()
    t = np.array(ts); d = np.diff(t)
    bad = np.where(~np.isfinite(d) | (d > 100) | (d <= 0))[0]
    bounds = np.concatenate(([0], bad, [len(t) - 1]))
    runs = [(bounds[i] + 1, bounds[i+1]) for i in range(len(bounds) - 1)]
    runs = [(a, b) for a, b in runs if b - a > 100]
    if not runs:
        print(f"{tag}: no clean run"); continue
    a, b = max(runs, key=lambda ab: ab[1] - ab[0])
    ivl = np.diff(t[a:b]); ivl = ivl[np.isfinite(ivl) & (ivl > 0)]
    print(f"{tag}: {p.stat().st_size/1e6:6.1f} MB  {len(t)} frames  "
          f"clean run {a}..{b} = {b-a} frames ({(b-a)/30:.0f}s)  "
          f"interval {ivl.mean():.2f}+-{ivl.std():.2f} ms")
