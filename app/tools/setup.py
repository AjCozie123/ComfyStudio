# -*- coding: utf-8 -*-
"""Comfy Studio setup (run by "Install Comfy Studio.bat", which has already found a suitable Python).

Every step checks its own result. When something can't be done, it stops and says, in plain words, what is wrong
and exactly what to do; nothing fails silently. A summary is saved to app\\data\\setup_report.txt.

Steps: folder check, unblock downloaded files, Python packages, Windows' WebView2, find ComfyUI (running or on
disk), shortcuts, start Comfy Studio and confirm it really opened.
"""
from __future__ import annotations

import json
import os
import string
import struct
import subprocess
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # ...\ComfyStudio\app
TOP = os.path.dirname(APP)                                             # ...\ComfyStudio
DATA = os.path.join(APP, "data")
REPORT = os.path.join(DATA, "setup_report.txt")
PORT = int(os.environ.get("COMFYSTUDIO_PORT") or 8198)        # tests run a copy on another port
APP_URL = "http://127.0.0.1:%d/" % PORT
COMFY_PORTS = (8188, 8000, 8189, 8001, 8288)      # 8188 portable / manual, 8000 the ComfyUI Desktop app
REQUIRED = [("webview", "pywebview"), ("PIL", "pillow"), ("numpy", "numpy"), ("win32com.client", "pywin32")]
OPTIONAL = [("pillow_heif", "pillow-heif"), ("scipy", "scipy")]   # iPhone HEIC photos; smoother inpaint edges
NO_WINDOW = 0x08000000
_log = []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def say(msg=""):
    print(msg, flush=True)
    _log.append(msg)


def step(n, total, title):
    say("")
    say(" [%d/%d] %s" % (n, total, title))


def save_report(ok):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("Comfy Studio setup - %s - %s\n" % (time.strftime("%Y-%m-%d %H:%M"), "OK" if ok else "STOPPED"))
            f.write("Python: %s (%s)\n\n" % (sys.executable, sys.version.split()[0]))
            f.write("\n".join(_log) + "\n")
    except Exception:
        pass


def stop(problem, fix):
    say("")
    say(" ------------------------------------------------------------------")
    say(" SETUP STOPPED: " + problem)
    say("")
    for line in fix.split("\n"):
        say("   " + line)
    say(" ------------------------------------------------------------------")
    say(" (Saved in app\\data\\setup_report.txt. More help: the Help folder, 4 - Troubleshooting.txt)")
    save_report(False)
    sys.exit(1)


def ask(question, default="y"):
    try:
        a = input("   %s [%s/%s] " % (question, "Y" if default == "y" else "y", "N" if default == "n" else "n")).strip().lower()
    except EOFError:
        a = ""
    return (a or default).startswith("y")


def run(cmd, show=True):
    """Run a command, showing its output live; returns (exit code, last lines of output)."""
    tail = []
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", creationflags=0)
    except OSError as e:
        return 1, [str(e)]
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-25:]
        if show and line and not line.startswith(("  Downloading", "  Using cached", "Requirement already")):
            print("     " + line[:150], flush=True)
    return p.wait(), tail


