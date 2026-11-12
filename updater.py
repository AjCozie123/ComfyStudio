"""In-app updater.

Two kinds of install:
  * git clone  -> git fetch / fast-forward pull (stops if the user edited app files)
  * zip from a GitHub release -> download the release zip, back up the app files, copy the new ones in
Either way data/ (PIN, library, settings, uploaded workflows) is never touched, and after a restart a watchdog
rolls the app files back if the new version doesn't start.

The GitHub repository is set in release.json ({"repo": "owner/name"}). Until it is set, nothing is checked online.
A release can mark itself critical (an update.json asset {"version", "critical", "min_safe_version", "notes"},
or "[critical]" in its notes); the app then shows a banner, and installs it by itself only if the user switched
on "Install important fixes automatically" (and only while nothing is generating).
"""
from __future__ import annotations

import json, os, re, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
VERSION_FILE = os.path.join(HERE, "VERSION")
RELEASE_FILE = os.path.join(HERE, "release.json")
UPD_DIR = os.path.join(DATA, "_update")
PENDING = os.path.join(UPD_DIR, "pending.json")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ZIP_HOSTS = ("github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
             "release-assets.githubusercontent.com")
KEEP = {"data", ".git", "__pycache__"}          # never replaced by an update

_cache = {"t": 0, "info": None}


def app_version():
    try:
        return open(VERSION_FILE, encoding="utf-8").read().strip()
    except OSError:
        return "dev"


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0"))[:4]) or (0,)


def release_repo():
    try:
        return (json.load(open(RELEASE_FILE, encoding="utf-8")).get("repo") or "").strip()
    except (OSError, ValueError):
        return ""


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ComfyStudio-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _allowed(url):
    host = urllib.parse.urlsplit(url or "").hostname or ""
    return (url or "").startswith("https://") and any(host == h or host.endswith("." + h) for h in ZIP_HOSTS)


def latest_release(force=False):
    """{version, notes, critical, min_safe, zip_url} for the newest GitHub release, or None (no repo / offline)."""
    repo = release_repo()
    if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return None
    if not force and _cache["info"] and time.time() - _cache["t"] < 600:
        return _cache["info"]
    rel = _get_json("https://api.github.com/repos/%s/releases/latest" % repo)
    info = {"version": (rel.get("tag_name") or "").lstrip("v"), "notes": (rel.get("body") or "")[:2000],
            "critical": "[critical]" in (rel.get("body") or "").lower(), "min_safe": None,
            "zip_url": rel.get("zipball_url"), "published": rel.get("published_at")}
    for a in rel.get("assets") or []:
        name = (a.get("name") or "").lower()
        url = a.get("browser_download_url")
        if name.endswith(".zip") and _allowed(url):
            info["zip_url"] = url
        if name == "update.json" and _allowed(url):
            try:
                meta = _get_json(url)
                info["critical"] = bool(meta.get("critical", info["critical"]))
                info["min_safe"] = meta.get("min_safe_version")
                info["notes"] = meta.get("notes") or info["notes"]
                info["version"] = meta.get("version") or info["version"]
            except Exception:
                pass
    _cache.update(t=time.time(), info=info)
    return info


def _git(*args, timeout=60):
    p = subprocess.run(["git", "-C", HERE] + list(args), capture_output=True, text=True,
                       timeout=timeout, creationflags=NO_WINDOW)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def is_git_install():
    return os.path.isdir(os.path.join(HERE, ".git"))


