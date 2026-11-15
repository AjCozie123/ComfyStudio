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
    # turning the person / which side we see: its own instructions and its own Krea 2 Edit setting, so a turn is never
    # written as "facing the camera" like an ordinary pose
    ("turn", r"\b(turn\w* (?:her|him|them|the (?:woman|man|girl|guy|person))(?:self|selves)?\b(?=,? (?:to|so|around|left|right|"
             r"side|sideways|away|toward|towards|back|slightly|fully|completely|halfway|\d+|her|his|their)\b(?! (?:dress|top|shirt|"
             r"hair|hoodie|jacket|coat|skirt|pants|jeans|outfit|clothes|shoes|hat|cap|eyes|lips|skin|nails|bag)))|turn\w* around|"
             r"turn\w* (?:her|his|their) (?:head|face|body|shoulders|torso|hips|whole body)\b(?! (?:red|blue|green|pale|white|black))|"
             r"side[- ]?profile|in profile|profile view|(?:left|right)[- ]facing|from (?:behind|the back|the side)|back view|"
             r"rear view|side view|three[- ]quarters?(?: view| angle| profile)?|(?:facing|face|faces) (?:left|right|away|"
             r"the wall|sideways|backwards?)|look\w* over (?:her|his|their) shoulder|(?:back|side) (?:to|towards) the camera|"
             r"rotat\w*|spin\w* (?:her|him|them|around))\b"),
    ("pose", r"\b(sit|sits|sitting|sat|stand|stands|standing|lie|lies|lying|lay|kneel\w*|squat\w*|lean\w*|pose|posing|"
             r"facing|face (?:left|right|the camera)|look\w* (?:at|up|down|away|over)|raise\w*|wave\w*|"
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
               "turn": "Turn / view", "background": "Background or lighting", "add": "Add something", "text": "Text",
               "general": "General"}


_FRAMING = ("Same framing and camera distance: the person stays the same size and in the same place in the picture; "
            "do not zoom in, crop or move closer.")
_OTHERS = ("Every other person and object in the picture stays exactly as it is, in the same place and size; no one is "
           "removed or added.")
_DROP_FROM_KEEP = {"pose": ("standing pose", "body pose", "pose", "arms", "hands", "hand"), "body": ("body shape", "build"),
                  "turn": ("standing pose", "body pose", "pose", "orientation", "direction")}


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
    if not caption or route not in ("pose", "turn", "body", "background"):
        return caption
    out = caption
    words = _DROP_FROM_KEEP.get(route)
    if words:
        parts = re.split(r"(?<=[.!?])\s+", out)
        out = " ".join(_drop_keep_items(p, words) if re.match(r"(?i)\s*(keep|preserve)\b", p) else p for p in parts)
    if not re.search(r"(?i)camera distance|same size", out):
        out = out.rstrip() + " " + _FRAMING
    # shared by every edit that moves or reshapes a person: the people around them don't drift or vanish
    if route in ("pose", "turn", "body") and not re.search(r"(?i)every other person", out):
        out = out.rstrip() + " " + _OTHERS
    return out


# ---------------------------------------------------------------- masks, helmets and costumes that enclose the head
# The model keeps a bun / ponytail / afro sticking out of an Iron Man helmet unless told plainly that the hair is gone,
# and a "keep her hair / face" sentence (enhancer or guard) tells it the opposite. So when the instruction puts a head-
# enclosing mask or helmet on someone, the hair (and, for full-face ones, the face) leaves every keep sentence and one
# plain sentence says where the hair went. Eye masks, masquerade, surgical and sheet masks leave the hair alone.
_FULL_FACE_CHARS = (r"iron[- ]?man|war[- ]?machine|spider[- ]?(?:man|woman|gwen)|spiderman|deadpool|venom|carnage|"
                    r"black[- ]?panther|storm[- ]?trooper|darth[- ]?vader|boba[- ]?fett|mandalorian|daft[- ]?punk|"
                    r"power[- ]?rangers?|master[- ]?chief|ultron|ghost[- ]?rider|ant[- ]?man|moon[- ]?knight|"
                    r"crimson[- ]?dynamo|daredevil|scorpion|sub[- ]?zero|ninja|gimp|slipknot|kamen[- ]?rider|ultraman|"
                    r"tokusatsu|sentai|halo spartan|robocop")
