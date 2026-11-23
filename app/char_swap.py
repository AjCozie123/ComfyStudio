"""Character swap: put the person from Picture 2 (3, 4) in place of a person in Picture 1.

Built on BFS Nodes' "MiniMax H3 Image character swap by color" workflow (alisson-anjos, LoRA v0.1):
  1. SAM 3.1 finds the people in Picture 1 (BFS Video Segment Picker, one frame).
  2. The people to replace are drawn over in a colour (red = Picture 2, blue = Picture 3, green = Picture 4):
     hatching lines (the trained default) or their 3D surface (normal map, keeps the original's figure).
  3. MiniMax H3 Image + the swap LoRA redraws each marked person as their reference, in the same pose and place.

This module has no ComfyUI or web code of its own: server.py passes in its `comfy()` helper. It covers
  * understanding a request ("swap her with picture 2", "put pic 2 in her place", "replace the guy on the left") -
    wants_swap(), body_mode(), position_words()
  * finding people (segment_graph() + people_from_history()) and choosing them (auto_pick())
  * building the job graph (build_graph()) and the prompt (swap_prompt()).
"""
from __future__ import annotations

import copy
import json
import re

CARD_ID = "h3_char_swap"
COLORS = ("red", "blue", "green")
MAX_PEOPLE = 3

# guide styles the user can pick (Manual). Keys are what the app sends; values are the picker node's options.
GUIDE_STYLES = {
    "hatching": "colored hatching + contour",              # the LoRA's trained default
    "shape": "normal map inside mask (needs normals)",     # the person's 3D surface: keeps the original figure
    "outline": "colored outline",
    "overlay": "translucent overlay",
}
GUIDE_LABELS = {
    "hatching": "Lines (recommended)",
    "shape": "Body shape (3D surface)",
    "outline": "Outline only",
    "overlay": "See-through tint",
}
# the conditioning node's prompt mode for each picker style
_PROMPT_MODE = {"hatching": "hatching", "shape": "normal map guide", "outline": "outline guide", "overlay": "translucent overlay guide"}

# what Auto uses, and the limits Manual may set (checked again on the server)
DEFAULTS = {
    "guide": "hatching", "body": "new", "mp": 2.0, "lora": 1.0, "steps": 6,
    "margin": 0.0, "feather": 0.01, "clean": 0.5, "hatch_width": 1.0, "hatch_spacing": 1.0,
    "threshold": 0.4, "concepts": "person, character",
}
LIMITS = {"mp": (0.5, 4.0), "lora": (0.0, 1.5), "steps": (4, 12), "margin": (0.0, 0.3), "feather": (0.0, 0.1),
          "clean": (0.0, 5.0), "hatch_width": (0.25, 4.0), "hatch_spacing": (0.25, 4.0), "threshold": (0.1, 0.9)}

# ------------------------------------------------------------------ understanding the request
_PIC = r"(?:<\s*picture\s*[2-9]\s*>|@?(?:picture|pic|image|img|photo|ref(?:erence)?)\s*#?\s*[2-9]|(?:second|2nd|third|3rd|fourth|4th|other|next|new) (?:picture|pic|image|photo|one)|the reference)"
_WHO = (r"(?:(?:the|this|that|her|his|their|my|our|your)\s+)?(?:(?:\w+\s+){0,3})?"
        r"(?:person|people|woman|women|man|men|girl|girls|guy|guys|lady|ladies|boy|boys|dude|chick|bloke|model|subject|"
        r"character|figure|individual|one|someone|somebody|her|him|them|she|he|they|me|myself|gf|bf|girlfriend|boyfriend|wife|husband)")
_SWAP_VERB = (r"(?:swap\w*|switch\w*|replac\w*|substitut\w*|exchang\w*|trad\w*|chang\w*|turn\w*|mak\w*|put\w*|stick\w*|"
              r"plac\w*|drop\w*|insert\w*|sub(?:bing|bed|s)?|recast\w*|recast|becom\w*)")
# head / face only, an outfit, or a person ADDED to the scene are other edits (they keep the body / the people)
_NOT_THIS = re.compile(r"\b(?:head|face|faces|heads)[- ]?(?:swap|transfer)|\bswap\w* (?:the |her |his |their )?(?:head|face)s?\b"
                       r"|\b(?:only|just) (?:the |her |his |their )?(?:head|face)\b"
                       r"|\b(?:outfit|clothes|clothing|dress|costume)[- ]?(?:swap|transfer|change)"
                       r"|\b(?:wear|wearing|dress(?:ed)? (?:her|him|them) in)\b", re.I)
