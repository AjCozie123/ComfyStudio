"""Rules-based Auto understanding for MiniMax H3 Image Edit prompts.

No extra VRAM, fully deterministic. Official image-slot tags are <Picture N>.
<Subject N> is not treated as a picture-slot token (see module docstring in
tests and the parent report).
"""
from __future__ import annotations

import re

import camera as CAMERA
import appearance as APPEARANCE

ROLES = ("auto", "subject", "clothing", "accessory", "object", "style", "background")
EDIT_KINDS = ("edit", "ref")

# Words that hint a slot's role, used only when the user left the chip on Auto.
_ROLE_WORDS = {
    "subject": (
        "person", "people", "man", "woman", "men", "women", "girl", "boy",
        "guy", "lady", "gentleman", "child", "kid", "baby", "face", "identity",
        "character", "subject", "her", "him", "she", "he", "they", "someone",
        "model", "portrait", "body", "figure",
    ),
    "clothing": (
        "clothing", "clothes", "outfit", "dress", "shirt", "pants", "trousers",
        "jeans", "skirt", "jacket", "coat", "hoodie", "jumper", "sweater",
        "top", "blouse", "suit", "uniform", "costume", "wear", "wearing",
        "garment", "shorts", "tracksuit", "lingerie", "bra", "underwear",
    ),
    "accessory": (
        "accessory", "accessories", "jewelry", "jewellery", "necklace", "earrings",
        "ring", "bracelet", "watch", "hat", "cap", "glasses", "sunglasses",
        "bag", "handbag", "purse", "shoes", "boots", "heels", "scarf", "tie",
        "belt", "crown", "tiara", "wig",
    ),
    "object": (
        "object", "item", "prop", "product", "car", "vehicle", "furniture",
        "chair", "table", "phone", "bottle", "cup", "gun", "weapon", "tool",
        "book", "logo", "sign", "box", "toy", "guitar", "flower", "bouquet",
    ),
    "style": (
        "style", "aesthetic", "look", "palette", "colour grading", "color grading",
        "lighting style", "in the style", "same style", "art style", "filter",
    ),
    "background": (
        "background", "backdrop", "scenery", "setting", "environment", "scene",
        "location", "room", "landscape", "studio backdrop",
    ),
}

_PRESERVE_MARKERS = (
    "keep the rest", "leave the rest", "unchanged", "do not change",
    "don't change", "dont change", "preserve", "exact face", "exact identity",
    "facial features", "hair style", "skin tone", "body shape", "body proportion",
    "match the lighting", "match lighting", "match the perspective",
    "keep his", "keep her", "keep the subject", "keep their",
    "same clothes", "same clothing", "exact clothes", "identity",
)

_INTENT_ORDER = (
    "compose_references",
    "clothing_transfer",
    "accessory_add",
    "style_transfer",
    "background_replace",
    "subject_swap",
    "subject_into_environment",
    "object_insert",
    "generic_multi",
    "edit_single",
)

_SLOT_RE = re.compile(
    r"(?:<picture\s*(\d+)>|@?(?:picture|image|pic)\s*(\d+))",
    re.I,
)


def picture_tag(n: int) -> str:
    return "<Picture %d>" % int(n)


def normalize_roles(raw, n_images: int, ref_mode: bool = False) -> list:
    """Return a role per slot (1-based list of length n_images)."""
    n = max(0, int(n_images or 0))
    src = []
    if isinstance(raw, dict):
        src = [raw.get(i) or raw.get(str(i)) for i in range(1, n + 1)]
    elif isinstance(raw, (list, tuple)):
        src = list(raw)
    roles = []
    for i in range(n):
        val = ""
        if i < len(src) and src[i] is not None:
            val = str(src[i]).strip().lower()
        if val in ("base", "edit", "focal", "canvas", "target"):
            val = "base"
        if val not in ROLES and val != "base":
            val = "auto"
        roles.append(val)
    if n and not ref_mode:
        # Picture 1 is always the base/focal image in Edit Picture 1 mode.
        if roles[0] in ("auto", "base"):
            roles[0] = "base"
        # Extra slots should not be 'base'
        for i in range(1, n):
            if roles[i] == "base":
                roles[i] = "auto"
    else:
        for i in range(n):
            if roles[i] == "base":
                roles[i] = "auto"
    return roles


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _norm_lower(text: str) -> str:
    return _norm(text).lower()


def already_has_preservation(prompt: str) -> bool:
    t = _norm_lower(prompt)
    hits = sum(1 for m in _PRESERVE_MARKERS if m in t)
    return hits >= 2


def _slot_windows(prompt: str, slot: int) -> list:
    """Text windows that talk about a given picture number."""
    text = prompt or ""
    n = int(slot)
    pat = re.compile(
        r"(?:<picture\s*%d>|@?(?:picture|image|pic)\s*%d)\b" % (n, n),
        re.I,
    )
    windows = []
    for m in pat.finditer(text):
        a = max(0, m.start() - 72)
        b = min(len(text), m.end() + 72)
        windows.append(text[a:b])
    return windows


def _role_score(text: str) -> dict:
    t = _norm_lower(text)
    # whole-word-ish match
    scores = {role: 0 for role in _ROLE_WORDS}
    for role, words in _ROLE_WORDS.items():
        for w in words:
            if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(w), t):
                scores[role] += 1
    return scores



_ENV_NOUN = r"(?:environment|background|backdrop|scenery|setting|location|room|interior|landscape|scene)"
_PIC_CAP = r"(?:<picture\s*(\d+)>|@?(?:picture|image|pic)\s*(\d+))"
_ENV_LINK_PATTERNS = [
    # use / using / treat picture N as the background|room|...
    re.compile(r"(?:use|using|treat)\s+" + _PIC_CAP + r"\s+as\s+(?:the\s+)?(?:" + _ENV_NOUN + r"|place)", re.I),
    # <Picture N> as the background
    re.compile(_PIC_CAP + r"\s+as\s+(?:the\s+)?(?:" + _ENV_NOUN + r"|place)", re.I),
    # in the <Picture 3> room
    re.compile(r"(?:in|into|inside)\s+the\s+" + _PIC_CAP + r"\s+" + _ENV_NOUN, re.I),
    # in <Picture 3>'s room
    re.compile(r"(?:in|into|inside)\s+" + _PIC_CAP + r"(?:'s|\u2019s)\s+" + _ENV_NOUN, re.I),
    # the background / room / setting in|from|of picture 2
    re.compile(r"(?:the\s+)?(?:" + _ENV_NOUN + r"|place)\s+(?:in|from|of)\s+" + _PIC_CAP, re.I),
    # into the environment of / from picture 2
    re.compile(r"(?:in|into|inside)\s+(?:the\s+)?(?:" + _ENV_NOUN + r"|place)\s+(?:of|from|in)\s+" + _PIC_CAP, re.I),
    # use image 2 as the background (image_url style already in PIC_CAP)
]


def _slot_from_match(m):
    for g in m.groups():
        if g:
            try:
                n = int(g)
            except (TypeError, ValueError):
                continue
            if n >= 1:
                return n
    return None


def detect_environment_slots(prompt: str, n_images: int) -> list:
    """Picture numbers explicitly linked to environment/background words.

    A mere mention of 'room' or 'scene' in a text-described setting does not count.
    """
    text = prompt or ""
    n = int(n_images or 0)
    found = []
    for pat in _ENV_LINK_PATTERNS:
        for m in pat.finditer(text):
            slot = _slot_from_match(m)
            if slot and slot <= n and slot not in found:
                found.append(slot)
    return found


