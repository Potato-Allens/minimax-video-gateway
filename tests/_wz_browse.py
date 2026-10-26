# -*- coding: utf-8 -*-
"""R696 regression: read-only browse token unlocks every viewer surface and nothing else."""
import hashlib, hmac, json, os, re, sqlite3, sys, time, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = open("/opt/wzmm/admin.key").read().strip()
KEY = open("/opt/wzmm/apikey.txt").read().split()[0]
DB = os.environ.get("WZMM_DB", "/opt/wzmm/wzmm.db")
VID = os.environ.get("WZMM_VID", "vid_b1edeb7ea5d5c378028219c3")
rows = []

def http(method, path, body=None, admin=None, key=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if admin:
        r.add_header("X-Admin-Token", admin)
    elif key:
        r.add_header("Authorization", "Bearer " + key)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(34), detail)

def total_of(html):
    m = re.search(r"(\d+)\s*[–-]\s*\d+\s*/\s*(\d+)", html.decode("utf-8", "replace"))
    return int(m.group(2)) if m else None

# mint
code, raw = http("GET", "/admin/browse-token?ttl_h=1", admin=ADMIN)
ok("mint needs admin -> 200", code == 200, str(code))
tok = json.loads(raw)["token"] if code == 200 else ""
ok("token shape bw_<exp>.<mac>", bool(re.fullmatch(r"bw_\d+\.[0-9a-f]{32}", tok)), tok[:20])

c = sqlite3.connect(DB)
db_n = c.execute("select count(*) from tasks where status='succeeded'").fetchone()[0]
c.close()

code, raw = http("GET", "/gallery?browse=%s&limit=6" % tok)
ok("gallery?browse -> sees all", code == 200 and total_of(raw) == db_n, "%s total=%s db=%s" % (code, total_of(raw), db_n))

code, raw = http("GET", "/gallery?key=%s&limit=6" % KEY)
ok("gallery?key stays scoped", code == 200 and total_of(raw) < db_n, "total=%s" % total_of(raw))

code, raw = http("GET", "/gallery?limit=6", admin=ADMIN)
ok("admin gallery unchanged", code == 200 and total_of(raw) == db_n, "total=%s" % total_of(raw))

_farm_n = (len([_f for _f in os.listdir("/opt/h3v2/data/render/out") if _f.endswith(".mp4")])
           if os.path.isdir("/opt/h3v2/data/render/out") else 0)
code, raw = http("GET", "/library?browse=%s&limit=6" % tok)
ok("library?browse matches farm size", code == 200 and (total_of(raw) or 0) == _farm_n,
   "total=%s farm=%s" % (total_of(raw), _farm_n))

code, raw = http("GET", "/v1/videos/%s/status?browse=%s" % (VID, tok))
ok("status?browse -> 200", code == 200 and json.loads(raw).get("id") == VID)

code, raw = http("GET", "/v1/videos/%s/content?browse=%s" % (VID, tok))
ok("content?browse -> mp4", code == 200 and len(raw) > 100000, "%d B" % len(raw))

code, raw = http("GET", "/v1/videos/%s/thumb?browse=%s" % (VID, tok))
ok("thumb?browse -> jpeg", code == 200 and raw[:2] == b"\xff\xd8", "%d B" % len(raw))

code, _ = http("GET", "/gallery?browse=bw_9999999999.deadbeef")
ok("tampered token -> 401", code == 401, str(code))

exp = int(time.time()) - 60
mac = hmac.new(ADMIN.encode(), ("browse:%d" % exp).encode(), hashlib.sha256).hexdigest()[:32]
code, raw = http("GET", "/gallery?browse=bw_%d.%s" % (exp, mac))
ok("expired but signed -> 401", code == 401, str(code))

code, raw = http("POST", "/v1/videos/generations?browse=%s" % tok, body={"prompt": "x", "dry_run": True})
ok("browse cannot create -> 401", code == 401, str(code))

code, _ = http("GET", "/admin/pool?browse=%s" % tok)
ok("browse is not admin -> 401", code == 401, str(code))

code, _ = http("GET", "/admin/browse-token")
ok("mint without admin -> 401", code == 401, str(code))

code, _ = http("GET", "/gallery")
ok("gallery no auth -> 401", code == 401, str(code))

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)