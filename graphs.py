"""Build ComfyUI API-format prompts for MiniMax H3 Image text-to-image and edit."""
from __future__ import annotations

import math
import os
import re
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates")
# a sampler choice that is a recipe, not a KSampler name (see _sample_and_save)
LCM_DETAIL = "lcm_detail"

DEFAULTS = {
    "unet_name": r"MiniMax\minimax_h3_image_pruned_w4a8.safetensors",
    "clip_name": r"MiniMax\qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
    "clip_type": "minimax",
    "vae_name": r"MiniMax\minimax_h3_image_vae_fp16.safetensors",
    "pe_clip_name": "qwen3.5_9b_w4a8_gptq.safetensors",
    "sampler_name": "lcm_detail",   # SamplerLCM, noise 1.10 -> 0.90 (tested better than euler, 2026-11-11)
    "scheduler": "simple",
    "cfg": 1.0,
    "denoise": 1.0,
    "steps_t2i": 6,
    "steps_edit": 6,
    "weight_dtype": "default",
    "ref_megapixels": 8.0,
    "ref_multiple": 32,
    "scale_method": "area",
}

# (id, label, width, height, group)  -- width/height None means Match input image
RESOLUTIONS = [
    ("match", "Match Picture 1", None, None, "edit"),
    ("4096x4096", "1:1  4096x4096", 4096, 4096, "16mp"),
    ("4736x3552", "4:3  4736x3552", 4736, 3552, "16mp"),
    ("3552x4736", "3:4  3552x4736", 3552, 4736, "16mp"),
    ("5024x3360", "3:2  5024x3360", 5024, 3360, "16mp"),
    ("3360x5024", "2:3  3360x5024", 3360, 5024, "16mp"),
    ("5472x3072", "16:9  5472x3072", 5472, 3072, "16mp"),
    ("3072x5472", "9:16  3072x5472", 3072, 5472, "16mp"),
    ("6272x2688", "21:9  6272x2688", 6272, 2688, "16mp"),
    ("2048x2048", "1:1  2048x2048", 2048, 2048, "4mp"),
    ("2368x1760", "4:3  2368x1760", 2368, 1760, "4mp"),
    ("1760x2368", "3:4  1760x2368", 1760, 2368, "4mp"),
    ("2496x1664", "3:2  2496x1664", 2496, 1664, "4mp"),
    ("1664x2496", "2:3  1664x2496", 1664, 2496, "4mp"),
    ("2720x1536", "16:9  2720x1536", 2720, 1536, "4mp"),
    ("1536x2720", "9:16  1536x2720", 1536, 2720, "4mp"),
    ("3136x1344", "21:9  3136x1344", 3136, 1344, "4mp"),
]

RES_BY_ID = {r[0]: r for r in RESOLUTIONS}


ASPECT_RATIOS = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "21:9": (21, 9),
}
MP_MIN = 1.0
MP_MAX = 20.0
MP_STEP = 0.5



def size_for_megapixels(aw, ah, mp, multiple=32):
    """Closest width x height, each a multiple of `multiple`, for area mp*1e6 at aspect aw:ah.

    Candidates are chosen to minimize aspect-ratio error, then area error.
    """
    mp = float(mp)
    if mp < MP_MIN - 1e-9 or mp > MP_MAX + 1e-9:
        raise ValueError("Custom size must be between 1 and 20 megapixels.")
    if aw <= 0 or ah <= 0:
        raise ValueError("Aspect ratio must be positive.")
    area = mp * 1_000_000.0
    ar = float(aw) / float(ah)
    w0 = math.sqrt(area * ar)
    h0 = w0 / ar
    # Independently rounded pair is the default; only replaced if a neighbour
    # is strictly better on aspect, or tied on aspect and better on area.
    wc = int(round(w0 / multiple))
    hc = int(round(h0 / multiple))

    def pack(w, h):
        w = max(multiple, int(w))
        h = max(multiple, int(h))
        aspect_err = abs((w / h) - ar)
        area_err = abs((w * h) - area) / area
        return (aspect_err, area_err, abs(w - w0) + abs(h - h0)), w, h

    cands = []
    for dw in range(-12, 13):
        for dh in range(-12, 13):
            cands.append(pack(max(multiple, (wc + dw) * multiple), max(multiple, (hc + dh) * multiple)))
    # Prefer accurate aspect among sizes close to the target area (8%),
    # so 16:9 at 8 MP stays near 8 MP rather than drifting to a smaller exact ratio.
    tight = [c for c in cands if c[0][1] <= 0.08] or cands
    best = min(tight, key=lambda c: c[0])
    return int(best[1]), int(best[2])