_SWAP_RX = [
    re.compile(r"\b(?:character|person|people|body|full[- ]body|whole[- ]body|identity|charac)[- ]?swap\w*", re.I),
    re.compile(r"\b(?:swap|switch|exchange|trade|sub)\w*\s+(?:out\s+)?" + _WHO + r"\b.{0,40}?\b(?:with|for|by|to|into)\b", re.I),
    re.compile(r"\b(?:replac|substitut)\w*\s+" + _WHO + r"\b.{0,60}?\b(?:with|by|for)\b", re.I),
    re.compile(r"\b(?:put|stick|place|drop|insert|have|use|get)\w*\s+.{0,40}?\b(?:in|into|at)\s+(?:her|his|their|the|that)\s+"
               r"(?:place|spot|position|shoes|stead)\b", re.I),
    re.compile(r"\b(?:in place of|instead of|in (?:her|his|their) place|in (?:her|his|their) stead)\b", re.I),
    re.compile(r"\b(?:turn|make|change|transform|morph)\w*\s+" + _WHO + r"\b\s+(?:(?:into|to|be|look like)\s+)?(?:the\s+)?(?:" + _PIC
               + r"|(?:person|woman|man|girl|guy|one|character|lady)\s+(?:from|in|of)\s+" + _PIC + r")", re.I),
    re.compile(r"\b" + _WHO + r"\s+(?:becomes?|turns? into|is now|should be)\s+(?:the\s+)?(?:" + _PIC
               + r"|(?:person|woman|man|girl|guy|one|character|lady)\s+(?:from|in|of)\s+" + _PIC + r")", re.I),
    re.compile(r"\b" + _PIC + r"\s+(?:in|into|as)\s+(?:her|his|their|the)\s+(?:place|spot|position|role)\b", re.I),
    re.compile(r"\bswap\w*\s+(?:" + _PIC + r"|them|the two|both|these)\b.{0,30}\b(?:in|into|with)\b", re.I),
    re.compile(r"\bswap\w*\s+(?:her|him|them|it)\b", re.I),
]


def wants_swap(prompt: str, n_images: int) -> bool:
    """True when the words ask to replace a person in Picture 1 with someone from another picture (any wording:
    'swap her with picture 2', 'put pic 2 in her place', 'replace the guy on the left with the man in image 3',
    'turn her into picture 2', 'character swap', 'sub him out for the 2nd pic'). Head / face-only swaps, outfit
    transfers and adding a person to the scene are other edits, so they don't count."""
    if int(n_images or 0) < 2:
        return False
    t = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not t or _NOT_THIS.search(t):
        return False
    return any(rx.search(t) for rx in _SWAP_RX)


def wants_swap_words(prompt: str) -> bool:
    """Swap wording with only ONE picture attached (so the app can say: add the new person as Picture 2)."""
    return wants_swap(prompt, 2)


_KEEP_BODY = re.compile(r"\b(?:keep|same|preserve|maintain|retain|don'?t change|do not change|leave)\b.{0,30}\b"
                        r"(?:body|figure|shape|build|proportions?|curves|physique|silhouette|frame|size|height)\b"
                        r"|\b(?:her|his|their|the original|the old|original)\s+(?:body|figure|shape|build|proportions|physique)\b", re.I)


def body_mode(prompt: str) -> str:
    """'keep' when the words ask to keep the original person's body / figure (only the identity changes), else 'new'
    (the trained default: the new person's own body, face, hair and clothes)."""
    return "keep" if _KEEP_BODY.search(str(prompt or "")) else "new"


_POS = (("left", re.compile(r"\bleft(?:most|[- ]hand)?\b", re.I)), ("right", re.compile(r"\bright(?:most|[- ]hand)?\b", re.I)),
        ("middle", re.compile(r"\b(?:middle|cent(?:er|re)|central)\b", re.I)),
        ("front", re.compile(r"\b(?:front|closest|nearest|foreground)\b", re.I)),
        ("back", re.compile(r"\b(?:back|behind|furthest|farthest|background)\b", re.I)),
        ("biggest", re.compile(r"\b(?:biggest|largest|tallest|main)\b", re.I)),
        ("smallest", re.compile(r"\b(?:smallest|shortest|little|tiny)\b", re.I)))


