# -*- coding: utf-8 -*-
"""Server-side MiniMax _token renewal (no PC / no browser).

Two paths, both verified from this host through the 1024proxy exit
(account.minimax.io answers Akamai 403 to the datacenter IP directly):

  A) refreshToken -> securetoken.googleapis.com  -> /oauth2/login loginType=11
  B) password     -> RSA PKCS1v15(password)      -> /oauth2/login loginType=20
  both then  -> /oauth2/authorize?client_id=hub&redirect_uri=design.minimax.io
             -> Set-Cookie _token   (exp = now + 40.000d)

Note: client_id=agent-minimax + source=agent_web now 500s at the callback;
      client_id=hub + design.minimax.io is the variant that issues _token.

Usage:
  renew.py --days 25                  # renew anything with <25d left
  renew.py --days 25 --mode pw        # password accounts only
  renew.py --force --limit 2          # trial
  renew.py --days 25 --dry-run
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import uuid as U
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cr

POOL = os.environ.get("WZMM_POOL", "/opt/wzmm/pool.json")
API = os.environ.get("WZMM_LOCAL", "http://127.0.0.1:8808")
ADMIN_KEY_FILE = "/opt/wzmm/admin.key"
LOG = "/var/log/wzmm-renew.log"

FB_KEY = os.environ.get("MM_FB_KEY", "AIzaSy" + "REPLACE_WITH_PUBLIC_FIREBASE_WEB_KEY")
SALT = "I*7Cf%WZ#S&%1RlZJ&C2"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PROXY_TMPL = os.environ.get(
    "PX_TMPL",
    "http://user000000-region-XX-sid-{sid}-t-5:REPLACE_ME@us.1024proxy.io:3000")
ACC = "https://account.minimax.io"

PUB = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF5ndG2/UB4L5tbvQaNLHSoBTW
DKbrNBuOmUIP23eCmC2ELMx3kppEikxTp5cV8NxUZl6ii+KLwKugioAXApzypHXb
gXbq13kTKA7OCA1xtAoMdH9cltjBiFAUJlgmVjr0MuJCknhVAjWLjCVRHege+Atl
gkUBUeGa9O+cWcPEwQIDAQAB
-----END PUBLIC KEY-----"""

sys.path.insert(0, "/opt/wzmm")
try:
    import agent_mm as A
except Exception:  # pragma: no cover
    A = None

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    _PUBKEY = serialization.load_pem_public_key(PUB)
except Exception:  # pragma: no cover
    _PUBKEY = None


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def new_proxy():
    return PROXY_TMPL.format(sid="wzr" + secrets.token_hex(4))


def jwt_exp(tok):
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return int(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0)
    except Exception:
        return 0


def enc_pw(pw):
    return base64.b64encode(_PUBKEY.encrypt(pw.encode(), padding.PKCS1v15())).decode()


def sso_post(sess, path, body, dev, uu):
    ms = int(time.time() * 1000)
    p = {"device_platform": "web", "biz_id": 3, "app_id": 3001, "version_code": 22201, "unix": ms,
         "timezone_offset": 480, "sys_language": "en", "uuid": uu, "device_id": dev or 0,
         "os_name": "Windows", "browser_name": "Chrome", "device_memory": 8, "cpu_core_num": 8,
         "browser_language": "en-US", "browser_platform": "Win32", "user_id": 0,
         "screen_width": 1920, "screen_height": 1080, "client": "web"}
    full = path + "?" + urllib.parse.urlencode(p)
    bs = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    ts = int(time.time())
    h = {"Content-Type": "application/json",
         "yy": md5(urllib.parse.quote(full, safe="-_.!~*'()") + "_" + bs + md5(str(ts)) + "ooui"),
         "x-timestamp": str(ts), "x-signature": md5(str(ts) + SALT + bs),
         "Accept": "application/json, text/plain, */*", "User-Agent": UA,
         "Origin": ACC, "Referer": ACC + "/unified-login"}
    return sess.post(ACC + full, data=bs.encode(), headers=h, timeout=40)


def mint_token(sess):
    st = urllib.parse.quote(json.dumps({"csrf": "wz"}), safe="")
    au = (ACC + "/oauth2/authorize?client_id=hub&redirect_uri="
          + urllib.parse.quote("https://design.minimax.io/auth/callback", safe="")
          + "&response_type=code&state=" + st)
    r = sess.get(au, allow_redirects=False, timeout=35)
    loc = r.headers.get("location", "")
    if loc.startswith("http"):
        sess.get(loc, allow_redirects=False, timeout=35)
    return sess.cookies.get("_token") or ""