def infer_roles(prompt: str, roles: list, ref_mode: bool = False) -> list:
    """Resolve Auto chips from the prompt. Locked (non-auto) chips stay as set."""
    n = len(roles or [])
    out = list(roles)
    text = prompt or ""
    # Per-slot mention scores
    mention_best = {}
    for i in range(n):
        if out[i] != "auto":
            continue
        if not ref_mode and i == 0:
            out[i] = "base"
            continue
        wins = _slot_windows(text, i + 1)
        if not wins:
            continue
        # Words immediately before "from/in Picture N" are the strongest cue
        # ("the woman from picture 2" vs trailing "wearing the dress...").
        attach = []
        pat = re.compile(
            r"([A-Za-z][A-Za-z\s]{0,40}?)\s+(?:from|in|on|of)\s+(?:<picture\s*%d>|@?(?:picture|image|pic)\s*%d)\b"
            % (i + 1, i + 1),
            re.I,
        )
        attach = pat.findall(text)
        scored_text = " ".join(attach) if attach else " ".join(wins)
        scores = _role_score(scored_text)
        best = max(scores, key=lambda r: scores[r])
        if scores[best] > 0:
            out[i] = best
            mention_best[i] = best

    # Global fallback for remaining Auto extras
    global_scores = _role_score(text)
    used = {r for r in out if r not in ("auto", "base")}

    def take(role, start=0):
        for i in range(start, n):
            if out[i] == "auto":
                out[i] = role
                used.add(role)
                return i
        return None

    if ref_mode:
        start = 0
    else:
        if n:
            out[0] = "base" if out[0] in ("auto", "base") else out[0]
        start = 1

    # In Reference mode an image is an environment only if the user tagged it
    # Background or the prompt explicitly links that slot to environment words.
    if ref_mode:
        for s in detect_environment_slots(text, n):
            idx = s - 1
            if 0 <= idx < n and roles[idx] in ("auto", "background"):
                # Prompt-linked environment wins over Auto inference, but not over a locked role chip.
                out[idx] = "background"
        used = {r for r in out if r not in ("auto", "base")}

    # If the prompt clearly talks about a person/outfit/etc and no slot holds that role yet, assign the first Auto extra.
    # Do not globally assign 'background' in Reference mode — that would treat a text-described
    # scene word (room, setting, place) as if it named a reference image.
    role_order = ("subject", "clothing", "accessory", "style", "object")
    if not ref_mode:
        role_order = ("subject", "clothing", "accessory", "background", "style", "object")
    for role in role_order:
        if role not in used and global_scores.get(role, 0) > 0:
            take(role, start)

    # Remaining Auto extras stay 'auto' (generic reference) — resolved to 'object' only for wording later.
    return out


def _has_any(text: str, phrases) -> bool:
    t = _norm_lower(text)
    return any(p in t for p in phrases)



_SHEET_PHRASES = (
    "character sheet", "character-sheet", "charactersheet",
    "turnaround", "turn-around", "turn around",
    "reference sheet", "reference-sheet", "model sheet", "model-sheet",
    "expression sheet", "expression-sheet",
    "multiple views", "multi-view", "multiview", "multi view",
    "front side back", "front, side, back", "front/side/back",
    "front view, side view", "views of the same",
    "concept sheet", "design sheet", "ortho sheet",
)

_SHEET_TEMPLATE = (
    "This is a new composition, not an edit or reshoot of any one input photograph. "
    "All input pictures are identity references for the same character; do not treat <Picture 1> "
    "as a canvas to crop, rotate or paste. Create a professional character-design sheet / turnaround "
    "of that person on a clean, even, neutral background. "
    "Keep the same character consistent in every panel: exact face and identity, facial features, "
    "hair style and colour, skin tone, body shape, proportions and figure, and the same outfit "
    "(colours, patterns, fabric and logos) unless the user asked otherwise. "
    "Lay out clearly separated panels on a wide sheet, including at least a front view, a 3-quarter view, "
    "a side view and a back view, plus optional close-up face or expression studies. "
    "Panel labels are optional and should stay subtle if used. No cropped-off limbs, and do not collage "
    "the original photographs into the sheet. Use every loaded <Picture N> only as a reference for who the person is."
)



# ---------------------------------------------------------------------------
# Aspect-aware multi-panel layouts (character sheets, expressions, etc.)
# Do not force a canvas size; adapt the panel grid to the chosen W x H.
# ---------------------------------------------------------------------------

NAMED_ASPECTS = (
    ("21:9", 21 / 9),
    ("16:9", 16 / 9),
    ("3:2", 3 / 2),
    ("4:3", 4 / 3),
    ("1:1", 1.0),
    ("3:4", 3 / 4),
    ("2:3", 2 / 3),
    ("9:16", 9 / 16),
    ("9:21", 9 / 21),
)

MULTIPANEL_INTENTS = frozenset({
    "character_sheet", "expression_sheet", "outfit_sheet",
    "product_multiview", "before_after", "comic_panels",
    "pose_sheet", "sprite_sheet", "photo_collage",
})

_BODY_VIEWS_4 = ("front view", "3/4 view", "side view", "back view")
_BODY_VIEWS_3 = ("front view", "side view", "back view")

_FULL_BODY_RULE = (
    "each panel showing the full body from head to toe with space above the head "
    "and below the feet, same scale and eye line across panels, evenly spaced, "
    "plain neutral background"
)


def _named_aspect(ratio: float):
    best, best_err = None, 9.0
    for name, val in NAMED_ASPECTS:
        err = abs(float(ratio) - val) / val
        if err < best_err:
            best, best_err = name, err
    return best if best_err <= 0.08 else None


def normalize_aspect(aspect=None, width=None, height=None, resolution=None):
    """Return (ratio, label) such as (0.75, "3:4"). Defaults to square 1:1."""
    w = h = None
    label = ""
    try:
        if width and height:
            w, h = float(width), float(height)
    except (TypeError, ValueError):
        w = h = None
    if (not w or not h) and aspect:
        s = str(aspect).strip().lower().replace(" ", "")
        m = re.match(r"^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$", s)
        if m:
            try:
                w, h = float(m.group(1)), float(m.group(2))
                label = "%s:%s" % (m.group(1), m.group(2))
            except ValueError:
                w = h = None
    if (not w or not h) and resolution:
        s = str(resolution).strip()
        m = re.search(r"(?i)(\d+)\s*x\s*(\d+)", s)
        if m:
            try:
                w, h = float(m.group(1)), float(m.group(2))
            except ValueError:
                w = h = None
        if (not w or not h):
            m = re.match(r"(?i)^custom:(\d+):(\d+):", s)
            if m:
                try:
                    w, h = float(m.group(1)), float(m.group(2))
                    label = "%s:%s" % (m.group(1), m.group(2))
                except ValueError:
                    w = h = None
    if not w or not h or w <= 0 or h <= 0:
        return 1.0, "1:1"
    ratio = float(w) / float(h)
    named = _named_aspect(ratio)
    return ratio, (named or label or ("%.3g:1" % ratio if ratio >= 1 else "1:%.3g" % (1 / ratio)))


