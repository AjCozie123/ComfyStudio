"""MiniMax H3 AIO installer: node packs (pinned to the tested commits), their pip packages, the models.

Safe to run again: packs already present are left alone (never updated or deleted), finished model
files are skipped, interrupted downloads resume. Never installs or changes torch / numpy.
"""

import os
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CUSTOM_NODES = os.path.dirname(HERE)
COMFY = os.path.dirname(CUSTOM_NODES)
MODELS = os.path.join(COMFY, "models")
sys.path.insert(0, HERE)
import downloads  # noqa: E402

PACKS = [
    # (folder, repo, commit, why)
    ("ComfyUI-Spectrum-MiniMax-H3", "xmarre/ComfyUI-Spectrum-MiniMax-H3", "5161f0457bc8c52535212d6783eee73f439e1537", "Spectrum speed-up"),
    ("ComfyUI-H3-Motion-Context-MultiRef-V3", "Aitrepreneur/ComfyUI-H3-Motion-Context-MultiRef-V3", "d299ea552d49213e25a337f6e2f24fbf64e78f40", "seamless extensions"),
    ("Comfyui_Minimax_h3_latent_Upscaler-Plus", "xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus", "0e46b6d95259746ac82cf451f5b4f71b50849105", "two-stage latent upscale + refine"),
    ("ComfyUI-VideoHelperSuite", "Kosinkadink/ComfyUI-VideoHelperSuite", "4d907bee61e92c2e65af3bd6383a4e4d356126d1", "Load Video + multi-clip file writer"),
    ("ComfyUI-KJNodes", "kijai/ComfyUI-KJNodes", "d3cfe21625e5170126ce06fbfcfe1d88108688c3", "live preview"),
    ("ComfyUI-Fantastic-MiniMaxH3-PromptBuilder", "Adudeguyman/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder", "01b6d8d91180c6994840e34312a65febe02cc4f5", "MiniMax H3 Media Loader (all pictures / videos / audio)"),
    ("MaskVidExperiments", "drozbay/MaskVidExperiments", "c32ed8c17e6fe892a174ebf25fe98b1317de97fb", "Inpainting: subject crop / uncrop"),
    ("ComfyUI-NKD-Basic-Tools", "Nekodificador/ComfyUI-NKD-Basic-Tools", "4f487a5617f36fbd9d5e069bca92ea834a74d7a1", "Inpainting: mask ops + masked AV latent"),
]
NEVER = ("torch", "torchvision", "torchaudio", "numpy", "xformers")
# The upscaler pack ships under another folder name in some guides; either counts as installed.
ALIASES = {"Comfyui_Minimax_h3_latent_Upscaler-Plus": ("Comfyui_Minimax_h3_latent_Upscaler",)}


def have_pack(folder):
    return any(os.path.isdir(os.path.join(CUSTOM_NODES, f)) for f in (folder,) + ALIASES.get(folder, ()))


def clone(folder, repo, sha):
    dest = os.path.join(CUSTOM_NODES, folder)
    try:
        subprocess.run(["git", "clone", "-q", f"https://github.com/{repo}", dest], check=True)
        subprocess.run(["git", "-C", dest, "checkout", "-q", sha], check=True)
        return
    except (OSError, subprocess.CalledProcessError):
        print("    git not available - downloading the zip instead")
    tmp = dest + ".zip"
    urllib.request.urlretrieve(f"https://codeload.github.com/{repo}/zip/{sha}", tmp)
    with zipfile.ZipFile(tmp) as z:
        root = z.namelist()[0].split("/")[0]
        z.extractall(CUSTOM_NODES)
    os.remove(tmp)
    os.rename(os.path.join(CUSTOM_NODES, root), dest)


def pip_requirements(folder):
    req = os.path.join(CUSTOM_NODES, folder, "requirements.txt")
    if not os.path.exists(req):
        return
    wanted = []
    for line in open(req, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        name = line.split(";")[0]
        for sep in "<>=!~[ ":
            name = name.split(sep)[0]
        if name.lower() not in NEVER:
            wanted.append(line)
    if wanted:
        subprocess.run([sys.executable, "-s", "-m", "pip", "install", "--disable-pip-version-check", *wanted])


def main():
    print("\n[1/2] Node packs")
    for folder, repo, sha, why in PACKS:
        if have_pack(folder):
            print(f"  ok      {folder}")
            continue
        print(f"  install {folder}  ({why})")
        try:
            clone(folder, repo, sha)
            pip_requirements(folder)
        except Exception as e:
            print(f"    FAILED: {e}")

    print("\n[2/2] Models (from their original Hugging Face pages)")
    with_optional = "--no-optional" not in sys.argv
    todo = downloads.missing(MODELS, with_optional)
    for e in downloads.manifest(with_optional):
        if e not in todo:
            print(f"  ok      {e['file']}")
    for e in todo:
        print(f"  get     {e['file']}  ({e['size_gb']} GB, {e['for']})")
        try:
            downloads.fetch(MODELS, e, lambda d, t: print(f"\r          {d / 1e9:6.2f} / {t / 1e9:6.2f} GB", end=""))
            print()
        except Exception as err:
            print(f"\n    FAILED: {err}  (run install.bat again to resume)")

    print("\nDone. Start ComfyUI with run_minimax_h3_aio.bat and open MiniMax H3 AIO from the workflow templates (custom nodes).")
    bat = os.path.join(os.path.dirname(COMFY), "run_minimax_h3_aio.bat")
    if os.path.exists(os.path.join(os.path.dirname(COMFY), "python_embeded")) and not os.path.exists(bat):
        with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
            f.write('@echo off\ntitle ComfyUI (MiniMax H3 AIO)\ncd /d "%~dp0"\n'
                    'rem No --cache-lru: it never evicts, so the text encoder would stay in RAM next to the video model.\n'
                    '.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build --reserve-vram 2\n'
                    'pause\n')
        print(f"Created {bat}")


if __name__ == "__main__":
    main()
