"""How ComfyUI is started, and what to do when a job runs out of graphics memory.

ComfyUI can be started with options that trade speed for memory. Comfy Studio uses three "start styles":
  normal  - ComfyUI's own memory handling (whatever the user started it with, minus memory options we add)
  lowmem  - --lowvram --reserve-vram 2: parks parts of big models in RAM so they fit a small graphics card
  lowest  - lowmem + no live previews and no model cache: the most room for very big models (e.g. MiniMax H3 video)

Auto (the default): ComfyUI runs as the user started it. If a job runs out of memory (or ComfyUI falls over mid-job,
which is how a full graphics card often shows), Comfy Studio restarts ComfyUI one style lighter, keeping every other
option the user started it with (input folder, listen address, ...), and runs the job again.
Manual: the user picks the style; Comfy Studio never changes it by itself.

Pure functions only (no ComfyUI calls), so they can be tested on their own.
"""
from __future__ import annotations

import os

STYLES = ("normal", "lowmem", "lowest")
STYLE_NAMES = {"normal": "Normal", "lowmem": "Low memory", "lowest": "Lowest memory"}
# the PC Launcher's own modes on the developer's PC, for the same three styles
LAUNCHER_PROFILE = {"normal": "everyday", "lowmem": "video", "lowest": "h3"}

# options that decide memory use: removed before a style adds its own
_FLAG_VALUE = ("--reserve-vram", "--preview-method", "--cache-lru")
_FLAG_ALONE = ("--lowvram", "--normalvram", "--highvram", "--novram", "--gpu-only", "--cache-none", "--cache-classic")
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


def strip_memory_flags(args):
    """ComfyUI's start options without the ones that decide memory use."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        key = a.split("=", 1)[0]
        if key in _FLAG_VALUE:
            skip = "=" not in a          # "--reserve-vram 2" (two items) vs "--reserve-vram=2" (one)
            continue
        if key in _FLAG_ALONE:
            continue
        out.append(a)
    return out


def start_command(argv, style, python_exe, comfy_root):
    """The command that starts ComfyUI again with the user's own options and this style's memory options.
    argv is ComfyUI's own sys.argv (from /system_stats): [main.py, options...]."""
    if style not in STYLES:
        raise ValueError("Unknown start style: %s" % style)
    args = list(argv or [])
    if args and args[0].lower().endswith("main.py"):
        args = args[1:]
    args = strip_memory_flags(args)
    if "--disable-auto-launch" not in args:
        args.append("--disable-auto-launch")       # no browser tab opening on a restart
    return [python_exe, "-s", os.path.join(comfy_root, "main.py")] + args + _STYLE_FLAGS[style]


def style_of(argv) -> str:
    """Which style a running ComfyUI was started with (by its options)."""
    a = " ".join(argv or [])
    if "--lowvram" in a and "--preview-method none" in a:
        return "lowest"
    if "--lowvram" in a:
        return "lowmem"
    return "normal"
