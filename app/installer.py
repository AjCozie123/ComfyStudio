"""Install manager: the model files each engine needs, where they officially come from, and downloads the
user starts (never automatic).

- What a workflow needs is read from its own graph (every input that names a model file).
- Whether it's already on this PC: ComfyUI's own folder list (/internal/folder_paths), matched by file name in
  any sub-folder, the same way the app re-points moved files when it runs a job.
- Where to get it: deps/model_links.json (official links harvested from ComfyUI's templates; Hugging Face only).
- Downloads: one at a time in the background, to '<file>.part' then renamed, after a free-space check.
"""
from __future__ import annotations

import json, os, re, shutil, threading, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LINKS_FILE = os.path.join(HERE, "deps", "model_links.json")
MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".sft", ".pt", ".pth", ".bin")
ALLOWED_HOSTS = ("huggingface.co",)
# which ComfyUI folder an input's file lives in
KEY_FOLDER = {
    "unet_name": "diffusion_models", "ckpt_name": "checkpoints", "vae_name": "vae", "lora_name": "loras",
    "clip_name": "text_encoders", "clip_name1": "text_encoders", "clip_name2": "text_encoders", "clip_name3": "text_encoders",
    "text_encoder": "text_encoders", "model_text_image": "diffusion_models", "model_references": "diffusion_models",
    "video_vae": "vae", "audio_vae": "vae", "image_vae": "vae", "speed_lora_name": "loras", "upscale_model": "upscale_models",
    "model_name": "upscale_models",
}
SAFETY_GB = 5.0           # always leave this much free on the drive

_links = {"t": 0, "map": {}}
_state_lock = threading.Lock()
STATE = {"active": None, "queue": [], "done": [], "errors": [], "cancel": False}


def links():
    try:
        mt = os.path.getmtime(LINKS_FILE)
    except OSError:
        return {}
    if mt != _links["t"]:
        try:
            _links["map"] = (json.load(open(LINKS_FILE, encoding="utf-8")).get("links") or {})
        except Exception:
            _links["map"] = {}
        _links["t"] = mt
    return _links["map"]


def _folder_for(key, value):
    k = key.lower()
    if k in KEY_FOLDER:
        return KEY_FOLDER[k]
    if "lora" in k:
        return "loras"
    if "vae" in k:
        return "vae"
    if "clip" in k or "text_encoder" in k:
        return "text_encoders"
    return "diffusion_models"


def workflow_files(graph):
    """[(file name as written, ComfyUI folder)] that a graph loads, de-duplicated."""
    out, seen = [], set()
    for n in (graph or {}).values():
        for k, v in (n.get("inputs") or {}).items():
            if isinstance(v, str) and v.lower().endswith(MODEL_EXT) and v != "None":
                base = re.split(r"[\\/]", v)[-1]
                if base.lower() not in seen:
                    seen.add(base.lower())
                    out.append((v, _folder_for(k, v)))
    return out


def index_present(folder_paths):
    """{folder: {basename_lower: full path}} for everything in ComfyUI's model folders."""
    idx = {}
    for folder, dirs in (folder_paths or {}).items():
        m = idx.setdefault(folder, {})
        for d in dirs if isinstance(dirs, list) else []:
            if not os.path.isdir(d):
                continue
            for dp, dns, fns in os.walk(d):
                for fn in fns:
                    if fn.lower().endswith(MODEL_EXT):
                        m.setdefault(fn.lower(), os.path.join(dp, fn))
    return idx


def _present(idx, folder, base):
    alias = {"diffusion_models": ("diffusion_models", "unet", "checkpoints"), "text_encoders": ("text_encoders", "clip"),
             "checkpoints": ("checkpoints", "diffusion_models")}.get(folder, (folder,))
    for f in alias:
        hit = (idx.get(f) or {}).get(base.lower())
        if hit:
            return hit
    return None


