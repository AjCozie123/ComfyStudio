"""Camera helper: one selection per category, plain-English phrases for MiniMax H3 Image.

Official MiniMax-H3-Image docs do not define special camera tokens. They ask for a
detailed English description of the finished image. Third-party H3 shot guides
recommend naming shot size, angle, and lens feel in ordinary cinematographic
English, with one clear setup (not stacked conflicting moves). Millimetre values
are used as a visual character (field of view, compression, distortion), not as a
guaranteed numeric lens control.
"""
from __future__ import annotations

CATEGORIES = [
    {
        "id": "angle",
        "label": "Angle",
        "options": [
            {"id": "eye_level", "label": "Eye level", "phrase": "eye-level camera"},
            {"id": "low_angle", "label": "Low angle", "phrase": "low angle, looking up at the subject"},
            {"id": "worms_eye", "label": "Worm's-eye view", "phrase": "worm's-eye view, looking straight up"},
            {"id": "high_angle", "label": "High angle", "phrase": "high angle, looking down at the subject"},
            {"id": "birds_eye", "label": "Bird's-eye / top-down", "phrase": "bird's-eye, top-down view"},
            {"id": "overhead_flat_lay", "label": "Overhead flat lay", "phrase": "overhead flat-lay, camera looking straight down"},
            {"id": "dutch", "label": "Dutch / tilted", "phrase": "dutch angle, camera tilted in the frame"},
            {"id": "ots", "label": "Over-the-shoulder", "phrase": "over-the-shoulder shot"},
            {"id": "pov", "label": "POV / first-person", "phrase": "point-of-view, first-person shot"},
            {"id": "profile_left", "label": "Side profile (left)", "phrase": "side-profile view from the subject's left"},
            {"id": "profile_right", "label": "Side profile (right)", "phrase": "side-profile view from the subject's right"},
            {"id": "three_quarter", "label": "Three-quarter view", "phrase": "three-quarter view"},
            {"id": "back_view", "label": "Back view", "phrase": "back view of the subject"},
            {"id": "from_behind", "label": "From behind", "phrase": "from behind the subject"},
        ],
    },
    {
        "id": "shot",
        "label": "Shot size",
        "options": [
            {"id": "ecu", "label": "Extreme close-up", "phrase": "extreme close-up"},
            {"id": "cu", "label": "Close-up", "phrase": "close-up"},
            {"id": "mcu", "label": "Medium close-up", "phrase": "medium close-up"},
            {"id": "medium", "label": "Medium", "phrase": "medium shot"},
            {"id": "cowboy", "label": "Cowboy", "phrase": "cowboy shot, framed mid-thigh up"},
            {"id": "full", "label": "Full body", "phrase": "full-body shot"},
            {"id": "wide", "label": "Wide", "phrase": "wide shot"},
            {"id": "ews", "label": "Extreme wide / establishing", "phrase": "extreme wide, establishing shot"},
        ],
    },
    {
        "id": "move",
        "label": "Camera move / position",
        "options": [
            {"id": "orbit_left_45", "label": "Orbit left 45°", "phrase": "camera orbited 45 degrees to the left of the original viewpoint", "vphrase": "the camera slowly orbits 45 degrees to the left around the subject"},
            {"id": "orbit_right_45", "label": "Orbit right 45°", "phrase": "camera orbited 45 degrees to the right of the original viewpoint", "vphrase": "the camera slowly orbits 45 degrees to the right around the subject"},
            {"id": "orbit_left_90", "label": "Orbit left 90°", "phrase": "camera orbited 90 degrees to the left of the original viewpoint", "vphrase": "the camera orbits 90 degrees to the left around the subject"},
            {"id": "orbit_right_90", "label": "Orbit right 90°", "phrase": "camera orbited 90 degrees to the right of the original viewpoint", "vphrase": "the camera orbits 90 degrees to the right around the subject"},
            {"id": "rotate_front", "label": "Rotate to front", "phrase": "camera rotated around to a front view", "vphrase": "the camera circles around to a front view"},
            {"id": "rotate_back", "label": "Rotate to back", "phrase": "camera rotated around to a back view", "vphrase": "the camera circles around to a back view"},
            {"id": "closer", "label": "Move closer", "phrase": "camera moved closer to the subject", "vphrase": "slow push-in, the camera moves closer to the subject"},
            {"id": "further", "label": "Move further", "phrase": "camera moved further from the subject", "vphrase": "slow pull-back, the camera moves away from the subject"},
            {"id": "raise", "label": "Raise camera", "phrase": "camera raised higher", "vphrase": "the camera cranes up"},
            {"id": "lower", "label": "Lower camera", "phrase": "camera lowered", "vphrase": "the camera cranes down"},
            {"id": "aerial", "label": "Aerial / drone", "phrase": "aerial, drone-like viewpoint", "vphrase": "aerial drone shot gliding over the scene"},
            # video only: moves that need time to happen
            {"id": "static", "label": "Locked-off (still)", "video": True, "phrase": "locked-off static camera, no camera movement"},
            {"id": "pan_left", "label": "Pan left", "video": True, "phrase": "the camera pans slowly to the left"},
            {"id": "pan_right", "label": "Pan right", "video": True, "phrase": "the camera pans slowly to the right"},
            {"id": "tilt_up", "label": "Tilt up", "video": True, "phrase": "the camera tilts slowly upward"},
            {"id": "tilt_down", "label": "Tilt down", "video": True, "phrase": "the camera tilts slowly downward"},
            {"id": "tracking", "label": "Tracking", "video": True, "phrase": "tracking shot, the camera follows the subject as they move"},
            {"id": "handheld", "label": "Handheld", "video": True, "phrase": "handheld camera with natural slight shake"},
            {"id": "zoom_in", "label": "Zoom in", "video": True, "phrase": "slow smooth zoom in"},
            {"id": "zoom_out", "label": "Zoom out", "video": True, "phrase": "slow smooth zoom out"},
        ],
    },
    {
        "id": "lens",
        "label": "Lens",
        "options": [
            {"id": "14mm", "label": "Ultra-wide 14mm", "phrase": "ultra-wide 14mm-equivalent look, very broad field of view"},
            {"id": "24mm", "label": "24mm", "phrase": "24mm-equivalent wide-lens look, broad field of view and natural verticals"},
            {"id": "35mm", "label": "35mm", "phrase": "35mm-equivalent look"},
            {"id": "50mm", "label": "50mm", "phrase": "50mm-equivalent natural field of view"},
            {"id": "85mm", "label": "85mm portrait", "phrase": "85mm-equivalent portrait look, narrower field of view and gently compressed background"},
            {"id": "200mm", "label": "Telephoto 200mm", "phrase": "telephoto 200mm-equivalent look, tight field of view and compressed perspective"},
            {"id": "fisheye", "label": "Fisheye", "phrase": "fisheye look with strong barrel distortion"},
            {"id": "macro", "label": "Macro", "phrase": "macro, extreme close detail"},
            {"id": "tilt_shift", "label": "Tilt-shift", "phrase": "tilt-shift look"},
        ],
    },
    {
        "id": "focus",
        "label": "Focus / depth",
        "options": [
            {"id": "shallow", "label": "Shallow DOF bokeh", "phrase": "shallow depth of field with soft bokeh"},
            {"id": "deep", "label": "Deep focus", "phrase": "deep focus, sharp from foreground to background"},
        ],
    },
    {
        "id": "style",
        "label": "Style framing",
        "options": [
            {"id": "symmetrical", "label": "Symmetrical centered", "phrase": "symmetrical, subject centered in the frame"},
            {"id": "thirds", "label": "Rule of thirds", "phrase": "rule-of-thirds framing"},
            {"id": "selfie", "label": "Selfie", "phrase": "handheld selfie framing"},
            {"id": "mirror_selfie", "label": "Mirror selfie", "phrase": "mirror-selfie framing"},
            {"id": "cctv", "label": "CCTV", "phrase": "CCTV / security-camera framing, slightly high and wide"},
            {"id": "gopro", "label": "GoPro", "phrase": "GoPro-style wide action framing"},
        ],
    },
]

