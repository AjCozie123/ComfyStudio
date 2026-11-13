"""Where ComfyUI and its folders are on THIS PC.

Comfy Studio runs on each user's own computer, so nothing about the owner's machine may be hard-coded.
Order of truth:
  1. data/settings.json (written once detected; the user can edit or re-detect from the app)
  2. ComfyUI itself, when it's running: /internal/folder_paths gives the real model folders (-> ComfyUI root and
     output folder), /system_stats gives its launch options (--input-directory / --output-directory)
  3. COMFYSTUDIO_* environment variables
  4. a local placeholder, flagged paths_ok = False so the app can ask the user to start ComfyUI and press Find
"""
from __future__ import annotations

import ipaddress, json, os, socket, urllib.request

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def _settings_path(data_dir):
    return os.path.join(data_dir, "settings.json")


def load(data_dir):
    try:
        with open(_settings_path(data_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data_dir, settings):
    os.makedirs(data_dir, exist_ok=True)
    tmp = _settings_path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, _settings_path(data_dir))


def _get(url, path, timeout=3.0):
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as r:
        return json.load(r)


def _argv_value(argv, flag):
    for i, a in enumerate(argv or []):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def detect(comfy_url=DEFAULT_COMFY_URL, timeout=3.0):
    """Ask a running ComfyUI where its folders are. {} when it isn't reachable."""
    try:
        fp = _get(comfy_url, "/internal/folder_paths", timeout)
    except Exception:
        return {}
    roots = [p for p in (fp.get("checkpoints") or fp.get("diffusion_models") or []) if isinstance(p, str)]
    model_dir = next((p for p in roots if os.path.basename(os.path.dirname(p)).lower() == "models"), roots[0] if roots else None)
    if not model_dir:
        return {}
    comfy_root = os.path.dirname(os.path.dirname(model_dir))
    out = {"comfy_root": comfy_root, "output_dir": os.path.join(comfy_root, "output"),
           "input_dir": os.path.join(comfy_root, "input"), "comfy_url": comfy_url}
    # ComfyUI also registers <output>/checkpoints etc.: that gives the real output folder if it was moved
    for p in roots:
        parent = os.path.dirname(p)
        if parent != os.path.dirname(model_dir) and os.path.basename(p).lower() == "checkpoints":
            out["output_dir"] = parent
            break
    try:
        argv = (_get(comfy_url, "/system_stats", timeout).get("system") or {}).get("argv") or []
    except Exception:
        argv = []
    base = os.path.dirname(comfy_root)       # portable builds start ComfyUI from the folder above it
    for flag, key in (("--input-directory", "input_dir"), ("--output-directory", "output_dir")):
        v = _argv_value(argv, flag)
        if v:
            out[key] = v if os.path.isabs(v) else os.path.abspath(os.path.join(base, v))
    return out


def tailnet_ips():
    """This PC's Tailscale addresses (for the optional phone access). Empty without Tailscale."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ipaddress.ip_address(ip) in TAILNET:
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def resolve(data_dir, legacy=None):
    """The settings the app runs with. Re-detects when a saved folder no longer exists."""
    s = load(data_dir)
    s.setdefault("comfy_url", os.environ.get("COMFYSTUDIO_COMFY_URL") or DEFAULT_COMFY_URL)
    have = all(s.get(k) and os.path.isdir(s[k]) for k in ("comfy_root", "output_dir", "input_dir"))
    if not have:
        det = detect(s["comfy_url"])
        if det:
            s.update(det)
            s["paths_ok"] = True
            save(data_dir, s)
            have = True
    if not have:
        env = {k: os.environ.get("COMFYSTUDIO_" + k.upper()) for k in ("comfy_root", "output_dir", "input_dir")}
        if all(env.values()) and all(os.path.isdir(v) for v in env.values()):
            s.update(env)
            have = True
    if not have and legacy and all(os.path.isdir(legacy.get(k) or "") for k in ("comfy_root", "output_dir", "input_dir")):
        s.update(legacy)          # an older copy of this app on this PC: keep working until detection succeeds
        have = True
    if not have:
        ph = os.path.join(data_dir, "_comfyui_not_found")
        s.update(comfy_root=ph, output_dir=os.path.join(ph, "output"), input_dir=os.path.join(ph, "input"))
    s["paths_ok"] = bool(have)
    s["extra_hosts"] = [h for h in (s.get("extra_hosts") or []) if isinstance(h, str)]
    return s
