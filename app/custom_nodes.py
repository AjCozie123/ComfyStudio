"""Custom node packs that aren't in the Comfy registry: install from a GitHub link or a zip, remove again.

These are other people's code that ComfyUI runs, so the app only ever does this when the user taps Install after
seeing exactly what will be added. Rules:
  * GitHub links only (https://github.com/owner/repo); zips are checked for unsafe paths
  * never overwrite a pack that's already there
  * a pack's requirements are installed with ComfyUI's OWN Python (never the system one), and torch / xformers /
    triton lines are skipped so a pack can't replace ComfyUI's GPU libraries
  * every install is recorded (data/custom_nodes.json); Remove moves the folder to custom_nodes_quarantine
  * ComfyUI must restart to load new nodes
"""
from __future__ import annotations

import io, json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, zipfile

GH_RE = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
SKIP_REQ = re.compile(r"^\s*(torch|torchvision|torchaudio|xformers|triton|nvidia-|--)", re.I)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_ZIP = 300 * 1024 * 1024

_lock = threading.Lock()
JOBS = {}          # id -> {state, name, log, ...}


def parse_github(url):
    """'https://github.com/owner/repo' or 'owner/repo' -> (owner, repo) or None."""
    m = GH_RE.match((url or "").strip())
    if not m or m.group(2) in (".", ".."):
        return None
    return m.group(1), m.group(2)


def comfy_python(comfy_root):
    """ComfyUI's own Python: portable (python_embeded), a venv, or None."""
    base = os.path.dirname(comfy_root)
    for p in (os.path.join(base, "python_embeded", "python.exe"), os.path.join(comfy_root, "venv", "Scripts", "python.exe"),
              os.path.join(comfy_root, ".venv", "Scripts", "python.exe"), os.path.join(base, ".venv", "Scripts", "python.exe")):
        if os.path.isfile(p):
            return p
    return None


def _staging(nodes_dir):
    """Work folder NEXT TO custom_nodes (ComfyUI would try to load anything inside custom_nodes)."""
    d = os.path.join(os.path.dirname(nodes_dir), "custom_nodes_staging")
    os.makedirs(d, exist_ok=True)
    return d


def _record_path(data_dir):
    return os.path.join(data_dir, "custom_nodes.json")