_HEAD_CHARS = r"batman|batgirl|bat[- ]?woman|captain[- ]?america|astronaut|spacesuit|space[- ]?suit|knight|gladiator"
_OPEN_MASK = re.compile(r"(?i)\b(eye|domino|masquerade|venetian|mardi[- ]gras|lace|half|surgical|medical|face[- ]?(?:mask|covering)"
                        r"|sheet|sleep(?:ing)?|gas|hockey|clay|mud|facial|beauty|skincare|zorro|robin|catwoman|lone ranger)"
                        r"[- ]+(?:\w+[- ]+)?masks?\b|\bmasks? over (?:her|his|their|the) (?:eyes|mouth|nose)\b")
_COVER_WORD = re.compile(r"(?i)\b(full[- ]?face (?:mask|helmet)|helmets?|masks?|cowls?|balaclavas?|ski[- ]?masks?|"
                         r"visors?|swim(?:ming)? caps?|bathing caps?|bald caps?)\b")
_COVER_NEG = re.compile(r"(?i)\b(remove|removes|removing|take off|takes off|taking off|without|no|lift(?:s|ing)? (?:up )?|"
                        r"pull(?:s|ing)? (?:off|up)|unmask\w*|less)\s+(?:(?:her|his|their|the|a|an|its|that)\s+)?"
                        r"(?:\w+[- ]+){0,3}(?:helmet|mask|cowl|balaclava|visor|cap)s?\b")


def head_cover(prompt):
    """('full' | 'head', what to call it, her/his/their) when the instruction puts on a mask, helmet or costume that
    encloses the head (full = the face is covered too), else None."""
    t = re.split(r"(?i)\b(?:do\s*not|don'?t|never)\s*:?\s*(?:change|edit|remove|replace|touch|alter|modify|move|delete)\b",
                 str(prompt or ""))[0]
    if not t or _COVER_NEG.search(t):
        return None
    low = t.lower()
    pron = "her" if re.search(r"\b(she|her|woman|women|girl|lady|female)\b", low) else \
        "his" if re.search(r"\b(he|his|him|man|men|boy|guy|male)\b", low) else "their"
    word = None
    for m in _COVER_WORD.finditer(t):
        w = m.group(1).lower()
        if "mask" in w and _OPEN_MASK.search(t[max(0, m.start() - 40):m.end() + 30]):
            continue
        word = re.sub(r"s$", "", w.replace("full-face", "full-face").split()[-1]) if "full" not in w else w
        break
    full_char = re.search(r"(?i)\b(?:%s)\b" % _FULL_FACE_CHARS, t)
    head_char = re.search(r"(?i)\b(?:%s)\b" % _HEAD_CHARS, t)
    if not word and not full_char and not head_char:
        return None
    if word in ("swim cap", "cap", "bathing cap", "bald cap", "visor"):
        kind = "head"
    elif full_char or word in ("mask", "balaclava", "ski-mask", "ski mask") or (word and "full" in word):
        kind = "full"
    elif head_char or word in ("helmet", "cowl"):
        kind = "head"
    else:
        kind = "full"
    if word in ("balaclava",):
        kind = "head"   # the eyes and part of the face show
    item = word or ("helmet" if re.search(r"(?i)iron|war[- ]?machine|storm|vader|fett|mandalorian|daft|ranger|chief|"
                                          r"ultron|kamen|robocop|astronaut|space|knight|gladiator|captain", t) else "mask")
    if word == "helmet" and full_char:
        kind = "full"
    return kind, item, pron


_HAIR_WORDS = ("hairstyle", "hair colour", "hair color", "hairline", "hair", "bun", "ponytail", "braids")
_FACE_WORDS = ("facial features", "facial identity", "face", "identity", "expression", "makeup", "eyes", "smile")


