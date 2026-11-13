# -*- coding: utf-8 -*-
"""Convert any ComfyUI workflow file into the exact prompt ComfyUI would run, using ComfyUI's OWN web page.

ComfyUI's "Export (API)" runs in its browser page, where every custom node's own code is loaded (rgthree group
buttons, KJNodes Set/Get, subgraphs, primitives, dynamic widgets ...). Copies of that logic in Python miss
cases (wrong wires out of subgraphs, Set/Get pass-through ...). So this opens ComfyUI's page in a hidden Edge /
Chrome window (its own temporary profile: the user's ComfyUI tabs and settings are untouched), loads the workflow
there and asks ComfyUI for the prompt, exactly like the Export (API) button.

Standard library only (a tiny DevTools-protocol websocket client), so it works on any Windows PC with Edge.
If no browser or ComfyUI is available, convert() raises ConverterUnavailable and the caller uses its fallback.
"""
from __future__ import annotations

import base64, json, os, random, shutil, socket, struct, subprocess, tempfile, threading, time, urllib.request

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
IDLE_SECONDS = 600            # close the hidden browser after 10 minutes without uploads
_lock = threading.Lock()
_state = {"proc": None, "port": None, "profile": None, "ws": None, "t": 0.0}


class ConverterUnavailable(Exception):
    pass


def _browser_path():
    for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
              os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
              os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
        if os.path.isfile(p):
            return p
    return None


