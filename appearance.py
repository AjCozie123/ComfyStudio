# -*- coding: utf-8 -*-
"""Image-aware appearance attributes for Auto-understanding preservation clauses.

Background queue (concurrency 1-2), per-image timeout, SHA-256 cache in memory
and under data/appearance_cache. Never blocks Generate: wait at most
GENERATE_WAIT_SEC (default 1s), then use neutral wording for any image that is
not ready. Failures are isolated to that image and never raise out to Generate.

The Prompt Enhancer vision LLM shares the 8 GB ComfyUI GPU, so this module does
not submit Comfy /prompt jobs or load that model. Attribute checks go through a
pluggable provider (tests inject one). When no provider has a result, callers
fall back to neutral wording; the PE system prompt still forbids inventing
absent attributes when PE itself can see the images at generate time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

MAX_IMAGES = 16
MAX_SIDE = 384
PROVIDER_TIMEOUT_SEC = 8.0
GENERATE_WAIT_SEC = 1.0
DEFAULT_WORKERS = 1
MAX_WORKERS = 2
CACHE_KEEP = 200

_LOCK = threading.RLock()
_MEM = {}  # digest -> attrs dict
_INFLIGHT = {}  # digest -> Future
_EXECUTOR = None
_DATA_DIR = None
_PROVIDER = None
_TIMEOUT = PROVIDER_TIMEOUT_SEC
_GENERATE_WAIT = GENERATE_WAIT_SEC
_MAX_SIDE = MAX_SIDE
_WORKERS = DEFAULT_WORKERS
_TIMINGS = []  # recent {event, ms, digest}
_TIMINGS_KEEP = 80

# Tiny structured prompt for a future / optional vision provider.
ATTR_PROMPT = (
    "Look at this image. Reply with JSON only, no markdown: "
    '{"people_count":0,"has_clothing":false,"has_accessories":false,'
    '"has_tattoos":false,"has_hair":false}. '
    "people_count is the number of distinct people clearly visible (0-16). "
    "has_clothing is false if the subject is nude or only minimally clothed "
    "(underwear/swimwear with no worn outfit). "
    "has_accessories is true only if jewelry, glasses, a hat, bag, watch or "
    "similar is clearly visible. has_tattoos is true only if tattoos are "
    "clearly visible. has_hair is true only if scalp hair is clearly visible. "
    "Do not guess; if unsure, use false (and people_count 0 if no person is clear)."
)


def configure(data_dir=None, max_workers=None, provider=None, timeout=None,
              generate_wait=None, max_side=None, reset=False):
    """Set cache directory and queue options. Safe to call more than once."""
    global _DATA_DIR, _PROVIDER, _TIMEOUT, _GENERATE_WAIT, _MAX_SIDE, _WORKERS, _EXECUTOR
    with _LOCK:
        if data_dir:
            _DATA_DIR = data_dir
        if provider is not None or reset:
            _PROVIDER = provider
        if timeout is not None:
            _TIMEOUT = float(timeout)
        if generate_wait is not None:
            _GENERATE_WAIT = float(generate_wait)
        if max_side is not None:
            _MAX_SIDE = int(max_side)
        if max_workers is not None:
            n = int(max_workers)
            _WORKERS = 1 if n < 1 else (MAX_WORKERS if n > MAX_WORKERS else n)
            if _EXECUTOR is not None:
                try:
                    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    _EXECUTOR.shutdown(wait=False)
                _EXECUTOR = None
        if reset:
            _MEM.clear()
            _INFLIGHT.clear()
            _TIMINGS.clear()
            if _EXECUTOR is not None:
                try:
                    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    _EXECUTOR.shutdown(wait=False)
                _EXECUTOR = None


def reset_for_tests(data_dir=None, provider=None, timeout=0.4, generate_wait=1.0,
                    max_workers=1, max_side=384):
    configure(data_dir=data_dir, provider=provider, timeout=timeout,
              generate_wait=generate_wait, max_workers=max_workers,
              max_side=max_side, reset=True)


def _executor():
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="h3-appear")
        return _EXECUTOR


def _record(event, ms, digest=""):
    rec = {"event": event, "ms": round(float(ms), 3), "digest": digest[:12], "t": time.time()}
    with _LOCK:
        _TIMINGS.append(rec)
        if len(_TIMINGS) > _TIMINGS_KEEP:
            del _TIMINGS[: len(_TIMINGS) - _TIMINGS_KEEP]
    return rec


def recent_timings(n=20):
    with _LOCK:
        return list(_TIMINGS[-int(n or 20):])


def file_digest(path: str) -> Optional[str]:
    """SHA-256 of the file bytes. None if unreadable."""
    if not path:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _cache_file(digest: str) -> Optional[str]:
    if not _DATA_DIR or not digest:
        return None
    return os.path.join(_DATA_DIR, "appearance_cache", digest[:2], digest + ".json")


def _normalize_attrs(data, source="vision") -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    try:
        people = int(data.get("people_count") if data.get("people_count") is not None else 0)
    except (TypeError, ValueError):
        people = 0
    if people < 0:
        people = 0
    if people > MAX_IMAGES:
        people = MAX_IMAGES

    def flag(key):
        v = data.get(key)
        if v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("1", "true", "yes")):
            return True
        return False

    out = {
        "ok": True,
        "people_count": people,
        "has_clothing": flag("has_clothing"),
        "has_accessories": flag("has_accessories"),
        "has_tattoos": flag("has_tattoos"),
        "has_hair": flag("has_hair"),
        "source": source or data.get("source") or "vision",
    }
    return out


def parse_attrs(text, source="vision") -> Optional[dict]:
    """Parse a short JSON attribute object from model text. None if unusable."""
    if not text or not str(text).strip():
        return None
    src = str(text)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", src):
        try:
            data, _ = decoder.raw_decode(src, match.start())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if not any(k in data for k in ("people_count", "has_clothing", "has_hair")):
            continue
        got = _normalize_attrs(data, source=source)
        if got:
            return got
    return None


def _disk_get(digest):
    path = _cache_file(digest)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if rec.get("ok") is False:
        return None
    return _normalize_attrs(rec, source=rec.get("source") or "cache")


def _disk_put(digest, attrs):
    path = _cache_file(digest)
    if not path or not attrs or not attrs.get("ok"):
        return
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        rec = dict(attrs)
        rec["t"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, path)
        _cache_prune()
    except OSError:
        pass


def _cache_prune():
    root = os.path.join(_DATA_DIR or "", "appearance_cache")
    if not _DATA_DIR or not os.path.isdir(root):
        return
    files = []
    try:
        for dirpath, _dirnames, names in os.walk(root):
            for n in names:
                if n.endswith(".json"):
                    files.append(os.path.join(dirpath, n))
    except OSError:
        return
    if len(files) <= CACHE_KEEP:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for p in files[: max(0, len(files) - CACHE_KEEP)]:
        try:
            os.remove(p)
        except OSError:
            pass


def cache_get(digest):
    if not digest:
        return None
    with _LOCK:
        hit = _MEM.get(digest)
    if hit and hit.get("ok"):
        return hit
    rec = _disk_get(digest)
    if rec and rec.get("ok"):
        with _LOCK:
            _MEM[digest] = rec
        return rec
    return None


def cache_put(digest, attrs):
    if not digest or not attrs or not attrs.get("ok"):
        return
    norm = _normalize_attrs(attrs, source=attrs.get("source") or "cache")
    if not norm:
        return
    with _LOCK:
        _MEM[digest] = norm
    _disk_put(digest, norm)


def downscale_image(path, max_side=None):
    """Return a downscaled RGB PIL image, or None. Does not raise."""
    side = int(max_side or _MAX_SIDE or MAX_SIDE)
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        elif im.mode == "L":
            im = im.convert("RGB")
        im.thumbnail((side, side), Image.Resampling.LANCZOS)
        return im
    except Exception:
        return None


def _run_provider(path, digest):
    t0 = time.perf_counter()
    provider = _PROVIDER
    if provider is None:
        _record("no_provider", (time.perf_counter() - t0) * 1000, digest)
        return None
    timeout = float(_TIMEOUT or PROVIDER_TIMEOUT_SEC)
    img = None
    try:
        t1 = time.perf_counter()
        img = downscale_image(path)
        _record("downscale", (time.perf_counter() - t1) * 1000, digest)
    except Exception:
        img = None
    try:
        describe = getattr(provider, "describe", None)
        if callable(describe):
            raw = describe(path, image=img, timeout=timeout)
        elif callable(provider):
            raw = provider(path, image=img, timeout=timeout)
        else:
            raw = None
    except Exception as exc:
        _record("provider_error", (time.perf_counter() - t0) * 1000, digest)
        return {"ok": False, "error": type(exc).__name__}
    attrs = raw if isinstance(raw, dict) and "people_count" in raw or (isinstance(raw, dict) and raw.get("ok")) else None
    if isinstance(raw, dict) and not attrs:
        attrs = _normalize_attrs(raw) if raw.get("ok") is not False else None
    if isinstance(raw, dict) and raw.get("ok") is not False and attrs is None:
        attrs = _normalize_attrs(raw)
    if isinstance(raw, str):
        attrs = parse_attrs(raw)
    if not attrs or not attrs.get("ok"):
        _record("provider_empty", (time.perf_counter() - t0) * 1000, digest)
        return None
    cache_put(digest, attrs)
    _record("provider_ok", (time.perf_counter() - t0) * 1000, digest)
    return attrs


def _worker(path, digest, timeout):
    """Run the provider with a hard timeout; never raise to the caller."""
    done = []

    def target():
        done.append(_run_provider(path, digest))

    th = threading.Thread(target=target, name="h3-appear-one", daemon=True)
    th.start()
    th.join(timeout=float(timeout or _TIMEOUT or PROVIDER_TIMEOUT_SEC))
    if th.is_alive():
        _record("timeout", float(timeout) * 1000, digest)
        return None
    return done[0] if done else None


def _submit(path):
    digest = file_digest(path)
    if not digest:
        return None, None
    hit = cache_get(digest)
    if hit:
        return digest, None
    with _LOCK:
        fut = _INFLIGHT.get(digest)
        if fut is not None:
            return digest, fut
        timeout = float(_TIMEOUT or PROVIDER_TIMEOUT_SEC)
        fut = _executor().submit(_worker, path, digest, timeout)
        _INFLIGHT[digest] = fut

        def _clear(f, d=digest):
            with _LOCK:
                _INFLIGHT.pop(d, None)

        fut.add_done_callback(_clear)
        return digest, fut


def prefetch(path):
    """Queue an attribute check if needed. Returns immediately. Never raises."""
    try:
        if not path or not os.path.isfile(path):
            return False
        digest, _fut = _submit(path)
        return bool(digest)
    except Exception:
        return False


def prefetch_many(paths):
    n = 0
    for p in list(paths or [])[:MAX_IMAGES]:
        if prefetch(p):
            n += 1
    return n


def get(path, wait=None):
    """Return attrs dict or None. wait defaults to GENERATE_WAIT_SEC. Never raises."""
    if not path:
        return None
    try:
        t0 = time.perf_counter()
        digest = file_digest(path)
        if not digest:
            return None
        hit = cache_get(digest)
        if hit:
            _record("cache_hit", (time.perf_counter() - t0) * 1000, digest)
            return hit
        digest, fut = _submit(path)
        if not fut:
            hit = cache_get(digest) if digest else None
            return hit
        budget = _GENERATE_WAIT if wait is None else float(wait)
        if budget < 0:
            budget = 0.0
        try:
            attrs = fut.result(timeout=budget)
        except Exception:
            attrs = None
        if attrs and attrs.get("ok"):
            _record("wait_ok", (time.perf_counter() - t0) * 1000, digest)
            return attrs
        _record("wait_fallback", (time.perf_counter() - t0) * 1000, digest)
        return None
    except Exception:
        return None


def get_many(paths, wait=None):
    """Attrs for each path (None if missing/not ready). Total wait is capped. Never raises."""
    items = list(paths or [])
    deadline = time.perf_counter() + (_GENERATE_WAIT if wait is None else max(0.0, float(wait)))
    # Kick the queue first so work overlaps the wait window.
    try:
        prefetch_many(items)
    except Exception:
        pass
    out = []
    for p in items:
        remain = deadline - time.perf_counter()
        if remain < 0:
            remain = 0.0
        if not p:
            out.append(None)
            continue
        try:
            out.append(get(p, wait=remain))
        except Exception:
            out.append(None)
    return out


def slot_attrs(appearances, n):
    """Look up 1-based slot attributes from a list or dict. None if missing."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if not appearances:
        return None
    if isinstance(appearances, dict):
        rec = appearances.get(n)
        if rec is None:
            rec = appearances.get(str(n))
        return rec if isinstance(rec, dict) and rec.get("ok") else None
    if isinstance(appearances, (list, tuple)) and 1 <= n <= len(appearances):
        rec = appearances[n - 1]
        return rec if isinstance(rec, dict) and rec.get("ok") else None
    return None