def head_polish(prompt, caption):
    """See head_cover: take the hair (and a covered face) out of every keep sentence, and say plainly that the hair is
    hidden inside the mask / helmet. Safe to run twice."""
    hc = head_cover(prompt)
    if not hc or not caption:
        return caption
    kind, item, pron = hc
    words = _HAIR_WORDS + (_FACE_WORDS if kind == "full" else ())
    parts = re.split(r"(?<=[.!?])\s+", caption)
    out = " ".join(_drop_keep_items(p, words) if re.match(r"(?i)\s*(keep|preserve)\b", p) else p for p in parts)
    out = re.sub(r"(?i)\bkeep (?:the person's|her|his|their)\s*,\s*", "Keep %s " % pron, out)   # "Keep her , body" -> tidy
    out = re.sub(r"\s+,", ",", out)
    if "none of %s hair" % pron in out.lower():
        return out
    P = pron.capitalize()
    face = (" and face" if kind == "full" else "")
    line = ("The %s covers %s whole head%s, so none of %s hair shows anywhere: all of it is hidden inside, with no "
            "bun, ponytail, braids, curls, locs, fringe or loose strands sticking out at the back, top, sides or "
            "below it. The back of the %s is a smooth, closed shell sized for a head with the hair tucked away."
            % (item, pron, face, pron, item))
    if kind == "full":
        line += " No skin, hair or beard shows around the %s's edges." % item
    else:
        line += " %s face shows in the opening, unchanged." % P
    # said early as well (edit models weigh the start most): long hair must not hang below it either
    early = ("None of %s hair hangs below the %s: %s neck, shoulders, back and chest are clear of hair."
             % (pron, item, pron))
    return _second_sentence(out, early).rstrip() + " " + line


