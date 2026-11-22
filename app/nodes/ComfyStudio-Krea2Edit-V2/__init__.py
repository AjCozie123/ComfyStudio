"""Krea 2 Edit v2 nodes for Comfy Studio's built-in Krea 2 Edit engine (see NOTICE.md).

Registers comfyui-krea2edit's two nodes under V2 names (Krea2EditModelPatchV2, Krea2EditGroundedEncodeV2), the names
Comfy Studio's Krea 2 Edit workflow uses. Names another pack already registered are left alone."""
import logging

log = logging.getLogger(__name__)
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

try:
    from . import krea2edit as _k
    try:
        import nodes as _core
        _taken = set(getattr(_core, "NODE_CLASS_MAPPINGS", {}) or {})
    except Exception:
        _taken = set()
    for _key, _cls in (_k.NODE_CLASS_MAPPINGS or {}).items():
        _alias = _key + "V2"
        if _alias in _taken:
            continue
        NODE_CLASS_MAPPINGS[_alias] = _cls
        NODE_DISPLAY_NAME_MAPPINGS[_alias] = (_k.NODE_DISPLAY_NAME_MAPPINGS or {}).get(_key, _key) + " V2"
except Exception as e:                      # an older ComfyUI without the Krea 2 model code: say so, break nothing
    log.warning("[ComfyStudio-Krea2Edit-V2] couldn't load (%s). Update ComfyUI to use Krea 2 Edit.", e)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
