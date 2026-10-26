# -*- coding: utf-8 -*-
"""R697 regression: kid rotation revokes old browse links instantly, audit is honest.

The test snapshots browse_state.json, rotates twice, then restores the snapshot so a
long-lived token handed to a human keeps working after the drill.
"""
import hashlib, hmac, json, os, shutil, sys, time, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = open("/opt/wzmm/admin.key").read().strip()
STATE = os.environ.get("WZMM_BROWSE_STATE", "/opt/wzmm/browse_state.json")
rows = []

def http(method, path, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(38), detail)

def mint(ttl=1, note=""):
    c, raw = http("GET", "/admin/browse-token?ttl_h=%s&note=%s" % (ttl, note),
                  {"X-Admin-Token": ADMIN})
    return c, (json.loads(raw) if c == 200 else None)

def works(tok):
    c, _ = http("GET", "/gallery?browse=%s&limit=2" % tok)
    return c

snapshot = None
if os.path.exists(STATE):
    snapshot = STATE + ".drillbak"
    shutil.copy2(STATE, snapshot)
try:
    c, a = mint(1, "rotate-drill-A")
    ok("mint A at current kid", c == 200 and a and a["kid"] == 1, "kid=%s" % (a or {}).get("kid"))
    ta = a["token"]
    ok("A works before rotation", works(ta) == 200)

    c, raw = http("POST", "/admin/browse-rotate?note=drill", {"X-Admin-Token": ADMIN})
    rot = json.loads(raw) if c == 200 else {}
    ok("rotate -> 200", c == 200 and rot.get("kid") == 2, str(rot)[:90])
    ok("old token A now 401", works(ta) == 401)

    c, b = mint(1, "rotate-drill-B")
    ok("mint B carries new kid", c == 200 and b and b["kid"] == 2, "kid=%s" % (b or {}).get("kid"))
    ok("B works after rotation", works(b["token"]) == 200)

    c, raw = http("GET", "/admin/browse-audit?limit=5", {"X-Admin-Token": ADMIN})
    aud = json.loads(raw) if c == 200 else {}
    ok("audit lists min_kid=2", aud.get("min_kid") == 2, "min_kid=%s" % aud.get("min_kid"))
    ok("audit marks kid 1 revoked", aud.get("revoked_kids") == [1], str(aud.get("revoked_kids")))
    ok("audit records both mints", aud.get("issued_total", 0) >= 2, "total=%s" % aud.get("issued_total"))

    exp = int(time.time()) + 3600
    mac9 = hmac.new(ADMIN.encode(), ("browse:v9:%d" % exp).encode(), hashlib.sha256).hexdigest()[:32]
    ok("forged future kid -> 401", works("bw_9_%d.%s" % (exp, mac9)) == 401)
    mac0 = hmac.new(ADMIN.encode(), ("browse:%d" % exp).encode(), hashlib.sha256).hexdigest()[:32]
    ok("tiny kid 0 -> 401", works("bw_0_%d.%s" % (exp, mac0)) == 401)

    c, _ = http("POST", "/admin/browse-rotate", {})   # no admin header
    ok("rotate needs admin -> 401", c == 401, str(c))
finally:
    if snapshot:
        shutil.copy2(snapshot, STATE)
        os.remove(snapshot)
    else:
        if os.path.exists(STATE):
            os.remove(STATE)

c, _ = http("GET", "/gallery?browse=bw_9999999999.deadbeef&limit=2")
ok("30d human token restored", c == 200, str(c))
c, raw = http("GET", "/admin/browse-audit?limit=3", {"X-Admin-Token": ADMIN})
st = json.loads(raw) if c == 200 else {}
ok("state rolled back to kid=1", st.get("kid") == 1 and st.get("min_kid") == 1, str(st)[:80])

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)