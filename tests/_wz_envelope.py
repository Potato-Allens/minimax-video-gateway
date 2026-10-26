# -*- coding: utf-8 -*-
"""R695 regression: every error path must return {detail, error{message,type,code,request_id}}
and echo X-Request-Id. Runs against a live gateway (default 127.0.0.1:8808)."""
import json, os, sys, time, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
KEY = os.environ.get("WZMM_KEY") or open("/opt/wzmm/apikey.txt").read().split()[0]
ADMIN = open("/opt/wzmm/admin.key").read().strip()

def call(method, path, body=None, key=KEY, admin=False, rid=None, raw_body=None):
    data = raw_body if raw_body is not None else (None if body is None else json.dumps(body).encode())
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if admin:
        r.add_header("X-Admin-Token", key)
    elif key:
        r.add_header("Authorization", "Bearer " + key)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if rid:
        r.add_header("X-Request-Id", rid)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, str(e).encode(), {}

rows = []
def check(name, want, **kw):
    code, raw, hdrs = call(**kw)
    rid_h = hdrs.get("X-Request-Id") or hdrs.get("x-request-id") or ""
    env_ok = False
    typ = msg = ""
    try:
        j = json.loads(raw)
        err = j.get("error") or {}
        typ, msg = err.get("type", ""), err.get("message", "")
        env_ok = ("detail" in j and isinstance(err, dict) and err
                  and err.get("code") == code and err.get("request_id")
                  and err.get("type") and rid_h == err.get("request_id"))
    except Exception:
        j = {}
    ok = code == want and env_ok
    rows.append((ok, name, code, want))
    print(("PASS" if ok else "FAIL"), str(code).rjust(4), "/", want, name.ljust(26),
          ("type=%-22s" % typ) if typ else "NO-ENVELOPE",
          "| " + str(msg)[:88])
    return code, raw

# --- router-level (Starlette) ---
check("404 unknown route",   404, method="GET", path="/nope/x")
check("404 deep unknown",    404, method="GET", path="/v1/nope/deep/path")
check("405 wrong method",    405, method="DELETE", path="/healthz")
check("405 post on gallery", 405, method="POST", path="/gallery", key="")
check("422 bad query param", 422, method="GET", path="/gallery?limit=abc", key="")
check("422 admin usage days", 422, method="GET", path="/admin/usage?days=abc", admin=True)
check("404 bad path shape",  404, method="GET", path="/v1/videos/batch/1/2/3")
# --- app-level (FastAPI HTTPException) ---
check("401 no key",          401, method="POST", path="/v1/videos/generations", body={}, key="")
check("401 bad key",         401, method="POST", path="/v1/videos/generations", body={}, key="sk-nope")
check("401 gallery no key",  401, method="GET", path="/gallery", key="")
check("400 invalid json",    400, method="POST", path="/v1/videos/generations", raw_body=b"{oops")
check("400 bad model",       400, method="POST", path="/v1/videos/generations",
      body={"model": "NOPE", "prompt": "x", "dry_run": True})
check("400 empty prompt",    400, method="POST", path="/v1/videos/generations",
      body={"model": "MiniMax-Hailuo-2.3", "prompt": "  ", "dry_run": True})
check("404 task missing",    404, method="GET", path="/v1/videos/vid_nope/status")
check("404 batch missing",   404, method="GET", path="/v1/videos/batch/bat_nope")
check("401 admin missing",   401, method="GET", path="/admin/pool", admin=False, key="")
check("403 admin wrong",     403, method="GET", path="/admin/pool", admin=True, key="bad-token")
check("403 admin patch bad", 403, method="PATCH", path="/admin/keys/key_nope", body={"rpm": 1}, admin=True)
check("404 admin patch nokey", 404, method="PATCH", path="/admin/keys/key_nope", body={"rpm": 1}, admin=True, key=ADMIN)
# --- request id echo ---
code, raw, hdrs = call("GET", "/nope/echo", rid="req_clientmade123")
try:
    echoed = json.loads(raw)["error"]["request_id"]
except Exception:
    echoed = None
ok = code == 404 and echoed == "req_clientmade123"
rows.append((ok, "client rid echoed", code, 404))
print(("PASS" if ok else "FAIL"), "    ", "client-supplied X-Request-Id ->", echoed)

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
for r in bad:
    print("  FAILED:", r)
sys.exit(1 if bad else 0)