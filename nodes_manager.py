"""Find and install missing ComfyUI node packs through ComfyUI-Manager (v2 API).

Only packs from the official Comfy registry are installed directly; that is what the
Manager allows at its default 'normal' security level. Packs that exist only as a
GitHub link need the user to allow git-URL installs in the Manager first, and the
app says so instead of trying to work around it.
"""
from __future__ import annotations

import json, time, urllib.error, urllib.request, uuid

COMFY = "http://127.0.0.1:8188"
_cache = {"t": 0, "map": None, "packs": None}


def _req(method, path, body=None, timeout=60):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(COMFY + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw or b"null")
        except ValueError:
            return r.status, raw.decode("utf-8", "replace")


def manager_available():
    # ComfyUI answers slowly while it is generating, so wait a while and try twice before saying it's unreachable
    for _ in range(2):
        try:
            st, _ = _req("GET", "/v2/manager/version", timeout=15)
            if st == 200:
                return True
        except Exception:
            pass
    return False


# Node names that carry their author's tag, for a useful hint even when the lookup can't run
KNOWN_TAGS = {"pysssss": "ComfyUI-Custom-Scripts (pythongosssss)", "rgthree": "rgthree-comfy", "kjnodes": "ComfyUI-KJNodes",
              "impact": "ComfyUI-Impact-Pack", "was": "WAS Node Suite", "essentials": "ComfyUI_essentials"}


def tag_hint(cls):
    low = str(cls).lower()
    for tag, pack in KNOWN_TAGS.items():
        if "|" + tag in low or "(" + tag + ")" in low or low.endswith(" " + tag):
            return pack
    return None


def _load(force=False):
    if not force and _cache["map"] is not None and time.time() - _cache["t"] < 1800:
        return _cache["map"], _cache["packs"]
    _, mapping = _req("GET", "/v2/customnode/getmappings?mode=cache", timeout=90)
    _, lst = _req("GET", "/v2/customnode/getlist?mode=cache&skip_update=true", timeout=90)
    packs = (lst or {}).get("node_packs") or {}
    _cache.update(t=time.time(), map=mapping or {}, packs=packs)
    return _cache["map"], packs


def resolve(class_types):
    """Map missing node class names to the packs that provide them."""
    if not manager_available():
        hints = ["%s comes from %s." % (c, tag_hint(c)) for c in class_types if tag_hint(c)]
        return {"manager": False, "packs": [], "unknown": list(class_types),
                "message": "ComfyUI-Manager didn't answer (ComfyUI may be busy), so missing nodes can't be looked up right now. "
                           "Tap Check again in a moment, or install them in ComfyUI." + (" " + " ".join(hints) if hints else "")}
    mapping, packs = _load()
    by_url = {}
    for pid, p in packs.items():
        for u in (p.get("files") or []) + [p.get("repository") or "", p.get("reference") or ""]:
            if u:
                by_url[u.rstrip("/").lower().removesuffix(".git")] = pid
    found = {}
    unknown = []
    for cls in class_types:
        hits = [key for key, v in mapping.items() if isinstance(v, list) and v and cls in (v[0] or [])]
        if not hits:
            # pattern-based packs (nodename_pattern)
            for key, v in mapping.items():
                pat = (v[1] or {}).get("nodename_pattern") if isinstance(v, list) and len(v) > 1 and isinstance(v[1], dict) else None
                if pat:
                    try:
                        import re
                        if re.search(pat, cls):
                            hits.append(key)
                    except Exception:
                        pass
        if not hits:
            unknown.append(cls)
            continue
        # Prefer a registry id, then the most-starred pack.
        best, best_score = None, -1
        for key in hits:
            pid = key if key in packs else by_url.get(key.rstrip("/").lower().removesuffix(".git"))
            p = packs.get(pid) if pid else None
            score = (p.get("stars") or 0) if p else -1
            if p and score > best_score:
                best, best_score = pid, score
        if best is None:
            found.setdefault("url:" + hits[0], {"id": None, "title": hits[0].rsplit("/", 1)[-1], "author": "",
                                                "repository": hits[0], "registry": False, "installed": False,
                                                "nodes": []})["nodes"].append(cls)
            continue
        p = packs[best]
        entry = found.setdefault(best, {
            "id": best, "title": p.get("title") or best, "author": p.get("author") or "",
            "repository": p.get("repository") or p.get("reference") or "",
            "description": (p.get("description") or "")[:240], "stars": p.get("stars") or 0,
            "registry": bool(p.get("cnr_latest")) or p.get("version") not in (None, "unknown"),
            "installed": p.get("state") in ("enabled", "disabled"), "state": p.get("state"),
            "version": p.get("version") or "unknown", "nodes": [],
        })
        entry["nodes"].append(cls)
    return {"manager": True, "packs": list(found.values()), "unknown": unknown}


def install(pack_ids):
    """Queue registry packs for install. Returns {queued:[...], blocked:[...], failed:[...]}."""
    _, packs = _load()
    batch, blocked = [], []
    for pid in pack_ids:
        p = packs.get(pid)
        if not p:
            blocked.append({"id": pid, "reason": "Not in the Comfy registry."})
            continue
        if p.get("version") in (None, "unknown"):
            blocked.append({"id": pid, "reason": "Only available as a GitHub link. Enable git-URL installs in ComfyUI-Manager, or install it there."})
            continue
        item = dict(p)
        item["selected_version"] = "latest" if p.get("cnr_latest") else "nightly"
        item["ui_id"] = "cs-" + uuid.uuid4().hex[:8]
        item["skip_post_install"] = False
        batch.append(item)
    failed = []
    if batch:
        try:
            st, res = _req("POST", "/v2/manager/queue/batch", {"install": batch, "batch_id": uuid.uuid4().hex}, timeout=120)
            failed = list((res or {}).get("failed") or []) if isinstance(res, dict) else []
        except urllib.error.HTTPError as e:
            return {"queued": [], "blocked": blocked, "failed": [b["id"] for b in batch],
                    "message": "ComfyUI-Manager refused the install (%s). Check its security settings." % e.code}
    _cache["t"] = 0  # states change after install
    return {"queued": [b["id"] for b in batch if b["id"] not in failed], "blocked": blocked, "failed": failed}


def queue_status():
    try:
        _, st = _req("GET", "/v2/manager/queue/status", timeout=10)
        return st if isinstance(st, dict) else {}
    except Exception as e:
        return {"error": str(e)}


def reboot():
    _req("POST", "/v2/manager/reboot", {}, timeout=10)
    return True
