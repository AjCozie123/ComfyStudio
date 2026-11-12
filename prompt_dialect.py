"""How each image model wants its prompt written.

Auto understanding and the Prompt Enhancer write picture references as <Picture N> (MiniMax H3's own syntax).
Other models name their inputs differently, so the finished prompt is translated:
  * FLUX.2 / FLUX.3 Klein: "image 1", "image 2" (BFL's multi-reference wording)
  * Qwen Image / Qwen Image Edit: "Picture 1" (its text encoder labels the inputs "Picture N:")
  * Krea 2 style reference, MOSS: plain words ("the reference picture")
Each family also gets a short style note for the Prompt Enhancer, so the rewrite suits that model.
"""
from __future__ import annotations

import re

DIALECTS = {
    "minimax_h3_image": {"ref": "<Picture {n}>", "hint": ""},
    "flux2_klein": {
        "ref": "image {n}",
        "hint": ("Target model: FLUX.2 Klein. Write natural, concrete prose: the main subject first, then setting, "
                 "light, materials, camera and style. About 40-120 words. No keyword lists, no negative phrasing. "
                 "Refer to input pictures as <Picture 1>, <Picture 2> (they are renamed for the model)."),
    },
    "flux3_klein": {
        "ref": "image {n}",
        "hint": ("Target model: FLUX.3 Klein. Natural, concrete prose with the subject first, then setting, light, "
                 "camera and style. No keyword lists."),
    },
    "krea2": {
        "ref": "reference picture {n}",
        "hint": ("Target model: Krea 2. Use natural photographic language in 1-3 rich sentences: subject, setting, "
                 "light quality, texture, lens and mood. No tag lists, no quality boilerplate."),
    },
    "qwen_image": {
        "ref": "Picture {n}",
        "hint": ("Target model: Qwen Image. One long, flowing English paragraph that describes the finished picture "
                 "from foreground to background. Put any text that must appear in the picture in double quotes, "
                 "exactly as written."),
    },
    "moss": {
        "ref": "picture {n}",
        "hint": ("Target model: MOSS Image. Write a full scene caption: every subject with position, clothing and "
                 "action, then the setting, light, camera angle and mood, in one detailed paragraph."),
    },
}
# video engines: the enhancer writes for motion over time, camera and sound
DIALECTS["minimax_h3_video"] = {
    "ref": "<Picture {n}>",
    "hint": ("Target: MiniMax H3 VIDEO with native sound. This is a video, not a still: describe what happens over "
             "time (actions in order), camera movement, and the sounds, music or spoken lines. Keep people's looks "
             "consistent. Refer to input pictures as <Picture 1>, <Picture 2>. One paragraph."),
}
DIALECTS["ltx"] = {
    "ref": "image {n}",
    "hint": ("Target: LTX-2 VIDEO with audio. Describe events and actions as they happen over time, every visual "
             "detail that must appear, the camera movement, and the sounds or dialogue. One flowing paragraph, "
             "present tense."),
}
GENERIC_VIDEO = {"ref": "image {n}", "hint": "Target: a VIDEO. Describe the action over time, camera movement and sound."}
GENERIC = {"ref": "image {n}", "hint": "Write one clear, detailed English description of the finished picture."}

_TAG = re.compile(r"<\s*(?:picture|image|pic|img)\s*(\d{1,2})\s*>", re.I)


def for_card(card: dict) -> dict:
    """The dialect for an engine card (built-in or uploaded workflow)."""
    video = card.get("media") == "video"
    if card.get("prompt_style") in ("h3", "h3_frames") or (card.get("family") or "").startswith("MiniMax"):
        key = "minimax_h3_video" if video else "minimax_h3_image"
        d = dict(DIALECTS[key])
        d["id"] = key
        return d
    if video:
        key = "ltx" if (card.get("family") == "LTX Video" or str(card.get("id", "")).startswith("ltx")) else ""
        d = dict(DIALECTS.get(key) or GENERIC_VIDEO)
        d["id"] = key or "generic_video"
        return d
    pid = card.get("profile") or ""
    if pid in ("minimax_h3_video", "ltx", "generic_video"):
        card = dict(card, media="video")
        return for_card(dict(card, profile=None, family="LTX Video" if pid == "ltx" else card.get("family"),
                             prompt_style="h3" if pid == "minimax_h3_video" else card.get("prompt_style")))
    d = dict(DIALECTS.get(pid) or GENERIC)
    d["id"] = pid or "generic"
    return d


def translate(text: str, dialect: dict) -> str:
    """Rewrite <Picture N> tags into the model's own wording."""
    if not text:
        return text
    ref = dialect.get("ref") or "image {n}"
    if ref == "<Picture {n}>":
        return text
    return _TAG.sub(lambda m: ref.format(n=int(m.group(1))), text)