# ------------------------------------------------------------------ minimal websocket client (RFC 6455)
class _WS:
    def __init__(self, url, timeout=30):
        assert url.startswith("ws://")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n") % (path, hostport, key)
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConverterUnavailable("The hidden browser closed the connection.")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise ConverterUnavailable("The hidden browser refused the connection.")
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _recv_exact(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(max(65536, n - len(self.rest)))
            if not chunk:
                raise ConverterUnavailable("The hidden browser closed the connection.")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127); head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._recv_exact(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            if b2 & 0x80:
                self._recv_exact(4)
            payload = self._recv_exact(n)
            if op == 8:
                raise ConverterUnavailable("The hidden browser closed the connection.")
            if op in (9, 10):          # ping / pong
                continue
            msg += payload
            if b1 & 0x80:
                return json.loads(msg.decode("utf-8", "replace"))

    def call(self, method, params=None, timeout=60):
        self.next_id += 1
        mid = self.next_id
        self.send({"id": mid, "method": method, "params": params or {}})
        end = time.time() + timeout
        self.sock.settimeout(timeout)
        while time.time() < end:
            m = self.recv()
            if m.get("id") == mid:
                if "error" in m:
                    raise ConverterUnavailable(str(m["error"].get("message") or m["error"]))
                return m.get("result") or {}
        raise ConverterUnavailable("ComfyUI's page took too long to answer.")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ hidden browser with ComfyUI's page
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _eval(ws, expr, timeout=60):
    r = ws.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        d = r["exceptionDetails"]
        raise ValueError(((d.get("exception") or {}).get("description") or d.get("text") or "error").split("\n")[0][:300])
    return (r.get("result") or {}).get("value")


def _shutdown():
    ws, proc, prof, port = _state["ws"], _state["proc"], _state["profile"], _state["port"]
    _state.update(ws=None, proc=None, port=None, profile=None)
    if ws:
        ws.close()
    # Edge's launcher exits straight away and the real browser keeps running: close it through DevTools
    if port:
        try:
            bws = _WS(_json("http://127.0.0.1:%d/json/version" % port, timeout=3)["webSocketDebuggerUrl"], timeout=5)
            try:
                bws.call("Browser.close", timeout=5)
            except Exception:
                pass
            bws.close()
        except Exception:
            pass
    if proc:
        try:
            proc.kill()
        except OSError:
            pass
    if prof:
        for _ in range(20):            # the profile folder is locked until the browser has exited
            shutil.rmtree(prof, ignore_errors=True)
            if not os.path.isdir(prof):
                break
            time.sleep(0.25)


def _kill_leftovers():
    """Hidden browsers left by a crash or a hard stop of the app (their profile folder name gives them away)."""
    if os.name != "nt":
        return
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
          "Where-Object { $_.CommandLine -match 'cs_convert_' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=30, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        if name.startswith("cs_convert_"):
            shutil.rmtree(os.path.join(tmp, name), ignore_errors=True)


_cleaned = {"done": False}


def _ensure_page():
    """A hidden browser showing ComfyUI's page, ready to convert. Reused between uploads."""
    if not _cleaned["done"]:
        _cleaned["done"] = True
        _kill_leftovers()
    if _state["ws"]:
        try:
            if _eval(_state["ws"], "!!(window.app && window.app.graph)", timeout=10):
                return _state["ws"]
        except Exception:
            pass
    _shutdown()
    exe = _browser_path()
    if not exe:
        raise ConverterUnavailable("No Edge or Chrome on this PC.")
    try:
        _json(COMFY_URL + "/system_stats", timeout=5)
    except Exception:
        raise ConverterUnavailable("ComfyUI isn't running.")
    port = _free_port()
    prof = tempfile.mkdtemp(prefix="cs_convert_")
    flags = ["--headless=new", "--remote-debugging-port=%d" % port, "--user-data-dir=" + prof, "--no-first-run",
             "--no-default-browser-check", "--disable-extensions", "--disable-gpu", "--mute-audio",
             "--window-size=1280,900", "about:blank"]
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([exe] + flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation)
    _state.update(proc=proc, port=port, profile=prof)
    page = None
    for _ in range(60):
        time.sleep(0.25)
        try:
            page = next((t for t in _json("http://127.0.0.1:%d/json" % port) if t.get("type") == "page"), None)
            if page:
                break
        except Exception:
            continue
    if not page:
        _shutdown()
        raise ConverterUnavailable("The hidden browser didn't start.")
    ws = _WS(page["webSocketDebuggerUrl"])
    _state["ws"] = ws
    ws.call("Page.enable")
    ws.call("Page.navigate", {"url": COMFY_URL + "/"})
    # wait for ComfyUI's app (and its custom-node scripts) to finish loading
    for _ in range(240):
        time.sleep(0.25)
        try:
            if _eval(ws, "!!(window.app && window.app.graph && window.app.extensionManager !== undefined && "
                         "document.readyState === 'complete')", timeout=10):
                break
        except Exception:
            continue
    else:
        _shutdown()
        raise ConverterUnavailable("ComfyUI's page didn't finish loading.")
    time.sleep(1.0)       # custom-node extensions register a moment after the app is up
    return ws


_JS = r"""
(async () => {
  const wf = %s;
  const app = window.app;
  // a stale dialog or an earlier workflow must not leak into this one
  await app.loadGraphData(wf, true, false, null, {showMissingNodesDialog: false, showMissingModelsDialog: false});
  await new Promise(r => setTimeout(r, 150));
  const p = await app.graphToPrompt();
  return JSON.stringify(p.output || {});
})()
"""


def convert(workflow):
    """UI-format workflow (dict) -> the API prompt ComfyUI would queue (dict of node id -> node)."""
    with _lock:
        ws = _ensure_page()
        _state["t"] = time.time()
        out = _eval(ws, _JS % json.dumps(workflow), timeout=120)
        _state["t"] = time.time()
        _schedule_idle_close()
    g = json.loads(out or "{}")
    if not isinstance(g, dict) or not g:
        raise ValueError("ComfyUI turned this workflow into an empty job (everything in it is switched off).")
    return {str(k): v for k, v in g.items() if isinstance(v, dict) and "class_type" in v}


def _schedule_idle_close():
    def later(stamp):
        time.sleep(IDLE_SECONDS + 1)
        with _lock:
            if _state["t"] == stamp:
                _shutdown()
    threading.Thread(target=later, args=(_state["t"],), daemon=True).start()


def close():
    with _lock:
        _shutdown()
