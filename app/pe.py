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


# ------------------------------------------------------------------ which focused edit instructions fit the prompt
# Order matters: the first family whose words appear (and no other family's) wins; mixed or unclear -> "general".
_ROUTE_RULES = [
    ("text", r"'[^']+'|\"[^\"]+\"|\b(text|sign|writing|words?|caption|label|letters?|logo says|say|says|reads?|write\w*|wrote|written|spell\w*|print\w* .{1,30}\bon)\b"),
    ("pose", r"\b(sit|sits|sitting|sat|stand|stands|standing|lie|lies|lying|lay|kneel\w*|squat\w*|lean\w*|pose|posing|"
             r"turn\w*|facing|face (?:left|right|the camera)|profile|look\w* (?:at|up|down|away|over)|raise\w*|wave\w*|"
             r"walk(?:s|ing|ed)?|run(?:s|ning)?|jump(?:s|ing|ed)?|cross\w* (?:his|her|their) (?:arms|legs)|hands? on)\b"),
    ("body", r"\b(fat\w*|heav(?:y|ier)|thin(?:ner)?|skinn\w*|slim\w*|muscular|muscles?|bulk\w*|chubb\w*|plump\w*|curv\w*|"
             r"taller|shorter|weight|body shape|build|belly|bigger body|smaller body|older|younger)\b"),
    ("background", r"\b(background|backdrop|scene|setting|location|sky|sunset|sunrise|night\w*|daytime|weather|rain\w*|snow\w*|"
                   r"lighting|light(?:s|ing)? up|golden hour|season|beach|city|forest|indoors|outdoors|studio)\b"),
    ("remove", r"\b(remove\w*|take off|takes off|taking off|delete|erase|get rid of|no longer)\b"),
    ("replace", r"\b(replace\w*|swap\w* .{1,40}\b(?:for|with)\b|instead of|change\w* .{1,40}\b(?:to|into) (?:a|an)\b|"
                r"turn\w* .{1,40}\binto (?:a|an)\b|(?:is|now) wearing (?:a|an))\b"),
    ("recolour", r"\b(recolou?r\w*|colou?r\w*|dye\w*|(?:make|change|turn)\w* .{0,40}\b(?:red|blue|green|yellow|black|white|"
                 r"pink|purple|orange|brown|gr[ae]y|gold\w*|silver|beige|navy|maroon|teal)\b|leather|denim|silk|velvet|wool)\b"),
    ("add", r"\b(add\w*|put\w* (?:a|an|some)|place\w* (?:a|an)|give\w* (?:him|her|them) (?:a|an)|holding (?:a|an))\b"),
]
ROUTE_LABEL = {"replace": "Replace", "remove": "Remove", "recolour": "Colour or material", "body": "Body shape", "pose": "Pose",
               "background": "Background or lighting", "add": "Add something", "text": "Text", "general": "General"}


_FRAMING = ("Same framing and camera distance: the person stays the same size and in the same place in the picture; "
            "do not zoom in, crop or move closer.")
_DROP_FROM_KEEP = {"pose": ("standing pose", "body pose", "pose", "arms", "hands", "hand"), "body": ("body shape", "build")}


def _drop_keep_items(sentence, words):
    for w in words:
        w = re.escape(w)
        sentence = re.sub(r"(?i)\b%s\s+and\s+" % w, "", sentence)        # "pose and expression" -> "expression"
        sentence = re.sub(r"(?i),\s*%s\b(?=\s*(?:,|and\b))" % w, "", sentence)  # ", pose," -> ","
        sentence = re.sub(r"(?i)\b%s,\s*" % w, "", sentence)             # "pose, hair" -> "hair"
        sentence = re.sub(r"(?i)\s+and\s+%s\b" % w, "", sentence)         # "hair and pose" -> "hair"
    return sentence


def route_polish(route, caption):
    """Rules the small enhancer model does not reliably follow, made certain in code: a pose or body edit must not
    also ask to keep the pose / body shape, and edits that tend to recompose the shot get an explicit framing line."""
    if not caption or route not in ("pose", "body", "background"):
        return caption
    out = caption
    words = _DROP_FROM_KEEP.get(route)
    if words:
        parts = re.split(r"(?<=[.!?])\s+", out)
        out = " ".join(_drop_keep_items(p, words) if re.match(r"(?i)\s*(keep|preserve)\b", p) else p for p in parts)
    if not re.search(r"(?i)camera distance|same size", out):
        out = out.rstrip() + " " + _FRAMING
    return out


def route_edit(prompt):
    """The focused edit instructions for this prompt (a key of ROUTE_LABEL). Only the edit part counts (a
    'Do not change ...' part is protection, not the edit)."""
    t = re.split(r"(?i)\b(?:do\s*not|don'?t|never)\s+(?:change|edit|remove|replace|touch|alter|modify|move|delete)\b", str(prompt or ""))[0]
    t = t.lower()
    hits = [k for k, rx in _ROUTE_RULES if re.search(rx, t)]
    if not hits:
        return "general"
    # "remove X ... he is wearing Y" / "replace X with a Y-coloured Z": a replace that names a colour is still a replace
    if "background" in hits and "replace" in hits:
        hits.remove("replace")          # "change the background to a beach": a background edit
    if "replace" in hits and "recolour" in hits:
        hits.remove("recolour")
    if "remove" in hits and "replace" in hits:
        hits.remove("replace")          # taking a layer off: the remove rules cover what is underneath
    if "recolour" in hits and "remove" in hits:
        hits.remove("recolour")
    if "text" in hits and "add" in hits:
        hits.remove("add")             # "add the word SALE": a text edit
    return hits[0] if len(hits) == 1 else "general"
