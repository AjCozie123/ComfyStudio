"""Read-only browse of this PC for the phone 'From PC' picker.

Shortcut folders in data/pc_folders.json appear as quick chips and are the
locations scanned by Recent. Search recursively scans the selected drive or
folder (not only shortcuts). Local fixed drives are detected automatically
and can also be browsed.

Never writes or deletes inside those folders. Selecting an image copies it into
the app's own input folder (ComfyUI's input folder\\ComfyStudio\\).
"""
from __future__ import annotations

import hashlib, json, os, re, threading, time

try:
    import ctypes
    from ctypes import wintypes
except Exception:
    ctypes = None
    wintypes = None

_BASE_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_HEIC_EXT = {".heic", ".heif"}
_heic_available = None

SKIP_NAMES = {".", "..", "desktop.ini", "thumbs.db"}
# Always hidden from browse, even if someone types the path.
PROTECTED_ALWAYS = {"$recycle.bin", "system volume information"}
# Hidden when listing a parent, and blocked unless the current root
# is a shortcut that already lives inside one of these (explicit).
SKIP_UNLESS_EXPLICIT = {"windows", "appdata", "application data"}
# Always skipped while recursively searching, even under an explicit shortcut.
SEARCH_SKIP_ALWAYS = {
    "$recycle.bin", "system volume information", "recycler", "recovery",
    "node_modules", ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
    "lost+found", "windows.old", "$windows.~bt", "$windows.~ws",
    "config.msi", "msocache", "perflogs", "windowsapps",
}
# Skipped when walking a drive / ordinary folder (not a shortcut already inside one).
SEARCH_SKIP_UNLESS_EXPLICIT = SKIP_UNLESS_EXPLICIT | {
    "program files", "program files (x86)", "programdata", "application data",
}
SEARCH_MAX_RESULTS = 300
SEARCH_MAX_DEPTH = 16
SEARCH_TIMEOUT_SEC = 10.0
SEARCH_SOFT_SEC = 2.0
SEARCH_MAX_VISITS = 50000

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_INVALID = 0xFFFFFFFF
DRIVE_FIXED = 3

_lock = threading.Lock()
_cfg = {"mtime": None, "path": None, "roots": [], "include_drives": True}

_HOME = os.path.expanduser("~")
# first-run shortcuts on any PC (the server adds ComfyUI's own input / output folders); each user's own list
# lives in data/pc_folders.json
DEFAULT_ROOTS = [
    {"id": "pictures", "name": "Pictures", "path": os.path.join(_HOME, "Pictures")},
    {"id": "videos", "name": "Videos", "path": os.path.join(_HOME, "Videos")},
    {"id": "downloads", "name": "Downloads", "path": os.path.join(_HOME, "Downloads")},
    {"id": "desktop", "name": "Desktop", "path": os.path.join(_HOME, "Desktop")},
]


def set_comfy_roots(input_dir, output_dir):
    """Put ComfyUI's own folders first in the first-run shortcuts."""
    global DEFAULT_ROOTS
    base = [r for r in DEFAULT_ROOTS if r["id"] not in ("input", "output")]
    DEFAULT_ROOTS = [{"id": "input", "name": "ComfyUI input", "path": input_dir},
                     {"id": "output", "name": "ComfyUI output", "path": output_dir}] + base

CONFIG_COMMENT = (
    "These are shortcut folders: quick chips at the top of Browse, and the only "
    "locations scanned by Recent and Search. Local fixed drives (C:, D:, E:, ...) "
    "are detected automatically and also appear in Browse. Set include_drives to "
    "false to hide the drive chips. Only shortcuts that exist are shown. The app "
    "never writes or deletes inside these folders or on the drives. The file is "
    "re-read when it changes; a restart is not required."
)


def image_exts():
    """Extensions treated as images. HEIC/HEIF only if pillow-heif is installed."""
    global _heic_available
    exts = set(_BASE_IMG_EXT)
    if _heic_available is None:
        try:
            import pillow_heif  # noqa: F401
            _heic_available = True
        except Exception:
            _heic_available = False
    if _heic_available:
        exts.update(_HEIC_EXT)
    return exts


def heic_supported():
    image_exts()
    return bool(_heic_available)


def _ensure_heic_opener():
    if not heic_supported():
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def _is_img(name):
    return os.path.splitext(name)[1].lower() in image_exts()


VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
FFMPEG = None          # set by the server (PATH or ComfyUI's bundled imageio-ffmpeg)


def _is_kind(name, media="image"):
    """What the picker shows: pictures (default), videos or audio clips."""
    ext = os.path.splitext(name)[1].lower()
    if media == "video":
        return ext in VIDEO_EXT
    if media == "audio":
        return ext in AUDIO_EXT
    return ext in image_exts()


def media_kind(name):
    ext = os.path.splitext(name)[1].lower()
    return "video" if ext in VIDEO_EXT else ("audio" if ext in AUDIO_EXT else ("image" if ext in image_exts() else None))


def _real(path):
    return os.path.realpath(path)


def _norm(path):
    return os.path.normcase(_real(path))


def _is_under(path, root):
    """True if path is root or inside it, after resolving junctions and symlinks."""
    try:
        p, r = _norm(path), _norm(root)
    except OSError:
        return False
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:
        return False  # different drives


def _safe_rel(rel):
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel:
        return ""
    parts = [p for p in rel.split("/") if p]
    if any(p in ("..", "") or p.endswith(":") or ":" in p or p.startswith("\\\\") for p in parts):
        raise ValueError("That path is not allowed.")
    return "/".join(parts)


def _file_attrs(path):
    if os.name != "nt" or ctypes is None:
        return 0
    try:
        kernel32 = ctypes.windll.kernel32
        attrs = kernel32.GetFileAttributesW(str(path))
        if attrs == FILE_ATTRIBUTE_INVALID:
            return 0
        return int(attrs)
    except Exception:
        return 0


def _is_hidden_or_system(path):
    attrs = _file_attrs(path)
    return bool(attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))


def _name_blocked(name, *, allow_explicit=False):
    n = (name or "").lower().rstrip(". ")
    if n in SKIP_NAMES or n in PROTECTED_ALWAYS:
        return True
    if n in SKIP_UNLESS_EXPLICIT and not allow_explicit:
        return True
    return False


def _root_is_explicit_system(root_path):
    """True if this root already lives inside Windows/AppData (a configured shortcut)."""
    try:
        parts = os.path.normpath(root_path).replace("/", os.sep).split(os.sep)
    except Exception:
        return False
    return any((p or "").lower().rstrip(". ") in SKIP_UNLESS_EXPLICIT or
               (p or "").lower().rstrip(". ") in PROTECTED_ALWAYS for p in parts)


def _rel_blocked(rel, *, allow_explicit=False):
    for p in [x for x in (rel or "").split("/") if x]:
        if _name_blocked(p, allow_explicit=allow_explicit):
            return True
    return False


def config_path(data_dir):
    return os.path.join(data_dir, "pc_folders.json")


def ensure_config(data_dir):
    path = config_path(data_dir)
    if not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "include_drives": True,
                "roots": DEFAULT_ROOTS,
                "_comment": CONFIG_COMMENT,
            }, f, indent=2)
    return path


def _read_config_file(data_dir):
    path = ensure_config(data_dir)
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        raw = {"roots": list(DEFAULT_ROOTS), "include_drives": True}
    if not isinstance(raw, dict):
        raw = {"roots": list(DEFAULT_ROOTS), "include_drives": True}
    return path, raw


def include_drives_enabled(data_dir):
    load_roots(data_dir)
    return bool(_cfg.get("include_drives", True))