_BY_CAT = {c["id"]: c for c in CATEGORIES}
_OPT = {(c["id"], o["id"]): o for c in CATEGORIES for o in c["options"]}


def catalog():
    """JSON-serialisable catalogue for the Camera sheet (+ which options each engine follows in Edit)."""
    return {"categories": CATEGORIES, "edit_support": {k: sorted(v) for k, v in EDIT_SUPPORT.items()}}


# ---------------------------------------------------------------- Edit: re-shooting an existing photo
# Tested 2026-11-22 (samples/camera/sheets): "Camera: orbit right 90 degrees" style notes were ignored by every edit
# model, but describing the FINISHED view ("seen from their right side, in a full side profile view") works on
# MiniMax H3, FLUX.2 Klein 9B and Qwen Image. Each option below says what the new picture shows.
EDIT_PHRASE = {
    ("angle", "eye_level"): "The camera is at the main subject's eye height, looking straight at them.",
    ("angle", "low_angle"): "Show the same scene from a low camera angle: the camera is near the floor looking up at the main subject.",
    ("angle", "worms_eye"): "Show the same scene from a worm's-eye view: the camera is on the ground looking steeply up at the main subject.",
    ("angle", "high_angle"): "Show the same scene from a high camera angle: the camera is above the main subject looking down, so we see more of the floor.",
    ("angle", "birds_eye"): "Show the same scene from directly above, a top-down view looking straight down on the main subject and the floor.",
    ("angle", "dutch"): "Show the same scene with the camera tilted sideways, so the whole picture is at a slanted dutch angle.",
    ("angle", "profile_left"): "Show the main subject seen from their left side, in a full side profile view, eye-level shot.",
    ("angle", "profile_right"): "Show the main subject seen from their right side, in a full side profile view, eye-level shot.",
    ("angle", "three_quarter"): "Show the main subject in a three-quarter view: their body turned about 45 degrees away from the camera.",
    ("angle", "back_view"): "Show the main subject seen from directly behind: we see their back and the back of their head, not their face.",
    ("angle", "from_behind"): "Show the main subject seen from directly behind: we see their back and the back of their head, not their face.",
    ("shot", "ecu"): "Show the same scene as an extreme close-up of the main subject's face.",
    ("shot", "cu"): "Show the same scene as a close-up: framed on the main subject's face and shoulders.",
    ("shot", "mcu"): "Show the same scene as a medium close-up: the main subject from the chest up.",
    ("shot", "medium"): "Show the same scene as a medium shot: the main subject from the waist up.",
    ("shot", "cowboy"): "Show the same scene framed from the main subject's mid-thigh up.",
    ("shot", "full"): "Show the same scene as a full-body shot: the main subject from head to feet with some space around them.",
    ("shot", "wide"): "Show the same scene as a wide shot: the camera further back, the main subject smaller with much more of the place around them.",
    ("shot", "ews"): "Show the same scene as an extreme wide shot: the camera far away, the main subject small in a large view of the place.",
    ("move", "orbit_left_45"): "Show the main subject in a three-quarter view from their left side: the camera has moved about 45 degrees around them.",
    ("move", "orbit_right_45"): "Show the main subject in a three-quarter view from their right side: the camera has moved about 45 degrees around them.",
    ("move", "orbit_left_90"): "Show the main subject seen from their left side, in a full side profile view, eye-level shot.",
    ("move", "orbit_right_90"): "Show the main subject seen from their right side, in a full side profile view, eye-level shot.",
    ("move", "rotate_front"): "Show the main subject seen from the front, facing the camera.",
    ("move", "rotate_back"): "Show the main subject seen from directly behind: we see their back and the back of their head, not their face.",
    ("move", "closer"): "Show the same scene with the camera much closer to the main subject: a tighter, closer framing.",
    ("move", "further"): "Zoom out: the camera is much further back, the main subject smaller, with much more of the place visible around them.",
    ("move", "raise"): "Show the same scene from a higher camera position looking down at the main subject.",
    ("move", "lower"): "Show the same scene from a lower camera position looking up at the main subject.",
    ("move", "aerial"): "Show the same place from high up in the air, a drone view looking down on it.",
    ("lens", "14mm"): "Show the same scene as if shot with an ultra-wide 14 mm lens: strong perspective and much more of the place in view.",
    ("lens", "24mm"): "Show the same scene as if shot with a wide 24 mm lens: a broader view of the place.",
    ("lens", "35mm"): "Show the same scene as if shot with a 35 mm lens.",
    ("lens", "50mm"): "Show the same scene as if shot with a 50 mm lens: natural perspective.",
    ("lens", "85mm"): "Show the same scene as if shot with an 85 mm portrait lens: tighter framing on the main subject and a softly blurred background.",
    ("lens", "200mm"): "Show the same scene as if shot with a 200 mm telephoto lens: very tight framing and a flattened, blurred background.",
    ("lens", "fisheye"): "Show the same scene through a fisheye lens: a strongly curved, bulging wide view.",
    ("lens", "macro"): "Show an extreme macro close-up detail of the main subject.",
    ("lens", "tilt_shift"): "Show the same scene with a tilt-shift look: a thin band of sharp focus and a miniature feel.",
    ("focus", "shallow"): "Give the picture a shallow depth of field: the main subject sharp and the background strongly blurred.",
    ("focus", "deep"): "Give the picture deep focus: everything sharp from front to back.",
}
# not "keep the same clothes": the same prompt may ask for a new outfit or place together with the new angle
EDIT_KEEP = "Apart from the camera view and anything asked above, keep the same person and the same picture."