def describe_size(w, h):
    actual = (int(w) * int(h)) / 1_000_000.0
    return {"width": int(w), "height": int(h), "mp": round(actual, 2)}


KEEP_NAMES = {
    "keep", "keep-input", "keep_input", "keep input", "keep input resolution",
    "original", "original size", "same", "same size",
}

PICTURE1_NAMES = {
    "picture1", "picture-1", "match-picture-1", "match picture 1",
    "match_picture_1", "match p1", "match-p1",
}


def is_keep_resolution(value):
    if isinstance(value, dict):
        kind = str(value.get("kind") or value.get("mode") or "").strip().lower()
        if kind in KEEP_NAMES or kind in {"original", "picture1-original"}:
            return True
        value = value.get("resolution") or value.get("id") or ""
    s = str(value or "").strip().lower()
    if s in KEEP_NAMES:
        return True
    # picture1:original / picture1 (no mp) count as Original
    if s in PICTURE1_NAMES or s in {"picture1:original", "picture1:orig", "custom:picture1:original"}:
        return True
    if s.startswith("picture1:") and s.split(":", 1)[1] in {"original", "orig", "keep"}:
        return True
    return False


def is_picture1_resolution(value):
    """True for Match Picture 1 (Original or a megapixel target), plus legacy keep/match."""
    if is_keep_resolution(value):
        return True
    if isinstance(value, dict):
        kind = str(value.get("kind") or value.get("mode") or value.get("aspect") or "").strip().lower()
        if kind in PICTURE1_NAMES or kind in {"match", "input", "matchinput", "match-input"}:
            return True
        value = value.get("resolution") or value.get("id") or ""
    s = str(value or "").strip().lower()
    if s in PICTURE1_NAMES or s in {"match", "match input image", "input", "match input"}:
        return True
    if s.startswith("picture1:"):
        return True
    if s.startswith("custom:match:") or s.startswith("custom:picture1:"):
        return True
    return False


def snap_to_multiple(w, h, multiple=32):
    """Snap each side to the nearest multiple of `multiple`, at least `multiple`."""
    def one(n):
        n = float(n)
        return max(multiple, int(round(n / multiple)) * multiple)
    return int(one(w)), int(one(h))


def keep_input_size(input_size, multiple=32):
    """Model size for Original: exact if already aligned, else minimal snap to `multiple`."""
    if not input_size or not input_size[0] or not input_size[1]:
        raise ValueError("Match Picture 1 needs Picture 1 so the original size can be read.")
    return snap_to_multiple(input_size[0], input_size[1], multiple=multiple)


def original_size_spec(input_size, multiple=32):
    """Return a size spec for the Original setting.

    model_w/h are sent to MiniMaxH3ImageConditioning (multiple of 32).
    output_w/h are the saved image size (exact Picture 1 pixels).
    restore is True when a post-decode scale back to the exact original is needed.
    """
    if not input_size or not input_size[0] or not input_size[1]:
        raise ValueError("Match Picture 1 — Original needs Picture 1 so the original size can be read.")
    ow, oh = int(input_size[0]), int(input_size[1])
    if ow < 8 or oh < 8:
        raise ValueError("Picture 1 is too small.")
    if ow % multiple == 0 and oh % multiple == 0:
        return {
            "width": ow, "height": oh,
            "output_width": ow, "output_height": oh,
            "orig_width": ow, "orig_height": oh,
            "restore": False, "original": True, "picture1": True, "mp": None,
            "match_input": False, "keep_input": True,
        }
    mw, mh = snap_to_multiple(ow, oh, multiple=multiple)
    return {
        "width": mw, "height": mh,
        "output_width": ow, "output_height": oh,
        "orig_width": ow, "orig_height": oh,
        "restore": True, "original": True, "picture1": True, "mp": None,
        "match_input": False, "keep_input": True,
    }


