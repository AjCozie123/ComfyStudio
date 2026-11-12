"""Workflow registry for Comfy Studio.

Any ComfyUI workflow (UI save or "Export (API)") can be uploaded. This module:
  * converts a UI-format workflow to API format using ComfyUI's /object_info,
  * scans it and recognises the standard parts (prompt, negative, pictures, seed,
    steps, CFG, sampler, size, video length, model loaders, outputs),
  * guesses the task (text to image, edit, text to video, image to video) and
    the model family from the model file names,
  * lists node types this ComfyUI does not have,
  * fills the recognised parts at generate time.

Cards (the mapping) and graphs are plain JSON files:
  workflows/                built-in, shipped with the app (tracked in git)
  data/user_workflows/      uploaded by the user (never touched by updates)
"""
from __future__ import annotations

import copy, json, os, random, re, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_DIR = os.path.join(HERE, "workflows")
USER_DIR = os.path.join(HERE, "data", "user_workflows")

TASKS = {
    "t2i": "Text to image",
    "edit": "Image edit",
    "t2v": "Text to video",
    "i2v": "Image to video",
    "r2v": "Reference to video",
    "v2v": "Video to video",
    "other": "Other",
}
TASK_MEDIA = {"t2i": "image", "edit": "image", "t2v": "video", "i2v": "video", "r2v": "video", "v2v": "video", "other": "image"}

UI_ONLY = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "PrimitiveStringMultiline_UI", "Label (rgthree)",
           "Fast Groups Bypasser (rgthree)", "Fast Groups Muter (rgthree)", "Bookmark (rgthree)"}

IMAGE_OUT = {"SaveImage", "PreviewImage", "Image Save", "SaveImageWebsocket", "ImageSave", "Save Image (WAS)"}
VIDEO_OUT = {"VHS_VideoCombine", "SaveVideo", "SaveWEBM", "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveVideoFile"}
IMAGE_IN = {"LoadImage", "LoadImageMask", "LoadImageOutput", "Load Image (WAS)", "LoadImageFromPath",
            "VHS_LoadImagePath", "Image Load"}
# video / audio loaders and the input that names the file
VIDEO_IN = {"LoadVideo": ("file", "video"), "VHS_LoadVideo": ("video",), "VHS_LoadVideoPath": ("video",),
            "VHS_LoadVideoFFmpeg": ("video",), "VHS_LoadVideoFFmpegPath": ("video",), "LoadVideoPath": ("video", "path")}
AUDIO_IN = {"LoadAudio": ("audio",), "VHS_LoadAudio": ("audio_file",), "VHS_LoadAudioUpload": ("audio",)}
_QUANT_TOKENS = re.compile(r"(?i)(^|[_\-. ])(bf16|fp16|fp32|fp8|fp4|nvfp4|int8|int4|w4a8|w8a8|e4m3fn|e5m2|scaled|convrot|"
                           r"mixed|pruned|gguf|q\d(_k)?(_[msl])?|q\d_\d|awq|gptq|ema|step\d+|v\d+(\.\d+)?_?)(?=$|[_\-. ])")


def pretty_model_name(fname):
    """'Krea2\\z_image_turbo_bf16.safetensors' -> 'Z Image Turbo'. Used to name a model family the app doesn't know."""
    base = re.split(r"[\\/]", str(fname or ""))[-1]
    base = re.sub(r"\.(safetensors|gguf|ckpt|pt|pth|sft|bin)$", "", base, flags=re.I)
    prev = None
    while prev != base:
        prev = base
        base = _QUANT_TOKENS.sub(" ", base)
    words = [w for w in re.split(r"[_\-\s]+", base) if w]
    filler = {"transformer", "diffusion", "model", "models", "unet", "dit", "checkpoint", "weights"}
    while len(words) > 1 and words[-1].lower() in filler:
        words.pop()
    # keep the maker's own capitals (FireRed, LTX) and only lift a lowercase first letter (ideogram4 -> Ideogram4)
    out = " ".join(w[0].upper() + w[1:] for w in words).strip()
    return out[:40] or "Custom"


STRING_SOURCES = ("PrimitiveStringMultiline", "PrimitiveString", "String Literal", "easy string", "Text Multiline",
                  "StringConstant", "StringConstantMultiline", "CR Text", "Text box")

FAMILIES = (
    ("ltx", "LTX Video"), ("wan", "Wan"), ("hunyuan", "Hunyuan"), ("minimax", "MiniMax H3"),
    ("h3_image", "MiniMax H3"), ("krea", "Krea"), ("klein", "Klein"), ("flux", "Flux"), ("qwen_image", "Qwen Image"),
    ("sdxl", "SDXL"), ("pony", "SDXL"), ("illustrious", "SDXL"), ("sd3", "SD3"), ("moss", "Moss"),
    ("cosmos", "Cosmos"), ("mochi", "Mochi"), ("hidream", "HiDream"), ("chroma", "Chroma"), ("lumina", "Lumina"),
)


# ------------------------------------------------------------------ storage
def _ensure_dirs():
    os.makedirs(USER_DIR, exist_ok=True)
    os.makedirs(BUILTIN_DIR, exist_ok=True)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def list_cards():
    _ensure_dirs()
    cards = []
    for src, folder in (("builtin", BUILTIN_DIR), ("user", USER_DIR)):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".card.json"):
                continue
            try:
                card = _read_json(os.path.join(folder, name))
            except (OSError, ValueError):
                continue
            card["source"] = src
            cards.append(card)
    cards.sort(key=lambda c: (c.get("family") or "", c.get("task") or "", c.get("name") or ""))
    return cards