# ------------------------------------------------------------------ 1. folder
def check_folder():
    need = ["server.py", "ComfyStudio.pyw", "app_paths.py", os.path.join("static", "index.html"), "templates", "workflows"]
    missing = [n for n in need if not os.path.exists(os.path.join(APP, n))]
    if missing:
        stop("Comfy Studio's files are incomplete (missing: %s)." % ", ".join(missing),
             "Download the zip again, right-click it > Extract All..., and run Install Comfy Studio.bat\n"
             "from the extracted folder. Don't run it from inside the zip.")
    low = TOP.lower()
    if "\\appdata\\local\\temp\\" in low + "\\":
        stop("Setup is running from a temporary folder (%s)." % TOP,
             "That happens when you double-click a file INSIDE the zip without extracting it.\n"
             "1. Close this window.\n"
             "2. Right-click the downloaded zip > Extract All... (or 7-Zip > Extract to \"ComfyStudio\\\").\n"
             "   A good place is C:\\ComfyStudio or your Documents folder.\n"
             "3. Open the extracted folder and double-click Install Comfy Studio.bat.")
    try:
        os.makedirs(DATA, exist_ok=True)
        probe = os.path.join(DATA, "_write_test.tmp")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError:
        stop("Comfy Studio can't save files in this folder (%s)." % TOP,
             "Windows protects folders like Program Files. Move the whole ComfyStudio folder somewhere\n"
             "you own, for example C:\\ComfyStudio or your Documents folder, then run Install again.")
    say("   Folder OK: %s" % TOP)
    if "\\onedrive" in low:
        say("   Note: this folder is inside OneDrive. It works, but OneDrive may sync every picture you make.")
        say("         If you'd rather not, move the ComfyStudio folder to e.g. C:\\ComfyStudio.")


# ------------------------------------------------------------------ 2. unblock
def unblock():
    """Files from the internet carry a 'downloaded' mark that makes Windows block or warn about the scripts."""
    n = 0
    for root, _dirs, files in os.walk(TOP):
        if os.path.join(TOP, "app", "data") in root:
            continue
        for f in files:
            try:
                os.remove(os.path.join(root, f) + ":Zone.Identifier")
                n += 1
            except OSError:
                pass
    say("   Unblocked %d downloaded file(s)." % n if n else "   Nothing to unblock.")


# ------------------------------------------------------------------ 3. Python + packages
def imports_ok(mods):
    code = "import importlib,sys\nbad=[]\nfor m in %r:\n    try: importlib.import_module(m)\n    except Exception as e: bad.append('%%s: %%s' %% (m, e))\nprint('|'.join(bad))" % list(mods)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    return [x for x in out.split("|") if x] if r.returncode == 0 else ["python: " + (r.stderr or "").strip()[-300:]]


def packages():
    if struct.calcsize("P") != 8:
        stop("This Python is 32-bit (%s)." % sys.executable,
             "Comfy Studio needs 64-bit Python. Install Python 3.12 (64-bit) from\n"
             "https://www.python.org/downloads/  (tick \"Add python.exe to PATH\"), then run Install again.")
    v = sys.version_info
    say("   Python %d.%d.%d, 64-bit: %s" % (v[0], v[1], v[2], sys.executable))
    if v < (3, 10):
        stop("Python %d.%d is too old." % (v[0], v[1]),
             "Install Python 3.12 from https://www.python.org/downloads/ (tick \"Add python.exe to PATH\"),\n"
             "then run Install again.")
    if run([sys.executable, "-m", "pip", "--version"], show=False)[0] != 0:
        say("   pip is missing: adding it...")
        run([sys.executable, "-m", "ensurepip", "--upgrade"])
    bad = imports_ok([m for m, _ in REQUIRED])
    if bad:
        pkgs = [p for m, p in REQUIRED]
        say("   Installing: %s (this can take a few minutes the first time)" % ", ".join(pkgs))
        code, tail = run([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check"] + pkgs)
        if code != 0:
            say("   That needed admin rights or failed; trying again just for your Windows user...")
            code, tail = run([sys.executable, "-m", "pip", "install", "--user", "--upgrade",
                              "--disable-pip-version-check"] + pkgs)
        bad = imports_ok([m for m, _ in REQUIRED])
        if bad:
            stop("Some Python packages could not be installed: " + "; ".join(bad),
                 "Usually one of these:\n"
                 "- No internet connection, or a firewall / VPN blocking pypi.org. Check, then run Install again.\n"
                 "- Antivirus blocked the download. Allow Python, then run Install again.\n"
                 "- A very new Python (3.14+): install Python 3.12 from https://www.python.org/downloads/\n"
                 "Last messages from the installer:\n  " + "\n  ".join(tail[-8:]))
    say("   Required packages OK (pywebview, Pillow, numpy, pywin32).")
    opt_bad = imports_ok([m for m, _ in OPTIONAL])
    if opt_bad:
        miss = [p for m, p in OPTIONAL if any(b.startswith(m + ":") for b in opt_bad)]
        run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"] + miss, show=False)
        still = imports_ok([m for m, _ in OPTIONAL])
        say("   Optional extras: %s." % ("installed" if not still else "skipped (not needed to run)"))


# ------------------------------------------------------------------ 4. WebView2 (the app's window)
def webview2_installed():
    import winreg
    key = r"SOFTWARE\{}Microsoft\EdgeUpdate\Clients\{{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}}"
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, key.format("WOW6432Node\\")), (winreg.HKEY_LOCAL_MACHINE, key.format("")),
                      (winreg.HKEY_CURRENT_USER, key.format(""))):
        try:
            with winreg.OpenKey(hive, sub) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def webview2():
    if webview2_installed():
        say("   WebView2 OK (the app opens in its own window).")
        return
    say("   Microsoft WebView2 is missing (older Windows 10). Without it the app opens in your web browser instead.")
    if run(["winget", "--version"], show=False)[0] == 0 and ask("Install WebView2 now (free, from Microsoft)?"):
        run(["winget", "install", "-e", "--id", "Microsoft.EdgeWebView2Runtime",
             "--accept-package-agreements", "--accept-source-agreements"])
        say("   WebView2 %s." % ("installed" if webview2_installed() else "not installed: the app will use your browser"))
    else:
        say("   You can install it any time from https://developer.microsoft.com/microsoft-edge/webview2/")


