# -*- coding: utf-8 -*-
"""R708 regression: signup egress/domain/warm-up contract + relay plumbing.

Guards the facts proven by the 2x2 matrix:
  - producer must sign up through a non-HK residential exit (JP),
  - the mailbox must be yopmail (read in-browser), not guerrillamail,
  - the signup browser must do a human-like warm-up,
  - relays/probe must stay parameterised (PX_USER {sid}, WZ_PROXY).
"""
import io, json, os, py_compile, sys

P = "/opt/wzmm"
ok = 0
bad = []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print("PASS", name, detail)
    else:
        bad.append(name)
        print("FAIL", name, detail)


def rd(name):
    try:
        return io.open(os.path.join(P, name), encoding="utf-8").read()
    except Exception:
        return ""


prod = rd("producer.sh")
up = rd("_r710_up.py")
old = rd("_r680_up.py")
relay = rd("proxy_relay.py")
probe = rd("_w120_reg_probe.py")

check("producer exists", bool(prod))
check("producer uses JP exit", "region-JP" in prod)
check("producer has no HK exit", "region-HK" not in prod)
check("producer runs _r710_up", "_r710_up.py 1" in prod)
check("producer keeps risk backoff 420", "cooldown 420" in prod)
check("producer keeps risk backoff 1800", "cooldown 1800" in prod)
check("producer restarts relay with sid", "PX_SID=$NEW" in prod)

check("r710 exists", bool(up))
try:
    py_compile.compile(os.path.join(P, "_r710_up.py"), doraise=True)
    check("r710 compiles", True)
except Exception as e:
    check("r710 compiles", False, str(e)[:60])

check("r710 reads yopmail frames", "/en/mail?b=" in up)
check("r710 has yop_wait_code", "def yop_wait_code" in up)
check("r710 does not sign up at guerrillamail", "@guerrillamail.com" not in up.lower())
check("r710 honours WZ_PROXY", 'os.environ.get("WZ_PROXY"' in up)
check("r710 honours WZ_CDP_BASE", 'os.environ.get("WZ_CDP_BASE"' in up)
check("r710 warms youtube", "youtube.com" in up)
check("r710 moves the mouse", "def human(pg,sec)" in up)
check("r710 stops on 1200063", "1200063" in up)

check("legacy r680 kept", "guerrillamail" in old.lower())
check("relay default host", "us.1024proxy.io" in relay)
check("relay user template has sid", "{sid}" in relay)
check("relay supports PX_LISTEN", "PX_LISTEN" in relay)
check("probe honours WZ_PROXY", "WZ_PROXY" in probe)

acc = rd("accounts_sso.jsonl").strip().splitlines()
last = {}
if acc:
    try:
        last = json.loads(acc[-1])
    except Exception:
        last = {}
check("latest account is yopmail", str(last.get("email", "")).endswith("@yopmail.com"),
      str(last.get("email", ""))[:40])
check("latest account has token", bool(last.get("token")))

pool = []
try:
    raw = json.load(io.open(os.path.join(P, "pool.json"), encoding="utf-8"))
    pool = raw.get("accounts", []) if isinstance(raw, dict) else raw
except Exception:
    pool = []
check("pool >= 149 accounts", len(pool) >= 149, str(len(pool)))

print("----")
print("ok=%d bad=%d" % (ok, len(bad)))
if bad:
    print("failed:", ", ".join(bad))
    sys.exit(1)
print("ALL GREEN")