def position_words(prompt: str) -> list:
    """Place words in order of appearance ('the woman on the left ... the man on the right' -> ['left', 'right'])."""
    found = []
    for name, rx in _POS:
        for m in rx.finditer(str(prompt or "")):
            found.append((m.start(), name))
    return [n for _i, n in sorted(found)]


# ------------------------------------------------------------------ finding and choosing people
def segment_graph(base_graph: dict, image: str, mp: float, opts: dict | None = None) -> dict:
    """The swap graph cut down to: load Picture 1, resize it exactly like the real job, find the people. Same sizes
    and detection settings as build_graph(), so the person ids (a1, a2 ...) match the real job's."""
    o = dict(DEFAULTS, **(opts or {}))
    g = copy.deepcopy(base_graph)
    keep = {"63", "56", "48", "84", "258", "276"}
    g = {k: v for k, v in g.items() if k in keep}
    g["63"]["inputs"]["image"] = image
    g["258"]["inputs"]["value"] = float(mp)
    p = g["276"]["inputs"]
    p["selection"] = json.dumps({"frames": {"0": {"1": {"selected": [], "added": [], "next": 1}}}, "active_frame": 0,
                                 "segment_only": True})
    p["concepts"] = o["concepts"]
    p["threshold"] = float(o["threshold"])
    p["mark_style"] = GUIDE_STYLES["hatching"]
    p.pop("normals", None)
    return g


def people_from_history(entry: dict) -> dict:
    """The picker's result in a finished /history entry -> {'width', 'height', 'image': {...}, 'people': [...]}.
    Each person: id, box [x, y, w, h] (0-1 of the picture), area (0-1), score, mask ({filename, subfolder, type})."""
    out = (entry.get("outputs") or {}).get("276") or {}
    vs = (out.get("bfs_vsegpick") or [{}])[0]
    fr = (vs.get("frames") or [{}])[0]
    people = []
    for o in fr.get("objects") or []:
        if o.get("remove"):
            continue
        people.append({"id": o.get("id"), "label": o.get("label") or "person", "box": o.get("box") or [0, 0, 0, 0],
                       "area": float(o.get("area") or 0), "score": float(o.get("score") or 0), "mask": o.get("mask")})
    # likely people first: big and confident (a speck on a shelf the finder half-believed is a person sorts last)
    people.sort(key=lambda p: -(p["area"] * (0.3 + p["score"])))
    return {"width": fr.get("width"), "height": fr.get("height"), "image": fr.get("image"), "people": people}


def real_people(people: list) -> list:
    """Drop specks: anything far smaller or far less certain than the main person."""
    if not people:
        return []
    top = max(p["area"] for p in people)
    return [p for p in people if p["area"] >= max(0.01, top * 0.12) and p["score"] >= 0.3]


def auto_pick(people: list, n_refs: int, prompt: str = "") -> dict:
    """Who gets replaced, as {'1': id, '2': id, ...} (1 = red = Picture 2). Place words in the prompt decide first
    ('the woman on the left' -> the left-most person); otherwise the biggest person for one swap, or the people
    left to right for two or three."""
    cands = real_people(people) or list(people)
    n = max(1, min(int(n_refs or 1), MAX_PEOPLE, len(cands)))
    if not cands:
        return {}
    cx = lambda p: p["box"][0] + p["box"][2] / 2
    cy_bottom = lambda p: p["box"][1] + p["box"][3]
    chooser = {
        "left": lambda ps: min(ps, key=cx), "right": lambda ps: max(ps, key=cx),
        "middle": lambda ps: min(ps, key=lambda p: abs(cx(p) - 0.5)),
        "front": lambda ps: max(ps, key=lambda p: (cy_bottom(p), p["area"])),
        "back": lambda ps: min(ps, key=lambda p: (cy_bottom(p), p["area"])),
        "biggest": lambda ps: max(ps, key=lambda p: p["area"]), "smallest": lambda ps: min(ps, key=lambda p: p["area"]),
    }
    picks, left = {}, list(cands)
    for i, word in enumerate(position_words(prompt)[:n], 1):
        if not left:
            break
        p = chooser[word](left)
        picks[str(i)] = p["id"]
        left.remove(p)
    if len(picks) < n:
        rest = sorted(left, key=lambda p: -p["area"])[:n - len(picks)] if n == 1 else sorted(
            sorted(left, key=lambda p: -p["area"])[:n - len(picks)], key=cx)
        for p in rest:
            picks[str(len(picks) + 1)] = p["id"]
    return picks


