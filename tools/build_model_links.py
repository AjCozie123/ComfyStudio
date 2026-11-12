"""Build deps/model_links.json: official download links for model files, by file name.

Sources: the official ComfyUI templates the built-in engines came from (tools/official_templates) and, when found,
ComfyUI's own installed template packages. Each template lists its models as
{"name", "url", "directory"} in node properties. Only Hugging Face links are kept (the official hosts).

    python tools/build_model_links.py [path to ComfyUI's site-packages]
"""
import glob, json, os, sys, urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "deps", "model_links.json")
ALLOWED = ("huggingface.co",)


def nodes_of(w):
    nodes = list(w.get("nodes") or [])
    for sg in ((w.get("definitions") or {}).get("subgraphs") or []):
        nodes += sg.get("nodes") or []
    return nodes


def harvest(files):
    links = {}
    for f in files:
        try:
            w = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(w, dict):
            continue
        for nd in nodes_of(w):
            for m in ((nd.get("properties") or {}).get("models") or []):
                name, url, folder = m.get("name"), m.get("url"), m.get("directory")
                if not (isinstance(name, str) and isinstance(url, str) and isinstance(folder, str)):
                    continue
                host = urllib.parse.urlsplit(url).hostname or ""
                if url.startswith("https://") and any(host == h or host.endswith("." + h) for h in ALLOWED):
                    links.setdefault(name.lower(), {"name": name, "url": url, "directory": folder.strip("/\\")})
    return links


def main():
    files = glob.glob(os.path.join(ROOT, "tools", "official_templates", "*.json"))
    site = sys.argv[1] if len(sys.argv) > 1 else None
    if not site:
        try:
            sys.path.insert(0, ROOT)
            import app_paths
            s = app_paths.load(os.path.join(ROOT, "data"))
            site = os.path.join(os.path.dirname(s.get("comfy_root") or ""), "python_embeded", "Lib", "site-packages")
        except Exception:
            site = None
    if site and os.path.isdir(site):
        for pkg in glob.glob(os.path.join(site, "comfyui_workflow_templates*")):
            files += glob.glob(os.path.join(pkg, "**", "*.json"), recursive=True)
    links = harvest(files)
    # verified official repos for files the templates don't link (deps/verified_repos.json)
    vr = os.path.join(ROOT, "deps", "verified_repos.json")
    if os.path.isfile(vr):
        import re as _re, urllib.request as _u
        for r in json.load(open(vr, encoding="utf-8")).get("repos") or []:
            try:
                req = _u.Request("https://huggingface.co/api/models/" + r["repo"], headers={"User-Agent": "ComfyStudio"})
                sib = json.load(_u.urlopen(req, timeout=30)).get("siblings") or []
            except Exception as e:
                print("  skipped", r["repo"], e)
                continue
            for s in sib:
                f = s.get("rfilename") or ""
                if "/" not in f and _re.search(r["pattern"], f):
                    links.setdefault(f.lower(), {"name": f, "url": "https://huggingface.co/%s/resolve/main/%s" % (r["repo"], f),
                                                 "directory": r["directory"]})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"generated_from": len(files), "links": dict(sorted(links.items()))}, open(OUT, "w", encoding="utf-8"), indent=1)
    print("templates read:", len(files), "| model links:", len(links), "->", OUT)


if __name__ == "__main__":
    main()
