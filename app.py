# -*- coding: utf-8 -*-
"""WZ MiniMax Video API v1.1 - OpenAI-style gateway over the Hailuo account pool.

Auth : Authorization: Bearer sk-wz-...
Admin: X-Admin-Token: <WZMM_ADMIN_KEY>

v1.1 adds: RPM token bucket, per-key quota, batch submit, trial-aware scheduling,
per-account daily credit cap, disk guard + TTL cleanup, admin pool view.
"""
import os, sys, json, time, sqlite3, threading, secrets, string, shutil, collections, hmac, hashlib, subprocess, base64, tempfile, zipfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, PlainTextResponse, HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask
import uvicorn

sys.path.insert(0, "/opt/wzmm")
import agent_mm as A

DB_PATH    = os.environ.get("WZMM_DB", "/opt/wzmm/wzmm.db")
POOL_PATH  = os.environ.get("WZMM_POOL", "/opt/wzmm/pool.json")
ADMIN_KEY  = os.environ.get("WZMM_ADMIN_KEY", "")
PORT       = int(os.environ.get("WZMM_PORT", "8808"))
DEFAULT_MODEL = "MiniMax-Hailuo-2.3"
VIDEOS_DIR = os.environ.get("WZMM_VIDEOS", "/opt/wzmm/videos")
DEAD_PATH  = os.environ.get("WZMM_DEAD", "/opt/wzmm/dead.jsonl")
TTL_DAYS   = float(os.environ.get("WZMM_TTL_DAYS", "7"))
MIN_FREE_GB = float(os.environ.get("WZMM_MIN_FREE_GB", "6"))
MIN_ACCEPT_GB = float(os.environ.get("WZMM_MIN_ACCEPT_GB", "8"))
DISK_SOFT_GB = float(os.environ.get("WZMM_DISK_SOFT_GB", "14"))
GC_MIN_AGE_H = float(os.environ.get("WZMM_GC_MIN_AGE_H", "24"))
GC_WARN_GIB = float(os.environ.get("WZMM_GC_WARN_GB", "8"))
GC_KEEP_NEWEST = int(os.environ.get("WZMM_GC_KEEP_NEWEST", "1000"))
os.makedirs(VIDEOS_DIR, exist_ok=True)

# trial accounts are free; once trial ends a 6s 1080P run costs 80 credits out of
# the 400/day sign-in grant -> 5 runs/day. keep one slot in reserve by default.
EXPIRED_DAILY_CAP = int(os.environ.get("WZMM_EXPIRED_CAP", "20"))
COST_1080 = int(os.environ.get("WZMM_COST_1080", "80"))  # credits per 6s 1080P
ONLY_1080P = os.environ.get("WZMM_ONLY_1080P", "1").strip().lower() not in ("0", "false", "no", "off")
CHECKIN_SCRIPT = os.environ.get("WZMM_CHECKIN_SCRIPT", "/opt/wzmm/checkin.py")
CHECKIN_LAST = os.environ.get("WZMM_CHECKIN_LAST", "/opt/wzmm/_checkin_last.json")
DAY_TZ = 8 * 3600  # Asia/Shanghai day boundary

# ---- webhook signing (R535) -----------------------------------------------
WEBHOOK_SECRET_ENV = os.environ.get("WZMM_WEBHOOK_SECRET", "").strip()
WEBHOOK_SECRET_PATH = os.environ.get("WZMM_WEBHOOK_SECRET_FILE", "/opt/wzmm/webhook.secret")
_WEBHOOK_SECRET = [WEBHOOK_SECRET_ENV]

def _load_webhook_secret():
    """Global fallback secret. Auto-generated once and persisted 0600."""
    if _WEBHOOK_SECRET[0]:
        return _WEBHOOK_SECRET[0]
    try:
        with open(WEBHOOK_SECRET_PATH, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            _WEBHOOK_SECRET[0] = v
            return v
    except Exception:
        pass
    v = "whsec_" + secrets.token_hex(24)
    try:
        fd = os.open(WEBHOOK_SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(v + "\n")
    except Exception:
        pass
    _WEBHOOK_SECRET[0] = v
    return v

def _mask_secret(v):
    if not v:
        return ""
    return v[:10] + "..." + v[-4:] if len(v) > 18 else "***"

def _sign(secret, ts, body_bytes):
    return hmac.new(secret.encode(), ("%d." % ts).encode() + body_bytes, hashlib.sha256).hexdigest()

def _webhook_secret_for(key_id):
    try:
        c = db()
        row = c.execute("select webhook_secret from keys where id=?", (key_id,)).fetchone()
        c.close()
        if row is not None and row["webhook_secret"]:
            return row["webhook_secret"]
    except Exception:
        pass
    return _load_webhook_secret()

def _send_webhook(url, payload, event, delivery, secret, timeout=15, attempts=3):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    last = ""
    for attempt in range(attempts):
        ts = int(time.time())
        try:
            import urllib.request
            rq = urllib.request.Request(url, data=body, method="POST")
            rq.add_header("Content-Type", "application/json")
            rq.add_header("User-Agent", "wz-video-webhook/1.2")
            rq.add_header("X-WZ-Event", event)
            rq.add_header("X-WZ-Delivery", delivery)
            rq.add_header("X-WZ-Attempt", str(attempt + 1))
            rq.add_header("X-WZ-Timestamp", str(ts))
            rq.add_header("X-WZ-Signature-Version", "1")
            rq.add_header("X-WZ-Signature", "t=%d,v1=%s" % (ts, _sign(secret, ts, body)))
            with urllib.request.urlopen(rq, timeout=timeout) as resp:
                code = resp.status
            last = "http %s" % code
            if 200 <= code < 300:
                return last
        except Exception as ex:
            last = repr(ex)[:160]
        time.sleep(2 + attempt * 3)
    return last

app = FastAPI(title="WZ MiniMax Video API", version="1.4")

# Browser clients (gallery/playground/external SPAs) need CORS; "*" by default,
# override with WZMM_CORS="https://a.example,https://b.example".
_CORS = [o.strip() for o in os.environ.get("WZMM_CORS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining",
                    "X-WZ-Event", "X-WZ-Delivery", "X-WZ-Signature", "X-WZ-Timestamp",
                    "X-WZ-Attempt", "X-WZ-Signature-Version", "Content-Range", "Accept-Ranges"],
    max_age=600,
)
_db_lock = threading.Lock()
_rl_lock = threading.Lock()
RL = {}  # key_id -> deque[timestamps]

def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    with _db_lock:
        c.executescript("""
        create table if not exists keys(
          id text primary key, key text unique, name text, created real,
          rpm integer default 60, quota integer default 0, used integer default 0,
          last_used real default 0, disabled integer default 0);
        create table if not exists tasks(
          id text primary key, upstream text, email text, model text, resolution text,
          duration integer, ratio text, prompt text, status text, url text, err text,
          created real, updated real, key_id text);
        create index if not exists ix_tasks_status on tasks(status);
        create index if not exists ix_tasks_email on tasks(email, created);
        """)
        for ddl in ("alter table tasks add column local_path text",
                    "alter table tasks add column batch_id text",
                    "alter table tasks add column retry integer default 0",
                    "alter table tasks add column callback_url text",
                    "alter table tasks add column idem_key text",
                    "alter table tasks add column pinned integer default 0",
                    "alter table tasks add column expires_at real",
                    "alter table tasks add column released_at real",
                    "alter table keys add column max_concurrent integer default 32",
                    "alter table keys add column daily_quota integer default 0",
                    "create index if not exists ix_tasks_idem on tasks(key_id, idem_key)",
                    "alter table keys add column webhook_secret text"):
            try:
                c.execute(ddl)
            except Exception:
                pass
        c.commit(); c.close()

def day_key(ts=None):
    return int((ts or time.time()) + DAY_TZ) // 86400

def day_start(ts=None):
    return ((ts or time.time()) + DAY_TZ) // 86400 * 86400 - DAY_TZ

_ACT_CACHE = {"ts": 0.0, "map": {}}
_ACT_LOCK = threading.Lock()

def _active_map():
    """Running tasks per account, cached for 1s (R560).

    R560: Pool.pick used to treat "trial account" as an absolute first class, so a
    200-job burst hammered 49 trial accounts while 65 expired accounts idled
    (547.6s wall / 21.9 per min). Spreading by live running count first fixes it.
    """
    now = time.time()
    with _ACT_LOCK:
        if now - _ACT_CACHE["ts"] < 1.0:
            return _ACT_CACHE["map"]
    try:
        c = db()
        rows = c.execute("select email, count(*) n from tasks where status='running' "
                         "and email is not null group by email").fetchall()
        c.close()
        m = {r["email"]: int(r["n"]) for r in rows}
    except Exception:
        m = _ACT_CACHE["map"]
    with _ACT_LOCK:
        _ACT_CACHE["ts"] = now
        _ACT_CACHE["map"] = m
    return m

def _daily_room(key_row):
    """Tasks this key may still create today; None means unlimited."""
    try:
        dq = int(key_row["daily_quota"] or 0)
    except Exception:
        dq = 0
    if not dq:
        return None
    c = db()
    n = c.execute("select count(*) n from tasks where key_id=? and created>=?",
                  (key_row["id"], day_start())).fetchone()["n"]
    c.close()
    return max(0, dq - n)

def _quota_room(key_row):
    q = int(key_row["quota"] or 0)
    if not q:
        return None
    return max(0, q - int(key_row["used"] or 0))


def _enforce_quota_room(key_row, want, allow_partial=False):
    """R580b: clamp a batch to the key's remaining task quota before creating rows."""
    room = _quota_room(key_row)
    if room is None or want <= room:
        return want
    if allow_partial and room > 0:
        return room
    raise HTTPException(402, "quota: %d left, requested %d" % (room, want))


def _enforce_daily_room(key_row, want, allow_partial=False):
    room = _daily_room(key_row)
    if room is None or want <= room:
        return want
    if allow_partial and room > 0:
        return room
    raise HTTPException(402, "daily quota: %d left today, requested %d" % (room, want))

# ---------------------------------------------------------------- pool ----
class Pool:
    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))
        accs = d["accounts"] if isinstance(d, dict) else d
        self.accs = [a for a in accs if a.get("token") and a.get("ruid")]
        self.by = {a["email"]: a for a in self.accs}
        self.load = {a["email"]: 0 for a in self.accs}
        self.dead = set()
        self.cool = {}
        self.usage = {}      # email -> [daykey, count]
        self.credits = {}    # email -> credits reported by hailuoai equity
        self.streak = {}     # email -> consecutive submit failures (health hint)
        self.trial = {}
        for a in self.accs:
            try:
                self.trial[a["email"]] = float(a.get("trial_exp") or 0) / 1000.0
            except Exception:
                self.trial[a["email"]] = 0.0
            c = a.get("credits_now")
            if isinstance(c, (int, float)):
                self.credits[a["email"]] = float(c)
        self.lock = threading.Lock()
        self._load_state()

    def _load_state(self):
        try:
            for line in open(DEAD_PATH, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if j.get("t") == "dead":
                    # stale events (older than 48h) are ignored; accounts get probed again
                    if time.time() - float(j.get("ts") or 0) < 48 * 3600:
                        self.dead.add(j["email"])
                elif j.get("t") == "cool":
                    self.cool[j["email"]] = float(j.get("until") or 0)
        except FileNotFoundError:
            pass
        # rebuild today's per-account usage from db
        try:
            c = db()
            rows = c.execute("select email, count(*) n from tasks where created>=? and email is not null "
                             "and status in ('queued','running','succeeded') group by email",
                             (day_start(),)).fetchall()
            c.close()
            for r in rows:
                self.usage[r["email"]] = [day_key(), r["n"]]
        except Exception:
            pass

    def _persist(self, obj):
        try:
            with open(DEAD_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj) + "\n")
        except Exception:
            pass

    def used_today(self, email):
        v = self.usage.get(email)
        if not v or v[0] != day_key():
            return 0
        return v[1]

    def cap_left(self, email):
        if self.trial.get(email, 0) > time.time():
            return 10 ** 6
        left = EXPIRED_DAILY_CAP - self.used_today(email)
        c = self.credits.get(email)
        # only clamp when the account actually holds credits; a 0-credit account may still
        # have an untracked free daily allowance (verified 2026-10-20 on an expired account)
        if isinstance(c, (int, float)) and c > 0:
            left = min(left, int(c) // max(1, COST_1080))
        return max(0, left)

    def available(self, now=None):
        """Accounts that can be handed a job right now (not dead / not cooling / cap left)."""
        now = now or time.time()
        with self.lock:
            n = 0
            for a in self.accs:
                e = a["email"]
                if e in self.dead:
                    continue
                if self.cool.get(e, 0) > now:
                    continue
                if self.cap_left(e) <= 0:
                    continue
                n += 1
            return n

    def reload_meta(self):
        """pick up refreshed credits/trial_exp from pool.json without restarting"""
        try:
            d = json.load(open(POOL_PATH, encoding="utf-8"))
            accs = d["accounts"] if isinstance(d, dict) else d
        except Exception:
            return 0
        n = 0
        added = 0
        with self.lock:
            for a in accs:
                e = a.get("email")
                if not e:
                    continue
                if e not in self.by:
                    if not (a.get("token") and a.get("ruid")):
                        continue
                    self.accs.append(a)
                    self.by[e] = a
                    self.load[e] = 0
                    self.streak[e] = 0
                    added += 1
                try:
                    self.trial[e] = float(a.get("trial_exp") or 0) / 1000.0
                except Exception:
                    pass
                c = a.get("credits_now")
                if isinstance(c, (int, float)):
                    self.credits[e] = float(c)
                # keep the in-memory record in sync so save() never writes stale numbers
                rec = self.by[e]
                rec["trial_exp"] = a.get("trial_exp")
                rec["credits_now"] = c
                if a.get("equity_checked_at"):
                    rec["equity_checked_at"] = a["equity_checked_at"]
                if a.get("alive") is True and e in self.dead:
                    self.dead.discard(e)
                n += 1
        return n

    def take(self, email):
        with self.lock:
            d = day_key()
            v = self.usage.get(email)
            if not v or v[0] != d:
                self.usage[email] = [d, 1]
            else:
                v[1] += 1
            self.load[email] = self.load.get(email, 0) + 1

    def release(self, email):
        with self.lock:
            self.load[email] = max(0, self.load.get(email, 1) - 1)

    def pick(self, prefer=None):
        act = _active_map()          # R560: spread the burst across idle accounts
        with self.lock:
            if prefer and prefer in self.by and prefer not in self.dead:
                self.usage.setdefault(prefer, [day_key(), 0])
                return self.by[prefer]
            now = time.time()
            cand = [a for a in self.accs if a["email"] not in self.dead
                    and self.cool.get(a["email"], 0) <= now
                    and self.cap_left(a["email"]) > 0]
            if not cand:
                raise RuntimeError("no live accounts in pool (all dead/parked/capped)")
            # R560: idle accounts first; trial preference only breaks ties.
            cand.sort(key=lambda a: (act.get(a["email"], 0),
                                     0 if self.trial.get(a["email"], 0) > now else 1,
                                     self.streak.get(a["email"], 0),
                                     self.load.get(a["email"], 0),
                                     self.usage.get(a["email"], [0, 0])[1],
                                     a["email"]))
            a = cand[0]
            self.load[a["email"]] = self.load.get(a["email"], 0) + 1
            return a

    def save(self):
        with self.lock:
            payload = {"accounts": self.accs}
            tmp = POOL_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, POOL_PATH)

    def merge_tokens(self, items):
        """items: [{email, token, ruid}] -> update pool, persist"""
        n = 0
        with self.lock:
            for it in items:
                a = self.by.get(it.get("email"))
                if not a or not it.get("token"):
                    continue
                a["token"] = it["token"]
                if it.get("ruid"):
                    a["ruid"] = str(it["ruid"])
                # keep exp truthful: decode the JWT we just accepted
                try:
                    import base64 as _b64, json as _json
                    _p = it["token"].split(".")[1]
                    _p += "=" * (-len(_p) % 4)
                    _d = _json.loads(_b64.urlsafe_b64decode(_p))
                    if _d.get("exp"):
                        a["exp"] = int(_d["exp"])
                    _u = (_d.get("user") or {}).get("id")
                    if _u and not it.get("ruid"):
                        a["ruid"] = str(_u)
                except Exception:
                    if it.get("exp"):
                        a["exp"] = int(it["exp"])
                if it.get("exp") and not a.get("exp"):
                    a["exp"] = int(it["exp"])
                a["alive"] = True
                a["usable"] = True
                a["revived_by"] = it.get("how") or "merge"
                a["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.dead.discard(a["email"])
                self.cool.pop(a["email"], None)
                n += 1
        if n:
            self.save()
        return n

    def note(self, email, ok):
        with self.lock:
            self.streak[email] = 0 if ok else self.streak.get(email, 0) + 1

    def mark_dead(self, email, why=""):
        with self.lock:
            self.dead.add(email)
            self.streak[email] = self.streak.get(email, 0) + 1
        self._persist({"t": "dead", "email": email, "why": why, "ts": time.time()})

    def park(self, email, seconds=7200, why=""):
        with self.lock:
            self.cool[email] = time.time() + seconds
        self._persist({"t": "cool", "email": email, "until": time.time() + seconds, "why": why})

    def stats(self, now=None):
        now = now or time.time()
        trial_alive = [e for e in self.by if e not in self.dead and self.trial.get(e, 0) > now]
        expired_alive = [e for e in self.by if e not in self.dead and self.trial.get(e, 0) <= now]
        points_total = 0
        signin_done = 0
        for a in self.accs:
            try:
                points_total += int(a.get("points") or 0)
            except Exception:
                pass
            if str(a.get("signin_status") or "") == "3":
                signin_done += 1
        exp_24h = [e for e in trial_alive if self.trial.get(e, 0) - now < 86400]
        exp_7d = [e for e in trial_alive if self.trial.get(e, 0) - now < 7 * 86400]
        # trial accounts are effectively uncapped upstream; clamp their contribution so the
        # headline "daily_room" stays a readable floor (1000 = "at least this many").
        daily_room = sum(min(self.cap_left(e), 1000) for e in self.by if e not in self.dead)
        return {"total": len(self.accs), "dead": len(self.dead),
                "parked": len([v for v in self.cool.values() if v > now]),
                "trial_alive": len(trial_alive),
                "expired_alive": len(expired_alive),
                "expired_capacity": sum(self.cap_left(e) for e in expired_alive),
                "trial_expiring_24h": len(exp_24h),
                "trial_expiring_7d": len(exp_7d),
                "daily_room": daily_room,
                "credits_total": int(sum(v for v in self.credits.values())),
                "points_total": points_total,
                "signin_done_today": signin_done}

POOL = Pool(POOL_PATH)
_local = threading.local()
def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = A.sess()
        _local.s = s
    return s

# ---------------------------------------------------------------- keys ----
def _rand_key():
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    return "sk-wz-" + body

def find_key(key):
    c = db()
    row = c.execute("select * from keys where key=? and disabled=0", (key,)).fetchone()
    c.close()
    return row

def touch_key(row):
    """R580: `used` counts accepted tasks (see _create_task), not requests.

    Polling/downloads must not burn a key's quota, so this only refreshes last_used.
    """
    now = time.time()
    c = db()
    with _db_lock:
        c.execute("update keys set last_used=? where id=?", (now, row["id"]))
        c.commit(); c.close()

def check_rpm(row):
    rpm = int(row["rpm"] or 0)
    if rpm <= 0:
        return
    now = time.time()
    with _rl_lock:
        q = RL.setdefault(row["id"], collections.deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= rpm:
            raise HTTPException(429, "rate limit exceeded: %d req/min" % rpm)
        q.append(now)

def require_key(request: Request):
    h = request.headers.get("authorization") or ""
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer key")
    row = find_key(h[7:].strip())
    if not row:
        raise HTTPException(401, "invalid api key")
    check_rpm(row)
    rpm = int(row["rpm"] or 0)
    if rpm:
        with _rl_lock:
            used = len(RL.get(row["id"]) or ())
        request.state.rl = (rpm, max(0, rpm - used))
    # quota / daily_quota are enforced at intake (_create_task), so an exhausted
    # key can still poll and download its existing videos.
    touch_key(row)
    return row

def require_key_light(request: Request):
    """Validate a key from Authorization header or ?key= (for <video src> / downloads).

    No RPM accounting: byte-range streaming would otherwise burn the caller's quota.
    """
    h = request.headers.get("authorization") or ""
    k = ""
    if h.lower().startswith("bearer "):
        k = h[7:].strip()
    if not k:
        k = (request.query_params.get("key") or "").strip()
    if not k:
        raise HTTPException(401, "missing api key")
    row = find_key(k)
    if not row:
        raise HTTPException(401, "invalid api key")
    # R601: read endpoints do not append to the bucket, but still expose honest
    # X-RateLimit-* headers so clients can see the shared budget.
    rpm = int(row["rpm"] or 0)
    if rpm:
        now = time.time()
        with _rl_lock:
            q = RL.get(row["id"]) or ()
            live = sum(1 for t in q if now - t <= 60)
        request.state.rl = (rpm, max(0, rpm - live))
    return row

def is_admin(request: Request):
    return bool(ADMIN_KEY) and request.headers.get("x-admin-token") == ADMIN_KEY


# ---- R696: read-only browse token (no api key, no admin, no writes) --------
# A signed, expiring token that only unlocks *reading* the gallery / legacy library
# and the mp4/thumb/meta endpoints. Write endpoints never look at it.
BROWSE_TTL_MAX_H = float(os.environ.get("WZMM_BROWSE_TTL_MAX_H", "720"))


# R697: rotating key ids. Each rotation bumps `kid` and raises `min_kid`, which
# invalidates every token signed with an older kid in one shot (no denylist needed).
BROWSE_STATE = os.environ.get("WZMM_BROWSE_STATE", "/opt/wzmm/browse_state.json")
_BROWSE_LOCK = threading.RLock()
_BROWSE_CACHE = {"mtime": -1.0, "state": {"kid": 1, "min_kid": 1, "issued": []}}


def _browse_load() -> dict:
    st = {"kid": 1, "min_kid": 1, "issued": [], "rotated_at": 0}
    try:
        with open(BROWSE_STATE, encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            st.update(got)
    except Exception:
        pass
    try:
        st["kid"] = max(1, int(st.get("kid") or 1))
        st["min_kid"] = max(1, int(st.get("min_kid") or st["kid"]))
    except Exception:
        st["kid"] = st["min_kid"] = 1
    if not isinstance(st.get("issued"), list):
        st["issued"] = []
    return st


def _browse_state() -> dict:
    try:
        mt = os.stat(BROWSE_STATE).st_mtime
    except OSError:
        mt = 0.0
    with _BROWSE_LOCK:
        if _BROWSE_CACHE["mtime"] == mt:
            return _BROWSE_CACHE["state"]
    st = _browse_load()
    with _BROWSE_LOCK:
        _BROWSE_CACHE["mtime"] = mt
        _BROWSE_CACHE["state"] = st
    return st


def _browse_save(st: dict) -> None:
    tmp = BROWSE_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, BROWSE_STATE)
    with _BROWSE_LOCK:
        _BROWSE_CACHE["mtime"] = -1.0        # force a reload on the next request


def _browse_mac(exp: int, kid: int = 1) -> str:
    # kid 1 keeps the original message shape so already-issued links stay valid.
    msg = ("browse:%d" % exp) if int(kid) == 1 else ("browse:v%d:%d" % (int(kid), exp))
    return hmac.new((ADMIN_KEY or "wz-browse").encode(), msg.encode(),
                    hashlib.sha256).hexdigest()[:32]


def make_browse_token(ttl_h: float = 168.0, note: str = "", request: Request = None) -> dict:
    ttl_h = max(0.05, min(float(ttl_h), BROWSE_TTL_MAX_H))
    exp = int(time.time() + ttl_h * 3600)
    with _BROWSE_LOCK:
        st = _browse_state()
        kid = int(st["kid"])
    tok = ("bw_%d.%s" % (exp, _browse_mac(exp, 1))) if kid == 1 else \
          ("bw_%d_%d.%s" % (kid, exp, _browse_mac(exp, kid)))
    rec = {"kid": kid, "exp": exp, "at": int(time.time()), "ttl_h": round(ttl_h, 2),
           "note": (note or "")[:80]}
    try:
        if request is not None:
            rec["ip"] = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
                        (request.client.host if request.client else "")
            rec["ua"] = (request.headers.get("user-agent") or "")[:80]
        st = _browse_load()
        st["issued"] = (st.get("issued") or [])[-199:] + [rec]
        _browse_save(st)
    except Exception:
        pass
    return {"token": tok, "expires": exp, "ttl_h": round(ttl_h, 2), "kid": kid,
            "scope": "read:gallery,library,videos"}


def is_browse(request: Request, token: str = "") -> bool:
    tok = (token or request.query_params.get("browse")
           or request.headers.get("x-browse-token") or "").strip()
    if not tok.startswith("bw_") or "." not in tok:
        return False
    head, _, mac = tok[3:].partition(".")
    kid = 1
    if "_" in head:
        ks, _, es = head.partition("_")
        if not (ks.isdigit() and es.isdigit()):
            return False
        kid, head = int(ks), es
    try:
        exp = int(head)
    except ValueError:
        return False
    if exp < time.time():
        return False
    st = _browse_state()
    if kid < int(st["min_kid"]) or kid > int(st["kid"]):
        return False                        # rotated out
    return hmac.compare_digest(mac, _browse_mac(exp, kid))


_AUTO_BROWSE = {"kid": 0, "tok": "", "exp": 0.0}


def _auto_browse_token() -> str:
    """R698: admin pages need working links without pasting a key.

    Mint (and cache for its lifetime) a short browse token so every card link on
    an admin-rendered gallery/library page is clickable.
    """
    st = _browse_state()
    now = time.time()
    if _AUTO_BROWSE["kid"] == int(st["kid"]) and _AUTO_BROWSE["exp"] > now + 300:
        return _AUTO_BROWSE["tok"]
    t = make_browse_token(2.0, note="auto:admin-view")
    _AUTO_BROWSE.update({"kid": t["kid"], "tok": t["token"], "exp": t["expires"]})
    return t["token"]


def _page_auth(request: Request, key: str = "", browse: str = ""):
    """(query_fragment, hidden_input_html) for template links/forms.

    Prefers the browse token (works for every key), then ?key=, then admin.
    """
    if browse and is_browse(request, browse):
        aq = "browse=" + urllib.parse.quote(browse)
        return aq, '<input type="hidden" name="browse" value="%s">' % _esc(browse)
    if key:
        aq = "key=" + urllib.parse.quote(key)
        return aq, '<input type="hidden" name="key" value="%s">' % _esc(key)
    if is_admin(request):
        tok = _auto_browse_token()
        aq = "browse=" + urllib.parse.quote(tok)
        return aq, '<input type="hidden" name="browse" value="%s">' % tok
    return "", ""


def viewer_ok(request: Request, token: str = "") -> bool:
    """Admin OR a valid browse token: the only two ways to see every key's videos."""
    return is_admin(request) or is_browse(request, token)


def can_read_task(row, request: Request, key_row):
    """A task (and its mp4) belongs to the key that created it; admin/browse read all."""
    if is_admin(request) or is_browse(request):
        return True
    return str(row["key_id"] or "") == str(key_row["id"])


def check_concurrency(row):
    try:
        mc = int(row["max_concurrent"] or 0)
    except Exception:
        mc = 0
    if not mc:
        return
    c = db()
    n = c.execute("select count(*) n from tasks where key_id=? and status in ('queued','running')",
                  (row["id"],)).fetchone()["n"]
    c.close()
    if n >= mc:
        raise HTTPException(429, "too many concurrent tasks for this key (%d in flight, max %d)" % (n, mc))

def require_admin(request: Request):
    tok = request.headers.get("x-admin-token") or ""
    if not tok:
        raise HTTPException(401, "missing X-Admin-Token")
    if not ADMIN_KEY or tok != ADMIN_KEY:
        raise HTTPException(403, "invalid admin token")
    return True

# --------------------------------------------------------------- tasks ----
EXEC = ThreadPoolExecutor(max_workers=24, thread_name_prefix="wz")
HOOK = ThreadPoolExecutor(max_workers=8, thread_name_prefix="hook")
POLL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="poll")

MODEL_SPEC = {
    "MiniMax-Hailuo-2.3": {"res": ("768P", "1080P"), "dur": (6, 10), "dur_label": "6 or 10",
                           "dur_res": {6: ("768P", "1080P"), 10: ("768P",)},
                           "note": "free 1080P path (no credits consumed); 10s only at 768P"},
    "MiniMax-H3": {"res": ("768P", "2K"), "dur": tuple(range(4, 16)), "dur_label": "4-15",
                   "note": "requires paid account credits"},
    "MiniMax-H3-Max": {"res": ("768P", "2K"), "dur": tuple(range(5, 16)), "dur_label": "5-15",
                       "note": "requires paid account credits"},
}
PRICES = {"MiniMax-Hailuo-2.3": {("768P", 6): 25, ("1080P", 6): 80, ("768P", 10): 50}}
TASK_LOG = "/opt/wzmm/tasks.log"
MAX_INFLIGHT = int(os.environ.get("WZMM_MAX_INFLIGHT", "64") or 0)

def _log_evt(kind, **kw):
    try:
        rec = {"ts": int(time.time()), "evt": kind}
        rec.update(kw)
        with open(TASK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False)[:600] + "\n")
    except Exception:
        pass

def _norm_res(v):
    if not v:
        return "1080P"
    s = str(v).upper().replace(" ", "").replace("*", "X")
    if s in ("1080P", "1080", "FHD", "1920X1080"):
        return "1080P"
    if s in ("768P", "768", "720P", "720", "1280X720"):
        return "768P"
    if s in ("2K", "1440P", "2560X1440", "QHD"):
        return "2K"
    if s in ("4K", "2160P", "UHD"):
        return "4K"
    if s in ("1K", "1024"):
        return "1K"
    return "1080P"

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_RESERVED = 0

# R608: one statvfs cache for the whole process; avoids a syscall per slot check.
_DISK_CACHE = {"ts": 0.0, "free_gb": None}


def _disk_free_gib(ttl: float = 1.0):
    now = time.time()
    if _DISK_CACHE["free_gb"] is not None and now - _DISK_CACHE["ts"] < ttl:
        return _DISK_CACHE["free_gb"]
    try:
        v = shutil.disk_usage(VIDEOS_DIR).free / (1 << 30)
    except Exception:
        v = -1.0
    _DISK_CACHE.update(ts=now, free_gb=v)
    return v


def _disk_mode():
    """0 = normal, 1 = soft (thin), 2 = hard (refuse new work).

    Soft mode is the difference between "slow down and survive" and
    "stop dead the moment the last GiB is gone".
    """
    f = _disk_free_gib()
    if f < 0:
        return 0
    if MIN_ACCEPT_GB > 0 and f < MIN_ACCEPT_GB:
        return 2
    if DISK_SOFT_GB > 0 and f < DISK_SOFT_GB:
        return 1
    return 0


def _eff_cap():
    """Admission ceiling = configured cap, but never far beyond the live account pool.

    R554: 256 concurrent passed 256/256, yet when a 503 storm parks most accounts the
    queue would keep admitting work that cannot be routed. Cap at ~3 jobs per live
    account (floor 6) so a degraded pool degrades throughput instead of wedging.
    """
    cap = MAX_INFLIGHT or 10 ** 6
    try:
        avail = POOL.available()
        if avail:
            cap = min(cap, max(6, avail * 3))
    except Exception:
        pass
    m = _disk_mode()
    if m == 2:
        return 0
    if m == 1:
        cap = max(4, int(cap * 0.25))
    return cap


def _slot_take():
    """Reserve an upstream slot; DB 'running' + reservations must stay below the cap."""
    global _INFLIGHT_RESERVED
    with _INFLIGHT_LOCK:
        c = db()
        run = c.execute("select count(*) n from tasks where status='running'").fetchone()["n"]
        c.close()
        if run + _INFLIGHT_RESERVED >= _eff_cap():
            return False
        _INFLIGHT_RESERVED += 1
        return True


def _slot_give():
    global _INFLIGHT_RESERVED
    with _INFLIGHT_LOCK:
        _INFLIGHT_RESERVED = max(0, _INFLIGHT_RESERVED - 1)


# R552: consecutive upstream-transient hits per account (escalating park)
# R554: persisted so a restart does not re-probe accounts that are already known-bad.
_BAD503_PATH = "/opt/wzmm/bad503.json"
_BAD503_LOCK = threading.Lock()
_BAD503_SAVED = [0.0]

def _bad503_load():
    try:
        d = json.load(open(_BAD503_PATH, encoding="utf-8"))
        if time.time() - float(d.get("ts") or 0) < 24 * 3600:
            return {str(k): int(v) for k, v in (d.get("map") or {}).items()}
    except Exception:
        pass
    return {}

def _bad503_save(force=False):
    with _BAD503_LOCK:
        now = time.time()
        if not force and now - _BAD503_SAVED[0] < 5:
            return
        _BAD503_SAVED[0] = now
        try:
            tmp = _BAD503_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": now, "map": _BAD503}, f)
            os.replace(tmp, _BAD503_PATH)
        except Exception:
            pass

_BAD503 = _bad503_load()

def _submit(row_id):
    """Admission-controlled entry point; the check+reserve is atomic under a lock."""
    if MAX_INFLIGHT and not _slot_take():
        time.sleep(3 + (hash(row_id) % 5))
        EXEC.submit(_submit, row_id)
        return
    try:
        _submit_inner(row_id)
    finally:
        if MAX_INFLIGHT:
            _slot_give()