# ------------------------------------------------------------------ 5. ComfyUI
def comfy_running():
    for port in COMFY_PORTS:
        url = "http://127.0.0.1:%d" % port
        try:
            with urllib.request.urlopen(url + "/system_stats", timeout=2) as r:
                if "system" in json.loads(r.read().decode("utf-8", "replace") or "{}"):
                    return url
        except Exception:
            continue
    return None


def _is_comfy_dir(d):
    j = lambda *p: os.path.isdir(os.path.join(d, *p))
    return j("models") and (j("input") or j("output")) and (
        os.path.isfile(os.path.join(d, "main.py")) or j("comfy") or j("user") or j("custom_nodes"))


def comfy_on_disk():
    """ComfyUI folders on this PC: the Desktop app's own setting, then the usual places, then any drive's top
    folders whose name contains 'comfy' (two levels deep, so it stays quick)."""
    found = []
    add = lambda d, kind: found.append((os.path.normpath(d), kind)) if d and _is_comfy_dir(d) and \
        os.path.normpath(d).lower() not in [f[0].lower() for f in found] else None
    try:   # ComfyUI Desktop keeps its folder in %APPDATA%\ComfyUI\config.json
        cfg = json.load(open(os.path.join(os.environ.get("APPDATA", ""), "ComfyUI", "config.json"), encoding="utf-8"))
        add(cfg.get("basePath") or "", "desktop")
    except Exception:
        pass
    home = os.path.expanduser("~")
    for d in (os.path.join(home, "Documents", "ComfyUI"), os.path.join(home, "ComfyUI"), os.path.join(home, "Desktop", "ComfyUI")):
        add(d, "desktop" if d.endswith(os.path.join("Documents", "ComfyUI")) else "manual")
    for letter in string.ascii_uppercase:
        root = "%s:\\" % letter
        if not os.path.isdir(root):
            continue
        try:
            first = [os.path.join(root, n) for n in os.listdir(root)]
        except OSError:
            continue
        for a in first:
            if not os.path.isdir(a) or a.lower().endswith(("\\windows", "\\$recycle.bin", "\\program files",
                                                            "\\program files (x86)", "\\programdata")):
                continue
            add(a, "manual")
            try:
                second = [os.path.join(a, n) for n in os.listdir(a)] if ("comfy" in a.lower() or "ai" in os.path.basename(a).lower()) else []
            except OSError:
                second = []
            for b in second:
                if os.path.isdir(b):
                    add(b, "manual")
                    if "comfy" in b.lower():
                        try:
                            for c in os.listdir(b):
                                add(os.path.join(b, c), "manual")
                        except OSError:
                            pass
    return found


