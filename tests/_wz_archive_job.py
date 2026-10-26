# -*- coding: utf-8 -*-
import io, json, os, sqlite3, sys, time, zipfile, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = (open("/opt/wzmm/admin.key").read().strip()
         if os.path.exists("/opt/wzmm/admin.key") else "")
KEY = os.environ.get("WZMM_KEY", "")
OTHER = os.environ.get("WZMM_KEY", "")
DB = "/opt/wzmm/wzmm.db"
LEG = "/opt/h3v2/data/render/out"
JOBD = "/opt/wzmm/archive_jobs"
rows = []


def http(path, method="GET", body=None, key=None, tok=None, admin=False, extra=None):
    hdrs = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if key:
        hdrs["Authorization"] = "Bearer " + key
    if admin:
        hdrs["X-Admin-Token"] = ADMIN
    if extra:
        hdrs.update(extra)
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def q(tok):
    return "?browse=" + tok


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(40), detail)


def wait(jid, tok="", key=None, tries=120):
    last = {}
    for _ in range(tries):
        c, raw, _ = http("/v1/archive/jobs/%s%s" % (jid, q(tok) if tok else ""), key=key)
        if c != 200:
            return c, {"state": "http_%d" % c}
        last = json.loads(raw)
        if last["state"] in ("ready", "failed"):
            return c, last
        time.sleep(0.25)
    return 200, last


c, raw, _ = http("/admin/browse-token?ttl_h=1&note=job-regress", admin=True)
TOK = json.loads(raw)["token"]