def plan_for(graph, idx, folder_paths):
    """Files a workflow needs: present / missing (+ official link and target folder)."""
    lk = links()
    files = []
    for value, folder in workflow_files(graph):
        base = re.split(r"[\\/]", value)[-1]
        have = _present(idx, folder, base)
        link = lk.get(base.lower())
        tgt_folder = (link or {}).get("directory") or folder
        dirs = (folder_paths or {}).get(tgt_folder) or (folder_paths or {}).get(folder) or []
        files.append({
            "file": base, "folder": tgt_folder, "present": bool(have),
            "size_gb": round(os.path.getsize(have) / 1e9, 2) if have else None,
            "url": (link or {}).get("url"), "target_dir": dirs[0] if dirs else None,
        })
    return files


def free_gb(path):
    try:
        return round(shutil.disk_usage(path).free / 1e9, 1)
    except OSError:
        return None


def _head_size(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ComfyStudio"})
    with urllib.request.urlopen(req, timeout=30) as r:
        n = r.headers.get("Content-Length") or r.headers.get("X-Linked-Size")
        return int(n) if n and n.isdigit() else None


def _allowed(url):
    host = urllib.parse.urlsplit(url or "").hostname or ""
    return (url or "").startswith("https://") and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def status():
    with _state_lock:
        return json.loads(json.dumps(STATE))


def cancel():
    with _state_lock:
        STATE["cancel"] = True
        STATE["queue"] = []


def start(items, opener=None):
    """Queue downloads. items: [{file, url, target_dir}]. Only official links; one worker thread."""
    bad = [i.get("file") for i in items if not _allowed(i.get("url")) or not i.get("target_dir")]
    if bad:
        raise ValueError("No official download link for: " + ", ".join(bad[:5]))
    with _state_lock:
        known = {q["file"] for q in STATE["queue"]} | ({STATE["active"]["file"]} if STATE["active"] else set())
        for i in items:
            if i["file"] not in known:
                STATE["queue"].append({"file": i["file"], "url": i["url"], "target_dir": i["target_dir"]})
        STATE["cancel"] = False
        running = STATE["active"] is not None
    if not running:
        threading.Thread(target=_worker, args=(opener or urllib.request.urlopen,), daemon=True).start()


def _worker(opener):
    while True:
        with _state_lock:
            if not STATE["queue"] or STATE["cancel"]:
                STATE["active"] = None
                STATE["cancel"] = False
                return
            job = STATE["queue"].pop(0)
            STATE["active"] = dict(job, got=0, total=None, t0=time.time())
        dest = os.path.join(job["target_dir"], job["file"])
        part = dest + ".part"
        try:
            os.makedirs(job["target_dir"], exist_ok=True)
            if os.path.exists(dest):
                raise FileExistsError("already there")
            req = urllib.request.Request(job["url"], headers={"User-Agent": "ComfyStudio"})
            with opener(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0) or None
                fg = free_gb(job["target_dir"])
                if total and fg is not None and total / 1e9 > fg - SAFETY_GB:
                    raise OSError("Not enough free space: needs %.1f GB, %.1f GB free." % (total / 1e9, fg))
                with _state_lock:
                    STATE["active"]["total"] = total
                with open(part, "wb") as f:
                    while True:
                        if STATE["cancel"]:
                            raise InterruptedError("cancelled")
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        with _state_lock:
                            STATE["active"]["got"] += len(chunk)
            if total and os.path.getsize(part) != total:
                raise IOError("The download was incomplete.")
            os.replace(part, dest)
            with _state_lock:
                STATE["done"].append({"file": job["file"], "path": dest, "t": time.time()})
        except FileExistsError:
            with _state_lock:
                STATE["done"].append({"file": job["file"], "path": dest, "t": time.time(), "note": "already there"})
        except Exception as e:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
            with _state_lock:
                STATE["errors"].append({"file": job["file"], "error": str(e)[:200], "t": time.time()})
