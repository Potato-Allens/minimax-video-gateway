# -*- coding: utf-8 -*-
import io, os, io as _io, json, sqlite3, sys, zipfile, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = (open("/opt/wzmm/admin.key").read().strip()
         if os.path.exists("/opt/wzmm/admin.key") else "")
KEY = os.environ.get("WZMM_KEY", "")
DB = "/opt/wzmm/wzmm.db"
LEG = "/opt/h3v2/data/render/out"
rows = []

def http(path, key=None, admin=False):
    r = urllib.request.Request(BASE + path)
    if admin:
        r.add_header("X-Admin-Token", ADMIN)
    if key:
        r.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def http_full(path, key=None, admin=False):
    r = urllib.request.Request(BASE + path)
    if admin:
        r.add_header("X-Admin-Token", ADMIN)
    if key:
        r.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}

def zip_get(path, tries=10, pause=1.0):
    """Archives are built one at a time server-side; a 429 just means "wait"."""
    last = (0, b"", {})
    for _ in range(tries):
        last = http_full(path)
        if last[0] != 429:
            return last
        time.sleep(pause)
    return last

def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(34), detail)

c, raw = http("/admin/browse-token?ttl_h=1&note=pick-regress", admin=True)
TOK = json.loads(raw)["token"]

# ---- gallery UI ----
c, raw = http("/gallery?browse=%s&limit=6" % TOK)
g = raw.decode("utf-8", "replace")
ok("gallery has pick bar", 'id="bar"' in g and 'id="pkdl"' in g)
ok("gallery cards have checkboxes", g.count('class="pick"') >= 6, str(g.count('class="pick"')))
ok("gallery zip target", '/v1/videos/archive' in g)
ok("gallery auth query embedded", 'AQ="browse=bw_' in g)
ok("gallery no template leftovers", "__" + "AQ__" not in g and "__ARCH__" not in g)
# ---- R701: client-side caps (100 items / 1.5GB) ----
ok("cards expose data-bytes", g.count("data-bytes=") >= 6, str(g.count("data-bytes=")))
ok("client cap constants", "CAP_N=100" in g and "CAP_B=1500000000" in g)
ok("size readout element", 'id="plim"' in g)
ok("blocked link state", "dataset.blocked" in g and "preventDefault" in g)
ok("select-all respects cap", "sel.size>=CAP_N" in g)
# ---- R703: server-side archive jobs (no blob in RAM) ----
ok("gallery posts archive job", g.count("/v1/archive/jobs") >= 3, str(g.count("/v1/archive/jobs")))
ok("gallery job kind", "var JKIND='videos'" in g)
ok("gallery polls job status", "var jid=j.job_id" in g and "s.pct" in g)
ok("gallery native download", "location.href='/v1/archive/jobs/'+jid+'/file'" in g)
ok("gallery busy cursor css", "#bar a.zip[data-busy]" in g)
ok("gallery blocks double click", "dataset.blocked||dl.dataset.busy" in g.replace(" ", ""))
ok("gallery no blob buffering", "getReader" not in g and "new Blob" not in g)

# ---- library UI ----
_FARM_N = len([f for f in os.listdir(LEG) if f.endswith(".mp4")])
c, raw = http("/library?browse=%s&limit=6" % TOK)
l = raw.decode("utf-8", "replace")
ok("library has pick bar", 'id="bar"' in l and 'id="pkdl"' in l)
_expect = min(_FARM_N, 6)
ok("library cards have checkboxes", l.count('class="pick"') == _expect,
   "%s vs farm %s" % (l.count('class="pick"'), _FARM_N))
ok("library zip target", '/v1/library/archive' in l)
ok("library posts archive job", l.count("/v1/archive/jobs") >= 3, str(l.count("/v1/archive/jobs")))
ok("library job kind", "var JKIND='library'" in l)
ok("library native download", "location.href='/v1/archive/jobs/'+jid+'/file'" in l)
ok("library busy cursor css", "#bar a.zip[data-busy]" in l)
ok("library blocks double click", "dataset.blocked||dl.dataset.busy" in l.replace(" ", ""))
ok("library no blob buffering", "getReader" not in l and "new Blob" not in l)

# ---- new-production zip ----
db = sqlite3.connect(DB)
ids = [r[0] for r in db.execute("select id from tasks where status='succeeded' and local_path is not null "
                                "order by rowid desc limit 3")]
sizes = {r[0]: r[1] for r in db.execute("select id, local_path from tasks where id in (%s)"
                                        % ",".join("?" * len(ids)), ids)}
