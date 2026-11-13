"""Model loading for the AIO node, with the memory order an 8 GB / 32 GB PC needs.

In a graph the loader nodes are cached by ComfyUI. Inside one node we keep our own references,
or every queue would re-read 12 GB from disk. Holding a reference only avoids the disk read;
ComfyUI's model management still moves weights on and off the GPU as usual.

H3 is RAM-bound: the 32B text encoder (~15 GB) and the video model (~12.5 GB) do not fit in
32 GB together with Windows. So the text encoder is loaded only when a prompt changed, every
prompt of the run is encoded in one go, and it is released before the video model is needed.
The encoded prompts are small and kept (_COND), so changing only the seed, steps or speed
settings never reloads the text encoder.
"""

import gc
import hashlib
import logging
import os

import psutil
import torch

import comfy.model_management as mm
import folder_paths

from . import engine

log = logging.getLogger("MiniMax-H3-AIO")
GB = 1024 ** 3

_DIT = {}
_VAE = {}
_TE = {}
_COND = {}
_COND_LIMIT = 24


def _size(kind, name):
    path = folder_paths.get_full_path(kind, name)
    return os.path.getsize(path) if path else 0


file_size = _size


def free_now():
    _free()


def ram_guard(need_bytes, what, notes):
    """Before a big load: if RAM is short, release everything ComfyUI holds (not our cached prompts) and say so
    when it still will not fit, instead of letting Windows page and stall."""
    if psutil.virtual_memory().available >= need_bytes + 3 * GB:
        return
    log.info("[H3 AIO] low RAM before loading %s - freeing cached models", what)
    if _TE:
        _TE.clear()
    _free()
    free = psutil.virtual_memory().available
    if free < need_bytes + 1 * GB:
        notes.append(f"low RAM: {free / GB:.1f} GB free for {what} (~{need_bytes / GB:.1f} GB) - expect pagefile "
                     f"slowdowns; close other apps or pick a smaller resolution")


def after_run(notes):
    """Keep the video model for the next run only when the PC has room for it."""
    if psutil.virtual_memory().available < 4 * GB:
        drop_video_model()
        notes.append("RAM was tight - released the video model after the run")


def _free():
    mm.unload_all_models()
    gc.collect()
    mm.soft_empty_cache()


def drop_video_model():
    if _DIT:
        log.info("[H3 AIO] releasing %s from RAM", _DIT["name"])
        _DIT.clear()
        _free()


def video_model(name):
    if _DIT.get("name") != name:
        drop_video_model()
        log.info("[H3 AIO] loading %s", name)
        _DIT["model"] = engine.call1("UNETLoader", unet_name=name, weight_dtype="default")
        _DIT["name"] = name
    return _DIT["model"]


def vae(name):
    if name not in _VAE:
        _VAE[name] = engine.call1("VAELoader", vae_name=name)
    return _VAE[name]


def text_encoder(name, keep):
    if _TE.get("name") == name:
        return _TE["clip"]
    _TE.clear()
    need = _size("text_encoders", name)
    if _DIT and psutil.virtual_memory().available < need + 4 * GB:
        # Encoding with the video model still in RAM is what pushes a 32 GB PC into the pagefile.
        drop_video_model()
    log.info("[H3 AIO] loading text encoder %s", name)
    clip = engine.call1("CLIPLoader", clip_name=name, type="minimax", device="default")
    if keep:
        _TE.update(name=name, clip=clip)
    return clip


def release_text_encoder(keep):
    if not keep:
        _TE.clear()
        _free()


def tensor_digest(*items):
    h = hashlib.sha1()
    for item in items:
        if item is None:
            h.update(b"-")
        elif isinstance(item, torch.Tensor):
            h.update(str(tuple(item.shape)).encode())
            h.update(item.detach().to("cpu", torch.float16).contiguous().numpy().tobytes())
        elif isinstance(item, dict) and "waveform" in item:
            h.update(str(item["sample_rate"]).encode())
            h.update(item["waveform"].detach().to("cpu", torch.float16).contiguous().numpy().tobytes())
        else:
            h.update(repr(item).encode())
    return h.hexdigest()


def cached_conditioning(key):
    return _COND.get(key)


def store_conditioning(key, cond):
    if len(_COND) >= _COND_LIMIT:
        _COND.pop(next(iter(_COND)))
    _COND[key] = cond