def picture1_mp_spec(input_size, mp, multiple=32):
    if not input_size or not input_size[0] or not input_size[1]:
        raise ValueError("Match Picture 1 needs Picture 1 so the aspect can be read.")
    ow, oh = int(input_size[0]), int(input_size[1])
    w, h = size_for_megapixels(ow, oh, mp, multiple=multiple)
    return {
        "width": w, "height": h,
        "output_width": w, "output_height": h,
        "orig_width": ow, "orig_height": oh,
        "restore": False, "original": False, "picture1": True, "mp": float(mp),
        "match_input": False, "keep_input": False,
    }


def _parse_custom(value, mode="t2i", input_size=None):
    """Accept 'custom:16:9:4.5', 'custom:match:8', 'picture1:8', or a dict {kind, aspect, mp}."""
    if isinstance(value, dict):
        kind = str(value.get("kind") or value.get("mode") or "").strip().lower()
        aspect = str(value.get("aspect") or "").strip()
        mp = value.get("mp") if value.get("mp") is not None else value.get("megapixels")
        if kind in KEEP_NAMES or str(mp).lower() in {"original", "orig", "keep"} and (
            kind in PICTURE1_NAMES or aspect.lower() in PICTURE1_NAMES or aspect.lower() in {"match", "input"}
        ):
            return None  # handled as keep/original
        if kind in {"custom", "custom_mp", "mp", "custom-mp", "picture1"} or kind in PICTURE1_NAMES:
            if not aspect:
                aspect = "picture1" if kind in PICTURE1_NAMES or kind == "picture1" else "1:1"
            if mp is None:
                return None
            value = "custom:%s:%s" % (aspect, mp)
        else:
            return None
    s = str(value).strip()
    # picture1:8 / picture1:8.0
    m_p1 = re.match(r"(?i)^(?:picture1|match-?picture-?1|custom:picture1|custom:match):([0-9]+(?:\.[0-9]+)?)$", s)
    if m_p1:
        if mode != "edit":
            raise ValueError("Match Picture 1 is only available in Image Edit.")
        return picture1_mp_spec(input_size, float(m_p1.group(1)))
    m = re.match(r"(?i)^custom:(.+):([0-9]+(?:\.[0-9]+)?)$", s)
    if not m:
        return None
    asp = m.group(1).strip()
    mp = float(m.group(2))
    key = asp.lower().replace(" ", "").replace("_", "-")
    if key in {"match", "input", "matchinput", "match-input"} or key in {n.replace(" ", "") for n in PICTURE1_NAMES}:
        if mode != "edit":
            raise ValueError("Match Picture 1 is only available in Image Edit.")
        return picture1_mp_spec(input_size, mp)
    asp_key = asp.replace(" ", "").replace("-", ":")
    if asp_key not in ASPECT_RATIOS:
        raise ValueError(
            "Unknown aspect ratio for Custom MP. Use 1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16, 21:9, or Match Picture 1."
        )
    aw, ah = ASPECT_RATIOS[asp_key]
    w, h = size_for_megapixels(aw, ah, mp)
    return {
        "width": w, "height": h,
        "output_width": w, "output_height": h,
        "orig_width": None, "orig_height": None,
        "restore": False, "original": False, "picture1": False, "mp": mp,
        "match_input": False, "keep_input": False,
    }


def _spec(w, h, **extra):
    d = {
        "width": int(w) if w else w,
        "height": int(h) if h else h,
        "output_width": int(w) if w else w,
        "output_height": int(h) if h else h,
        "orig_width": None, "orig_height": None,
        "restore": False, "original": False, "picture1": False, "mp": None,
        "match_input": False, "keep_input": False,
    }
    d.update(extra)
    return d


def parse_resolution(value, mode="t2i", input_size=None):
    """Return (width, height, match_input) for compatibility. Prefer resolve_size()."""
    spec = resolve_size(value, mode=mode, input_size=input_size)
    return spec["width"], spec["height"], bool(spec.get("match_input"))