def _view_count_from_prompt(prompt: str, default: int) -> int:
    t = _norm_lower(prompt or "")
    if not t:
        return default
    # Count named body views if the user listed them.
    names = []
    if _has_any(t, ("front view", "front, ", "front side", "front/side", "a front")) or re.search(r"\bfront\b", t):
        if _has_any(t, ("front view", "front side", "front/side", "front, side", "front, 3", "front 3")) or "front" in t:
            names.append("front")
    if _has_any(t, ("3/4", "3-quarter", "three-quarter", "three quarter", "3 quarter")):
        names.append("34")
    if re.search(r"\bside\b", t):
        names.append("side")
    if re.search(r"\bback\b", t):
        names.append("back")
    # Only trust this when the user is clearly listing views, not just saying "back to..."
    if "front" in names and ("side" in names or "back" in names) and len(names) >= 2:
        return max(len(set(names)), 2)
    m = re.search(r"\b(\d+)\s*(?:views?|panels?|shots?|frames?)\b", t)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 12:
            return n
    return default


def panel_layout(intent: str, aspect=None, width=None, height=None,
                 resolution=None, prompt: str = "") -> dict:
    """Choose a panel grid that fits the canvas so subjects aren't cropped or squashed."""
    ratio, label = normalize_aspect(aspect=aspect, width=width, height=height, resolution=resolution)
    intent = (intent or "").strip()
    t = _norm_lower(prompt or "")
    want_faces = _has_any(t, ("expression", "close-up", "close up", "face inset", "headshot", "facial"))

    if intent == "before_after":
        if ratio >= 0.95:
            ident, short, rows, cols, prompt_s = (
                "side_by_side", "side by side", 1, 2,
                "arranged as two panels side by side (before on the left, after on the right), "
                "same scale and alignment, evenly spaced, plain neutral background",
            )
        else:
            ident, short, rows, cols, prompt_s = (
                "stack_2", "vertical stack", 2, 1,
                "arranged as two stacked panels (before on top, after below), "
                "same scale and alignment, evenly spaced, plain neutral background",
            )
        return _layout_dict(ident, short, label, ratio, rows, cols, ("before", "after"), prompt_s)

    if intent == "expression_sheet":
        n = _view_count_from_prompt(prompt, 9 if ratio < 1.15 and ratio > 0.85 else (6 if ratio >= 1.15 else 6))
        if ratio >= 1.45:
            cols, rows = (6, 1) if n <= 6 else (4, 2)
            if n > 6 and n <= 8:
                cols, rows = 4, 2
            short = "%dx%d grid" % (cols, rows) if rows > 1 else "single row of expressions"
        elif ratio >= 0.9:
            cols, rows = 3, 3
            short = "3x3 grid"
        elif ratio >= 0.5:
            cols, rows = 2, 3
            short = "2x3 grid"
        else:
            cols, rows = 2, 4
            short = "2x4 grid"
        prompt_s = (
            "arranged in a %s of evenly spaced expression close-ups, "
            "same scale and eye line across panels, plain neutral background"
            % short
        )
        ident = "grid_%dx%d" % (cols, rows) if rows > 1 else "single_row"
        return _layout_dict(ident, short, label, ratio, rows, cols, tuple("expression %d" % i for i in range(1, cols * rows + 1)), prompt_s)

    if intent == "comic_panels":
        n = _view_count_from_prompt(prompt, 6 if ratio >= 1.45 else 4)
        if ratio >= 1.45:
            cols, rows = (3, 2) if n >= 5 else (2, 2)
        elif ratio >= 0.9:
            cols, rows = (2, 2) if n <= 4 else (2, 3)
        elif ratio >= 0.5:
            cols, rows = (2, 3) if n >= 5 else (2, 2)
        else:
            cols, rows = 1, min(max(n, 3), 4)
        short = "vertical stack" if cols == 1 else ("%dx%d grid" % (cols, rows))
        prompt_s = (
            "arranged in a %s of comic / storyboard panels, clearly separated, "
            "evenly spaced, with consistent characters across panels"
            % short
        )
        ident = "stack_%d" % rows if cols == 1 else "grid_%dx%d" % (cols, rows)
        return _layout_dict(ident, short, label, ratio, rows, cols, tuple("panel %d" % i for i in range(1, cols * rows + 1)), prompt_s)

    if intent == "pose_sheet":
        n = _view_count_from_prompt(prompt, 6 if ratio >= 1.15 else 4)
        if ratio >= 1.45:
            cols, rows = (n, 1) if n <= 5 else (min(n, 8) // 2 + min(n, 8) % 2, 2)
        elif ratio >= 0.9:
            cols, rows = (2, 2) if n <= 4 else (3, 2)
        else:
            cols, rows = (2, 2) if n <= 4 else (2, 3)
        short = "single row of poses" if rows == 1 else "%dx%d grid" % (cols, rows)
        prompt_s = (
            "arranged in a %s of distinct full-body poses, %s" % (short, _FULL_BODY_RULE)
        )
        ident = "row_%d" % cols if rows == 1 else "grid_%dx%d" % (cols, rows)
        return _layout_dict(ident, short, label, ratio, rows, cols, tuple("pose %d" % i for i in range(1, cols * rows + 1)), prompt_s)

    if intent == "sprite_sheet":
        n = _view_count_from_prompt(prompt, 8 if ratio >= 1.45 else 6)
        n = max(2, min(n, 16))
        if ratio >= 1.45:
            cols, rows = (n, 1) if n <= 8 else ((n + 1) // 2, 2)
        elif ratio >= 0.9:
            cols = 4 if n > 6 else 3
            rows = (n + cols - 1) // cols
        else:
            cols = 2
            rows = (n + 1) // 2
        short = "single row of %d frames" % cols if rows == 1 else "%dx%d grid of frames" % (cols, rows)
        prompt_s = (
            "arranged as a game sprite sheet: a %s, each frame the same size with the character "
            "at the same scale and baseline, evenly spaced on a flat plain background, frames reading left to right "
            "as one smooth motion cycle" % short
        )
        ident = "row_%d" % cols if rows == 1 else "grid_%dx%d" % (cols, rows)
        return _layout_dict(ident, short, label, ratio, rows, cols, tuple("frame %d" % i for i in range(1, cols * rows + 1)), prompt_s)

    if intent == "photo_collage":
        short = "editorial collage"
        prompt_s = (
            "arranged as a tidy editorial photo collage that fills the whole canvas, with a clear focal "
            "picture and smaller supporting pictures, consistent colour grading and even spacing or a "
            "neat layered scrapbook look"
        )
        return _layout_dict("collage", short, label, ratio, 0, 0, ("collage",), prompt_s)

    # Full-body multi-view: character sheet, outfit/turnaround, product multi-view
    default_n = 4
    n = _view_count_from_prompt(prompt, default_n)
    if n == 3:
        views = _BODY_VIEWS_3
    elif n <= 2:
        views = _BODY_VIEWS_4[:2]
        n = 2
    else:
        views = _BODY_VIEWS_4
        n = 4
    if intent == "product_multiview":
        views = tuple(v.replace("view", "view of the product") for v in views)

    if ratio >= 1.45:
        ident, short, rows, cols = "row_plus_faces", "single row + face close-ups", 1, n
        prompt_s = (
            "arranged in a single row of full-body views left to right (%s), "
            "with optional face close-ups in a smaller row beneath, %s"
            % (", ".join(views), _FULL_BODY_RULE)
        )
    elif n <= 3 and ratio < 0.5:
        ident, short, rows, cols = "stack_%d" % n, "vertical stack of %d" % n, n, 1
        prompt_s = (
            "arranged in a vertical stack of %d views (%s), %s"
            % (n, ", ".join(views), _FULL_BODY_RULE)
        )
    elif n == 3 and ratio < 1.45:
        ident, short, rows, cols = "grid_2x2_face_inset", "3 full-body + face inset", 2, 2
        prompt_s = (
            "arranged as three full-body views (%s) plus a face close-up inset in a 2x2 grid, %s"
            % (", ".join(views), _FULL_BODY_RULE)
        )
    elif ratio >= 0.9 or n >= 4:
        # Square, 4:3, portrait, and tall-with-4-views: 2x2 keeps each panel portrait-capable
        if ratio >= 0.9 and ratio < 1.15 and want_faces and n >= 3:
            ident, short, rows, cols = "grid_2x2_face_inset", "3 full-body + face inset", 2, 2
            prompt_s = (
                "arranged as three full-body views plus a face close-up inset in a 2x2 grid, %s"
                % _FULL_BODY_RULE
            )
        else:
            ident, short, rows, cols = "grid_2x2", "2x2 grid", 2, 2
            prompt_s = "arranged in a 2x2 grid (%s), %s" % (", ".join(views), _FULL_BODY_RULE)
    else:
        ident, short, rows, cols = "stack_%d" % min(n, 3), "vertical stack", min(n, 3), 1
        prompt_s = (
            "arranged in a vertical stack of %d views (%s), %s"
            % (min(n, 3), ", ".join(views[:min(n, 3)]), _FULL_BODY_RULE)
        )
    return _layout_dict(ident, short, label, ratio, rows, cols, views, prompt_s)


def _layout_dict(ident, short, label, ratio, rows, cols, views, prompt_s):
    hint = "%s for %s" % (short, label)
    return {
        "id": ident,
        "short": short,
        "hint": hint,
        "aspect": label,
        "ratio": round(float(ratio), 4),
        "rows": int(rows),
        "cols": int(cols),
        "views": list(views),
        "prompt": prompt_s.rstrip(".") + ".",
    }


def is_before_after_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, (
        "before and after", "before/after", "before & after", "before-and-after",
        "before after comparison", "split before after", "a before and after",
    ))


def is_comic_panel_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, (
        "comic panel", "comic panels", "comic page", "comic strip",
        "manga panel", "manga page", "storyboard panel", "storyboard panels",
        "panel comic", "multi-panel comic", "multipanel comic",
    ))


