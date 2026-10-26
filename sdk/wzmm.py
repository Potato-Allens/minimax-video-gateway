# -*- coding: utf-8 -*-
"""wzmm - single-file client for the WZ MiniMax video API (stdlib only, v2.3).

Covers the /v1 + /admin surface (91 paths; newest: archive jobs, library archive, capacity plan). Env:
    WZMM_BASE   default https://api.example.com
    WZMM_KEY    sk-wz-...        (business endpoints)
    WZMM_ADMIN  admin token      (X-Admin-Token, /admin/* endpoints)

Quick start
    export WZMM_KEY=sk-wz-...
    python wzmm.py models
    python wzmm.py submit "a lantern over a lake, slow dolly-in" -o out.mp4
    python wzmm.py batch prompts.txt --concurrency 24 --outdir out/
    python wzmm.py bulk items.json --dry-run
    python wzmm.py library --res 1080p --limit 5
    python wzmm.py manifest --res 1080p --format csv -o lib.csv
    python wzmm.py libarchive --ids t-aaa,t-bbb -o legacy.zip
    python wzmm.py job --kind library --ids t-aaa -o job.zip
    python wzmm.py api GET /v1/pricing
    WZMM_ADMIN=... python wzmm.py admin-health
"""
import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("WZMM_BASE", "http://127.0.0.1:8808").rstrip("/")
KEY = os.environ.get("WZMM_KEY", "")
ADMIN = os.environ.get("WZMM_ADMIN", "")
RETRIES = int(os.environ.get("WZMM_RETRIES", "4"))   # extra attempts on 429/5xx/conn (R624: 2 -> 4)
QUIET = False
COMPACT = False   # --json: one-line, machine-friendly output
TIMEOUT = None
RETRY_STATUS = {429, 500, 502, 503, 504}
# R624: live counters so batch tools can report the real submit-side cost
STATS = {"requests": 0, "retries": 0, "http_429": 0, "http_5xx": 0, "conn_errors": 0}


class APIError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__("http %s: %s" % (status, body))


def _req(method, path, body=None, key=None, admin=False, raw=False, timeout=120, headers=None):
    url = path if path.startswith("http") else BASE + path
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if admin:
        r.add_header("X-Admin-Token", ADMIN)
    else:
        k = key if key is not None else KEY
        if k:
            r.add_header("Authorization", "Bearer " + k)
    t = timeout if timeout is not None else (TIMEOUT or 120)
    tries = max(1, RETRIES + 1)
    for attempt in range(tries):
        STATS["requests"] += 1
        try:
            with urllib.request.urlopen(r, timeout=t) as resp:
                payload = resp.read()
                if raw:
                    return payload
                return json.loads(payload.decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                STATS["http_429"] += 1
            elif e.code >= 500:
                STATS["http_5xx"] += 1
            body = e.read().decode()[:500]
            if e.code in RETRY_STATUS and attempt + 1 < tries:
                ra = e.headers.get("Retry-After")
                delay = float(ra) if (ra or "").isdigit() else min(30.0, 1.5 ** attempt + random.random())
                STATS["retries"] += 1
                if not QUIET:
                    print("retry %d/%d after http %s in %.1fs" % (attempt + 1, tries - 1, e.code, delay),
                          file=sys.stderr)
                time.sleep(delay)
                continue
            raise APIError(e.code, body)
        except urllib.error.URLError as e:
            STATS["conn_errors"] += 1
            if attempt + 1 < tries:
                delay = min(30.0, 1.5 ** attempt + random.random())
                if not QUIET:
                    print("retry %d/%d after %s in %.1fs" % (attempt + 1, tries - 1, e.reason, delay),
                          file=sys.stderr)
                time.sleep(delay)
                continue
            raise APIError(0, "connection failed: %s" % (getattr(e, "reason", e),))


# ---------------------------------------------------------------- business --
def models(**kw):
    return _req("GET", "/v1/models", **kw)


def pricing(**kw):
    return _req("GET", "/v1/pricing", **kw)


def usage(days=7, **kw):
    return _req("GET", "/v1/usage?days=%d" % int(days), **kw)


def submit(prompt, model="MiniMax-Hailuo-2.3", resolution="1080P", duration=6, ratio="16:9",
           callback_url=None, idem=None, wait=False, retention_hours=None, **kw):
    body = {"prompt": prompt, "model": model, "resolution": resolution,
            "duration": duration, "ratio": ratio}
    if callback_url:
        body["callback_url"] = callback_url
    if retention_hours:
        body["retention_hours"] = retention_hours
    if wait:
        body["wait"] = True
        body["wait_timeout"] = kw.pop("wait_timeout", 900)
    hdrs = {"X-Idempotency-Key": idem} if idem else None
    return _req("POST", "/v1/videos/generations", body, headers=hdrs, timeout=900, **kw)


def get(tid, **kw):
    return _req("GET", "/v1/videos/generations/%s" % urllib.parse.quote(tid), **kw)


def status(tid, **kw):
    return _req("GET", "/v1/videos/%s/status" % urllib.parse.quote(tid), **kw)


def pages(**kw):
    """Every HTML/static page the service exposes (no request is made)."""
    return {"play": BASE + "/play", "gallery": BASE + "/gallery?key=<key>",
            "library": BASE + "/library?key=<key>", "metrics": BASE + "/metrics?key=<key>",
            "sdk": BASE + "/sdk/wzmm.py", "healthz": BASE + "/healthz",
            "openapi": BASE + "/openapi.json", "docs": BASE + "/docs"}


def meta(tid, **kw):
    return _req("GET", "/v1/videos/%s/meta" % urllib.parse.quote(tid), **kw)


def cancel(tid, **kw):
    return _req("POST", "/v1/videos/%s/cancel" % urllib.parse.quote(tid), **kw)
def pin(tid, **kw):
    """Keep this clip forever (janitor + disk GC skip pinned tasks)."""
    return _req("POST", "/v1/videos/%s/pin" % urllib.parse.quote(tid), **kw)
def unpin(tid, **kw):
    return _req("DELETE", "/v1/videos/%s/pin" % urllib.parse.quote(tid), **kw)


def wait_for(tid, timeout=1800, interval=5, key=None):
    end = time.time() + timeout
    while time.time() < end:
        j = get(tid, key=key)
        if j.get("status") in ("succeeded", "failed", "canceled"):
            return j
        time.sleep(interval)
    raise TimeoutError("task %s still running after %ss" % (tid, timeout))


def _content_url(path, key=None):
    k = key if key is not None else KEY
    return "%s%s?key=%s" % (BASE, path, urllib.parse.quote(k or ""))


def release(tid, **kw):
    return _req("POST", "/v1/videos/%s/release" % tid, {}, **kw)


def download(tid, path, key=None):
    """R653: stream /content with the same retry policy as every other call.

    The byte stream used to be a bare urlopen: one 503/429 from the content
    edge aborted a clip that had already been generated (credit spent, no file).
    Retries 429/5xx/connection errors with backoff, and writes to
    ``<path>.part`` first so a half file never looks like a finished clip.
    """
    url = _content_url("/v1/videos/%s/content" % urllib.parse.quote(tid), key)
    tries = max(1, RETRIES + 1)
    tmp = path + ".part"
    for attempt in range(tries):
        STATS["requests"] += 1
        try:
            with urllib.request.urlopen(url, timeout=600) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, path)
            return os.path.getsize(path)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                STATS["http_429"] += 1
            elif e.code >= 500:
                STATS["http_5xx"] += 1
            body = e.read().decode("utf-8", "replace")[:300]
            try:
                os.remove(tmp)
            except OSError:
                pass
            if e.code in RETRY_STATUS and attempt + 1 < tries:
                ra = e.headers.get("Retry-After")
                delay = float(ra) if (ra or "").isdigit() else min(30.0, 1.5 ** attempt + random.random())
                STATS["retries"] += 1
                if not QUIET:
                    print("retry %d/%d after http %s in %.1fs" %
                          (attempt + 1, tries - 1, e.code, delay), file=sys.stderr)
                time.sleep(delay)
                continue
            raise APIError(e.code, body)
        except urllib.error.URLError as e:
            STATS["conn_errors"] += 1
            try:
                os.remove(tmp)
            except OSError:
                pass
            if attempt + 1 < tries:
                delay = min(30.0, 1.5 ** attempt + random.random())
                if not QUIET:
                    print("retry %d/%d after %s in %.1fs" %
                          (attempt + 1, tries - 1, e.reason, delay), file=sys.stderr)
                time.sleep(delay)
                continue
            raise APIError(0, "connection failed: %s" % (getattr(e, "reason", e),))


