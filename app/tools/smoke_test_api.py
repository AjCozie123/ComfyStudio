"""Smoke test every Comfy Studio API route WITHOUT generating anything.

    python tools/smoke_test_api.py [base_url]      (default http://127.0.0.1:8199 = the dev copy)

Never sends a request that could queue a generation: /api/generate is only called with a malformed body that
must be refused. Data-changing calls use ids that don't exist, or are undone right away (favourite toggled back,
test upload moved out).
"""
import io, json, os, shutil, sys, urllib.error, urllib.parse, urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199").rstrip("/")
results = []


def call(method, path, body=None, headers=None, raw=False, timeout=60):
    data = None
    h = dict(headers or {})
    if body is not None and not isinstance(body, (bytes, bytearray)):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = bytes(body)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return r.status, (b if raw else _j(b)), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, (b if raw else _j(b)), e.headers.get("Content-Type", "")


def _j(b):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return b


def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail)[:160] if detail and not ok else ""))


# ---------------------------------------------------------------- pages and static files
for p in ("/", "/static/app.js", "/static/app.css", "/static/manifest.webmanifest"):
    s, b, ct = call("GET", p, raw=True)
    check("GET " + p, s == 200 and len(b) > 100, s)

# ---------------------------------------------------------------- read-only API
s, st, _ = call("GET", "/api/status")
check("GET /api/status", s == 200 and isinstance(st, dict), st)
s, cfg, _ = call("GET", "/api/config")
check("GET /api/config (models + defaults)", s == 200 and isinstance(cfg, dict) and cfg.get("models") and cfg.get("defaults"), s)
s, hw, _ = call("GET", "/api/hardware")
check("GET /api/hardware", s == 200 and isinstance(hw, dict) and "image_mp_max" in hw, hw)
s, jobs, _ = call("GET", "/api/jobs")
check("GET /api/jobs", s == 200 and isinstance(jobs, list), s)
s, gal, _ = call("GET", "/api/gallery")
items = gal.get("items") if isinstance(gal, dict) else gal
check("GET /api/gallery", s == 200 and isinstance(items, list), type(gal))
s, live, _ = call("GET", "/api/live")
check("GET /api/live (204 = no preview while idle)", s in (200, 204), s)
s, lor, _ = call("GET", "/api/loras")
check("GET /api/loras", s == 200 and isinstance(lor, dict) and isinstance(lor.get("items"), list), s)
s, ns, _ = call("GET", "/api/nodes/status")
check("GET /api/nodes/status", s == 200, s)
s, al, _ = call("GET", "/api/aliases")
check("GET /api/aliases", s in (200, 404, 405), s)
for q in ("aspect=16:9&mp=2&mode=t2i", "aspect=match&mp=4&mode=edit&width=1200&height=1600", "aspect=1:1&mp=abc"):
    s, sz, _ = call("GET", "/api/size?" + q)
    check("GET /api/size?" + q, s in (200, 400) and isinstance(sz, dict), sz)

# every engine card + its Manual settings list
s, wfs, _ = call("GET", "/api/workflows")
wl = wfs.get("items") if isinstance(wfs, dict) else wfs
check("GET /api/workflows", s == 200 and isinstance(wl, list) and len(wl) > 5, s)
for w in (wl or []):
    wid = w.get("id")
    s, d, _ = call("GET", "/api/workflow?id=" + urllib.parse.quote(wid))
    ok = s == 200 and isinstance(d, dict) and d.get("card", {}).get("id") == wid and isinstance(d.get("knobs"), list)
    check("GET /api/workflow?id=%s (card, options, %s settings)" % (wid, len(d.get("knobs") or []) if isinstance(d, dict) else "?"), ok, s)
s, d, _ = call("GET", "/api/workflow?id=does_not_exist")
check("GET /api/workflow unknown id -> 404", s == 404, s)

# gallery item routes on a real item
it = next((x for x in (items or []) if x.get("filename")), None)
if it:
    gid = it["id"]
    for p in ("/api/image?id=", "/api/thumb?id=", "/api/download?id="):
        s, b, ct = call("GET", p + gid, raw=True)
        check("GET %s<item>" % p, s == 200 and len(b) > 1000, (s, ct))
    s, b, ct = call("GET", "/api/upscale/options?id=" + gid)
    check("GET /api/upscale/options", s == 200 and isinstance(b, dict), s)
    if it.get("input_snapshots") or it.get("source_images"):
        s, b, ct = call("GET", "/api/source?id=%s&slot=1" % gid, raw=True)
        check("GET /api/source (edit's input picture)", s == 200 and len(b) > 1000, s)
    if it.get("source_images"):
        s, b, ct = call("GET", "/api/input?name=" + urllib.parse.quote(it["source_images"][0]), raw=True)
        check("GET /api/input (input file)", s == 200 and len(b) > 1000, s)
    # favourite: toggle and put back
    was = bool(it.get("fav"))
    s1, r1, _ = call("POST", "/api/favourite", {"id": gid, "fav": not was})
    s2, r2, _ = call("POST", "/api/favourite", {"id": gid, "fav": was})
    check("POST /api/favourite (toggled and restored)", s1 == 200 and s2 == 200 and r2.get("fav") == was, (r1, r2))