def _git_status(out, fetch):
    code, _, err = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code != 0:
        out["message"] = "This copy has no GitHub branch to update from."
        return out
    if fetch:
        code, _, err = _git("fetch", "--quiet", timeout=90)
        if code != 0:
            out["message"] = "Couldn't reach GitHub: " + (err.splitlines() or ["unknown error"])[-1]
            return out
    _, head, _ = _git("rev-parse", "--short", "HEAD")
    _, behind, _ = _git("rev-list", "--count", "HEAD..@{u}")
    _, ahead, _ = _git("rev-list", "--count", "@{u}..HEAD")
    _, dirty, _ = _git("status", "--porcelain", "--untracked-files=no")
    _, log, _ = _git("log", "--pretty=format:%s", "-n", "15", "HEAD..@{u}")
    out.update({"head": head, "behind": int(behind or 0), "ahead": int(ahead or 0),
                "changed_files": [l[3:] for l in dirty.splitlines() if l.strip()],
                "incoming": [l for l in log.splitlines() if l.strip()]})
    out["message"] = "You're up to date." if out["behind"] == 0 else "%d update%s available." % (
        out["behind"], "" if out["behind"] == 1 else "s")
    return out


def status(fetch=True):
    out = {"version": app_version(), "git": is_git_install(), "repo": release_repo(), "behind": 0, "critical": False}
    try:
        info = latest_release(force=fetch) if out["repo"] else None
    except Exception as e:
        info = None
        out["release_error"] = str(e)[:160]
    if info:
        newer = vtuple(info["version"]) > vtuple(out["version"])
        out.update(latest=info["version"], notes=info["notes"],
                   critical=bool(newer and (info["critical"] or (info["min_safe"] and vtuple(out["version"]) < vtuple(info["min_safe"])))))
    if out["git"]:
        try:
            return _git_status(out, fetch)
        except (OSError, subprocess.TimeoutExpired) as e:
            out["message"] = "Update check failed: %s" % e
            return out
    if not out["repo"]:
        out["message"] = "Updates switch on once this copy knows its GitHub page (release.json)."
        return out
    if not info:
        out["message"] = "Couldn't reach GitHub to check for updates."
        return out
    if vtuple(info["version"]) > vtuple(out["version"]):
        out["behind"] = 1
        out["message"] = ("Important fix available: %s" if out["critical"] else "Update available: %s") % info["version"]
    else:
        out["message"] = "You're up to date."
    return out


