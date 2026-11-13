"""Comfy Studio as a desktop app: its own window (no browser, no tabs, no address bar).

Starts the Comfy Studio server if it isn't running, then opens the studio in a native window
(pywebview on Windows' built-in WebView2). Does not start or stop ComfyUI.
Run with pythonw.exe so no console appears.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("H3STUDIO_PORT") or (8199 if "_dev" in HERE.lower() else 8198))
URL = "http://127.0.0.1:%d/" % PORT
TITLE = "Comfy Studio" + (" (dev)" if PORT != 8198 else "")
ICON = os.path.join(HERE, "comfy_studio.ico")
DATA = os.path.join(HERE, "data")
WIN_STATE = os.path.join(DATA, "window.json")
APP_ID = "ComfyStudio.Desktop" + (".Dev" if PORT != 8198 else "")
# the console-less python that runs this file has a python.exe next to it (any PC)
PY = os.path.join(os.path.dirname(sys.executable), "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def server_up():
    """Is Comfy Studio's server answering? Checks the page itself (instant), not /api/status: that one asks ComfyUI
    how it is doing, which can take seconds while ComfyUI is down or starting, and made the window start a second
    server that could never get the port (endless "Starting Comfy Studio")."""
    try:
        with urllib.request.urlopen(URL, timeout=4) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def start_server():
    py = PY if os.path.isfile(PY) else sys.executable.replace("pythonw.exe", "python.exe")
    env = dict(os.environ, H3STUDIO_PORT=str(PORT))
    subprocess.Popen([py, os.path.join(HERE, "server.py")], cwd=HERE, env=env,
                     creationflags=0x08000000 | 0x00000200)   # no window, own process group


def bring_existing_to_front():
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return bool(hwnd)


def set_icon(title):
    """Give the window (and its taskbar button) the Comfy Studio icon."""
    if not os.path.isfile(ICON):
        return
    for _ in range(40):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            big = user32.LoadImageW(None, ICON, 1, 256, 256, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE
            small = user32.LoadImageW(None, ICON, 1, 32, 32, 0x10)
            user32.SendMessageW(hwnd, 0x80, 1, big)                     # WM_SETICON, ICON_BIG
            user32.SendMessageW(hwnd, 0x80, 0, small)                   # ICON_SMALL
            return
        time.sleep(0.1)


def apply_native_icon(window):
    """pywebview's Windows window sets its own icon from the running program (python); replace it on the form
    itself, on the window's own thread, so the title bar and taskbar keep the Comfy Studio logo."""
    if not os.path.isfile(ICON):
        return False
    try:
        import clr  # pythonnet: what pywebview's Windows window runs on
        clr.AddReference("System.Drawing")
        from System import Action
        from System.Drawing import Icon
        form = getattr(window, "native", None)
        if form is None:
            return False

        def put():
            form.Icon = Icon(ICON)
            form.ShowIcon = True
        if form.InvokeRequired:
            form.Invoke(Action(put))
        else:
            put()
        return True
    except Exception:
        return False


def load_state():
    try:
        with open(WIN_STATE, encoding="utf-8") as f:
            s = json.load(f)
        return {k: int(s[k]) for k in ("width", "height", "x", "y") if k in s}, bool(s.get("maximized"))
    except Exception:
        return {}, False


def save_state(window, maximized):
    try:
        os.makedirs(DATA, exist_ok=True)
        s = {"maximized": maximized}
        if not maximized:
            s.update(width=window.width, height=window.height, x=window.x, y=window.y)
        else:
            old, _ = load_state()
            s.update(old)
        with open(WIN_STATE, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0b0b0c;color:#d8d8dc;font:15px system-ui,-apple-system,Segoe UI,sans-serif}
body{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}
.d{width:26px;height:26px;border:2px solid #333;border-top-color:#d8d8dc;border-radius:50%;animation:s .9s linear infinite}
@keyframes s{to{transform:rotate(360deg)}} p{margin:0;opacity:.7}</style></head>
<body><div class="d"></div><p id="m">Starting Comfy Studio…</p></body></html>"""


def main():
    # one copy only: a second launch just brings the open window forward
    mutex = kernel32.CreateMutexW(None, False, "Local\\" + APP_ID)
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        bring_existing_to_front()
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    import webview
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    geo, maximized = load_state()
    up = server_up()
    window = webview.create_window(
        TITLE, URL if up else None, html=None if up else SPLASH,
        width=geo.get("width", 1280), height=geo.get("height", 900),
        x=geo.get("x"), y=geo.get("y"), min_size=(380, 560),
        background_color="#0b0b0c", text_select=True, maximized=maximized,
    )
    state = {"max": maximized}
    window.events.maximized += lambda: state.__setitem__("max", True)
    window.events.restored += lambda: state.__setitem__("max", False)
    window.events.closing += lambda: save_state(window, state["max"])

    def logo():
        # the form may reset its icon while it opens: set it on the form and on the window handle, more than once
        for delay in (0, 0.6, 2.0):
            time.sleep(delay)
            apply_native_icon(window)
            set_icon(TITLE)
    window.events.shown += logo
    window.events.loaded += lambda: (apply_native_icon(window), set_icon(TITLE))

    def boot():
        set_icon(TITLE)
        if up:
            return
        start_server()
        for _ in range(120):
            if server_up():
                window.load_url(URL)
                return
            time.sleep(0.5)
        window.evaluate_js("document.getElementById('m').textContent="
                           "\"Comfy Studio didn't start. Close this window and open the app again.\"")

    webview.start(boot, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(DATA, "app_webview"), icon=ICON)
    del mutex


if __name__ == "__main__":
    main()
