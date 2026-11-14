"""Comfy Studio - a simple local web app for MiniMax-H3-Image via ComfyUI.

Binds ONLY to 127.0.0.1 and this PC's own Tailscale address (if it has one). Never 0.0.0.0.
Port 8198: 8197 is already taken as a lock socket by the AI Studio App keeper.
Tailnet (non-loopback) clients need the PIN in data\\PIN.txt; a session cookie lasts 30 days.
Talks to ComfyUI at 127.0.0.1:8188. Does not start or stop ComfyUI.
"""
from __future__ import annotations

import gzip, hashlib, hmac, io, ipaddress, json, logging, os, re, secrets, shutil, socket, struct
import sys, threading, time, traceback, uuid, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graphs
import aliases
import pe as PE
import pc_browse
import installer as INST
import custom_nodes as CN
import clothing_guard as CG
import lora_index as LI
import understanding as UND
import library as LIB
import assist as ASSIST
import camera as CAMERA
import appearance as APPEARANCE
import workflow_registry as WF
import prompt_dialect as PD
import nodes_manager as NM
import updater as UPD
import specs as SP
from validate_graphs import fetch_object_info, validate_graph, _combo_values

PORT = int(os.environ.get("H3STUDIO_PORT", "8198"))
DATA = os.path.join(HERE, "data")
# Where ComfyUI and its folders are on THIS PC: detected from ComfyUI itself, saved in data/settings.json
import app_paths  # noqa: E402
SETTINGS = app_paths.resolve(DATA)
COMFY = SETTINGS["comfy_url"].rstrip("/")
_cu = urllib.parse.urlsplit(COMFY)
COMFY_HOST, COMFY_PORT = (_cu.hostname or "127.0.0.1"), (_cu.port or 8188)
OUTPUT = SETTINGS["output_dir"]
APP_OUT = "ComfyStudio"
INPUT = SETTINGS["input_dir"]
INPUT_SUB = "ComfyStudio"
# this PC only, plus its own Tailscale address(es) for the optional phone access
def _remembered_tailnet():
    """Tailscale addresses seen before (kept in settings.json), so a start before Tailscale is up still retries them."""
    out = []
    for h in SETTINGS.get("tailnet_ips") or []:
        try:
            if isinstance(h, str) and ipaddress.ip_address(h) in app_paths.TAILNET:
                out.append(h)
        except ValueError:
            pass
    return out


def _edition():
    """'pc' for the public PC version (release.json): this PC only, no phone / PIN. Anything else: full."""
    try:
        return str(json.load(open(os.path.join(HERE, "release.json"), encoding="utf-8")).get("edition") or "full")
    except (OSError, ValueError):
        return "full"


PC_ONLY = _edition() == "pc"
HOSTS = ["127.0.0.1"] + ([] if PC_ONLY else [h for h in dict.fromkeys(app_paths.tailnet_ips() + _remembered_tailnet() + SETTINGS.get("extra_hosts", []))
                                             if h not in ("127.0.0.1", "0.0.0.0")])
STATIC = os.path.join(HERE, "static")
GALLERY = os.path.join(DATA, "gallery.jsonl")
THUMBS = os.path.join(DATA, "thumbs")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
CLIENT_ID = "h3studio-" + uuid.uuid4().hex[:8]
STOP_FILE = os.path.join(DATA, "STOP")
MAX_UPLOAD = 80 * 1024 * 1024
MAX_MEDIA_UPLOAD = 600 * 1024 * 1024       # videos / audio clips for workflows that take them
MEDIA_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
             ".m4v": "video/mp4", ".avi": "video/x-msvideo", ".mp3": "audio/mpeg", ".wav": "audio/wav",
             ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac"}

os.makedirs(DATA, exist_ok=True)
pc_browse.set_comfy_roots(INPUT, OUTPUT)
NM.COMFY = COMFY
os.makedirs(THUMBS, exist_ok=True)
os.makedirs(os.path.join(INPUT, INPUT_SUB), exist_ok=True)
os.makedirs(os.path.join(OUTPUT, APP_OUT), exist_ok=True)
os.makedirs(os.path.join(OUTPUT, APP_OUT, "_sources"), exist_ok=True)


# ---------- where results are saved: ComfyStudio/<engine>/<model>/<t2i | edit | video>/ ----------
_BAD_SEG = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _seg(s, fallback):
    """One safe Windows folder name."""
    s = re.sub(r"\s+", " ", _BAD_SEG.sub(" ", str(s or ""))).strip(" .")
    return s[:60].strip(" .") or fallback


def model_label(name):
    """'Krea2\\krea2_turbo_int8_convrot.safetensors' -> 'krea2_turbo_int8_convrot'."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    return re.sub(r"\.(safetensors|gguf|ckpt|pt|pth|sft|bin)$", "", base, flags=re.I)


def engine_label(card):
    """'Krea 2 Edit · Image edit' -> 'Krea 2 Edit'."""
    return str((card or {}).get("name") or (card or {}).get("id") or "Workflow").split(" \u00b7 ")[0].replace("(built-in)", "").strip()


def kind_label(card=None, mode=None):
    if card is not None:
        if (card.get("media") or "") == "video":
            return "video"
        t = str(card.get("task") or "")
        return "edit" if t in ("edit", "inpaint") else ("video" if "v" in t and t != "t2i" else "t2i")
    return "edit" if mode == "edit" else "t2i"


_MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".sft", ".pt", ".pth", ".bin")


def graph_model(g):
    """The main diffusion model file a graph loads (UNET / checkpoint / GGUF / MiniMax H3 AIO loader)."""
    nodes = list((g or {}).values())
    for key in ("unet_name", "ckpt_name", "model_text_image"):
        for n in nodes:
            v = (n.get("inputs") or {}).get(key)
            if isinstance(v, str) and v:
                return v
    # any other loader: an input called model... holding a model file (not a LoRA / VAE / encoder / upscaler)
    for n in nodes:
        cls = str(n.get("class_type") or "")
        if not re.search(r"loader|model", cls, re.I) or re.search(r"lora|vae|clip|upscale|encoder", cls, re.I):
            continue
        for k, v in (n.get("inputs") or {}).items():
            if k.lower().startswith("model") and isinstance(v, str) and v.lower().endswith(_MODEL_EXT):
                return v
    return ""


def out_dir(engine, model, kind):
    return "/".join([APP_OUT, _seg(engine, "Other engine"), _seg(model_label(model), "Unknown model"), _seg(kind, "other")])
pc_browse.ensure_config(DATA)
try:
    APPEARANCE.configure(data_dir=DATA, max_workers=1)
except Exception:
    pass

logging.basicConfig(
    filename=os.path.join(DATA, "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("h3studio")


def _safe_input_path(name):
    rel = str(name or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    full = os.path.abspath(os.path.join(INPUT, rel.replace("/", os.sep)))
    root = os.path.abspath(INPUT)
    try:
        if os.path.commonpath([root, full]) != root:
            return None
    except ValueError:
        return None
    return full


def appearances_for(names, wait=1.0):
    names = list(names or [])
    if not names:
        return []
    try:
        paths = [_safe_input_path(n) for n in names]
        APPEARANCE.prefetch_many([p for p in paths if p])
        return APPEARANCE.get_many(paths, wait=wait)
    except Exception:
        return [None] * len(names)




# ------------------------------------------------------------------ auth
def _ph(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def _conf():
    p = os.path.join(DATA, "auth.json")
    if not os.path.exists(p):
        pin = "%06d" % secrets.randbelow(10 ** 6)
        salt = secrets.token_hex(16)
        rec = {"salt": salt, "hash": _ph(pin, salt), "secret": secrets.token_hex(32)}
        json.dump(rec, open(p, "w"))
        if PC_ONLY:
            return rec              # PC version: nothing is reachable from other devices, so no PIN file
        with open(os.path.join(DATA, "PIN.txt"), "w", encoding="utf-8") as f:
            f.write(pin + "\n\nPhone PIN for Comfy Studio (http://%s:%d).\nChange it from this PC: open http://127.0.0.1:%d and use Advanced, or edit this file after stopping the app and deleting data\\auth.json.\n" % (
                (HOSTS[1] if len(HOSTS) > 1 else "your-tailscale-ip"), PORT, PORT))
    return json.load(open(p))


AUTH = _conf()
_fails = {}


def make_session():
    exp = int(time.time()) + 30 * 86400
    body = "%d.%s" % (exp, secrets.token_hex(8))
    return body + "." + hmac.new(bytes.fromhex(AUTH["secret"]), body.encode(), "sha256").hexdigest()


def session_ok(tok):
    try:
        exp, nonce, sig = tok.split(".")
        good = hmac.new(bytes.fromhex(AUTH["secret"]), ("%s.%s" % (exp, nonce)).encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


def set_pin(pin):
    global AUTH
    if not re.fullmatch(r"\d{4,10}", pin or ""):
        return "PIN must be 4-10 digits."
    AUTH = dict(AUTH, salt=secrets.token_hex(16), secret=secrets.token_hex(32))
    AUTH["hash"] = _ph(pin, AUTH["salt"])
    json.dump(AUTH, open(os.path.join(DATA, "auth.json"), "w"))
    with open(os.path.join(DATA, "PIN.txt"), "w", encoding="utf-8") as f:
        f.write(pin + "\n")
    return "PIN changed. Phones need to sign in again."


# ------------------------------------------------------------------ ComfyUI HTTP
def comfy(method, path, body=None, timeout=30, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        COMFY + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            b = f.read()
            return b if raw else (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        raw_b = e.read() or b""
        try:
            parsed = json.loads(raw_b.decode("utf-8", "replace"))
        except Exception:
            parsed = {"error": raw_b.decode("utf-8", "replace") or str(e)}
        parsed["_http"] = e.code
        raise ComfyError(format_comfy_error(parsed), parsed)


class ComfyError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


def format_comfy_error(payload):
    """Turn ComfyUI's /prompt and execution errors into a short readable message."""
    if not isinstance(payload, dict):
        return str(payload)
    parts = []
    err = payload.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or ""
        details = err.get("details") or err.get("extra_info") or ""
        if isinstance(details, dict):
            details = details.get("message") or json.dumps(details)
        parts.append(" ".join(str(x) for x in (msg, details) if x).strip())
    elif isinstance(err, str) and err.strip():
        parts.append(err.strip())
    node_errors = payload.get("node_errors") or {}
    for nid, rec in node_errors.items():
        if isinstance(rec, dict):
            cls = rec.get("class_type") or nid
            for e in rec.get("errors") or []:
                if isinstance(e, dict):
                    parts.append("%s: %s" % (cls, e.get("message") or e.get("details") or e))
                else:
                    parts.append("%s: %s" % (cls, e))
            if rec.get("message"):
                parts.append("%s: %s" % (cls, rec["message"]))
        else:
            parts.append("%s: %s" % (nid, rec))
    text = " ".join(p for p in parts if p).strip()
    if not text:
        text = payload.get("exception_message") or payload.get("message") or ""
    low = text.lower()
    if any(s in low for s in ("out of memory", "cuda oom", "cuda out of memory", "allocation on device")):
        return "The GPU ran out of memory. Try the 4 MP size (the default), fewer reference images, or Prompt Enhancer off. " + text
    return text or "ComfyUI rejected the workflow."


def comfy_up():
    try:
        comfy("GET", "/system_stats", timeout=3)
        return True
    except Exception:
        return False


def comfy_queue():
    try:
        return comfy("GET", "/queue", timeout=5)
    except Exception:
        return {"queue_running": [], "queue_pending": []}


def comfy_busy_with_other(our_ids):
    q = comfy_queue()
    def pid_of(item):
        # items are [number, prompt_id, prompt, extra, outputs?]
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return str(item[1])
        if isinstance(item, dict):
            return str(item.get("prompt_id") or item.get("promptId") or "")
        return ""
    running = [pid_of(x) for x in (q.get("queue_running") or [])]
    pending = [pid_of(x) for x in (q.get("queue_pending") or [])]
    ours = set(our_ids)
    foreign = [p for p in running + pending if p and p not in ours]
    return bool(foreign), running, pending


# ------------------------------------------------------------------ object_info cache
_oi = {"t": 0, "data": None}


def object_info(force=False):
    if force or not _oi["data"] or time.time() - _oi["t"] > 300:
        try:
            _oi["data"] = fetch_object_info()
            _oi["t"] = time.time()
        except Exception as e:
            log.info("object_info: %s", e)
            if not _oi["data"]:
                raise
    return _oi["data"]


def combo_list(oi, node, key):
    schema = (oi.get(node) or {}).get("input") or {}
    spec = (schema.get("required") or {}).get(key) or (schema.get("optional") or {}).get(key)
    return _combo_values(spec) or []


def model_choices(oi):
    unets = combo_list(oi, "UNETLoader", "unet_name")
    clips = combo_list(oi, "CLIPLoader", "clip_name")
    vaes = combo_list(oi, "VAELoader", "vae_name")
    def prefer(items, needle_parts, always):
        out, seen = [], set()
        for x in list(always) + list(items):
            if x in seen:
                continue
            if x in always or any(p.lower() in x.lower() for p in needle_parts):
                out.append(x)
                seen.add(x)
        # Always include the default even if the list was empty (offline cache).
        for a in always:
            if a not in seen:
                out.insert(0, a)
        return out or list(always)
    d = graphs.DEFAULTS
    return {
        "unet_name": prefer(unets, ("minimax", "h3_image"), [d["unet_name"]]),
        "clip_name": prefer(clips, ("minimax", "qwen3vl_32b"), [d["clip_name"]]),
        "pe_clip_name": prefer(clips, ("qwen3.5_9b", "qwen3.5"), [d["pe_clip_name"]]),
        "vae_name": prefer(vaes, ("minimax_h3_image_vae", "minimax"), [d["vae_name"]]),
        # "lcm_detail" (the MiniMax H3 detail recipe) is offered first when this ComfyUI has SamplerLCM
        "sampler_name": ([graphs.LCM_DETAIL] if "SamplerLCM" in (oi or {}) else []) + (combo_list(oi, "KSampler", "sampler_name") or [d["sampler_name"]]),
        "scheduler": combo_list(oi, "KSampler", "scheduler") or [d["scheduler"]],
        "weight_dtype": combo_list(oi, "UNETLoader", "weight_dtype") or [d["weight_dtype"]],
        "clip_type": combo_list(oi, "CLIPLoader", "type") or [d["clip_type"]],
    }


# ------------------------------------------------------------------ websocket (stdlib)
live = {"prompt_id": None, "value": 0, "max": 0, "node": None, "preview": None, "t": 0, "text": ""}
done_events = {}


def _ws_handshake(path):
    key = __import__("base64").b64encode(os.urandom(16)).decode()
    s = socket.create_connection((COMFY_HOST, COMFY_PORT), timeout=15)
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n" % (path, COMFY_HOST, COMFY_PORT, key)
    )
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise ConnectionError("websocket handshake closed")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b"101" not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError("websocket handshake failed: %s" % head.split(b"\r\n", 1)[0])
    s.settimeout(60)
    return s, rest


def _ws_send(s, opcode, payload=b""):
    mask = os.urandom(4)
    plen = len(payload)
    hdr = bytearray()
    hdr.append(0x80 | (opcode & 0x0F))
    if plen < 126:
        hdr.append(0x80 | plen)
    elif plen < 65536:
        hdr.append(0x80 | 126)
        hdr += struct.pack("!H", plen)
    else:
        hdr.append(0x80 | 127)
        hdr += struct.pack("!Q", plen)
    hdr += mask
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    s.sendall(bytes(hdr) + body)


def _recv_exact(s, leftover, n):
    buf = leftover
    while len(buf) < n:
        chunk = s.recv(max(n - len(buf), 4096))
        if not chunk:
            raise ConnectionError("websocket closed")
        buf += chunk
    return buf[:n], buf[n:]


def _ws_recv(s, leftover):
    hdr, leftover = _recv_exact(s, leftover, 2)
    opcode = hdr[0] & 0x0F
    plen = hdr[1] & 0x7F
    masked = bool(hdr[1] & 0x80)
    if plen == 126:
        ext, leftover = _recv_exact(s, leftover, 2)
        plen = struct.unpack("!H", ext)[0]
    elif plen == 127:
        ext, leftover = _recv_exact(s, leftover, 8)
        plen = struct.unpack("!Q", ext)[0]
    mask = b""
    if masked:
        mask, leftover = _recv_exact(s, leftover, 4)
    data, leftover = _recv_exact(s, leftover, plen)
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data, leftover


def _ws_thread():
    path = "/ws?clientId=" + CLIENT_ID
    while True:
        try:
            s, leftover = _ws_handshake(path)
            log.info("comfy websocket connected")
            while True:
                try:
                    opcode, data, leftover = _ws_recv(s, leftover)
                except socket.timeout:
                    continue
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    _ws_send(s, 0xA, data)
                    continue
                if opcode == 0x2 and len(data) > 8:
                    typ = int.from_bytes(data[:4], "big")
                    if typ == 1:
                        live["preview"] = data[8:]
                        live["t"] = time.time()
                    continue
                if opcode != 0x1:
                    continue
                try:
                    msg = json.loads(data)
                except Exception:
                    continue
                t, d = msg.get("type"), msg.get("data") or {}
                if t == "progress":
                    live.update(prompt_id=d.get("prompt_id"), value=d.get("value", 0), max=d.get("max", 0), node=d.get("node"))
                elif t == "executing":
                    if d.get("node") is None and d.get("prompt_id"):
                        done_events.setdefault(d["prompt_id"], {"ok": True})
                    else:
                        live.update(prompt_id=d.get("prompt_id"), node=d.get("node"))
                elif t == "execution_start":
                    live.update(prompt_id=d.get("prompt_id"), value=0, max=0, preview=None, text="")
                elif t == "execution_success":
                    done_events[d.get("prompt_id")] = {"ok": True}
                elif t in ("execution_error", "execution_interrupted"):
                    msg_e = d.get("exception_message") or d.get("message") or t.replace("_", " ")
                    done_events[d.get("prompt_id")] = {"ok": False, "error": format_comfy_error(d) if d.get("node_errors") or d.get("error") else msg_e}
                elif t == "progress_state":
                    pass
        except Exception as e:
            log.info("ws reconnect: %s", e)
            time.sleep(3)


# ------------------------------------------------------------------ gallery / jobs
_glock = threading.Lock()
jobs = {}
order = []
job_q = []
q_cv = threading.Condition()


def gallery_all():
    items = []
    if os.path.exists(GALLERY):
        with open(GALLERY, encoding="utf-8") as f:
            latest = {}
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                gid = rec.get("id")
                if not gid:
                    continue
                if rec.get("deleted"):
                    latest.pop(gid, None)
                    continue
                if "filename" in rec or "prompt" in rec:
                    fav = latest.get(gid, {}).get("fav")
                    latest[gid] = rec
                    if rec.get("fav") is None and fav:
                        latest[gid]["fav"] = fav
                elif "fav" in rec and gid in latest:
                    latest[gid]["fav"] = bool(rec["fav"])
            items = list(latest.values())
    items.sort(key=lambda x: x.get("t") or 0, reverse=True)
    seen, out = set(), []
    for it in items:                      # newest first: an older entry for the same file is a leftover copy
        key = ((it.get("subfolder") or "") + "/" + (it.get("filename") or "")).lower() if it.get("filename") else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(it)
    return out


# ------------------------------------------------------------------ lineage
# input file name -> the library item it was copied from ("Use as Picture 1"), so edits of edits stay together.
_lin_lock = threading.Lock()
_lineage = None


def _lineage_path():
    return os.path.join(DATA, "lineage.json")


def lineage_get(name):
    global _lineage
    with _lin_lock:
        if _lineage is None:
            try:
                _lineage = json.load(open(_lineage_path(), encoding="utf-8"))
            except Exception:
                _lineage = {}
        return _lineage.get(str(name or "").replace("\\", "/"))


def lineage_put(name, gid):
    global _lineage
    lineage_get(name)
    with _lin_lock:
        _lineage[str(name).replace("\\", "/")] = gid
        if len(_lineage) > 20000:
            for k in list(_lineage)[:5000]:
                _lineage.pop(k, None)
        tmp = _lineage_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_lineage, f)
        os.replace(tmp, _lineage_path())


# class_type -> what the progress strip says the job is doing
PHASES = (("TextGenerate", "enhance"), ("Loader", "load"), ("LoadImage", "prepare"), ("ImageScale", "prepare"),
          ("VAEEncode", "prepare"), ("TextEncode", "read"), ("CLIPTextEncode", "read"), ("Sampler", "paint"),
          ("KSampler", "paint"), ("MiniMax", "paint"), ("Upscale", "upscale"), ("FlashVSR", "upscale"),
          ("SeedVR2", "upscale"), ("VAEDecode", "decode"), ("Decoder", "decode"), ("Save", "save"))


def job_phase(job):
    if live.get("prompt_id") != job.get("prompt_id") or not job.get("prompt_id"):
        return ""
    node = (job.get("graph") or {}).get(str(live.get("node") or ""), {})
    ct = node.get("class_type") or ""
    for key, phase in PHASES:
        if key.lower() in ct.lower():
            return phase
    return "work" if ct else ""


def gallery_add(rec):
    with _glock, open(GALLERY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def gallery_get(gid):
    for it in gallery_all():
        if it.get("id") == gid:
            return it
    return None


def output_path(subfolder, filename):
    sub = (subfolder or "").replace("\\", "/").lstrip("/")
    if ".." in sub.split("/") or ".." in filename or "/" in filename or "\\" in filename:
        return None
    base = os.path.abspath(OUTPUT)
    full = os.path.abspath(os.path.join(OUTPUT, sub.replace("/", os.sep), filename))
    if full == base or full.startswith(base + os.sep):
        return full
    return None


def job_view(job):
    return {
        "id": job["id"],
        "mode": job.get("mode"),
        "status": job.get("status"),
        "prompt": job.get("prompt"),
        "seed": job.get("seed"),
        "steps": job.get("steps"),
        # an inpaint is made on a cut-out but ends up the whole picture's size: report that size from the start
        "width": ((job.get("inpaint") or {}).get("size") or [job.get("width")])[0] if (job.get("inpaint") or {}).get("box") else job.get("width"),
        "height": ((job.get("inpaint") or {}).get("size") or [0, job.get("height")])[1] if (job.get("inpaint") or {}).get("box") else job.get("height"),
        "match_input": job.get("match_input"),
        "keep_input": job.get("keep_input"),
        "pe": job.get("pe"),
        "pe_mode": job.get("pe_mode"),
        "prompt_user": job.get("prompt_user"),
        "enhanced_prompt": job.get("enhanced_prompt") or "",
        "pe_fallback": bool(job.get("pe_fallback")),
        "pe_cached": bool(job.get("pe_cached")),
        "pe_clip_name": job.get("pe_clip_used") or job.get("pe_clip_name"),
        "progress": job.get("progress") or 0,
        "progress_max": job.get("progress_max") or 0,
        "node": job.get("node"),
        "message": job.get("message") or "",
        "error": job.get("error") or "",
        "images": job.get("images") or [],
        "source_images": job.get("source_images") or [],
        "alias_warnings": job.get("alias_warnings") or [],
        "t": job.get("t"),
        "t_done": job.get("t_done"),
        "waiting_for_comfy": job.get("status") == "waiting_comfy",
        "resolution_in": job.get("resolution_in"),
        "sampler_name": job.get("sampler_name"),
        "scheduler": job.get("scheduler"),
        "cfg": job.get("cfg"),
        "models": job.get("models") or {},
        "input_snapshots": job.get("input_snapshots") or [],
        "has_before": bool((job.get("input_snapshots") or job.get("source_images"))),
        "auto_assist": bool(job.get("auto_assist")),
        "roles": job.get("roles") or {},
        "camera": job.get("camera") or {},
        "ref_mode": bool(job.get("ref_mode")),
        "edit_kind": job.get("edit_kind") or ("ref" if job.get("ref_mode") else job.get("mode")),
        "prompt_sent": job.get("prompt_sent") or job.get("prompt") or "",
        "assist_preview": job.get("assist_preview") or "",
        "media": ((job.get("images") or [{}])[0].get("kind")) or job.get("media") or "image",
        "workflow_id": job.get("workflow_id"),
        "workflow_name": job.get("workflow_name"),
        "task": job.get("task"),
    }


def _our_prompt_ids():
    return [j.get("prompt_id") for j in jobs.values() if j.get("prompt_id")]




THUMB_CACHE_VER = "exif2"


def _exif_orientation(im):
    try:
        exif = im.getexif()
        return int(exif.get(0x0112, 1) or 1) if exif else 1
    except Exception:
        return 1


def oriented_size(full):
    """Width, height of an image after applying EXIF orientation."""
    from PIL import Image, ImageOps
    with Image.open(full) as im:
        im2 = ImageOps.exif_transpose(im)
        if im2 is None:
            im2 = im
        return im2.size


def bake_orientation_file(full):
    """Rewrite a file so stored pixels are upright and EXIF orientation is 1.

    Only used for Studio copies under the Comfy input folder. Returns (w, h)
    after transpose. Leaves the file untouched if it is already upright or
    cannot be rewritten.
    """
    from PIL import Image, ImageOps
    import io as _io
    full = os.fspath(full)
    try:
        with Image.open(full) as im:
            orient = _exif_orientation(im)
            if orient in (0, 1):
                return im.size
            im2 = ImageOps.exif_transpose(im)
            if im2 is None:
                return im.size
            im2.load()
            w, h = im2.size
            if im2.mode not in ("RGB", "L", "RGBA"):
                im2 = im2.convert("RGB")
            tmp = full + ".exifbake"
            if (im.format or "").upper() == "PNG" and im2.mode in ("RGB", "RGBA"):
                im2.save(tmp, "PNG")
            else:
                if im2.mode != "RGB":
                    im2 = im2.convert("RGB")
                im2.save(tmp, "JPEG", quality=95)
    except Exception:
        return None
    try:
        os.replace(tmp, full)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return (w, h)


VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
               ".gif": "image/gif", ".webp": "image/webp"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}


def serve_oriented_bytes(full):
    """Return (bytes, content_type) with EXIF orientation baked for display."""
    ext = os.path.splitext(full)[1].lower()
    if ext in VIDEO_EXT:
        return open(full, "rb").read(), VIDEO_TYPES[ext]
    from PIL import Image, ImageOps
    try:
        with Image.open(full) as im:
            orient = _exif_orientation(im)
            if orient in (0, 1):
                data = open(full, "rb").read()
                ctype = "image/png" if ext == ".png" else (
                    "image/webp" if ext == ".webp" else (
                    "image/gif" if ext == ".gif" else "image/jpeg"))
                return data, ctype
            im2 = ImageOps.exif_transpose(im) or im
            im2.load()
            if im2.mode not in ("RGB", "L", "RGBA"):
                im2 = im2.convert("RGB")
            buf = io.BytesIO()
            if ext == ".png" and im2.mode in ("RGB", "RGBA"):
                im2.save(buf, "PNG")
                return buf.getvalue(), "image/png"
            if im2.mode != "RGB":
                im2 = im2.convert("RGB")
            im2.save(buf, "JPEG", quality=92)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        data = open(full, "rb").read()
        ctype = "image/png" if ext == ".png" else "image/jpeg"
        return data, ctype


