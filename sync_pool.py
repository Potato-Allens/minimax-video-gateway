# -*- coding: utf-8 -*-
"""Merge accounts_sso.jsonl into pool.json (idempotent), then reload the service pool."""
import base64,json,os,subprocess,sys,time
POOL="/opt/wzmm/pool.json"; SSO="/opt/wzmm/accounts_sso.jsonl"; KF="/opt/wzmm/admin.key"
def exp_of(t):
    try:
        p=t.split("."); p[1]+="="*(-len(p[1])%4)
        return json.loads(base64.urlsafe_b64decode(p[1])).get("exp") or 0
    except Exception: return 0
def main():
    d=json.load(open(POOL,encoding="utf-8")); accs=d["accounts"]
    have={a.get("email") for a in accs}
    added=[]
    for line in open(SSO,encoding="utf-8"):
        line=line.strip()
        if not line: continue
        r=json.loads(line); em=r.get("email")
        if not em or em in have: continue
        accs.append({"email":em,"password":r.get("password",""),"token":r.get("token",""),
          "ruid":str(r.get("ruid","")),"refreshToken":r.get("refreshToken",""),
          "src":r.get("src","sync_pool"),"ts":str(r.get("ts") or time.time()),
          "exp":str(exp_of(r.get("token",""))),"alive":True,"usable":True,"is_new":True,
          "checked_at":time.strftime("%Y-%m-%d %H:%M:%S"),"trial_http":200,
          "trial_exp":str(int((time.time()+7*86400)*1000)),"points":400,
          "biz_uid":str(r.get("ruid","")),"member":False,"why":"sync_pool"})
        have.add(em); added.append(em)
    if added:
        json.dump(d,open(POOL,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print("added",len(added),added)
    print("pool",len(accs))
    if added:
        key=open(KF).read().strip()
        out=subprocess.run(["curl","-s","-X","POST","-H","X-Admin-Token: "+key,
            "http://127.0.0.1:8808/admin/reload-pool?full=1"],capture_output=True,text=True).stdout
        print(out[:200])
main()