# -*- coding: utf-8 -*-
"""R698 regression: /watch player pages, prev/next, and browse-mode card links."""
import json, os, re, sqlite3, sys, time, urllib.request, urllib.error

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808")
ADMIN = (open("/opt/wzmm/admin.key").read().strip()
         if os.path.exists("/opt/wzmm/admin.key") else "")
KEY = os.environ.get("WZMM_KEY", "")
DB = os.environ.get("WZMM_DB", "/opt/wzmm/wzmm.db")
rows = []

def http(path, admin=False, key=None, method="GET"):
    r = urllib.request.Request(BASE + path, method=method)
    if admin:
        r.add_header("X-Admin-Token", ADMIN)
    if key:
        r.add_header("Authorization", "Bearer " + key)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            return resp.status, resp.read(), round(time.time() - t0, 3)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), round(time.time() - t0, 3)

def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(36), detail)

c, raw, _ = http("/admin/browse-token?ttl_h=1&note=watch-regress", admin=True)
TOK = json.loads(raw)["token"]

db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
mine = db.execute("select id,key_id from tasks where key_id=(select id from keys where key=?) "
                  "and status='succeeded' order by created desc limit 1", (KEY,)).fetchone()
other = db.execute("select id from tasks where key_id!=(select id from keys where key=?) "
                   "and status='succeeded' limit 1", (KEY,)).fetchone()
db.close()

code, g, dt = http("/gallery?browse=%s&limit=6" % TOK)
ok("gallery?browse 200", code == 200, "%ss" % dt)
ok("cards link to /watch", g.decode("utf-8", "replace").count('href="/watch/') >= 6)
ok("thumbs carry browse token", "thumb?browse=bw_" in g.decode("utf-8", "replace"))
ok("no empty thumb auth", 'thumb?"' not in g.decode("utf-8", "replace"))

code, w, dt = http("/watch/%s?browse=%s" % (mine["id"], TOK))
hw = w.decode("utf-8", "replace")
ok("watch (browse) 200", code == 200, "%ss" % dt)
ok("video src carries token", ('/v1/videos/%s/content?browse=bw_' % mine["id"]) in hw)
ok("has download button", "\u4e0b\u8f7d MP4" in hw)
ok("has keyboard nav script", "ArrowRight" in hw and "ArrowLeft" in hw)
ok("watch renders <video controls", "<video src=" in hw and "controls" in hw)
# ---- R699: ?? (autoplay-next) ----
ok("has autoplay-next toggle", 'id="ap"' in hw and "\u8fde\u64ad" in hw)
ok("toggle persists in localStorage", 'wz_autoplay' in hw)
ok("advances on video ended", 'addEventListener("ended"' in hw)
ok("supports ?autoplay=1", 'q.get("autoplay")==="1"' in hw)
ok("keyboard A toggles ??", 'e.key==="a"' in hw)

code, _, _ = http("/watch/%s?key=%s" % (mine["id"], KEY))
ok("watch (own key) 200", code == 200, str(code))
code, _, _ = http("/watch/%s?key=%s" % (other["id"], KEY))
ok("watch (other key) 403", code == 403, str(code))
code, _, _ = http("/watch/%s" % mine["id"])
ok("watch no auth 401", code == 401, str(code))
code, _, _ = http("/watch/vid_nope?browse=%s" % TOK)
ok("watch missing 404", code == 404, str(code))

code, w2, dt2 = http("/watch/%s?browse=%s" % (mine["id"], TOK))
ok("watch warm under 300ms", code == 200 and dt2 < 0.3, "%ss" % dt2)

# admin gallery must hand out working links without a key
code, ga, _ = http("/gallery?limit=3", admin=True)
ha = ga.decode("utf-8", "replace")
m = re.search(r'href="(/watch/[^"]+)"', ha)
ok("admin gallery links to /watch", bool(m))
if m:
    code, _, _ = http(m.group(1).replace("&amp;", "&"))
    ok("admin's own card link works", code == 200, str(code))

# legacy farm
try:
    files = sorted(os.listdir("/opt/h3v2/data/render/out"))
    mp4 = [f for f in files if f.endswith(".mp4")]
    fid = mp4[len(mp4) // 2][:-4]
except Exception:
    fid = ""
if fid:
    code, wl, dt3 = http("/watch/lib/%s?browse=%s" % (fid, TOK))
    hl = wl.decode("utf-8", "replace")
    ok("watch/lib (browse) 200", code == 200, "%ss" % dt3)
    ok("legacy video src ok", "/v1/library/%s/content?browse=bw_" % fid in hl)
    ok("legacy prev/next or edge", ('href="/watch/lib/' in hl))
    code, raw, _ = http("/v1/library/%s/thumb?browse=%s" % (fid, TOK))
    ok("legacy thumb via browse", code == 200 and raw[:2] == b"\xff\xd8", "%d B" % len(raw))
    code, lb, _ = http("/library?browse=%s&limit=4" % TOK)
    hb = lb.decode("utf-8", "replace")
    ok("library cards -> /watch/lib", hb.count('href="/watch/lib/') >= 4)
    ok("library thumbs carry token", "thumb?browse=bw_" in hb)

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)