def load_roots(data_dir):
    """Load shortcut folders from pc_folders.json (not drives)."""
    path = ensure_config(data_dir)
    with _lock:
        try:
            m = os.path.getmtime(path)
        except OSError:
            m = None
        if _cfg["path"] == path and _cfg["mtime"] == m:
            return list(_cfg["roots"])
        try:
            raw = json.load(open(path, encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        items = raw.get("shortcuts") or raw.get("roots") or []
        include = raw.get("include_drives")
        if include is None:
            include = True
        roots = []
        seen = set()
        for i, r in enumerate(items):
            p = (r.get("path") or "").strip()
            if not p:
                continue
            try:
                real = _real(p)
            except OSError:
                continue
            rid = str(r.get("id") or "root%d" % i)
            if rid in seen:
                rid = rid + "_%d" % i
            seen.add(rid)
            roots.append({
                "id": rid,
                "name": str(r.get("name") or os.path.basename(p.rstrip("\\/")) or rid),
                "path": real,
                "exists": os.path.isdir(real),
                "kind": "shortcut",
            })
        _cfg.update(path=path, mtime=m, roots=roots, include_drives=bool(include))
        return list(roots)


def _volume_label(root):
    if os.name != "nt" or ctypes is None or wintypes is None:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32
        vol = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        maxcomp = wintypes.DWORD()
        flags = wintypes.DWORD()
        ok = kernel32.GetVolumeInformationW(
            root, vol, 261, ctypes.byref(serial), ctypes.byref(maxcomp),
            ctypes.byref(flags), fs, 261,
        )
        if not ok:
            return ""
        return (vol.value or "").strip()
    except Exception:
        return ""


def list_fixed_drives():
    """Return local fixed drives as root dicts (kind=drive). Empty on non-Windows."""
    out = []
    if os.name != "nt" or ctypes is None:
        return out
    try:
        kernel32 = ctypes.windll.kernel32
        mask = int(kernel32.GetLogicalDrives() or 0)
    except Exception:
        return out
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        root = letter + ":\\"
        try:
            dtype = int(kernel32.GetDriveTypeW(root) or 0)
        except Exception:
            continue
        if dtype != DRIVE_FIXED:
            continue
        if not os.path.isdir(root):
            continue
        try:
            real = _real(root)
        except OSError:
            continue
        label = _volume_label(root)
        name = ("%s (%s:)" % (label, letter)) if label else ("Local Disk (%s:)" % letter)
        out.append({
            "id": "drive_%s" % letter.lower(),
            "name": name,
            "path": real,
            "exists": True,
            "kind": "drive",
            "letter": letter,
            "label": label,
        })
    return out


def all_roots(data_dir, include_drives=None):
    """Shortcut folders plus, optionally, detected fixed drives."""
    shortcuts = load_roots(data_dir)
    if include_drives is None:
        include_drives = include_drives_enabled(data_dir)
    out = list(shortcuts)
    if not include_drives:
        return out
    seen = set()
    for r in out:
        try:
            if r.get("exists"):
                seen.add(_norm(r["path"]))
        except OSError:
            continue
    for d in list_fixed_drives():
        try:
            key = _norm(d["path"])
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def public_roots(data_dir):
    """JSON-safe roots for /api/pc/roots."""
    shortcuts, drives = [], []
    for r in all_roots(data_dir):
        if not r.get("exists"):
            continue
        item = {
            "id": r["id"],
            "name": r["name"],
            "path": r["path"],
            "kind": r.get("kind") or "shortcut",
        }
        if r.get("letter"):
            item["letter"] = r["letter"]
            item["label"] = r.get("label") or ""
        if item["kind"] == "drive":
            drives.append(item)
        else:
            shortcuts.append(item)
    return {
        "roots": shortcuts + drives,
        "shortcuts": shortcuts,
        "drives": drives,
        "include_drives": include_drives_enabled(data_dir),
    }


def _root_by_id(data_dir, rid):
    for r in all_roots(data_dir):
        if r["id"] == rid:
            if not r["exists"] or not os.path.isdir(r["path"]):
                raise ValueError("That folder is not available.")
            return r
    raise ValueError("Unknown folder.")


def resolve(data_dir, rid, rel, must_exist=True, want="any"):
    """Resolve a root id + relative path to a real path still inside that root."""
    root = _root_by_id(data_dir, rid)
    rel = _safe_rel(rel)
    allow_explicit = (root.get("kind") == "shortcut") and _root_is_explicit_system(root["path"])
    if _rel_blocked(rel, allow_explicit=allow_explicit):
        raise ValueError("That folder is not available.")
    parts = [p for p in rel.split("/") if p]
    full = _real(os.path.join(root["path"], *parts)) if parts else _real(root["path"])
    if not _is_under(full, root["path"]):
        raise ValueError("That path is outside the allowed folders.")
    if must_exist and not os.path.exists(full):
        raise ValueError("Not found.")
    if want == "dir" and not os.path.isdir(full):
        raise ValueError("Folder not found.")
    if want == "file" and not os.path.isfile(full):
        raise ValueError("File not found.")
    # Block protected path components on the resolved path below the root.
    try:
        below = os.path.relpath(full, root["path"])
    except ValueError:
        raise ValueError("That path is outside the allowed folders.")
    if below not in (os.curdir, ""):
        if _rel_blocked(below.replace("\\", "/"), allow_explicit=allow_explicit):
            raise ValueError("That folder is not available.")
    return root, full, rel


def _parent_location(data_dir, root, rel):
    """Location the Up button should go to, or None at a drive root."""
    rel = _safe_rel(rel)
    if rel:
        parts = [p for p in rel.split("/") if p]
        return {"root": root["id"], "path": "/".join(parts[:-1])}
    if root.get("kind") == "drive":
        return None
    # Shortcut at its own root: step up onto the containing drive, if we have one.
    try:
        parent = os.path.dirname(_real(root["path"]).rstrip("\\/"))
        if not parent or parent == root["path"]:
            return None
    except OSError:
        return None
    for d in list_fixed_drives():
        try:
            if _is_under(parent, d["path"]):
                rest = os.path.relpath(parent, d["path"])
                path = "" if rest in (os.curdir, "") else rest.replace("\\", "/")
                if _rel_blocked(path, allow_explicit=False):
                    return None
                return {"root": d["id"], "path": path}
        except (OSError, ValueError):
            continue
    return None


def list_dir(data_dir, rid, rel, offset=0, limit=80, media="image"):
    """List subfolders (all) and image files only. Never raises an unexpected 500-style error."""
    try:
        root, full, rel = resolve(data_dir, rid, rel, want="dir")
    except ValueError as e:
        try:
            shown = _safe_rel(rel) if rel is not None else ""
        except ValueError:
            shown = ""
        return {
            "root": rid, "path": shown,
            "crumbs": [], "dirs": [], "files": [],
            "total": 0, "total_dirs": 0, "total_files": 0,
            "offset": max(0, int(offset or 0)), "more": False,
            "parent": None, "kind": None,
            "error": str(e),
        }
    allow_explicit = (root.get("kind") == "shortcut") and _root_is_explicit_system(root["path"])
    dirs, files = [], []
    try:
        with os.scandir(full) as it:
            for e in it:
                try:
                    name = e.name
                    if name.startswith((".", "$")) or _name_blocked(name, allow_explicit=allow_explicit):
                        continue
                    try:
                        target = _real(e.path)
                    except OSError:
                        continue
                    if not _is_under(target, root["path"]):
                        continue
                    # Skip hidden/system children (AppData, etc.) unless this root is already there.
                    if not allow_explicit and _is_hidden_or_system(e.path):
                        continue
                    if e.is_dir(follow_symlinks=False) or os.path.isdir(target):
                        if os.path.isdir(target):
                            dirs.append(name)
                    elif e.is_file(follow_symlinks=False) or os.path.isfile(target):
                        if _is_kind(name, media) and os.path.isfile(target):
                            st = e.stat(follow_symlinks=True)
                            files.append((st.st_mtime, name, st.st_size))
                except OSError:
                    continue
    except OSError as e:
        crumbs = _crumbs(root, rel)
        return {
            "root": rid, "path": rel, "crumbs": crumbs,
            "dirs": [], "files": [],
            "total": 0, "total_dirs": 0, "total_files": 0,
            "offset": max(0, int(offset or 0)), "more": False,
            "parent": _parent_location(data_dir, root, rel),
            "kind": root.get("kind"),
            "error": "Couldn't open that folder.",
        }
    dirs.sort(key=lambda n: n.lower())
    files.sort(key=lambda x: (-x[0], x[1].lower()))
    crumbs = _crumbs(root, rel)
    base = rel
    join = lambda n: (base + "/" + n) if base else n
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    combined = [("dir", n, None, None) for n in dirs] + [("file", n, mt, sz) for mt, n, sz in files]
    page = combined[offset:offset + limit]
    page_dirs = [{"name": n, "path": join(n)} for kind, n, mt, sz in page if kind == "dir"]
    page_files = [{"name": n, "path": join(n), "size": sz, "mtime": int(mt)} for kind, n, mt, sz in page if kind == "file"]
    return {
        "root": rid,
        "path": base,
        "crumbs": crumbs,
        "dirs": page_dirs,
        "files": page_files,
        "total": len(files),
        "total_dirs": len(dirs),
        "total_files": len(files),
        "offset": offset,
        "more": offset + limit < len(combined),
        "parent": _parent_location(data_dir, root, rel),
        "kind": root.get("kind"),
    }


def _crumbs(root, rel):
    crumbs, acc = [{"name": root["name"], "path": ""}], []
    for p in [x for x in (rel or "").split("/") if x]:
        acc.append(p)
        crumbs.append({"name": p, "path": "/".join(acc)})
    return crumbs


def _walk_images(data_dir, budget=6000, max_depth=8, query=None, media="image"):
    """Yield images from shortcut folders only (not whole drives)."""
    roots = [r for r in load_roots(data_dir) if r["exists"]]
    prefer = {"output": 0, "input": 1}
    roots = sorted(roots, key=lambda r: prefer.get(r["id"], 9))
    q = (query or "").strip().lower()
    found = []
    left = [budget]

    def walk(rid, root_path, current, rel, depth, allow_explicit):
        if left[0] <= 0 or depth > max_depth:
            return
        try:
            it = os.scandir(current)
        except OSError:
            return
        with it:
            sub = []
            for e in it:
                if left[0] <= 0:
                    return
                left[0] -= 1
                try:
                    name = e.name
                    if name.startswith((".", "$")) or _name_blocked(name, allow_explicit=allow_explicit):
                        continue
                    target = _real(e.path)
                    if not _is_under(target, root_path):
                        continue
                    if not allow_explicit and _is_hidden_or_system(e.path):
                        continue
                    if e.is_dir(follow_symlinks=False) or (os.path.isdir(target) and not e.is_file(follow_symlinks=False)):
                        if os.path.isdir(target):
                            sub.append((target, (rel + "/" + name) if rel else name))
                    elif _is_kind(name, media) and os.path.isfile(target):
                        if q and q not in name.lower():
                            continue
                        st = e.stat(follow_symlinks=True)
                        found.append((st.st_mtime, rid, (rel + "/" + name) if rel else name, name, st.st_size))
                except OSError:
                    continue
            for target, nrel in sub:
                walk(rid, root_path, target, nrel, depth + 1, allow_explicit)

    for r in roots:
        allow = _root_is_explicit_system(r["path"])
        walk(r["id"], r["path"], r["path"], "", 0, allow)
        if left[0] <= 0:
            break
    found.sort(key=lambda x: -x[0])
    return found


def recent(data_dir, offset=0, limit=80, media="image"):
    items = _walk_images(data_dir, media=media)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    page = items[offset:offset + limit]
    return {
        "items": [{"root": rid, "path": rel, "name": name, "size": sz, "mtime": int(mt)} for mt, rid, rel, name, sz in page],
        "total": len(items),
        "offset": offset,
        "more": offset + limit < len(items),
        "truncated": False,
        "scope": "shortcuts",
    }


def _search_dir_skipped(name, *, allow_explicit=False):
    """True if this directory name should not be descended into during search."""
    n = (name or "").lower().rstrip(". ")
    if not n or n in SKIP_NAMES or n in PROTECTED_ALWAYS or n in SEARCH_SKIP_ALWAYS:
        return True
    if n.startswith(".") or n.startswith("$"):
        return True
    if n in SEARCH_SKIP_UNLESS_EXPLICIT and not allow_explicit:
        return True
    return False


def _folder_label(root, rel_file):
    """Display path of the folder that contains rel_file, plus the relative parent."""
    parts = [p for p in (rel_file or "").replace("\\", "/").split("/") if p]
    parent = "/".join(parts[:-1])
    letter = root.get("letter")
    if letter:
        shown = letter + ":\\"
        if parent:
            shown += parent.replace("/", "\\")
        return shown, parent
    base = (root.get("path") or "").rstrip("\\/")
    if parent:
        shown = base + "\\" + parent.replace("/", "\\")
    else:
        shown = base or (root.get("name") or "")
    return shown, parent


def _scope_label(root, rel):
    letter = root.get("letter")
    rel = "/".join([p for p in (rel or "").replace("\\", "/").split("/") if p])
    if letter:
        shown = letter + ":\\"
        if rel:
            shown += rel.replace("/", "\\")
        return shown
    base = (root.get("path") or "").rstrip("\\/")
    if rel:
        return base + "\\" + rel.replace("/", "\\")
    return base or (root.get("name") or "folder")


def search(data_dir, query, offset=0, limit=80, rid=None, rel="", media="image"):
    """Recursively search the selected drive or folder for images whose names contain query.

    Current folder is scanned first (so those hits are instant and sorted first),
    then a breadth-first walk of subfolders. System and hidden directories are
    skipped. Caps results, depth, visits and wall time so a whole-drive search
    cannot run forever.
    """
    query = (query or "").strip()
    if len(query) < 2:
        raise ValueError("Type at least two letters to search.")
    q = query.lower()
    rid = (rid or "").strip() or None
    rel = rel or ""
    if not rid:
        existing = [r for r in load_roots(data_dir) if r.get("exists")]
        if not existing:
            raise ValueError("Pick a drive or folder to search.")
        rid = existing[0]["id"]
        rel = ""
    try:
        root, start, rel = resolve(data_dir, rid, rel, want="dir")
    except ValueError as e:
        return {
            "query": query, "items": [], "total": 0, "offset": 0, "more": False,
            "truncated": False, "timed_out": False, "here_count": 0,
            "scope": "", "scope_name": "", "error": str(e),
        }

    allow_explicit = (root.get("kind") == "shortcut") and _root_is_explicit_system(root["path"])
    deadline = time.monotonic() + float(SEARCH_TIMEOUT_SEC)
    found = []
    here_items = []
    deep_items = []
    visits = [0]
    timed_out = False
    hit_cap = False
    seen = set()

    def expired():
        return time.monotonic() >= deadline

    def note(rel_path, name, st, bucket):
        nonlocal hit_cap
        if q not in (name or "").lower():
            return
        if len(found) >= SEARCH_MAX_RESULTS:
            hit_cap = True
            return
        label, parent = _folder_label(root, rel_path)
        rec = (
            st.st_mtime, rid, rel_path, name, int(st.st_size),
            parent, label,
        )
        found.append(rec)
        bucket.append(rec)

    def scan_dir(current, rel_here, depth, bucket, collect_subs):
        nonlocal timed_out
        subs = []
        if expired():
            timed_out = True
            return subs
        try:
            key = _norm(current)
        except OSError:
            return subs
        if key in seen:
            return subs
        seen.add(key)
        try:
            it = os.scandir(current)
        except OSError:
            return subs
        with it:
            for e in it:
                if expired():
                    timed_out = True
                    break
                if found and (time.monotonic() >= (deadline - float(SEARCH_TIMEOUT_SEC) + float(SEARCH_SOFT_SEC))):
                    break
                if len(found) >= SEARCH_MAX_RESULTS:
                    break
                visits[0] += 1
                if visits[0] > SEARCH_MAX_VISITS:
                    break
                try:
                    name = e.name
                    if e.is_dir(follow_symlinks=False) or (os.path.isdir(e.path) and not e.is_file(follow_symlinks=False)):
                        if _search_dir_skipped(name, allow_explicit=allow_explicit):
                            continue
                        if not allow_explicit and _is_hidden_or_system(e.path):
                            continue
                        try:
                            target = _real(e.path)
                        except OSError:
                            continue
                        if not _is_under(target, root["path"]):
                            continue
                        if os.path.isdir(target) and collect_subs:
                            nrel = (rel_here + "/" + name) if rel_here else name
                            subs.append((target, nrel))
                    elif _is_kind(name, media):
                        try:
                            target = _real(e.path)
                        except OSError:
                            continue
                        if not _is_under(target, root["path"]) or not os.path.isfile(target):
                            continue
                        if not allow_explicit and _is_hidden_or_system(e.path):
                            continue
                        nrel = (rel_here + "/" + name) if rel_here else name
                        st = e.stat(follow_symlinks=True)
                        note(nrel, name, st, bucket)
                except OSError:
                    continue
        return subs

    # Phase 1: current folder only (instant, returned first).
    first_subs = scan_dir(start, rel, 0, here_items, True)

    # Phase 2: breadth-first through subfolders.
    queue = [(p, r, 1) for p, r in first_subs]
    qi = 0
    while qi < len(queue):
        # Return promptly once we already have hits (don't walk the rest of a
        # large drive for the full hard timeout just to look for more).
        if found and (time.monotonic() >= (deadline - float(SEARCH_TIMEOUT_SEC) + float(SEARCH_SOFT_SEC))):
            hit_cap = True
            break
        if expired() or len(found) >= SEARCH_MAX_RESULTS or visits[0] > SEARCH_MAX_VISITS:
            if expired():
                timed_out = True
            if len(found) >= SEARCH_MAX_RESULTS or visits[0] > SEARCH_MAX_VISITS:
                hit_cap = True
            break
        current, rel_here, depth = queue[qi]
        qi += 1
        if depth > SEARCH_MAX_DEPTH:
            continue
        subs = scan_dir(current, rel_here, depth, deep_items, True)
        if depth < SEARCH_MAX_DEPTH:
            for p, r in subs:
                queue.append((p, r, depth + 1))

    # Keep current-folder hits first; then deeper matches newest-first.
    here_items.sort(key=lambda x: (-x[0], x[3].lower()))
    deep_items.sort(key=lambda x: (-x[0], x[3].lower()))
    ordered = here_items + deep_items

    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 80), 200))
    page = ordered[offset:offset + limit]
    items = []
    for mt, item_rid, rel_path, name, sz, parent, label in page:
        items.append({
            "root": item_rid,
            "path": rel_path,
            "name": name,
            "size": sz,
            "mtime": int(mt),
            "folder": parent,
            "folder_label": label,
        })
    return {
        "query": query,
        "items": items,
        "total": len(ordered),
        "offset": offset,
        "more": offset + limit < len(ordered),
        "truncated": bool(hit_cap),
        "timed_out": bool(timed_out),
        "here_count": len(here_items),
        "scanned": visits[0],
        "scope": _scope_label(root, rel),
        "scope_name": root.get("name") or _scope_label(root, rel),
        "root": root["id"],
        "path": rel,
    }


