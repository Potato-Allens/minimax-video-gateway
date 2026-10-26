#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R666: tiny CONNECT relay -> residential proxy, so Chrome can use proxy auth.

Chrome's --proxy-server cannot carry credentials, and the old registration
scripts hardcode http://127.0.0.1:3999. This relay listens there and forwards
every connection to the rotating residential proxy, injecting
Proxy-Authorization and pinning one sticky session id so the whole signup flow
sees a single IP.

    ./venv/bin/python proxy_relay.py            # 127.0.0.1:3999 -> 1024proxy
    PX_SID=wzrtest ./venv/bin/python proxy_relay.py

Env: PX_LISTEN (127.0.0.1:3999), PX_HOST, PX_PORT, PX_USER, PX_PASS, PX_SID.
"""
import asyncio
import base64
import os
import secrets
import sys

LISTEN = os.environ.get("PX_LISTEN", "127.0.0.1:3999")
UP_HOST = os.environ.get("PX_HOST", "us.1024proxy.io")
UP_PORT = int(os.environ.get("PX_PORT", "3000"))
USER = os.environ.get("PX_USER", "user000000-region-XX-sid-{sid}-t-5")
PASS = os.environ.get("PX_PASS", "REPLACE_ME")
SID = os.environ.get("PX_SID") or ("wzr" + secrets.token_hex(4))
AUTH = "Basic " + base64.b64encode(
    ("%s:%s" % (USER.format(sid=SID), PASS)).encode()).decode()

STATS = {"conn": 0, "ok": 0, "fail": 0}


def log(msg):
    print(msg, flush=True)


async def pipe(reader, writer):
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(cr, cw):
    STATS["conn"] += 1
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 20)
    except Exception:
        try:
            cw.close()
        except Exception:
            pass
        return
    try:
        first = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = first.split(" ")
        method = parts[0].upper() if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        ur, uw = await asyncio.open_connection(UP_HOST, UP_PORT)
        if method == "CONNECT":
            req = ("CONNECT %s HTTP/1.1\r\nHost: %s\r\n"
                   "Proxy-Authorization: %s\r\n\r\n" % (target, target, AUTH)).encode()
            uw.write(req)
            await uw.drain()
            resp = await asyncio.wait_for(ur.readuntil(b"\r\n\r\n"), 20)
            if b" 200 " not in resp.split(b"\r\n", 1)[0]:
                STATS["fail"] += 1
                cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await cw.drain()
                cw.close()
                uw.close()
                return
            STATS["ok"] += 1
            cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await cw.drain()
        else:
            lines = head.split(b"\r\n")
            lines.insert(1, b"Proxy-Authorization: " + AUTH.encode())
            uw.write(b"\r\n".join(lines))
            await uw.drain()
            STATS["ok"] += 1
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
    except Exception as e:
        STATS["fail"] += 1
        if STATS["fail"] <= 5:
            log("relay error: %r" % (e,))
        try:
            cw.close()
        except Exception:
            pass


async def main():
    host, port = LISTEN.rsplit(":", 1)
    srv = await asyncio.start_server(handle, host, int(port))
    log("relay %s -> %s:%d sid=%s" % (LISTEN, UP_HOST, UP_PORT, SID))
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
