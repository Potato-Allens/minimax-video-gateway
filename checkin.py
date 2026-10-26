#!/opt/wzmm/venv/bin/python
# -*- coding: utf-8 -*-
"""Daily sign-in / claim for the MiniMax account pool.

Reads /opt/wzmm/pool.json, calls the official sign-in endpoints through
agent_mm.py, optionally claims today's reward, and atomically rewrites the pool.

Default is dry-run. Use --write to claim and persist.
"""
import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_mm


def today_entry(days):
    for d in days or []:
        if isinstance(d, dict) and d.get("is_today"):
            return d
    return None


def one_account(a, do_claim):
    email = str(a.get("email") or "")
    token = str(a.get("token") or "")
    ruid = str(a.get("ruid") or "")
    rec = {"email": email, "result": "unknown", "awarded": 0}
    if not token or not ruid:
        rec["result"] = "no_credentials"
        return rec
    try:
        prof = agent_mm.profile_for(email)
        s = agent_mm.sess()
        r = agent_mm.call(s, "GET", agent_mm.CLOUD + "/signin/status",
                          token, ruid, prof, timeout=30)
        rec["status_http"] = r.status_code
        if r.status_code != 200:
            rec["result"] = "status_http_%s" % r.status_code
            rec["body"] = (r.text or "")[:180]
            return rec
        j = r.json()
        data = j.get("data") or {}
        days = data.get("days") or []
        t = today_entry(days)
        rec["scene"] = data.get("scene")
        rec["days_n"] = len(days)
        if not t:
            rec["result"] = "no_today"
            return rec
        rec["day_no"] = t.get("day_no")
        rec["day_status"] = t.get("status")
        rec["day_points"] = t.get("points")
        if t.get("status") == 3:
            rec["result"] = "already"
            return rec
        if t.get("status") != 2:
            rec["result"] = "not_claimable"
            return rec
        if not do_claim:
            rec["result"] = "claimable"
            return rec

        r2 = agent_mm.call(s, "POST", agent_mm.CLOUD + "/signin/claim",
                           token, ruid, prof, body={}, timeout=30)
        rec["claim_http"] = r2.status_code
        if r2.status_code != 200:
            rec["result"] = "claim_http_%s" % r2.status_code
            rec["body"] = (r2.text or "")[:180]
            return rec
        j2 = r2.json()
        base = j2.get("base_resp") or {}
        if base.get("status_code") not in (None, 0, "0"):
            rec["result"] = "claim_biz"
            rec["body"] = json.dumps(base, ensure_ascii=False)[:180]
            return rec
        data2 = j2.get("data") or {}
        cr = data2.get("claim_result")
        rec["claim_result"] = cr
        rec["claim_id"] = data2.get("claim_id")
        rec["day_no"] = data2.get("day_no") or rec.get("day_no")
        panel = data2.get("panel") or {}
        pt = today_entry(panel.get("days") or [])
        if pt:
            rec["panel_day_status"] = pt.get("status")
        if cr == 1:
            rec["result"] = "claimed"
            try:
                rec["awarded"] = int(data2.get("points") or 0)
            except Exception:
                rec["awarded"] = 0
        elif cr == 2:
            rec["result"] = "already"
        else:
            rec["result"] = "claim_miss"
        return rec
    except Exception as e:
        rec["result"] = "exception"
        rec["error"] = repr(e)[:220]
        return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="/opt/wzmm/pool.json")
    ap.add_argument("--write", action="store_true", help="claim and persist (default: dry-run)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--email", default="")
    ap.add_argument("--json-out", default="/opt/wzmm/_checkin_last.json")
    ap.add_argument("--log", default="/opt/wzmm/checkin.log")
    a = ap.parse_args()

    d = json.load(open(a.pool, encoding="utf-8"))
    accs = d.get("accounts") if isinstance(d, dict) else d
    if not isinstance(accs, list):
        print(json.dumps({"ok": False, "error": "pool accounts is not a list"}, ensure_ascii=False))
        return 2
    if a.email:
        accs = [x for x in accs if str(x.get("email") or "") == a.email]
    if a.limit and a.limit > 0:
        accs = accs[:a.limit]

    t0 = time.time()
    out = []
    workers = max(1, min(int(a.workers or 8), 24))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one_account, x, bool(a.write)) for x in accs]
        for f in as_completed(futs):
            out.append(f.result())
    out.sort(key=lambda x: x.get("email") or "")

    by_email = {r.get("email"): r for r in out}
    changed = 0
    awarded = 0
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    if a.write:
        for x in accs:
            r = by_email.get(str(x.get("email") or ""))
            if not r or r.get("result") not in ("claimed", "already"):
                continue
            x["checked_at"] = now_str
            x["last_checkin_at"] = int(time.time())
            x["last_checkin_result"] = r.get("result")
            x["last_checkin_claim_id"] = r.get("claim_id")
            if r.get("day_no") is not None:
                x["signin_day"] = r.get("day_no")
            x["signin_status"] = r.get("panel_day_status") or 3
            if r.get("result") == "claimed" and r.get("awarded"):
                try:
                    x["points"] = int(x.get("points") or 0) + int(r["awarded"])
                except Exception:
                    x["points"] = int(r["awarded"])
                awarded += int(r["awarded"])
            changed += 1

        if changed:
            bak = a.pool + ".bak.checkin." + time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(a.pool, bak)
            tmp = a.pool + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, a.pool)
            try:
                os.chmod(a.pool, 0o600)
            except Exception:
                pass

    counts = {}
    for r in out:
        counts[r.get("result")] = counts.get(r.get("result"), 0) + 1
    summary = {
        "ok": True,
        "ts": int(time.time()),
        "time": now_str,
        "write": bool(a.write),
        "accounts": len(accs),
        "workers": workers,
        "duration_s": round(time.time() - t0, 2),
        "counts": counts,
        "claimed": counts.get("claimed", 0),
        "already": counts.get("already", 0),
        "claimable": counts.get("claimable", 0),
        "awarded_points": awarded if a.write else sum(int(r.get("awarded") or 0) for r in out),
        "changed": changed,
        "results": out,
    }
    try:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    try:
        with open(a.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    if a.email or a.limit:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not any(r.get("result") in ("exception",) for r in out) else 1


if __name__ == "__main__":
    raise SystemExit(main())