# Which options each edit engine really follows (from the tests above). An engine missing here: Camera is hidden in
# Edit. ots / pov / flat lay and the Style framings are left out of Edit everywhere (they compose a new picture).
# Tested 2026-11-22 at 640x832, seed 777 (samples/camera/sheets 01-04). Kept: what worked, plus options of the same
# kind as a passing one (e.g. orbit 90 = side profile, raise = high angle). Dropped: what did nothing or broke.
_VIEWS = {"angle:eye_level", "angle:low_angle", "angle:worms_eye", "angle:high_angle", "angle:birds_eye",
          "angle:profile_left", "angle:profile_right", "angle:three_quarter", "angle:back_view", "angle:from_behind",
          "move:orbit_left_45", "move:orbit_right_45", "move:orbit_left_90", "move:orbit_right_90",
          "move:rotate_front", "move:rotate_back", "move:raise", "move:lower"}
_TIGHT = {"shot:ecu", "shot:cu", "shot:mcu", "shot:medium", "shot:cowboy", "move:closer"}
_WIDE = {"shot:full", "shot:wide", "shot:ews", "move:further"}
EDIT_SUPPORT = {
    # H3: every view + tighter framing, 85/200 mm look and background blur work; dutch came out sideways, wider
    # framing / 14 mm did nothing
    "minimax_h3": _VIEWS | _TIGHT | {"lens:85mm", "lens:200mm", "focus:shallow"},
    # Klein: views and every shot size work; dutch, lens looks and focus did nothing
    "flux2_klein": _VIEWS | _TIGHT | _WIDE,
    # Qwen Image (2.1 and Edit 2509): views, shot sizes, dutch, zooming out and wide lenses work; 85 mm / blur didn't
    "qwen_image": _VIEWS | _TIGHT | _WIDE | {"angle:dutch", "lens:14mm", "lens:24mm"},
}


