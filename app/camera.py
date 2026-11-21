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
    """JSON-serialisable catalogue for the Camera sheet."""
    return {"categories": CATEGORIES}


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
        return (
            "Re-shoot the same scene of <Picture 1> from this new viewpoint, "
            "keeping the subject's identity and details and the scene contents consistent. "
            "Camera: %s." % details
        )
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
