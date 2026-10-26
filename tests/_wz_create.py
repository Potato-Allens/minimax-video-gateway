# -*- coding: utf-8 -*-
"""R711 regression: /create studio page + the API surface it drives.

Read-only: submits 2 dry_run requests (nothing queued, nothing consumed) and
checks one already-succeeded clip. No new generation is triggered here.
"""
import json, os, sys, urllib.request

BASE = "http://127.0.0.1:8808"
KEY = os.environ.get("WZMM_KEY", "")
TID = "vid_1864fee2b269c68abc80cdc2"
ok = 0
bad = []


def get(path, key=KEY, raw=False):
    r = urllib.request.Request(BASE + path)
    if key:
        r.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read()
            ct = resp.headers.get("content-type", "")
            return resp.status, (body if raw else body.decode("utf-8", "replace")), ct
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("content-type", "")


def post(path, body, key=KEY):
    data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method="POST")
    if key:
        r.add_header("Authorization", "Bearer " + key)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print("PASS", name, detail)
    else:
        bad.append(name)
        print("FAIL", name, detail)


st, html, _ = get("/create", key=None)
check("create page 200", st == 200, str(st))
check("has prompt box", 'id="prompt"' in html)
check("has generate button", 'id="go"' in html and "\u751f\u6210\u89c6\u9891" in html)
check("calls generations api", "/v1/videos/generations" in html)
check("sends bearer auth", "Authorization" in html)
check("no key leaked into html", KEY not in html)

st, oa, _ = get("/openapi.json", key=None)
check("openapi lists /create", '"/create"' in oa, str(st))

st, gal, _ = get("/gallery?key=" + KEY)
check("gallery links to studio", 'href="/create"' in gal, str(st))

st, lib, _ = get("/library?key=" + KEY)
check("library still serves", st == 200, str(st))

st, models, _ = get("/v1/models")
mj = json.loads(models)
mm = [m for m in mj.get("data", []) if m["id"] == "MiniMax-Hailuo-2.3"]
check("model list has Hailuo-2.3", bool(mm))
check("model is 1080P capable", bool(mm and mm[0].get("supports_1080p")), str(mm[:1])[:80])
check("only_1080p enforced", mj.get("only_1080p") is True)

st, body = post("/v1/videos/generations", {"prompt": "test", "dry_run": True,
                                           "model": "MiniMax-Hailuo-2.3",
                                           "resolution": "1080P", "duration": 6})
dj = json.loads(body)
check("dry_run 1080P accepted", st == 200 and dj.get("charged") is False, str(st))

st, body = post("/v1/videos/generations", {"prompt": "test", "dry_run": True,
                                           "model": "MiniMax-Hailuo-2.3", "resolution": "768P"})
check("768P rejected", st == 400, str(st))

st, _ = post("/v1/videos/generations", {"prompt": "x", "dry_run": True}, key=None)
check("no key is 401", st == 401, str(st))
st, _ = post("/v1/videos/generations", {"prompt": "x", "dry_run": True}, key="sk-wz-nope")
check("bad key is 401", st == 401, str(st))

st, meta, _ = get("/v1/videos/%s/meta" % TID)
m = json.loads(meta) if st == 200 else {}
check("sample clip succeeded", st == 200 and m.get("status") == "succeeded", str(st))
check("sample clip is 1920x1080", m.get("width_i") == 1920 and m.get("height_i") == 1080,
      "%sx%s" % (m.get("width_i"), m.get("height_i")))

st, blob, ct = get("/v1/videos/%s/content" % TID, raw=True)
check("content streams mp4", st == 200 and ct.startswith("video/mp4"), ct)
check("content bytes match meta", m.get("bytes") == len(blob), "%s vs %s" % (m.get("bytes"), len(blob)))

st, thumb, ct = get("/v1/videos/%s/thumb" % TID, raw=True)
check("thumb is jpeg", st == 200 and ct.startswith("image/jpeg"), ct)

st, stats, _ = get("/v1/library/stats")
sj = json.loads(stats) if st == 200 else {}
check("legacy farm purged", sj.get("files") == 0, str(sj.get("files")))

print("----")
print("ok=%d bad=%d" % (ok, len(bad)))
if bad:
    print("failed:", ", ".join(bad))
    sys.exit(1)
print("ALL GREEN")