def _paths(card_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", card_id or "")
    if not safe:
        raise ValueError("Bad workflow id.")
    for folder in (USER_DIR, BUILTIN_DIR):
        card = os.path.join(folder, safe + ".card.json")
        if os.path.isfile(card):
            return card, os.path.join(folder, safe + ".graph.json"), folder
    return None, None, None


def get(card_id):
    card_p, graph_p, folder = _paths(card_id)
    if not card_p:
        raise ValueError("That workflow was not found. It may have been deleted.")
    card = _read_json(card_p)
    card["source"] = "builtin" if folder == BUILTIN_DIR else "user"
    return card, _read_json(graph_p)


def save(card, graph):
    _ensure_dirs()
    card = dict(card)
    card["id"] = re.sub(r"[^A-Za-z0-9_-]", "", card.get("id") or "") or ("wf_" + uuid.uuid4().hex[:10])
    card["saved"] = time.time()
    card.pop("source", None)
    _write_json(os.path.join(USER_DIR, card["id"] + ".graph.json"), graph)
    _write_json(os.path.join(USER_DIR, card["id"] + ".card.json"), card)
    return card


def delete(card_id):
    card_p, graph_p, folder = _paths(card_id)
    if not card_p:
        raise ValueError("That workflow was not found.")
    if folder != USER_DIR:
        raise ValueError("Built-in workflows can't be deleted.")
    for p in (card_p, graph_p):
        try:
            os.remove(p)
        except OSError:
            pass


# ------------------------------------------------------------------ format conversion
def is_api_format(data):
    if not isinstance(data, dict) or not data:
        return False
    if "nodes" in data and "links" in data:
        return False
    return all(isinstance(v, dict) and "class_type" in v for v in data.values() if isinstance(v, dict)) and \
        any(isinstance(v, dict) and "class_type" in v for v in data.values())


def _input_specs(oi, cls):
    info = (oi or {}).get(cls) or {}
    inp = info.get("input") or {}
    order = info.get("input_order") or {}
    out = []
    for group in ("required", "optional"):
        specs = inp.get(group) or {}
        names = order.get(group) or list(specs.keys())
        for name in names:
            if name in specs:
                out.append((name, specs[name]))
    return out


def _is_widget(spec):
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    t = spec[0]
    if isinstance(t, list):
        return True  # combo list
    if t in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3"):
        opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        return not opts.get("forceInput")  # forceInput values are sockets, not widgets
    return False


def _extra_widget_count(name, spec, seed_by_name=True):
    opts = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    n = 0
    if opts.get("control_after_generate") or (seed_by_name and spec and spec[0] == "INT" and name in ("seed", "noise_seed")):
        n += 1
    if opts.get("image_upload") or opts.get("video_upload") or opts.get("audio_upload"):
        n += 1
    return n


def _sub_specs(spec):
    """Ordered (name, spec) pairs of a dynamic-combo option's inputs."""
    out = []
    for group in ("required", "optional"):
        for k, v in ((spec or {}).get(group) or {}).items():
            out.append((k, v))
    return out


def _consume(specs, vals, seed_by_name):
    """Walk widget specs over the saved values. Returns (inputs, consumed)."""
    inputs, k = {}, 0
    for name, spec in specs:
        if isinstance(spec, (list, tuple)) and spec and spec[0] == "COMFY_DYNAMICCOMBO_V3":
            key = vals[k] if k < len(vals) else None
            if key is not None:
                inputs[name] = key
            k += 1
            opts = (spec[1] or {}).get("options") if len(spec) > 1 and isinstance(spec[1], dict) else []
            chosen = next((o for o in opts or [] if o.get("key") == key), None)
            for sub, sspec in _sub_specs((chosen or {}).get("inputs")):
                if not _is_widget(sspec):
                    continue
                if k < len(vals):
                    inputs[name + "." + sub] = vals[k]
                k += 1 + _extra_widget_count(sub, sspec, seed_by_name=False)
            continue
        if not _is_widget(spec):
            continue
        if k < len(vals):
            inputs[name] = vals[k]
        k += 1 + _extra_widget_count(name, spec, seed_by_name=seed_by_name)
    return inputs, k


def ui_to_api(ui, oi):
    """Convert a ComfyUI UI-format workflow into API format. Returns (graph, warnings)."""
    warnings = []
    if ui.get("definitions", {}).get("subgraphs"):
        warnings.append("This workflow uses subgraphs. Export it with Workflow > Export (API) in ComfyUI for best results.")
    nodes = {str(n["id"]): n for n in ui.get("nodes") or []}
    links = {}
    for l in ui.get("links") or []:
        if isinstance(l, list) and len(l) >= 5:
            links[l[0]] = (str(l[1]), l[2], str(l[3]), l[4])
        elif isinstance(l, dict):
            links[l.get("id")] = (str(l.get("origin_id")), l.get("origin_slot"), str(l.get("target_id")), l.get("target_slot"))

    def resolve(link_id, depth=0):
        """Follow reroutes and bypassed nodes back to a real output."""
        if link_id is None or link_id not in links or depth > 40:
            return None
        src, slot, _t, _s = links[link_id]
        n = nodes.get(src)
        if not n:
            return None
        if n.get("type") == "Reroute":
            ins = n.get("inputs") or []
            return resolve(ins[0].get("link") if ins else None, depth + 1)
        if n.get("mode") == 4:  # bypassed: pass through the first input of the same type
            out_type = ((n.get("outputs") or [{}])[slot] or {}).get("type") if (n.get("outputs") or []) else None
            for i in n.get("inputs") or []:
                if i.get("link") is not None and (out_type is None or i.get("type") == out_type):
                    return resolve(i.get("link"), depth + 1)
            return None
        if n.get("type") == "PrimitiveNode":
            return ("value", (n.get("widgets_values") or [None])[0])
        return [src, slot]

    graph = {}
    for nid, n in nodes.items():
        cls = n.get("type")
        if cls in UI_ONLY or n.get("mode") in (2, 4):
            continue
        if cls not in (oi or {}):
            # Unknown node: keep it so the missing-node check can report it.
            graph[nid] = {"class_type": cls, "inputs": {}, "_meta": {"title": n.get("title") or cls}}
            continue
        specs = _input_specs(oi, cls)
        wv = n.get("widgets_values")
        inputs = {}
        linked = {}
        for i in n.get("inputs") or []:
            if i.get("link") is not None:
                linked[i.get("name")] = i.get("link")
            w = i.get("widget") or {}
            if w.get("name") and i.get("link") is not None:
                linked[w["name"]] = i.get("link")
        if isinstance(wv, dict):
            for name, spec in specs:
                if _is_widget(spec) and name in wv:
                    inputs[name] = wv[name]
        else:
            vals = list(wv or [])
            # Try both seed conventions; keep the one that uses every saved value exactly.
            a, ka = _consume(specs, vals, seed_by_name=True)
            b, kb = _consume(specs, vals, seed_by_name=False)
            inputs.update(b if (kb == len(vals) and ka != len(vals)) else a)
        for name, link_id in linked.items():
            r = resolve(link_id)
            if r is None:
                continue
            if isinstance(r, tuple) and r[0] == "value":
                if r[1] is not None:
                    inputs[name] = r[1]
            else:
                inputs[name] = r
        graph[nid] = {"class_type": cls, "inputs": inputs, "_meta": {"title": n.get("title") or cls}}
    return graph, warnings


_conv = {"tried": False, "fn": None}


def _ui_converter():
    """comfy-cli's UI -> API converter (opens subgraphs). Loaded from wherever comfy-cli is installed on this PC;
    None if it isn't, and the app's own converter is used instead."""
    if _conv["tried"]:
        return _conv["fn"]
    _conv["tried"] = True
    import glob, importlib.util
    paths = []
    spec = importlib.util.find_spec("comfy_cli")
    if spec and spec.submodule_search_locations:
        paths += [os.path.join(list(spec.submodule_search_locations)[0], "workflow_to_api.py")]
    paths += glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\Lib\site-packages\comfy_cli\workflow_to_api.py"))
    for path in paths:
        try:
            if os.path.isfile(path):
                sp = importlib.util.spec_from_file_location("cs_workflow_to_api", path)
                mod = importlib.util.module_from_spec(sp)
                sp.loader.exec_module(mod)
                _conv["fn"] = mod.convert_ui_to_api
                break
        except Exception:
            continue
    return _conv["fn"]


_PRIMS = ("PrimitiveInt", "PrimitiveFloat", "PrimitiveBoolean", "PrimitiveString", "PrimitiveStringMultiline")
_DROP = ("PreviewAny", "ImageCompare")


def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


SIMPLIFY_MOVES = {}   # removed node id -> [(node, input), ...] that received its value (last simplify() call)


def _repoint(g, nid, idx, new):
    if idx == 0:
        SIMPLIFY_MOVES.setdefault(str(nid), [])
        for k2, n2 in g.items():
            for a2, v2 in (n2.get("inputs") or {}).items():
                if _is_link(v2) and str(v2[0]) == str(nid) and v2[1] == 0:
                    SIMPLIFY_MOVES[str(nid)].append((k2, a2))
    for n in g.values():
        for a, v in list((n.get("inputs") or {}).items()):
            if _is_link(v) and str(v[0]) == str(nid) and v[1] == idx:
                if new is None:
                    del n["inputs"][a]
                else:
                    n["inputs"][a] = copy.deepcopy(new)


def _upstream_has(g, link, words, seen=None):
    seen = seen if seen is not None else set()
    if not _is_link(link) or str(link[0]) in seen or str(link[0]) not in g:
        return False
    seen.add(str(link[0]))
    n = g[str(link[0])]
    if any(w in n.get("class_type", "") for w in words):
        return True
    return any(_upstream_has(g, v, words, seen) for v in (n.get("inputs") or {}).values())


def simplify(g, oi):
    """Tidy an uploaded graph so the app can map it like a hand-made one:
    preview-only nodes go, switches are fixed to one side (the side WITHOUT a built-in prompt enhancer, since the
    app has its own Enhance), constant primitives are written into the nodes that use them (except where a math
    node needs a connection), and anything that no longer feeds a saved output is dropped."""
    SIMPLIFY_MOVES.clear()
    for k in [k for k, n in g.items() if n.get("class_type") == "PreviewAny"]:
        _repoint(g, k, 0, (g[k].get("inputs") or {}).get("source"))
        del g[k]
    # rgthree "Any Switch": passes on its first connected input; prefer one that isn't a built-in prompt enhancer
    for k in [k for k, n in g.items() if n.get("class_type") == "Any Switch (rgthree)"]:
        ins = g[k].get("inputs") or {}
        links = [ins[a] for a in sorted(ins) if _is_link(ins[a])]
        plain = [l for l in links if not _upstream_has(g, l, ("TextGenerate",))]
        _repoint(g, k, 0, (plain or links or [None])[0])
        del g[k]
    for k in [k for k, n in g.items() if n.get("class_type") in _DROP]:
        g.pop(k, None)
    for _ in range(20):
        sw = [k for k, n in g.items() if n.get("class_type") == "ComfySwitchNode"]
        if not sw:
            break
        for k in sw:
            ins = g[k].get("inputs") or {}
            v = ins.get("switch")
            if _is_link(v):
                src = g.get(str(v[0])) or {}
                v = (src.get("inputs") or {}).get("value", True)
            val = bool(v) and str(v).lower() != "false"
            on_t, on_f = ins.get("on_true"), ins.get("on_false")
            if val and on_f is not None and _upstream_has(g, on_t, ("TextGenerate",)):
                val = False   # skip a built-in prompt enhancer
            _repoint(g, k, 0, on_t if val else on_f)
            del g[k]
    # a Resolution Selector becomes plain width / height on the nodes it fed, so the Size pill can drive them
    for k in [k for k, n in g.items() if n.get("class_type") == "ResolutionSelector"]:
        ins = g[k].get("inputs") or {}
        if any(_is_link(v) for v in ins.values()):
            continue
        try:
            a_, b_ = [float(x) for x in str(ins.get("aspect_ratio") or "1:1").split(" ")[0].split(":")]
            mp = float(ins.get("megapixels") or 1.0) * 1e6
            mult = int(ins.get("multiple") or 8) or 8
            w = int(round((mp * a_ / b_) ** 0.5 / mult) * mult)
            h = int(round((mp * b_ / a_) ** 0.5 / mult) * mult)
        except (ValueError, ZeroDivisionError):
            continue
        _repoint(g, k, 0, w)
        _repoint(g, k, 1, h)
        del g[k]
    math_fed = {str(v[0]) for n in g.values() if "MathExpression" in n.get("class_type", "")
                for v in (n.get("inputs") or {}).values() if _is_link(v)}
    for k in [k for k, n in g.items() if n.get("class_type") in _PRIMS and k not in math_fed]:
        _repoint(g, k, 0, (g[k].get("inputs") or {}).get("value"))
        del g[k]
    saves = [k for k, n in g.items() if str(n.get("class_type", "")).startswith(("Save", "VHS_VideoCombine"))]
    if saves:
        for k in [k for k, n in g.items() if n.get("class_type") in ("PreviewImage", "PreviewAudio")]:
            del g[k]
    outs = [k for k, n in g.items() if (oi or {}).get(n.get("class_type"), {}).get("output_node")]
    if outs:
        keep, todo = set(), list(outs)
        while todo:
            k = str(todo.pop())
            if k in keep or k not in g:
                continue
            keep.add(k)
            todo += [v[0] for v in (g[k].get("inputs") or {}).values() if _is_link(v)]
        for k in [k for k in g if k not in keep]:
            del g[k]
    return g


def app_mode_inputs(data):
    """ComfyUI App Mode (frontend 1.41.13+): the inputs the workflow's author exposed, as [(node_id, widget)]."""
    try:
        if isinstance(data, (bytes, str)):
            data = json.loads(data)
        lin = ((data or {}).get("extra") or {}).get("linearData") or {}
        return [(str(i[0]), str(i[1])) for i in (lin.get("inputs") or []) if isinstance(i, (list, tuple)) and len(i) >= 2]
    except Exception:
        return []


_APP_KIND = {"seed": "seed", "noise_seed": "seed", "steps": "steps", "cfg": "cfg", "denoise": "denoise",
             "sampler_name": "sampler", "scheduler": "scheduler", "width": "width", "height": "height",
             "length": "length", "frames": "length", "num_frames": "length", "batch_size": None}


def apply_app_mode(card, graph, picks, oi):
    """Use the author's App Mode choices for the mapping (they decide; the auto-detection fills the rest)."""
    if not picks:
        return 0
    slots = card.setdefault("slots", {})
    used, texts, images = 0, [], []
    for nid, w in picks:
        targets = []
        if nid in graph and w in (graph[nid].get("inputs") or {}):
            targets = [(nid, w)]
        elif nid in SIMPLIFY_MOVES:                       # a folded text box / number: follow it to where it went
            targets = SIMPLIFY_MOVES[nid]
        else:                                            # a widget promoted from inside a grouped node
            targets = [(k, w) for k, n in graph.items() if k.startswith(nid + ":") and w in (n.get("inputs") or {})]
        targets = [(k, i) for k, i in targets if k in graph]   # only nodes that are still in the tidied graph
        for node, inp in targets[:1]:
            n = graph.get(node) or {}
            ct = n.get("class_type", "")
            title = ((n.get("_meta") or {}).get("title") or "").lower()
            if ct == "LoadImage" and inp == "image":
                images.append({"node": node, "input": "image", "label": "Picture %d" % (len(images) + 1)})
            elif inp in _APP_KIND and _APP_KIND[inp]:
                slots[_APP_KIND[inp]] = [{"node": node, "input": inp, "label": inp.replace("_", " ").title()}]
            elif isinstance(n.get("inputs", {}).get(inp), str) and inp in ("text", "prompt", "value", "string", "text_g", "text_l"):
                texts.append(({"node": node, "input": inp, "label": "Prompt"}, "neg" in title))
            else:
                continue
            used += 1
    pos = [t for t, neg in texts if not neg]
    neg = [t for t, neg in texts if neg]
    if pos:
        slots["prompt"] = pos[:1]
    if neg:
        slots["negative"] = neg[:1]
    if images:
        slots["images"] = images
        card["picture_min"] = card.get("picture_min") or 1
        card["picture_max"] = max(card.get("picture_max") or 0, len(images))
    if used:
        card["app_mode"] = True
        card["notes"] = [x for x in (card.get("notes") or []) if "No prompt box" not in x or not pos]
    return used


def load_any(data, oi):
    """Accept UI or API JSON (dict or text). Returns (api_graph, warnings)."""
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    if isinstance(data, dict) and "prompt" in data and is_api_format(data.get("prompt")):
        data = data["prompt"]  # a saved /prompt body
    if is_api_format(data):
        g = {str(k): v for k, v in data.items() if isinstance(v, dict) and "class_type" in v}
        return simplify(g, oi), []
    if isinstance(data, dict) and "nodes" in data:
        conv = _ui_converter() if oi else None
        if conv:
            try:
                g = {str(k): v for k, v in conv(copy.deepcopy(data), oi).items() if isinstance(v, dict) and "class_type" in v}
                if g:
                    note = (["Grouped nodes (subgraphs) were opened automatically."]
                            if (data.get("definitions") or {}).get("subgraphs") else [])
                    return simplify(g, oi), note
            except Exception as e:
                log_warn = "The full converter couldn't read this file (%s); used the basic one." % str(e)[:120]
                g, w = ui_to_api(data, oi)
                return simplify(g, oi), [log_warn] + w
        g, w = ui_to_api(data, oi)
        return simplify(g, oi), w
    raise ValueError("This doesn't look like a ComfyUI workflow file.")


# ------------------------------------------------------------------ model family profiles
_profiles = {"t": 0, "list": []}


def profiles():
    path = os.path.join(HERE, "model_families.json")
    try:
        mt = os.path.getmtime(path)
        if mt != _profiles["t"]:
            _profiles.update(t=mt, list=_read_json(path).get("families") or [])
    except (OSError, ValueError):
        pass
    return _profiles["list"]


def match_profile(model_names, media=None):
    """First family whose match strings appear in the workflow's model file names."""
    text = " ".join(str(n or "").lower() for n in model_names)
    for p in profiles():
        if media and p.get("media") and p["media"] != media:
            continue
        if any(m in text for m in p.get("match") or []):
            return p
    return None


def frames_for(seconds, fps, rule=""):
    f = max(1, int(round(float(seconds) * float(fps))))
    if rule == "8n+1":
        return max(9, int(round((f - 1) / 8.0)) * 8 + 1)
    if rule == "4n+1":
        return max(5, int(round((f - 1) / 4.0)) * 4 + 1)
    return f


LORA_LOADERS = ("UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple", "LTXInt8CheckpointLoader", "UNETLoaderMultiGPU",
                "CheckpointLoader", "ImageOnlyCheckpointLoader")


def inject_loras(graph, loras, card=None):
    """Add the user's LoRAs. MiniMax H3 AIO: its spare LoRA slots. Anything else: a LoraLoaderModelOnly chain
    right after the model loader, with every model consumer re-pointed to the end of the chain."""
    loras = [l for l in (loras or []) if l and l.get("name") and l.get("name") != "None"][:6]
    if not loras:
        return []
    aio_l = _find(graph, "MiniMaxH3AIOLoras")
    if aio_l:
        ins = graph[aio_l]["inputs"]
        free = [i for i in (2, 3, 4) if not ins.get("lora_%d" % i) or ins.get("lora_%d" % i) == "None"]
        used = []
        for l, i in zip(loras, free):
            ins["lora_%d" % i] = l["name"]
            ins["strength_%d" % i] = float(l.get("strength", 1.0))
            used.append(l["name"])
        return used
    loader = next((k for k, v in graph.items() if v.get("class_type") in LORA_LOADERS), None)
    if not loader:
        return []
    src = [loader, 0]
    for i, l in enumerate(loras):
        nid = "cs_lora_%d" % i
        graph[nid] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": src, "lora_name": l["name"], "strength_model": float(l.get("strength", 1.0))}}
        src = [nid, 0]
    for k, v in graph.items():
        if k.startswith("cs_lora_"):
            continue
        for name, val in (v.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2 and str(val[0]) == str(loader) and val[1] == 0 and name == "model":
                v["inputs"][name] = list(src)
    return [l["name"] for l in loras]


# ------------------------------------------------------------------ MiniMax H3 AIO (all-in-one node)
# mode -> (task, min pictures, max pictures, media, fast)
AIO_MODES = {
    "Text to Video": ("t2v", 0, 0, "video", False),
    "Fast Text to Video": ("t2v", 0, 0, "video", True),
    "Image to Video / First-Last Frame": ("i2v", 1, 2, "video", False),
    "Fast Image to Video": ("i2v", 1, 2, "video", True),
    "References to Video": ("r2v", 1, 9, "video", False),
    "Fast References to Video": ("r2v", 1, 9, "video", True),
    "Long Text to Video": ("long", 0, 0, "video", False),
    "Long Image to Video": ("long", 1, 1, "video", False),
    "Image Editing": ("edit", 1, 9, "image", False),
    "Extend Video": ("other", 0, 0, "video", False),
    "Inpainting": ("other", 1, 1, "image", False),
}
AIO_RES = {"480p": "480p (0.4 MP)", "576p": "576p (0.6 MP)", "720p": "720p (0.9 MP)", "832p": "832p (1.2 MP)", "1080p": "1080p (2.0 MP)"}
AIO_ASPECT = {"16:9": "16:9 landscape", "9:16": "9:16 portrait", "1:1": "1:1 square", "4:3": "4:3", "3:4": "3:4", "match": "Match input"}


def _find(graph, cls):
    return next((k for k, v in graph.items() if v.get("class_type") == cls), None)


def aio_card(graph):
    """Exact mapping for a MiniMax H3 AIO workflow (built-in or uploaded): which inputs the app controls,
    how many pictures the chosen mode takes, its lengths / sizes and output kind."""
    aio = _find(graph, "MiniMaxH3AIO")
    if not aio:
        return None
    ins = graph[aio].get("inputs") or {}
    mode = ins.get("mode") or "Text to Video"
    task, pmin, pmax, media, fast = AIO_MODES.get(mode, ("other", 0, 0, "video", False))
    long = mode.startswith("Long")
    slot = lambda node, inp, label, **x: dict({"node": node, "input": inp, "label": label}, **x)
    slots = {
        "seed": [slot(aio, "seed", "Seed")],
        "negative": [slot(aio, "negative_prompt", "Negative prompt")],
        "negative_flag": [slot(aio, "use_negative", "Use negative")],
        "res_label": slot(aio, "resolution", "Resolution", map=AIO_RES),
        "aspect_label": slot(aio, "aspect_ratio", "Aspect", map=AIO_ASPECT),
        "models": [],
    }
    defaults = {"seed": 0}
    if "cfg" in ins:
        slots["cfg"] = [slot(aio, "cfg", "CFG")]
        defaults["cfg"] = float(ins["cfg"])
    speed = _find(graph, "MiniMaxH3AIOSpeed")
    if speed and "steps" in (graph[speed].get("inputs") or {}):
        slots["steps"] = [slot(speed, "steps", "Steps")]
        defaults["steps"] = int(graph[speed]["inputs"]["steps"])
    planner = _find(graph, "MiniMaxH3AIOPlanner")
    if long and planner:
        slots["prompt"] = [slot(planner, "story", "Story")]
        slots["seconds"] = [slot(planner, "total_seconds", "Total length")]
        slots["clip_label"] = slot(planner, "seconds_per_clip", "Seconds per clip",
                                   map={"5": "5 s - fastest, most control", "7": "7 s", "10": "10 s", "15": "15 s - longest H3 clip"})
    else:
        slots["prompt"] = [slot(aio, "prompt", "Prompt")]
        if media == "video":
            slots["seconds"] = [slot(aio, "duration_seconds", "Length")]
    media_node = _find(graph, "MiniMaxH3MediaLoader")
    if pmax and media_node:
        slots["media"] = slot(media_node, "media_state", "Pictures", max=pmax)
    models = _find(graph, "MiniMaxH3AIOModels")
    if models:
        for inp, label, kind in (("model_text_image", "Video model (text / image)", "model"), ("model_references", "Reference model", "model"),
                                 ("text_encoder", "Text encoder", "text encoder"), ("video_vae", "Video VAE", "vae"),
                                 ("audio_vae", "Audio VAE", "vae"), ("image_vae", "Image VAE", "vae"),
                                 ("speed_lora_name", "Speed LoRA", "lora")):
            if inp in (graph[models].get("inputs") or {}):
                slots["models"].append(slot(models, inp, label, kind=kind, class_type="MiniMaxH3AIOModels",
                                            value=graph[models]["inputs"][inp], title="MiniMax H3 models"))
    loras = _find(graph, "MiniMaxH3AIOLoras")
    if loras:
        for i in range(1, 5):
            inp = "lora_%d" % i
            if inp in (graph[loras].get("inputs") or {}):
                slots["models"].append(slot(loras, inp, "LoRA %d" % i, kind="lora", class_type="MiniMaxH3AIOLoras",
                                            value=graph[loras]["inputs"][inp], title="LoRAs"))
    out_cls = "SaveVideo" if media == "video" else "SaveImage"
    out = _find(graph, out_cls)
    if long:
        lengths, res, limits = [10, 15, 20, 30], ["480p", "576p"], {"480p": 30, "576p": 20}
    elif media == "image":
        lengths, res, limits = [], ["720p", "1080p"], {}
    elif fast:
        lengths, res, limits = [3, 5, 8, 10], ["720p", "832p"], {"720p": 8, "832p": 5}
    else:
        lengths, res, limits = [3, 5, 8, 10], ["480p", "720p"], {"480p": 10, "576p": 8, "720p": 5}
    return {
        "family": "MiniMax H3", "task": task, "media": media, "aio_mode": mode,
        "all_resolutions": list(AIO_RES.keys()),
        "picture_min": pmin, "picture_max": pmax,
        "lengths": lengths, "default_length": (15 if long else 5) if lengths else None,
        "resolutions": res, "default_resolution": res[0],
        "aspects": ["16:9", "9:16", "1:1", "4:3", "3:4"] + (["match"] if pmin else []),
        "limits": limits,
        "needs": {"vram_gb": 8, "ram_gb": 30, "profile": "h3",
                  "note": "Needs ComfyUI started in MiniMax H3 mode (run_comfyui_minimax_h3.bat) and about 30 GB of system RAM."},
        "prompt_style": "h3",
        "prompt_trigger": "r34l1sm" if "realism" in str((graph.get(loras) or {}).get("inputs", {}).get("lora_1", "")).lower() else "",
        "slots": slots,
        "outputs": [{"node": out, "kind": media, "class_type": out_cls}] if out else [],
        "defaults": defaults,
        "sampling_note": ("MiniMax H3 video: %s steps with the turbo speed LoRA and CFG %s. No sampler or scheduler to choose; "
                          "the AIO node handles them." % (defaults.get("steps", 8), defaults.get("cfg", 1.5))),
    }


# ------------------------------------------------------------------ requirements
CORE_PREFIXES = ("comfy_extras", "nodes")
MODEL_FOLDERS = {"model": "diffusion_models", "text encoder": "text_encoders", "vae": "vae", "lora": "loras"}


def requirements(graph, card, oi):
    """What a workflow needs: node packs (by class type, filled in by the server) and model files."""
    nodes = sorted({n.get("class_type") for n in graph.values() if n.get("class_type")})
    custom = []
    for cls in nodes:
        mod = str(((oi or {}).get(cls) or {}).get("python_module") or "")
        if mod and not mod.startswith(CORE_PREFIXES):
            custom.append({"class_type": cls, "module": mod.split(".")[-1] if mod.startswith("custom_nodes") else mod})
    # Only LoRAs travel with a workflow. Diffusion models, text encoders and VAEs are the user's own choice
    # (picked in Settings), so they are never listed as something to install.
    loras = []
    seen = set()
    for m in ((card.get("slots") or {}).get("models") or []):
        v = m.get("value")
        if (m.get("kind") or "") != "lora" or not v or v == "None" or v in seen:
            continue
        seen.add(v)
        loras.append({"file": v, "kind": "lora", "folder": "loras"})
    return {"custom_nodes": custom, "loras": loras}


# ------------------------------------------------------------------ scanning
def _title(node):
    return ((node.get("_meta") or {}).get("title") or node.get("class_type") or "").strip()


def _consumers(graph):
    """node_id -> list of (consumer_id, input_name)."""
    out = {}
    for nid, n in graph.items():
        for name, v in (n.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in graph:
                out.setdefault(str(v[0]), []).append((nid, name))
    return out


def _polarity(graph, cons, start):
    """Walk downstream from a text encoder to find whether it feeds a positive or negative input."""
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nid, name in cons.get(cur, []):
            ln = name.lower()
            if ln in ("negative", "neg", "negative_conditioning", "conditioning_negative"):
                return "negative"
            if ln in ("positive", "pos", "positive_conditioning", "conditioning_positive", "conditioning"):
                if ln != "conditioning":
                    return "positive"
            stack.append(nid)
    return ""


def _slot(nid, name, node, label=None):
    return {"node": nid, "input": name, "title": _title(node), "class_type": node.get("class_type"), "label": label or name}


def scan(graph, oi=None):
    """Recognise the standard parts of an API-format graph. Returns a draft card."""
    known = aio_card(graph)
    if known:
        # A MiniMax H3 AIO workflow: use the exact mapping, not guesses.
        missing = sorted({n.get("class_type") for n in graph.values() if oi is not None and n.get("class_type") not in oi})
        known.update(name="", missing_nodes=missing, subgraphs=0, unknown=[], picture_count=known["picture_max"],
                     notes=["Recognised as MiniMax H3 AIO (%s). Pictures: %s." % (
                         known["aio_mode"], ("%d-%d" % (known["picture_min"], known["picture_max"])) if known["picture_max"] else "none")])
        return known
    cons = _consumers(graph)
    slots = {"images": [], "videos": [], "audios": [], "models": [], "seed": [], "steps": [], "cfg": [], "sampler": [],
             "scheduler": [], "width": [], "height": [], "length": [], "fps": [], "denoise": []}
    prompts = []
    outputs = []
    unknown = []
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    subgraph_nodes = [nid for nid, n in graph.items() if uuid_re.match(str(n.get("class_type") or ""))]
    missing = sorted({n.get("class_type") for n in graph.values()
                      if oi is not None and n.get("class_type") not in oi and not uuid_re.match(str(n.get("class_type") or ""))})

    for nid, n in graph.items():
        cls = n.get("class_type") or ""
        ins = n.get("inputs") or {}
        title_l = _title(n).lower()
        if cls in IMAGE_OUT or cls in VIDEO_OUT or ((oi or {}).get(cls) or {}).get("output_node"):
            if cls in VIDEO_OUT or "video" in cls.lower():
                outputs.append({"node": nid, "kind": "video", "class_type": cls})
            elif cls in IMAGE_OUT or "image" in cls.lower() or "save" in cls.lower():
                outputs.append({"node": nid, "kind": "image", "class_type": cls})
        if cls in IMAGE_IN and isinstance(ins.get("image"), str):
            slots["images"].append(_slot(nid, "image", n, "Picture"))
        # video and audio files the workflow loads (a video to edit, a driving video, a voice clip ...)
        vkeys = VIDEO_IN.get(cls) or (("file", "video") if "loadvideo" in cls.lower().replace("_", "") else ())
        for key in vkeys:
            if isinstance(ins.get(key), str):
                slots["videos"].append(_slot(nid, key, n, "Video"))
                break
        akeys = AUDIO_IN.get(cls) or (("audio", "file") if "loadaudio" in cls.lower().replace("_", "") else ())
        for key in akeys:
            if isinstance(ins.get(key), str):
                slots["audios"].append(_slot(nid, key, n, "Audio"))
                break
        for name, v in ins.items():
            if isinstance(v, list):
                continue  # linked, not a value
            ln = name.lower()
            if isinstance(v, str) and ln in ("text", "prompt", "positive", "text_g", "string", "value", "positive_prompt", "text_positive") \
                    and ("encode" in cls.lower() or "prompt" in cls.lower() or "text" in cls.lower() or cls in STRING_SOURCES or "string" in cls.lower()
                         or ("conditioning" in cls.lower() and ln == "prompt")):   # e.g. MiniMaxH3ImageConditioning
                prompts.append((nid, name))
            elif ln in ("negative_prompt", "text_negative", "negative") and isinstance(v, str):
                slots.setdefault("negative_direct", []).append(_slot(nid, name, n, "Negative"))
            elif ln in ("seed", "noise_seed") and isinstance(v, (int, float)):
                slots["seed"].append(_slot(nid, name, n, "Seed"))
            elif ln == "steps" and isinstance(v, (int, float)):
                slots["steps"].append(_slot(nid, name, n, "Steps"))
            elif ln == "cfg" and isinstance(v, (int, float)):
                slots["cfg"].append(_slot(nid, name, n, "CFG"))
            elif ln == "sampler_name" and isinstance(v, str):
                slots["sampler"].append(_slot(nid, name, n, "Sampler"))
            elif ln == "scheduler" and isinstance(v, str):
                slots["scheduler"].append(_slot(nid, name, n, "Scheduler"))
            elif ln == "denoise" and isinstance(v, (int, float)):
                slots["denoise"].append(_slot(nid, name, n, "Denoise"))
            elif ln == "width" and isinstance(v, (int, float)) and ("latent" in cls.lower() or "empty" in cls.lower() or "size" in cls.lower() or "resolution" in title_l):
                slots["width"].append(_slot(nid, name, n, "Width"))
            elif ln == "height" and isinstance(v, (int, float)) and ("latent" in cls.lower() or "empty" in cls.lower() or "size" in cls.lower() or "resolution" in title_l):
                slots["height"].append(_slot(nid, name, n, "Height"))
            elif ln in ("length", "num_frames", "frame_count", "frames", "video_frames") and isinstance(v, (int, float)):
                slots["length"].append(_slot(nid, name, n, "Frames"))
            elif ln in ("frame_rate", "fps") and isinstance(v, (int, float)):
                slots["fps"].append(_slot(nid, name, n, "FPS"))
            elif isinstance(v, str) and (ln.endswith("_name") or ln in ("ckpt_name", "model", "model_name")) and \
                    ("loader" in cls.lower() or "load" in cls.lower()) and ln not in ("sampler_name",):
                kind = "lora" if "lora" in ln or "lora" in cls.lower() else (
                    "vae" if "vae" in ln else ("text encoder" if "clip" in ln or "text_encoder" in ln else "model"))
                slots["models"].append(dict(_slot(nid, name, n, kind.capitalize()), kind=kind, value=v))

    # Positive vs negative prompt.
    pos, neg = [], []
    for nid, name in prompts:
        pol = _polarity(graph, cons, nid)
        t = _title(graph[nid]).lower()
        if pol == "negative" or "negative" in t or t.startswith("neg"):
            neg.append(_slot(nid, name, graph[nid], "Negative prompt"))
        else:
            pos.append(_slot(nid, name, graph[nid], "Prompt"))
    # Prefer a string source feeding an encoder over the encoder's own (unused) text.
    pos.sort(key=lambda s: (0 if s["class_type"] in STRING_SOURCES or "string" in (s["class_type"] or "").lower() else 1,
                            -len(str((graph[s["node"]].get("inputs") or {}).get(s["input"]) or ""))))
    slots["prompt"] = pos[:1]
    slots["prompt_extra"] = pos[1:]
    slots["negative"] = neg[:1] or (slots.get("negative_direct") or [])[:1]
    slots.pop("negative_direct", None)

    # Picture order: titles with a number first, then node id.
    def pic_key(s):
        m = re.search(r"(\d+)", s["title"] or "")
        return (int(m.group(1)) if m else 999, int(s["node"]) if str(s["node"]).isdigit() else 9999)
    slots["images"].sort(key=pic_key)

    video_out = any(o["kind"] == "video" for o in outputs)
    n_img = len(slots["images"])
    n_vid, n_aud = len(slots["videos"]), len(slots["audios"])
    if video_out and n_vid:
        task = "v2v"      # edits / restyles / drives from a video you give it
    elif video_out:
        task = "i2v" if n_img == 1 else ("r2v" if n_img > 1 else "t2v")
    else:
        task = "edit" if n_img else "t2i"
    if not slots["prompt"]:
        task = task if outputs else "other"

    # the family comes from the MAIN model first (a VAE or text encoder shared with another family must not decide it)
    main = [m.get("value") for m in slots["models"] if m.get("kind") == "model" and m.get("value")]
    allm = [m.get("value") for m in slots["models"]]
    family = "Custom"
    names = main or allm       # with a main model, only it decides (Ideogram uses the FLUX.2 VAE; it isn't FLUX)
    prof = match_profile(names, "video" if video_out else "image") or match_profile(names)
    if prof:
        family = prof["name"]
    else:
        text = " ".join(str(x or "").lower() for x in names)
        family = next((name for needle, name in FAMILIES if needle in text), "Custom")
        if family == "Custom" and main:
            family = pretty_model_name(main[0])     # a model the app doesn't know yet: name it after its file

    defaults = {}
    for key in ("seed", "steps", "cfg", "sampler", "scheduler", "width", "height", "length", "fps", "denoise"):
        if slots[key]:
            s = slots[key][0]
            defaults[key] = (graph[s["node"]].get("inputs") or {}).get(s["input"])
    if slots["negative"]:
        s = slots["negative"][0]
        defaults["negative"] = (graph[s["node"]].get("inputs") or {}).get(s["input"]) or ""

    recognised = {s["node"] for k, v in slots.items() if isinstance(v, list) for s in v}
    recognised |= {o["node"] for o in outputs}
    for nid, n in graph.items():
        cls = n.get("class_type") or ""
        if nid in recognised or cls in missing:
            continue
        free = [k for k, v in (n.get("inputs") or {}).items() if isinstance(v, str) and len(v) > 40]
        if free:
            unknown.append({"node": nid, "title": _title(n), "class_type": cls, "inputs": free})

    # Real save nodes first; previews and comparers last.
    rank = lambda o: (0 if o["class_type"] in VIDEO_OUT or o["class_type"] == "SaveImage" else
                      (1 if o["class_type"] in IMAGE_OUT and o["class_type"] != "PreviewImage" else 2))
    outputs.sort(key=rank)

    notes = []
    if subgraph_nodes:
        notes.append("This workflow contains %d grouped node(s) (subgraphs) that the app can't open. In ComfyUI, use "
                     "Workflow > Export (API) and upload that file instead." % len(subgraph_nodes))
    if not outputs:
        notes.append("No output node was found (Save Image or Video Combine). Results won't be collected.")
    if not slots["prompt"]:
        notes.append("No prompt box was found. Pick one under 'Not sure about these' or the prompt will be ignored.")
    if len(outputs) > 1:
        notes.append("Several outputs were found; the app shows the first saved result.")
    extra = {}
    if prof:
        extra = {"profile": prof["id"], "supports_loras": prof.get("supports_loras", True),
                 "sampling_note": prof.get("sampling_note") or ""}
        if video_out:
            fps = prof.get("fps") or defaults.get("fps") or 24
            defaults.setdefault("fps", fps)
            extra["frame_rule"] = prof.get("frame_rule") or ""
            top = int(prof.get("max_seconds") or 10)
            extra["lengths"] = [s for s in (3, 5, 8, 10, 15) if s <= top]
            extra["default_length"] = 5
    return dict(extra, **{
        "name": "",
        "family": family,
        "task": task,
        "media": TASK_MEDIA.get(task, "image"),
        "slots": slots,
        "outputs": outputs,
        "defaults": defaults,
        "missing_nodes": missing,
        "subgraphs": len(subgraph_nodes),
        "unknown": unknown[:12],
        "notes": notes,
        "picture_count": n_img,
        "picture_min": n_img,
        "picture_max": n_img,
        "video_min": n_vid, "video_max": n_vid,
        "audio_min": n_aud, "audio_max": n_aud,
    })


def summary(card):
    s = card.get("slots") or {}
    found = []
    for key, label in (("prompt", "prompt"), ("negative", "negative prompt"), ("images", "picture inputs"),
                       ("videos", "video inputs"), ("audios", "audio inputs"),
                       ("width", "size"), ("seed", "seed"), ("steps", "steps"), ("cfg", "CFG"),
                       ("sampler", "sampler"), ("length", "video length"), ("models", "model loaders")):
        v = s.get(key) or []
        if v:
            found.append("%d %s" % (len(v), label) if key in ("images", "videos", "audios", "models") else label)
    outs = ", ".join(sorted({o["kind"] for o in card.get("outputs") or []})) or "none"
    return {"found": found, "output": outs, "where": "%s · %s" % (
        "Video" if card.get("media") == "video" else "Image", TASKS.get(card.get("task"), "Other"))}


# ------------------------------------------------------------------ filling
def _set(graph, slot, value):
    n = graph.get(str(slot["node"]))
    if n is None:
        return
    n.setdefault("inputs", {})[slot["input"]] = value


def snap(v, mult=32, lo=64, hi=8192):
    try:
        v = int(round(float(v) / mult) * mult)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, v))


def ltx_frames(seconds, fps):
    """LTX wants 8n+1 frames."""
    f = max(9, int(round(float(seconds) * float(fps))))
    return int(round((f - 1) / 8.0)) * 8 + 1


_QUOTE_RX = re.compile(r"[\"“]([^\"”]{2,200})[\"”]")
_CAMERA_RX = re.compile(r"\b(camera|shot|pan|tilt|dolly|zoom|tracking|handheld|close-?up|wide|aerial|orbit)\b", re.I)
_ANIMATED_RX = re.compile(r"\b(anime|cartoon|cel[- ]shad\w*|2d animation|pixel ?art|illustration|claymation|stop[- ]motion|3d render)\b", re.I)


def h3_frames(prompt, n_images, seconds):
    """MiniMax H3 multi-frame prompt (the official template's layout): what each picture is, then the summary."""
    lines = ["subject_definitions:", "<Picture 1> is the first frame of [Shot 1]."]
    secs = float(seconds or 5)
    for k in range(2, int(n_images or 1) + 1):
        t = secs * (k - 1) / (int(n_images) - 1)
        lines.append("<Picture %d> is the keyframe of [Shot %d] at %.1fs." % (k, k, t))
    return "\n".join(lines) + "\n\nsummary:\n" + (prompt or "").strip()


def h3_wrap(text, trigger=""):
    """Plain words -> MiniMax H3's field format (prompts that already use the fields pass through)."""
    t = (text or "").strip()
    if not t or re.search(r"integrated_multimodal_description|detailed_description|overall_soundscape", t):
        return t
    t = _QUOTE_RX.sub(lambda m: "<d>[English] %s</d>" % m.group(1).strip(), t)
    if not _CAMERA_RX.search(t):
        t = t.rstrip() + (" " if t.endswith((".", "!", "?", ">")) else ". ") + "The camera is steady with a slow, natural drift."
    words = t.split()
    if len(words) > 90:
        t = " ".join(words[:90]).rstrip(",;:") + "."
    lead = (trigger + " ") if trigger and not _ANIMATED_RX.search(t) else ""
    sound = "Voices and natural room tone, no voice-over." if "<d>" in t else "Natural ambient sound and room tone, no voice-over."
    return ("integrated_multimodal_description: [Shot 1] %s%s\n\noverall_soundscape: %s\n\nnon_diegetic_music: N/A"
            % (lead, t, sound))


def size_from_rule(rule, res, aspect):
    """(width, height) for a megapixel rule, e.g. LTX 480p = 0.37 MP, multiples of 64."""
    mp = (rule.get("mp") or {}).get(res) or (rule.get("mp") or {}).get("480p") or 0.4
    snap_to = int(rule.get("snap") or 32)
    try:
        a, b = [float(x) for x in str(aspect or "16:9").split(":")]
    except ValueError:
        a, b = 16.0, 9.0
    px = mp * 1024 * 1024
    w = max(snap_to, round((px * a / b) ** 0.5 / snap_to) * snap_to)
    h = max(snap_to, round((px * b / a) ** 0.5 / snap_to) * snap_to)
    return int(w), int(h)


def fix_moved_files(graph, oi):
    """A model file referenced as 'ltx\\x.safetensors' that now lives at 'x.safetensors' (or another
    sub-folder) is re-pointed by file name, the way ComfyUI users expect a downloaded workflow to just work.
    Returns a list of (node, input, old, new)."""
    fixed = []
    for nid, n in graph.items():
        specs = dict(_input_specs(oi, n.get("class_type")))
        for name, v in list((n.get("inputs") or {}).items()):
            if not isinstance(v, str) or "." not in v:
                continue
            spec = specs.get(name)
            choices = spec[0] if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list) else (
                (spec[1] or {}).get("options") if isinstance(spec, (list, tuple)) and len(spec) > 1 and spec[0] == "COMBO" and isinstance(spec[1], dict) else None)
            if not choices or v in choices:
                continue
            base = re.split(r"[\\/]", v)[-1].lower()
            hits = [c for c in choices if isinstance(c, str) and re.split(r"[\\/]", c)[-1].lower() == base]
            if len(hits) == 1:
                n["inputs"][name] = hits[0]
                fixed.append((nid, name, v, hits[0]))
    return fixed