def picture_size(rel_name):
    """Return (width, height) of an image under the ComfyUI input root, or None."""
    rel = str(rel_name or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    full = os.path.abspath(os.path.join(INPUT, rel.replace("/", os.sep)))
    root = os.path.abspath(INPUT)
    if not (full.startswith(root + os.sep) and os.path.isfile(full)):
        return None
    try:
        return oriented_size(full)
    except Exception:
        return None



def _clip_names():
    try:
        return combo_list(object_info(), "CLIPLoader", "clip_name")
    except Exception:
        return []


# BFS swap LoRAs per engine (Alissonerdx/BFS-Best-Face-Swap guides). All take Picture 1 = the body / scene kept and
# Picture 2 = the head or person, which is the app's order. (BFS Face V1 for 2509 takes the face first, so it isn't used.)
SWAP_LORAS = [
    # (card id prefix, intents, file pattern, strength)
    ("klein9b_", ("head_swap", "face_swap"), r"bfs_head_v1_flux-klein_9b.*step3500_rank128|bfs_head_v1_flux-klein_9b", 1.0),
    ("qwen_edit2509", ("head_swap", "face_swap"), r"bfs_head_v3_qwen_image_edit_2509|bfs_head_v4_qwen_image_edit_2509", 1.0),
    ("qwen21_edit", ("head_swap", "face_swap"), r"bfs_head_v1\.1_qwen_2\.1(?!_alt)|bfs_head_v1_qwen_2\.1", 1.0),
    ("qwen21_edit", ("body_swap",), r"bfs_body_swap_v1\.0_qwen_2\.1", 1.0),
]


def swap_loras(card, intent, user_loras):
    """The BFS swap LoRA this engine needs for a head / face / body swap, when it's installed and the user hasn't
    added one. Krea 2 Edit picks its BFS LoRA itself (krea_swap_pick), so it isn't listed here."""
    if not intent or card.get("task") != "edit":
        return []
    names = [str((l or {}).get("name") or "") for l in (user_loras or [])]
    if any("bfs_" in n.lower() for n in names):
        return []
    rules = [r for r in SWAP_LORAS if str(card.get("id") or "").startswith(r[0]) and intent in r[1]]
    if not rules:
        return []
    try:
        have = combo_list(object_info(), "LoraLoaderModelOnly", "lora_name")
    except Exception:
        return []
    for _prefix, _intents, pat, strength in rules:
        for alt in pat.split("|"):                       # the first pattern is the preferred file
            hit = sorted((n for n in have if re.search(alt, n, re.I)), key=len)
            if hit:
                return [{"name": hit[0], "strength": strength}]
    return []


def krea_swap_pick(data, sw):
    """Krea 2 Edit (Auto, two pictures): head / face swap -> BFS head swap; body swap -> BFS head swap + body swap 0.5;
    outfit -> the outfit-transfer LoRA. Sets data's edit_intent / Edit LoRA / steps; returns extra LoRAs to add.
    Nothing changes when the LoRA isn't installed (the person-into-scene edit is used, as before)."""
    want = {"head_swap": "headswap", "face_swap": "headswap", "body_swap": "bodyswap", "clothing_transfer": "outfit"}.get(sw)
    if not want:
        return []
    try:
        have = combo_list(object_info(), "LoraLoaderModelOnly", "lora_name")
    except Exception:
        return []
    def find(part):
        return next((n for n in have if part in n.lower()), "")
    lora = find("outfittransfer") if want == "outfit" else find("bfs_head_swap")
    if not lora:
        return []
    data["edit_intent"] = want
    models = data.setdefault("models", {})
    models["1"] = lora
    data["steps"] = 10 if want == "bodyswap" else 8
    if want == "bodyswap":
        body = find("bfs_body_swap")
        if body and not any(body == (l or {}).get("name") for l in (data.get("loras") or [])):
            return [{"name": body, "strength": 0.5}]
    return []


def lora_trigger_prompt(prompt, loras):
    """Auto-picked LoRAs picked by their name can need their trigger word: put it in front of the prompt sent to the
    model (the person's prompt box isn't changed). Only short triggers the prompt doesn't already have."""
    adds = []
    low = (prompt or "").lower()
    for l in loras or []:
        t = l.get("add_trigger") if isinstance(l, dict) and l.get("auto") else None
        if isinstance(t, str):
            t = " ".join(t.split())[:40]
            if t and t.lower() not in low and t.lower() not in [a.lower() for a in adds]:
                adds.append(t)
    return (", ".join(adds[:3]) + ", " + (prompt or "")) if adds else prompt


def server_error_text(e):
    """What the person sees when something unexpected breaks: the actual problem, short, and where the details are."""
    what = ("%s: %s" % (type(e).__name__, e)).strip().rstrip(":")[:220]
    return "Something went wrong on the PC (%s). The full details are in app.log in the data folder." % what


def guard_on(data, name, n_images=0):
    """Whether a prompt helper (keep_clothing / only_garment / keep_quality / keep_face) applies to this job.
    Auto (Settings): decided from the instruction itself. Manual: the user's own ticks, exactly."""
    if (data.get("tune_mode") or "") == "auto":
        return bool(CG.auto_guards(data.get("prompt") or "", n_images).get(name, True))
    if name == "keep_face":
        return data.get("keep_face") is True          # off unless ticked
    if name in ("only_garment", "only_target"):
        name = "keep_clothing"                        # part of Keep clothing
    return data.get(name) is not False


def _has_node(name):
    try:
        return name in object_info()
    except Exception:
        return False


def build_main_graph(mode, prompt, resolution, seed, steps, models, images, input_size, ref_mode=False):
    kind = "t2i" if mode == "t2i" else "edit"
    if mode == "t2i":
        graph, meta = graphs.build_t2i(
            prompt, resolution=resolution, seed=seed, steps=steps, pe=False,
            models=models, prefix=APP_OUT + "/" + kind, input_size=input_size,
        )
    else:
        graph, meta = graphs.build_edit(
            prompt, images, resolution=resolution, seed=seed, steps=steps, pe=False,
            models=models, prefix=APP_OUT + "/" + kind, input_size=input_size,
            ref_mode=bool(ref_mode),
        )
    # sorted save folder from the model the graph really loads (the request may not name one)
    sub = out_dir("MiniMax H3", graph_model(graph) or (models or {}).get("unet_name"), kind) + "/" + kind
    for n in graph.values():
        if isinstance((n.get("inputs") or {}).get("filename_prefix"), str):
            n["inputs"]["filename_prefix"] = sub
    return graph, meta


PE_PROMPTS = os.path.join(DATA, "pe_prompts.json")


def pe_prompts():
    try:
        d = json.load(open(PE_PROMPTS, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def pe_override(engine, mode):
    """The user's own enhancer system prompt for this engine + mode ('t2i' = new picture/video, 'edit'), or ''."""
    v = (pe_prompts().get(str(engine or "builtin")) or {}).get("edit" if mode == "edit" else "t2i")
    return v if isinstance(v, str) else ""


def pe_tokens_in(v):
    """The dial value from a request: 'auto' (None) or a token count."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 64 <= n <= 32768 else None


def make_pe_graph(prompt, mode, images, pe_mode, models, pe_system_extra="", pe_task_extra="", pe_system_override="",
                  pe_tokens=None, media="image"):
    free = _has_node("AUSBOSS_NODES_FreeMemory")
    return graphs.build_pe_only(
        prompt, mode=mode, images=images, pe_mode=pe_mode, models=models, free_memory=free,
        pe_system_extra=pe_system_extra, pe_task_extra=pe_task_extra, pe_system_override=pe_system_override,
        pe_tokens=pe_tokens, media=media,
    )


def _pe_cache_payload(job):
    images = list(job.get("pe_images") if job.get("pe_images") is not None else (job.get("source_images") or []))
    st = PE.settings(job.get("pe_mode") or "fast")
    images = images[: int(st.get("max_images") or 16)]
    return {
        "mode": job.get("mode"),
        "pe_mode": st["id"],
        "prompt": job.get("pe_input") or job.get("prompt_user") or job.get("prompt"),
        "pe_clip": job.get("pe_clip_used"),
        "images": [PE.image_fingerprint(INPUT, n) for n in images],
        "thinking": st["thinking"],
        "max_length": PE.resolve_tokens(st["id"], job.get("mode"), job.get("pe_tokens"),
                                        job.get("pe_input") or job.get("prompt_user") or job.get("prompt"), len(images),
                                        generic=job.get("pe_media") == graphs.GENERIC_MEDIA),
        "ref_mp": st["ref_megapixels"],
        "media": job.get("pe_media") or "image",
        "templates": [graphs.pe_system_file(job.get("mode"), st["id"], job.get("pe_media") or "image"),
                      graphs.pe_task_file(job.get("mode"), st["id"], job.get("pe_media") or "image"),
                      "edit_v2" if job.get("mode") == "edit" else ""],
        "ref_mode": bool(job.get("ref_mode")),
        "auto_assist": bool(job.get("auto_assist")),
        "roles": job.get("roles") or {},
        "camera": job.get("camera") or {},
        "pe_system_extra": job.get("pe_system_extra") or "",
        "pe_system_override": job.get("pe_system_override") or "",
        "pe_task_extra": job.get("pe_task_extra") or "",
    }


def _prompt_lost(pid, st):
    """Checked about every 10 s while we wait for a ComfyUI prompt. Returns an error message once ComfyUI clearly
    no longer has it: it closed for 90 s, or the prompt is in neither its queue nor its history twice in a row
    (ComfyUI restarted or ran out of memory). Without this a lost job showed "Generating" for 40 minutes."""
    now = time.time()
    if now - st.get("t", now - 11) < 10:
        return None
    st["t"] = now
    try:
        q = comfy("GET", "/queue", timeout=10)
    except Exception:
        st["down"] = st.get("down") or now
        if now - st["down"] > 90:
            return "ComfyUI closed while this was running (it may have run out of memory). Start it again, then retry."
        return None
    st["down"] = None
    ids = set()
    for x in (q.get("queue_running") or []) + (q.get("queue_pending") or []):
        if isinstance(x, (list, tuple)) and len(x) > 1:
            ids.add(str(x[1]))
    if pid in ids:
        st["miss"] = 0
        return None
    try:
        h = comfy("GET", "/history/" + pid, timeout=10)
    except Exception:
        h = {}
    if h:
        st["miss"] = 0
        return None
    st["miss"] = st.get("miss", 0) + 1
    if st["miss"] >= 2:
        return "ComfyUI lost this job (it restarted or ran out of memory). Try again."
    return None


def _wait_for_pe(job):
    """Wait until the PE-only prompt finishes. Does not touch the library."""
    pid = job["prompt_id"]
    t0 = time.time()
    lost = {}
    while time.time() - t0 < 60 * 40:
        if job["status"] == "cancelled":
            return None
        gone = _prompt_lost(pid, lost)
        if gone:
            job["pe_error"] = gone
            return None
        ev = done_events.get(pid)
        hist = None
        if ev:
            try:
                hist = comfy("GET", "/history/" + pid, timeout=30)
            except Exception:
                hist = {}
            entry = hist.get(pid) or (next(iter(hist.values())) if hist else {})
            if ev.get("ok"):
                return PE.history_preview_text(entry, "pe_preview")
            # PE failed in ComfyUI — caller falls back to the raw prompt
            job["pe_error"] = ev.get("error") or "Prompt Enhancer failed."
            return None
        if live.get("prompt_id") == pid:
            job["progress"] = live.get("value") or 0
            job["progress_max"] = live.get("max") or 0
            job["message"] = "Enhancing prompt (%s)..." % ("Quality" if job.get("pe_mode") == "quality" else "Fast")
        try:
            hist = comfy("GET", "/history/" + pid, timeout=10)
            if pid in hist or (hist and list(hist)[0] == pid):
                entry = hist.get(pid) or next(iter(hist.values()))
                st = (entry.get("status") or {})
                s = (st.get("status_str") or "").lower()
                if s == "success" or st.get("completed") is True:
                    done_events[pid] = {"ok": True}
                    continue
                if s in ("error", "interrupted"):
                    msgs = st.get("messages") or []
                    err = ""
                    for m in msgs:
                        if isinstance(m, list) and m and m[0] in ("execution_error", "execution_interrupted"):
                            d = m[1] if len(m) > 1 and isinstance(m[1], dict) else {}
                            err = d.get("exception_message") or m[0]
                    done_events[pid] = {"ok": False, "error": err or s}
                    continue
        except Exception:
            pass
        time.sleep(0.4)
    job["pe_error"] = "Prompt Enhancer timed out."
    return None


def run_prompt_enhancer(job):
    """Fill job['enhanced_prompt'] from cache or a PE-only ComfyUI run. Never fails the image job: falls back to the user prompt."""
    raw = (job.get("pe_input") or job.get("prompt_user") or job.get("prompt") or "").strip()
    job["enhanced_prompt"] = ""
    job["pe_fallback"] = False
    job["pe_cached"] = False
    key = PE.cache_key(_pe_cache_payload(job))
    cached = PE.cache_get(DATA, key)
    if cached:
        job["enhanced_prompt"] = cached
        job["pe_cached"] = True
        job["prompt"] = cached
        job["message"] = "Using cached enhanced prompt."
        return True
    models = dict(job.get("models") or {})
    models["pe_clip_name"] = job.get("pe_clip_used") or models.get("pe_clip_name")
    try:
        graph, meta = make_pe_graph(
            raw, job["mode"], job.get("pe_images") if job.get("pe_images") is not None else (job.get("source_images") or []),
            job.get("pe_mode") or "fast", models,
            pe_system_extra=job.get("pe_system_extra") or "", pe_task_extra=job.get("pe_task_extra") or "",
            pe_system_override=job.get("pe_system_override") or "", pe_tokens=job.get("pe_tokens"),
            media=job.get("pe_media") or "image",
        )
        oi = object_info()
        problems = validate_graph(graph, oi, input_root=INPUT)
        if problems and any("FreeMemory" in p or "AUSBOSS" in p for p in problems):
            graph, meta = graphs.build_pe_only(
                raw, mode=job["mode"], images=job.get("pe_images") if job.get("pe_images") is not None else (job.get("source_images") or []),
                pe_mode=job.get("pe_mode") or "fast",
                models=models, free_memory=False,
                pe_system_extra=job.get("pe_system_extra") or "", pe_task_extra=job.get("pe_task_extra") or "",
                pe_system_override=job.get("pe_system_override") or "", pe_tokens=job.get("pe_tokens"),
                media=job.get("pe_media") or "image",
            )
            problems = validate_graph(graph, oi, input_root=INPUT)
        if problems:
            raise ValueError("The Prompt Enhancer workflow did not match this ComfyUI install. " + " | ".join(problems[:8]))
    except Exception as e:
        log.info("PE graph: %s", e)
        job["pe_fallback"] = True
        job["pe_error"] = str(e)
        job["message"] = "Prompt Enhancer was skipped; using your original prompt."
        return False
    job["message"] = "Enhancing prompt (%s)..." % ("Quality" if job.get("pe_mode") == "quality" else "Fast")
    body = {"prompt": graph, "client_id": CLIENT_ID}
    try:
        resp = comfy("POST", "/prompt", body, timeout=60)
    except Exception as e:
        job["pe_fallback"] = True
        job["pe_error"] = str(e)
        job["message"] = "Prompt Enhancer could not start; using your original prompt."
        return False
    pid = resp.get("prompt_id") or resp.get("promptId")
    if not pid:
        job["pe_fallback"] = True
        job["pe_error"] = "ComfyUI did not accept the Prompt Enhancer job."
        return False
    job["prompt_id"] = pid
    text = _wait_for_pe(job)
    job["prompt_id"] = None
    if job["status"] == "cancelled":
        return False
    caption = PE.extract_expanded_caption(text or "")
    if not caption:
        job["pe_fallback"] = True
        job["pe_raw"] = text or ""
        job["pe_error"] = job.get("pe_error") or "Prompt Enhancer did not return usable JSON."
        job["message"] = ("Prompt Enhancer did not return usable JSON, so your original prompt was used."
                          + (" It may have run out of room: raise the Tokens dial under Enhance." if job.get("pe_tokens") else ""))
        return False
    job["enhanced_prompt"] = caption
    job["prompt"] = caption
    try:
        PE.cache_put(DATA, key, caption, {"mode": job.get("mode"), "pe_mode": job.get("pe_mode")})
    except Exception:
        pass
    return True


def apply_enhanced_prompt(job):
    """Put the (enhanced or original) prompt into the image graph, same seed and size."""
    prompt = (job.get("prompt") or job.get("prompt_user") or "").strip()
    prompt = lora_trigger_prompt(prompt, job.get("pe_loras"))   # the rewrite would otherwise drop LoRA trigger words
    g_now = job.get("graph") or {}
    node = g_now.get("user_prompt")
    if node and node.get("class_type") == "PrimitiveStringMultiline" and "value" in (node.get("inputs") or {}):
        # change only the prompt text: rebuilding would throw away what was added after the build (LoRAs ...)
        node["inputs"]["value"] = prompt
        job["prompt_sent"] = prompt
        return g_now
    graph, meta = build_main_graph(
        job["mode"], prompt, job.get("resolution_in") or "2048x2048",
        job.get("seed"), job.get("steps"), job.get("models") or {},
        job.get("source_images") or [], job.get("input_size"),
        ref_mode=bool(job.get("ref_mode")),
    )
    try:
        problems = validate_graph(graph, object_info(), input_root=INPUT)
    except Exception:
        problems = []
    if problems:
        raise ValueError("The workflow did not match this ComfyUI install. " + " | ".join(problems[:12]))
    job["graph"] = graph
    pe_flag = job.get("pe")
    pe_mode = job.get("pe_mode")
    pe_clip = job.get("pe_clip_used")
    pe_small = job.get("pe_small_model")
    enhanced = job.get("enhanced_prompt")
    prompt_user = job.get("prompt_user")
    job.update(meta)
    # Keep PE flags that meta would otherwise overwrite (build_main is pe=False).
    job["pe"] = pe_flag
    job["pe_mode"] = pe_mode
    job["pe_clip_used"] = pe_clip
    job["pe_small_model"] = pe_small
    job["enhanced_prompt"] = enhanced
    job["prompt_user"] = prompt_user
    return graph


_wf_drafts = {}


def _wf_model_options(card):
    """Dropdown choices for each mapped loader / sampler, read from ComfyUI."""
    try:
        oi = object_info()
    except Exception:
        oi = {}
    opts = {"models": [], "sampler": [], "scheduler": []}
    for i, m in enumerate((card.get("slots") or {}).get("models") or []):
        choices = combo_list(oi, m.get("class_type"), m.get("input")) or []
        if m.get("only"):          # e.g. the Krea 2 Edit LoRA list: just the edit LoRAs, not every LoRA on the PC
            keep = [c for c in choices if any(s in c.lower() for s in m["only"])]
            choices = keep or choices
        want = m.get("value")
        have = (not choices) or want in choices
        sug = None if have else (WF.match_model_file(want, choices)[0] or (choices[0] if choices else None))
        opts["models"].append({"key": str(i), "label": m.get("label") or m.get("kind") or "Model",
                               "title": m.get("title"), "value": want if have else sug, "choices": choices,
                               "wanted": want, "installed": have})
    for key, inp in (("sampler", "sampler_name"), ("scheduler", "scheduler")):
        sl = ((card.get("slots") or {}).get(key) or [])
        if sl:
            opts[key] = combo_list(oi, sl[0].get("class_type"), inp) or []
    return opts


def _wf_candidates(graph):
    """Free-text fields for the check screen's Prompt box / Negative prompt lists: best guess first, technical
    fields (separators, maths, file names) left out, each with a plain description."""
    try:
        oi = object_info()
    except Exception:
        oi = {}
    out = []
    for i, c in enumerate(WF.rank_prompt_candidates(graph, oi)[:60]):
        c = dict(c)
        c["suggested"] = (i == 0 and c["score"] >= 4 and not c["negative"])
        out.append(c)
    return out


def explain_problems(graph, problems):
    """Turn ComfyUI validation problems into plain fixes the app can show (and act on)."""
    try:
        oi = object_info()
    except Exception:
        oi = {}
    missing = sorted({n.get("class_type") for n in graph.values() if oi and n.get("class_type") not in oi})
    fixes = []
    packs = []
    if missing:
        try:
            res = NM.resolve(missing)
            packs = res.get("packs") or []
            for pk in packs:
                fixes.append({"text": "Install the node pack \u201c%s\u201d (needed for %s)." % (pk["title"], ", ".join(pk["nodes"])),
                              "pack": pk["id"] if pk.get("registry") and not pk.get("installed") else None,
                              "restart": bool(pk.get("installed"))})
            for cls in res.get("unknown") or []:
                fixes.append({"text": "%s isn't in the ComfyUI registry. Install the pack that provides it by hand." % cls})
        except Exception:
            fixes.append({"text": "These nodes are missing: %s." % ", ".join(missing)})
    for p in problems:
        low = p.lower()
        if "not an option comfyui currently lists" in low or "value not in list" in low:
            m = re.search(r"is '([^']+)'", p)
            fixes.append({"text": "The file %s isn't in your ComfyUI. Pick another one in Settings \u203a Models in this workflow." % (m.group(1) if m else ""),
                          "settings": True})
        elif "missing required input" in low:
            fixes.append({"text": p + " The workflow may need a newer version of that node pack."})
        elif any(cls in p for cls in missing):
            continue
        else:
            fixes.append({"text": p})
    msg = "This workflow can't run yet. " + (fixes[0]["text"] if fixes else "")
    return NeedsAction(msg, "fix", fixes=fixes[:8])


def apply_effective(card, hw):
    """Swap a video card's sizes and lengths for what this PC can do (see specs.py)."""
    eff = SP.effective(card, hw or {})
    if not eff:
        return card
    card = dict(card)
    card["resolutions"] = eff["resolutions"]
    card["limits"] = eff["limits"]
    card["limits_basis"] = eff["basis"]
    if eff.get("clip_seconds"):
        card["clip_seconds"] = eff["clip_seconds"]
    if card.get("default_resolution") not in card["resolutions"]:
        card["default_resolution"] = card["resolutions"][0]
    return card


def upscale_submit(data):
    """Upscale a finished video from the library with a method this PC can use."""
    it = gallery_get(data.get("id") or "")
    if not it or (it.get("media") or "image") != "video":
        raise ValueError("Pick a finished video to upscale.")
    hw = hardware()
    oi = object_info(force=True)
    methods = {m["id"]: m for m in SP.upscalers(hw, oi, os.path.join(COMFY_ROOT, "models"))}
    m = methods.get(data.get("method") or "")
    target = data.get("target") or "1080p"
    if not m or target not in m["targets"]:
        raise ValueError("That upscale isn't available on this PC.")
    short = dict(SP.TARGETS)[target]
    src = output_path(it.get("subfolder") or "", it["filename"])
    if not src or not os.path.isfile(src):
        raise ValueError("The video file is missing.")
    name = "cs_up_%s%s" % (it["id"], os.path.splitext(src)[1])
    shutil.copy2(src, os.path.join(INPUT, name))
    w0, h0 = it.get("width") or 0, it.get("height") or 0
    if w0 and h0:
        k = short / float(min(w0, h0))
        tw, th = int(round(w0 * k / 8)) * 8, int(round(h0 * k / 8)) * 8
    else:
        tw, th = (1920, 1080) if short == 1080 else ((2560, 1440) if short == 1440 else (3840, 2160))
    g = {"in": {"class_type": "LoadVideo", "inputs": {"file": name}},
         "parts": {"class_type": "GetVideoComponents", "inputs": {"video": ["in", 0]}}}
    if m["id"] == "rtx":
        g["up"] = {"class_type": "RTXVideoSuperResolution", "inputs": {"images": ["parts", 0], "resize_type": "target dimensions",
                   "resize_type.width": tw, "resize_type.height": th, "quality": "ULTRA"}}
        frames = ["up", 0]
    elif m["id"] == "flashvsr_plus":
        f = m["files"]
        sw, sh = (w0 or 832), (h0 or 480)
        lw, lh = max(128, int(round(sw / 64.0)) * 64), max(128, int(round(sh / 64.0)) * 64)
        scale = max(2, min(4, int(-(-short // min(lw, lh)))))   # smallest whole factor that reaches the target
        g["fm"] = {"class_type": "FlashVSR_SM_Model", "inputs": {"dit": f["dit"], "lq_proj": f["lq_proj"],
                   "prompt_embed": f["prompt"], "version": "plus_turbo"}}
        g["fv"] = {"class_type": "FlashVSR_SM_Vae", "inputs": {"vae": "none", "tcd_encoder": f["tcd"]}}
        g["fs"] = {"class_type": "FlashVSR_SM_Sampler", "inputs": {"model": ["fm", 0], "image": ["parts", 0], "width": lw, "height": lh,
                   "seed": 42, "scale": scale, "kv_ratio": 3.5, "local_range": 11, "sparse_ratio": 2.0,
                   "offload": True, "chunk_q": 0, "long_mode": False}}
        g["fd"] = {"class_type": "FlashVSR_SM_Decoder", "inputs": {"latent": ["fs", 0], "vae": ["fv", 0], "tiled": True, "tile_size": 64,
                   "overlap": 16, "temporal_size": 64, "temporal_overlap": 8, "color_fix": True, "fix_method": "wavelet"}}
        g["up"] = {"class_type": "ImageScale", "inputs": {"image": ["fd", 0], "upscale_method": "lanczos", "width": tw, "height": th, "crop": "disabled"}}
        frames = ["up", 0]
    elif m["id"] == "esrgan":
        g["um"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": m["model"]}}
        g["u4"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["um", 0], "image": ["parts", 0]}}
        g["up"] = {"class_type": "ImageScale", "inputs": {"image": ["u4", 0], "upscale_method": "lanczos", "width": tw, "height": th, "crop": "disabled"}}
        frames = ["up", 0]
    else:
        def defaults(cls, over):
            spec = (oi.get(cls) or {}).get("input") or {}
            ins = {}
            for grp in ("required",):
                for k, v in (spec.get(grp) or {}).items():
                    if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], dict) and "default" in v[1]:
                        ins[k] = v[1]["default"]
                    elif isinstance(v, (list, tuple)) and v and isinstance(v[0], list) and v[0]:
                        ins[k] = v[0][0]
            ins.update(over)
            return {"class_type": cls, "inputs": ins}
        g["dit"] = defaults("SeedVR2LoadDiTModel", {"model": m["model"], "blocks_to_swap": 36 if hw.get("vram_gb", 0) < 12 else 0})
        g["vae"] = defaults("SeedVR2LoadVAEModel", {"model": "ema_vae_fp16.safetensors", "encode_tiled": True, "decode_tiled": True})
        g["up"] = defaults("SeedVR2VideoUpscaler", {"image": ["parts", 0], "dit": ["dit", 0], "vae": ["vae", 0], "seed": 42,
                                                    "resolution": short, "max_resolution": max(tw, th),
                                                    "batch_size": 5 if hw.get("vram_gb", 0) < 12 else 21})
        frames = ["up", 0]
    g["mk"] = {"class_type": "CreateVideo", "inputs": {"images": frames, "fps": ["parts", 2], "audio": ["parts", 1]}}
    g["save"] = {"class_type": "SaveVideo", "inputs": {"video": ["mk", 0], "filename_prefix": APP_OUT + "/Upscale/" + it["id"],
                                                       "format": "auto", "codec": "auto"}}
    job = {
        "id": uuid.uuid4().hex[:12], "mode": it.get("mode") or "t2i", "media": "video", "task": "upscale",
        "workflow_name": "Upscale to %s (%s)" % (target, m["label"]), "prompt": it.get("prompt") or "",
        "prompt_user": "Upscale to %s" % target, "prompt_sent": it.get("prompt_sent") or it.get("prompt") or "",
        "status": "queued", "graph": g, "t": time.time(), "images": [], "source_images": [], "alias_warnings": [],
        "message": "Waiting to start…", "progress": 0, "progress_max": 0, "error": "", "prompt_id": None,
        "seed": it.get("seed"), "width": tw, "height": th, "pe": False, "camera": {}, "models": {}, "resolution_in": target,
    }
    jobs[job["id"]] = job
    order.append(job["id"])
    with q_cv:
        job_q.append(job["id"])
        q_cv.notify()
    return job


COMFY_ROOT = SETTINGS["comfy_root"]
NODES_DIR = os.path.join(COMFY_ROOT, "custom_nodes")
COMFY_PY = CN.comfy_python(COMFY_ROOT)        # ComfyUI's own Python, for a node pack's requirements
COMFY_PORTABLE = os.path.dirname(COMFY_ROOT)
LAUNCH_PS1 = os.path.join(COMFY_PORTABLE, "comfy_launch.ps1")
PROFILE_NAMES = {"h3": "MiniMax H3 mode", "video": "low-VRAM video mode", "everyday": "everyday mode"}
_model_index = {"t": 0, "map": {}}


class NeedsAction(ValueError):
    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


_hw_last = {}


def _hw_remembered():
    """The last hardware ComfyUI reported (kept in memory and in data/hardware.json across restarts)."""
    if not _hw_last:
        try:
            _hw_last.update(json.load(open(os.path.join(DATA, "hardware.json"), encoding="utf-8")))
        except Exception:
            pass
    return dict(_hw_last)


def hardware():
    """GPU / RAM / ComfyUI launch profile, read from ComfyUI itself. When ComfyUI is busy or briefly unreachable,
    the last known values are used, so the app never mistakes this PC for a 0 GB one (that capped pictures at 2 MP
    and could call workflows "too big")."""
    out = {"gpu": "", "vram_gb": 0.0, "ram_gb": 0.0, "profile": "", "can_switch": os.path.isfile(LAUNCH_PS1)}
    try:
        st = comfy("GET", "/system_stats", timeout=12) or {}
    except Exception:
        last = _hw_remembered()
        if last.get("vram_gb"):
            last.update(stale=True, can_switch=out["can_switch"])
            return last
        out["error"] = "ComfyUI isn't running."
        return out
    dev = (st.get("devices") or [{}])[0]
    out["gpu"] = re.sub(r"^cuda:\d+\s*", "", dev.get("name") or "").split(" : ")[0].replace("NVIDIA GeForce ", "").strip()
    out["vram_gb"] = round((dev.get("vram_total") or 0) / 1024 ** 3, 1)
    sysinfo = st.get("system") or {}
    out["ram_gb"] = round((sysinfo.get("ram_total") or 0) / 1024 ** 3, 1)
    argv = " ".join(sysinfo.get("argv") or [])
    out["profile"] = "h3" if "--preview-method none" in argv else ("video" if "--lowvram" in argv else "everyday")
    if out["vram_gb"] and {k: _hw_last.get(k) for k in out} != out:
        _hw_last.clear()
        _hw_last.update(out)
        try:
            with open(os.path.join(DATA, "hardware.json"), "w", encoding="utf-8") as f:
                json.dump(out, f)
        except Exception:
            pass
    return out


def _model_size_gb(name):
    """Size of a model file anywhere under ComfyUI/models (cached index by file name)."""
    if time.time() - _model_index["t"] > 600:
        idx = {}
        root = os.path.join(COMFY_ROOT, "models")
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith((".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")):
                    try:
                        idx[f.lower()] = os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        _model_index.update(t=time.time(), map=idx)
    base = re.split(r"[\\/]", str(name or ""))[-1].lower()
    return round(_model_index["map"].get(base, 0) / 1024 ** 3, 2)


def fit_verdict(card, hw=None):
    """'ok' / 'heavy' / 'no' + a short reason, from the card's needs or its model file sizes."""
    hw = hw or hardware()
    vram, ram = hw.get("vram_gb") or 0, hw.get("ram_gb") or 0
    if not vram:
        return {"fit": "unknown", "reason": ""}
    needs = card.get("needs") or {}
    if needs.get("vram_gb"):
        if vram + 0.4 < needs["vram_gb"]:
            return {"fit": "no", "reason": "Needs %s GB of graphics memory; this PC has %s GB." % (needs["vram_gb"], vram)}
        if needs.get("ram_gb") and ram + 1 < needs["ram_gb"]:
            return {"fit": "heavy", "reason": "Wants about %s GB of system RAM; this PC has %s GB. Close other apps first." % (needs["ram_gb"], ram)}
        return {"fit": "ok", "reason": ""}
    biggest = 0.0
    for m in ((card.get("slots") or {}).get("models") or []):
        if (m.get("kind") or "model") == "model":
            biggest = max(biggest, _model_size_gb(m.get("value")))
    if biggest > vram + ram * 0.6:
        return {"fit": "no", "reason": "Its model is %.0f GB, more than this PC can hold." % biggest}
    if biggest > vram:
        return {"fit": "heavy", "reason": "Its model (%.0f GB) is bigger than the %s GB graphics card, so it will be slow." % (biggest, vram)}
    return {"fit": "ok", "reason": ""}


def switch_comfy_profile(profile):
    if profile not in PROFILE_NAMES:
        raise ValueError("Unknown ComfyUI mode.")
    if not os.path.isfile(LAUNCH_PS1):
        raise ValueError("This PC has no comfy_launch.ps1, so switch modes by restarting ComfyUI with the right .bat file.")
    q = comfy_queue() or {}
    if q.get("queue_running") or q.get("queue_pending"):
        raise ValueError("ComfyUI is busy. Wait for the current job to finish, then switch.")
    import subprocess
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LAUNCH_PS1,
                      "-Profile", profile, "-Switch", "-Hidden"], cwd=COMFY_PORTABLE,
                     creationflags=0x08000000 | 0x00000200, env=_quiet_env())
    return "Switching ComfyUI to %s. This takes about a minute." % PROFILE_NAMES[profile]


_comfy_start = {"t": 0}


def _quiet_env():
    """Starts from the app: no browser tab and no console. (Double-clicking a run .bat yourself is unchanged.)"""
    env = dict(os.environ)
    extra = env.get("COMFY_EXTRA", "")
    if "--disable-auto-launch" not in extra:
        env["COMFY_EXTRA"] = (extra + " --disable-auto-launch").strip()
    env["PCL_HIDDEN"] = "1"
    return env


def start_comfy(profile="everyday"):
    """Start ComfyUI wherever it is installed (found from its output folder). Never starts a second copy."""
    import subprocess
    if comfy_up():
        return "ComfyUI is already running."
    if time.time() - _comfy_start["t"] < 120:
        return "ComfyUI is already starting. This takes about a minute."
    flags = 0x08000000 | 0x00000200   # no window, own process group. NOT DETACHED_PROCESS: PowerShell exits at once without a console
    if os.path.isfile(LAUNCH_PS1):
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LAUNCH_PS1, "-Profile",
               profile if profile in PROFILE_NAMES else "everyday", "-Hidden"]
    else:
        bat = next((os.path.join(COMFY_PORTABLE, b) for b in ("run_nvidia_gpu.bat", "run_comfyui.bat", "run_cpu.bat")
                    if os.path.isfile(os.path.join(COMFY_PORTABLE, b))), None)
        py = os.path.join(COMFY_PORTABLE, "python_embeded", "python.exe")
        if os.path.isfile(py) and os.path.isfile(os.path.join(COMFY_ROOT, "main.py")):
            cmd = [py, "-s", os.path.join(COMFY_ROOT, "main.py"), "--windows-standalone-build", "--disable-auto-launch"]
        elif bat:
            cmd = ["cmd", "/c", bat]
        else:
            raise ValueError("Couldn't find how to start ComfyUI on this PC (looked in %s)." % COMFY_PORTABLE)
    subprocess.Popen(cmd, cwd=COMFY_PORTABLE, creationflags=flags, env=_quiet_env())
    _comfy_start["t"] = time.time()
    log.info("starting ComfyUI: %s", " ".join(cmd[:6]))
    return "Starting ComfyUI. This takes about a minute."


def stop_comfy(force=False):
    """Stop the ComfyUI running on this PC (the process listening on its port, with its child processes).
    Refuses while it is generating unless force is set. Only works for a ComfyUI on this PC."""
    import subprocess, urllib.parse
    u = urllib.parse.urlsplit(COMFY)
    if (u.hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("ComfyUI runs on another computer, so it can't be stopped from here.")
    if not comfy_up():
        return "ComfyUI isn't running."
    if not force:
        try:
            q = comfy("GET", "/queue", timeout=5)
            if (q.get("queue_running") or []) or (q.get("queue_pending") or []):
                raise ValueError("ComfyUI is still generating. Stop anyway?")
        except ValueError:
            raise
        except Exception:
            pass
    port = u.port or 8188
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            pids.add(parts[4])
    pids.discard(str(os.getpid()))
    if not pids:
        raise ValueError("Couldn't find the ComfyUI process to stop.")
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, creationflags=0x08000000)
    _comfy_start["t"] = 0
    log.info("stopped ComfyUI (pid %s)", ",".join(sorted(pids)))
    return "ComfyUI stopped."


_wf_test_ids = {}      # upload draft -> its hidden test-run entry


def _card_from_draft(data):
    """The card + graph the upload screen describes (name, family, task, prompt box), not saved yet."""
    d = _wf_drafts.get(data.get("draft") or "")
    if not d:
        raise ValueError("This upload expired. Upload the file again.")
    card, graph = dict(d[0]), d[1]
    card["name"] = (data.get("name") or card.get("name") or "My workflow").strip()[:80]
    card["family"] = (data.get("family") or card.get("family") or "Custom").strip()[:40]
    task = data.get("task") or card.get("task")
    if task in WF.TASKS:
        card["task"] = task
        card["media"] = WF.TASK_MEDIA[task]
    slots = dict(card.get("slots") or {})
    for key in ("prompt", "negative"):
        pick = data.get(key + "_pick")
        if pick == "none":
            slots[key] = []
        elif pick and ":" in pick:
            nid, inp = pick.rsplit(":", 1)      # node ids inside subgraphs contain ":" ("69:49:165")
            n = graph.get(nid)
            if n is not None and inp in (n.get("inputs") or {}):
                slots[key] = [{"node": nid, "input": inp, "title": WF._title(n), "class_type": n.get("class_type"), "label": key}]
    card["slots"] = slots
    if not card.get("description"):
        # a short line for the card, from what the app understood
        found = [k for k in ("prompt", "images", "videos", "audios", "width", "seed", "steps", "length", "loras") if slots.get(k)]
        words = {"prompt": "prompt", "images": "pictures", "videos": "video input", "audios": "audio input",
                 "width": "size", "seed": "seed", "steps": "steps", "length": "length", "loras": "LoRAs"}
        kind = {"t2i": "Text to image", "edit": "Image edit", "t2v": "Text to video", "i2v": "Image to video",
                "r2v": "Reference to video", "v2v": "Video to video", "long": "Long video"}.get(card.get("task"), "Workflow")
        card["description"] = "%s, uploaded. The app controls: %s." % (kind, ", ".join(words[k] for k in found) or "the prompt")
    try:
        card["requires"] = WF.requirements(graph, card, object_info())
    except Exception as e:
        log.info("requirements skipped: %s", e)
    return card, graph


_TEST_PROMPTS = {
    "t2i": "a red apple on a wooden table by a window, soft daylight",
    "edit": "make the sky in the picture a warm sunset orange",
    "t2v": "a slow camera pan across a calm beach at sunset",
    "i2v": "the camera slowly pushes in, gentle natural movement",
    "r2v": "the subject turns slowly and smiles, gentle camera movement",
    "v2v": "keep the scene, make the colours warmer",
    "other": "a red apple on a wooden table by a window, soft daylight",
}


def _test_picture():
    """A plain test picture in the input folder (made once): sky, hills and a sun, so edit and video workflows have
    something to work on."""
    rel = INPUT_SUB + "/_test/test_picture.png"
    full = os.path.join(INPUT, rel.replace("/", os.sep))
    if not os.path.isfile(full):
        from PIL import Image, ImageDraw
        os.makedirs(os.path.dirname(full), exist_ok=True)
        w, h = 768, 1024
        im = Image.new("RGB", (w, h))
        dr = ImageDraw.Draw(im)
        for y in range(h):
            t = y / h
            dr.line([(0, y), (w, y)], fill=(int(120 + 90 * t), int(170 + 40 * t), int(235 - 60 * t)) if t < .62
                    else (int(70 + 40 * t), int(120 + 30 * t), int(60 + 10 * t)))
        dr.ellipse([w * .62, h * .12, w * .82, h * .27], fill=(255, 226, 150))
        dr.polygon([(0, h * .62), (w * .35, h * .48), (w * .7, h * .6), (w, h * .52), (w, h * .66), (0, h * .7)],
                   fill=(88, 128, 82))
        im.save(full)
    return rel


def _test_mask(rel):
    """A painted area in the middle of the test picture (for workflows that repaint an area you choose)."""
    import base64
    from PIL import Image, ImageDraw
    full = os.path.join(INPUT, rel.replace("/", os.sep))
    with Image.open(full) as im:
        w, h = im.size
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse([w * .3, h * .3, w * .7, h * .6], fill=255)
    buf = io.BytesIO()
    m.convert("RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _test_request(card):
    """A small, quick Generate request for a test run of this card."""
    task = card.get("task") or "t2i"
    slots = card.get("slots") or {}
    pic = _test_picture()
    need = max(int(card.get("picture_min") or 0), 1 if task in ("edit", "i2v", "r2v") else 0)
    lengths = [x for x in (card.get("lengths") or []) if isinstance(x, (int, float))]
    req = {"mode": "workflow", "workflow_id": card["id"], "prompt": _TEST_PROMPTS.get(task, _TEST_PROMPTS["t2i"]),
           "images": [pic] * need, "random_seed": True, "pe": False, "auto_assist": False, "understanding": False,
           "res": (card.get("resolutions") or [card.get("default_resolution")])[0] if card.get("media") == "video" else card.get("default_resolution"),
           "aspect": "1:1" if card.get("media") != "video" else ((card.get("aspects") or ["16:9"])[0]),
           "seconds": (min(lengths) if lengths else (1 if card.get("media") == "video" else None))}
    if slots.get("width") or slots.get("megapixels") or slots.get("size_combo") or slots.get("aspect_combo"):
        req["width"], req["height"] = 640, 640          # small: a test only has to prove the workflow runs
    if card.get("native_mask") and need:
        req["inpaint_mask"] = _test_mask(pic)
    return req


def plain_test_problem(msg):
    """A test-run problem in plain words, with what to do about it."""
    low = (msg or "").lower()
    if "out of memory" in low or "oom" in low:
        return "Your graphics card ran out of memory with this workflow. It may be too big for this PC."
    if "no node type" in low or "missing" in low and "node" in low:
        return "This workflow needs an add-on that isn't installed. Install it on this screen, then test again. (" + msg[:200] + ")"
    if "not an option comfyui currently lists" in low or ".safetensors" in low:
        return "This workflow uses a model file that isn't on this PC. (" + msg[:220] + ")"
    if "comfyui" in low and ("not running" in low or "refused" in low or "connect" in low):
        return "ComfyUI isn't running. Start it, then test again."
    return msg[:400] or "The test didn't finish."



def pe_clip_available(name):
    """The Prompt Enhancer model to use: the chosen one if it's on this PC, else another Qwen text model you have
    (the enhancer needs one), else None (enhancing is skipped, your own prompt is used)."""
    try:
        have = _clip_names()
    except Exception:
        return name
    if not have or name in have:
        return name
    hit, _how = WF.match_model_file(name, have)
    if hit:
        return hit
    qwen = [n for n in have if re.search(r"qwen[\s_-]?3", n, re.I) and not re.search(r"(?i)minimax|t5|umt5|clip_l|gemma", n)]
    pref = sorted(qwen, key=lambda n: (0 if re.search(r"(?i)(9b|8b|7b)", n) else 1 if re.search(r"(?i)4b", n) else 2, len(n)))
    return pref[0] if pref else None



def lora_family_for(card):
    """Which LoRAs fit this engine (the key Auto-pick uses). Uploaded workflows: from the model files they load
    (krea2_turbo_... -> Krea 2), because their family label is a guess; built-ins: their label first."""
    label = LI.ENGINE_FAMILY.get(card.get("family") or "")
    files = [m.get("value") for m in ((card.get("slots") or {}).get("models") or [])
             if isinstance(m, dict) and isinstance(m.get("value"), str) and (m.get("kind") in (None, "model", "checkpoint", "unet"))]
    from_files = next((k for k in (LI.family_of(f) for f in files) if k), None)
    if card.get("source") == "user":
        return from_files or label or LI.family_of(card.get("family")) or LI.family_of(card.get("name"))
    return label or from_files or LI.family_of(card.get("family")) or LI.family_of(card.get("name"))


def submit_workflow_job(data):
    wid = data.get("workflow_id") or ""
    card, graph = WF.get(wid)
    slots = card.get("slots") or {}
    prompt = (data.get("prompt") or "").strip()
    if prompt and data.get("understanding") is not False:
        prompt = UND.apply_words(prompt, DATA, data.get("workflow_id") or "")   # the user's own words
    if slots.get("prompt") and not prompt:
        raise ValueError("Enter a prompt.")
    images = [str(x).replace("\\", "/") for x in (data.get("images") or []) if str(x).strip()]
    need = int(card.get("picture_min", len(slots.get("images") or [])) or 0)
    most = int(card.get("picture_max", need) or 0) or len(slots.get("images") or [])
    if need and len(images) < need:
        raise ValueError("This workflow needs %d picture%s. Add %s with + first." % (
            need, "" if need == 1 else "s", "it" if need == 1 else "them"))
    images = images[:most]
    # video / audio inputs (a video to edit, a driving video, a voice clip): uploaded into the app's input folder
    media_in = {}
    for key, word, cnt in (("videos", "video", "video_min"), ("audios", "audio clip", "audio_min")):
        files = [str(x).replace("\\", "/") for x in (data.get(key) or []) if str(x).strip()]
        n_need = int(card.get(cnt) or 0)
        if n_need and len(files) < n_need:
            raise ValueError("This workflow needs %d %s%s. Add %s with + first." % (
                n_need, word, "" if n_need == 1 else "s", "it" if n_need == 1 else "them"))
        for name in files:
            full = os.path.abspath(os.path.join(INPUT, name.replace("/", os.sep)))
            if not (full.startswith(os.path.abspath(INPUT) + os.sep) and os.path.isfile(full)):
                raise ValueError("%s %s was not found. Add it again." % (word.capitalize(), name))
        media_in[key] = files[:len(slots.get(key) or [])]
    hw = hardware()
    card = apply_effective(card, hw)
    needs = card.get("needs") or {}
    verdict = fit_verdict(card, hw)
    if verdict["fit"] == "no":
        alt = card.get("alternative") or ""
        raise NeedsAction(verdict["reason"] + (" Try %s instead." % alt if alt else ""), "hardware", alternative=alt)
    if needs.get("profile") and hw.get("profile") and hw["profile"] != needs["profile"]:
        raise NeedsAction("%s needs ComfyUI in %s, but it's running in %s." % (
            card.get("name") or "This workflow", PROFILE_NAMES.get(needs["profile"], needs["profile"]),
            PROFILE_NAMES.get(hw["profile"], hw["profile"])), "profile", profile=needs["profile"], can_switch=hw.get("can_switch"))
    for name in images:
        full = os.path.abspath(os.path.join(INPUT, name.replace("/", os.sep)))
        if not (full.startswith(os.path.abspath(INPUT) + os.sep) and os.path.isfile(full)):
            raise ValueError("Image %s was not found. Upload it again." % name)
        bake_orientation_file(full)
    inpaint = None
    orig_images = list(images)
    native = card.get("native_mask")
    native_set = None
    if data.get("inpaint_mask") and native and images:
        # the workflow inpaints by itself: give it the mask in the form it reads, keep the whole picture
        nid = uuid.uuid4().hex[:12]
        rel = native_mask_prepare(nid, images[0], data["inpaint_mask"], native["kind"])
        if native["kind"] == "alpha":
            images[0] = rel
        else:
            native_set = (native["node"], rel)
        inpaint = {"native": native["kind"]}
        prompt = area_refs(prompt, data.get("inpaint_areas"))
    elif native and card.get("media") == "image" and not data.get("inpaint_mask"):
        raise ValueError("This workflow repaints an area you choose. Tap Inpaint, paint where to change, then send.")
    if data.get("inpaint_mask") and card.get("media") == "image" and images and not inpaint:
        pre_id = uuid.uuid4().hex[:12]
        crop_rel, inpaint = inpaint_prepare(pre_id, images[0], data["inpaint_mask"], areas=data.get("inpaint_areas"),
                                            numbers=False, explicit=bool(data.get("inpaint_explicit")))
        images[0] = crop_rel
        intent = edit_intent(prompt)
        inpaint["intent"] = intent
        prompt = area_refs(prompt, inpaint.get("areas") or data.get("inpaint_areas"), inpaint_frame(inpaint), inpaint.get("marked"), False)
        if prompt and inpaint.get("marked") and card.get("edit_kind") == "krea_edit":
            # Krea 2 Edit was trained on one plain instruction sentence: keep it short (long rule lists dilute it)
            prompt = (prompt.strip().rstrip(" .") + ", only inside the magenta-outlined area. Keep everything else "
                      "exactly the same, and leave no magenta lines.")
        elif prompt and inpaint.get("marked"):
            prompt = inpaint_instruction(prompt, inpaint.get("areas") or [], False, intent).replace("<Picture 1>", "the picture")
        elif prompt:
            prompt = (prompt.rstrip(" .") + ". Keep the framing, lighting, colours and edges of the picture the same "
                      "so the change blends in seamlessly.")
    pe_keep_tail = []      # sentences that must survive the Prompt Enhancer's rewrite (added back after it)
    if prompt and card.get("edit_kind") == "krea_edit" and guard_on(data, "keep_face", len(images or [])) \
            and "facial identity" not in prompt.lower():
        # the Krea 2 Edit notes' own fix for faces drifting generic
        prompt = prompt.strip().rstrip(" .") + ". Preserve the exact facial identity."
        pe_keep_tail.append("Preserve the exact facial identity.")
    # keep the subject's clothing when the edit isn't about clothing (on unless the user switched it off)
    keep_cloth = guard_on(data, "keep_clothing", len(images or [])) and card.get("edit_kind") != "anypaint" and card.get("media") == "image" \
        and ASSIST.swap_intent(data.get("prompt") or "", len(images or [])) not in ("body_swap", "subject_into_environment", "clothing_transfer")
    if prompt and keep_cloth:
        style = "plain" if (card.get("edit_kind") == "krea_edit" or card.get("prompt_style") == "plain") else ""
        prompt, _cg = CG.apply(prompt, data.get("prompt") or "", "edit" if images else "t2i", style)
    # an edit that names one garment changes only that one (the others stay as they are)
    if (prompt and images and card.get("media") == "image" and guard_on(data, "only_garment", len(images))
            and card.get("edit_kind") != "anypaint" and ASSIST.swap_intent(data.get("prompt") or "", len(images)) not in ("body_swap", "subject_into_environment", "clothing_transfer", "head_swap", "face_swap")):
        gstyle = "plain" if (card.get("edit_kind") == "krea_edit" or card.get("prompt_style") == "plain") else ""
        prompt, _og = CG.apply_only_garment(prompt, data.get("prompt") or "", "edit", gstyle)
    # a removal changes only what it names
    if prompt and images and card.get("media") == "image" and card.get("edit_kind") != "anypaint" \
            and guard_on(data, "only_target", len(images)):
        rstyle = "plain" if (card.get("edit_kind") == "krea_edit" or card.get("prompt_style") == "plain") else ""
        prompt, _rg = CG.apply_removal(prompt, data.get("prompt") or "", "edit", rstyle)
    # a full mask / helmet the prompt adds hides the hair (models tend to leave hair poking out around it)
    if prompt and card.get("media") == "image" and card.get("edit_kind") != "anypaint":
        hstyle = "plain" if (card.get("edit_kind") == "krea_edit" or card.get("prompt_style") == "plain") else ""
        prompt, _hg = CG.apply_headgear(prompt, data.get("prompt") or "", "edit" if images else "t2i", hstyle)
    keep_q = (guard_on(data, "keep_quality", len(images or [])) and bool(images) and card.get("media") == "image"
              and card.get("edit_kind") != "anypaint" and not data.get("ref_mode") and not data.get("inpaint_mask"))
    if prompt and keep_q:
        qstyle = "plain" if (card.get("edit_kind") == "krea_edit" or card.get("prompt_style") == "plain") else ""
        prompt, _qg = CG.apply_quality(prompt, data.get("prompt") or "", "edit", qstyle)
    if prompt and data.get("understanding") is not False:
        prompt = UND.apply_extra(prompt, DATA, card.get("id"))   # the user's "always add" sentences
    camera = CAMERA.normalize_camera(data.get("camera") or {})
    pe_input = prompt      # an edit's enhancer reads your words WITH the keep-clothing / removal / quality guards
    prompt_sent = prompt
    dialect = PD.for_card(card)
    is_image = card.get("media") == "image"
    assist_info = None
    # Auto understanding: the same <Picture N> notes the built-in engine gets (roles, outfits, sheets, ...).
    # every engine: new pictures get the text-to-image understanding (sheets, camera ...), edits the edit one, and a
    # video made from pictures uses them as references (keeps who is who)
    if prompt and data.get("auto_assist") and not data.get("inpaint_mask") and card.get("assist", True) is not False:
        video_refs = (not is_image) and bool(images)
        a_mode = ("ref" if (data.get("ref_mode") or video_refs) else "edit") if images else "t2i"
        try:
            assist_info = ASSIST.build_assist_prompt(
                prompt, data.get("image_roles"), n_images=len(images), ref_mode=bool(data.get("ref_mode")) or video_refs,
                enabled=True, camera=camera, mode=a_mode,
                width=data.get("width"), height=data.get("height"))
            prompt_sent = assist_info["prompt"]
        except Exception as e:
            log.info("workflow assist skipped: %s", e)
            assist_info = None
    if assist_info is None and camera and prompt:
        try:
            prompt_sent = CAMERA.apply_camera(prompt, camera, mode="edit" if images else "t2i")["prompt"]
        except Exception:
            prompt_sent = prompt
    prompt_sent = PD.translate(prompt_sent, dialect)
    prompt_sent = lora_trigger_prompt(prompt_sent, data.get("loras"))
    # Enhance: new pictures, videos and picture EDITS (inpaint keeps your own words: its area wording is exact)
    pe_on = bool(card.get("media") in ("image", "video") and prompt and data.get("pe") and card.get("enhance", True) is not False
                 and not (card.get("media") == "image" and data.get("inpaint_mask"))
                 and (slots.get("prompt") or []))
    seed = data.get("seed")
    if data.get("random_seed") or seed in (None, "", "random"):
        seed = None
    if card.get("prompt_style") == "h3":
        prompt_sent = WF.h3_wrap(prompt_sent, card.get("prompt_trigger") or "")
    res = data.get("res") or card.get("default_resolution")
    if card.get("resolutions") and res not in card["resolutions"]:
        res = card.get("default_resolution") or card["resolutions"][0]
    aspect = data.get("aspect") or "16:9"
    if card.get("aspects") and aspect not in card["aspects"]:
        aspect = card["aspects"][0]
    seconds = data.get("seconds") or card.get("default_length")
    notes = []
    cap = (card.get("limits") or {}).get(res)
    if seconds and cap and float(seconds) > cap:
        notes.append("%s makes at most %s s at %s on this PC, so the clip is %s s." % (card.get("name"), cap, res, cap))
        seconds = cap
    if card.get("prompt_style") == "h3_frames":
        prompt_sent = WF.h3_frames(prompt_sent, len(images), seconds)
    if card.get("prompt_suffix") and prompt_sent and card["prompt_suffix"] not in prompt_sent:
        prompt_sent = prompt_sent.rstrip(" .") + ". " + card["prompt_suffix"]
    width, height = data.get("width"), data.get("height")
    if inpaint and inpaint.get("box") and (slots.get("width") or []):
        cx0, cy0, cx1, cy1 = inpaint["box"]
        mp = max(1.0, min(2.0, inpaint["crop_mp"])) * 1e6
        k = (mp / float((cx1 - cx0) * (cy1 - cy0))) ** 0.5
        width, height = int((cx1 - cx0) * k), int((cy1 - cy0) * k)
    if inpaint and inpaint.get("box") and slots.get("aspect_label"):
        aspect = "match"
    if card.get("size_rule"):
        width, height = WF.size_from_rule(card["size_rule"], res, aspect if aspect != "match" else "16:9")
    neg_in = data.get("negative")
    try:
        cfg_now = float(data.get("cfg") or (card.get("defaults") or {}).get("cfg") or 1)
    except (TypeError, ValueError):
        cfg_now = 1.0
    if keep_cloth and (slots.get("negative") or []) and cfg_now > 1.05:
        neg_in = CG.negative(neg_in, data.get("prompt") or "", "edit" if images else "t2i")
    if keep_q and (slots.get("negative") or []) and cfg_now > 1.05:
        neg_in = CG.negative_quality(neg_in, data.get("prompt") or "", "edit")
    krea_extra_loras = []
    if card.get("edit_kind") == "krea_edit" and data.get("edit_intent") and len(images or []) >= 2:
        krea_extra_loras = krea_swap_pick(data, ASSIST.swap_intent(data.get("prompt") or "", len(images)))
        if data.get("edit_intent") == "outfit":
            prompt_sent = (prompt_sent.strip().rstrip(" .") + ". Use only the outfit from the second picture; keep "
                           "their face, hair, body shape, pose and the background exactly the same.")
            pe_keep_tail.append("Use only the outfit from the second picture; keep their face, hair, body shape, "
                                "pose and the background exactly the same.")
    values = {
        "prompt": prompt_sent,
        "negative": neg_in,
        "images": images,
        "seed": seed,
        "steps": data.get("steps"),
        "cfg": data.get("cfg"),
        # "lcm_detail" is the built-in engine's recipe, not a sampler name a workflow node accepts
        "sampler": None if data.get("sampler_name") == graphs.LCM_DETAIL else data.get("sampler_name"),
        "scheduler": data.get("scheduler"),
        # engines that follow Picture 1 (Krea 2 Edit): "Original" keeps the workflow's own working size
        "width": None if (card.get("size_follows_input") and data.get("size_auto")) else width,
        "height": None if (card.get("size_follows_input") and data.get("size_auto")) else height,
        "seconds": seconds,
        "res": res,
        "aspect": aspect,
        "clip": card.get("clip_seconds"),
        "fps": data.get("fps"),
        "models": data.get("models") or {},
        "loras": [l for l in (data.get("loras") or []) if isinstance(l, dict)]
                 + swap_loras(card, (assist_info or {}).get("intent"), data.get("loras")) + krea_extra_loras,
        "videos": media_in.get("videos") or [],
        "audios": media_in.get("audios") or [],
        # Auto mode only: the edit type the app read from the instruction (Krea 2 Edit knobs)
        "edit_intent": data.get("edit_intent") if (card.get("edit_kind") == "krea_edit"
                                                   and isinstance(data.get("edit_intent"), str)
                                                   and data.get("edit_intent") in WF.KREA_INTENT_KNOBS) else None,
    }
    g, used_seed = WF.fill(card, graph, values, APP_OUT + "/wf/" + card["id"])
    # sorted save folder: ComfyStudio/<engine>/<model>/<t2i|edit|video>/<workflow id>_00001_.png
    wf_model = graph_model(g)
    wf_prefix = out_dir(engine_label(card), wf_model, kind_label(card)) + "/" + _seg(card["id"], "workflow")
    for n in g.values():
        fp = (n.get("inputs") or {}).get("filename_prefix")
        if isinstance(fp, str) and fp.replace("\\", "/").startswith(APP_OUT + "/wf/"):
            n["inputs"]["filename_prefix"] = wf_prefix
    if isinstance(data.get("overrides"), dict) and data["overrides"]:
        # Manual mode: the user's own value for any setting in the workflow, checked against the node's limits
        try:
            n_set = WF.apply_overrides(g, data["overrides"], WF.workflow_knobs(card, graph, object_info()))
            log.info("manual overrides applied: %d", n_set)
        except Exception as e:
            log.info("manual overrides skipped: %s", e)
    if inpaint and inpaint.get("box"):
        try:
            inpaint["noise_mask"] = inpaint_noise_mask(g, inpaint, pre_id)
        except Exception as e:
            log.exception("inpaint area")
            # running without it would redraw the whole picture, so stop with a reason instead
            raise ValueError("Couldn't prepare the inpaint area (%s: %s). Close and reopen Comfy Studio, then try "
                             "again; if it keeps happening, app.log in the data folder has the details."
                             % (type(e).__name__, str(e)[:120]))
    if native_set and native_set[0] in g:
        g[native_set[0]]["inputs"]["image"] = native_set[1]
        if "channel" in g[native_set[0]]["inputs"]:
            g[native_set[0]]["inputs"]["channel"] = "red"
    try:
        WF.fix_moved_files(g, object_info())
    except Exception as e:
        log.info("fix_moved_files skipped: %s", e)
    try:
        # a model / LoRA the workflow wants but this PC doesn't have never stops the run: use what you have
        notes += WF.use_installed_models(g, object_info())
    except Exception as e:
        log.info("use_installed_models skipped: %s", e)
    try:
        problems = validate_graph(g, object_info(), input_root=INPUT)
    except Exception as e:
        log.info("workflow validate skipped: %s", e)
        problems = []
    if problems:
        raise explain_problems(g, problems)
    defaults = card.get("defaults") or {}
    job = {
        "id": uuid.uuid4().hex[:12],
        "mode": "edit" if images else "t2i",
        "media": card.get("media") or "image",
        "task": card.get("task"),
        "workflow_id": card["id"],
        "workflow_name": card.get("name") or "Workflow",
        "model_name": model_label(wf_model),
        "prompt": prompt_sent,
        "prompt_user": (data.get("prompt") or "").strip() or prompt,   # what was typed (Reuse / Edit this refill it)
        "prompt_sent": prompt_sent,
        "status": "queued",
        "graph": g,
        "t": time.time(),
        "images": [],
        "source_images": orig_images,
        "note": " ".join(notes),
        "inpaint": inpaint,
        "edit_kind": "inpaint" if inpaint else None,
        "auto_assist": bool(assist_info),
        "assist_intent": (assist_info or {}).get("intent"),
        "dialect": dialect.get("id"),
        "alias_warnings": [],
        "message": " ".join(notes) or "Waiting to start…",
        "progress": 0,
        "progress_max": 0,
        "error": "",
        "prompt_id": None,
        "seed": used_seed,
        "steps": values["steps"] or defaults.get("steps"),
        "width": WF.snap(values["width"]) if values["width"] else defaults.get("width"),
        "height": WF.snap(values["height"]) if values["height"] else defaults.get("height"),
        "sampler_name": values["sampler"] or defaults.get("sampler"),
        "scheduler": values["scheduler"] or defaults.get("scheduler"),
        "cfg": values["cfg"] if values["cfg"] not in (None, "") else defaults.get("cfg"),
        "pe": False,
        "camera": camera,
        "models": {},
        "resolution_in": "",
    }
    if pe_on:
        pe_mode = "quality" if data.get("pe_mode") == "quality" else "fast"
        pe_clip_used, pe_small = PE.pe_clip_for(pe_mode, _clip_names(), graphs.DEFAULTS["pe_clip_name"])
        pe_clip_used = pe_clip_available(pe_clip_used)
        if not pe_clip_used:            # no enhancer model on this PC: run with your own prompt
            pe_on = False
            job["note"] = (job.get("note", "") + " No Prompt Enhancer model is installed, so your own prompt was used.").strip()
            job["message"] = job["note"]
    if pe_on:
        pe_task = card.get("pe_task") or ("edit" if images else "t2i")
        pe_media = card.get("media") or "image"
        image_edit = pe_media == "image" and pe_task == "edit"
        if image_edit and dialect.get("id") != "minimax_h3_image":
            # not MiniMax H3: a plain edit instruction (these models don't read <Subject N> captions), and no
            # new-picture style note (it asks for a full scene description, which makes an edit redraw everything)
            pe_media = graphs.GENERIC_MEDIA
            extra = "Target edit model: %s." % (card.get("name") or card.get("family") or "an image-editing model")
        else:
            extra = dialect.get("hint") or ""
        if assist_info:
            extra = (ASSIST.pe_task_prefix(assist_info) + "\n" + extra).strip()
        job.update({
            "pe": True, "pe_mode": pe_mode, "pe_tokens": pe_tokens_in(data.get("pe_tokens")), "pe_media": pe_media,
            # edits: the enhancer reads your words plus the guards; sentences in pe_keep_tail are added back after it
            "pe_input": pe_input if image_edit else "",
            "pe_keep_tail": list(pe_keep_tail) if image_edit else [],
            "pe_loras": [l for l in (data.get("loras") or []) if isinstance(l, dict)],
            "pe_clip_used": pe_clip_used, "pe_small_model": pe_small,
            "mode": pe_task, "pe_images": list(images) if pe_task == "edit" else [],
            "pe_system_extra": ASSIST.pe_system_addon(assist_info) if assist_info else "",
            "pe_task_extra": extra,
            "pe_system_override": pe_override(card.get("id"), pe_task),
            "wf_prompt_slots": slots.get("prompt") or [],
            "prompt_style": card.get("prompt_style") or "",
            "prompt_trigger": card.get("prompt_trigger") or "",
            "prompt_suffix": card.get("prompt_suffix") or "",
            "frames_n": len(images), "seconds": seconds,
            "roles": (assist_info or {}).get("roles") or [],
            "ref_mode": bool(data.get("ref_mode")),
        })
    jobs[job["id"]] = job
    order.append(job["id"])
    with q_cv:
        job_q.append(job["id"])
        q_cv.notify()
    return job


def apply_wf_enhanced_prompt(job):
    """Workflow engines: put the enhanced prompt into the workflow's own prompt box(es)."""
    card = {"profile": job.get("dialect"), "prompt_style": job.get("prompt_style")}
    dialect = PD.for_card(card) if job.get("dialect") != "minimax_h3_image" else PD.for_card({"prompt_style": "h3"})
    text = (job.get("prompt") or job.get("prompt_user") or "").strip()
    camera = job.get("camera") or {}
    if camera and text:
        try:
            text = CAMERA.apply_camera(text, camera, mode=job.get("mode") or "t2i")["prompt"]
        except Exception:
            pass
    for s in job.get("pe_keep_tail") or []:      # e.g. Krea's "Preserve the exact facial identity."
        if s.lower().rstrip(" .") not in text.lower():
            text = text.rstrip(" .") + ". " + s
    text = PD.translate(text, dialect)
    text = lora_trigger_prompt(text, job.get("pe_loras"))   # the rewrite would otherwise drop LoRA trigger words
    if job.get("prompt_style") == "h3":
        text = WF.h3_wrap(text, job.get("prompt_trigger") or "")
    elif job.get("prompt_style") == "h3_frames":
        text = WF.h3_frames(text, job.get("frames_n") or 1, job.get("seconds"))
    if job.get("prompt_suffix") and job["prompt_suffix"] not in text:
        text = text.rstrip(" .") + ". " + job["prompt_suffix"]
    for slot in job.get("wf_prompt_slots") or []:
        node = job["graph"].get(str(slot["node"]))
        if node is not None:
            node["inputs"][slot["input"]] = text
    job["prompt_sent"] = text


def native_mask_prepare(job_id, image_rel, mask_data, kind):
    """Painted areas -> what the workflow's own mask input expects.
    alpha: a copy of Picture 1 whose painted areas are transparent (Load Image reads that as its MASK output).
    mask_node: a black/white mask picture (white = change), read by Load Image (as Mask) on its red channel."""
    import base64
    from PIL import Image, ImageOps, ImageFilter
    raw = base64.b64decode(str(mask_data).split(",", 1)[-1])
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("That mask is too large.")
    src_full = os.path.abspath(os.path.join(INPUT, image_rel.replace("/", os.sep)))
    with Image.open(src_full) as im:
        src = ImageOps.exif_transpose(im).convert("RGB")
    m = Image.open(io.BytesIO(raw)).convert("L")
    if m.size != src.size:
        m = m.resize(src.size, Image.BILINEAR)
    m = m.point(lambda v: 255 if v > 40 else 0)
    if not m.getbbox():
        raise ValueError("Paint over the area you want to change first.")
    m = m.filter(ImageFilter.MaxFilter(5))          # a touch of growth so edges blend
    sub = os.path.join(INPUT, INPUT_SUB, "_inpaint")
    os.makedirs(sub, exist_ok=True)
    if kind == "alpha":
        rgba = src.convert("RGBA")
        rgba.putalpha(m.point(lambda v: 255 - v))
        name = "%s_masked.png" % job_id
        rgba.save(os.path.join(sub, name))
    else:
        name = "%s_mask.png" % job_id
        m.convert("RGB").save(os.path.join(sub, name))
    return "%s/_inpaint/%s" % (INPUT_SUB, name)


_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ORDW = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
         "ninth": 9, "tenth": 10, "last": -1}
_AREA_NOUN = r"(?:painted\s+area|paint(?:ed)?\s+(?:spot|bit|part)|brush\s*stroke|brush|stroke|area|mask|masked\s+area|" \
             r"selection|spot|region|patch|box|zone|section|marked\s+area|mark)"


def _area_num(tok, count):
    t = tok.lower().strip(".")
    if t.isdigit():
        return int(t)
    if t in _NUMW:
        return _NUMW[t]
    m = re.match(r"(\d+)(?:st|nd|rd|th)$", t)
    if m:
        return int(m.group(1))
    if t in _ORDW:
        return count if _ORDW[t] == -1 else _ORDW[t]
    return None


def area_refs(prompt, areas, frame=(0.0, 0.0, 1.0, 1.0), marked=False, numbered=True):
    """'brush 1', 'the second area', 'selection #2', '2nd brush' ... -> where that painted area is in the picture the
    model sees (e.g. 'the lower-left painted area'). Edit models understand places, not the app's brush numbers.
    areas: [{"n", "x0", "y0", "x1", "y1"}] in 0..1 of the full picture; frame: the part the model sees, same units."""
    try:
        areas = [a for a in (areas or []) if all(k in a for k in ("x0", "y0", "x1", "y1"))][:20]
    except Exception:
        return prompt
    if not prompt or not areas:
        return prompt
    fx0, fy0, fx1, fy1 = frame
    fw, fh = max(1e-6, fx1 - fx0), max(1e-6, fy1 - fy0)
    info = []
    for i, a in enumerate(areas):
        cx = ((float(a["x0"]) + float(a["x1"])) / 2 - fx0) / fw
        cy = ((float(a["y0"]) + float(a["y1"])) / 2 - fy0) / fh
        col = "left" if cx < 0.36 else ("right" if cx > 0.64 else "")
        row = "top" if cy < 0.36 else ("bottom" if cy > 0.64 else "")
        info.append({"n": int(a.get("n") or i + 1), "cx": cx, "cy": cy, "row": row, "col": col})
    for d in info:
        d["name"] = "-".join(x for x in (d["row"], d["col"]) if x) or "centre"
    # same place name twice: tell them apart by order across the picture
    groups = {}
    for d in info:
        groups.setdefault(d["name"], []).append(d)
    for name, g in groups.items():
        if len(g) < 2:
            continue
        horiz = (max(x["cx"] for x in g) - min(x["cx"] for x in g)) >= (max(x["cy"] for x in g) - min(x["cy"] for x in g))
        g.sort(key=lambda x: x["cx"] if horiz else x["cy"])
        words = (["leftmost", "rightmost"] if horiz else ["upper", "lower"]) if len(g) == 2 else None
        for k, d in enumerate(g):
            if words:
                d["name"] = words[k] + " " + name
            else:
                d["name"] = "%s %s" % (["1st", "2nd", "3rd"][k] if k < 3 else "%dth" % (k + 1), name) + \
                            (" from the left" if horiz else " from the top")
    by_n = {d["n"]: d for d in info}
    for a, d in zip(areas, info):
        for extra in a.get("also") or []:
            by_n.setdefault(int(extra), d)
    count = max(by_n) if by_n else 0
    shown = len(info)
    num = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|\d+(?:st|nd|rd|th))"
    ordw = r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|\d+(?:st|nd|rd|th))"

    def place(n):
        d = by_n.get(n)
        if not d:
            return None
        if marked:
            if shown < 2:
                return "the outlined area"
            return ("outlined area %d (%s)" % (d["n"], d["name"])) if numbered else ("the %s outlined area" % d["name"])
        return "the %s painted area" % d["name"]

    def sub_a(m):
        r = place(_area_num(m.group(1), count))
        if not r:
            return m.group(0)
        before = m.string[:m.start()].rstrip(" ")
        sentence_start = not before or before[-1] in ".!?:\n"
        return r[0].upper() + r[1:] if sentence_start else r

    out = re.sub(r"(?i)\b(?:the\s+)?" + ordw + r"\s+" + _AREA_NOUN + r"\b", sub_a, prompt)
    out = re.sub(r"(?i)\b(?:the\s+)?" + _AREA_NOUN + r"\s*(?:number|no\.?|#)?\s*" + num + r"\b", sub_a, out)
    if out != prompt:
        log.info("area refs: %r -> %r", prompt[:160], out[:200])
    return out