def is_product_multiview_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    if _has_any(t, (
        "product sheet", "product turnaround", "product multi-view", "product multiview",
        "product views", "packshot views", "pack shot views", "360 of the product",
        "product from all sides", "multi-view of the product",
    )):
        return True
    if "product" in t and _has_any(t, ("multi-view", "multiview", "turnaround", "all sides", "front side back")):
        return True
    return False


def is_expression_sheet_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    if _has_any(t, ("expression sheet", "expressions sheet", "emotion sheet", "emotions sheet")):
        return True
    if "sheet" in t and _has_any(t, ("expressions", "emotions")) and not _has_any(t, ("character sheet", "turnaround", "model sheet")):
        return True
    return False


def is_outfit_sheet_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, ("outfit sheet", "outfit turnaround", "clothing sheet", "wardrobe sheet"))



def is_character_sheet_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    if not t:
        return False
    if _has_any(t, _SHEET_PHRASES):
        return True
    # "sheet" plus a view/character cue, to catch terse phrasing
    if "sheet" in t and _has_any(t, ("character", "turnaround", "reference", "model", "expression", "views", "view")):
        return True
    return False


def is_pose_sheet_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, (
        "pose sheet", "poses sheet", "pose reference sheet", "different poses", "multiple poses",
        "various poses", "pose variations", "a sheet of poses", "pose study", "pose studies",
    ))


def is_sprite_sheet_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, (
        "sprite sheet", "spritesheet", "sprite-sheet", "character sprite", "game sprite",
        "pixel sprite", "animation frames", "walk cycle", "run cycle", "idle animation frames",
    ))


def is_collage_request(prompt: str) -> bool:
    t = _norm_lower(prompt or "")
    return _has_any(t, (
        "collage", "mood board", "moodboard", "scrapbook", "photo grid of", "photo dump",
    ))


# Single-image tasks (the kinds of edits Grok Imagine offers as templates).
_BG_REMOVE = (
    "remove the background", "remove background", "transparent background", "no background",
    "white background", "plain background", "solid background", "cut out the", "cutout of",
    "cut-out of", "isolate the", "background removed", "studio white backdrop",
)
_BG_CHANGE = (
    "change the background", "replace the background", "new background", "swap the background",
    "different background", "background to ", "background into ", "background with ",
)
_OUTPAINT = (
    "zoom out", "outpaint", "uncrop", "extend the image", "extend the photo", "extend the picture",
    "extend the canvas", "extend the scene", "extend the frame", "expand the image", "expand the photo",
    "expand the canvas", "widen the shot", "wider shot", "show more of the scene", "smart resize",
    "fill the new frame", "make it wider", "make it taller",
)
_RELIGHT = (
    "relight", "re-light", "change the lighting", "new lighting", "make it night", "make it nighttime",
    "turn it into night", "make it golden hour", "golden hour lighting", "make it sunset", "sunset lighting",
    "make it daytime", "make it day time", "change the time of day", "time of day to", "blue hour",
    "studio lighting", "cinematic lighting", "moody lighting", "neon lighting",
)
_RESTORE = (
    "restore", "colorize", "colourise", "colorise", "colourize", "old photo", "repair the photo",
    "fix the photo", "remove scratches", "remove the scratches", "upscale", "enhance the quality",
    "improve the quality", "make it sharper", "sharpen", "denoise", "remove noise", "unblur", "deblur",
)
_HEADSHOT = (
    "headshot", "head shot", "profile picture", "profile photo", "profile pic", "pfp", "linkedin photo",
    "linkedin picture", "passport photo", "id photo", "corporate portrait", "business portrait",
)
_PRODUCT = (
    "product photo", "product shot", "product image", "e-commerce", "ecommerce", "packshot", "pack shot",
    "listing photo", "listing image", "amazon listing", "catalog photo", "catalogue photo", "hero product",
    "product on a white", "studio product",
)
_RECOLOR = (
    "change the color", "change the colour", "change its color", "change its colour", "recolor", "recolour",
    "in a different color", "in a different colour", "color to ", "colour to ", "make it a different color",
    "make it a different colour",
)
_RESTYLE = (
    "anime style", "as an anime", "into anime", "cartoon style", "as a cartoon", "into a cartoon",
    "watercolor", "watercolour", "oil painting", "pencil sketch", "charcoal sketch", "line art",
    "comic book style", "3d render", "3d animated", "claymation", "clay style", "pixel art", "low poly",
    "vector illustration", "flat illustration", "ukiyo-e", "pop art", "art nouveau", "cyberpunk style",
    "vintage photo", "film photo", "polaroid", "in the style of", "as a painting", "reimagine",
)
_STICKER = (
    "sticker", "emoji", "app icon", "an icon of", "icon of", "mascot", "chibi", "cartoon avatar",
    "bobblehead", "figurine", "action figure", "funko", "plush toy",
)
_GROUP = (
    "group photo", "group shot", "group picture", "together in one", "together in the same",
    "in the same photo", "in the same picture", "both of them", "all of them", "the two of them",
    "the three of them", "side by side", "all together", "together in a",
)
_REMOVE_VERBS = ("remove the ", "erase the ", "delete the ", "get rid of the ", "take out the ", "remove that ", "remove all the ")