def strip_unknown_inputs(graph, oi):
    """Drop UI-only fields a saved workflow carries (button labels, preview toggles) that aren't node inputs."""
    for n in graph.values():
        specs = dict(_input_specs(oi, n.get("class_type")))
        if not specs:
            continue
        for name in list((n.get("inputs") or {}).keys()):
            root = name.split(".", 1)[0]
            if name not in specs and root not in specs:
                del n["inputs"][name]
    return graph


def media_items(names):
    return json.dumps([{"kind": "picture", "file": n, "name": os.path.basename(n), "enabled": True,
                        "has_audio": False, "audio_mode": "off"} for n in names])


def apply_bypass(g, plan):
    """Take unused optional pictures out of a graph (plan made by tools/image_builtins.bypass_plan)."""
    for nid in plan.get("delete") or []:
        g.pop(str(nid), None)
    for nid, inp, link in plan.get("rewire") or []:
        if str(nid) in g:
            g[str(nid)]["inputs"][inp] = list(link)
    for nid, inp in plan.get("unset") or []:
        if str(nid) in g:
            g[str(nid)]["inputs"].pop(inp, None)


def fill(card, graph, values, out_prefix):
    """Return a ready-to-queue copy of graph with the user's values applied."""
    g = copy.deepcopy(graph)
    s = card.get("slots") or {}
    if s.get("prompt") and values.get("prompt") is not None:
        _set(g, s["prompt"][0], values["prompt"])
    neg = values.get("negative")
    if s.get("negative") and neg is not None:
        _set(g, s["negative"][0], neg)
    for slot in s.get("negative_flag") or []:
        _set(g, slot, bool((neg or "").strip()))
    for key in ("videos", "audios"):          # the user's video / audio files, in slot order
        files = list(values.get(key) or [])
        for i, slot in enumerate(s.get(key) or []):
            if i < len(files):
                _set(g, slot, files[i])
    imgs = list(values.get("images") or [])
    for i, slot in enumerate(s.get("images") or []):
        if i < len(imgs):
            _set(g, slot, imgs[i])
    for i, slot in enumerate(s.get("images") or []):
        if i >= len(imgs) and slot.get("if_missing"):
            apply_bypass(g, slot["if_missing"])  # removes this picture and every later one
            break
    m = s.get("media")
    if m:
        _set(g, m, media_items(imgs[: int(m.get("max") or 9)]))
    seed = values.get("seed")
    if seed is None:
        seed = random.randint(1, 2 ** 50)
    for slot in s.get("seed") or []:
        _set(g, slot, int(seed) + int(slot.get("offset") or 0))
    if values.get("seconds") not in (None, ""):
        for slot in s.get("seconds") or []:
            _set(g, slot, float(values["seconds"]))
        # keyframes pinned along the clip: picture k of n sits at (k-1)/(n-1) of the length, the last at the end
        n = len(imgs)
        for slot in s.get("seconds_frac") or []:
            k = int(slot.get("picture") or 2)
            if n >= 2 and k <= n:
                _set(g, slot, round(float(values["seconds"]) * (k - 1) / (n - 1), 3))
    for key, vkey in (("res_label", "res"), ("aspect_label", "aspect"), ("clip_label", "clip")):
        sl = s.get(key)
        if sl and values.get(vkey) not in (None, ""):
            label = (sl.get("map") or {}).get(str(values[vkey]))
            if label:
                _set(g, sl, label)
    for key, cast in (("steps", int), ("cfg", float), ("denoise", float)):
        if values.get(key) not in (None, ""):
            for slot in s.get(key) or []:
                _set(g, slot, cast(values[key]))
    for key in ("sampler", "scheduler"):
        if values.get(key):
            for slot in s.get(key) or []:
                _set(g, slot, values[key])
    w, h = snap(values.get("width"), 8), snap(values.get("height"), 8)
    if w and h:
        for slot in s.get("width") or []:
            _set(g, slot, snap(w * float(slot.get("scale") or 1), 32))
        for slot in s.get("height") or []:
            _set(g, slot, snap(h * float(slot.get("scale") or 1), 32))
    fps = values.get("fps") or (card.get("defaults") or {}).get("fps") or 24
    if values.get("seconds") and s.get("length"):
        rule = card.get("frame_rule") or ("8n+1" if (card.get("family") == "LTX Video" or any(
            "ltx" in (sl.get("class_type") or "").lower() for sl in s["length"])) else "")
        frames = frames_for(values["seconds"], fps, rule)
        for slot in s["length"]:
            _set(g, slot, frames)
    for i, slot in enumerate(s.get("models") or []):
        v = (values.get("models") or {}).get(str(i))
        if v:
            _set(g, slot, v)
    for o in card.get("outputs") or []:
        n = g.get(str(o["node"]))
        if n is not None and "filename_prefix" in (n.get("inputs") or {}) and not isinstance(n["inputs"]["filename_prefix"], list):
            n["inputs"]["filename_prefix"] = out_prefix
        elif n is not None and o.get("class_type") in ("SaveImage", "VHS_VideoCombine", "SaveVideo", "SaveWEBM", "SaveAnimatedWEBP"):
            n.setdefault("inputs", {})["filename_prefix"] = out_prefix
    krea_edit_tune(g, values.get("edit_intent"))
    if values.get("loras") and card.get("supports_loras", True):
        inject_loras(g, values["loras"], card)
    for n in g.values():
        n.pop("_meta", None)
    return g, int(seed)