def _second_sentence(text, sentence):
    """Put a short sentence right after the first one."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] + " " + sentence + (" " + parts[1] if len(parts) > 1 else "")


# ---------------------------------------------------------------- full costumes replace the whole outfit
# "Change her outfit to an Iron Man suit": the enhancer tends to keep "all other clothing" or "the brown boots' lacing",
# so the old shoes / jeans survive under a full suit. A full-body costume replaces everything worn, feet and hands too.
_FULL_COSTUME = re.compile(
    r"(?i)\b(?:(?:%s|%s|superhero|super[- ]hero|villain|mascot|power armou?r|mech)\b[\w' -]{0,25}?\b(?:suit|costume|armou?r|outfit|"
    r"uniform|gear|cosplay)|(?:full[- ]?body|head[- ]?to[- ]?toe|full)\s+(?:suit|costume|armou?r|outfit|bodysuit|catsuit)|"
    r"(?:space|hazmat|diving|wet|ski|bee[- ]?keeper|racing|flight|mascot|animal|bear|bunny|dinosaur|inflatable)[- ]?suit|"
    r"spacesuit|wetsuit|catsuit|bodysuit|onesie|jumpsuit|mascot|suit of armou?r|plate armou?r|knight'?s armou?r|"
    r"(?:%s|%s)\b)" % (_FULL_FACE_CHARS, _HEAD_CHARS, _FULL_FACE_CHARS, _HEAD_CHARS))
_OUTFIT_WORDS = re.compile(
    r"(?i)\b(cloth\w*|outfit|garments?|wardrobe|attire|sweaters?|jumpers?|jeans|trousers|pants|leggings|shorts|skirts?|"
    r"dress(?:es)?|shirts?|t-shirts?|tops?|hoodies?|jackets?|coats?|blazers?|vests?|bikini|bra|underwear|lingerie|socks?|"
    r"stockings|tights|shoes?|boots?|sneakers?|trainers?|heels|sandals?|slippers?|footwear|laces?|lacing|soles?|gloves?|"
    r"mittens?|belts?|scarf|scarves|hats?|caps?|beanies?|uniform)\b")


def full_costume(prompt):
    """True when the instruction dresses someone in a full-body costume / suit / armour (not for 'take off ...')."""
    t = re.split(r"(?i)\b(?:do\s*not|don'?t|never)\s*:?\s*(?:change|edit|remove|replace|touch|alter|modify|move|delete)\b",
                 str(prompt or ""))[0]
    if not t or re.search(r"(?i)\b(remove|take off|takes off|taking off|without)\b[\w' -]{0,30}\b(suit|costume|armou?r)\b", t):
        return False
    return bool(_FULL_COSTUME.search(t))


def _keep_items_without(sentence, rx):
    """'Keep her body shape, pose, the yellow sweater's cut and the brown boots exactly the same.' minus the items that
    match rx (the sentence goes when nothing is left)."""
    m = re.match(r"(?is)(\s*(?:keep|preserve)\s+)(.*?)(\s+(?:exactly\s+)?(?:the\s+same|unchanged|as\s+(?:it|they)\s+(?:is|are|was|were))\b.*)$",
                 sentence)
    if not m:
        return sentence
    head, body, tail = m.groups()
    kept, changed = [], False
    for item in (x.strip() for x in re.split(r",\s*(?:and\s+)?", body) if x.strip()):
        subs = [s.strip() for s in re.split(r"\s+and\s+", item) if s.strip()]
        if not rx.search(item):
            kept.append(item)
        elif rx.search(subs[0]):
            changed = True        # "the yellow sweater's cut and fit": all of it is about the old clothes
        else:
            changed = True        # "pose and the brown boots": keep "pose"
            kept += [s for s in subs if not rx.search(s)]
    if not changed:
        return sentence
    if not kept:
        return ""
    lst = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + (", " if " and " in kept[-1] else " and ") + kept[-1]
    return head + lst + tail


def costume_polish(prompt, caption):
    """See full_costume: drop every garment / shoe from the keep sentences, and say the costume covers head to toe with
    its own boots and gloves. Safe to run twice."""
    if not caption or not full_costume(prompt):
        return caption
    hc = head_cover(prompt)
    pron = hc[2] if hc else ("her" if re.search(r"(?i)\b(she|her|woman|girl|lady)\b", prompt or "") else
                             "his" if re.search(r"(?i)\b(he|his|him|man|boy|guy)\b", prompt or "") else "their")
    parts = re.split(r"(?<=[.!?])\s+", caption)
    out = " ".join(x for x in (_keep_items_without(p, _OUTFIT_WORDS) if re.match(r"(?i)\s*(keep|preserve)\b", p) else p
                               for p in parts) if x)
    if "from head to toe" in out.lower():
        return out
    subj = {"her": "she", "his": "he"}.get(pron, "they")
    out = _second_sentence(out, "%s shoes are replaced too: %s feet are in the costume's own boots, not %s old shoes."
                           % (pron.capitalize(), pron, pron))
    return (out.rstrip() + " The costume replaces everything %s %s wearing, from head to toe: none of %s old clothes, "
            "shoes or socks show anywhere. %s feet are in the costume's own matching boots and %s hands in its own "
            "matching gloves, in the same style, colours and material as the suit."
            % (subj, "were" if subj == "they" else "was", pron, pron.capitalize(), pron))


_MOVE_ASK = re.compile(r"(?i)\b(step\w*|mov(?:e|es|ed|ing)|walk\w*|closer|further away|farther|forwards?|"
                       r"back(?:wards)? (?:a bit|a step|up)|to the (?:left|right) (?:side )?of the (?:picture|frame|room))\b")


_CROP_ASK = re.compile(r"(?i)\b(full[- ]?body|head to toe|whole body|zoom\w*|wider shot|wide shot|show (?:her|his|their) "
                       r"(?:legs|feet|full)|from (?:far|further)|pull(?:ed)? back)\b")


_AWAY_ASK = re.compile(r"(?i)\b(?:(?:further|farther|more) (?:back|away)(?: from (?:the )?camera)?|step\w* back|"
                       r"move\w* (?:her|him|them) back|walk\w* away|away from (?:the )?camera|into the (?:background|distance))\b")
_CLOSER_ASK = re.compile(r"(?i)\b(?:closer(?: to (?:the )?camera)?|step\w* (?:forward|towards?)|move\w* (?:her|him|them) "
                         r"(?:forward|closer)|walk\w* (?:towards?|up to) (?:the )?camera|into the foreground)\b")


def move_polish(prompt, caption):
    """'Move her further back from the camera' / 'step closer': the person moves to a realistic new distance (smaller /
    larger in the picture, same floor, natural perspective, same proportions). Safe to run twice."""
    if not caption or "realistic distance" in caption.lower():
        return caption
    t = str(prompt or "")
    pron = _pron_of(t)
    subj = {"her": "She", "his": "He"}.get(pron, "They")
    if _AWAY_ASK.search(t):
        line = ("%s stands further back from the camera at a realistic distance: smaller in the picture, standing on the "
                "same floor with natural perspective and the same body proportions, more of the room visible around %s."
                % (subj, {"her": "her", "his": "him"}.get(pron, "them")))
    elif _CLOSER_ASK.search(t):
        line = ("%s stands closer to the camera at a realistic distance: larger in the picture, on the same floor with "
                "natural perspective and the same body proportions." % subj)
    else:
        return caption
    return caption.rstrip() + " " + line


def _pron_of(prompt):
    low = str(prompt or "").lower()
    return "her" if re.search(r"\b(she|her|woman|women|girl|lady|female)\b", low) else \
        "his" if re.search(r"\b(he|his|him|man|men|boy|guy|male)\b", low) else "their"


def wants_new_framing(prompt):
    """The instruction moves the person or changes the crop ('step back', 'full body', 'zoom out')."""
    t = str(prompt or "")
    return bool(_MOVE_ASK.search(t) or _CROP_ASK.search(t) or _AWAY_ASK.search(t) or _CLOSER_ASK.search(t))


def turn_polish(prompt, caption):
    """A turn of someone in an existing picture: they turn in place (same spot, same size) unless the instruction asks
    to move them, and keep the very same clothes seen from the new side. Said plainly whatever the enhancer wrote, and
    with Enhance off. Safe to run twice."""
    if not caption or route_edit(prompt) != "turn" or "turns in place" in caption.lower():
        return caption
    pron = _pron_of(prompt)
    subj = {"her": "She", "his": "He"}.get(pron, "They")
    lines = []
    if not _MOVE_ASK.search(str(prompt or "")) and not _CROP_ASK.search(str(prompt or "")):
        # said as the CROP, never as feet / legs: naming a body part the photo cuts off makes the model zoom out to show it
        lines.append("%s turns in place and the picture keeps exactly the same crop: the same parts of %s body are in view "
                     "and cut off at the same place, %s head at the same height, the same size and position in the picture. "
                     "Nothing that was outside the picture comes into view." % (subj, pron, pron))
    lines.append("%s clothes are the very same garments seen from the new side, with the same colours, fabric, pattern, "
                 "fit and coverage, continuing naturally around the body; nothing is added or removed." % pron.capitalize())
    # the looser turn setting lets bystanders drift or vanish: only the named person turns
    if not re.search(r"(?i)every other person", caption):
        lines.append("Only %s turns: every other person and object in the picture stays exactly as it is, in the same place "
                     "and size, and no one is removed or added." % {"her": "she", "his": "he"}.get(pron, "they"))
    return caption.rstrip() + " " + " ".join(lines)


# words that mean the body takes a new pose / does something (not just 'standing' or 'facing'), so a turn that comes
# with them is a pose edit, not a turn in place
_POSE_ACTION = re.compile(r"\b(bent|bend\w*|kneel\w*|sit|sits|sitting|sat|seated|squat\w*|crouch\w*|lie|lies|lying|"
                          r"lay(?:s|ing)?|lean\w*|legs?|arms?|hands?|fingers?|reach\w*|grip\w*|grab\w*|grasp\w*|hold\w*|"
                          r"spread\w*|rais\w*|lift\w*|jump\w*|walk\w*|run(?:s|ning)?|danc\w*|pos(?:e|es|ing)|arch\w*|"
                          r"stretch\w*|twerk\w*|bounc\w*|on (?:all fours|her knees|his knees|their knees)|touch\w*|"
                          r"pull\w*|push\w*|cross\w*|fold\w*|wav\w*|point\w*|hug\w*|kiss\w*)\b", re.I)


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
    if "turn" in hits:
        if _POSE_ACTION.search(t):
            # a turn PLUS a pose ("turn her back to the camera, bent over, reaching back"): the whole body moves, so it is
            # a pose edit (every detail kept, no 'turn in place / same crop' lines that would forbid the pose)
            hits = [h for h in hits if h != "turn"]
            if "pose" not in hits:
                hits.append("pose")
        elif "pose" in hits:
            # "turn her standing side profile left": the turn is the edit; 'standing', 'facing', 'look' are part of it
            hits.remove("pose")
    if "remove" in hits and "replace" in hits:
        hits.remove("replace")          # taking a layer off: the remove rules cover what is underneath
    if "recolour" in hits and "remove" in hits:
        hits.remove("recolour")
    if "text" in hits and "add" in hits:
        hits.remove("add")             # "add the word SALE": a text edit
    return hits[0] if len(hits) == 1 else "general"