def _remove_target(t: str) -> str:
    for v in _REMOVE_VERBS:
        i = t.find(v)
        if i >= 0:
            rest = t[i + len(v):]
            return re.split(r"[.,;!?]| and | from | in | on | behind ", rest, 1)[0].strip()
    return ""


def single_task_intent(prompt: str, n_images: int) -> str:
    """Grok-style single-picture tasks. Returns an intent name or ''."""
    t = _norm_lower(prompt or "")
    if not t:
        return ""
    if n_images >= 2 and _has_any(t, _GROUP):
        return "group_photo"
    if _has_any(t, _BG_REMOVE):
        return "background_remove"
    if n_images <= 1 and _has_any(t, _BG_CHANGE):
        return "background_change"
    if _has_any(t, _OUTPAINT):
        return "outpaint"
    if _has_any(t, _HEADSHOT):
        return "headshot"
    if _has_any(t, _PRODUCT):
        return "product_shot"
    if _has_any(t, _STICKER):
        return "stylize_asset"
    if _has_any(t, _RESTORE):
        return "restore"
    if _has_any(t, _RELIGHT):
        return "relight"
    if n_images <= 1 and _has_any(t, _RESTYLE):
        return "restyle"
    if _has_any(t, _RECOLOR):
        return "recolor"
    target = _remove_target(t)
    if target and "background" not in target:
        # Clothing targets are left to the plain edit wording.
        words = set(re.findall(r"[a-z]+", target))
        if not words & set(_ROLE_WORDS["clothing"]):
            return "remove_object"
    return ""


def character_sheet_appendix(n_images: int, layout=None, appearances=None) -> str:
    layout_sentence = (layout or {}).get("prompt") or (
        "arranged in clearly separated panels, each panel showing the full body from head to toe "
        "with space above the head and below the feet, same scale and eye line across panels, "
        "evenly spaced, plain neutral background"
    )
    outfit = APPEARANCE.character_outfit_sentence(appearances)
    keep = (
        "Keep the same character consistent in every panel: exact face and identity, facial features, "
        "hair style and colour, skin tone, body shape, proportions and figure"
    )
    keep = (keep + ", " + outfit) if outfit else (keep + ". ")
    body = (
        "This is a new composition, not an edit or reshoot of any one input photograph. "
        "All input pictures are identity references for the same character; do not treat <Picture 1> "
        "as a canvas to crop, rotate or paste. Create a professional character-design sheet / turnaround "
        "of that person on a clean, even, neutral background. "
        + keep +
        "Lay out the sheet " + layout_sentence + " "
        "Panel labels are optional and should stay subtle if used. No cropped-off limbs, and do not collage "
        "the original photographs into the sheet. Use every loaded <Picture N> only as a reference for who the person is."
    )
    if n_images <= 0:
        return body
    tags = ", ".join(picture_tag(i) for i in range(1, int(n_images) + 1))
    if n_images == 1:
        extra = " %s is the identity reference for this character." % picture_tag(1)
    else:
        extra = " %s are identity references for the same character." % tags
    return body + extra


def classify_intent(prompt: str, roles: list, ref_mode: bool = False) -> str:
    n = len(roles or [])
    if is_before_after_request(prompt):
        return "before_after"
    if is_comic_panel_request(prompt):
        return "comic_panels"
    if is_product_multiview_request(prompt):
        return "product_multiview"
    if is_expression_sheet_request(prompt) and not _has_any(_norm_lower(prompt or ""), ("character sheet", "turnaround", "model sheet", "reference sheet")):
        return "expression_sheet"
    if is_outfit_sheet_request(prompt) and not is_character_sheet_request(prompt):
        return "outfit_sheet"
    if is_character_sheet_request(prompt):
        return "character_sheet"
    if is_sprite_sheet_request(prompt):
        return "sprite_sheet"
    if is_pose_sheet_request(prompt):
        return "pose_sheet"
    if is_collage_request(prompt) and n >= 2:
        return "photo_collage"
    if ref_mode:
        return "compose_references"
    task = single_task_intent(prompt, n)
    if task:
        return task
    if n <= 1:
        return "edit_single"
    t = _norm_lower(prompt)
    resolved = infer_roles(prompt, roles, ref_mode=False)
    role_set = set(resolved[1:])  # extras

    if _has_any(t, (
        "wear the", "wearing the", "put on the", "dress in", "dressed in",
        "change his clothes", "change her clothes", "change their clothes",
        "change the outfit", "change his outfit", "change her outfit",
        "in that outfit", "in this outfit", "the dress from", "the shirt from",
        "the clothes from", "the outfit from", "the jacket from",
    )) or ("clothing" in role_set and _has_any(t, ("wear", "wearing", "outfit", "clothes", "dress", "put on"))):
        return "clothing_transfer"
    if _has_any(t, (
        "wear the hat", "add the hat", "add the glasses", "put the necklace",
        "add her jewelry", "add the accessory", "wear the glasses",
    )) or ("accessory" in role_set and _has_any(t, ("add", "wear", "put on", "with the"))):
        return "accessory_add"
    if _has_any(t, (
        "in the style of", "same style as", "in this style", "match the style",
        "art style of", "like the style",
    )) or ("style" in role_set and _has_any(t, ("style", "aesthetic", "look like"))):
        return "style_transfer"
    if _has_any(t, (
        "change the background", "replace the background", "new background",
        "swap the background", "different background", "background from",
    )) or ("background" in role_set and _has_any(t, ("background", "backdrop", "replace the scene"))):
        return "background_replace"
    if _has_any(t, (
        "replace him with", "replace her with", "replace the person",
        "replace the man", "replace the woman", "swap him", "swap her",
        "use the face", "use the identity", "with the person from",
        "replace the subject",
    )):
        return "subject_swap"
    if _has_any(t, (
        "into this", "into the scene", "into the photo", "into the picture",
        "into the room", "in this scene", "in this photo", "in this room",
        "in this environment", "place him", "place her", "place them",
        "put him", "put her", "put them", "add him", "add her",
        "add the person", "add the man", "add the woman", "add the girl",
        "insert him", "insert her", "stand in", "sitting in this",
        "into <picture 1>", "in <picture 1>",
    )) or (
        resolved[0] == "base" and "subject" in role_set and _has_any(t, (
            "put", "place", "add", "insert", "into", "in this", "in the",
        ))
    ):
        return "subject_into_environment"
    if _has_any(t, (
        "put the", "place the", "add the", "insert the", "into the scene",
    )) and ("object" in role_set or global_objectish(t)):
        return "object_insert"
    return "generic_multi"


def global_objectish(t: str) -> bool:
    return _has_any(t, _ROLE_WORDS["object"])


def _ref_list(resolved: list, ref_mode: bool) -> list:
    lines = []
    start = 0 if ref_mode else 1
    for i, role in enumerate(resolved[start:], start=start + 1):
        label = role if role not in ("auto", "base") else "reference"
        lines.append((i, label))
    return lines


def _subject_preserve(tag: str, attrs=None) -> str:
    return APPEARANCE.clause_for(tag, attrs)


def _integration(base: str) -> str:
    return (
        "Match the lighting, perspective, scale and shadows of %s so the subject sits naturally in the scene. "
        "Keep the rest of %s unchanged except for the requested edit."
    ) % (base, base)