# Auto (per edit type) settings for Krea 2 Edit, from the krea2-identity-edit model card (V2):
#   Identity Edit V2 = restage a person (pose, angle, scene, wardrobe, person into a scene):
#       one person ref_boost 2, person 'beside'; person into scene: person 2-3, scene 1, scene 'aligned'
#   General Edit V2 = everything else (remove, recolor, relight, background swap, add, outfit, text): ref_boost 4, aligned
#   grounding_px 768 for both (the card's setting; 384-768 is the trained range)
# The app picks the LoRA per edit type (Auto only); these are what the patch gets for each.
#   'aligned' pins the reference onto the output layout; with a strong ref_boost an edit that MOVES hands / arms keeps
#   the old limbs pinned while drawing new ones (= duplicated limbs). So a same-image action keeps 'aligned' (scene and
#   framing stay) but at 1.5 (the card's face-locked range 1-2; 1.0 = how the LoRAs were trained); a real pose / angle
#   change or a new place drops the pin ('beside', 2).
KREA_INTENT_KNOBS = {
    "action": {"lora": "identity", "ref_boost": 1.5, "position": "aligned (trained)", "grounding_px": 768},
    # one picture = the photo IS the scene: 'beside' would unpin the background too and redraw it. So a turn / pose
    # change stays 'aligned' (background and framing kept) at 1.0, the strength the LoRAs were trained at, which lets
    # the body move without the old pose staying pinned. 'beside' is only for person-into-scene (two pictures).
    "restage": {"lora": "identity", "ref_boost": 1.0, "position": "aligned (trained)", "grounding_px": 768},
    "insert": {"lora": "identity", "ref_boost": 2.5, "ref_boost_a": 1.0, "position": "beside (right of target)", "grounding_px": 768},
    "background": {"lora": "general", "ref_boost": 4.0, "position": "aligned (trained)", "grounding_px": 768},
    "remove": {"lora": "general", "ref_boost": 4.0, "position": "aligned (trained)", "grounding_px": 768},
    "face": {"lora": "general", "ref_boost": 4.0, "position": "aligned (trained)", "grounding_px": 768},
    "general": {"lora": "general", "ref_boost": 4.0, "position": "aligned (trained)", "grounding_px": 768},
}