def gen(prompt, out=None, model="MiniMax-Hailuo-2.3", resolution="1080P", duration=6,
        ratio="16:9", timeout=1800, interval=5, key=None, pin_clip=False):
    """One call: submit -> poll -> download. Returns id/status/path/bytes/elapsed_s.

    >>> gen("a cat surfing", out="cat.mp4")
    """
    t0 = time.time()
    j = submit(prompt, model=model, resolution=resolution, duration=duration,
               ratio=ratio, key=key)
    tid = j["id"]
    last = None
    end = t0 + max(30, int(timeout))
    while time.time() < end:
        try:
            last = get(tid, key=key)
        except Exception:
            last = last or {"status": "unknown"}
        if (last or {}).get("status") in ("succeeded", "failed", "canceled"):
            break
        time.sleep(max(1, int(interval)))
    st = (last or {}).get("status")
    rec = {"id": tid, "status": st, "model": model, "resolution": resolution,
           "duration": duration, "ratio": ratio, "elapsed_s": round(time.time() - t0, 1)}
    if st == "succeeded":
        path = out or ("%s.mp4" % tid)
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        rec["bytes"] = download(tid, path, key=key)
        rec["path"] = os.path.abspath(path)
        if pin_clip:
            try:
                pin(tid, key=key)
                rec["pinned"] = True
            except Exception:
                rec["pinned"] = False
    else:
        rec["error"] = (last or {}).get("error") or (last or {}).get("err")
        if last:
            rec["still_running"] = last.get("still_running")
    return rec


def thumb(tid, path, key=None):
    url = _content_url("/v1/videos/%s/thumb" % urllib.parse.quote(tid), key)
    with urllib.request.urlopen(url, timeout=120) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return os.path.getsize(path)


def events(tid, timeout=1800, key=None, out=sys.stdout):
    """Stream SSE /v1/videos/{id}/events until terminal or timeout."""
    url = "%s/v1/videos/%s/events" % (BASE, urllib.parse.quote(tid))
    r = urllib.request.Request(url)
    k = key if key is not None else KEY
    if k:
        r.add_header("Authorization", "Bearer " + k)
    end = time.time() + timeout
    with urllib.request.urlopen(r, timeout=max(30, timeout)) as resp:
        for raw in resp:
            if time.time() > end:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("data:"):
                payload = line[5:].strip()
                print(payload, file=out, flush=True)
                if any(s in payload for s in ('"succeeded"', '"failed"', '"canceled"')):
                    return payload
    return None


def bulk(items, defaults=None, dry_run=False, key=None, **kw):
    body = {"items": items, "dry_run": bool(dry_run)}
    if defaults:
        body["defaults"] = defaults
    return _req("POST", "/v1/videos/bulk", body, key=key, timeout=300, **kw)


def batch_status(batch_id, **kw):
    return _req("GET", "/v1/videos/batch/%s" % urllib.parse.quote(batch_id), **kw)


def batch_cancel(batch_id, **kw):
    return _req("POST", "/v1/videos/batch/%s/cancel" % urllib.parse.quote(batch_id), **kw)


def batch_create(items, defaults=None, dry_run=False, **kw):
    """POST /v1/videos/batch - hard cap 64 items per call."""
    body = {"items": items, "dry_run": bool(dry_run)}
    if defaults:
        body["defaults"] = defaults
    return _req("POST", "/v1/videos/batch", body, timeout=300, **kw)


