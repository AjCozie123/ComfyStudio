"""What the internet knows about each LoRA on this PC: its trigger words, real name, tags and base model.

Many LoRAs carry no trigger words inside the file (ai-toolkit and extracted LoRAs never do), so the app looks each one
up by its fingerprint (the SHA256 of the file, the same on every PC whatever the file is called):
  1. CivitAI's public API (batch, up to 100 fingerprints per call; no account needed). CivitAI blocks some countries
     (the UK answers 451 "region blocked"); then it is skipped for a day.
  2. CivArchive (an archive of CivitAI and Hugging Face LoRAs, also reachable from the UK) for anything CivitAI didn't
     find, one fingerprint per second.
Only the fingerprint is sent: never the file, its name, a prompt or anything else. Nothing is written next to the
user's LoRAs. Results (and "not found") are kept in data/lora_online.json, fingerprints in data/lora_hashes.json (by
path + size + date, so a file is hashed once). Runs in the background only while nothing is generating, and can be
switched off in Settings ("Look up LoRA info online").
"""
from __future__ import annotations

import hashlib, html, json, os, re, threading, time, urllib.error, urllib.request

UA = "ComfyStudio (local LoRA trigger lookup)"
CIVITAI_BATCH = "https://civitai.com/api/v1/model-versions/by-hash"
CIVARCHIVE = "https://civarchive.com/api/sha256/"
RETRY_NOT_FOUND = 30 * 86400     # look again after a month (a LoRA can be published later)
BLOCK_PAUSE = 86400              # a region-blocked service is skipped for a day
PACE = 6.5                       # seconds between CivArchive lookups (it allows 600 an hour; this stays well under)

_lock = threading.RLock()
STATE = {"running": False, "hashed": 0, "total": 0, "found": 0, "not_found": 0, "pending": 0, "last_error": "",
         "civitai_blocked_until": 0, "civarchive_paused_until": 0, "updated": 0}


def _load(p):
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(p, d):
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def hashes_path(data_dir):
    return os.path.join(data_dir, "lora_hashes.json")


def online_path(data_dir):
    return os.path.join(data_dir, "lora_online.json")


def sidecar_sha256(full):
    """LoRA Manager already stores the fingerprint next to the file (<name>.metadata.json "sha256"): no need to hash."""
    base = re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", full, flags=re.I)
    for p in (base + ".metadata.json", full + ".rgthree-info.json"):
        try:
            if os.path.isfile(p) and os.path.getsize(p) < 4 * 1024 * 1024:
                h = str((json.load(open(p, encoding="utf-8", errors="replace")) or {}).get("sha256") or "").lower()
                if re.fullmatch(r"[0-9a-f]{64}", h):
                    return h
        except (OSError, ValueError, AttributeError):
            pass
    return ""


def sha256_file(full, stop=None):
    h = hashlib.sha256()
    with open(full, "rb") as f:
        while True:
            if stop and stop():
                return ""
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _clean_about(text, n=400):
    t = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    return " ".join(t.split())[:n]


def _record(src, model, version, files_names=()):
    """One plain record from a CivitAI or CivArchive answer."""
    model, version = model or {}, version or {}
    words = version.get("trainedWords")
    if words is None:
        words = version.get("trigger")
    tags = model.get("tags") or []
    tags = [t.get("name") if isinstance(t, dict) else t for t in tags]
    return {
        "found": True, "src": src, "t": int(time.time()),
        "trainedWords": [str(w) for w in (words or []) if str(w or "").strip()][:12],
        "name": str(model.get("name") or "")[:120],
        "version": str(version.get("name") or "")[:80],
        "base": str(version.get("baseModel") or "")[:60],
        "tags": [str(t) for t in tags if t][:16],
        "about": _clean_about(version.get("description") or model.get("description")),
        "nsfw": bool(model.get("nsfw") or model.get("is_nsfw")),
        "files": [str(f) for f in files_names if f][:8],
    }