def krea_edit_tune(g, intent=None):
    """Krea 2 Edit: set the patch node the way the chosen edit LoRA was trained / tested.
      v1, v1.1  -> fit_mode 'crop (legacy)', ref_boost 1.0 (their geometry; no fidelity dial yet)
      v1.2, V2  -> fit_mode 'fit', ref_boost 4 (the tested fidelity setting)
      V2 with a second picture (person into a scene) -> person 'beside', scene 'aligned' (the pack's advice for V2;
      v1.2 was only trained 'aligned', so it keeps that)."""
    for n in g.values():
        if not str(n.get("class_type", "")).startswith("Krea2EditModelPatch"):
            continue
        ins = n["inputs"]
        src = ins.get("model")
        lora = ""
        while isinstance(src, list) and str(src[0]) in g:
            up = g[str(src[0])]
            if up.get("class_type") == "LoraLoaderModelOnly" and "edit" in str(up["inputs"].get("lora_name", "")).lower():
                lora = str(up["inputs"]["lora_name"]).lower()
                break
            src = (up.get("inputs") or {}).get("model")
        if not lora:
            continue
        legacy = re.search(r"edit_v1(_1)?(\.|_r)", lora) is not None
        v2 = "edit_v2" in lora
        if "fit_mode" in ins:
            ins["fit_mode"] = "crop (legacy)" if legacy else "fit"
        if "ref_boost" in ins:
            ins["ref_boost"] = 1.0 if legacy else 4.0
        two = isinstance(ins.get("source_latent_b"), list) or isinstance(ins.get("source_image_b"), list)
        if "ref_position" in ins:
            ins["ref_position"] = "beside (right of target)" if (v2 and two) else "aligned (trained)"
        if "ref_position_a" in ins:
            ins["ref_position_a"] = "aligned (trained)"
        knobs = KREA_INTENT_KNOBS.get(intent) if intent else None
        if knobs and not legacy:   # v1/v1.1 keep their own geometry and boost
            if two:
                # person into a scene (the card): person 2.5 'beside' (V2), scene 1 'aligned'
                if "ref_boost" in ins:
                    ins["ref_boost"] = 2.5
                if "ref_boost_a" in ins:
                    ins["ref_boost_a"] = 1.0
                if "ref_position" in ins:
                    ins["ref_position"] = "beside (right of target)" if v2 else "aligned (trained)"
            else:
                # per edit type, whatever LoRA is loaded (so a General LoRA on a body move still loses the hard pin);
                # 'beside' only on V2 weights (v1.2 was trained 'aligned' only)
                if "ref_boost" in ins:
                    ins["ref_boost"] = knobs["ref_boost"]
                if "ref_position" in ins:
                    ins["ref_position"] = knobs["position"] if (v2 or knobs["position"].startswith("aligned")) else "aligned (trained)"
            for e in g.values():
                if str(e.get("class_type", "")).startswith("Krea2EditGroundedEncode") and "grounding_px" in e.get("inputs", {}):
                    e["inputs"]["grounding_px"] = knobs["grounding_px"]