def _submit_inner(row_id):
    c = db()
    row = c.execute("select * from tasks where id=?", (row_id,)).fetchone()
    c.close()
    if not row or row["status"] != "queued":
        return
    args = {"prompt": row["prompt"], "model": row["model"], "resolution": row["resolution"],
            "duration": int(row["duration"]), "ratio": row["ratio"]}
    last = None
    for attempt in range(8):
        try:
            acc = POOL.pick()
        except Exception as e:
            # pool temporarily exhausted (all capped/parked): wait instead of failing the job
            retry = int(row["retry"] or 0)
            if retry < 3:
                c = db()
                with _db_lock:
                    c.execute("update tasks set retry=retry+1, err=?, updated=? where id=?",
                              ("pool busy, waiting: %s" % str(e)[:160], time.time(), row_id))
                    c.commit(); c.close()
                threading.Timer(60, lambda rid=row_id: EXEC.submit(_submit, rid)).start()
                return
            _fail(row_id, "pool: %s" % e); return
        try:
            sub = A.tool_call(sess(), acc["token"], str(acc["ruid"]),
                              A.profile_for(acc["email"]), A.SUBMIT_TOOL, args, timeout=120)
        except Exception as e:
            sub = {"_err": str(e)[:200]}
        finally:
            POOL.release(acc["email"])
        tid = str((sub or {}).get("task_id") or "")
        if tid:
            POOL.take(acc["email"])
            POOL.note(acc["email"], True)
            if _BAD503.pop(acc["email"], None) is not None:
                _bad503_save()
            _log_evt("submit_ok", id=row_id, upstream=tid, account=acc["email"],
                     model=row["model"], res=row["resolution"], dur=row["duration"])
            c = db()
            with _db_lock:
                c.execute("update tasks set upstream=?, email=?, status='running', updated=? where id=?",
                          (tid, acc["email"], time.time(), row_id))
                c.commit(); c.close()
            return
        last = sub
        code = (sub or {}).get("_http")
        inner0 = sub.get("code") if isinstance(sub, dict) else None
        txt0 = json.dumps(sub, ensure_ascii=False)[:200]
        low0 = txt0.lower()
        if (code in (500, 502, 503, 504) or inner0 in (500, 502, 503, 504)
                or "temporarily unavailable" in low0 or "try again" in low0
                or "upstream busy" in low0):
            # upstream overload: short cool-off + backoff, never a hard fail on first hit
            _log_evt("transient", id=row_id, account=acc["email"], detail=txt0[:160])
            POOL.note(acc["email"], False)          # deprioritise in pick()
            _n = _BAD503.get(acc["email"], 0) + 1
            _BAD503[acc["email"]] = _n
            _bad503_save()
            _cool = 90 if _n < 2 else (900 if _n < 4 else 6 * 3600)
            POOL.park(acc["email"], _cool, "upstream transient x%d" % _n)
            time.sleep(min(8.0, 2.0 + attempt * 2.0))
            continue
        if code in (401, 403):
            POOL.mark_dead(acc["email"], "http %s" % code); continue
        POOL.note(acc["email"], False)
        txt = json.dumps(sub, ensure_ascii=False)[:200]
        low = txt.lower()
        inner = sub.get("code") if isinstance(sub, dict) else None
        # caller-side validation problems must not burn the account
        if code == 400 or inner == 400 or "must be" in low or " requires " in low or "invalid" in low:
            _log_evt("reject", id=row_id, detail=txt[:200])
            _fail(row_id, txt); return
        # account-level exhaustion / throttling -> cool the account; model-level billing
        # limits (e.g. MiniMax-H3 needs paid credits) -> fail the job, keep the account
        if code == 429 or "rate limit" in low or "plan balance has run out" in low:
            POOL.park(acc["email"], 6 * 3600, txt[:120]); continue
        if code == 402 or inner == 402 or "insufficient" in low or "quota" in low or "credit" in low:
            _log_evt("no_credit", id=row_id, detail=txt[:200])
            _park_on_402(acc, txt)
            _fail(row_id, txt); return
        POOL.park(acc["email"], 7200, txt[:120])
        continue
    _log_evt("submit_fail", id=row_id, detail=json.dumps(last, ensure_ascii=False)[:200] if last else "")
    _fail(row_id, json.dumps(last, ensure_ascii=False)[:300] if last else "submit failed")


def _park_on_402(acc, txt=""):
    """R704: a 402 means this account cannot pay for another run right now.

    Before this the submit loop only logged and failed, so the same broke account
    kept winning the scheduler and burned one failed task per attempt (10 in 24h).
    """
    try:
        hrs = float(os.environ.get("WZMM_402_COOLDOWN_H", "2"))
    except ValueError:
        hrs = 2.0
    hrs = max(0.1, min(hrs, 24.0))
    why = ("402 " + (txt or ""))[:100]
    POOL.park(acc["email"], int(hrs * 3600), why)
    _alert("credit_402", "account %s returned 402; parked %.1fh" % (acc["email"], hrs), "warn", 1800)
    return int(hrs * 3600)


