"""What each LoRA on this PC is for, so the app can pick LoRAs from the words in a prompt.

Works for anyone's LoRAs, labelled or not:
  * the .safetensors header (read without loading the file) usually says the base model it was trained on
    (ss_base_model_version / modelspec.architecture) and its trigger words (modelspec.trigger_phrase, trigger words,
    or the training tags in ss_tag_frequency)
  * otherwise the folder and file name are used (e.g. 'Krea2\\something.safetensors')
  * a LoRA whose model family can't be told is never auto-loaded (it can still be added by hand)
Results are cached in data/lora_index.json by path + size + date.
"""
from __future__ import annotations

import json, os, re, struct

# family keys shared with the app's engines
FAMILY_WORDS = [
    ("minimax_h3", ("minimax", "h3_", "h3-", "_h3", "-h3", "mmh3")),
    ("krea2", ("krea2", "krea_2", "krea-2", "krea 2")),
    ("qwen_image", ("qwen_image", "qwen-image", "qwenimage", "qwen image", "qwen")),
    ("flux2", ("flux2", "flux.2", "flux-2", "flux_2", "klein")),
    ("flux1", ("flux1", "flux.1", "flux-1", "flux_1", "flux-dev", "flux_dev", "flux")),
    ("moss", ("moss", "kromoss")),
    ("sdxl", ("sdxl", "sd_xl", "pony", "illustrious", "noobai")),
    ("sd15", ("sd15", "sd1.5", "sd_1.5", "v1-5")),
    ("ltx", ("ltx",)),
    ("wan", ("wan2", "wan_2", "wan-2", "wan21", "wan22", "wan2.1", "wan2.2", "wan ")),
    ("hunyuan", ("hunyuan",)),
    ("z_image", ("z_image", "z-image", "zimage")),
]
ENGINE_FAMILY = {  # engine card family -> key
    "MiniMax H3": "minimax_h3", "MiniMax H3 Fast": "minimax_h3", "Krea 2": "krea2", "Qwen Image": "qwen_image",
    "FLUX.2 Klein": "flux2", "FLUX.3 Klein": "flux2", "FLUX.1": "flux1", "Flux": "flux1", "MOSS": "moss",
    "SDXL": "sdxl", "LTX Video": "ltx", "Wan": "wan", "Hunyuan Video": "hunyuan", "Hunyuan": "hunyuan",
}
# words too common to pick a LoRA by
STOP = set("""lora loras model models style styles v1 v2 v3 v4 v5 final best real realism realistic photo photoreal
people person woman women man men girl boy edit general turbo lightning fast speed step steps rank fp16 bf16 fp8 int8
comfyui comfy diffusers kohya ostris aitoolkit the and with for from high quality detail details detailer enhance
enhancer base dev pro version new old test epoch lowres image images video videos t2v i2v r2v t2i flux krea qwen
minimax wan ltx sdxl pony moss safetensors""".split())
GENERIC_TAGS = set("""1girl 1boy solo female male woman man girl boy person people looking at viewer smile
long hair short hair blonde hair black hair brown hair photo realistic photorealistic portrait upper body full body
simple background white background indoors outdoors standing sitting""".split())

MAX_HEADER = 32 * 1024 * 1024


def read_meta(path):
    """The __metadata__ dict of a .safetensors file ({} if none or not safetensors)."""
    if not path.lower().endswith(".safetensors"):
        return {}
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > MAX_HEADER:
                return {}
            head = json.loads(f.read(n).decode("utf-8", "replace"))
        meta = head.get("__metadata__") or {}
        return {k: v for k, v in meta.items() if isinstance(v, str)}
    except Exception:
        return {}


def family_of(text):
    t = (text or "").lower()
    for key, words in FAMILY_WORDS:
        if any(w in t for w in words):
            return key
    return None


def _tokens(name):
    stem = re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", os.path.basename(name), flags=re.I)
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    out = []
    for w in re.split(r"[^A-Za-z0-9]+", stem):
        w = w.lower()
        if len(w) >= 4 and w not in STOP and not re.fullmatch(r"(v|e|s|r|step|rank)?\d+[a-z]?", w) \
                and not re.fullmatch(r"(bf|fp|int)\d+", w):
            out.append(w)
    return out[:6]


def _triggers(meta):
    trig = []
    for k in ("modelspec.trigger_phrase", "trigger_phrase", "trigger_words", "ss_trigger_words", "activation_text"):
        v = meta.get(k)
        if v:
            trig += [x.strip() for x in re.split(r"[,;\n]", v) if x.strip()]
    # training tags: the most frequent tag is often the trigger word (only if it isn't a generic tag)
    tf = meta.get("ss_tag_frequency")
    if tf:
        try:
            counts = {}
            for ds in json.loads(tf).values():
                for tag, c in (ds or {}).items():
                    counts[tag.strip()] = counts.get(tag.strip(), 0) + int(c)
            for tag, _c in sorted(counts.items(), key=lambda x: -x[1])[:2]:
                if tag.lower() not in GENERIC_TAGS and len(tag) >= 3:
                    trig.append(tag)
        except Exception:
            pass
    seen, out = set(), []
    for t in trig:
        if t.lower() not in seen and len(t) <= 60:
            seen.add(t.lower())
            out.append(t)
    return out[:6]


def build(names, lora_dirs, data_dir):
    """[{name, folder, family, base, triggers, tokens}] for the LoRA names ComfyUI lists."""
    cache_p = os.path.join(data_dir, "lora_index.json")
    try:
        cache = json.load(open(cache_p, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    out, fresh = [], {}
    for name in names:
        full = next((os.path.join(d, name) for d in lora_dirs if os.path.isfile(os.path.join(d, name))), None)
        key = name
        sig = None
        if full:
            st = os.stat(full)
            sig = "%d|%d" % (st.st_size, int(st.st_mtime))
        hit = cache.get(key)
        if hit and hit.get("sig") == sig:
            rec = hit
        else:
            meta = read_meta(full) if full else {}
            base = meta.get("ss_base_model_version") or meta.get("modelspec.architecture") or meta.get("ss_sd_model_name") or ""
            fam = family_of(base) or family_of(os.path.dirname(name)) or family_of(os.path.basename(name))
            rec = {"name": name, "folder": os.path.dirname(name).replace("\\", "/"), "family": fam, "base": base[:80],
                   "triggers": _triggers(meta), "tokens": _tokens(name), "sig": sig}
        fresh[key] = rec
        out.append({k: rec.get(k) for k in ("name", "folder", "family", "base", "triggers", "tokens")})
    try:
        json.dump(fresh, open(cache_p, "w", encoding="utf-8"))
    except OSError:
        pass
    return out