def archive(batch_id="", ids="", pinned=-1, limit=50, out=None, **kw):
    """R613: zip of ready clips -> bytes or written to out."""
    q = {"limit": int(limit)}
    if batch_id:
        q["batch_id"] = batch_id
    if ids:
        q["ids"] = ids if isinstance(ids, str) else ",".join(ids)
    if pinned in (0, 1):
        q["pinned"] = pinned
    path = "/v1/videos/archive?" + urllib.parse.urlencode(q)
    data = _req("GET", path, raw=True, timeout=900, **kw)
    if out:
        with open(out, "wb") as f:
            f.write(data)
        return len(data)
    return data


def library_archive(ids="", limit=100, out=None, **kw):
    """R706: zip of hand-picked legacy-farm clips (ids may omit the .mp4 suffix)."""
    q = {"limit": max(1, min(int(limit), 100))}
    if ids:
        q["ids"] = ids if isinstance(ids, str) else ",".join(ids)
    data = _req("GET", "/v1/library/archive?" + urllib.parse.urlencode(q),
                raw=True, timeout=900, **kw)
    if out:
        with open(out, "wb") as f:
            f.write(data)
        return len(data)
    return data


def archive_job_drop(jid, **kw):
    """R706: delete a finished job and its zip immediately."""
    return _req("DELETE", "/v1/archive/jobs/" + urllib.parse.quote(jid), **kw)


def archive_job(kind="videos", ids=None, batch_id="", pinned=-1, limit=100,
                out=None, poll=0.7, timeout=1800, **kw):
    """R703: build the zip server-side, poll progress, then download.

    Returns (bytes, meta) when out is set, else (zip_bytes, meta). Survives
    browser-free use and the download itself supports HTTP Range.
    """
    body = {"kind": kind, "limit": int(limit)}
    if ids:
        body["ids"] = ids if isinstance(ids, list) else [x for x in str(ids).split(",") if x.strip()]
    if batch_id:
        body["batch_id"] = batch_id
    if pinned in (0, 1):
        body["pinned"] = pinned
    job = _req("POST", "/v1/archive/jobs", body, **kw)
    jid = job["job_id"]
    t0 = time.time()
    st = job
    while True:
        st = _req("GET", "/v1/archive/jobs/" + urllib.parse.quote(jid), **kw)
        state = st.get("state")
        if state == "ready":
            break
        if state == "failed":
            raise APIError(500, st.get("error") or "archive job failed")
        if time.time() - t0 > timeout:
            raise APIError(504, "job %s still %s after %ss" % (jid, state, timeout))
        if not QUIET and sys.stderr.isatty():
            sys.stderr.write("\r  %s %s%% %s MB" % (jid, st.get("pct", 0),
                                                    round(st.get("built", 0) / 1048576.0, 1)))
            sys.stderr.flush()
        time.sleep(poll)
    data = _req("GET", "/v1/archive/jobs/%s/file" % urllib.parse.quote(jid),
                raw=True, timeout=900, **kw)
    if out:
        with open(out, "wb") as f:
            f.write(data)
        return len(data), st
    return data, st


def video_list(limit=20, status="", cursor="", **kw):
    """Paginated list; pass the previous response's next_cursor to continue."""
    qs = "?limit=%d" % max(1, min(int(limit), 200))
    if status:
        qs += "&status=" + urllib.parse.quote(status)
    if cursor:
        qs += "&cursor=" + urllib.parse.quote(cursor)
    return _req("GET", "/v1/videos/generations" + qs, **kw)


def batch(prompts, **kw):
    return bulk([{"prompt": p} for p in prompts],
                defaults={k: v for k, v in kw.items() if v}, timeout=300)


def library(limit=50, offset=0, q="", res="", min_h=0, **kw):
    qs = "?limit=%d&offset=%d" % (int(limit), int(offset))
    if q:
        qs += "&q=" + urllib.parse.quote(q)
    if res:
        qs += "&res=" + urllib.parse.quote(res)
    if min_h:
        qs += "&min_h=%d" % int(min_h)
    return _req("GET", "/v1/library" + qs, **kw)


def library_stats(**kw):
    return _req("GET", "/v1/library/stats", **kw)


def library_meta(fid, **kw):
    return _req("GET", "/v1/library/%s/meta" % urllib.parse.quote(fid), **kw)


