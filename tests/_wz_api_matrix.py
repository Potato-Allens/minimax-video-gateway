import json, time, urllib.request, urllib.error, sys

import os
BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
KEY = os.environ.get("WZMM_KEY", "")
ADMIN = (open("/opt/wzmm/admin.key").read().strip()
         if os.path.exists("/opt/wzmm/admin.key") else "")
VID = "vid_a9ca975b82cc69b524b0e263"

def call(method, path, body=None, key=KEY, admin=False):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if admin:
        r.add_header("X-Admin-Token", key or ADMIN)
    elif key:
        r.add_header("Authorization", "Bearer " + key)
    if data:
        r.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read(), round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), round(time.time() - t0, 2)
    except Exception as e:
        return 0, str(e).encode(), round(time.time() - t0, 2)

rows = []
def check(name, method, path, body=None, key=None, want=(200,), show=0, admin=False):
    if not admin and key is None:
        key = KEY
    if admin and key is None:
        key = ADMIN
    code, raw, dt = call(method, path, body, key, admin)
    ok = code in want
    txt = raw[:show].decode("utf-8", "replace") if show else ""
    rows.append((ok, name, method, path, code, dt))
    print(("PASS" if ok else "FAIL"), f"{code:>4}", f"{dt:>5.2f}s", method.ljust(6), path[:58], ("| " + txt.replace("\n", " ")) if show else "")
    return code, raw

check("healthz no-auth", "GET", "/healthz", want=(200,), show=120)
check("models no-auth -> 401", "GET", "/v1/models", key="", want=(401, 403))
check("models", "GET", "/v1/models", want=(200,), show=130)
check("pricing 1080P/6s", "GET", "/v1/pricing?model=MiniMax-Hailuo-2.3&resolution=1080P&duration=6", want=(200,), show=170)
check("pricing 768P/10s -> 400 (only-1080p)", "GET", "/v1/pricing?model=MiniMax-Hailuo-2.3&resolution=768P&duration=10", want=(400,), show=110)
check("pricing H3 2K/15s -> 400 (h3 disabled)", "GET", "/v1/pricing?model=MiniMax-H3&resolution=2K&duration=15", want=(400,), show=110)
check("pricing bad model -> 400", "GET", "/v1/pricing?model=NOPE", want=(400,), show=110)
check("pricing 1080P/10s -> 400", "GET", "/v1/pricing?model=MiniMax-Hailuo-2.3&resolution=1080P&duration=10", want=(400,), show=140)
check("pricing H3 1080P -> 400", "GET", "/v1/pricing?model=MiniMax-H3&resolution=1080P&duration=6", want=(400,), show=120)

check("create bad model -> 400", "POST", "/v1/videos/generations", {"model": "NOPE", "prompt": "x"}, want=(400,), show=100)
check("create no prompt -> 400", "POST", "/v1/videos/generations", {"model": "MiniMax-Hailuo-2.3"}, want=(400,), show=100)
check("create 1080P/10s -> 400", "POST", "/v1/videos/generations", {"model": "MiniMax-Hailuo-2.3", "prompt": "x", "resolution": "1080P", "duration": 10}, want=(400,), show=120)
check("batch empty -> 400", "POST", "/v1/videos/batch", {"items": []}, want=(400,), show=100)
check("batch >64 -> 400", "POST", "/v1/videos/batch", {"items": [{"prompt": "x"}] * 65}, want=(400,), show=100)

check("list videos", "GET", "/v1/videos/generations?limit=2", want=(200,), show=120)
check("get video", "GET", f"/v1/videos/generations/{VID}", want=(200,), show=100)
check("video status", "GET", f"/v1/videos/{VID}/status", want=(200,), show=100)
check("video content", "GET", f"/v1/videos/{VID}/content", want=(200,))
check("batch missing -> 404", "GET", "/v1/videos/batch/batch_nope", want=(404,))
check("tools list", "GET", "/v1/tools", want=(200,), show=90)
check("tools call query", "POST", "/v1/tools/call", {"tool": "matrix_query_video_generation", "arguments": {"task_id": VID}}, want=(200,), show=90)
check("tools call unknown -> 400", "POST", "/v1/tools/call", {"tool": "nope"}, want=(400,))
check("play html", "GET", "/play", want=(200,), show=60)
check("metrics", "GET", "/metrics", want=(200,), show=60)

check("admin stats", "GET", "/admin/stats", admin=True, show=150)
check("admin pool", "GET", "/admin/pool", admin=True, show=100)
check("admin credits", "GET", "/admin/credits", admin=True, show=90)
check("admin token-age", "GET", "/admin/token-age", admin=True, show=120)
check("admin usage", "GET", "/admin/usage?days=3", admin=True, show=90)
check("admin tasks", "GET", "/admin/tasks?limit=2", admin=True, show=90)
check("admin keys wrong -> 403", "GET", "/admin/keys", key="bad-admin", admin=True, want=(401, 403))