def _role_line(n: int, role: str, appearances=None) -> str:
    tag = picture_tag(n)
    if role == "subject":
        return _subject_preserve(tag, APPEARANCE.slot_attrs(appearances, n))
    if role == "clothing":
        return "Use the exact garment in %s — same cut, colours, patterns, fabric and logos." % tag
    if role == "accessory":
        return "Use the exact accessory in %s, matching its design, colours and materials." % tag
    if role == "object":
        return "Use the exact object in %s, keeping its design, colours, materials and logos." % tag
    if role == "style":
        return "Use %s only as a style and look reference (palette, lighting, rendering), not as a second scene to copy wholesale." % tag
    if role == "background":
        return "Use the environment in %s as the background, keeping its layout, lighting and distinctive details." % tag
    return "Use %s as a reference and preserve the exact design and details of what is taken from it." % tag


def _multipanel_appendix(intent: str, n: int, layout=None, appearances=None) -> str:
    sentence = (layout or {}).get("prompt") or (
        "arranged in clearly separated panels, evenly spaced, plain neutral background"
    )
    if intent == "character_sheet" or intent == "outfit_sheet":
        return character_sheet_appendix(n, layout=layout, appearances=appearances)
    if intent == "expression_sheet":
        return (
            "This is a new composition, not an edit or reshoot of any one input photograph. "
            "All input pictures are identity references for the same person. "
            "Create an expression sheet of that person, %s "
            "Keep identity consistent in every panel. Do not collage the original photographs."
            % sentence
        )
    if intent == "product_multiview":
        return (
            "This is a new composition, not an edit of <Picture 1>. "
            "All input pictures are references for the same product. "
            "Create a clean multi-view product sheet, %s "
            "Keep the product identical in every panel (shape, colours, logos and details). "
            "Do not collage the original photographs."
            % sentence
        )
    if intent == "before_after":
        return (
            "This is a new composition, not a crop of one input. "
            "Create a clear before-and-after comparison, %s "
            "Both panels must show the same subject at the same scale."
            % sentence
        )
    if intent == "comic_panels":
        return (
            "This is a new composition. Create a multi-panel comic / storyboard page, %s "
            "Keep characters consistent across panels. Do not collage the original photographs as the panels."
            % sentence
        )
    if intent == "pose_sheet":
        return (
            "This is a new composition, not an edit or reshoot of any one input photograph. "
            "All input pictures are identity references for the same person. Create a pose reference sheet, %s "
            "Every pose is clearly different (standing, walking, sitting, turning, reaching and so on) and natural, "
            "with correct anatomy and weight. Keep the exact same face, hair, skin tone, body shape and proportions "
            "in every pose. Do not collage the original photographs."
            % sentence
        )
    if intent == "sprite_sheet":
        return (
            "This is a new composition. Create a 2D game sprite sheet of the character from the references, %s "
            "Keep the design, colours and proportions identical in every frame; only the pose changes between frames. "
            "Crisp clean edges, no motion blur, no text. Do not collage the original photographs."
            % sentence
        )
    if intent == "photo_collage":
        tags = ", ".join(picture_tag(i) for i in range(1, max(1, int(n)) + 1))
        return (
            "Create a photo collage, %s Use %s as the pictures in the collage, "
            "keeping each picture's content recognisable and not distorted; crop for composition only, "
            "never stretch. Keep people's faces and identities exactly as in their pictures."
            % (sentence, tags)
        )
    return sentence