_ADD_RE = re.compile(r"(?i)\b(add|adding|place|put|insert|install|build|create|draw|include|spawn|plant|replace\b.{0,60}\bwith|"
                     r"turn\b.{0,40}\binto\s+an?|swap\b.{0,40}\bfor)\b")
_REMOVE_RE = re.compile(r"(?i)\b(remove|erase|delete|get rid of|take out|take away|clear|clean up|without)\b")
_CHANGE_RE = re.compile(r"(?i)\b(make|change|reshape|resize|enlarge|shrink|widen|narrow|wider|bigger|larger|smaller|thicker|"
                        r"thinner|slimmer|fuller|longer|shorter|taller|lift|tone|tighten|curvier|colou?r|recolou?r|darker|"
                        r"lighter|fix|adjust|straighten|smooth|sharpen|more|less|increase|decrease)\b")


def edit_intent(text):
    """What the person wants done in the painted area: "add" something new, "change" what's there, "remove" it,
    or "mixed". Only steers the instruction the edit model gets."""
    t = str(text or "")
    found = [k for k, rx in (("add", _ADD_RE), ("remove", _REMOVE_RE), ("change", _CHANGE_RE)) if rx.search(t)]
    if not found:
        # just a thing named ("a red sports car") means put it there
        return "add" if re.match(r"(?i)\s*(a|an|some|two|three|several|\d+)\b", t) else "change"
    return found[0] if len(found) == 1 else "mixed"


