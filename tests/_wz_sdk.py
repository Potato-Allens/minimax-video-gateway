# -*- coding: utf-8 -*-
import io, os, subprocess, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk"))
os.environ.setdefault("WZMM_KEY", "")
os.environ["WZMM_BASE"] = "http://127.0.0.1:8808"
import wzmm

LEG = "/opt/h3v2/data/render/out"
rows = []


def ok(name, cond, detail=""):
    rows.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), name.ljust(40), detail)


ok("sdk exposes archive_job", callable(getattr(wzmm, "archive_job", None)))
ok("sdk exposes library_archive", callable(getattr(wzmm, "library_archive", None)))
ok("docstring version bumped", "v2.3" in (wzmm.__doc__ or ""))
ok("docstring mentions 91 paths", "91 paths" in (wzmm.__doc__ or ""))

h = subprocess.run([sys.executable, "/opt/wzmm/sdk/wzmm.py", "--help"],
                   capture_output=True, text=True).stdout
ok("cli help lists libarchive", "libarchive" in h)
ok("cli help lists job", ",job," in h)

_FARM_N = len([f for f in os.listdir(LEG) if f.endswith(".mp4")])
names = [f[:-4] for f in sorted(os.listdir(LEG)) if f.endswith(".mp4")][:2]
if _FARM_N >= 2:
    sizes = {n: os.path.getsize(os.path.join(LEG, n + ".mp4")) for n in names}

    b = wzmm.library_archive(ids=",".join(names))
    ok("library_archive returns zip", isinstance(b, (bytes, bytearray)) and b[:2] == b"PK", str(len(b)))
    z = zipfile.ZipFile(io.BytesIO(b))
    ok("libarchive entries", sorted(z.namelist()) == sorted(n + ".mp4" for n in names), str(z.namelist()))
    ok("libarchive sizes", all(z.getinfo(n + ".mp4").file_size == sizes[n] for n in names))

    out = "/tmp/_sdk_job.zip"
    n, meta = wzmm.archive_job(kind="library", ids=names[0], out=out)
    ok("job reaches ready", meta.get("state") == "ready", str(meta.get("state")))
    ok("job wrote file", os.path.isfile(out) and n == os.path.getsize(out), "%s vs %s" % (n, os.path.getsize(out) if os.path.isfile(out) else -1))
    z2 = zipfile.ZipFile(out)
    ok("job zip entry", z2.namelist() == [names[0] + ".mp4"], str(z2.namelist()))
    os.remove(out)
    did = wzmm.archive_job_drop(meta["job_id"])
    ok("job drop", isinstance(did, dict) and did.get("ok"), str(did)[:60])
else:
    print("SKIP sdk library archive/job (legacy farm empty)")

try:
    wzmm.archive_job(kind="library", ids="definitely-missing-clip", timeout=5)
    ok("missing id raises", False)
except wzmm.APIError as e:
    ok("missing id -> 404", e.status == 404, str(e.status))

if _FARM_N >= 2:
    try:
        wzmm.library_archive(ids=names[0], key="")
        ok("empty key -> 401", False)
    except wzmm.APIError as e:
        ok("empty key -> 401", e.status == 401, str(e.status))
else:
    print("SKIP sdk empty key (legacy farm empty)")

bad = [r for r in rows if not r[0]]
print("\nSUMMARY %d/%d passed" % (len(rows) - len(bad), len(rows)))
sys.exit(1 if bad else 0)