def remap_picks(old_people: list, new_people: list, picks: dict) -> dict:
    """Picks made on one person list -> the same people in another list (found again at another size): best box overlap."""
    def iou(a, b):
        ax0, ay0, aw, ah = a
        bx0, by0, bw, bh = b
        ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
        iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
        inter = ix * iy
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0
    by_id = {p["id"]: p for p in old_people}
    out = {}
    for k, pid in (picks or {}).items():
        src = by_id.get(pid)
        if not src or not new_people:
            continue
        best = max(new_people, key=lambda p: iou(src["box"], p["box"]))
        if iou(src["box"], best["box"]) > 0.3:
            out[k] = best["id"]
    return out


# ------------------------------------------------------------------ the prompt
def swap_prompt(count: int, guide: str = "hatching", body: str = "new") -> str:
    """The swap LoRA's own prompt (BFS Nodes' image_swap_prompt / hatching_swap_prompt, same words), with one change
    when body == 'keep': the person keeps the ORIGINAL body shape and proportions; only the face, hair, skin and
    outfit come from the reference."""
    count = max(1, min(int(count or 1), MAX_PEOPLE))
    keep = body == "keep"
    if guide == "hatching":
        defs = " ".join(
            f"<Subject {i + 1}> is identified by the {c} hatching lines and matching {c} contour in <Picture 1>; "
            f"its complete appearance comes from <Picture {i + 2}>." for i, c in enumerate(COLORS[:count]))
        transfer = ("Transfer the face, hair, skin and complete outfit from each assigned reference, and keep the body "
                    "shape, figure and proportions of the marked subject in <Picture 1>. " if keep else
                    "Transfer the face, hair, skin, body proportions and complete outfit from each assigned reference. ")
        return ("character_swap: subject_definitions: <Picture 1> is the source image. Colored hatching lines and matching "
                "contours identify the subjects to replace; the visible pixels between the lines provide pose, expression, "
                "lighting and scene context. " + defs
                + " summary: [image editing + reference generation] Replace each marked subject with its assigned reference "
                "character. Remove all colored lines and contours from the result. retention_analysis: " + transfer
                + "Preserve the source pose, hand positions, head orientation, gaze and facial expression. Adapt each "
                "replacement to the source lighting, shadows and exposure. Do not copy the pose or lighting of the reference. "
                "Do not retain the original marked subject's identity or clothing. Preserve every unmarked person, the "
                "background, held objects and foreground occluders. "
                + ("The marked silhouette is the body to keep." if keep else
                   "The hatching identifies who changes and does not constrain the replacement silhouette."))
    marks = {
        "normal map guide": ("normal map", "each subject to replace is marked with its surface-normal map colorized in one solid color (its 3D surface, without its real colors)"),
        "translucent overlay guide": ("translucent overlay", "each subject to replace is identified by a translucent color overlay; the appearance beneath the tint is the source appearance to replace"),
        "outline guide": ("outline", "each subject to replace is identified by a thin colored outline around that subject"),
    }
    mark, explanation = marks[_PROMPT_MODE.get(guide, "normal map guide")]
    takes = "the face, hair, skin and whole outfit" if keep else "the face, hair, body and whole outfit"
    defs = "\n".join(
        f"<Subject {j}> is the subject marked by the {COLORS[j - 1]} {mark} in <Picture 1>; its appearance comes from "
        f"<Picture {j + 1}>, its position, pose, head orientation, gaze and facial expression"
        + (", body shape and proportions" if keep else "") + f" from the {COLORS[j - 1]} mark." for j in range(1, count + 1))
    repl = ", ".join(f"the {COLORS[j - 1]} subject replaced by <Picture {j + 1}>" for j in range(1, count + 1))
    ret = "\n".join(
        f"<Subject {j}>: attribute_transfer - keeps the position, pose, head orientation, gaze"
        + (", body shape, figure and proportions" if keep else " and facial expression")
        + f" of the {COLORS[j - 1]} mark and takes {takes} of <Picture {j + 1}>." for j in range(1, count + 1))
    return ("character_swap:\nsubject_definitions:\n"
            f"<Picture 1> is the source of the edit. In it, {explanation}; "
            + ("the mark gives the body shape to keep" if keep else "the mark says who changes, not where the new look must end")
            + ", and everything else in the guide is the original image.\n"
            f"{defs}\n"
            f"summary: [image editing + reference generation] The target image is <Picture 1> with {repl}, following each "
            "mark's pose and expression. Every other person, the layout, the background, lighting, camera perspective and "
            "framing stay exactly as in <Picture 1>. Keep held objects and foreground occluders in their original positions, "
            "shapes and colors; they are separate from the subjects being replaced. Remove the normal-map colors, skeletons, "
            "overlays and outlines from the final image.\n"
            f"retention_analysis:\n{ret}\n"
            "Everyone else, held objects, foreground occluders, the layout and the background: fully_preserved.")


