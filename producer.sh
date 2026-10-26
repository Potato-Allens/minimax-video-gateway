#!/bin/bash
# /opt/wzmm/producer.sh - durable single-lane account producer (R684)
# one Chromium at a time, HK residential egress, auto-sync + auto-checkin
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd /opt/wzmm || exit 1
export TZ=Asia/Hong_Kong
export PX_USER="${PX_USER:-user000000-region-XX-sid-{sid}-t-5}"

MAXOK=${MAXOK:-0}           # 0 = run forever
MAXTRY=${MAXTRY:-0}         # 0 = unbounded attempts
COOLDOWN=${COOLDOWN:-45}
DAILY_CAP=${DAILY_CAP:-40}  # max new accounts per calendar day (0 = no cap)
LOG=${LOG:-/var/log/wzmm-producer.log}
LOCK=/tmp/wzmm-producer.lock
DAILY_FILE=/opt/wzmm/.producer_daily

exec 9>"$LOCK"
if ! flock -n 9; then echo "another producer is running" >&2; exit 3; fi

log(){ echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

BASE=$(wc -l < accounts_sso.jsonl)
SEEN=$(cat /opt/wzmm/.producer_seen 2>/dev/null || echo "$BASE")
OK=0
TRY=0
FAILSTREAK=0
RISK=0

today(){ date '+%F'; }

daily_count(){
  local d="" c=""
  if [ -f "$DAILY_FILE" ]; then read -r d c < "$DAILY_FILE" || true; fi
  if [ "${d:-}" != "$(today)" ]; then d=$(today); c=0; fi
  echo "$d ${c:-0}"
}

daily_inc(){
  local d="" c=""
  read -r d c < <(daily_count)
  echo "$d $((c+1))" > "$DAILY_FILE"
}

log "producer start base=$BASE maxok=$MAXOK maxtry=$MAXTRY cap=$DAILY_CAP"

while :; do
  TRY=$((TRY+1))

  if [ "$DAILY_CAP" -gt 0 ]; then
    read -r _dd _dc < <(daily_count)
    if [ "${_dc:-0}" -ge "$DAILY_CAP" ]; then
      log "daily cap reached ($_dc/$DAILY_CAP) -> sleep 1800"
      sleep 1800
      continue
    fi
  fi

  FREE=$(df -Pk / | awk 'NR==2{print int($4/1048576)}')
  if [ "$FREE" -lt 4 ]; then log "disk free ${FREE}G < 4G -> sleep 600"; sleep 600; continue; fi

  NEW="wzr$(openssl rand -hex 4)"
  pkill -f "proxy_relay.py" 2>/dev/null; sleep 1
  PX_SID=$NEW setsid nohup ./venv/bin/python proxy_relay.py >> /tmp/relay.log 2>&1 9>&- &
  sleep 3
  IP=$(curl -s --max-time 20 -x http://127.0.0.1:3999 https://api.ipify.org)
  O=/tmp/prod_${TRY}_$$.log
  log "attempt $TRY ip=${IP:-?}"

  timeout 800 xvfb-run -a ./venv/bin/python -u _r710_up.py 1 > "$O" 2>&1 9>&-
  grep -E "send-retry|send-ok|send-fail|send-risk|\[code\]|renew|agent|SAVED|EXC|nav-retry|no-cdp" "$O" | tail -6 | sed 's/^/  /' | tee -a "$LOG" >/dev/null

  NOW=$(wc -l < accounts_sso.jsonl)
  if [ "$NOW" -gt "$BASE" ]; then
    OK=$((NOW-BASE)); FAILSTREAK=0; RISK=0
    NEWMAIL=$(sed -n "$((SEEN+1)),$((NOW))p" accounts_sso.jsonl | ./venv/bin/python -c "import sys,json;[print(json.loads(l)['email']) for l in sys.stdin if l.strip()]")
    echo "$NOW" > /opt/wzmm/.producer_seen; SEEN=$NOW
    ./venv/bin/python sync_pool.py 2>&1 | tail -2 | sed 's/^/  /' | tee -a "$LOG" >/dev/null
    for em in $NEWMAIL; do
      timeout 60 ./venv/bin/python checkin.py --email "$em" --write >/dev/null 2>&1 && log "  checkin $em ok" || log "  checkin $em fail"
      daily_inc
    done
    log "saved total+=$OK ($NEWMAIL)"
  else
    FAILSTREAK=$((FAILSTREAK+1))
    log "attempt $TRY failed (streak=$FAILSTREAK)"
  fi

  # R707: 1200063 is upstream risk control, not a mail-domain block. Retrying it
  # every 45s makes the next attempt worse, so back off by failure kind.
  if [ -f "$O" ] && grep -q "1200063" "$O" 2>/dev/null; then
    RISK=$((RISK+1))
    rm -f "$O"
    if [ "$RISK" -ge 2 ]; then
      log "risk reject 1200063 streak=$RISK -> cooldown 1800"
      sleep 1800
      RISK=0
    else
      log "risk reject 1200063 streak=$RISK -> cooldown 420"
      sleep 420
    fi
    continue
  fi

  if [ "$MAXOK" -gt 0 ] && [ "$OK" -ge "$MAXOK" ]; then log "target reached ok=$OK"; exit 0; fi
  if [ "$MAXTRY" -gt 0 ] && [ "$TRY" -ge "$MAXTRY" ]; then log "maxtry reached try=$TRY ok=$OK"; exit 0; fi

  if [ "$FAILSTREAK" -ge 12 ]; then
    log "streak=$FAILSTREAK -> cooldown 3600"; sleep 3600; FAILSTREAK=0
  elif [ "$FAILSTREAK" -ge 6 ]; then
    log "streak=$FAILSTREAK -> cooldown 900"; sleep 900; FAILSTREAK=0
  else
    sleep "$COOLDOWN"
  fi
done