code, raw = check("admin create key", "POST", "/admin/keys", {"name": "matrix-test", "rpm": 5}, admin=True, show=170)
kid = json.loads(raw).get("id") if code == 200 else None
check("admin list keys", "GET", "/admin/keys", admin=True, show=100)
if kid:
    check("admin patch key", "PATCH", f"/admin/keys/{kid}", {"rpm": 7, "quota": 3}, admin=True, show=170)
check("admin patch unknown -> 404", "PATCH", "/admin/keys/key_nope", {"rpm": 1}, admin=True, want=(404,))
if kid:
    check("admin delete key", "DELETE", f"/admin/keys/{kid}", admin=True, show=80)
check("admin reload-pool", "POST", "/admin/reload-pool", admin=True, show=110)
check("admin apply-liveness", "POST", "/admin/apply-liveness", {"dead": [], "ok": []}, admin=True, show=80)
check("admin merge-tokens empty", "POST", "/admin/merge-tokens", {"tokens": []}, admin=True, show=80)
check("admin unpark", "POST", "/admin/unpark", admin=True, show=80)
check("admin revive", "POST", "/admin/revive", admin=True, show=80)

# ---- R584: full read surface (was covered ad-hoc, now in one run) ----
check("usage", "GET", "/v1/usage", want=(200,), show=90)
check("video meta", "GET", f"/v1/videos/{VID}/meta", want=(200,), show=90)
check("video thumb", "GET", f"/v1/videos/{VID}/thumb", want=(200,))
check("library stats", "GET", "/v1/library/stats", want=(200,), show=90)
check("library list", "GET", "/v1/library?limit=2", want=(200,), show=90)
check("library manifest", "GET", "/v1/library/manifest?limit=2", want=(200,), show=90)
check("webhook secret get", "GET", "/v1/webhook/secret", want=(200,), show=80)
check("gallery with key", "GET", "/gallery?key=" + KEY, want=(200,), show=60)
check("sdk file", "GET", "/sdk/wzmm.py", want=(200,), show=40)
check("openapi", "GET", "/openapi.json", want=(200,), show=40)
check("docs", "GET", "/docs", want=(200,), show=30)
check("admin public", "GET", "/admin/public", admin=True, show=120)
check("admin summary", "GET", "/admin/summary", admin=True, show=120)
check("admin capacity", "GET", "/admin/capacity", admin=True, show=120)
check("admin health", "GET", "/admin/health", admin=True, show=90)
check("admin alerts", "GET", "/admin/alerts?limit=2", admin=True, show=90)
check("admin webhook", "GET", "/admin/webhook", admin=True, show=80)
check("admin webhook queue", "GET", "/admin/webhook/queue", admin=True, show=80)
check("admin usage by key", "GET", "/admin/usage?days=1&by_key=1", admin=True, show=80)
check("admin checkin status", "GET", "/admin/checkin", admin=True, show=80)
# ---- R687/R695: keys gc + guard ----
# ---- R696: read-only browse token ----
_bcode, _braw = check("admin browse-token mint", "GET", "/admin/browse-token?ttl_h=1", admin=True, show=90)
_browse = json.loads(_braw).get("token", "") if _bcode == 200 else ""
check("gallery via browse token", "GET", "/gallery?limit=4&browse=" + _browse, key="", want=(200,), show=60)
check("gallery tampered browse -> 401", "GET", "/gallery?browse=bw_9999999999.deadbeef", key="", want=(401,))
check("watch page via browse", "GET", "/watch/" + VID + "?browse=" + _browse, key="", want=(200,), show=70)
check("watch no auth -> 401", "GET", "/watch/" + VID, key="", want=(401,))
_farm_n = (len([_f for _f in _os.listdir("/opt/h3v2/data/render/out") if _f.endswith(".mp4")])
           if _os.path.isdir("/opt/h3v2/data/render/out") else 0)
check("legacy archive (browse)", "GET", "/v1/library/archive?ids=t-00026670df7146a89acd&browse=" + _browse,
      key="", want=((200,) if _farm_n else (404,)), show=20)
check("archive no auth -> 401", "GET", "/v1/videos/archive?ids=" + VID, key="", want=(401,))
check("admin browse-audit", "GET", "/admin/browse-audit?limit=3", admin=True, show=110)
check("admin browse-rotate needs admin", "POST", "/admin/browse-rotate", key="", want=(401,))
check("admin keys gc (dry)", "POST", "/admin/keys/gc", admin=True, show=140)
check("admin keys gc guard -> 400", "POST", "/admin/keys/gc?dry_run=0&max_age_h=0.001", admin=True, want=(400,), show=120)

ok = sum(1 for r in rows if r[0])
print(f"\nSUMMARY {ok}/{len(rows)} passed")
for r in rows:
    if not r[0]:
        print("  FAILED:", r)
sys.exit(0 if ok == len(rows) else 1)