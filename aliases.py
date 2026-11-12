"""Normalise casual image-reference aliases to MiniMax H3's '<Picture N>' form.

Only references to slots that actually have an image are rewritten. Mentions of
empty slots are left as typed. Bare words like 'image' or 'picture' with no
number are left alone, and we avoid matches inside longer words.
"""
from __future__ import annotations

import re

# 1-16, with 10-16 tried before 1-9 so Image10 is not Image1 + '0'.
_NUM = r"(1[0-6]|[1-9])"

# Order matters: bracket and @-prefixed forms first, then bare Picture, Image, @img.
# Lookbehind/ahead keep 'imagery', 'images', 'reimage 2', etc. from matching.
_ALIAS_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"<\s*Picture\s*(?P<n1>" + _NUM + r")\s*>"
    r"|@\s*Picture[\s_]*(?P<n2>" + _NUM + r")(?![0-9A-Za-z])"
    r"|@\s*Image[\s_]*(?P<n3>" + _NUM + r")(?![0-9A-Za-z])"
    r"|@\s*img[\s_]*(?P<n4>" + _NUM + r")(?![0-9A-Za-z])"
    r"|(?<![A-Za-z])Picture[\s_]*(?P<n5>" + _NUM + r")(?![0-9A-Za-z])"
    r"|(?<![A-Za-z])Image[\s_]*(?P<n6>" + _NUM + r")(?![0-9A-Za-z])"
    r"|(?<![A-Za-z])img[\s_]*(?P<n7>" + _NUM + r")(?![0-9A-Za-z])"
    r")"
)


def _num(m):
    for k in ("n1", "n2", "n3", "n4", "n5", "n6", "n7"):
        v = m.group(k)
        if v:
            return int(v)
    return None


def normalize(text, loaded_slots):
    """Rewrite aliases in `text` for slots in `loaded_slots` (iterable of 1..16).

    Returns dict: text, refs (in order of appearance, unique N), warnings.
    """
    loaded = {int(n) for n in (loaded_slots or []) if 1 <= int(n) <= 16}
    text = text or ""
    out = []
    refs = []          # all detected, in order (including repeats and empty)
    seen = set()
    warnings = []
    pos = 0
    for m in _ALIAS_RE.finditer(text):
        n = _num(m)
        if n is None:
            continue
        out.append(text[pos:m.start()])
        raw = m.group(0)
        ok = n in loaded
        if ok:
            out.append("<Picture %d>" % n)
        else:
            out.append(raw)
        rec = {"n": n, "raw": raw, "ok": ok, "canonical": "<Picture %d>" % n}
        refs.append(rec)
        if not ok and n not in seen:
            warnings.append("Image %d has no image" % n)
        seen.add(n)
        pos = m.end()
    out.append(text[pos:])
    return {"text": "".join(out), "refs": refs, "warnings": warnings, "loaded": sorted(loaded)}