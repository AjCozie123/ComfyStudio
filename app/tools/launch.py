# -*- coding: utf-8 -*-
"""Start Comfy Studio and confirm it really opened (run by "Start Comfy Studio.bat").

Opens the app (its own window, or the browser when Windows' WebView2 is missing) and waits until it answers. If it
doesn't, it says why and what to do instead of closing silently.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(APP, "data")
PORT = int(os.environ.get("COMFYSTUDIO_PORT") or 8198)        # tests run a copy on another port
URL = "http://127.0.0.1:%d/" % PORT
NO_WINDOW = 0x08000000

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def up():
    try:
        with urllib.request.urlopen(URL, timeout=3) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def tail(path, n=12):
    try:
        return open(path, encoding="utf-8", errors="replace").read().strip().splitlines()[-n:]
    except Exception:
        return []


def main():
    print(" Starting Comfy Studio...", flush=True)
    missing = []
    for mod in ("webview", "PIL", "numpy", "win32com.client"):
        r = subprocess.run([sys.executable, "-c", "import " + mod], capture_output=True)
        if r.returncode != 0:
            missing.append(mod)
    if missing:
        print("\n Some parts Comfy Studio needs are missing (%s)." % ", ".join(missing))
        print(" Double-click Install Comfy Studio.bat again: it adds them, then starts the app.")
        return 1
    pw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pw):
        pw = sys.executable
    # starts the app, or (already running) just brings its window forward: the app itself takes care of that
    subprocess.Popen([pw, os.path.join(APP, "ComfyStudio.pyw")], cwd=APP, env=dict(os.environ, H3STUDIO_PORT=str(PORT)),
                     creationflags=NO_WINDOW | 0x00000200)
    for _ in range(60):
        if up():
            print(" Comfy Studio is open. (If no window appeared, go to %s in your browser.)" % URL)
            time.sleep(2)
            return 0
        time.sleep(1)
    print("\n Comfy Studio didn't start within a minute. The last messages were:")
    for line in (tail(os.path.join(DATA, "startup_error.log")) + tail(os.path.join(DATA, "server_start.log")))[-12:] \
            or ["(no error was written)"]:
        print("   " + line)
    print("\n What to try:")
    print("  1. Double-click Start Comfy Studio.bat once more (a first start can be slow).")
    print("  2. Restart the PC (another program may be using port 8198), then try again.")
    print("  3. Run Install Comfy Studio.bat again: it re-checks everything.")
    print("  4. Still stuck: send app\\data\\server_start.log to whoever gave you Comfy Studio.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
