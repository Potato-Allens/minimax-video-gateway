# -*- coding: utf-8 -*-
import io, json, os, sys, urllib.request, urllib.error
BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
KEY = os.environ.get("WZMM_KEY", "")
APP = "/opt/wzmm/app.py"
rows = []


def http(path, method="GET", body=None, key=None):
    hdrs = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if key:
        hdrs["Authorization"] = "Bearer " + key
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(42), detail)


src = io.open(APP, encoding="utf-8").read()
ok("gate defined once", src.count("def _gate_h3_credits(") == 1)
ok("gate used in both paths", src.count("    _gate_h3_credits(") == 2)
ok("env override present", "WZMM_H3_REQUIRE_CREDITS" in src)

for model, res, dur in (("MiniMax-H3", "768P", 4), ("MiniMax-H3", "2K", 6),
                        ("MiniMax-H3-Max", "2K", 5)):
    c, raw = http("/v1/videos/generations", "POST",
                  {"model": model, "resolution": res, "duration": dur,
                   "prompt": "gate probe", "dry_run": 1}, key=KEY)
    j = json.loads(raw)
    ok("dry_run %s %s %ss -> 400" % (model, res, dur), c == 400, str(c))
    ok("  message names credits", "credits" in (j.get("detail") or ""), (j.get("detail") or "")[:70])
    ok("  envelope is invalid_request_error",
       (j.get("error") or {}).get("type") == "invalid_request_error", str((j.get("error") or {}).get("type")))

c, raw = http("/v1/videos/generations", "POST",
              {"model": "MiniMax-Hailuo-2.3", "resolution": "1080P", "duration": 6,
               "prompt": "free path", "dry_run": 1}, key=KEY)
j = json.loads(raw)
ok("hailuo 1080P dry_run still 200", c == 200 and j.get("dry_run"), str(c))
ok("hailuo cost reported", j.get("credit_cost") == 80, str(j.get("credit_cost")))

c, raw = http("/v1/videos/batch", "POST",
              {"items": [{"prompt": "h3 in batch", "model": "MiniMax-H3",
                          "resolution": "768P", "duration": 4}], "dry_run": 1}, key=KEY)
j = json.loads(raw)
ok("batch dry_run rejects H3", c == 200 and j.get("rejected") == 1, "rej=%s" % j.get("rejected"))
ok("batch error mentions credits", "credits" in json.dumps(j.get("errors") or []), json.dumps(j.get("errors"))[:90])

c, raw = http("/v1/videos/bulk", "POST",
              {"items": [{"prompt": "h3 in bulk", "model": "MiniMax-H3-Max",
                          "resolution": "2K", "duration": 5}], "dry_run": 1}, key=KEY)
j = json.loads(raw)
ok("bulk dry_run rejects H3", c == 200 and j.get("rejected") == 1, "rej=%s" % j.get("rejected"))

c, raw = http("/v1/videos/generations", "POST",
              {"model": "MiniMax-H3", "resolution": "768P", "duration": 4, "prompt": "no key"}, key=None)
ok("no key still 401 first", c == 401, str(c))

# nothing was created by the gate probes
c, raw = http("/v1/videos/generations?limit=5&model=MiniMax-H3", key=KEY)
j = json.loads(raw)
recent = j.get("data") or []
ok("no H3 task created by probes", all(t.get("model") != "MiniMax-H3" or t.get("status") != "queued" for t in recent),
   str(len(recent)))

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)