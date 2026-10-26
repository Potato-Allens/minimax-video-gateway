# -*- coding: utf-8 -*-
import io, json, os, sys, urllib.request, urllib.error
BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = open("/opt/wzmm/admin.key").read().strip()
rows = []


def http(path, admin=False):
    hdrs = {"X-Admin-Token": ADMIN} if admin else {}
    req = urllib.request.Request(BASE + path, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(38), detail)


c, _ = http("/admin/capacity/plan")
ok("plan no auth -> 401", c == 401, str(c))
c, raw = http("/admin/capacity/plan", admin=True)
d = json.loads(raw)
ok("plan 200", c == 200, str(c))
need = {"object", "videos_gb", "files", "growth_gb_day", "free_gb", "plans", "max_ttl_days_before_full"}
ok("plan fields", need <= set(d), str(sorted(need - set(d))))
ok("plan files > 2000", d.get("files", 0) > 2000, str(d.get("files")))
ok("plan growth > 0", d.get("growth_gb_day", 0) > 0, str(d.get("growth_gb_day")))
ok("plan has 6 ttl options", len(d.get("plans", [])) == 6, str(len(d.get("plans", []))))
ttls = [p["ttl_days"] for p in d.get("plans", [])]
ok("plan ttl ladder", ttls == [0.5, 1.0, 2.0, 3.0, 5.0, 7.0], str(ttls))
consistent = all((p["free_after_gb"] > 2.0) == p["fits"] for p in d["plans"])
ok("fits matches free_after", consistent)
mono = all(d["plans"][i]["steady_gb"] <= d["plans"][i + 1]["steady_gb"] for i in range(len(d["plans"]) - 1))
ok("steady grows with ttl", mono)
ok("max ttl is positive", (d.get("max_ttl_days_before_full") or 0) > 0, str(d.get("max_ttl_days_before_full")))

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)