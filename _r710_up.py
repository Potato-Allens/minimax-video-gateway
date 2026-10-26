# -*- coding: utf-8 -*-
"""R710: browser sends+enters code, YOPMAIL delivers (guerrillamail is 1200063 now), renew.py mints token."""
import base64,json,os,re,shutil,string,random,subprocess,sys,time,urllib.parse,urllib.request as u
from playwright.sync_api import sync_playwright
sys.path.insert(0,"/opt/wzmm")
import renew, agent_mm as A
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
HERE="/opt/wzmm"; CHROME="/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
renew.PROXY_TMPL=os.environ.get("PX_TMPL","http://user000000-region-XX-sid-{sid}-t-5:REPLACE_ME@us.1024proxy.io:3000")
def log(*a): print(time.strftime("%H:%M:%S")," ".join(str(x) for x in a)[:300],flush=True)
def human(pg,sec):
    t0=time.time()
    while time.time()-t0<sec:
        try:
            pg.mouse.move(random.randint(150,1150),random.randint(120,680),steps=4)
            if random.random()<0.3: pg.mouse.wheel(0,random.randint(-140,180))
        except Exception: pass
        time.sleep(random.uniform(0.1,0.25))
YOP = "https://yopmail.com/en/?login=%s"
def yop_codes(pg):
    out = []
    for fr in pg.frames:
        u_ = fr.url or ""
        if "/en/mail?b=" not in u_:
            continue
        try:
            t = fr.inner_text("body")
        except Exception:
            continue
        m = re.search(r"verification code is\s*[:\uff1a]?\s*(\d{6})", t, re.I)
        if m:
            out.append(m.group(1))
        else:
            out += re.findall(r"(?<!\d)(\d{6})(?!\d)", t)
    return list(dict.fromkeys(out))