_INTENT_RULES = {
    "add": "Add only what is asked, nothing else. Put it within the outline at a realistic size for the scene, sitting "
           "naturally on or in whatever is beneath it, in the same perspective, lighting, colour, sharpness and grain "
           "as the rest of the photo, with natural contact shadows.",
    "change": "Change only what is asked. Everything else about the subject stays the same: identity, pose, colours, "
              "materials, patterns and texture, continuing seamlessly into the parts outside the outline so it reads "
              "as one. The change follows the subject's natural shape, with the same lighting, sharpness and grain as "
              "the rest of the photo.",
    "remove": "Take out what is asked and fill its place with what would naturally be behind it, continuing the "
              "surrounding background and light.",
}


def inpaint_instruction(text, areas, numbered=True, intent=None):
    nums = [int(a.get("n") or i + 1) for i, a in enumerate(areas)] if isinstance(areas, list) else list(range(1, int(areas or 0) + 1))
    if len(nums) < 2:
        tag = ""
    elif numbered:
        tag = "s (numbered %s)" % ", ".join(str(x) for x in nums)
    else:
        tag = "s"
    intent = intent or edit_intent(text)
    rules = " ".join(_INTENT_RULES[k] for k in (("add", "change", "remove") if intent == "mixed" else (intent,)))
    return ("Edit only inside the area%s outlined in magenta in <Picture 1>: %s. %s Everything outside the outline "
            "stays exactly as it is. The final picture shows no magenta outline%s."
            % (tag, text.strip().rstrip(" ."), rules, " and number tags" if tag.startswith("s (") else ""))


_SAMPLERS = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")


