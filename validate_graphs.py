"""Validate API-format MiniMax H3 graphs against ComfyUI /object_info. Never posts /prompt."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graphs

COMFY = "http://127.0.0.1:8188"

# Inputs ComfyUI injects itself; we must not require them in the API prompt.
HIDDEN_OK = {"unique_id", "prompt", "extra_pnginfo", "dynprompt", "instance_id"}

# Autogrow / dynamic-combo child keys (images.image_1, resize_type.megapixels, ...)
def _base_name(key: str) -> str:
    return key.split(".", 1)[0]


def _combo_values(spec):
    """Return the allowed list for a COMBO / list-of-strings input, or None if not a combo."""
    if not isinstance(spec, list) or not spec:
        return None
    first = spec[0]
    if isinstance(first, list) and first and all(isinstance(x, str) for x in first):
        return first
    return None


def _dyn_keys(spec):
    """Return (kind, option_keys_or_None) for dynamic widgets."""
    if not isinstance(spec, list) or len(spec) < 2 or not isinstance(spec[1], dict):
        return None, None
    meta = spec[1]
    typ = spec[0]
    if typ == "COMFY_DYNAMICCOMBO_V3" or (isinstance(typ, str) and "DYNAMICCOMBO" in typ):
        opts = meta.get("options") or []
        keys = [o.get("key") for o in opts if isinstance(o, dict) and "key" in o]
        return "dyncombo", keys
    if typ == "COMFY_AUTOGROW_V3" or (isinstance(typ, str) and "AUTOGROW" in typ):
        return "autogrow", meta
    return None, None


def fetch_object_info(timeout=60):
    with urllib.request.urlopen(COMFY + "/object_info", timeout=timeout) as f:
        return json.loads(f.read())


def validate_graph(graph: dict, oi: dict, input_root: str | None = None) -> list[str]:
    """Return a list of human-readable problems (empty means OK)."""
    problems = []
    if not graph:
        return ["The graph is empty."]
    ids = set(graph)

    for nid, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            problems.append("Node %s is not a valid API node." % nid)
            continue
        ctype = node["class_type"]
        info = oi.get(ctype)
        if not info:
            problems.append("Node %s: ComfyUI has no node type %s." % (nid, ctype))
            continue
        inputs = node.get("inputs") or {}
        schema = info.get("input") or {}
        required = schema.get("required") or {}
        optional = schema.get("optional") or {}
        hidden = set(schema.get("hidden") or {})
        known = dict(required)
        known.update(optional)

        # required present (hidden ones are injected by ComfyUI)
        for name in required:
            if name in HIDDEN_OK or name in hidden:
                continue
            # present either as itself or as a dotted child of a dyn combo / autogrow
            if name in inputs:
                continue
            if any(_base_name(k) == name for k in inputs):
                continue
            spec = required[name]
            if (isinstance(spec, (list, tuple)) and spec and spec[0] == "COMFY_AUTOGROW_V3"
                    and isinstance(spec[1] if len(spec) > 1 else None, dict)
                    and int(((spec[1].get("template") or {}).get("min")) or 0) == 0):
                continue  # an autogrow group with min 0 may be left empty (ComfyUI accepts it)
            problems.append("Node %s (%s) is missing required input %s." % (nid, ctype, name))

        for key, val in inputs.items():
            base = _base_name(key)
            if key in hidden or key in HIDDEN_OK:
                continue
            if base not in known and key not in known:
                problems.append("Node %s (%s) has unexpected input %s." % (nid, ctype, key))
                continue
            spec = known.get(key) or known.get(base)

            # links
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], (str, int)) and isinstance(val[1], int):
                src = str(val[0])
                if src not in ids:
                    problems.append("Node %s input %s points at missing node %s." % (nid, key, src))
                continue

            # combo membership
            combo = _combo_values(spec) if spec else None
            if combo is not None:
                # LoadImage's list is the current input folder; a newly uploaded file may not be in it yet.
                if ctype in ("LoadImage", "LoadImageMask") and key == "image":
                    if not isinstance(val, str) or not val.strip():
                        problems.append("Node %s: choose an image to load." % nid)
                    elif input_root:
                        # Allow forward or backslash subpaths under the input root.
                        rel = val.replace("/", os.sep)
                        full = os.path.join(input_root, rel)
                        if not os.path.isfile(full):
                            problems.append("Node %s: image %s was not found in the ComfyUI input folder." % (nid, val))
                    continue
                if val not in combo:
                    problems.append(
                        "Node %s (%s) input %s is %r, which is not an option ComfyUI currently lists."
                        % (nid, ctype, key, val)
                    )
                continue

            kind, extra = _dyn_keys(spec) if spec else (None, None)
            if kind == "dyncombo":
                if key == base:
                    if extra and val not in extra:
                        problems.append(
                            "Node %s (%s) input %s is %r; expected one of: %s."
                            % (nid, ctype, key, val, ", ".join(extra))
                        )
                # child keys like resize_type.megapixels / sampling_mode.temperature are values; type-check loosely
                continue
            if kind == "autogrow":
                # images.image_1 etc. must be links; but a template slot typed "*" (any) or a plain value type
                # (STRING / INT / FLOAT / BOOLEAN / a list of options) also takes a typed-in value, e.g. CreateList
                # with its texts written straight into the node
                if not (isinstance(val, list) and len(val) == 2):
                    tpl_type = None
                    try:
                        tpl = ((spec[1] or {}).get("template") or {}).get("input") or {}
                        first = next(iter((tpl.get("required") or tpl.get("optional") or {}).values()))
                        tpl_type = first[0] if isinstance(first, (list, tuple)) and first else None
                    except (StopIteration, AttributeError, IndexError, TypeError):
                        tpl_type = None
                    value_type = isinstance(tpl_type, str) and all(t.strip() in ("STRING", "INT", "FLOAT", "BOOLEAN")
                                                                   for t in tpl_type.split(","))   # "FLOAT,INT,BOOLEAN"
                    if not (tpl_type == "*" or value_type or isinstance(tpl_type, list)):
                        problems.append("Node %s input %s should be a connection to another node." % (nid, key))
                continue

            # primitive type checks when a schema exists
            if isinstance(spec, list) and spec:
                typ = spec[0]
                if typ == "INT" and not isinstance(val, int):
                    problems.append("Node %s input %s should be a whole number, got %r." % (nid, key, val))
                elif typ == "FLOAT" and not isinstance(val, (int, float)):
                    problems.append("Node %s input %s should be a number, got %r." % (nid, key, val))
                elif typ == "BOOLEAN" and not isinstance(val, bool):
                    problems.append("Node %s input %s should be true or false, got %r." % (nid, key, val))
                elif typ == "STRING" and not isinstance(val, str):
                    problems.append("Node %s input %s should be text, got %r." % (nid, key, type(val).__name__))
    return problems


def build_all_samples():
    """Representative graphs the app will actually submit (not posted here)."""
    models = None
    cases = []
    g, m = graphs.build_t2i("a red ceramic mug on a wooden table, soft morning light", "2048x2048", seed=1, pe=False, models=models)
    cases.append(("t2i 2048 PE off (default)", g, m))
    g, m = graphs.build_t2i("a red ceramic mug on a wooden table", "2048x2048", seed=1, pe=True, models=models)
    cases.append(("t2i 2048 PE on", g, m))
    g, m = graphs.build_edit("keep everything in <Picture 1>, slightly warmer evening light", ["ComfyStudio/sample.png"], "2048x2048", seed=2, pe=False, models=models)
    cases.append(("edit 1 image 2048 PE off (default)", g, m))
    g, m = graphs.build_edit("use <Picture 1> and <Picture 2>", ["ComfyStudio/a.png", "ComfyStudio/b.png"], "match", seed=3, pe=True, models=models, input_size=(1024, 1536))
    cases.append(("edit 2 images match PE on", g, m))
    # 16-ref skeleton (PE off) to confirm image slots 1..16
    names = ["ComfyStudio/im%d.png" % i for i in range(1, 17)]
    g, m = graphs.build_edit("refs " + " ".join("<Picture %d>" % i for i in range(1, 17)), names, "2048x2048", seed=4, pe=False)
    cases.append(("edit 16 images PE off", g, m))
    # Keep input resolution: explicit snapped WxH, not GetImageSize / match
    g, m = graphs.build_edit("keep size", ["ComfyStudio/sample.png"], "keep", seed=5, pe=False, input_size=(832, 1088))
    cases.append(("edit keep input 832x1088", g, m))
    g, m = graphs.build_t2i("custom mp", "custom:1:1:4", seed=6, pe=False)
    cases.append(("t2i custom 4mp 1:1", g, m))
    import pe as PE
    fast_name, found = PE.pick_fast_clip([])
    # Use the on-disk default candidates; validation checks the filename against CLIPLoader combo.
    pe_models_fast = {"pe_clip_name": r"Qwen\qwen3-vl-4b-heretic_fp8_e4m3fn.safetensors"}
    g, m = graphs.build_pe_only("a red ceramic mug", mode="t2i", pe_mode="fast", models=pe_models_fast)
    cases.append(("PE only t2i Fast (no UNET)", g, m))
    g, m = graphs.build_pe_only("a red ceramic mug", mode="t2i", pe_mode="quality")
    cases.append(("PE only t2i Quality (no UNET)", g, m))
    g, m = graphs.build_pe_only("warmer light on <Picture 1>", mode="edit", images=["ComfyStudio/a.png", "ComfyStudio/b.png"], pe_mode="fast", models=pe_models_fast)
    cases.append(("PE only edit Fast 2 imgs", g, m))
    g, m = graphs.build_pe_only("warmer light on <Picture 1>", mode="edit", images=["ComfyStudio/a.png"], pe_mode="quality")
    cases.append(("PE only edit Quality", g, m))
    return cases


def main():
    print("Fetching /object_info ...")
    oi = fetch_object_info()
    print("Node types in ComfyUI:", len(oi))
    input_root = r"E:\Saved\input"
    all_ok = True
    for title, graph, meta in build_all_samples():
        # skip missing-file complaints for synthetic LoadImage names by not passing input_root
        problems = validate_graph(graph, oi, input_root=None)
        # still confirm default model filenames are in the combos
        extra = []
        for nid, node in graph.items():
            if node["class_type"] in ("UNETLoader", "CLIPLoader", "VAELoader"):
                pass
        status = "OK" if not problems else "FAIL"
        if problems:
            all_ok = False
        print("=" * 60)
        print(status, title, "nodes=", len(graph), "meta=", meta)
        for p in problems:
            print("  -", p)
    # Default model presence
    print("=" * 60)
    print("Default model filenames:")
    checks = [
        ("UNETLoader", "unet_name", graphs.DEFAULTS["unet_name"]),
        ("CLIPLoader", "clip_name", graphs.DEFAULTS["clip_name"]),
        ("CLIPLoader", "clip_name", graphs.DEFAULTS["pe_clip_name"]),
        ("CLIPLoader", "clip_name", r"Qwen\qwen3-vl-4b-heretic_fp8_e4m3fn.safetensors"),
        ("CLIPLoader", "type", graphs.DEFAULTS["clip_type"]),
        ("VAELoader", "vae_name", graphs.DEFAULTS["vae_name"]),
        ("KSampler", "sampler_name", graphs.DEFAULTS["sampler_name"]),
        ("KSampler", "scheduler", graphs.DEFAULTS["scheduler"]),
        ("UNETLoader", "weight_dtype", graphs.DEFAULTS["weight_dtype"]),
        ("ResizeImageMaskNode", "scale_method", graphs.DEFAULTS["scale_method"]),
    ]
    for ctype, key, val in checks:
        spec = ((oi.get(ctype) or {}).get("input") or {}).get("required", {}).get(key)
        if spec is None:
            spec = ((oi.get(ctype) or {}).get("input") or {}).get("optional", {}).get(key)
        combo = _combo_values(spec)
        kind, extra = _dyn_keys(spec) if spec else (None, None)
        ok = (combo is not None and val in combo) or (kind == "dyncombo" and extra and val in extra) or (combo is None and kind is None)
        if not ok and key == "sampler_name" and val == graphs.LCM_DETAIL:
            ok = "SamplerLCM" in oi     # the built-in recipe runs as its own SamplerLCM node, not a KSampler choice
        if not ok:
            all_ok = False
        print(("  OK " if ok else "  MISSING "), ctype, key, "=", val)

    # Resize type option keys
    spec = ((oi.get("ResizeImageMaskNode") or {}).get("input") or {}).get("required", {}).get("resize_type")
    kind, keys = _dyn_keys(spec)
    print("  ResizeImageMaskNode resize_type keys:", keys)
    for need in ("scale total pixels", "scale to multiple"):
        ok = keys and need in keys
        if not ok:
            all_ok = False
        print(("  OK " if ok else "  MISSING "), "resize_type", need)

    tg = ((oi.get("TextGenerateV2") or {}).get("input") or {}).get("required", {}).get("sampling_mode")
    kind, keys = _dyn_keys(tg)
    print("  TextGenerateV2 sampling_mode keys:", keys)
    if not (keys and "on" in keys):
        all_ok = False
        print("  MISSING  sampling_mode on")
    else:
        print("  OK  sampling_mode on")

    print("=" * 60)
    print("RESULT:", "ALL OK" if all_ok else "ISSUES FOUND")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())