def resolve_size(value, mode="t2i", input_size=None):
    """Return a size spec dict (model W/H, saved output W/H, flags)."""
    if is_keep_resolution(value):
        if mode != "edit":
            raise ValueError("Match Picture 1 — Original is only available in Image Edit.")
        spec = original_size_spec(input_size)
        if spec["width"] < 32 or spec["height"] < 32 or spec["width"] > 16384 or spec["height"] > 16384:
            raise ValueError("Match Picture 1 — Original is out of range after snapping to a multiple of 32.")
        return spec
    # picture1 with no mp suffix == Original
    s0 = str(value or "").strip().lower() if not isinstance(value, (dict, list, tuple)) else ""
    if s0 in PICTURE1_NAMES:
        if mode != "edit":
            raise ValueError("Match Picture 1 is only available in Image Edit.")
        return original_size_spec(input_size)
    custom = _parse_custom(value, mode=mode, input_size=input_size)
    if custom is not None:
        if isinstance(custom, tuple):
            w, h, match = custom
            return _spec(w, h, match_input=bool(match))
        return custom
    if value is None or value == "":
        if mode == "edit":
            # Legacy default used to be match (GetImageSize after 8 MP prep).
            # New default: if we have Picture 1, Original; else 2048.
            if input_size and input_size[0] and input_size[1]:
                return original_size_spec(input_size)
            return _spec(2048, 2048)
        return _spec(2048, 2048)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _spec(int(value[0]), int(value[1]))
    if isinstance(value, dict):
        if value.get("width") and value.get("height"):
            return _spec(int(value["width"]), int(value["height"]))
        value = value.get("resolution") or value.get("id") or ""
        if is_keep_resolution(value):
            if mode != "edit":
                raise ValueError("Match Picture 1 — Original is only available in Image Edit.")
            return original_size_spec(input_size)
        custom = _parse_custom(value, mode=mode, input_size=input_size)
        if custom is not None:
            return custom if isinstance(custom, dict) else _spec(custom[0], custom[1], match_input=bool(custom[2]))
        if not str(value).strip():
            return _spec(2048, 2048)
        s = str(value).strip()
    else:
        s = str(value).strip()
    # Legacy "match": was GetImageSize after the 8 MP resize. Honour the
    # user's intent (same aspect as Picture 1) as an 8 MP Picture 1 target
    # with explicit W/H so the result is deterministic and EXIF-correct.
    if s.lower() in {"match", "match input image", "input", "match input"}:
        if mode != "edit":
            raise ValueError("Match Picture 1 is only available in Image Edit.")
        return picture1_mp_spec(input_size, 8.0)
    if s in RES_BY_ID:
        _id, _label, w, h, _g = RES_BY_ID[s]
        if w is None:
            if mode != "edit":
                raise ValueError("Match Picture 1 is only available in Image Edit.")
            return picture1_mp_spec(input_size, 8.0)
        return _spec(int(w), int(h))
    m = re.search(r"(\d+)\s*[xX\u00d7]\s*(\d+)", s)
    if not m:
        raise ValueError("Could not read that resolution. Use a preset, Custom MP, or WIDTHxHEIGHT, for example 2048x2048.")
    w, h = int(m.group(1)), int(m.group(2))
    if w < 32 or h < 32 or w > 16384 or h > 16384:
        raise ValueError("Resolution is out of range.")
    return _spec(w, h)

def new_seed():
    return secrets.randbelow(2**53)


def _node(class_type, inputs, title=None):
    n = {"class_type": class_type, "inputs": inputs}
    if title:
        n["_meta"] = {"title": title}
    return n


def _loaders(g, cfg):
    g["unet"] = _node("UNETLoader", {
        "unet_name": cfg["unet_name"],
        "weight_dtype": cfg.get("weight_dtype") or "default",
    }, "Load Diffusion Model")
    g["clip"] = _node("CLIPLoader", {
        "clip_name": cfg["clip_name"],
        "type": cfg.get("clip_type") or "minimax",
        "device": "default",
    }, "Load CLIP")
    g["vae"] = _node("VAELoader", {
        "vae_name": cfg["vae_name"],
    }, "Load VAE")