def inpaint_noise_mask(g, inpaint, pre_id):
    """Real inpainting inside any workflow: the sampler starts from your picture and may only change the painted
    area (ComfyUI's VAE Encode + Set Latent Noise Mask). The marked picture stays the model's reference, so it
    still knows what goes where. Returns True when the workflow was rewired."""
    from PIL import Image, ImageFilter, ImageOps
    vae = next((n["inputs"].get("vae") for n in g.values() if n.get("class_type") == "VAEDecode"
                and isinstance(n.get("inputs", {}).get("vae"), list)), None)
    samplers = [k for k, n in g.items() if n.get("class_type") in _SAMPLERS
                and isinstance((n.get("inputs") or {}).get("latent_image"), list)]
    # first pass only: a second sampler that refines the first one's result is left alone
    first = [k for k in samplers if str(g[k]["inputs"]["latent_image"][0]) not in samplers]
    if not vae or not first:
        return False
    src_node = g.get(str(g[first[0]]["inputs"]["latent_image"][0])) or {}
    si = src_node.get("inputs") or {}
    x0, y0, x1, y1 = inpaint["box"]
    if isinstance(si.get("width"), int) and isinstance(si.get("height"), int):
        tw, th = si["width"], si["height"]
    else:
        # Krea 2 Edit works on a ~1 MP copy (its trained size); other models get 1.5 MP of detail
        area = 1.0e6 if any(str(n.get("class_type", "")).startswith("Krea2EditModelPatch") for n in g.values()) else 1.5e6
        k = (area / float((x1 - x0) * (y1 - y0))) ** 0.5
        tw, th = int((x1 - x0) * k), int((y1 - y0) * k)
    tw, th = max(64, tw // 16 * 16), max(64, th // 16 * 16)
    with Image.open(os.path.join(INPUT, inpaint["source"].replace("/", os.sep))) as im:
        plain = ImageOps.exif_transpose(im).convert("RGB").crop((x0, y0, x1, y1)).resize((tw, th), Image.LANCZOS)
    with Image.open(os.path.join(INPUT, inpaint["mask"].replace("/", os.sep))) as im:
        m = im.convert("L").crop((x0, y0, x1, y1)).resize((tw, th), Image.BILINEAR)
    # room around the area so new things can be completed, soft so it blends
    m = m.filter(ImageFilter.MaxFilter(max(3, (min(tw, th) // 40) | 1))).filter(ImageFilter.GaussianBlur(max(2, min(tw, th) / 120)))
    folder = os.path.join(INPUT, INPUT_SUB, "_inpaint")
    plain.save(os.path.join(folder, "%s_plain.png" % pre_id))
    m.convert("RGB").save(os.path.join(folder, "%s_nmask.png" % pre_id))
    g["ip_plain"] = {"class_type": "LoadImage", "inputs": {"image": "%s/_inpaint/%s_plain.png" % (INPUT_SUB, pre_id)},
                     "_meta": {"title": "Inpaint: your picture"}}
    g["ip_mask"] = {"class_type": "LoadImageMask", "inputs": {"image": "%s/_inpaint/%s_nmask.png" % (INPUT_SUB, pre_id),
                    "channel": "red"}, "_meta": {"title": "Inpaint: painted area"}}
    g["ip_enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["ip_plain", 0], "vae": vae}, "_meta": {"title": "Inpaint: encode"}}
    g["ip_noise"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["ip_enc", 0], "mask": ["ip_mask", 0]},
                     "_meta": {"title": "Inpaint: only change the painted area"}}
    for k in first:
        g[k]["inputs"]["latent_image"] = ["ip_noise", 0]
        if "denoise" in g[k]["inputs"]:
            g[k]["inputs"]["denoise"] = 1.0
    # Krea 2 Edit copies its reference picture pixel by pixel: give that channel the CLEAN cut-out (the magenta
    # outline would be copied into the result). The grounded text encoder keeps the marked picture, so it still
    # reads where the outlined area is.
    for n in g.values():
        if str(n.get("class_type", "")).startswith("Krea2EditModelPatch"):
            ins = n["inputs"]
            if "source_image" in ins:
                ins["source_image"] = ["ip_plain", 0]
            ins["source_latent"] = ["ip_enc", 0]
            if "target_latent" in ins:
                ins["target_latent"] = ["ip_noise", 0]
    log.info("inpaint: rewired %s to change only the painted area (%dx%d)", ",".join(first), tw, th)
    return True


def inpaint_frame(inpaint):
    """The part of the picture the edit model sees (the cut-out), in 0..1."""
    try:
        x0, y0, x1, y1 = inpaint["box"]
        w, h = inpaint["size"]
        return (x0 / w, y0 / h, x1 / w, y1 / h)
    except Exception:
        return (0.0, 0.0, 1.0, 1.0)


def merge_areas(mask, areas):
    """Brush strokes that overlap or touch make one shape: give it one number. Returns areas with "also": the other
    brush numbers that now point at it (so "brush 2" still finds it)."""
    areas = [dict(a) for a in (areas or []) if all(k in a for k in ("x0", "y0", "x1", "y1"))]
    if len(areas) < 2:
        return areas
    try:
        import numpy as np
        from scipy import ndimage
        from PIL import Image
        k = 512.0 / max(mask.size)
        w, h = max(8, int(mask.width * k)), max(8, int(mask.height * k))
        lab, n = ndimage.label(np.asarray(mask.resize((w, h), Image.NEAREST)) > 100)
        if n < 1:
            return areas
        def comp(a):
            x0, y0 = int(float(a["x0"]) * w), int(float(a["y0"]) * h)
            x1, y1 = max(x0 + 1, int(float(a["x1"]) * w)), max(y0 + 1, int(float(a["y1"]) * h))
            v = lab[y0:y1, x0:x1].ravel()
            v = v[v > 0]
            return int(np.bincount(v).argmax()) if v.size else -1 - id(a) % 1000
        out, seen = [], {}
        for i, a in enumerate(areas):
            a.setdefault("n", i + 1)
            c = comp(a)
            if c in seen:
                m = seen[c]
                m["also"] = m.get("also", []) + [int(a["n"])]
                for kk, f in (("x0", min), ("y0", min), ("x1", max), ("y1", max)):
                    m[kk] = f(float(m[kk]), float(a[kk]))
            else:
                seen[c] = a
                out.append(a)
        # each shape keeps the number of its first brush (what the chips show); the others point at it
        return out
    except Exception:
        return areas


def inpaint_mark(crop, mask_crop, areas, box, size, numbers=True):
    """Outline the painted areas in magenta and, when there are several, give each a small numbered tag on its
    outline, so an instruction-edit model knows exactly where to change. The inside is NOT covered: the model has
    to see the person / thing that is there to reshape it (a tint gets kept and drawn under). Returns the picture."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    marked = crop.copy()
    w = max(3, (min(crop.size) // 200) | 1)
    ring = ImageChops.subtract(mask_crop.filter(ImageFilter.MaxFilter(w)), mask_crop.filter(ImageFilter.MinFilter(w)))
    marked.paste((255, 0, 255), mask=ring)
    areas = [x for x in (areas or []) if all(k in x for k in ("x0", "y0", "x1", "y1"))]
    if numbers and len(areas) > 1:
        d = ImageDraw.Draw(marked)
        cx0, cy0 = box[0], box[1]
        W, H = size
        for i, ar in enumerate(areas):
            ax0, ay0 = float(ar["x0"]) * W - cx0, float(ar["y0"]) * H - cy0
            ax1, ay1 = float(ar["x1"]) * W - cx0, float(ar["y1"]) * H - cy0
            fs = int(max(18, min(crop.size) * 0.045))
            try:
                font = ImageFont.truetype("arialbd.ttf", fs)
            except Exception:
                font = ImageFont.load_default()
            txt = str(int(ar.get("n") or i + 1))
            # a small magenta tag on the area's own edge: the painted point nearest its top-left (cleaned off later)
            tx, ty = max(0, ax0), max(0, ay0)
            try:
                bx0, by0 = int(max(0, ax0)), int(max(0, ay0))
                bx1, by1 = int(min(crop.width, ax1)), int(min(crop.height, ay1))
                sub = mask_crop.crop((bx0, by0, bx1, by1)).resize((max(1, (bx1 - bx0) // 8), max(1, (by1 - by0) // 8)))
                pts = [(x, y) for y in range(sub.height) for x in range(sub.width) if sub.getpixel((x, y)) > 100]
                if pts:
                    px, py = min(pts, key=lambda q: q[0] + q[1])
                    tx, ty = bx0 + px * 8, by0 + py * 8
            except Exception:
                pass
            r = fs * 0.75
            d.rounded_rectangle([tx, ty, tx + 2 * r, ty + 2 * r], radius=r * 0.4, fill=(255, 0, 255))
            d.text((tx + r, ty + r), txt, font=font, fill=(255, 255, 255), anchor="mm")
    return marked


def inpaint_prepare(job_id, image_rel, mask_data, pad=0.35, areas=None, numbers=True, explicit=False):
    """Cut out the painted area of Picture 1 (with margin) as a new input; return (crop_rel, info)."""
    import base64
    from PIL import Image, ImageOps
    raw = base64.b64decode(str(mask_data).split(",", 1)[-1])
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("That mask is too large.")
    src_full = os.path.abspath(os.path.join(INPUT, image_rel.replace("/", os.sep)))
    with Image.open(src_full) as im:
        src = ImageOps.exif_transpose(im).convert("RGB")
    m = Image.open(io.BytesIO(raw)).convert("L")
    if m.size != src.size:
        m = m.resize(src.size, Image.BILINEAR)
    m = m.point(lambda v: 255 if v > 40 else 0)
    box = m.getbbox()
    if not box:
        raise ValueError("Paint over the area you want to change first.")
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    padx = max(64, int(bw * pad))
    pady = max(64, int(bh * pad))
    cx0, cy0 = max(0, x0 - padx), max(0, y0 - pady)
    cx1, cy1 = min(src.width, x1 + padx), min(src.height, y1 + pady)
    # keep the cut-out between 2:1 and 1:2: edit models re-frame (shift / zoom) very wide or tall strips
    for _ in range(2):
        if (cx1 - cx0) < (cy1 - cy0) / 2:
            extra = int(((cy1 - cy0) / 2 - (cx1 - cx0)) / 2) + 1
            cx0, cx1 = max(0, cx0 - extra), min(src.width, cx1 + extra)
        if (cy1 - cy0) < (cx1 - cx0) / 2:
            extra = int(((cx1 - cx0) / 2 - (cy1 - cy0)) / 2) + 1
            cy0, cy1 = max(0, cy0 - extra), min(src.height, cy1 + extra)
            if cy1 - cy0 < (cx1 - cx0) / 2 and cy0 == 0:
                cy1 = min(src.height, cy0 + int((cx1 - cx0) / 2))
            if cy1 - cy0 < (cx1 - cx0) / 2 and cy1 == src.height:
                cy0 = max(0, cy1 - int((cx1 - cx0) / 2))
    folder = os.path.join(INPUT, INPUT_SUB, "_inpaint")
    os.makedirs(folder, exist_ok=True)
    crop_name = "%s_crop.png" % job_id
    mask_name = "%s_mask.png" % job_id
    crop = src.crop((cx0, cy0, cx1, cy1))
    if not explicit:                       # areas you made yourself (Separate) are never joined
        areas = merge_areas(m, areas)
    try:
        crop = inpaint_mark(crop, m.crop((cx0, cy0, cx1, cy1)), areas, (cx0, cy0), src.size, numbers)
        marked = True
    except Exception as e:
        log.warning("inpaint marking skipped: %s", e)
        marked = False
    crop.save(os.path.join(folder, crop_name))
    m.save(os.path.join(folder, mask_name))
    crop_mp = (cx1 - cx0) * (cy1 - cy0) / 1e6
    info = {"box": [cx0, cy0, cx1, cy1], "mask": INPUT_SUB + "/_inpaint/" + mask_name, "source": image_rel,
            "size": [src.width, src.height], "crop_mp": round(crop_mp, 3), "area": [x0, y0, x1, y1], "marked": marked,
            "numbered": bool(marked and numbers and len(areas or []) > 1), "areas": areas}
    return INPUT_SUB + "/_inpaint/" + crop_name, info


_live_bg = {}


def inpaint_live(job, preview):
    """While an inpaint runs, show the cut-out's live preview inside the whole picture (so the stage keeps the
    picture's own shape instead of the cut-out's, e.g. a wide strip)."""
    try:
        from PIL import Image, ImageOps
        info = job["inpaint"]
        key = job["id"]
        if key not in _live_bg:
            src = os.path.abspath(os.path.join(INPUT, info["source"].replace("/", os.sep)))
            with Image.open(src) as im:
                bg = ImageOps.exif_transpose(im).convert("RGB")
            bg.thumbnail((1280, 1280))
            _live_bg.clear()
            _live_bg[key] = bg
        bg = _live_bg[key].copy()
        k = bg.width / float(info["size"][0])
        x0, y0, x1, y1 = [int(round(v * k)) for v in info["box"]]
        with Image.open(io.BytesIO(preview)) as pv:
            patch = pv.convert("RGB").resize((max(1, x1 - x0), max(1, y1 - y0)))
        bg.paste(patch, (x0, y0))
        out = io.BytesIO()
        bg.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:
        return None


def inpaint_align(orig, edited, mask):
    """Some edit models (Qwen Edit especially) zoom or shift the whole picture a little. Find that zoom and shift
    from the parts outside your area and undo it, so the edit lands exactly where you painted. Returns the
    re-aligned edited picture (or the same one when it was already aligned)."""
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        return edited
    W, H = orig.size
    k = 384.0 / max(W, H)
    w, h = max(16, int(W * k)), max(16, int(H * k))
    O = np.asarray(orig.convert("L").resize((w, h), Image.BILINEAR), dtype=np.float64)
    E0 = edited.convert("L").resize((w, h), Image.BILINEAR)
    keep = np.asarray(mask.resize((w, h), Image.BILINEAR).filter(ImageFilter.MaxFilter(9)), dtype=np.float64) < 20
    win = np.outer(np.hanning(h), np.hanning(w)) * keep
    def prep(a):
        a = a - a[keep].mean() if keep.any() else a - a.mean()
        return a * win
    Of = np.fft.fft2(prep(O))
    cx, cy = w / 2.0, h / 2.0
    def score(sc):
        Es = E0.transform((w, h), Image.AFFINE, (sc, 0, cx * (1 - sc), 0, sc, cy * (1 - sc)), Image.BILINEAR)
        Ea = np.asarray(Es, dtype=np.float64)
        R = Of * np.conj(np.fft.fft2(prep(Ea)))
        r = np.real(np.fft.ifft2(R / (np.abs(R) + 1e-9)))
        iy, ix = np.unravel_index(np.argmax(r), r.shape)
        dy = iy - h if iy > h // 2 else iy
        dx = ix - w if ix > w // 2 else ix
        return r[iy, ix], -dx, -dy
    best = (score(1.0), 1.0)
    for sc in np.arange(0.80, 1.2001, 0.01):
        if abs(sc - 1.0) < 1e-6:
            continue
        r = score(float(sc))
        if r[0] > best[0][0]:
            best = (r, float(sc))
    (peak, dx, dy), sc = best
    ident = score(1.0)
    if (abs(sc - 1.0) < 0.005 and dx == 0 and dy == 0) or peak < ident[0] * 1.15:
        return edited
    # a model that re-framed a little: believable. Anything bigger means it redrew the picture: don't "fix" that
    if not (0.9 <= sc <= 1.1) or abs(dx) > w * 0.06 or abs(dy) > h * 0.06:
        log.info("inpaint: alignment not applied (zoom %.2f, shift %d,%d looks like a redraw)", sc, dx, dy)
        return edited
    # edited pixel for original position x:  sc * (x + d - c) + c   (full-size units)
    f = W / float(w)
    DX, DY, CX, CY = dx * f, dy * f, W / 2.0, H / 2.0
    # finer pass: at higher resolution, measure what shift is left (to a fraction of a pixel) and add it
    try:
        k2 = min(1.0, 1280.0 / max(W, H))
        w2, h2 = max(16, int(W * k2)), max(16, int(H * k2))
        f2 = W / float(w2)
        O2 = np.asarray(orig.convert("L").resize((w2, h2), Image.BILINEAR), dtype=np.float64)
        A2 = edited.convert("L").transform((W, H), Image.AFFINE, (sc, 0, sc * (DX - CX) + CX, 0, sc, sc * (DY - CY) + CY),
                                           Image.BILINEAR).resize((w2, h2), Image.BILINEAR)
        A2 = np.asarray(A2, dtype=np.float64)
        keep2 = np.asarray(mask.resize((w2, h2), Image.BILINEAR).filter(ImageFilter.MaxFilter(15)), dtype=np.float64) < 20
        win2 = np.outer(np.hanning(h2), np.hanning(w2)) * keep2
        pa = lambda a: (a - (a[keep2].mean() if keep2.any() else a.mean())) * win2
        R = np.fft.fft2(pa(O2)) * np.conj(np.fft.fft2(pa(A2)))
        r = np.real(np.fft.ifft2(R / (np.abs(R) + 1e-9)))
        iy, ix = np.unravel_index(np.argmax(r), r.shape)
        def sub(c, n, get):
            l, m0, rr = get((c - 1) % n), get(c), get((c + 1) % n)
            den = l - 2 * m0 + rr
            return c + (0.5 * (l - rr) / den if abs(den) > 1e-12 else 0.0)
        fy = sub(iy, h2, lambda j: r[j, ix]); fx = sub(ix, w2, lambda j: r[iy, j])
        fy = fy - h2 if fy > h2 / 2 else fy
        fx = fx - w2 if fx > w2 / 2 else fx
        if abs(fx) < 8 and abs(fy) < 8:
            DX -= fx * f2 / sc
            DY -= fy * f2 / sc
    except Exception:
        pass
    log.info("inpaint: re-aligned the model's result (zoom %.2f, shift %d,%d px)", sc, int(DX), int(DY))
    print("ALIGN", sc, DX, DY) if os.environ.get("ALIGN_DEBUG") else None
    data_ = (sc, 0, sc * (DX - CX) + CX, 0, sc, sc * (DY - CY) + CY)
    moved_img = edited.transform((W, H), Image.AFFINE, data_, Image.BICUBIC)
    inside = Image.new("L", edited.size, 255).transform((W, H), Image.AFFINE, data_, Image.NEAREST)
    out = orig.copy()                       # edges the shifted result doesn't cover keep the original
    out.paste(moved_img, (0, 0), inside)
    return out


def inpaint_tone_match(orig, edited, mask):
    """Edit models shift colour and contrast a little. Measure that in a band just outside your area (where the
    edit and the original should look the same) and correct the whole edit, so fabric, skin and ground match."""
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        return edited
    W, H = orig.size
    k = min(1.0, 768.0 / max(W, H))
    w, h = max(8, int(W * k)), max(8, int(H * k))
    mk = mask.resize((w, h), Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
    near = mk.filter(ImageFilter.MaxFilter(5))
    far = near
    for _ in range(max(1, int(min(w, h) * 0.06) // 4)):
        far = far.filter(ImageFilter.MaxFilter(9))
    ring = (np.asarray(far) > 0) & ~(np.asarray(near) > 0)
    if ring.sum() < 200:
        return edited
    o = np.asarray(orig.resize((w, h), Image.BILINEAR), dtype=np.float64)[ring]
    e = np.asarray(edited.resize((w, h), Image.BILINEAR), dtype=np.float64)[ring]
    mo, me = o.mean(0), e.mean(0)
    so, se = o.std(0) + 1e-6, e.std(0) + 1e-6
    gain = np.clip(so / se, 0.85, 1.18)
    if np.all(np.abs(mo - me) < 1.5) and np.all(np.abs(gain - 1) < 0.02):
        return edited
    if np.any(np.abs(mo - me) > 14):
        return edited                       # that's different content around the area, not a colour drift
    arr = np.asarray(edited, dtype=np.float64)
    arr = (arr - me) * gain + me + np.clip(mo - me, -25, 25)
    log.info("inpaint: matched tone (shift %s)", np.round(mo - me, 1).tolist())
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))


def inpaint_blend_mask(orig, edited, mask, intent=None):
    """Where to take the edit from. Your painted area, plus whatever the model changed right next to it (the rest
    of a pool's edging, the end of a path), so new things are never cut off at the brush edge. Far-away changes are
    ignored, and the edge is kept tight so it doesn't look smeared."""
    from PIL import Image, ImageChops, ImageFilter
    W, H = orig.size
    k = min(1.0, 1024.0 / max(W, H))
    w, h = max(8, int(W * k)), max(8, int(H * k))
    o, e = orig.resize((w, h), Image.BILINEAR), edited.resize((w, h), Image.BILINEAR)
    mk = mask.resize((w, h), Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
    side = min(w, h)
    # what the model really changed (small colour drift everywhere is ignored)
    # (blurred first, so re-drawn fine detail like leaves and windows doesn't count, only real new things)
    diff = ImageChops.difference(o, e).convert("L").filter(ImageFilter.GaussianBlur(max(2, max(w, h) * 0.006)))
    changed = diff.point(lambda v: 255 if v > 34 else 0)
    # and never further than about a seventh of the cut-out from your area
    cap = mk
    for _ in range(max(1, int(max(w, h) * (0.3 if intent in ("change", "mixed") else 0.15)) // 4)):
        cap = cap.filter(ImageFilter.MaxFilter(9))
    changed = ImageChops.multiply(changed, cap)
    # only changes connected to your area count: grow out from it, step by step, through changed pixels only
    # (up to about a fifth of the cut-out), so a pool keeps all its edging but unrelated changes elsewhere don't
    far = ImageChops.invert(cap)
    nz = lambda im: im.width * im.height - im.histogram()[0]
    far_px = nz(far) or 1
    moved = nz(ImageChops.multiply(diff.point(lambda v: 255 if v > 34 else 0), far))
    if moved / far_px > 0.45:   # (alignment already fixed small shifts; reshaping a body changes a lot nearby)
        # the model shifted or zoomed the whole picture: only your area can be used
        log.info("inpaint: model moved the picture (%.0f%% changed far away), strict blend", 100.0 * moved / far_px)
        strict = mk.filter(ImageFilter.MaxFilter(5)).resize((W, H), Image.BILINEAR)
        return strict.filter(ImageFilter.GaussianBlur(max(2, min(W, H) * 0.012)))
    keep = mk
    for _ in range(max(4, int(max(w, h) * 0.2) // 2)):
        grown = ImageChops.lighter(mk, ImageChops.multiply(keep.filter(ImageFilter.MaxFilter(5)), changed))
        if grown.tobytes() == keep.tobytes():
            break
        keep = grown
    # close pin-holes, drop specks
    keep = keep.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    # drop thin slivers that crept out along an edge (they show as stray bits), keep solid shapes
    sl = max(3, (min(w, h) // 90) | 1)
    keep = keep.filter(ImageFilter.MinFilter(sl)).filter(ImageFilter.MaxFilter(sl))
    keep = ImageChops.lighter(keep, mk)
    # a slight grow and a short soft edge, at full size
    keep = keep.filter(ImageFilter.MaxFilter(3)).resize((W, H), Image.BILINEAR)
    soft = 0.01 if intent in ("change", "mixed") else 0.006       # a subject blends over a slightly wider edge
    alpha = keep.filter(ImageFilter.GaussianBlur(max(1.5, min(W, H) * soft)))
    # where the edit and the original are very different (red dress vs white floor), a soft edge mixes them into a
    # see-through ghost: there, the edge is made crisp; where they're alike (fabric to fabric) it stays soft
    strong = diff.point(lambda v: 255 if v > 55 else 0).resize((W, H), Image.BILINEAR)
    hard = alpha.point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.GaussianBlur(max(1.0, min(W, H) * 0.0015)))
    return Image.composite(hard, alpha, strong)


def leftover_magenta(img):
    """Where the model left the magenta marking (it drew something smaller than the area): those spots keep the
    original picture. Returns a soft mask (white = leftover magenta), a little wider so no pink fringe remains."""
    from PIL import Image, ImageChops, ImageFilter
    W, H = img.size
    k = min(1.0, 1536.0 / max(W, H))
    sm = img.resize((max(8, int(W * k)), max(8, int(H * k))), Image.BILINEAR)
    r, g, b = sm.split()
    rg = ImageChops.subtract(r, g)
    bg = ImageChops.subtract(b, g)
    # magenta: red and blue both well above green, and blue not far below red
    hit = ImageChops.multiply(rg.point(lambda v: 255 if v > 60 else 0), bg.point(lambda v: 255 if v > 45 else 0))
    hit = hit.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))    # ignore single specks
    if not hit.getbbox():
        return Image.new("L", (W, H), 0)
    hit = hit.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.MinFilter(9))   # closes over digits inside tags
    return hit.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(max(1.5, min(W, H) * 0.003)))


def inpaint_finish(job):
    """Blend the edited close-up back into the original with a soft edge; replace the job's result."""
    from PIL import Image, ImageChops, ImageFilter, ImageOps
    info = job["inpaint"]
    first = job["images"][0]
    edited_path = output_path(first.get("subfolder") or "", first["filename"])
    cx0, cy0, cx1, cy1 = info["box"]
    with Image.open(edited_path) as im:
        edited = im.convert("RGB").resize((cx1 - cx0, cy1 - cy0), Image.LANCZOS)
    with Image.open(os.path.join(INPUT, info["source"].replace("/", os.sep))) as im:
        src = ImageOps.exif_transpose(im).convert("RGB")
    with Image.open(os.path.join(INPUT, info["mask"].replace("/", os.sep))) as im:
        m = im.convert("L").crop((cx0, cy0, cx1, cy1))
    edited = inpaint_align(src.crop((cx0, cy0, cx1, cy1)), edited, m)
    edited = inpaint_tone_match(src.crop((cx0, cy0, cx1, cy1)), edited, m)
    m = inpaint_blend_mask(src.crop((cx0, cy0, cx1, cy1)), edited, m, info.get("intent"))
    if info.get("marked"):
        m = ImageChops.subtract(m, leftover_magenta(edited))
    out = src.copy()
    out.paste(edited, (cx0, cy0), m)
    name = "inpaint_%s.png" % job["id"]
    # next to the raw result, in its engine / model / type folder
    sub = (first.get("subfolder") or APP_OUT).replace("\\", "/")
    if not (sub == APP_OUT or sub.startswith(APP_OUT + "/")):
        sub = APP_OUT
    os.makedirs(os.path.join(OUTPUT, sub.replace("/", os.sep)), exist_ok=True)
    out.save(os.path.join(OUTPUT, sub.replace("/", os.sep), name))
    job["images"] = [{"filename": name, "subfolder": sub, "type": "output", "kind": "image"}] + job["images"][1:]
    job["width"], job["height"] = src.width, src.height


def _clean_request(data):
    """Wrong-typed fields (a list where text belongs, text where a dict belongs ...) become a clear 400, never a crash."""
    for k in ("pe_mode", "prompt", "negative", "mode", "workflow_id", "sampler_name", "scheduler", "edit_intent",
              "unet_name", "clip_name", "vae_name", "pe_clip_name"):
        v = data.get(k)
        if v is not None and not isinstance(v, str):
            if isinstance(v, (dict, list)):
                raise ValueError("That request wasn't understood (%s)." % k)
            data[k] = str(v)
    for k in ("models", "camera", "overrides"):
        if data.get(k) is not None and not isinstance(data[k], dict):
            raise ValueError("That request wasn't understood (%s)." % k)
    for k in ("images", "loras", "image_roles", "inpaint_areas", "videos", "audios"):
        if data.get(k) is not None and not isinstance(data[k], list):
            raise ValueError("That request wasn't understood (%s)." % k)
    return data


def submit_job(data):
    _clean_request(data)
    if str(data.get("mode") or "").strip().lower() in ("workflow", "wf"):
        return submit_workflow_job(data)
    mode = (data.get("mode") or "t2i").strip().lower()
    if mode in ("text-to-image", "txt2img", "t2i"):
        mode = "t2i"
    elif mode in ("edit", "image-edit", "image_edit"):
        mode = "edit"
    else:
        raise ValueError("Choose Text to Image or Image Edit.")
    prompt = (data.get("prompt") or "").strip()
    if prompt and data.get("understanding") is not False:
        prompt = UND.apply_words(prompt, DATA, "builtin")
    if not prompt:
        raise ValueError("Enter a prompt.")
    resolution = data.get("resolution") or ("2048x2048")
    # Enhance works for new pictures AND edits. Edits get the H3 edit caption (Subject definitions + "is fully
    # preserved"), which is what keeps the rest of Picture 1 unchanged. Brush numbers are turned into place words
    # before the enhancer sees the prompt. Inpaint keeps your own words: its crop + marked-area wording is exact.
    pe = bool(data.get("pe")) and not data.get("inpaint_mask")
    pe_mode = (data.get("pe_mode") or "fast").strip().lower()
    if pe_mode not in ("fast", "quality"):
        pe_mode = "fast"
    seed = data.get("seed")
    if data.get("random_seed") or seed in (None, "", "random"):
        seed = None
    steps = data.get("steps")
    models = {k: data[k] for k in ("unet_name", "clip_name", "vae_name", "pe_clip_name", "sampler_name", "scheduler", "cfg", "denoise", "weight_dtype", "clip_type", "ref_megapixels") if k in data and data[k] not in (None, "")}
    alias_info = {"text": prompt, "refs": [], "warnings": []}
    input_size = None
    pe_clip_used, pe_small = PE.pe_clip_for(pe_mode, _clip_names(), models.get("pe_clip_name") or graphs.DEFAULTS["pe_clip_name"])
    pe_models = dict(models)
    pe_models["pe_clip_name"] = pe_clip_used
    ref_mode = bool(data.get("ref_mode")) and mode == "edit"
    edit_kind = "ref" if ref_mode else mode
    # Auto Prompt Assist is on by default for Edit / Reference mode; off for t2i unless the client sends true.
    if "auto_assist" in data:
        auto_assist = bool(data.get("auto_assist"))
    else:
        auto_assist = mode == "edit"
    if data.get("inpaint_mask"):
        auto_assist = False      # the inpaint instruction says what to do; extra rules contradicted it
    roles = data.get("image_roles") if data.get("image_roles") is not None else data.get("roles")
    if roles is None:
        roles = []
    camera = CAMERA.normalize_camera(data.get("camera") or {})
    pe_system_extra = ""
    pe_task_extra = ""
    assist_info = {"prompt": prompt, "added": False, "roles": {}, "notes": []}
    inpaint = None
    orig_images = None
    prompt_user = prompt
    prompt_sent = prompt
    if mode == "t2i":
        images = []
        if auto_assist or camera:
            assist_info = ASSIST.build_assist_prompt(
                prompt_user, image_roles=roles, n_images=0, ref_mode=False,
                enabled=bool(auto_assist), camera=camera, mode="t2i",
            )
            prompt_sent = assist_info.get("prompt") or prompt_user
        if guard_on(data, "keep_clothing", 0) and (assist_info or {}).get("intent") not in ("body_swap", "subject_into_environment", "clothing_transfer"):
            prompt_sent, _cg = CG.apply(prompt_sent, data.get("prompt") or "", "edit" if mode == "edit" else "t2i")
        prompt_sent, _hg = CG.apply_headgear(prompt_sent, data.get("prompt") or "", "edit" if mode == "edit" else "t2i")
        if data.get("understanding") is not False:
            prompt_sent = UND.apply_extra(prompt_sent, DATA, "builtin")
        prompt_sent = lora_trigger_prompt(prompt_sent, data.get("loras"))
        graph, meta = build_main_graph(mode, prompt_sent, resolution, seed, steps, models, images, input_size, ref_mode=False)
    else:
        images = list(data.get("images") or [])
        images = [str(x).replace("\\", "/") for x in images if str(x).strip()]
        if not images:
            raise ValueError("Add at least one reference image first." if ref_mode else "Add a main image (Picture 1) first.")
        if len(images) > 16:
            raise ValueError("MiniMax H3 Image takes at most 16 images.")
        for name in images:
            rel = name.replace("/", os.sep)
            full = os.path.abspath(os.path.join(INPUT, rel))
            root = os.path.abspath(INPUT)
            if not (full.startswith(root + os.sep) and os.path.isfile(full)):
                raise ValueError("Image %s was not found. Upload it again." % name)
        loaded = set(range(1, len(images) + 1))
        alias_info = aliases.normalize(prompt, loaded)
        prompt_user = alias_info["text"]
        n_imgs = len(images)
        # Bake EXIF orientation into Studio copies so Comfy LoadImage and our
        # W/H agree with the upright pixels (phone photos are often stored landscape).
        for name in images:
            rel = name.replace("/", os.sep)
            full = os.path.abspath(os.path.join(INPUT, rel))
            bake_orientation_file(full)
        if data.get("inpaint_mask"):
            orig_images = list(images)
            pre_id = uuid.uuid4().hex[:12]
            crop_rel, inpaint = inpaint_prepare(pre_id, images[0], data["inpaint_mask"], areas=data.get("inpaint_areas"),
                                                  explicit=bool(data.get("inpaint_explicit")))
            inpaint["pre_id"] = pre_id
            images[0] = crop_rel
            resolution = "picture1:%g" % round(max(1.5, min(4.0, inpaint["crop_mp"])), 1)
            intent = edit_intent(prompt_user)
            inpaint["intent"] = intent
            prompt_user = area_refs(prompt_user, inpaint.get("areas") or data.get("inpaint_areas"), inpaint_frame(inpaint), inpaint.get("marked"),
                                    inpaint.get("numbered", True))
            if inpaint.get("marked"):
                prompt_user = inpaint_instruction(prompt_user, inpaint.get("areas") or [], inpaint.get("numbered", True), intent)
            else:
                prompt_user = (prompt_user.rstrip(" .") + ". Keep the framing, lighting, colours and edges of <Picture 1> "
                               "the same so the change blends in seamlessly.")
        input_size = picture_size(images[0])
        try:
            size_spec = graphs.resolve_size(resolution, mode="edit", input_size=input_size)
            assist_w = size_spec.get("output_width") or size_spec.get("width")
            assist_h = size_spec.get("output_height") or size_spec.get("height")
        except Exception:
            size_spec, assist_w, assist_h = None, None, None
        try:
            appearances = appearances_for(images, wait=1.0)
        except Exception:
            appearances = [None] * len(images)
        assist_info = ASSIST.build_assist_prompt(
            prompt_user, image_roles=roles, n_images=n_imgs, ref_mode=ref_mode,
            enabled=bool(auto_assist), camera=camera, mode=edit_kind,
            aspect=None, width=assist_w, height=assist_h, resolution=resolution,
            appearances=appearances,
        )
        prompt_sent = assist_info.get("prompt") or prompt_user
        if assist_info.get("auto_ref") or assist_info.get("intent") in getattr(ASSIST, "MULTIPANEL_INTENTS", {"character_sheet"}):
            ref_mode = True
            edit_kind = "ref"
            # Keep the user's chosen aspect / Match Picture 1 size. Layout adapts to W x H.
        if guard_on(data, "keep_clothing", len(images or [])) and (assist_info or {}).get("intent") not in ("body_swap", "subject_into_environment", "clothing_transfer"):
            prompt_sent, _cg = CG.apply(prompt_sent, data.get("prompt") or "", "edit" if mode == "edit" else "t2i")
        prompt_sent, _hg = CG.apply_headgear(prompt_sent, data.get("prompt") or "", "edit" if mode == "edit" else "t2i")
        if guard_on(data, "only_garment", len(images or [])) and (assist_info or {}).get("intent") not in ("body_swap", "subject_into_environment", "clothing_transfer", "head_swap", "face_swap"):
            prompt_sent, _og = CG.apply_only_garment(prompt_sent, data.get("prompt") or "", "edit")
        if guard_on(data, "only_target", len(images or [])) and not ref_mode:
            prompt_sent, _rg = CG.apply_removal(prompt_sent, data.get("prompt") or "", "edit")
        # an edit keeps the original picture's quality, texture and light (Settings > Keep image quality)
        if guard_on(data, "keep_quality", len(images or [])) and not ref_mode and not inpaint:
            prompt_sent, _qg = CG.apply_quality(prompt_sent, data.get("prompt") or "", "edit")
        if data.get("understanding") is not False:
            prompt_sent = UND.apply_extra(prompt_sent, DATA, "builtin")
        prompt_sent = lora_trigger_prompt(prompt_sent, data.get("loras"))
        graph, meta = build_main_graph(mode, prompt_sent, resolution, seed, steps, models, images, input_size, ref_mode=ref_mode)
        if inpaint and inpaint.get("box"):
            # without this the model may redraw the whole cut-out (new framing, people gone); with it, only your area
            try:
                inpaint["noise_mask"] = inpaint_noise_mask(graph, inpaint, inpaint.get("pre_id") or uuid.uuid4().hex[:12])
            except Exception as e:
                log.warning("inpaint: couldn't lock the rest of the picture: %s", e)
    user_loras = [l for l in (data.get("loras") or []) if isinstance(l, dict) and l.get("name")]
    if user_loras:
        WF.inject_loras(graph, user_loras)
    if isinstance(assist_info, dict):
        pe_system_extra = ASSIST.pe_system_addon(assist_info)
        pe_task_extra = ASSIST.pe_task_prefix(assist_info)
    else:
        pe_system_extra = ""
        pe_task_extra = ""
    model_notes = []
    if pe:
        clip_ok = pe_clip_available(pe_models.get("pe_clip_name"))
        if not clip_ok:
            pe = False
            model_notes.append("No Prompt Enhancer model is installed, so your own prompt was used.")
        elif clip_ok != pe_models.get("pe_clip_name"):
            pe_models["pe_clip_name"] = pe_clip_used = clip_ok
    if pe:
        pe_graph, _pe_meta = make_pe_graph(prompt_sent, mode, images, pe_mode, pe_models, pe_system_extra=pe_system_extra,
                                           pe_task_extra=pe_task_extra, pe_system_override=pe_override("builtin", mode),
                                           pe_tokens=pe_tokens_in(data.get("pe_tokens")))
        try:
            pe_problems = validate_graph(pe_graph, object_info(), input_root=INPUT)
        except Exception:
            pe_problems = []
        if pe_problems and any("FreeMemory" in p or "AUSBOSS" in p for p in pe_problems):
            pe_graph, _pe_meta = graphs.build_pe_only(
                prompt_sent, mode=mode, images=images, pe_mode=pe_mode, models=pe_models, free_memory=False,
                pe_system_extra=pe_system_extra, pe_task_extra=pe_task_extra,
            )
            try:
                pe_problems = validate_graph(pe_graph, object_info(), input_root=INPUT)
            except Exception:
                pe_problems = []
        if pe_problems:
            # the enhancer can't run here (its model or nodes aren't on this PC): your own prompt is used instead
            log.info("PE skipped: %s", " | ".join(pe_problems[:4]))
            pe = False
            model_notes.append("The Prompt Enhancer can't run on this PC yet (its model isn't installed), so your own "
                               "prompt was used. Pick a Prompt Enhancer model in Settings, or download one.")
    prompt = prompt_sent
    try:
        model_notes += WF.use_installed_models(graph, object_info())
    except Exception as e:
        log.info("use_installed_models skipped: %s", e)
    try:
        oi = object_info()
        problems = validate_graph(graph, oi, input_root=INPUT)
    except Exception as e:
        log.info("validate skipped (object_info): %s", e)
        problems = []
    if problems:
        raise ValueError("The workflow did not match this ComfyUI install:\n" + "\n".join(problems[:12]))
    job = {
        "id": uuid.uuid4().hex[:12],
        "mode": mode,
        "prompt": prompt,
        "status": "queued",
        "graph": graph,
        "t": time.time(),
        "images": [],
        "source_images": orig_images or images,
        "alias_warnings": alias_info.get("warnings") or [],
        "message": " ".join(model_notes) or "Waiting to start…",
        "note": " ".join(model_notes),
        "progress": 0,
        "progress_max": 0,
        "error": "",
        "prompt_id": None,
        "sampler_name": (models or {}).get("sampler_name") or graphs.DEFAULTS["sampler_name"],
        "scheduler": (models or {}).get("scheduler") or graphs.DEFAULTS["scheduler"],
        "cfg": (models or {}).get("cfg") if (models or {}).get("cfg") not in (None, "") else graphs.DEFAULTS["cfg"],
        **meta,
    }
    job["prompt_user"] = prompt_user
    job["prompt_sent"] = prompt_sent
    if inpaint:
        job["inpaint"] = inpaint
        job["edit_kind"] = "inpaint"
    job["pe"] = pe
    job["pe_mode"] = pe_mode if pe else None
    job["pe_tokens"] = pe_tokens_in(data.get("pe_tokens")) if pe else None
    job["enhanced_prompt"] = ""
    job["pe_fallback"] = False
    job["pe_cached"] = False
    job["pe_clip_used"] = pe_clip_used if pe else None
    job["pe_small_model"] = bool(pe and pe_small)
    job["resolution_in"] = resolution
    job["models"] = models
    job["input_size"] = input_size
    job["auto_assist"] = bool(auto_assist)
    job["roles"] = assist_info.get("roles") or roles
    job["pe_loras"] = [l for l in (data.get("loras") or []) if isinstance(l, dict)]
    job["camera"] = camera
    job["ref_mode"] = bool(ref_mode)
    job["edit_kind"] = "inpaint" if inpaint else edit_kind
    job["assist_preview"] = assist_info.get("prompt") or prompt_sent
    job["pe_system_extra"] = pe_system_extra
    job["pe_task_extra"] = pe_task_extra
    jobs[job["id"]] = job
    order.append(job["id"])
    with q_cv:
        job_q.append(job["id"])
        q_cv.notify()
    return job


def _queue_prompt_ids(items):
    """Collect Comfy prompt ids from /queue running or pending lists."""
    out = set()
    for it in items or []:
        if isinstance(it, (list, tuple)) and len(it) > 1 and not isinstance(it[1], dict):
            out.add(str(it[1]))
        elif isinstance(it, (list, tuple)) and len(it) > 1 and isinstance(it[1], dict):
            pid = it[1].get("prompt_id") or it[1].get("promptId")
            if pid:
                out.add(str(pid))
        elif isinstance(it, dict):
            pid = it.get("prompt_id") or it.get("promptId")
            if pid:
                out.add(str(pid))
    return out


def cancel_comfy_actions(pid, queue=None):
    """Return ('interrupt'|'delete'|None, running, pending) for our prompt only.

    Never interrupts a foreign running job. Pending items are deleted from /queue.
    """
    pid = str(pid or "")
    if not pid:
        return None, set(), set()
    q = queue if queue is not None else comfy_queue()
    running = _queue_prompt_ids((q or {}).get("queue_running"))
    pending = _queue_prompt_ids((q or {}).get("queue_pending"))
    if pid in running:
        return "interrupt", running, pending
    if pid in pending:
        return "delete", running, pending
    return None, running, pending


def cancel(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Job not found."
    if job["status"] in ("success", "error", "cancelled"):
        return "That job has already finished."
    job["status"] = "cancelled"
    job["message"] = "Cancelled."
    job["t_done"] = time.time()
    pid = job.get("prompt_id")
    if not pid:
        return "Cancelled."
    try:
        action, _running, _pending = cancel_comfy_actions(pid)
    except Exception as e:
        log.info("cancel queue inspect: %s", e)
        action = None
    if action == "delete":
        try:
            comfy("POST", "/queue", {"delete": [pid]})
        except Exception:
            pass
    elif action == "interrupt":
        # Only when OUR prompt is the one currently running.
        try:
            comfy("POST", "/interrupt", {})
        except Exception as e:
            log.info("interrupt: %s", e)
        try:
            comfy("POST", "/queue", {"delete": [pid]})
        except Exception:
            pass
    return "Cancelled."


def _collect_images(hist_entry):
    out = []
    outputs = (hist_entry or {}).get("outputs") or {}
    # Prefer our SaveImage node, then any image outputs.
    preferred = []
    others = []
    for nid, rec in outputs.items():
        media_list = []
        for key in ("gifs", "videos", "images"):
            media_list += [m for m in (rec.get(key) or []) if isinstance(m, dict) and m.get("filename")]
        for im in media_list:
            if (im.get("type") or "output") != "output":
                continue
            ext = os.path.splitext(im.get("filename") or "")[1].lower()
            item = {"filename": im.get("filename"), "subfolder": im.get("subfolder") or "", "type": "output",
                    "kind": "video" if ext in VIDEO_EXT else "image"}
            if nid == "save" or (item["subfolder"] or "").replace("\\", "/").startswith(APP_OUT):
                preferred.append(item)
            else:
                others.append(item)
    return preferred or others


def _wait_for_job(job):
    """Poll history / live events until our prompt finishes. Never interrupts a foreign job."""
    pid = job["prompt_id"]
    t0 = time.time()
    lost = {}
    while time.time() - t0 < 60 * 40:
        if job["status"] == "cancelled":
            return
        ev = done_events.get(pid)
        if ev:
            if ev.get("ok"):
                try:
                    hist = comfy("GET", "/history/" + pid, timeout=30)
                except Exception:
                    hist = {}
                entry = hist.get(pid) or (next(iter(hist.values())) if hist else {})
                job["images"] = _collect_images(entry)
                if job["images"] and job.get("inpaint") and not job["inpaint"].get("native"):
                    try:
                        inpaint_finish(job)
                    except Exception:
                        log.exception("inpaint blend")
                        job["message"] = "Blending failed; showing the edited close-up."
                if not job["images"]:
                    job["status"] = "error"
                    job["error"] = "The run finished but no image was saved."
                else:
                    job["status"] = "success"
                    job["message"] = "Done."
                    rec = LIB.library_record(job, OUTPUT, APP_OUT, INPUT)
                    first = (job.get("images") or [{}])[0]
                    rec["media"] = first.get("kind") or job.get("media") or "image"
                    if job.get("workflow_id"):
                        rec.update(workflow_id=job["workflow_id"], workflow_name=job.get("workflow_name"), task=job.get("task"),
                                   model_name=job.get("model_name"))
                    src0 = (job.get("source_images") or [None])[0]
                    parent = lineage_get(src0) if src0 else None
                    if parent:
                        rec["parent_id"] = parent
                    job["input_snapshots"] = rec.get("input_snapshots") or []
                    if not job.get("test_run"):      # a workflow's Test run stays out of the Library
                        gallery_add(rec)
                job["t_done"] = time.time()
                return
            job["status"] = "error"
            job["error"] = ev.get("error") or "ComfyUI stopped the run."
            low = (job["error"] or "").lower()
            if any(s in low for s in ("out of memory", "cuda oom", "cuda out of memory")):
                job["error"] = format_comfy_error({"exception_message": job["error"]})
            if "interrupted" in low:
                job["status"] = "cancelled"
                job["error"] = ""
                job["message"] = "Cancelled."
            job["t_done"] = time.time()
            return
        # live progress
        if live.get("prompt_id") == pid:
            job["progress"] = live.get("value") or 0
            job["progress_max"] = live.get("max") or 0
            job["node"] = live.get("node")
            if job["progress_max"]:
                job["message"] = "Generating… %d / %d" % (job["progress"], job["progress_max"])
        gone = _prompt_lost(pid, lost)
        if gone and not done_events.get(pid):
            job["status"] = "error"
            job["error"] = gone
            job["t_done"] = time.time()
            return
        # history fallback (if websocket missed the event)
        try:
            hist = comfy("GET", "/history/" + pid, timeout=10)
            if pid in hist or (hist and list(hist)[0] == pid):
                entry = hist.get(pid) or next(iter(hist.values()))
                st = (entry.get("status") or {})
                s = (st.get("status_str") or "").lower()
                if s == "success" or st.get("completed") is True:
                    done_events[pid] = {"ok": True}
                    continue
                if s in ("error", "interrupted"):
                    msgs = st.get("messages") or []
                    err = ""
                    for m in msgs:
                        if isinstance(m, list) and m and m[0] in ("execution_error", "execution_interrupted"):
                            d = m[1] if len(m) > 1 and isinstance(m[1], dict) else {}
                            err = d.get("exception_message") or m[0]
                    done_events[pid] = {"ok": False, "error": err or s}
                    continue
        except Exception:
            pass
        time.sleep(0.4)
    job["status"] = "error"
    job["error"] = "Timed out waiting for ComfyUI. The run may still be going in ComfyUI itself."
    job["t_done"] = time.time()


def _worker():
    while True:
        with q_cv:
            while not job_q:
                q_cv.wait()
            jid = job_q.pop(0)
        job = jobs.get(jid)
        if not job or job["status"] == "cancelled":
            continue
        try:
            if not comfy_up():
                job["status"] = "error"
                job["error"] = "ComfyUI is not running at http://127.0.0.1:8188. Start it first, then try again."
                job["t_done"] = time.time()
                continue
            # Queue behind a foreign job rather than interrupting it.
            # Our own previous jobs are allowed to sit in Comfy's native queue.
            foreign, _, _ = comfy_busy_with_other(_our_prompt_ids())
            if foreign:
                job["status"] = "queued"
                job["message"] = "Queued behind another ComfyUI job. This app will not cancel someone else's run."
            if job["status"] == "cancelled":
                continue
            if job.get("pe"):
                job["status"] = "running"
                job["message"] = "Enhancing prompt (%s)..." % ("Quality" if job.get("pe_mode") == "quality" else "Fast")
                ran = run_prompt_enhancer(job)
                if job["status"] == "cancelled":
                    continue
                if ran and job.get("workflow_id"):
                    apply_wf_enhanced_prompt(job)
                elif ran:
                    try:
                        apply_enhanced_prompt(job)
                    except ValueError as e:
                        job["status"] = "error"
                        job["error"] = str(e)
                        job["t_done"] = time.time()
                        continue
            job["status"] = "running"
            job["message"] = "Starting…"
            body = {"prompt": job["graph"], "client_id": CLIENT_ID}
            try:
                resp = comfy("POST", "/prompt", body, timeout=60)
            except ComfyError as e:
                job["status"] = "error"
                job["error"] = str(e)
                job["t_done"] = time.time()
                continue
            pid = resp.get("prompt_id") or resp.get("promptId")
            if not pid:
                job["status"] = "error"
                job["error"] = "ComfyUI did not accept the job."
                job["t_done"] = time.time()
                continue
            job["prompt_id"] = pid
            job["message"] = "Generating…"
            # WAIT_IN_BACKGROUND_FOR_QUEUE
            # Comfy queues natively: do not block this worker on our own job.
            threading.Thread(target=_wait_for_job, args=(job,), daemon=True).start()
        except Exception:
            log.exception("worker")
            job["status"] = "error"
            job["error"] = "Something went wrong in the app: %s" % traceback.format_exc().splitlines()[-1]
            job["t_done"] = time.time()


# ------------------------------------------------------------------ uploads
_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",  # checked more carefully below
}


def _sniff(data, fname=""):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if len(data) > 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in (b"heic", b"heif", b"mif1", b"msf1", b"heix", b"hevc", b"avif"):
            # avif is not HEIC, but some phones send similar boxes; only treat heic/heif family as heic
            if brand != b"avif":
                return "heic"
    ext = os.path.splitext(fname or "")[1].lower()
    if ext in (".heic", ".heif"):
        return "heic"
    return None


_HEIC_HELP = (
    "This photo is in HEIC format (common on iPhone), and this PC does not have a HEIC decoder installed. "
    "On your iPhone, export or share it as JPEG, or set Settings > Camera > Formats to Most Compatible, then try again."
)


def _open_image(data, kind, fname):
    from PIL import Image, ImageOps
    if kind == "heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            raise ValueError(_HEIC_HELP)
    try:
        im = Image.open(io.BytesIO(data))
        orient = _exif_orientation(im)
        im = ImageOps.exif_transpose(im) or im
        im.load()
        im.info = dict(im.info or {})
        im.info["h3_baked"] = orient not in (0, 1)
    except ValueError:
        raise
    except Exception as e:
        if kind == "heic":
            raise ValueError(_HEIC_HELP) from e
        raise ValueError("Couldn't read that image: %s" % e) from e
    return im


def find_ffmpeg():
    """ffmpeg on PATH, else ComfyUI's bundled imageio-ffmpeg (video previews; optional)."""
    import glob as _glob, shutil as _sh
    exe = _sh.which("ffmpeg")
    if exe:
        return exe
    base = os.path.dirname(COMFY_ROOT)
    for pat in (os.path.join(base, "python_embeded", "Lib", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg*.exe"),
                os.path.join(COMFY_ROOT, "venv", "Lib", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg*.exe")):
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def _media_sniff(head, ext):
    """Loose check that a video / audio upload is what its name says (no executables renamed to .mp4)."""
    if ext in (".mp4", ".mov", ".m4v", ".m4a", ".aac"):
        return head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide") or (ext == ".aac" and head[:2] in (b"\xff\xf1", b"\xff\xf9"))
    if ext in (".webm", ".mkv"):
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext in (".avi", ".wav"):
        return head[:4] == b"RIFF"
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if ext == ".flac":
        return head[:4] == b"fLaC"
    if ext == ".ogg":
        return head[:4] == b"OggS"
    return False


def save_media_upload(data, fname):
    """A video / audio file for a workflow input, saved as-is into the app's input folder."""
    if not data:
        raise ValueError("The file was empty.")
    if len(data) > MAX_MEDIA_UPLOAD:
        raise ValueError("That file is larger than %d MB." % (MAX_MEDIA_UPLOAD // (1024 * 1024)))
    orig = os.path.basename(fname or "upload")
    stem, ext = os.path.splitext(orig)
    ext = ext.lower()
    if ext not in MEDIA_EXT or not _media_sniff(data[:16], ext):
        raise ValueError("Please add an MP4, MOV, WebM, MKV video or an MP3, WAV, FLAC, OGG, M4A audio file.")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "media"
    name = "%s_%s%s" % (time.strftime("%Y%m%d_%H%M%S"), stem[:40], ext)
    dest_dir = os.path.join(INPUT, INPUT_SUB)
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, name), "wb") as f:
        f.write(data)
    kind = "video" if MEDIA_EXT[ext].startswith("video") else "audio"
    return {"name": INPUT_SUB + "/" + name, "kind": kind, "size": len(data), "label": orig}


_inst_cache = {"t": 0, "fp": None, "idx": None}


def install_index(force=False):
    """ComfyUI's model folders + what's in them (cached a minute; re-read after downloads)."""
    if force or not _inst_cache["fp"] or time.time() - _inst_cache["t"] > 60:
        fp = comfy("GET", "/internal/folder_paths", timeout=15)
        _inst_cache.update(fp=fp, idx=INST.index_present(fp), t=time.time())
    return _inst_cache["fp"], _inst_cache["idx"]


def install_plan(wid=None):
    """For each engine: fit for this PC, files present / missing (with official links), node packs, free space."""
    fp, idx = install_index()
    hw = hardware()
    try:
        oi = object_info()
    except Exception:
        oi = {}
    out = []
    for card in WF.list_cards():
        if wid and card.get("id") != wid:
            continue
        try:
            card, graph = WF.get(card["id"])
        except Exception:
            continue
        files = INST.plan_for(graph, idx, fp)
        missing = [f for f in files if not f["present"]]
        nodes = sorted({n.get("class_type") for n in graph.values() if n.get("class_type") and oi and n.get("class_type") not in oi})
        verdict = fit_verdict(apply_effective(card, hw), hw)
        tgt = next((f["target_dir"] for f in missing if f.get("target_dir")), None) or COMFY_ROOT
        out.append({
            "id": card["id"], "name": card.get("name"), "family": card.get("family"), "media": card.get("media"),
            "fit": verdict.get("fit"), "fit_reason": verdict.get("reason"),
            "files": files, "missing": len(missing), "downloadable": sum(1 for f in missing if f.get("url")),
            "no_source": [f["file"] for f in missing if not f.get("url")], "missing_nodes": nodes,
            "free_gb": INST.free_gb(tgt),
        })
    return {"hardware": {k: hw.get(k) for k in ("gpu", "vram_gb", "ram_gb", "profile")}, "items": out}


UPDATE_STATE = {"checked": 0, "status": None}


def auto_critical_on():
    """Important fixes install by themselves (only while idle) unless the user switched it off in Settings."""
    v = app_paths.load(DATA).get("auto_critical")
    return True if v is None else bool(v)


def _active_job_count():
    return len([j for j in jobs.values() if j.get("status") in ("queued", "running", "waiting_comfy")])


def _update_loop():
    time.sleep(60)
    while True:
        try:
            if UPD.release_repo() or UPD.is_git_install():
                st = UPD.status(fetch=True)
                UPDATE_STATE.update(checked=time.time(), status=st)
                if st.get("critical") and st.get("behind") and auto_critical_on():
                    # wait for an idle moment (never restart under someone's generation)
                    for _ in range(120):
                        if _active_job_count() == 0:
                            break
                        time.sleep(30)
                    if _active_job_count() == 0:
                        log.info("installing critical update %s automatically", st.get("latest"))
                        res = UPD.apply()
                        if res.get("updated"):
                            UPD.restart_soon(port=PORT)
                            return
        except Exception as e:
            log.info("update check: %s", e)
        time.sleep(12 * 3600)


def _pc_media(q):
    m = (q.get("media") or ["image"])[0]
    return m if m in ("image", "video", "audio") else "image"


def save_upload(data, fname):
    if not data:
        raise ValueError("The file was empty.")
    if len(data) > MAX_UPLOAD:
        raise ValueError("That image is larger than 80 MB.")
    kind = _sniff(data, fname)
    if not kind:
        raise ValueError("Please upload a JPEG, PNG, WebP, GIF, or BMP image. HEIC from iPhone needs a JPEG export if conversion is unavailable.")
    im = _open_image(data, kind, fname)
    w, h = im.size
    if w < 8 or h < 8:
        raise ValueError("That image is too small.")
    # Very large phone photos: downscale the working copy so ComfyUI can load them.
    converted = False
    if w * h > 40_000_000:
        from PIL import Image
        im.thumbnail((8192, 8192), Image.Resampling.LANCZOS)
        w, h = im.size
        converted = True
    needs_bake = bool((im.info or {}).get("h3_baked"))
    if kind == "heic" or converted or needs_bake or im.mode not in ("RGB", "L", "RGBA"):
        buf = io.BytesIO()
        rgb = im.convert("RGB") if im.mode != "RGB" else im
        rgb.save(buf, "JPEG", quality=92)
        data = buf.getvalue()
        kind = "jpeg"
        converted = True
    orig = os.path.basename(fname or "upload")
    stem, ext = os.path.splitext(orig)
    ext = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "heic": ".jpg"}.get(kind, ext or ".png")
    if converted:
        ext = ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "image"
    name = "%s_%s%s" % (time.strftime("%Y%m%d_%H%M%S"), stem[:40], ext)
    dest_dir = os.path.join(INPUT, INPUT_SUB)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(path):
        name = "%s_%s_%d%s" % (time.strftime("%Y%m%d_%H%M%S"), stem[:40], n, ext)
        path = os.path.join(dest_dir, name)
        n += 1
    with open(path, "wb") as f:
        f.write(data)
    rel = INPUT_SUB + "/" + name
    return {"name": rel, "filename": name, "width": w, "height": h, "url": "/api/input?name=" + urllib.parse.quote(rel)}


def make_thumb(full, size=384):
    cache = os.path.join(THUMBS, hashlib.sha1((full + "|s%d|%s|" % (size, THUMB_CACHE_VER) + str(os.path.getmtime(full))).encode()).hexdigest() + ".jpg")
    if os.path.isfile(cache):
        return open(cache, "rb").read()
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(full))
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    data = buf.getvalue()
    try:
        open(cache, "wb").write(data)
    except OSError:
        pass
    return data


# ------------------------------------------------------------------ HTTP
LOGIN_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Comfy Studio</title>
<link rel="stylesheet" href="/static/app.css">
</head><body class="login-body">
<form class="login-card" method="post" action="/login">
  <p class="eyebrow">Comfy Studio</p>
  <h1>Enter the PIN</h1>
  <p class="lede">This screen is only shown on the tailnet. The PIN is in C:\\ComfyStudio\\data\\PIN.txt on the PC.</p>
  <input name="pin" type="password" inputmode="numeric" autocomplete="current-password" autofocus placeholder="PIN">
  <button type="submit">Unlock</button>
  <p class="err">%ERR%</p>
</form>
</body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        log.info("%s " + fmt, self.client_address[0], *a)

    def _ip(self):
        return ipaddress.ip_address(self.client_address[0].removeprefix("::ffff:"))

    def local(self):
        return self._ip().is_loopback and not self.headers.get("X-Forwarded-For")

    def on_host(self):
        """True when the page is open on the PC that runs Comfy Studio (loopback, or this PC's own tailnet address)."""
        try:
            return self.local() or str(self._ip()) == str(ipaddress.ip_address(self.connection.getsockname()[0].removeprefix("::ffff:")))
        except Exception:
            return False

    def network_ok(self):
        ip = self._ip()
        return ip.is_loopback or ip in TAILNET

    def authed(self):
        if self.local():
            return True
        c = self.headers.get("Cookie") or ""
        m = re.search(r"(?:^|;\s*)h3_sess=([^;]+)", c)
        return bool(m and session_ok(m.group(1)))

    def send_bytes(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, str):
            body = body.encode()
        # text (JSON, JS, CSS, HTML) is compressed when the browser accepts it: the library list goes from ~800 KB to
        # ~100 KB, which matters on a phone over Tailscale
        gz = False
        if len(body) > 2048 and ctype.split(";")[0] in ("application/json", "text/javascript", "text/css", "text/html") \
                and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
            body = gzip.compress(body, compresslevel=5)
            gz = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if not any((headers or {}) and k.lower() == "cache-control" for k in (headers or {})):
            if ctype.startswith(("application/json", "text/html")):
                self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_range(self, full, ctype, download_name=None):
        size = os.path.getsize(full)
        start, end = 0, size - 1
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        partial = False
        if m and (m.group(1) or m.group(2)):
            partial = True
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=3600")
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if download_name:
            self.send_header("Content-Disposition", "attachment; filename=\"%s\"" % download_name)
        self.end_headers()
        with open(full, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def j(self, obj, code=200):
        return self.send_bytes(code, json.dumps(obj), "application/json; charset=utf-8")

    def body(self, limit=MAX_UPLOAD):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("That upload is too large.")
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("GET")
            try:
                self.j({"error": server_error_text(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError as e:
            self.j({"error": str(e)}, 400)
        except Exception as e:
            log.exception("POST")
            try:
                self.j({"error": server_error_text(e)}, 500)
            except Exception:
                pass

    def _static(self, name):
        safe = os.path.basename(name)
        path = os.path.abspath(os.path.join(STATIC, safe))
        if not path.startswith(os.path.abspath(STATIC) + os.sep) or not os.path.isfile(path):
            return self.send_bytes(404, "not found", "text/plain")
        ext = os.path.splitext(path)[1].lower()
        ctype = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}.get(ext, "application/octet-stream")
        # app.js / app.css are asked for as ?v=<version>, and index.html (never cached) names a new version whenever
        # they change: so a versioned copy can be kept by the browser instead of re-downloading ~500 KB every time
        versioned = ext in (".js", ".css") and "v=" in urllib.parse.urlsplit(self.path).query
        cache = "public, max-age=31536000, immutable" if versioned else "no-store"
        return self.send_bytes(200, open(path, "rb").read(), ctype, {"Cache-Control": cache})

    def _get(self):
        if not self.network_ok():
            return self.send_bytes(403, "Forbidden", "text/plain")
        u = urllib.parse.urlsplit(self.path)
        path = u.path
        q = urllib.parse.parse_qs(u.query)
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if not self.authed():
            if path.startswith("/api/"):
                return self.j({"error": "auth"}, 401)
            return self.send_bytes(200, LOGIN_HTML.replace("%ERR%", ""), "text/html; charset=utf-8")
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path == "/api/workflows":
            items = []
            hw = hardware()
            for c in WF.list_cards():
                it = {k: c.get(k) for k in ("id", "name", "family", "task", "media", "source", "picture_count", "missing_nodes",
                                            "saved", "description", "picture_min", "picture_max", "lengths", "default_length",
                                            "resolutions", "default_resolution", "aspects", "limits", "needs", "builtin", "alternative")}
                sl = c.get("slots") or {}
                it["has"] = {"seed": bool(sl.get("seed")), "negative": bool(sl.get("negative")),
                             "length": bool(sl.get("length") or sl.get("seconds")),
                             "size": bool(sl.get("width") or sl.get("res_label") or c.get("size_rule"))}
                if it.get("picture_min") is None:
                    it["picture_min"] = it["picture_max"] = len(sl.get("images") or [])
                it["lora_family"] = lora_family_for(c)
                it.update(fit_verdict(c, hw))
                eff = SP.effective(c, hw)
                if eff:
                    it.update(resolutions=eff["resolutions"], limits=eff["limits"], limits_basis=eff["basis"])
                req = c.get("requires") or {}
                try:
                    oi_now = object_info()
                except Exception:
                    oi_now = {}
                it["missing_custom"] = sorted({x["class_type"] for x in req.get("custom_nodes") or [] if oi_now and x["class_type"] not in oi_now})
                it["missing_models"] = [m["file"] for m in req.get("loras") or [] if not _model_size_gb(m["file"])]
                it["requires"] = {"models": [m["file"] for m in req.get("loras") or []],
                                  "custom_nodes": sorted({x.get("module") or x["class_type"] for x in req.get("custom_nodes") or []})}
                items.append(it)
            return self.j({"items": items, "tasks": WF.TASKS, "hardware": hw})
        if path == "/api/workflow":
            try:
                card, _graph = WF.get((q.get("id") or [""])[0])
            except ValueError as e:
                return self.j({"error": str(e)}, 404)
            card = apply_effective(card, hardware())
            if not card.get("mp_max") and card.get("media") == "image":
                # an uploaded workflow doesn't say how big it draws well: use its model family's range (MOSS 1-2 MP ...)
                prof = next((p for p in WF.profiles() if p.get("id") == card.get("profile")), None)
                if prof and prof.get("mp_options"):
                    card = dict(card, mp_max=max(prof["mp_options"]))
            try:
                knobs = WF.workflow_knobs(card, _graph, object_info())
            except Exception as e:
                log.info("workflow knobs: %s", e)
                knobs = []
            card["lora_family"] = lora_family_for(card)
            return self.j({"card": card, "options": _wf_model_options(card), "summary": WF.summary(card), "knobs": knobs})
        if path == "/api/install/plan":
            try:
                return self.j(install_plan((q.get("id") or [""])[0] or None))
            except Exception as e:
                log.info("install plan: %s", e)
                return self.j({"error": "ComfyUI isn't answering, so the app can't check its model folders.", "items": []}, 503)
        if path == "/api/install/progress":
            return self.j(INST.status())
        if path == "/api/loras/index":
            # every LoRA with its folder, model family and trigger words (read from the file itself)
            try:
                oi = object_info()
            except Exception:
                oi = {}
            names = [n for n in (combo_list(oi, "LoraLoaderModelOnly", "lora_name") or []) if n and n != "None"]
            try:
                fp, _idx = install_index()
                dirs = [d for d in (fp.get("loras") or []) if isinstance(d, str)]
            except Exception:
                dirs = []
            return self.j(dict({"items": LI.build(names, dirs, DATA), "engine_family": LI.ENGINE_FAMILY}, **LI.client_lists()))
        if path == "/api/loras":
            try:
                oi = object_info()
            except Exception:
                oi = {}
            names = combo_list(oi, "LoraLoaderModelOnly", "lora_name") or []
            return self.j({"items": [n for n in names if n and n != "None"]})
        if path == "/api/hardware":
            hw = hardware()
            hw["image_mp_max"] = SP.image_mp_max(hw)
            return self.j(hw)
        if path == "/api/upscale/options":
            it = gallery_get((q.get("id") or [""])[0])
            hw = hardware()
            try:
                oi = object_info()
            except Exception:
                oi = {}
            ms = SP.upscalers(hw, oi, os.path.join(COMFY_ROOT, "models"))
            short = min(it.get("width") or 720, it.get("height") or 720) if it else 720
            for m in ms:
                m["targets"] = [t for t in m["targets"] if dict(SP.TARGETS)[t] > short]
            return self.j({"methods": [m for m in ms if m["targets"]], "source_short": short, "hardware": hw})
        if path == "/api/pe/system":
            eng = (q.get("engine") or ["builtin"])[0] or "builtin"
            mode = "edit" if (q.get("mode") or ["t2i"])[0] == "edit" else "t2i"
            pe_mode = "quality" if (q.get("pe_mode") or ["fast"])[0] == "quality" else "fast"
            note, name, media, modes = "", "MiniMax H3", "image", ["t2i", "edit"]   # built-in: new pictures and edits
            tpl_media = "image"
            if eng != "builtin":
                try:
                    card, _g = WF.get(eng)
                except Exception:
                    return self.j({"error": "Unknown engine."}, 404)
                name = card.get("name") or eng
                note = PD.for_card(card).get("hint") or ""
                media = card.get("media") or "image"
                applies = card.get("enhance", True) is not False and bool((card.get("slots") or {}).get("prompt"))
                if media == "video":
                    lo, hi = int(card.get("picture_min") or 0), int(card.get("picture_max") or 0)
                    modes = (["t2i"] if lo == 0 else []) + (["edit"] if hi > 0 else [])
                else:      # pictures: new pictures (no Picture 1 needed) and edits (takes pictures)
                    modes = (["t2i"] if int(card.get("picture_min") or 0) == 0 else []) \
                        + (["edit"] if int(card.get("picture_max") or 0) > 0 else [])
                    if PD.for_card(card).get("id") != "minimax_h3_image":
                        tpl_media = graphs.GENERIC_MEDIA      # edits for other models: plain instruction
                if not applies:
                    modes = []
                if media == "video":
                    tpl_media = "video"
            if modes and mode not in modes:
                mode = modes[0]
            return self.j({"engine": eng, "engine_name": name, "mode": mode, "pe_mode": pe_mode, "media": media,
                           "modes": modes, "default": graphs.default_pe_system(mode, pe_mode, tpl_media) if modes else "",
                           "custom": pe_override(eng, mode), "model_note": note})
        if path == "/api/understanding":
            eng = (q.get("engine") or ["builtin"])[0] or "builtin"
            d = UND.load(DATA)
            return self.j({"all": d.get("*") or {}, "engine": d.get(eng) or {}, "engine_id": eng})
        if path == "/api/nodes/job":
            j = CN.JOBS.get((q.get("id") or [""])[0])
            return self.j(j) if j else self.j({"error": "unknown job"}, 404)
        if path == "/api/nodes/required":
            try:
                have = set(object_info())
            except Exception:
                have = set()
            return self.j({"packs": CN.required_status(HERE, NODES_DIR, have), "comfy_up": bool(have),
                           "nodes_dir": NODES_DIR, "comfy_python": bool(COMFY_PY)})
        if path == "/api/nodes/custom":
            return self.j({"installed": CN.installed(DATA), "comfy_python": bool(COMFY_PY), "nodes_dir": NODES_DIR})
        if path == "/api/nodes/status":
            return self.j(NM.queue_status())
        if path == "/api/update/state":
            # what the last background check found (no network), plus the user's auto-fix choice
            st = dict(UPDATE_STATE.get("status") or {"version": UPD.app_version(), "repo": UPD.release_repo()})
            st["auto_critical"] = bool(auto_critical_on())
            st["checked"] = UPDATE_STATE.get("checked")
            return self.j(st)
        if path == "/api/update/check":
            try:
                st = UPD.status(fetch=True)
                UPDATE_STATE.update(checked=time.time(), status=st)
                st = dict(st, auto_critical=bool(auto_critical_on()))
                return self.j(st)
            except Exception as e:
                return self.j({"error": str(e)}, 500)
        if path == "/api/status":
            foreign, running, pending = (False, [], [])
            up = comfy_up()
            if up:
                foreign, running, pending = comfy_busy_with_other(_our_prompt_ids())
            return self.j({
                "comfy": up,
                "busy": foreign,
                "running": len(running),
                "pending": len(pending),
                "port": PORT,
            })
        if path == "/api/config":
            try:
                oi = object_info()
                models = model_choices(oi)
            except Exception as e:
                models = model_choices({})
                log.info("config models: %s", e)
            res = [{"id": r[0], "label": r[1], "width": r[2], "height": r[3], "group": r[4]} for r in graphs.RESOLUTIONS]
            fast_clip, pe_small = PE.pe_clip_for("fast", (models.get("pe_clip_name") or []) + _clip_names(), graphs.DEFAULTS["pe_clip_name"])
            return self.j({
                "resolutions": res,
                "defaults": graphs.DEFAULTS,
                "models": models,
                "fast_pe_clip": fast_clip,
                "fast_pe_has_small_model": pe_small,
                "default_resolution": "2048x2048",
                "on_host": self.on_host(),   # this PC: "From my PC" browsing; other devices: their own file picker
                "default_steps": graphs.DEFAULTS["steps_t2i"],
                "default_pe": False,
                "aspects": list(graphs.ASPECT_RATIOS.keys()),
                "mp_min": graphs.MP_MIN,
                "mp_max": graphs.MP_MAX,
                "mp_step": graphs.MP_STEP,
                "mp_note": "The model works from about 4 to 20 megapixels and is at its best around 16 MP.",
                "default_pe_mode": "fast",
                "pe_hint": "Fast is lighter on an 8 GB card and usually finishes in seconds. Quality is the full enhancer and is slower.",
            })
        if path == "/api/size":
            aspect = (q.get("aspect") or ["1:1"])[0]
            mp = (q.get("mp") or ["4"])[0]
            mode = (q.get("mode") or ["t2i"])[0]
            res_token = (q.get("res") or [""])[0].strip()
            iw = (q.get("width") or [None])[0]
            ih = (q.get("height") or [None])[0]
            input_size = None
            if iw and ih:
                try:
                    input_size = (int(iw), int(ih))
                except ValueError:
                    input_size = None
            try:
                if res_token:
                    spec = graphs.resolve_size(res_token, mode=mode or "edit", input_size=input_size)
                else:
                    asp = str(aspect or "").strip()
                    mp_s = str(mp or "").strip().lower()
                    if graphs.is_keep_resolution(asp) or (asp.lower() in {"keep", "original", "picture1"} and mp_s in {"", "original", "orig", "keep"}):
                        spec = graphs.resolve_size("picture1:original", mode=mode or "edit", input_size=input_size)
                    elif asp.lower() in {"picture1", "match", "keep", "original"}:
                        token = "picture1:original" if mp_s in {"original", "orig", "keep", ""} else "picture1:%s" % (mp_s or "8")
                        spec = graphs.resolve_size(token, mode=mode or "edit", input_size=input_size)
                    else:
                        spec = graphs.resolve_size("custom:%s:%s" % (asp, mp), mode=mode, input_size=input_size)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            w, h = spec.get("width"), spec.get("height")
            ow, oh = spec.get("output_width") or w, spec.get("output_height") or h
            info = graphs.describe_size(ow, oh) if ow and oh else {"width": None, "height": None, "mp": None}
            info["width"] = ow
            info["height"] = oh
            info["model_width"] = w
            info["model_height"] = h
            info["original"] = bool(spec.get("original"))
            info["restore"] = bool(spec.get("restore"))
            info["picture1"] = bool(spec.get("picture1") or spec.get("original"))
            info["ref_megapixels"] = graphs.target_ref_megapixels(spec, input_size)
            info["match"] = False
            info["orig_width"] = spec.get("orig_width")
            info["orig_height"] = spec.get("orig_height")
            return self.j(info)
        if path == "/api/workflows/test/status":
            j = jobs.get((q.get("id") or [""])[0])
            if not j:
                return self.j({"error": "That test run wasn't found."}, 404)
            v = job_view(j)
            if v.get("status") == "error":
                v["problem"] = plain_test_problem(v.get("error") or "")
            return self.j(v)
        if path == "/api/jobs":
            out = []
            for i in reversed(order[-80:]):
                if i not in jobs:
                    continue
                if jobs[i].get("test_run"):
                    continue                    # Test runs show on the upload screen, not in the feed
                view = job_view(jobs[i])
                view["has_preview"] = bool(
                    live.get("preview")
                    and live.get("prompt_id") == jobs[i].get("prompt_id")
                    and time.time() - live.get("t", 0) < 30
                )
                jb = jobs[i]
                src0 = (jb.get("source_images") or [None])[0]
                view.update(phase=job_phase(jb), media=jb.get("media") or "image",
                            source0=src0, parent_id=lineage_get(src0) if src0 else None,
                            workflow_name=jb.get("workflow_name") or "", t_start=jb.get("t"))
                out.append(view)
            return self.j(out)
        if path.startswith("/api/job"):
            jid = (q.get("id") or [""])[0]
            job = jobs.get(jid)
            if not job:
                return self.j({"error": "not found"}, 404)
            view = job_view(job)
            view["has_preview"] = bool(live.get("preview") and live.get("prompt_id") == job.get("prompt_id") and time.time() - live.get("t", 0) < 30)
            return self.j(view)
        if path == "/api/live":
            jid = (q.get("id") or [""])[0]
            job = jobs.get(jid) if jid else None
            img = live.get("preview")
            if not img or time.time() - live.get("t", 0) > 30:
                return self.send_bytes(204, b"")
            if job and job.get("prompt_id") and live.get("prompt_id") not in (None, job.get("prompt_id")):
                return self.send_bytes(204, b"")
            if job and (job.get("inpaint") or {}).get("box"):
                img = inpaint_live(job, img) or img
            ctype = "image/jpeg" if img[:2] == b"\xff\xd8" else "image/png"
            return self.send_bytes(200, img, ctype, {"Cache-Control": "no-store"})
        if path == "/api/gallery":
            mode = (q.get("mode") or ["all"])[0].lower()
            fav_only = (q.get("fav") or ["0"])[0] in ("1", "true", "yes")
            items = []
            for it in gallery_all():
                if mode in ("t2i", "edit") and it.get("mode") != mode:
                    continue
                if fav_only and not it.get("fav"):
                    continue
                d = LIB.decorate(it)
                # the page never reads assist_preview, and prompt_sent only as a fall-back for prompt
                d.pop("assist_preview", None)
                if d.get("prompt_sent") == d.get("prompt"):
                    d.pop("prompt_sent", None)
                items.append(d)
                if len(items) >= 5000:       # was 200: older pictures were missing from the Library
                    break
            return self.j({"items": items})
        if path in ("/api/image", "/api/thumb", "/api/download"):
            jid = (q.get("id") or [""])[0]
            it = gallery_get(jid) or (jobs.get(jid) and {"id": jid, "filename": (jobs[jid].get("images") or [{}])[0].get("filename"), "subfolder": (jobs[jid].get("images") or [{}])[0].get("subfolder")})
            if not it or not it.get("filename"):
                return self.send_bytes(404, b"not found", "text/plain")
            full = output_path(it.get("subfolder") or "", it["filename"])
            if not full or not os.path.isfile(full) or not LIB.is_app_output(full, OUTPUT, APP_OUT):
                return self.send_bytes(404, b"not found", "text/plain")
            if path == "/api/thumb":
                try:
                    return self.send_bytes(200, make_thumb(full), "image/jpeg", {"Cache-Control": "max-age=86400"})
                except Exception:
                    pass
            if os.path.splitext(full)[1].lower() in VIDEO_EXT and path != "/api/thumb":
                return self._send_range(full, VIDEO_TYPES[os.path.splitext(full)[1].lower()],
                                        it["filename"] if path == "/api/download" else None)
            if path == "/api/thumb" and os.path.splitext(full)[1].lower() in VIDEO_EXT:
                return self.send_bytes(404, b"video", "text/plain")
            data, ctype = serve_oriented_bytes(full)
            headers = {"Cache-Control": "max-age=3600"}
            if path == "/api/download":
                headers["Content-Disposition"] = "attachment; filename=\"%s\"" % it["filename"]
            return self.send_bytes(200, data, ctype, headers)
        if path == "/api/input":
            name = (q.get("name") or [""])[0].replace("\\", "/")
            if not name or ".." in name.split("/"):
                return self.send_bytes(404, b"", "text/plain")
            full = os.path.abspath(os.path.join(INPUT, name.replace("/", os.sep)))
            root = os.path.abspath(INPUT)
            if not (full.startswith(root + os.sep) and os.path.isfile(full)):
                return self.send_bytes(404, b"", "text/plain")
            ext = os.path.splitext(full)[1].lower()
            if ext in MEDIA_EXT:
                if (q.get("frame") or [""])[0] == "1" and MEDIA_EXT[ext].startswith("video"):
                    try:
                        st = os.stat(full)
                        key = hashlib.sha1(("in|%s|%s|%s" % (full, st.st_mtime, st.st_size)).encode()).hexdigest()
                        cache = pc_browse.video_frame_jpeg(full, os.path.join(THUMBS, key + ".jpg"), 384)
                        return self.send_bytes(200, open(cache, "rb").read(), "image/jpeg", {"Cache-Control": "max-age=86400"})
                    except Exception:
                        return self.send_bytes(404, b"", "text/plain")
                if os.path.getsize(full) > MAX_MEDIA_UPLOAD:
                    return self.send_bytes(413, b"", "text/plain")
                return self.send_bytes(200, open(full, "rb").read(), MEDIA_EXT[ext], {"Cache-Control": "max-age=3600"})
            data, ctype = serve_oriented_bytes(full)
            return self.send_bytes(200, data, ctype, {"Cache-Control": "max-age=3600"})
        if path == "/api/source":
            it = gallery_get((q.get("id") or [""])[0])
            if not it:
                return self.send_bytes(404, b"not found", "text/plain")
            slot = (q.get("slot") or ["1"])[0]
            try:
                slot_n = int(slot)
            except ValueError:
                slot_n = 1
            snap = None
            for s in (it.get("input_snapshots") or []):
                if int(s.get("slot") or 0) == slot_n:
                    snap = s
                    break
            if snap is None and slot_n == 1 and it.get("source_filename"):
                snap = {"subfolder": it.get("source_subfolder") or "", "filename": it["source_filename"]}
            if not snap or not snap.get("filename"):
                return self.send_bytes(404, b"not found", "text/plain")
            full = output_path(snap.get("subfolder") or "", snap["filename"])
            if not full or not os.path.isfile(full) or not LIB.is_app_output(full, OUTPUT, APP_OUT):
                return self.send_bytes(404, b"not found", "text/plain")
            ctype = "image/png" if full.lower().endswith(".png") else "image/jpeg"
            return self.send_bytes(200, open(full, "rb").read(), ctype, {"Cache-Control": "max-age=3600"})
        # ----- From PC (read-only browse) -----
        if path == "/api/pc/roots":
            pub = pc_browse.public_roots(DATA)
            if isinstance(pub, dict) and ("shortcuts" in pub or "drives" in pub or "roots" in pub):
                # Flatten {roots, shortcuts, drives} to the top-level JSON object.
                return self.j(pub)
            return self.j({"roots": pub})
        if path == "/api/pc/list":
            try:
                off = max(0, int((q.get("offset") or ["0"])[0]))
                lim = max(1, min(200, int((q.get("limit") or ["80"])[0])))
                return self.j(pc_browse.list_dir(DATA, (q.get("root") or [""])[0], (q.get("path") or [""])[0], off, lim, media=_pc_media(q)))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception:
                return self.j({"error": "Couldn't open that folder.", "dirs": [], "files": [], "total": 0, "more": False}, 400)
        if path == "/api/pc/recent":
            try:
                off = max(0, int((q.get("offset") or ["0"])[0]))
                lim = max(1, min(200, int((q.get("limit") or ["80"])[0])))
                return self.j(pc_browse.recent(DATA, off, lim, media=_pc_media(q)))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception:
                return self.j({"error": "Couldn't list recent images.", "items": [], "total": 0, "more": False}, 400)
        if path == "/api/pc/search":
            try:
                off = max(0, int((q.get("offset") or ["0"])[0]))
                lim = max(1, min(200, int((q.get("limit") or ["80"])[0])))
                return self.j(pc_browse.search(
                    DATA,
                    (q.get("q") or [""])[0],
                    off,
                    lim,
                    rid=(q.get("root") or [""])[0],
                    rel=(q.get("path") or [""])[0],
                    media=_pc_media(q),
                ))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception:
                return self.j({"error": "Couldn't search those folders.", "items": [], "total": 0, "more": False}, 400)
        if path == "/api/pc/thumb":
            try:
                data = pc_browse.thumb_bytes(DATA, THUMBS, (q.get("root") or [""])[0], (q.get("path") or [""])[0], size=384)
                return self.send_bytes(200, data, "image/jpeg", {"Cache-Control": "max-age=86400"})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception:
                return self.send_bytes(404, b"", "text/plain")
        if path == "/api/pc/preview":
            try:
                data = pc_browse.thumb_bytes(DATA, THUMBS, (q.get("root") or [""])[0], (q.get("path") or [""])[0], size=1600, quality=88)
                return self.send_bytes(200, data, "image/jpeg", {"Cache-Control": "max-age=3600"})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception:
                return self.send_bytes(404, b"", "text/plain")
        return self.send_bytes(404, "{}", "application/json")

    def _post(self):
        if not self.network_ok():
            return self.send_bytes(403, "{}", "application/json")
        path = urllib.parse.urlsplit(self.path).path
        if path == "/login":
            ip = str(self._ip())
            f = [t for t in _fails.get(ip, []) if time.time() - t < 600]
            if len(f) >= 6:
                return self.send_bytes(429, LOGIN_HTML.replace("%ERR%", "Too many tries. Wait 10 minutes."), "text/html; charset=utf-8")
            pin = urllib.parse.parse_qs(self.body(4096).decode()).get("pin", [""])[0]
            if hmac.compare_digest(_ph(pin, AUTH["salt"]), AUTH["hash"]):
                _fails.pop(ip, None)
                log.info("tailnet sign-in from %s", ip)
                return self.send_bytes(303, b"", "text/plain", {"Location": "/", "Set-Cookie": "h3_sess=%s; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict" % make_session()})
            _fails[ip] = f + [time.time()]
            log.info("wrong PIN from %s", ip)
            return self.send_bytes(401, LOGIN_HTML.replace("%ERR%", "Wrong PIN."), "text/html; charset=utf-8")
        if not self.authed():
            return self.j({"error": "auth"}, 401)
        if path == "/api/nodes/zip":
            fname = urllib.parse.unquote(self.headers.get("X-Filename") or "nodes.zip")
            try:
                raw = self.body(CN.MAX_ZIP)
                if not fname.lower().endswith(".zip"):
                    raise ValueError("Pick a .zip file with the node pack in it.")
                os.makedirs(NODES_DIR, exist_ok=True)
                return self.j({"job": CN.start_zip(raw, fname, DATA, NODES_DIR, COMFY_PY)})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/upload":
            ctype = (self.headers.get("Content-Type") or "")
            fname = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.png")
            is_media = os.path.splitext(fname)[1].lower() in MEDIA_EXT
            try:
                raw = self.body(MAX_MEDIA_UPLOAD if is_media else MAX_UPLOAD)
            except ValueError:
                return self.j({"error": "That file is too large."}, 413)
            if ctype.startswith("multipart/form-data"):
                raw, fname = _parse_multipart(raw, ctype, fname)
                is_media = os.path.splitext(fname)[1].lower() in MEDIA_EXT
            try:
                if is_media:
                    return self.j(save_media_upload(raw, fname))
                return self.j(save_upload(raw, fname))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        data = json.loads(self.body(2 * 1024 * 1024) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Bad request.")
        if path == "/api/clientlog":
            # errors and heartbeats from the page (phones included), for debugging what the PC can't see
            try:
                fp = os.path.join(DATA, "client.log")
                if os.path.exists(fp) and os.path.getsize(fp) > 2_000_000:
                    os.replace(fp, fp + ".old")
                with open(fp, "a", encoding="utf-8") as f:
                    f.write("%s %s %s" % (time.strftime("%H:%M:%S"), self.client_address[0], json.dumps(data)[:3000]) + chr(10))
            except Exception:
                pass
            return self.j({})
        for k in ("prompt", "negative", "mode", "workflow_id", "id"):
            if data.get(k) is not None and not isinstance(data[k], str):
                if isinstance(data[k], (list, dict)):      # never turn a list into the text "['x']" and run it
                    return self.j({"error": "That request wasn't understood (%s)." % k}, 400)
                data[k] = str(data[k])
        if path == "/api/workflows/warm":
            # the Workflows page is open: start the hidden ComfyUI page now, so the first upload is quick
            def _warm():
                try:
                    import comfy_convert
                    with comfy_convert._lock:
                        comfy_convert._ensure_page()
                        comfy_convert._state["t"] = time.time()
                        comfy_convert._schedule_idle_close()
                except Exception as e:
                    log.info("warm converter: %s", e)
            threading.Thread(target=_warm, daemon=True).start()
            return self.j({"ok": True})
        if path == "/api/workflows/inspect":
            text = data.get("text") or ""
            try:
                parsed = json.loads(text) if isinstance(text, str) else text
            except ValueError:
                return self.j({"error": "That file isn't a ComfyUI workflow (it isn't readable JSON). In ComfyUI use "
                                        "Workflow > Save, or Export, and upload that file."}, 400)
            section = data.get("section")
            # several workflows in one file (each in its own group, switched on with buttons): one entry per part
            parts = WF.sections(parsed) if section in (None, "") else []
            if parts:
                try:
                    oi = object_info()
                    out = []
                    for p in parts:
                        try:
                            g, w = WF.load_any(parsed, oi, section=p["id"])
                            c = WF.scan(g, oi)
                            c["task"] = WF.task_from_title(p["title"], c)
                            c["media"] = WF.TASK_MEDIA.get(c["task"], c.get("media") or "image")
                        except Exception as e:
                            log.info("part %s: %s", p["title"], e)
                            continue
                        c["name"] = (WF.plain_part_name(p["title"]) + " · " + ((data.get("name") or "").strip() or "Workflow"))[:80]
                        dkey = uuid.uuid4().hex[:12]
                        _wf_drafts[dkey] = (c, g, time.time())
                        out.append({"draft": dkey, "title": WF.plain_part_name(p["title"]), "task": c.get("task"),
                                    "media": c.get("media"), "plain": WF.plain_what(c),
                                    "ready": bool((c.get("slots") or {}).get("prompt")) and not c.get("missing_nodes"),
                                    "missing_nodes": c.get("missing_nodes") or [], "card": c, "summary": WF.summary(c),
                                    "candidates": _wf_candidates(g)})
                except Exception as e:
                    return self.j({"error": "Couldn't read that workflow: %s" % e}, 400)
                if out:
                    return self.j({"parts": out, "tasks": WF.TASKS,
                                   "message": "This file contains %d different workflows." % len(out)})
            try:
                oi = object_info()
                graph, warnings = WF.load_any(parsed, oi, section=section)
                card = WF.scan(graph, oi)
                if card.get("missing_nodes"):
                    # the cached node list may be older than a pack the user just installed: check once more
                    oi = object_info(force=True)
                    graph, warnings = WF.load_any(parsed, oi, section=section)
                    card = WF.scan(graph, oi)
                if WF.apply_app_mode(card, graph, WF.app_mode_inputs(parsed), oi):
                    warnings = ["Using the controls the workflow's author chose in ComfyUI App Mode."] + list(warnings)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            except Exception as e:
                return self.j({"error": "Couldn't read that workflow: %s" % e}, 400)
            card["name"] = (data.get("name") or "").strip()[:80] or "My workflow"
            card["plain"] = WF.plain_what(card)
            draft = uuid.uuid4().hex[:12]
            _wf_drafts[draft] = (card, graph, time.time())
            for k in [k for k, v in _wf_drafts.items() if time.time() - v[2] > 3600]:
                _wf_drafts.pop(k, None)
            nodes = None
            if card.get("missing_nodes"):
                try:
                    nodes = NM.resolve(card["missing_nodes"])
                except Exception as e:
                    nodes = {"manager": False, "packs": [], "unknown": card["missing_nodes"], "message": str(e)}
            # where each missing node comes from (the pack stamps ComfyUI saves into a workflow)
            src = CN.node_sources(data.get("text") or "")
            node_sources = {c: src[c] for c in (card.get("missing_nodes") or []) if c in src}
            return self.j({"draft": draft, "card": card, "summary": WF.summary(card), "warnings": warnings,
                           "nodes": nodes, "node_sources": node_sources, "candidates": _wf_candidates(graph), "tasks": WF.TASKS})
        if path == "/api/install/start":
            wid = str(data.get("id") or "")
            plan = install_plan(wid).get("items") or []
            if not plan:
                return self.j({"error": "Unknown engine."}, 404)
            it = plan[0]
            if it["fit"] == "no":
                return self.j({"error": "This engine is too big for this PC, so nothing was downloaded. " + (it.get("fit_reason") or "")}, 400)
            todo = [f for f in it["files"] if not f["present"] and f.get("url") and f.get("target_dir")]
            if not todo:
                return self.j({"error": "Nothing to download: " + ("every file is already here." if not it["missing"] else
                                                                  "the missing files have no official link; add them yourself.")}, 400)
            try:
                INST.start(todo)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            _inst_cache["t"] = 0
            return self.j({"ok": True, "queued": [f["file"] for f in todo]})
        if path == "/api/install/cancel":
            INST.cancel()
            return self.j({"ok": True})
        if path == "/api/workflows/test":
            # Test run: the upload, saved as a hidden test entry, run once small through the normal Generate path
            try:
                card, graph = _card_from_draft(data)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            card["id"] = "wft_" + re.sub(r"[^A-Za-z0-9]", "", data.get("draft") or "")[:12]
            card["test_only"] = True
            card["name"] = "Test run: " + (card.get("name") or "workflow")
            WF.save(card, graph)
            _wf_test_ids[data.get("draft")] = card["id"]
            try:
                job = submit_workflow_job(_test_request(card))
            except ValueError as e:
                return self.j({"ok": False, "test_id": card["id"], "problem": plain_test_problem(str(e))})
            except Exception as e:
                log.exception("test run")
                return self.j({"ok": False, "test_id": card["id"], "problem": plain_test_problem(str(e))})
            job["test_run"] = True
            return self.j({"ok": True, "job": job["id"], "test_id": card["id"], "prompt": job.get("prompt_user") or ""})
        if path == "/api/workflows/save":
            d = _wf_drafts.get(data.get("draft") or "")
            if not d:
                return self.j({"error": "This upload expired. Upload the file again."}, 400)
            try:
                card, graph = _card_from_draft(data)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            tid = _wf_test_ids.pop(data.get("draft"), None)
            if tid:
                try:
                    WF.delete(tid)          # the hidden test entry: the real one replaces it
                except ValueError:
                    pass
            saved = WF.save(card, graph)
            _wf_drafts.pop(data.get("draft"), None)
            return self.j({"card": saved})
        if path == "/api/workflows/delete":
            try:
                WF.delete(data.get("id") or "")
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            return self.j({"ok": True})
        if path == "/api/pe/system":
            eng = str(data.get("engine") or "builtin")[:80]
            mode = "edit" if data.get("mode") == "edit" else "t2i"
            if eng != "builtin":
                try:
                    WF.get(eng)
                except Exception:
                    return self.j({"error": "Unknown engine."}, 404)
            text = data.get("text")
            if text is not None and not isinstance(text, str):
                return self.j({"error": "That request wasn't understood (text)."}, 400)
            d = pe_prompts()
            cur = dict(d.get(eng) or {})
            if data.get("reset") or not (text or "").strip():
                cur.pop(mode, None)
            else:
                if len(text) > 20000:
                    return self.j({"error": "That system prompt is too long (20,000 characters at most)."}, 400)
                cur[mode] = text.strip()
            if cur:
                d[eng] = cur
            else:
                d.pop(eng, None)
            tmp = PE_PROMPTS + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            os.replace(tmp, PE_PROMPTS)
            return self.j({"ok": True, "custom": cur.get(mode, "")})
        if path == "/api/understanding":
            eng = str(data.get("engine") or "builtin")[:80]
            key = "*" if data.get("scope") == "all" else eng
            d = UND.load(DATA)
            if data.get("reset"):
                d.pop(key, None)
            else:
                if not isinstance(data.get("words", []), list) or not isinstance(data.get("extra", ""), str):
                    return self.j({"error": "That request wasn't understood."}, 400)
                entry = UND.clean({"words": data.get("words") or [], "extra": data.get("extra") or ""})
                if entry:
                    d[key] = entry
                else:
                    d.pop(key, None)
            UND.save(DATA, d)
            return self.j({"ok": True, "saved": d.get(key) or {}})
        if path == "/api/nodes/required":
            try:
                if not os.path.isdir(NODES_DIR):
                    raise ValueError("ComfyUI's custom_nodes folder wasn't found.")
                try:
                    have = set(object_info(force=True))
                except Exception:
                    have = set()
                return self.j({"job": CN.start_required(HERE, DATA, NODES_DIR, COMFY_PY, have)})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/nodes/github":
            try:
                if not os.path.isdir(NODES_DIR):
                    raise ValueError("ComfyUI's custom_nodes folder wasn't found.")
                return self.j({"job": CN.start_github(str(data.get("url") or ""), DATA, NODES_DIR, COMFY_PY)})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/nodes/remove":
            try:
                return self.j(CN.remove(str(data.get("name") or ""), DATA, NODES_DIR))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/nodes/install":
            try:
                return self.j(NM.install([str(x) for x in (data.get("packs") or [])][:12]))
            except Exception as e:
                return self.j({"error": "Install failed: %s" % e}, 500)
        if path == "/api/upscale":
            try:
                return self.j({"job": job_view(upscale_submit(data))})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/comfy/start":
            try:
                return self.j({"ok": True, "message": start_comfy(data.get("profile") or "everyday")})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/comfy/stop":
            try:
                return self.j({"ok": True, "message": stop_comfy(bool(data.get("force")))})
            except ValueError as e:
                return self.j({"error": str(e), "busy": "generating" in str(e)}, 400)
        if path == "/api/comfy/switch":
            try:
                return self.j({"ok": True, "message": switch_comfy_profile(data.get("profile") or "")})
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
        if path == "/api/comfy/restart":
            try:
                NM.reboot()
            except Exception:
                pass  # the connection drops while ComfyUI restarts
            return self.j({"ok": True, "message": "ComfyUI is restarting. This takes about a minute."})
        if path == "/api/update/apply":
            try:
                res = UPD.apply()
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            if res.get("updated"):
                UPD.restart_soon(port=PORT)
            return self.j(res)
        if path == "/api/update/auto":
            st = app_paths.load(DATA)
            st["auto_critical"] = bool(data.get("on"))
            app_paths.save(DATA, st)
            return self.j({"auto_critical": st["auto_critical"]})
        if path == "/api/generate":
            try:
                job = submit_job(data)
            except NeedsAction as e:
                return self.j(dict({"error": str(e), "code": e.code}, **e.extra), 409)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            return self.j({"job": job_view(job)})
        if path == "/api/aliases":
            loaded = data.get("loaded") or data.get("slots") or []
            prompt = data.get("prompt") or ""
            return self.j(aliases.normalize(prompt, loaded))
        if path == "/api/appearance/prefetch":
            raw_names = data.get("names") if isinstance(data.get("names"), list) else []
            if data.get("name"):
                raw_names = list(raw_names) + [data.get("name")]
            queued = cached = 0
            try:
                for name in raw_names:
                    pth = _safe_input_path(name)
                    if not pth:
                        continue
                    digest = APPEARANCE.file_digest(pth)
                    if digest and APPEARANCE.cache_get(digest):
                        cached += 1
                    elif APPEARANCE.prefetch(pth):
                        queued += 1
            except Exception:
                pass
            return self.j({"ok": True, "queued": queued, "cached": cached})
        if path == "/api/assist":
            prompt = data.get("prompt") or ""
            try:
                n = int(data.get("n_images") or 0)
            except (TypeError, ValueError):
                n = 0
            roles = data.get("image_roles") if data.get("image_roles") is not None else (data.get("roles") or [])
            ref_mode = bool(data.get("ref_mode"))
            enabled = True if "enabled" not in data else bool(data.get("enabled"))
            camera = data.get("camera") or {}
            mode = data.get("mode") or ("ref" if ref_mode else "edit")
            loaded = set(range(1, max(n, 0) + 1))
            alias_info = aliases.normalize(prompt, loaded)
            aspect = data.get("aspect")
            resolution = data.get("resolution")
            try:
                aw = int(data["width"]) if data.get("width") else None
                ah = int(data["height"]) if data.get("height") else None
            except (TypeError, ValueError):
                aw = ah = None
            try:
                appearances = appearances_for(data.get("images") or [], wait=0.25)
            except Exception:
                appearances = []
            info = ASSIST.build_assist_prompt(
                alias_info["text"], image_roles=roles, n_images=n,
                ref_mode=ref_mode, enabled=enabled, camera=camera, mode=mode,
                aspect=aspect, width=aw, height=ah, resolution=resolution,
                appearances=appearances,
            )
            info["alias_text"] = alias_info["text"]
            info["alias_warnings"] = alias_info.get("warnings") or []
            return self.j(info)
        if path == "/api/cancel":
            return self.j({"msg": cancel(str(data.get("id") or ""))})
        if path == "/api/setpin":
            if PC_ONLY:
                return self.j({"error": "This version only runs on this PC, so it has no PIN."}, 404)
            if not self.local():
                return self.j({"error": "Change the PIN from the PC itself."}, 403)
            pin = str(data.get("pin") or "")
            if not re.fullmatch(r"\d{4,10}", pin):
                return self.j({"error": "PIN must be 4-10 digits."}, 400)
            return self.j({"msg": set_pin(pin)})
        if path == "/api/pc/pick":
            items = data.get("items")
            if not items:
                items = [{"root": data.get("root", ""), "path": data.get("path", "")}]
            if len(items) > 16:
                return self.j({"error": "You can add at most 16 pictures at once."}, 400)
            out = []
            try:
                for it in items:
                    rid, rel = str(it.get("root") or ""), str(it.get("path") or "")
                    if pc_browse.media_kind(rel) in ("video", "audio"):
                        full, fname, _k = pc_browse.open_media_file(DATA, rid, rel, MAX_MEDIA_UPLOAD)
                        with open(full, "rb") as fh:
                            out.append(save_media_upload(fh.read(), fname))
                        continue
                    raw, fname = pc_browse.open_image_file(DATA, rid, rel, MAX_UPLOAD)
                    out.append(save_upload(raw, fname))
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            return self.j({"items": out, "item": out[0] if out else None})
        if path == "/api/favourite":
            it = gallery_get(str(data.get("id") or ""))
            if not it:
                return self.j({"error": "not found"}, 404)
            fav = bool(data.get("fav") if "fav" in data else (not it.get("fav")))
            gallery_add({"id": it["id"], "fav": fav, "t": time.time()})
            return self.j({"id": it["id"], "fav": fav})
        if path == "/api/delete":
            it = gallery_get(str(data.get("id") or ""))
            if not it:
                return self.j({"error": "not found"}, 404)
            full = output_path(it.get("subfolder") or "", it.get("filename") or "")
            if not full or not LIB.is_app_output(full, OUTPUT, APP_OUT):
                return self.j({"error": "That file is not inside the Comfy Studio output folder, so it was not deleted."}, 403)
            try:
                if os.path.isfile(full):
                    os.remove(full)
            except OSError as e:
                return self.j({"error": "Could not delete the file: %s" % e}, 500)
            snaps = list(it.get("input_snapshots") or [])
            if it.get("source_filename") and not snaps:
                snaps = [{"subfolder": it.get("source_subfolder") or "", "filename": it.get("source_filename")}]
            for s in snaps:
                src = output_path(s.get("subfolder") or "", s.get("filename") or "")
                if src and LIB.is_app_output(src, OUTPUT, APP_OUT) and os.path.isfile(src):
                    try:
                        os.remove(src)
                    except OSError:
                        pass
            gallery_add({"id": it["id"], "deleted": True, "t": time.time()})
            return self.j({"msg": "Deleted from the library."})
        if path == "/api/library/import":
            it = gallery_get(str(data.get("id") or ""))
            if not it or not it.get("filename"):
                return self.j({"error": "not found"}, 404)
            full = output_path(it.get("subfolder") or "", it["filename"])
            if not full or not os.path.isfile(full) or not LIB.is_app_output(full, OUTPUT, APP_OUT):
                return self.j({"error": "The image file is not in the app output folder."}, 404)
            try:
                out = LIB.copy_output_to_input(full, INPUT, INPUT_SUB, save_upload)
            except ValueError as e:
                return self.j({"error": str(e)}, 400)
            try:
                if isinstance(out, dict) and out.get("name"):
                    lineage_put(out["name"], it["id"])
            except Exception as e:
                log.info("lineage: %s", e)
            return self.j(out)
        if path == "/api/library/reuse-inputs":
            it = gallery_get(str(data.get("id") or ""))
            if not it:
                return self.j({"error": "not found"}, 404)
            items, missing = reuse_input_images(it)
            return self.j({"items": items, "missing": missing})
        return self.j({"error": "unknown"}, 404)



def reuse_input_images(it):
    """Copy stored input snapshots (or still-present upload files) back into the app input folder."""
    items = []
    missing = []
    snaps = list(it.get("input_snapshots") or [])
    if not snaps and it.get("source_filename"):
        snaps = [{"slot": 1, "subfolder": it.get("source_subfolder") or "", "filename": it.get("source_filename")}]
    # Fall back to original upload paths if this is an older record without snapshots.
    if not snaps:
        for i, rel in enumerate(it.get("source_images") or [], 1):
            snaps.append({"slot": i, "source_name": rel, "filename": None, "subfolder": None})
    snaps.sort(key=lambda s: int(s.get("slot") or 0))
    for s in snaps:
        slot = int(s.get("slot") or (len(items) + 1))
        label = "Picture %d" % slot
        data = None
        fname = s.get("filename") or ("picture_%d.png" % slot)
        full = output_path(s.get("subfolder") or "", s.get("filename") or "") if s.get("filename") else None
        if full and os.path.isfile(full) and LIB.is_app_output(full, OUTPUT, APP_OUT):
            data = open(full, "rb").read()
            fname = os.path.basename(full)
        else:
            rel = str(s.get("source_name") or "").replace("\\", "/").lstrip("/")
            if rel and ".." not in rel.split("/"):
                alt = os.path.abspath(os.path.join(INPUT, rel.replace("/", os.sep)))
                root = os.path.abspath(INPUT)
                if alt.startswith(root + os.sep) and os.path.isfile(alt):
                    data = open(alt, "rb").read()
                    fname = os.path.basename(alt)
        if not data:
            missing.append(label + " could not be found.")
            continue
        try:
            items.append(save_upload(data, fname))
        except ValueError as e:
            missing.append(label + ": " + str(e))
    return items, missing


def _parse_multipart(raw, ctype, fallback):
    m = re.search(r"boundary=([^;]+)", ctype)
    if not m:
        return raw, fallback
    boundary = m.group(1).strip().strip('"').encode()
    parts = raw.split(b"--" + boundary)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        if not body:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        fn = re.search(br'filename="([^"]+)"', head)
        name = fn.group(1).decode("utf-8", "replace") if fn else fallback
        return body, name
    return raw, fallback


class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A browser dropping a request it no longer needs (a newer live preview, a video seek, leaving the page)
        # is normal; only log real errors.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _studio_answers(port):
    """True when a Comfy Studio page is already served on 127.0.0.1:port (any HTTP answer from our app)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=3) as r:
            return b"Comfy Studio" in r.read(4096)
    except Exception:
        return False


def serve(host):
    while True:
        if os.path.exists(STOP_FILE):
            log.info("stop file present, not binding %s", host)
            time.sleep(2)
            continue
        try:
            srv = Server((host, PORT), H)
            log.info("listening on http://%s:%d", host, PORT)
            srv.serve_forever()
        except OSError as e:
            # Another Comfy Studio already answering on this port (the app was opened twice, or an older copy is
            # still running): this copy is not needed. Without this it retried forever and copies piled up.
            if host == "127.0.0.1" and _studio_answers(PORT):
                log.info("another Comfy Studio already serves port %d; this copy exits", PORT)
                os._exit(0)
            log.info("bind %s:%d failed (%s); retrying in 30 s", host, PORT, e)
            time.sleep(30)


_served_hosts = set()


def _tailnet_watch():
    """Every 30 s: bind any Tailscale address this PC has that isn't served yet, and remember it so the next start
    keeps retrying it even if Tailscale isn't up yet (serve() retries a failed bind every 30 s)."""
    while True:
        try:
            ips = app_paths.tailnet_ips()
            if ips:
                st = app_paths.load(DATA)
                known = [h for h in (st.get("tailnet_ips") or []) if isinstance(h, str)]
                merged = sorted(set(known) | set(ips))
                if merged != sorted(known):
                    st["tailnet_ips"] = merged
                    app_paths.save(DATA, st)
            for h in ips:
                if h not in _served_hosts and h != "0.0.0.0":
                    _served_hosts.add(h)
                    log.info("tailnet address appeared: %s", h)
                    threading.Thread(target=serve, args=(h,), daemon=True).start()
        except Exception as e:
            log.info("tailnet watch: %s", e)
        time.sleep(30)


def main():
    assert all(h != "0.0.0.0" for h in HOSTS)
    pc_browse.FFMPEG = find_ffmpeg()      # video previews for video inputs (optional)
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass
    threading.Thread(target=_ws_thread, daemon=True).start()
    threading.Thread(target=_update_loop, daemon=True).start()
    threading.Thread(target=_worker, daemon=True).start()
    # Bind the Tailscale IP in a side thread; skip gracefully if it is absent.
    extra = []
    for h in HOSTS[1:]:
        try:
            socket.inet_aton(h)
            # Presence check: is this address on the machine?
            ok = False
            for info in socket.getaddrinfo(h, None, socket.AF_INET):
                ok = True
                break
            # Also try binding later; getaddrinfo may succeed even if the iface is down.
            extra.append(h)
        except Exception as e:
            log.info("skipping host %s (%s)", h, e)
    for h in extra:
        _served_hosts.add(h)
        threading.Thread(target=serve, args=(h,), daemon=True).start()
    # Tailscale may come up after the app (a reboot): keep looking, so the phone link never stays dead
    if not PC_ONLY:
        threading.Thread(target=_tailnet_watch, daemon=True).start()
    print("Comfy Studio  http://127.0.0.1:%d" % PORT, flush=True)
    if extra:
        print("Tailnet            http://%s:%d" % (extra[0], PORT), flush=True)
    if not PC_ONLY:
        print("PIN file           %s" % os.path.join(DATA, "PIN.txt"), flush=True)
    serve(HOSTS[0])


if __name__ == "__main__":
    main()