def save_settings(values):
    path = os.path.join(DATA, "settings.json")
    try:
        s = json.load(open(path, encoding="utf-8"))
    except Exception:
        s = {}
    s.update(values)
    json.dump(s, open(path, "w", encoding="utf-8"), indent=2)


def comfyui():
    url = comfy_running()
    if url:
        sys.path.insert(0, APP)
        try:
            import app_paths
            det = app_paths.detect(url)
        except Exception:
            det = {}
        if det:
            save_settings(dict(det, paths_ok=True))
            say("   ComfyUI is running at %s" % url)
            say("   Its folder: %s" % det.get("comfy_root"))
            return
        save_settings({"comfy_url": url})
        say("   ComfyUI is running at %s (its folders are read when Comfy Studio starts)." % url)
        return
    say("   ComfyUI isn't running right now. Looking for it on this PC...")
    found = comfy_on_disk()
    pick = None
    if len(found) == 1:
        pick = found[0]
        say("   Found: %s" % pick[0])
    elif len(found) > 1:
        say("   Found more than one ComfyUI:")
        for i, (d, _k) in enumerate(found, 1):
            say("     %d) %s" % (i, d))
        try:
            a = input("   Which one do you use? Type its number and press Enter [1]: ").strip()
        except EOFError:
            a = ""
        pick = found[int(a) - 1] if a.isdigit() and 1 <= int(a) <= len(found) else found[0]
    else:
        say("   No ComfyUI folder found automatically.")
        try:
            typed = input("   If you have ComfyUI, paste its folder (the one with 'models' inside) and press Enter,\n"
                          "   or just press Enter to skip: ").strip().strip('"')
        except EOFError:
            typed = ""
        if typed and _is_comfy_dir(typed):
            pick = (typed, "manual")
        elif typed:
            say("   That folder doesn't look like ComfyUI (no 'models' and 'input'/'output' folders inside).")
    if not pick:
        say("   Skipped. Install ComfyUI from https://www.comfy.org/download , start it, and Comfy Studio")
        say("   connects to it by itself. (See the Help folder, 3 - ComfyUI.txt.)")
        return
    d, kind = pick
    port = 8000 if kind == "desktop" else 8188
    save_settings({"comfy_root": d, "input_dir": os.path.join(d, "input"), "output_dir": os.path.join(d, "output"),
                   "comfy_url": "http://127.0.0.1:%d" % port})
    say("   Saved. Start ComfyUI before generating (Comfy Studio looks for it at port %d;" % port)
    say("   if yours uses another port, Comfy Studio finds it when ComfyUI is running and you run Install again).")


# ------------------------------------------------------------------ 6. shortcuts
def pythonw():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.isfile(w) else sys.executable


