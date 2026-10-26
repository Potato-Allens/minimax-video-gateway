# -*- coding: utf-8 -*-
import io, json, os, sys, time, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = open("/opt/wzmm/admin.key").read().strip()
DEAD = "/opt/wzmm/dead.jsonl"
APP = "/opt/wzmm/app.py"
rows = []


def http(path, method="GET", body=None, admin=False):
    hdrs = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if admin:
        hdrs["X-Admin-Token"] = ADMIN
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(40), detail)


def pool_row(email):
    c, raw = http("/admin/pool", admin=True)
    d = json.loads(raw)
    for a in d["data"]:
        if a["email"] == email:
            return a
    return None


src = io.open(APP, encoding="utf-8").read()
ok("submit loop calls _park_on_402", src.count("_park_on_402(acc, txt)") == 1)
ok("cooldown env knob", "WZMM_402_COOLDOWN_H" in src)

c, _ = http("/admin/simulate-402", "POST", {"email": "x@y.z"})
ok("simulate no auth -> 401", c == 401, str(c))
c, _ = http("/admin/simulate-402", "POST", {"email": "nobody@nowhere.invalid"}, admin=True)
ok("simulate unknown email -> 404", c == 404, str(c))

c, raw = http("/admin/pool", admin=True)
pool = json.loads(raw)["data"]
target = next((a for a in pool if a.get("parked_for", 0) == 0), None)
ok("pool has a free account", target is not None)
email = target["email"]

try:
    c, raw = http("/admin/simulate-402", "POST", {"email": email}, admin=True)
    j = json.loads(raw)
    ok("simulate -> ok", c == 200 and j.get("ok"), str(c))
    ok("default park is 2h", j.get("parked_s") == 7200, str(j.get("parked_s")))
    ok("cool_for ~7200", 7000 <= j.get("cool_for_s", 0) <= 7200, str(j.get("cool_for_s")))

    row = pool_row(email)
    ok("pool shows parked_for", row and row.get("parked_for", 0) > 7000, str(row and row.get("parked_for")))
    ok("account not selectable", row and row.get("parked_for", 0) > 0)

    cool_lines = 0
    if os.path.isfile(DEAD):
        for line in io.open(DEAD, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("t") == "cool" and rec.get("email") == email and rec.get("why", "").startswith("402"):
                cool_lines += 1
    ok("402 recorded in dead.jsonl", cool_lines >= 1, str(cool_lines))

    c, raw = http("/admin/simulate-402", "POST", {"email": email, "restore": 1}, admin=True)
    j2 = json.loads(raw)
    ok("restore -> ok", c == 200 and j2.get("restored"), str(c))
    ok("restore clears cooldown", j2.get("cool_for_s") == 0, str(j2.get("cool_for_s")))
    row = pool_row(email)
    ok("pool parked_for back to 0", row and row.get("parked_for", 0) == 0, str(row and row.get("parked_for")))
    left = 0
    if os.path.isfile(DEAD):
        for line in io.open(DEAD, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("t") == "cool" and rec.get("email") == email:
                left += 1
    ok("dead.jsonl has no residue", left == 0, str(left))
finally:
    http("/admin/simulate-402", "POST", {"email": email, "restore": 1}, admin=True)

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)