def _download(url, dest):
    if not _allowed(url):
        raise ValueError("The update link isn't a GitHub address, so it wasn't used.")
    req = urllib.request.Request(url, headers={"User-Agent": "ComfyStudio-updater"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def _app_root_in(folder):
    """The folder inside an extracted zip that holds server.py (GitHub zips wrap everything in one folder)."""
    for dp, dns, fns in os.walk(folder):
        if "server.py" in fns and "VERSION" in fns:
            return dp
        if dp.count(os.sep) - folder.count(os.sep) > 2:
            dns[:] = []
    return None


def apply_zip(zip_path=None):
    """Install a release zip over this copy (data/ kept). Returns the pending record. zip_path: for tests/offline."""
    old = app_version()
    os.makedirs(UPD_DIR, exist_ok=True)
    work = os.path.join(UPD_DIR, "new")
    shutil.rmtree(work, ignore_errors=True)
    if not zip_path:
        info = latest_release(force=True)
        if not info or not info.get("zip_url"):
            raise ValueError("No downloadable release was found.")
        zip_path = os.path.join(UPD_DIR, "release.zip")
        _download(info["zip_url"], zip_path)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():                 # no paths that escape the folder
            if m.startswith("/") or ".." in m.replace("\\", "/").split("/"):
                raise ValueError("The update file looks unsafe, so it wasn't used.")
        z.extractall(work)
    src = _app_root_in(work)
    if not src:
        raise ValueError("The update file doesn't contain Comfy Studio.")
    new = open(os.path.join(src, "VERSION"), encoding="utf-8").read().strip()
    backup = os.path.join(UPD_DIR, "backup_" + re.sub(r"[^\w.-]", "_", old))
    shutil.rmtree(backup, ignore_errors=True)
    os.makedirs(backup)
    for name in os.listdir(HERE):              # back up the app's own files
        if name in KEEP:
            continue
        p = os.path.join(HERE, name)
        (shutil.copytree if os.path.isdir(p) else shutil.copy2)(p, os.path.join(backup, name))
    for name in os.listdir(src):               # copy the new ones in
        if name in KEEP:
            continue
        s, d = os.path.join(src, name), os.path.join(HERE, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    removed = _prune(src)
    rec = {"from": old, "to": new, "backup": backup, "t": time.time(), "removed": removed}
    json.dump(rec, open(PENDING, "w", encoding="utf-8"))
    shutil.rmtree(work, ignore_errors=True)
    return rec


SHIPPED_DIRS = ("static", "workflows", "deps", "nodes", "tools")   # folders that belong to the app itself


def _prune(src):
    """Remove app files the new version no longer ships (e.g. a retired engine), only inside the app's own folders.
    They are in the backup already, so a rollback brings them back."""
    removed = []
    for top in SHIPPED_DIRS:
        new_top, old_top = os.path.join(src, top), os.path.join(HERE, top)
        if not (os.path.isdir(new_top) and os.path.isdir(old_top)):
            continue
        for dp, dns, fns in os.walk(old_top):
            dns[:] = [d for d in dns if d not in KEEP]
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), HERE)
                if not os.path.exists(os.path.join(src, rel)):
                    try:
                        os.remove(os.path.join(HERE, rel))
                        removed.append(rel.replace(os.sep, "/"))
                    except OSError:
                        pass
    return removed


def rollback():
    """Put the backed-up app files back (used by the watchdog when the new version doesn't start)."""
    rec = json.load(open(PENDING, encoding="utf-8"))
    for name in os.listdir(rec["backup"]):
        s, d = os.path.join(rec["backup"], name), os.path.join(HERE, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    os.remove(PENDING)
    return rec


def apply():
    st = status(fetch=True)
    if st.get("git"):
        if st.get("changed_files"):
            raise ValueError("Some app files were changed on this PC (%s). Updating would overwrite them, so it was "
                             "stopped. Move your changes into data/ or undo them, then try again."
                             % ", ".join(st["changed_files"][:5]))
        if not st.get("behind"):
            return {"updated": False, "message": "Already up to date."}
        code, out, err = _git("pull", "--ff-only", timeout=180)
        if code != 0:
            raise ValueError("The update couldn't be applied: " + (err.splitlines() or ["unknown error"])[-1])
        _, head, _ = _git("rev-parse", "--short", "HEAD")
        return {"updated": True, "head": head, "message": "Updated. Restarting the app…"}
    if not st.get("repo"):
        raise ValueError(st.get("message"))
    if not st.get("behind"):
        return {"updated": False, "message": "Already up to date."}
    rec = apply_zip()
    return {"updated": True, "version": rec["to"], "message": "Updated to %s. Restarting the app…" % rec["to"]}


def restart_soon(delay=1.0, port=None):
    """Start a fresh copy of the server after this one exits. If an update is pending, a watchdog checks that the
    new version answers within ~60 s and otherwise restores the previous app files and starts that instead."""
    script = os.path.join(HERE, "server.py")
    port = port or int(os.environ.get("H3STUDIO_PORT", "8198"))
    launcher = (
        "import json,os,subprocess,sys,time,urllib.request\n"
        "time.sleep(2.5)\n"
        "here=r'%s';pend=r'%s'\n"
        "def start(): subprocess.Popen([sys.executable, os.path.join(here,'server.py')], cwd=here, creationflags=%d)\n"
        "start()\n"
        "if os.path.exists(pend):\n"
        "    ok=False\n"
        "    for _ in range(30):\n"
        "        time.sleep(2)\n"
        "        try:\n"
        "            urllib.request.urlopen('http://127.0.0.1:%d/api/status', timeout=2); ok=True; break\n"
        "        except Exception: pass\n"
        "    if ok: os.remove(pend)\n"
        "    else:\n"
        "        sys.path.insert(0, here); import updater; updater.rollback(); start()\n"
    ) % (HERE, PENDING, NO_WINDOW | 0x00000008, port)
    subprocess.Popen([sys.executable, "-c", launcher], cwd=HERE, env=os.environ.copy(),
                     creationflags=NO_WINDOW | 0x00000008 | 0x00000200)
    threading.Timer(delay, lambda: os._exit(0)).start()