def _appendix_for(intent: str, resolved: list, ref_mode: bool, skip_preserve: bool, layout=None, appearances=None) -> str:
    parts = []
    n = len(resolved)
    base = picture_tag(1) if n else "<Picture 1>"
    extras = _ref_list(resolved, ref_mode=ref_mode)

    if intent in MULTIPANEL_INTENTS:
        return _multipanel_appendix(intent, n, layout=layout, appearances=appearances)

    if intent == "compose_references" or ref_mode:
        parts.append(
            "All input pictures are references only; there is no base image to edit."
        )
        env_slots = [i for i, role in extras if role == "background"]
        if not env_slots:
            # extras may start at 1; roles are 1-based in extras already
            env_slots = [i for i, role in enumerate(resolved, 1) if role == "background"]
        if env_slots:
            env = picture_tag(env_slots[0])
            parts.append(
                "Place all other referenced items and subjects into the environment from %s, "
                "matching its layout, lighting and perspective." % env
            )
            if len(env_slots) > 1:
                others = ", ".join(picture_tag(i) for i in env_slots[1:])
                parts.append("Also draw environment cues from %s." % others)
        else:
            parts.append(
                "Every picture is an item or subject reference; the environment and scene come from the text prompt, not from a reference image."
            )
        parts.append("Preserve each referenced item's exact design and details.")
        for i, role in extras:
            if env_slots and i == env_slots[0] and role == "background":
                continue  # already stated as the environment
            parts.append(_role_line(i, role, appearances))
        return " ".join(parts)

    if intent == "edit_single":
        if skip_preserve:
            return ""
        return "Keep the rest of %s unchanged except for the requested edit." % base

    if intent == "subject_into_environment":
        parts.append("Use %s as the scene to edit." % base)
        # Prefer an explicit/inferred subject slot, else Picture 2
        subj = next((i for i, r in extras if r == "subject"), extras[0][0] if extras else 2)
        if not skip_preserve:
            parts.append(_subject_preserve(picture_tag(subj), APPEARANCE.slot_attrs(appearances, subj)))
            parts.append(_integration(base))
        for i, role in extras:
            if i == subj:
                continue
            parts.append(_role_line(i, role, appearances))
        return " ".join(parts)

    if intent == "subject_swap":
        subj = next((i for i, r in extras if r == "subject"), extras[0][0] if extras else 2)
        parts.append("Replace the person in %s with the person in %s." % (base, picture_tag(subj)))
        if not skip_preserve:
            parts.append(_subject_preserve(picture_tag(subj), APPEARANCE.slot_attrs(appearances, subj)))
            parts.append(_integration(base))
        return " ".join(parts)

    if intent == "clothing_transfer":
        cloth = next((i for i, r in extras if r == "clothing"), extras[0][0] if extras else 2)
        parts.append("Dress the person in %s in the exact clothing from %s." % (base, picture_tag(cloth)))
        parts.append(_role_line(cloth, "clothing"))
        if not skip_preserve:
            parts.append("Keep the person's identity, face, hair and body in %s, and keep the rest of %s unchanged." % (base, base))
        for i, role in extras:
            if i == cloth:
                continue
            parts.append(_role_line(i, role, appearances))
        return " ".join(parts)

    if intent == "accessory_add":
        acc = next((i for i, r in extras if r == "accessory"), extras[0][0] if extras else 2)
        parts.append("Add the accessory from %s onto the subject in %s." % (picture_tag(acc), base))
        parts.append(_role_line(acc, "accessory"))
        if not skip_preserve:
            parts.append("Keep the rest of %s unchanged." % base)
        return " ".join(parts)

    if intent == "style_transfer":
        st = next((i for i, r in extras if r == "style"), extras[0][0] if extras else 2)
        parts.append(_role_line(st, "style"))
        if not skip_preserve:
            parts.append("Keep the subject's identity and the content of %s, changing only the look." % base)
        return " ".join(parts)

    if intent == "background_replace":
        bg = next((i for i, r in extras if r == "background"), extras[0][0] if extras else 2)
        parts.append("Replace the background of %s with the environment in %s." % (base, picture_tag(bg)))
        if not skip_preserve:
            parts.append(APPEARANCE.keep_subject_fragment(base, APPEARANCE.slot_attrs(appearances, 1)) + ", and match lighting and scale so they sit naturally in the new environment.")
        return " ".join(parts)

    if intent == "object_insert":
        obj = next((i for i, r in extras if r == "object"), extras[0][0] if extras else 2)
        parts.append("Place the object from %s into the scene in %s." % (picture_tag(obj), base))
        parts.append(_role_line(obj, "object"))
        if not skip_preserve:
            parts.append("Match lighting, perspective, scale and shadows of %s, and keep the rest of %s unchanged." % (base, base))
        return " ".join(parts)

    subj_keep = APPEARANCE.keep_subject_fragment(base, APPEARANCE.slot_attrs(appearances, 1))

    if intent == "group_photo":
        tags = [picture_tag(i) for i in range(1, n + 1)]
        parts.append(
            "Combine the people from %s into one natural photograph together, as if taken with one camera "
            "at the same moment: same lighting, colour temperature, perspective and believable relative scale."
            % (", ".join(tags[:-1]) + " and " + tags[-1] if len(tags) > 1 else tags[0])
        )
        if not skip_preserve:
            for i in range(1, n + 1):
                parts.append(_subject_preserve(picture_tag(i), APPEARANCE.slot_attrs(appearances, i)))
        parts.append("Each person appears exactly once; no duplicated or merged people.")
        return " ".join(parts)

    if intent == "background_remove":
        parts.append(
            "Isolate the main subject of %s on a clean, plain, evenly lit background (pure white unless the prompt "
            "names another colour), with crisp natural edges around hair and fine details and a soft contact shadow only."
            % base
        )
        if not skip_preserve:
            parts.append(subj_keep + "; do not change the subject's pose, crop or proportions.")
        return " ".join(parts)

    if intent == "background_change":
        parts.append("Replace only the background of %s with the setting described in the prompt." % base)
        if not skip_preserve:
            parts.append(subj_keep + ", and match lighting direction, colour and scale so they sit naturally in the new setting.")
        return " ".join(parts)

    if intent == "outpaint":
        parts.append(
            "Extend %s beyond its original edges to fill the new frame: keep every original pixel's content "
            "exactly where it is, at the same scale, and continue the scene seamlessly outward with matching "
            "perspective, lighting, texture and grain. Do not stretch, squash or re-crop the original content."
            % base
        )
        return " ".join(parts)

    if intent == "relight":
        parts.append(
            "Change only the lighting and time of day of %s as described: light direction, colour temperature, "
            "shadows, reflections and sky consistent with the new light." % base
        )
        if not skip_preserve:
            parts.append(subj_keep + "; keep the composition, layout and every object in place.")
        return " ".join(parts)

    if intent == "restore":
        parts.append(
            "Restore %s as a faithful high-quality photograph: remove scratches, dust, noise, blur and compression "
            "artifacts, recover natural detail and, if the prompt asks for colour, add realistic period-appropriate colour. "
            "Do not add, remove or redesign anything." % base
        )
        if not skip_preserve:
            parts.append("Keep faces and identities exactly as they are, without beautifying or changing age or features.")
        return " ".join(parts)

    if intent == "headshot":
        parts.append(
            "Create a professional head-and-shoulders portrait of the person in %s: soft flattering studio light, "
            "clean softly blurred or neutral backdrop, natural confident expression, sharp focus on the eyes."
            % base
        )
        if not skip_preserve:
            parts.append(_subject_preserve(base, APPEARANCE.slot_attrs(appearances, 1)))
        return " ".join(parts)

    if intent == "product_shot":
        obj = picture_tag(next((i for i, r in extras if r == "object"), 1))
        parts.append(
            "Photograph the product from %s as a clean commercial product shot: centred, crisp focus, soft studio "
            "lighting with gentle reflections and a natural contact shadow, on a clean background or the setting the prompt names."
            % obj
        )
        parts.append(
            "Keep the product identical: exact shape, proportions, colours, materials, labels, printed text and logos, "
            "with no invented branding."
        )
        return " ".join(parts)

    if intent == "stylize_asset":
        parts.append(
            "Turn the main subject of %s into the requested asset (sticker, emoji, icon, mascot or figurine style): "
            "simplified clean shapes, bold readable silhouette, isolated on a plain background, keeping the subject's "
            "recognisable features, colours and distinctive details." % base
        )
        return " ".join(parts)

    if intent == "restyle":
        parts.append(
            "Re-render %s in the requested style while keeping the same composition, layout, poses and "
            "recognisable identity of every subject; change the rendering, not the content." % base
        )
        return " ".join(parts)

    if intent == "recolor":
        parts.append(
            "Change only the colour of the item the prompt names in %s; keep its shape, material, texture, "
            "shading, logos and printed details, and keep everything else in %s unchanged." % (base, base)
        )
        return " ".join(parts)

    if intent == "remove_object":
        parts.append(
            "Remove only what the prompt names from %s and fill that area naturally with what would be behind it, "
            "matching texture, lighting and perspective, with no smudges or leftover outline." % base
        )
        if not skip_preserve:
            parts.append("Keep everything else in %s unchanged." % base)
        return " ".join(parts)

    # generic_multi
    parts.append("Use %s as the image to edit." % base)
    for i, role in extras:
        parts.append(_role_line(i, role, appearances))
    if not skip_preserve:
        parts.append("Keep the rest of %s unchanged except for the requested edit." % base)
    return " ".join(parts)


def build_assist_prompt(user_prompt: str, image_roles=None, n_images: int = 0,
                        ref_mode: bool = False, enabled: bool = True,
                        camera=None, mode: str = "",
                        aspect=None, width=None, height=None, resolution=None,
                        appearances=None) -> dict:
    """Return a structured result. If disabled or nothing to add, prompt == user prompt.

    `camera` is an optional {category: option_id} map. Camera notes are appended
    after Auto understanding, for Edit, Reference, or T2I.
    """
    user = (user_prompt or "").strip()
    n = int(n_images or 0)
    if isinstance(image_roles, list) and n == 0:
        n = len(image_roles)
    roles_in = normalize_roles(image_roles, n, ref_mode=ref_mode)
    resolved = infer_roles(user, roles_in, ref_mode=ref_mode) if n else []
    camera_mode = (mode or ("ref" if ref_mode else ("edit" if n else "t2i"))).strip().lower()
    if camera_mode in ("image-edit", "image_edit"):
        camera_mode = "edit"
    if camera_mode in ("reference", "ref_mode"):
        camera_mode = "ref"

    skipped = ""
    intent = "none"
    appendix = ""
    layout = None
    if not user:
        skipped = "empty"
        intent = "none"
    elif not enabled:
        skipped = "off"
        intent = "none" if n <= 0 else ("compose_references" if ref_mode else ("edit_single" if n <= 1 else "generic_multi"))
    elif n <= 0:
        skipped = "no_images"
        intent = "none"
    else:
        intent = classify_intent(user, roles_in, ref_mode=ref_mode)
        auto_ref = intent in MULTIPANEL_INTENTS
        layout = panel_layout(intent, aspect=aspect, width=width, height=height,
                              resolution=resolution, prompt=user) if auto_ref else None
        if auto_ref:
            ref_mode = True
            camera_mode = "ref" if camera_mode == "edit" else camera_mode
            resolved = infer_roles(user, roles_in, ref_mode=True) if n else resolved
        skip = already_has_preservation(user) and intent not in (("compose_references",) + tuple(MULTIPANEL_INTENTS))
        appendix = _norm(_appendix_for(intent, resolved, ref_mode=ref_mode, skip_preserve=skip, layout=layout, appearances=appearances))
        if appendix and appendix.lower() in user.lower():
            appendix = ""
            skip = True
        skipped = "already_present" if skip and not appendix else ""

    auto_ref = intent in MULTIPANEL_INTENTS
    if auto_ref and layout is None:
        layout = panel_layout(intent, aspect=aspect, width=width, height=height,
                              resolution=resolution, prompt=user)
    body = user if not appendix else (user.rstrip() + "\n\n" + appendix)
    cam = CAMERA.apply_camera(body, camera, mode=camera_mode)
    final = cam["prompt"]
    return {
        "enabled": bool(enabled) and n > 0 and bool(user),
        "prompt": final,
        "prompt_user": user,
        "appended": appendix,
        "intent": intent,
        "roles": resolved,
        "roles_in": roles_in,
        "ref_mode": bool(ref_mode) or auto_ref,
        "auto_ref": bool(auto_ref),
        "suggested_aspect": "",
        "suggested_mp": None,
        "suggested_resolution": "",
        "layout": layout,
        "layout_hint": (layout or {}).get("hint") or "",
        "skipped": skipped,
        "camera": cam["selections"],
        "camera_block": cam["appended"],
        "camera_chips": cam["chips"],
        "appearances": appearances or [],
    }


