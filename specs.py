"""What this PC can run: video sizes / lengths, image megapixels, and post-upscalers, by graphics memory.

Numbers marked "tested" were measured on the owner's 8 GB RTX 4060 Ti / 31 GB RAM (PC Launcher guide 4.12,
2026-11-06 / -18). Other tiers are estimates scaled from those runs; the UI says so. A tier applies when the
PC's VRAM is at least the tier's number. Low system RAM (< 24 GB) trims the longest options.
"""
from __future__ import annotations

import os

TESTED_VRAM = 8

# per engine kind: vram tier -> {resolution: max seconds in one run}
VIDEO_TIERS = {
    "h3": {4: {"480p": 3}, 6: {"480p": 5}, 8: {"480p": 10, "720p": 5},
           12: {"480p": 10, "720p": 10, "832p": 5}, 16: {"720p": 10, "832p": 10, "1080p": 5},
           24: {"720p": 15, "832p": 15, "1080p": 10}},
    "h3_fast": {8: {"720p": 8, "832p": 5}, 12: {"720p": 10, "832p": 10, "1080p": 5},
                16: {"832p": 10, "1080p": 10}, 24: {"1080p": 15}},
    "h3_long": {6: {"480p": 15}, 8: {"480p": 30, "576p": 20}, 12: {"576p": 35, "720p": 25},
                16: {"720p": 49, "832p": 35}, 24: {"720p": 70, "1080p": 35}},
    "ltx": {4: {"480p": 3}, 6: {"480p": 5}, 8: {"480p": 10, "720p": 10, "1080p": 3},
            12: {"480p": 10, "720p": 10, "1080p": 5}, 16: {"720p": 10, "1080p": 10},
            24: {"1080p": 10, "1440p": 5}},
    "generic": {4: {"480p": 3}, 6: {"480p": 5}, 8: {"480p": 10, "720p": 5}, 12: {"480p": 10, "720p": 10},
                16: {"720p": 10, "1080p": 5}, 24: {"720p": 10, "1080p": 10}},
}
# MiniMax H3 long video: seconds per clip the planner may use, by tier (max 7 clips per run)
H3_CLIP = {4: 5, 8: 5, 12: 7, 16: 10, 24: 15}
# image megapixel ceiling by tier (the MP menu hides bigger sizes)
IMAGE_MP = {4: 2, 6: 4, 8: 20, 12: 20, 16: 20, 24: 20}   # 8 GB: 20 MP tested (5120x3840, 91 s, MiniMax H3 image)
RES_ORDER = ["480p", "576p", "720p", "768p", "832p", "1080p", "1440p", "2160p"]


def _tier(table, vram):
    keys = sorted(k for k in table if k <= (vram or 0))
    return table[keys[-1]] if keys else table[min(table)]


def engine_kind(card):
    cid = str(card.get("id") or "")
    if card.get("aio_mode", "").startswith("Long") or card.get("task") == "long":
        return "h3_long"
    if card.get("family") == "MiniMax H3" and card.get("media") == "video":
        return "h3_fast" if "Fast" in (card.get("aio_mode") or "") or "fast" in cid else "h3"
    if card.get("family") == "LTX Video" or cid.startswith("ltx"):
        return "ltx"
    return "generic"


def effective(card, hw):
    """Sizes and lengths this PC can use for a video engine (card values untouched for image engines)."""
    if card.get("media") != "video":
        return {}
    vram, ram = float(hw.get("vram_gb") or 0), float(hw.get("ram_gb") or 0)
    kind = engine_kind(card)
    tier = dict(_tier(VIDEO_TIERS[kind], vram))
    # Only offer sizes the engine itself can draw.
    native = card.get("all_resolutions") or card.get("resolutions") or list(tier)
    if card.get("size_rule"):
        native = list((card["size_rule"].get("mp") or {}).keys()) or native
    tier = {r: s for r, s in tier.items() if r in native} or {native[0]: 5}
    if ram and ram < 24:
        tier = {r: max(3, int(s * 0.6)) for r, s in tier.items()}
    out = {"resolutions": sorted(tier, key=lambda r: RES_ORDER.index(r) if r in RES_ORDER else 99),
           "limits": tier,
           "basis": "tested" if int(vram) == TESTED_VRAM else "estimated",
           "kind": kind}
    if kind == "h3_long":
        clip = _tier(H3_CLIP, vram)
        out["clip_seconds"] = clip
        out["limits"] = {r: min(s, clip * 7) for r, s in tier.items()}
    return out