# ------------------------------------------------------------------ settings
def clean_settings(s: dict | None, manual: bool) -> dict:
    """Auto: the defaults, plus the few choices Auto leaves to you (who, body, size). Manual: every value, in range."""
    s = dict(s or {})
    out = dict(DEFAULTS)
    keys = list(DEFAULTS) if manual else ["body", "mp"]
    for k in keys:
        if k not in s or s[k] in (None, ""):
            continue
        v = s[k]
        if k in ("guide",):
            out[k] = v if v in GUIDE_STYLES else DEFAULTS[k]
        elif k == "body":
            out[k] = "keep" if v == "keep" else "new"
        elif k == "concepts":
            out[k] = re.sub(r"[^\w ,'-]", "", str(v))[:80] or DEFAULTS[k]
        else:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            lo, hi = LIMITS.get(k, (f, f))
            out[k] = min(hi, max(lo, f))
    out["steps"] = int(round(out["steps"]))
    if out["body"] == "keep" and not manual:
        out["guide"] = "shape"        # Auto: keeping the figure uses the body-shape guide
    return out


# ------------------------------------------------------------------ the job graph
def build_graph(base_graph: dict, image: str, refs: list, picks: dict, settings: dict, seed: int,
                prompt_override: str = "", prefix: str = "ComfyStudio/wf/h3_char_swap") -> tuple:
    """-> (graph, prompt). refs = Pictures 2.. (one per pick, in colour order). picks = {'1': 'a1', ...}."""
    s = settings
    g = copy.deepcopy(base_graph)
    n = max(1, min(len(refs), len(picks), MAX_PEOPLE))
    g["63"]["inputs"]["image"] = image
    g["258"]["inputs"]["value"] = float(s["mp"])
    # references: a plain image loader + the same proportional resize the BFS loader does (handles sub-folders)
    g.pop("200", None)
    cond = g["203"]["inputs"]
    for key in ("red_reference", "blue_reference", "green_reference"):
        cond.pop(key, None)
    for i in range(n):
        lid, rid = "cs_ref%d" % (i + 1), "cs_ref%d_size" % (i + 1)
        g[lid] = {"class_type": "LoadImage", "inputs": {"image": refs[i]}}
        g[rid] = {"class_type": "ResizeImageMaskNode", "inputs": {"resize_type": "scale total pixels",
                  "resize_type.megapixels": ["258", 0], "scale_method": "lanczos", "input": [lid, 0]}}
        cond[COLORS[i] + "_reference"] = [rid, 0]
    sel = {"frames": {"0": {str(i + 1): {"selected": [picks[str(i + 1)]], "added": [], "next": 1}
                            for i in range(n) if picks.get(str(i + 1))}}, "active_frame": 0}
    p = g["276"]["inputs"]
    p.update({"selection": json.dumps(sel), "mark_style": GUIDE_STYLES.get(s["guide"], GUIDE_STYLES["hatching"]),
              "concepts": s["concepts"], "threshold": float(s["threshold"]), "hybrid_grow": float(s["margin"]),
              "edge_feather": float(s["feather"]), "clean_masks": float(s["clean"]), "hatch_width": float(s["hatch_width"]),
              "hatch_spacing": float(s["hatch_spacing"]), "max_subjects": str(n), "track": False})
    if "needs normals" in p["mark_style"]:
        p["normals"] = ["252", 0]
    else:
        p.pop("normals", None)
        for k in ("250", "251", "252"):
            g.pop(k, None)
    prompt = (prompt_override or "").strip() or swap_prompt(n, s["guide"], s["body"])
    cond["prompt_override"] = prompt
    g["115"]["inputs"]["strength_model"] = float(s["lora"])
    k = g["38"]["inputs"]
    k["seed"] = int(seed)
    k["steps"] = int(s["steps"])
    g["9"]["inputs"]["filename_prefix"] = prefix
    return g, prompt