def installed(data_dir):
    try:
        return json.load(open(_record_path(data_dir), encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _record(data_dir, item):
    items = [i for i in installed(data_dir) if i.get("name") != item["name"]] + [item]
    json.dump(items, open(_record_path(data_dir), "w", encoding="utf-8"), indent=1)


def _safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "").strip("._")
    if not name or name.lower() in ("custom_nodes", "comfyui"):
        raise ValueError("That pack has no usable folder name.")
    return name[:80]


def _pip_requirements(py, pack_dir, log):
    req = os.path.join(pack_dir, "requirements.txt")
    if not os.path.isfile(req):
        log.append("No requirements.txt: nothing to install.")
        return True
    lines = [l for l in open(req, encoding="utf-8", errors="ignore").read().splitlines() if l.strip() and not l.strip().startswith("#")]
    keep = [l for l in lines if not SKIP_REQ.match(l)]
    skipped = [l for l in lines if SKIP_REQ.match(l)]
    if skipped:
        log.append("Skipped (ComfyUI's own GPU libraries): " + ", ".join(skipped[:8]))
    if not keep:
        return True
    if not py:
        log.append("ComfyUI's Python wasn't found, so these weren't installed: " + ", ".join(keep[:8]))
        return False
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("\n".join(keep))
    tmp.close()
    try:
        r = subprocess.run([py, "-s", "-m", "pip", "install", "--disable-pip-version-check", "-r", tmp.name],
                           capture_output=True, text=True, timeout=1200, creationflags=NO_WINDOW)
        log.append((r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
        return r.returncode == 0
    finally:
        os.remove(tmp.name)


def _pack_root(folder):
    """The folder inside an extracted zip that is the node pack (has __init__.py)."""
    if os.path.isfile(os.path.join(folder, "__init__.py")):
        return folder
    subs = [os.path.join(folder, d) for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))]
    for s in subs:
        if os.path.isfile(os.path.join(s, "__init__.py")):
            return s
    return None


def _finish(job, data_dir, nodes_dir, py, staged, name, source):
    dest = os.path.join(nodes_dir, name)
    if os.path.exists(dest):
        raise ValueError("A node pack called %s is already installed." % name)
    shutil.move(staged, dest)
    job["log"].append("Added %s" % dest)
    ok = _pip_requirements(py, dest, job["log"])
    _record(data_dir, {"name": name, "source": source, "path": dest, "t": time.time(), "requirements_ok": ok})
    job.update(state="done", requirements_ok=ok,
               message="Installed %s. Restart ComfyUI to load its nodes." % name + ("" if ok else " Some of its requirements didn't install; see the log."))


def _run(job_id, fn):
    job = JOBS[job_id]
    try:
        fn(job)
    except Exception as e:
        job.update(state="error", message=str(e)[:300])


def start_github(url, data_dir, nodes_dir, py, git=None, opener=None):
    """Clone (or download) a GitHub node pack into custom_nodes, in the background. Returns a job id."""
    gh = parse_github(url)
    if not gh:
        raise ValueError("That isn't a GitHub repository link (https://github.com/owner/repo).")
    owner, repo = gh
    name = _safe_name(repo)
    if os.path.exists(os.path.join(nodes_dir, name)):
        raise ValueError("A node pack called %s is already installed." % name)
    jid = "n%d" % int(time.time() * 1000)
    JOBS[jid] = {"id": jid, "state": "running", "name": name, "source": "https://github.com/%s/%s" % (owner, repo), "log": []}

    def work(job):
        tmp = tempfile.mkdtemp(prefix="cs_node_", dir=_staging(nodes_dir))
        staged = os.path.join(tmp, name)
        try:
            git_exe = git if git is not None else shutil.which("git")
            src = job["source"]
            if git_exe:
                r = subprocess.run([git_exe, "clone", "--depth", "1", src if not src.startswith("file:") else src[5:], staged],
                                   capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
                if r.returncode != 0:
                    raise ValueError("Couldn't download it: " + ((r.stderr or "").strip().splitlines() or ["git failed"])[-1])
            else:                                   # no git: GitHub's zip of the default branch
                data = (opener or urllib.request.urlopen)(urllib.request.Request(
                    "https://codeload.github.com/%s/%s/zip/HEAD" % (owner, repo), headers={"User-Agent": "ComfyStudio"}), timeout=300).read()
                _extract_zip(data, tmp)
                root = _pack_root(tmp) or next((os.path.join(tmp, d) for d in os.listdir(tmp)), None)
                if not root:
                    raise ValueError("The download was empty.")
                os.rename(root, staged)
            if not os.path.isfile(os.path.join(staged, "__init__.py")):
                raise ValueError("That repository isn't a ComfyUI node pack (no __init__.py).")
            _finish(job, data_dir, nodes_dir, py, staged, name, job["source"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threading.Thread(target=_run, args=(jid, work), daemon=True).start()
    return jid


def _extract_zip(data, into):
    if len(data) > MAX_ZIP:
        raise ValueError("That zip is larger than %d MB." % (MAX_ZIP // (1024 * 1024)))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.namelist():
            if m.startswith(("/", "\\")) or ".." in m.replace("\\", "/").split("/") or ":" in m:
                raise ValueError("The zip contains unsafe paths, so it wasn't used.")
        z.extractall(into)


def start_zip(data, fname, data_dir, nodes_dir, py):
    """Install a node pack from a zip the user picked. Returns a job id."""
    tmp = tempfile.mkdtemp(prefix="cs_nodezip_", dir=_staging(nodes_dir))
    try:
        _extract_zip(data, tmp)
        root = _pack_root(tmp)
        if not root:
            raise ValueError("No ComfyUI node pack in that zip (a folder with __init__.py).")
        name = _safe_name(os.path.basename(root) if root != tmp else os.path.splitext(os.path.basename(fname or "nodes"))[0])
        if os.path.exists(os.path.join(nodes_dir, name)):
            raise ValueError("A node pack called %s is already installed." % name)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    jid = "n%d" % int(time.time() * 1000)
    JOBS[jid] = {"id": jid, "state": "running", "name": name, "source": "zip: " + os.path.basename(fname or ""), "log": []}

    def work(job):
        try:
            if root != tmp:
                staged = root
            else:                                    # files at the top of the zip: gather them into one folder
                staged = tempfile.mkdtemp(prefix="cs_pack_", dir=_staging(nodes_dir))
                for item in os.listdir(tmp):
                    shutil.move(os.path.join(tmp, item), os.path.join(staged, item))
            _finish(job, data_dir, nodes_dir, py, staged, name, job["source"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threading.Thread(target=_run, args=(jid, work), daemon=True).start()
    return jid


def remove(name, data_dir, nodes_dir):
    """Take out a pack this app installed: moved to custom_nodes_quarantine (restorable), never deleted."""
    rec = next((i for i in installed(data_dir) if i.get("name") == name), None)
    if not rec:
        raise ValueError("Comfy Studio only removes node packs it installed itself.")
    src = os.path.join(nodes_dir, rec["name"])
    if not os.path.isdir(src):
        raise ValueError("That pack's folder is already gone.")
    q = os.path.join(os.path.dirname(nodes_dir), "custom_nodes_quarantine")
    os.makedirs(q, exist_ok=True)
    dst = os.path.join(q, "%s_%s" % (rec["name"], time.strftime("%Y%m%d_%H%M%S")))
    shutil.move(src, dst)
    items = [i for i in installed(data_dir) if i.get("name") != name]
    json.dump(items, open(_record_path(data_dir), "w", encoding="utf-8"), indent=1)
    return {"moved_to": dst}


def node_sources(text):
    """{class_type: {"registry": id, "github": url}} from the pack stamps ComfyUI writes into a saved workflow."""
    try:
        w = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {}
    if not isinstance(w, dict):
        return {}
    nodes = list(w.get("nodes") or [])
    for sg in ((w.get("definitions") or {}).get("subgraphs") or []):
        nodes += sg.get("nodes") or []
    out = {}
    for n in nodes:
        p = n.get("properties") or {}
        cls = n.get("type") or ""
        cnr, aux = p.get("cnr_id"), p.get("aux_id")
        if not cls or cnr == "comfy-core":
            continue
        cur = out.setdefault(cls, {})
        if cnr and not cur.get("registry"):
            cur["registry"] = str(cnr)
        if aux and parse_github(str(aux)) and not cur.get("github"):
            o, r = parse_github(str(aux))
            cur["github"] = "https://github.com/%s/%s" % (o, r)
    return {k: v for k, v in out.items() if v}


# ------------------------------------------------------------------ the packs the built-in engines need
def required_packs(app_dir):
    try:
        return json.load(open(os.path.join(app_dir, "deps", "node_packs.json"), encoding="utf-8")).get("packs") or []
    except (OSError, ValueError):
        return []


def required_status(app_dir, nodes_dir, have_classes):
    """Each required pack with installed: True/False (its probe node is loaded, or its folder exists)."""
    out = []
    for p in required_packs(app_dir):
        folders = [p["folder"]] + list(p.get("aliases") or [])
        on_disk = any(os.path.isdir(os.path.join(nodes_dir, f)) for f in folders)
        loaded = bool(p.get("probe")) and p["probe"] in (have_classes or ())
        out.append(dict(p, installed=on_disk or loaded, loaded=loaded))
    return out


def _fetch_pinned(repo, commit, staged, git_exe, opener=None):
    """The pack at exactly the tested commit: git clone + checkout, or GitHub's zip of that commit."""
    if git_exe:
        r = subprocess.run([git_exe, "clone", "-q", "https://github.com/" + repo, staged],
                           capture_output=True, text=True, timeout=900, creationflags=NO_WINDOW)
        if r.returncode == 0:
            r = subprocess.run([git_exe, "-C", staged, "checkout", "-q", commit], capture_output=True, text=True,
                               timeout=120, creationflags=NO_WINDOW)
            if r.returncode == 0:
                return
        shutil.rmtree(staged, ignore_errors=True)
    tmp = staged + "_zip"
    os.makedirs(tmp, exist_ok=True)
    data = (opener or urllib.request.urlopen)(urllib.request.Request(
        "https://codeload.github.com/%s/zip/%s" % (repo, commit), headers={"User-Agent": "ComfyStudio"}), timeout=600).read()
    _extract_zip(data, tmp)
    root = _pack_root(tmp)
    if not root:
        raise ValueError("%s: the download has no node pack in it." % repo)
    os.rename(root, staged)
    shutil.rmtree(tmp, ignore_errors=True)


def start_required(app_dir, data_dir, nodes_dir, py, have_classes, git=None, opener=None):
    """Install every missing required pack (pinned commits / bundled copy), one after another. Returns a job id."""
    todo = [p for p in required_status(app_dir, nodes_dir, have_classes) if not p["installed"]]
    if not todo:
        raise ValueError("Every node pack Comfy Studio needs is already installed.")
    jid = "n%d" % int(time.time() * 1000)
    JOBS[jid] = {"id": jid, "state": "running", "name": "required packs", "log": [], "total": len(todo), "done": 0,
                 "failed": []}

    def work(job):
        git_exe = git if git is not None else shutil.which("git")
        for p in todo:
            job["message"] = "Installing %s (%d of %d)..." % (p["folder"], job["done"] + 1, len(todo))
            tmp = tempfile.mkdtemp(prefix="cs_node_", dir=_staging(nodes_dir))
            staged = os.path.join(tmp, p["folder"])
            try:
                if p.get("bundled"):
                    shutil.copytree(os.path.join(app_dir, *p["bundled"].split("/")), staged,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
                    source = "bundled with Comfy Studio"
                else:
                    _fetch_pinned(p["repo"], p["commit"], staged, git_exe, opener)
                    source = "https://github.com/%s@%s" % (p["repo"], p["commit"][:10])
                if not os.path.isfile(os.path.join(staged, "__init__.py")):
                    raise ValueError("no __init__.py")
                dest = os.path.join(nodes_dir, p["folder"])
                if os.path.exists(dest):
                    raise ValueError("a folder called %s is already in custom_nodes" % p["folder"])
                shutil.move(staged, dest)
                ok = _pip_requirements(py, dest, job["log"])
                _record(data_dir, {"name": p["folder"], "source": source, "path": dest, "t": time.time(), "requirements_ok": ok})
                job["log"].append("Installed %s (%s)" % (p["folder"], source))
            except Exception as e:
                job["failed"].append(p["folder"])
                job["log"].append("%s failed: %s" % (p["folder"], e))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            job["done"] += 1
        bad = job["failed"]
        job.update(state="error" if bad and len(bad) == len(todo) else "done",
                   message=("Installed %d node pack(s). Restart ComfyUI to load them." % (len(todo) - len(bad)))
                   + (" Couldn't install: %s (see the log)." % ", ".join(bad) if bad else ""))

    threading.Thread(target=_run, args=(jid, work), daemon=True).start()
    return jid
