"""The user's own additions to Auto understanding, per engine or for all engines (data/understanding.json).

  words  : [{"from": "Maya", "to": "the woman with long dark hair"}]  replaced in the prompt (whole words)
  extra  : sentences added to every prompt ("Keep her tattoos visible.")
"*" holds the all-engines additions; an engine id holds that engine's. Reverting deletes the user's additions, so
the app's built-in understanding is exactly as shipped.
"""
from __future__ import annotations

import json, os, re

MAX_WORDS = 60
MAX_EXTRA = 2000


def _path(data_dir):
    return os.path.join(data_dir, "understanding.json")


def load(data_dir):
    try:
        d = json.load(open(_path(data_dir), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data_dir, d):
    tmp = _path(data_dir) + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, _path(data_dir))


def clean(entry):
    """Validate one scope's additions: {"words": [...], "extra": str}."""
    words = []
    for w in (entry or {}).get("words") or []:
        if not isinstance(w, dict):
            continue
        a, b = str(w.get("from") or "").strip(), str(w.get("to") or "").strip()
        if a and b and len(a) <= 80 and len(b) <= 400:
            words.append({"from": a, "to": b})
    extra = str((entry or {}).get("extra") or "").strip()[:MAX_EXTRA]
    out = {}
    if words:
        out["words"] = words[:MAX_WORDS]
    if extra:
        out["extra"] = extra
    return out


def scopes_for(data_dir, engine):
    d = load(data_dir)
    return [d.get("*") or {}, d.get(str(engine or "builtin")) or {}]


def apply_words(prompt, data_dir, engine):
    """Replace the user's own words (all-engines first, then this engine's), whole words, any case."""
    text = prompt or ""
    for scope in scopes_for(data_dir, engine):
        for w in scope.get("words") or []:
            text = re.sub(r"(?<![\w])" + re.escape(w["from"]) + r"(?![\w])", w["to"].replace("\\", "\\\\"), text, flags=re.I)
    return text


def extra_text(data_dir, engine):
    parts = [s.get("extra") for s in scopes_for(data_dir, engine) if s.get("extra")]
    return " ".join(p.strip().rstrip(".") + "." for p in parts)


def apply_extra(prompt, data_dir, engine):
    x = extra_text(data_dir, engine)
    if not x or x.lower() in (prompt or "").lower():
        return prompt
    return (prompt or "").strip().rstrip(" .") + ". " + x