def pe_system_addon(assist_info: dict) -> str:
    """Extra PE system (and task-prefix) rules so enhancement keeps the same guarantees."""
    info = assist_info or {}
    if not info.get("enabled"):
        return ""
    roles = info.get("roles") or []
    intent = info.get("intent") or ""
    ref_mode = bool(info.get("ref_mode")) or intent in MULTIPANEL_INTENTS
    layout = info.get("layout") or {}
    lines = [
        "Auto-understanding constraints (honor these in expanded_caption; do not drop or weaken them):",
        "- Refer to input images with the official tags <Picture 1>, <Picture 2>, and so on. Those tags name image slots.",
        "- <Subject N> is not a picture-slot name. Only use <Subject N> if you first define it in natural language as an element grounded in a named <Picture N> (for example: 'The woman in <Picture 2> is <Subject 1>.').",
    ]
    if intent in MULTIPANEL_INTENTS:
        kind = intent.replace("_", " ")
        layout_bit = layout.get("prompt") or ""
        hint = layout.get("hint") or ""
        lines.append(
            "- The user asked for a %s. This is a new composition, not an edit of <Picture 1>, "
            "and must use the canvas aspect the user chose (do not change or ignore the width and height). "
            "Every input picture is a reference; do not rotate or collage the original photographs."
            % kind
        )
        if hint:
            lines.append("- Panel layout (adapt the subject so it is not cropped or squashed): %s." % hint)
        if layout_bit:
            lines.append("- Layout instruction to honor in the caption: %s" % layout_bit)
    if ref_mode:
        lines.append(
            "- Reference mode is on: every input picture is a reference only. There is no base image to edit. "
            "The user is describing a new scene to compose from the references. Do not treat <Picture 1> as a canvas that must be preserved wholesale."
        )
        env_slots = [i for i, role in enumerate(roles, 1) if role == "background"]
        if env_slots:
            lines.append(
                "- Environment reference: %s supplies the setting. Place all other referenced items and subjects into that environment, matching its layout, lighting and perspective."
                % ", ".join(picture_tag(i) for i in env_slots)
            )
        else:
            lines.append(
                "- No picture is an environment reference unless the user tagged it Background or the prompt explicitly links that picture to environment/background/scene/setting/location/place/room/backdrop words. Otherwise the environment comes from the text prompt."
            )
    else:
        lines.append(
            "- Edit mode: <Picture 1> is the base/focal image (the scene or target being edited). Pictures 2 and after are linked references."
        )
    if roles:
        bits = []
        for i, role in enumerate(roles, 1):
            label = "base/focal image" if (role == "base" and not ref_mode and i == 1) else (
                role if role not in ("auto", "base") else "reference"
            )
            bits.append("%s is the %s" % (picture_tag(i), label))
        lines.append("- Slot roles: " + "; ".join(bits) + ".")
    if intent:
        lines.append("- Classified intent: %s." % intent.replace("_", " ") + " Keep that intent.")
    lines.append(
        "- Never invent or describe attributes that are not visible in a referenced picture. "
        "If a subject is unclothed or only minimally clothed, do not mention clothing, fabric, patterns or logos; "
        "preserve body, skin, face and hair as shown instead. Mention accessories or tattoos only when they are visible."
    )
    appear = info.get("appearances")
    pe_slots = []
    for i, role in enumerate(roles, 1):
        attrs_i = APPEARANCE.slot_attrs(appear, i)
        if role == "subject" or (attrs_i and attrs_i.get("people_count", 0) >= 1 and role not in ("clothing", "accessory", "object", "style", "background")):
            pe_slots.append(i)
    if not pe_slots and appear:
        for i in range(1, 17):
            if APPEARANCE.slot_attrs(appear, i):
                pe_slots.append(i)
    for i in pe_slots:
        lines.append("- " + APPEARANCE.clause_for(picture_tag(i), APPEARANCE.slot_attrs(appear, i)))
    if not ref_mode:
        lines.append(
            "- Match lighting, perspective, scale and shadows of <Picture 1> when integrating references. "
            "Keep the rest of <Picture 1> unchanged except for the requested edit and its necessary consequences."
        )
    else:
        lines.append("- Preserve each referenced item's exact design and details in the new scene.")
    if info.get("appended"):
        lines.append("- The user's prompt already includes Auto-understanding notes; keep those guarantees in the expanded caption.")
    cam_note = CAMERA.pe_camera_addon(info.get("camera") or info.get("camera_selections"), mode=("ref" if ref_mode else "edit"))
    if cam_note:
        lines.append("- " + cam_note)
    return "\n".join(lines)


def pe_task_prefix(assist_info: dict) -> str:
    """Short prefix for the PE task prompt (especially Fast, which otherwise says Picture 1 is being edited)."""
    if not (assist_info or {}).get("enabled"):
        return ""
    if assist_info.get("ref_mode") or assist_info.get("intent") in MULTIPANEL_INTENTS:
        if assist_info.get("intent") in MULTIPANEL_INTENTS:
            layout = assist_info.get("layout") or {}
            hint = layout.get("hint") or ""
            sentence = layout.get("prompt") or ""
            kind = str(assist_info.get("intent") or "multi-panel").replace("_", " ")
            bits = [
                "%s override: all input pictures are references only; none is the base image to edit." % kind.capitalize(),
                "Compose a new multi-panel image on the user's chosen canvas (do not change the aspect ratio).",
            ]
            if hint:
                bits.append("Layout: %s." % hint)
            if sentence:
                bits.append(sentence)
            bits.append("Ignore any later line that says Picture 1 is the image being edited.")
            return " ".join(bits)
        return (
            "Reference mode override: all input pictures are references only; none is the base image to edit. "
            "The user describes a new scene. Compose the referenced people, clothes, objects and styles into that scene. "
            "Ignore any later line that says Picture 1 is the image being edited."
        )
    return (
        "Edit mode: <Picture 1> is the image being edited; later pictures are references. "
        "Honor the Auto-understanding constraints in the system prompt."
    )


def pe_task_addon(assist_info, **_kwargs):
    return pe_task_prefix(assist_info if isinstance(assist_info, dict) else {})