def shortcuts():
    pw = pythonw()
    with open(os.path.join(DATA, "python_path.txt"), "w", encoding="utf-8") as f:
        f.write(pw)
    code = (
        "import sys, win32com.client\n"
        "sh = win32com.client.Dispatch('WScript.Shell')\n"
        "made = []\n"
        "import os\n"
        "test_dir = os.environ.get('COMFYSTUDIO_SHORTCUT_DIR')\n"
        "for where in (['test'] if test_dir else ['Desktop', 'Programs']):\n"
        "    folder = test_dir if test_dir else sh.SpecialFolders(where)\n"
        "    if not folder: continue\n"
        "    lnk = folder + '\\\\Comfy Studio.lnk'\n"
        "    s = sh.CreateShortcut(lnk)\n"
        "    s.TargetPath = sys.argv[1]\n"
        "    s.Arguments = '\"' + sys.argv[2] + '\\\\ComfyStudio.pyw\"'\n"
        "    s.WorkingDirectory = sys.argv[2]\n"
        "    s.IconLocation = sys.argv[2] + '\\\\comfy_studio.ico'\n"
        "    s.Description = 'Comfy Studio'\n"
        "    s.Save()\n"
        "    made.append(lnk)\n"
        "print('\\n'.join(made))\n")
    r = subprocess.run([sys.executable, "-c", code, pw, APP], capture_output=True, text=True, encoding="utf-8", errors="replace")
    links = [l for l in (r.stdout or "").splitlines() if l.strip()]
    if not links:
        say("   Couldn't make a shortcut (%s). Use Start Comfy Studio.bat to open the app." % ((r.stderr or "").strip()[-160:] or "unknown"))
        return
    for lnk in links:
        subprocess.run([sys.executable, os.path.join(APP, "tools", "set_shortcut_appid.py"), lnk], capture_output=True)
    say("   Shortcut added: Desktop and Start menu (\"Comfy Studio\").")


# ------------------------------------------------------------------ 7. start + confirm
def app_up():
    try:
        with urllib.request.urlopen(APP_URL, timeout=3) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def tail(path, n=12):
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().strip().splitlines()
        return lines[-n:]
    except Exception:
        return []


def port_taken_by_other():
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, creationflags=NO_WINDOW)
        return any(":%d " % PORT in l and "LISTENING" in l for l in r.stdout.splitlines())
    except Exception:
        return False


def start_and_confirm(wait=60):
    if app_up():
        say("   Comfy Studio is already running: %s" % APP_URL)
        return True
    env = dict(os.environ, H3STUDIO_PORT=str(PORT))
    subprocess.Popen([pythonw(), os.path.join(APP, "ComfyStudio.pyw")], cwd=APP, env=env, creationflags=NO_WINDOW | 0x00000200)
    t0 = time.time()
    while time.time() - t0 < wait:
        if app_up():
            say("   Comfy Studio is running (it opened in its own window, or your browser).")
            return True
        time.sleep(1)
    lines = tail(os.path.join(DATA, "startup_error.log")) + tail(os.path.join(DATA, "server_start.log"))
    why = "\n".join("  " + l for l in lines[-12:]) or "  (no error was written)"
    fix = ("The last messages were:\n" + why + "\n\n")
    if port_taken_by_other():
        fix += ("Another program is using port %d. Close it (or restart the PC), then double-click\n" % PORT +
                "Start Comfy Studio.bat.")
    else:
        fix += ("Double-click Start Comfy Studio.bat once more: the first start can be slow.\n"
                "If it still doesn't open, send app\\data\\setup_report.txt and app\\data\\server_start.log\n"
                "to whoever gave you Comfy Studio.")
    stop("Comfy Studio did not start within %d seconds." % wait, fix)


def main():
    total = 7          # (the title is printed by Install Comfy Studio.bat)
    step(1, total, "Checking the Comfy Studio folder");            check_folder()
    step(2, total, "Unblocking the downloaded files");             unblock()
    step(3, total, "Python packages");                              packages()
    step(4, total, "Windows WebView2 (the app window)");            webview2()
    step(5, total, "Finding ComfyUI");                              comfyui()
    step(6, total, "Shortcuts");                                    shortcuts()
    step(7, total, "Starting Comfy Studio");                        start_and_confirm()
    say("")
    say(" Done! Next time open Comfy Studio from the desktop shortcut or Start Comfy Studio.bat.")
    say(" In the app, open Workflows: each engine shows what it needs and downloads it for you.")
    save_report(True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop("Setup was cancelled.", "Run Install Comfy Studio.bat again whenever you're ready.")
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        stop("Unexpected error: %s" % e, "".join(traceback.format_exc())[-1200:] +
             "\nPlease send app\\data\\setup_report.txt to whoever gave you Comfy Studio.")
