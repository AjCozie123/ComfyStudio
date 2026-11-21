"""How ComfyUI is started, and what to do when a job runs out of graphics memory.

Comfy Studio never edits the user's own run .bat file. It only starts ComfyUI itself in three cases: the Auto memory
restart after a job ran out of graphics memory, Manual "Restart ComfyUI this way", and Start when ComfyUI isn't running.
Each time it starts from the user's OWN start options (what their .bat used, read from ComfyUI itself) and changes
only what that style needs:
  normal  - exactly the user's own options, nothing changed
  lowmem  - the user's options with the graphics-memory ones replaced by --lowvram --reserve-vram 2
            (live previews, cache, input folder, listen address, ... all kept)
  lowest  - lowmem + live previews off and no model cache: the most room for very big models (e.g. MiniMax H3 video)

Auto (the default): ComfyUI runs as the user started it. If a job runs out of memory (or ComfyUI falls over mid-job,
which is how a full graphics card often shows), Comfy Studio restarts ComfyUI one style lighter and runs the job again.
Manual: the user picks the style; Comfy Studio never changes it by itself.

Pure functions only (no ComfyUI calls), so they can be tested on their own.
"""
from __future__ import annotations

import os

STYLES = ("normal", "lowmem", "lowest")
STYLE_NAMES = {"normal": "Normal", "lowmem": "Low memory", "lowest": "Lowest memory"}
# the PC Launcher's own modes on the developer's PC, for the same three styles
LAUNCHER_PROFILE = {"normal": "everyday", "lowmem": "video", "lowest": "h3"}

# graphics-memory options (replaced by lowmem / lowest)
_VRAM_VALUE = ("--reserve-vram",)
_VRAM_ALONE = ("--lowvram", "--normalvram", "--highvram", "--novram", "--gpu-only")
# previews and model cache (only replaced by lowest)
_EXTRA_VALUE = ("--preview-method", "--cache-lru")
_EXTRA_ALONE = ("--cache-none", "--cache-classic")
_STYLE_FLAGS = {
    "normal": [],
    "lowmem": ["--lowvram", "--reserve-vram", "2"],
    "lowest": ["--lowvram", "--reserve-vram", "2", "--preview-method", "none", "--cache-none"],
}

_OOM_WORDS = ("out of memory", "cuda oom", "outofmemoryerror", "allocation on device", "cudamalloc failed",
              "not enough memory", "unable to allocate")
_CRASH_WORDS = ("closed while this was running", "comfyui lost this job", "comfyui is not running")


def is_memory_failure(text: str) -> bool:
    """A job error that a lighter start style can fix: out of memory, or ComfyUI falling over mid-job."""
    low = (text or "").lower()
    return any(w in low for w in _OOM_WORDS) or any(w in low for w in _CRASH_WORDS)


def next_style(current: str):
    """The next lighter style, or None when already at the lightest."""
    cur = current if current in STYLES else "normal"
    i = STYLES.index(cur)
    return STYLES[i + 1] if i + 1 < len(STYLES) else None


def _strip(args, value_flags, alone_flags):
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        key = a.split("=", 1)[0]
        if key in value_flags:
            skip = "=" not in a          # "--reserve-vram 2" (two items) vs "--reserve-vram=2" (one)
            continue
        if key in alone_flags:
            continue
        out.append(a)
    return out


def strip_memory_flags(args, style="lowest"):
    """The user's options without the ones this style replaces."""
    out = _strip(args, _VRAM_VALUE, _VRAM_ALONE)
    if style == "lowest":
        out = _strip(out, _EXTRA_VALUE, _EXTRA_ALONE)
    return out


def start_command(argv, style, python_exe, comfy_root):
    """The command that starts ComfyUI with the user's own options (argv = ComfyUI's sys.argv from /system_stats,
    as THEY started it) and this style's memory options."""
    if style not in STYLES:
        raise ValueError("Unknown start style: %s" % style)
    args = list(argv or [])
    if args and args[0].lower().endswith("main.py"):
        args = args[1:]
    if style != "normal":
        args = strip_memory_flags(args, style)
    if "--disable-auto-launch" not in args:
        args.append("--disable-auto-launch")       # no browser tab opening on a restart
    return [python_exe, "-s", os.path.join(comfy_root, "main.py")] + args + _STYLE_FLAGS[style]


def style_of(argv) -> str:
    """Which style a running ComfyUI looks like (by its options)."""
    a = " ".join(argv or [])
    if "--lowvram" in a and "--preview-method none" in a:
        return "lowest"
    if "--lowvram" in a:
        return "lowmem"
    return "normal"
