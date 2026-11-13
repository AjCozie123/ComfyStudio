# -*- coding: utf-8 -*-
"""Prompt Enhancer helpers: Fast/Quality settings, JSON extract, disk cache, model pick."""
from __future__ import annotations

import hashlib, json, os, re, time

# Smaller Qwen text/VL models already on this PC, preferred first. Never downloads.
FAST_PE_CANDIDATES = [
    r"Qwen\qwen3-vl-4b-heretic_fp8_e4m3fn.safetensors",
    r"Qwen/qwen3-vl-4b-heretic_fp8_e4m3fn.safetensors",
    "qwen3vl_4b_int8_convrot.safetensors",
    r"Qwen\qwen3vl_4b_fp8_scaled.safetensors",
    r"Qwen\qwen3-vl-4b-heretic_fp8_visionbf16.safetensors",
    r"Qwen\qwen3vl_4b_bf16.safetensors",
    r"Qwen\qwen3-vl-4b-heretic.safetensors",
    r"Qwen/qwen3vl_4b_fp8_scaled.safetensors",
    r"Qwen/qwen3-vl-4b-heretic.safetensors",
]

QUALITY_PE_DEFAULT = r"qwen3.5_9b_w4a8_gptq.safetensors"

FAST = {
    "id": "fast",
    "max_length": 384,
    "thinking": False,
    "mtp": "off",
    "temperature": 0.7,
    "top_k": 20,
    "ref_megapixels": 0.5,
    "max_images": 4,
    "system_t2i": "pe_t2i_system_fast.txt",
    "task_t2i": "pe_t2i_task_fast.txt",
    "system_edit": "pe_edit_system_fast.txt",
    "task_edit": "pe_edit_task_fast.txt",
    # edits for models other than MiniMax H3 (Klein, Qwen Edit, Krea, ...): a plain instruction, no <Subject N>
    "system_edit_generic": "pe_edit_system_fast.txt",
    "task_edit_generic": "pe_edit_task_generic.txt",
    "temperature_edit": 0.5,      # edits want literal, steady wording
}

QUALITY = {
    "id": "quality",
    "max_length_t2i": 8192,
    "max_length_edit": 6144,
    "thinking": True,
    "mtp": "auto",
    "temperature": 1.0,
    "top_k": 20,
    # what the enhancer LOOKS at (not what the image model gets). 8 MP was ~8,000 vision tokens per picture; H3's own
    # text encoder looks at references at a 1024 px short edge, so ~1 MP shows the enhancer the same detail.
    "ref_megapixels": 1.0,
    "max_images": 16,
    "system_t2i": "pe_t2i_system.txt",
    "task_t2i": "pe_t2i_task.txt",
    "system_edit": "pe_edit_system.txt",
    "task_edit": "pe_edit_task.txt",
    "system_edit_generic": "pe_edit_system_fast.txt",
    "task_edit_generic": "pe_edit_task_generic.txt",
    "temperature_edit": 0.7,
}


def settings(pe_mode):
    mode = (pe_mode or "fast").strip().lower()
    return QUALITY if mode == "quality" else FAST


def _norm(name):
    return str(name or "").replace("\\", "/").lower()


def pick_fast_clip(available):
    """Return (name, found_smaller) from installed CLIPLoader names. No downloads."""
    have = list(available or [])
    index = {_norm(n): n for n in have}
    for cand in FAST_PE_CANDIDATES:
        if _norm(cand) in index:
            return index[_norm(cand)], True
        # suffix match (folder prefix may differ)
        suffix = _norm(cand).split("/")[-1]
        for n in have:
            if _norm(n).split("/")[-1] == suffix:
                return n, True
    return None, False


def pe_clip_for(pe_mode, available, quality_name):
    if (pe_mode or "fast").strip().lower() != "quality":
        name, found = pick_fast_clip(available)
        if found and name:
            return name, True
    return quality_name or QUALITY_PE_DEFAULT, False


