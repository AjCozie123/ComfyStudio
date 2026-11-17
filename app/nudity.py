# -*- coding: utf-8 -*-
"""Is the person in a picture undressed? (NudeNet, on the CPU, ~0.05 s a picture; never touches the GPU.)

Used only to stop clothing lines being added for someone who has no clothes on: "keep their clothing exactly the
same" / "his clothes are the very same garments seen from the new side" made Krea 2 Edit dress a nude man on a
side-profile turn (2026-11-16). Everything else keeps the normal clothing lines.

undressed(path) -> True  : genitals, female breasts, buttocks or anus clearly exposed
                   False : none of those exposed (dressed, or only a bare chest / belly / feet)
                   None  : NudeNet isn't installed or the picture couldn't be read (callers keep normal behaviour)
"""
from __future__ import annotations

import os
import threading

_EXPOSED = {"MALE_GENITALIA_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED",
            "ANUS_EXPOSED"}
MIN_SCORE = 0.45
_LOCK = threading.Lock()
_DET = None
_CACHE = {}          # (path, mtime, size) -> bool


def _detector():
    global _DET
    if _DET is None:
        from nudenet import NudeDetector          # imported on first use: the app starts fine without it
        _DET = NudeDetector()
    return _DET


def undressed(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.abspath(path), st.st_mtime, st.st_size)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with _LOCK:
            found = _detector().detect(path)
    except Exception:
        return None
    hit = any(d.get("class") in _EXPOSED and float(d.get("score") or 0) >= MIN_SCORE for d in (found or []))
    if len(_CACHE) > 500:
        _CACHE.clear()
    _CACHE[key] = hit
    return hit


def prefetch(path):
    """Check a picture in the background as soon as it is added, so Generate never waits for it."""
    threading.Thread(target=undressed, args=(path,), daemon=True).start()