def yop_wait_code(ctx, box, deadline=180):
    t0 = time.time()
    mb = None
    try:
        mb = ctx.new_page()
        while time.time() - t0 < deadline:
            try:
                mb.goto(YOP % box, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                log("[yop-nav]", str(e)[:50])
            mb.wait_for_timeout(6000)
            c = yop_codes(mb)
            if c:
                return c[0]
            mb.wait_for_timeout(2000)
    finally:
        try:
            if mb:
                mb.close()
        except Exception:
            pass
    return ""
def attempt(idx):
    user="".join(random.choice(string.ascii_lowercase) for _ in range(10))
    em=user+"@yopmail.com"
    pw="Wz"+str(random.randint(100000,999999))+"Aa!"
    log("[inbox]",em)
    prof="/tmp/wzprof_g_%d_%d"%(idx,int(time.time()))
    proc=subprocess.Popen([CHROME,"--remote-debugging-port=%d"%(int(os.environ.get("WZ_CDP_BASE","9400"))+idx),"--user-data-dir="+prof,"--no-sandbox",
      "--disable-dev-shm-usage","--disable-blink-features=AutomationControlled",
      "--proxy-server="+os.environ.get("WZ_PROXY","http://127.0.0.1:3999"),"--window-size=1920,1080","--lang=en-US",
      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      "--no-first-run","--no-default-browser-check","about:blank"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    st=base64.b64encode(json.dumps({"biz_id":"0","device_platform":"desktop","device_id":"w120","version_code":"1.0.102","csrf":"x","back":"https://design.minimax.io/login"}).encode()).decode()
    AUTH=("/oauth2/authorize?client_id=hub&redirect_uri="+urllib.parse.quote("https://design.minimax.io/auth/callback",safe="")+"&response_type=code&state="+urllib.parse.quote(st,safe=""))
    URL="https://account.minimax.io/unified-login?login_redirect="+urllib.parse.quote(AUTH,safe="")
    sendresp=[]
    try:
        time.sleep(4)
        with sync_playwright() as p:
            b=None
            for _ in range(20):
                try: b=p.chromium.connect_over_cdp("http://127.0.0.1:%d"%(int(os.environ.get("WZ_CDP_BASE","9400"))+idx),timeout=8000); break
                except Exception: time.sleep(1)
            if not b: log("[no-cdp]"); return None
            ctx=b.contexts[0]; pg=ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.on("response",lambda r:(sendresp.append(r.text()),log("[SEND]",r.status,(sendresp[-1] or "")[:80])) if "/login/sms/send" in r.url else None)
            for uu,sec in (("https://www.google.com/search?q=weather+today",7),("https://www.youtube.com/",8),("https://account.minimax.io/",5)):
                for att in range(3):
                    try: pg.goto(uu,wait_until="domcontentloaded",timeout=45000); human(pg,sec); log("[warm-ok]",uu[:30]); break
                    except Exception as e: log("[warm-retry]",uu[:26],att+1,str(e)[:30]); pg.wait_for_timeout(2000)
            oknav=False
            for att in range(2):
                try: pg.goto(URL,wait_until="domcontentloaded",timeout=75000); oknav=True; break
                except Exception as e: log("[nav-retry]",att+1,str(e)[:40])
            if not oknav: return None
            try: pg.wait_for_selector('input[type="email"]',timeout=60000)
            except Exception: log("[no-input]")
            pg.wait_for_timeout(3000)
            el=pg.query_selector('input[type="email"]')
            if not el: log("[no-email]"); return None
            el.click(); el.type(em,delay=110); pg.wait_for_timeout(900)
            pg.evaluate("""()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>/size-4/.test(x.className||'')&&!x.disabled);if(b)b.click();}""")
            pg.wait_for_timeout(600)
            pg.get_by_role("button",name="Continue",exact=True).last.click(timeout=15000)
            pg.wait_for_timeout(6000)
            pel=pg.query_selector('input[type="password"]')
            if not pel: log("[no-pass]", re.sub(r"\s+"," ",pg.inner_text("body"))[:120]); return None
            pel.click(); pel.type(pw,delay=110); pg.wait_for_timeout(1500)
            pg.evaluate("""()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>/size-4/.test(x.className||''));if(b&&b.disabled){b.removeAttribute('disabled');b.click();}}""")
            pg.wait_for_timeout(600)
            for satt in range(3):
                sendresp.clear()
                pg.get_by_role("button",name="Continue",exact=True).last.click(timeout=15000)
                for _ in range(45):
                    pg.wait_for_timeout(1000)
                    if sendresp: break
                if sendresp and '"code":0' in sendresp[-1]: break
                if sendresp and '1200063' in sendresp[-1]:
                    # R707: risk control, not a transient error - hammering it makes
                    # the next attempt worse. Fail fast and let producer.sh back off.
                    log("[send-risk]","1200063 no-retry"); break
                log("[send-retry]",satt+1,(sendresp[-1] if sendresp else "")[:60].replace("\n"," "))
                pg.wait_for_timeout(3500)
            if not sendresp or '"code":0' not in sendresp[-1]:
                log("[send-fail]",(sendresp[-1] if sendresp else "")[:80].replace("\n"," ")); return None
            log("[send-ok]",em)
            open(os.path.join(HERE,"_reg_partial.jsonl"),"a",encoding="utf-8").write(json.dumps({"email":em,"password":pw,"ts":time.time()},ensure_ascii=False)+"\n")
            code=yop_wait_code(ctx,user,180)
            log("[code]",code or "NONE")
            if not code: return None
            boxes=pg.query_selector_all('input[maxlength="1"]')
            if len(boxes)>=6:
                for i,ch in enumerate(code[:6]): boxes[i].type(ch,delay=70)
            else:
                inp=pg.query_selector('input:not([type=password]):not([type=email])')
                if inp: inp.click(); inp.fill(code)
            pg.wait_for_timeout(1200)
            try: pg.get_by_role("button",name="Continue").first.click(timeout=12000)
            except Exception: pass
            pg.wait_for_timeout(15000)
            try: b.close()
            except Exception: pass
    except Exception as e:
        log("[EXC]",type(e).__name__,str(e)[:100]); return None
    finally:
        try: proc.terminate()
        except Exception: pass
        time.sleep(1); shutil.rmtree(prof,ignore_errors=True)
    s2=""; info=None
    for rnd in range(2):
        e2,s2,info=renew.renew_one({"email":em,"password":pw,"refreshToken":""},tries=4)
        log("[renew]",rnd+1,s2,(info or {}).get("ruid",""))
        if s2=="ok" and info: break
        time.sleep(20)
    if s2!="ok" or not info: return None
    r=A.call(A.sess(None),"GET",A.CLOUD+"/agent",info["token"],info["ruid"],A.profile_for(em),timeout=30)
    log("[agent]",r.status_code)
    if r.status_code!=200: return None
    rec={"email":em,"password":pw,"token":info["token"],"ruid":info["ruid"],"refreshToken":"","sid":"","ts":time.time(),"src":"R680 guerrilla+renew","alive":200,"usable":True}
    open(os.path.join(HERE,"accounts_sso.jsonl"),"a",encoding="utf-8").write(json.dumps(rec,ensure_ascii=False)+"\n")
    log("[SAVED]",em); return rec
if __name__=="__main__":
    n=int(sys.argv[1]) if len(sys.argv)>1 else 1
    ok=0
    for i in range(n):
        if attempt(i): ok+=1
    log("DONE ok=%d/%d"%(ok,n))