def library_download(fid, path, key=None):
    url = _content_url("/v1/library/%s/content" % urllib.parse.quote(fid), key)
    with urllib.request.urlopen(url, timeout=600) as resp, open(path, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return os.path.getsize(path)


def library_thumb(fid, path, key=None):
    url = _content_url("/v1/library/%s/thumb" % urllib.parse.quote(fid), key)
    with urllib.request.urlopen(url, timeout=120) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return os.path.getsize(path)


def manifest(res="1080p", min_h=0, fmt="txt", embed_key=False, limit=0, key=None):
    qs = "?format=%s&res=%s&min_h=%d&limit=%d" % (fmt, urllib.parse.quote(res), int(min_h), int(limit))
    if embed_key:
        qs += "&embed_key=1"
    return _req("GET", "/v1/library/manifest" + qs, key=key, raw=(fmt in ("txt", "csv")))


def tools(**kw):
    return _req("GET", "/v1/tools", **kw)


def tool(name, args=None, **kw):
    return _req("POST", "/v1/tools/call", {"name": name, "args": args or {}}, **kw)


def secret_get(**kw):
    return _req("GET", "/v1/webhook/secret", **kw)


def secret_set(secret=None, **kw):
    return _req("POST", "/v1/webhook/secret", {"secret": secret} if secret else {}, **kw)


def secret_del(**kw):
    return _req("DELETE", "/v1/webhook/secret", **kw)


def webhook_test(url, **kw):
    return _req("POST", "/v1/webhook/test", {"url": url}, **kw)


def verify_webhook(secret, headers, raw_body, tolerance=300):
    ts = headers.get("X-WZ-Timestamp") or headers.get("x-wz-timestamp") or ""
    sig = headers.get("X-WZ-Signature") or headers.get("x-wz-signature") or ""
    parts = dict(x.split("=", 1) for x in sig.split(",") if "=" in x)
    v1 = parts.get("v1", "")
    if not ts or not v1:
        return False
    want = hmac.new(secret.encode(), (ts + ".").encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(v1, want):
        return False
    return abs(time.time() - int(ts)) <= tolerance


# ------------------------------------------------------------------- admin --
def admin(path, method="GET", body=None, **kw):
    return _req(method, path, body, admin=True, **kw)


def admin_health(**kw): return admin("/admin/health", **kw)
def admin_alerts(limit=50, **kw): return admin("/admin/alerts?limit=%d" % int(limit), **kw)
def admin_alert_test(**kw): return admin("/admin/alert-test", "POST", {}, **kw)
def admin_alert(payload=None, **kw): return admin("/admin/alert", "POST", payload or {}, **kw)
def admin_pin(payload=None, **kw): return admin("/admin/pin", "POST", payload or {}, **kw)
def admin_retention(sample=10, **kw): return admin("/admin/retention?sample=%d" % int(sample), **kw)
def admin_pins(limit=100, **kw): return admin("/admin/pins?limit=%d" % int(limit), **kw)
def admin_gc_preview(limit=100, **kw): return admin("/admin/gc/preview?limit=%d" % int(limit), **kw)
def admin_orphans(limit=200, **kw): return admin("/admin/orphans?limit=%d" % int(limit), **kw)
def admin_orphans_import(payload=None, **kw): return admin("/admin/orphans/import", "POST", payload or {}, **kw)
def admin_verify_start(**kw): return admin("/admin/verify", "POST", {}, **kw)
def admin_verify(**kw): return admin("/admin/verify", **kw)
def admin_verify_report(payload=None, **kw): return admin("/admin/verify/report", "POST", payload or {}, **kw)
def admin_keys(**kw): return admin("/admin/keys", **kw)
def admin_ui(token=None, base=None):
    """URL of the browser key console (?token=<admin>); no request is sent."""
    t = ADMIN if token is None else token
    return "%s/admin/ui?token=%s" % ((base or BASE).rstrip("/"), urllib.parse.quote(t or ""))
def admin_key_create(name="default", rpm=60, max_concurrent=32, quota=0, daily_quota=0, **kw):
    return admin("/admin/keys", "POST", {"name": name, "rpm": rpm, "max_concurrent": max_concurrent,
                                         "quota": quota, "daily_quota": daily_quota}, **kw)
def admin_key_del(kid, hard=False, **kw):
    return admin("/admin/keys/%s%s" % (urllib.parse.quote(kid), "?hard=1" if hard else ""),
                 "DELETE", **kw)
def admin_key_update(kid, **fields):
    """PATCH /admin/keys/{kid}: name/rpm/max_concurrent/quota/daily_quota/disabled."""
    return admin("/admin/keys/%s" % urllib.parse.quote(kid), "PATCH", fields)
def admin_keys_stale(**kw): return admin("/admin/keys/stale", **kw)
def admin_pool(**kw): return admin("/admin/pool", **kw)
def admin_park(emails=None, n=0, seconds=120, **kw):
    return admin("/admin/park", "POST", {"emails": emails or [], "n": n, "seconds": seconds}, **kw)
def admin_unpark(**kw): return admin("/admin/unpark", "POST", {}, **kw)
def admin_tasks(limit=50, **kw): return admin("/admin/tasks?limit=%d" % int(limit), **kw)
def admin_stats(**kw): return admin("/admin/stats", **kw)
def admin_summary(**kw): return admin("/admin/summary", **kw)
def admin_capacity(**kw): return admin("/admin/capacity", **kw)
def admin_plan(target_per_hour=600, ttl_days=3, release=False, **kw):
    """release=True models the download->release loop (R636): server holds only
    the in-flight window, so 'feasible_now' answers today's real capacity."""
    return admin("/admin/plan?target_per_hour=%d&ttl_days=%d&release=%d"
                 % (int(target_per_hour), int(ttl_days), 1 if release else 0), **kw)
def admin_preflight(rate_per_hour=300, hours=8.0, ttl_days=3, outdir_free_gb=-1.0, **kw):
    """R640: GO / NO-GO before a long production run."""
    return admin("/admin/preflight?rate_per_hour=%d&hours=%g&ttl_days=%d&outdir_free_gb=%g"
                 % (int(rate_per_hour), float(hours), int(ttl_days), float(outdir_free_gb)), **kw)


def admin_forecast(refresh=False, **kw):
    """R688: pool runway forecast (expiry by day, producer rate, net daily change)."""
    return admin("/admin/forecast?refresh=%d" % (1 if refresh else 0), **kw)


def admin_producer_stats(refresh=False, **kw):
    """R691: account-producer health parsed from its log (success rate, pace, risk codes)."""
    return admin("/admin/producer-stats?refresh=%d" % (1 if refresh else 0), **kw)


def admin_keys_gc(names="", dry_run=True, max_age_h=0, **kw):
    """R687: drop test-harness keys that own no tasks.

    dry_run defaults to True. A real delete needs dry_run=False **and** max_age_h>=0.5,
    otherwise the server answers 400 on purpose (protects freshly created keys).
    """
    q = "?dry_run=%d" % (1 if dry_run else 0)
    if names:
        q += "&names=" + urllib.parse.quote(names)
    if max_age_h:
        q += "&max_age_h=%g" % float(max_age_h)
    return admin("/admin/keys/gc" + q, "POST", {}, **kw)
def admin_preflight_matrix(rates="300,600,1200", hours=8, ttl_days=3, outdir_free_gb=-1, **kw):
    """R647: GO/NO-GO for several rates in one call."""
    q = "rates=%s&hours=%s&ttl_days=%d&outdir_free_gb=%s" % (
        urllib.parse.quote(str(rates)), hours, int(ttl_days), outdir_free_gb)
    return admin("/admin/preflight/matrix?" + q, **kw)
def admin_runner(max_age_s=180, **kw):
    """R638: heartbeat of the long-running producer (runner.py)."""
    return admin("/admin/runner?max_age_s=%d" % int(max_age_s), **kw)
def admin_thumbs(**kw): return admin("/admin/thumbs", **kw)
def admin_thumbs_prewarm(workers=4, force=False, **kw):
    return admin("/admin/thumbs/prewarm?workers=%d&force=%d"
                 % (int(workers), 1 if force else 0), "POST", {}, **kw)
def admin_public(**kw): return admin("/admin/public", **kw)
def admin_public_run(**kw): return admin("/admin/public/run", "POST", {}, **kw)
def admin_webhook_queue(**kw): return admin("/admin/webhook/queue", **kw)
def admin_usage(days=7, by_key=False, **kw):
    return admin("/admin/usage?days=%d&by_key=%d" % (max(1, min(int(days), 90)), 1 if by_key else 0), **kw)
def admin_credits(**kw): return admin("/admin/credits", **kw)
def admin_checkin(dry_run=True, **kw):
    return admin("/admin/checkin", "POST", {"dry_run": bool(dry_run)}, **kw)
def admin_token_age(**kw): return admin("/admin/token-age", **kw)
def admin_liveness(**kw): return admin("/admin/apply-liveness", "POST", {}, **kw)
def admin_merge(tokens, **kw): return admin("/admin/merge-tokens", "POST", {"tokens": tokens}, **kw)
def admin_reload(**kw): return admin("/admin/reload-pool", "POST", {}, **kw)
def admin_revive(**kw): return admin("/admin/revive", "POST", {}, **kw)
def admin_gc(apply=False, res="", **kw):
    return admin("/admin/library/gc", "POST", {"apply": bool(apply), "res": res}, **kw)
def admin_webhook(**kw): return admin("/admin/webhook", **kw)
def admin_webhook_rotate(**kw): return admin("/admin/webhook/rotate", "POST", {}, **kw)


# --------------------------------------------------------------------- cli --
def bulk_download(items, outdir, model="MiniMax-Hailuo-2.3", resolution="1080P",
                  duration=6, ratio="16:9", workers=8, dry_run=False, poll=10):
    """One bulk submit -> poll batch -> download every succeeded item."""
    defaults = {"model": model, "resolution": resolution, "duration": duration, "ratio": ratio}
    r = bulk(items, defaults=defaults, dry_run=dry_run)
    if dry_run:
        return r
    bid = r["batch_id"]
    total = int(r.get("accepted") or 0)
    os.makedirs(outdir, exist_ok=True)
    last = None
    st = {}
    while True:
        st = batch_status(bid)
        sm = st.get("summary") or {}
        done = int((st.get("progress") or {}).get("done", 0))
        if not QUIET and done != last:
            print("[%s] %d/%d succeeded=%d failed=%d" % (bid, done, total,
                  sm.get("succeeded", 0), sm.get("failed", 0)), file=sys.stderr, flush=True)
            last = done
        if done >= total:
            break
        time.sleep(max(2, poll))
    rows = [rw for rw in (st.get("data") or []) if rw.get("status") == "succeeded"]
    files, errs, lock = [], [], threading.Lock()
    sem = threading.Semaphore(max(1, workers))

    def one(i, rw):
        with sem:
            try:
                p = os.path.join(outdir, "%04d_%s.mp4" % (i, rw["id"]))
                n = download(rw["id"], p)
                rec = {"id": rw["id"], "path": p, "bytes": n}
            except Exception as e:
                rec = {"id": rw.get("id"), "error": repr(e)[:200]}
        with lock:
            (files if "path" in rec else errs).append(rec)
            if not QUIET:
                print(json.dumps(rec, ensure_ascii=False), file=sys.stderr, flush=True)

    ths = [threading.Thread(target=one, args=(i, rw)) for i, rw in enumerate(rows)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return {"batch": bid, "total": total, "succeeded": len(rows),
            "downloaded": len(files), "failed": len(errs) + (total - len(rows)),
            "bytes": sum(f["bytes"] for f in files), "errors": errs[:20]}


def _run_pool(prompts, workers, outdir, resolution, duration, ratio, model):
    sem = threading.Semaphore(workers)
    results, lock = [], threading.Lock()

    def one(i, p):
        with sem:
            try:
                j = submit(p, model=model, resolution=resolution, duration=duration, ratio=ratio)
                tid = j["id"]
                final = wait_for(tid, timeout=2400)
                if final.get("status") == "succeeded":
                    out = os.path.join(outdir, "%03d_%s.mp4" % (i, tid))
                    n = download(tid, out)
                    rec = {"i": i, "id": tid, "ok": True, "path": out, "bytes": n}
                else:
                    rec = {"i": i, "id": tid, "ok": False, "error": final.get("error") or final.get("status")}
            except Exception as e:
                rec = {"i": i, "ok": False, "error": repr(e)[:200]}
        with lock:
            results.append(rec)
            if not QUIET:
                print(json.dumps(rec, ensure_ascii=False), flush=True)

    ths = [threading.Thread(target=one, args=(i, p)) for i, p in enumerate(prompts)]
    for t in ths:
        t.start()
        time.sleep(0.15)
    for t in ths:
        t.join()
    ok = sum(1 for r in results if r.get("ok"))
    return ok, results


def _dump(obj, indent=2):
    if COMPACT and not isinstance(obj, bytes):
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        return
    if isinstance(obj, bytes):
        sys.stdout.buffer.write(obj)
        if not obj.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
        return
    print(json.dumps(obj, ensure_ascii=False, indent=indent))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wzmm", description="WZ MiniMax video API client (53 endpoints)")
    ap.add_argument("--base", help="override WZMM_BASE")
    ap.add_argument("--key", help="override WZMM_KEY")
    ap.add_argument("--admin-token", help="override WZMM_ADMIN")
    ap.add_argument("--retries", type=int, default=None, help="retries on 429/5xx/conn (default env WZMM_RETRIES=2)")
    ap.add_argument("--timeout", type=int, default=None, help="default request timeout seconds")
    ap.add_argument("--quiet", action="store_true", help="suppress progress output")
    ap.add_argument("--json", action="store_true", help="compact single-line JSON only (implies --quiet)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kwargs):
        return sub.add_parser(name, **kwargs)

    add("models"); add("pricing"); add("tools")
    p = add("tool"); p.add_argument("name"); p.add_argument("args", nargs="?", default="{}")

    p = add("submit"); p.add_argument("prompt"); p.add_argument("-o", "--out")
    p.add_argument("--res", default="1080P"); p.add_argument("--dur", type=int, default=6)
    p.add_argument("--ratio", default="16:9"); p.add_argument("--model", default="MiniMax-Hailuo-2.3")
    p.add_argument("--callback"); p.add_argument("--idem"); p.add_argument("--wait", action="store_true")

    p = add("get"); p.add_argument("id")
    p = add("status"); p.add_argument("id")
    add("pages")
    p = add("meta"); p.add_argument("id")
    p = add("cancel"); p.add_argument("id")
    p = add("archive")
    p.add_argument("--batch-id", default="")
    p.add_argument("--ids", default="")
    p.add_argument("--pinned", type=int, default=-1)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", default="")
    p = add("libarchive"); p.add_argument("--ids", default=""); p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", default="")
    p = add("job"); p.add_argument("--kind", default="videos", choices=["videos", "library"])
    p.add_argument("--ids", default=""); p.add_argument("--batch-id", default="")
    p.add_argument("--pinned", type=int, default=-1); p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", default=""); p.add_argument("--poll", type=float, default=0.7)
    p.add_argument("--timeout", type=int, default=1800)
    p = add("pin"); p.add_argument("id")
    p = add("unpin"); p.add_argument("id")
    p = add("wait"); p.add_argument("id"); p.add_argument("--timeout", type=int, default=1800)
    p = add("download"); p.add_argument("id"); p.add_argument("-o", "--out")
    p = add("thumb"); p.add_argument("id"); p.add_argument("-o", "--out", default="thumb.jpg")
    p = add("events"); p.add_argument("id"); p.add_argument("--timeout", type=int, default=1800)

    p = add("usage"); p.add_argument("--days", type=int, default=7)
    p = add("batch"); p.add_argument("file"); p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--outdir", default="out"); p.add_argument("--res", default="1080P")
    p.add_argument("--dur", type=int, default=6); p.add_argument("--ratio", default="16:9")
    p.add_argument("--model", default="MiniMax-Hailuo-2.3")
    p = add("bulk"); p.add_argument("file", help="JSON file: [prompt,...] or [{prompt,...},...]")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--chunk", type=int, default=64)
    p = add("batch-status"); p.add_argument("batch_id")
    p = add("batch-cancel"); p.add_argument("batch_id")
    p = add("batch-create"); p.add_argument("file"); p.add_argument("--dry-run", action="store_true")
    p = add("bulk-download"); p.add_argument("file"); p.add_argument("--outdir", default="out")
    p.add_argument("--workers", type=int, default=8); p.add_argument("--model", default="MiniMax-Hailuo-2.3")
    p.add_argument("--res", default="1080P"); p.add_argument("--dur", type=int, default=6)
    p.add_argument("--ratio", default="16:9"); p.add_argument("--dry-run", action="store_true")
    p = add("video-list"); p.add_argument("--limit", type=int, default=20)
    p.add_argument("--status", default=""); p.add_argument("--cursor", default="")

    p = add("gen", help="submit -> wait -> download in one command (1080P default)")
    p.add_argument("prompt"); p.add_argument("-o", "--out")
    p.add_argument("--model", default="MiniMax-Hailuo-2.3"); p.add_argument("--res", default="1080P")
    p.add_argument("--dur", type=int, default=6); p.add_argument("--ratio", default="16:9")
    p.add_argument("--timeout", type=int, default=1800); p.add_argument("--interval", type=int, default=5)
    p.add_argument("--pin", action="store_true", help="pin the clip so GC never deletes it")

    p = add("library"); p.add_argument("--limit", type=int, default=20); p.add_argument("--offset", type=int, default=0)
    p.add_argument("--q", default=""); p.add_argument("--res", default=""); p.add_argument("--min-h", type=int, default=0)
    add("library-stats")
    p = add("library-meta"); p.add_argument("id")
    p = add("library-download"); p.add_argument("id"); p.add_argument("-o", "--out")
    p = add("library-thumb"); p.add_argument("id"); p.add_argument("-o", "--out", default="thumb.jpg")
    p = add("manifest"); p.add_argument("--res", default="1080p"); p.add_argument("--min-h", type=int, default=0)
    p.add_argument("--format", default="txt", choices=("txt", "csv", "json"))
    p.add_argument("--embed-key", action="store_true"); p.add_argument("--limit", type=int, default=0)
    p.add_argument("-o", "--out")

    p = add("secret"); p.add_argument("--set"); p.add_argument("--delete", action="store_true")
    p = add("webhook-test"); p.add_argument("url")
    p = add("webhook-verify"); p.add_argument("--secret", required=True)
    p.add_argument("--ts"); p.add_argument("--sig")

    add("admin-health"); add("admin-alerts"); add("admin-alert-test"); add("admin-alert"); add("admin-keys")
    add("admin-verify-start"); add("admin-verify"); add("admin-verify-report")
    add("admin-pin"); add("admin-retention"); add("admin-pins"); add("admin-gc-preview")
    add("admin-orphans"); add("admin-orphans-import")
    add("admin-capacity")
    p = add("admin-key-create"); p.add_argument("--name", default="default"); p.add_argument("--rpm", type=int, default=60)
    p.add_argument("--max-concurrent", type=int, default=32); p.add_argument("--quota", type=int, default=0)
    p.add_argument("--daily-quota", type=int, default=0)
    p = add("admin-key-del"); p.add_argument("kid")
    p = add("admin-key-update"); p.add_argument("kid")
    p.add_argument("--name"); p.add_argument("--rpm", type=int); p.add_argument("--max-concurrent", type=int)
    p.add_argument("--quota", type=int); p.add_argument("--daily-quota", type=int)
    p.add_argument("--disable", action="store_true"); p.add_argument("--enable", action="store_true")
    add("admin-pool")
    p = add("admin-park"); p.add_argument("--emails", default=""); p.add_argument("--n", type=int, default=0)
    p.add_argument("--seconds", type=int, default=120)
    add("admin-unpark"); p = add("admin-tasks"); p.add_argument("--limit", type=int, default=50)
    add("admin-public"); add("admin-public-run")
    add("admin-thumbs")
    add("admin-keys-stale")
    p = add("admin-thumbs-prewarm"); p.add_argument("--workers", type=int, default=4)
    p.add_argument("--force", action="store_true")
    add("admin-webhook-queue")
    p = add("admin-usage"); p.add_argument("--days", type=int, default=7)
    p.add_argument("--by-key", action="store_true")
    p = add("admin-checkin"); p.add_argument("--apply", action="store_true")
    add("admin-token-age"); add("admin-liveness"); add("admin-reload"); add("admin-revive")
    p = add("admin-gc"); p.add_argument("--apply", action="store_true"); p.add_argument("--res", default="")
    add("admin-webhook"); add("admin-webhook-rotate")
    p = add("admin-merge"); p.add_argument("file")

    p = add("api", help="generic passthrough for any of the 53 endpoints")
    p.add_argument("method"); p.add_argument("path"); p.add_argument("body", nargs="?", default="")
    p.add_argument("--admin", action="store_true")

    a = ap.parse_args(argv)
    if a.base:
        globals()["BASE"] = a.base.rstrip("/")
    if a.key:
        globals()["KEY"] = a.key
    if a.admin_token:
        globals()["ADMIN"] = a.admin_token
    if a.retries is not None:
        globals()["RETRIES"] = max(0, a.retries)
    if a.timeout:
        globals()["TIMEOUT"] = a.timeout
    if a.quiet:
        globals()["QUIET"] = True
    if a.json:
        globals()["COMPACT"] = True
        globals()["QUIET"] = True

    c = a.cmd
    if c == "models": _dump(models()); return 0
    if c == "pricing": _dump(pricing()); return 0
    if c == "tools": _dump(tools()); return 0
    if c == "tool": _dump(tool(a.name, json.loads(a.args))); return 0
    if c == "usage": _dump(usage(a.days)); return 0

    if c == "submit":
        j = submit(a.prompt, model=a.model, resolution=a.res, duration=a.dur, ratio=a.ratio,
                   callback_url=a.callback, idem=a.idem, wait=a.wait)
        _dump(j)
        if a.out:
            tid = j["id"]
            f = wait_for(tid)
            if f.get("status") == "succeeded":
                n = download(tid, a.out)
                print("saved %s (%d bytes)" % (a.out, n), file=sys.stderr); return 0
            print("failed: %s" % (f.get("error") or f.get("status")), file=sys.stderr); return 1
        return 0
    if c == "gen":
        _dump(gen(a.prompt, a.out, a.model, a.res, a.dur, a.ratio, a.timeout,
                  a.interval, pin_clip=a.pin)); return 0
    if c == "get": _dump(get(a.id)); return 0
    if c == "status": _dump(status(a.id)); return 0
    if c == "pages": _dump(pages()); return 0
    if c == "meta": _dump(meta(a.id)); return 0
    if c == "cancel": _dump(cancel(a.id)); return 0
    if c == "archive":
        n = archive(batch_id=a.batch_id, ids=a.ids, pinned=a.pinned, limit=a.limit,
                    out=(a.out or "wzmm_archive.zip"), admin=bool(a.admin_token))
        print("wrote %s (%d bytes)" % (a.out or "wzmm_archive.zip", n)); return 0
    if c == "libarchive":
        if not a.ids:
            print("--ids required", file=sys.stderr); return 2
        out = a.out or "wzlib.zip"
        print("wrote %s (%d bytes)" % (out, library_archive(ids=a.ids, limit=a.limit, out=out))); return 0
    if c == "job":
        if not a.ids and not a.batch_id:
            print("--ids or --batch-id required", file=sys.stderr); return 2
        out = a.out or ("wzmm_%s.zip" % a.kind)
        n, meta = archive_job(kind=a.kind, ids=a.ids, batch_id=a.batch_id, pinned=a.pinned,
                              limit=a.limit, out=out, poll=a.poll, timeout=a.timeout)
        print("wrote %s (%d bytes, %d files)" % (out, n, meta.get("count", 0))); return 0
    if c == "pin": _dump(pin(a.id, admin=bool(a.admin_token))); return 0
    if c == "unpin": _dump(unpin(a.id, admin=bool(a.admin_token))); return 0
    if c == "wait": _dump(wait_for(a.id, timeout=a.timeout)); return 0
    if c == "download":
        out = a.out or ("%s.mp4" % a.id)
        print("%s (%d bytes)" % (out, download(a.id, out))); return 0
    if c == "thumb":
        print("%s (%d bytes)" % (a.out, thumb(a.id, a.out))); return 0
    if c == "events": events(a.id, timeout=a.timeout); return 0

    if c == "batch":
        prompts = [l.strip() for l in open(a.file, encoding="utf-8") if l.strip()]
        os.makedirs(a.outdir, exist_ok=True)
        print("prompts=%d concurrency=%d" % (len(prompts), a.concurrency), file=sys.stderr)
        ok, _ = _run_pool(prompts, a.concurrency, a.outdir, a.res, a.dur, a.ratio, a.model)
        print(json.dumps({"ok": ok, "total": len(prompts)}, ensure_ascii=False))
        return 0 if ok else 1
    if c == "bulk":
        items = json.load(open(a.file, encoding="utf-8"))
        if isinstance(items, str):
            items = [items]
        items = [{"prompt": x} if isinstance(x, str) else x for x in items]
        _dump(bulk(items, dry_run=a.dry_run, chunk=a.chunk) if False else bulk(items, dry_run=a.dry_run)); return 0
    if c == "batch-status": _dump(batch_status(a.batch_id)); return 0
    if c == "batch-cancel": _dump(batch_cancel(a.batch_id)); return 0
    if c == "batch-create":
        items = json.load(open(a.file, encoding="utf-8"))
        items = [{"prompt": x} if isinstance(x, str) else x for x in items]
        _dump(batch_create(items, dry_run=a.dry_run)); return 0
    if c == "video-list": _dump(video_list(a.limit, a.status, a.cursor)); return 0
    if c == "bulk-download":
        items = json.load(open(a.file, encoding="utf-8"))
        items = [{"prompt": x} if isinstance(x, str) else x for x in items]
        _dump(bulk_download(items, a.outdir, model=a.model, resolution=a.res, duration=a.dur,
                            ratio=a.ratio, workers=a.workers, dry_run=a.dry_run))
        return 0

    if c == "library": _dump(library(a.limit, a.offset, a.q, a.res, a.min_h)); return 0
    if c == "library-stats": _dump(library_stats()); return 0
    if c == "library-meta": _dump(library_meta(a.id)); return 0
    if c == "library-download":
        out = a.out or ("%s.mp4" % a.id)
        print("%s (%d bytes)" % (out, library_download(a.id, out))); return 0
    if c == "library-thumb":
        print("%s (%d bytes)" % (a.out, library_thumb(a.id, a.out))); return 0
    if c == "manifest":
        out = manifest(a.res, a.min_h, a.format, a.embed_key, a.limit)
        text = out.decode() if isinstance(out, bytes) else json.dumps(out, ensure_ascii=False, indent=1)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text if text.endswith("\n") else text + "\n")
            print("%s (%d lines)" % (a.out, len([x for x in text.splitlines() if x.strip()])), file=sys.stderr); return 0
        sys.stdout.write(text if text.endswith("\n") else text + "\n"); return 0

    if c == "secret":
        if a.delete: _dump(secret_del()); return 0
        if a.set: _dump(secret_set(a.set)); return 0
        _dump(secret_get()); return 0
    if c == "webhook-test": _dump(webhook_test(a.url)); return 0
    if c == "webhook-verify":
        body = sys.stdin.buffer.read()
        ok = verify_webhook(a.secret, {"X-WZ-Timestamp": a.ts or "", "X-WZ-Signature": a.sig or ""}, body)
        print("valid" if ok else "invalid"); return 0 if ok else 1

    if c == "admin-health": _dump(admin_health()); return 0
    if c == "admin-alerts": _dump(admin_alerts()); return 0
    if c == "admin-alert-test": _dump(admin_alert_test()); return 0
    if c == "admin-alert": _dump(admin_alert({"key": "cli", "msg": "cli alert", "level": "info"})); return 0
    if c == "admin-verify-start": _dump(admin_verify_start()); return 0
    if c == "admin-verify": _dump(admin_verify()); return 0
    if c == "admin-verify-report": _dump(admin_verify_report({})); return 0
    if c == "admin-pin": _dump(admin_pin({})); return 0
    if c == "admin-retention": _dump(admin_retention()); return 0
    if c == "admin-pins": _dump(admin_pins()); return 0
    if c == "admin-gc-preview": _dump(admin_gc_preview()); return 0
    if c == "admin-orphans": _dump(admin_orphans()); return 0
    if c == "admin-orphans-import": _dump(admin_orphans_import({})); return 0
    if c == "admin-capacity": _dump(admin_capacity()); return 0
    if c == "admin-keys": _dump(admin_keys()); return 0
    if c == "admin-key-create":
        _dump(admin_key_create(a.name, a.rpm, a.max_concurrent, a.quota, a.daily_quota)); return 0
    if c == "admin-key-del": _dump(admin_key_del(a.kid)); return 0
    if c == "admin-key-update":
        f = {}
        for name in ("name", "rpm", "max_concurrent", "quota", "daily_quota"):
            v = getattr(a, name, None)
            if v is not None:
                f[name] = v
        if a.disable: f["disabled"] = True
        if a.enable: f["disabled"] = False
        if not f: raise SystemExit("nothing to update (pass --rpm/--quota/... or --disable/--enable)")
        _dump(admin_key_update(a.kid, **f)); return 0
    if c == "admin-pool": _dump(admin_pool()); return 0
    if c == "admin-park":
        emails = [x.strip() for x in a.emails.split(",") if x.strip()]
        _dump(admin_park(emails, a.n, a.seconds)); return 0
    if c == "admin-unpark": _dump(admin_unpark()); return 0
    if c == "admin-tasks": _dump(admin_tasks(a.limit)); return 0
    if c == "admin-stats": _dump(admin_stats()); return 0
    if c == "admin-summary": _dump(admin_summary()); return 0
    if c == "admin-capacity": _dump(admin_capacity()); return 0
    if c == "admin-keys-stale": _dump(admin_keys_stale()); return 0
    if c == "admin-thumbs": _dump(admin_thumbs()); return 0
    if c == "admin-thumbs-prewarm": _dump(admin_thumbs_prewarm(a.workers, a.force)); return 0
    if c == "admin-public": _dump(admin_public()); return 0
    if c == "admin-public-run": _dump(admin_public_run()); return 0
    if c == "admin-webhook-queue": _dump(admin_webhook_queue()); return 0
    if c == "admin-usage": _dump(admin_usage(a.days, a.by_key)); return 0
    if c == "admin-credits": _dump(admin_credits()); return 0
    if c == "admin-checkin": _dump(admin_checkin(not a.apply)); return 0
    if c == "admin-token-age": _dump(admin_token_age()); return 0
    if c == "admin-liveness": _dump(admin_liveness()); return 0
    if c == "admin-reload": _dump(admin_reload()); return 0
    if c == "admin-revive": _dump(admin_revive()); return 0
    if c == "admin-gc": _dump(admin_gc(a.apply, a.res)); return 0
    if c == "admin-webhook": _dump(admin_webhook()); return 0
    if c == "admin-webhook-rotate": _dump(admin_webhook_rotate()); return 0
    if c == "admin-merge":
        _dump(admin_merge(json.load(open(a.file, encoding="utf-8")))); return 0

    if c == "api":
        body = json.loads(a.body) if a.body else None
        is_admin = a.admin or a.path.startswith("/admin/")
        _dump(_req(a.method.upper(), a.path, body, admin=is_admin)); return 0
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except APIError as e:
        print("error: http %s: %s" % (e.status, str(e.body)[:400]), file=sys.stderr)
        sys.exit(1)
    except (TimeoutError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)