def video_frame_jpeg(full, cache, size=384):
    """First second of a video as a JPEG thumbnail (needs ffmpeg). Returns the cache path."""
    import subprocess
    if not FFMPEG:
        raise ValueError("No ffmpeg to make a video preview.")
    if not os.path.isfile(cache):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "0.5", "-i", full, "-frames:v", "1",
                        "-vf", "scale='min(%d,iw)':-2" % size, cache], timeout=30, check=True,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    return cache


def thumb_bytes(data_dir, thumbs_dir, rid, rel, size=384, quality=86):
    from PIL import Image, ImageOps
    _ensure_heic_opener()
    root, full, rel = resolve(data_dir, rid, rel, want="file")
    if os.path.splitext(full)[1].lower() in VIDEO_EXT:
        st = os.stat(full)
        key = hashlib.sha1(("pcv|%s|%s|%s|%d" % (full, st.st_mtime, st.st_size, size)).encode("utf-8", "replace")).hexdigest()
        folder = os.path.join(thumbs_dir, "pc")
        os.makedirs(folder, exist_ok=True)
        return open(video_frame_jpeg(full, os.path.join(folder, key + ".jpg"), size), "rb").read()
    if not _is_img(full):
        raise ValueError("Not an image.")
    st = os.stat(full)
    key = hashlib.sha1(("pc|exif2|%s|%s|%s|%d" % (full, st.st_mtime, st.st_size, size)).encode("utf-8", "replace")).hexdigest()
    folder = os.path.join(thumbs_dir, "pc")
    os.makedirs(folder, exist_ok=True)
    cache = os.path.join(folder, key + ".jpg")
    if not os.path.isfile(cache):
        with Image.open(full) as im:
            im = ImageOps.exif_transpose(im) or im
            try:
                im.draft("RGB", (size * 2, size * 2))
            except Exception:
                pass
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif im.mode == "L":
                im = im.convert("RGB")
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            im.save(cache, "JPEG", quality=quality)
    return open(cache, "rb").read()


def open_media_file(data_dir, rid, rel, max_bytes):
    """Return (full path, basename, kind) for a video / audio file inside a browsable root. Read-only."""
    root, full, rel = resolve(data_dir, rid, rel, want="file")
    kind = media_kind(full)
    if kind not in ("video", "audio"):
        raise ValueError("Not a video or audio file.")
    size = os.path.getsize(full)
    if size > max_bytes:
        raise ValueError("That file is larger than the %d MB limit." % (max_bytes // (1024 * 1024)))
    if size <= 0:
        raise ValueError("That file is empty.")
    return full, os.path.basename(full), kind


def open_image_file(data_dir, rid, rel, max_bytes):
    """Return (bytes, basename) for an image inside a browsable root. Read-only."""
    root, full, rel = resolve(data_dir, rid, rel, want="file")
    if not _is_img(full):
        raise ValueError("Not an image file.")
    size = os.path.getsize(full)
    if size > max_bytes:
        raise ValueError("That image is larger than the upload limit.")
    if size <= 0:
        raise ValueError("That file is empty.")
    with open(full, "rb") as f:
        data = f.read()
    return data, os.path.basename(full)