def login(sess, acc, uu):
    """Returns (ruid, status). status == 'ok' on success."""
    dev = ""
    r = sso_post(sess, "/v1/api/user/device/register", {"uuid": uu}, "", uu)
    try:
        dev = str((r.json().get("data") or {}).get("deviceID") or "")
    except Exception:
        pass
    rt = acc.get("refreshToken")
    if rt:
        r = sess.post("https://securetoken.googleapis.com/v1/token?key=" + FB_KEY,
                      data={"grant_type": "refresh_token", "refresh_token": rt}, timeout=35)
        if r.status_code != 200:
            return "", "securetoken-%d" % r.status_code
        jt = r.json() or {}
        idt = jt.get("id_token") or ""
        # Google may rotate the refresh token on every exchange; keep the newest one
        # even if the following /oauth2/login fails, otherwise the stored RT goes stale.
        rt_new = jt.get("refresh_token") or ""
        if rt_new:
            acc["_rt_new"] = rt_new
        if not idt:
            return "", "no-id-token"
        r = sso_post(sess, "/oauth2/login",
                     {"loginType": "11", "authToken": idt, "deviceID": dev}, dev, uu)
        tag = "login-rt"
    elif acc.get("password") and _PUBKEY is not None:
        r = sso_post(sess, "/oauth2/login",
                     {"loginType": "20", "email": acc["email"],
                      "authToken": enc_pw(acc["password"]), "deviceID": dev}, dev, uu)
        tag = "login-pw"
    else:
        return "", "no-credential"
    try:
        j = r.json() if r.status_code == 200 else {}
    except Exception:
        j = {}
    if j.get("code") != 0:
        return "", "%s-%s" % (tag, j.get("code") if j else r.status_code)
    d = j.get("data") or {}
    return str(d.get("str_biz_uid") or d.get("user_id") or ""), "ok"


def renew_one(acc, tries=4):
    email = acc.get("email")
    if not acc.get("refreshToken") and not acc.get("password"):
        return email, "no-credential", None
    last = "unknown"
    for _ in range(tries):
        proxy = new_proxy()
        try:
            s = cr.Session(impersonate="chrome131", timeout=45,
                           proxies={"http": proxy, "https": proxy})
            uu = str(U.uuid4())
            ruid, st = login(s, acc, uu)
            if st != "ok":
                last = st
                if "login-pw" in st or "login-rt" in st:
                    # credential side rejected: retrying another IP will not help
                    if st.split("-")[-1] not in ("0",):
                        return email, st, None
                continue
            tok = mint_token(s)
            if not tok:
                last = "no-token"
                continue
            exp = jwt_exp(tok)
            if exp <= time.time():
                last = "expired-token"
                continue
            if A is not None:
                try:
                    vr = A.call(A.sess(None), "GET", A.CLOUD + "/signin/status", tok, ruid,
                                A.profile_for(email), timeout=40)
                    if vr.status_code not in (200, 204):
                        last = "verify-%d" % vr.status_code
                        continue
                except Exception as e:
                    last = "verify-exc:%s" % repr(e)[:40]
                    continue
            return email, "ok", {"token": tok, "ruid": ruid, "exp": exp}
        except Exception as e:
            last = "exc:%s" % repr(e)[:60]
            time.sleep(0.8)
    return email, last, None


def reload_pool():
    try:
        key = open(ADMIN_KEY_FILE).read().strip()
        req = urllib.request.Request(API + "/admin/reload-pool", data=b"", method="POST")
        req.add_header("X-Admin-Token", key)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        log("reload-pool: ok accounts=%s total=%s" % (body.get("accounts"),
                                                     (body.get("stats") or {}).get("total")))
        return True
    except Exception as e:
        log("reload-pool FAILED: %r" % (e,))
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=25.0)
    ap.add_argument("--mode", choices=("all", "rt", "pw"), default="all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-reload", action="store_true")
    args = ap.parse_args()

    data = json.load(open(POOL, encoding="utf-8"))
    accs = data["accounts"] if isinstance(data, dict) else data
    now = time.time()
    cut = now + args.days * 86400

    def wanted(a):
        if args.mode == "rt" and not a.get("refreshToken"):
            return False
        if args.mode == "pw" and not a.get("password"):
            return False
        if not a.get("refreshToken") and not a.get("password"):
            return False
        return args.force or (jwt_exp(a.get("token") or "") or 0) < cut

    targets = [a for a in accs if wanted(a)]
    if args.limit:
        targets = targets[:args.limit]
    log("renew start: targets=%d/%d cutoff=%.0fd mode=%s workers=%d dry=%s"
        % (len(targets), len(accs), args.days, args.mode, args.workers, args.dry_run))
    if not targets:
        return 0
    if args.dry_run:
        for a in targets[:10]:
            e = jwt_exp(a.get("token") or "")
            log("  %s exp %s (%.1fd) via %s" % (a["email"], time.strftime("%Y-%m-%d", time.gmtime(e)),
                                                (e - now) / 86400,
                                                "rt" if a.get("refreshToken") else "pw"))
        return 0

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        res = list(ex.map(renew_one, targets))
    upd, stats = {}, {}
    rt_changed = 0
    for a in accs:
        if a.get("_rt_new"):
            if a["_rt_new"] != a.get("refreshToken"):
                rt_changed += 1
            a["refreshToken"] = a["_rt_new"]
            a.pop("_rt_new", None)
    for em, st, rec in res:
        stats[st] = stats.get(st, 0) + 1
        if st == "ok" and rec:
            upd[em] = rec
    fails = [(em, st) for em, st, rec in res if st != "ok"]
    for a in accs:
        u = upd.get(a.get("email"))
        if u:
            a["token"] = u["token"]
            a["ruid"] = u["ruid"]
            a["alive"] = True
            a["usable"] = True
            a["exp"] = u["exp"]
            a["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            a["revived_by"] = "refresh_token_srv" if a.get("refreshToken") else "password_srv"
    if upd or rt_changed:
        tmp = POOL + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, POOL)
        log("pool written: %s (+%d, rt_rotated=%d)" % (POOL, len(upd), rt_changed))
    log("renew done in %.0fs updated=%d %s" % (time.time() - t0, len(upd), stats))
    if fails:
        log("failures: %s" % json.dumps(fails[:12], ensure_ascii=False))
    if upd and not args.no_reload:
        reload_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())