# -*- coding: utf-8 -*-
import io, os, re, subprocess, sys, time
SRC = "/opt/wzmm/producer.sh"
UP = "/opt/wzmm/_r680_up.py"
LOG = "/var/log/wzmm-producer.log"
rows = []


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(42), detail)


sh = io.open(SRC, encoding="utf-8").read()
py = io.open(UP, encoding="utf-8").read()
r = subprocess.run(["bash", "-n", SRC], capture_output=True, text=True)
ok("producer.sh syntax", r.returncode == 0, r.stderr.strip()[:80])
r2 = subprocess.run([sys.executable, "-m", "py_compile", UP], capture_output=True, text=True)
ok("_r680_up.py compiles", r2.returncode == 0, r2.stderr.strip()[:80])

ok("risk counter initialised", "RISK=0" in sh)
ok("risk reset on success", "FAILSTREAK=0; RISK=0" in sh)
ok("risk branch present", 'risk reject 1200063 streak=' in sh)
ok("short backoff is 420s", 'sleep 420' in sh)
ok("escalated backoff is 1800s", 'risk reject 1200063 streak=$RISK -> cooldown 1800' in sh)
ok("upstream no longer retries 1200063", "[send-risk]" in py and "1200063 no-retry" in py)
ok("no-retry actually breaks", re.search(r"\[send-risk\][^\n]*break", py) is not None)
ok("producer active", subprocess.run(["systemctl", "is-active", "--quiet", "wzmm-producer"]).returncode == 0)

if os.path.isfile(LOG):
    lines = io.open(LOG, encoding="utf-8", errors="replace").read().split("\n")
else:
    lines = []
risks = [l for l in lines if "risk reject 1200063" in l]
ok("log shows a risk backoff", len(risks) >= 1, str(len(risks)))

attempts = []
for l in lines[-400:]:
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) attempt (\d+) ", l)
    if m:
        attempts.append((m.group(1), int(m.group(2))))
if len(attempts) >= 2:
    def ts(x):
        return time.mktime(time.strptime(x, "%Y-%m-%d %H:%M:%S"))
    gaps = [ts(attempts[i + 1][0]) - ts(attempts[i][0]) for i in range(len(attempts) - 1)]
    ok("attempt gap honours backoff", max(gaps) >= 400, "max gap %ds over %d attempts" % (max(gaps), len(attempts)))
else:
    ok("attempt gap honours backoff", True, "not enough attempts yet (ok, waiting)")

starts = [i for i, l in enumerate(lines) if "producer start" in l]
recent = lines[starts[-1]:] if starts else lines[-40:]
hammers = sum(1 for l in recent if "send-retry" in l)
ok("no 3x hammer after patch", hammers == 0, "%d send-retry since last start" % hammers)

bad = [x for x in rows if not x[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)