def _unpark_one(email):
    with POOL.lock:
        POOL.cool.pop(email, None)
    try:
        keep = []
        for line in open(DEAD_PATH, encoding="utf-8"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("t") == "cool" and j.get("email") == email:
                continue
            keep.append(line)
        with open(DEAD_PATH, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except FileNotFoundError:
        pass


def _disk_admit():
    """R574: refuse new work *before* the GC has to delete anything.

    Costs credits to start a render we cannot store; 8 GiB default headroom keeps
    a few thousand clips of slack. WZMM_MIN_ACCEPT_GB=0 disables the gate.
    """
    if MIN_ACCEPT_GB <= 0:
        return
    try:
        free = shutil.disk_usage(VIDEOS_DIR).free / (1 << 30)
    except Exception:
        return
    if free < MIN_ACCEPT_GB:
        _alert("disk_reject", "refused new task: free %.1f GiB < %.1f GiB" % (free, MIN_ACCEPT_GB),
               "crit", 600)
        raise HTTPException(503, detail="storage nearly full (%.1f GiB free); retry later" % free,
                            headers={"Retry-After": "600"})
    if DISK_SOFT_GB > 0 and free < DISK_SOFT_GB:
        _alert("disk_soft", "disk soft mode: free %.1f GiB < %.1f GiB, concurrency capped at 25%%"
               % (free, DISK_SOFT_GB), "warn", 3600)


def _disk_guard():
    try:
        free = shutil.disk_usage(VIDEOS_DIR).free / (1 << 30)
    except Exception:
        return
    if free > MIN_FREE_GB:
        return
    # R562: soft mode keeps files younger than 6h; only a real emergency (<=12 GiB
    # free) is allowed to touch fresh outputs.
    emergency = free <= MIN_FREE_GB - 8
    min_age = 0 if emergency else GC_MIN_AGE_H * 3600
    now_ts = time.time()
    files = []
    for fn in os.listdir(VIDEOS_DIR):
        if not fn.endswith(".mp4"):
            continue
        p = os.path.join(VIDEOS_DIR, fn)
        try:
            files.append((os.path.getmtime(p), os.path.getsize(p), p))
        except Exception:
            pass
    files.sort()
    pinned = _pinned_ids()
    n = 0
    freed = 0
    skipped = 0
    # R587: hard floor - never touch the newest N clips, even in emergency
    keep_from = max(0, len(files) - GC_KEEP_NEWEST)
    for mt, sz, p in files[:keep_from]:
        if shutil.disk_usage(VIDEOS_DIR).free / (1 << 30) > MIN_FREE_GB + 2:
            break
        if not emergency and (now_ts - mt) < min_age:
            skipped += 1
            continue
        tid = os.path.basename(p)[:-4]
        if tid in pinned:
            skipped += 1
            continue
        try:
            os.remove(p)
            n += 1
            freed += sz
            try:
                cc = db()
                with _db_lock:
                    cc.execute("update tasks set local_path=null where id=?", (tid,))
                    cc.commit()
                cc.close()
            except Exception:
                pass
            _log_evt("disk_gc_file", id=tid, bytes=sz)
        except Exception:
            pass
    if n or skipped:
        # R561/R562: never let the guard remove files silently, and say what was held back
        _alert("disk_gc",
               "auto-GC removed %d video(s), freed %.0f MB, kept %d fresh + %d newest file(s) "
               "(free now %.1f GiB, emergency=%s)"
               % (n, freed / 1e6, skipped, min(GC_KEEP_NEWEST, len(files)),
                  shutil.disk_usage(VIDEOS_DIR).free / (1 << 30), emergency), "crit", 300)

def _download(row_id, url):
    import urllib.request
    _disk_guard()
    path = os.path.join(VIDEOS_DIR, row_id + ".mp4")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        if os.path.getsize(path) < 10000:
            raise RuntimeError("too small")
        c = db()
        rel = c.execute("select released_at from tasks where id=?", (row_id,)).fetchone()
        if rel and rel["released_at"]:
            # R634: the client released the clip while we were still downloading it.
            c.close()
            try:
                os.remove(path)
            except Exception:
                pass
            _log_evt("download_skipped_released", id=row_id)
            return
        with _db_lock:
            c.execute("update tasks set local_path=? where id=?", (path, row_id))
            c.commit(); c.close()
    except Exception:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

def _deliver(row_id):
    c = db()
    row = c.execute("select * from tasks where id=?", (row_id,)).fetchone()
    c.close()
    if not row or not row["callback_url"]:
        return
    payload = {"id": row["id"], "object": "video.generation", "status": row["status"],
               "created": int(row["created"]), "model": row["model"], "resolution": row["resolution"],
               "duration": row["duration"], "ratio": row["ratio"], "url": row["url"],
               "bytes": os.path.getsize(row["local_path"]) if row["local_path"] and os.path.isfile(row["local_path"]) else None,
               "error": row["err"], "batch_id": row["batch_id"] if "batch_id" in row.keys() else None}
    if row["status"] == "succeeded":
        payload["content_url"] = "/v1/videos/%s/content" % row["id"]
    secret = _webhook_secret_for(row["key_id"] if "key_id" in row.keys() else None)
    delivery = "dlv_" + hashlib.sha256(("%s|%s" % (row["id"], row["status"])).encode()).hexdigest()[:24]
    last = _send_webhook(row["callback_url"], payload, "video.%s" % row["status"], delivery, secret)
    if not last.startswith("http 2"):
        _hookq_add({"delivery": delivery, "url": row["callback_url"], "payload": payload,
                    "event": "video.%s" % row["status"], "secret": secret,
                    "tries": 1, "last": last, "next_ts": time.time() + HOOK_BACKOFF[0]})
        _log_evt("hook_enqueued", id=row["id"], delivery=delivery, detail=str(last)[:120])
    try:
        with open("/opt/wzmm/webhook.log", "a", encoding="utf-8") as f:
            f.write("[%s] %s %s -> %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), row["id"], delivery,
                                             row["callback_url"][:120], last))
    except Exception:
        pass

# ------------------------------------------------------- webhook retry queue --
HOOKQ_PATH = os.environ.get("WZMM_HOOKQ", "/opt/wzmm/webhook_queue.json")
HOOKQ = []
HOOKQ_LOCK = threading.Lock()
HOOK_MAX_TRIES = 6
HOOK_BACKOFF = [60, 300, 900, 3600, 6 * 3600]
HOOK_STATS = {"retried": 0, "delivered": 0, "dropped": 0}
HOOK_STATS_LOCK = threading.Lock()


def _hook_stat(name, n=1):
    with HOOK_STATS_LOCK:
        HOOK_STATS[name] = int(HOOK_STATS.get(name, 0)) + n


def _hookq_save():
    try:
        tmp = HOOKQ_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(HOOKQ, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, HOOKQ_PATH)
    except Exception:
        pass


def _hookq_add(item):
    with HOOKQ_LOCK:
        while len(HOOKQ) >= 500:
            HOOKQ.pop(0)
        HOOKQ.append(item)
        _hookq_save()


def _hookq_load():
    try:
        data = json.load(open(HOOKQ_PATH, encoding="utf-8"))
        if isinstance(data, list):
            with HOOKQ_LOCK:
                HOOKQ.extend(data[:500])
    except FileNotFoundError:
        pass
    except Exception:
        pass


def hook_retrier():
    """R571: events whose 3 inline attempts failed are retried for ~7h.

    Delivery ids are stable, so the receiver can de-duplicate. The queue is
    persisted so a restart does not lose pending events.
    """
    _hookq_load()
    while True:
        try:
            now = time.time()
            due = []
            with HOOKQ_LOCK:
                for it in list(HOOKQ):
                    if float(it.get("next_ts") or 0) <= now:
                        due.append(dict(it))
            for it in due[:10]:
                res = _send_webhook(it["url"], it["payload"], it["event"], it["delivery"],
                                    it["secret"], attempts=1)
                ok = res.startswith("http 2")
                with HOOKQ_LOCK:
                    cur = next((x for x in HOOKQ if x.get("delivery") == it["delivery"]), None)
                    if cur is not None:
                        if ok:
                            HOOKQ.remove(cur)
                            _hook_stat("delivered")
                        else:
                            cur["tries"] = int(cur.get("tries") or 1) + 1
                            cur["last"] = res
                            _hook_stat("retried")
                            if cur["tries"] >= HOOK_MAX_TRIES:
                                HOOKQ.remove(cur)
                                _hook_stat("dropped")
                                _alert("webhook_dropped",
                                       "webhook %s gave up after %d tries: %s"
                                       % (cur["delivery"], cur["tries"], res), "warn", 1800)
                            else:
                                cur["next_ts"] = time.time() + HOOK_BACKOFF[
                                    min(cur["tries"] - 1, len(HOOK_BACKOFF) - 1)]
                    _hookq_save()
        except Exception:
            pass
        time.sleep(15)


def _fail(row_id, err):
    c = db()
    with _db_lock:
        c.execute("update tasks set status='failed', err=?, updated=? where id=?",
                  (str(err)[:400], time.time(), row_id))
        c.commit(); c.close()

QUERY_ERR = {}
QUERY_ERR_LOCK = threading.Lock()
AUTH_ERR = ("401", "403", "unauthorized", "forbidden", "token expired", "token invalid",
            "invalid token", "login required", "not logged in", "session expired", "auth")


def _query_err_kind(msg):
    """R576: classify poll failures so a dead account does not burn 45 minutes."""
    low = (msg or "").lower()
    if any(k in low for k in AUTH_ERR):
        return "auth"
    if any(k in low for k in TRANSIENT_ERR):
        return "transient"
    return "other"


def _query_one(row):
    if not row["email"]:
        _fail(row["id"], "poll: task has no account")
        return
    prof = A.profile_for(row["email"])
    acc = POOL.by.get(row["email"])
    if not acc:
        return
    try:
        q = A.tool_call(sess(), acc["token"], str(acc["ruid"]), prof, A.QUERY_TOOL,
                        {"task_id": row["upstream"], "model": row["model"]}, timeout=30)
    except Exception as e:
        msg = str(e)[:200]
        kind = _query_err_kind(msg)
        with QUERY_ERR_LOCK:
            n = QUERY_ERR.get(row["id"], 0) + 1
            QUERY_ERR[row["id"]] = n
            if len(QUERY_ERR) > 2000:
                for k in list(QUERY_ERR)[:500]:
                    QUERY_ERR.pop(k, None)
        # transient (timeouts/5xx) keeps polling: upstream is likely still rendering,
        # and a requeue here would pay for a second render. Unknown errors requeue at 8.
        if kind == "auth" or (kind != "transient" and n >= 8):
            if kind == "auth" and row["email"]:
                POOL.mark_dead(row["email"], "poll auth: " + msg[:80])
            if int(row["retry"] or 0) < 2:
                c = db()
                with _db_lock:
                    c.execute("update tasks set status='queued', retry=retry+1, upstream=null, email=null, "
                              "err=?, updated=? where id=?",
                              ("requeue after %s poll error: %s" % (kind, msg), time.time(), row["id"]))
                    c.commit(); c.close()
                _log_evt("poll_requeue", id=row["id"], err_kind=kind, tries=n, detail=msg[:120])
                with QUERY_ERR_LOCK:
                    QUERY_ERR.pop(row["id"], None)
                EXEC.submit(_submit, row["id"])
            else:
                _fail(row["id"], "poll %s error x%d: %s" % (kind, n, msg))
        return
    with QUERY_ERR_LOCK:
        QUERY_ERR.pop(row["id"], None)
    st = str((q or {}).get("status") or "").lower()
    url = next((q.get(k) for k in ("video_url", "url", "download_url", "file_url")
                if isinstance(q.get(k), str) and q.get(k).startswith("http")), "")
    new = None
    if st in ("succeeded", "success", "completed"):
        new = "succeeded"
    elif st in ("failed", "error", "withheld"):
        new = "failed"
    if new == "failed" and int(row["retry"] or 0) < 1:
        # one silent retry on a different account before surfacing the failure
        c = db()
        with _db_lock:
            c.execute("update tasks set status='queued', retry=retry+1, upstream=null, email=null, "
                      "err=?, updated=? where id=?",
                      ("retry after upstream failure: %s" % json.dumps(q, ensure_ascii=False)[:200],
                       time.time(), row["id"]))
            c.commit(); c.close()
        if row["email"]:
            POOL.park(row["email"], 900, "upstream failed")
        EXEC.submit(_submit, row["id"])
        return
    if url or new:
        c = db()
        with _db_lock:
            c.execute("update tasks set status=coalesce(?,status), url=coalesce(nullif(?,''),url), updated=? where id=?",
                      (new, url, time.time(), row["id"]))
            c.commit(); c.close()
        _log_evt("terminal", id=row["id"], status=new, account=row["email"],
                 upstream=row["upstream"], has_url=bool(url), error=(row["err"] or "")[:120])
        if new == "succeeded" and url and not row["local_path"]:
            EXEC.submit(_download, row["id"], url)
        if new and row["callback_url"]:
            t0 = time.time()
            while time.time() - t0 < 60:
                time.sleep(2)
                c2 = db()
                r2 = c2.execute("select local_path from tasks where id=?", (row["id"],)).fetchone()
                c2.close()
                if not r2 or r2["local_path"] or new == "failed":
                    break
            HOOK.submit(_deliver, row["id"])

_POLL_LAST = {}
_POLL_LOCK = threading.Lock()

def poller():
    """R569: rate-limited, per-task-scheduled polling.

    Before this the poller re-queried every running task each cycle through a
    16-thread pool, which serialised badly: 250 in-flight tasks meant minutes
    between two looks at the same task.  Now each task has its own due time
    (20s early, 30s mid, 60s late), at most 120 tasks are polled per cycle and
    the pool is 48 wide, so no task is starved by its neighbours.
    """
    while True:
        try:
            c = db()
            rows = c.execute("select * from tasks where status='running' and upstream is not null "
                             "limit 800").fetchall()
            c.close()
            now = time.time()
            stale = [r for r in rows if now - (r["updated"] or r["created"]) > 2700]
            for r in stale:
                _fail(r["id"], "timeout: upstream still running after 45min")
                if r["email"]:
                    POOL.park(r["email"], 600, "timeout")
            dead_ids = {x["id"] for x in stale}
            due = []
            with _POLL_LOCK:
                for r in rows:
                    if r["id"] in dead_ids:
                        continue
                    born = r["created"] or now
                    age = now - born
                    interval = 20 if age < 300 else (30 if age < 1800 else 60)
                    if now - _POLL_LAST.get(r["id"], 0) >= interval:
                        due.append(r)
                        _POLL_LAST[r["id"]] = now
                if len(_POLL_LAST) > 2000:
                    live_ids = {r["id"] for r in rows}
                    for k in [k for k in _POLL_LAST if k not in live_ids][:1500]:
                        _POLL_LAST.pop(k, None)
            futs = [POLL.submit(_query_one, r) for r in due[:120]]
            for f in futs:
                try:
                    f.result(timeout=45)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(2)

# ---------------------------------------------------------------- alerts ----
# R556: built-in watchdog. Everything lands in /opt/wzmm/alerts.log, is served at
# /admin/alerts, and is POSTed to WZMM_ALERT_WEBHOOK when configured.
ALERTS = collections.deque(maxlen=300)
_ALERT_STATE = {}
_ALERT_LOCK = threading.Lock()
ALERT_LOG = os.environ.get("WZMM_ALERT_LOG", "/opt/wzmm/alerts.log")
ALERT_WEBHOOK_FILE = "/opt/wzmm/alert_webhook.txt"
ALERT_LOG_MAX_BYTES = 2 * 1024 * 1024
ALERT_LOG_KEEP_LINES = 1000


def _alerts_load():
    """R690: rehydrate the in-memory deque from disk so a restart keeps history."""
    try:
        if not os.path.isfile(ALERT_LOG):
            return
        with open(ALERT_LOG, encoding="utf-8") as f:
            for line in f.readlines()[-300:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    ALERTS.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass


def _alerts_append(rec):
    """R690: append one jsonl record, compact in place past 2 MB."""
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if os.path.getsize(ALERT_LOG) > ALERT_LOG_MAX_BYTES:
            with open(ALERT_LOG, encoding="utf-8") as f:
                tail = f.readlines()[-ALERT_LOG_KEEP_LINES:]
            with open(ALERT_LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass


_alerts_load()


def _alert_webhook():
    """env wins; otherwise a line in /opt/wzmm/alert_webhook.txt (no restart needed)."""
    try:
        if os.path.isfile(ALERT_WEBHOOK_FILE):
            v = open(ALERT_WEBHOOK_FILE, encoding="utf-8").read().strip()
            if v:
                return v
    except Exception:
        pass
    return os.environ.get("WZMM_ALERT_WEBHOOK", "").strip()


def _post_alert(hook, rec):
    try:
        import urllib.request
        data = json.dumps(rec, ensure_ascii=False).encode()
        req = urllib.request.Request(hook, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "wzmm-watchdog/1")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def _alert(key, msg, level="warn", cooldown=1800):
    now = time.time()
    with _ALERT_LOCK:
        if cooldown and now - _ALERT_STATE.get(key, 0) < cooldown:
            return None
        _ALERT_STATE[key] = now
    rec = {"ts": int(now), "level": level, "key": key, "msg": msg}
    ALERTS.append(rec)
    _alerts_append(rec)
    hook = _alert_webhook()
    if hook:
        threading.Thread(target=_post_alert, args=(hook, rec), daemon=True).start()
    return rec


def watchdog():
    """Every 2 min: pool size / disk / stuck jobs / failure burst (de-duped)."""
    while True:
        try:
            avail = POOL.available()
            st_wd = POOL.stats()
            if avail < 30:
                _alert("pool_low", "only %d accounts available (parked=%d dead=%d)"
                       % (avail, st_wd["parked"], st_wd["dead"]), "crit", 900)
            if st_wd["trial_expiring_24h"] >= 25 and st_wd["expired_alive"] < 90:
                # R597: expired trial accounts still serve the free 1080P path
                # (verified: trial_exp=0 / credits=0 account submitted and completed 1080P).
                # Keep this visible but informational; real capacity risk is pool_low / dead.
                _alert("trial_expiry",
                       "%d trial accounts expire within 24h; expired_alive=%d "
                       "(expired accounts still serve free 1080P; watch dead/pool_low instead)"
                       % (st_wd["trial_expiring_24h"], st_wd["expired_alive"]), "info", 6 * 3600)
            try:
                free_gib = shutil.disk_usage(VIDEOS_DIR).free / (1 << 30)
                if free_gib < MIN_FREE_GB - 2:
                    _alert("disk_low", "free %.1f GiB (< %.0f GiB); auto-GC is deleting files"
                           % (free_gib, MIN_FREE_GB - 2), "crit", 900)
                elif free_gib < MIN_FREE_GB + GC_WARN_GIB:
                    # R582: warn GC_WARN_GIB before _disk_guard starts removing videos
                    _alert("disk_warn", "free %.1f GiB (< %.0f GiB); auto-GC starts at %.0f GiB"
                           % (free_gib, MIN_FREE_GB + GC_WARN_GIB, MIN_FREE_GB), "warn", 3600)
            except Exception:
                pass
            c = db()
            stuck = c.execute("select count(*) n from tasks where status='running' and updated < ?",
                              (time.time() - 2400,)).fetchone()["n"]
            bad = c.execute("select count(*) n from tasks where status='failed' and updated >= ?",
                            (time.time() - 900,)).fetchone()["n"]
            c.close()
            if stuck:
                _alert("stuck", "%d running task(s) with no update for 40min" % stuck, "warn", 1800)
            if bad > 10:
                _alert("fail_burst", "%d failures in the last 15min" % bad, "warn", 900)
            # R570: the nightly post-trial probe is our early warning that credits/1080P
            # still work after a trial ends - shout if it failed or stopped running.
            try:
                pj = json.load(open("/opt/wzmm/expiry_test.json", encoding="utf-8"))
                age = now_ts2 = time.time() - float(pj.get("ts") or 0)
                if pj.get("ok_all") is False and age < 24 * 3600:
                    _alert("expiry_probe_failed",
                           "post-trial 1080P probe failed: ok=%s/%s (%s)"
                           % (pj.get("ok"), pj.get("n"),
                              (pj.get("results") or [{}])[0].get("verdict", "?")), "crit", 6 * 3600)
                elif age > 48 * 3600:
                    _alert("expiry_probe_stale",
                           "post-trial probe has not run for %.1f h" % (age / 3600.0), "warn", 12 * 3600)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(120)


def _pinned_ids():
    """R609: ids the operator marked keep-forever. GC must skip them."""
    try:
        c = db()
        rows = c.execute("select id from tasks where coalesce(pinned,0)=1").fetchall()
        c.close()
        return {r["id"] for r in rows}
    except Exception:
        return set()


def janitor():
    while True:
        try:
            c = db()
            rows = c.execute("select id, local_path, expires_at from tasks where local_path is not null "
                             "and coalesce(pinned,0)=0").fetchall()
            c.close()
            now_j = time.time()
            cutoff = now_j - TTL_DAYS * 86400
            for r in rows:
                p = r["local_path"]
                if not p or not os.path.isfile(p):
                    continue
                due = bool(r["expires_at"]) and now_j >= float(r["expires_at"])
                if due or (not r["expires_at"] and os.path.getmtime(p) < cutoff):
                    try:
                        os.remove(p)
                    except Exception:
                        continue
                    c = db()
                    with _db_lock:
                        c.execute("update tasks set local_path=null where id=?", (r["id"],))
                        c.commit(); c.close()
            _disk_guard()
        except Exception:
            pass
        time.sleep(1800)

TRANSIENT_ERR = ("temporarily unavailable", "try again", "timeout", "timed out",
                 "connection reset", "connection aborted", "upstream busy", " 502", " 503", " 504",
                 '"code": 502', '"code": 503', '"code": 504')


def retrier():
    """Requeue tasks that failed for transient upstream reasons (max 3 extra tries)."""
    while True:
        try:
            c = db()
            rows = c.execute("select id, err, retry from tasks where status='failed' and retry<3").fetchall()
            c.close()
            for r in rows:
                e = (r["err"] or "").lower()
                if not any(m in e for m in TRANSIENT_ERR):
                    continue
                c = db()
                with _db_lock:
                    c.execute("update tasks set status='queued', retry=retry+1, err=null, "
                              "email=null, upstream=null, updated=? where id=?", (time.time(), r["id"]))
                    c.commit(); c.close()
                _log_evt("requeue", id=r["id"], retry=int(r["retry"] or 0) + 1, err=e[:120])
                EXEC.submit(_submit, r["id"])
        except Exception:
            pass
        time.sleep(120)


def _esc(x) -> str:
    return (str(x if x is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _err_type(status: int) -> str:
    if status in (400, 405, 422):
        return "invalid_request_error"
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 404:
        return "not_found_error"
    if status == 402:
        return "insufficient_quota"
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "server_error"
    return "api_error"


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    rid = getattr(request.state, "rid", "")
    body = {"detail": exc.detail,
            "error": {"message": str(exc.detail), "type": _err_type(exc.status_code),
                      "code": exc.status_code, "request_id": rid}}
    headers = dict(getattr(exc, "headers", None) or {})
    if rid:
        headers["X-Request-Id"] = rid
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


def _envelope(detail, status: int, rid: str, headers=None):
    if isinstance(detail, str):
        msg = detail
    elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
        e0 = detail[0]
        loc = ".".join(str(x) for x in (e0.get("loc") or []) if x != "body")
        msg = ("%s: %s" % (loc, e0.get("msg"))) if loc else str(e0.get("msg") or "validation failed")
    else:
        msg = json.dumps(detail, ensure_ascii=False)[:600]
    body = {"detail": detail,
            "error": {"message": msg, "type": _err_type(status),
                      "code": status, "request_id": rid}}
    h = dict(headers or {})
    if rid:
        h["X-Request-Id"] = rid
    return JSONResponse(body, status_code=status, headers=h)


@app.exception_handler(StarletteHTTPException)
async def _starlette_error(request: Request, exc: StarletteHTTPException):
    """R695: unmatched routes / wrong method raise Starlette's HTTPException.

    FastAPI only auto-wires its own subclass, so without this the router fell back
    to the bare {"detail": "Not Found"} body with no envelope and no request_id.
    """
    rid = getattr(request.state, "rid", "")
    return _envelope(exc.detail, exc.status_code, rid, getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """R695: 422 keeps the classic detail list but gains the envelope + request_id."""
    rid = getattr(request.state, "rid", "")
    errs = exc.errors() or []
    if errs:
        e0 = errs[0]
        loc = ".".join(str(x) for x in (e0.get("loc") or []))
        msg = "%s: %s" % (loc, e0.get("msg")) if loc else str(e0.get("msg"))
    else:
        msg = "request validation failed"
    try:
        detail = json.loads(json.dumps(exc.errors(), default=str))
    except Exception:
        detail = msg
    return _envelope(detail, 422, rid)


@app.middleware("http")
async def _rl_headers(request: Request, call_next):
    rid = request.headers.get("x-request-id") or ("req_" + secrets.token_hex(6))
    request.state.rid = rid
    resp = await call_next(request)
    resp.headers["X-Request-Id"] = rid
    info = getattr(request.state, "rl", None)
    if info:
        resp.headers["X-RateLimit-Limit"] = str(info[0])
        resp.headers["X-RateLimit-Remaining"] = str(info[1])
    return resp


@app.on_event("startup")
def _startup():
    init_db()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    threading.Thread(target=retrier, daemon=True).start()
    threading.Thread(target=hook_retrier, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    # tasks left queued across a restart would otherwise wait forever
    try:
        c = db()
        rows = c.execute("select id from tasks where status='queued'").fetchall()
        c.close()
        for r in rows:
            EXEC.submit(_submit, r["id"])
    except Exception:
        pass

def _canary_state():
    """Last line of canary.log + its age, so /healthz exposes end-to-end proof."""
    cp = "/opt/wzmm/canary.log"
    try:
        with open(cp, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            return None
        last = lines[-1]
        return {"ok": " canary OK " in last, "last": last[-220:],
                "age_h": round((time.time() - os.path.getmtime(cp)) / 3600, 1)}
    except Exception:
        return None


# ------------------------------------------------------------------ api ----
@app.get("/healthz")
def healthz():
    c = db()
    n = c.execute("select count(*) c from tasks where status in ('queued','running')").fetchone()["c"]
    running = c.execute("select count(*) c from tasks where status='running'").fetchone()["c"]
    k = c.execute("select count(*) c from keys where disabled=0").fetchone()["c"]
    c.close()
    st = POOL.stats()
    return {"ok": True, "accounts": len(POOL.accs), "dead": len(POOL.dead),
            "only_1080p": ONLY_1080P,
            "parked": st["parked"], "trial_alive": st["trial_alive"],
            "expired_alive": st["expired_alive"],
            "points_total": st["points_total"], "signin_done_today": st["signin_done_today"],
            "inflight": n, "running": running,
            "queued": n - running, "max_inflight": MAX_INFLIGHT,
            "available": POOL.available(), "eff_max_inflight": _eff_cap(),
            "keys": k, "ts": int(time.time()),
            "disk_free_gib": round(shutil.disk_usage(VIDEOS_DIR).free / (1 << 30), 1),
            "min_accept_gib": MIN_ACCEPT_GB,
            "disk_soft_gib": DISK_SOFT_GB, "disk_mode": _disk_mode(),
            "min_free_gib": MIN_FREE_GB, "gc_min_age_h": GC_MIN_AGE_H,
            "gc_keep_newest": GC_KEEP_NEWEST,
            "ttl_days": TTL_DAYS, "canary": _canary_state()}


@app.get("/admin/alerts")
def admin_alerts(request: Request, limit: int = 50):
    require_admin(request)
    lim = max(1, min(int(limit), 300))
    return {"object": "list", "count": len(ALERTS), "webhook": bool(_alert_webhook()),
            "data": list(ALERTS)[-lim:]}


@app.post("/admin/alert-test")
def admin_alert_test(request: Request):
    require_admin(request)
    rec = _alert("test", "watchdog self-test", "info", 0)
    return {"ok": True, "fired": rec, "webhook": bool(_alert_webhook())}


@app.post("/admin/alert")
async def admin_alert(request: Request):
    """R605: let external checks (verify_all.sh / cron) raise a real alert.

    body: {"key":"verify","msg":"...","level":"warn|crit|info","dedupe":900}
    """
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    rec = _alert(str(body.get("key") or "manual")[:40],
                 str(body.get("msg") or "manual alert")[:400],
                 str(body.get("level") or "info")[:8],
                 int(body.get("dedupe") or 0))
    return {"ok": True, "fired": rec, "webhook": bool(_alert_webhook())}


# ---------- R606: on-demand verification runner ----------
VERIFY_LOCK = threading.Lock()
VERIFY_STATE = {"run_id": None, "state": "idle", "rc": None, "started": None,
                "finished": None, "duration_s": None, "summary": None, "lines": [],
                "fail_total": 0}


def _verify_worker(run_id):
    t0 = time.time()
    try:
        p = subprocess.run(["bash", "/opt/wzmm/verify_all.sh"], capture_output=True,
                           text=True, timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except Exception as e:
        out = "verify_all failed to run: %r" % (e,)
        rc = 99
    lines = [x for x in out.splitlines() if x.strip()]
    fails = [x for x in lines if "[FAIL]" in x]
    summary = next((x for x in reversed(lines) if x.startswith("SUMMARY:")), "")
    with VERIFY_LOCK:
        VERIFY_STATE.update({"state": "done" if rc == 0 else "failed", "rc": rc,
                             "finished": time.time(),
                             "duration_s": round(time.time() - t0, 1),
                             "summary": summary, "lines": lines[-80:],
                             "fail_total": len(fails), "source": "api"})
    if rc != 0:
        _alert("verify", "on-demand verify rc=%s %s" % (rc, "; ".join(fails[:3]) or summary),
               "crit", 900)


@app.post("/admin/verify")
def admin_verify_start(request: Request):
    """R606: run verify_all.sh in the background; poll GET /admin/verify."""
    require_admin(request)
    with VERIFY_LOCK:
        if VERIFY_STATE.get("state") == "running":
            return {"ok": True, "started": False, "state": "running",
                    "run_id": VERIFY_STATE.get("run_id"),
                    "started_at": VERIFY_STATE.get("started")}
        run_id = "vfy_" + secrets.token_hex(4)
        VERIFY_STATE.update({"run_id": run_id, "state": "running", "rc": None,
                             "started": time.time(), "finished": None,
                             "duration_s": None, "summary": None, "lines": [],
                             "fail_total": 0})
    threading.Thread(target=_verify_worker, args=(run_id,), daemon=True).start()
    return JSONResponse({"ok": True, "started": True, "state": "running", "run_id": run_id,
                         "poll": "GET /admin/verify"}, status_code=202)


@app.get("/admin/verify")
def admin_verify_state(request: Request):
    """R606: latest on-demand verify result (no side effects)."""
    require_admin(request)
    with VERIFY_LOCK:
        s = dict(VERIFY_STATE)
    if s.get("started"):
        end = s.get("finished") or time.time()
        s["age_s"] = round(time.time() - s["started"], 1)
        s["elapsed_s"] = round(end - s["started"], 1)
    return {"ok": True, "data": s}


@app.post("/admin/verify/report")
async def admin_verify_report(request: Request):
    """R606: let verify_wrapper.sh publish hourly results into /metrics."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    rc = int(body.get("rc") or 0)
    fin = time.time()
    try:
        dur = float(body.get("duration_s") or 0)
    except Exception:
        dur = 0.0
    with VERIFY_LOCK:
        VERIFY_STATE.update({"run_id": str(body.get("run_id") or "timer"),
                             "state": "done" if rc == 0 else "failed", "rc": rc,
                             "started": (fin - dur) if dur > 0 else fin,
                             "finished": fin,
                             "duration_s": round(dur, 1),
                             "summary": str(body.get("summary") or "")[:200],
                             "lines": [str(x)[:300] for x in (body.get("lines") or [])][-40:],
                             "fail_total": int(body.get("fail_total") or 0),
                             "source": str(body.get("source") or "report")[:20]})
        st = dict(VERIFY_STATE)
    return {"ok": True, "state": st["state"], "rc": rc, "source": st["source"]}

@app.get("/admin/health")
def admin_health(request: Request):
    """One-screen production health: queue, pool availability, 1h/24h outcomes, disk."""
    require_admin(request)
    now = time.time()
    c = db()
    q = {r["status"]: r["n"] for r in c.execute(
        "select status,count(*) n from tasks group by status")}
    win = {}
    for label, secs in (("1h", 3600), ("24h", 86400)):
        rows = c.execute("select status,count(*) n from tasks where created>=? group by status",
                         (now - secs,)).fetchall()
        win[label] = {r["status"]: r["n"] for r in rows}
    last_err = c.execute("select id,email,err,updated from tasks where status='failed' "
                         "order by updated desc limit 3").fetchall()
    c.close()
    st = POOL.stats()
    try:
        du = shutil.disk_usage(VIDEOS_DIR)
        disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                "used_pct": round(100.0 * du.used / du.total, 1),
                "free_gib": round(du.free / (1 << 30), 1),
                "min_free_gib": MIN_FREE_GB, "ttl_days": TTL_DAYS,
                "auto_gc_below_gib": MIN_FREE_GB,
                "gc_armed": du.free / (1 << 30) <= MIN_FREE_GB + 2}
    except Exception:
        disk = {}
    succ_1h = win["1h"].get("succeeded", 0)
    fail_1h = win["1h"].get("failed", 0)
    tot_1h = succ_1h + fail_1h
    return {
        "ok": POOL.available() > 0,
        "only_1080p": ONLY_1080P,
        "queue": {"inflight": q.get("queued", 0) + q.get("running", 0),
                  "running": q.get("running", 0), "queued": q.get("queued", 0)},
        "capacity": {"max_inflight": MAX_INFLIGHT, "eff_max_inflight": _eff_cap(),
                     "available_accounts": POOL.available(),
                     "parked": st["parked"], "dead": st["dead"],
                     "trial_alive": st["trial_alive"], "expired_alive": st["expired_alive"],
                     "expired_daily_room": st["expired_capacity"],
                     "daily_room": st["daily_room"],
                     "trial_expiring_24h": st["trial_expiring_24h"],
                     "trial_expiring_7d": st["trial_expiring_7d"]},
        "outcomes": {"all": q, "last_1h": win["1h"], "last_24h": win["24h"],
                     "success_rate_1h": (round(100.0 * succ_1h / tot_1h, 1) if tot_1h else None)},
        "bad503_tracked": len(_BAD503),
        "alerts": {"count": len(ALERTS), "webhook": bool(_alert_webhook()),
                   "last": (list(ALERTS)[-1] if ALERTS else None)},
        "last_errors": [{"id": r["id"], "email": r["email"], "err": (r["err"] or "")[:160],
                         "at": int(r["updated"] or 0)} for r in last_err],
        "disk": disk,
        "points_total": st["points_total"], "signin_done_today": st["signin_done_today"],
        "ts": int(now),
    }

@app.get("/v1/models")
def models(request: Request):
    # R601: read-only metadata does not consume rpm (polling must stay cheap)
    # R602: admin token also accepted (ops/monitoring convenience)
    if not is_admin(request):
        require_key_light(request)
    data = []
    for mid, spec in MODEL_SPEC.items():
        res = list(spec.get("res") or [])
        supports_1080 = "1080P" in res
        data.append({
            "id": mid, "object": "model", "owned_by": "minimax",
            "resolutions": res,
            "durations": list(spec.get("dur") or []),
            "ratios": ["16:9", "9:16", "1:1"],
            "default_resolution": "1080P" if supports_1080 else (res[-1] if res else None),
            "supports_1080p": supports_1080,
            "available": (not ONLY_1080P) or supports_1080,
            "note": spec.get("note"),
        })
    return {"object": "list", "only_1080p": ONLY_1080P, "data": data}

def _create_task(key_id, body, batch_id=None, idem_key=None, check_daily=True):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    callback = (body.get("callback_url") or "").strip()
    if callback and not (callback.startswith("http://") or callback.startswith("https://")):
        raise HTTPException(400, "callback_url must be http(s)")
    # R631: optional per-task retention so high-volume batches can free disk early
    # without touching the global TTL or anyone else's clips.
    rh = body.get("retention_hours")
    if rh in ("", None):
        rh = None
    else:
        try:
            rh = float(rh)
        except Exception:
            raise HTTPException(400, "retention_hours must be a number between 1 and 720")
        if not (1 <= rh <= 720):
            raise HTTPException(400, "retention_hours must be between 1 and 720")
    model = body.get("model") or DEFAULT_MODEL
    res = _norm_res(body.get("resolution") or body.get("size"))
    _gate_h3_credits(model)
    if ONLY_1080P and res != "1080P":
        raise HTTPException(400, "only 1080P is enabled (got %s); set WZMM_ONLY_1080P=0 to allow other resolutions" % res)
    try:
        dur = int(body.get("duration") or 6)
    except Exception:
        dur = 6
    ratio = body.get("ratio") or body.get("aspect_ratio") or "16:9"
    spec = MODEL_SPEC.get(model)
    if not spec:
        raise HTTPException(400, "unsupported model %r; use one of %s" % (model, ", ".join(MODEL_SPEC)))
    if res not in spec["res"]:
        raise HTTPException(400, "model %s supports resolution %s (got %s)"
                            % (model, "/".join(spec["res"]), res))
    if dur not in spec["dur"]:
        raise HTTPException(400, "model %s supports duration %s seconds (got %s)"
                            % (model, spec["dur_label"], dur))
    dur_res = spec.get("dur_res")
    if dur_res and res not in dur_res.get(dur, spec["res"]):
        raise HTTPException(400, "model %s at %ss supports resolution %s (got %s)"
                            % (model, dur, "/".join(dur_res.get(dur, ())), res))
    tid = "vid_" + secrets.token_hex(12)
    now = time.time()
    expires_at = (now + rh * 3600.0) if rh else None
    c = db()
    krow = c.execute("select * from keys where id=?", (key_id,)).fetchone()
    if krow is None:
        c.close()
        raise HTTPException(401, "invalid api key")
    quota = int(krow["quota"] or 0)
    if quota and int(krow["used"] or 0) >= quota:
        c.close()
        raise HTTPException(402, "quota exhausted for this key (%d tasks)" % quota)
    c.close()
    if check_daily:
        _enforce_daily_room(krow, 1)
    c = db()
    with _db_lock:
        c.execute("update keys set used=used+1 where id=?", (key_id,))
        c.execute("""insert into tasks(id,model,resolution,duration,ratio,prompt,status,created,updated,
                                    key_id,batch_id,callback_url,idem_key,expires_at)
                     values(?,?,?,?,?,?, 'queued', ?,?,?,?,?,?,?)""",
                  (tid, model, res, dur, ratio, prompt, now, now, key_id, batch_id,
                   (callback or None), (idem_key or None), expires_at))
        c.commit(); c.close()
    EXEC.submit(_submit, tid)
    out = {"id": tid, "object": "video.generation", "created": int(now), "status": "queued",
           "model": model, "resolution": res, "duration": dur, "ratio": ratio}
    if expires_at:
        out["retention_hours"] = rh
        out["expires_at"] = int(expires_at)
    return out

def _wait_task(tid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        c = db()
        row = c.execute("select * from tasks where id=?", (tid,)).fetchone()
        c.close()
        if row and row["status"] in ("succeeded", "failed"):
            if row["status"] == "succeeded":
                # give the downloader a moment so callers get bytes/content_url in one shot
                for _ in range(10):
                    if row["local_path"] and os.path.isfile(row["local_path"]):
                        break
                    time.sleep(2)
                    c2 = db()
                    row = c2.execute("select * from tasks where id=?", (tid,)).fetchone()
                    c2.close()
            return row
        time.sleep(3)
    return None

@app.post("/v1/videos/generations")
async def create_video(request: Request):
    key = require_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid json")
    idem = (request.headers.get("x-idempotency-key") or body.get("idempotency_key") or "").strip()[:120]
    if idem:
        c = db()
        prev = c.execute("select id,status,url,local_path,err,model,resolution,duration,ratio,created "
                         "from tasks where key_id=? and idem_key=? order by created limit 1",
                         (key["id"], idem)).fetchone()
        c.close()
        if prev:
            out = {"id": prev["id"], "object": "video.generation", "created": int(prev["created"]),
                   "status": prev["status"], "model": prev["model"], "resolution": prev["resolution"],
                   "duration": prev["duration"], "ratio": prev["ratio"], "idempotent_replay": True}
            if prev["local_path"] and os.path.isfile(prev["local_path"]):
                out["local"] = True
                out["bytes"] = os.path.getsize(prev["local_path"])
            if prev["url"]:
                out["url"] = prev["url"]
                out["content_url"] = str(request.base_url).rstrip("/") + "/v1/videos/%s/content" % prev["id"]
            if prev["err"]:
                out["error"] = prev["err"]
            return out
    if (body.get("dry_run") or request.query_params.get("dry_run") in ("1", "true", "yes")):
        _res, _dur = _check_spec(body)
        _m = body.get("model") or DEFAULT_MODEL
        _cost = PRICES.get(_m, {}).get((_res, _dur), 0)
        return {"object": "video.generation.preview", "dry_run": True,
                "model": _m, "resolution": _res, "duration": _dur,
                "ratio": body.get("ratio") or "16:9",
                "credit_cost": _cost, "queued": False, "charged": False,
                "note": "dry_run: nothing queued, nothing consumed"}
    check_concurrency(key)
    _disk_admit()
    out = _create_task(key["id"], body, idem_key=idem)
    if body.get("wait") or request.query_params.get("wait") in ("1", "true", "yes"):
        try:
            timeout = int(body.get("wait_timeout") or request.query_params.get("wait_timeout") or 600)
        except Exception:
            timeout = 600
        row = _wait_task(out["id"], max(30, min(timeout, 1800)))
        if row is None:
            out["status"] = "running"
            out["note"] = "still running; poll /v1/videos/generations/%s" % out["id"]
            return out
        out["status"] = row["status"]
        if row["local_path"] and os.path.isfile(row["local_path"]):
            out["local"] = True
            out["bytes"] = os.path.getsize(row["local_path"])
        if row["url"]:
            out["url"] = row["url"]
            out["content_url"] = str(request.base_url).rstrip("/") + "/v1/videos/%s/content" % out["id"]
        if row["err"]:
            out["error"] = row["err"]
    return out

_H3_CRED = {"ts": 0.0, "v": 0}


def _pool_credits_cached(ttl=5.0):
    now = time.time()
    if now - _H3_CRED["ts"] < ttl:
        return _H3_CRED["v"]
    try:
        v = int(POOL.stats().get("credits_total") or 0)
    except Exception:
        v = 0
    _H3_CRED.update(ts=now, v=v)
    return v


def _gate_h3_credits(model):
    """R705: MiniMax-H3/H3-Max bill account credits only; Token Plan is rejected upstream.

    With credits_total == 0 every H3 submit is a guaranteed 402, so refuse it
    locally instead of burning a task and a scheduler slot.
    """
    if not str(model or "").startswith("MiniMax-H3"):
        return
    if os.environ.get("WZMM_H3_REQUIRE_CREDITS", "1") == "0":
        return
    if _pool_credits_cached() > 0:
        return
    raise HTTPException(400, "model %s bills account credits only (Token Plan is not accepted) "
                            "and the pool has 0 credits; use MiniMax-Hailuo-2.3 for the free 1080P path" % model)


def _check_spec(base):
    """Validate one job spec exactly like _create_task does, without side effects."""
    spec = MODEL_SPEC.get(base.get("model") or DEFAULT_MODEL)
    if not spec:
        raise HTTPException(400, "unsupported model %r; use one of %s"
                            % (base.get("model"), ", ".join(MODEL_SPEC)))
    if not (base.get("prompt") or "").strip():
        raise HTTPException(400, "prompt required")
    res = _norm_res(base.get("resolution") or base.get("size"))
    _gate_h3_credits(base.get("model") or DEFAULT_MODEL)
    if ONLY_1080P and res != "1080P":
        raise HTTPException(400, "only 1080P is enabled (got %s); set WZMM_ONLY_1080P=0 to allow other resolutions" % res)
    try:
        dur = int(base.get("duration") or 6)
    except Exception:
        dur = 6
    if res not in spec["res"]:
        raise HTTPException(400, "model %s supports resolution %s (got %s)"
                            % (base.get("model") or DEFAULT_MODEL, "/".join(spec["res"]), res))
    if dur not in spec["dur"]:
        raise HTTPException(400, "model %s supports duration %s seconds (got %s)"
                            % (base.get("model") or DEFAULT_MODEL, spec["dur_label"], dur))
    dur_res = spec.get("dur_res")
    if dur_res and res not in dur_res.get(dur, spec["res"]):
        raise HTTPException(400, "model %s at %ss supports resolution %s (got %s)"
                            % (base.get("model") or DEFAULT_MODEL, dur, "/".join(dur_res.get(dur, ())), res))
    return res, dur

@app.post("/v1/videos/batch")
async def create_batch(request: Request):
    key = require_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid json")
    check_concurrency(key)
    items = body.get("items") or body.get("prompts") or []
    if isinstance(items, str):
        items = [{"prompt": items}]
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items required: [{prompt,resolution,duration,ratio}]")
    if len(items) > 64:
        raise HTTPException(400, "max 64 items per batch")
    dry = bool(body.get("dry_run"))
    if not dry:
        _disk_admit()
        allow_partial = bool(body.get("allow_partial"))
        n_ok = _enforce_daily_room(key, len(items), allow_partial)
        items = items[:_enforce_quota_room(key, n_ok, allow_partial)]
    top = {k: body[k] for k in ("model", "resolution", "size", "duration", "ratio", "aspect_ratio")
           if k in body}
    batch_id = ("dry_" if dry else "bat_") + secrets.token_hex(8)
    out, errs = [], []
    for idx, it in enumerate(items):
        if isinstance(it, str):
            it = {"prompt": it}
        base = dict(top)
        base.update(body.get("defaults") or {})
        base.update(it or {})
        try:
            if dry:
                _check_spec(base)      # validate only: no DB row, no upstream call
                out.append({"index": idx, "ok": True, "prompt": str(base.get("prompt"))[:80],
                            "resolution": _norm_res(base.get("resolution") or base.get("size")),
                            "duration": int(base.get("duration") or 6)})
            else:
                out.append(_create_task(key["id"], base, batch_id, check_daily=False))
        except HTTPException as e:
            errs.append({"index": idx, "error": e.detail,
                         "prompt": str((it or {}).get("prompt", ""))[:80]})
    if dry:
        return {"object": "list", "batch_id": batch_id, "dry_run": True, "total": len(items),
                "accepted": len(out), "rejected": len(errs), "data": out, "errors": errs,
                "note": "dry_run: nothing was queued"}
    res = {"object": "list", "batch_id": batch_id, "dry_run": False, "total": len(items),
           "accepted": len(out), "rejected": len(errs), "data": out}
    if errs:
        res["errors"] = errs
    return res

@app.post("/v1/videos/bulk")
async def create_bulk(request: Request):
    """Large job intake: up to 2000 prompts in one call, split into <=64-item chunks.

    Same per-item semantics as /v1/videos/batch; all items share one batch_id so
    GET /v1/videos/batch/{id} reports the whole job.
    """
    key = require_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid json")
    check_concurrency(key)
    items = body.get("items") or body.get("prompts") or []
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items required: [{prompt,resolution,duration,ratio}]")
    if len(items) > 2000:
        raise HTTPException(400, "max 2000 items per bulk request (got %d)" % len(items))
    defaults = body.get("defaults") or {}
    dry = bool(body.get("dry_run"))
    if not dry:
        _disk_admit()
        allow_partial = bool(body.get("allow_partial"))
        n_ok = _enforce_daily_room(key, len(items), allow_partial)
        items = items[:_enforce_quota_room(key, n_ok, allow_partial)]
    batch_id = ("dry_" if dry else "bat_") + secrets.token_hex(8)
    ids, errs = [], []
    for idx, it in enumerate(items):
        if isinstance(it, str):
            it = {"prompt": it}
        base = dict(defaults)
        if isinstance(it, dict):
            base.update(it)
        try:
            if dry:
                # validate only: same checks as _create_task, no DB row, no upstream call
                spec = MODEL_SPEC.get(base.get("model") or DEFAULT_MODEL)
                if not spec:
                    raise HTTPException(400, "unsupported model %r" % base.get("model"))
                res = _norm_res(base.get("resolution") or base.get("size"))
                if ONLY_1080P and res != "1080P":
                    raise HTTPException(400, "only 1080P is enabled (got %s)" % res)
                dur = int(base.get("duration") or 6)
                if res not in spec["res"]:
                    raise HTTPException(400, "model supports %s" % "/".join(spec["res"]))
                if dur not in spec["dur"]:
                    raise HTTPException(400, "model supports %s" % spec["dur_label"])
                if not (base.get("prompt") or "").strip():
                    raise HTTPException(400, "prompt required")
                ids.append("dry_%04d" % idx)
            else:
                ids.append(_create_task(key["id"], base, batch_id, check_daily=False)["id"])
        except HTTPException as e:
            errs.append({"index": idx, "error": e.detail,
                         "prompt": str((it or {}).get("prompt", ""))[:80]})
    try:
        chunk = max(1, min(int(body.get("chunk") or 64), 64))
    except Exception:
        chunk = 64
    chunks = [ids[i:i + chunk] for i in range(0, len(ids), chunk)]
    out = {"object": "list", "batch_id": batch_id, "dry_run": dry, "total": len(items),
           "accepted": len(ids), "rejected": len(errs), "chunks": len(chunks),
           "chunk_size": chunk, "data": ids[:200], "errors": errs[:50]}
    if dry:
        cost = PRICES.get(defaults.get("model") or DEFAULT_MODEL, {}).get(
            (_norm_res(defaults.get("resolution") or "1080P"), int(defaults.get("duration") or 6)))
        out["estimated_credits"] = (cost * len(ids)) if cost else None
        out["note"] = "dry_run: nothing was queued"
    else:
        out["poll"] = "/v1/videos/batch/%s" % batch_id
        out["cancel"] = "/v1/videos/batch/%s/cancel" % batch_id
    return out

@app.post("/v1/videos/{tid}/cancel")
def cancel_task(tid: str, request: Request):
    key = None if is_admin(request) else require_key(request)
    c = db()
    row = c.execute("select id,status,key_id from tasks where id=?", (tid,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        c.close(); raise HTTPException(403, "task belongs to another key")
    with _db_lock:
        c.execute("update tasks set status='canceled', updated=? where id=? and status='queued'",
                  (time.time(), tid))
        c.commit()
    cur = c.execute("select status from tasks where id=?", (tid,)).fetchone()["status"]
    c.close()
    return {"id": tid, "status": cur, "canceled": cur == "canceled"}

@app.post("/v1/videos/batch/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: Request):
    key = None if is_admin(request) else require_key(request)
    c = db()
    rows = c.execute("select id,status,key_id from tasks where batch_id=?", (batch_id,)).fetchall()
    if not rows:
        c.close(); raise HTTPException(404, "batch not found")
    if key is not None and any(r["key_id"] != key["id"] for r in rows):
        c.close(); raise HTTPException(403, "batch belongs to another key")
    now = time.time()
    with _db_lock:
        c.execute("update tasks set status='canceled', updated=? where batch_id=? and status='queued'",
                  (now, batch_id))
        c.commit()
    after = c.execute("select status,count(*) n from tasks where batch_id=? group by status",
                      (batch_id,)).fetchall()
    c.close()
    counts = {r["status"]: r["n"] for r in after}
    return {"batch_id": batch_id, "canceled": counts.get("canceled", 0),
            "still_running": counts.get("running", 0), "summary": counts}

@app.get("/v1/pricing")
def pricing(request: Request, model: str = "MiniMax-Hailuo-2.3", resolution: str = "1080P",
            duration: int = 6):
    # R601: read-only metadata does not consume rpm
    # R602: admin token also accepted
    if not is_admin(request):
        require_key_light(request)
    spec = MODEL_SPEC.get(model)
    if not spec:
        raise HTTPException(400, "unsupported model %r; use one of %s" % (model, ", ".join(MODEL_SPEC)))
    res = _norm_res(resolution)
    if ONLY_1080P and res != "1080P":
        raise HTTPException(400, "only 1080P is enabled (got %s)" % res)
    if res not in spec["res"]:
        raise HTTPException(400, "model %s supports resolution %s (got %s)"
                            % (model, "/".join(spec["res"]), res))
    if int(duration) not in spec["dur"]:
        raise HTTPException(400, "model %s supports duration %s seconds (got %s)"
                            % (model, spec.get("dur_label"), duration))
    dur_res = spec.get("dur_res")
    if dur_res and res not in dur_res.get(int(duration), spec["res"]):
        raise HTTPException(400, "model %s at %ss supports resolution %s (got %s)"
                            % (model, duration, "/".join(dur_res.get(int(duration), ())), res))
    table = PRICES.get(model) or {}
    cost = table.get((res, int(duration)))
    return {"model": model, "resolution": res, "duration": int(duration), "credits": cost,
            "note": spec.get("note"), "supported": {"resolutions": list(spec.get("res") or []),
                                                    "durations": list(spec.get("dur") or [])},
            "known_prices": {"%s/%ss" % (r, d): c for (r, d), c in table.items()},
            "hint": "credits \u4e3a null \u8868\u793a\u8be5\u7ec4\u5408\u672a\u5b9e\u6d4b\u4ef7\u76ee\uff1bHailuo-2.3 \u51fa\u7247\u5f53\u524d\u4e0d\u6263\u4f59\u989d"}

@app.get("/v1/videos/batch/{batch_id}")
def get_batch(batch_id: str, request: Request):
    # R601: polling a batch is read-only and must not burn rpm
    key = require_key_light(request)
    c = db()
    rows = c.execute("select id,status,model,resolution,duration,ratio,prompt,created,url,local_path,err "
                     "from tasks where batch_id=? and key_id=? order by created", (batch_id, key["id"])).fetchall()
    c.close()
    if not rows:
        raise HTTPException(404, "batch not found")
    data = []
    for r in rows:
        d = dict(r)
        d.pop("local_path", None)
        data.append(d)
    summary = {st: sum(1 for r in rows if r["status"] == st) for st in set(x["status"] for x in rows)}
    done = summary.get("succeeded", 0) + summary.get("failed", 0) + summary.get("canceled", 0)
    total = len(rows)
    t0 = min(r["created"] for r in rows)
    elapsed = max(0.0, time.time() - t0)
    rate = (done / elapsed * 3600.0) if elapsed > 5 and done else None
    eta = None
    if rate and total > done:
        eta = int((total - done) / rate * 3600.0)
    return {"object": "list", "batch_id": batch_id, "summary": summary,
            "progress": {"total": total, "done": done, "elapsed_s": int(elapsed),
                         "rate_per_hour": round(rate, 1) if rate else None, "eta_s": eta},
            "data": data}

@app.get("/v1/videos/{tid}/status")
def video_status(tid: str, request: Request):
    return get_video(tid, request)

@app.get("/v1/videos/generations/{tid}")
def get_video(tid: str, request: Request):
    # R601: /status + /generations/{id} are polling endpoints; exempt from rpm
    key = None if viewer_ok(request) else require_key_light(request)
    c = db()
    row = c.execute("select * from tasks where id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        raise HTTPException(403, "task belongs to another key")
    out = {"id": row["id"], "object": "video.generation", "created": int(row["created"]),
           "status": row["status"], "model": row["model"], "resolution": row["resolution"],
           "duration": row["duration"], "ratio": row["ratio"],
           "upstream_task": row["upstream"], "retry": int(row["retry"] or 0),
           "pinned": bool(row["pinned"])}
    if row["expires_at"]:
        out["expires_at"] = int(row["expires_at"])
    if row["local_path"] and os.path.isfile(row["local_path"]):
        out["local"] = True
        out["bytes"] = os.path.getsize(row["local_path"])
    if row["url"]:
        out["url"] = row["url"]
        out["content_url"] = str(request.base_url).rstrip("/") + "/v1/videos/%s/content" % tid
    if row["err"]:
        out["error"] = row["err"]
    return out

def _mp4_meta(path: str):
    """ffprobe once, then cache next to the file (JSON sidecar)."""
    cache = path + ".meta.json"
    try:
        if os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    out = {"bytes": os.path.getsize(path)}
    try:
        import subprocess
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,duration,codec_name,nb_frames,bit_rate",
                            "-of", "json", path], capture_output=True, text=True, timeout=40)
        st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        for k in ("width", "height", "duration", "codec_name", "nb_frames", "bit_rate"):
            if k in st:
                out[k] = st[k]
        try:
            out["width_i"], out["height_i"] = int(st.get("width") or 0), int(st.get("height") or 0)
            out["resolution"] = "%dx%d" % (out["width_i"], out["height_i"])
            out["is_1080p"] = out["width_i"] >= 1920 and out["height_i"] >= 1080
            out["duration_s"] = round(float(st.get("duration") or 0), 3)
        except Exception:
            pass
    except Exception as e:
        out["probe_error"] = repr(e)[:120]
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except Exception:
        pass
    return out


def _thumb_for(path: str, at: float = 1.0):
    """640px-wide JPEG frame, cached next to the mp4."""
    cache = path + ".thumb.jpg"
    try:
        if os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
            return cache
    except Exception:
        pass
    import subprocess
    r = subprocess.run(["ffmpeg", "-y", "-nostdin", "-v", "error", "-ss", str(at), "-i", path,
                        "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", cache],
                       capture_output=True, text=True, timeout=60)
    if os.path.isfile(cache) and os.path.getsize(cache) > 0:
        return cache
    raise HTTPException(500, "thumbnail failed: %s" % (r.stderr or "")[:200])


@app.post("/v1/videos/{tid}/pin")
def pin_video(tid: str, request: Request):
    """R609: keep this clip forever; janitor and disk GC skip pinned tasks."""
    key = None if is_admin(request) else require_key(request)
    c = db()
    row = c.execute("select id,key_id from tasks where id=?", (tid,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        c.close(); raise HTTPException(403, "task belongs to another key")
    with _db_lock:
        c.execute("update tasks set pinned=1 where id=?", (tid,))
        c.commit()
    c.close()
    _RET_CACHE.update(ts=0.0, data=None)
    return {"id": tid, "pinned": True}


@app.delete("/v1/videos/{tid}/pin")
def unpin_video(tid: str, request: Request):
    """R609: allow TTL/GC to reclaim this clip again."""
    key = None if is_admin(request) else require_key(request)
    c = db()
    row = c.execute("select id,key_id from tasks where id=?", (tid,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        c.close(); raise HTTPException(403, "task belongs to another key")
    with _db_lock:
        c.execute("update tasks set pinned=0 where id=?", (tid,))
        c.commit()
    c.close()
    _RET_CACHE.update(ts=0.0, data=None)
    return {"id": tid, "pinned": False}


@app.post("/v1/videos/{tid}/release")
def release_video(tid: str, request: Request):
    """R633: the client already has the bytes - drop our copy now instead of waiting TTL.

    Idempotent. An explicit owner request outranks the janitor's pin guard, so a
    pinned clip is released too; the row itself stays (status/url/history intact).
    """
    key = None if is_admin(request) else require_key(request)
    c = db()
    row = c.execute("select id,key_id,local_path,pinned from tasks where id=?", (tid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        c.close()
        raise HTTPException(403, "task belongs to another key")
    p = row["local_path"]
    freed = 0
    already = not p
    if p:
        for extra in ("", ".thumb.jpg", ".meta.json"):
            fp = p + extra
            try:
                if os.path.isfile(fp):
                    if not extra:
                        freed = os.path.getsize(fp)
                    os.remove(fp)
            except Exception:
                continue
    with _db_lock:
        c.execute("update tasks set local_path=null, released_at=?, updated=? where id=?",
                  (time.time(), time.time(), tid))
        c.commit()
    c.close()
    _RET_CACHE.update(ts=0.0, data=None)
    return {"id": tid, "released": True, "already": already, "bytes_freed": freed}


@app.post("/admin/pin")
async def admin_pin(request: Request):
    """R609: bulk pin/unpin by ids or batch_id. body {"ids":[...]} or {"batch_id":"..."}."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pinned = 0 if body.get("pinned") is False else 1
    ids = [str(x) for x in (body.get("ids") or [])][:5000]
    batch_id = body.get("batch_id")
    c = db()
    n = 0
    with _db_lock:
        if batch_id:
            n = c.execute("update tasks set pinned=? where batch_id=?", (pinned, str(batch_id))).rowcount
        elif ids:
            q = ",".join("?" * len(ids))
            n = c.execute("update tasks set pinned=? where id in (%s)" % q, [pinned] + ids).rowcount
        else:
            c.close()
            raise HTTPException(400, "need ids[] or batch_id")
        c.commit()
    total = c.execute("select count(*) n from tasks where pinned=1").fetchone()["n"]
    c.close()
    _RET_CACHE.update(ts=0.0, data=None)
    return {"ok": True, "updated": n, "pinned_total": total, "pinned": bool(pinned)}


def _retention_scan():
    """R610: one pass over local mp4s -> what TTL will delete and when."""
    now = time.time()
    ttl_s = TTL_DAYS * 86400
    c = db()
    rows = c.execute("select id, local_path, coalesce(pinned,0) pinned, created from tasks "
                     "where local_path is not null").fetchall()
    c.close()
    s = {"files": 0, "bytes": 0, "pinned_files": 0, "pinned_bytes": 0,
         "expiring_24h": {"files": 0, "bytes": 0},
         "expiring_72h": {"files": 0, "bytes": 0},
         "expired": {"files": 0, "bytes": 0}, "items": []}
    for r in rows:
        p = r["local_path"]
        try:
            if not p or not os.path.isfile(p):
                continue
            st = os.stat(p)
            sz, mt = st.st_size, st.st_mtime
        except OSError:
            continue
        s["files"] += 1
        s["bytes"] += sz
        if r["pinned"]:
            s["pinned_files"] += 1
            s["pinned_bytes"] += sz
            continue
        left_h = (ttl_s - (now - mt)) / 3600.0
        s["items"].append({"id": r["id"], "mb": round(sz / 1e6, 2),
                           "age_h": round((now - mt) / 3600.0, 1),
                           "left_h": round(left_h, 1)})
        if left_h <= 0:
            s["expired"]["files"] += 1
            s["expired"]["bytes"] += sz
        if left_h <= 24:
            s["expiring_24h"]["files"] += 1
            s["expiring_24h"]["bytes"] += sz
        if left_h <= 72:
            s["expiring_72h"]["files"] += 1
            s["expiring_72h"]["bytes"] += sz
    return s


_RET_CACHE = {"ts": 0.0, "data": None}


def _retention_cached(ttl: float = 60.0):
    """R611: /metrics and /admin/retention share one stat pass per minute."""
    now = time.time()
    if _RET_CACHE["data"] is not None and now - _RET_CACHE["ts"] < ttl:
        return _RET_CACHE["data"]
    s = _retention_scan()
    _RET_CACHE.update(ts=now, data=s)
    return s


def _gb(n):
    return round((n or 0) / 1e9, 2)


_POOL_GROWTH_JSON = os.environ.get("WZMM_POOL_GROWTH_JSON", "/opt/wzmm/_pool_growth.json")
_POOL_GROWTH_CACHE = {"mtime": None, "data": None}


def _pool_growth(max_age_h: float = 12.0):
    """R635: last account-pool growth probe. Read-only, never raises.

    The probe answers one question: could this host create a new MiniMax account
    right now (proxy up + signup page not Akamai-403)? None = never probed.
    """
    try:
        st = os.stat(_POOL_GROWTH_JSON)
    except Exception:
        return None
    if _POOL_GROWTH_CACHE["mtime"] == st.st_mtime and _POOL_GROWTH_CACHE["data"] is not None:
        d = dict(_POOL_GROWTH_CACHE["data"])
    else:
        try:
            with open(_POOL_GROWTH_JSON, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return None
        d = {k: raw.get(k) for k in ("state", "ts", "when", "http", "proxy_up",
                                     "used_proxy", "proxy_port", "verdict", "hosts")}
        _POOL_GROWTH_CACHE.update(mtime=st.st_mtime, data=d)
    age_h = round(max(0.0, time.time() - (d.get("ts") or 0)) / 3600.0, 1)
    d["age_h"] = age_h
    d["stale"] = age_h > max_age_h
    return d


@app.get("/admin/retention")
def admin_retention(request: Request, sample: int = 10):
    """R610: how much of the video cache TTL will reclaim in 24h / 72h."""
    require_admin(request)
    s = dict(_retention_cached())
    items = sorted(s.pop("items"), key=lambda x: x["left_h"])
    du = shutil.disk_usage("/")
    return {"ttl_days": TTL_DAYS, "gc_min_age_h": GC_MIN_AGE_H,
            "disk_free_gib": round(du.free / (1 << 30), 1),
            "totals": {"files": s["files"], "gb": _gb(s["bytes"])},
            "pinned": {"files": s["pinned_files"], "gb": _gb(s["pinned_bytes"])},
            "expiring_24h": {"files": s["expiring_24h"]["files"],
                             "gb": _gb(s["expiring_24h"]["bytes"])},
            "expiring_72h": {"files": s["expiring_72h"]["files"],
                             "gb": _gb(s["expiring_72h"]["bytes"])},
            "already_expired": {"files": s["expired"]["files"],
                                "gb": _gb(s["expired"]["bytes"])},
            "next_to_expire": items[:max(1, min(int(sample), 100))],
            "hint": "POST /admin/pin {ids:[...]} or {batch_id} to keep clips past TTL"}


@app.get("/admin/pins")
def admin_pins(request: Request, limit: int = 100):
    """R610: list pinned clips (survive TTL + disk GC)."""
    require_admin(request)
    lim = max(1, min(int(limit), 1000))
    c = db()
    total = c.execute("select count(*) n from tasks where coalesce(pinned,0)=1").fetchone()["n"]
    rows = c.execute("select id, local_path, created from tasks where coalesce(pinned,0)=1 "
                     "order by created desc limit ?", (lim,)).fetchall()
    c.close()
    data = []
    nb = 0
    for r in rows:
        sz = None
        try:
            if r["local_path"] and os.path.isfile(r["local_path"]):
                sz = os.path.getsize(r["local_path"])
                nb += sz
        except OSError:
            pass
        data.append({"id": r["id"], "mb": round(sz / 1e6, 2) if sz else None,
                     "created": int(r["created"]) if r["created"] else None})
    return {"object": "list", "count": total, "returned": len(data),
            "bytes": nb, "gb": _gb(nb), "data": data}


@app.get("/admin/gc/preview")
def admin_gc_preview(request: Request, limit: int = 100):
    """R610: dry-run of janitor's next pass (nothing is deleted)."""
    require_admin(request)
    s = _retention_cached()
    dead = [x for x in s["items"] if x["left_h"] <= 0]
    dead.sort(key=lambda x: x["left_h"])
    byt = sum(x["mb"] for x in dead) * 1e6
    return {"dry_run": True, "ttl_days": TTL_DAYS,
            "would_delete": {"files": len(dead), "gb": _gb(byt)},
            "pinned_excluded": {"files": s["pinned_files"], "gb": _gb(s["pinned_bytes"])},
            "sample": dead[:max(1, min(int(limit), 500))],
            "note": "janitor runs every 30 min; use POST /admin/pin to keep a clip"}


def _orphan_scan():
    """R612: mp4 files in VIDEOS_DIR that no tasks.local_path points at.

    They are invisible to janitor (which walks DB rows) and to /gallery,
    so they neither expire nor show up. Import instead of deleting.
    """
    try:
        names = os.listdir(VIDEOS_DIR)
    except OSError:
        return []
    c = db()
    known = set()
    for r in c.execute("select local_path from tasks where local_path is not null"):
        p = r["local_path"]
        if p:
            known.add(os.path.basename(p))
    c.close()
    now = time.time()
    out = []
    for fn in names:
        if not fn.lower().endswith(".mp4") or fn in known:
            continue
        p = os.path.join(VIDEOS_DIR, fn)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"id": fn[:-4], "name": fn, "mb": round(st.st_size / 1e6, 2),
                    "bytes": st.st_size,
                    "age_days": round((now - st.st_mtime) / 86400.0, 1),
                    "mtime": int(st.st_mtime), "path": p})
    out.sort(key=lambda x: -x["bytes"])
    return out


@app.get("/admin/orphans")
def admin_orphans(request: Request, limit: int = 200):
    """R612: read-only list of mp4s missing from the task table."""
    require_admin(request)
    rows = _orphan_scan()
    lim = max(1, min(int(limit), 2000))
    tot = sum(r["bytes"] for r in rows)
    return {"object": "list", "count": len(rows), "bytes": tot, "gb": _gb(tot),
            "data": [{k: r[k] for k in ("id", "mb", "age_days")} for r in rows[:lim]],
            "hint": "POST /admin/orphans/import {ids:[...]} to register them (no deletion)"}


@app.post("/admin/orphans/import")
async def admin_orphans_import(request: Request):
    """R612: register orphan files as succeeded tasks so TTL/gallery see them."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    want = {str(x) for x in (body.get("ids") or [])}
    rows = [r for r in _orphan_scan() if not want or r["id"] in want]
    n = 0
    c = db()
    with _db_lock:
        for r in rows:
            cur = c.execute(
                "insert or ignore into tasks(id, model, resolution, duration, ratio, prompt, status, "
                "created, updated, local_path) values(?,?,?,?,?,?,?,?,?,?)",
                (r["id"], DEFAULT_MODEL, "1080P", 6, "16:9", "imported orphan (expiry probe)",
                 "succeeded", r["mtime"], time.time(), r["path"]))
            n += cur.rowcount
        c.commit()
    c.close()
    _RET_CACHE.update(ts=0.0, data=None)
    return {"ok": True, "imported": n, "scanned": len(rows),
            "note": "registered under TTL; pin them if they must survive"}


_ARCHIVE_SEM = threading.Semaphore(1)



# ---------------- R703: server-side archive jobs ----------------
ARCHIVE_JOB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_jobs")
ARCHIVE_JOB_TTL = 7200
ARCHIVE_JOB_MAX = 2
_ARCHIVE_JOBS = {}
_ARCHIVE_JOBS_LOCK = threading.Lock()
_ARCHIVE_JOB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="arcjob")
_JOB_POOL_SLOTS = threading.BoundedSemaphore(2)


def _job_gc():
    now = time.time()
    dead = []
    with _ARCHIVE_JOBS_LOCK:
        for jid, j in list(_ARCHIVE_JOBS.items()):
            if j["state"] != "building" and now - j["created"] > ARCHIVE_JOB_TTL:
                dead.append((jid, j))
                _ARCHIVE_JOBS.pop(jid, None)
    for jid, j in dead:
        for suf in (".zip", ".part"):
            try:
                os.remove(os.path.join(ARCHIVE_JOB_DIR, jid + suf))
            except OSError:
                pass


def _job_set(jid, **kw):
    with _ARCHIVE_JOBS_LOCK:
        j = _ARCHIVE_JOBS.get(jid)
        if j:
            j.update(kw)


def _job_public(j):
    total = j.get("total") or 0
    built = j.get("built") or 0
    pct = 100 if j["state"] == "ready" else (min(99, int(built * 100 / total)) if total else 0)
    return {"job_id": j["jid"], "state": j["state"], "kind": j["kind"], "count": j.get("count", 0),
            "total": total, "built": built, "pct": pct, "bytes": j.get("bytes", 0),
            "filename": j.get("filename", ""), "error": j.get("error", ""),
            "created": j["created"], "expires_in": max(0, int(ARCHIVE_JOB_TTL - (time.time() - j["created"])))}


def _job_build(jid):
    with _ARCHIVE_JOBS_LOCK:
        j = _ARCHIVE_JOBS.get(jid)
        if not j:
            return
        files = list(j["files"])
    os.makedirs(ARCHIVE_JOB_DIR, exist_ok=True)
    part = os.path.join(ARCHIVE_JOB_DIR, jid + ".part")
    final = os.path.join(ARCHIVE_JOB_DIR, jid + ".zip")
    done = 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for path, arc in files:
                with _ARCHIVE_JOBS_LOCK:
                    if jid not in _ARCHIVE_JOBS:
                        raise RuntimeError("job cancelled")
                z.write(path, arcname=arc)
                try:
                    done += os.path.getsize(path)
                except OSError:
                    pass
                _job_set(jid, built=done)
        with _ARCHIVE_JOBS_LOCK:
            if jid not in _ARCHIVE_JOBS:
                raise RuntimeError("job cancelled")
        os.replace(part, final)
        with _ARCHIVE_JOBS_LOCK:
            gone = jid not in _ARCHIVE_JOBS
        if gone:
            try:
                os.remove(final)
            except OSError:
                pass
        else:
            _job_set(jid, state="ready", path=final, built=os.path.getsize(final),
                     bytes=os.path.getsize(final))
    except Exception as e:
        cancelled = "cancelled" in str(e)
        if not cancelled:
            _job_set(jid, state="failed", error=("%s: %s" % (type(e).__name__, e))[:200])
        for suf in (".part", ".zip"):
            try:
                os.remove(os.path.join(ARCHIVE_JOB_DIR, jid + suf))
            except OSError:
                pass
    finally:
        try:
            _JOB_POOL_SLOTS.release()
        except ValueError:
            pass


def _job_owner_key(request: Request):
    if viewer_ok(request):
        return "viewer"
    return require_key_light(request)["id"]


def _job_collect(kind, want, batch_id, pinned, lim, owner):
    if kind == "library":
        ids = [os.path.basename(x.strip()) for x in want if x.strip()][:lim]
        if not ids:
            raise HTTPException(400, "ids is empty")
        files, total = [], 0
        for name in ids:
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            fp = os.path.join(LEGACY_DIR, name)
            if not os.path.isfile(fp):
                continue
            total += os.path.getsize(fp)
            files.append((fp, name))
        if not files:
            raise HTTPException(404, "no legacy clips matched")
        return files, total

    if not want and not batch_id and pinned != 1:
        raise HTTPException(400, "ids or batch_id required")
    where, args = ["local_path is not null"], []
    if owner != "viewer":
        where.append("key_id = ?")
        args.append(owner)
    if batch_id:
        where.append("batch_id = ?")
        args.append(batch_id)
    if want:
        ids = [x.strip() for x in want if x.strip()][:lim]
        if not ids:
            raise HTTPException(400, "ids is empty")
        where.append("id in (%s)" % ",".join("?" * len(ids)))
        args += ids
    if pinned == 1:
        where.append("coalesce(pinned,0)=1")
    c = db()
    rows = c.execute("select id, local_path from tasks where " + " and ".join(where) +
                     " order by created desc limit ?", args + [lim]).fetchall()
    c.close()
    files, total = [], 0
    for r in rows:
        fp = r["local_path"]
        try:
            if not fp or not os.path.isfile(fp):
                continue
            total += os.path.getsize(fp)
        except OSError:
            continue
        files.append((fp, r["id"] + ".mp4"))
    if not files:
        raise HTTPException(404, "no ready clips matched")
    return files, total


@app.post("/v1/archive/jobs")
async def archive_job_create(request: Request):
    owner = _job_owner_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    kind = (body.get("kind") or "videos").strip().lower()
    if kind not in ("videos", "library"):
        raise HTTPException(400, "kind must be videos or library")
    raw_ids = body.get("ids") or []
    if isinstance(raw_ids, str):
        raw_ids = raw_ids.split(",")
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "ids must be a list")
    want = [str(x).strip() for x in raw_ids if str(x).strip()][:100]
    lim = max(1, min(int(body.get("limit") or 100), 100))
    files, total = _job_collect(kind, want, str(body.get("batch_id") or ""),
                                int(body.get("pinned") or 0), lim, owner)
    if total > 1_500_000_000:
        raise HTTPException(413, "selection is %.2f GB; pick fewer clips" % (total / 1e9))
    _job_gc()
    with _ARCHIVE_JOBS_LOCK:
        live = sum(1 for j in _ARCHIVE_JOBS.values() if j["state"] == "building")
    if live >= ARCHIVE_JOB_MAX:
        raise HTTPException(429, "too many archive jobs building; retry shortly",
                            headers={"Retry-After": "20"})
    os.makedirs(ARCHIVE_JOB_DIR, exist_ok=True)
    try:
        free = shutil.disk_usage(ARCHIVE_JOB_DIR).free
    except OSError:
        free = 0
    if free < total + 300_000_000:
        raise HTTPException(507, "not enough disk for a %.2f GB zip" % (total / 1e9))
    if not _JOB_POOL_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "archive builder busy; retry shortly",
                            headers={"Retry-After": "20"})
    jid = "aj_" + secrets.token_hex(8)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = ("wzlib_%d_%s.zip" % (len(files), stamp)) if kind == "library" \
        else ("wzmm_%d_%s.zip" % (len(files), stamp))
    with _ARCHIVE_JOBS_LOCK:
        _ARCHIVE_JOBS[jid] = {"jid": jid, "kind": kind, "owner": owner, "state": "building",
                              "files": files, "count": len(files), "total": total, "built": 0,
                              "bytes": 0, "filename": name, "error": "",
                              "created": time.time()}
    _ARCHIVE_JOB_POOL.submit(_job_build, jid)
    return {"ok": True, "job_id": jid, "state": "building", "count": len(files),
            "total_bytes": total, "filename": name}


@app.get("/v1/archive/jobs/{jid}")
def archive_job_status(request: Request, jid: str):
    owner = _job_owner_key(request)
    with _ARCHIVE_JOBS_LOCK:
        j = _ARCHIVE_JOBS.get(jid)
    if not j or (j["owner"] != "viewer" and j["owner"] != owner):
        raise HTTPException(404, "job not found")
    return _job_public(j)


@app.get("/v1/archive/jobs/{jid}/file")
def archive_job_file(request: Request, jid: str):
    owner = _job_owner_key(request)
    with _ARCHIVE_JOBS_LOCK:
        j = _ARCHIVE_JOBS.get(jid)
    if not j or (j["owner"] != "viewer" and j["owner"] != owner):
        raise HTTPException(404, "job not found")
    if j["state"] != "ready" or not j.get("path"):
        raise HTTPException(409, "job is %s" % j["state"])
    return FileResponse(j["path"], media_type="application/zip", filename=j["filename"])


@app.delete("/v1/archive/jobs/{jid}")
def archive_job_drop(request: Request, jid: str):
    owner = _job_owner_key(request)
    with _ARCHIVE_JOBS_LOCK:
        j = _ARCHIVE_JOBS.pop(jid, None)
    if not j or (j["owner"] != "viewer" and j["owner"] != owner):
        raise HTTPException(404, "job not found")
    for suf in (".zip", ".part"):
        try:
            os.remove(os.path.join(ARCHIVE_JOB_DIR, jid + suf))
        except OSError:
            pass
    return {"ok": True, "job_id": jid}


@app.get("/v1/videos/archive")
def videos_archive(request: Request, batch_id: str = "", ids: str = "", pinned: int = -1,
                   limit: int = 50):
    """R613: stream a zip of ready clips. One at a time; capped by count and bytes."""
    key = None if viewer_ok(request) else require_key_light(request)
    lim = max(1, min(int(limit), 100))
    where, args = ["local_path is not null"], []
    if key is not None:
        where.append("key_id = ?"); args.append(key["id"])
    if batch_id:
        where.append("batch_id = ?"); args.append(batch_id)
    if ids:
        want = [x.strip() for x in ids.split(",") if x.strip()][:lim]
        if not want:
            raise HTTPException(400, "ids is empty")
        where.append("id in (%s)" % ",".join("?" * len(want))); args += want
    if pinned == 1:
        where.append("coalesce(pinned,0)=1")
    c = db()
    rows = c.execute("select id, local_path from tasks where " + " and ".join(where) +
                     " order by created desc limit ?", args + [lim]).fetchall()
    c.close()
    files = []
    total = 0
    for r in rows:
        p = r["local_path"]
        try:
            if not p or not os.path.isfile(p):
                continue
            total += os.path.getsize(p)
        except OSError:
            continue
        files.append((p, r["id"] + ".mp4"))
    if not files:
        raise HTTPException(404, "no ready clips matched")
    if total > 1_500_000_000:
        raise HTTPException(413, "selection is %.2f GB; use batch_id or fewer ids" % (total / 1e9))
    if not _ARCHIVE_SEM.acquire(blocking=False):
        raise HTTPException(429, "another archive is being built; retry shortly",
                            headers={"Retry-After": "30"})
    try:
        tmp = tempfile.NamedTemporaryFile(prefix="wzmm_archive_", suffix=".zip", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for p, arc in files:
                z.write(p, arcname=arc)
    except Exception:
        _ARCHIVE_SEM.release()
        raise
    tag = (batch_id or ("pinned" if pinned == 1 else "sel"))[:40]
    name = "wzmm_%s_%s.zip" % (tag, time.strftime("%Y%m%d_%H%M%S"))
    return FileResponse(tmp.name, media_type="application/zip", filename=name,
                        background=BackgroundTask(_archive_done, tmp.name))


def _archive_done(path):
    try:
        os.remove(path)
    except OSError:
        pass
    _ARCHIVE_SEM.release()


@app.get("/admin/producer-stats")
def admin_producer_stats(request: Request, refresh: int = 0):
    """R691: read-only health of the account producer, parsed from its log."""
    require_admin(request)
    p = "/opt/wzmm/producer_stats.json"
    if refresh:
        try:
            subprocess.run([sys.executable, "/opt/wzmm/producer_stats.py"],
                           capture_output=True, timeout=30, cwd="/opt/wzmm")
        except Exception as e:
            raise HTTPException(500, "stats run failed: %s" % e)
    if not os.path.isfile(p):
        raise HTTPException(404, "no stats yet; call with ?refresh=1")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@app.get("/admin/forecast")
def admin_forecast(request: Request, refresh: int = 0):
    """R688: pool runway forecast (read-only). refresh=1 recomputes via pool_forecast.py."""
    require_admin(request)
    p = "/opt/wzmm/pool_forecast.json"
    if refresh:
        try:
            subprocess.run([sys.executable, "/opt/wzmm/pool_forecast.py"],
                           capture_output=True, timeout=30, cwd="/opt/wzmm")
        except Exception as e:
            raise HTTPException(500, "forecast run failed: %s" % e)
    if not os.path.isfile(p):
        raise HTTPException(404, "no forecast yet; call with ?refresh=1")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@app.get("/admin/capacity")
def admin_capacity(request: Request):
    """R615: one screen for the disk decision (A/B/C). Reads only; deletes nothing."""
    require_admin(request)
    du = shutil.disk_usage("/")
    ret = _retention_cached()
    _cat, _r24, _w7 = _catalog_sizes()
    _burn, _bh = _disk_burn((_w7 / 7.0) if _w7 > 0 else _r24)
    _free_gib = du.free / (1 << 30)
    out = {"disk": {"total_gb": round(du.total / 1e9, 1), "used_gb": round(du.used / 1e9, 1),
                    "free_gb": round(du.free / 1e9, 1),
                    "used_pct": round(du.used * 100.0 / du.total, 1),
                    "soft_gib": DISK_SOFT_GB, "hard_gib": MIN_ACCEPT_GB,
                    "mode": _disk_mode(),
                    "free_gib": round(_free_gib, 1),
                    "catalog_gb": round(_cat, 2), "catalog_gb_7d": round(_w7, 2),
                    "burn_gb_per_day": _burn, "burn_window_h": _bh,
                    "burn_source": "observed_free_space" if _bh else "catalog_7d_avg",
                    "days_to_accept_gate": (round((_free_gib - MIN_ACCEPT_GB) / _burn, 1)
                                            if _burn > 0 else None),
                    "days_to_full": round(_free_gib / _burn, 1) if _burn > 0 else None},
           "api_cache": {"files": ret["files"], "gb": _gb(ret["bytes"]),
                         "pinned_files": ret["pinned_files"],
                         "pinned_gb": _gb(ret["pinned_bytes"]),
                         "expiring_72h": ret["expiring_72h"]["files"]},
           "legacy": None, "options": {}, "deleted": False}
    p = "/opt/wzmm/_farm_resolution.json"
    if os.path.isfile(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            out["legacy"] = {"error": repr(e)[:120]}
            return out
        groups = d.get("groups") or []
        total = float(d.get("total_bytes") or 0)

        def parse(res):
            try:
                a, b = str(res).split("x")
                return int(a), int(b)
            except Exception:
                return 0, 0

        exact_gb = exact_n = class_gb = class_n = 0
        for g in groups:
            w, h = parse(g.get("res"))
            if g.get("res") == "1920x1080":
                exact_gb += float(g.get("gb") or 0)
                exact_n += int(g.get("n") or 0)
            if w and h and min(w, h) >= 1080:
                class_gb += float(g.get("gb") or 0)
                class_n += int(g.get("n") or 0)
        out["legacy"] = {"files": d.get("files"), "gb": round(total / 1e9, 2),
                         "scan_age_h": round((time.time() - os.path.getmtime(p)) / 3600.0, 1),
                         "groups": groups[:10]}
        out["options"] = {
            "A_keep_1920x1080_only": {
                "keep_files": exact_n, "keep_gb": round(exact_gb, 2),
                "reclaim_gb": round(total / 1e9 - exact_gb, 2),
                "risk": "deletes every non-1920x1080 clip in the legacy farm"},
            "B_keep_min_side_1080": {
                "keep_files": class_n, "keep_gb": round(class_gb, 2),
                "reclaim_gb": round(total / 1e9 - class_gb, 2),
                "risk": "keeps 1080-class (incl. 1440x1080 / 1080x1920), drops smaller"},
            "C_expand_disk": {
                "reclaim_gb": 0, "needs": "cloud console 100G -> 150G",
                "downtime": "none", "command": "bash expand_disk.sh --apply",
                "risk": "none, but depends on provider console access"},
        }
        out["recommendation"] = "C (no data loss) if console is reachable, else B"
        out["note"] = "A/B require purge_legacy.py --apply; nothing has been deleted"
    return out


@app.get("/v1/videos/{tid}/meta")
def video_meta(tid: str, request: Request):
    key = None if is_admin(request) else require_key_light(request)
    c = db()
    row = c.execute("select * from tasks where id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        raise HTTPException(403, "task belongs to another key")
    if not row["local_path"] or not os.path.isfile(row["local_path"]):
        raise HTTPException(409, "video not ready (status=%s)" % row["status"])
    m = _mp4_meta(row["local_path"])
    m.update({"id": row["id"], "status": row["status"], "model": row["model"],
              "requested_resolution": row["resolution"], "requested_duration": row["duration"],
              "ratio": row["ratio"], "created": int(row["created"]),
              "thumb_url": str(request.base_url).rstrip("/") + "/v1/videos/%s/thumb" % tid})
    return m


@app.api_route("/v1/videos/{tid}/thumb", methods=["GET", "HEAD"])
def video_thumb(tid: str, request: Request, at: float = 1.0):
    key = None if viewer_ok(request) else require_key_light(request)
    c = db()
    row = c.execute("select * from tasks where id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        raise HTTPException(403, "task belongs to another key")
    if not row["local_path"] or not os.path.isfile(row["local_path"]):
        raise HTTPException(409, "video not ready (status=%s)" % row["status"])
    return FileResponse(_thumb_for(row["local_path"], max(0.0, min(float(at), 30.0))),
                        media_type="image/jpeg")


@app.get("/v1/videos/{tid}/events")
def video_events(tid: str, request: Request, timeout: int = 900):
    """Server-Sent Events: one frame per state change until the task is terminal.

    curl -N "https://.../v1/videos/<id>/events" -H "Authorization: Bearer <key>"
    """
    key = None if is_admin(request) else require_key_light(request)
    c = db()
    row = c.execute("select id,status,key_id,created from tasks where id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "task not found")
    if not can_read_task(row, request, key):
        raise HTTPException(403, "task belongs to another key")

    def gen():
        deadline = time.time() + max(10, min(int(timeout), 1800))
        last = None
        yield "retry: 3000\n\n"
        while time.time() < deadline:
            c2 = db()
            r = c2.execute("select id,status,model,resolution,duration,ratio,created,updated,url,err,"
                           "local_path,retry,upstream from tasks where id=?", (tid,)).fetchone()
            c2.close()
            if not r:
                yield "event: error\ndata: {\"error\":\"task vanished\"}\n\n"
                return
            payload = {"id": r["id"], "status": r["status"], "model": r["model"],
                       "resolution": r["resolution"], "duration": r["duration"], "ratio": r["ratio"],
                       "created": int(r["created"]), "updated": int(r["updated"] or r["created"]),
                       "retry": r["retry"], "elapsed_s": int(time.time() - r["created"]),
                       "ready": bool(r["local_path"] and os.path.isfile(r["local_path"])),
                       "bytes": os.path.getsize(r["local_path"]) if r["local_path"] and os.path.isfile(r["local_path"]) else None,
                       "error": r["err"]}
            sig = (r["status"], payload["ready"], payload["bytes"])
            if sig != last:
                last = sig
                yield "event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            if r["status"] in ("succeeded", "failed", "canceled"):
                yield "event: done\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                return
            time.sleep(1.0)
        yield "event: timeout\ndata: {\"id\":\"" + tid + "\"}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@app.api_route("/v1/videos/{tid}/content", methods=["GET", "HEAD"])
def video_content(tid: str, request: Request):
    key = None if viewer_ok(request) else require_key_light(request)
    c = db()
    row = c.execute("select url,local_path,key_id from tasks where id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "no video yet")
    if not can_read_task(row, request, key):
        raise HTTPException(403, "task belongs to another key")
    if row["local_path"] and os.path.isfile(row["local_path"]):
        return FileResponse(row["local_path"], media_type="video/mp4", filename=tid + ".mp4")
    if not row["url"]:
        raise HTTPException(404, "no video yet")
    return RedirectResponse(row["url"], status_code=302)

LEGACY_DIR = os.environ.get("WZMM_LEGACY", "/opt/h3v2/data/render/out")

_LIB_LOCK = threading.Lock()
_LIB_CACHE = {"ts": 0.0, "data": [], "root": ""}


# R553: resolution map built from the .meta.json sidecars written by _mp4_meta().
# Kept separate from _LIB_CACHE so the plain listing stays at ~2ms (selftest guards 120ms).
_LIB_RES = {"ts": 0.0, "map": {}}

_LIB_RES_CACHE_FILE = "/opt/wzmm/library_res_cache.json"


def _library_res_map(ttl: float = 600.0, force: bool = False):
    """R694: persist the 24k-sidecar resolution map to disk, keyed by the dir mtime.

    Building it costs ~2.9 s (24,494 open+json.load calls); the farm is append-only
    most of the time, so the on-disk copy turns a cold start into a ~20 ms read.
    """
    now = time.time()
    with _LIB_LOCK:
        if not force and _LIB_RES["map"] and now - _LIB_RES["ts"] < ttl:
            return _LIB_RES["map"]
        try:
            dir_mtime = os.stat(LEGACY_DIR).st_mtime
        except OSError:
            dir_mtime = 0.0
        if not force and dir_mtime:
            try:
                with open(_LIB_RES_CACHE_FILE, encoding="utf-8") as f:
                    c = json.load(f)
                if c.get("dir_mtime") == dir_mtime and c.get("map"):
                    _LIB_RES.update(ts=now, map={k: tuple(v) for k, v in c["map"].items()})
                    return _LIB_RES["map"]
            except Exception:
                pass
        m = {}
        try:
            with os.scandir(LEGACY_DIR) as it:
                for e in it:
                    if not e.name.lower().endswith(".mp4"):
                        continue
                    try:
                        with open(os.path.join(LEGACY_DIR, e.name + ".meta.json"), "r", encoding="utf-8") as f:
                            d = json.load(f)
                        w, h = int(d.get("width") or 0), int(d.get("height") or 0)
                    except Exception:
                        continue
                    if w and h:
                        m[e.name[:-4]] = (w, h)
        except FileNotFoundError:
            raise
        _LIB_RES.update(ts=now, map=m)
        try:
            tmp = _LIB_RES_CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"dir_mtime": dir_mtime,
                           "map": {k: list(v) for k, v in m.items()}}, f)
            os.replace(tmp, _LIB_RES_CACHE_FILE)
        except Exception:
            pass
        return m

_RES_ALIAS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080),
              "1440p": (2560, 1440), "2k": (2560, 1440), "4k": (3840, 2160)}

def _parse_res_filter(res):
    """'1080p' -> exact 1920x1080 (same definition the 1080P gate uses).

    '1920x1080' -> exact; '<n>p' for n not in _RES_ALIAS -> height >= n.
    '' -> None
    """
    r = (res or "").strip().lower().replace(" ", "")
    if not r:
        return None
    if r in _RES_ALIAS:
        return ("wh", _RES_ALIAS[r])
    if r.endswith("p") and r[:-1].isdigit():
        return ("h", int(r[:-1]))
    if "x" in r:
        a, b = r.split("x", 1)
        if a.isdigit() and b.isdigit():
            return ("wh", (int(a), int(b)))
    raise HTTPException(400, "bad res filter %r; use 1080p or 1920x1080" % res)


def _library_index(ttl: float = 30.0, force: bool = False):
    """Cached directory index; 24k stat() calls cost ~300ms, cache makes it ~2ms."""
    now = time.time()
    with _LIB_LOCK:
        if not force and _LIB_CACHE["data"] and now - _LIB_CACHE["ts"] < ttl and _LIB_CACHE["root"] == LEGACY_DIR:
            return _LIB_CACHE["data"]
        files = []
        with os.scandir(LEGACY_DIR) as it:
            for e in it:
                try:
                    if not e.is_file() or not e.name.lower().endswith(".mp4"):
                        continue
                    st = e.stat()
                except OSError:
                    continue
                files.append({"id": e.name[:-4], "name": e.name,
                              "bytes": st.st_size, "mtime": int(st.st_mtime),
                              "mb": round(st.st_size / 1e6, 2),
                              "age_days": round((now - st.st_mtime) / 86400.0, 1)})
        files.sort(key=lambda x: (-x["mtime"], x["name"]))
        _LIB_CACHE.update(ts=now, data=files, root=LEGACY_DIR)
        return files


def _quick_sig(path, size, probe=262144):
    """Size + head/tail hash: cheap near-duplicate signature for large mp4s."""
    h = hashlib.md5()
    h.update(b"%d|" % size)
    with open(path, "rb") as f:
        h.update(f.read(probe))
        if size > probe * 2:
            f.seek(-probe, os.SEEK_END)
            h.update(f.read(probe))
    return h.hexdigest()

@app.post("/admin/library/gc")
def library_gc(request: Request, dry_run: int = 1, mode: str = "dedupe",
               older_than_days: float = 0, keep: str = "newest"):
    """Reclaim space in the legacy farm dir. Never deletes unless dry_run=0.

    mode=dedupe           -> identical files (size + head/tail md5), keep `newest`/`oldest` of each group
    mode=older_than_days  -> delete every file older than N days
    mode=both             -> dedupe first, then age filter (age filter wins)
    """
    require_admin(request)
    try:
        entries = []
        with os.scandir(LEGACY_DIR) as it:
            for e in it:
                try:
                    if e.is_file() and e.name.lower().endswith(".mp4"):
                        st = e.stat()
                        entries.append({"name": e.name, "path": e.path,
                                        "bytes": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    continue
    except FileNotFoundError:
        raise HTTPException(404, "library dir missing: " + LEGACY_DIR)
    total_bytes = sum(x["bytes"] for x in entries)
    doomed, groups = {}, []
    if mode in ("dedupe", "both"):
        by_size = {}
        for x in entries:
            by_size.setdefault(x["bytes"], []).append(x)
        for size, grp in by_size.items():
            if len(grp) < 2:
                continue
            by_sig = {}
            for x in grp:
                try:
                    by_sig.setdefault(_quick_sig(x["path"], size), []).append(x)
                except OSError:
                    continue
            for sig, same in by_sig.items():
                if len(same) < 2:
                    continue
                same.sort(key=lambda y: y["mtime"], reverse=(keep == "newest"))
                extra = same[1:]
                for x in extra:
                    doomed[x["name"]] = "duplicate"
                groups.append({"size": size, "n": len(same), "kept": same[0]["name"],
                               "reclaim": sum(y["bytes"] for y in extra)})
    if older_than_days and mode in ("older_than_days", "both"):
        cutoff = time.time() - older_than_days * 86400
        for x in entries:
            if x["mtime"] < cutoff:
                doomed[x["name"]] = "older_than_%gd" % older_than_days
    freed = sum(x["bytes"] for x in entries if x["name"] in doomed)
    deleted, errors = 0, []
    if not dry_run:
        for x in entries:
            if x["name"] not in doomed:
                continue
            try:
                os.remove(x["path"]); deleted += 1
            except OSError as ex:
                errors.append({"name": x["name"], "error": str(ex)[:120]})
        _library_index(force=True)
    return {"dry_run": bool(dry_run), "mode": mode, "root": LEGACY_DIR,
            "files_total": len(entries), "bytes_total": total_bytes,
            "files_to_delete": len(doomed), "bytes_to_free": freed,
            "deleted": deleted, "errors": errors[:20],
            "duplicate_groups": len(groups),
            "sample_groups": sorted(groups, key=lambda g: -g["reclaim"])[:10],
            "disk": (lambda du: {"total_gb": round(du.total / 1e9, 1),
                                 "used_gb": round(du.used / 1e9, 1),
                                 "free_gb": round(du.free / 1e9, 1)})(shutil.disk_usage("/"))}

_FARM = {"ts": 0.0, "d": None}


def _farm_summary(ttl: float = 60.0):
    """R642: tiny legacy-farm summary for the gallery nav (reads farm_scan output)."""
    now = time.time()
    if _FARM["d"] is not None and now - _FARM["ts"] < ttl:
        return _FARM["d"]
    p = os.environ.get("WZMM_FARM_STATS", "/opt/wzmm/_farm_resolution.json")
    out = None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        g = next((x for x in d.get("groups", []) if x.get("res") == "1920x1080"), None) or {}
        out = {"files": d.get("files"), "gb": round((d.get("total_bytes") or 0) / 1e9, 2),
               "n1080": g.get("n"), "gb1080": g.get("gb")}
    except Exception:
        out = None
    _FARM.update(ts=now, d=out)
    return out


@app.get("/v1/library/stats")
def library_stats(request: Request):
    """Resolution/size distribution of the legacy farm (produced by farm_scan.py)."""
    if not is_admin(request):
        require_key_light(request)
    p = os.environ.get("WZMM_FARM_STATS", "/opt/wzmm/_farm_resolution.json")
    if not os.path.isfile(p):
        raise HTTPException(404, "no scan yet: run /opt/wzmm/farm_scan.py")
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    du = shutil.disk_usage("/")
    keep_1080 = next((g for g in d.get("groups", []) if g["res"] == "1920x1080"), None)
    out = {"files": d.get("files"), "total_gb": round((d.get("total_bytes") or 0) / 1e9, 2),
           "scanned_in_s": d.get("elapsed_s"), "groups": d.get("groups", [])[:20],
           "disk": {"total_gb": round(du.total / 1e9, 1), "used_gb": round(du.used / 1e9, 1),
                    "free_gb": round(du.free / 1e9, 1),
                    "used_pct": round(du.used * 100.0 / du.total, 1)}}
    if keep_1080:
        out["keep_only_1080p"] = {"files": keep_1080["n"], "gb": keep_1080["gb"],
                                  "reclaim_gb": round(out["total_gb"] - keep_1080["gb"], 2)}
    return out


@app.get("/v1/library")
def library_list(request: Request, limit: int = 50, offset: int = 0, q: str = "",
                 res: str = "", min_h: int = 0, refresh: int = 0, embed_key: int = 0):
    if not viewer_ok(request):
        require_key_light(request)
    try:
        files = _library_index(force=bool(refresh))
    except FileNotFoundError:
        raise HTTPException(404, "library dir missing: " + LEGACY_DIR)
    flt = _parse_res_filter(res)
    rmap = _library_res_map(force=bool(refresh)) if (flt or min_h) else (_LIB_RES["map"] or None)
    if q:
        ql = q.lower()
        files = [f for f in files if ql in f["name"].lower()]
    if flt or min_h:
        rmap = rmap or {}
        mode, val = flt if flt else ("h", 0)
        def _keep(f):
            wh = (rmap or {}).get(f["id"])
            if not wh:
                return False
            if mode == "wh":
                return wh == val
            return wh[1] >= max(int(min_h or 0), val or 0)
        files = [f for f in files if _keep(f)]
    total = len(files)
    total_mb = round(sum(f["bytes"] for f in files) / 1e6, 1) if (flt or min_h) else None
    off = max(0, int(offset)); lim = max(1, min(int(limit), 200))
    page = files[off:off + lim]
    base = str(request.base_url).rstrip("/")
    kq = ""
    if embed_key:
        _k = request.query_params.get("key") or ""
        if not _k:
            _a = request.headers.get("authorization") or ""
            if _a.lower().startswith("bearer "):
                _k = _a[7:].strip()
        if _k:
            kq = "?key=" + urllib.parse.quote(_k, safe="")
    for f in page:
        f["content_url"] = base + "/v1/library/" + f["id"] + "/content" + kq
        wh = (rmap or {}).get(f["id"])
        if wh:
            f["res"] = "%dx%d" % wh
    out = {"object": "list", "root": LEGACY_DIR, "total": total, "offset": off, "limit": lim, "data": page}
    if flt or min_h:
        out["filter"] = {"res": res or None, "min_h": min_h or None}
        out["total_mb"] = total_mb
    return out


@app.get("/v1/library/manifest")
def library_manifest(request: Request, res: str = "", min_h: int = 0, limit: int = 0,
                     format: str = "json", embed_key: int = 0, refresh: int = 0):
    """Bulk export of the legacy library: every matching id + direct content/thumb URLs.

    format=csv  -> one row per file, streamable into aria2c/wget/ffmpeg
    limit       -> 0 means all matches
    """
    if not is_admin(request):
        require_key_light(request)
    try:
        files = _library_index(force=bool(refresh))
    except FileNotFoundError:
        raise HTTPException(404, "library dir missing: " + LEGACY_DIR)
    flt = _parse_res_filter(res)
    rmap = _library_res_map(force=bool(refresh)) if (flt or min_h) else (_LIB_RES["map"] or {})
    if flt or min_h:
        mode, val = flt if flt else ("h", 0)
        def _keep(f):
            wh = rmap.get(f["id"])
            if not wh:
                return False
            if mode == "wh":
                return wh == val
            return wh[1] >= max(int(min_h or 0), val or 0)
        files = [f for f in files if _keep(f)]
    n = len(files) if not limit else min(len(files), max(1, int(limit)))
    files = files[:n]
    base = str(request.base_url).rstrip("/")
    fmt = (format or "json").lower()
    if fmt == "txt":
        from fastapi.responses import PlainTextResponse
        tok = request.query_params.get("key") or ""
        q = ("?key=" + tok) if (tok and str(request.query_params.get("embed_key") or "") not in ("", "0", "false")) else ""
        return PlainTextResponse("".join(base + "/v1/library/" + f["id"] + "/content" + q + "\n" for f in files),
                                 media_type="text/plain",
                                 headers={"Content-Disposition": 'attachment; filename="wz_library_urls.txt"'})
    if fmt == "csv":
        from fastapi.responses import PlainTextResponse
        rows = ["id,name,bytes,mb,res,content_url,thumb_url"]
        for f in files:
            wh = rmap.get(f["id"])
            rows.append("%s,%s,%d,%.2f,%s,%s/v1/library/%s/content,%s/v1/library/%s/thumb" % (
                f["id"], f["name"], f["bytes"], f["mb"],
                ("%dx%d" % wh) if wh else "",
                base, f["id"], base, f["id"]))
        return PlainTextResponse("\n".join(rows) + "\n", media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="wz_library.csv"'})
    return {"object": "list", "format": "json", "total": n,
            "total_mb": round(sum(f["bytes"] for f in files) / 1e6, 1),
            "filter": {"res": res or None, "min_h": min_h or None},
            "data": [{"id": f["id"], "name": f["name"], "bytes": f["bytes"], "mb": f["mb"],
                      "res": ("%dx%d" % rmap[f["id"]]) if f["id"] in rmap else None,
                      "content_url": base + "/v1/library/" + f["id"] + "/content",
                      "thumb_url": base + "/v1/library/" + f["id"] + "/thumb"} for f in files]}

def _lib_path(fid: str) -> str:
    name = os.path.basename(fid)
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    p = os.path.join(LEGACY_DIR, name)
    if not os.path.isfile(p):
        raise HTTPException(404, "file not found")
    return p


@app.get("/v1/library/{fid}/meta")
def library_meta(fid: str, request: Request):
    if not viewer_ok(request):
        require_key_light(request)
    p = _lib_path(fid)
    m = _mp4_meta(p)
    base = str(request.base_url).rstrip("/")
    m.update({"id": os.path.basename(p)[:-4], "name": os.path.basename(p),
              "mtime": int(os.path.getmtime(p)),
              "age_days": round((time.time() - os.path.getmtime(p)) / 86400.0, 1),
              "thumb_url": base + "/v1/library/%s/thumb" % os.path.basename(p)[:-4],
              "content_url": base + "/v1/library/%s/content" % os.path.basename(p)[:-4]})
    return m


@app.api_route("/v1/library/{fid}/thumb", methods=["GET", "HEAD"])
def library_thumb(fid: str, request: Request, at: float = 1.0):
    if not viewer_ok(request):
        require_key_light(request)
    p = _lib_path(fid)
    return FileResponse(_thumb_for(p, max(0.0, min(float(at), 30.0))), media_type="image/jpeg")


LIB_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>WZ Library</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e8eaed}
 header{position:sticky;top:0;background:#151922;border-bottom:1px solid #232936;padding:12px 16px;z-index:5}
 h1{font-size:16px;margin:0 0 8px;font-weight:600}
 .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 input,select,button{background:#1a1d24;border:1px solid #2b303a;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px}
 button{cursor:pointer}button.p{background:#4f7cff;border:0}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;padding:16px}
 .card{background:#151922;border:1px solid #232936;border-radius:10px;overflow:hidden}
 .card img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#0b0d11}
 .meta{padding:8px 10px;font-size:12px;color:#9aa4b2;line-height:1.5}
 .name{color:#e8eaed;font-size:12px;word-break:break-all}
 .row{display:flex;gap:6px;margin-top:6px}
 .row a,.row button{font-size:11px;padding:4px 8px;border-radius:6px;text-decoration:none;color:#cfd6e4;background:#1d2330;border:1px solid #2b303a}
 .pager{display:flex;gap:10px;justify-content:center;padding:18px;color:#9aa4b2;font-size:13px}
 .pager a{color:#8fb0ff;text-decoration:none}
 .tag{background:#243043;color:#9fc3ff;border-radius:4px;padding:1px 5px;font-size:11px}
 .sub{font-size:12px;color:#9aa4b2;margin:4px 0 0}
 .sub a{color:#8fb0ff;text-decoration:none}
.pick{position:absolute;top:8px;left:8px;width:20px;height:20px;z-index:5;cursor:pointer}
.card{position:relative}
.card.sel{outline:2px solid #4c8dff;outline-offset:-2px}
#bar{position:fixed;left:50%;transform:translateX(-50%);bottom:14px;background:#182031;border:1px solid #2a3344;border-radius:12px;padding:10px 14px;display:none;gap:10px;align-items:center;z-index:30;box-shadow:0 6px 24px rgba(0,0,0,.5);font-size:14px}
#bar a,#bar button{background:#232b3d;color:#dfe3ea;border:1px solid #2a3344;border-radius:8px;padding:6px 10px;cursor:pointer;text-decoration:none;font-size:13px}
#bar a.zip{background:#1f4d2e;border-color:#2f7a4a;color:#dff5e6}
#bar.bad{border-color:#7a2f2f}
#bar a.zip[data-busy]{cursor:progress}
</style></head><body>
<header><h1>片库（老农场） <span class="tag" id="tot">__TOT__</span></h1>
<div class="sub"><a href="/gallery?__AQ__">← 回成品画廊</a> ·
<a href="/library?__AQ__&res=1080p">只看 1080P</a> ·
<a href="/v1/library/manifest?__AQ__&res=1920x1080&format=csv">导出 1080P 清单 CSV</a></div>
<form class="bar" method="get" action="/library">
 __AUTHHIDDEN__
 <input name="q" value="__Q__" placeholder="文件名筛选，如 t- 或 9680">
 <select name="sort">
  <option value="newest" __S1__>最新优先</option><option value="oldest" __S2__>最旧优先</option>
  <option value="largest" __S3__>体积最大</option><option value="smallest" __S4__>体积最小</option>
 </select>
 <select name="res">__ROPTS__</select>
 <select name="limit">__LIMOPTS__</select>
 <button class="p" type="submit">筛选</button>
</form></header>
<div class="grid">__CARDS__</div>
<div class="pager">__PAGER__</div>
<script>
function probe(id, el){
  fetch('/v1/library/'+encodeURIComponent(id)+'/meta?__AQ__')
   .then(r=>r.json()).then(j=>{
     el.textContent = (j.resolution||'?') + ' · ' + (j.duration_s||'?') + 's · ' + (j.codec_name||'') + (j.is_1080p?' · 1080P':'');
   }).catch(e=>{el.textContent='探测失败'});
}
</script><div id="bar"><span>已选 <b id="pk">0</b> 条</span>
<a id="pkdl" class="zip" href="#">打包下载 zip</a>
<button id="pka">全选本页</button><button id="pkc">清空</button>
<span id="plim" style="color:#6b7686;font-size:12px">上限 100 条 / 1.5GB</span></div>
<script>
(function(){
 var AQ="__AQ__", ARCH="/v1/library/archive", LS="wz_pick_lib";
 var bar=document.getElementById('bar'), cnt=document.getElementById('pk'), dl=document.getElementById('pkdl'),
     lim=document.getElementById('plim');
 var boxes=[].slice.call(document.querySelectorAll('.pick'));
 var CAP_N=100, CAP_B=1500000000;
 function load(){
   try{
     var a=JSON.parse(sessionStorage.getItem(LS)||'[]'), m=new Map();
     a.forEach(function(p){ if(Array.isArray(p)) m.set(String(p[0]), +p[1]||0); else m.set(String(p),0); });
     return m;
   }catch(e){return new Map();}
 }
 function save(){try{sessionStorage.setItem(LS,JSON.stringify(Array.from(sel.entries()).slice(0,300)))}catch(e){}}
 var sel=load();
 function totalBytes(){var t=0;sel.forEach(function(v){t+=v||0});return t;}
 function over(){return sel.size>CAP_N||totalBytes()>CAP_B;}
 function paint(){
   var n=sel.size, b=totalBytes(), bad=over();
   cnt.textContent=n;
   if(lim){lim.textContent=(n?(b/1048576).toFixed(1)+' MB / ':'')+'1.5GB';}
   bar.style.display=n?'flex':'none';
   bar.classList.toggle('bad',bad);
   if(bad){
     dl.textContent='\u8d85\u51fa\u4e0a\u9650\uff08\u6700\u591a 100 \u6761 / 1.5GB\uff09';
     dl.href='#'; dl.dataset.blocked='1'; dl.style.opacity='.5';
   }else{
     dl.textContent='\u6253\u5305\u4e0b\u8f7d zip';
     dl.href=ARCH+'?ids='+encodeURIComponent(Array.from(sel.keys()).join(','))+'&limit=100'+(AQ?('&'+AQ):'');
     dl.dataset.blocked=''; dl.style.opacity='';
   }
 }
 var JKIND='library';
 dl.addEventListener('click',function(e){
   if(dl.dataset.blocked||dl.dataset.busy){e.preventDefault();return;}
   e.preventDefault();
   var ids=Array.from(sel.keys());
   if(!ids.length)return;
   dl.dataset.busy='1'; dl.style.opacity='.7'; dl.textContent='\u6253\u5305\u4e2d\u2026 0%';
   fetch('/v1/archive/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({kind:JKIND,ids:ids})})
   .then(function(r){return r.json().then(function(j){
     if(!r.ok)throw new Error((j.error&&j.error.message)||('HTTP '+r.status));return j;});})
   .then(function(j){
     var jid=j.job_id;
     function poll(){
       return fetch('/v1/archive/jobs/'+jid).then(function(r){
         return r.json().then(function(s){if(!r.ok)throw new Error('HTTP '+r.status);return s;});})
       .then(function(s){
         if(s.state==='ready'){
           dl.textContent='\u5f00\u59cb\u4e0b\u8f7d '+(s.bytes/1048576).toFixed(1)+' MB';
           location.href='/v1/archive/jobs/'+jid+'/file';
           return;
         }
         if(s.state==='failed')throw new Error(s.error||'build failed');
         dl.textContent='\u6253\u5305\u4e2d\u2026 '+s.pct+'% \u00b7 '+(s.built/1048576).toFixed(1)+' MB';
         return new Promise(function(res){setTimeout(res,700);}).then(poll);
       });
     }
     return poll();
   })
   .catch(function(err){dl.textContent='\u6253\u5305\u5931\u8d25\uff1a'+err.message;})
   .finally(function(){
     setTimeout(function(){delete dl.dataset.busy; dl.style.opacity=''; paint();},1500);
   });
 });
 function setBox(b,on){
   b.checked=on;
   var card=b.closest('.card');
   if(on){sel.set(b.value,+b.dataset.bytes||0);}else{sel.delete(b.value);}
   if(card){card.classList.toggle('sel',on);}
 }
 boxes.forEach(function(b){
   if(sel.has(b.value)){b.checked=true;var c=b.closest('.card');if(c)c.classList.add('sel');}
   b.addEventListener('change',function(){setBox(b,b.checked);save();paint();});
 });
 document.getElementById('pka').onclick=function(){
   boxes.forEach(function(b){
     if(b.checked)return;
     if(sel.size>=CAP_N)return;
     if(totalBytes()+(+b.dataset.bytes||0)>CAP_B)return;
     setBox(b,true);
   });
   save();paint();
 };
 document.getElementById('pkc').onclick=function(){boxes.forEach(function(b){setBox(b,false)});sel.clear();save();paint();};
 paint();
})();
</script>
</body></html>"""


GAL_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>WZ Gallery</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e8eaed}
 header{position:sticky;top:0;background:#151922;border-bottom:1px solid #232936;padding:12px 16px;z-index:5}
 h1{font-size:16px;margin:0 0 8px;font-weight:600}
 .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 input,select,button{background:#1a1d24;border:1px solid #2b303a;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px}
 button{cursor:pointer}button.p{background:#4f7cff;border:0}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;padding:16px}
 .card{background:#151922;border:1px solid #232936;border-radius:10px;overflow:hidden}
 .card img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#0b0d11}
 .meta{padding:8px 10px;font-size:12px;color:#9aa4b2;line-height:1.5}
 .p{color:#cfd6e4;font-size:12px;max-height:3.2em;overflow:hidden}
 .row{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
 .row a,.row button{font-size:11px;padding:4px 8px;border-radius:6px;text-decoration:none;color:#cfd6e4;background:#1d2330;border:1px solid #2b303a}
 .pager{display:flex;gap:10px;justify-content:center;padding:18px;color:#9aa4b2;font-size:13px}
 .pager a{color:#8fb0ff;text-decoration:none}
 .tag{background:#243043;color:#9fc3ff;border-radius:4px;padding:1px 5px;font-size:11px}
 .sub{font-size:12px;color:#9aa4b2;margin:4px 0 0}
 .sub a{color:#8fb0ff;text-decoration:none}
.pick{position:absolute;top:8px;left:8px;width:20px;height:20px;z-index:5;cursor:pointer}
.card{position:relative}
.card.sel{outline:2px solid #4c8dff;outline-offset:-2px}
#bar{position:fixed;left:50%;transform:translateX(-50%);bottom:14px;background:#182031;border:1px solid #2a3344;border-radius:12px;padding:10px 14px;display:none;gap:10px;align-items:center;z-index:30;box-shadow:0 6px 24px rgba(0,0,0,.5);font-size:14px}
#bar a,#bar button{background:#232b3d;color:#dfe3ea;border:1px solid #2a3344;border-radius:8px;padding:6px 10px;cursor:pointer;text-decoration:none;font-size:13px}
#bar a.zip{background:#1f4d2e;border-color:#2f7a4a;color:#dff5e6}
#bar a.zip[data-busy]{cursor:progress}
</style></head><body>
<header><h1>成品画廊 <span class="tag" id="tot">__TOT__</span> <a class="tag" href="/create" style="text-decoration:none">去创作 →</a></h1>
<div class="sub">__LEGACY__</div>
<form class="bar" method="get" action="/gallery">
 __AUTHHIDDEN__
 <input name="q" value="__Q__" placeholder="提示词关键词">
 <select name="status">__SOPTS__</select>
<select name="res">__ROPTS__</select>
 <select name="limit">__LIMOPTS__</select>
 <button class="p" type="submit">筛选</button>
</form></header>
<div class="grid">__CARDS__</div>
<div class="pager">__PAGER__</div><div id="bar"><span>已选 <b id="pk">0</b> 条</span>
<a id="pkdl" class="zip" href="#">打包下载 zip</a>
<button id="pka">全选本页</button><button id="pkc">清空</button>
<span id="plim" style="color:#6b7686;font-size:12px">上限 100 条 / 1.5GB</span></div>
<script>
(function(){
 var AQ="__AQ__", ARCH="/v1/videos/archive", LS="wz_pick";
 var bar=document.getElementById('bar'), cnt=document.getElementById('pk'), dl=document.getElementById('pkdl');
 var boxes=[].slice.call(document.querySelectorAll('.pick'));
 var CAP_N=100, CAP_B=1500000000;
 function load(){
   try{
     var a=JSON.parse(sessionStorage.getItem(LS)||'[]'), m=new Map();
     a.forEach(function(p){ if(Array.isArray(p)) m.set(String(p[0]), +p[1]||0); else m.set(String(p),0); });
     return m;
   }catch(e){return new Map();}
 }
 function save(){try{sessionStorage.setItem(LS,JSON.stringify(Array.from(sel.entries()).slice(0,300)))}catch(e){}}
 var sel=load();
 function totalBytes(){var t=0;sel.forEach(function(v){t+=v||0});return t;}
 function over(){return sel.size>CAP_N||totalBytes()>CAP_B;}
 function paint(){
   var n=sel.size, b=totalBytes(), bad=over();
   cnt.textContent=n;
   if(lim){lim.textContent=(n?(b/1048576).toFixed(1)+' MB / ':'')+'1.5GB';}
   bar.style.display=n?'flex':'none';
   bar.classList.toggle('bad',bad);
   if(bad){
     dl.textContent='\u8d85\u51fa\u4e0a\u9650\uff08\u6700\u591a 100 \u6761 / 1.5GB\uff09';
     dl.href='#'; dl.dataset.blocked='1'; dl.style.opacity='.5';
   }else{
     dl.textContent='\u6253\u5305\u4e0b\u8f7d zip';
     dl.href=ARCH+'?ids='+encodeURIComponent(Array.from(sel.keys()).join(','))+'&limit=100'+(AQ?('&'+AQ):'');
     dl.dataset.blocked=''; dl.style.opacity='';
   }
 }
var JKIND='videos';;
 dl.addEventListener('click',function(e){
   if(dl.dataset.blocked||dl.dataset.busy){e.preventDefault();return;}
   e.preventDefault();
   var ids=Array.from(sel.keys());
   if(!ids.length)return;
   dl.dataset.busy='1'; dl.style.opacity='.7'; dl.textContent='\u6253\u5305\u4e2d\u2026 0%';
   fetch('/v1/archive/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({kind:JKIND,ids:ids})})
   .then(function(r){return r.json().then(function(j){
     if(!r.ok)throw new Error((j.error&&j.error.message)||('HTTP '+r.status));return j;});})
   .then(function(j){
     var jid=j.job_id;
     function poll(){
       return fetch('/v1/archive/jobs/'+jid).then(function(r){
         return r.json().then(function(s){if(!r.ok)throw new Error('HTTP '+r.status);return s;});})
       .then(function(s){
         if(s.state==='ready'){
           dl.textContent='\u5f00\u59cb\u4e0b\u8f7d '+(s.bytes/1048576).toFixed(1)+' MB';
           location.href='/v1/archive/jobs/'+jid+'/file';
           return;
         }
         if(s.state==='failed')throw new Error(s.error||'build failed');
         dl.textContent='\u6253\u5305\u4e2d\u2026 '+s.pct+'% \u00b7 '+(s.built/1048576).toFixed(1)+' MB';
         return new Promise(function(res){setTimeout(res,700);}).then(poll);
       });
     }
     return poll();
   })
   .catch(function(err){dl.textContent='\u6253\u5305\u5931\u8d25\uff1a'+err.message;})
   .finally(function(){
     setTimeout(function(){delete dl.dataset.busy; dl.style.opacity=''; paint();},1500);
   });
 });
 function setBox(b,on){
   b.checked=on;
   var card=b.closest('.card');
   if(on){sel.set(b.value,+b.dataset.bytes||0);}else{sel.delete(b.value);}
   if(card){card.classList.toggle('sel',on);}
 }
 boxes.forEach(function(b){
   if(sel.has(b.value)){b.checked=true;var c=b.closest('.card');if(c)c.classList.add('sel');}
   b.addEventListener('change',function(){setBox(b,b.checked);save();paint();});
 });
 document.getElementById('pka').onclick=function(){
   boxes.forEach(function(b){
     if(b.checked)return;
     if(sel.size>=CAP_N)return;
     if(totalBytes()+(+b.dataset.bytes||0)>CAP_B)return;
     setBox(b,true);
   });
   save();paint();
 };
 document.getElementById('pkc').onclick=function(){boxes.forEach(function(b){setBox(b,false)});sel.clear();save();paint();};
 paint();
})();
</script>
</body></html>"""


@app.get("/gallery", response_class=HTMLResponse)
def gallery_page(request: Request, key: str = "", q: str = "", status: str = "succeeded",
                 res: str = "", offset: int = 0, limit: int = 24, browse: str = ""):
    admin = viewer_ok(request, browse)
    if browse and not is_browse(request, browse):
        raise HTTPException(401, "invalid/expired browse token")
    if not admin:
        if not key or not find_key(key):
            raise HTTPException(401, "missing/invalid ?key=")
    AQ, AUTHHIDDEN = _page_auth(request, key, browse)
    KQ = AQ or ("key=" + urllib.parse.quote(key))
    lim = max(6, min(int(limit), 96))
    off = max(0, int(offset))
    where, args = [], []
    if status in ("succeeded", "failed", "running", "queued", "canceled"):
        where.append("status=?"); args.append(status)
    if q:
        where.append("prompt like ?"); args.append("%" + q + "%")
    if res in ("1080P", "768P", "2K"):
        where.append("resolution=?"); args.append(res)
    if not admin and key:
        where.append("key_id=?"); args.append(find_key(key)["id"])
    clause = (" where " + " and ".join(where)) if where else ""
    c = db()
    total = c.execute("select count(*) n from tasks" + clause, args).fetchone()["n"]
    rows = c.execute("select id,status,prompt,resolution,duration,ratio,created,local_path,err "
                     "from tasks" + clause + " order by created desc limit ? offset ?",
                     args + [lim, off]).fetchall()
    c.close()
    cards = []
    for r in rows:
        tid = r["id"]
        ready = bool(r["local_path"] and os.path.isfile(r["local_path"]))
        mb = (os.path.getsize(r["local_path"]) / 1e6) if ready else 0
        fb = int(os.path.getsize(r["local_path"])) if ready else 0
        img = ('<img loading="lazy" src="/v1/videos/%s/thumb?%s" alt="">' % (tid, KQ)
               if ready else '<div style="aspect-ratio:16/9;display:flex;align-items:center;'
                             'justify-content:center;color:#5c6675;font-size:12px">%s</div>' % r["status"])
        cards.append(
            '<div class="card"><input type="checkbox" class="pick" value="%s" data-bytes="%d">%s<div class="meta"><div class="p">%s</div>'
            '<div>%s · %ss · %.2f MB</div><div>%s</div>'
            '<div class="row"><a href="/watch/%s?%s">播放</a>'
            '<a href="/v1/videos/%s/meta?%s" target="_blank">meta</a>'
            '<a href="/v1/videos/%s/content?%s" target="_blank">mp4</a>'
            '<a href="/v1/videos/%s/events?%s" target="_blank">SSE</a></div></div></div>'
            % (tid, fb, img, (r["prompt"] or "")[:140].replace("<", "&lt;"), r["resolution"], r["duration"], mb,
               time.strftime("%m-%d %H:%M", time.localtime(r["created"])),
               tid, KQ, tid, KQ, tid, KQ, tid, KQ))
    def link(o):
        return ("/gallery?%s&q=%s&status=%s&res=%s&limit=%d&offset=%d"
                % (urllib.parse.quote(key), urllib.parse.quote(q), status,
                   urllib.parse.quote(res), lim, max(0, o)))
    nav = []
    if off > 0:
        nav.append('<a href="%s">← 上一页</a>' % link(off - lim))
    nav.append("%d–%d / %d" % (off + 1, min(off + lim, total), total))
    if off + lim < total:
        nav.append('<a href="%s">下一页 →</a>' % link(off + lim))
    sel = lambda v: " selected" if status == v else ""
    copts = "".join('<option value="%s"%s>%s</option>' % (v, sel(v), v)
                    for v in ("succeeded", "failed", "running", "queued", "canceled", "all"))
    lopts = "".join('<option value="%d"%s>%d / 页</option>' % (n, " selected" if n == lim else "", n)
                    for n in (12, 24, 48, 96))
    ropts = '<option value="">全部清晰度</option>' + "".join(
        '<option value="%s"%s>%s</option>' % (v, " selected" if res == v else "", v)
        for v in ("1080P", "768P", "2K"))
    fs = _farm_summary()
    if fs and fs.get("files"):
        legacy_note = ('老农场（直连上游老片库）：%s 支 · 1080P '
                       '<a href="/library?%s&res=1080p">%s 支 / %s GB</a> · 合计 %s GB'
                       % (fs["files"], urllib.parse.quote(key), fs.get("n1080"),
                          fs.get("gb1080"), fs.get("gb")))
    else:
        legacy_note = ('老农场：<a href="/library?%s">打开片库</a>'
                       % urllib.parse.quote(key))
    html = (GAL_HTML.replace("__AQ__", AQ).replace("__AUTHHIDDEN__", AUTHHIDDEN).replace("__Q__", q)
            .replace("__LEGACY__", legacy_note)
            .replace("__TOT__", str(total)).replace("__CARDS__", "".join(cards) or
                                                    '<div class="meta">暂无数据</div>')
            .replace("__PAGER__", " ".join(nav)).replace("__SOPTS__", copts)
            .replace("__ROPTS__", ropts).replace("__LIMOPTS__", lopts))
    return HTMLResponse(html)


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request, key: str = "", q: str = "", offset: int = 0,
                 limit: int = 24, sort: str = "newest", res: str = "", browse: str = ""):
    AQ, AUTHHIDDEN = _page_auth(request, key, browse)
    if not viewer_ok(request, browse):
        if not key:
            raise HTTPException(401, "missing ?key=")
        if not find_key(key):
            raise HTTPException(401, "invalid api key")
    try:
        files = _library_index()
    except FileNotFoundError:
        raise HTTPException(404, "library dir missing")
    if q:
        ql = q.lower()
        files = [f for f in files if ql in f["name"].lower()]
    flt = _parse_res_filter(res)
    rmap = _library_res_map() if flt else (_LIB_RES["map"] or None)
    if flt:
        mode, val = flt
        def _keep(f):
            wh = (rmap or {}).get(f["id"])
            if not wh:
                return False
            if mode == "wh":
                return wh == val
            return wh[1] >= max(0, val or 0)
        files = [f for f in files if _keep(f)]
    if sort == "oldest":
        files = sorted(files, key=lambda x: x["mtime"])
    elif sort == "largest":
        files = sorted(files, key=lambda x: -x["bytes"])
    elif sort == "smallest":
        files = sorted(files, key=lambda x: x["bytes"])
    lim = max(6, min(int(limit), 96))
    off = max(0, int(offset))
    page = files[off:off + lim]
    cards = []
    for f in page:
        fid = f["id"]
        cards.append(
            '<div class="card"><input type="checkbox" class="pick" value="%s" data-bytes="%d"><a href="/watch/lib/%s?%s">'
            '<img loading="lazy" src="/v1/library/%s/thumb?%s" alt=""></a>'
            '<div class="meta"><div class="name">%s</div>'
            '<div>%.2f MB · %s · %.1f 天前</div>'
            '<div class="row"><button onclick="probe(&#39;%s&#39;,this)">查分辨率</button>'
            '<a href="/watch/lib/%s?%s">播放</a>'
            '<a href="/v1/library/%s/meta?%s" target="_blank">JSON</a></div></div></div>'
            % (fid, int(f["bytes"]), fid, AQ, fid, AQ, f["name"], f["bytes"] / 1e6,
               time.strftime("%m-%d %H:%M", time.localtime(f["mtime"])), f.get("age_days") or 0,
               fid, fid, AQ, fid, AQ))
    total = len(files)
    tot_text = "%d 条" % total
    if flt:
        tot_text = "%d 条 · %.2f GB · %s" % (total, sum(f["bytes"] for f in files) / 2**30, res)
    limopts = "".join('<option value="%d"%s>%d / 页</option>'
                      % (n, " selected" if n == lim else "", n) for n in (12, 24, 48, 96))
    ropts = '<option value="">全部清晰度</option>' + "".join(
        '<option value="%s"%s>%s</option>' % (v, " selected" if res == v else "", v)
        for v in ("1080p", "720p", "480p"))
    def link(o):
        return ("/library?%s&q=%s&sort=%s&res=%s&limit=%d&offset=%d"
                % (AQ, urllib.parse.quote(q), sort,
                   urllib.parse.quote(res), lim, max(0, o)))
    nav = []
    if off > 0:
        nav.append('<a href="%s">← 上一页</a>' % link(off - lim))
    nav.append("%d–%d / %d" % (off + 1, min(off + lim, total), total))
    if off + lim < total:
        nav.append('<a href="%s">下一页 →</a>' % link(off + lim))
    html = (LIB_HTML.replace("__AQ__", AQ).replace("__AUTHHIDDEN__", AUTHHIDDEN).replace("__Q__", q)
            .replace("__CARDS__", "".join(cards) or ('<div class="meta" style="grid-column:1/-1;padding:28px;text-align:center;line-height:1.9">片库已清空（老农场 24,494 支样片已按指令删除）<br><a href="/create">去创作台用提示词生成新视频 &rarr;</a></div>'))
            .replace("__PAGER__", (" ".join(nav) if total else "")).replace("__LIMOPTS__", limopts)
            .replace("__ROPTS__", ropts).replace("__TOT__", tot_text)
            .replace("__S1__", "selected" if sort == "newest" else "")
            .replace("__S2__", "selected" if sort == "oldest" else "")
            .replace("__S3__", "selected" if sort == "largest" else "")
            .replace("__S4__", "selected" if sort == "smallest" else ""))
    return HTMLResponse(html)


@app.api_route("/v1/library/{fid}/content", methods=["GET", "HEAD"])
def library_content(fid: str, request: Request):
    if not viewer_ok(request):
        require_key_light(request)
    name = os.path.basename(fid)
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    path = os.path.join(LEGACY_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="video/mp4", filename=name)

@app.get("/v1/library/archive")
def library_archive(request: Request, ids: str = "", limit: int = 100):
    """R700: zip a hand-picked set of legacy clips (names, no .mp4 needed)."""
    if not viewer_ok(request):
        require_key_light(request)
    lim = max(1, min(int(limit), 100))
    want = [os.path.basename(x.strip()) for x in (ids or "").split(",") if x.strip()][:lim]
    if not want:
        raise HTTPException(400, "ids is empty")
    files, total = [], 0
    for name in want:
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
        p = os.path.join(LEGACY_DIR, name)
        if not os.path.isfile(p):
            continue
        total += os.path.getsize(p)
        files.append((p, name))
    if not files:
        raise HTTPException(404, "no legacy clips matched")
    if total > 1_500_000_000:
        raise HTTPException(413, "selection is %.2f GB; pick fewer clips" % (total / 1e9))
    if not _ARCHIVE_SEM.acquire(blocking=False):
        raise HTTPException(429, "another archive is being built; retry shortly",
                            headers={"Retry-After": "30"})
    try:
        tmp = tempfile.NamedTemporaryFile(prefix="wzmm_libzip_", suffix=".zip", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for p, arc in files:
                z.write(p, arcname=arc)
    except Exception:
        _ARCHIVE_SEM.release()
        raise
    zname = "wzlib_%d_%s.zip" % (len(files), time.strftime("%H%M%S"))

    def _cleanup():
        try:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
        except OSError:
            pass
        _ARCHIVE_SEM.release()          # R700 fix: release even on success

    return FileResponse(tmp.name, media_type="application/zip", filename=zname,
                        background=BackgroundTask(_cleanup))


def _enc_cursor(created, tid):
    raw = ("%r|%s" % (float(created), tid)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _dec_cursor(cur):
    try:
        pad = "=" * (-len(cur) % 4)
        ts, tid = base64.urlsafe_b64decode(cur + pad).decode("utf-8").split("|", 1)
        return float(ts), tid
    except Exception:
        return None


@app.get("/v1/videos/generations")
def list_videos(request: Request, limit: int = 20, status: str = "", cursor: str = ""):
    """List this key's tasks, newest first.

    Pass `cursor` (from `next_cursor`) to page; `limit` is capped at 200.
    """
    # R601: listing is read-only and exempt from rpm
    key = require_key_light(request)
    lim = max(1, min(int(limit), 200))
    where = ["key_id=?"]
    args = [key["id"]]
    if status:
        where.append("status=?")
        args.append(status)
    dec = _dec_cursor(cursor) if cursor else None
    if cursor and not dec:
        raise HTTPException(400, "bad cursor")
    if dec:
        ts, tid = dec
        where.append("(created < ? or (created = ? and id < ?))")
        args.extend([ts, ts, tid])
    sql = ("select id,status,model,resolution,created,batch_id,local_path,released_at from tasks where "
           + " and ".join(where) + " order by created desc, id desc limit ?")
    c = db()
    rows = [dict(r) for r in c.execute(sql, args + [lim + 1]).fetchall()]
    c.close()
    for r in rows:                      # R634: clients need to know what is still downloadable
        lp = r.pop("local_path", None)
        r["local"] = bool(lp) and os.path.isfile(lp)
        r["released"] = bool(r.pop("released_at", None))
    has_more = len(rows) > lim
    rows = rows[:lim]
    out = {"object": "list", "data": rows, "has_more": has_more}
    if has_more and rows:
        out["next_cursor"] = _enc_cursor(rows[-1]["created"], rows[-1]["id"])
    return out

@app.get("/v1/usage")
def my_usage(request: Request, days: int = 7):
    key = require_key(request)
    days = max(1, min(int(days), 90))
    since = time.time() - days * 86400
    c = db()
    rows = c.execute("select strftime('%Y-%m-%d', created, 'unixepoch', '+8 hours') d, status, count(*) n "
                     "from tasks where key_id=? and created>=? group by d, status order by d",
                     (key["id"], since)).fetchall()
    tot = c.execute("select count(*) n, sum(case when status='succeeded' then 1 else 0 end) ok "
                    "from tasks where key_id=? and created>=?", (key["id"], since)).fetchone()
    paths = c.execute("select local_path from tasks where key_id=? and created>=? and status='succeeded'",
                      (key["id"], since)).fetchall()
    c.close()
    nbytes, nfiles = 0, 0
    for r in paths:
        p = r["local_path"]
        try:
            if p and os.path.isfile(p):
                nbytes += os.path.getsize(p); nfiles += 1
        except OSError:
            pass
    return {"key": {"id": key["id"], "name": key["name"], "rpm": key["rpm"], "quota": key["quota"],
                    "used": key["used"], "daily_quota": key["daily_quota"]},
            "days": days, "since": int(since),
            "totals": {"tasks": tot["n"] or 0, "succeeded": tot["ok"] or 0,
                       "videos_on_disk": nfiles, "bytes": nbytes},
            "series": [dict(r) for r in rows]}


@app.post("/v1/tools/call")
async def tools_call(request: Request):
    require_key(request)
    body = await request.json()
    name = body.get("tool") or body.get("name")
    args = body.get("arguments") or body.get("args") or {}
    m = {"matrix_submit_video_generation": A.SUBMIT_TOOL,
         "matrix_query_video_generation": A.QUERY_TOOL}
    if name in m:
        tool = m[name]
    elif isinstance(name, str) and name.startswith("matrix_"):
        # pass-through for the whole connector surface (image / tts / music / understand / asset url ...)
        tool = ("matrix", name, "connector__matrix__" + name)
    else:
        raise HTTPException(400, "unknown tool; pass matrix_<tool_name> (see GET /v1/tools)")
    if tool == A.SUBMIT_TOOL:
        _disk_admit()
    acc = POOL.pick()
    try:
        r = A.tool_call(sess(), acc["token"], str(acc["ruid"]), A.profile_for(acc["email"]), tool, args, timeout=300)
    finally:
        POOL.release(acc["email"])
    return {"tool": name, "account": acc["email"], "result": r}

TOOL_NAMES = ["matrix_submit_video_generation", "matrix_query_video_generation", "matrix_generate_image",
              "matrix_synthesize_speech", "matrix_batch_text_to_audio", "matrix_batch_text_to_music",
              "matrix_listen_audio", "matrix_get_voice_list", "matrix_get_asset_url", "matrix_upload_temp_url",
              "matrix_describe_images", "matrix_search_images", "matrix_reverse_image_search",
              "matrix_videos_understand", "matrix_audios_understand"]

@app.get("/v1/tools")
def tools_list(request: Request):
    require_key(request)
    return {"object": "list", "passthrough": "POST /v1/tools/call {tool,arguments} accepts any of these",
            "names": TOOL_NAMES, "data": [
        {"name": "matrix_submit_video_generation", "provider": "matrix",
         "arguments": {"prompt": "string", "model": "MiniMax-Hailuo-2.3",
                       "resolution": "1080P", "duration": 6, "ratio": "16:9"}},
        {"name": "matrix_query_video_generation", "provider": "matrix",
         "arguments": {"task_id": "string", "model": "MiniMax-Hailuo-2.3"}},
    ]}

# ------------------------------------------------------------ webhooks ----
@app.get("/v1/webhook/secret")
def get_webhook_secret(request: Request):
    key = require_key(request)
    c = db()
    row = c.execute("select webhook_secret from keys where id=?", (key["id"],)).fetchone()
    c.close()
    own = row["webhook_secret"] if row is not None else None
    return {"present": bool(own), "secret_masked": _mask_secret(own or ""),
            "effective": "key" if own else "global",
            "algorithm": "HMAC-SHA256 over '<timestamp>.<raw_body>'",
            "headers": ["X-WZ-Signature", "X-WZ-Timestamp", "X-WZ-Delivery", "X-WZ-Event", "X-WZ-Attempt"]}

@app.post("/v1/webhook/secret")
async def rotate_webhook_secret(request: Request):
    key = require_key(request)
    body = {}
    try:
        if request.headers.get("content-length") not in (None, "0"):
            body = await request.json()
    except Exception:
        body = {}
    given = (body.get("secret") or "").strip()
    if given and (len(given) < 16 or len(given) > 200):
        raise HTTPException(400, "secret must be 16..200 chars")
    sec = given or ("whsec_" + secrets.token_hex(24))
    c = db()
    with _db_lock:
        c.execute("update keys set webhook_secret=? where id=?", (sec, key["id"]))
        c.commit(); c.close()
    return {"ok": True, "secret": sec, "note": "store it now; GET only returns a masked value"}

@app.delete("/v1/webhook/secret")
def delete_webhook_secret(request: Request):
    key = require_key(request)
    c = db()
    with _db_lock:
        c.execute("update keys set webhook_secret=null where id=?", (key["id"],))
        c.commit(); c.close()
    return {"ok": True, "effective": "global"}

@app.post("/v1/webhook/test")
async def webhook_self_test(request: Request):
    """Fire one signed sample event at your endpoint so you can verify the signature."""
    key = require_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid json")
    url = (body.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "url must be http(s)")
    secret = _webhook_secret_for(key["id"])
    payload = {"id": "vid_selftest_" + secrets.token_hex(6), "object": "video.generation",
               "status": "succeeded", "test": True, "created": int(time.time()),
               "model": DEFAULT_MODEL, "resolution": "1080P", "duration": 6, "ratio": "16:9",
               "url": None, "error": None, "batch_id": None}
    delivery = "dlv_test_" + secrets.token_hex(8)
    ts = int(time.time())
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    res = _send_webhook(url, payload, "video.test", delivery, secret, attempts=1)
    return {"ok": res.startswith("http 2"), "result": res, "delivery": delivery,
            "timestamp": ts, "signature": "t=%d,v1=%s" % (ts, _sign(secret, ts, raw))}

# ---------------------------------------------------------------- admin ----
@app.get("/admin/webhook")
def admin_webhook_info(request: Request):
    require_admin(request)
    sec = _load_webhook_secret()
    c = db()
    n = c.execute("select count(*) n from keys where webhook_secret is not null").fetchone()["n"]
    c.close()
    return {"configured": bool(sec), "secret_masked": _mask_secret(sec), "file": WEBHOOK_SECRET_PATH,
            "per_key_secrets": n, "algorithm": "HMAC-SHA256 over '<timestamp>.<raw_body>'"}

@app.get("/admin/webhook/queue")
def admin_webhook_queue(request: Request):
    require_admin(request)
    now = time.time()
    with HOOKQ_LOCK:
        items = [{"delivery": it.get("delivery"), "event": it.get("event"),
                  "url": str(it.get("url"))[:120], "tries": it.get("tries"),
                  "next_in_s": max(0, int(float(it.get("next_ts") or 0) - now)),
                  "last": str(it.get("last"))[:80]} for it in HOOKQ]
    return {"object": "list", "count": len(items), "max_tries": HOOK_MAX_TRIES,
            "backoff_s": HOOK_BACKOFF, "data": items[:50]}


@app.post("/admin/webhook/rotate")
async def admin_webhook_rotate(request: Request):
    require_admin(request)
    body = {}
    try:
        if request.headers.get("content-length") not in (None, "0"):
            body = await request.json()
    except Exception:
        body = {}
    given = (body.get("secret") or "").strip()
    sec = given or ("whsec_" + secrets.token_hex(24))
    try:
        fd = os.open(WEBHOOK_SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sec + "\n")
    except Exception as e:
        raise HTTPException(500, "cannot persist secret: %r" % (e,))
    _WEBHOOK_SECRET[0] = sec
    return {"ok": True, "secret": sec}

@app.get("/admin/checkin")
def admin_checkin_status(request: Request, include_results: int = 0):
    require_admin(request)
    if not os.path.isfile(CHECKIN_LAST):
        return {"ok": False, "error": "no check-in run yet", "path": CHECKIN_LAST}
    with open(CHECKIN_LAST, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not include_results:
        d.pop("results", None)
    return d


@app.post("/admin/checkin")
def admin_checkin(request: Request, dry_run: int = 0, limit: int = 0, workers: int = 8):
    require_admin(request)
    py = "/opt/wzmm/venv/bin/python"
    if not os.path.isfile(py):
        py = sys.executable
    cmd = [py, CHECKIN_SCRIPT, "--workers", str(max(1, min(int(workers), 24))),
           "--json-out", CHECKIN_LAST]
    if limit:
        cmd += ["--limit", str(max(1, min(int(limit), 120)))]
    if not dry_run:
        cmd.append("--write")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        raise HTTPException(500, "checkin failed to start: %s" % e)
    data = {}
    try:
        with open(CHECKIN_LAST, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("results", None)
    except Exception:
        pass
    if not dry_run and p.returncode == 0:
        try:
            POOL.reload_meta()
        except Exception:
            pass
    return {"ok": p.returncode == 0, "exit": p.returncode,
            "elapsed_s": round(time.time() - t0, 2), "dry_run": bool(dry_run),
            "stdout": (p.stdout or "")[-1200:], "stderr": (p.stderr or "")[-600:],
            "summary": data}


@app.post("/admin/keys")
async def admin_create_key(request: Request):
    require_admin(request)
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    name = body.get("name") or "default"
    rpm = int(body.get("rpm") or 60)
    quota = int(body.get("quota") or 0)
    mc = int(body.get("max_concurrent") or 32)
    dq = int(body.get("daily_quota") or 0)
    kid = "key_" + secrets.token_hex(8); k = _rand_key()
    c = db()
    with _db_lock:
        c.execute("insert into keys(id,key,name,created,rpm,quota,max_concurrent,daily_quota) "
                  "values(?,?,?,?,?,?,?,?)", (kid, k, name, time.time(), rpm, quota, mc, dq))
        c.commit(); c.close()
    return {"id": kid, "key": k, "name": name, "rpm": rpm, "quota": quota,
            "max_concurrent": mc, "daily_quota": dq}

@app.patch("/admin/keys/{kid}")
async def admin_update_key(kid: str, request: Request):
    """R591: update limits/name/disabled in place; the sk-wz string never changes."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid json")
    fields = {}
    if "name" in body:
        fields["name"] = str(body["name"])[:80]
    for k in ("rpm", "max_concurrent", "quota", "daily_quota"):
        if k in body:
            try:
                v = int(body[k])
            except Exception:
                raise HTTPException(400, "%s must be a non-negative integer" % k)
            if v < 0:
                raise HTTPException(400, "%s must be a non-negative integer" % k)
            fields[k] = v
    if "disabled" in body:
        fields["disabled"] = 1 if body["disabled"] else 0
    if not fields:
        raise HTTPException(400, "nothing to update")
    c = db()
    if not c.execute("select 1 from keys where id=?", (kid,)).fetchone():
        c.close()
        raise HTTPException(404, "key not found")
    sets = ", ".join("%s=?" % k for k in fields)
    with _db_lock:
        c.execute("update keys set %s where id=?" % sets, list(fields.values()) + [kid])
        c.commit()
    row = c.execute("select id,key,name,rpm,quota,max_concurrent,daily_quota,used,disabled "
                    "from keys where id=?", (kid,)).fetchone()
    c.close()
    return {"ok": True, "key": dict(row)}

@app.get("/admin/keys")
def admin_list_keys(request: Request):
    require_admin(request)
    c = db()
    rows = c.execute("select id,name,key,created,rpm,quota,used,last_used,disabled from keys order by created desc").fetchall()
    c.close()
    return {"object": "list", "data": [dict(r) for r in rows]}

@app.get("/admin/keys/stale")
def admin_stale_keys(request: Request):
    """R622: disabled keys, split into deletable (no tasks) and keepable."""
    require_admin(request)
    c = db()
    rows = c.execute("select k.id, k.name, k.created, k.used, "
                     "(select count(*) from tasks t where t.key_id=k.id) n "
                     "from keys k where k.disabled=1 order by k.created").fetchall()
    c.close()
    items = [{"id": r["id"], "name": r["name"], "used": int(r["used"] or 0),
              "age_h": round((time.time() - float(r["created"])) / 3600.0, 1),
              "tasks": int(r["n"])} for r in rows]
    pure = [i for i in items if i["tasks"] == 0]
    return {"ok": True, "disabled_total": len(items), "deletable": len(pure),
            "with_tasks": len(items) - len(pure), "data": pure[:200],
            "hint": "DELETE /admin/keys/{id}?hard=1 removes a key that has no tasks"}


@app.get("/admin/browse-token")
def admin_browse_token(request: Request, ttl_h: float = 168.0, note: str = ""):
    """R696/R697: mint a read-only gallery/library token (default 7 days, max 30).

    Each token is stamped with the current `kid` and logged to browse_state.json.
    """
    require_admin(request)
    t = make_browse_token(ttl_h, note=note, request=request)
    q = "browse=" + urllib.parse.quote(t["token"])
    t["urls"] = {"gallery": "/gallery?" + q, "library": "/library?" + q,
                 "library_1080p": "/library?" + q + "&res=1080p",
                 "usage": "append ?browse=<token> to /v1/videos/<id>/content|thumb|meta|status"}
    t["note"] = "read-only: cannot create/cancel/pin/release/admin"
    return t


@app.get("/admin/browse-audit")
def admin_browse_audit(request: Request, limit: int = 50):
    """R697: what was minted, when, from where, and which tokens are now dead."""
    require_admin(request)
    st = _browse_state()
    now = time.time()
    lim = max(1, min(int(limit), 200))
    issued = list(reversed(st.get("issued") or []))[:lim]
    live = [r for r in st.get("issued") or []
            if int(r.get("kid") or 1) >= int(st["min_kid"]) and float(r.get("exp") or 0) > now]
    return {"kid": st["kid"], "min_kid": st["min_kid"], "rotated_at": st.get("rotated_at") or 0,
            "revoked_kids": list(range(1, int(st["min_kid"]))),
            "live_count": len(live), "issued_total": len(st.get("issued") or []),
            "data": issued}


@app.post("/admin/browse-rotate")
def admin_browse_rotate(request: Request, note: str = ""):
    """R697: bump kid -> every previously issued browse link dies immediately."""
    require_admin(request)
    with _BROWSE_LOCK:
        st = _browse_load()
        old = int(st["kid"])
        st["kid"] = old + 1
        st["min_kid"] = old + 1
        st["rotated_at"] = int(time.time())
        st["issued"] = (st.get("issued") or [])[-199:]
        _browse_save(st)
    return {"ok": True, "old_kid": old, "kid": old + 1, "min_kid": old + 1,
            "note": (note or "")[:120],
            "effect": "all tokens with kid <= %d now return 401; mint a new one" % old}


@app.post("/admin/keys/gc")
def admin_keys_gc(request: Request, names: str = "", dry_run: int = 1, max_age_h: float = 0):
    """R687: drop test-harness keys that own no tasks. dry_run defaults to 1."""
    require_admin(request)
    want = [x.strip() for x in (names or "selftest-scope,selftest-dq,matrix-test").split(",") if x.strip()]
    if not want:
        raise HTTPException(400, "names required")
    ph = ",".join("?" * len(want))
    c = db()
    rows = c.execute("select k.id, k.name, k.created, k.used, "
                     "(select count(*) from tasks t where t.key_id=k.id) n "
                     "from keys k where k.name in (%s) order by k.created" % ph, want).fetchall()
    now = time.time()
    kill, keep = [], []
    for r in rows:
        age_h = (now - float(r["created"] or 0)) / 3600.0
        if int(r["n"] or 0) > 0 or (max_age_h and age_h < max_age_h):
            keep.append({"id": r["id"], "name": r["name"], "age_h": round(age_h, 1), "tasks": int(r["n"] or 0)})
        else:
            kill.append({"id": r["id"], "name": r["name"], "age_h": round(age_h, 1), "used": int(r["used"] or 0)})
    if not dry_run and (max_age_h <= 0 or max_age_h < 0.5):
        c.close()
        raise HTTPException(400, "refusing to delete: pass max_age_h>=0.5 so keys created in the last 30 min are protected")
    deleted = 0
    if not dry_run and kill:
        with _db_lock:
            c.executemany("delete from keys where id=?", [(k["id"],) for k in kill])
            c.commit()
            deleted = len(kill)
    c.close()
    return {"ok": True, "dry_run": bool(dry_run), "names": want,
            "matched": len(rows), "deletable": len(kill), "kept": len(keep),
            "deleted": deleted, "data": kill[:300], "kept_data": keep[:50],
            "hint": "POST /admin/keys/gc?dry_run=0 to actually delete"}


@app.delete("/admin/keys/{kid}")
def admin_del_key(kid: str, request: Request, hard: int = 0):
    """R622: default disables; ?hard=1 deletes the row (refused if it owns tasks)."""
    require_admin(request)
    c = db()
    if hard:
        n = c.execute("select count(*) n from tasks where key_id=?", (kid,)).fetchone()["n"]
        if n:
            c.close()
            raise HTTPException(409, "key owns %d task(s); disable instead of delete" % n)
        with _db_lock:
            cur = c.execute("delete from keys where id=?", (kid,))
            c.commit(); c.close()
        if cur.rowcount == 0:
            raise HTTPException(404, "key not found")
        return {"ok": True, "id": kid, "deleted": True}
    with _db_lock:
        c.execute("update keys set disabled=1 where id=?", (kid,))
        c.commit(); c.close()
    return {"ok": True, "id": kid, "disabled": True}


ADMIN_UI_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>WZ Key Console</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e8eaed}
 .wrap{max-width:1080px;margin:0 auto;padding:24px}
 h1{font-size:18px;margin:0 0 6px}
 h2{font-size:14px;margin:0 0 12px;color:#cfd6e4}
 .sub{font-size:12px;color:#9aa4b2;line-height:1.6;margin:0 0 14px}
 .card{background:#151922;border:1px solid #232936;border-radius:10px;padding:16px;margin:0 0 16px}
 .grid{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 12px}
 label{font-size:12px;color:#9aa4b2;display:flex;flex-direction:column;gap:4px}
 input{background:#1a1d24;border:1px solid #2b303a;color:#e8eaed;border-radius:8px;padding:7px 9px;font-size:13px;width:130px}
 button{background:#1d2330;border:1px solid #2b303a;color:#cfd6e4;border-radius:8px;padding:7px 11px;font-size:12px;cursor:pointer}
 button.p{background:#4f7cff;border:0;color:#fff}
 button.danger{color:#ffb4b4;border-color:#5a2a2a}
 button.mini{padding:3px 7px;font-size:11px}
 table{width:100%;border-collapse:collapse;font-size:12px}
 th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #202634;vertical-align:middle}
 th{color:#8f9aab;font-weight:500}
 .mono,code{font-family:ui-monospace,Consolas,monospace;font-size:11px;word-break:break-all}
 .tag{background:#243043;color:#9fc3ff;border-radius:4px;padding:1px 6px;font-size:11px;margin-left:6px}
 .on{color:#7ee2a8}.off{color:#ff9d9d}
 .fresh{margin-top:10px;font-size:12px;color:#9fc3ff;word-break:break-all}
 .msg{position:fixed;right:18px;bottom:18px;padding:9px 13px;border-radius:8px;font-size:12px;background:#1d2330;border:1px solid #2b303a;opacity:0;transition:opacity .2s}
 .msg.good{opacity:1;color:#7ee2a8}.msg.bad{opacity:1;color:#ff9d9d;border-color:#5a2a2a}
</style></head><body>
<div class="wrap">
 <h1>王炸 · Key 控制台</h1>
 <p class="sub">创建 key、改限速/并发、停用或硬删。页面里的 token 只用于本机 API 调用，不会发往别处。</p>
 <section class="card">
  <h2>创建 Key</h2>
  <div class="grid">
   <label>名称<input id="name" value="grandpa"></label>
   <label>rpm<input id="rpm" type="number" value="600"></label>
   <label>max_concurrent<input id="mc" type="number" value="64"></label>
   <label>quota（0=不限）<input id="quota" type="number" value="0"></label>
   <label>daily_quota（0=不限）<input id="dq" type="number" value="0"></label>
  </div>
  <button class="p" onclick="createKey()">创建</button>
  <div id="fresh" class="fresh"></div>
 </section>
 <section class="card">
  <h2>现有 Key <span id="cnt" class="tag">…</span></h2>
  <div id="stale" class="sub"></div>
  <table><thead><tr><th>名称</th><th>Key</th><th>rpm</th><th>并发</th><th>已用</th><th>状态</th><th>操作</th></tr></thead>
  <tbody id="rows"></tbody></table>
 </section>
 <div id="msg" class="msg"></div>
</div>
<script>
const TOKEN = __TOKEN__;
const H = {"X-Admin-Token": TOKEN, "Content-Type": "application/json"};
function say(t, bad){ const m=document.getElementById("msg"); m.textContent=t; m.className="msg "+(bad?"bad":"good"); setTimeout(function(){ m.textContent=""; m.className="msg"; }, 6000); }
function esc(v){ const d=document.createElement("div"); d.textContent=(v===null||v===undefined)?"":String(v); return d.innerHTML; }
async function api(method, path, body){
  const r = await fetch(path, {method:method, headers:H, body: body?JSON.stringify(body):undefined});
  let j = {}; try { j = await r.json(); } catch(e) {}
  if(!r.ok){ throw new Error((j && j.detail) || ("HTTP "+r.status)); }
  return j;
}
async function load(){
  try{
    const d = await api("GET", "/admin/keys");
    const rows = d.data || [];
    document.getElementById("cnt").textContent = rows.length + " 把";
    document.getElementById("rows").innerHTML = rows.map(function(k){
      const dis = Number(k.disabled) ? 1 : 0;
      const key = k.key || "";
      return "<tr><td>"+esc(k.name)+"</td><td class=mono title='"+esc(key)+"'>"+esc(key.slice(0,10))+"…"+esc(key.slice(-6))+" <button class=mini data-copy='"+esc(key)+"'>复制</button></td><td>"+esc(k.rpm)+"</td><td>"+esc(k.max_concurrent===undefined?"-":k.max_concurrent)+"</td><td>"+esc(k.used||0)+"</td><td>"+(dis?"<span class=off>停用</span>":"<span class=on>启用</span>")+"</td><td><button class=mini data-toggle='"+esc(k.id)+"' data-dis='"+dis+"'>"+(dis?"启用":"停用")+"</button> <button class=mini data-rpm='"+esc(k.id)+"' data-v='"+esc(k.rpm)+"'>改 rpm</button> <button class='mini danger' data-del='"+esc(k.id)+"'>硬删</button></td></tr>";
    }).join("");
    const st = await api("GET", "/admin/keys/stale");
    document.getElementById("stale").textContent = "禁用 "+st.disabled_total+" 把，其中 "+st.deletable+" 把无任务（可硬删），"+st.with_tasks+" 把有任务（只能停用）。";
  }catch(e){ say("加载失败: "+e.message, true); }
}
async function createKey(){
  const body = {name: document.getElementById("name").value || "default",
                rpm: +document.getElementById("rpm").value || 60,
                max_concurrent: +document.getElementById("mc").value || 32,
                quota: +document.getElementById("quota").value || 0,
                daily_quota: +document.getElementById("dq").value || 0};
  try{
    const j = await api("POST", "/admin/keys", body);
    document.getElementById("fresh").innerHTML = "新 key：<code>"+esc(j.key)+"</code> <button class=mini data-copy='"+esc(j.key)+"'>复制</button>";
    load();
  }catch(e){ say("创建失败: "+e.message, true); }
}
document.addEventListener("click", async function(ev){
  const t = ev.target;
  try{
    if(t.dataset.copy){ if(navigator.clipboard) await navigator.clipboard.writeText(t.dataset.copy); say("已复制"); return; }
    if(t.dataset.toggle){ await api("PATCH", "/admin/keys/"+t.dataset.toggle, {disabled: t.dataset.dis==="1"?0:1}); say("已更新"); load(); return; }
    if(t.dataset.rpm){ const v = prompt("新的 rpm", t.dataset.v); if(v===null) return; await api("PATCH", "/admin/keys/"+t.dataset.rpm, {rpm:+v}); say("rpm 已改"); load(); return; }
    if(t.dataset.del){ if(!confirm("硬删这把 key？只有无任务的才能删。")) return; await api("DELETE", "/admin/keys/"+t.dataset.del+"?hard=1"); say("已删除"); load(); return; }
  }catch(e){ say(e.message, true); }
});
load();
</script>
</body></html>"""


@app.get("/admin/ui", response_class=HTMLResponse)
def admin_ui(request: Request, token: str = ""):
    """R646: browser console for the key lifecycle (pass ?token=<admin>)."""
    ok = bool(ADMIN_KEY) and (is_admin(request) or (token and secrets.compare_digest(token, ADMIN_KEY)))
    if not ok:
        raise HTTPException(401, "missing/invalid ?token=")
    return HTMLResponse(ADMIN_UI_HTML.replace("__TOKEN__", json.dumps(token or "")))


PLAY_HTML = '''<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>WZ Video Playground</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e8eaed}
 .wrap{max-width:860px;margin:0 auto;padding:24px}
 h1{font-size:18px;font-weight:600;margin:0 0 16px}
 .row{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
 input,textarea,select{background:#1a1d24;border:1px solid #2b303a;color:#e8eaed;border-radius:8px;padding:10px;font-size:14px}
 textarea{width:100%;min-height:88px;resize:vertical}
 input{flex:1;min-width:220px}
 button{background:#4f7cff;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:14px;cursor:pointer}
 button:disabled{opacity:.5;cursor:default}
 .muted{color:#9aa4b2;font-size:12px}
 pre{background:#151922;border:1px solid #232833;border-radius:8px;padding:12px;overflow:auto;font-size:12px}
 video{width:100%;border-radius:10px;background:#000;margin-top:12px}
 a{color:#7aa2ff}
</style></head><body><div class="wrap">
<h1>WZ MiniMax Video Playground · 1080P</h1>
<div class="row"><input id="key" placeholder="sk-wz-..." value=""><button id="save">记住 Key</button></div>
<details id="mkbox"><summary class="muted">创建新 Key / 列出现有 Key（需要 Admin Token）</summary>
<div class="row"><input id="adm" placeholder="X-Admin-Token"></div>
<div class="row"><input id="kname" placeholder="name" value="grandpa-key"><input id="krpm" type="number" value="180" style="max-width:110px" title="rpm"><input id="kquota" type="number" value="0" style="max-width:110px" title="quota，0=不限"></div>
<div class="row"><button id="mk">创建</button><button id="mklist" style="background:#2b303a">列出现有 Key</button><span id="mkstat" class="muted"></span></div>
<pre id="mkout" hidden></pre>
</details>
<textarea id="prompt">a paper boat drifting down a rainy street at night, cinematic</textarea>
<div class="row">
 <select id="res"><option>1080P</option></select>
 <select id="dur"><option value="6">6s</option><option value="10">10s</option></select>
 <select id="ratio"><option>16:9</option><option>9:16</option><option>1:1</option></select>
 <button id="go">生成</button><span id="stat" class="muted"></span>
</div>
<pre id="out">就绪</pre>
<video id="v" controls playsinline hidden></video>
<div class="muted" id="dl"></div>
<script>
const $=id=>document.getElementById(id);
const K='wz_key_v1';
$('key').value=localStorage.getItem(K)||'';
$('save').onclick=()=>{localStorage.setItem(K,$('key').value.trim());$('stat').textContent='key 已保存';};
$('mk').onclick=async()=>{
 const adm=($('adm').value||'').trim(); if(!adm){$('mkstat').textContent='先填 Admin Token';return;}
 $('mk').disabled=true;$('mkstat').textContent='创建中…';
 try{
  const r=await fetch('/admin/keys',{method:'POST',headers:{'Content-Type':'application/json','X-Admin-Token':adm},
    body:JSON.stringify({name:($('kname').value||'default'),rpm:+($('krpm').value||180),max_concurrent:8,quota:+($('kquota').value||0),daily_quota:0})});
  const t=await r.text();let j;try{j=JSON.parse(t)}catch(e){j={raw:t}};
  $('mkout').hidden=false;
  if(!r.ok){$('mkout').textContent=r.status+' '+t;$('mkstat').textContent='失败';return;}
  $('mkout').textContent=JSON.stringify(j,null,2);
  $('key').value=j.key;localStorage.setItem(K,j.key);
  $('mkstat').textContent='已创建，Key 已填入上方';
 }catch(e){$('mkout').hidden=false;$('mkout').textContent='错误: '+e.message;$('mkstat').textContent='失败';}
 finally{$('mk').disabled=false;}
};
$('mklist').onclick=async()=>{
 const adm=($('adm').value||'').trim(); if(!adm){$('mkstat').textContent='先填 Admin Token';return;}
 $('mklist').disabled=true;$('mkstat').textContent='读取中…';
 try{
  const r=await fetch('/admin/keys',{headers:{'X-Admin-Token':adm}});
  const t=await r.text();let j;try{j=JSON.parse(t)}catch(e){j={raw:t}};
  $('mkout').hidden=false;
  $('mkout').textContent=r.ok?JSON.stringify(j,null,2):(r.status+' '+t);
  $('mkstat').textContent=r.ok?('共 '+(j.data||[]).length+' 个 key'):'失败';
 }catch(e){$('mkout').textContent='错误: '+e.message;}
 finally{$('mklist').disabled=false;}
};
async function api(path,opt={}){const r=await fetch(path,{...opt,headers:{'Content-Type':'application/json','Authorization':'Bearer '+($('key').value.trim()||localStorage.getItem(K)||'')}});
 const t=await r.text();let j;try{j=JSON.parse(t)}catch(e){j={raw:t}};if(!r.ok)throw new Error(r.status+' '+t.slice(0,200));return j;}
$('go').onclick=async()=>{
 const body={prompt:$('prompt').value.trim(),resolution:$('res').value,duration:+$('dur').value,ratio:$('ratio').value};
 if(!body.prompt)return;
 $('go').disabled=true;$('v').hidden=true;$('dl').textContent='';$('stat').textContent='提交中…';$('out').textContent='';
 try{
  const sub=await api('/v1/videos/generations',{method:'POST',body:JSON.stringify(body)});
  const id=sub.id;$('out').textContent='任务 '+id+' 已排队';
  for(let i=0;i<200;i++){
   await new Promise(r=>setTimeout(r,4000));
   const g=await api('/v1/videos/generations/'+id);
   $('stat').textContent='状态 '+g.status+' · '+((i+1)*4)+'s';
   $('out').textContent=JSON.stringify(g,null,2);
   if(g.status==='succeeded'){
     const src='/v1/videos/'+id+'/content?key='+encodeURIComponent($('key').value.trim()||localStorage.getItem(K)||'');
     const v=$('v');v.src=src;v.hidden=false;v.load();
     $('dl').innerHTML='<a href="'+src+'" download>下载 mp4</a>'+(g.bytes?(' · '+g.bytes+' bytes'):'');
     break;
   }
   if(g.status==='failed')break;
  }
 }catch(e){$('out').textContent='错误: '+e.message;}
 finally{$('go').disabled=false;}
};
</script></div></body></html>'''

WATCH_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>__TITLE__</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{margin:0;background:#0b0d12;color:#e8eaed;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
 .wrap{max-width:1100px;margin:0 auto;padding:14px}
 video{width:100%;max-height:78vh;background:#000;border-radius:12px;display:block}
 .bar{display:flex;gap:10px;align-items:center;margin:12px 0;flex-wrap:wrap}
 .btn{background:#1b2130;color:#dfe3ea;border:1px solid #2a3344;border-radius:10px;
      padding:8px 14px;text-decoration:none;font-size:14px}
 .btn:hover{background:#232b3d}
 .meta{color:#9aa4b2;font-size:13px}
 .p{color:#cfd6e0;font-size:14px;margin:4px 0 12px;line-height:1.6}
 .tag{background:#1f2a3d;color:#9ecbff;border-radius:6px;padding:2px 8px;font-size:12px}
 .kbd{color:#6b7686;font-size:12px;margin-top:6px}
 .off{opacity:.35;pointer-events:none}
</style></head><body><div class="wrap">
<div class="bar"><a class="btn" href="__BACK__">← 回列表</a>
 <span class="tag">__RES__</span><span class="meta">__META__</span></div>
<video src="__SRC__" controls autoplay muted playsinline preload="metadata"></video>
<div class="p">__PROMPT__</div>
<div class="bar">
 <a class="btn __PREVOFF__" href="__PREV__">← 上一个</a>
 <a class="btn __NEXTOFF__" href="__NEXT__">下一个 →</a>
 <a class="btn" href="__SRC__" download>下载 MP4</a>
 <a class="btn" href="__SRC__" target="_blank">原始文件</a>
 <button class="btn" id="ap" type="button">连播：关</button>
</div>
<div class="kbd">← / → 键切换 · 连播打开后播完自动跳下一个 · __IDTXT__</div>
</div>
<script>
(function(){
  var NEXT="__NEXT__", PREV="__PREV__", LS="wz_autoplay";
  var q=new URLSearchParams(location.search);
  if(q.get("autoplay")==="1"){try{localStorage.setItem(LS,"1");}catch(e){}}
  if(q.get("autoplay")==="0"){try{localStorage.setItem(LS,"0");}catch(e){}}
  var btn=document.getElementById("ap"), v=document.querySelector("video");
  function on(){try{return localStorage.getItem(LS)==="1";}catch(e){return false;}}
  function paint(){
    var o=on();
    btn.textContent="连播："+(o?"开":"关");
    btn.style.background=o?"#1f4d2e":"";
    btn.style.borderColor=o?"#2f7a4a":"";
  }
  btn.onclick=function(){
    try{localStorage.setItem(LS, on()?"0":"1");}catch(e){}
    paint();
    if(on()&&NEXT!=="#"){btn.textContent="连播：开 · 下一个";}
  };
  paint();
  if(v){v.addEventListener("ended",function(){
    if(on()&&NEXT!=="#"){location.href=NEXT;}
  });}
  document.addEventListener("keydown",function(e){
    if(e.key==="ArrowLeft"&&PREV!=="#"){location.href=PREV;}
    if(e.key==="ArrowRight"&&NEXT!=="#"){location.href=NEXT;}
    if(e.key==="a"||e.key==="A"){btn.click();}
  });
})();
</script></body></html>"""


def _watch_html(*, title, src, back, prev, nxt, res, meta, prompt, idtxt):
    return (WATCH_HTML.replace("__TITLE__", _esc(title)).replace("__SRC__", src)
            .replace("__BACK__", back).replace("__PREV__", prev or "#")
            .replace("__NEXT__", nxt or "#").replace("__PREVOFF__", "" if prev else "off")
            .replace("__NEXTOFF__", "" if nxt else "off").replace("__RES__", _esc(res))
            .replace("__META__", meta).replace("__PROMPT__", _esc(prompt))
            .replace("__IDTXT__", _esc(idtxt)))


@app.get("/watch/{tid}", response_class=HTMLResponse)
def watch_task(tid: str, request: Request, browse: str = "", key: str = ""):
    """R698: in-page player with prev/next, so clicking a card keeps you in the flow."""
    full = viewer_ok(request, browse)
    krow = None
    if not full:
        if not key or not find_key(key):
            raise HTTPException(401, "missing/invalid ?key= or ?browse=")
        krow = find_key(key)
    c = db()
    row = c.execute("select * from tasks where id=?", (tid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "task not found")
    if not full and str(row["key_id"] or "") != str(krow["id"]):
        c.close()
        raise HTTPException(403, "task belongs to another key")
    where, args = "status='succeeded'", []
    if not full:
        where += " and key_id=?"
        args.append(krow["id"])
    prev = c.execute("select id from tasks where %s and (created < ? or (created = ? and id < ?)) "
                     "order by created desc, id desc limit 1" % where,
                     args + [row["created"], row["created"], row["id"]]).fetchone()
    nxt = c.execute("select id from tasks where %s and (created > ? or (created = ? and id > ?)) "
                    "order by created asc, id asc limit 1" % where,
                    args + [row["created"], row["created"], row["id"]]).fetchone()
    c.close()
    aq, _ = _page_auth(request, key, browse)
    tail = ("?" + aq) if aq else ""
    ready = bool(row["local_path"] and os.path.isfile(row["local_path"]))
    mb = (os.path.getsize(row["local_path"]) / 1e6) if ready else 0
    src = ("/v1/videos/%s/content%s" % (tid, ("?" + aq) if aq else "")) if ready else (row["url"] or "")
    if not src:
        raise HTTPException(409, "video not ready (status=%s)" % row["status"])
    meta = ("%s · %ss · %.2f MB · %s" % (row["resolution"], row["duration"], mb,
            time.strftime("%m-%d %H:%M", time.localtime(row["created"]))))
    return HTMLResponse(_watch_html(
        title="WZ · %s" % tid, src=src, back="/gallery" + tail,
        prev=("/watch/%s%s" % (prev["id"], tail)) if prev else "",
        nxt=("/watch/%s%s" % (nxt["id"], tail)) if nxt else "",
        res=row["resolution"], meta=meta, prompt=row["prompt"] or "", idtxt=tid))


@app.get("/watch/lib/{fid}", response_class=HTMLResponse)
def watch_library(fid: str, request: Request, browse: str = "", key: str = ""):
    """R698: same player for the 24k legacy farm clips."""
    if not viewer_ok(request, browse):
        if not key or not find_key(key):
            raise HTTPException(401, "missing/invalid ?key= or ?browse=")
    try:
        files = _library_index()
    except FileNotFoundError:
        raise HTTPException(404, "library dir missing")
    name = os.path.basename(fid)
    ids = [f["id"] for f in files]
    try:
        idx = ids.index(name)
    except ValueError:
        raise HTTPException(404, "clip not found")
    aq, _ = _page_auth(request, key, browse)
    tail = ("?" + aq) if aq else ""
    f = files[idx]
    prev = files[idx - 1] if idx > 0 else None
    nxt = files[idx + 1] if idx + 1 < len(files) else None
    m = {}
    try:
        m = _mp4_meta(_lib_path(name)) or {}
    except Exception:
        pass
    res = m.get("resolution") or "?"
    meta = ("%.2f MB · %s · %.1f 天前 · %s" % (f["bytes"] / 1e6,
            time.strftime("%m-%d %H:%M", time.localtime(f["mtime"])),
            f.get("age_days") or 0, m.get("duration_s") or "?")) + "s"
    return HTMLResponse(_watch_html(
        title="WZ Library · %s" % name, src="/v1/library/%s/content%s" % (name, tail),
        back="/library" + tail,
        prev=("/watch/lib/%s%s" % (prev["id"], tail)) if prev else "",
        nxt=("/watch/lib/%s%s" % (nxt["id"], tail)) if nxt else "",
        res=res, meta=meta, prompt=name, idtxt="%d / %d" % (idx + 1, len(files))))


@app.get("/play", response_class=HTMLResponse)
def play():
    return HTMLResponse(PLAY_HTML)

@app.get("/sdk/wzmm.py")
def sdk_file():
    """Single-file client (stdlib only) - `curl -o wzmm.py .../sdk/wzmm.py`."""
    p = "/opt/wzmm/sdk/wzmm.py"
    if not os.path.isfile(p):
        raise HTTPException(404, "sdk not installed")
    return FileResponse(p, media_type="text/x-python", filename="wzmm.py")


# ---- R621: gallery thumbnail cache ----------------------------------------
THUMB_JSON = os.environ.get("WZMM_THUMB_JSON", "/opt/wzmm/_thumbs.json")
_THUMB_CACHE = {"ts": 0.0, "data": None}
THUMB_LOCK = threading.Lock()
THUMB_STATE = {"running": False, "started": None, "finished": None, "rc": None}


def _thumb_stats(ttl=60.0):
    """(total mp4, ready .thumb.jpg, cache bytes) for the local catalog."""
    now = time.time()
    if _THUMB_CACHE["data"] is not None and now - _THUMB_CACHE["ts"] < ttl:
        return _THUMB_CACHE["data"]
    total = ready = cache_bytes = 0
    try:
        with os.scandir(VIDEOS_DIR) as it:
            for e in it:
                if not e.name.endswith(".mp4"):
                    continue
                total += 1
                try:
                    c = e.path + ".thumb.jpg"
                    if os.path.isfile(c) and os.path.getsize(c) > 0:
                        ready += 1
                        cache_bytes += os.path.getsize(c)
                except Exception:
                    pass
    except Exception:
        pass
    data = {"total": total, "ready": ready, "missing": max(0, total - ready),
            "pct": round(100.0 * ready / total, 1) if total else 100.0,
            "cache_bytes": cache_bytes}
    _THUMB_CACHE.update(ts=now, data=data)
    return data


@app.get("/admin/thumbs")
def admin_thumbs(request: Request):
    """R621: thumbnail cache coverage + last prewarm run (read-only)."""
    require_admin(request)
    last = None
    try:
        with open(THUMB_JSON, encoding="utf-8") as f:
            last = json.load(f)
    except Exception:
        pass
    with THUMB_LOCK:
        st = dict(THUMB_STATE)
    if st.get("started"):
        st["elapsed_s"] = round((st.get("finished") or time.time()) - st["started"], 1)
    return {"ok": True, "cache": _thumb_stats(0.0), "state": st, "last_run": last,
            "profile": "640px jpeg q3, seek 1.0s", "worker": "prewarm_thumbs.py"}


@app.post("/admin/thumbs/prewarm")
def admin_thumbs_prewarm(request: Request, workers: int = 4, force: int = 0):
    """R621: fill missing thumbnails in the background (202 immediately)."""
    require_admin(request)
    with THUMB_LOCK:
        if THUMB_STATE.get("running"):
            return {"ok": True, "started": False, "state": dict(THUMB_STATE)}
        THUMB_STATE.update({"running": True, "started": time.time(),
                            "finished": None, "rc": None})
    w = max(1, min(int(workers or 4), 8))

    def work():
        rc = -1
        try:
            cmd = ["/opt/wzmm/venv/bin/python", "-u", "/opt/wzmm/prewarm_thumbs.py",
                   "--workers", str(w)]
            if int(force or 0):
                cmd.append("--force")
            rc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600).returncode
        except Exception:
            rc = -1
        finally:
            _THUMB_CACHE.update(ts=0.0, data=None)
            with THUMB_LOCK:
                THUMB_STATE.update({"running": False, "finished": time.time(), "rc": rc})

    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({"ok": True, "started": True, "workers": w}, status_code=202)


# ---- R616: external reachability probe (public HTTPS) --------------------
PUBLIC_PROBE_JSON = os.environ.get("WZMM_PUBLIC_PROBE", "/opt/wzmm/public_probe.json")
_PUB_CACHE = {"ts": 0.0, "data": None}


def _public_state(ttl=15.0):
    """Cached view of public_probe.json (written by public_probe.py / timer)."""
    now = time.time()
    if _PUB_CACHE["data"] is not None and now - _PUB_CACHE["ts"] < ttl:
        return _PUB_CACHE["data"]
    data = {"ok": False, "ts": 0.0, "age_s": -1.0, "error": "no probe yet",
            "healthz": {}, "models": {}}
    try:
        with open(PUBLIC_PROBE_JSON, encoding="utf-8") as f:
            rec = json.load(f)
        data = dict(rec)
        data["ts"] = float(rec.get("ts") or 0.0)
        data["age_s"] = round(now - data["ts"], 1) if data["ts"] else -1.0
        data.setdefault("error", "")
    except Exception as e:
        data["error"] = repr(e)[:120]
    _PUB_CACHE.update(ts=now, data=data)
    return data


@app.get("/admin/public")
def admin_public(request: Request):
    """R616: last external probe result (public HTTPS domain -> /healthz + /v1/models)."""
    require_admin(request)
    return {"ok": True, "data": _public_state(0.0),
            "source": "public_probe.py", "timer": "wzmm-public.timer"}


@app.post("/admin/public/run")
def admin_public_run(request: Request):
    """R616: run the public probe now (blocking, <=90s) and return the fresh record."""
    require_admin(request)
    rc, lines = -1, []
    try:
        pr = subprocess.run(["/opt/wzmm/venv/bin/python", "/opt/wzmm/public_probe.py"],
                            capture_output=True, text=True, timeout=90)
        rc = pr.returncode
        lines = [x[:300] for x in (pr.stdout or pr.stderr or "").strip().splitlines()[-3:]]
    except Exception as e:
        lines = [repr(e)[:200]]
    data = _public_state(0.0)
    return {"ok": data.get("ok") is True, "rc": rc, "data": data, "lines": lines}


@app.get("/metrics")
def metrics(request: Request):
    # admin token or any valid api key; keeps pool size from being world-readable
    if not (ADMIN_KEY and request.headers.get("x-admin-token") == ADMIN_KEY):
        require_key_light(request)
    c = db()
    by = c.execute("select status,count(*) n from tasks group by status").fetchall()
    keys = c.execute("select count(*) n from keys where disabled=0").fetchone()["n"]
    c.close()
    st = POOL.stats()
    lines = ["# TYPE wzmm_accounts gauge", "wzmm_accounts %d" % st["total"],
             "wzmm_accounts_dead %d" % st["dead"],
             "wzmm_accounts_parked %d" % st["parked"],
             "wzmm_accounts_trial %d" % st["trial_alive"],
             "wzmm_credits_total %d" % st["credits_total"],
             "wzmm_points_total %d" % st["points_total"],
             "wzmm_signin_done_today %d" % st["signin_done_today"],
             "wzmm_keys_active %d" % keys]
    with HOOKQ_LOCK:
        depth = len(HOOKQ)
        oldest = max([0.0] + [time.time() - float(x.get("next_ts") or time.time()) for x in HOOKQ])
    du = shutil.disk_usage("/")
    cat_gb, recent_gb, week_gb = _catalog_sizes()
    fallback = (week_gb / 7.0) if week_gb > 0 else recent_gb
    burn_gb, burn_h = _disk_burn(fallback)
    # 9999 = no observed burn (space flat or being reclaimed): not a countdown
    days_gate = ((du.free / (1 << 30) - MIN_ACCEPT_GB) / burn_gb) if burn_gb > 0 else 9999.0
    with HOOK_STATS_LOCK:
        hs = dict(HOOK_STATS)
    lines += ["# TYPE wzmm_webhook_queue_depth gauge",
              "wzmm_webhook_queue_depth %d" % depth,
              "wzmm_webhook_queue_oldest_age_seconds %.1f" % oldest,
              "wzmm_webhook_retried_total %d" % hs.get("retried", 0),
              "wzmm_webhook_delivered_total %d" % hs.get("delivered", 0),
              "wzmm_webhook_dropped_total %d" % hs.get("dropped", 0),
              "wzmm_disk_free_bytes %d" % du.free,
              "wzmm_disk_total_bytes %d" % du.total,
              "# TYPE wzmm_disk_gate_bytes gauge",
              "wzmm_disk_gate_bytes %d" % int(MIN_ACCEPT_GB * (1 << 30)),
              "# TYPE wzmm_catalog_bytes gauge",
              "wzmm_catalog_bytes %d" % int(cat_gb * 1e9),
              "wzmm_catalog_bytes_24h %d" % int(recent_gb * 1e9),
              "wzmm_catalog_bytes_7d %d" % int(week_gb * 1e9),
              "# TYPE wzmm_disk_burn_bytes_per_day gauge",
              "wzmm_disk_burn_bytes_per_day %d" % int(burn_gb * 1e9),
              "wzmm_disk_burn_window_hours %.1f" % burn_h,
              "# TYPE wzmm_disk_days_to_gate gauge",
              "wzmm_disk_days_to_gate %.2f" % days_gate,
              "# TYPE wzmm_disk_mode gauge",
              "wzmm_disk_mode %d" % _disk_mode(),
              "wzmm_disk_soft_bytes %d" % int(DISK_SOFT_GB * (1 << 30))]
    _orph = _orphan_scan()
    lines += ["# TYPE wzmm_clips_orphan gauge",
              "wzmm_clips_orphan %d" % len(_orph),
              "wzmm_clips_orphan_bytes %d" % sum(r["bytes"] for r in _orph)]
    _th = _thumb_stats()
    lines += ["# TYPE wzmm_thumbs_ready gauge",
              "wzmm_thumbs_ready %d" % _th["ready"],
              "wzmm_thumbs_total %d" % _th["total"],
              "wzmm_thumbs_cache_bytes %d" % _th["cache_bytes"]]
    _ret = _retention_cached()
    lines += ["# TYPE wzmm_clips_local gauge",
              "wzmm_clips_local %d" % _ret["files"],
              "wzmm_clips_bytes %d" % _ret["bytes"],
              "wzmm_clips_pinned %d" % _ret["pinned_files"],
              "wzmm_clips_expiring_24h %d" % _ret["expiring_24h"]["files"],
              "wzmm_clips_expiring_72h %d" % _ret["expiring_72h"]["files"]]
    with VERIFY_LOCK:
        _vs = dict(VERIFY_STATE)
    _vfin = _vs.get("finished") or 0
    lines += ["# TYPE wzmm_verify_ok gauge",
              "wzmm_verify_ok %d" % (1 if _vs.get("state") == "done" else 0),
              "wzmm_verify_rc %s" % (int(_vs["rc"]) if _vs.get("rc") is not None else -1),
              "wzmm_verify_age_seconds %d" % (int(time.time() - _vfin) if _vfin else -1)]
    _pub = _public_state()
    lines += ["# TYPE wzmm_public_ok gauge",
              "wzmm_public_ok %d" % (1 if _pub.get("ok") else 0),
              "wzmm_public_age_seconds %.1f" % float(_pub.get("age_s") if _pub.get("age_s") is not None else -1),
              "wzmm_public_healthz_http %d" % int((_pub.get("healthz") or {}).get("http") or 0),
              "wzmm_public_healthz_ms %.1f" % float((_pub.get("healthz") or {}).get("ms") or 0),
              "wzmm_public_models_ms %.1f" % float((_pub.get("models") or {}).get("ms") or 0)]
    for r in by:
        lines.append('wzmm_tasks{status="%s"} %d' % (r["status"], r["n"]))
    return PlainTextResponse("\n".join(lines) + "\n")

@app.get("/admin/summary")
def admin_summary(request: Request):
    """One call for the ops dashboard: pool, queue, throughput, disk, errors."""
    require_admin(request)
    now = time.time()
    c = db()
    by = {r["status"]: r["n"] for r in
          c.execute("select status,count(*) n from tasks group by status").fetchall()}
    h1 = c.execute("select count(*) n from tasks where status='succeeded' and updated>=?",
                   (now - 3600,)).fetchone()["n"]
    h24 = c.execute("select count(*) n from tasks where status='succeeded' and updated>=?",
                    (now - 86400,)).fetchone()["n"]
    today = c.execute("select count(*) n from tasks where created>=?", (day_start(),)).fetchone()["n"]
    errs = c.execute("select substr(coalesce(err,''),1,60) e, count(*) n from tasks "
                     "where status='failed' and created>=? group by e order by n desc limit 5",
                     (now - 86400,)).fetchall()
    keys = c.execute("select count(*) n from keys where disabled=0").fetchone()["n"]
    videos = c.execute("select count(*) n from tasks where local_path is not null").fetchone()["n"]
    durs = [r["d"] for r in c.execute("select (updated-created) d from tasks where status='succeeded' "
                                      "and updated>=? and updated>created order by d", (now - 86400,)).fetchall()]
    c.close()
    st = POOL.stats()
    du = shutil.disk_usage("/")
    try:
        vsize = sum(os.path.getsize(os.path.join(VIDEOS_DIR, f)) for f in os.listdir(VIDEOS_DIR)
                    if f.endswith(".mp4"))
    except Exception:
        vsize = None
    running = by.get("running", 0)

    def _pct(xs, q):
        if not xs:
            return None
        return round(xs[min(len(xs) - 1, int(round((len(xs) - 1) * q)))], 1)

    return {"ts": int(now), "pool": st, "keys": keys,
            "latency_24h": {"n": len(durs), "p50_s": _pct(durs, 0.50), "p90_s": _pct(durs, 0.90),
                            "p99_s": _pct(durs, 0.99), "max_s": (round(durs[-1], 1) if durs else None)},
            "tasks": by, "today_created": today,
            "throughput": {"last_1h": h1, "last_24h": h24},
            "queue": {"running": running, "queued": by.get("queued", 0),
                      "max_inflight": MAX_INFLIGHT,
                      "headroom": max(0, MAX_INFLIGHT - running - by.get("queued", 0))},
            "disk": {"total_gb": round(du.total / 1e9, 1), "used_gb": round(du.used / 1e9, 1),
                     "free_gb": round(du.free / 1e9, 1),
                     "used_pct": round(du.used * 100.0 / du.total, 1),
                     "videos_bytes": vsize, "videos_files": videos},
            "top_errors_24h": [{"error": r["e"], "n": r["n"]} for r in errs]}

@app.get("/admin/capacity/plan")
def admin_capacity_plan(request: Request):
    """R704: read-only what-if for retention TTL. No files are touched."""
    require_admin(request)
    now = time.time()
    total = fresh = 0
    n = fresh_n = 0
    with os.scandir(VIDEOS_DIR) as it:
        for e in it:
            if not e.name.lower().endswith(".mp4"):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            total += st.st_size
            n += 1
            if now - st.st_mtime <= 86400:
                fresh += st.st_size
                fresh_n += 1
    growth = fresh / 1e9
    du = shutil.disk_usage(VIDEOS_DIR)
    free = du.free / 1e9
    cur = total / 1e9
    plans = []
    for ttl in (0.5, 1.0, 2.0, 3.0, 5.0, 7.0):
        steady = growth * ttl
        extra = max(0.0, steady - cur)
        free_after = free - extra
        plans.append({"ttl_days": ttl, "steady_gb": round(steady, 1),
                      "extra_gb": round(extra, 1), "free_after_gb": round(free_after, 1),
                      "fits": free_after > 2.0})
    max_ttl = (free + cur) / growth if growth > 0 else None
    return {"object": "capacity.plan", "videos_gb": round(cur, 2), "files": n,
            "fresh_files_24h": fresh_n, "growth_gb_day": round(growth, 2),
            "free_gb": round(free, 2),
            "max_ttl_days_before_full": round(max_ttl, 2) if max_ttl else None,
            "plans": plans,
            "note": "read-only projection; changing retention still needs an explicit decision"}


@app.get("/admin/stats")
def admin_stats(request: Request):
    require_admin(request)
    c = db()
    by = c.execute("select status,count(*) n from tasks group by status").fetchall()
    acc = c.execute("select email,count(*) n from tasks where status='succeeded' group by email order by n desc limit 10").fetchall()
    c.close()
    return {"tasks": {r["status"]: r["n"] for r in by}, "top_accounts": [dict(r) for r in acc],
            "pool": POOL.stats(), "accounts": len(POOL.accs), "dead": sorted(POOL.dead)}

def _plan_calc_one(target_per_hour: int, ttl_days: int = 3, release: int = 0):
    """R628/R636: pure planner (no auth, no side effects).

    The calibration is the busiest hour of the last 7 days, so the answer tracks the
    live pool instead of being a static guess. release=1 models the batch_run loop:
    the server only holds the in-flight window, not ttl_days of clips.
    """
    if ttl_days <= 0:
        ttl_days = int(os.environ.get("WZMM_TTL_DAYS", "3") or 3)
    now = time.time()
    c = db()
    try:
        peak = c.execute("select strftime('%Y-%m-%d %H', created, 'unixepoch') h, count(*) n "
                         "from tasks where status='succeeded' and created>=? "
                         "group by h order by n desc limit 1", (now - 7 * 86400,)).fetchone()
        clips24 = c.execute("select count(*) n from tasks where status='succeeded' and created>=?",
                            (now - 86400,)).fetchone()["n"]
    finally:
        c.close()
    peak_hour = int(peak["n"]) if peak else 0
    try:
        total = len(POOL.accs)
        avail = POOL.available()
    except Exception:
        total, avail = 0, 0
    try:
        ret = _retention_cached()
        files = int(ret["files"])
        cat_gb = _gb(ret["bytes"])
    except Exception:
        files, cat_gb = 0, 0.0
    mb_per_clip = round(cat_gb * 1024.0 / files, 2) if files else 4.3
    # clips per account per hour at the observed peak; 5.0 is the R626/R627 calibration
    # (120 accounts -> ~600 clips/h).
    per_account_hour = round(peak_hour / float(total), 2) if (peak_hour and total) else 5.0
    per_account_hour = max(0.5, per_account_hour)
    required = int(-(-target_per_hour * 1.0 // per_account_hour))   # ceil(target / per-account)
    gap = max(0, required - total)
    disk_gb_day = round(target_per_hour * 24 * mb_per_clip / 1024.0, 2)
    release_mode = bool(int(release or 0))
    # concurrency the run will actually hold open (same number the key recommendation uses)
    inflight_clips = min(128, max(32, required)) if release_mode else 0
    inflight_gb = round(inflight_clips * mb_per_clip / 1024.0, 2)
    # x2: a clip can sit finished-but-not-yet-released across a retry window
    steady_gb = round(inflight_gb * 2, 2) if release_mode else round(disk_gb_day * ttl_days, 2)
    free_gib = round(shutil.disk_usage("/").free / float(1 << 30), 1)
    headroom = round(free_gib - min(DISK_SOFT_GB, MIN_ACCEPT_GB), 2)
    disk_ok = steady_gb <= headroom
    extra_gb = round(max(0.0, steady_gb - headroom), 2)
    max_per_hour_ttl = int(headroom * 1024.0 / (mb_per_clip * 24.0 * ttl_days)) if mb_per_clip else 0
    ttl_needed_h = round(headroom * 24.0 / disk_gb_day, 1) if disk_gb_day else None
    key_rec = {"rpm": max(60, int(target_per_hour)),
               "max_concurrent": min(128, max(32, required))}
    notes = []
    if gap:
        notes.append("short %d account(s): add them or lower target_per_hour" % gap)
    if release_mode:
        notes.append("release mode: server holds only the in-flight window (~%.1f GB at %d concurrent), "
                     "%s; output dir needs %.1f GB/day (keep it off this volume)"
                     % (steady_gb, inflight_clips, "fits in headroom" if disk_ok else "still tight",
                        disk_gb_day))
    elif not disk_ok:
        notes.append("steady state %.1f GB > headroom %.1f GB: need %.1f GB more disk, or purge within %.1f h"
                     " (per-task retention_hours<=%d), or lower target_per_hour"
                     % (steady_gb, headroom, extra_gb, ttl_needed_h or 0,
                        max(1, int(ttl_needed_h or 1))))
    if target_per_hour > 600:
        notes.append("above the measured sweet spot (64 concurrent, ~600/h): expect p90 to grow")
    pg = _pool_growth()
    if pg and not pg.get("stale") and pg.get("state") != "ok":
        if pg.get("state") == "reachable_alt":
            notes.append("signup page reachable on account.minimaxi.com, but account creation "
                         "is untested - exercise the flow before planning pool growth")
        else:
            notes.append("pool cannot grow: %s - %s" % (pg.get("state"), pg.get("verdict")))
    return {"object": "plan", "mode": ("release" if release_mode else "retain"),
            "target_per_hour": target_per_hour, "ttl_days": ttl_days,
            "feasible_now": (gap == 0 and disk_ok), "required_accounts": required,
            "accounts": {"total": total, "available": avail, "gap": gap},
            "key": key_rec,
            "disk": {"mb_per_clip": mb_per_clip, "gb_per_day": disk_gb_day,
                     "steady_gb": steady_gb, "free_gib": free_gib, "headroom_gib": headroom,
                     "inflight_clips": inflight_clips, "inflight_gb": inflight_gb,
                     "outdir_gb_per_day": disk_gb_day,
                     "max_per_hour_at_ttl": max_per_hour_ttl,
                     "ttl_needed_h": ttl_needed_h, "extra_gb_needed": extra_gb,
                     "retention_hint_hours": (max(1, int(ttl_needed_h)) if ttl_needed_h else None)},
            "measured": {"peak_hour_clips": peak_hour, "clips_24h": int(clips24 or 0),
                         "clips_per_account_hour": per_account_hour,
                         "window": "last 7d peak hour"},
            "pool_growth": pg,
            "notes": notes}

def _plan_brief(p):
    """R663: one-line shape of a plan, for the side-by-side view."""
    if not p:
        return None
    d = p.get("disk") or {}
    return {"mode": p.get("mode"), "feasible_now": bool(p.get("feasible_now")),
            "steady_gb": d.get("steady_gb"), "extra_gb_needed": d.get("extra_gb_needed"),
            "inflight_clips": d.get("inflight_clips"),
            "max_per_hour_at_ttl": d.get("max_per_hour_at_ttl"),
            "gb_per_day": d.get("gb_per_day")}


def _plan_calc(target_per_hour: int, ttl_days: int = 3, release: int = 0):
    """R663: always answer in both retention modes, not only the one passed in.

    `retain` is what ttl_days of clips cost on disk; `release` is what runner.py
    actually holds in flight. On a nearly full volume they differ by two orders
    of magnitude, which made a 300/h plan read NO-GO when the release loop fits
    comfortably. `both` carries the comparison so nobody has to call twice.
    """
    want = 1 if int(release or 0) else 0
    out = _plan_calc_one(target_per_hour, ttl_days, want)
    try:
        other = _plan_calc_one(target_per_hour, ttl_days, 1 - want)
    except Exception:
        other = None
    modes = {out.get("mode"): _plan_brief(out)}
    if other:
        modes[other.get("mode")] = _plan_brief(other)
    rec = None
    for m in ("release", "retain"):
        if modes.get(m) and modes[m].get("feasible_now"):
            rec = m
            break
    if rec:
        alt = "retain" if rec == "release" else "release"
        why = "%s fits (steady %s GB); %s steady %s GB" % (
            rec, modes[rec].get("steady_gb"), alt, (modes.get(alt) or {}).get("steady_gb"))
    else:
        why = "neither mode fits: lower target_per_hour, free disk, or shorten ttl_days"
    out["both"] = {"modes": modes, "recommend": rec, "why": why}
    return out


@app.get("/admin/plan")
def admin_plan(request: Request, target_per_hour: int = 600, ttl_days: int = 3,
               release: int = 0):
    """What a target throughput costs in accounts / disk / key. Read-only."""
    require_admin(request)
    if target_per_hour <= 0:
        raise HTTPException(400, "target_per_hour must be > 0")
    return _plan_calc(target_per_hour, ttl_days, release)


def _preflight_calc(rate_per_hour, ttl_days, hours, outdir_free_gb):
    """R640/R647: the body of /admin/preflight, reused by the rate matrix.

    Aggregates accounts / server disk / outdir budget / canary / pool growth / an
    already-running runner / queue depth into GO or NO-GO with explicit blockers.
    """
    if rate_per_hour <= 0:
        raise HTTPException(400, "rate_per_hour must be > 0")
    if hours <= 0 or hours > 720:
        raise HTTPException(400, "hours must be 0..720")
    plan = _plan_calc(rate_per_hour, ttl_days, 1)      # release mode = the runner loop
    blockers, warnings = [], []
    checks = {}

    acc = plan["accounts"]
    checks["accounts"] = acc
    if acc["gap"] > 0:
        blockers.append("short %d account(s) for %d/h" % (acc["gap"], rate_per_hour))

    d = plan["disk"]
    checks["disk_server"] = {k: d[k] for k in ("steady_gb", "headroom_gib", "inflight_clips")}
    if d["steady_gb"] > d["headroom_gib"]:
        blockers.append("server disk: in-flight %.1f GB > headroom %.1f GB"
                        % (d["steady_gb"], d["headroom_gib"]))

    need_gb = round(d["outdir_gb_per_day"] * hours / 24.0, 1)
    checks["outdir"] = {"needed_gb": need_gb, "free_gb": outdir_free_gb, "hours": hours}
    if outdir_free_gb >= 0 and outdir_free_gb < need_gb:
        blockers.append("outdir needs %.1f GB for %.1fh, only %.1f GB free"
                        % (need_gb, hours, outdir_free_gb))

    can = _canary_state()
    checks["canary"] = can
    if not can.get("ok"):
        blockers.append("canary failing (journalctl -u wzmm-canary)")
    elif (can.get("age_h") or 99) > 26:
        warnings.append("canary older than 26h - end-to-end proof is stale")

    pg = plan.get("pool_growth") or {}
    checks["pool_growth"] = pg
    if pg and pg.get("state") == "reachable_alt":
        warnings.append("signup page reachable on account.minimaxi.com; account creation untested")
    elif pg and pg.get("state") != "ok":
        warnings.append("pool cannot grow (%s): new accounts need a residential proxy"
                        % pg.get("state"))

    hb = None
    try:
        with open(RUNNER_HB, "r", encoding="utf-8") as f:
            hb = json.load(f)
    except Exception:
        hb = None
    if hb:
        age = max(0, int(time.time() - (hb.get("ts") or 0)))
        checks["runner"] = {"age_s": age, "target_rate": hb.get("target_rate"),
                            "achieved_per_h": hb.get("achieved_per_h"),
                            "paused": bool(hb.get("paused")),
                            "stopped": bool(hb.get("stopped"))}
        if age < 180 and not hb.get("stopped"):
            warnings.append("a runner is already active (%s/h) - starting another doubles the burn"
                            % hb.get("target_rate"))

    c = db()
    try:
        q = {r["status"]: r["n"] for r in c.execute(
            "select status,count(*) n from tasks where status in ('queued','running') "
            "group by status")}
    finally:
        c.close()
    inflight = int(q.get("running", 0)) + int(q.get("queued", 0))
    checks["queue"] = {"inflight": inflight, "max_inflight": MAX_INFLIGHT}
    if MAX_INFLIGHT and inflight >= MAX_INFLIGHT:
        warnings.append("queue is full (%d/%d): new submissions will wait"
                        % (inflight, MAX_INFLIGHT))

    return {"object": "preflight", "verdict": "GO" if not blockers else "NO-GO",
            "rate_per_hour": rate_per_hour, "hours": hours, "ttl_days": ttl_days,
            "planned_clips": int(rate_per_hour * hours), "blockers": blockers,
            "warnings": warnings, "checks": checks,
            "plan": {"required_accounts": plan["required_accounts"],
                     "key": plan["key"], "mode": plan["mode"],
                     "outdir_gb_per_day": plan["disk"]["outdir_gb_per_day"],
                     "steady_gb": plan["disk"]["steady_gb"],
                     "headroom_gib": plan["disk"]["headroom_gib"]}}


@app.get("/admin/preflight")
def admin_preflight(request: Request, rate_per_hour: int = 300, ttl_days: int = 3,
                    hours: float = 8.0, outdir_free_gb: float = -1.0):
    """R640: one call that answers "can I start a production run right now?"."""
    require_admin(request)
    return _preflight_calc(rate_per_hour, ttl_days, hours, outdir_free_gb)


@app.get("/admin/preflight/matrix")
def admin_preflight_matrix(request: Request, rates: str = "300,600,1200",
                           ttl_days: int = 3, hours: float = 8.0,
                           outdir_free_gb: float = -1.0):
    """R647: run the same preflight at several rates, so the target is picked with numbers."""
    require_admin(request)
    vals = []
    for part in str(rates).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(float(part))
        except Exception:
            raise HTTPException(400, "bad rate: %r" % part)
        if v <= 0 or v > 100000:
            raise HTTPException(400, "rate out of range: %d" % v)
        if v not in vals:
            vals.append(v)
    if not vals:
        raise HTTPException(400, "rates is empty")
    if len(vals) > 8:
        raise HTTPException(400, "at most 8 rates")
    out = []
    for v in vals:
        d = _preflight_calc(v, ttl_days, hours, outdir_free_gb)
        p = d.get("plan") or {}
        out.append({"rate_per_hour": v, "verdict": d["verdict"],
                    "blockers": d["blockers"], "warnings": d["warnings"],
                    "required_accounts": p.get("required_accounts"),
                    "outdir_gb_per_day": p.get("outdir_gb_per_day"),
                    "steady_gb": p.get("steady_gb"),
                    "headroom_gib": p.get("headroom_gib"),
                    "outdir_needed_gb": ((d.get("checks") or {}).get("outdir") or {}).get("needed_gb")})
    return {"object": "preflight_matrix", "hours": hours, "ttl_days": ttl_days,
            "outdir_free_gb": outdir_free_gb,
            "summary": {str(r["rate_per_hour"]): r["verdict"] for r in out},
            "scenarios": out}



RUNNER_HB = os.environ.get("WZMM_RUNNER_HB", "/opt/wzmm/runner_heartbeat.json")


@app.get("/admin/runner")
def admin_runner(request: Request, max_age_s: int = 180):
    """R638: what the long-running producer (runner.py) is doing right now.

    Reads the heartbeat the runner writes every stats tick. Absent file = not running.
    """
    require_admin(request)
    try:
        with open(RUNNER_HB, "r", encoding="utf-8") as f:
            hb = json.load(f)
    except Exception:
        return {"object": "runner", "heartbeat": None, "age_s": None, "healthy": False,
                "note": "no heartbeat - runner.py is not running"}
    age = max(0, int(time.time() - (hb.get("ts") or 0)))
    # R642: heartbeat files written before the adaptive controller lack this pair
    if "concurrency_effective" not in hb:
        hb["concurrency_effective"] = hb.get("concurrency")
        hb["auto_concurrency"] = False
    return {"object": "runner", "heartbeat": hb, "age_s": age,
            "stopped": bool(hb.get("stopped")),
            "stale": age > max_age_s,
            "healthy": (age <= max_age_s and not hb.get("paused")
                        and not hb.get("stopped"))}


@app.get("/admin/pool")
def admin_pool(request: Request, limit: int = 200):
    require_admin(request)
    now = time.time()
    out = []
    for a in POOL.accs[:max(1, min(limit, 1000))]:
        e = a["email"]
        out.append({"email": e, "dead": e in POOL.dead,
                    "parked_for": max(0, int(POOL.cool.get(e, 0) - now)),
                    "trial_left_h": round((POOL.trial.get(e, 0) - now) / 3600, 2),
                    "used_today": POOL.used_today(e), "cap_left": POOL.cap_left(e),
                    "points": a.get("points"), "credits": a.get("credits_now")})
    return {"object": "list", "stats": POOL.stats(), "data": out}

@app.post("/admin/merge-tokens")
async def admin_merge_tokens(request: Request):
    require_admin(request)
    body = await request.json()
    items = body.get("tokens") or body.get("items") or []
    n = POOL.merge_tokens(items)
    return {"ok": True, "merged": n, "stats": POOL.stats()}

@app.post("/admin/reload-pool")
def admin_reload_pool(request: Request, full: int = 0):
    require_admin(request)
    if full:
        # rebuild from disk so deletions (dedupe / prune) take effect without a restart
        global POOL
        POOL = Pool(POOL_PATH)
        return {"ok": True, "full": True, "accounts": len(POOL.accs), "stats": POOL.stats()}
    n = POOL.reload_meta()
    return {"ok": True, "accounts": n, "stats": POOL.stats()}

_DISK_SNAP = {"ts": 0.0}

def _catalog_sizes(ttl=60.0):
    """(total_gb, last24h_gb, last7d_gb) for the API catalog, cached 60s.

    R619: the 24h number can be dominated by a one-off bulk import, so the
    runway is computed from the 7-day average and, once enough history exists,
    from the observed free-space slope.
    """
    now = time.time()
    if _DISK_SNAP.get("ts") and now - _DISK_SNAP["ts"] < ttl and "week" in _DISK_SNAP:
        return _DISK_SNAP["total"], _DISK_SNAP["recent"], _DISK_SNAP["week"]
    total = recent = week = 0
    cut = now - 86400
    cut7 = now - 7 * 86400
    try:
        with os.scandir(VIDEOS_DIR) as it:
            for e in it:
                if not e.name.endswith(".mp4"):
                    continue
                try:
                    st = e.stat()
                except Exception:
                    continue
                total += st.st_size
                if st.st_mtime >= cut:
                    recent += st.st_size
                if st.st_mtime >= cut7:
                    week += st.st_size
    except Exception:
        pass
    _DISK_SNAP.update(ts=now, total=total / 1e9, recent=recent / 1e9, week=week / 1e9)
    return _DISK_SNAP["total"], _DISK_SNAP["recent"], _DISK_SNAP["week"]


DISK_HIST = os.environ.get("WZMM_DISK_HIST", "/opt/wzmm/_disk_history.jsonl")


def _disk_sample():
    """Append (ts, free) at most once per 30 min; returns [(ts, free_bytes)]."""
    now = time.time()
    hist = []
    try:
        with open(DISK_HIST, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                    hist.append((float(j["ts"]), float(j["free"])))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    if not hist or now - hist[-1][0] >= 1800:
        try:
            du = shutil.disk_usage("/")
            with open(DISK_HIST, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": int(now), "free": int(du.free)}) + "\n")
            hist.append((now, float(du.free)))
        except Exception:
            pass
    if len(hist) > 6000:
        try:
            with open(DISK_HIST, "w", encoding="utf-8") as f:
                for ts, fr in hist[-4000:]:
                    f.write(json.dumps({"ts": int(ts), "free": int(fr)}) + "\n")
        except Exception:
            pass
    return hist


def _disk_burn(fallback_gb, min_hours=6.0):
    """Observed net burn (GB/day) from local free-space history.

    Uses up to 7 days of samples; returns (rate_gb_per_day, window_hours).
    Falls back to the catalog estimate until the window is long enough.
    """
    hist = _disk_sample()
    now = time.time()
    win = [(t, f) for (t, f) in hist if now - t <= 7 * 86400]
    if len(win) >= 2:
        span_h = (win[-1][0] - win[0][0]) / 3600.0
        if span_h >= min_hours:
            drop_gb = (win[0][1] - win[-1][1]) / 1e9
            if drop_gb <= 0:
                return 0.0, round(span_h, 1)
            return round(drop_gb / (span_h / 24.0), 3), round(span_h, 1)
    return round(max(0.0, fallback_gb), 3), 0.0

def _legacy_gb(ttl=300.0):
    now = time.time()
    if _DISK_SNAP.get("lts") and now - _DISK_SNAP["lts"] < ttl and "legacy" in _DISK_SNAP:
        return _DISK_SNAP["legacy"]
    total = 0
    try:
        with os.scandir(LEGACY_DIR) as it:
            for e in it:
                if e.name.endswith(".mp4"):
                    try:
                        total += e.stat().st_size
                    except Exception:
                        pass
    except Exception:
        pass
    _DISK_SNAP.update(lts=now, legacy=total / 1e9)
    return _DISK_SNAP["legacy"]


@app.get("/admin/usage")
def admin_usage(request: Request, days: int = 7, by_key: int = 0):
    require_admin(request)
    since = time.time() - max(1, min(days, 90)) * 86400
    c = db()
    if by_key:
        krows = c.execute(
            "select k.id, k.name, k.rpm, k.quota, k.daily_quota, k.disabled, k.used, k.last_used, "
            "       count(t.id) n, "
            "       sum(case when t.status='succeeded' then 1 else 0 end) ok, "
            "       sum(case when t.status='failed' then 1 else 0 end) bad, "
            "       sum(case when t.status='canceled' then 1 else 0 end) canceled, "
            "       min(t.created) first_ts, max(t.created) last_ts "
            "from keys k left join tasks t on t.key_id=k.id and t.created>=? "
            "group by k.id order by n desc, k.created desc", (since,)).fetchall()
        c.close()
        data = []
        for r in krows:
            d = dict(r)
            d["n"] = d["n"] or 0
            for f in ("ok", "bad", "canceled"):
                d[f] = d[f] or 0
            if d["n"]:
                d["success_rate"] = round(100.0 * d["ok"] / d["n"], 1)
            data.append(d)
        return {"object": "list", "days": days, "by": "key", "data": data}
    rows = c.execute("select strftime('%Y-%m-%d', created, 'unixepoch', '+8 hours') d, status, count(*) n "
                     "from tasks where created>=? group by d, status order by d desc", (since,)).fetchall()
    c.close()
    out = {}
    for r in rows:
        out.setdefault(r["d"], {})[r["status"]] = r["n"]
    return {"object": "list", "days": days, "data": out}

@app.get("/admin/credits")
def admin_credits(request: Request):
    require_admin(request)
    now = time.time()
    rows = []
    tot = 0
    for a in POOL.accs:
        e = a["email"]
        c = POOL.credits.get(e)
        tot += int(c or 0)
        rows.append({"email": e, "credits": c, "trial_left_h": round((POOL.trial.get(e, 0) - now) / 3600, 2),
                     "used_today": POOL.used_today(e), "cap_left": POOL.cap_left(e),
                     "dead": e in POOL.dead})
    rows.sort(key=lambda r: (r["credits"] if r["credits"] is not None else -1, -r["trial_left_h"]))
    return {"credits_total": tot, "cost_1080": COST_1080,
            "runs_if_1080": tot // max(1, COST_1080), "data": rows[:500]}

@app.get("/admin/token-age")
def admin_token_age(request: Request):
    require_admin(request)
    import base64 as _b64
    now = time.time()
    rows = []
    for a in POOL.accs:
        t = a.get("token") or ""
        exp = 0
        try:
            p = t.split(".")[1]
            p += "=" * (-len(p) % 4)
            exp = int(json.loads(_b64.urlsafe_b64decode(p)).get("exp") or 0)
        except Exception:
            exp = 0
        if a.get("refreshToken"):
            rby = "refresh"
        elif a.get("password"):
            rby = "password"
        else:
            rby = "none"
        rows.append({"email": a["email"], "has_refresh": bool(a.get("refreshToken")),
                     "renewable_by": rby, "exp": exp,
                     "days_left": round((exp - now) / 86400, 1) if exp else None,
                     "dead": a["email"] in POOL.dead})
    rows.sort(key=lambda r: (r["exp"] or 0))
    buckets = {}
    for r in rows:
        d = time.strftime("%Y-%m-%d", time.gmtime(r["exp"])) if r["exp"] else "unknown"
        buckets[d] = buckets.get(d, 0) + 1
    wr = sum(1 for r in rows if r["has_refresh"])
    nonrenew = [r for r in rows if r["renewable_by"] == "none"]
    soon = [{"email": r["email"], "has_refresh": r["has_refresh"],
             "renewable_by": r["renewable_by"], "exp": r["exp"],
             "days_left": r["days_left"], "dead": r["dead"]}
            for r in [x for x in rows if x["exp"]][:8]]
    exps = [r["exp"] for r in rows if r["exp"]]
    first_death = min(exps) if exps else 0
    last_death = max(exps) if exps else 0
    hard = [r["exp"] for r in rows if r["exp"] and r["renewable_by"] == "none"]
    forecast, forecast_renewed = [], []
    for i in range(0, 61):
        day_end = now + i * 86400
        alive = sum(1 for e in exps if e > day_end)
        alive_r = sum(1 for r in rows
                      if not r["exp"] or r["exp"] > day_end or r["renewable_by"] != "none")
        ds = time.strftime("%Y-%m-%d", time.gmtime(day_end))
        forecast.append({"day": i, "date": ds, "alive": alive})
        forecast_renewed.append({"day": i, "date": ds, "alive": alive_r})
    return {"total": len(rows), "with_refresh": wr, "without_refresh": len(rows) - wr,
            "renewable": len(rows) - len(nonrenew),
            "unrenewable": len(nonrenew),
            "expiring_by_day": dict(sorted(buckets.items())),
            "soonest": soon,
            "first_death": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(first_death)) if first_death else None,
            "days_to_first_death": round((first_death - now) / 86400, 1) if first_death else None,
            "last_death": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last_death)) if last_death else None,
            "first_hard_death": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(min(hard))) if hard else None,
            "lifetime_days": 40,
            "renewal_capable": True,
            "replenish_per_day": round(len(nonrenew) / 40.0, 2),
            "forecast_60d": forecast,
            "forecast_60d_renewed": forecast_renewed,
            "note": ("token exp = 建号或续期时间 + 40 天（实测 40.000d）。续期已经全部搬到服务器侧，不依赖任何浏览器：account.minimax.io 对本机 IP 直接返回 Akamai 403，必须经 us.1024proxy.io:3000 出口。两条路：(1) refreshToken -> securetoken.googleapis.com -> /oauth2/login loginType=11；(2) 邮箱+密码 -> /oauth2/login loginType=20（authToken = base64(RSA_PKCS1v15(password))）。两条路之后都走 /oauth2/authorize?client_id=hub&redirect_uri=design.minimax.io/auth/callback 取 _token。注意 client_id=agent-minimax&source=agent_web 这条现在 callback 返 500，不再下发 _token。因此 40 天不是硬上限；renewable_by=none（既无 refreshToken 也无 password）才是真死。forecast_60d = 裸口径（不续期，最坏情况）；forecast_60d_renewed = 续期口径。replenish_per_day 只对 unrenewable 计算。")}


@app.post("/admin/apply-liveness")
async def admin_apply_liveness(request: Request):
    require_admin(request)
    body = await request.json()
    dead = [e for e in (body.get("dead") or []) if isinstance(e, str)]
    ok = [e for e in (body.get("ok") or []) if isinstance(e, str)]
    revived = 0
    for e in dead:
        if e in POOL.by and e not in POOL.dead:
            POOL.mark_dead(e, body.get("why") or "liveness-probe")
    for e in ok:
        if e in POOL.dead:
            POOL.dead.discard(e)
            POOL.streak[e] = 0
            revived += 1
    if revived:
        # drop the resurrected accounts from the persisted dead log
        try:
            keep = []
            for line in open(DEAD_PATH, encoding="utf-8"):
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if j.get("t") == "dead" and j.get("email") in set(ok):
                    continue
                keep.append(line)
            with open(DEAD_PATH, "w", encoding="utf-8") as f:
                f.writelines(keep)
        except FileNotFoundError:
            pass
    return {"ok": True, "dead_added": len([e for e in dead if e in POOL.by]),
            "revived": revived, "stats": POOL.stats()}

@app.get("/admin/tasks")
def admin_tasks(request: Request, status: str = "", limit: int = 50):
    require_admin(request)
    c = db()
    if status:
        rows = c.execute("select id,status,model,resolution,duration,email,upstream,retry,created,err "
                         "from tasks where status=? order by created desc limit ?",
                         (status, max(1, min(limit, 500)))).fetchall()
    else:
        rows = c.execute("select id,status,model,resolution,duration,email,upstream,retry,created,err "
                         "from tasks order by created desc limit ?", (max(1, min(limit, 500)),)).fetchall()
    c.close()
    return {"object": "list", "data": [dict(r) for r in rows]}



@app.post("/admin/simulate-402")
async def admin_simulate_402(request: Request):
    """R704: exercise the 402 demotion path without spending a render.

    body: {"email": "..."} optional, {"restore": 1} to undo immediately.
    """
    require_admin(request)
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    email = str(body.get("email") or "").strip()
    if not email:
        cand = [a["email"] for a in POOL.accs if a["email"] not in POOL.dead]
        if not cand:
            raise HTTPException(404, "pool is empty")
        email = cand[0]
    acc = POOL.by.get(email)
    if not acc:
        raise HTTPException(404, "no such account: %s" % email)
    parked = _park_on_402(acc, "simulated 402")
    out = {"ok": True, "email": email, "parked_s": parked,
           "cool_for_s": max(0, int(POOL.cool.get(email, 0) - time.time()))}
    if int(body.get("restore") or 0):
        _unpark_one(email)
        out["restored"] = True
        out["cool_for_s"] = max(0, int(POOL.cool.get(email, 0) - time.time()))
    return out

@app.post("/admin/park")
async def admin_park(request: Request):
    """Temporarily take accounts out of rotation (ops lever; /admin/unpark clears it).

    body: {"emails":[...]} or {"n":40}  + {"seconds":120}
    """
    require_admin(request)
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    emails = [str(x) for x in (body.get("emails") or []) if x]
    n = int(body.get("n") or 0)
    seconds = int(body.get("seconds") or 120)
    if not emails and n > 0:
        emails = [a["email"] for a in POOL.accs][:n]
    if not emails:
        raise HTTPException(400, "emails or n required")
    for e in emails:
        POOL.park(e, seconds, "admin park")
    return {"ok": True, "parked": len(emails), "seconds": seconds,
            "available": POOL.available(), "eff_max_inflight": _eff_cap()}


@app.post("/admin/unpark")
def admin_unpark(request: Request):
    require_admin(request)
    with POOL.lock:
        n = len(POOL.cool)
        POOL.cool.clear()
    try:
        keep = []
        for line in open(DEAD_PATH, encoding="utf-8"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("t") != "cool":
                keep.append(line)
        with open(DEAD_PATH, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except FileNotFoundError:
        pass
    return {"ok": True, "unparked": n, "stats": POOL.stats()}

@app.post("/admin/revive")
def admin_revive(request: Request):
    require_admin(request)
    n = len(POOL.dead)
    POOL.dead.clear()
    try:
        os.remove(DEAD_PATH)
    except Exception:
        pass
    return {"ok": True, "revived": n}


# ---- R583: richer OpenAPI (request bodies, security schemes, tags) ----
_OA_SCHEMAS = {
    "VideoCreate": {
        "type": "object", "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "text prompt", "example": "雪山日出，镜头缓慢推近"},
            "model": {"type": "string", "enum": ["MiniMax-Hailuo-2.3"], "default": "MiniMax-Hailuo-2.3"},
            "resolution": {"type": "string", "enum": ["1080P"], "default": "1080P"},
            "duration": {"type": "integer", "enum": [6, 10], "default": 6},
            "ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"], "default": "16:9"},
            "callback_url": {"type": "string", "format": "uri"},
            "idempotency_key": {"type": "string", "maxLength": 120},
            "wait": {"type": "boolean", "default": False},
            "wait_timeout": {"type": "integer", "minimum": 30, "maximum": 1800, "default": 600},
        },
    },
    "BatchCreate": {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"oneOf": [
                {"type": "string"}, {"$ref": "#/components/schemas/VideoCreate"}]}},
            "prompts": {"type": "array", "items": {"type": "string"}},
            "defaults": {"$ref": "#/components/schemas/VideoCreate"},
            "dry_run": {"type": "boolean", "default": False},
            "allow_partial": {"type": "boolean", "default": False},
        },
    },
    "KeyCreate": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "default"},
            "rpm": {"type": "integer", "default": 60},
            "max_concurrent": {"type": "integer", "default": 32},
            "quota": {"type": "integer", "default": 0, "description": "0 = unlimited"},
            "daily_quota": {"type": "integer", "default": 0, "description": "0 = unlimited"},
        },
    },
    "ToolsCall": {
        "type": "object", "required": ["tool"],
        "properties": {
            "tool": {"type": "string", "description": "matrix_<tool_name>; see GET /v1/tools"},
            "name": {"type": "string", "description": "alias of tool"},
            "arguments": {"type": "object", "additionalProperties": True},
            "args": {"type": "object", "additionalProperties": True},
        },
    },
    "WebhookSecret": {"type": "object", "properties": {"secret": {"type": "string"}}},
    "WebhookTest": {"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string", "format": "uri"}}},
    "Park": {"type": "object", "properties": {
        "emails": {"type": "array", "items": {"type": "string"}},
        "n": {"type": "integer"}, "seconds": {"type": "integer", "default": 120}}},
    "ApplyLiveness": {"type": "object", "properties": {
        "dead": {"type": "array", "items": {"type": "string"}},
        "ok": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string"}}},
}

_OA_BODIES = {
    "/v1/videos/generations": ("VideoCreate", True),
    "/v1/videos/batch": ("BatchCreate", True),
    "/v1/videos/bulk": ("BatchCreate", True),
    "/v1/tools/call": ("ToolsCall", True),
    "/v1/webhook/secret": ("WebhookSecret", True),
    "/v1/webhook/test": ("WebhookTest", True),
    "/admin/keys": ("KeyCreate", False),
    "/admin/park": ("Park", False),
    "/admin/apply-liveness": ("ApplyLiveness", False),
}

def _oa_tag(path):
    p = path.split("/")
    if len(p) > 1 and p[1] == "admin":
        return "admin"
    if len(p) > 2 and p[1] == "v1":
        return {"videos": "videos", "library": "library", "webhook": "webhook"}.get(p[2], "misc")
    return "pages"

def _custom_openapi():
    from fastapi.openapi.utils import get_openapi
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title="WZ MiniMax Video API", version="1.5", routes=app.routes,
                         description="MiniMax video generation gateway. Create keys with POST /admin/keys "
                                     "(X-Admin-Token), then call /v1/* with Authorization: Bearer sk-wz-...")
    comp = schema.setdefault("components", {})
    comp.setdefault("schemas", {}).update(_OA_SCHEMAS)
    comp["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "sk-wz-..."},
        "AdminToken": {"type": "apiKey", "in": "header", "name": "X-Admin-Token"},
    }
    for path, (model, required) in _OA_BODIES.items():
        op = schema["paths"].get(path, {}).get("post")
        if not op:
            continue
        op["requestBody"] = {"required": required, "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/" + model}}}}
    for path, ops in schema["paths"].items():
        for m, op in ops.items():
            if m == "parameters":
                continue
            op["tags"] = [_oa_tag(path)]
            if path.startswith("/v1/"):
                op.setdefault("security", [{"BearerAuth": []}])
            elif path.startswith("/admin/"):
                op.setdefault("security", [{"AdminToken": []}])
    schema["info"]["version"] = "1.5"
    app.openapi_schema = schema
    return schema

app.openapi = _custom_openapi

# ---------------- R711: prompt -> video studio (page only; uses the public /v1 API) ----------------
CREATE_HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>WZ 创作台 · 提示词生成视频</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e8eaed}
 header{position:sticky;top:0;background:#151922;border-bottom:1px solid #232936;padding:12px 16px;z-index:5}
 h1{font-size:16px;margin:0;font-weight:600}
 .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
 input,select,textarea,button{background:#1a1d24;border:1px solid #2b303a;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px;font-family:inherit}
 button{cursor:pointer}button.p{background:#4f7cff;border:0;font-weight:600}
 button:disabled{opacity:.55;cursor:not-allowed}
 .wrap{max-width:1120px;margin:0 auto;padding:16px}
 .panel{background:#151922;border:1px solid #232936;border-radius:12px;padding:14px}
 textarea{width:100%;min-height:110px;resize:vertical;line-height:1.6;box-sizing:border-box}
 .chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
 .chip{font-size:11px;padding:4px 8px;border-radius:999px;background:#1d2330;border:1px solid #2b303a;color:#9aa4b2;cursor:pointer}
 .chip:hover{color:#cfd6e4}
 .grid{display:grid;grid-template-columns:1fr 340px;gap:14px}
 @media(max-width:860px){.grid{grid-template-columns:1fr}}
 .stat{font-size:12px;color:#9aa4b2}
 .pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:#243043;color:#9fc3ff}
 .pill.ok{background:#1f4d2e;color:#c7f5d6}
 .pill.bad{background:#4d1f24;color:#ffc9cf}
 video{width:100%;border-radius:10px;background:#000;display:block}
 #links a{font-size:12px;padding:5px 9px;border-radius:7px;text-decoration:none;color:#cfd6e4;background:#1d2330;border:1px solid #2b303a}
 .hist{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
 .hcard{background:#12161d;border:1px solid #232936;border-radius:10px;overflow:hidden;cursor:pointer}
 .hcard:hover{border-color:#3a4257}
 .hcard img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#0b0d11}
 .hmeta{padding:6px 8px;font-size:11px;color:#9aa4b2;line-height:1.45}
 a{color:#8fb0ff}
</style></head><body>
<header><h1>创作台 <span class="pill">1080P only</span></h1>
<div class="bar">
 <span id="kstate" class="stat">未设置 key</span>
 <input id="key" type="password" placeholder="sk-wz-..." style="min-width:300px">
 <button id="ksave">保存 key</button>
 <a data-nav="/gallery">成品画廊</a>
 <a data-nav="/library">片库</a>
</div></header>
<div class="wrap">
<div class="grid">
 <div class="panel">
  <div class="stat">提示词（中文/英文都行；主体 + 动作 + 场景 + 镜头 + 风格，越具体越稳）</div>
  <textarea id="prompt" placeholder="例：一只橘猫在雨后的东京街头慢慢走过，霓虹灯倒影在地面，35mm 电影感，浅景深，缓慢推镜"></textarea>
  <div class="chips" id="chips"></div>
  <div class="bar">
   <span class="stat">模型</span><select id="model" style="min-width:230px"></select>
   <span class="stat">时长</span><select id="dur"></select>
   <span class="stat">比例</span><select id="ratio"></select>
   <span class="stat">清晰度</span><span class="pill">1080P</span>
   <button class="p" id="go">生成视频</button>
   <button id="stop" style="display:none">取消</button>
  </div>
  <div id="msg" class="stat" style="margin-top:10px"></div>
 </div>
 <div class="panel">
  <div id="playerHint" class="stat">生成完成后这里会出现播放器</div>
  <video id="player" controls playsinline style="display:none"></video>
  <div id="links" class="bar" style="display:none"></div>
 </div>
</div>
<div class="panel" style="margin-top:14px">
 <div class="bar"><b style="font-size:13px">我的作品</b><button id="refresh">刷新</button><span id="hstat" class="stat"></span></div>
 <div class="hist" id="hist" style="margin-top:10px"></div>
</div>
</div>
<script>
var $ = function(s){ return document.querySelector(s); };
var qs = new URLSearchParams(location.search);
var KEY = (qs.get('key') || localStorage.getItem('wz_key') || '').trim();
var MODELS = [], CUR = null, T0 = 0, TIMER = null;
var CHIPS = ["电影感运镜，浅景深，35mm","霓虹夜景，雨后街道倒影","航拍俯冲，云层穿过","人物特写，缓慢推镜，柔光"];
function msg(t, bad){ var m = $('#msg'); m.textContent = t || ''; m.className = 'stat'; if(bad) m.style.color = '#ff9aa6'; else m.style.color = '#9aa4b2'; }
function paintKey(){
  $('#kstate').textContent = KEY ? ('key ' + KEY.slice(0, 10) + '…') : '未设置 key';
  $('#key').value = KEY || '';
  var suf = KEY ? ('?key=' + encodeURIComponent(KEY)) : '';
  var links = document.querySelectorAll('[data-nav]');
  for (var i = 0; i < links.length; i++) links[i].href = links[i].getAttribute('data-nav') + suf;
}
function setKey(k){ KEY = (k || '').trim(); if (KEY) localStorage.setItem('wz_key', KEY); paintKey(); }
async function api(path, opt){
  opt = opt || {};
  opt.headers = Object.assign({'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}, opt.headers || {});
  var r = await fetch(path, opt);
  var j = null; try { j = await r.json(); } catch (e) {}
  if (!r.ok) {
    var m = j && (j.detail || (j.error && (j.error.message || j.error)) || j.message);
    if (typeof m !== 'string') m = JSON.stringify(m);
    throw new Error(m || ('HTTP ' + r.status));
  }
  return j;
}
async function loadModels(){
  if (!KEY) return;
  try {
    var j = await api('/v1/models');
    MODELS = (j.data || []).filter(function(m){ return m.available; });
    var sel = $('#model'); sel.innerHTML = '';
    MODELS.forEach(function(m){
      var o = document.createElement('option');
      o.value = m.id; o.textContent = m.id + (m.supports_1080p ? ' · 1080P' : '');
      if (m.id === 'MiniMax-Hailuo-2.3') o.selected = true;
      sel.appendChild(o);
    });
    fillSpec();
  } catch (e) { msg('读取模型失败：' + e.message, true); }
}
function fillSpec(){
  var id = $('#model').value, m = null;
  for (var i = 0; i < MODELS.length; i++) if (MODELS[i].id === id) m = MODELS[i];
  if (!m) return;
  var d = $('#dur'); d.innerHTML = '';
  (m.durations || [6]).forEach(function(v){
    var o = document.createElement('option'); o.value = v; o.textContent = v + 's';
    if (v === 6) o.selected = true; d.appendChild(o);
  });
  var r = $('#ratio'); r.innerHTML = '';
  (m.ratios || ['16:9']).forEach(function(v){
    var o = document.createElement('option'); o.value = v; o.textContent = v;
    if (v === '16:9') o.selected = true; r.appendChild(o);
  });
}
async function go(){
  msg('');
  if (!KEY) { msg('先在右上角填入 key', true); return; }
  var prompt = $('#prompt').value.trim();
  if (!prompt) { msg('提示词不能为空', true); return; }
  $('#go').disabled = true; $('#stop').style.display = '';
  $('#player').style.display = 'none'; $('#links').style.display = 'none';
  try {
    var body = {prompt: prompt, model: $('#model').value, resolution: '1080P',
                duration: parseInt($('#dur').value, 10), ratio: $('#ratio').value};
    var t = await api('/v1/videos/generations', {method: 'POST', body: JSON.stringify(body)});
    CUR = t.id; T0 = Date.now();
    msg('已提交 · id ' + t.id + ' · 正在排队');
    poll();
  } catch (e) {
    msg('提交失败：' + e.message, true);
    $('#go').disabled = false; $('#stop').style.display = 'none';
  }
}
function poll(){
  clearTimeout(TIMER);
  if (!CUR) return;
  api('/v1/videos/generations/' + CUR).then(function(v){
    var el = Math.round((Date.now() - T0) / 1000);
    if (v.status === 'succeeded') { done(v); return; }
    if (v.status === 'failed' || v.status === 'canceled') {
      msg('生成失败：' + (v.error || v.status), true);
      $('#go').disabled = false; $('#stop').style.display = 'none'; return;
    }
    msg('生成中 · ' + v.status + ' · 已等 ' + el + 's');
    TIMER = setTimeout(poll, 2500);
  }).catch(function(e){
    msg('轮询失败：' + e.message, true);
    TIMER = setTimeout(poll, 5000);
  });
}
function done(v){
  var mb = v.bytes ? (v.bytes / 1e6).toFixed(2) + ' MB' : '';
  msg('完成 · ' + v.resolution + ' · ' + v.duration + 's · ' + mb);
  var u = './v1/videos/' + v.id + '/content?key=' + encodeURIComponent(KEY);
  var pl = $('#player'); pl.src = u; pl.style.display = 'block';
  $('#playerHint').style.display = 'none';
  var l = $('#links'); l.style.display = 'flex'; l.innerHTML = '';
  function add(text, href, dl){
    var a = document.createElement('a'); a.textContent = text; a.href = href;
    if (dl) a.setAttribute('download', v.id + '.mp4');
    l.appendChild(a);
  }
  add('下载 mp4', u, true);
  add('meta', './v1/videos/' + v.id + '/meta?key=' + encodeURIComponent(KEY));
  add('详情页', './watch/' + v.id + '?key=' + encodeURIComponent(KEY));
  pl.play().catch(function(){});
  $('#go').disabled = false; $('#stop').style.display = 'none';
  loadHist();
}
async function loadHist(){
  if (!KEY) return;
  try {
    var j = await api('/v1/videos/generations?limit=12');
    var box = $('#hist'); box.innerHTML = '';
    var rows = j.data || [];
    $('#hstat').textContent = rows.length ? ('最近 ' + rows.length + ' 条') : '';
    if (!rows.length) { box.innerHTML = '<div class="stat">这个 key 还没有作品</div>'; return; }
    rows.forEach(function(r){
      var c = document.createElement('div'); c.className = 'hcard';
      if (r.local) {
        c.innerHTML = '<img loading="lazy" src="./v1/videos/' + r.id + '/thumb?key=' + encodeURIComponent(KEY) + '">'
          + '<div class="hmeta">' + r.status + ' · ' + r.resolution + ' · ' + r.duration + 's<br>'
          + new Date(r.created * 1000).toLocaleString() + '</div>';
        c.onclick = function(){ loadOne(r.id); };
      } else {
        c.innerHTML = '<div style="aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;color:#5c6675;font-size:11px">'
          + r.status + '</div><div class="hmeta">' + r.resolution + ' · ' + r.duration + 's<br>'
          + new Date(r.created * 1000).toLocaleString() + '</div>';
      }
      box.appendChild(c);
    });
  } catch (e) { /* silent */ }
}
async function loadOne(id){
  try { var v = await api('/v1/videos/generations/' + id); done(v); }
  catch (e) { msg(e.message, true); }
}
(function init(){
  var chips = $('#chips');
  CHIPS.forEach(function(t){
    var s = document.createElement('span'); s.className = 'chip'; s.textContent = t;
    s.onclick = function(){ var p = $('#prompt'); p.value = (p.value ? p.value + '，' : '') + t; p.focus(); };
    chips.appendChild(s);
  });
  paintKey();
  $('#ksave').onclick = function(){ setKey($('#key').value); loadModels(); loadHist(); };
  $('#model').onchange = fillSpec;
  $('#go').onclick = go;
  $('#refresh').onclick = loadHist;
  $('#stop').onclick = async function(){
    if (!CUR) return;
    try { await api('/v1/videos/' + CUR + '/cancel', {method: 'POST'}); msg('已请求取消'); }
    catch (e) { msg(e.message, true); }
  };
  $('#prompt').addEventListener('keydown', function(e){
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) go();
  });
  if (KEY) { loadModels(); loadHist(); }
})();
</script></body></html>"""


@app.get("/create", response_class=HTMLResponse)
def create_page(request: Request):
    return HTMLResponse(CREATE_HTML)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
