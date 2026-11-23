"""Install manager: the model files each engine needs, where they officially come from, and downloads the
user starts (never automatic).

- What a workflow needs is read from its own graph (every input that names a model file).
- Whether it's already on this PC: ComfyUI's own folder list (/internal/folder_paths), matched by file name in
  any sub-folder, the same way the app re-points moved files when it runs a job.
- Where to get it: deps/model_links.json (official links harvested from ComfyUI's templates; Hugging Face only).
- Downloads: one at a time in the background, to '<file>.part' then renamed, after a free-space check.
"""
from __future__ import annotations

import json, os, re, shutil, threading, time, urllib.error, urllib.parse, urllib.request

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


FOLDER_ALIAS = {"diffusion_models": ("diffusion_models", "unet", "checkpoints"), "text_encoders": ("text_encoders", "clip"),
                "checkpoints": ("checkpoints", "diffusion_models")}


def alternatives(idx, folder, base, limit=60):
    """Files already on this PC that could stand in for a missing one: same ComfyUI folder, same kind of file
    (a .gguf needs a GGUF loader, so .gguf only replaces .gguf), most similar name first."""
    import difflib
    gguf = base.lower().endswith(".gguf")
    names = set()
    for f in FOLDER_ALIAS.get(folder, (folder,)):
        for fn, full in (idx.get(f) or {}).items():
            if fn.endswith(".gguf") == gguf:
                names.add(os.path.basename(full))
    want = base.lower()
    return sorted(names, key=lambda n: (-difflib.SequenceMatcher(None, want, n.lower()).ratio(), n.lower()))[:limit]


def plan_for(graph, idx, folder_paths, choices=None):
    """Files a workflow needs: present / missing (+ official link and target folder). choices = {wanted file
    (lower case): a file the user already has and picked instead}; such a file counts as present."""
    lk = links()
    choices = choices or {}
    files = []
    for value, folder in workflow_files(graph):
        base = re.split(r"[\\/]", value)[-1]
        link = lk.get(base.lower())
        tgt_folder = (link or {}).get("directory") or folder
        # look where the official link says AND where the input name suggests (they differ, e.g. a latent upscaler
        # read through 'model_name'), in any sub-folder, so a file the user already has is never fetched twice
        have = _present(idx, tgt_folder, base) or _present(idx, folder, base)
        dirs = (folder_paths or {}).get(tgt_folder) or (folder_paths or {}).get(folder) or []
        # download into ComfyUI's standard folder: models\diffusion_models rather than the old models\unet, and never
        # into the output\<folder> entries ComfyUI also lists
        dirs = sorted(dirs, key=lambda d: (os.path.basename(os.path.normpath(d)).lower() != tgt_folder.lower(),
                                           os.path.basename(os.path.dirname(os.path.normpath(d))).lower() != "models"))
        item = {
            "file": base, "folder": tgt_folder, "present": bool(have),
            "size_gb": round(os.path.getsize(have) / 1e9, 2) if have else None,
            "url": (link or {}).get("url"), "target_dir": dirs[0] if dirs else None,
        }
        if not have:
            pick = choices.get(base.lower())
            got = (_present(idx, tgt_folder, pick) or _present(idx, folder, pick)) if pick else None
            if got:
                item.update(present=True, using=os.path.basename(got), size_gb=round(os.path.getsize(got) / 1e9, 2))
            else:
                item["alternatives"] = alternatives(idx, tgt_folder, base) or alternatives(idx, folder, base)
        files.append(item)
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


def cancel():
    with _state_lock:
        STATE["cancel"] = True
        STATE["queue"] = []


def repo_page(url):
    """https://huggingface.co/<owner>/<repo> for a file link (where a model's licence is accepted)."""
    parts = urllib.parse.urlsplit(url or "").path.strip("/").split("/")
    return "https://huggingface.co/%s/%s" % (parts[0], parts[1]) if len(parts) >= 2 else "https://huggingface.co"


def _request(url, token=None, start_at=0):
    req = urllib.request.Request(url, headers={"User-Agent": "ComfyStudio"})
    if start_at:
        req.add_header("Range", "bytes=%d-" % start_at)
    if token and _allowed(url):
        # only ever sent to huggingface.co itself: an 'unredirected' header is dropped when Hugging Face
        # redirects the download to its file servers
        req.add_unredirected_header("Authorization", "Bearer " + token)
    return req