else:
    check("gallery has an item to test with", False, "empty gallery")
for p in ("/api/image?id=nope", "/api/thumb?id=nope", "/api/source?id=nope"):
    s, b, ct = call("GET", p, raw=True)
    check("GET %s -> 404" % p, s == 404, s)
s, b, ct = call("GET", "/api/input?name=..%2F..%2Fwindows%2Fwin.ini", raw=True)
check("GET /api/input path escape blocked", s == 404, s)

# PC browser (read-only)
s, roots, _ = call("GET", "/api/pc/roots")
check("GET /api/pc/roots", s == 200, s)
s, rec, _ = call("GET", "/api/pc/recent")
check("GET /api/pc/recent", s == 200, s)
rl = roots.get("roots") if isinstance(roots, dict) else roots
if rl:
    r0 = rl[0].get("id") or rl[0].get("root") or rl[0].get("key") if isinstance(rl[0], dict) else rl[0]
    s, lst, _ = call("GET", "/api/pc/list?root=%s&path=" % urllib.parse.quote(str(r0)))
    check("GET /api/pc/list (first root)", s == 200, (s, str(lst)[:120]))
s, srch, _ = call("GET", "/api/pc/search?q=png")
check("GET /api/pc/search", s == 200, s)

# ---------------------------------------------------------------- POST routes that don't generate
s, b, _ = call("POST", "/api/aliases", {"prompt": "put img1 next to @Image2", "loaded": [1, 2]})
check("POST /api/aliases (Image 1 / @Image2 -> <Picture N>)", s == 200 and "<Picture 1>" in json.dumps(b), b)
s, b, _ = call("POST", "/api/assist", {"prompt": "a woman drinking coffee in a cafe", "n_images": 0, "mode": "t2i"})
check("POST /api/assist (prompt helper, local)", s == 200 and isinstance(b, dict), b)
s, b, _ = call("POST", "/api/appearance/prefetch", {"names": []})
check("POST /api/appearance/prefetch (empty)", s == 200, b)
s, b, _ = call("POST", "/api/clientlog", {"level": "info", "msg": "smoke test"})
check("POST /api/clientlog", s in (200, 204), s)
s, b, _ = call("POST", "/api/cancel", {"id": "nope"})
check("POST /api/cancel unknown id (no crash)", s == 200, b)
s, b, _ = call("POST", "/api/delete", {"id": "nope"})
check("POST /api/delete unknown id -> 404 (nothing deleted)", s == 404, b)
s, b, _ = call("POST", "/api/library/import", {"id": "nope"})
check("POST /api/library/import unknown id -> 404", s == 404, b)
s, b, _ = call("POST", "/api/pc/pick", {"root": "nope", "path": "nope.png"})
check("POST /api/pc/pick bad path handled", s in (200, 400, 403, 404) and isinstance(b, dict), (s, b))
s, b, _ = call("POST", "/api/upscale", {"id": "nope"})
check("POST /api/upscale unknown id refused (no job)", s in (400, 404), (s, b))
# malformed generate requests must be refused before anything is queued
for bad in ({"prompt": ["x"], "mode": "t2i"}, {"prompt": "x", "mode": "workflow", "workflow_id": ["krea2_edit"]},
            {"prompt": "x", "mode": "workflow", "workflow_id": "krea2_edit", "overrides": ["a"]}):
    s, b, _ = call("POST", "/api/generate", bad)
    check("POST /api/generate malformed refused: %s" % list(bad.keys()), s == 400, (s, b))

# workflow inspect (an API-format graph from the app's own engines)
g = json.load(open(os.path.join(os.path.dirname(__file__), "..", "workflows", "krea2_t2i.graph.json"), encoding="utf-8"))
s, b, _ = call("POST", "/api/workflows/inspect", {"text": json.dumps(g)}, timeout=120)
check("POST /api/workflows/inspect (reads a workflow)", s == 200 and isinstance(b, dict) and (b.get("card") or b.get("slots")), (s, str(b)[:200]))
s, b, _ = call("POST", "/api/workflows/inspect", {"text": "not json"})
check("POST /api/workflows/inspect bad text -> clear error", s in (200, 400) and isinstance(b, dict) and b.get("error"), (s, b))

# upload a tiny picture, read it back, then move it out of the input folder
try:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 200)).save(buf, "PNG")
    s, b, _ = call("POST", "/api/upload", buf.getvalue(), {"Content-Type": "image/png", "X-Filename": "_smoke_test.png"})
    name = (b or {}).get("name") if isinstance(b, dict) else None
    check("POST /api/upload", s == 200 and name, (s, b))
    if name:
        s2, b2, _ = call("GET", "/api/input?name=" + urllib.parse.quote(name), raw=True)
        check("GET /api/input (uploaded file back)", s2 == 200 and len(b2) > 50, s2)
        full = os.path.join(r"E:\Saved\input", name.replace("/", os.sep))
        if os.path.isfile(full):
            os.remove(full)
except ImportError:
    pass

bad = [r for r in results if not r[0]]
print("\n%d checks, %d passed, %d failed" % (len(results), len(results) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