def image_mp_max(hw):
    return _tier(IMAGE_MP, float(hw.get("vram_gb") or 0))


# ------------------------------------------------------------------ upscalers for finished videos
TARGETS = [("1080p", 1080), ("1440p", 1440), ("4K", 2160)]


def upscalers(hw, oi, models_root):
    """Upscale methods this PC can use, each with the targets it may reach. Only models already on disk."""
    vram = float(hw.get("vram_gb") or 0)
    gpu = (hw.get("gpu") or "").upper()
    out = []
    if "RTXVideoSuperResolution" in (oi or {}) and "RTX" in gpu:
        out.append({"id": "rtx", "label": "Fast (NVIDIA RTX Video Super Resolution)", "speed": "fast",
                    "note": "Very quick and light. Good for clean footage.",
                    "targets": [t for t, _ in TARGETS if vram >= 4]})
    esr_dir = os.path.join(models_root, "upscale_models")
    esr = sorted(f for f in (os.listdir(esr_dir) if os.path.isdir(esr_dir) else []) if f.lower().endswith((".pth", ".safetensors")))
    if "ImageUpscaleWithModel" in (oi or {}) and esr and vram >= 4:
        out.append({"id": "esrgan", "label": "Sharp (%s)" % os.path.splitext(esr[0])[0], "speed": "medium",
                    "note": "Frame-by-frame detail. Slower on long clips.", "model": esr[0],
                    "targets": ["1080p", "1440p"] if vram >= 6 else ["1080p"]})
    fv_dir = os.path.join(models_root, "FlashVSR")
    fv = set(os.listdir(fv_dir)) if os.path.isdir(fv_dir) else set()
    plus = {"dit": "turbo_dit_plus.safetensors", "lq_proj": "lq_proj_in_plus_turbo.safetensors",
            "prompt": "posi_prompt_plus.pth", "tcd": "TCDecoder_plus.ckpt"}
    if "FlashVSR_SM_Sampler" in (oi or {}) and all(f in fv for f in plus.values()) and vram >= 8:
        out.append({"id": "flashvsr_plus", "label": "Detail, fast (FlashVSR+ Turbo)", "speed": "medium", "files": plus,
                    "note": "New one-step diffusion upscaler: SeedVR2-like detail, much quicker. Limits estimated for this PC.",
                    "targets": ["1080p"] + (["1440p"] if vram >= 12 else []) + (["4K"] if vram >= 24 else [])})
    svr_dir = next((os.path.join(models_root, d) for d in ("SEEDVR2", "seedvr2") if os.path.isdir(os.path.join(models_root, d))), "")
    files = set(os.listdir(svr_dir)) if svr_dir else set()
    if "SeedVR2VideoUpscaler" in (oi or {}) and "ema_vae_fp16.safetensors" in files:
        small = next((f for f in ("seedvr2_ema_3b_fp8_e4m3fn.safetensors", "seedvr2_ema_3b-Q8_0.gguf", "seedvr2_ema_3b-Q4_K_M.gguf") if f in files), None)
        big = next((f for f in ("seedvr2_ema_7b_sharp-Q4_K_M.gguf", "seedvr2_ema_7b-Q4_K_M.gguf") if f in files), None)
        if small and vram >= 8:
            out.append({"id": "seedvr2_small", "label": "Best detail (SeedVR2 3B)", "speed": "slow", "model": small,
                        "note": "Real skin and texture detail. Slow: about 8-10 min for 3 s at 1080p on 8 GB.",
                        "targets": ["1080p"] + (["1440p"] if vram >= 12 else [])})
        if big and vram >= 16:
            out.append({"id": "seedvr2_big", "label": "Best detail, sharper (SeedVR2 7B)", "speed": "slow", "model": big,
                        "note": "The strongest SeedVR2 on disk. Needs 16 GB+.",
                        "targets": ["1080p", "1440p"] + (["4K"] if vram >= 24 else [])})
    return out