db.close()
c, raw = http("/v1/videos/archive?ids=%s&limit=100&browse=%s" % (",".join(ids), TOK))
ok("archive 3 ids -> 200 zip", c == 200 and raw[:2] == b"PK", str(c))
if c == 200:
    z = zipfile.ZipFile(_io.BytesIO(raw))
    names = z.namelist()
    ok("zip has 3 entries", len(names) == 3, str(names[:3]))
    ok("zip bytes match disk", all(z.getinfo(i + ".mp4").file_size == os.path.getsize(sizes[i]) for i in ids),
       str([z.getinfo(i + ".mp4").file_size for i in ids]))

c, _ = http("/v1/videos/archive?ids=vid_0ae28585f08174f0fd4d4507&key=" + KEY)
ok("archive scope: other key -> 404", c == 404, str(c))
c, _ = http("/v1/videos/archive?ids=%s" % ",".join(ids))
ok("archive no auth -> 401", c == 401, str(c))

db = sqlite3.connect(DB)
more = [r[0] for r in db.execute("select id from tasks where status='succeeded' and local_path is not null "
                                 "order by rowid desc limit 8")]
db.close()
c, raw, _hd = zip_get("/v1/videos/archive?ids=%s&limit=3&browse=%s" % (",".join(more), TOK))
if c == 200:
    z = zipfile.ZipFile(_io.BytesIO(raw))
    ok("archive respects limit=3", len(z.namelist()) == 3, str(len(z.namelist())))
else:
    ok("archive respects limit=3", False, "http %s" % c)

if _FARM_N >= 2:
    # ---- legacy zip ----
    names = [f[:-4] for f in sorted(os.listdir(LEG)) if f.endswith(".mp4")][:2]
    c, raw, _hd = zip_get("/v1/library/archive?ids=%s&browse=%s" % (",".join(names), TOK))
    ok("legacy archive 2 -> zip", c == 200 and raw[:2] == b"PK", str(c))
    if c == 200:
        z = zipfile.ZipFile(_io.BytesIO(raw))
        ok("legacy zip entries", sorted(z.namelist()) == sorted(n + ".mp4" for n in names), str(z.namelist()))
        ok("legacy zip sizes", all(z.getinfo(n + ".mp4").file_size == os.path.getsize(os.path.join(LEG, n + ".mp4"))
                                   for n in names))
else:
    print("SKIP legacy zip (legacy farm empty)")
if _FARM_N >= 2:
    # R700: the archive semaphore must be released after each successful build
    import time as _t
    c2, raw2 = 0, b""
    for _try in range(8):
        c2, raw2 = http("/v1/library/archive?ids=%s&browse=%s" % (names[0], TOK))
        if c2 != 429:
            break
        _t.sleep(1.0)
    ok("second archive not rate-limited", c2 == 200 and raw2[:2] == b"PK", str(c2))
else:
    print("SKIP legacy archive semaphore (legacy farm empty)")
if _FARM_N >= 2:
    c, _ = http("/v1/library/archive?ids=nope-missing&browse=%s" % TOK)
    ok("legacy archive missing -> 404", c == 404, str(c))
    c, _ = http("/v1/library/archive?ids=%s" % ",".join(names))
    ok("legacy archive no auth -> 401", c == 401, str(c))
else:
    print("SKIP legacy archive auth/missing (legacy farm empty)")
# ---- R702: archive responses carry Content-Length + filename ----
c, raw, hd = zip_get("/v1/videos/archive?ids=%s&browse=%s" % (",".join(ids), TOK))
ok("videos archive has content-length", c == 200 and int(hd.get("content-length", -1)) == len(raw),
   "%s vs %s" % (hd.get("content-length"), len(raw)))
ok("videos archive names file", "filename=" in hd.get("content-disposition", "") and
   ".zip" in hd.get("content-disposition", ""), hd.get("content-disposition", ""))
if _FARM_N >= 2:
    c, raw, hd = zip_get("/v1/library/archive?ids=%s&browse=%s" % (names[0], TOK))
    ok("legacy archive has content-length", c == 200 and int(hd.get("content-length", -1)) == len(raw),
       "%s vs %s" % (hd.get("content-length"), len(raw)))
    ok("legacy archive names file", "filename=" in hd.get("content-disposition", "") and
       ".zip" in hd.get("content-disposition", ""), hd.get("content-disposition", ""))
else:
    print("SKIP legacy archive headers (legacy farm empty)")
bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)