def explain_http(code, url, token=None):
    """Plain words for a failed download."""
    if code in (401, 403):
        page = repo_page(url)
        if token:
            return ("Hugging Face refused the download (%d). The makers of this model ask you to accept their licence first: "
                    "open %s , sign in, press the button to agree, then try again. If you did, check your Hugging Face token "
                    "(Workflows page: Add Hugging Face token)." % (code, page))
        return ("This model needs a free Hugging Face account (%d): its makers ask you to accept their licence first. "
                "1) Open %s , sign in and press the button to agree. 2) Make a token at https://huggingface.co/settings/tokens "
                "(type: Read). 3) In Comfy Studio press Add Hugging Face token (Workflows page), or run Install Comfy Studio.bat "
                "again. Then download again." % (code, page))
    if code == 404:
        return "The file is no longer at its official link (404). Comfy Studio needs an update for this model."
    if code == 429:
        return "Hugging Face says too many downloads right now (429). Wait a few minutes and try again."
    return "Hugging Face answered with error %d. Try again later." % code


def start(items, opener=None, token=None):
    """Queue downloads. items: [{file, url, target_dir}]. Only official links; one worker thread.
    token: the user's own Hugging Face token (optional; only needed for licence-gated models)."""
    bad = [i.get("file") for i in items if not _allowed(i.get("url")) or not i.get("target_dir")]
    if bad:
        raise ValueError("No official download link for: " + ", ".join(bad[:5]))
    with _state_lock:
        known = {q["file"] for q in STATE["queue"]} | ({STATE["active"]["file"]} if STATE["active"] else set())
        for i in items:
            if i["file"] not in known:
                STATE["queue"].append({"file": i["file"], "url": i["url"], "target_dir": i["target_dir"]})
        STATE["cancel"] = False
        STATE["token"] = token or None
        running = STATE["active"] is not None
    if not running:
        threading.Thread(target=_worker, args=(opener or urllib.request.urlopen,), daemon=True).start()


def status():
    with _state_lock:
        return json.loads(json.dumps({k: v for k, v in STATE.items() if k != "token"}))


def _download(job, opener, part):
    """Fetch into part, carrying on from where an earlier try stopped (big files over a shaky connection).
    Returns the full size when the server told it, else None."""
    url, token = job["url"], STATE.get("token")
    for attempt in range(4):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        try:
            r = opener(_request(url, token, have), timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:                 # the .part is already complete (or broken): start again
                os.remove(part)
                continue
            raise IOError(explain_http(e.code, url, token))
        except (urllib.error.URLError, OSError) as e:      # no internet / timeout: try a few times
            if attempt == 3:
                raise IOError("Couldn't reach Hugging Face (%s). Check the internet connection, then press Download "
                              "again: it carries on from where it stopped." % (getattr(e, "reason", None) or e))
            time.sleep(5 * (attempt + 1))
            continue
        with r:
            resumed = have and getattr(r, "status", 200) == 206
            rest = int(r.headers.get("Content-Length") or 0) or None
            total = (have + rest if resumed else rest) if rest else None
            if not resumed:
                have = 0
            fg = free_gb(job["target_dir"])
            if total and fg is not None and (total - have) / 1e9 > fg - SAFETY_GB:
                raise OSError("Not enough free space on that drive: needs %.1f GB more, %.1f GB free (Comfy Studio keeps "
                              "%d GB spare)." % ((total - have) / 1e9, fg, SAFETY_GB))
            with _state_lock:
                STATE["active"].update(total=total, got=have)
            try:
                with open(part, "ab" if resumed else "wb") as f:
                    while True:
                        if STATE["cancel"]:
                            raise InterruptedError("cancelled")
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        with _state_lock:
                            STATE["active"]["got"] += len(chunk)
            except (OSError, ValueError) as e:            # connection dropped: carry on from here
                if isinstance(e, InterruptedError) or attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
                continue
        if total and os.path.getsize(part) < total and attempt < 3:
            continue
        return total
    return None


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
            total = _download(job, opener, part)
            if total and os.path.getsize(part) != total:
                raise IOError("The download was incomplete (%.1f of %.1f GB). Press Download again: it carries on "
                              "from where it stopped." % (os.path.getsize(part) / 1e9, total / 1e9))
            os.replace(part, dest)
            with _state_lock:
                STATE["done"].append({"file": job["file"], "path": dest, "t": time.time()})
        except FileExistsError:
            with _state_lock:
                STATE["done"].append({"file": job["file"], "path": dest, "t": time.time(), "note": "already there"})
        except Exception as e:
            # a cancelled or failed download keeps its .part so the next try resumes; only a refused one is removed
            try:
                if os.path.exists(part) and os.path.getsize(part) == 0:
                    os.remove(part)
            except OSError:
                pass
            with _state_lock:
                STATE["errors"].append({"file": job["file"], "error": str(e)[:600], "t": time.time()})