def _http_json(url, body=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def civitai_batch(hashes):
    """{sha256: record} for the fingerprints CivitAI knows. Raises PermissionError when region-blocked."""
    out = {}
    try:
        res = _http_json(CIVITAI_BATCH, [h.upper() for h in hashes], timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (451, 403):
            raise PermissionError("CivitAI is not available from this country") from e
        raise
    want = set(hashes)
    for v in res if isinstance(res, list) else []:
        names = [f.get("name") for f in v.get("files") or []]
        for f in v.get("files") or []:
            h = str((f.get("hashes") or {}).get("SHA256") or "").lower()
            if h in want:
                out[h] = _record("civitai", v.get("model"), v, names)
    return out


def civarchive_one(h):
    """A record, None when CivArchive doesn't know it. Raises on network trouble."""
    try:
        d = _http_json(CIVARCHIVE + h)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    m = d.get("model") if isinstance(d, dict) else None
    if not m:
        return None
    names = [f.get("filename") for f in d.get("files") or []]
    return _record("civarchive", m, m.get("version") or {}, names)


def info_for(name, data_dir, _cache={}):
    """What is known online about one LoRA (by its ComfyUI name), {} if nothing (yet)."""
    hp, op = hashes_path(data_dir), online_path(data_dir)
    try:
        sig = (os.path.getmtime(hp), os.path.getmtime(op))
    except OSError:
        return {}
    if _cache.get("sig") != sig:
        _cache.update(sig=sig, hashes=_load(hp), online=_load(op))
    h = (_cache["hashes"].get(name) or {}).get("sha256")
    rec = _cache["online"].get(h) if h else None
    return rec if rec and rec.get("found") else {}


class Worker:
    """Background job: fingerprint the LoRAs, then look the new ones up. targets() -> [(name, full_path)],
    enabled() -> bool (the Settings switch), idle() -> bool (nothing generating)."""

    def __init__(self, data_dir, targets, enabled, idle, log=None):
        self.data_dir, self.targets, self.enabled, self.idle, self.log = data_dir, targets, enabled, idle, log
        self.kick = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _note(self, msg):
        if self.log:
            try:
                self.log.info("lora lookup: %s", msg)
            except Exception:
                pass

    def _loop(self):
        time.sleep(45)   # let the app and ComfyUI settle first
        while True:
            try:
                if self.enabled():
                    self.run_once()
            except Exception as e:   # never take the app down
                STATE["last_error"] = str(e)[:200]
                self._note(e)
            STATE["running"] = False
            self.kick.wait(15 * 60)
            self.kick.clear()

    def run_once(self):
        STATE["running"] = True
        stop = lambda: not self.enabled() or not self.idle()
        hp, op = hashes_path(self.data_dir), online_path(self.data_dir)
        hashes, online = _load(hp), _load(op)
        targets = [(n, f) for n, f in (self.targets() or []) if f and os.path.isfile(f)]
        STATE["total"] = len(targets)
        # 1. fingerprints (a LoRA Manager sidecar already has one; else read the file once)
        dirty = 0
        for name, full in targets:
            st = os.stat(full)
            sig = "%d|%d" % (st.st_size, int(st.st_mtime))
            cur = hashes.get(name) or {}
            if cur.get("sig") == sig and cur.get("sha256"):
                continue
            while not self.idle():
                if not self.enabled():
                    return
                time.sleep(20)
            h = sidecar_sha256(full) or sha256_file(full, stop=lambda: not self.enabled())
            if not h:
                return
            hashes[name] = {"sig": sig, "sha256": h}
            dirty += 1
            if dirty % 20 == 0:
                _save(hp, hashes)
        if dirty:
            _save(hp, hashes)
        STATE["hashed"] = sum(1 for n, _f in targets if (hashes.get(n) or {}).get("sha256"))
        # 2. look up the fingerprints not known yet (or "not found" a month ago)
        now = time.time()
        todo = []
        for name, _f in targets:
            h = (hashes.get(name) or {}).get("sha256")
            r = online.get(h) if h else None
            if h and h not in todo and (r is None or (not r.get("found") and now - r.get("t", 0) > RETRY_NOT_FOUND)):
                todo.append(h)
        STATE["pending"] = len(todo)
        civ_found = set()
        if todo and now > STATE["civitai_blocked_until"]:
            for i in range(0, len(todo), 100):
                if stop():
                    return
                try:
                    got = civitai_batch(todo[i:i + 100])
                except PermissionError:
                    STATE["civitai_blocked_until"] = now + BLOCK_PAUSE
                    self._note("CivitAI is region-blocked here; using CivArchive")
                    break
                except Exception as e:
                    self._note("CivitAI: %s" % e)
                    break
                civ_found.update(got)
                with _lock:
                    online.update(got)
                    _save(op, online)
                time.sleep(2)
        if time.time() < STATE["civarchive_paused_until"]:
            return
        for h in todo:
            if h in civ_found:
                continue   # CivitAI just answered for it
            if stop():
                return
            try:
                rec = civarchive_one(h)
            except urllib.error.HTTPError as e:
                if e.code == 429:   # its limit (600 an hour) is used up: wait as long as it says, then carry on
                    try:
                        wait = int(e.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        wait = 0
                    STATE["civarchive_paused_until"] = time.time() + max(300, min(wait, 7200))
                    self._note("CivArchive asks to wait %d s" % max(300, min(wait, 7200)))
                    return
                self._note("CivArchive %s: %s" % (e.code, h[:10]))
                continue
            except Exception as e:   # offline: stop, try again later
                self._note("CivArchive: %s" % e)
                return
            with _lock:
                online[h] = rec or {"found": False, "t": int(time.time())}
                _save(op, online)
            STATE["pending"] = max(0, STATE["pending"] - 1)
            time.sleep(PACE)
        STATE["found"] = sum(1 for n, _f in targets if (online.get((hashes.get(n) or {}).get("sha256")) or {}).get("found"))
        STATE["not_found"] = sum(1 for n, _f in targets
                                 if (online.get((hashes.get(n) or {}).get("sha256")) or {}).get("found") is False)
        STATE["updated"] = int(time.time())


def status():
    return dict(STATE)