# ---- validation ----
c, _, _ = http("/v1/archive/jobs", "POST", {"kind": "library", "ids": ["x"]})
ok("create no auth -> 401", c == 401, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", None, extra={"Content-Type": "application/json"})
ok("create bad body -> 400", c == 400, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "nope", "ids": ["x"]})
ok("create bad kind -> 400", c == 400, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": []})
ok("library empty ids -> 400", c == 400, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "videos", "ids": []})
ok("videos empty ids -> 400", c == 400, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": ["no-such-clip"]})
ok("library missing -> 404", c == 404, str(c))
c, _, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": ["../../etc/passwd"]})
ok("path traversal -> 404", c == 404, str(c))

_FARM_N = len([f for f in os.listdir(LEG) if f.endswith(".mp4")])
if _FARM_N >= 2:
    # ---- library job end to end ----
    names = [f[:-4] for f in sorted(os.listdir(LEG)) if f.endswith(".mp4")][:2]
    sizes = {n: os.path.getsize(os.path.join(LEG, n + ".mp4")) for n in names}
    c, raw, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": names})
    ok("library job created", c == 200, str(c))
    j = json.loads(raw)
    jid = j["job_id"]
    ok("job id shape", jid.startswith("aj_") and len(jid) == 19, jid)
    ok("job counts files", j["count"] == 2, str(j["count"]))
    ok("job total bytes", j["total_bytes"] == sum(sizes.values()), str(j["total_bytes"]))
    ok("job filename is zip", j["filename"].endswith(".zip"), j["filename"])

    c, s = wait(jid, TOK)
    ok("job reaches ready", s.get("state") == "ready", str(s.get("state")))
    ok("job pct 100", s.get("pct") == 100, str(s.get("pct")))
    ok("job expires_in ~7200", 7000 <= s.get("expires_in", 0) <= 7200, str(s.get("expires_in")))
    ok("job built == bytes", s.get("built") == s.get("bytes") and s.get("bytes", 0) > sum(sizes.values()),
       "%s/%s" % (s.get("built"), s.get("bytes")))

    c, raw, hd = http("/v1/archive/jobs/%s/file%s" % (jid, q(TOK)))
    ok("download 200 zip", c == 200 and raw[:2] == b"PK", str(c))
    ok("download content-length matches", int(hd.get("content-length", -1)) == len(raw),
       "%s vs %s" % (hd.get("content-length"), len(raw)))
    ok("download filename header", j["filename"] in hd.get("content-disposition", ""),
       hd.get("content-disposition", ""))
    z = zipfile.ZipFile(io.BytesIO(raw))
    ok("zip entries match", sorted(z.namelist()) == sorted(n + ".mp4" for n in names), str(z.namelist()))
    ok("zip entry sizes match", all(z.getinfo(n + ".mp4").file_size == sizes[n] for n in names))

    c, part, hd = http("/v1/archive/jobs/%s/file%s" % (jid, q(TOK)), extra={"Range": "bytes=0-99"})
    ok("range -> 206", c == 206 and len(part) == 100, "%s len=%d" % (c, len(part)))
    ok("range payload matches", part == raw[:100])
    ok("range content-range", hd.get("content-range", "").startswith("bytes 0-99/"), hd.get("content-range", ""))

    # ---- authz: a key-owned job is private to that key ----
    c, raw, _ = http("/v1/archive/jobs", "POST", {"kind": "library", "ids": [names[1]]}, key=KEY)
    ok("key-owned job created", c == 200, str(c))
    kjid = json.loads(raw)["job_id"]
    c, s = wait(kjid, key=KEY)
    ok("key-owned job ready", s.get("state") == "ready", str(s.get("state")))
    c, rawk, _ = http("/v1/archive/jobs/%s/file" % kjid, key=KEY)
    ok("owner key can download", c == 200 and rawk[:2] == b"PK", str(c))
    c, _, _ = http("/v1/archive/jobs/%s" % kjid, key=OTHER)
    ok("other key status -> 404", c == 404, str(c))
    c, _, _ = http("/v1/archive/jobs/%s/file" % kjid, key=OTHER)
    ok("other key file -> 404", c == 404, str(c))
    c, _, _ = http("/v1/archive/jobs/%s" % kjid)
    ok("key job no auth -> 401", c == 401, str(c))
    http("/v1/archive/jobs/%s" % kjid, "DELETE", key=KEY)
    c, _, _ = http("/v1/archive/jobs/%s" % jid)
    ok("status no auth -> 401", c == 401, str(c))
    c, _, _ = http("/v1/archive/jobs/%s/file" % jid)
    ok("file no auth -> 401", c == 401, str(c))

    # ---- viewer-created job is readable by a key (global owner) ----
    c, raw, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": [names[0]]})
    vjid = json.loads(raw)["job_id"]
    c, s = wait(vjid, TOK)
    c2, raw2, _ = http("/v1/archive/jobs/%s/file" % vjid, key=KEY)
    ok("viewer job readable by key", c2 == 200 and raw2[:2] == b"PK", str(c2))
    http("/v1/archive/jobs/%s%s" % (vjid, q(TOK)), "DELETE")

    # ---- delete ----
    c, _, _ = http("/v1/archive/jobs/%s%s" % (jid, q(TOK)), "DELETE")
    ok("delete -> ok", c == 200, str(c))
    c, _, _ = http("/v1/archive/jobs/%s%s" % (jid, q(TOK)))
    ok("status after delete -> 404", c == 404, str(c))
    c, _, _ = http("/v1/archive/jobs/%s/file%s" % (jid, q(TOK)))
    ok("file after delete -> 404", c == 404, str(c))
else:
    print("SKIP library job end-to-end (legacy farm empty)")
# ---- videos job (viewer scope) ----
db = sqlite3.connect(DB)
vids = [r[0] for r in db.execute("select id from tasks where status='succeeded' and local_path is not null "
                                 "order by rowid desc limit 2")]
vsizes = {r[0]: os.path.getsize(r[1]) for r in db.execute(
    "select id, local_path from tasks where id in (%s)" % ",".join("?" * len(vids)), vids)}
db.close()
c, raw, _ = http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "videos", "ids": vids})
ok("videos job created", c == 200, str(c))
vj = json.loads(raw)
c, s = wait(vj["job_id"], TOK)
ok("videos job ready", s.get("state") == "ready", str(s.get("state")))
c, raw, hd = http("/v1/archive/jobs/%s/file%s" % (vj["job_id"], q(TOK)))
ok("videos download zip", c == 200 and raw[:2] == b"PK", str(c))
z = zipfile.ZipFile(io.BytesIO(raw))
ok("videos entries match", sorted(z.namelist()) == sorted(i + ".mp4" for i in vids), str(z.namelist()))
ok("videos sizes match", all(z.getinfo(i + ".mp4").file_size == vsizes[i] for i in vids))
http("/v1/archive/jobs/%s%s" % (vj["job_id"], q(TOK)), "DELETE")

if _FARM_N >= 2:
    # ---- two concurrent jobs ----
    j1 = json.loads(http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": [names[0]]})[1])["job_id"]
    j2 = json.loads(http("/v1/archive/jobs" + q(TOK), "POST", {"kind": "library", "ids": [names[1]]})[1])["job_id"]
    _, s1 = wait(j1, TOK)
    _, s2 = wait(j2, TOK)
    ok("two jobs both ready", s1.get("state") == "ready" and s2.get("state") == "ready",
       "%s/%s" % (s1.get("state"), s2.get("state")))
    http("/v1/archive/jobs/%s%s" % (j1, q(TOK)), "DELETE")
    http("/v1/archive/jobs/%s%s" % (j2, q(TOK)), "DELETE")
else:
    print("SKIP two concurrent library jobs (legacy farm empty)")
# ---- no leftovers ----
left = [f for f in os.listdir(JOBD)] if os.path.isdir(JOBD) else []
ok("no .part leftovers", not [f for f in left if f.endswith(".part")], str(left[:5]))
ok("no orphan zips after deletes", not [f for f in left if f.endswith(".zip")], str(left[:5]))

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)