def edit_family(card=None, builtin=False):
    """The EDIT_SUPPORT key for an engine card (None: Camera isn't offered in Edit for it)."""
    if builtin:
        return "minimax_h3"
    c = card or {}
    if c.get("edit_kind") in ("krea_edit", "inpaint") or c.get("task") not in (None, "edit"):
        return None
    txt = " ".join(str(c.get(k) or "") for k in ("family", "id", "profile", "name")).lower()
    if "minimax" in txt or "h3" in txt.split("_")[0:1]:
        return "minimax_h3"
    if "klein" in txt or "flux.2" in txt or "flux2" in txt:
        return "flux2_klein"
    if "qwen" in txt:
        return "qwen_image"
    return None


def filter_for_edit(selections, family):
    """Drop the options an engine doesn't follow in Edit (stale picks from another engine must not leak in)."""
    sel = normalize_camera(selections)
    ok = EDIT_SUPPORT.get(family or "", set())
    return {cat: v for cat, v in sel.items() if "%s:%s" % (cat, v) in ok}


def edit_block(selections) -> str:
    sel = normalize_camera(selections)
    parts = []
    for cat in CATEGORIES:
        v = sel.get(cat["id"])
        if v and (cat["id"], v) in EDIT_PHRASE:
            parts.append(EDIT_PHRASE[(cat["id"], v)])
    if not parts:
        return ""
    return " ".join(parts) + " " + EDIT_KEEP