def _join(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "%s and %s" % (parts[0], parts[1])
    return "%s, and %s" % (", ".join(parts[:-1]), parts[-1])


def _visible_detail_parts(attrs):
    """Only attributes actually flagged present. Never lists absent items."""
    parts = []
    if attrs.get("has_clothing"):
        parts.append("clothing with exact colours, patterns, fabric and logos")
    if attrs.get("has_accessories"):
        parts.append("accessories")
    if attrs.get("has_tattoos"):
        parts.append("tattoos")
    return parts


def neutral_clause(tag, people_count=None):
    tag = tag or "<Picture 1>"
    if people_count is not None and int(people_count) >= 2:
        return (
            "Preserve the exact appearance of the people in %s as shown in that picture, "
            "unless this prompt asks otherwise."
        ) % tag
    if people_count == 0:
        return (
            "Preserve the exact appearance of what is shown in %s, unless this prompt asks otherwise."
        ) % tag
    return (
        "The subject is the person in %s. Preserve that subject's exact appearance as shown in %s, "
        "including exact face and identity, unless this prompt asks otherwise."
    ) % (tag, tag)


def clause_for(tag, attrs=None) -> str:
    """Per-image preservation sentence. Neutral if attrs are missing or failed."""
    tag = tag or "<Picture 1>"
    if not isinstance(attrs, dict) or not attrs.get("ok"):
        return neutral_clause(tag)

    people = 0
    try:
        people = int(attrs.get("people_count") or 0)
    except (TypeError, ValueError):
        people = 0

    if people >= 2:
        extras = _visible_detail_parts(attrs)
        if extras:
            return (
                "Preserve the exact appearance of the people in %s, including their %s, "
                "unless this prompt asks otherwise."
            ) % (tag, _join(extras))
        return (
            "Preserve the exact appearance of the people in %s as shown in that picture, "
            "unless this prompt asks otherwise."
        ) % tag

    if people == 0:
        extras = _visible_detail_parts(attrs)
        if extras:
            return (
                "Preserve the exact appearance of what is shown in %s, including %s, "
                "unless this prompt asks otherwise."
            ) % (tag, _join(extras))
        return neutral_clause(tag, people_count=0)

    parts = ["exact face and identity", "facial features"]
    if attrs.get("has_hair"):
        parts.append("hair style and colour")
    parts.append("skin tone, body shape, proportions and figure")
    parts.append("pose unless this prompt asks otherwise")
    parts.extend(_visible_detail_parts(attrs))
    parts.append("fine details")
    return "The subject is the person in %s. Preserve that subject's %s." % (tag, _join(parts))


def keep_subject_fragment(tag, attrs=None) -> str:
    """Short 'keep the subject in <Picture N> â€” ...' for background-replace etc."""
    tag = tag or "<Picture 1>"
    if not isinstance(attrs, dict) or not attrs.get("ok"):
        return "Keep the subject in %s â€” exact appearance as shown, including face, identity, body and pose" % tag
    people = 0
    try:
        people = int(attrs.get("people_count") or 0)
    except (TypeError, ValueError):
        people = 0
    who = "people" if people >= 2 else "subject"
    bits = ["exact face and identity", "body"]
    if attrs.get("has_hair"):
        bits.append("hair")
    if attrs.get("has_clothing"):
        bits.append("clothing")
    bits.append("pose")
    return "Keep the %s in %s â€” %s" % (who, tag, _join(bits))


def character_outfit_sentence(appearances) -> str:
    """Outfit wording only when at least one identity image is known to show clothing."""
    known = []
    if not appearances:
        return ""
    seq = appearances.values() if isinstance(appearances, dict) else list(appearances)
    for rec in seq:
        if isinstance(rec, dict) and rec.get("ok"):
            known.append(rec)
    if not known:
        return ""
    if any(rec.get("has_clothing") for rec in known):
        return (
            "and the same outfit (colours, patterns, fabric and logos) unless the user asked otherwise. "
        )
    return ""
