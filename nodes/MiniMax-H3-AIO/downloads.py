"""Fetch the model files listed in models.json from their original sources.

Used by install.py and by the node's "missing models" routes. Only URLs from models.json are
fetched, only into ComfyUI/models, and each file is written to <name>.part and renamed when
complete, so a cut-off download never looks like a finished model. A file whose name contains the
entry's "match" text anywhere in the same top-level models folder counts as present.
"""

import json
import os
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 8 << 20

state = {"running": False, "file": "", "done_bytes": 0, "total_bytes": 0, "finished": [], "failed": []}


def manifest(include_optional=True):
    with open(os.path.join(HERE, "models.json"), encoding="utf-8") as f:
        data = json.load(f)
    return data["required"] + (data["optional"] if include_optional else [])


def target(models_dir, entry):
    root = os.path.realpath(models_dir)
    path = os.path.realpath(os.path.join(root, *entry["folder"].split("/"), entry["file"]))
    if os.path.commonpath([root, path]) != root:
        raise ValueError(f"{entry['file']}: folder leaves the models directory")
    return path


def _comfy_names(kind):
    try:
        import folder_paths
        return folder_paths.get_filename_list(kind)
    except (ImportError, KeyError):
        return []


def present(models_dir, entry):
    needle = entry.get("match", entry["file"]).lower()
    top = entry["folder"].split("/")[0]
    kinds = {"diffusion_models": ("diffusion_models", "unet")}.get(top, (top,))
    for kind in kinds:
        if any(needle in os.path.basename(n.replace("\\", "/")).lower() for n in _comfy_names(kind)):
            return True
    for _, _, files in os.walk(os.path.join(models_dir, top)):
        if any(needle in f.lower() and not f.endswith(".part") for f in files):
            return True
    return False


def missing(models_dir, include_optional=True):
    return [e for e in manifest(include_optional) if not present(models_dir, e)]


def fetch(models_dir, entry, progress=None):
    path = target(models_dir, entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(entry["url"], headers={"User-Agent": "MiniMax-H3-AIO"})
    if done:
        req.add_header("Range", f"bytes={done}-")
    with urllib.request.urlopen(req, timeout=60) as r:
        if done and r.status != 206:
            done = 0
        total = done + int(r.headers.get("Content-Length") or 0)
        with open(part, "ab" if done else "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(part, path)
    return path


def _worker(models_dir, todo):
    try:
        for e in todo:
            state.update(file=e["file"], done_bytes=0, total_bytes=0)
            try:
                fetch(models_dir, e, lambda d, t: state.update(done_bytes=d, total_bytes=t))
                state["finished"].append(e["file"])
            except Exception as err:
                state["failed"].append(f"{e['file']}: {err}")
    finally:
        state.update(running=False, file="")


def start(models_dir, include_optional=True):
    if state["running"]:
        return False
    todo = missing(models_dir, include_optional)
    if not todo:
        return False
    state.update(running=True, finished=[], failed=[])
    threading.Thread(target=_worker, args=(models_dir, todo), daemon=True).start()
    return True