# ------------------------------------------------------------------ Manual mode: every setting of any workflow
# Read straight from the graph + ComfyUI's own node definitions, so it works for any workflow anyone uploads:
# every input that holds a fixed value (not a link) and isn't already one of the app's own controls.
KNOB_SKIP_INPUTS = {"filename_prefix", "upload", "control_after_generate", "control_before_generate", "unique_id"}
KNOB_SKIP_CLASSES = {"Note", "MarkdownNote", "PrimitiveNode", "Reroute"}


def _knob_spec(oi, cls, name):
    schema = (oi.get(cls) or {}).get("input") or {}
    for grp in ("required", "optional"):
        spec = (schema.get(grp) or {}).get(name)
        if spec is not None:
            return spec
    return None


def _knob_type(spec):
    """(type, options, meta) from an object_info input spec, or None if it's not a plain value."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    t = spec[0]
    meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(t, list):
        return "combo", [str(v) for v in t], meta
    if t == "COMBO":
        return "combo", [str(v) for v in (meta.get("options") or [])], meta
    if t in ("INT", "FLOAT", "BOOLEAN", "STRING"):
        return t.lower(), None, meta
    return None


def workflow_knobs(card, graph, oi):
    """Every adjustable value in this workflow, for Manual mode. The app's own controls (prompt, seed, steps,
    size, models ...) are left out so nothing is set in two places."""
    covered = set()
    for lst in (card.get("slots") or {}).values():
        for sl in (lst if isinstance(lst, list) else []):
            if isinstance(sl, dict) and sl.get("node") is not None and sl.get("input"):
                covered.add((str(sl["node"]), sl["input"]))
    out = []
    for nid, n in graph.items():
        cls = str(n.get("class_type") or "")
        if cls in KNOB_SKIP_CLASSES or cls not in oi:
            continue
        if cls.startswith("LoadImage") or cls.startswith("Save") or cls.startswith("Preview"):
            continue
        title = ((n.get("_meta") or {}).get("title") or "").strip()
        disp = (oi.get(cls) or {}).get("display_name") or cls
        group = title if title and title != cls else disp
        for name, val in (n.get("inputs") or {}).items():
            if isinstance(val, list) or name in KNOB_SKIP_INPUTS or (str(nid), name) in covered:
                continue
            kt = _knob_type(_knob_spec(oi, cls, name))
            if not kt:
                continue
            typ, options, meta = kt
            if typ == "combo" and not options:
                continue
            k = {"node": str(nid), "input": name, "group": group, "class_type": cls, "type": typ,
                 "label": name.replace("_", " "), "value": val}
            if options is not None:
                k["options"] = options[:400]
            for m in ("min", "max", "step"):
                if isinstance(meta.get(m), (int, float)) and not isinstance(meta.get(m), bool):
                    k[m] = meta[m]
            if typ == "string":
                k["multiline"] = bool(meta.get("multiline"))
            tip = meta.get("tooltip")
            if isinstance(tip, str) and tip.strip():
                k["tip"] = tip.strip()[:300]
            out.append(k)
    return out


def apply_overrides(graph, overrides, knobs):
    """Manual mode: put the user's values into the graph, but only into inputs listed in `knobs` and only values
    that fit the node's own limits (combo option, number range, true/false). Anything else is ignored.
    Returns the number of values applied."""
    if not isinstance(overrides, dict):
        return 0
    allowed = {(k["node"], k["input"]): k for k in knobs}
    done = 0
    for key, v in overrides.items():
        if not isinstance(key, str) or "." not in key:
            continue
        nid, name = key.rsplit(".", 1)
        k = allowed.get((nid, name))
        n = graph.get(nid)
        if not k or n is None or isinstance((n.get("inputs") or {}).get(name), list):
            continue
        t = k["type"]
        try:
            if t == "combo":
                if not isinstance(v, str) or v not in (k.get("options") or []):
                    continue
            elif t == "boolean":
                if not isinstance(v, bool):
                    continue
            elif t in ("int", "float"):
                if isinstance(v, bool):
                    continue
                v = float(v)
                if v != v or v in (float("inf"), float("-inf")):
                    continue
                if "min" in k:
                    v = max(float(k["min"]), v)
                if "max" in k:
                    v = min(float(k["max"]), v)
                v = int(round(v)) if t == "int" else v
            elif t == "string":
                if not isinstance(v, str) or len(v) > 20000:
                    continue
            else:
                continue
        except (TypeError, ValueError):
            continue
        n["inputs"][name] = v
        done += 1
    return done


# ------------------------------------------------------------------ native inpainting (any workflow)
def detect_native_mask(graph):
    """Where a workflow takes its inpaint mask, if it has one:
      * "alpha": a Load Image whose MASK output (the transparent area painted in ComfyUI's mask editor) is used
      * "mask_node": a Load Image (as Mask) node
    Returns {"node", "kind", ...} or None."""
    used = {}
    for n in graph.values():
        for v in (n.get("inputs") or {}).values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int):
                used.setdefault(str(v[0]), set()).add(v[1])
    for k, n in graph.items():
        if n.get("class_type") in ("LoadImageMask",):
            return {"node": k, "kind": "mask_node"}
    for k, n in graph.items():
        if n.get("class_type") in ("LoadImage", "LoadImageOutput") and 1 in used.get(k, set()):
            return {"node": k, "kind": "alpha"}
    return None


_scan_base = scan


def scan(graph, oi=None):
    card = _scan_base(graph, oi)
    c = card[0] if isinstance(card, tuple) else card
    nm = detect_native_mask(graph)
    if nm and isinstance(c, dict):
        c["native_mask"] = nm
        # a Load Image (as Mask) is not a picture the user adds
        if nm["kind"] == "mask_node":
            c["slots"]["images"] = [s for s in (c.get("slots") or {}).get("images") or [] if str(s.get("node")) != nm["node"]]
            c["picture_count"] = len(c["slots"]["images"])
            c["picture_min"] = min(c.get("picture_min") or 0, c["picture_count"]) or (1 if c["picture_count"] else 0)
            c["picture_max"] = max(c.get("picture_max") or 0, c["picture_count"])
        if c.get("media") == "image":
            c["task"] = "edit"
            c["picture_min"] = max(1, c.get("picture_min") or 0)
            c["picture_max"] = max(1, c.get("picture_max") or 0)
        notes = c.setdefault("notes", [])
        notes.append("This workflow inpaints: paint the area to change with the Inpaint pill, and the app feeds it in as the mask.")
    return card