def default_pe_system(mode="t2i", pe_mode="fast"):
    """The built-in Prompt Enhancer system prompt (what's used unless the user saved their own)."""
    import pe as PE
    st = PE.settings(pe_mode)
    return _read_template(st["system_edit"] if mode == "edit" else st["system_t2i"])


def _read_template(name):
    path = os.path.join(TEMPLATES, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _pe_chain(g, user_prompt_node, enable, images, system_file, task_file, max_length, pe_clip_name,
              thinking=True, mtp="auto", temperature=1.0, top_k=20, pe_clip_type="minimax",
              standalone=False, free_memory=False, pe_system_extra="", pe_task_extra="", pe_system_override=""):
    """Prompt enhancer. images is a list of [node_id, 0] links, already in <Picture N> order."""
    g["pe_clip"] = _node("CLIPLoader", {
        "clip_name": pe_clip_name,
        "type": pe_clip_type or "minimax",
        "device": "default",
    }, "Load PE CLIP")
    system = _read_template(system_file)
    if pe_system_extra:
        system = system.rstrip() + "\n\n" + str(pe_system_extra).strip() + "\n"
    if (pe_system_override or "").strip():
        system = str(pe_system_override).strip() + "\n"     # the user's own system prompt for this engine
        if "expanded_caption" not in system:
            # the app reads the result from this field; without it the enhanced prompt would be lost silently
            system += ('\nOutput format (added by Comfy Studio): reply with exactly one JSON object '
                       '{"expanded_caption": "<the finished prompt>"} and nothing else.\n')
    task = _read_template(task_file)
    if pe_task_extra:
        task = str(pe_task_extra).strip() + "\n\n" + task
    g["pe_system"] = _node("PrimitiveStringMultiline", {
        "value": system,
    }, "Text (system_prompt)")
    g["pe_task"] = _node("PrimitiveStringMultiline", {
        "value": task,
    }, "Text (task_prompt)")
    g["pe_fill"] = _node("StringReplace", {
        "string": ["pe_task", 0],
        "find": "{input text}",
        "replace": [user_prompt_node, 0],
    }, "Replace Text")
    tg = {
        "prompt": ["pe_fill", 0],
        "max_length": int(max_length),
        "sampling_mode": "on",
        "sampling_mode.temperature": float(temperature),
        "sampling_mode.top_k": int(top_k),
        "sampling_mode.top_p": 0.95,
        "sampling_mode.min_p": 0.0,
        "sampling_mode.repetition_penalty": 1.0,
        "sampling_mode.presence_penalty": 0.0,
        "sampling_mode.seed": 0,
        "thinking": bool(thinking),
        "use_default_template": True,
        "mtp": mtp or "auto",
        "clip": ["pe_clip", 0],
        "system_prompt": ["pe_system", 0],
    }
    for i, link in enumerate(images, start=1):
        tg["images.image%d" % i] = link
    g["pe_gen"] = _node("TextGenerateV2", tg, "Generate Text")
    src = ["pe_gen", 0]
    if standalone:
        if free_memory:
            g["pe_free"] = _node("AUSBOSS_NODES_FreeMemory", {"value": src}, "Free PE memory")
            src = ["pe_free", 0]
        g["pe_preview"] = _node("PreviewAny", {"source": src}, "Preview as Text")
        return ["pe_preview", 0]
    g["pe_json"] = _node("JsonExtractString", {
        "json_string": src,
        "key": "expanded_caption",
    }, "Extract Text from JSON")
    g["pe_switch"] = _node("ComfySwitchNode", {
        "switch": bool(enable),
        "on_false": [user_prompt_node, 0],
        "on_true": ["pe_json", 0],
    }, "If/Else Switch")
    g["pe_preview"] = _node("PreviewAny", {
        "source": ["pe_switch", 0],
    }, "Preview as Text")
    return ["pe_preview", 0]



def target_ref_megapixels(spec, input_size=None):
    """Megapixels to scale every input image to, matching the output's detail level.

    Each input keeps its own aspect ratio; ResizeImageMaskNode then snaps sides
    to a multiple of 32. In Original mode this is Picture 1's own megapixel
    area so every reference is at the same detail as the exact-size output.
    """
    if not spec:
        return float(DEFAULTS["ref_megapixels"])
    if spec.get("original") or spec.get("keep_input"):
        ow = spec.get("orig_width") or (input_size[0] if input_size else None)
        oh = spec.get("orig_height") or (input_size[1] if input_size else None)
        if ow and oh:
            return max(0.01, (int(ow) * int(oh)) / 1_000_000.0)
    if spec.get("mp") is not None:
        return float(spec["mp"])
    w = spec.get("output_width") or spec.get("width")
    h = spec.get("output_height") or spec.get("height")
    if w and h:
        return max(0.01, (int(w) * int(h)) / 1_000_000.0)
    return float(DEFAULTS["ref_megapixels"])


REF_MP_MAX = 16.0   # ResizeImageMaskNode's own ceiling; a bigger picture is read at 16 MP (the output keeps its size)


def _resize_chain(g, index, image_name, megapixels, multiple, method):
    """Load Image N and the two resize steps from the user's edit workflow."""
    load_id = "load_%d" % index
    a_id = "rs_px_%d" % index
    b_id = "rs_mult_%d" % index
    g[load_id] = _node("LoadImage", {"image": image_name}, "Load Image %d (<Picture %d>)" % (index, index))
    g[a_id] = _node("ResizeImageMaskNode", {
        "resize_type": "scale total pixels",
        "resize_type.megapixels": min(REF_MP_MAX, max(0.01, float(megapixels))),
        "scale_method": method,
        "input": [load_id, 0],
    }, "Resize to megapixels")
    g[b_id] = _node("ResizeImageMaskNode", {
        "resize_type": "scale to multiple",
        "resize_type.multiple": int(multiple),
        "scale_method": method,
        "input": [a_id, 0],
    }, "Resize to multiple")
    return [b_id, 0]


def _sample_and_save(g, positive, width_in, height_in, image_links, seed, steps, cfg, sampler, scheduler, denoise, prefix, output_size=None):
    cond_in = {
        "prompt": positive,
        "clip": ["clip", 0],
        "vae": ["vae", 0],
    }
    if width_in is None:
        g["size"] = _node("GetImageSize", {"image": image_links[0]}, "Input image size")
        cond_in["width"] = ["size", 0]
        cond_in["height"] = ["size", 1]
    else:
        cond_in["width"] = int(width_in)
        cond_in["height"] = int(height_in)
    for i, link in enumerate(image_links, start=1):
        cond_in["images.image_%d" % i] = link
    g["cond"] = _node("MiniMaxH3ImageConditioning", cond_in, "MiniMax H3 Image Conditioning")
    g["neg"] = _node("ConditioningZeroOut", {"conditioning": ["cond", 0]}, "Conditioning Zero Out")
    if sampler == LCM_DETAIL:
        # Kijai's MiniMax H3 Image settings (via the 20 MP community workflow): LCM with the per-step noise easing
        # from 1.10 to 0.90 over the run, simple scheduler. Same model, steps and CFG as the plain sampler.
        g["noise"] = _node("RandomNoise", {"noise_seed": int(seed)}, "Noise")
        g["guider"] = _node("CFGGuider", {"model": ["unet", 0], "positive": ["cond", 0], "negative": ["neg", 0],
                                          "cfg": float(cfg)}, "Guider")
        g["lcm"] = _node("SamplerLCM", {"s_noise": 1.10, "s_noise_end": 0.90, "noise_clip_std": 0.0}, "Sampler LCM (detail)")
        g["sigmas"] = _node("BasicScheduler", {"model": ["unet", 0], "scheduler": scheduler or "simple",
                                               "steps": int(steps), "denoise": float(denoise)}, "Scheduler")
        g["sampler"] = _node("SamplerCustomAdvanced", {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["lcm", 0],
                                                       "sigmas": ["sigmas", 0], "latent_image": ["cond", 1]}, "Sampler")
    else:
        g["sampler"] = _node("KSampler", {
            "seed": int(seed),
            "steps": int(steps),
            "cfg": float(cfg),
            "sampler_name": sampler,
            "scheduler": scheduler,
            "denoise": float(denoise),
            "model": ["unet", 0],
            "positive": ["cond", 0],
            "negative": ["neg", 0],
            "latent_image": ["cond", 1],
        }, "KSampler")
    g["decode"] = _node("VAEDecode", {
        "samples": ["sampler", 0],
        "vae": ["vae", 0],
    }, "VAE Decode")
    save_from = ["decode", 0]
    if output_size and output_size[0] and output_size[1]:
        ow, oh = int(output_size[0]), int(output_size[1])
        if width_in is None or int(width_in) != ow or int(height_in) != oh:
            # Scale/crop-free resize back to the exact original Picture 1 pixel size.
            method = "area"
            if width_in and height_in and (ow * oh) > (int(width_in) * int(height_in)):
                method = "lanczos"
            g["out_scale"] = _node("ImageScale", {
                "upscale_method": method,
                "width": ow,
                "height": oh,
                "crop": "disabled",
                "image": ["decode", 0],
            }, "Restore original size")
            save_from = ["out_scale", 0]
    g["save"] = _node("SaveImage", {
        "filename_prefix": prefix,
        "images": save_from,
    }, "Save Image")


def merge_cfg(overrides=None):
    cfg = dict(DEFAULTS)
    if overrides:
        for k, v in overrides.items():
            if v is not None and v != "":
                cfg[k] = v
    return cfg



def build_pe_only(prompt, mode="t2i", images=None, pe_mode="fast", models=None, free_memory=True,
                   pe_system_extra="", pe_task_extra="", pe_system_override="", pe_tokens=None):
    """A Prompt-Enhancer-only graph: no UNET or VAE, so ComfyUI can free the PE model before the image run."""
    import pe as PE
    if not (prompt or "").strip():
        raise ValueError("Write a prompt first.")
    mode = "edit" if mode == "edit" else "t2i"
    cfg = merge_cfg(models)
    st = PE.settings(pe_mode)
    pe_clip = cfg.get("pe_clip_name") or PE.QUALITY_PE_DEFAULT
    pe_clip_type = cfg.get("pe_clip_type") or "minimax"
    g = {}
    if mode == "t2i":
        system, task = st["system_t2i"], st["task_t2i"]
        max_length = st.get("max_length") or st.get("max_length_t2i")
        links = []
    else:
        if not images:
            raise ValueError("Image Edit needs at least one reference image.")
        system, task = st["system_edit"], st["task_edit"]
        max_length = st.get("max_length") or st.get("max_length_edit")
        used = list(images)[: int(st.get("max_images") or 16)]
        links = []
        for i, name in enumerate(used, start=1):
            links.append(_resize_chain(
                g, i, name,
                st["ref_megapixels"], cfg["ref_multiple"], cfg["scale_method"],
            ))
    g["user_prompt"] = _node("PrimitiveStringMultiline", {"value": prompt}, "Text (user_prompt)")
    max_length, thinking = PE.resolve_tokens(st["id"], mode, pe_tokens, prompt, len(links))
    _pe_chain(
        g, "user_prompt", True, links,
        system, task, max_length, pe_clip,
        thinking=thinking, mtp=st["mtp"], temperature=st["temperature"], top_k=st["top_k"],
        pe_clip_type=pe_clip_type, standalone=True, free_memory=bool(free_memory),
        pe_system_extra=pe_system_extra, pe_task_extra=pe_task_extra, pe_system_override=pe_system_override,
    )
    return g, {
        "mode": mode,
        "pe_mode": st["id"],
        "pe_clip_name": pe_clip,
        "pe_thinking": thinking,
        "pe_max_length": int(max_length),
        "pe_images": len(links),
        "pe_ref_megapixels": st["ref_megapixels"],
    }


def build_t2i(prompt, resolution="2048x2048", seed=None, steps=None, pe=False, models=None, prefix="ComfyStudio/t2i", input_size=None):
    if not (prompt or "").strip():
        raise ValueError("Write a prompt first.")
    cfg = merge_cfg(models)
    spec = resolve_size(resolution, mode="t2i", input_size=input_size)
    if spec.get("match_input") or spec.get("picture1"):
        raise ValueError("Match Picture 1 is only available in Image Edit.")
    w, h = spec["width"], spec["height"]
    seed = new_seed() if seed in (None, "", 0, "random") else int(seed)
    steps = int(steps if steps not in (None, "") else cfg["steps_t2i"])
    g = {}
    _loaders(g, cfg)
    g["user_prompt"] = _node("PrimitiveStringMultiline", {"value": prompt}, "Text (user_prompt)")
    if pe:
        positive = _pe_chain(
            g, "user_prompt", True, [],
            "pe_t2i_system.txt", "pe_t2i_task.txt", 8192, cfg["pe_clip_name"],
        )
    else:
        positive = ["user_prompt", 0]
    _sample_and_save(
        g, positive, w, h, [],
        seed, steps, cfg["cfg"], cfg["sampler_name"], cfg["scheduler"], cfg["denoise"], prefix,
        output_size=(spec.get("output_width"), spec.get("output_height")),
    )
    return g, {"seed": seed, "steps": steps, "width": w, "height": h,
               "output_width": spec.get("output_width") or w, "output_height": spec.get("output_height") or h,
               "pe": bool(pe), "mode": "t2i"}


def build_edit(prompt, images, resolution="2048x2048", seed=None, steps=None, pe=False, models=None, prefix="ComfyStudio/edit", input_size=None, ref_mode=False):
    """images: list of LoadImage names (ComfyStudio/foo.png), index 0 is Picture 1 (or first reference in Ref mode)."""
    if not (prompt or "").strip():
        raise ValueError("Write a prompt first.")
    if not images:
        raise ValueError("Add at least one reference image." if ref_mode else "Image Edit needs a main image (Picture 1).")
    if len(images) > 16:
        raise ValueError("MiniMax H3 Image takes at most 16 reference images.")
    cfg = merge_cfg(models)
    spec = resolve_size(resolution, mode="edit", input_size=input_size)
    w, h = spec["width"], spec["height"]
    if ref_mode and not (w and h):
        raise ValueError("Reference mode needs an explicit width and height.")
    seed = new_seed() if seed in (None, "", 0, "random") else int(seed)
    steps = int(steps if steps not in (None, "") else cfg["steps_edit"])
    g = {}
    _loaders(g, cfg)
    ref_mp = target_ref_megapixels(spec, input_size)
    links = []
    for i, name in enumerate(images, start=1):
        links.append(_resize_chain(
            g, i, name,
            ref_mp, cfg["ref_multiple"], cfg["scale_method"],
        ))
    g["user_prompt"] = _node("PrimitiveStringMultiline", {"value": prompt}, "Text (user_prompt)")
    if pe:
        positive = _pe_chain(
            g, "user_prompt", True, links,
            "pe_edit_system.txt", "pe_edit_task.txt", 512, cfg["pe_clip_name"],
        )
    else:
        positive = ["user_prompt", 0]
    _sample_and_save(
        g, positive, w, h, links,
        seed, steps, cfg["cfg"], cfg["sampler_name"], cfg["scheduler"], cfg["denoise"], prefix,
        output_size=(spec.get("output_width"), spec.get("output_height")),
    )
    return g, {
        "seed": seed, "steps": steps, "width": w, "height": h,
        "output_width": spec.get("output_width") or w,
        "output_height": spec.get("output_height") or h,
        "orig_width": spec.get("orig_width"),
        "orig_height": spec.get("orig_height"),
        "restore_original": bool(spec.get("restore")),
        "match_input": False,
        "keep_input": bool(spec.get("keep_input") or spec.get("original")),
        "picture1": bool(spec.get("picture1") or spec.get("original") or spec.get("keep_input")),
        "original": bool(spec.get("original")),
        "pe": bool(pe), "mode": "edit",
        "n_images": len(images),
        "ref_mode": bool(ref_mode),
        "ref_megapixels": ref_mp,
    }