def extract_expanded_caption(text):
    """Return expanded_caption from the first valid JSON object, or None.

    Mirrors ComfyUI JsonExtractString (first object containing the key, including
    objects buried in markdown) but returns None instead of an empty string so
    the caller can fall back to the user's raw prompt.
    """
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
        value = data.get("expanded_caption")
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return None


def history_preview_text(entry, node_id="pe_preview"):
    rec = ((entry or {}).get("outputs") or {}).get(node_id) or {}
    t = rec.get("text")
    if isinstance(t, (list, tuple)) and t:
        return str(t[0] if not isinstance(t[0], dict) else t[0].get("text") or t[0])
    if isinstance(t, str):
        return t
    # some wrappers
    for key in ("string", "strings", "result"):
        v = rec.get(key)
        if isinstance(v, (list, tuple)) and v:
            return str(v[0])
        if isinstance(v, str):
            return v
    return ""


def _cache_path(data_dir):
    return os.path.join(data_dir, "pe_cache")


def cache_key(payload):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_get(data_dir, key):
    path = os.path.join(_cache_path(data_dir), key + ".json")
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    text = (rec.get("text") or "").strip()
    return text or None


def cache_put(data_dir, key, text, meta=None):
    folder = _cache_path(data_dir)
    os.makedirs(folder, exist_ok=True)
    rec = {"text": text, "t": time.time(), "meta": meta or {}}
    tmp = os.path.join(folder, key + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, key + ".json"))
    _cache_prune(folder, keep=80)


def _cache_prune(folder, keep=80):
    try:
        files = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return
    if len(files) <= keep:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for p in files[: max(0, len(files) - keep)]:
        try:
            os.remove(p)
        except OSError:
            pass


def image_fingerprint(input_root, rel_name):
    rel = str(rel_name or "").replace("/", os.sep)
    full = os.path.abspath(os.path.join(input_root, rel))
    try:
        st = os.stat(full)
        return {"name": str(rel_name).replace("\\", "/"), "size": int(st.st_size), "mtime": int(st.st_mtime)}
    except OSError:
        return {"name": str(rel_name).replace("\\", "/")}


# ------------------------------------------------------------------ the token dial (Settings -> Enhance)
TOKEN_STEPS = [256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192]
# Quality "thinks" before it writes, and the limit covers the thinking AND the answer. Below these limits it
# writes directly instead, so the thinking can never use up the room and leave no prompt.
# (The edit limit used to be 512: thinking switched on with no room to finish, so Quality edits came back empty.)
THINK_MIN = {"t2i": 4096, "edit": 4096}


def auto_tokens(pe_mode, mode, prompt="", n_images=0, generic=False):
    """The token limit Auto picks for what the user is doing. It is a ceiling: the enhancer stops when it is done,
    so a roomy limit costs nothing, while a tight one cuts the answer off (no JSON -> original prompt used)."""
    words = len(str(prompt or "").split())
    edit = mode == "edit"
    if edit:
        if generic:                      # a 2-5 sentence instruction
            return 768
        if (pe_mode or "fast") != "quality":
            return 1536 if n_images > 2 else 1024     # the Subject caption is ~350 words
        return 8192 if n_images > 2 else 6144         # Quality thinks first, then writes the caption
    if (pe_mode or "fast") != "quality":
        return 768      # the sectioned caption is up to ~250 words (~350 tokens); 384 could cut it off
    return 4096


def resolve_tokens(pe_mode, mode, choice=None, prompt="", n_images=0, generic=False):
    """(max_length, thinking) for a run. choice: None / 'auto' / a number of tokens from the dial.
    generic: an edit for a model other than MiniMax H3 (short instruction, never thinks)."""
    st = settings(pe_mode)
    mode = "edit" if mode == "edit" else "t2i"
    try:
        n = int(choice)
    except (TypeError, ValueError):
        n = 0
    max_length = (min(TOKEN_STEPS, key=lambda s: abs(s - n)) if n > 0
                  else auto_tokens(st["id"], mode, prompt, n_images, generic=generic))
    thinking = bool(st.get("thinking")) and not (generic and mode == "edit") and max_length >= THINK_MIN[mode]
    return max_length, thinking