def normalize_camera(raw) -> dict:
    """Keep at most one valid option per category, in catalogue order."""
    if not raw:
        return {}
    src = raw if isinstance(raw, dict) else {}
    out = {}
    for cat in CATEGORIES:
        val = src.get(cat["id"]) or src.get(cat["label"])
        if not val:
            continue
        val = str(val).strip()
        if (cat["id"], val) in _OPT:
            out[cat["id"]] = val
            continue
        # allow label match
        for o in cat["options"]:
            if val.lower() == o["id"].lower() or val.lower() == o["label"].lower():
                out[cat["id"]] = o["id"]
                break
    return out


def chips(selections) -> list:
    sel = normalize_camera(selections)
    out = []
    for cat in CATEGORIES:
        oid = sel.get(cat["id"])
        if not oid:
            continue
        opt = _OPT[(cat["id"], oid)]
        out.append({
            "category": cat["id"],
            "category_label": cat["label"],
            "id": oid,
            "label": opt["label"],
            "phrase": opt["phrase"],
        })
    return out


def details_text(selections, mode: str = "t2i") -> str:
    video = (mode or "").lower() == "video"
    parts = []
    for c in chips(selections):
        opt = _OPT[(c["category"], c["id"])]
        if opt.get("video") and not video:
            continue                          # a pan or a zoom means nothing in a still picture
        parts.append(opt.get("vphrase") or opt["phrase"] if video else opt["phrase"])
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def camera_block(selections, mode: str = "t2i") -> str:
    """Standalone camera instruction appended to the user/assist prompt."""
    mode = (mode or "t2i").strip().lower()
    details = details_text(selections, mode)
    if not details:
        return ""
    if mode == "video":
        # a video moves through time: the camera notes describe the shot and its motion, never a re-shoot of a still
        return "Camera: %s." % details
    if mode in ("edit", "image-edit", "image_edit"):
        return edit_block(selections)
    # t2i and reference mode: new or composed scene, not a reshoot of Picture 1
    return "Camera: %s." % details


def already_has_block(prompt: str, block: str) -> bool:
    if not block:
        return True
    return block.lower() in (prompt or "").lower()


def apply_camera(prompt: str, selections=None, mode: str = "t2i") -> dict:
    user = (prompt or "").rstrip()
    sel = normalize_camera(selections)
    block = camera_block(sel, mode=mode)
    appended = ""
    final = user
    if block and not already_has_block(user, block):
        appended = block
        final = user + ("\n\n" if user else "") + block
    return {
        "prompt": final,
        "appended": appended,
        "block": block,
        "selections": sel,
        "chips": chips(sel),
        "details": details_text(sel),
    }


def pe_camera_addon(selections, mode: str = "t2i") -> str:
    block = camera_block(selections, mode=mode)
    if not block:
        return ""
    return (
        "Camera instructions (honor these in the finished image and in expanded_caption; "
        "do not drop or contradict them): " + block
    )
