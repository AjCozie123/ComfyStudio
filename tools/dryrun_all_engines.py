"""Build every engine's graph the way a real job would, WITHOUT queueing it, and validate it against ComfyUI.

    python tools/dryrun_all_engines.py

The ComfyUI POST /prompt call is replaced by a stub, so nothing is generated. Uses one synthetic test picture
(never the owner's photos), removed again at the end.
"""
import json, os, shutil, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from PIL import Image  # noqa: E402
import server as S  # noqa: E402
import workflow_registry as WF  # noqa: E402
from validate_graphs import validate_graph  # noqa: E402

sent = []
real = S.comfy


def fake(method, path, body=None, timeout=30, raw=False):
    if method == "POST" and path == "/prompt":
        sent.append(body)
        return {"prompt_id": "dry-run", "number": 0}
    return real(method, path, body, timeout, raw)


S.comfy = fake
S._wait_for_job = lambda job: None          # never wait / never record a gallery item
# build every engine whatever mode ComfyUI is in right now (the real app asks to switch first)
_real_hw = S.hardware
S.hardware = lambda *a, **k: dict(_real_hw(*a, **k), profile=None)
oi = S.object_info(force=True)

folder = os.path.join(S.INPUT, S.INPUT_SUB)
os.makedirs(folder, exist_ok=True)
pics = []
for i, col in enumerate([(120, 150, 190), (200, 170, 140), (90, 160, 110), (180, 120, 160)]):
    n = "_cs_dryrun_%d.png" % i
    Image.new("RGB", (1024, 1280), col).save(os.path.join(folder, n))
    pics.append(S.INPUT_SUB + "/" + n)

results = []


def graph_of(job):
    if sent:
        return sent[-1].get("prompt")
    return (job or {}).get("graph") if isinstance(job, dict) else None


def run(label, data, expect_prefix=True):
    sent.clear()
    try:
        job = (S.submit_workflow_job(data) if data.get("mode") == "workflow" else S.submit_job(data))
    except Exception as e:
        results.append((False, label, "build error: %s" % e))
        return
    g = graph_of(job)
    if not g:
        results.append((False, label, "no graph"))
        return
    probs = validate_graph(g, oi, S.INPUT)
    prefixes = [n["inputs"].get("filename_prefix") for n in g.values()
                if isinstance((n.get("inputs") or {}).get("filename_prefix"), str)]
    sorted_ok = (not expect_prefix) or any(str(p).startswith("ComfyStudio/") and str(p).count("/") >= 4 for p in prefixes)
    ok = not probs and sorted_ok
    results.append((ok, label, ("; ".join(probs[:3]) if probs else "") + ("" if sorted_ok else " | save prefix not sorted: %s" % prefixes)))
    results[-1] = results[-1] + (prefixes[:1],)


# ---------------------------------------------------------------- built-in MiniMax H3
run("MiniMax H3 text to image", {"mode": "t2i", "prompt": "a red apple on a wooden table", "seed": 1, "resolution": "1024x1024"})
run("MiniMax H3 edit (1 picture)", {"mode": "edit", "prompt": "make the sky orange", "images": pics[:1], "seed": 1})
run("MiniMax H3 edit (3 pictures)", {"mode": "edit", "prompt": "put <Picture 2> next to <Picture 1>", "images": pics[:3], "seed": 1})

# ---------------------------------------------------------------- every workflow engine (built-in + uploaded)
for card in WF.list_cards() if hasattr(WF, "list_cards") else []:
    pass
cards = []
for f in sorted(os.listdir(os.path.join(ROOT, "workflows"))):
    if f.endswith(".card.json"):
        cards.append(json.load(open(os.path.join(ROOT, "workflows", f), encoding="utf-8")))
udir = os.path.join(ROOT, "data", "user_workflows")
if os.path.isdir(udir):
    for f in sorted(os.listdir(udir)):
        if f.endswith(".card.json"):
            cards.append(json.load(open(os.path.join(udir, f), encoding="utf-8")))

for card in cards:
    wid = card["id"]
    try:
        WF.get(wid)
    except Exception:
        continue
    need = card.get("picture_min") or 0
    most = card.get("picture_max") if card.get("picture_max") is not None else need
    n = max(need, min(most, 1)) if need or most else 0
    base = {"mode": "workflow", "workflow_id": wid, "prompt": "a woman in a red coat walking in a park",
            "seed": 1, "images": pics[:n], "width": 1024, "height": 1024}
    if card.get("media") == "video":
        base.update(seconds=2, fps=(card.get("defaults") or {}).get("fps") or 24)
    if card.get("video_min"):
        vids = sorted(f for f in os.listdir(S.INPUT) if f.lower().endswith(".mp4"))[: card["video_min"]]
        if len(vids) < card["video_min"]:
            results.append((False, "%s (%s)" % (card.get("name", wid), wid), "no .mp4 in the input folder to test with"))
            continue
        base["videos"] = vids
    if card.get("api"):
        S.comfy_org_key = lambda: "dryrun_placeholder_not_a_key"   # /prompt is faked above: nothing is sent
    if card.get("edit_kind") == "anypaint" or card.get("native_mask"):
        import base64, io as _io
        from PIL import ImageDraw
        m = Image.new("L", (1024, 1280), 0)
        ImageDraw.Draw(m).rectangle((300, 400, 700, 900), fill=255)
        b = _io.BytesIO(); m.save(b, "PNG")
        base["inpaint_mask"] = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    run("%s (%s)" % (card.get("name", wid), wid), base)
    if card.get("edit_kind") == "krea_edit":
        for intent in ("action", "restage", "remove", "general"):
            run("  Krea 2 Edit auto: %s" % intent, dict(base, edit_intent=intent))
        run("  Krea 2 Edit two pictures", dict(base, images=pics[:2], edit_intent="insert"))
        for intent in ("headswap", "bodyswap"):
            run("  Krea 2 Edit %s" % intent, dict(base, images=pics[:2], edit_intent=intent,
                                                   models={"1": "Krea2\\bfs_head_swap_v1.1_krea2.safetensors"}))
        run("  Krea 2 Edit manual overrides", dict(base, overrides={"patch.ref_boost": 1.5, "pos.grounding_px": 512}))

# ---------------------------------------------------------------- clean up the synthetic pictures
for p in pics:
    try:
        os.remove(os.path.join(S.INPUT, p.replace("/", os.sep)))
    except OSError:
        pass

bad = [r for r in results if not r[0]]
for r in results:
    print(("PASS " if r[0] else "FAIL ") + r[1] + ("" if r[0] else "  | " + r[2]) + ("  -> " + str(r[3][0]) if len(r) > 3 and r[3] and r[0] else ""))
print("\n%d engines/variants built, %d passed, %d failed" % (len(results), len(results) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
