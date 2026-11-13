const ASPECTS = [
  { id: "1:1", label: "1:1", w: 1, h: 1 },
  { id: "4:3", label: "4:3", w: 4, h: 3 },
  { id: "3:4", label: "3:4", w: 3, h: 4 },
  { id: "3:2", label: "3:2", w: 3, h: 2 },
  { id: "2:3", label: "2:3", w: 2, h: 3 },
  { id: "16:9", label: "16:9", w: 16, h: 9 },
  { id: "9:16", label: "9:16", w: 9, h: 16 },
  { id: "21:9", label: "21:9", w: 21, h: 9 },
];
const PRESETS = {
  "4mp":  { "1:1": "2048x2048", "4:3": "2368x1760", "3:4": "1760x2368", "3:2": "2496x1664", "2:3": "1664x2496", "16:9": "2720x1536", "9:16": "1536x2720", "21:9": "3136x1344" },
  "16mp": { "1:1": "4096x4096", "4:3": "4736x3552", "3:4": "3552x4736", "3:2": "5024x3360", "2:3": "3360x5024", "16:9": "5472x3072", "9:16": "3072x5472", "21:9": "6272x2688" },
};
const MP_CHIPS = [2, 4, 8, 12, 16];
const ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/bmp,image/heic,image/heif,.heic,.heif";

const state = {
  mode: "t2i",
  quality: "4mp",
  aspect: "1:1",
  customMp: 4,
  customAspect: "1:1",
  p1Original: true,
  p1Mp: 8,
  mp: 4,
  _sizeInfo: null,
  peMode: "fast",
  autoAssist: true,
  refMode: false,
  roles: [],
  camera: {},
  images: [],
  jobId: null,
  poll: null,
  cfg: null,
  view: "create",
  libMode: "all",
  libFav: false,
  library: [],
  viewerIndex: 0,
  compareOn: false,
  pc: { open: false, tab: "recent", root: "", path: "", multi: true, selected: {}, items: [], dirs: [], crumbs: [], offset: 0, more: false, roots: [], q: "" },
  confirm: null,
};
/* Remember the tools between sessions (not prompts, pictures or anything already saved elsewhere). */
// Edit sizes (Match Picture 1 / Original / MP) and Reference mode are per-job choices and are NOT remembered: an edit
// must start at Picture 1's own size, or the model redraws the whole picture (a 0.5 MP photo made 8 MP = all new pixels).
const REMEMBER_STATE = ["mode", "quality", "aspect", "customMp", "customAspect", "mp", "peMode", "peTokens",
  "autoAssist", "camera"];
(function () {
  try {
    const s = JSON.parse(localStorage.getItem("cs.remember") || "null");
    if (s && s.state) REMEMBER_STATE.forEach((k) => { if (k in s.state && s.state[k] != null) state[k] = s.state[k]; });
  } catch (e) {}
})();
var _pcSearchTimer = null;
var _pcSearchSeq = 0;


const $ = (id) => document.getElementById(id);

function pyRound(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const n = Math.floor(ax);
  const frac = ax - n;
  let r;
  if (frac < 0.5) r = n;
  else if (frac > 0.5) r = n + 1;
  else r = (n % 2 === 0) ? n : n + 1;
  return sign * r;
}

function formatMp(mp) {
  const n = Math.round(Number(mp) * 2) / 2;
  const clamped = Math.min(20, Math.max(1, n));
  return (clamped % 1 === 0) ? String(clamped) : clamped.toFixed(1);
}

function vramHint(mp) {
  if (mp >= 16) return "16 MP is the model's preferred quality, but this is heavy on an 8 GB card.";
  if (mp > 8) return "Heavy on an 8 GB card.";
  if (mp < 4) return "The model works best from about 4 MP up; this is on the small side.";
  return "A comfortable size for an 8 GB card.";
}



async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = (data && data.error) || res.statusText || "Request failed";
    throw new Error(msg);
  }
  return data;
}

function fillSelect(id, values, preferred) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = "";
  (values || []).forEach((v) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    el.appendChild(o);
  });
  if (preferred && [...el.options].some((o) => o.value === preferred)) el.value = preferred;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  state.cfg = cfg;
  const d = cfg.defaults || {};
  const m = cfg.models || {};
  fillSelect("unet_name", m.unet_name, d.unet_name);
  fillSelect("clip_name", m.clip_name, d.clip_name);
  fillSelect("vae_name", m.vae_name, d.vae_name);
  fillSelect("pe_clip_name", m.pe_clip_name, d.pe_clip_name);
  fillSelect("sampler_name", m.sampler_name, d.sampler_name || "euler");
  // where the app is open decides where pictures come from: this PC browses its folders, other devices use
  // their own file picker (so a phone or someone else's computer never sees this PC's files by accident)
  document.body.classList.toggle("on-host", !!(state.cfg && state.cfg.on_host));
  fillSelect("scheduler", m.scheduler, d.scheduler || "simple");
  if ($("steps")) $("steps").value = cfg.default_steps || 6;
  if ($("cfg")) $("cfg").value = d.cfg ?? 1;
  if (tuneMode() === "manual" && (!state.engine || state.engine === "builtin" || state.engine === "none")) restoreManual("builtin");
  if ($("pe")) $("pe").checked = false; if (typeof syncPills === "function") syncPills();
  const hint = $("pe-hint");
  if (hint && cfg.fast_pe_has_small_model === false) {
    hint.textContent = "Fast is lighter on an 8 GB card and usually finishes in seconds. Quality is the full enhancer and is slower. Uses more VRAM and time when on. No smaller Qwen model was found in text_encoders, so Fast still uses the 9B model but with lighter settings.";
  }
}


const ASPECT_NAMES = {
  "1:1": "Square 1:1",
  "4:3": "Standard 4:3",
  "3:4": "Portrait 3:4",
  "3:2": "Photo 3:2",
  "2:3": "Portrait 2:3",
  "16:9": "Widescreen 16:9",
  "9:16": "Tall 9:16",
  "21:9": "Ultrawide 21:9",
};

function ratioIconHtml(aw, ah) {
  aw = Number(aw) || 1; ah = Number(ah) || 1;
  const boxW = 22, boxH = 16;
  const s = Math.min(boxW / aw, boxH / ah);
  const w = Math.max(5, Math.round(aw * s));
  const h = Math.max(5, Math.round(ah * s));
  return '<span class="res-ratio" aria-hidden="true"><i class="ratio" style="width:' + w + "px;height:" + h + 'px"></i></span>';
}

function picture1IconHtml() {
  const img = state.images[0];
  const url = img && (img.url || img.thumb || (img.name ? ("/api/input?name=" + encodeURIComponent(img.name)) : ""));
  if (url) return '<span class="res-thumb" aria-hidden="true"><img alt="" src="' + url + '"></span>';
  return '<span class="res-icon" aria-hidden="true"><span class="res-pic-icon"></span></span>';
}

function formatDims(w, h, mp) {
  if (!w || !h) return "";
  const m = (mp != null) ? mp : Math.round((w * h) / 1e4) / 100;
  return w + " \u00d7 " + h + " \u00b7 " + m + " MP";
}

function currentAssistSize() {
  const out = {};
  if (typeof currentResolution === "function") out.resolution = currentResolution();
  const info = state._sizeInfo;
  if (info && info.width && info.height) {
    out.width = info.width;
    out.height = info.height;
  }
  if (state.quality === "custom") out.aspect = state.customAspect || state.aspect || "1:1";
  else if (state.quality !== "picture1" && state.quality !== "keep" && state.quality !== "match") {
    out.aspect = state.aspect || "1:1";
  }
  return out;
}

function currentMp() {
  if (state.quality === "16mp") return 16;
  if (state.quality === "4mp") return 4;
  const raw = (state.mp != null) ? state.mp : (state.customMp != null ? state.customMp : (state.p1Mp != null ? state.p1Mp : 4));
  const n = Number(raw);
  return formatMp(isFinite(n) ? n : 4);
}

function currentResolution() {
  const mp = currentMp();
  if (state.quality === "picture1" || state.quality === "keep") {
    if (state.quality === "keep" || state.p1Original !== false) return "picture1:original";
    return "picture1:" + mp;
  }
  if (state.quality === "match") return "picture1:" + mp;
  const asp = (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
  return "custom:" + asp + ":" + mp;
}

function picture1Label() {
  const img = state.images[0];
  if (state.p1Original !== false) {
    if (img && img.width && img.height) return "Original (" + img.width + " \u00d7 " + img.height + ")";
    return "Original";
  }
  return "Match Picture 1 \u00b7 " + formatMp(state.p1Mp || 8) + " MP";
}

function triggerTitle() {
  if (state.quality === "picture1" || state.quality === "keep" || state.quality === "match") {
    return "Match Picture 1";
  }
  const asp = (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
  return ASPECT_NAMES[asp] || asp;
}

function triggerIconHtml() {
  if (state.quality === "picture1" || state.quality === "keep" || state.quality === "match") return picture1IconHtml();
  const asp = (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
  const a = ASPECTS.find((x) => x.id === asp) || ASPECTS[0];
  return ratioIconHtml(a.w, a.h);
}

let _sizeTimer = 0;
let _sizeReq = 0;

function picture1NeedsInput() {
  return (state.quality === "picture1" || state.quality === "keep" || state.quality === "match") &&
    !(state.images[0] && state.images[0].width && state.images[0].height);
}

function applyEmptyPicture1Labels() {
  if (!picture1NeedsInput()) return false;
  state._sizeInfo = null;
  if ($("res-trigger-meta")) $("res-trigger-meta").textContent = "Add Picture 1";
  if ($("p1-output")) $("p1-output").textContent = "Add Picture 1 to use Match Picture 1.";
  if ($("p1-hint")) {
    $("p1-hint").textContent = state.p1Original !== false
      ? "Original keeps Picture 1's exact pixels (snapped only to a multiple of 32 if needed, then scaled back)."
      : "Add Picture 1 so the aspect can be read, then this megapixel setting is applied.";
  }
  return true;
}

function scheduleOutputSize() {
  if (_sizeTimer) clearTimeout(_sizeTimer);
  applyEmptyPicture1Labels();
  _sizeTimer = setTimeout(refreshOutputSize, 80);
  renderResTrigger();
  renderP1Dial();
  renderCustomMp();
}

async function refreshOutputSize() {
  if (applyEmptyPicture1Labels()) {
    renderResTrigger();
    return null;
  }
  const params = new URLSearchParams({
    res: currentResolution(),
    mode: state.mode === "edit" ? "edit" : "t2i",
  });
  const img = state.images[0];
  if (img && img.width && img.height) {
    params.set("width", String(img.width));
    params.set("height", String(img.height));
  }
  const req = ++_sizeReq;
  try {
    const info = await api("/api/size?" + params.toString());
    if (req !== _sizeReq) return null;
    state._sizeInfo = info;
    const meta = (info && info.width) ? formatDims(info.width, info.height, info.mp) : "";
    if ($("res-trigger-meta")) $("res-trigger-meta").textContent = meta || (state.quality === "picture1" && !img ? "Add Picture 1" : "");
    const out = $("p1-output");
    if (out) {
      if (state.quality === "picture1" && !img) out.textContent = "Add Picture 1 to use Match Picture 1.";
      else if (info && info.original && info.restore) {
        out.textContent = "Output: " + info.width + " \u00d7 " + info.height + " (" + info.mp + " MP) \u2014 exact Picture 1 size.";
      } else if (info && info.width) {
        out.textContent = "Output: " + info.width + " \u00d7 " + info.height + " (" + info.mp + " MP)";
      } else out.textContent = "Output: \u2014";
    }
    if (typeof window.__sizeWarn === "function") window.__sizeWarn(info, img);
    const hint = $("p1-hint");
    if (hint) {
      const refMp = (info && info.ref_megapixels) ? (Math.round(info.ref_megapixels * 100) / 100) : null;
      const refNote = (state.mode === "edit")
        ? (refMp
          ? (" Every input image is scaled to " + refMp + " MP, keeping its own aspect, sides in multiples of 32.")
          : " Every input image is scaled to the same megapixels, keeping its own aspect.")
        : "";
      if (!img && (state.quality === "picture1" || state.quality === "keep") && state.p1Original !== false) {
        hint.textContent = "Original keeps Picture 1's exact pixels (snapped only to a multiple of 32 if needed, then scaled back). Other inputs are scaled to Picture 1's megapixels.";
      } else if (info && info.original) {
        const heavy = info.mp && info.mp > 8;
        hint.textContent = (heavy
          ? ("Original is " + info.mp + " MP. On an 8 GB card, 8 MP is usually a comfortable size if this is too heavy.")
          : "Original keeps Picture 1's exact pixel size.")
          + " Other inputs are scaled to Picture 1's megapixels so detail matches.";
      } else if (info && info.mp) {
        hint.textContent = vramHint(info.mp) + " Output aspect at this megapixel count, sides snapped to multiples of 32." + refNote;
      } else hint.textContent = "";
    }
    if ($("mp-size") && state.quality === "custom") {
      if (info && info.width) $("mp-size").textContent = info.width + " \u00d7 " + info.height + "  (" + info.mp + " MP)";
    }
    if ($("mp-hint") && state.quality === "custom") {
      $("mp-hint").textContent = (info && info.mp ? vramHint(info.mp) + " " : "") + "Width and height are the closest multiples of 32 to that area, chosen to keep the aspect accurate.";
    }
    renderResTrigger();
    if (typeof scheduleAssistPreview === "function") scheduleAssistPreview();
    return info;
  } catch (e) {
    if (req !== _sizeReq) return null;
    if (applyEmptyPicture1Labels()) renderResTrigger();
    return null;
  }
}

function renderResTrigger() {
  if ($("res-trigger-label")) $("res-trigger-label").textContent = triggerTitle();
  if ($("res-trigger-icon")) $("res-trigger-icon").innerHTML = triggerIconHtml();
  const info = state._sizeInfo;
  if ($("res-trigger-meta") && info && info.width) {
    $("res-trigger-meta").textContent = formatDims(info.width, info.height, info.mp);
  }
}

function renderP1Dial() {
  const box = $("p1-dial");
  if (!box) return;
  box.classList.remove("hidden");
  const isP1 = state.quality === "picture1" || state.quality === "keep" || state.quality === "match";
  const orig = !!(isP1 && state.mode === "edit" && state.p1Original !== false);
  const origBtn = $("p1-original");
  if (origBtn) {
    origBtn.classList.toggle("hidden", state.mode !== "edit");
    origBtn.classList.toggle("is-on", orig);
    origBtn.setAttribute("aria-pressed", orig ? "true" : "false");
    const img = state.images[0];
    origBtn.textContent = (img && img.width && img.height)
      ? ("Original (" + img.width + " \u00d7 " + img.height + ")")
      : "Original";
  }
  let displayMp = currentMp();
  if (orig && state.images[0] && state.images[0].width && state.images[0].height) {
    const actual = (state.images[0].width * state.images[0].height) / 1e6;
    displayMp = Math.round(actual * 100) / 100;
  }
  if ($("p1-mp-num")) {
    $("p1-mp-num").value = displayMp;
    $("p1-mp-num").disabled = orig;
  }
  if ($("p1-mp-slider")) {
    const sliderVal = Math.min(20, Math.max(1, Math.round(Number(displayMp) * 2) / 2));
    $("p1-mp-slider").value = sliderVal;
    $("p1-mp-slider").disabled = orig;
  }
  document.querySelectorAll("#p1-mp-chips button").forEach((b) => {
    b.classList.toggle("is-on", !orig && Number(b.dataset.p1mp) === Number(currentMp()));
    // Chips stay enabled so tapping one leaves Original and sets that MP.
    b.disabled = false;
  });
}

function setP1Original(on) {
  if (on == null) on = !(state.p1Original !== false && (state.quality === "picture1" || state.quality === "keep"));
  state.quality = "picture1";
  state.p1Original = !!on;
  if (!state.p1Original && state.images[0] && state.images[0].width && state.images[0].height) {
    const actual = (state.images[0].width * state.images[0].height) / 1e6;
    if (state.mp == null && state.p1Mp == null) {
      state.mp = Math.min(20, Math.max(1, Math.round(actual * 2) / 2));
      state.p1Mp = state.mp;
      state.customMp = state.mp;
    }
  }
  scheduleOutputSize();
  renderResMenu();
  renderP1Dial();
}

function setMp(value) {
  let n = Math.round(Number(value) * 2) / 2;
  if (!isFinite(n)) n = 4;
  n = Math.min(20, Math.max(1, n));
  state.mp = n;
  state.customMp = n;
  state.p1Mp = n;
  state.p1Original = false;
  if (state.quality === "4mp" || state.quality === "16mp") {
    state.quality = "custom";
    if (!state.customAspect) state.customAspect = state.aspect || "1:1";
  }
  scheduleOutputSize();
  renderResMenu();
  renderP1Dial();
}

function setP1Mp(value) {
  setMp(value);
}

function closeResMenu() {
  const menu = $("res-menu");
  const bd = $("res-backdrop");
  const btn = $("res-trigger");
  if (menu) { menu.classList.add("hidden"); menu.hidden = true; }
  if (bd) { bd.classList.add("hidden"); bd.hidden = true; }
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function openResMenu() {
  renderResMenu();
  const menu = $("res-menu");
  const bd = $("res-backdrop");
  const btn = $("res-trigger");
  if (menu) { menu.classList.remove("hidden"); menu.hidden = false; }
  if (bd) { bd.classList.remove("hidden"); bd.hidden = false; }
  if (btn) btn.setAttribute("aria-expanded", "true");
  const on = menu && menu.querySelector(".res-option.is-on");
  if (on) on.focus();
}

function toggleResMenu() {
  const menu = $("res-menu");
  if (menu && !menu.classList.contains("hidden")) closeResMenu();
  else openResMenu();
}

function renderResMenu() {
  const body = $("res-menu-body");
  if (!body) return;
  body.innerHTML = "";
  const addGroup = (label) => {
    const g = document.createElement("div");
    g.className = "res-group-label";
    g.textContent = label;
    body.appendChild(g);
  };
  const addOption = (opts) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "res-option" + (opts.on ? " is-on" : "");
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", opts.on ? "true" : "false");
    b.innerHTML = (opts.icon || "") + '<span class="res-option-copy"><strong></strong><span class="res-option-meta"></span></span>';
    b.querySelector(".res-option-copy > strong").textContent = opts.label;
    const metaEl = b.querySelector(".res-option-copy > .res-option-meta");
    if (metaEl) metaEl.textContent = opts.meta || "";
    b.addEventListener("click", (e) => { e.stopPropagation(); opts.pick(); closeResMenu(); scheduleOutputSize(); });
    body.appendChild(b);
    return b;
  };

  const mp = currentMp();
  if (state.mode === "edit") {
    addGroup("Match Picture 1");
    const img = state.images[0];
    const origOn = state.quality === "picture1" && state.p1Original !== false;
    const origMeta = img && img.width && img.height
      ? ("Original \u2014 " + formatDims(img.width, img.height))
      : "Exact pixels of Picture 1";
    addOption({
      icon: picture1IconHtml(),
      label: "Match Picture 1",
      meta: origOn ? origMeta : ("Same aspect as Picture 1 \u00b7 " + mp + " MP"),
      on: state.quality === "picture1" || state.quality === "keep" || state.quality === "match",
      pick: () => {
        // Keep the current MP; user taps Original for exact pixels.
        state.p1Original = false;
        setQuality("picture1");
      },
    });
  }

  addGroup("Aspect ratio");
  ASPECTS.forEach((a) => {
    const asp = (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
    const on = state.quality !== "picture1" && state.quality !== "keep" && state.quality !== "match" && asp === a.id;
    addOption({
      icon: ratioIconHtml(a.w, a.h),
      label: ASPECT_NAMES[a.id] || a.label,
      meta: mp + " MP \u2014 sides snap to multiples of 32",
      on: on,
      pick: () => {
        state.aspect = a.id;
        state.customAspect = a.id;
        state.p1Original = false;
        if (state.quality === "4mp" || state.quality === "16mp" || state.quality === "picture1" || state.quality === "keep" || state.quality === "match") {
          state.quality = "custom";
        }
        setQuality(state.quality === "custom" ? "custom" : "custom");
      },
    });
  });
}

function renderAspects() {
  const box = $("custom-aspects") || $("aspects");
  if (!box) return;
  box.innerHTML = "";
  const list = ASPECTS.map((a) => Object.assign({}, a));
  const current = state.customAspect || state.aspect;
  list.forEach((a) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "aspect" + (current === a.id ? " is-on" : "");
    b.dataset.aspect = a.id;
    const icon = document.createElement("i");
    icon.className = "icon";
    const scale = 22 / Math.max(a.w, a.h);
    icon.style.width = Math.max(8, a.w * scale) + "px";
    icon.style.height = Math.max(8, a.h * scale) + "px";
    const lab = document.createElement("span");
    lab.textContent = a.label;
    b.append(icon, lab);
    b.addEventListener("click", () => {
      state.customAspect = a.id;
      state.aspect = a.id;
      renderAspects();
      scheduleOutputSize();
    });
    box.appendChild(b);
  });
}

function renderCustomMp() {
  const panel = $("custom-mp");
  if (panel) panel.classList.add("hidden");
}

function renderResNote() {
  const note = $("res-note");
  if (!note) return;
  note.textContent = "";
  note.classList.add("hidden");
}

function setCustomMp(value) {
  setMp(value);
}

function setQuality(q) {
  if (q === "keep" || q === "match") q = "picture1";
  state.quality = q;
  if (q === "custom" && !state.customAspect) state.customAspect = state.aspect || "1:1";
  renderP1Dial();
  renderCustomMp();
  renderResTrigger();
  renderResMenu();
  scheduleOutputSize();
  if (typeof scheduleAssistPreview === "function") scheduleAssistPreview();
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => {
    const on = b.dataset.mode === mode;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("images").classList.toggle("hidden", mode !== "edit");
  if ($("assist-toggle-wrap")) $("assist-toggle-wrap").classList.toggle("hidden", mode !== "edit");
  if ($("assist-hint")) $("assist-hint").classList.toggle("hidden", mode !== "edit");
  $("prompt-hint").classList.toggle("hidden", mode !== "edit");
  $("prompt").placeholder = mode === "edit"
    ? (state.refMode
      ? "Describe a new scene. All pictures are references. Tag one Background, or name it in the prompt, if you want that image as the environment."
      : "Describe the edit. You can write Image 1, img1, @Image2, or <Picture 1> — they become <Picture N> when that slot has an image.")
    : "Describe the image you want...";
  if (typeof scheduleAssistPreview === "function") scheduleAssistPreview();
  if (mode !== "edit" && (state.quality === "picture1" || state.quality === "match" || state.quality === "keep")) setQuality("4mp");
  if (mode !== "edit" && state.quality === "custom" && (state.customAspect === "match" || state.customAspect === "picture1")) {
    state.customAspect = state.aspect || "1:1";
  }
  if (typeof closeResMenu === "function") closeResMenu();
  if (typeof renderResMenu === "function") renderResMenu();
  if (typeof scheduleOutputSize === "function") scheduleOutputSize();
  else {
    renderAspects();
    renderCustomMp();
    renderResNote();
  }
  renderSlots();
  renderAliasPreview();
  hideAtPicker();
}

function setView(view) {
  state.view = view;
  $("view-create").classList.toggle("hidden", view !== "create");
  $("view-library").classList.toggle("hidden", view !== "library");
  document.querySelectorAll(".views button").forEach((b) => b.classList.toggle("is-on", b.dataset.view === view));
  if (view === "library") refreshLibrary();
}

function insertAtCursor(textarea, text, replaceFrom) {
  const start = replaceFrom != null ? replaceFrom : (textarea.selectionStart ?? textarea.value.length);
  const end = textarea.selectionEnd ?? start;
  const before = textarea.value.slice(0, start);
  const after = textarea.value.slice(end);
  const padL = before && !/\s$/.test(before) ? " " : "";
  const padR = after && !/^\s/.test(after) ? " " : "";
  textarea.value = before + padL + text + padR + after;
  const pos = (before + padL + text + padR).length;
  textarea.focus();
  textarea.setSelectionRange(pos, pos);
  renderAliasPreview();
}

function slotsLeft() {
  return Math.max(0, 16 - state.images.length);
}

function renderSlots(opts) {
  const box = $("slots");
  box.innerHTML = "";
  if (typeof syncRoles === "function") syncRoles();
  const dragging = opts && typeof opts.dragging === "number" ? opts.dragging : -1;
  state.images.forEach((item, i) => {
    const el = document.createElement("div");
    el.className = "slot" + (i === dragging ? " is-drag" : "");
    el.dataset.i = String(i);
    el.title = "Picture " + (i + 1) + (i === 0 ? " — the image being edited. " : " — ") + "Drag or long-press to reorder. Prompt references follow this number.";
    const thumb = document.createElement("span");
    thumb.className = "thumb";
    const img = document.createElement("img");
    img.alt = "Picture " + (i + 1);
    img.src = item.url;
    thumb.appendChild(img);
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = "Picture " + (i + 1);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "x";
    x.setAttribute("aria-label", "Remove Picture " + (i + 1));
    x.textContent = "x";
    x.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.images.splice(i, 1);
      if (state.roles) state.roles.splice(i, 1);
      renderSlots();
      renderAliasPreview();
      renderCustomMp();
    });
    const isBase = (state.mode === "edit" && !state.refMode && i === 0);
    if (state.mode === "edit") {
      if (isBase) {
        const base = document.createElement("span");
        base.className = "role-chip";
        base.textContent = "Base";
        base.title = "Picture 1 is the scene or target being edited";
        el.append(thumb, tag, base, x);
      } else {
        const sel = document.createElement("select");
        sel.className = "role-chip";
        sel.setAttribute("aria-label", "Role for Picture " + (i + 1));
        const cur = (state.roles && state.roles[i]) || "auto";
        (typeof roleOptions === "function" ? roleOptions() : [["auto","Auto"]]).forEach((pair) => {
          const o = document.createElement("option");
          o.value = pair[0];
          o.textContent = pair[1];
          if (pair[0] === cur) o.selected = true;
          sel.appendChild(o);
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
        sel.addEventListener("pointerdown", (e) => e.stopPropagation());
        sel.addEventListener("change", () => {
          if (!state.roles) state.roles = [];
          state.roles[i] = sel.value;
          if (typeof scheduleAssistPreview === "function") scheduleAssistPreview();
        });
        el.append(thumb, tag, sel, x);
      }
    } else {
      el.append(thumb, tag, x);
    }
    bindSlotPointer(el, i);
    box.appendChild(el);
  });
  if (state.images.length < 16) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "slot-add";
    add.id = "slot-add";
    add.innerHTML = "<strong>+</strong><span>Add image</span>";
    add.addEventListener("click", (e) => { e.stopPropagation(); openAddMenu(add); });
    box.appendChild(add);
  }
  $("clear-all").classList.toggle("hidden", state.images.length === 0);
  if (typeof scheduleOutputSize === "function") scheduleOutputSize();
  else renderResNote();
}

function bindSlotPointer(el, index) {
  el.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".x") || e.target.closest(".role-chip")) return;
    if (e.button && e.button !== 0) return;
    const start = { x: e.clientX, y: e.clientY, i: index, started: false, timer: null };
    const begin = () => {
      if (start.started) return;
      start.started = true;
      el.classList.add("is-drag");
      try { el.setPointerCapture(e.pointerId); } catch {}
    };
    start.timer = setTimeout(begin, 280);
    const move = (ev) => {
      if (!start.started) {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 8) {
          clearTimeout(start.timer);
          begin();
        } else return;
      }
      ev.preventDefault();
      const tiles = [...document.querySelectorAll("#slots .slot")];
      const over = tiles.find((t) => {
        const r = t.getBoundingClientRect();
        return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
      });
      if (!over) return;
      const to = Number(over.dataset.i);
      if (to === start.i) return;
      const item = state.images.splice(start.i, 1)[0];
      state.images.splice(to, 0, item);
      if (state.roles && state.roles.length) {
        const role = state.roles.splice(start.i, 1)[0];
        state.roles.splice(to, 0, role);
      }
      start.i = to;
      const box = $("slots");
      const current = tiles[tiles.findIndex((t) => t === el)] || el;
      if (to > start.i) {}
      if (over !== current) {
        const all = [...box.querySelectorAll(".slot")];
        const fromEl = all.find((t) => t === el) || el;
        if (fromEl.compareDocumentPosition(over) & Node.DOCUMENT_POSITION_FOLLOWING) {
          box.insertBefore(fromEl, over.nextSibling);
        } else {
          box.insertBefore(fromEl, over);
        }
      }
      [...box.querySelectorAll(".slot")].forEach((t, i) => {
        t.dataset.i = String(i);
        const tag = t.querySelector(".tag");
        if (tag) tag.textContent = "Picture " + (i + 1);
        const img = t.querySelector("img");
        if (img) img.alt = "Picture " + (i + 1);
        const btn = t.querySelector(".x");
        if (btn) btn.setAttribute("aria-label", "Remove Picture " + (i + 1));
      });
    };
    const up = () => {
      clearTimeout(start.timer);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      if (start.started) {
        renderSlots();
        renderAliasPreview();
        renderCustomMp();
      }
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    window.addEventListener("pointercancel", up);
  });
}

function openAddMenu(anchor) {
  const menu = $("add-menu");
  menu.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  menu.style.top = (r.bottom + 6) + "px";
  menu.style.left = r.left + "px";
  requestAnimationFrame(() => {
    const m = menu.getBoundingClientRect();
    if (m.right > window.innerWidth - 8) menu.style.left = Math.max(8, window.innerWidth - m.width - 8) + "px";
    if (m.bottom > window.innerHeight - 8) menu.style.top = Math.max(8, r.top - m.height - 6) + "px";
  });
}

function closeAddMenu() { $("add-menu").classList.add("hidden"); }

const ALIAS_RE = /<\s*Picture\s*(1[0-6]|[1-9])\s*>|@\s*Picture[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])|@\s*Image[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])|@\s*img[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])|(?<![A-Za-z])Picture[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])|(?<![A-Za-z])Image[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])|(?<![A-Za-z])img[\s_]*(1[0-6]|[1-9])(?![0-9A-Za-z])/gi;

function normalizeAliases(text, loaded) {
  const set = new Set((loaded || []).map(Number));
  const refs = [];
  const warnings = [];
  const seen = new Set();
  const out = [];
  let pos = 0;
  const src = text || "";
  const re = new RegExp(ALIAS_RE.source, "gi");
  let m;
  while ((m = re.exec(src))) {
    let n = null;
    for (let g = 1; g < m.length; g++) if (m[g]) { n = Number(m[g]); break; }
    if (!n) continue;
    out.push(src.slice(pos, m.index));
    const raw = m[0];
    const ok = set.has(n);
    out.push(ok ? ("<Picture " + n + ">") : raw);
    refs.push({ n, raw, ok, canonical: "<Picture " + n + ">" });
    if (!ok && !seen.has(n)) warnings.push("Image " + n + " has no image");
    seen.add(n);
    pos = m.index + raw.length;
  }
  out.push(src.slice(pos));
  return { text: out.join(""), refs, warnings };
}

function loadedSlots() {
  return state.images.map((_, i) => i + 1);
}

function renderAliasPreview() {
  const box = $("alias-preview");
  if (state.mode !== "edit") {
    box.classList.add("hidden");
    return;
  }
  const info = normalizeAliases($("prompt").value, loadedSlots());
  const chips = $("alias-chips");
  const warn = $("alias-warn");
  const norm = $("alias-norm");
  chips.innerHTML = "";
  warn.innerHTML = "";
  const seen = new Set();
  info.refs.forEach((r) => {
    if (seen.has(r.n)) return;
    seen.add(r.n);
    const el = document.createElement("span");
    el.className = "alias-chip" + (r.ok ? "" : " warn");
    if (r.ok && state.images[r.n - 1]) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = state.images[r.n - 1].url;
      el.appendChild(img);
    }
    const lab = document.createElement("span");
    lab.textContent = r.ok ? r.canonical : ("Image " + r.n);
    el.appendChild(lab);
    chips.appendChild(el);
  });
  info.warnings.forEach((w) => {
    const el = document.createElement("span");
    el.className = "warn-chip";
    el.textContent = w;
    warn.appendChild(el);
  });
  const typed = $("prompt").value;
  if (info.text !== typed && info.refs.some((r) => r.ok)) {
    norm.classList.remove("hidden");
    norm.textContent = "Will be sent as: " + info.text;
  } else {
    norm.classList.add("hidden");
    norm.textContent = "";
  }
  box.classList.toggle("hidden", !info.refs.length && !info.warnings.length);
}

function atToken(textarea) {
  const pos = textarea.selectionStart ?? 0;
  const before = textarea.value.slice(0, pos);
  const m = /(^|[^A-Za-z0-9_])@([A-Za-z0-9_ ]*)$/.exec(before);
  if (!m) return null;
  const typed = m[2];
  if (typed && !/^(img|image|picture|pic|p)?[\s_]*\d{0,2}$/i.test(typed)) return null;
  if (/^(img|image|picture)[\s_]*\d+$/i.test(typed)) return null;
  return { start: m.index + m[1].length, typed };
}

function showAtPicker() {
  const ta = $("prompt");
  const tok = atToken(ta);
  const box = $("at-picker");
  if (state.mode !== "edit" || !tok || !state.images.length) {
    box.classList.add("hidden");
    return;
  }
  const q = (tok.typed || "").toLowerCase();
  box.innerHTML = "";
  state.images.forEach((item, i) => {
    const n = i + 1;
    const label = "Picture " + n;
    if (q && !("picture " + n).includes(q) && !("image " + n).includes(q) && !String(n).startsWith(q.replace(/\D/g, "") || q)) {
      /* still show all if the typed prefix is just @image / @pic */
    }
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "option");
    const img = document.createElement("img");
    img.alt = "";
    img.src = item.url;
    const span = document.createElement("span");
    span.textContent = label;
    b.append(img, span);
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", () => {
      insertAtCursor(ta, "<Picture " + n + ">", tok.start);
      hideAtPicker();
    });
    box.appendChild(b);
  });
  box.classList.toggle("hidden", !box.childElementCount);
}

function hideAtPicker() { $("at-picker").classList.add("hidden"); }

function prefetchAppearance(name) {
  if (!name) return;
  fetch("/api/appearance/prefetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }).catch(function () {});
}
function prefetchAppearanceAll() {
  const names = (state.images || []).map(function (r) { return r && r.name; }).filter(Boolean);
  if (!names.length) return;
  fetch("/api/appearance/prefetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  }).catch(function () {});
}

async function uploadFile(file) {
  const res = await fetch("/api/upload", {
    method: "POST",
    headers: { "X-Filename": encodeURIComponent(file.name || "upload.png") },
    body: file,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Upload failed");
  return data;
}

async function addFiles(files) {
  const list = [...(files || [])];
  for (const f of list) {
    if (state.images.length >= 16) break;
    try {
      const rec = await uploadFile(f);
      state.images.push(rec);
      prefetchAppearance(rec && rec.name);
    } catch (err) { showError(err.message); }
  }
  renderSlots();
  renderAliasPreview();
  if (typeof scheduleOutputSize === "function") scheduleOutputSize();
  else renderCustomMp();
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const el = $("status");
    if (!s.comfy) { el.dataset.state = "down"; el.textContent = "ComfyUI is offline"; }
    else if (s.busy) { el.dataset.state = "busy"; el.textContent = "ComfyUI is busy"; }
    else { el.dataset.state = "up"; el.textContent = "ComfyUI connected"; }
  } catch {
    $("status").dataset.state = "down";
    $("status").textContent = "App unreachable";
  }
}

function setSplitEls(clip, img, wrap, pct) {
  if (!clip) return;
  clip.style.width = pct + "%";
  if (wrap && img) img.style.width = wrap.clientWidth + "px";
}

function setSplit(pct) {
  const clip = $("before-clip");
  if (!clip) return;
  setSplitEls(clip, $("before"), $("compare"), pct);
}
function setViewerSplit(pct) { setSplitEls($("viewer-before-clip"), $("viewer-before"), $("viewer-compare"), pct); }

function setResultAspect(img) {
  if (!img) return;
  const apply = () => {
    if (img.naturalWidth && img.naturalHeight) {
      img.style.aspectRatio = img.naturalWidth + " / " + img.naturalHeight;
      const frame = img.closest(".result-frame, .compare-stack, .compare-hold");
      if (frame) frame.style.aspectRatio = img.naturalWidth + " / " + img.naturalHeight;
    }
  };
  if (img.complete && img.naturalWidth) apply();
  else img.addEventListener("load", apply, { once: true });
}

function showResult(job) {
  const stage = $("stage");
  stage.classList.remove("hidden", "is-empty");
  $("progress-wrap").classList.add("hidden");
  $("result").classList.remove("hidden");
  $("error").classList.add("hidden");
  if ($("live-frame")) $("live-frame").classList.add("hidden");
  const img = (job.images && job.images[0]) || null;
  if (!img) return;
  const url = "/api/image?id=" + encodeURIComponent(job.id) + "&t=" + Date.now();
  $("dl").href = "/api/download?id=" + encodeURIComponent(job.id);
  $("dl").setAttribute("download", img.filename || "result.png");
  const single = $("single");
  single.classList.remove("hidden");
  single.src = url;
  setResultAspect(single);
  const canCompare = job.mode === "edit" && (job.has_before || (state.images && state.images[0]) || (job.source_images && job.source_images.length) || (job.input_snapshots && job.input_snapshots.length));
  if ($("result-compare")) $("result-compare").classList.toggle("hidden", !canCompare);
  const cmp = $("compare");
  if (cmp) {
    cmp.classList.add("hidden");
    cmp.classList.remove("is-hold", "is-side");
  }
  if ($("result-frame")) $("result-frame").classList.remove("hidden");
  state.compareOn = false;
  if (canCompare) {
    $("after").src = url;
    setResultAspect($("after"));
    const beforeSrc = (state.images && state.images[0] && state.images[0].url)
      || job.before_url
      || (job.id ? ("/api/source?id=" + encodeURIComponent(job.id) + "&slot=1") : "");
    if (beforeSrc) {
      $("before").src = beforeSrc;
      setResultAspect($("before"));
    }
  }
  const box = $("enhanced-box");
  if (box) {
    const text = job.enhanced_prompt || "";
    box.classList.toggle("hidden", !text);
    if (text) {
      $("enhanced-text").textContent = text;
      $("enhanced-note").textContent = job.pe_cached ? "Used a cached enhancement for this prompt and images." : (job.pe_fallback ? "The enhancer did not return usable JSON, so your original prompt was used." : (job.pe_mode === "quality" ? "Quality" : "Fast"));
    }
  }
  state.reuseItem = jobToReuseItem(job);
  if (typeof renderSentPrompt === "function") renderSentPrompt("sent-prompt-box", "sent-prompt-text", "sent-prompt-note", job);
  renderReuse("reuse-box", "reuse-actions", state.reuseItem);
  refreshRecent();
}

function showError(msg) {
  $("stage").classList.remove("hidden", "is-empty");
  $("progress-wrap").classList.add("hidden");
  $("result").classList.add("hidden");
  $("error").classList.remove("hidden");
  $("error").textContent = msg;
  setView("create");
}

function showProgress(job) {
  $("stage").classList.remove("hidden", "is-empty");
  $("progress-wrap").classList.remove("hidden");
  $("result").classList.add("hidden");
  $("error").classList.add("hidden");
  const max = job.progress_max || 0;
  const v = job.progress || 0;
  $("bar").style.width = max ? Math.min(100, (v / max) * 100) + "%" : (job.status === "running" ? "15%" : "5%");
  const step = $("progress-step");
  if (max) {
    $("progress-text").textContent = job.message || "Generating…";
    if (step) {
      step.textContent = v + " / " + max;
      step.classList.remove("hidden");
    }
  } else {
    $("progress-text").textContent = job.message || "Working...";
    if (step) step.classList.add("hidden");
  }
  const liveFrame = $("live-frame");
  if (job.has_preview) {
    if (liveFrame) liveFrame.classList.remove("hidden");
    $("live").classList.remove("hidden");
    $("live").src = "/api/live?id=" + encodeURIComponent(job.id) + "&t=" + Date.now();
    setResultAspect($("live"));
  }
}


async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api("/api/job?id=" + encodeURIComponent(state.jobId));
    if (job.status === "queued" || job.status === "waiting_comfy" || job.status === "running") {
      showProgress(job);
      $("cancel").classList.remove("hidden");
      /* generate stays enabled */
      return;
    }
    clearInterval(state.poll);
    state.poll = null;
    $("cancel").classList.add("hidden");
    /* generate stays enabled */
    if (job.status === "success") {
      $("stage").classList.remove("hidden");
      showResult(job);
      refreshLibrary();
    } else if (job.status === "cancelled") {
      $("progress-wrap").classList.add("hidden");
      $("go-note").textContent = "Cancelled.";
    } else {
      showError(job.error || "The run failed.");
    }
  } catch (e) {
    showError(e.message);
  }
}

async function generate() {
  $("go-note").textContent = "";
  $("error").classList.add("hidden");
  let prompt = $("prompt").value.trim();
  if (!prompt) { showError("Write a prompt first."); return; }
  if (state.mode === "edit" && !state.images.length) {
    showError(state.refMode ? "Add at least one reference image first." : "Add a main image (Picture 1) first.");
    return;
  }
  if (state.mode === "edit") {
    const info = normalizeAliases($("prompt").value, loadedSlots());
    prompt = info.text.trim();
  }
  if ((state.quality === "picture1" || state.quality === "keep" || state.quality === "match") && !state.images[0]) {
    showError("Add Picture 1 to use Match Picture 1.");
    return;
  }
  const body = {
    mode: state.mode,
    prompt,
    resolution: currentResolution(),
    pe: $("pe").checked,      /* new pictures and edits (the server skips it for inpaint) */
    pe_mode: state.peMode || "fast",
    pe_tokens: state.peTokens || null,
    keep_clothing: !$("keep-clothing") || $("keep-clothing").checked,
    keep_quality: !$("keep-quality") || $("keep-quality").checked,
    tune_mode: tuneMode(),
    understanding: !$("auto-assist") || $("auto-assist").checked,
    random_seed: $("random-seed").checked,
    seed: $("random-seed").checked ? null : Number($("seed").value || 0),
    steps: Number($("steps").value || 6),
    sampler_name: $("sampler_name").value,
    scheduler: $("scheduler").value,
    cfg: Number($("cfg").value || 1),
    unet_name: $("unet_name").value,
    clip_name: $("clip_name").value,
    vae_name: $("vae_name").value,
    pe_clip_name: $("pe_clip_name").value,
    auto_assist: !!(state.mode === "edit" && (!($("auto-assist")) || $("auto-assist").checked)),
    ref_mode: !!(state.mode === "edit" && state.refMode),
    image_roles: (state.roles || []).slice(),
    camera: Object.assign({}, state.camera || {}),
  };
  if (state.mode === "edit") body.images = state.images.map((r) => r.name);
  if (typeof window.__currentLoras === "function") body.loras = window.__currentLoras();
  if (state.mode === "edit" && state.inpaint && state.images[0] && state.inpaint.forName === state.images[0].name) {
    body.inpaint_mask = state.inpaint.mask;
    body.inpaint_areas = state.inpaint.areas || [];
    body.inpaint_explicit = !!state.inpaint.explicit;
    body.resolution = "picture1:original";
  }
  try {
    const res = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (window.__loraSent) window.__loraSent();
    if (typeof enqueueJob === "function") enqueueJob(res.job);
    else {
      state.jobId = res.job.id;
      $("stage").classList.remove("hidden");
      showProgress(res.job);
      $("cancel").classList.remove("hidden");
      if (state.poll) clearInterval(state.poll);
      state.poll = setInterval(pollJob, 400);
      pollJob();
    }
  } catch (e) {
    showError(e.message);
  }
}

/* ---------- From PC picker ---------- */
function pcKey(it) { return (it.root || "") + "::" + (it.path || ""); }

function openPcPicker() {
  const pc = state.pc;
  pc.open = true;
  pc.multi = true;
  pc.selected = {};
  pc.tab = "recent";
  pc.path = "";
  pc.offset = 0;
  $("pc-sheet").classList.remove("hidden");
  $("pc-confirm").classList.remove("hidden");
  $("pc-confirm").textContent = "Add";
  $("pc-title").textContent = "Add images from this PC";
  document.querySelectorAll("#pc-tabs button").forEach((b) => b.classList.toggle("is-on", b.dataset.tab === "recent"));
  loadPcRoots().then(() => loadPcTab());
}

function closePcPicker() {
  state.pc.open = false;
  $("pc-sheet").classList.add("hidden");
  closePcPreview();
}


function dedupePcRoots(roots) {
  const out = [];
  const seenId = new Set();
  const seenPath = new Set();
  (roots || []).forEach((r) => {
    const id = String((r && r.id) || "");
    const path = String((r && r.path) || "").toLowerCase();
    if (id && seenId.has(id)) return;
    if (path && seenPath.has(path)) return;
    if (id) seenId.add(id);
    if (path) seenPath.add(path);
    out.push(r);
  });
  return out;
}

function addPcChip(box, r) {
  if (!box || !r) return;
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = r.name;
  b.dataset.root = r.id;
  if (r.letter) b.dataset.letter = r.letter;
  b.setAttribute("aria-pressed", r.id === state.pc.root ? "true" : "false");
  if (r.id === state.pc.root) b.classList.add("is-on");
  b.addEventListener("click", () => {
    state.pc.root = r.id;
    state.pc.path = "";
    state.pc.hereFiles = [];
    if (state.pc.tab !== "search") {
      state.pc.tab = "browse";
      document.querySelectorAll("#pc-tabs button").forEach((x) => x.classList.toggle("is-on", x.dataset.tab === "browse"));
    }
    loadPcTab(true);
  });
  box.appendChild(b);
}

function highlightPcChips() {
  document.querySelectorAll("#pc-drives button, #pc-roots button").forEach((b) => {
    const on = b.dataset.root === state.pc.root && state.pc.tab !== "recent";
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

async function loadPcRoots() {
  const data = await api("/api/pc/roots");
  const shortcuts = dedupePcRoots(data.shortcuts || []);
  const drives = dedupePcRoots(data.drives || []);
  const combined = dedupePcRoots((shortcuts.length || drives.length) ? shortcuts.concat(drives) : (data.roots || []));
  state.pc.roots = combined;
  state.pc.shortcuts = shortcuts.length ? shortcuts : combined.filter((r) => r.kind !== "drive");
  state.pc.drives = drives.length ? drives : combined.filter((r) => r.kind === "drive");
  const driveBox = $("pc-drives");
  const folderBox = $("pc-roots");
  if (driveBox) {
    driveBox.innerHTML = "";
    state.pc.drives.forEach((r) => addPcChip(driveBox, r));
  }
  if (folderBox) {
    folderBox.innerHTML = "";
    (driveBox ? state.pc.shortcuts : combined).forEach((r) => addPcChip(folderBox, r));
  }
  const driveLab = $("pc-drives-label");
  const folderLab = $("pc-folders-label");
  if (driveLab) driveLab.classList.toggle("hidden", !state.pc.drives.length);
  if (driveBox) driveBox.classList.toggle("hidden", !state.pc.drives.length);
  if (folderLab) folderLab.classList.toggle("hidden", !(driveBox ? state.pc.shortcuts : combined).length);
  if (!state.pc.root) {
    const prefer = (state.pc.shortcuts[0] || combined[0] || {}).id;
    if (prefer) state.pc.root = prefer;
  }
  highlightPcChips();
}

function renderCrumbs(crumbs, parent) {
  const nav = $("pc-nav");
  const box = $("pc-crumbs");
  const up = $("pc-up");
  if (!box) return;
  box.innerHTML = "";
  const list = crumbs || [];
  const browsing = state.pc.tab === "browse";
  if (nav) nav.classList.toggle("hidden", !browsing);
  if (up) {
    const can = browsing && parent && parent.root;
    up.disabled = !can;
    up.onclick = can ? () => {
      state.pc.root = parent.root;
      state.pc.path = parent.path || "";
      loadPcTab(true);
    } : null;
  }
  if (!browsing || !list.length) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  list.forEach((c, i) => {
    if (i) {
      const s = document.createElement("span");
      s.textContent = "/";
      box.appendChild(s);
    }
    const last = i === list.length - 1;
    if (last) {
      const cur = document.createElement("span");
      cur.className = "crumb-current";
      cur.textContent = c.name;
      box.appendChild(cur);
    } else {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = c.name;
      b.addEventListener("click", () => { state.pc.path = c.path || ""; loadPcTab(true); });
      box.appendChild(b);
    }
  });
}

function renderPcGrid(dirs, files, append, opts) {
  const box = $("pc-grid");
  if (!box) return;
  const isSearch = !!(opts && opts.search) || state.pc.tab === "search";
  box.classList.toggle("is-search", isSearch);
  if (!append) {
    box.innerHTML = "";
    state.pc.items = [];
  }
  (dirs || []).forEach((d) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pc-cell folder";
    b.innerHTML = '<span aria-hidden="true">Folder</span><span class="cap"></span>';
    b.querySelector(".cap").textContent = d.name;
    b.addEventListener("click", () => { state.pc.path = d.path; loadPcTab(true); });
    box.appendChild(b);
  });
  (files || []).forEach((f) => {
    const it = { root: f.root || state.pc.root, path: f.path, name: f.name };
    const idx = state.pc.items.length;
    state.pc.items.push(it);
    const cell = document.createElement("div");
    cell.className = "pc-cell" + (isSearch ? " pc-result" : "") + (state.pc.selected[pcKey(it)] ? " is-on" : "");
    const thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = "thumb";
    thumb.setAttribute("aria-label", isSearch ? ("Add " + (f.name || "image")) : ("View " + (f.name || "image")));
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = f.name || "";
    img.src = "/api/pc/thumb?root=" + encodeURIComponent(it.root) + "&path=" + encodeURIComponent(it.path);
    thumb.appendChild(img);
    if (isSearch) {
      thumb.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); addPcItems([it]); });
    } else {
      thumb.addEventListener("click", () => openPcPreview(idx));
    }
    const check = document.createElement("button");
    check.type = "button";
    check.className = "check";
    check.setAttribute("aria-label", "Select " + (f.name || "image"));
    check.setAttribute("aria-pressed", state.pc.selected[pcKey(it)] ? "true" : "false");
    check.textContent = "\u2713";
    check.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); onPcFile(it, cell); });
    cell.append(thumb, check);
    if (isSearch) {
      const meta = document.createElement("button");
      meta.type = "button";
      meta.className = "pc-result-meta";
      meta.setAttribute("aria-label", "Add " + (f.name || "image"));
      const cap = document.createElement("span");
      cap.className = "cap";
      cap.textContent = f.name || "";
      const folder = document.createElement("span");
      folder.className = "folder-path";
      folder.textContent = f.folder_label || f.folder || "";
      meta.append(cap, folder);
      meta.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); addPcItems([it]); });
      cell.appendChild(meta);
      cell.addEventListener("click", (e) => {
        if (e.target.closest(".check")) return;
        addPcItems([it]);
      });
    }
    box.appendChild(cell);
  });
}

async function onPcFile(it, el) {
  const k = pcKey(it);
  if (state.pc.selected[k]) delete state.pc.selected[k];
  else {
    const n = Object.keys(state.pc.selected).length;
    if (n >= slotsLeft()) { $("pc-msg").textContent = "You already have 16 images, the most this model takes."; return; }
    state.pc.selected[k] = it;
  }
  const on = !!state.pc.selected[k];
  const nsel = Object.keys(state.pc.selected).length;
  $("pc-confirm").textContent = nsel ? ("Add (" + nsel + ")") : "Add";
  if (el) {
    el.classList.toggle("is-on", on);
    const check = el.querySelector(".check");
    if (check) check.setAttribute("aria-pressed", on ? "true" : "false");
  }
  syncPcPreviewSelect();
}

function pcPreviewUrl(it) {
  return "/api/pc/preview?root=" + encodeURIComponent(it.root) + "&path=" + encodeURIComponent(it.path);
}

function openPcPreview(index) {
  const items = state.pc.items || [];
  if (!items.length) return;
  state.pc.previewIndex = (index + items.length) % items.length;
  const it = items[state.pc.previewIndex];
  const box = $("pc-preview");
  if (!box) return;
  box.classList.remove("hidden");
  $("pc-preview-img").alt = it.name || "Image";
  $("pc-preview-img").src = pcPreviewUrl(it);
  $("pc-preview-name").textContent = it.name || "";
  syncPcPreviewSelect();
}

function closePcPreview() {
  const box = $("pc-preview");
  if (box) box.classList.add("hidden");
  const img = $("pc-preview-img");
  if (img) img.removeAttribute("src");
}

function syncPcPreviewSelect() {
  const btn = $("pc-preview-select");
  if (!btn) return;
  const items = state.pc.items || [];
  const it = items[state.pc.previewIndex];
  if (!it) { btn.textContent = "Select"; return; }
  btn.textContent = state.pc.selected[pcKey(it)] ? "Selected" : "Select";
}

function togglePcPreviewSelect() {
  const items = state.pc.items || [];
  const it = items[state.pc.previewIndex];
  if (!it) return;
  const cells = [...document.querySelectorAll("#pc-grid .pc-cell")].filter((c) => !c.classList.contains("folder"));
  onPcFile(it, cells[state.pc.previewIndex] || null);
}

function navPcPreview(delta) {
  const items = state.pc.items || [];
  if (!items.length) return;
  openPcPreview((state.pc.previewIndex || 0) + delta);
}

async function confirmPcMulti() {
  const items = Object.values(state.pc.selected);
  if (!items.length) { closePcPicker(); return; }
  try {
    $("pc-msg").textContent = "Adding...";
    const res = await api("/api/pc/pick", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
    (res.items || []).forEach((it) => {
      if (state.images.length < 16) {
        state.images.push(it);
        prefetchAppearance(it && it.name);
      }
    });
    renderSlots();
    renderAliasPreview();
    renderCustomMp();
    closePcPicker();
  } catch (e) {
    $("pc-msg").textContent = e.message;
  }
}

function pcScopeLabel() {
  const roots = [].concat(state.pc.drives || [], state.pc.shortcuts || [], state.pc.roots || []);
  const root = roots.find((r) => r.id === state.pc.root) || roots[0];
  const rel = (state.pc.path || "").replace(/\//g, "\\");
  if (root && root.letter) return root.letter + ":\\" + rel;
  if (root && root.path) return rel ? (String(root.path).replace(/[\\/]$/, "") + "\\" + rel) : root.path;
  return (root && root.name) || "this folder";
}

function pcSetSearching(on, msg) {
  const el = $("pc-msg");
  if (!el) return;
  if (on) {
    el.innerHTML = "";
    const spin = document.createElement("span");
    spin.className = "pc-spin";
    spin.setAttribute("aria-hidden", "true");
    el.appendChild(spin);
    el.appendChild(document.createTextNode(msg || ("Searching " + pcScopeLabel() + "\u2026")));
  } else if (msg != null) {
    el.textContent = msg;
  }
}

/* _pcSearchTimer/_pcSearchSeq declared near state */

function instantPcSearch(q) {
  const ql = (q || "").trim().toLowerCase();
  if (ql.length < 2) return false;
  const cached = state.pc.hereFiles || [];
  if (!cached.length) return false;
  const hits = cached.filter((f) => (f.name || "").toLowerCase().includes(ql)).map((f) => Object.assign({}, f, {
    folder: f.folder || "",
    folder_label: f.folder_label || pcScopeLabel(),
  }));
  if (!hits.length) return false;
  renderPcGrid([], hits, false, { search: true });
  return true;
}

function schedulePcSearch() {
  if (_pcSearchTimer) clearTimeout(_pcSearchTimer);
  const q = (($("pc-query") && $("pc-query").value) || "").trim();
  if (q.length < 2) {
    if (state.pc.tab === "search") {
      renderPcGrid([], [], false, { search: true });
      pcSetSearching(false, "Type at least two letters to search " + pcScopeLabel() + ".");
    }
    return;
  }
  if (state.pc.tab !== "search") {
    state.pc.tab = "search";
    document.querySelectorAll("#pc-tabs button").forEach((x) => x.classList.toggle("is-on", x.dataset.tab === "search"));
  }
  instantPcSearch(q);
  pcSetSearching(true, "Searching " + pcScopeLabel() + "\u2026");
  _pcSearchTimer = setTimeout(() => { loadPcTab(true); }, 300);
}

function bindPcSearchField() {
  const inp = $("pc-query");
  if (!inp || inp.dataset.boundSearch === "1") return;
  inp.dataset.boundSearch = "1";
  inp.addEventListener("input", () => schedulePcSearch());
  inp.addEventListener("search", () => { if (_pcSearchTimer) clearTimeout(_pcSearchTimer); loadPcTab(true); });
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (_pcSearchTimer) clearTimeout(_pcSearchTimer);
      if (state.pc.tab !== "search") {
        state.pc.tab = "search";
        document.querySelectorAll("#pc-tabs button").forEach((x) => x.classList.toggle("is-on", x.dataset.tab === "search"));
      }
      loadPcTab(true);
    }
  });
}

async function addPcItems(items, { close } = {}) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return;
  const room = (typeof slotsLeft === "function") ? slotsLeft() : Math.max(0, 16 - (state.images || []).length);
  if (room <= 0) {
    pcSetSearching(false, "You already have 16 images, the most this model takes.");
    return;
  }
  const payload = list.slice(0, room);
  try {
    pcSetSearching(false, "Adding\u2026");
    const res = await api("/api/pc/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: payload }),
    });
    (res.items || []).forEach((it) => { if (state.images.length < 16) state.images.push(it); prefetchAppearance((it) && (it).name); });
    renderSlots();
    renderAliasPreview();
    renderCustomMp();
    const n = (res.items || []).length;
    const name = (payload[0] && payload[0].name) || "image";
    pcSetSearching(false, n === 1 ? ("Added " + name) : ("Added " + n + " images"));
    if (close) closePcPicker();
  } catch (e) {
    pcSetSearching(false, e.message || "Couldn't add that image.");
  }
}

async function loadPcTab(reset) {
  const pc = state.pc;
  if (reset) pc.offset = 0;
  bindPcSearchField();
  if ($("pc-query")) $("pc-query").classList.remove("hidden");
  const rootsWrap = $("pc-roots-wrap");
  // Keep drive and folder chips visible on Search so the user can pick a scope.
  if (rootsWrap) rootsWrap.classList.remove("hidden");
  // Keep drive and folder chips visible on every tab so Search can be scoped
  // to a selected drive (and so a late Recent load cannot hide them again).
  if ($("pc-drives")) $("pc-drives").classList.toggle("hidden", !(state.pc.drives && state.pc.drives.length));
  if ($("pc-drives-label")) $("pc-drives-label").classList.toggle("hidden", !(state.pc.drives && state.pc.drives.length));
  if ($("pc-roots")) $("pc-roots").classList.remove("hidden");
  if ($("pc-folders-label")) $("pc-folders-label").classList.remove("hidden");
  if ($("pc-nav")) $("pc-nav").classList.toggle("hidden", pc.tab !== "browse");
  if ($("pc-crumbs")) $("pc-crumbs").classList.toggle("hidden", pc.tab !== "browse");
  const seq = ++_pcSearchSeq;
  if (pc.tab === "search") {
    const q0 = (($("pc-query") && $("pc-query").value) || "").trim();
    if (q0.length >= 2) pcSetSearching(true, "Searching " + pcScopeLabel() + "\u2026");
    else pcSetSearching(false, "Type at least two letters to search " + pcScopeLabel() + ".");
  } else {
    pcSetSearching(false, "Loading...");
  }
  $("pc-more").classList.add("hidden");
  try {
    if (pc.tab === "browse") {
      highlightPcChips();
      const data = await api("/api/pc/list?root=" + encodeURIComponent(pc.root) + "&path=" + encodeURIComponent(pc.path || "") + "&offset=" + pc.offset);
      if (seq !== _pcSearchSeq) return;
      renderCrumbs(data.crumbs, data.parent);
      renderPcGrid(data.dirs || [], data.files || [], !!pc.offset);
      const folderLabel = pcScopeLabel();
      pc.hereFiles = (data.files || []).map((f) => Object.assign({}, f, {
        root: f.root || pc.root,
        folder: (pc.path || ""),
        folder_label: folderLabel,
      }));
      // If the user is already typing, filter this folder instantly.
      const typed = (($("pc-query") && $("pc-query").value) || "").trim();
      if (typed.length >= 2 && instantPcSearch(typed)) {
        pcSetSearching(false, "Matches in " + folderLabel + ". Keep typing, or press Search to look in subfolders.");
      }
      pc.more = data.more;
      pc.offset = (data.offset || 0) + (data.dirs || []).length + (data.files || []).length;
      const nFiles = data.total_files != null ? data.total_files : data.total;
      const nDirs = data.total_dirs || 0;
      let msg = "";
      if (nDirs && nFiles) msg = nDirs + " folders · " + nFiles + " images";
      else if (nFiles) msg = nFiles + " images";
      else if (nDirs) msg = nDirs + " folders";
      else msg = "No images in this folder.";
      if (data.error) msg = data.error;
      if (!(typed.length >= 2)) pcSetSearching(false, msg);
    } else if (pc.tab === "recent") {
      highlightPcChips();
      const data = await api("/api/pc/recent?offset=" + pc.offset);
      if (seq !== _pcSearchSeq) return;
      renderCrumbs([]);
      renderPcGrid([], data.items, !!pc.offset);
      pc.more = data.more;
      pc.offset = (data.offset || 0) + (data.items || []).length;
      pcSetSearching(false, data.total ? "Newest images in your shortcut folders" : "No recent images found.");
    } else {
      highlightPcChips();
      const q = (($("pc-query") && $("pc-query").value) || "").trim();
      if (q.length < 2) {
        renderPcGrid([], [], false, { search: true });
        pcSetSearching(false, "Type at least two letters to search " + pcScopeLabel() + ".");
        return;
      }
      const url = "/api/pc/search?q=" + encodeURIComponent(q)
        + "&root=" + encodeURIComponent(pc.root || "")
        + "&path=" + encodeURIComponent(pc.path || "")
        + "&offset=" + pc.offset;
      const data = await api(url);
      if (seq !== _pcSearchSeq) return;
      renderCrumbs([]);
      renderPcGrid([], data.items || [], !!pc.offset, { search: true });
      pc.more = data.more;
      pc.offset = (data.offset || 0) + (data.items || []).length;
      let msg;
      if (data.error) msg = data.error;
      else if (!data.total) msg = "No matches in " + (data.scope || pcScopeLabel()) + ".";
      else {
        msg = data.total + " match" + (data.total === 1 ? "" : "es") + " in " + (data.scope || pcScopeLabel());
        if (data.here_count) msg += " \u00b7 " + data.here_count + " in this folder";
        if (data.total >= 300 && data.truncated) msg += " \u00b7 first " + data.total + " shown";
        else if (data.timed_out && !data.total) msg += " \u00b7 search timed out";
      }
      pcSetSearching(false, msg);
    }
    $("pc-more").classList.toggle("hidden", !pc.more);
  } catch (e) {
    if (seq !== _pcSearchSeq) return;
    pcSetSearching(false, e.message || "Search failed.");
  }
}

/* ---------- Library ---------- */
async function refreshLibrary() {
  const q = "/api/gallery?mode=" + encodeURIComponent(state.libMode) + (state.libFav ? "&fav=1" : "");
  try {
    const data = await api(q);
    const all = data.items || [];
    // engine filter: built from what is actually in the library (MiniMax H3, Krea 2 Edit, MOSS Image ...)
    const engineOf = (it) => String(it.workflow_name || "MiniMax H3").split(" · ")[0].replace(/\s*\(built-in\)\s*$/i, "").trim();
    const sel = $("lib-engine");
    if (sel) {
      const names = [...new Set(all.map(engineOf))].sort((a, b) => a.localeCompare(b));
      const want = state.libEngine || "";
      sel.innerHTML = '<option value="">All engines</option>' + names.map((n) =>
        '<option value="' + n.replace(/"/g, "&quot;") + '">' + n.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]) + "</option>").join("");
      sel.value = names.includes(want) ? want : "";
      state.libEngine = sel.value;
      sel.classList.toggle("hidden", names.length < 2);
    }
    state.library = state.libEngine ? all.filter((it) => engineOf(it) === state.libEngine) : all;
    const box = $("lib-grid");
    box.innerHTML = "";
    if (!state.library.length) {
      box.innerHTML = '<p class="muted">Nothing in the library yet. Generate an image and it will show up here.</p>';
      return;
    }
    state.library.forEach((it, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lib-item";
      b.title = it.prompt || it.filename;
      const thumb = document.createElement("span");
      thumb.className = "thumb";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = it.prompt || "Result";
      img.src = it.thumb;
      thumb.appendChild(img);
      b.appendChild(thumb);
      if (it.fav) {
        const s = document.createElement("span");
        s.className = "star";
        s.textContent = "\u2605";
        b.appendChild(s);
      }
      b.addEventListener("click", () => openViewer(i));
      box.appendChild(b);
    });
  } catch (e) {
    $("lib-grid").innerHTML = '<p class="error">' + e.message + "</p>";
  }
}

function applyResolution(res, match, keep) {
  const id = (res || "") + "";
  const low = id.toLowerCase();
  if (keep || low === "keep" || low === "picture1" || low === "picture1:original" || low === "picture1:orig") {
    state.p1Original = true;
    setQuality("picture1");
    return;
  }
  if (low.startsWith("picture1:") || low.startsWith("custom:match:") || low.startsWith("custom:picture1:")) {
    const mp = low.split(":").pop();
    if (mp === "original" || mp === "orig" || mp === "keep") {
      state.p1Original = true;
    } else {
      state.p1Original = false;
      state.p1Mp = Math.min(20, Math.max(1, Number(mp) || 8));
    }
    setQuality("picture1");
    return;
  }
  if (match || low === "match") {
    state.p1Original = false;
    state.p1Mp = 8;
    setQuality("picture1");
    return;
  }
  if (low.startsWith("custom:")) {
    const parts = id.split(":");
    const mp = parts.pop();
    const asp = parts.slice(1).join(":") || "1:1";
    if (asp === "match" || asp === "picture1") {
      state.p1Original = false;
      state.p1Mp = Math.min(20, Math.max(1, Number(mp) || 8));
      setQuality("picture1");
      return;
    }
    state.customAspect = asp;
    state.aspect = asp;
    setCustomMp(mp);
    setQuality("custom");
    return;
  }
  for (const q of ["4mp", "16mp"]) {
    for (const [asp, val] of Object.entries(PRESETS[q])) {
      if (val === id) { state.aspect = asp; setQuality(q); return; }
    }
  }
  const m = /(\d+)\s*[xX\u00d7]\s*(\d+)/.exec(id);
  if (m) {
    const want = m[1] + "x" + m[2];
    for (const q of ["4mp", "16mp"]) {
      for (const [asp, val] of Object.entries(PRESETS[q])) {
        if (val === want) { state.aspect = asp; setQuality(q); return; }
      }
    }
    const w = Number(m[1]), h = Number(m[2]);
    const mp = Math.round(((w * h) / 1e6) * 2) / 2;
    let best = ASPECTS[0], bestDiff = Infinity;
    ASPECTS.forEach((a) => {
      const diff = Math.abs(w / h - a.w / a.h);
      if (diff < bestDiff) { bestDiff = diff; best = a; }
    });
    state.aspect = best.id;
    state.customAspect = best.id;
    setCustomMp(Math.min(20, Math.max(1, mp)));
    setQuality("custom");
    return;
  }
  setQuality("4mp");
}

function reuseSettings(it) {
  return reuseAll(it);
}
function reuseSettings_legacy_unused(it) {
  setView("create");
  setMode(it.mode === "edit" ? "edit" : "t2i");
  $("prompt").value = it.prompt_user || it.prompt || "";
  applyResolution(it.resolution || ((it.width && it.height) ? (it.width + "x" + it.height) : ""), it.match_input, it.keep_input);
  if (it.steps) $("steps").value = it.steps;
  $("pe").checked = !!it.pe;
  state.peMode = it.pe_mode === "quality" ? "quality" : "fast";
  document.querySelectorAll("#pe-modes button").forEach((b) => b.classList.toggle("is-on", b.dataset.peMode === state.peMode));
  if (it.seed != null) {
    $("random-seed").checked = false;
    $("seed").disabled = false;
    $("seed").value = it.seed;
  }
  if (it.cfg != null) $("cfg").value = it.cfg;
  if (it.sampler_name) $("sampler_name").value = it.sampler_name;
  if (it.scheduler) $("scheduler").value = it.scheduler;
  renderAliasPreview();
  closeViewer();
}

async function editThis(it) {
  try {
    const item = await api("/api/library/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id }) });
    setView("create");
    setMode("edit");
    state.images = [item];
    prefetchAppearance(item && item.name);
    renderSlots();
    renderAliasPreview();
    renderCustomMp();
    closeViewer();
  } catch (e) {
    alert(e.message);
  }
}

function openViewer(index) {
  if (!state.library.length) return;
  state.viewerIndex = (index + state.library.length) % state.library.length;
  state.compareOn = false;
  const it = state.library[state.viewerIndex];
  $("viewer").classList.remove("hidden");
  $("viewer-prompt").textContent = it.prompt_user || it.prompt || "";
    if (typeof renderSentPrompt === "function") renderSentPrompt("viewer-sent-box", "viewer-sent-text", "viewer-sent-note", it);
  const ve = $("viewer-enhanced");
  if (ve) {
    const text = it.enhanced_prompt || "";
    ve.classList.toggle("hidden", !text || text === (it.prompt_user || it.prompt));
    if ($("viewer-enhanced-text")) $("viewer-enhanced-text").textContent = text;
  }
  const bits = [
    it.mode === "edit" ? "Image Edit" : "Text to Image",
    it.resolution || (it.width && it.height ? it.width + "x" + it.height : ""),
    it.steps != null ? it.steps + " steps" : "",
    it.seed != null ? "seed " + it.seed : "",
    it.pe ? "Prompt Enhancer on" : "",
    it.t ? new Date(it.t * 1000).toLocaleString() : "",
  ].filter(Boolean);
  $("viewer-meta").textContent = bits.join(" \u00b7 ");
  $("viewer-dl").href = it.download;
  $("viewer-dl").setAttribute("download", it.filename || "result.png");
  $("viewer-fav").textContent = it.fav ? "Favourited" : "Favourite";
  renderReuse("viewer-reuse-box", "viewer-reuse-actions", it);
  $("viewer-ba").classList.toggle("hidden", !it.has_before);
  $("viewer-edit").classList.toggle("hidden", false);
  const url = it.url + "&t=" + Date.now();
  $("viewer-compare").classList.add("hidden");
  $("viewer-img").classList.remove("hidden");
  $("viewer-img").src = url;
  $("viewer-after").src = url;
  if (it.has_before) $("viewer-before").src = it.before_url;
}

function closeViewer() { $("viewer").classList.add("hidden"); }

function toggleViewerCompare() {
  const it = state.library[state.viewerIndex];
  if (!it || !it.has_before) return;
  state.compareOn = !state.compareOn;
  $("viewer-compare").classList.toggle("hidden", !state.compareOn);
  $("viewer-img").classList.toggle("hidden", state.compareOn);
  if (state.compareOn) {
    $("viewer-split").value = 50;
    $("viewer-after").onload = () => setViewerSplit(50);
    setViewerSplit(50);
  }
}

async function toggleFav() {
  const it = state.library[state.viewerIndex];
  if (!it) return;
  const res = await api("/api/favourite", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id, fav: !it.fav }) });
  it.fav = res.fav;
  $("viewer-fav").textContent = it.fav ? "Favourited" : "Favourite";
  refreshLibrary();
}

const CONFIRM_DELETE = {
  title: "Delete this image?",
  body: "It will be removed from the library and from the Comfy Studio output folder only. Nothing else on the PC is touched.",
  yes: "Delete",
};

function askConfirm(opts) {
  state.confirm = opts;
  $("confirm-title").textContent = opts.title;
  $("confirm-body").textContent = opts.body;
  $("confirm-yes").textContent = opts.yes || "OK";
  $("confirm").classList.remove("hidden");
}

function askDelete() {
  askConfirm(Object.assign({}, CONFIRM_DELETE, { onYes: doDelete }));
}

function closeConfirm() { $("confirm").classList.add("hidden"); state.confirm = null; }

async function doDelete() {
  const it = state.library[state.viewerIndex];
  closeConfirm();
  if (!it) return;
  try {
    await api("/api/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id }) });
    closeViewer();
    refreshLibrary();
  } catch (e) {
    alert(e.message);
  }
}

function bind() {
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll(".views button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  document.querySelectorAll("#quality button").forEach((b) => b.addEventListener("click", () => setQuality(b.dataset.quality)));
  if ($("res-trigger")) $("res-trigger").addEventListener("click", (e) => { e.stopPropagation(); toggleResMenu(); });
  if ($("res-backdrop")) $("res-backdrop").addEventListener("click", closeResMenu);
  document.addEventListener("click", (e) => {
    if (!$("res-menu") || $("res-menu").classList.contains("hidden")) return;
    if (!e.target.closest("#res-picker") && !e.target.closest("#res-menu")) closeResMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (!$("res-menu") || $("res-menu").classList.contains("hidden")) return;
    const opts = [...$("res-menu").querySelectorAll(".res-option")];
    const i = opts.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); closeResMenu(); if ($("res-trigger")) $("res-trigger").focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); (opts[i + 1] || opts[0] || $("res-trigger")).focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (opts[i - 1] || opts[opts.length - 1] || $("res-trigger")).focus(); }
    else if (e.key === "Home") { e.preventDefault(); if (opts[0]) opts[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); if (opts.length) opts[opts.length - 1].focus(); }
    else if (e.key === "Enter" || e.key === " ") {
      if (document.activeElement && document.activeElement.classList.contains("res-option")) {
        e.preventDefault();
        document.activeElement.click();
      }
    }
  });
  if ($("p1-original")) $("p1-original").addEventListener("click", () => setP1Original());
  if ($("p1-mp-slider")) $("p1-mp-slider").addEventListener("input", (e) => setP1Mp(e.target.value));
  if ($("p1-mp-num")) $("p1-mp-num").addEventListener("change", (e) => setP1Mp(e.target.value));
  document.querySelectorAll("#p1-mp-chips button").forEach((b) => b.addEventListener("click", () => setP1Mp(b.dataset.p1mp)));
  $("generate").addEventListener("click", generate);
  if ($("cancel")) $("cancel").addEventListener("click", (e) => { e.preventDefault(); cancelRun(); });
  $("random-seed").addEventListener("change", () => { $("seed").disabled = $("random-seed").checked; });
  if ($("split")) $("split").addEventListener("input", (e) => setSplit(e.target.value));
  if ($("viewer-split")) $("viewer-split").addEventListener("input", (e) => setViewerSplit(e.target.value));
  window.addEventListener("resize", () => {
    if ($("split")) setSplit($("split").value);
    if ($("viewer-split")) setViewerSplit($("viewer-split").value);
  });
  $("open-library").addEventListener("click", () => setView("library"));

  document.querySelectorAll("#pe-modes button").forEach((b) => b.addEventListener("click", () => {
    state.peMode = b.dataset.peMode || "fast";
    document.querySelectorAll("#pe-modes button").forEach((x) => x.classList.toggle("is-on", x === b));
  }));
  if ($("enhanced-copy")) $("enhanced-copy").addEventListener("click", () => {
    const text = ($("enhanced-text") && $("enhanced-text").textContent) || "";
    copyText(text, $("enhanced-copy"), "Copied", "Copy enhanced prompt");
  });
  if ($("enhanced-use")) $("enhanced-use").addEventListener("click", () => {
    const text = ($("enhanced-text") && $("enhanced-text").textContent) || "";
    if (!text) return;
    $("prompt").value = text;
    renderAliasPreview();
  });
  $("mp-slider").addEventListener("input", (e) => setCustomMp(e.target.value));
  $("mp-num").addEventListener("change", (e) => setCustomMp(e.target.value));
  document.querySelectorAll("#mp-chips button").forEach((b) => b.addEventListener("click", () => setCustomMp(b.dataset.mp)));

  $("add-phone").addEventListener("click", () => { closeAddMenu(); $("file-add").click(); });
  $("add-pc").addEventListener("click", () => { closeAddMenu(); openPcPicker(); });
  document.addEventListener("click", (e) => {
    if (!$("add-menu").classList.contains("hidden") && !e.target.closest("#add-menu") && !e.target.closest("#slot-add") && !e.target.closest("#add-images") && !e.target.closest("#dock-add")) closeAddMenu();
  });
  $("file-add").addEventListener("change", async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = "";
    await addFiles(files);
  });
  $("clear-all").addEventListener("click", () => {
    askConfirm({
      title: "Clear all images?",
      body: "This only removes them from this edit. Files on the PC are not deleted.",
      yes: "Clear all",
      onYes: () => { state.images = []; renderSlots(); renderAliasPreview(); if (typeof scheduleOutputSize === "function") scheduleOutputSize(); else renderCustomMp(); },
    });
  });

  const slots = $("slots");
  ["dragenter", "dragover"].forEach((ev) => slots.addEventListener(ev, (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    slots.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((ev) => slots.addEventListener(ev, (e) => { e.preventDefault(); slots.classList.remove("is-over"); }));
  slots.addEventListener("drop", async (e) => {
    const files = [...(e.dataTransfer.files || [])].filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
    if (files.length) await addFiles(files);
  });

  $("prompt").addEventListener("input", () => { renderAliasPreview(); showAtPicker(); });
  $("prompt").addEventListener("keyup", (e) => {
    if (e.key === "Escape") hideAtPicker();
    else showAtPicker();
  });
  $("prompt").addEventListener("click", showAtPicker);
  $("prompt").addEventListener("blur", () => setTimeout(hideAtPicker, 150));

  $("pc-close").addEventListener("click", closePcPicker);
  $("pc-backdrop").addEventListener("click", closePcPicker);
  $("pc-confirm").addEventListener("click", confirmPcMulti);
  if ($("pc-preview-close")) $("pc-preview-close").addEventListener("click", closePcPreview);
  if ($("pc-preview-back")) $("pc-preview-back").addEventListener("click", closePcPreview);
  if ($("pc-preview-select")) $("pc-preview-select").addEventListener("click", togglePcPreviewSelect);
  if ($("pc-preview-prev")) $("pc-preview-prev").addEventListener("click", () => navPcPreview(-1));
  if ($("pc-preview-next")) $("pc-preview-next").addEventListener("click", () => navPcPreview(1));
  const previewStage = $("pc-preview-stage");
  if (previewStage) {
    let psx = 0;
    previewStage.addEventListener("touchstart", (e) => { psx = e.changedTouches[0].clientX; }, { passive: true });
    previewStage.addEventListener("touchend", (e) => {
      const dx = e.changedTouches[0].clientX - psx;
      if (dx > 50) navPcPreview(-1);
      else if (dx < -50) navPcPreview(1);
    }, { passive: true });
  }
  $("pc-more").addEventListener("click", () => loadPcTab(false));
  document.querySelectorAll("#pc-tabs button").forEach((b) => b.addEventListener("click", () => {
    state.pc.tab = b.dataset.tab;
    document.querySelectorAll("#pc-tabs button").forEach((x) => x.classList.toggle("is-on", x === b));
    loadPcTab(true);
  }));
  bindPcSearchField();

  document.querySelectorAll("#lib-modes button").forEach((b) => b.addEventListener("click", () => {
    state.libMode = b.dataset.lib;
    document.querySelectorAll("#lib-modes button").forEach((x) => x.classList.toggle("is-on", x === b));
    refreshLibrary();
  }));
  $("lib-fav").addEventListener("change", () => { state.libFav = $("lib-fav").checked; refreshLibrary(); });
  if ($("lib-engine")) $("lib-engine").addEventListener("change", () => { state.libEngine = $("lib-engine").value; refreshLibrary(); });

  $("viewer-close").addEventListener("click", closeViewer);
  $("viewer-prev").addEventListener("click", () => openViewer(state.viewerIndex - 1));
  $("viewer-next").addEventListener("click", () => openViewer(state.viewerIndex + 1));
  $("viewer-copy").addEventListener("click", () => {
    const it = state.library[state.viewerIndex];
    if (!it) return;
    copyText(promptText(it, "original"), $("viewer-copy"), "Copied", "Copy prompt");
  });
  $("viewer-reuse").addEventListener("click", () => reuseSettings(state.library[state.viewerIndex]));
  $("viewer-edit").addEventListener("click", () => editThis(state.library[state.viewerIndex]));
  $("viewer-ba").addEventListener("click", toggleViewerCompare);
  $("viewer-fav").addEventListener("click", () => toggleFav().catch((e) => alert(e.message)));
  $("viewer-del").addEventListener("click", askDelete);
  $("confirm-no").addEventListener("click", closeConfirm);
  $("confirm-backdrop").addEventListener("click", closeConfirm);
  $("confirm-yes").addEventListener("click", async () => {
    const fn = state.confirm && state.confirm.onYes;
    closeConfirm();
    if (fn) await fn();
  });

  let sx = 0;
  $("viewer-stage").addEventListener("touchstart", (e) => { sx = e.changedTouches[0].clientX; }, { passive: true });
  $("viewer-stage").addEventListener("touchend", (e) => {
    const dx = e.changedTouches[0].clientX - sx;
    if (dx > 50) openViewer(state.viewerIndex - 1);
    else if (dx < -50) openViewer(state.viewerIndex + 1);
  }, { passive: true });
  window.addEventListener("keydown", (e) => {
    if ($("pc-preview") && !$("pc-preview").classList.contains("hidden")) {
      if (e.key === "Escape") closePcPreview();
      if (e.key === "ArrowLeft") navPcPreview(-1);
      if (e.key === "ArrowRight") navPcPreview(1);
      return;
    }
    if (!$("viewer").classList.contains("hidden")) {
      if (e.key === "Escape") closeViewer();
      if (e.key === "ArrowLeft") openViewer(state.viewerIndex - 1);
      if (e.key === "ArrowRight") openViewer(state.viewerIndex + 1);
      return;
    }
    if (e.key === "Escape") { closeAddMenu(); hideAtPicker(); closePcPicker(); closeCopySheet(); closeResultFs(); }
  });
}

function toast(msg, ms) {
  const el = $("toast");
  if (!el) { try { console.log(msg); } catch (e) {} return; }
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), ms || 1800);
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "readonly");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.width = "2em";
  ta.style.height = "2em";
  ta.style.padding = "0";
  ta.style.border = "none";
  ta.style.outline = "none";
  ta.style.boxShadow = "none";
  ta.style.background = "transparent";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try { ta.setSelectionRange(0, ta.value.length); } catch (e) {}
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

function showCopySheet(text) {
  const sheet = $("copy-sheet");
  const box = $("copy-sheet-text");
  if (!sheet || !box) return;
  box.value = text;
  sheet.classList.remove("hidden");
  setTimeout(() => { try { box.focus(); box.select(); box.setSelectionRange(0, box.value.length); } catch (e) {} }, 50);
}

function closeCopySheet() {
  const sheet = $("copy-sheet");
  if (sheet) sheet.classList.add("hidden");
}

async function copyText(text, btn, okLabel, idleLabel) {
  const value = (text == null ? "" : String(text));
  if (!value) { toast("Nothing to copy"); return false; }
  let ok = false;
  try {
    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(value);
      ok = true;
    }
  } catch (e) { ok = false; }
  if (!ok) ok = fallbackCopy(value);
  if (ok) {
    toast("Copied");
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = okLabel || "Copied";
      setTimeout(() => { btn.textContent = idleLabel || prev || "Copy prompt"; }, 1200);
    }
    return true;
  }
  showCopySheet(value);
  toast("Couldn't copy");
  return false;
}

function promptText(it, which) {
  if (!it) return "";
  if (which === "enhanced") return it.enhanced_prompt || it.prompt || "";
  return it.prompt_user || it.prompt || "";
}

function jobToReuseItem(job) {
  if (!job) return null;
  return {
    id: job.id,
    mode: job.mode,
    prompt: job.prompt,
    prompt_user: job.prompt_user || job.prompt,
    enhanced_prompt: job.enhanced_prompt || "",
    pe: !!(job.pe || job.enhanced_prompt),
    pe_mode: job.pe_mode === "quality" ? "quality" : "fast",
    pe_fallback: !!job.pe_fallback,
    resolution: job.resolution_in || job.resolution || (job.width && job.height ? job.width + "x" + job.height : ""),
    match_input: job.match_input,
    keep_input: job.keep_input,
    auto_assist: job.auto_assist !== false,
    ref_mode: !!job.ref_mode,
    image_roles: job.image_roles || [],
    camera: job.camera || {},
    prompt_assist: job.prompt_assist || "",
    prompt_sent: job.prompt_sent || job.prompt || job.enhanced_prompt || "",
    intent: job.intent || "",
    width: job.width,
    height: job.height,
    steps: job.steps,
    seed: job.seed,
    cfg: job.cfg,
    sampler_name: job.sampler_name || (job.models && job.models.sampler_name),
    scheduler: job.scheduler || (job.models && job.models.scheduler),
    source_images: job.source_images || [],
    input_snapshots: job.input_snapshots || [],
    has_before: !!job.has_before,
    before_url: job.before_url,
  };
}

function renderReuse(boxId, actionsId, it) {
  const box = $(boxId);
  const actions = $(actionsId);
  if (!box || !actions) return;
  actions.innerHTML = "";
  if (!it || !it.id) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const hasEnhanced = !!(it.enhanced_prompt && it.enhanced_prompt !== (it.prompt_user || ""));
  const btns = [];
  btns.push({ id: "rp", label: hasEnhanced ? "Reuse original prompt" : "Reuse prompt", ico: "P", fn: () => reusePrompt(it, "original") });
  if (hasEnhanced) btns.push({ id: "re", label: "Reuse enhanced prompt", ico: "E", fn: () => reusePrompt(it, "enhanced") });
  const hasInputs = it.mode === "edit" && ((it.input_snapshots && it.input_snapshots.length) || (it.source_images && it.source_images.length) || it.has_before);
  btns.push({ id: "ri", label: "Reuse input images", ico: "I", fn: () => reuseInputs(it), disabled: !hasInputs });
  btns.push({ id: "o1", label: "Use output as Picture 1", ico: "1", fn: () => reuseOutput(it, "picture1") });
  btns.push({ id: "on", label: "Add output as next image", ico: "+", fn: () => reuseOutput(it, "next") });
  btns.push({ id: "ra", label: "Reuse all settings", ico: "*", fn: () => reuseAll(it) });
  btns.forEach((spec) => {
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = '<span class="ico" aria-hidden="true"></span><span></span>';
    b.querySelector(".ico").textContent = spec.ico;
    b.querySelectorAll("span")[1].textContent = spec.label;
    if (spec.disabled) b.disabled = true;
    b.addEventListener("click", spec.fn);
    actions.appendChild(b);
  });
  let note = box.querySelector(".reuse-note");
  if (!note) {
    note = document.createElement("p");
    note.className = "reuse-note";
    box.appendChild(note);
  }
  note.textContent = "";
  box._note = note;
}

function reusePrompt(it, which) {
  const text = promptText(it, which);
  setView("create");
  $("prompt").value = text;
  renderAliasPreview();
  closeViewer();
  closeResultFs();
  toast(which === "enhanced" ? "Enhanced prompt loaded" : "Prompt loaded");
}

async function reuseInputs(it) {
  if (!it || !it.id) { toast("No saved inputs for this image."); return; }
  try {
    const res = await api("/api/library/reuse-inputs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id }) });
    setView("create");
    setMode("edit");
    state.images = res.items || [];
    prefetchAppearanceAll();
    renderSlots();
    renderAliasPreview();
    renderCustomMp();
    closeViewer();
    closeResultFs();
    const missing = res.missing || [];
    if (!state.images.length && missing.length) {
      toast("The original input images could not be found.");
    } else if (missing.length) {
      toast("Some input images are missing");
      if ($("reuse-box") && $("reuse-box")._note) $("reuse-box")._note.textContent = missing.join(" ");
    } else {
      toast("Input images restored");
    }
  } catch (e) {
    toast(e.message || "Couldn't restore input images");
  }
}

async function reuseOutput(it, place) {
  if (!it || !it.id) return;
  try {
    const item = await api("/api/library/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id }) });
    setView("create");
    setMode("edit");
    if (place === "next") {
      if (state.images.length >= 16) { toast("You already have 16 images, the most this model takes."); return; }
      state.images.push(item);
      prefetchAppearance(item && item.name);
    } else {
      if (state.images.length) state.images[0] = item;
      else state.images = [item];
      prefetchAppearance(item && item.name);
    }
    renderSlots();
    renderAliasPreview();
    renderCustomMp();
    closeViewer();
    closeResultFs();
    toast(place === "next" ? "Output added as the next image" : "Output set as Picture 1");
  } catch (e) {
    toast(e.message || "Couldn't use the output as an input");
  }
}

async function reuseAll(it) {
  if (!it) return;
  try {
    setView("create");
    setMode(it.mode === "edit" ? "edit" : "t2i");
    $("prompt").value = promptText(it, "original");
    applyResolution(it.resolution || ((it.width && it.height) ? (it.width + "x" + it.height) : ""), it.match_input, it.keep_input);
    if (it.steps) $("steps").value = it.steps;
    const peOn = !!(it.pe || (it.enhanced_prompt && !it.pe_fallback));
    $("pe").checked = peOn;
    state.peMode = it.pe_mode === "quality" ? "quality" : "fast";
    state.autoAssist = it.auto_assist !== false;
    if ($("auto-assist")) $("auto-assist").checked = state.autoAssist;
    state.refMode = !!(it.mode === "edit" && it.ref_mode);
    if (typeof setEditKind === "function") setEditKind(state.refMode ? "ref" : "edit");
    state.roles = (it.image_roles || []).slice();
    state.camera = Object.assign({}, it.camera || {});
    if (typeof renderCameraChips === "function") renderCameraChips();
    document.querySelectorAll("#pe-modes button").forEach((b) => b.classList.toggle("is-on", b.dataset.peMode === state.peMode));
    if (it.seed != null) {
      $("random-seed").checked = false;
      $("seed").disabled = false;
      $("seed").value = it.seed;
    }
    if (it.cfg != null) $("cfg").value = it.cfg;
    if (it.sampler_name && $("sampler_name")) $("sampler_name").value = it.sampler_name;
    if (it.scheduler && $("scheduler")) $("scheduler").value = it.scheduler;
    renderAliasPreview();
    closeViewer();
    closeResultFs();
    if (it.mode === "edit") await reuseInputs(it);
    toast("Settings loaded");
  } catch (e) {
    toast(e.message || "Couldn't reuse those settings");
  }
}

function reuseSettings(it) {
  reuseAll(it);
}

async function editThis(it) {
  return reuseOutput(it, "picture1");
}

function openResultFs(src) {
  const box = $("result-fs");
  const img = $("result-fs-img");
  if (!box || !img || !src) return;
  img.src = src;
  box.classList.remove("hidden");
}

function closeResultFs() {
  const box = $("result-fs");
  if (box) box.classList.add("hidden");
}

function toggleResultCompare() {
  state.compareOn = !state.compareOn;
  const cmp = $("compare");
  const frame = $("result-frame");
  if (!cmp) return;
  cmp.classList.toggle("hidden", !state.compareOn);
  if (frame) frame.classList.toggle("hidden", state.compareOn);
  if ($("result-compare")) $("result-compare").textContent = state.compareOn ? "Show result" : "Compare";
  if (state.compareOn) {
    const wide = window.innerWidth >= 720;
    cmp.classList.toggle("is-side", wide);
    cmp.classList.remove("is-hold");
    const hint = $("compare-hint");
    if (hint) hint.textContent = wide ? "Original on the left, result on the right." : "Hold to see the original";
  }
}

function bindCompareHold(el) {
  if (!el || el._holdBound) return;
  el._holdBound = true;
  const on = () => el.classList.add("is-hold");
  const off = () => el.classList.remove("is-hold");
  el.addEventListener("pointerdown", on);
  el.addEventListener("pointerup", off);
  el.addEventListener("pointercancel", off);
  el.addEventListener("pointerleave", off);
}

async function refreshRecent() {
  const strip = $("recent-strip");
  const row = $("recent-row");
  if (!strip || !row) return;
  try {
    const data = await api("/api/gallery?mode=all");
    const items = (data.items || []).slice(0, 12);
    row.innerHTML = "";
    if (!items.length) { strip.classList.add("hidden"); return; }
    strip.classList.remove("hidden");
    items.forEach((it, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.title = it.prompt_user || it.prompt || it.filename || "";
      const thumb = document.createElement("span");
      thumb.className = "thumb";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      img.src = it.thumb;
      thumb.appendChild(img);
      b.appendChild(thumb);
      b.addEventListener("click", () => { setView("library"); state.library = items; openViewer(i); });
      row.appendChild(b);
    });
  } catch (e) {
    strip.classList.add("hidden");
  }
}

function bindExtras() {
  if ($("result-copy")) $("result-copy").addEventListener("click", () => copyText(promptText(state.reuseItem, "original"), $("result-copy"), "Copied", "Copy prompt"));
  if ($("result-compare")) $("result-compare").addEventListener("click", toggleResultCompare);
  if ($("result-frame")) $("result-frame").addEventListener("click", () => openResultFs($("single") && $("single").src));
  if ($("live-frame")) $("live-frame").addEventListener("click", () => openResultFs($("live") && $("live").src));
  if ($("result-fs-close")) $("result-fs-close").addEventListener("click", closeResultFs);
  if ($("result-fs")) $("result-fs").addEventListener("click", (e) => { if (e.target.id === "result-fs") closeResultFs(); });
  bindCompareHold($("compare") || $("compare-stack"));
  if ($("copy-sheet-close")) $("copy-sheet-close").addEventListener("click", closeCopySheet);
  if ($("copy-backdrop")) $("copy-backdrop").addEventListener("click", closeCopySheet);
  if ($("copy-sheet-try")) $("copy-sheet-try").addEventListener("click", () => copyText(($("copy-sheet-text") && $("copy-sheet-text").value) || "", $("copy-sheet-try"), "Copied", "Copy"));
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeCopySheet(); closeResultFs(); }
  });
  refreshRecent();
}


/* Auto understanding + Camera helper (dev/live integration). */
const CAMERA_CATALOG = {"categories":[{"id":"angle","label":"Angle","options":[{"id":"eye_level","label":"Eye level","phrase":"eye-level camera"},{"id":"low_angle","label":"Low angle","phrase":"low angle, looking up at the subject"},{"id":"worms_eye","label":"Worm's-eye view","phrase":"worm's-eye view, looking straight up"},{"id":"high_angle","label":"High angle","phrase":"high angle, looking down at the subject"},{"id":"birds_eye","label":"Bird's-eye / top-down","phrase":"bird's-eye, top-down view"},{"id":"overhead_flat_lay","label":"Overhead flat lay","phrase":"overhead flat-lay, camera looking straight down"},{"id":"dutch","label":"Dutch / tilted","phrase":"dutch angle, camera tilted in the frame"},{"id":"ots","label":"Over-the-shoulder","phrase":"over-the-shoulder shot"},{"id":"pov","label":"POV / first-person","phrase":"point-of-view, first-person shot"},{"id":"profile_left","label":"Side profile (left)","phrase":"side-profile view from the subject's left"},{"id":"profile_right","label":"Side profile (right)","phrase":"side-profile view from the subject's right"},{"id":"three_quarter","label":"Three-quarter view","phrase":"three-quarter view"},{"id":"back_view","label":"Back view","phrase":"back view of the subject"},{"id":"from_behind","label":"From behind","phrase":"from behind the subject"}]},{"id":"shot","label":"Shot size","options":[{"id":"ecu","label":"Extreme close-up","phrase":"extreme close-up"},{"id":"cu","label":"Close-up","phrase":"close-up"},{"id":"mcu","label":"Medium close-up","phrase":"medium close-up"},{"id":"medium","label":"Medium","phrase":"medium shot"},{"id":"cowboy","label":"Cowboy","phrase":"cowboy shot, framed mid-thigh up"},{"id":"full","label":"Full body","phrase":"full-body shot"},{"id":"wide","label":"Wide","phrase":"wide shot"},{"id":"ews","label":"Extreme wide / establishing","phrase":"extreme wide, establishing shot"}]},{"id":"move","label":"Camera move / position","options":[{"id":"orbit_left_45","label":"Orbit left 45\u00b0","phrase":"camera orbited 45 degrees to the left of the original viewpoint"},{"id":"orbit_right_45","label":"Orbit right 45\u00b0","phrase":"camera orbited 45 degrees to the right of the original viewpoint"},{"id":"orbit_left_90","label":"Orbit left 90\u00b0","phrase":"camera orbited 90 degrees to the left of the original viewpoint"},{"id":"orbit_right_90","label":"Orbit right 90\u00b0","phrase":"camera orbited 90 degrees to the right of the original viewpoint"},{"id":"rotate_front","label":"Rotate to front","phrase":"camera rotated around to a front view"},{"id":"rotate_back","label":"Rotate to back","phrase":"camera rotated around to a back view"},{"id":"closer","label":"Move closer","phrase":"camera moved closer to the subject"},{"id":"further","label":"Move further","phrase":"camera moved further from the subject"},{"id":"raise","label":"Raise camera","phrase":"camera raised higher"},{"id":"lower","label":"Lower camera","phrase":"camera lowered"},{"id":"aerial","label":"Aerial / drone","phrase":"aerial, drone-like viewpoint"}]},{"id":"lens","label":"Lens","options":[{"id":"14mm","label":"Ultra-wide 14mm","phrase":"ultra-wide 14mm-equivalent look, very broad field of view"},{"id":"24mm","label":"24mm","phrase":"24mm-equivalent wide-lens look, broad field of view and natural verticals"},{"id":"35mm","label":"35mm","phrase":"35mm-equivalent look"},{"id":"50mm","label":"50mm","phrase":"50mm-equivalent natural field of view"},{"id":"85mm","label":"85mm portrait","phrase":"85mm-equivalent portrait look, narrower field of view and gently compressed background"},{"id":"200mm","label":"Telephoto 200mm","phrase":"telephoto 200mm-equivalent look, tight field of view and compressed perspective"},{"id":"fisheye","label":"Fisheye","phrase":"fisheye look with strong barrel distortion"},{"id":"macro","label":"Macro","phrase":"macro, extreme close detail"},{"id":"tilt_shift","label":"Tilt-shift","phrase":"tilt-shift look"}]},{"id":"focus","label":"Focus / depth","options":[{"id":"shallow","label":"Shallow DOF bokeh","phrase":"shallow depth of field with soft bokeh"},{"id":"deep","label":"Deep focus","phrase":"deep focus, sharp from foreground to background"}]},{"id":"style","label":"Style framing","options":[{"id":"symmetrical","label":"Symmetrical centered","phrase":"symmetrical, subject centered in the frame"},{"id":"thirds","label":"Rule of thirds","phrase":"rule-of-thirds framing"},{"id":"selfie","label":"Selfie","phrase":"handheld selfie framing"},{"id":"mirror_selfie","label":"Mirror selfie","phrase":"mirror-selfie framing"},{"id":"cctv","label":"CCTV","phrase":"CCTV / security-camera framing, slightly high and wide"},{"id":"gopro","label":"GoPro","phrase":"GoPro-style wide action framing"}]}]};

function syncRoles() {
  if (!state.roles) state.roles = [];
  while (state.roles.length < state.images.length) state.roles.push("auto");
  if (state.roles.length > state.images.length) state.roles.length = state.images.length;
}

function setEditKind(kind) {
  state.refMode = kind === "ref";
  document.querySelectorAll("#edit-kind button").forEach((b) => {
    const on = (b.dataset.editKind === "ref") === state.refMode;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const lead = $("images-lead");
  if (lead) {
    lead.textContent = state.refMode
      ? "All pictures are references only — there is no base image to edit. Describe a new scene; the model composes the referenced people, clothes, objects and styles into it. An image is used as the environment only if you tag it Background or the prompt links it with words like background, room or setting. Up to 16 images."
      : "Picture 1 is the image being edited. Drag or long-press a tile to reorder — prompt references follow the numbers you see. Up to 16 images.";
  }
  const camLead = $("camera-lead");
  if (camLead) {
    camLead.textContent = (state.mode === "edit" && !state.refMode)
      ? "Pick one option per group. In Edit, this re-shoots the same scene of Picture 1 from the new viewpoint, keeping identity and scene contents consistent."
      : "Pick one option per group. These camera notes are added to the prompt that is sent.";
  }
  if ($("assist-toggle-wrap")) $("assist-toggle-wrap").classList.toggle("hidden", state.mode !== "edit");
  if ($("assist-hint")) $("assist-hint").classList.toggle("hidden", state.mode !== "edit");
  if (typeof renderResMenu === "function") renderResMenu();
  if (typeof scheduleOutputSize === "function") scheduleOutputSize();
  renderSlots();
  scheduleAssistPreview();
}

function roleOptions() {
  return [
    ["auto", "Auto"],
    ["subject", "Subject"],
    ["clothing", "Clothing"],
    ["accessory", "Accessory"],
    ["object", "Object"],
    ["style", "Style"],
    ["background", "Background"],
  ];
}

function renderSentPrompt(boxId, textId, noteId, it) {
  const box = $(boxId), text = $(textId), note = $(noteId);
  if (!box || !text) return;
  const sent = (it && (it.prompt_sent || it.prompt || it.enhanced_prompt || "")) || "";
  const user = (it && (it.prompt_user || "")) || "";
  if (!sent) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  text.textContent = sent;
  let n = "";
  if (it && it.enhanced_prompt && it.pe) n = "Prompt Enhancer expanded this.";
  else if (it && it.prompt_assist && it.prompt_assist !== user) n = "Auto understanding added reference notes.";
  if (it && it.camera && Object.keys(it.camera).length) n = (n ? n + " " : "") + "Camera notes are included.";
  if (note) note.textContent = n;
}

function renderCameraChips() {
  const row = $("camera-chips");
  const btn = $("camera-open");
  if (!row) return;
  const sel = state.camera || {};
  const cats = (CAMERA_CATALOG.categories || []);
  row.innerHTML = "";
  let count = 0;
  cats.forEach((cat) => {
    const id = sel[cat.id];
    if (!id) return;
    const opt = (cat.options || []).find((o) => o.id === id);
    if (!opt) return;
    count++;
    const chip = document.createElement("span");
    chip.className = "camera-chip";
    chip.innerHTML = "<span>" + opt.label.replace(/</g, "&lt;") + "</span>";
    const x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", "Remove " + opt.label);
    x.textContent = "×";
    x.addEventListener("click", () => {
      delete state.camera[cat.id];
      renderCameraChips();
      renderCameraSheet();
      scheduleAssistPreview();
    });
    chip.appendChild(x);
    row.appendChild(chip);
  });
  row.hidden = count === 0;
  if (btn) btn.classList.toggle("is-on", count > 0);
}

function cameraIcon(cat, id) {
  const base = '<rect x="8" y="15" width="20" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="18" cy="21.5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M13 15l2-4h6l2 4" fill="none" stroke="currentColor" stroke-width="1.6"/>';
  let extra = "";
  if (cat === "angle") extra = '<path d="M6 30h24" stroke="currentColor" stroke-width="1.4"/><path d="M18 8v6" stroke="currentColor" stroke-width="1.4"/>';
  if (id === "low_angle" || id === "worms_eye") extra = '<path d="M8 28l10-8 10 8" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (id === "high_angle" || id === "birds_eye" || id === "overhead_flat_lay") extra = '<path d="M8 10l10 8 10-8" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (id === "dutch") extra = '<path d="M6 12l24 12" stroke="currentColor" stroke-width="1.5"/>';
  if (cat === "shot") extra = '<rect x="10" y="10" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (id === "ecu" || id === "cu") extra = '<rect x="13" y="13" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.6"/>';
  if (id === "wide" || id === "ews") extra = '<rect x="6" y="12" width="24" height="14" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (cat === "move") extra = '<path d="M8 18h20M24 12l6 6-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (id === "orbit_left_45" || id === "orbit_left_90") extra = '<path d="M26 18A8 8 0 1 1 18 10" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M14 8l4 2-2 4" fill="none" stroke="currentColor" stroke-width="1.4"/>';
  if (id === "orbit_right_45" || id === "orbit_right_90") extra = '<path d="M10 18A8 8 0 1 0 18 10" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M22 8l-4 2 2 4" fill="none" stroke="currentColor" stroke-width="1.4"/>';
  if (id === "aerial") extra = '<circle cx="18" cy="18" r="3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M18 6v6M18 24v6M6 18h6M24 18h6" stroke="currentColor" stroke-width="1.4"/>';
  if (cat === "lens") extra = '<circle cx="18" cy="18" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="18" cy="18" r="4" fill="none" stroke="currentColor" stroke-width="1.5"/>';
  if (id === "fisheye") extra = '<circle cx="18" cy="18" r="10" fill="none" stroke="currentColor" stroke-width="1.5"/><ellipse cx="18" cy="18" rx="4" ry="10" fill="none" stroke="currentColor" stroke-width="1.3"/>';
  if (cat === "focus" && id === "shallow") extra = '<circle cx="18" cy="18" r="3" fill="currentColor"/><circle cx="18" cy="18" r="9" fill="none" stroke="currentColor" stroke-width="1.2" opacity=".45"/>';
  if (cat === "focus" && id === "deep") extra = '<circle cx="12" cy="18" r="2" fill="currentColor"/><circle cx="18" cy="18" r="2" fill="currentColor"/><circle cx="24" cy="18" r="2" fill="currentColor"/>';
  if (cat === "style" && id === "thirds") extra = '<rect x="7" y="7" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M14.3 7v22M21.7 7v22M7 14.3h22M7 21.7h22" stroke="currentColor" stroke-width="1.1"/>';
  if (cat === "style" && id === "symmetrical") extra = '<rect x="7" y="7" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M18 7v22M7 18h22" stroke="currentColor" stroke-width="1.1"/>';
  if (!extra) extra = base;
  return '<svg viewBox="0 0 36 36" aria-hidden="true">' + (extra === base ? extra : (base + extra)) + "</svg>";
}

function renderCameraSheet() {
  const body = $("camera-body");
  if (!body) return;
  body.innerHTML = "";
  const sel = state.camera || {};
  (CAMERA_CATALOG.categories || []).forEach((cat) => {
    const group = document.createElement("section");
    group.className = "camera-group";
    const h = document.createElement("h3");
    h.textContent = cat.label;
    group.appendChild(h);
    const tiles = document.createElement("div");
    tiles.className = "camera-tiles";
    (cat.options || []).forEach((opt) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "camera-tile" + (sel[cat.id] === opt.id ? " is-on" : "");
      b.dataset.cat = cat.id;
      b.dataset.id = opt.id;
      b.innerHTML = cameraIcon(cat.id, opt.id) + "<span></span>";
      b.querySelector("span").textContent = opt.label;
      b.addEventListener("click", () => {
        if (!state.camera) state.camera = {};
        if (state.camera[cat.id] === opt.id) delete state.camera[cat.id];
        else state.camera[cat.id] = opt.id;
        renderCameraChips();
        renderCameraSheet();
        scheduleAssistPreview();
      });
      tiles.appendChild(b);
    });
    group.appendChild(tiles);
    body.appendChild(group);
  });
}

function openCameraSheet() {
  renderCameraSheet();
  if ($("camera-sheet")) $("camera-sheet").classList.remove("hidden");
}
function closeCameraSheet() {
  if ($("camera-sheet")) $("camera-sheet").classList.add("hidden");
}
function clearCamera() {
  state.camera = {};
  renderCameraChips();
  renderCameraSheet();
  scheduleAssistPreview();
}

let _assistTimer = null;
function scheduleAssistPreview() {
  if (_assistTimer) clearTimeout(_assistTimer);
  _assistTimer = setTimeout(refreshAssistPreview, 280);
}

async function refreshAssistPreview() {
  const box = $("assist-preview");
  if (!box) return;
  const assistOn = state.mode === "edit" && !($("auto-assist") && !$("auto-assist").checked);
  const hasCam = Object.keys(state.camera || {}).length > 0;
  if (!assistOn && !hasCam) {
    box.classList.add("hidden");
    return;
  }
  const prompt = ($("prompt") && $("prompt").value.trim()) || "";
  if (!prompt) { box.classList.add("hidden"); return; }
  try {
    const res = await api("/api/assist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({
        prompt,
        n_images: state.images.length,
        images: (state.images || []).map(function (r) { return r && r.name; }).filter(Boolean),
        image_roles: state.roles || [],
        ref_mode: !!state.refMode,
        enabled: !($("auto-assist") && !$("auto-assist").checked),
        camera: state.camera || {},
        mode: state.refMode ? "ref" : (state.mode === "edit" ? "edit" : "t2i"),
      }, (typeof currentAssistSize === "function" ? currentAssistSize() : {}))),
    });
    if (res.auto_ref) {
      if (!state.refMode) setEditKind("ref");
    }
    const appended = (res.appended || "") + (res.camera_block ? ((res.appended ? "\n\n" : "") + res.camera_block) : "");
    if (!appended && !res.auto_ref && !res.layout_hint) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    const kind = (res.intent || "character_sheet").replace(/_/g, " ");
    const layoutHint = res.layout_hint || (res.layout && res.layout.hint) || "";
    const note = res.auto_ref
      ? (kind.charAt(0).toUpperCase() + kind.slice(1) + " detected — switched to Reference mode. "
        + (layoutHint ? ("Layout: " + layoutHint + ".") : "The panel layout follows the resolution you picked.")
        + " All pictures are identity references.")
      : "";
    box.innerHTML = (note ? "<strong>" + note + "</strong>" : "<strong>Will also send</strong>") + "<p></p>";
    box.querySelector("p").textContent = appended || "";
    if (!appended) box.querySelector("p").remove();
  } catch (e) {
    box.classList.add("hidden");
  }
}

function bindAssistCamera() {
  if ($("auto-assist")) {
    $("auto-assist").checked = state.autoAssist !== false;
    $("auto-assist").addEventListener("change", () => {
      state.autoAssist = $("auto-assist").checked;
      scheduleAssistPreview();
    });
  }
  document.querySelectorAll("#edit-kind button").forEach((b) => {
    b.addEventListener("click", () => setEditKind(b.dataset.editKind || "edit"));
  });
  if ($("camera-open")) $("camera-open").addEventListener("click", openCameraSheet);
  if ($("camera-close")) $("camera-close").addEventListener("click", closeCameraSheet);
  if ($("camera-backdrop")) $("camera-backdrop").addEventListener("click", closeCameraSheet);
  if ($("camera-clear")) $("camera-clear").addEventListener("click", clearCamera);
  if ($("prompt")) $("prompt").addEventListener("input", scheduleAssistPreview);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeCameraSheet();
  });
  if (typeof setEditKind === "function") setEditKind(state.refMode ? "ref" : "edit");
  renderCameraChips();
  if (state.mode === "t2i") {
    if ($("assist-toggle-wrap")) $("assist-toggle-wrap").classList.add("hidden");
    if ($("assist-hint")) $("assist-hint").classList.add("hidden");
  }
}


(async function init() {
  bind();
  bindExtras();
  if (typeof bindAssistCamera === "function") bindAssistCamera();
  renderAspects();
  renderCustomMp();
  if (typeof renderResMenu === "function") renderResMenu();
  if (typeof scheduleOutputSize === "function") scheduleOutputSize();
  renderSlots();
  const keepSize = REMEMBER_STATE.reduce((o, k) => { o[k] = state[k]; return o; }, {});
  setMode(state.mode === "edit" ? "edit" : "t2i");
  try { await loadConfig(); } catch (e) { showError("Could not load settings: " + e.message); }
  if (window.__restoreRemembered) window.__restoreRemembered(keepSize);
  refreshStatus();
  setInterval(refreshStatus, 8000);
})();


/* ===== Comfy Studio UI shell v20261111d =====
   One layer that owns layout behaviour. It never moves elements between containers
   (the cause of the old overlapping-controls bug); the HTML is already in its final
   place and CSS handles phone vs desktop. Base functions are wrapped through their
   global names, which the base code looks up at call time. */

const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};

/* ---------- settings mode: Auto (sampling follows the loaded model) or Manual (your own values, per engine) ---------- */
const TUNE_KEYS = ["steps", "cfg", "sampler_name", "scheduler"];
function tuneMode() { return LS.get("cs.tune.mode", "auto") === "manual" ? "manual" : "auto"; }
function tuneKey(id) { return "cs.manual." + (id && id !== "none" ? id : "builtin"); }
function saveManual(id) {
  const v = {};
  TUNE_KEYS.forEach((k) => { const el = $(k); if (el) v[k] = el.value; });
  LS.set(tuneKey(id), JSON.stringify(v));
}
function restoreManual(id) {
  let v = null;
  try { v = JSON.parse(LS.get(tuneKey(id), "null")); } catch (e) {}
  if (!v) return false;
  TUNE_KEYS.forEach((k) => {
    const el = $(k);
    if (!el || v[k] == null || v[k] === "") return;
    if (el.tagName === "SELECT") { if ([...el.options].some((o) => o.value === String(v[k]))) el.value = String(v[k]); }
    else el.value = v[k];
  });
  return true;
}
const mqPhone = window.matchMedia("(max-width: 719px)");
const mqTouch = window.matchMedia("(hover: none)");
function isPhoneUi() { return mqPhone.matches; }

/* ---------- dock height + keyboard ---------- */
function syncDockHeight() {
  const inner = document.querySelector(".dock-inner");
  if (!inner) return;
  const h = Math.ceil(inner.getBoundingClientRect().height || 0);
  document.documentElement.style.setProperty("--dock-h", (h + 40) + "px");
}
function syncKeyboardInset() {
  const vv = window.visualViewport;
  let kb = 0;
  if (vv) kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
  if (kb < 60) kb = 0;
  document.documentElement.style.setProperty("--kb", kb + "px");
}

/* ---------- popovers (desktop: anchored menu, phone: bottom sheet) ---------- */
let _openPop = null;
let _openAnchor = null;

function placePop(pop, anchor) {
  if (!pop) return;
  if (isPhoneUi() || !anchor) {
    pop.style.left = pop.style.top = pop.style.bottom = pop.style.maxHeight = "";
    return;
  }
  const r = anchor.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const w = pop.offsetWidth;
  const left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), vw - w - 12);
  const above = r.top - 16, below = vh - r.bottom - 16;
  pop.style.left = left + "px";
  if (above >= below) {
    pop.style.top = "auto";
    pop.style.bottom = (vh - r.top + 8) + "px";
    pop.style.maxHeight = Math.max(240, above) + "px";
  } else {
    pop.style.bottom = "auto";
    pop.style.top = (r.bottom + 8) + "px";
    pop.style.maxHeight = Math.max(240, below) + "px";
  }
}

function showPop(id, anchor) {
  const pop = $(id);
  if (!pop) return;
  if (_openPop === pop) { hidePops(); return; }
  hidePops();
  closeAddMenu();
  pop.classList.remove("hidden");
  pop.hidden = false;
  $("pop-backdrop").classList.remove("hidden");
  _openPop = pop;
  _openAnchor = anchor || null;
  if (anchor) anchor.setAttribute("aria-expanded", "true");
  placePop(pop, anchor);
}

function hidePops() {
  if (!_openPop) return;
  const pop = _openPop;
  _openPop = null;
  if (pop.id === "res-menu" && typeof _baseCloseRes === "function") _baseCloseRes();
  pop.classList.add("hidden");
  pop.hidden = true;
  $("pop-backdrop").classList.add("hidden");
  if (_openAnchor) _openAnchor.setAttribute("aria-expanded", "false");
  _openAnchor = null;
}

const _baseOpenRes = openResMenu;
const _baseCloseRes = closeResMenu;
openResMenu = function () {
  hidePops();
  closeAddMenu();
  _baseOpenRes();
  if ($("res-backdrop")) { $("res-backdrop").classList.add("hidden"); $("res-backdrop").hidden = true; }
  _openPop = $("res-menu");
  _openAnchor = $("res-trigger");
  $("pop-backdrop").classList.remove("hidden");
  placePop(_openPop, _openAnchor);
};
closeResMenu = function () {
  _baseCloseRes();
  if (_openPop && _openPop.id === "res-menu") {
    _openPop = null;
    _openAnchor = null;
    $("pop-backdrop").classList.add("hidden");
  }
};

/* Old patch layers exposed these; keep harmless aliases. */
function closePops() { hidePops(); }
function closeSettings() { hidePops(); }
function openSettings() { showPop("settings-pop", $("settings-gear")); }

/* ---------- + panel ---------- */
const _attached = {}; /* library id -> input name */

openAddMenu = function () {
  const p = $("add-menu");
  if (!p.classList.contains("hidden")) { closeAddMenu(); return; }
  hidePops();
  p.classList.remove("hidden");
  loadAddGrid();
  syncDockHeight();
};
closeAddMenu = function () {
  const p = $("add-menu");
  if (p) p.classList.add("hidden");
  syncDockHeight();
};

async function loadAddGrid() {
  const grid = $("add-grid");
  if (!grid) return;
  grid.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api("/api/gallery?mode=all");
    const items = (data && data.items) || [];
    grid.innerHTML = "";
    if (!items.length) {
      grid.innerHTML = '<p class="muted">Your library is empty. Use From PC or Upload.</p>';
      return;
    }
    const names = new Set((state.images || []).map((im) => im && im.name));
    items.slice(0, 150).forEach((it) => {
      if (!it || !it.id) return;
      const b = document.createElement("button");
      b.type = "button";
      b.title = it.prompt_user || it.prompt || "";
      b.setAttribute("aria-label", "Attach " + (it.prompt_user || it.prompt || "image"));
      const on = _attached[it.id] && names.has(_attached[it.id]);
      if (!on) delete _attached[it.id];
      b.classList.toggle("is-on", !!on);
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.src = it.thumb || it.url || "";
      b.appendChild(img);
      b.addEventListener("click", () => toggleAttach(it, b));
      grid.appendChild(b);
    });
  } catch (e) {
    grid.innerHTML = '<p class="muted">Couldn\'t load the library.</p>';
  }
}

async function toggleAttach(it, btn) {
  const name = _attached[it.id];
  if (name) {
    const i = state.images.findIndex((im) => im && im.name === name);
    if (i >= 0) {
      state.images.splice(i, 1);
      if (state.roles) state.roles.splice(i, 1);
    }
    delete _attached[it.id];
    btn.classList.remove("is-on");
    renderSlots(); renderAliasPreview();
    return;
  }
  if (state.images.length >= 16) { toast("16 pictures is the most this model takes."); return; }
  btn.classList.add("is-on");
  try {
    const item = await api("/api/library/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: it.id }),
    });
    if (!item || item.error) throw new Error((item && item.error) || "Import failed");
    state.images.push(item);
    _attached[it.id] = item.name;
    prefetchAppearance(item.name);
    renderSlots(); renderAliasPreview();
  } catch (e) {
    btn.classList.remove("is-on");
    toast(e.message || "Couldn't add that image");
  }
}

/* ---------- pills ---------- */
function syncPills() {
  const peOn = !!($("pe") && $("pe").checked);
  const peMode = state.peMode === "quality" ? "quality" : "fast";
  if ($("quality-pill-label")) $("quality-pill-label").textContent = peOn ? (peMode === "quality" ? "Quality" : "Fast") : "Off";
  if ($("quality-pill")) $("quality-pill").classList.toggle("is-on", peOn);
  document.querySelectorAll("#pe-modes button").forEach((b) => {
    const m = b.dataset.peMode;
    b.classList.toggle("is-on", m === "off" ? !peOn : (peOn && m === peMode));
  });

  if ($("kind-pill-label")) $("kind-pill-label").textContent = state.refMode ? "Reference" : "Edit Picture 1";

  const cam = Object.keys(state.camera || {}).filter((k) => state.camera[k]).length;
  if ($("camera-pill-label")) $("camera-pill-label").textContent = cam ? (cam + " set") : "Any";
  if ($("camera-open")) $("camera-open").classList.toggle("is-on", cam > 0);

  const roles = (state.roles || []).slice(0, state.images.length);
  const setRoles = roles.filter((r, i) => r && r !== "auto" && !(i === 0 && !state.refMode)).length;
  if ($("roles-pill-label")) $("roles-pill-label").textContent = setRoles ? (setRoles + " set") : "Auto";
  if ($("roles-pill")) {
    $("roles-pill").classList.toggle("is-on", setRoles > 0);
    $("roles-pill").classList.toggle("hidden", state.images.length < 2 && !state.refMode);
  }
  if ($("mark-pill")) $("mark-pill").classList.add("hidden");   // retired: Inpaint's box does this, with a real mask

  const rnd = $("random-seed");
  const seedVal = $("seed") ? $("seed").value : "";
  if ($("seed-pill-label")) $("seed-pill-label").textContent = (rnd && !rnd.checked) ? (seedVal || "0") : "Random";
  if ($("seed-pill")) $("seed-pill").classList.toggle("is-on", !!(rnd && !rnd.checked));
}

function syncPlaceholder() {
  const ta = $("prompt");
  if (!ta) return;
  ta.placeholder = state.mode !== "edit"
    ? "Type to imagine…"
    : (state.refMode ? "Describe a new scene · @ to use a picture" : "Describe the edit · @ to use a picture");
}

function renderRolesPop() {
  const list = $("roles-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.images.length) {
    list.innerHTML = '<p class="pop-note">Add pictures first.</p>';
    return;
  }
  syncRoles();
  state.images.forEach((im, i) => {
    const row = document.createElement("div");
    row.className = "roles-item";
    const img = document.createElement("img");
    img.alt = "";
    img.src = im.url || im.thumb || "";
    const lab = document.createElement("span");
    lab.textContent = "Picture " + (i + 1);
    row.append(img, lab);
    if (i === 0 && !state.refMode) {
      const base = document.createElement("span");
      base.className = "pop-note";
      base.style.flex = "0 0 auto";
      base.textContent = "Base image";
      row.appendChild(base);
    } else {
      const sel = document.createElement("select");
      sel.setAttribute("aria-label", "Role for Picture " + (i + 1));
      roleOptions().forEach(([v, n]) => {
        const o = document.createElement("option");
        o.value = v; o.textContent = n;
        sel.appendChild(o);
      });
      sel.value = state.roles[i] || "auto";
      sel.addEventListener("change", () => {
        state.roles[i] = sel.value;
        renderSlots();
        scheduleAssistPreview();
      });
      row.appendChild(sel);
    }
    list.appendChild(row);
  });
}

function renderHelp() {
  const ul = $("help-list");
  if (!ul) return;
  const desk = !mqTouch.matches;
  const tips = [];
  if (desk) tips.push("<b>Enter</b> generates. <kbd>Shift</kbd>+<kbd>Enter</kbd> adds a new line.");
  tips.push("Generate is never locked. Queue several jobs and each keeps its own settings. Cancel any of them above the prompt.");
  tips.push("Tap a pill to change it: <b>Size</b>, <b>Enhance</b>, <b>Camera</b>, <b>Seed</b>. Rare settings are under the sliders button.");
  if (state.mode === "edit") {
    tips.push("<b>Picture 1</b> is the image being edited. Drag or long-press a picture to reorder; prompt references follow the numbers.");
    tips.push("Type <b>@</b> to insert a picture, or write Image 2, img2 or @Image2. They become &lt;Picture 2&gt; when sent.");
    tips.push("<b>Reference</b> mode builds a brand-new scene from all pictures. Tag one <b>Background</b> to use it as the setting.");
    tips.push("<b>Swaps</b>: Picture 1 is the photo you keep, Picture 2 gives the head, face, person or outfit. Say \"head swap\", \"face swap\", \"body swap\", \"outfit swap\" or \"put the woman from Picture 2 into Picture 1\".");
    tips.push("<b>Inpaint</b> limits a change to the area you paint or box; everything outside stays as it is.");
    tips.push("Open <b>Will also send</b> under the prompt to see the notes added for you.");
  } else {
    tips.push("Tap <b>+</b> to attach pictures from your library, this PC or this device. That switches to Edit.");
    tips.push("You can also drop or paste images into the prompt box.");
  }
  tips.push("Results open above the recent feed. Prompt sent, enhanced prompt, reuse and job details stay folded until you open them.");
  ul.innerHTML = tips.map((t) => "<li>" + t + "</li>").join("");
}

/* ---------- jobs (live preview + cancel above the composer) ---------- */
const JOB_LABEL = { queued: "Waiting in queue", waiting_comfy: "Waiting for ComfyUI", running: "Generating" };

function resetGenerateUi(note) {
  if ($("go-note") && note != null) $("go-note").textContent = note;
}

async function cancelRun(jobId) {
  const id = jobId || state.jobId;
  if (!id) return;
  try {
    await api("/api/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
  } catch (e) {
    showError(e.message || "Couldn't cancel.");
    return;
  }
  const j = (state.activeJobs || []).find((x) => x.id === id);
  if (j) j.status = "cancelled";
  renderJobQueue();
  resetGenerateUi("Cancelled.");
  pollJobs();
}

function enqueueJob(job) {
  if (!state.activeJobs) state.activeJobs = [];
  if (!state.jobSnaps) state.jobSnaps = {};
  state.jobSnaps[job.id] = {
    prompt: job.prompt_user || job.prompt || "",
    thumbs: state.mode === "edit" ? (state.images || []).map((im) => im.url).slice(0, 1) : [],
  };
  state.activeJobs = state.activeJobs.filter((j) => j.id !== job.id);
  state.activeJobs.push(job);
  state.jobId = job.id;
  renderJobQueue();
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(pollJobs, 500);
  pollJobs();
}

function renderJobQueue() {
  const box = $("job-queue");
  if (!box) return;
  const active = (state.activeJobs || []).filter((j) => j && ["queued", "waiting_comfy", "running"].includes(j.status));
  box.classList.toggle("hidden", !active.length);
  const main = active.find((j) => j.status === "running") || active.find((j) => j.status === "waiting_comfy") || active[0];
  const shown = main ? [main] : [];
  const keep = new Set(shown.map((j) => j.id));
  [...box.children].forEach((c) => { if (c.dataset.id && !keep.has(c.dataset.id)) c.remove(); });
  shown.forEach((job) => {
    let card = [...box.children].find((c) => c.dataset.id === job.id);
    const snap = (state.jobSnaps && state.jobSnaps[job.id]) || {};
    if (!card) {
      card = document.createElement("div");
      card.className = "job-card";
      card.dataset.id = job.id;
      card.innerHTML =
        '<img class="job-thumb" alt="Live preview" hidden><div class="job-ph"></div>' +
        '<div class="job-meta"><p class="job-prompt"></p><p class="job-msg"></p><span class="job-bar"><i></i></span></div>' +
        '<button type="button" class="job-cancel">Cancel</button>';
      card.querySelector(".job-cancel").addEventListener("click", (e) => { e.preventDefault(); cancelRun(job.id); });
      card.querySelector(".job-thumb").addEventListener("click", (e) => { openResultFs(e.currentTarget.src); });
      if (snap.thumbs && snap.thumbs[0]) {
        const t = card.querySelector(".job-thumb");
        t.src = snap.thumbs[0];
        t.hidden = false;
        card.querySelector(".job-ph").hidden = true;
      }
      box.appendChild(card);
    }
    card.classList.toggle("is-live", job.status === "running");
    card.querySelector(".job-ph").textContent = job.status === "queued" ? "Queued" : "…";
    card.querySelector(".job-prompt").textContent = job.prompt_user || snap.prompt || job.prompt || "Image";
    const max = job.progress_max || 0, v = job.progress || 0;
    const label = job.message || JOB_LABEL[job.status] || job.status;
    card.querySelector(".job-msg").textContent = max ? (label + " · " + v + " / " + max) : label;
    card.querySelector(".job-bar i").style.width = (max ? Math.min(100, (100 * v) / max) : (job.status === "running" ? 8 : 3)) + "%";
    if (job.has_preview) {
      const now = Date.now();
      if (!card._liveAt || now - card._liveAt > 650) {
        card._liveAt = now;
        const url = "/api/live?id=" + encodeURIComponent(job.id) + "&t=" + now;
        const pre = new Image();
        pre.onload = () => {
          const t = card.querySelector(".job-thumb");
          t.src = url;
          t.hidden = false;
          card.querySelector(".job-ph").hidden = true;
        };
        pre.src = url;
      }
    }
  });
  // "N queued" button on the main card -> the Queue panel
  const waiting = active.length - shown.length;
  let more = box.querySelector(".job-more");
  if (waiting > 0) {
    if (!more) {
      more = document.createElement("button");
      more.type = "button";
      more.className = "job-more";
      more.setAttribute("aria-haspopup", "dialog");
      more.addEventListener("click", (e) => { renderQueuePop(); showPop("queue-pop", e.currentTarget); });
    }
    const card = main && [...box.children].find((c) => c.dataset.id === main.id);
    if (card && more.parentNode !== card) card.appendChild(more);
    more.textContent = waiting + " queued \u25BE";
    more.title = "See and cancel the " + waiting + " job" + (waiting === 1 ? "" : "s") + " waiting";
  } else if (more) {
    more.remove();
  }
  if ($("queue-pop") && !$("queue-pop").hidden) renderQueuePop();
  syncDockHeight();
}

/* The Queue panel: every job this device started that hasn't finished, newest last, each with Cancel. */
function renderQueuePop() {
  const list = $("queue-list");
  if (!list) return;
  const active = (state.activeJobs || []).filter((j) => j && ["queued", "waiting_comfy", "running"].includes(j.status));
  $("queue-count").textContent = active.length ? active.length + " job" + (active.length === 1 ? "" : "s") : "Nothing waiting";
  list.innerHTML = "";
  if (!active.length) { list.innerHTML = '<p class="pop-note">Nothing is generating or waiting.</p>'; hidePops(); return; }
  let n = 0;
  active.forEach((job) => {
    const snap = (state.jobSnaps && state.jobSnaps[job.id]) || {};
    const row = document.createElement("div");
    row.className = "queue-row" + (job.status === "running" ? " is-live" : "");
    const max = job.progress_max || 0, v = job.progress || 0;
    const pos = job.status === "running" ? "Generating" + (max ? " \u00b7 " + Math.round(100 * v / max) + "%" : "")
      : (job.status === "waiting_comfy" ? "Waiting for ComfyUI" : "Queued #" + (++n));
    row.innerHTML = '<div class="queue-main"><span class="queue-status"></span><span class="queue-prompt"></span>'
      + '<span class="job-bar"><i></i></span></div><button type="button" class="chip-btn queue-cancel">Cancel</button>';
    row.querySelector(".queue-status").textContent = pos + (job.workflow_name ? " \u00b7 " + String(job.workflow_name).split(" \u00b7 ")[0] : "");
    row.querySelector(".queue-prompt").textContent = job.prompt_user || snap.prompt || job.prompt || "(no prompt)";
    row.querySelector(".job-bar i").style.width = (max ? Math.min(100, 100 * v / max) : (job.status === "running" ? 8 : 0)) + "%";
    row.querySelector(".job-bar").hidden = job.status !== "running";
    row.querySelector(".queue-cancel").addEventListener("click", async (e) => {
      e.currentTarget.disabled = true;
      await cancelRun(job.id);
      renderQueuePop();
    });
    list.appendChild(row);
  });
}

async function pollJobs() {
  const tracked = (state.activeJobs || []).slice();
  if (!tracked.length) {
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
    renderJobQueue();
    return;
  }
  let list;
  try { list = await api("/api/jobs"); } catch (e) { return; }
  const byId = {};
  (list || []).forEach((j) => { byId[j.id] = j; });
  const next = [], finished = [];
  tracked.forEach((t) => {
    const job = byId[t.id] || t;
    if (["queued", "waiting_comfy", "running"].includes(job.status)) next.push(job);
    else finished.push(job);
  });
  state.activeJobs = next;
  state.jobId = next.length ? next[next.length - 1].id : null;
  renderJobQueue();
  finished.forEach((job) => {
    if (job.status === "success") {
      try { showResult(job); } catch (e) {}
    } else if (job.status === "cancelled") {
      resetGenerateUi("Cancelled.");
    } else if (job.error) {
      showError(job.error);
    }
  });
  if (!next.length && state.poll) { clearInterval(state.poll); state.poll = null; }
}
function pollJob() { return pollJobs(); }

/* Progress lives in the job card; keep the result on screen while new jobs run. */
showProgress = function () {};

/* ---------- errors ---------- */
let _alertTimer = 0;
showError = function (msg) {
  const box = $("dock-alert");
  if (state.view !== "create" || !box) { toast(msg, 3200); return; }
  $("error").textContent = msg || "Something went wrong (no details were sent back).";
  $("error").classList.remove("hidden");   // Generate hides the text line; without this the box showed empty
  box.classList.remove("hidden");
  clearTimeout(_alertTimer);
  _alertTimer = setTimeout(hideAlert, 9000);
  syncDockHeight();
};
function hideAlert() {
  if ($("dock-alert")) $("dock-alert").classList.add("hidden");
  syncDockHeight();
}

/* ---------- folds ---------- */
function oneLine(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
function setFoldPreview(id, text) {
  const d = $(id);
  if (!d) return;
  const p = d.querySelector(":scope > summary .fold-preview");
  if (p) p.textContent = oneLine(text);
}
function initFolds() {
  document.querySelectorAll("details.fold-block[data-fold]").forEach((d) => {
    if (d._fold) return;
    d._fold = true;
    const key = "cs.fold." + d.dataset.fold;
    if (LS.get(key, "0") === "1") d.setAttribute("open", "");
    else d.removeAttribute("open");
    d.addEventListener("toggle", () => {
      LS.set(key, d.open ? "1" : "0");
      if (d.closest(".dock")) syncDockHeight();
    });
  });
}

function fillJobDetails(job) {
  const bits = [];
  if (job.mode) bits.push(job.mode === "edit" ? (job.ref_mode ? "Reference" : "Edit") : "Text to image");
  if (job.width && job.height) bits.push(job.width + " × " + job.height);
  if (job.seed != null && job.seed !== "") bits.push("Seed " + job.seed);
  if (job.steps) bits.push(job.steps + " steps");
  if (job.sampler_name) bits.push(job.sampler_name + (job.scheduler ? " / " + job.scheduler : ""));
  if (job.pe || job.enhanced_prompt) bits.push("Enhance " + (job.pe_mode === "quality" ? "Quality" : "Fast"));
  const t = bits.join(" · ");
  $("job-details").classList.toggle("hidden", !t);
  $("job-details-text").textContent = t;
  setFoldPreview("job-details", t);
}

/* ---------- result + feed ---------- */
const _baseShowResult = showResult;
showResult = function (job) {
  const r = _baseShowResult.apply(this, arguments);
  document.body.classList.add("has-result");
  hideAlert();
  try {
    fillJobDetails(job);
    setFoldPreview("sent-prompt-box", $("sent-prompt-text").textContent);
    setFoldPreview("enhanced-box", $("enhanced-text").textContent);
    state.lastSeed = job.seed;
  } catch (e) {}
  if (state.view === "create") window.scrollTo({ top: 0, behavior: "smooth" });
  return r;
};

refreshRecent = async function () {
  const strip = $("recent-strip");
  const row = $("recent-row");
  if (!strip || !row) return;
  try {
    const data = await api("/api/gallery?mode=all");
    const items = (data.items || []).slice(0, 60);
    state.recentItems = items;
    if (items.length && !state.lastSeed && items[0].seed != null) state.lastSeed = items[0].seed;
    row.innerHTML = "";
    strip.classList.toggle("hidden", !items.length);
    document.body.classList.toggle("has-feed", items.length > 0);
    items.forEach((it, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.title = it.prompt_user || it.prompt || "";
      const thumb = document.createElement("span");
      thumb.className = "thumb";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = it.prompt_user || it.prompt || "Image";
      if (it.width && it.height) { img.width = it.width; img.height = it.height; }
      img.src = it.thumb;
      thumb.appendChild(img);
      b.appendChild(thumb);
      b.addEventListener("click", () => { state.library = items; openViewer(i); });
      row.appendChild(b);
    });
  } catch (e) {
    strip.classList.add("hidden");
  }
};

const _baseOpenViewer = openViewer;
openViewer = function (index) {
  const r = _baseOpenViewer.apply(this, arguments);
  const it = state.library[state.viewerIndex];
  if (it) {
    const meta = oneLine($("viewer-meta").textContent);
    $("viewer-job-details").classList.toggle("hidden", !meta);
    setFoldPreview("viewer-job-details", meta);
    setFoldPreview("viewer-enhanced", it.enhanced_prompt || "");
    setFoldPreview("viewer-sent-box", $("viewer-sent-text").textContent);
    $("viewer-edit").classList.remove("hidden");
  }
  return r;
};
const _baseCloseViewer = closeViewer;
closeViewer = function () {
  _baseCloseViewer();
  if (state.view === "create") refreshRecent();
};

/* ---------- assist preview ("Will also send") ---------- */
refreshAssistPreview = async function () {
  const box = $("assist-preview");
  if (!box) return;
  const assistOn = state.mode === "edit" && !($("auto-assist") && !$("auto-assist").checked);
  const hasCam = Object.keys(state.camera || {}).length > 0;
  const prompt = ($("prompt").value || "").trim();
  if ((!assistOn && !hasCam) || !prompt) { box.classList.add("hidden"); syncDockHeight(); return; }
  try {
    const res = await api("/api/assist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({
        prompt,
        n_images: state.images.length,
        images: (state.images || []).map((r) => r && r.name).filter(Boolean),
        image_roles: state.roles || [],
        ref_mode: !!state.refMode,
        enabled: !($("auto-assist") && !$("auto-assist").checked),
        camera: state.camera || {},
        mode: state.refMode ? "ref" : (state.mode === "edit" ? "edit" : "t2i"),
      }, currentAssistSize())),
    });
    if (res.auto_ref && !state.refMode) {
      setEditKind("ref");
      const kind = (res.intent || "character sheet").replace(/_/g, " ");
      toast(kind.charAt(0).toUpperCase() + kind.slice(1) + " detected: switched to Reference", 3200);
    }
    const appended = (res.appended || "") + (res.camera_block ? ((res.appended ? "\n\n" : "") + res.camera_block) : "");
    const layout = res.layout_hint || (res.layout && res.layout.hint) || "";
    const text = appended + (layout ? ((appended ? "\n\n" : "") + "Layout: " + layout) : "");
    if (!text) { box.classList.add("hidden"); syncDockHeight(); return; }
    $("assist-preview-text").textContent = text;
    setFoldPreview("assist-preview", text);
    box.classList.remove("hidden");
  } catch (e) {
    box.classList.add("hidden");
  }
  syncDockHeight();
};

/* ---------- wraps that keep chrome in sync ---------- */
let _lastImgCount = state.images.length;
const _baseRenderSlots = renderSlots;
renderSlots = function () {
  const grew = state.images.length > _lastImgCount;
  _lastImgCount = state.images.length;
  if (grew && state.mode !== "edit") {
    if (typeof window.onImagesAdded === "function") window.onImagesAdded();
    else { setMode("edit"); toast("Switched to Edit"); }
    return;
  }
  const r = _baseRenderSlots.apply(this, arguments);
  document.querySelectorAll("#slots .slot").forEach((el, i) => {
    const tag = el.querySelector(".tag");
    if (tag) tag.dataset.n = String(i + 1);
  });
  if (_openPop && _openPop.id === "roles-pop") renderRolesPop();
  syncPills();
  syncDockHeight();
  return r;
};

const _baseSetMode = setMode;
setMode = function (mode) {
  const r = _baseSetMode.apply(this, arguments);
  document.body.classList.toggle("mode-edit", mode === "edit");
  document.body.classList.toggle("mode-t2i", mode !== "edit");
  syncPlaceholder();
  syncPills();
  hideAlert();
  if (_openPop && _openPop.id === "help-pop") renderHelp();
  syncDockHeight();
  return r;
};

const _baseSetEditKind = setEditKind;
setEditKind = function () {
  const r = _baseSetEditKind.apply(this, arguments);
  syncPlaceholder();
  syncPills();
  return r;
};

const _baseRenderCameraChips = renderCameraChips;
renderCameraChips = function () {
  const r = _baseRenderCameraChips.apply(this, arguments);
  syncPills();
  syncDockHeight();
  return r;
};

const _baseSetView = setView;
setView = function (view) {
  if (view === "workflows") return;
  const r = _baseSetView.apply(this, arguments);
  document.body.classList.toggle("view-library", view === "library");
  document.body.classList.toggle("view-create", view !== "library");
  hidePops();
  closeAddMenu();
  window.scrollTo(0, 0);
  if (view === "create") { refreshRecent(); setTimeout(syncDockHeight, 0); }
  return r;
};

const _baseGenerate = generate;
generate = async function () {
  hideAlert();
  hidePops();
  closeAddMenu();
  return _baseGenerate.apply(this, arguments);
};

/* ---------- mark area ---------- */
const _mark = { box: null };
function openMarkSheet() {
  const im = state.images[0];
  if (!im) { toast("Add Picture 1 first"); return; }
  hidePops();
  $("mark-img").src = im.url || im.thumb || "";
  $("mark-box").classList.add("hidden");
  $("mark-insert").disabled = true;
  $("mark-out").textContent = "Drag on the picture to mark an area.";
  _mark.box = null;
  $("mark-sheet").classList.remove("hidden");
}
function closeMarkSheet() { $("mark-sheet").classList.add("hidden"); }
function markPhrase(b) {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  const h = cx < 0.36 ? "left" : (cx > 0.64 ? "right" : "");
  const v = cy < 0.36 ? "upper" : (cy > 0.64 ? "lower" : "");
  const area = b.w * b.h;
  let pos;
  if (!h && !v) pos = "centre";
  else if (h && v) pos = v + "-" + h;
  else pos = v ? (v === "upper" ? "top" : "bottom") : h;
  const size = area < 0.06 ? "small " : (area > 0.35 ? "large " : "");
  const frac = area > 0.7 ? "" : (" (about " + Math.max(5, Math.round(area * 20) * 5) + "% of the frame)");
  return "the " + size + "area in the " + pos + " of <Picture 1>" + frac;
}
function bindMark() {
  const stage = $("mark-stage");
  let start = null;
  const rel = (e) => {
    const r = $("mark-img").getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  };
  const draw = (b) => {
    const el = $("mark-box");
    el.style.left = (b.x * 100) + "%";
    el.style.top = (b.y * 100) + "%";
    el.style.width = (b.w * 100) + "%";
    el.style.height = (b.h * 100) + "%";
    el.classList.remove("hidden");
  };
  stage.addEventListener("pointerdown", (e) => {
    start = rel(e);
    try { stage.setPointerCapture(e.pointerId); } catch (err) {}
  });
  stage.addEventListener("pointermove", (e) => {
    if (!start) return;
    const p = rel(e);
    const b = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    draw(b);
    _mark.box = b;
  });
  const end = () => {
    if (!start) return;
    start = null;
    if (_mark.box && _mark.box.w > 0.02 && _mark.box.h > 0.02) {
      $("mark-out").textContent = "Will insert: " + markPhrase(_mark.box);
      $("mark-insert").disabled = false;
    }
  };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
  $("mark-insert").addEventListener("click", () => {
    if (!_mark.box) return;
    closeMarkSheet();
    insertAtCursor($("prompt"), markPhrase(_mark.box));
    autoGrow();
    scheduleAssistPreview();
  });
  $("mark-close").addEventListener("click", closeMarkSheet);
  $("mark-backdrop").addEventListener("click", closeMarkSheet);
}

/* ---------- prompt box ---------- */
function autoGrow() {
  const ta = $("prompt");
  if (!ta) return;
  ta.style.height = "auto";
  const max = Math.round(window.innerHeight * 0.38);
  ta.style.height = Math.min(max, ta.scrollHeight) + "px";
  document.body.classList.toggle("prompt-empty", !ta.value.trim());
  syncDockHeight();
}

/* ---------- boot ---------- */
function bindShell() {
  const on = (id, ev, fn) => { const el = $(id); if (el) el.addEventListener(ev, fn); };

  on("pop-backdrop", "click", () => { hidePops(); });
  on("quality-pill", "click", (e) => showPop("quality-pop", e.currentTarget));
  on("kind-pill", "click", (e) => showPop("kind-pop", e.currentTarget));
  on("seed-pill", "click", (e) => showPop("seed-pop", e.currentTarget));
  on("roles-pill", "click", (e) => { renderRolesPop(); showPop("roles-pop", e.currentTarget); });
  on("help-pill", "click", (e) => { renderHelp(); showPop("help-pop", e.currentTarget); });
  on("settings-gear", "click", (e) => showPop("settings-pop", e.currentTarget));
  on("settings-close", "click", hidePops);
  on("mark-pill", "click", openMarkSheet);
  on("add-images", "click", (e) => { e.stopPropagation(); openAddMenu(e.currentTarget); });
  on("add-close", "click", closeAddMenu);
  on("add-library", "click", loadAddGrid);
  on("dock-alert-close", "click", hideAlert);
  on("brand", "click", () => setView("create"));
  on("result-dismiss", "click", () => { $("stage").classList.add("hidden"); document.body.classList.remove("has-result"); });
  document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.goto)));

  /* Enhancer: Off | Fast | Quality drive the hidden checkbox. */
  document.querySelectorAll("#pe-modes button").forEach((b) => b.addEventListener("click", () => {
    const m = b.dataset.peMode;
    $("pe").checked = m !== "off";
    state.peMode = m === "quality" ? "quality" : "fast";
    syncPills();
  }));
  on("pe", "change", syncPills);
  on("random-seed", "change", syncPills);
  on("seed", "input", syncPills);
  on("seed-last", "click", () => {
    const s = (state.reuseItem && state.reuseItem.seed != null) ? state.reuseItem.seed : state.lastSeed;
    if (s == null) { toast("No seed yet"); return; }
    $("random-seed").checked = false;
    $("seed").disabled = false;
    $("seed").value = s;
    syncPills();
  });
  on("auto-assist", "change", syncPills);

  /* Prompt: Enter sends on desktop, autogrow, paste images. */
  const ta = $("prompt");
  ta.addEventListener("input", () => { autoGrow(); hideAlert(); });
  ta.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    const pickerOpen = !$("at-picker").classList.contains("hidden");
    if (e.ctrlKey || e.metaKey || (!e.shiftKey && !mqTouch.matches && !pickerOpen)) {
      e.preventDefault();
      generate();
    }
  });
  ta.addEventListener("paste", (e) => {
    const files = [...((e.clipboardData && e.clipboardData.files) || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  on("alias-norm", "click", (e) => e.currentTarget.classList.toggle("is-open"));

  /* Drop images anywhere on Create. */
  document.addEventListener("dragover", (e) => {
    if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault();
  });
  document.addEventListener("drop", (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
    if (e.target.closest && e.target.closest("#slots")) return; /* base handles the slot row */
    e.preventDefault();
    if (state.view !== "create") return;
    const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
    if (files.length) addFiles(files);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (_openPop) { hidePops(); return; }
    if (!$("add-menu").classList.contains("hidden")) closeAddMenu();
    if (!$("mark-sheet").classList.contains("hidden")) closeMarkSheet();
  });

  window.addEventListener("resize", () => {
    if (_openPop) placePop(_openPop, _openAnchor);
    syncKeyboardInset();
    syncDockHeight();
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncKeyboardInset);
    window.visualViewport.addEventListener("scroll", syncKeyboardInset);
  }
  try { new ResizeObserver(syncDockHeight).observe(document.querySelector(".dock-inner")); } catch (e) {}

  /* Status tooltip (text is hidden on phones). */
  const st = $("status");
  if (st) new MutationObserver(() => { st.title = st.textContent; }).observe(st, { childList: true, characterData: true, subtree: true });

  /* Short status notes fade out. */
  const note = $("go-note");
  if (note) new MutationObserver(() => {
    clearTimeout(note._t);
    if (note.textContent) note._t = setTimeout(() => { note.textContent = ""; syncDockHeight(); }, 5000);
    syncDockHeight();
  }).observe(note, { childList: true, characterData: true, subtree: true });

  bindMark();
}

(function bootShell() {
  try {
    bindShell();
    initFolds();
    document.body.classList.toggle("mode-edit", state.mode === "edit");
    document.body.classList.toggle("mode-t2i", state.mode !== "edit");
    syncPlaceholder();
    syncPills();
    autoGrow();
    syncKeyboardInset();
    refreshRecent();
    syncDockHeight();
    /* loadConfig finishes after this runs; pick up its defaults. */
    setTimeout(syncPills, 600);
    setTimeout(syncPills, 2000);
  } catch (e) {
    console.error("UI shell failed to start", e);
  }
})();
/* ===== end UI shell v20261111d ===== */

/* ===== Touch, swipe and back-button layer v20261111f =====
   Works the same in Brave, Safari, Chrome, Firefox and Samsung Internet:
   plain touch events for swipes, pointer events only for mouse drag,
   and the real visible height from innerHeight (not vh, which iOS measures
   with the toolbars hidden). */
(function () {
  /* ---- real visible height ---- */
  function syncAppHeight() {
    const h = window.innerHeight || document.documentElement.clientHeight;
    if (h) document.documentElement.style.setProperty("--app-h", h + "px");
  }
  syncAppHeight();
  window.addEventListener("resize", syncAppHeight);
  window.addEventListener("orientationchange", () => setTimeout(syncAppHeight, 250));

  /* ---- pill row: fades, wheel, mouse drag ---- */
  const hud = $("hud"), sc = $("hud-scroller");
  function syncHudFades() {
    if (!hud || !sc) return;
    const max = sc.scrollWidth - sc.clientWidth;
    hud.classList.toggle("more-left", sc.scrollLeft > 4);
    hud.classList.toggle("more-right", max - sc.scrollLeft > 4);
  }
  window.syncHudFades = syncHudFades;
  if (sc) {
    sc.addEventListener("scroll", syncHudFades, { passive: true });
    window.addEventListener("resize", syncHudFades);
    try { new ResizeObserver(syncHudFades).observe(sc); } catch (e) {}
    /* Desktop: vertical mouse wheel scrolls the row sideways. */
    sc.addEventListener("wheel", (e) => {
      if (sc.scrollWidth <= sc.clientWidth) return;
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        sc.scrollLeft += e.deltaY;
        e.preventDefault();
      }
    }, { passive: false });
    /* Desktop: click-and-drag the row. Touch uses native scrolling. */
    let drag = null;
    sc.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse" || e.button !== 0) return;
      drag = { x: e.clientX, left: sc.scrollLeft, moved: false, id: e.pointerId };
    });
    window.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) > 5) { drag.moved = true; sc.classList.add("is-dragging"); }
      if (drag.moved) sc.scrollLeft = drag.left - dx;
    });
    window.addEventListener("pointerup", () => {
      if (drag && drag.moved) {
        /* swallow the click that ends a drag */
        const stop = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
        sc.addEventListener("click", stop, { capture: true, once: true });
        setTimeout(() => sc.removeEventListener("click", stop, true), 0);
      }
      drag = null;
      sc.classList.remove("is-dragging");
    });
    syncHudFades();
  }
  /* Start the row at the beginning (Size) whenever the mode changes. */
  const _sm = setMode;
  setMode = function () {
    const r = _sm.apply(this, arguments);
    if (sc) sc.scrollLeft = 0;
    syncHudFades();
    return r;
  };

  /* ---- swipe down to close ---- */
  const NO_SWIPE = "input[type=range], textarea, select, .hud-scroller, .slots, .pc-roots, .crumbs, .mark-stage, #viewer-split, .compare";
  function swipeClose(card, onClose, when) {
    if (!card || card._swipe) return;
    card._swipe = true;
    let s = null;
    card.addEventListener("touchstart", (e) => {
      s = null;
      if (e.touches.length !== 1) return;
      if (when && !when()) return;
      const t = e.target;
      if (t.closest && t.closest(NO_SWIPE)) return;
      for (let el = t; el && el !== card.parentNode; el = el.parentElement) {
        if (el.scrollTop > 0 && el.scrollHeight > el.clientHeight + 1) return; /* let it scroll up first */
      }
      s = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now(), dy: 0, on: false };
    }, { passive: true });
    card.addEventListener("touchmove", (e) => {
      if (!s) return;
      const dx = e.touches[0].clientX - s.x;
      s.dy = e.touches[0].clientY - s.y;
      if (!s.on) {
        if (s.dy < -6 || Math.abs(dx) > 12) { s = null; return; } /* scrolling up or sideways */
        if (s.dy < 8 || Math.abs(dx) > s.dy) return;
        s.on = true;
        card.classList.add("swipe-dragging");
      }
      e.preventDefault();
      card.style.transform = "translateY(" + Math.max(0, s.dy) + "px)";
    }, { passive: false });
    const end = () => {
      if (!s) return;
      const st = s; s = null;
      if (!st.on) return;
      card.classList.remove("swipe-dragging");
      const v = st.dy / Math.max(1, Date.now() - st.t);
      if (st.dy > 110 || (st.dy > 40 && v > 0.45)) {
        card.style.transition = "transform .16s ease-in";
        card.style.transform = "translateY(" + (card.offsetHeight + 40) + "px)";
        setTimeout(() => { card.style.transition = ""; card.style.transform = ""; onClose(); }, 160);
      } else {
        card.style.transition = "transform .18s ease-out";
        card.style.transform = "";
        setTimeout(() => { card.style.transition = ""; }, 200);
      }
    };
    card.addEventListener("touchend", end);
    card.addEventListener("touchcancel", end);
  }
  window.__swipeClose = swipeClose;

  document.querySelectorAll(".pop").forEach((p) => swipeClose(p, () => { hidePops(); closeResMenu(); }, isPhoneUi));
  swipeClose(document.querySelector("#camera-sheet .sheet-card"), closeCameraSheet);
  swipeClose(document.querySelector("#pc-sheet .sheet-card"), closePcPicker);
  swipeClose(document.querySelector("#mark-sheet .sheet-card"), closeMarkSheet);
  swipeClose(document.querySelector("#confirm .sheet-card"), closeConfirm);
  swipeClose(document.querySelector("#copy-sheet .sheet-card"), closeCopySheet);
  swipeClose($("add-menu"), closeAddMenu);
  swipeClose($("viewer-stage"), closeViewer, () => $("viewer-compare").classList.contains("hidden"));
  swipeClose($("pc-preview-stage"), closePcPreview);
  swipeClose(document.querySelector("#result-fs .viewer-stage"), closeResultFs);

  /* Tap the dark area around a full-screen image to close it (desktop + phone). */
  ["viewer-stage", "pc-preview-stage"].forEach((id) => {
    const st = $(id);
    if (st) st.addEventListener("click", (e) => {
      if (e.target !== st) return;
      if (id === "viewer-stage") closeViewer(); else closePcPreview();
    });
  });

  /* ---- Escape closes anything on desktop ---- */
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("confirm").classList.contains("hidden")) closeConfirm();
  });

  /* ---- phone Back button / back-swipe closes the open layer instead of leaving ---- */
  const LAYERS = ["upscale-pop", "lora-pop", "inpaint-sheet", "wf-sheet", "compare-fs", "vsize-pop", "vlen-pop", "viewer", "pc-preview", "result-fs", "pc-sheet", "camera-sheet", "mark-sheet", "confirm", "copy-sheet", "add-menu",
    "res-menu", "quality-pop", "kind-pop", "seed-pop", "roles-pop", "help-pop", "settings-pop"];
  let pushed = false, fromPop = false;
  const anyOpen = () => LAYERS.some((id) => { const el = $(id); return el && !el.classList.contains("hidden") && !el.hidden; });
  function closeAll() {
    try { hidePops(); closeResMenu(); } catch (e) {}
    [closeAddMenu, closeMarkSheet, closeCameraSheet, () => window.closeCompare && window.closeCompare(), () => window.closeWfSheet && window.closeWfSheet(), () => window.closeInpaintEditor && window.closeInpaintEditor(), closeConfirm, closeCopySheet, closeResultFs, closePcPreview].forEach((f) => { try { f(); } catch (e) {} });
    try { if (!$("pc-sheet").classList.contains("hidden")) closePcPicker(); } catch (e) {}
    try { if (!$("viewer").classList.contains("hidden")) closeViewer(); } catch (e) {}
  }
  function onLayerChange() {
    const open = anyOpen();
    if (open && !pushed) {
      try { history.pushState({ csLayer: 1 }, ""); pushed = true; } catch (e) {}
    } else if (!open && pushed && !fromPop) {
      pushed = false;
      try { history.back(); } catch (e) {}
    }
  }
  window.addEventListener("popstate", () => {
    if (!pushed) return;
    pushed = false;
    fromPop = true;
    closeAll();
    setTimeout(() => { fromPop = false; }, 0);
  });
  const mo = new MutationObserver(() => { clearTimeout(mo._t); mo._t = setTimeout(onLayerChange, 30); });
  LAYERS.forEach((id) => { const el = $(id); if (el) mo.observe(el, { attributes: true, attributeFilter: ["class", "hidden"] }); });
})();
/* ===== end touch layer ===== */

/* ===== Full-screen compare v20261111g =====
   One box sized to the result's aspect ratio; both images object-fit: contain inside it,
   so a wide input is never stretched. The slider clips the "before" layer instead of
   resizing it. All pointer input is captured here, so nothing behind can be clicked. */
(function () {
  const fs = $("compare-fs"), box = $("cmp-box"), stage = $("cmp-stage");
  const after = $("cmp-after"), before = $("cmp-before"), layer = $("cmp-before-layer"), line = $("cmp-line");
  let pct = 50, mode = "slide";

  function setSplit(p) {
    pct = Math.max(0, Math.min(100, p));
    const clip = "inset(0 " + (100 - pct) + "% 0 0)";
    layer.style.clipPath = clip;
    layer.style.webkitClipPath = clip;
    line.style.left = pct + "%";
  }
  function layout() {
    if (fs.classList.contains("hidden")) return;
    const r = stage.getBoundingClientRect();
    const w = after.naturalWidth || before.naturalWidth || 1;
    const h = after.naturalHeight || before.naturalHeight || 1;
    const ar = w / h;
    const maxW = Math.max(50, r.width - 24), maxH = Math.max(50, r.height - 16);
    let bw = maxW, bh = bw / ar;
    if (bh > maxH) { bh = maxH; bw = bh * ar; }
    box.style.width = Math.round(bw) + "px";
    box.style.height = Math.round(bh) + "px";
  }
  function setMode(m) {
    mode = m;
    document.querySelectorAll("#cmp-modes button").forEach((b) => b.classList.toggle("is-on", b.dataset.cmp === m));
    box.classList.toggle("hidden", m !== "slide");
    $("cmp-side").classList.toggle("hidden", m !== "side");
    $("cmp-hint").textContent = m === "slide" ? "Drag anywhere on the image to compare" : "Original on one side, result on the other";
    layout();
  }
  function openCompare(beforeUrl, afterUrl) {
    if (!beforeUrl || !afterUrl) { toast("No original image to compare with"); return; }
    after.src = afterUrl;
    before.src = beforeUrl;
    $("cmp-side-before").src = beforeUrl;
    $("cmp-side-after").src = afterUrl;
    fs.classList.remove("hidden");
    setMode(LS.get("cs.cmp.mode", "slide") === "side" ? "side" : "slide");
    setSplit(50);
    layout();
    after.onload = layout;
    before.onload = layout;
  }
  function closeCompare() { fs.classList.add("hidden"); }
  window.openCompare = openCompare;
  window.closeCompare = closeCompare;

  const fromEvent = (e) => {
    const r = box.getBoundingClientRect();
    return ((e.clientX - r.left) / r.width) * 100;
  };
  let dragging = false;
  box.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { box.setPointerCapture(e.pointerId); } catch (err) {}
    setSplit(fromEvent(e));
    e.preventDefault(); e.stopPropagation();
  });
  box.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    setSplit(fromEvent(e));
    e.preventDefault(); e.stopPropagation();
  });
  const stop = (e) => { dragging = false; if (e) e.stopPropagation(); };
  box.addEventListener("pointerup", stop);
  box.addEventListener("pointercancel", stop);
  /* Block touches and clicks from reaching anything underneath. */
  ["touchstart", "touchmove", "touchend", "click", "wheel"].forEach((ev) =>
    fs.addEventListener(ev, (e) => { e.stopPropagation(); if (ev === "touchmove") e.preventDefault(); }, { passive: false }));
  /* Tap the dark area (not the image or the bar) to close. */
  stage.addEventListener("click", (e) => { if (e.target === stage) closeCompare(); });
  $("cmp-close").addEventListener("click", closeCompare);
  document.querySelectorAll("#cmp-modes button").forEach((b) => b.addEventListener("click", () => {
    LS.set("cs.cmp.mode", b.dataset.cmp);
    setMode(b.dataset.cmp);
  }));
  /* Arrow keys nudge the slider on desktop; Escape closes. */
  document.addEventListener("keydown", (e) => {
    if (fs.classList.contains("hidden")) return;
    if (e.key === "Escape") { closeCompare(); e.stopImmediatePropagation(); }
    else if (e.key === "ArrowLeft") { setSplit(pct - 5); e.stopImmediatePropagation(); e.preventDefault(); }
    else if (e.key === "ArrowRight") { setSplit(pct + 5); e.stopImmediatePropagation(); e.preventDefault(); }
  }, true);
  window.addEventListener("resize", layout);

  /* Route both Compare buttons here. */
  toggleViewerCompare = function () {
    const it = state.library[state.viewerIndex];
    if (!it || !it.has_before) { toast("No original image saved for this one"); return; }
    openCompare(it.before_url, it.url + (it.url.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now());
  };
  toggleResultCompare = function () {
    const it = state.reuseItem || {};
    const beforeSrc = (it.id ? ("/api/source?id=" + encodeURIComponent(it.id) + "&slot=1") : "") || ($("before") && $("before").src);
    openCompare(beforeSrc, $("single") && $("single").src);
  };
  /* The base code bound the old functions directly; swap in clean buttons. */
  [["viewer-ba", () => toggleViewerCompare()], ["result-compare", () => toggleResultCompare()]].forEach(([id, fn]) => {
    const old = $(id);
    if (!old) return;
    const fresh = old.cloneNode(true);
    fresh.textContent = "Compare";
    old.replaceWith(fresh);
    fresh.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); fn(); });
  });
})();
/* ===== end compare ===== */

/* ===== Image / Video switch v20261111g =====
   Image: Create (text to image) | Edit. Video: Create (text to video) | Image to video.
   Video uses the same prompt box, picture strip (one start frame) and Camera/Seed pills.
   The video engine is not wired to ComfyUI yet; sending explains that instead of failing. */
(function () {
  const VASPECTS = [
    { id: "16:9", w: 16, h: 9, name: "Landscape" },
    { id: "9:16", w: 9, h: 16, name: "Vertical" },
    { id: "1:1", w: 1, h: 1, name: "Square" },
    { id: "4:3", w: 4, h: 3, name: "Classic" },
    { id: "3:4", w: 3, h: 4, name: "Portrait" },
    { id: "21:9", w: 21, h: 9, name: "Cinema" },
  ];
  state.media = LS.get("cs.media", "image") === "video" ? "video" : "image";
  state.vmode = ["t2v", "i2v", "r2v", "v2v", "long"].indexOf(LS.get("cs.vmode", "t2v")) >= 0 ? LS.get("cs.vmode", "t2v") : "t2v";
  state.video = {
    aspect: LS.get("cs.v.aspect", "16:9"),
    res: LS.get("cs.v.res", "720p"),
    len: Number(LS.get("cs.v.len", "5")) || 5,
  };
  let inVideoSwitch = false;

  function syncVideoPills() {
    const a = VASPECTS.find((x) => x.id === state.video.aspect) || VASPECTS[0];
    $("vsize-icon").innerHTML = ratioIconHtml(a.w, a.h);
    $("vsize-label").textContent = a.id;
    $("vsize-meta").textContent = state.video.res;
    $("vlen-label").textContent = state.video.len + "s";
    document.querySelectorAll("#vres-seg button").forEach((b) => b.classList.toggle("is-on", b.dataset.vres === state.video.res));
    document.querySelectorAll("#vlen-seg button").forEach((b) => b.classList.toggle("is-on", Number(b.dataset.vlen) === state.video.len));
    const grid = $("vsize-grid");
    grid.innerHTML = "";
    VASPECTS.filter((x) => !window.__videoAspects || window.__videoAspects.indexOf(x.id) >= 0).forEach((x) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "res-option" + (x.id === state.video.aspect ? " is-on" : "");
      b.innerHTML = ratioIconHtml(x.w, x.h) + '<span class="res-option-copy"><strong></strong></span>';
      b.querySelector("strong").textContent = x.name + " " + x.id;
      b.addEventListener("click", () => {
        state.video.aspect = x.id;
        LS.set("cs.v.aspect", x.id);
        syncVideoPills();
        hidePops();
      });
      grid.appendChild(b);
    });
  }

  function syncMediaChrome() {
    const video = state.media === "video";
    document.body.classList.toggle("media-video", video);
    document.body.classList.toggle("media-image", !video);
    document.querySelectorAll("#media-seg button").forEach((b) => {
      const on = b.dataset.media === state.media;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll("#vmode-seg button").forEach((b) => {
      const on = b.dataset.vmode === state.vmode;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (video) {
      $("prompt").placeholder = ({ i2v: "Describe how Picture 1 should move…", r2v: "Describe a scene with the people and things in your pictures…",
        long: "Tell the whole story. Mark shots with [Shot 2] or 00:05…" })[state.vmode] || "Describe a video…";
    } else {
      syncPlaceholder();
    }
    const add = $("add-sub");
    if (add) add.textContent = video
      ? "Tap one image to use as the first frame."
      : "Tap images to attach them. Picture 1 is the one being edited.";
    syncVideoPills();
    syncPills();
    if (window.syncHudFades) { $("hud-scroller").scrollLeft = 0; window.syncHudFades(); }
    syncDockHeight();
  }

  function setVideoMode(vm) {
    state.vmode = ["t2v", "i2v", "r2v", "v2v", "long"].indexOf(vm) >= 0 ? vm : "t2v";
    LS.set("cs.vmode", state.vmode);
    inVideoSwitch = true;
    try { setMode(state.vmode === "i2v" || state.vmode === "r2v" || state.vmode === "v2v" ? "edit" : "t2i"); } finally { inVideoSwitch = false; }
    syncMediaChrome();
  }

  function setMedia(m) {
    state.media = m === "video" ? "video" : "image";
    LS.set("cs.media", state.media);
    hidePops();
    closeAddMenu();
    if (state.media === "video") setVideoMode(state.images.length ? "i2v" : state.vmode);
    else { setMode(state.images.length ? "edit" : "t2i"); syncMediaChrome(); }
  }
  window.setMedia = setMedia;
  window.setVideoMode = setVideoMode;
  window.syncVideoPills = syncVideoPills;

  /* Adding pictures: Image -> Edit, Video -> Image to video (one start frame). */
  window.onImagesAdded = function () {
    if (state.media === "video") {
      if (state.vmode !== "i2v" && state.vmode !== "r2v" && state.vmode !== "v2v") {
        setVideoMode("i2v");
        toast("Switched to Image to video");
      } else {
        setVideoMode(state.vmode);
      }
    } else {
      setMode("edit");
      toast("Switched to Edit");
    }
  };

  /* Keep the video chrome when base code calls setMode (e.g. after removing pictures). */
  const baseSetMode = setMode;
  setMode = function () {
    const r = baseSetMode.apply(this, arguments);
    if (state.media === "video" && !inVideoSwitch) syncMediaChrome();
    return r;
  };

  const baseHelp = renderHelp;
  renderHelp = function () {
    baseHelp();
    if (state.media !== "video") return;
    $("help-list").innerHTML = [
      "Tap the <b>video icon</b> next to + to switch between Image and Video.",
      "<b>Create</b> makes a clip from text. The <b>image-to-video</b> icon animates one start picture.",
      "Describe the motion: what moves, how fast, and where the camera goes.",
      "<b>Size</b> sets the frame shape and resolution, <b>Length</b> the clip duration.",
      "Video generation is not connected to ComfyUI yet. The interface is ready for when it is.",
    ].map((t) => "<li>" + t + "</li>").join("");
  };

  const baseGenerate = generate;
  generate = async function () {
    if (state.media === "video") {
      hidePops();
      if (!$("prompt").value.trim()) { showError("Write a prompt first."); return; }
      if (state.vmode === "i2v" && !state.images.length) { showError("Add a start image first."); return; }
      showError("Video generation isn't connected to ComfyUI yet. Your prompt and settings are kept; switch to Image to generate now.");
      return;
    }
    return baseGenerate.apply(this, arguments);
  };
  /* The send button was bound to the original generate; route it through the wrapper. */
  const sendOld = $("generate");
  const send = sendOld.cloneNode(true);
  sendOld.replaceWith(send);
  send.addEventListener("click", () => generate());

  document.querySelectorAll("#media-seg button").forEach((b) => b.addEventListener("click", () => setMedia(b.dataset.media)));
  document.querySelectorAll("#vmode-seg button").forEach((b) => b.addEventListener("click", () => setVideoMode(b.dataset.vmode)));
  $("vsize-pill").addEventListener("click", (e) => { syncVideoPills(); showPop("vsize-pop", e.currentTarget); });
  $("vlen-pill").addEventListener("click", (e) => showPop("vlen-pop", e.currentTarget));
  document.querySelectorAll("#vres-seg button").forEach((b) => b.addEventListener("click", () => {
    state.video.res = b.dataset.vres; LS.set("cs.v.res", state.video.res); syncVideoPills();
  }));
  document.querySelectorAll("#vlen-seg button").forEach((b) => b.addEventListener("click", () => {
    state.video.len = Number(b.dataset.vlen); LS.set("cs.v.len", String(state.video.len)); syncVideoPills(); hidePops();
  }));
  ["vsize-pop", "vlen-pop"].forEach((id) => { if (window.__swipeClose) window.__swipeClose($(id), () => hidePops(), isPhoneUi); });

  if (state.media === "video") setMedia("video"); else syncMediaChrome();
})();
/* ===== end media switch ===== */

/* ===== Workflows: engines, upload, node install, updates v20261111h ===== */
(function () {
  const TASK_LABEL = { t2i: "Text to image", edit: "Image edit", t2v: "Text to video", i2v: "Image to video", r2v: "Reference to video", long: "Long text to video", other: "Other" };
  const TASK_MEDIA = { t2i: "image", edit: "image", t2v: "video", i2v: "video", r2v: "video", long: "video", other: "image" };
  const ICON = {
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="3.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.5 3.5 3 3-2.5 4.5 4"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6" width="13.5" height="12" rx="3"/><path d="M16 10.5l5.5-3v9l-5.5-3z"/></svg>',
  };
  const BUILTIN = { id: "builtin", name: "MiniMax H3 (built-in)", family: "MiniMax H3", tasks: ["t2i", "edit"] };
  let list = [];
  let active = null;      /* {card, options, summary} for a non-built-in engine */
  let applyToken = 0;
  let wasWf = false;

  function currentTask() {
    if (state.media === "video") return ["t2v", "i2v", "r2v", "v2v", "long"].indexOf(state.vmode) >= 0 ? state.vmode : "t2v";
    return state.mode === "edit" ? "edit" : "t2i";
  }
  function isImageTask(t) { return t === "t2i" || t === "edit"; }
  function forTask(t) { return list.filter((w) => w.task === t); }
  window.__wfForTask = forTask;
  window.__wfCurrentTask = currentTask;
  window.__wfList = () => list;
  window.__wfActive = () => active;
  function chosenId(t) {
    const saved = LS.get("cs.wf." + t, "");
    if (saved && (saved === "builtin" || forTask(t).some((w) => w.id === saved))) return saved;
    if (isImageTask(t)) return "builtin";
    /* Default: an engine that works with the pictures attached right now. */
    const usable = forTask(t).filter((w) => (w.picture_min || 0) <= state.images.length);
    return ((usable[0] || forTask(t)[0]) || {}).id || "";
  }

  let listRetry = 0;
  async function loadList() {
    try {
      const d = await api("/api/workflows"); list = d.items || []; listRetry = 0;
      window.__hw = Object.assign(window.__hw || {}, d.hardware || {});   /* merge: never wipe image_mp_max */
    } catch (e) {
      list = [];
      // the server or ComfyUI was busy: try again shortly instead of leaving the engine list empty until a reload
      if (listRetry++ < 8) setTimeout(() => { renderList().catch(() => {}); if (typeof applyEngine === "function") applyEngine(); }, 4000);
    }
    /* MiniMax H3 is the default engine for each task; other families follow. */
    const rank = (w) => (w.family === "MiniMax H3" ? 0 : 2) + (/\bFast\b/.test(w.name || "") ? 1 : 0);
    list.sort((a, b) => rank(a) - rank(b));
    return list;
  }

  function setBody(on) {
    const s = (on && active && active.card.slots) || {};
    document.body.classList.toggle("wf-active", !!on);
    const card = (on && active && active.card) || {};
    /* Size: width/height inputs, a resolution label (MiniMax H3 AIO) or a megapixel rule (LTX). */
    const hasSize = !!((s.width || []).length || s.res_label || (s.megapixels || []).length || (s.size_combo || []).length || (s.aspect_combo || []).length || card.size_rule);
    /* Length: a frame count (LTX, Wan...) or seconds (MiniMax H3 AIO). */
    const hasLength = !!((s.length || []).length || (s.seconds || []).length);
    document.body.classList.toggle("wf-no-size", !!on && !hasSize);
    document.body.classList.toggle("wf-no-seed", !!on && !(s.seed || []).length);
    document.body.classList.toggle("wf-no-length", !!on && !hasLength);
    if ($("wf-negative-group")) $("wf-negative-group").classList.toggle("hidden", !(s.negative || []).length);
    /* Sampling: show only what this engine really has (built-in MiniMax H3 image shows all four). */
    const showField = (id, show) => { const f = $(id) && $(id).closest(".field"); if (f) f.classList.toggle("hidden", !show); };
    showField("steps", !on || !!(s.steps || []).length);
    showField("cfg", !on || !!(s.cfg || []).length);
    showField("sampler_name", !on || !!(s.sampler || []).length);
    showField("scheduler", !on || !!(s.scheduler || []).length);
    $("sampling-note").textContent = on
      ? ((active && active.card.sampling_note) || "These values come from the workflow you picked.")
      : "MiniMax H3 image: the distilled model is tuned for 6 steps and CFG 1. 8 steps also works well.";
  }

  function fillSelect(el, values, pick) {
    el.innerHTML = "";
    (values || []).forEach((v) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      el.appendChild(o);
    });
    if (pick != null && [...el.options].some((o) => o.value === String(pick))) el.value = String(pick);
  }

  function fillWfSettings() {
    const card = active.card, opt = active.options || {}, d = card.defaults || {};
    if (d.steps != null) $("steps").value = d.steps;
    if (d.cfg != null) $("cfg").value = d.cfg;
    if ((opt.sampler || []).length) fillSelect($("sampler_name"), opt.sampler, d.sampler);
    if ((opt.scheduler || []).length) fillSelect($("scheduler"), opt.scheduler, d.scheduler);
    if ($("sampler_name").value === "lcm_detail") {   // built-in recipe only: workflows use their own sampler
      const want = d.sampler || "euler";
      $("sampler_name").value = [...$("sampler_name").options].some((o) => o.value === want) ? want : "euler";
    }
    $("wf-negative").value = LS.get("cs.wfneg." + card.id, d.negative || "");
    const box = $("wf-models");
    box.innerHTML = "";
    (opt.models || []).forEach((m) => {
      const lab = document.createElement("label");
      lab.className = "field";
      lab.textContent = (m.label || "Model") + (m.title && m.title !== m.label ? " · " + m.title : "") + " ";
      const sel = document.createElement("select");
      sel.dataset.key = m.key;
      const choices = (m.choices || []).length ? m.choices : [m.value];
      fillSelect(sel, choices, LS.get("cs.wfm." + card.id + "." + m.key, m.value));
      sel.addEventListener("change", () => LS.set("cs.wfm." + card.id + "." + m.key, sel.value));
      lab.appendChild(sel);
      box.appendChild(lab);
      if (m.installed === false && m.wanted) {
        /* the workflow was made with a file this PC doesn't have: say so, keep it usable with your own */
        const tip = document.createElement("p");
        tip.className = "pop-note";
        const short = String(m.wanted).split(/[\/]/).pop();
        tip.textContent = "This workflow was made with " + short + ", which isn't on this PC. It will use the model picked above. "
          + "Choose any of yours, or download the original on the Workflows page.";
        box.appendChild(tip);
      }
    });
    if (!(opt.models || []).length) box.innerHTML = '<p class="pop-note">This workflow has no model loaders the app can change.</p>';
    // settings follow the model you pick (a Turbo model wants 8 steps, a Base model 50 ...)
    box.querySelectorAll("select").forEach((sel) => sel.addEventListener("change", () => applyModelRecipe(true)));
    if (tuneMode() === "manual") restoreManual(card.id);
    applyModelRecipe(false);
    if ($("krea-keepface-group")) $("krea-keepface-group").classList.toggle("hidden", card.edit_kind !== "krea_edit");
    setTimeout(() => window.__syncKreaHint && window.__syncKreaHint(), 0);
    renderKnobs();
  }

  /* ---------- the tested recipe for the loaded model ----------
     Matched on the Image model file name (and LoRAs that change how the model samples). Values come from each
     model's own card / official template / the owner's tested workflows. Unknown models keep the workflow's own. */
  /* ---------- Krea 2 Edit: what kind of edit the instruction asks for ----------
     From the krea2-identity-edit model card (V2): Identity Edit restages a PERSON (new pose, angle, scene, or a
     person put into a scene); General Edit does everything else (remove, recolor, relight, background swap, add
     things, outfit, text). Auto mode picks the LoRA and steps here; the server sets ref_boost / placement /
     grounding for whichever LoRA is loaded. The prompt itself is never rewritten. */
  const KREA_KINDS = {
    action: { label: "Action in the same picture", lora: "identity", steps: 8, knobs: "ref_boost 1.5, aligned (scene kept), grounding 768" },
    restage: { label: "Pose / angle change", lora: "identity", steps: 8, knobs: "ref_boost 1, aligned (background kept), grounding 768" },
    insert: { label: "Person into scene", lora: "identity", steps: 8, knobs: "person 2.5 beside, scene 1 aligned, grounding 768" },
    background: { label: "Background swap", lora: "general", steps: 8, knobs: "ref_boost 4, aligned, grounding 768" },
    remove: { label: "Removal", lora: "general", steps: 8, knobs: "ref_boost 4, aligned, grounding 768" },
    face: { label: "Face / expression", lora: "general", steps: 8, knobs: "ref_boost 4, aligned, grounding 768" },
    general: { label: "General edit", lora: "general", steps: 8, knobs: "ref_boost 4, aligned, grounding 768" },
    headswap: { label: "Head swap", lora: "head_swap_v", steps: 8, knobs: "BFS head swap: Picture 2's head onto Picture 1, grounding 512" },
    bodyswap: { label: "Body swap", lora: "head_swap_v", steps: 10, knobs: "BFS head swap 1.0 + body swap 0.5: Picture 2's person into Picture 1" },
    outfit: { label: "Outfit swap", lora: "outfittransfer", steps: 8, knobs: "outfit transfer (v1.1 geometry): Picture 2's outfit onto Picture 1's person" },
  };
  const SWAP_RX = {
    headswap: /\b((head|face)[- ]?swap\w*|swap\w* (the |her |his |their )?(heads?|faces?)|replace (the |her |his |their )?(head|face)|(put|use|give (her|him|them)) (the |picture 2'?s? |image 2'?s? )?(head|face) (of|from))/i,
    outfit: /\b((outfit|clothes|clothing|dress|costume)[- ]?(swap|transfer)\w*|(wear\w*|put on|dress\w*( (her|him|them))? in) (the |that |this )?(same )?(outfit|clothes|clothing|dress|top|shirt|jacket|look|costume|uniform|bikini|suit)s? (from|in|of)|(transfer|copy|take|bring|swap|put|give)\w* (the |her |his |their )?(outfit|clothes|clothing|dress|costume)s? (from|in|of)|same (outfit|clothes|clothing|dress) as)/i,
    bodyswap: /\b(body[- ]?swap\w*|character[- ]?swap\w*|swap\w* (the |her |his |their )?(body|bodies|person|people|characters?)|replace (the |her |his |their )?(body|person|woman|man|girl|guy|character))/i,
  };
  function swapLora(part) {
    const sel = editLoraSelect();
    return sel ? ([...sel.options].map((o) => o.value).find((v) => v.toLowerCase().includes(part)) || "") : "";
  }
  window.__swapLora = swapLora;
  const RX = {
    // the person moves: pose, body, camera angle, framing
    pose: /\b(turn(s|ed|ing)?(?!\s+(on|off|up|down|into)\b)|rotat\w*|profile|facing|face (left|right|away|the camera|forward)|from (behind|the back|the side|above|below)|back view|side view|over (her|his|their|the) shoulder|look(s|ing)? (at|over|away|up|down|back|left|right)|camera angle|(low|high|eye[- ]level|overhead|dutch|birds?[- ]eye) angle|zoom (in|out)|close[- ]?up|full[- ]body|wide shot|medium shot|pose|posing|sit(s|ting)?|seated|stand(s|ing)?|kneel\w*|lie|lying|lay(ing)?|crouch\w*|squat\w*|lean\w*|bend\w*|walk\w*|run(s|ning)?|jump\w*|danc\w*|character sheet|turnaround)\b/i,
    // hands / arms / legs act in the SAME picture. On 'aligned' + ref_boost 4 the old limbs stay pinned to the
    // layout while new ones are drawn = duplicated limbs; these keep 'aligned' (same scene) at ref_boost 1.5
    body: /\b(rais\w* (her|his|their|both)|arms? (up|crossed|raised)|cross(ed)? (her|his|their) (arms|legs)|hands? on|hold(s|ing)?|held|grab\w*|grip\w*|grasp\w*|cup(s|ped|ping)?|squeez\w*|touch\w*|caress\w*|fondl\w*|rub(s|bed|bing)?|strok\w*|hug\w*|embrac\w*|cuddl\w*|clasp\w*|pinch\w*|pull\w* (up|down|aside|apart|back|her|his|their|on|at)|lift\w*|spread\w*|point\w*|wav\w*|reach\w*|stretch\w*|drink\w*|eat\w*|sip\w*|lick\w*|suck\w*|bit(e|es|ing)|kiss\w*|blow\w* (a )?kiss|cover\w* (her|his|their)|fold\w* (her|his|their)|put\w* (her|his|their) (hand|hands|arm|arms|leg|legs|finger|fingers)|(in|with|between|on) (her|his|their|both) (left |right |own )?(hands?|arms?|fingers?|legs?|thighs?|lap)|(her|his|their|both) (left |right )?(hands?|arms?|fingers?|legs?|knees?|feet|foot|elbows?|wrists?|thumbs?) (on|in|over|around|behind|between|up|down|together|apart|out|at)|on (her|his|their) (knees|back|side|stomach|belly|tiptoes)|arch\w* (her|his|their) back|tilt\w* (her|his|their) head)\b/i,
    // the person goes somewhere new (restaging), not just the backdrop swapped
    place: /\b(place (her|him|them|this (person|woman|man|girl|guy))|put (her|him|them) (in|on|at|into)|move (her|him|them) (to|into|onto)|(she|he|they) (is|are) (now )?(at|in|on) (a|an|the)|at the (beach|cafe|café|park|office|gym|bar|pool|club|restaurant)|in a (cafe|café|park|kitchen|bedroom|car|forest|city|street))\b/i,
    background: /\b(background|backdrop|behind (her|him|them))\b/i,
    remove: /\b(remove|erase|delete|get rid of|take (off|away|out)|without (the|her|his|their))\b/i,
    face: /\b(smil\w*|expression|makeup|make-up|lipstick|eyeliner|mascara|freckles|wink\w*|frown\w*|laugh\w*|eyes? (open|closed|shut)|close (her|his|their) eyes|open (her|his|their) mouth|blush\w*|eyebrows?)\b/i,
  };
  function kreaKind(prompt, pics) {
    const p = String(prompt || "");
    if ((pics == null ? state.images.length : pics) >= 2) {
      // head / body swap needs the BFS head swap LoRA; without it a swap is done as person-into-scene
      if (SWAP_RX.bodyswap.test(p) && swapLora("head_swap_v") && swapLora("body_swap_v")) return "bodyswap";
      if ((SWAP_RX.headswap.test(p) || SWAP_RX.bodyswap.test(p)) && swapLora("head_swap_v")) return "headswap";
      if (SWAP_RX.outfit.test(p) && swapLora("outfittransfer")) return "outfit";
      return "insert";                                                        // the card: person into a scene = Identity
    }
    if (RX.remove.test(p)) return "remove";                                   // removals are General's job
    if (RX.pose.test(p) || RX.place.test(p)) return "restage";               // the whole body / view / place changes
    if (RX.body.test(p)) return "action";                                     // hands / arms act, same picture
    if (RX.background.test(p)) return "background";
    if (RX.face.test(p)) return "face";
    return "general";
  }
  const ACTION = /^(please\s+)?(change|make|turn|add|remove|replace|put|place|give|relight|swap|move|rotate|take|set|show|convert|transform|restyle|dress|zoom|crop|extend|erase|delete|fix|keep|preserve|colou?r|paint|apply|use|edit|clean|brighten|darken|open|close|raise|lower|let|have|get|pose|tilt|shift|reposition|recolou?r|style|age|de-?age|straighten|widen|narrow|enlarge|shrink|sharpen|blur|light|cover|uncover|unbutton|undress|strip|lift|hold|wear|retouch|smooth|frame|reframe|flip|mirror|bring|send|sit|stand|kneel|lie|lay|look|face)\b/i;
  const FRAMING = /\b(full[- ]body|head to toe|waist[- ]up|from the waist|knees? up|three[- ]quarter|head and shoulders|close[- ]?up|portrait|sitting|seated|standing|kneeling|lying|crouch\w*|squat\w*)\b/i;
  function kreaTips(prompt, kind, model) {
    const p = String(prompt || "").trim();
    const tips = [];
    if (!p) return tips;
    if (kind === "headswap" || kind === "bodyswap")      // the BFS LoRA gets its own trigger sentence instead
      return ["Picture 1 = the one to keep, Picture 2 = whose " + (kind === "headswap" ? "head" : "person") + " goes in."];
    const sentences = p.split(/[.!?]+\s+/).filter((x) => x.trim().length > 2);
    if (!ACTION.test(p)) tips.push("Krea Edit follows commands best. Start with the action, e.g. 'Turn her to a left-facing side profile.'");
    const actions = sentences.filter((x) => ACTION.test(x.trim())).length;
    if (actions >= 3 || p.split(/\s+/).length > 45) tips.push("One clear edit per run works best. Do the rest as a second edit on the result.");
    // the model card's own prompt advice
    if ((kind === "restage" || kind === "insert") && !FRAMING.test(p))
      tips.push("Say how much of her shows ('full body', 'sitting cross-legged'), or it keeps the photo's crop.");
    if (/\b(neon|lighting|lit|light)\b/i.test(p) && !/\b(on|across|from) (her|his|their|the) (face|left|right|side|cheek|body|back)\b/i.test(p))
      tips.push("Describe where the light falls, e.g. 'warm light across the left side of her face'.");
    if (kind === "remove" && !/raw/i.test(model || "")) tips.push("If it isn't removed cleanly, try the Raw model (20 steps, CFG 3).");
    return tips;
  }
  window.__kreaKind = kreaKind;

  /* Auto picks General Edit V2 or Identity Edit V2 for the kind of edit (same size: full or r128). A v1.x LoRA you
     picked yourself is left alone. Not saved as your choice: Manual brings back what you picked. */
  function editLoraSelect() {
    return [...document.querySelectorAll("#wf-models select")].find((sel) => [...sel.options].some((o) => /(general|identity)_edit_v2/i.test(o.value))) || null;
  }
  function pickEditLora(kind) {
    const sel = editLoraSelect();
    if (!sel) return "";
    const cur = sel.value;
    if (!/(general|identity)_edit_v2|_swap_v|outfittransfer/i.test(cur)) return cur;      // v1.2 / v1.1 / other: yours
    if (KREA_KINDS[kind].lora === "head_swap_v" || KREA_KINDS[kind].lora === "outfittransfer") {
      const sw = swapLora(KREA_KINDS[kind].lora);
      if (sw) sel.value = sw;
      return sel.value;
    }
    const want = KREA_KINDS[kind].lora === "identity" ? "identity_edit_v2" : "general_edit_v2";
    const r128 = /_r128/i.test(cur);
    const opts = [...sel.options].map((o) => o.value).filter((v) => v.toLowerCase().includes(want));
    const best = opts.find((v) => /_r128/i.test(v) === r128) || opts[0];
    if (best && best !== cur) sel.value = best;
    return sel.value;
  }
  function restoreEditLora() {
    const sel = editLoraSelect();
    if (!sel || !active) return;
    const saved = LS.get("cs.wfm." + active.card.id + "." + sel.dataset.key, null);
    if (saved && [...sel.options].some((o) => o.value === saved)) sel.value = saved;
  }
  window.__restoreEditLora = restoreEditLora;
  function loraShort(v) {
    v = String(v || "").split(/[\\/]/).pop().replace(/\.safetensors$/i, "");
    if (/identity_edit_v2/i.test(v)) return "Identity Edit V2" + (/_r128/i.test(v) ? " r128" : "");
    if (/general_edit_v2/i.test(v)) return "General Edit V2" + (/_r128/i.test(v) ? " r128" : "");
    if (/head_swap_v/i.test(v)) return "BFS Head Swap";
    if (/outfittransfer/i.test(v)) return "Outfit Transfer";
    return v;
  }

  const RECIPES = [
    { fam: "MOSS", lora: /8-steps-dmd/i, steps: 8, cfg: 1, sampler: "euler", scheduler: "simple",
      note: "MOSS with the 8-step DMD2 LoRA: 8 steps, CFG 1, euler / simple (KroMOSS model card). The negative prompt is not used at CFG 1." },
    { fam: "MOSS", model: /kromoss|moss_image_base/i, steps: 50, cfg: 3, sampler: "euler", scheduler: "simple",
      note: "KroMOSS / MOSS Base: 50 steps, CFG 3, euler (the model card's settings). For speed add the 8-steps-dmd2 LoRA: 8 steps, CFG 1." },
    { fam: "MOSS", model: /turbo/i, steps: 8, cfg: 1, sampler: "euler", scheduler: "simple",
      note: "MOSS Turbo: 8 steps, CFG 1, euler / simple." },
    { fam: "MOSS", model: /sft/i, steps: 25, cfg: 2.5, sampler: "er_sde", scheduler: "simple",
      note: "MOSS SFT: 25 steps, CFG 2.5, er_sde / simple (the official template)." },
    { card: /^krea2_edit$/, model: /raw/i, steps: 40, cfg: 3.5, sampler: "euler", scheduler: "simple",
      note: "Krea 2 Edit on Raw: 40 steps, CFG 3.5, euler. Best for removals; slower." },
    { card: /^krea2_edit$/, steps: 10, cfg: 1, sampler: "euler", scheduler: "simple",
      note: "Krea 2 Edit on Turbo: 10 steps, CFG 1, euler / simple. 8 steps = stronger adherence, 12 = more face detail. Removals work better on the Raw model." },
    { card: /^krea2_paint$/, steps: 8, cfg: 1, sampler: "euler", scheduler: "simple",
      note: "Krea 2 AnyPaint: 8 steps, CFG 1, euler / simple. Describe the finished picture." },
    { fam: "Krea 2", model: /raw/i, steps: 28, cfg: 3.5, sampler: "res_multistep", scheduler: "simple",
      note: "Krea 2 Raw: 28 steps, CFG 3.5, res_multistep (high detail)." },
    { fam: "Krea 2", steps: 8, cfg: 1, sampler: "euler", scheduler: "simple",
      note: "Krea 2 Turbo: 8 steps, CFG 1, euler / simple (the official template)." },
    { fam: "FLUX.2 Klein", model: /klein-base|klein_base|base-9b/i, steps: 20, cfg: 5, sampler: "euler",
      note: "FLUX.2 Klein Base: 20 steps, CFG 5, euler (the official template)." },
  ];
  function currentImageModel() {
    const sel = $("wf-models") && $("wf-models").querySelector('select[data-key="0"]');
    return sel ? sel.value : "";
  }
  function pickedLoras() {
    try { return (window.__currentLoras ? window.__currentLoras() : []).map((l) => (l && (l.name || l)) || "").join(" "); } catch (e) { return ""; }
  }
  function findRecipe(card) {
    const model = currentImageModel(), loras = pickedLoras();
    return RECIPES.find((r) => (!r.card || r.card.test(card.id)) && (!r.fam || r.fam === card.family)
      && (!r.model || r.model.test(model)) && (!r.lora || r.lora.test(loras))) || null;
  }
  function setIfOption(el, v) {
    if (!el || v == null) return;
    if (el.tagName === "SELECT") { if ([...el.options].some((o) => o.value === String(v))) el.value = String(v); }
    else el.value = v;
  }
  function applyModelRecipe(announce) {
    if (!active) return;
    const card = active.card, s = card.slots || {};
    const r = findRecipe(card);
    const note = $("sampling-note");
    if (tuneMode() === "manual") {
      if (note) note.textContent = "Manual: your own settings are kept for this engine." + (r
        ? " Tested for this model: " + r.steps + " steps, CFG " + r.cfg + ", " + r.sampler + (r.scheduler ? " / " + r.scheduler : "") + "."
        : (card.sampling_note ? " " + card.sampling_note : ""));
      return;
    }
    if (!r) { if (note) note.textContent = card.sampling_note || "These values come from the workflow you picked."; return; }
    const before = [$("steps").value, $("cfg").value, $("sampler_name").value, $("scheduler").value].join("|");
    let steps = r.steps, cfg = r.cfg, kindNote = "";
    if (card.edit_kind === "krea_edit") {
      const kind = kreaKind($("prompt").value), k = KREA_KINDS[kind];
      const lora = pickEditLora(kind);
      const raw = /raw/i.test(currentImageModel());
      if (!raw) steps = k.steps;
      else if (kind === "remove") { steps = 20; cfg = 3; }               // the card: removals on Raw, 20 steps, CFG 3
      kindNote = " Auto, " + k.label.toLowerCase() + " \u2192 " + loraShort(lora) + ": " + steps + " steps, " + k.knobs + ".";
    }
    if ((s.steps || []).length) setIfOption($("steps"), steps);
    if ((s.cfg || []).length) setIfOption($("cfg"), cfg);
    if ((s.sampler || []).length) setIfOption($("sampler_name"), r.sampler);
    if ((s.scheduler || []).length) setIfOption($("scheduler"), r.scheduler);
    if (note) note.textContent = r.note + kindNote;
    const after = [$("steps").value, $("cfg").value, $("sampler_name").value, $("scheduler").value].join("|");
    if (announce && before !== after) toast("Settings set for this model: " + steps + " steps, CFG " + cfg);
  }
  window.__applyModelRecipe = () => applyModelRecipe(true);
  function syncKreaHint() {
    const el = $("krea-hint");
    if (!el) return;
    const on = !!(active && active.card.edit_kind === "krea_edit");
    document.body.classList.toggle("krea-edit", on);
    if (!on) { el.classList.add("hidden"); return; }
    const p = $("prompt").value;
    const kind = kreaKind(p), k = KREA_KINDS[kind];
    const auto = tuneMode() === "auto";
    const lines = [];
    if (p.trim()) {
      const sel = editLoraSelect();
      lines.push((auto ? "Auto \u00b7 " : "Manual \u00b7 ") + k.label + " \u2192 " + loraShort(sel ? sel.value : "")
        + (auto ? ": " + $("steps").value + " steps, " + k.knobs : ""));
    }
    kreaTips(p, kind, currentImageModel()).forEach((t) => lines.push("Tip: " + t));
    el.textContent = lines.join("  ");
    el.innerHTML = lines.map((l) => '<span>' + l.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]) + '</span>').join("");
    el.classList.toggle("hidden", !lines.length);
  }
  let kreaTimer = 0;
  $("prompt").addEventListener("input", () => {
    if (!(active && active.card.edit_kind === "krea_edit")) return;
    clearTimeout(kreaTimer);
    kreaTimer = setTimeout(() => { if (tuneMode() === "auto") applyModelRecipe(false); syncKreaHint(); }, 350);
  });
  window.__syncKreaHint = syncKreaHint;

  /* ---------- Manual: every setting in the loaded workflow ----------
     The server reads them from the workflow itself + ComfyUI's node definitions, so a newly uploaded workflow
     gets its own full list with no setup. Your values are kept per workflow and sent only in Manual mode; the
     server checks each one against the node's limits before using it. */
  function knobsKey(id) { return "cs.knobs." + id; }
  function loadKnobVals(id) { try { return JSON.parse(LS.get(knobsKey(id), "{}")) || {}; } catch (e) { return {}; } }
  function saveKnobVals(id, v) { LS.set(knobsKey(id), JSON.stringify(v)); }
  function renderKnobs() {
    const group = $("wf-knobs-group"), box = $("wf-knobs");
    if (!group || !box) return;
    const on = !!(active && tuneMode() === "manual");
    group.classList.toggle("hidden", !on);
    if (!on) return;
    const id = active.card.id, knobs = active.knobs || [];
    const vals = loadKnobVals(id);
    box.innerHTML = "";
    if (!knobs.length) { box.innerHTML = '<p class="pop-note">The app’s own controls already cover every setting in this workflow.</p>'; return; }
    // nodes that share a name get their id added, so two "Scale Image" nodes can be told apart
    const byGroup = new Map(), groupNodes = new Map();
    knobs.forEach((k) => {
      if (!groupNodes.has(k.group)) groupNodes.set(k.group, new Set());
      groupNodes.get(k.group).add(k.node);
    });
    knobs.forEach((k) => {
      const name = groupNodes.get(k.group).size > 1 ? k.group + " (" + k.node + ")" : k.group;
      if (!byGroup.has(name)) byGroup.set(name, []);
      byGroup.get(name).push(k);
    });
    let changed = 0;
    byGroup.forEach((list, name) => {
      const det = document.createElement("details");
      det.className = "knob-node";
      const sum = document.createElement("summary");
      sum.textContent = name;
      det.appendChild(sum);
      const grid = document.createElement("div");
      grid.className = "field-grid";
      list.forEach((k) => {
        const key = k.node + "." + k.input;
        const has = Object.prototype.hasOwnProperty.call(vals, key);
        const cur = has ? vals[key] : k.value;
        if (has && String(vals[key]) !== String(k.value)) { changed++; det.classList.add("is-changed"); }
        const lab = document.createElement("label");
        lab.className = "field" + (k.type === "string" && k.multiline ? " wide" : "");
        lab.appendChild(document.createTextNode(k.label + " "));
        let el;
        if (k.type === "combo") {
          el = document.createElement("select");
          (k.options || []).forEach((o) => { const op = document.createElement("option"); op.value = o; op.textContent = o; el.appendChild(op); });
          el.value = String(cur);
        } else if (k.type === "boolean") {
          el = document.createElement("input"); el.type = "checkbox"; el.checked = !!cur;
          lab.classList.add("check-row");
        } else if (k.type === "int" || k.type === "float") {
          el = document.createElement("input"); el.type = "number"; el.inputMode = k.type === "int" ? "numeric" : "decimal";
          if (k.min != null) el.min = k.min;
          if (k.max != null && k.max < 1e9) el.max = k.max;
          el.step = k.step != null ? k.step : (k.type === "int" ? 1 : "any");
          el.value = cur;
        } else {
          el = document.createElement(k.multiline ? "textarea" : "input");
          if (k.multiline) el.rows = 2;
          el.value = cur == null ? "" : cur;
        }
        if (k.tip) lab.title = k.tip;
        el.addEventListener("change", () => {
          const v = loadKnobVals(id);
          let val;
          if (k.type === "boolean") val = el.checked;
          else if (k.type === "int") val = Math.round(Number(el.value));
          else if (k.type === "float") val = Number(el.value);
          else val = el.value;
          if ((k.type === "int" || k.type === "float") && !isFinite(val)) return;
          if (String(val) === String(k.value)) delete v[key]; else v[key] = val;
          saveKnobVals(id, v);
          det.classList.toggle("is-changed", Object.keys(v).some((x) => list.some((q) => q.node + "." + q.input === x)));
          syncKnobCount();
        });
        lab.appendChild(el);
        if (k.tip) { const t = document.createElement("small"); t.className = "knob-tip"; t.textContent = k.tip; lab.appendChild(t); }
        grid.appendChild(lab);
      });
      det.appendChild(grid);
      box.appendChild(det);
    });
    syncKnobCount();
  }
  function syncKnobCount() {
    if (!active || !$("wf-knobs-count")) return;
    const n = Object.keys(loadKnobVals(active.card.id)).length;
    $("wf-knobs-count").textContent = n ? n + " changed" : "workflow defaults";
  }
  function manualOverrides() {
    if (!active || tuneMode() !== "manual") return null;
    const v = loadKnobVals(active.card.id);
    return Object.keys(v).length ? v : null;
  }
  if ($("wf-knobs-reset")) $("wf-knobs-reset").addEventListener("click", () => {
    if (!active) return;
    saveKnobVals(active.card.id, {});
    renderKnobs();
    toast("Workflow settings back to the workflow's own values");
  });
  window.__renderKnobs = renderKnobs;
  window.__refillWfSettings = () => { if (active) fillWfSettings(); };

  function renderEngine() {
    const t = currentTask();
    const fam = $("wf-family"), pick = $("wf-pick");
    const items = forTask(t);
    const families = [];
    if (isImageTask(t)) families.push(BUILTIN.family);
    items.forEach((w) => { if (families.indexOf(w.family || "Custom") < 0) families.push(w.family || "Custom"); });
    const cur = state.engine === "builtin" ? BUILTIN.family : ((items.find((w) => w.id === state.engine) || {}).family || families[0]);
    fam.innerHTML = "";
    if (!families.length) {
      fam.innerHTML = '<option value="">No ' + (TASK_MEDIA[t] === "video" ? "video" : "") + ' engine yet</option>';
    }
    families.forEach((f) => {
      const o = document.createElement("option");
      o.value = f; o.textContent = f;
      fam.appendChild(o);
    });
    if (cur) fam.value = cur;
    pick.innerHTML = "";
    if (isImageTask(t) && fam.value === BUILTIN.family) {
      const o = document.createElement("option");
      o.value = "builtin"; o.textContent = BUILTIN.name;
      pick.appendChild(o);
    }
    items.filter((w) => (w.family || "Custom") === fam.value).forEach((w) => {
      const o = document.createElement("option");
      o.value = w.id; o.textContent = w.name + (w.source === "user" ? "" : " (built-in)");
      pick.appendChild(o);
    });
    if (state.engine) pick.value = state.engine;
    $("wf-task-label").textContent = TASK_LABEL[t] + " workflow";
    const note = $("wf-note");
    if (state.engine === "builtin") note.textContent = "Built-in MiniMax H3 with Auto understanding, Enhance and Roles.";
    else if (active) note.textContent = (active.summary && active.summary.found || []).length
      ? "This workflow controls: " + active.summary.found.join(", ") + "."
      : "Uploaded workflow.";
    else note.textContent = "No " + TASK_LABEL[t].toLowerCase() + " workflow yet. Upload one on the Workflows page.";
  }

  function applyEngine() {
    const p = applyEngineInner();
    window.__enginePending = p;
    p.finally(() => { if (window.__enginePending === p) window.__enginePending = null; });
    return p;
  }
  async function applyEngineInner() {
    const token = ++applyToken;
    let t = currentTask();
    if (!list.length) await loadList();
    // a remembered video type whose engine is gone (e.g. a deleted uploaded workflow): back to Text to video
    if (!isImageTask(t) && t !== "t2v" && !forTask(t).length && forTask("t2v").length) {
      state.vmode = "t2v";
      LS.set("cs.vmode", "t2v");
      t = "t2v";
      setTimeout(() => { if (state.vmode === "t2v" && window.setVideoMode) window.setVideoMode("t2v"); }, 0);   // chip label
    }
    const items = forTask(t);
    let id = chosenId(t);
    if (id !== "builtin" && !items.find((w) => w.id === id)) id = isImageTask(t) ? "builtin" : (items[0] ? items[0].id : "");
    if (!isImageTask(t) && id === "builtin") id = items[0] ? items[0].id : "";
    state.engine = id || "none";
    if (!id || id === "builtin") {
      active = null;
      setBody(false);
      if (typeof window.onEngineChanged === "function") window.onEngineChanged(null, state.engine);
      if (wasWf) { wasWf = false; try { await loadConfig(); } catch (e) {} }
      if (token === applyToken) { renderEngine(); syncPills(); syncDockHeight(); }
      return;
    }
    if (!active || active.card.id !== id) {
      try {
        const d = await api("/api/workflow?id=" + encodeURIComponent(id));
        if (token !== applyToken) return;
        active = d;
      } catch (e) {
        active = null;
        state.engine = "none";
        toast(e.message || "Couldn't load that workflow");
      }
    }
    if (active) { wasWf = true; setBody(true); fillWfSettings(); }
    if (typeof window.onEngineChanged === "function") window.onEngineChanged(active, state.engine);
    renderEngine();
    syncPills();
    syncDockHeight();
  }
  window.applyEngine = applyEngine;

  $("wf-family").addEventListener("change", () => {
    const t = currentTask();
    const fam = $("wf-family").value;
    const first = fam === BUILTIN.family ? "builtin" : ((forTask(t).find((w) => (w.family || "Custom") === fam) || {}).id || "");
    if (first) LS.set("cs.wf." + t, first);
    applyEngine();
  });
  $("wf-pick").addEventListener("change", () => {
    LS.set("cs.wf." + currentTask(), $("wf-pick").value);
    applyEngine();
  });
  $("wf-negative").addEventListener("input", () => { if (active) LS.set("cs.wfneg." + active.card.id, $("wf-negative").value); });

  /* Re-pick the engine whenever Image/Video or Create/Edit changes. */
  ["setMode", "setVideoMode", "setMedia"].forEach((name) => {
    const orig = window[name];
    if (typeof orig !== "function") return;
    const wrapped = function () { const r = orig.apply(this, arguments); applyEngine(); return r; };
    window[name] = wrapped;
    if (name === "setMode") setMode = wrapped;
  });

  /* ---------- sizes for uploaded engines ---------- */
  function snap32(v) { return Math.max(64, Math.round(v / 32) * 32); }
  function wfSize() {
    if (state.media === "video") {
      const [a, b] = (state.video.aspect || "16:9").split(":").map(Number);
      const short = state.video.res === "480p" ? 480 : 720;
      return a >= b ? { width: snap32(short * a / b), height: snap32(short) } : { width: snap32(short), height: snap32(short * b / a) };
    }
    let a = 1, b = 1;
    const p1 = state.images[0];
    if ((state.quality === "picture1" || state.quality === "keep" || state.quality === "match") && p1 && p1.width && p1.height) {
      a = p1.width; b = p1.height;
    } else {
      const asp = (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
      [a, b] = asp.split(":").map(Number);
    }
    // Ceiling: what this model handles well (card mp_max) and what this PC can draw.
    const cap = Math.min((active && active.card && active.card.mp_max) || 4, (window.__hw && window.__hw.image_mp_max) || 8);
    if ((state.quality === "picture1" || state.quality === "keep") && state.p1Original !== false && p1 && p1.width && p1.height) {
      const k = Math.min(1, Math.sqrt(cap * 1e6 / (p1.width * p1.height)));   // Original: Picture 1's own size
      return { width: snap32(p1.width * k), height: snap32(p1.height * k), capped: k < 1, cap: cap };
    }
    const want = Number(currentMp()) || 1;
    const mp = Math.min(cap, want);
    const w = Math.sqrt(mp * 1e6 * a / b);
    return { width: snap32(w), height: snap32(w * b / a), capped: want > cap, cap: cap };
  }

  window.__wfSize = wfSize;
  /* ---------- generate with an uploaded engine ---------- */
  async function runWorkflow() {
    const card = active.card, s = card.slots || {};
    const prompt = $("prompt").value.trim();
    if ((s.prompt || []).length && !prompt) { showError("Write a prompt first."); return; }
    const need = card.picture_min != null ? card.picture_min : (s.images || []).length;
    const most = card.picture_max != null ? card.picture_max : need;
    if (state.images.length < need) {
      showError("This workflow needs " + need + " picture" + (need === 1 ? "" : "s") + ". Add " + (need === 1 ? "it" : "them") + " with + first.");
      return;
    }
    const models = {};
    document.querySelectorAll("#wf-models select").forEach((sel) => { models[sel.dataset.key] = sel.value; });
    const size = wfSize();
    const body = {
      mode: "workflow",
      workflow_id: card.id,
      prompt,
      negative: (s.negative || []).length ? $("wf-negative").value : null,
      images: state.images.slice(0, Math.max(need, most)).map((r) => r.name),
      res: card.media === "image" ? (window.__imgResLabel ? window.__imgResLabel(card) : card.default_resolution) : state.video.res,
      aspect: card.media === "image"
        ? ((state.quality === "picture1" || state.quality === "keep" || !state.customAspect) ? "match" : (state.customAspect || state.aspect))
        : state.video.aspect,
      random_seed: $("random-seed").checked,
      seed: $("random-seed").checked ? null : Number($("seed").value || 0),
      steps: $("steps").value,
      cfg: $("cfg").value,
      sampler_name: $("sampler_name").value,
      scheduler: $("scheduler").value,
      width: size.width,
      height: size.height,
      size_auto: state.quality === "picture1" || state.quality === "keep" || state.quality === "match",
      seconds: card.media === "video" ? state.video.len : null,
      fps: (card.defaults || {}).fps || null,
      models,
      camera: Object.assign({}, state.camera || {}),
      loras: typeof window.__currentLoras === "function" ? window.__currentLoras() : [],
      inpaint_mask: (state.inpaint && state.images[0] && state.inpaint.forName === state.images[0].name) ? state.inpaint.mask : null,
      inpaint_areas: (state.inpaint && state.inpaint.areas) || [],
      inpaint_explicit: !!(state.inpaint && state.inpaint.explicit),
      pe: (card.media === "video" || card.media === "image") && $("pe").checked,
      pe_mode: state.peMode || "fast",
      pe_tokens: state.peTokens || null,
      auto_assist: !!(card.media === "image" && state.images.length && (!$("auto-assist") || $("auto-assist").checked)),
      image_roles: (state.roles || []).slice(),
      ref_mode: !!(state.mode === "edit" && state.refMode),
      edit_intent: (card.edit_kind === "krea_edit" && tuneMode() === "auto") ? kreaKind(prompt, Math.min(state.images.length, most)) : null,
      keep_face: !!(card.edit_kind === "krea_edit" && $("krea-keepface") && $("krea-keepface").checked),
      keep_clothing: !$("keep-clothing") || $("keep-clothing").checked,
      keep_quality: !$("keep-quality") || $("keep-quality").checked,
      tune_mode: tuneMode(),
      understanding: !$("auto-assist") || $("auto-assist").checked,
      overrides: manualOverrides(),
      videos: window.__mediaInBody ? window.__mediaInBody().videos : [],
      audios: window.__mediaInBody ? window.__mediaInBody().audios : [],
    };
    if (card.edit_kind === "krea_edit" && tuneMode() === "auto") {
      applyModelRecipe(false);
      body.steps = $("steps").value; body.cfg = $("cfg").value;
      document.querySelectorAll("#wf-models select").forEach((sel) => { body.models[sel.dataset.key] = sel.value; });
      if (body.edit_intent === "bodyswap" && window.__swapLora("body_swap_v"))   // the BFS recipe: body swap LoRA at 0.5
        body.loras = (body.loras || []).concat([{ name: window.__swapLora("body_swap_v"), strength: 0.5 }]);
    }
    let resp, data;
    try {
      resp = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      data = await resp.json();
    } catch (e) {
      showError(e.message || "The app didn't answer.");
      return;
    }
    if (resp.ok && data.job) { if (window.__loraSent) window.__loraSent(); enqueueJob(data.job); return; }
    if (typeof window.showActionError === "function" && data && data.code) { window.showActionError(data); return; }
    showError((data && data.error) || "Couldn't start that.");
  }
  const prevGenerate = generate;
  generate = async function () {
    hidePops();
    closeAddMenu();
    if (state.engine && state.engine !== "builtin" && state.engine !== "none" && active) return runWorkflow();
    if (state.media === "video" || state.engine === "none") {
      showError("No " + TASK_LABEL[currentTask()].toLowerCase() + " workflow yet. Open Workflows and upload one from ComfyUI (for example an LTX or Wan workflow).");
      return;
    }
    return prevGenerate.apply(this, arguments);
  };
  (function rebindSend() {
    const old = $("generate");
    const fresh = old.cloneNode(true);
    old.replaceWith(fresh);
    fresh.addEventListener("click", () => generate());
  })();

  /* ---------- video results ---------- */
  const prevShow = showResult;
  showResult = function (job) {
    const r = prevShow.apply(this, arguments);
    const vid = $("single-video");
    const isVideo = job && job.media === "video";
    vid.classList.toggle("hidden", !isVideo);
    $("result-frame").classList.toggle("hidden", !!isVideo);
    if (isVideo) {
      vid.src = "/api/image?id=" + encodeURIComponent(job.id);
      try { vid.play().catch(() => {}); } catch (e) {}
      if ($("result-compare")) $("result-compare").classList.add("hidden");
    } else {
      try { vid.pause(); } catch (e) {}
      vid.removeAttribute("src");
    }
    return r;
  };

  function decorateVideos(container, items) {
    const kids = [...container.children].filter((c) => c.tagName === "BUTTON");
    (items || []).forEach((it, i) => {
      if (!it || it.media !== "video" || !kids[i]) return;
      const thumb = kids[i].querySelector(".thumb") || kids[i];
      if (thumb.querySelector("video")) return;
      thumb.innerHTML = "";
      const v = document.createElement("video");
      v.muted = true; v.playsInline = true; v.preload = "metadata";
      v.setAttribute("playsinline", "");
      v.src = it.url + "#t=0.1";
      thumb.appendChild(v);
      const badge = document.createElement("span");
      badge.className = "vid-badge";
      badge.textContent = "▶ Video";
      kids[i].appendChild(badge);
    });
  }
  const prevRecent = refreshRecent;
  refreshRecent = async function () {
    const r = await prevRecent.apply(this, arguments);
    decorateVideos($("recent-row"), state.recentItems);
    return r;
  };
  const prevLib = refreshLibrary;
  refreshLibrary = async function () {
    const r = await prevLib.apply(this, arguments);
    decorateVideos($("lib-grid"), state.library);
    return r;
  };
  const prevOpen = openViewer;
  openViewer = function (index) {
    const r = prevOpen.apply(this, arguments);
    const it = state.library[state.viewerIndex];
    const v = $("viewer-video");
    const isVideo = it && it.media === "video";
    $("viewer-img").classList.toggle("hidden", !!isVideo);
    v.classList.toggle("hidden", !isVideo);
    if (isVideo) { v.src = it.url; try { v.play().catch(() => {}); } catch (e) {} }
    else { try { v.pause(); } catch (e) {} v.removeAttribute("src"); }
    return r;
  };
  const prevClose = closeViewer;
  closeViewer = function () {
    try { $("viewer-video").pause(); } catch (e) {}
    return prevClose.apply(this, arguments);
  };

  /* ---------- Workflows page ---------- */
  const prevView = setView;
  setView = function (view) {
    const page = $("view-workflows");
    if (view === "workflows") {
      state.view = "workflows";
      $("view-create").classList.add("hidden");
      $("view-library").classList.add("hidden");
      page.classList.remove("hidden");
      document.body.classList.remove("view-create", "view-library");
      document.body.classList.add("view-workflows");
      document.querySelectorAll(".views button").forEach((b) => b.classList.toggle("is-on", b.dataset.view === "workflows"));
      hidePops();
      closeAddMenu();
      window.scrollTo(0, 0);
      // start reading-in help early (ComfyUI's page opens in the background) so an upload is quick
      fetch("/api/workflows/warm", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
      renderList();
      return;
    }
    page.classList.add("hidden");
    document.body.classList.remove("view-workflows");
    return prevView.apply(this, arguments);
  };

  function taskIcon(t) { return TASK_MEDIA[t] === "video" ? ICON.video : ICON.image; }

  /* ---------- install manager: files each engine needs on THIS PC, official downloads the user starts ---------- */
  let INSTALL = {};
  async function loadInstallPlan() {
    try {
      const d = await api("/api/install/plan");
      INSTALL = {};
      (d.items || []).forEach((i) => { INSTALL[i.id] = i; });
    } catch (e) { INSTALL = {}; }
  }
  let instTimer = 0;
  async function pollInstall() {
    clearTimeout(instTimer);
    let st;
    try { st = await api("/api/install/progress"); } catch (e) { return; }
    const bar = $("inst-bar");
    if (!bar) return;
    const busy = !!(st.active || (st.queue || []).length);
    bar.classList.toggle("hidden", !busy && !(st.errors || []).length);
    if (busy) {
      const a = st.active || {};
      const pct = a.total ? Math.floor(100 * (a.got || 0) / a.total) : null;
      bar.querySelector(".inst-text").textContent = "Downloading " + (a.file || "\u2026") + (pct != null ? " \u00b7 " + pct + "%" : "")
        + ((st.queue || []).length ? " \u00b7 " + st.queue.length + " more after this" : "");
      bar.querySelector(".inst-fill").style.width = (pct || 2) + "%";
      bar.querySelector(".inst-cancel").classList.remove("hidden");
      instTimer = setTimeout(pollInstall, 1500);
    } else {
      const err = (st.errors || []).slice(-1)[0];
      bar.querySelector(".inst-text").textContent = err ? "Download stopped: " + err.file + " \u2014 " + err.error : "";
      bar.querySelector(".inst-fill").style.width = "0%";
      bar.querySelector(".inst-cancel").classList.add("hidden");
      if (window.__instWasBusy) { window.__instWasBusy = false; renderList(); }
    }
    if (busy) window.__instWasBusy = true;
  }
  function startInstall(w, ip) {
    const files = (ip.files || []).filter((f) => !f.present && f.url);
    askConfirm({
      title: "Download " + files.length + " file" + (files.length === 1 ? "" : "s") + " for " + (w.name || "this engine") + "?",
      body: "From the official Hugging Face pages into your ComfyUI folders:\n" + files.map((f) => "\u2022 " + f.file + " \u2192 " + f.folder).join("\n")
        + "\n\nModel files are often several GB each. Free space on that drive: " + (ip.free_gb != null ? ip.free_gb + " GB" : "unknown")
        + ". The download stops by itself if space runs low.",
      yes: "Download",
      onYes: async () => {
        try {
          await api("/api/install/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: w.id }) });
          toast("Download started");
          pollInstall();
        } catch (err) { showError(err.message || "Couldn't start the download."); }
      },
    });
  }
  if (!$("inst-bar") && $("wf-list")) {
    const bar = document.createElement("div");
    bar.id = "inst-bar";
    bar.className = "inst-bar hidden";
    bar.innerHTML = '<div class="inst-row"><span class="inst-text"></span><button type="button" class="chip-btn inst-cancel">Cancel</button></div><div class="inst-track"><div class="inst-fill"></div></div>';
    $("wf-list").parentNode.insertBefore(bar, $("wf-list"));
    bar.querySelector(".inst-cancel").addEventListener("click", async () => {
      try { await api("/api/install/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) {}
      pollInstall();
    });
  }

  async function renderList() {
    await loadList();
    await loadInstallPlan();
    pollInstall();
    if (typeof window.renderHwBar === "function") window.renderHwBar();
    const box = $("wf-list");
    box.innerHTML = "";
    const groups = {};
    groups[BUILTIN.family] = [Object.assign({}, BUILTIN, { builtinCard: true })];
    list.forEach((w) => { (groups[w.family || "Custom"] = groups[w.family || "Custom"] || []).push(w); });
    Object.keys(groups).forEach((fam) => {
      const sec = document.createElement("section");
      sec.className = "wf-family";
      sec.innerHTML = "<h2></h2><div class=\"wf-cards\"></div>";
      sec.querySelector("h2").textContent = fam;
      const cards = sec.querySelector(".wf-cards");
      groups[fam].forEach((w) => cards.appendChild(cardEl(w)));
      box.appendChild(sec);
    });
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "wf-empty";
      empty.innerHTML = "No uploaded workflows yet. Tap <b>+ Upload workflow</b> to add any ComfyUI workflow, such as LTX or Wan for video.";
      box.appendChild(empty);
    }
  }

  function cardEl(w) {
    const el = document.createElement("div");
    el.className = "wf-card";
    const tasks = w.builtinCard ? w.tasks : [w.task];
    const inUse = tasks.some((t) => chosenId(t) === w.id);
    el.classList.toggle("is-active", inUse);
    el.innerHTML = '<div class="wf-card-top">' + taskIcon(tasks[0]) + '<h3></h3></div><div class="wf-meta"></div><div class="wf-actions"></div>';
    el.querySelector("h3").textContent = w.name || "Workflow";
    const meta = el.querySelector(".wf-meta");
    const tag = (text, cls) => { const s = document.createElement("span"); s.className = "wf-tag" + (cls ? " " + cls : ""); s.textContent = text; meta.appendChild(s); };
    tasks.forEach((t) => tag(TASK_LABEL[t] || t));
    tag(w.builtinCard ? "Built-in" : (w.source === "user" ? "Yours" : "Built-in"));
    if ((w.missing_nodes || []).length) tag(w.missing_nodes.length + " missing node" + (w.missing_nodes.length === 1 ? "" : "s"), "warn");
    if (inUse) tag("In use", "on");
    const missN = (w.missing_custom || []).length, missM = (w.missing_models || []).length;
    if (missN || missM) {
      tag("Missing " + [missN ? missN + " node" + (missN === 1 ? "" : "s") : "", missM ? missM + " LoRA" + (missM === 1 ? "" : "s") : ""].filter(Boolean).join(", "), "warn");
    }
    if (w.fit === "ok") tag("Fits your PC", "ok");
    else if (w.fit === "heavy") { tag("Heavy for your PC", "warn"); }
    else if (w.fit === "no") { tag("Too heavy for your PC", "no"); }
    const ip = INSTALL[w.id];
    if (ip && ip.missing) {
      tag("Needs " + ip.missing + " file" + (ip.missing === 1 ? "" : "s"), "warn");
      const det = document.createElement("details");
      det.className = "fold-block wf-req";
      det.innerHTML = '<summary><span class="fold-label">Missing files</span></summary><p class="fold-note"></p>';
      det.querySelector("p").textContent = (ip.files || []).filter((f) => !f.present).map((f) =>
        f.file + " (" + f.folder + ")" + (f.url ? "" : " \u2014 no official link: add it yourself or pick another file in Settings")).join("\n");
      el.insertBefore(det, el.querySelector(".wf-actions"));
    } else if (ip && (ip.files || []).length) {
      tag("All files here", "ok");
    }
    if (w.requires && ((w.requires.models || []).length || (w.requires.custom_nodes || []).length)) {
      const det = document.createElement("details");
      det.className = "fold-block wf-req";
      det.innerHTML = "<summary><span class=\"fold-label\">What it uses</span></summary><p class=\"fold-note\"></p>";
      const lines = [];
      if ((w.requires.custom_nodes || []).length) lines.push("Node packs: " + w.requires.custom_nodes.join(", "));
      (w.requires.models || []).forEach((f) => lines.push("LoRA: " + ((w.missing_models || []).indexOf(f) >= 0 ? "missing " : "") + f));
      det.querySelector("p").textContent = lines.join("\n");
      el.insertBefore(det, el.querySelector(".wf-actions"));
    }
    if (w.description || w.reason) {
      const d = document.createElement("p");
      d.className = "wf-desc";
      d.textContent = [w.description, w.fit !== "ok" ? w.reason : ""].filter(Boolean).join(" ");
      el.insertBefore(d, el.querySelector(".wf-actions"));
    }
    const actions = el.querySelector(".wf-actions");
    const use = document.createElement("button");
    use.type = "button";
    use.className = "chip-btn primary";
    use.textContent = "Use";
    use.addEventListener("click", () => useWorkflow(w));
    actions.appendChild(use);
    if (ip && ip.downloadable && ip.fit !== "no") {
      const dl = document.createElement("button");
      dl.type = "button";
      dl.className = "chip-btn";
      dl.textContent = "Download " + ip.downloadable + " file" + (ip.downloadable === 1 ? "" : "s");
      dl.addEventListener("click", () => startInstall(w, ip));
      actions.appendChild(dl);
    }
    if (!w.builtinCard && w.source === "user") {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "chip-btn danger";
      del.textContent = "Delete";
      del.addEventListener("click", () => askConfirm({
        title: "Delete this workflow?",
        body: "“" + (w.name || "Workflow") + "” is removed from the app. Your ComfyUI files and past results are not touched.",
        yes: "Delete",
        onYes: async () => {
          try { await api("/api/workflows/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: w.id }) }); }
          catch (e) { toast(e.message); return; }
          if (chosenId(w.task) === w.id) LS.set("cs.wf." + w.task, isImageTask(w.task) ? "builtin" : "");
          toast("Workflow deleted");
          renderList();
          applyEngine();
        },
      }));
      actions.appendChild(del);
    }
    return el;
  }

  function useWorkflow(w) {
    const task = w.builtinCard ? (state.mode === "edit" ? "edit" : "t2i") : w.task;
    if (w.builtinCard) { LS.set("cs.wf.t2i", "builtin"); LS.set("cs.wf.edit", "builtin"); }
    else LS.set("cs.wf." + task, w.id);
    setView("create");
    if (TASK_MEDIA[task] === "video") { setMedia("video"); setVideoMode(task); }
    else { setMedia("image"); setMode(task === "edit" ? "edit" : "t2i"); }
    applyEngine();
    toast((w.name || "Workflow") + " is ready");
  }

  /* ---------- upload + check screen ---------- */
  let draft = null;     /* last inspect response */
  let draftText = "";

  $("wf-upload-btn").addEventListener("click", () => $("wf-file").click());
  $("wf-file").addEventListener("change", async (e) => {
    const f = (e.target.files || [])[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > 8 * 1024 * 1024) { toast("That file is too big to be a workflow"); return; }
    draftText = await f.text();
    await inspect(f.name.replace(/\.json$/i, "").replace(/[_-]+/g, " ").trim());
  });

  /* ---- the upload sheet in plain words: loading -> one workflow (ready / needs one thing) or a list of parts ---- */
  function sheetState(which) {
    $("wf-loading").classList.toggle("hidden", which !== "loading");
    $("wf-parts").classList.toggle("hidden", which !== "parts");
    $("wf-simple").classList.toggle("hidden", which !== "one");
    $("wf-advanced").classList.toggle("hidden", which !== "one");
    $("wf-save").classList.toggle("hidden", which === "loading");
  }
  let parts = null;      /* several workflows in one file: [{draft, title, task, plain, ready, card, ...}] */

  async function inspect(name) {
    parts = null;
    $("wf-sheet-title").textContent = "Add a workflow";
    $("wf-loading-note").textContent = "The first one takes about 20 seconds while ComfyUI opens in the background. After that it's quick.";
    sheetState("loading");
    $("wf-sheet").classList.remove("hidden");
    let res;
    try {
      res = await api("/api/workflows/inspect", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: draftText, name: name || ($("wf-name").value || "") }) });
    } catch (e) {
      $("wf-sheet").classList.add("hidden");
      showError((e.message || "Couldn't read that file.") + " If it keeps happening, check that ComfyUI is running.");
      return;
    }
    if (res.parts) { parts = res.parts; draft = null; renderParts(res); return; }
    draft = res;
    renderSheet();
    sheetState("one");
  }

  function partIcon(media) { return media === "video" ? "\u{1F3AC}" : "\u{1F5BC}️"; }

  function renderParts(res) {
    $("wf-sheet-title").textContent = "Add workflows";
    $("wf-parts-lead").textContent = (res.message || "This file contains several workflows.") + " We'll add each one:";
    const box = $("wf-part-list");
    box.innerHTML = "";
    parts.forEach((p, i) => {
      const row = document.createElement("label");
      row.className = "wf-part" + (p.ready ? "" : " needs");
      row.innerHTML = '<input type="checkbox"><span class="wf-part-ic" aria-hidden="true"></span><span class="wf-part-txt"><strong></strong><small></small><em></em></span>';
      const cb = row.querySelector("input");
      cb.checked = !!p.ready;
      cb.dataset.i = i;
      row.querySelector(".wf-part-ic").textContent = partIcon(p.media);
      row.querySelector("strong").textContent = p.title;
      row.querySelector("small").textContent = p.plain || "";
      row.querySelector("em").textContent = p.ready ? "✓ Ready to use"
        : ((p.missing_nodes || []).length ? "Needs an add-on that isn't installed yet" : "Needs one quick answer");
      if (!p.ready) {
        const fix = document.createElement("button");
        fix.type = "button"; fix.className = "chip-btn"; fix.textContent = "Set up";
        fix.addEventListener("click", (e) => { e.preventDefault(); openPart(i); });
        row.appendChild(fix);
      }
      box.appendChild(row);
    });
    const n = () => box.querySelectorAll("input:checked").length;
    const sync = () => { $("wf-save").textContent = n() ? "Add " + n() : "Add"; $("wf-save").disabled = !n(); };
    box.addEventListener("change", sync);
    sync();
    sheetState("parts");
  }

  let partsBackup = null;
  function openPart(i) {
    /* one part of a several-workflows file, on its own: same screen as a single upload, with a way back */
    const p = parts[i];
    partsBackup = { parts, i };
    parts = null;
    draft = { draft: p.draft, card: p.card, summary: p.summary, candidates: p.candidates, tasks: WF_TASKS, warnings: [], nodes: null };
    renderSheet();
    $("wf-sheet-title").textContent = p.title;
    sheetState("one");
  }
  const WF_TASKS = { t2i: "Text to image", edit: "Image edit", t2v: "Text to video", i2v: "Image to video",
                     r2v: "Reference to video", v2v: "Video to video", other: "Other" };

  function renderPlain() {
    /* the top of the sheet: one sentence, then Ready / the one thing it needs */
    const c = draft.card, s = c.slots || {};
    $("wf-plain").textContent = c.plain || draft.plain || "";
    const missing = c.missing_nodes || [];
    const noPrompt = !(s.prompt || []).length;
    const st = $("wf-status");
    st.className = "wf-status " + (missing.length || noPrompt ? "warn" : "ok");
    st.textContent = missing.length ? "Needs an add-on that isn't installed yet. Install it below, then tap Check again."
      : noPrompt ? "Needs one quick answer below."
      : "✓ Ready to use. Tap Add.";
    const ask = $("wf-ask");
    ask.classList.toggle("hidden", !noPrompt || !(draft.candidates || []).length);
    if (noPrompt && (draft.candidates || []).length) {
      const sel = $("wf-ask-pick");
      sel.innerHTML = "";
      const none = document.createElement("option");
      none.value = "none"; none.textContent = "None of these (the workflow has no prompt)";
      const seen = new Set();
      (draft.candidates || []).filter((c2) => !c2.negative && c2.kind !== "template").slice(0, 8).forEach((c2) => {
        const label = (c2.title || "Text box") + (c2.preview ? " — “" + c2.preview.replace(/\s+/g, " ").trim().slice(0, 40) + "”" : " (empty)");
        if (seen.has(label)) return;
        seen.add(label);
        const o = document.createElement("option");
        o.value = c2.node + ":" + c2.input;
        o.textContent = label;
        sel.appendChild(o);
      });
      sel.appendChild(none);
      sel.onchange = () => { $("wf-prompt-pick").value = sel.value; };
      $("wf-prompt-pick").value = sel.value;
    }
  }

  /* ---- Test run: one small generation with this upload, before it's added ---- */
  let testToken = 0;
  function resetTest() {
    testToken++;
    const btn = $("wf-test-btn");
    btn.disabled = false; btn.textContent = "Test run";
    $("wf-test-note").classList.remove("hidden");
    const r = $("wf-test-result");
    r.classList.add("hidden"); r.className = "wf-test-result hidden"; r.innerHTML = "";
  }
  function testBody() {
    return { draft: draft.draft, name: $("wf-name").value.trim() || "My workflow", family: $("wf-family-in").value.trim() || "Custom",
             task: $("wf-task").value, prompt_pick: $("wf-prompt-pick").value, negative_pick: $("wf-negative-pick").value };
  }
  function testShow(kind, html) {
    const r = $("wf-test-result");
    r.className = "wf-test-result " + kind;
    r.innerHTML = html;
  }
  const esc = (t) => String(t || "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  $("wf-test-btn").addEventListener("click", async () => {
    if (!draft) return;
    const my = ++testToken;
    const btn = $("wf-test-btn");
    btn.disabled = true; btn.textContent = "Testing…";
    $("wf-test-note").classList.add("hidden");
    testShow("busy", '<span class="wf-spinner" aria-hidden="true"></span><span class="wf-test-msg">Starting the test…</span>');
    let r;
    try { r = await api("/api/workflows/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(testBody()) }); }
    catch (e) { r = { ok: false, problem: e.message || "The test couldn't start." }; }
    if (r.test_id) lastTestId = r.test_id;
    if (my !== testToken) return;
    if (!r.ok) {
      testShow("bad", "<strong>✕ It didn't run.</strong><p>" + esc(r.problem) + "</p>");
      btn.disabled = false; btn.textContent = "Test again";
      return;
    }
    const t0 = Date.now();
    for (;;) {
      await new Promise((res) => setTimeout(res, 2000));
      if (my !== testToken) return;
      let j;
      try { j = await api("/api/workflows/test/status?id=" + encodeURIComponent(r.job)); } catch (e) { continue; }
      const secs = Math.round((Date.now() - t0) / 1000);
      if (j.status === "success" || j.status === "done") {
        const im = (j.images || [])[0] || {};
        const url = "/api/image?id=" + encodeURIComponent(r.job) + "&t=" + Date.now();
        const media = im.kind === "video" ? '<video src="' + url + '" controls muted loop playsinline></video>' : '<img src="' + url + '" alt="Test result">';
        testShow("good", "<strong>✓ It works.</strong><p>Made in " + secs + " s with the test prompt “" + esc(r.prompt) + "”. Tap <b>Add</b>.</p>" + media);
        btn.disabled = false; btn.textContent = "Test again";
        return;
      }
      if (j.status === "error" || j.status === "cancelled") {
        testShow("bad", "<strong>✕ It didn't work.</strong><p>" + esc(j.problem || j.error || "The test stopped.") + "</p>");
        btn.disabled = false; btn.textContent = "Test again";
        return;
      }
      const m = $("wf-test-result").querySelector(".wf-test-msg");
      if (m) m.textContent = (j.message && !/^Waiting/.test(j.message) ? j.message : "Testing…") + " (" + secs + " s)";
      if (secs > 900) { testShow("bad", "<strong>The test is taking very long.</strong><p>It may still finish; you can add the workflow anyway.</p>"); btn.disabled = false; return; }
    }
  });

  function renderSheet() {
    resetTest();
    const c = draft.card, sm = draft.summary || {};
    $("wf-name").value = c.name || "";
    $("wf-family-in").value = c.family || "Custom";
    $("wf-family-list").innerHTML = [...new Set(list.map((w) => w.family).concat(["LTX Video", "Wan", "Flux", "SDXL", "Krea", "MiniMax H3"]))]
      .map((f) => '<option value="' + String(f).replace(/"/g, "&quot;") + '">').join("");
    const task = $("wf-task");
    task.innerHTML = "";
    Object.keys(draft.tasks || TASK_LABEL).forEach((k) => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = (TASK_MEDIA[k] === "video" ? "Video · " : "Image · ") + (draft.tasks || TASK_LABEL)[k];
      task.appendChild(o);
    });
    task.value = c.task;
    const found = $("wf-found");
    found.innerHTML = "<div><strong>Found:</strong></div><div class=\"chips\"></div><div class=\"muted\"></div>";
    (sm.found || []).forEach((t) => { const s = document.createElement("span"); s.className = "chip"; s.textContent = t; found.querySelector(".chips").appendChild(s); });
    if (!(sm.found || []).length) found.querySelector(".chips").textContent = "Nothing standard was recognised.";
    found.querySelector(".muted").textContent = "Output: " + (sm.output || "none") + ". Controls the app doesn't recognise keep the values saved in the workflow.";

    const pickers = [["wf-prompt-pick", (c.slots.prompt || [])[0], "Not used"], ["wf-negative-pick", (c.slots.negative || [])[0], "None"]];
    pickers.forEach(([id, slot, noneLabel]) => {
      const sel = $(id);
      sel.innerHTML = "";
      const none = document.createElement("option");
      none.value = "none"; none.textContent = noneLabel;
      sel.appendChild(none);
      // best guess first, in plain words: "★ Suggested — Label the user's notes: empty box, goes into the image prompt"
      (draft.candidates || []).forEach((cand) => {
        const o = document.createElement("option");
        o.value = cand.node + ":" + cand.input;
        const what = cand.label || cand.input;
        const peek = cand.kind === "empty" ? "" : (cand.preview ? " (“" + cand.preview.replace(/\s+/g, " ").trim().slice(0, 36) + "”)" : "");
        o.textContent = (cand.suggested && id === "wf-prompt-pick" ? "★ Suggested — " : "") + cand.title + ": " + what + peek;
        sel.appendChild(o);
      });
      sel.value = slot ? (slot.node + ":" + slot.input) : "none";
    });

    const notes = $("wf-notes");
    notes.innerHTML = "";
    (draft.warnings || []).concat(c.notes || []).forEach((n) => { const li = document.createElement("li"); li.textContent = n; notes.appendChild(li); });
    renderMissing();
    renderPlain();
  }

  function renderMissing() {
    const box = $("wf-missing");
    const nodes = draft.nodes;
    const missing = draft.card.missing_nodes || [];
    box.classList.toggle("hidden", !missing.length);
    if (!missing.length) return;
    box.innerHTML = "<h3>Add-on needed</h3><p class=\"pop-note\">This workflow uses parts that aren't in your ComfyUI yet. " +
      "Tap <b>Install</b>, wait until it says done, then tap <b>Restart ComfyUI</b>. The app checks the workflow again by itself.</p>";
    if (nodes && nodes.message) { const p = document.createElement("p"); p.className = "pop-note"; p.textContent = nodes.message; box.appendChild(p); }
    ((nodes && nodes.packs) || []).forEach((p) => {
      const row = document.createElement("div");
      row.className = "wf-pack";
      row.innerHTML = "<div><strong></strong><small></small></div>";
      row.querySelector("strong").textContent = p.title + (p.author ? " · " + p.author : "");
      row.querySelector("small").textContent = "Provides " + p.nodes.join(", ") + (p.stars ? " · ★ " + p.stars : "") + (p.repository ? " · " + p.repository : "");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip-btn";
      if (p.installed) { b.textContent = "Restart ComfyUI"; b.addEventListener("click", restartComfy); }
      else if (p.registry && p.id) { b.textContent = "Install"; b.classList.add("primary"); b.addEventListener("click", () => installPacks([p.id], b)); }
      else if (/^https:\/\/github\.com\/[^/]+\/[^/]+/i.test(p.repository || "")) {
        b.textContent = "Install from GitHub"; b.classList.add("primary");
        b.addEventListener("click", () => installGithub(p.repository, b));
      }
      else { b.textContent = "Not on GitHub"; b.disabled = true; b.title = "Add it below from a zip file."; }
      row.appendChild(b);
      box.appendChild(row);
    });
    const srcs = draft.node_sources || {};
    ((nodes && nodes.unknown) || []).forEach((n) => {
      const row = document.createElement("div");
      row.className = "wf-pack";
      const gh = (srcs[n] || {}).github;
      row.innerHTML = "<div><strong></strong><small></small></div>";
      row.querySelector("strong").textContent = n;
      row.querySelector("small").textContent = gh
        ? "From " + gh.replace("https://github.com/", "") + " on GitHub."
        : "We couldn't find where this add-on comes from. Ask the workflow's author for a link, then use “I have a link” below.";
      if (gh) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip-btn primary"; b.textContent = "Install from GitHub";
        b.addEventListener("click", () => installGithub(gh, b));
        row.appendChild(b);
      }
      box.appendChild(row);
    });
    // any other pack: a GitHub link or a zip (folded away: most people never need it)
    const add = document.createElement("details");
    add.className = "wf-pack wf-pack-add";
    add.innerHTML = '<summary>I have a link or a zip file for the add-on</summary>'
      + '<div class="wf-pack-add-row"><input type="url" class="wf-gh-url" placeholder="https://github.com/owner/repo" aria-label="GitHub link of a node pack">'
      + '<button type="button" class="chip-btn wf-gh-go">Install</button><button type="button" class="chip-btn wf-zip-go">Add from zip</button>'
      + '<input type="file" class="wf-zip-file" accept=".zip,application/zip" hidden></div>';
    add.querySelector(".wf-gh-go").addEventListener("click", (e) => installGithub(add.querySelector(".wf-gh-url").value.trim(), e.currentTarget));
    add.querySelector(".wf-zip-go").addEventListener("click", () => add.querySelector(".wf-zip-file").click());
    add.querySelector(".wf-zip-file").addEventListener("change", (e) => installZip(e.target.files && e.target.files[0], add.querySelector(".wf-zip-go")));
    box.appendChild(add);
    const again = document.createElement("button");
    again.type = "button";
    again.className = "chip-btn";
    again.textContent = "Check again";
    again.addEventListener("click", () => inspect($("wf-name").value));
    box.appendChild(again);
  }

  /* packs that aren't in the registry: from a GitHub link or a zip (the server checks the link, never overwrites a
     pack, installs requirements with ComfyUI's own Python and skips torch) */
  async function followNodeJob(jobId, btn) {
    for (let i = 0; i < 600; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      let j = {};
      try { j = await api("/api/nodes/job?id=" + encodeURIComponent(jobId)); } catch (e) { continue; }
      if (j.state === "running") { btn.textContent = "Installing\u2026"; continue; }
      if (j.state === "done") {
        toast(j.message || "Installed. Restart ComfyUI to load it.", 4500);
        btn.textContent = "Restart ComfyUI"; btn.disabled = false; btn.classList.remove("primary"); btn.onclick = restartComfy;
      } else {
        showError(j.message || "The install didn't finish.");
        btn.textContent = "Try again"; btn.disabled = false;
      }
      return;
    }
  }
  async function installGithub(url, btn) {
    if (!/^https:\/\/github\.com\/[^/\s]+\/[^/\s]+/i.test(url || "") && !/^[\w.-]+\/[\w.-]+$/.test(url || "")) {
      showError("Paste a GitHub link like https://github.com/owner/repo"); return;
    }
    if (!window.confirm("Install the node pack from " + url + " into ComfyUI?\n\nNode packs are other people's code and run on this PC. Only install packs you trust.")) return;
    btn.disabled = true; btn.textContent = "Downloading\u2026";
    try {
      const r = await api("/api/nodes/github", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
      followNodeJob(r.job, btn);
    } catch (e) { btn.disabled = false; btn.textContent = "Install"; showError(e.message || "Couldn't install it."); }
  }
  async function installZip(file, btn) {
    if (!file) return;
    if (!window.confirm("Add the node pack in " + file.name + " to ComfyUI?\n\nNode packs are other people's code and run on this PC. Only add packs you trust.")) return;
    btn.disabled = true; btn.textContent = "Adding\u2026";
    try {
      const res = await fetch("/api/nodes/zip", { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
      const r = await res.json();
      if (!res.ok) throw new Error(r.error || "Couldn't add it.");
      followNodeJob(r.job, btn);
    } catch (e) { btn.disabled = false; btn.textContent = "Add from zip"; showError(e.message); }
  }
  window.__installGithub = installGithub;
  window.__installNodeZip = installZip;

  async function installPacks(ids, btn) {
    const ok = window.confirm("Install " + ids.join(", ") + " into ComfyUI? Node packs run code on this PC. Only install packs you trust.");
    if (!ok) return;
    btn.disabled = true;
    btn.textContent = "Installing…";
    let res;
    try {
      res = await api("/api/nodes/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ packs: ids }) });
    } catch (e) { btn.disabled = false; btn.textContent = "Install"; toast(e.message, 3500); return; }
    if ((res.blocked || []).length) toast(res.blocked[0].reason, 4000);
    if (!(res.queued || []).length) { btn.disabled = false; btn.textContent = "Install"; if (res.message) toast(res.message, 4000); return; }
    for (let i = 0; i < 300; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      let st = {};
      try { st = await api("/api/nodes/status"); } catch (e) {}
      if (st.done_count != null) btn.textContent = "Installing… " + st.done_count + "/" + (st.total_count || ids.length);
      if (!st.is_processing && i > 1) break;
    }
    btn.textContent = "Restart ComfyUI";
    btn.disabled = false;
    btn.classList.remove("primary");
    btn.onclick = restartComfy;
    toast("Installed. Restart ComfyUI to load it.", 3500);
  }

  async function restartComfy() {
    if (!window.confirm("Restart ComfyUI now? Anything it's generating will stop.")) return;
    try { await api("/api/comfy/restart", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) {}
    toast("ComfyUI is restarting…", 4000);
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      try { const s = await api("/api/status"); if (s.comfy && i > 2) break; } catch (e) {}
    }
    toast("ComfyUI is back. Checking the workflow again…", 2500);
    if (draft) inspect($("wf-name").value);
  }

  async function saveDraft(body) {
    return api("/api/workflows/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  function whereItIs(card) {
    return (TASK_MEDIA[card.task] === "video" ? "Video" : "Image") + " › " + (TASK_LABEL[card.task] || WF_TASKS[card.task] || "Other");
  }
  async function afterAdded(cards) {
    lastTestId = null;
    $("wf-sheet").classList.add("hidden");
    cards.forEach((card) => { if (!isImageTask(card.task) && !chosenId(card.task)) LS.set("cs.wf." + card.task, card.id); });
    await loadList();
    renderList();
    applyEngine();
    if (cards.length === 1) {
      const w = (list || []).find((x) => x.id === cards[0].id);
      if (w && window.confirm("Added “" + cards[0].name + "”. You'll find it under " + whereItIs(cards[0]) + ".\n\nUse it now?")) useWorkflow(w);
      else toast("Added. It's under " + whereItIs(cards[0]) + ".", 4000);
    } else {
      toast("Added " + cards.length + " workflows. Tap Use on any of them below.", 4500);
    }
  }

  $("wf-save").addEventListener("click", async () => {
    const btn = $("wf-save");
    if (parts) {                                  // several workflows from one file
      const picked = [...$("wf-part-list").querySelectorAll("input:checked")].map((cb) => parts[+cb.dataset.i]);
      if (!picked.length) return;
      btn.disabled = true;
      const done = [];
      for (const [k, p] of picked.entries()) {
        btn.textContent = "Adding " + (k + 1) + "/" + picked.length + "…";
        try { done.push((await saveDraft({ draft: p.draft, name: p.card.name, family: p.card.family, task: p.task })).card); }
        catch (e) { showError("Couldn't add “" + p.title + "”: " + e.message); }
      }
      btn.disabled = false; btn.textContent = "Add";
      if (done.length) afterAdded(done);
      return;
    }
    if (!draft) return;
    const body = {
      draft: draft.draft,
      name: $("wf-name").value.trim() || "My workflow",
      family: $("wf-family-in").value.trim() || "Custom",
      task: $("wf-task").value,
      prompt_pick: $("wf-prompt-pick").value,
      negative_pick: $("wf-negative-pick").value,
    };
    let res;
    try { res = await saveDraft(body); }
    catch (e) { showError(e.message || "Couldn't add it."); return; }
    if (partsBackup) {                            // came from the list: mark it done and go back
      const back = partsBackup;
      partsBackup = null;
      back.parts.splice(back.i, 1);
      if (back.parts.length) {
        parts = back.parts; draft = null;
        renderParts({ message: "Added “" + res.card.name + "”." });
        toast("Added. Add the others, or close this.", 3000);
        await loadList(); renderList();
        return;
      }
    }
    afterAdded([res.card]);
  });
  let lastTestId = null;     /* the hidden Test run entry of the open upload, removed when the sheet closes unsaved */
  const closeSheet = () => {
    $("wf-sheet").classList.add("hidden");
    testToken++;
    if (lastTestId) {
      const id = lastTestId; lastTestId = null;
      api("/api/workflows/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) }).catch(() => {});
    }
  };
  $("wf-sheet-close").addEventListener("click", closeSheet);
  $("wf-sheet-backdrop").addEventListener("click", closeSheet);
  if (window.__swipeClose) window.__swipeClose(document.querySelector("#wf-sheet .sheet-card"), closeSheet);
  window.closeWfSheet = closeSheet;

  /* ---------- app updates ---------- */
  async function checkUpdate() {
    const msg = $("update-msg"), apply = $("update-apply");
    msg.textContent = "Checking…";
    apply.classList.add("hidden");
    try {
      const s = await api("/api/update/check");
      $("app-version").textContent = "Comfy Studio " + (s.version || "") + (s.head ? " · " + s.head : "");
      msg.textContent = s.message || "";
      if (s.behind > 0) {
        if ((s.incoming || []).length) msg.textContent += " " + s.incoming.slice(0, 3).join(" · ");
        apply.classList.remove("hidden");
      }
    } catch (e) { msg.textContent = e.message || "Couldn't check for updates."; }
  }
  $("update-check").addEventListener("click", checkUpdate);
  window.checkUpdate = checkUpdate;
  $("update-apply").addEventListener("click", async () => {
    if (!window.confirm("Update Comfy Studio now? The app restarts; your library, PIN and workflows are kept.")) return;
    const msg = $("update-msg");
    try {
      const r = await api("/api/update/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      msg.textContent = r.message || "Updating…";
      if (!r.updated) return;
    } catch (e) { msg.textContent = e.message; return; }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try { await api("/api/status"); location.reload(); return; } catch (e) {}
    }
  });

  applyEngine();
})();
/* ===== end workflows ===== */

/* ===== Video types, engine limits, ComfyUI mode switch, PC fit v20261111i ===== */
(function () {
  const VTASKS = [
    { id: "t2v", label: "Text", title: "Text to video", desc: "A clip from words.",
      icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 11h14M5 16h9"/><path d="M17 15l4 2.5-4 2.5z"/></svg>' },
    { id: "i2v", label: "Image", title: "Image to video", desc: "Animates Picture 1 (some engines take a last frame too).",
      icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6.5" width="9" height="11" rx="2.2"/><path d="M5 15l2-2 1.5 1.5"/><path d="M13.5 12h2.5M14.8 10.5l1.5 1.5-1.5 1.5"/><path d="M18 8.5l4 3.5-4 3.5z"/></svg>' },
    { id: "r2v", label: "Refs", title: "Reference to video", desc: "A new scene with the people, things and places in your pictures.",
      icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="4" width="7" height="7" rx="1.6"/><rect x="2.5" y="13" width="7" height="7" rx="1.6"/><path d="M12 12h3M13.8 10.5l1.5 1.5-1.5 1.5"/><path d="M17.5 8.5l4 3.5-4 3.5z"/></svg>' },
    { id: "v2v", label: "Video", title: "Video to video", desc: "Edits, restyles or animates from a video you add (uploaded workflows).",
      icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="7" width="9" height="10" rx="2"/><path d="M4.5 10l3 2-3 2z"/><path d="M12.5 12h2.5M13.8 10.5l1.5 1.5-1.5 1.5"/><path d="M17.5 8.5l4 3.5-4 3.5z"/></svg>' },
    { id: "long", label: "Long", title: "Long text to video", desc: "A longer story made of seamless clips.",
      icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="7" width="6" height="10" rx="1.5"/><rect x="9" y="7" width="6" height="10" rx="1.5"/><path d="M17 9.5l5 2.5-5 2.5z"/></svg>' },
  ];

  function syncTaskChip() {
    const t = VTASKS.find((x) => x.id === state.vmode) || VTASKS[0];
    $("vtask-icon").innerHTML = t.icon;
    $("vtask-label").textContent = t.label;
    $("vtask-btn").setAttribute("aria-label", "Video type: " + t.title);
  }

  function renderTaskPop() {
    const box = $("vtask-list");
    box.innerHTML = "";
    VTASKS.forEach((t) => {
      const engines = (window.__wfForTask ? window.__wfForTask(t.id) : []);
      const b = document.createElement("button");
      b.type = "button";
      b.className = "option has-icon" + (t.id === state.vmode ? " is-on" : "") + (engines.length ? "" : " is-off");
      b.innerHTML = t.icon + "<div><strong></strong><span></span><span class=\"opt-engine\"></span></div>";
      b.querySelector("strong").textContent = t.title;
      b.querySelector("span").textContent = t.desc;
      b.querySelector(".opt-engine").textContent = engines.length
        ? engines.map((w) => w.family).filter((v, i, a) => a.indexOf(v) === i).join(" · ")
        : "No engine yet: add one on the Workflows page";
      b.addEventListener("click", () => {
        hidePops();
        setVideoMode(t.id);
        syncTaskChip();
      });
      box.appendChild(b);
    });
    const a = window.__wfActive && window.__wfActive();
    $("vtask-engine").textContent = a ? "Now using " + a.card.name : "Pick the engine in Settings";
  }

  $("vtask-btn").addEventListener("click", (e) => { renderTaskPop(); showPop("vtask-pop", e.currentTarget); });
  if (window.__swipeClose) window.__swipeClose($("vtask-pop"), () => hidePops(), isPhoneUi);

  /* Lengths, sizes and shapes follow the active engine; limits warn before you send. */
  function rebuildSeg(id, values, cur, fmt, onPick) {
    const seg = $(id);
    seg.innerHTML = "";
    values.forEach((v) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = fmt(v);
      b.classList.toggle("is-on", String(v) === String(cur));
      b.addEventListener("click", () => onPick(v));
      seg.appendChild(b);
    });
  }

  function lengthNote(card) {
    const lim = card && card.limits;
    let p = $("vlen-note");
    if (!p) {
      p = document.createElement("p");
      p.id = "vlen-note";
      p.className = "vlen-note";
      $("vlen-pop").appendChild(p);
    }
    if (!lim) { p.textContent = ""; return; }
    const cap = lim[state.video.res];
    p.textContent = Object.keys(lim).map((r) => r + ": up to " + lim[r] + " s").join(" · ") + " on this PC" +
      (cap && state.video.len > cap ? ". This clip will be " + cap + " s." : ".");
  }

  window.onEngineChanged = function (active) {
    const c = active && active.card;
    window.__videoAspects = c && c.aspects ? c.aspects.filter((a) => a !== "match") : null;
    if (c && c.media === "video") {
      const lengths = c.lengths || [3, 5, 8, 10];
      if (lengths.indexOf(state.video.len) < 0) state.video.len = c.default_length || lengths[0];
      const res = c.resolutions || ["480p", "720p"];
      if (res.indexOf(state.video.res) < 0) state.video.res = c.default_resolution || res[0];
      if (window.__videoAspects && window.__videoAspects.indexOf(state.video.aspect) < 0) state.video.aspect = window.__videoAspects[0];
      rebuildSeg("vlen-seg", lengths, state.video.len, (v) => v + "s", (v) => {
        state.video.len = v; LS.set("cs.v.len", String(v)); window.syncVideoPills && window.syncVideoPills(); lengthNote(c); hidePops();
      });
      rebuildSeg("vres-seg", res, state.video.res, (v) => v, (v) => {
        state.video.res = v; LS.set("cs.v.res", v); window.syncVideoPills && window.syncVideoPills(); lengthNote(c);
      });
      lengthNote(c);
    } else {
      lengthNote(null);
    }
    if (window.syncVideoPills) window.syncVideoPills();
    syncTaskChip();
  };

  /* Errors that come with a fix: switch ComfyUI mode, or use a lighter engine. */
  const actionBtn = $("dock-alert-action");
  window.showActionError = function (data) {
    showError(data.error);
    actionBtn.classList.add("hidden");
    actionBtn.onclick = null;
    if (data.code === "profile" && data.can_switch) {
      actionBtn.textContent = "Switch ComfyUI mode";
      actionBtn.classList.remove("hidden");
      actionBtn.onclick = () => switchProfile(data.profile);
    } else if (data.code === "hardware" && data.alternative) {
      const alt = (window.__wfList ? window.__wfList() : []).find((w) => w.id === data.alternative);
      if (alt) {
        actionBtn.textContent = "Use " + alt.name;
        actionBtn.classList.remove("hidden");
        actionBtn.onclick = () => {
          LS.set("cs.wf." + alt.task, alt.id);
          hideAlert();
          applyEngine();
          toast(alt.name + " selected");
        };
      }
    }
    syncDockHeight();
  };
  const baseHide = hideAlert;
  hideAlert = function () { actionBtn.classList.add("hidden"); return baseHide.apply(this, arguments); };

  async function switchProfile(profile) {
    if (!window.confirm("Restart ComfyUI in the mode this needs? It takes about a minute.")) return;
    actionBtn.disabled = true;
    actionBtn.textContent = "Switching…";
    try {
      const r = await api("/api/comfy/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile }) });
      toast(r.message, 4000);
    } catch (e) {
      actionBtn.disabled = false;
      actionBtn.textContent = "Switch ComfyUI mode";
      toast(e.message, 4000);
      return;
    }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 4000));
      try {
        const hw = await api("/api/hardware");
        if (hw.profile === profile) {
          actionBtn.disabled = false;
          hideAlert();
          toast("ComfyUI is ready. Tap generate again.", 4000);
          if (typeof window.renderHwBar === "function") window.renderHwBar();
          return;
        }
      } catch (e) {}
    }
    actionBtn.disabled = false;
    actionBtn.textContent = "Switch ComfyUI mode";
    toast("ComfyUI is taking a while. Check it on the PC.", 4000);
  }

  /* Workflows page: what this PC is, and the app version / updates. */
  const PROFILE = { h3: "MiniMax H3 mode", video: "low-VRAM video mode", everyday: "everyday mode" };
  window.renderHwBar = async function () {
    let hw = window.__hw;
    if (!hw) { try { hw = await api("/api/hardware"); } catch (e) { hw = null; } }
    const bar = $("hw-bar");
    if (!hw || hw.error) { bar.textContent = "ComfyUI isn't running, so this PC's graphics card can't be checked."; return; }
    bar.innerHTML = "";
    const item = (k, v) => { const s = document.createElement("span"); s.className = "hw-item"; s.innerHTML = k + " <b></b>"; s.querySelector("b").textContent = v; bar.appendChild(s); };
    item("Graphics", (hw.gpu || "GPU") + " · " + hw.vram_gb + " GB");
    item("RAM", hw.ram_gb + " GB");
    item("ComfyUI", PROFILE[hw.profile] || hw.profile || "?");
    const sp = document.createElement("span"); sp.className = "grow"; bar.appendChild(sp);
    if (hw.can_switch) {
      ["h3", "everyday"].filter((p) => p !== hw.profile).forEach((p) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip-btn";
        b.textContent = "Switch to " + PROFILE[p];
        b.addEventListener("click", () => switchProfile(p));
        bar.appendChild(b);
      });
    }
  };

  async function wfCheckUpdate() {
    const msg = $("wf-app-msg"), apply = $("wf-update-apply");
    msg.textContent = "Checking…";
    apply.classList.add("hidden");
    try {
      const s = await api("/api/update/check");
      $("wf-app-version").textContent = "Comfy Studio " + (s.version || "") + (s.head ? " · " + s.head : "");
      msg.textContent = s.message || "";
      if (s.behind > 0) apply.classList.remove("hidden");
    } catch (e) { msg.textContent = e.message || "Couldn't check for updates."; }
  }
  $("wf-update-check").addEventListener("click", wfCheckUpdate);
  $("wf-update-apply").addEventListener("click", () => $("update-apply").click());

  /* Keep the chip in step however the type changes, and never send while the engine is still loading. */
  const baseSetVideoMode = window.setVideoMode;
  window.setVideoMode = setVideoMode = function () {
    const r = baseSetVideoMode.apply(this, arguments);
    syncTaskChip();
    return r;
  };
  const baseGenerate = generate;
  generate = async function () {
    if (window.__enginePending) { try { await window.__enginePending; } catch (e) {} }
    return baseGenerate.apply(this, arguments);
  };

  syncTaskChip();
})();
/* ===== end video types ===== */

/* ===== Inpaint editor v20261111l =====
   Paint the area to change on Picture 1. The mask is sent with a normal MiniMax H3 edit; the server cuts out
   that area with margin, edits the close-up at higher resolution and blends it back, so nothing else changes. */
(function () {
  const sheet = $("inpaint-sheet"), img = $("ip-img"), cv = $("ip-canvas"), wrap = $("ip-wrap"), cursor = $("ip-cursor");
  const ctx = cv.getContext("2d");
  const COLOR = "rgba(255,64,96,1)";
  let tool = "brush", size = 48, drawing = null;
  // Every stroke / box / erase / invert is one action, stored in 0..1 picture coordinates. The mask is redrawn from
  // the list, so any single area can be removed later, and undo / redo are just moving actions between two lists.
  let actions = [], redo = [], flashId = 0;
  state.inpaint = null;
  /* Auto: strokes that touch are one piece (the server joins them). Separate: you choose the areas with + Area,
     each in its own colour, even when they touch. */
  let split = false, group = 1;
  try { split = localStorage.getItem("ipSplit") === "1"; } catch (e) {}
  const AREA_COL = ["255,64,160", "40,200,255", "255,184,40", "120,230,90", "170,120,255", "255,110,70"];
  const gOf = (a) => a.g || 1;
  const isPaint = (a) => a.t === "brush" || a.t === "box";
  const groups = () => [...new Set(actions.filter(isPaint).map(gOf))].sort((x, y) => x - y);

  function scale() { return cv.width / Math.max(1, cv.getBoundingClientRect().width); }
  function pos(e) {
    const r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left) * scale(), y: (e.clientY - r.top) * scale(), cx: e.clientX - r.left, cy: e.clientY - r.top };
  }
  const nx = (p) => ({ x: p.x / cv.width, y: p.y / cv.height });
  function setTool(t) {
    tool = t;
    document.querySelectorAll("#ip-modes button").forEach((b) => b.classList.toggle("is-on", b.dataset.tool === t));
  }
  function hasPaint() {
    const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
    for (let i = 3; i < d.length; i += 16) if (d[i] > 40) return true;
    return false;
  }

  function drawAction(a, color) {
    const W = cv.width, H = cv.height;
    if (a.t === "invert") {
      const d = ctx.getImageData(0, 0, W, H);
      for (let i = 0; i < d.data.length; i += 4) {
        const on = d.data[i + 3] > 40;
        d.data[i] = 255; d.data[i + 1] = 64; d.data[i + 2] = 96; d.data[i + 3] = on ? 0 : 255;
      }
      ctx.putImageData(d, 0, 0);
      return;
    }
    if (a.t === "clear") { ctx.clearRect(0, 0, W, H); return; }
    ctx.globalCompositeOperation = a.t === "erase" ? "destination-out" : "source-over";
    if (!color && split && isPaint(a)) color = "rgba(" + AREA_COL[(gOf(a) - 1) % AREA_COL.length] + ",1)";
    ctx.fillStyle = ctx.strokeStyle = color || COLOR;
    if (a.t === "box") {
      ctx.fillRect(a.x0 * W, a.y0 * H, (a.x1 - a.x0) * W, (a.y1 - a.y0) * H);
    } else {
      ctx.lineCap = ctx.lineJoin = "round";
      ctx.lineWidth = a.w * W;
      ctx.beginPath();
      a.pts.forEach((q, i) => (i ? ctx.lineTo(q.x * W + 0.01, q.y * H + 0.01) : ctx.moveTo(q.x * W, q.y * H)));
      if (a.pts.length === 1) ctx.lineTo(a.pts[0].x * W + 0.01, a.pts[0].y * H + 0.01);
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }
  function render(extra) {
    ctx.clearRect(0, 0, cv.width, cv.height);
    actions.forEach((a) => drawAction(a));
    if (extra) drawAction(extra);
  }
  const LABEL = { brush: "Brush", erase: "Erase", box: "Box", invert: "Invert", clear: "Clear" };
  function renderAreas() {
    const box = $("ip-areas");
    if (!box) return;
    box.innerHTML = "";
    if (split) {
      groups().forEach((g) => {
        const chip = document.createElement("span");
        chip.className = "ip-area is-group" + (g === group ? " is-active" : "");
        chip.style.setProperty("--ac", AREA_COL[(g - 1) % AREA_COL.length]);
        chip.innerHTML = '<button type="button" class="ip-area-name"><i class="ip-dot"></i></button><button type="button" class="ip-area-x" aria-label="Remove this area">✕</button>';
        chip.querySelector(".ip-area-name").append("Area " + g);
        chip.querySelector(".ip-area-name").addEventListener("click", () => { group = g; flashGroup(g); renderAreas(); syncSplit(); });
        chip.querySelector(".ip-area-x").addEventListener("click", () => {
          actions = actions.filter((a) => !((isPaint(a) || a.t === "erase") && gOf(a) === g));
          redo = []; render(); syncButtons();
        });
        box.appendChild(chip);
      });
      box.classList.toggle("hidden", !groups().length);
      return;
    }
    let paintN = 0;
    actions.forEach((a, i) => {
      const chip = document.createElement("span");
      chip.className = "ip-area" + (a.t === "erase" ? " is-erase" : "") + (a.t === "invert" || a.t === "clear" ? " is-op" : "");
      chip.innerHTML = '<button type="button" class="ip-area-name"></button><button type="button" class="ip-area-x" aria-label="Remove this area">✕</button>';
      // paint areas are numbered 1, 2, 3 ... so "brush 2" / "area 2" in the prompt means this chip
      const paint = a.t === "brush" || a.t === "box";
      chip.querySelector(".ip-area-name").textContent = paint ? (LABEL[a.t] + " " + (++paintN)) : LABEL[a.t];
      chip.querySelector(".ip-area-name").addEventListener("click", () => flash(a));
      chip.querySelector(".ip-area-x").addEventListener("click", () => { actions.splice(actions.indexOf(a), 1); redo = []; render(); syncButtons(); });
      box.appendChild(chip);
    });
    box.classList.toggle("hidden", !actions.length);
  }
  function flashGroup(g) {
    const id = ++flashId;
    ctx.clearRect(0, 0, cv.width, cv.height);
    actions.forEach((b) => drawAction(b, isPaint(b) && gOf(b) === g ? "rgba(255,255,255,1)" : "rgba(255,64,96,.2)"));
    setTimeout(() => { if (id === flashId) render(); }, 650);
  }
  function syncSplit() {
    const sw = $("ip-switch");
    if (!sw) return;
    sw.classList.toggle("is-split", split);
    sw.setAttribute("aria-checked", split ? "true" : "false");
    const nb = $("ip-newarea");
    nb.classList.toggle("hidden", !split);
    nb.style.setProperty("--ac", AREA_COL[(group - 1) % AREA_COL.length]);
    $("ip-split-note").textContent = split
      ? "Painting area " + group + ". Tap + Area for the next thing."
      : "Touching strokes are one piece.";
  }
  function flash(a) {
    // show which part of the picture this area is: everything else dims for a moment
    const id = ++flashId;
    if (a.t === "invert" || a.t === "clear") return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    actions.forEach((b) => drawAction(b, b === a ? "rgba(255,214,90,1)" : "rgba(255,64,96,.25)"));
    setTimeout(() => { if (id === flashId) render(); }, 700);
  }
  function syncButtons() {
    $("ip-undo").disabled = !actions.length;
    if ($("ip-redo")) $("ip-redo").disabled = !redo.length;
    $("ip-clear").disabled = !actions.length;
    renderAreas();
  }
  function commit(a) {
    if (isPaint(a) || a.t === "erase") a.g = split ? group : 1;
    actions.push(a); redo = []; render(); syncButtons();
  }

  function openEditor() {
    const p1 = state.images[0];
    if (!p1) { toast("Add a picture first"); return; }
    hidePops();
    closeAddMenu();
    sheet.classList.remove("hidden");
    img.onload = () => {
      const long = Math.max(img.naturalWidth, img.naturalHeight);
      const k = long > 2048 ? 2048 / long : 1;
      cv.width = Math.round(img.naturalWidth * k);
      cv.height = Math.round(img.naturalHeight * k);
      const keep = state.inpaint && state.inpaint.forName === p1.name && state.inpaint.actions;
      actions = keep ? state.inpaint.actions.map((a) => JSON.parse(JSON.stringify(a))) : [];
      redo = [];
      render();
      syncButtons();
    };
    img.src = p1.url || ("/api/input?name=" + encodeURIComponent(p1.name));
  }
  function closeEditor() { sheet.classList.add("hidden"); cursor.classList.add("hidden"); }
  window.openInpaintEditor = openEditor;
  window.closeInpaintEditor = closeEditor;

  function areaBoxes() {
    /* where each numbered paint area is (0..1), so the server can turn "brush 2" into "the right painted area" */
    const ar = cv.height / Math.max(1, cv.width);
    const out = [];
    if (split) {
      // one box per area you made: everything painted in it, together
      groups().forEach((g) => {
        const b = { x0: 1, y0: 1, x1: 0, y1: 0 };
        actions.filter((a) => isPaint(a) && gOf(a) === g).forEach((a) => {
          let q;
          if (a.t === "box") q = { x0: Math.min(a.x0, a.x1), y0: Math.min(a.y0, a.y1), x1: Math.max(a.x0, a.x1), y1: Math.max(a.y0, a.y1) };
          else {
            const rx = a.w / 2, ry = (a.w / 2) / ar;
            q = { x0: 1, y0: 1, x1: 0, y1: 0 };
            a.pts.forEach((p) => { q.x0 = Math.min(q.x0, p.x - rx); q.y0 = Math.min(q.y0, p.y - ry); q.x1 = Math.max(q.x1, p.x + rx); q.y1 = Math.max(q.y1, p.y + ry); });
          }
          b.x0 = Math.min(b.x0, q.x0); b.y0 = Math.min(b.y0, q.y0); b.x1 = Math.max(b.x1, q.x1); b.y1 = Math.max(b.y1, q.y1);
        });
        ["x0", "y0", "x1", "y1"].forEach((k) => { b[k] = Math.round(Math.max(0, Math.min(1, b[k])) * 1e4) / 1e4; });
        b.n = g;
        out.push(b);
      });
      return out;
    }
    actions.filter((a) => a.t === "brush" || a.t === "box").forEach((a, i) => {
      let b;
      if (a.t === "box") b = { x0: Math.min(a.x0, a.x1), y0: Math.min(a.y0, a.y1), x1: Math.max(a.x0, a.x1), y1: Math.max(a.y0, a.y1) };
      else {
        const rx = a.w / 2, ry = (a.w / 2) / ar;
        b = { x0: 1, y0: 1, x1: 0, y1: 0 };
        a.pts.forEach((q) => { b.x0 = Math.min(b.x0, q.x - rx); b.y0 = Math.min(b.y0, q.y - ry); b.x1 = Math.max(b.x1, q.x + rx); b.y1 = Math.max(b.y1, q.y + ry); });
      }
      ["x0", "y0", "x1", "y1"].forEach((k) => { b[k] = Math.round(Math.max(0, Math.min(1, b[k])) * 1e4) / 1e4; });
      b.n = i + 1;
      out.push(b);
    });
    return out;
  }
  function exportMask() {
    /* white = change, black = keep, at the picture's own size */
    const out = document.createElement("canvas");
    out.width = img.naturalWidth;
    out.height = img.naturalHeight;
    const o = out.getContext("2d");
    const src = ctx.getImageData(0, 0, cv.width, cv.height);
    const tmp = document.createElement("canvas");
    tmp.width = cv.width; tmp.height = cv.height;
    const t = tmp.getContext("2d");
    const dst = t.createImageData(cv.width, cv.height);
    for (let i = 0; i < src.data.length; i += 4) {
      const v = src.data[i + 3] > 40 ? 255 : 0;
      dst.data[i] = dst.data[i + 1] = dst.data[i + 2] = v;
      dst.data[i + 3] = 255;
    }
    t.putImageData(dst, 0, 0);
    o.imageSmoothingEnabled = true;
    o.drawImage(tmp, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  }

  function syncChip() {
    const on = !!(state.inpaint && state.images[0] && state.inpaint.forName === state.images[0].name);
    if (!on) state.inpaint = null;
    $("inpaint-chip").classList.toggle("hidden", !on);
    $("inpaint-pill").classList.toggle("is-on", on);
    const n = on ? (state.inpaint.explicit ? (state.inpaint.areas || []).length
      : (state.inpaint.actions || []).filter((a) => a.t === "brush" || a.t === "box").length) : 0;
    $("inpaint-pill-label").textContent = on ? (n > 1 ? n + " areas" : "Area set") : "Paint";
    if (on && state.media !== "video") $("prompt").placeholder = n > 1 ? (state.inpaint.explicit ? "Say what goes where: area 1 …, area 2 …" : "Say what goes where: brush 1 …, brush 2 …") : "Describe what goes in the painted area…";
    syncDockHeight();
  }
  window.syncInpaintChip = syncChip;

  /* drawing: the live action is drawn on top of the list until the finger / mouse lifts */
  wrap.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { wrap.setPointerCapture(e.pointerId); } catch (err) {}
    const p = pos(e);
    if (tool === "box") drawing = { t: "box", s: nx(p), x0: 0, y0: 0, x1: 0, y1: 0 };
    else drawing = { t: tool === "erase" ? "erase" : "brush", w: (size * scale()) / cv.width, pts: [nx(p)] };
    if (drawing.t !== "box") render(drawing);
  });
  /* PC box tool: a crosshair with guide lines across the picture, and the box size while dragging
     (touch screens don't get it: your finger is the pointer) */
  const cross = document.createElement("div");
  cross.className = "ip-cross hidden";
  cross.innerHTML = '<i class="ip-gh"></i><i class="ip-gv"></i><b class="ip-plus"></b><span class="ip-size hidden"></span>';
  wrap.appendChild(cross);
  function hideCross() { cross.classList.add("hidden"); }
  function showCross(e, p) {
    if (tool !== "box" || e.pointerType === "touch") { hideCross(); return; }
    cross.classList.remove("hidden");
    cross.style.setProperty("--x", p.cx + "px");
    cross.style.setProperty("--y", p.cy + "px");
    const lab = cross.querySelector(".ip-size");
    if (drawing && drawing.t === "box") {
      const W = img.naturalWidth || cv.width, H = img.naturalHeight || cv.height;
      lab.textContent = Math.round((drawing.x1 - drawing.x0) * W) + " × " + Math.round((drawing.y1 - drawing.y0) * H);
      lab.classList.remove("hidden");
    } else lab.classList.add("hidden");
  }
  wrap.addEventListener("pointermove", (e) => {
    const p = pos(e);
    cursor.style.width = cursor.style.height = size + "px";
    cursor.style.left = p.cx + "px";
    cursor.style.top = p.cy + "px";
    cursor.classList.toggle("hidden", tool === "box" || e.pointerType === "touch");
    showCross(e, p);
    if (!drawing) return;
    e.preventDefault();
    const q = nx(p);
    if (drawing.t === "box") {
      drawing.x0 = Math.min(drawing.s.x, q.x); drawing.y0 = Math.min(drawing.s.y, q.y);
      drawing.x1 = Math.max(drawing.s.x, q.x); drawing.y1 = Math.max(drawing.s.y, q.y);
    } else {
      drawing.pts.push(q);
    }
    render(drawing);
  });
  const end = () => {
    if (!drawing) return;
    const a = drawing; drawing = null;
    if (a.t === "box") { delete a.s; if ((a.x1 - a.x0) * cv.width < 3 || (a.y1 - a.y0) * cv.height < 3) { render(); return; } }
    commit(a);
  };
  wrap.addEventListener("pointerup", end);
  wrap.addEventListener("pointercancel", end);
  wrap.addEventListener("pointerleave", () => { cursor.classList.add("hidden"); hideCross(); });
  ["touchstart", "touchmove"].forEach((ev) => wrap.addEventListener(ev, (e) => e.preventDefault(), { passive: false }));

  /* tools */
  document.querySelectorAll("#ip-modes button").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
  $("ip-size").addEventListener("input", (e) => { size = Number(e.target.value) || 48; });
  $("ip-undo").addEventListener("click", () => { const a = actions.pop(); if (a) { redo.push(a); render(); syncButtons(); } });
  if ($("ip-redo")) $("ip-redo").addEventListener("click", () => { const a = redo.pop(); if (a) { actions.push(a); render(); syncButtons(); } });
  $("ip-clear").addEventListener("click", () => { if (actions.length) { commit({ t: "clear" }); group = 1; syncSplit(); } });
  $("ip-switch").addEventListener("click", () => {
    split = !split;
    try { localStorage.setItem("ipSplit", split ? "1" : "0"); } catch (e) {}
    const gs = groups();
    group = split && gs.length ? gs[gs.length - 1] : 1;
    render(); syncButtons(); syncSplit();
  });
  $("ip-newarea").addEventListener("click", () => {
    const gs = groups();
    if (gs.includes(group)) group = Math.max(...gs) + 1;      // the current area has paint: start the next one
    syncSplit(); renderAreas();
    toast("Area " + group + ": paint the next thing");
  });
  syncSplit();
  $("ip-invert").addEventListener("click", () => commit({ t: "invert" }));
  $("ip-close").addEventListener("click", closeEditor);
  $("ip-done").addEventListener("click", () => {
    if (!hasPaint()) { state.inpaint = null; closeEditor(); syncChip(); toast("No area painted"); return; }
    state.inpaint = { forName: state.images[0].name, mask: exportMask(), actions: actions.map((a) => JSON.parse(JSON.stringify(a))), areas: areaBoxes(), explicit: split };
    closeEditor();
    if (state.mode !== "edit") setMode("edit");
    syncChip();
    toast("Area set. Describe what should go there.");
    try { $("prompt").focus(); } catch (e) {}
  });
  document.addEventListener("keydown", (e) => {
    if (sheet.classList.contains("hidden")) return;
    if (e.key === "Escape") { closeEditor(); e.stopImmediatePropagation(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); $(e.shiftKey ? "ip-redo" : "ip-undo").click(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); $("ip-redo").click(); }
    if (e.key === "[") { size = Math.max(6, size - 6); $("ip-size").value = size; }
    if (e.key === "]") { size = Math.min(160, size + 6); $("ip-size").value = size; }
  }, true);

  /* entry points */
  $("inpaint-pill").addEventListener("click", openEditor);
  $("inpaint-chip-edit").addEventListener("click", openEditor);
  $("inpaint-chip-clear").addEventListener("click", () => { state.inpaint = null; syncChip(); syncPlaceholder(); });
  async function inpaintItem(it) {
    if (!it || !it.id) return;
    if (state.media === "video") setMedia("image");
    await reuseOutput(it, "picture1");
    state.inpaint = null;
    syncChip();
    openEditor();
  }
  $("result-inpaint").addEventListener("click", () => inpaintItem(state.reuseItem));
  $("viewer-inpaint").addEventListener("click", () => inpaintItem(state.library[state.viewerIndex]));

  /* Picture 1 changed or removed: the old mask no longer applies. */
  const baseRender = renderSlots;
  renderSlots = function () {
    const r = baseRender.apply(this, arguments);
    syncChip();
    return r;
  };
  /* Built-in MiniMax H3 image only: uploaded engines get their own controls. */
  const baseApply = window.onEngineChanged;
  window.onEngineChanged = function (active) {
    if (typeof baseApply === "function") baseApply.apply(this, arguments);
    syncChip();
  };
  if (window.__swipeClose) { /* full-screen editor: no swipe-to-close while painting */ }
})();
/* ===== end inpaint ===== */

/* ===== + panel gets the room v20261111m =====
   A long prompt used to push the + panel above the top of the screen on phones. Opening it now
   collapses the prompt to one line, hides the pill row and closes the keyboard; closing restores. */
(function () {
  const baseOpen = openAddMenu, baseClose = closeAddMenu;
  openAddMenu = function () {
    const r = baseOpen.apply(this, arguments);
    const open = !$("add-menu").classList.contains("hidden");
    document.body.classList.toggle("add-open", open);
    if (open) {
      try { $("prompt").blur(); } catch (e) {}
      $("prompt").scrollTop = 0;
    }
    syncDockHeight();
    return r;
  };
  closeAddMenu = function () {
    const r = baseClose.apply(this, arguments);
    if (document.body.classList.contains("add-open")) {
      document.body.classList.remove("add-open");
      autoGrow();
    }
    return r;
  };
  /* Tapping the collapsed prompt brings the full text back. */
  $("prompt").addEventListener("focus", () => {
    if (document.body.classList.contains("add-open")) closeAddMenu();
  });
})();
/* ===== end + panel room ===== */

/* ===== LoRAs, resolution advice, error fixes v20261111n ===== */
(function () {
  let options = null;
  const key = () => "cs.loras." + (state.engine || "builtin");
  function load() { try { return JSON.parse(LS.get(key(), "[]")) || []; } catch (e) { return []; } }
  function save(list) {
    LS.set(key(), JSON.stringify(list)); syncPill();
    if (window.__applyModelRecipe) window.__applyModelRecipe();   // e.g. the 8-step DMD2 LoRA changes the recipe
  }
  function supported() {
    const a = window.__wfActive && window.__wfActive();
    return !(a && a.card && a.card.supports_loras === false);
  }
  window.__currentLoras = () => {
    if (!supported()) return [];
    const manual = load();
    const auto = (window.__autoLoras ? window.__autoLoras() : []).filter((a) => !manual.some((m) => m.name === a.name));
    return manual.concat(auto);
  };

  function short(n) { return String(n || "").split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt)$/i, ""); }
  function syncPill() {
    const n = load().length;
    const a = window.__autoLoras ? window.__autoLoras().filter((x) => !load().some((m) => m.name === x.name)).length : 0;
    document.body.classList.toggle("no-loras", !supported());
    $("lora-pill-label").textContent = (n ? (n === 1 && !a ? short(load()[0].name).slice(0, 14) : n + " on") : (a ? "" : "None"))
      + (a ? (n ? " + " : "") + a + " auto" : "");
    $("lora-pill").classList.toggle("is-on", n + a > 0);
  }
  window.__syncLoraPill = syncPill;
  window.__manualLoras = () => load().map((l) => l.name);

  async function ensureOptions() {
    if (options) return options;
    try { options = (await api("/api/loras")).items || []; } catch (e) { options = []; }
    return options;
  }

  // LoRAs made for the active model (by folder / name, from the card's lora_hint) are listed first
  function fillOptions() {
    const a = window.__wfActive && window.__wfActive();
    const hint = (a && a.card.lora_hint) || (a ? [] : ["minimax", "h3"]);
    const mine = (o) => hint.some((h) => String(o).toLowerCase().indexOf(h) >= 0);
    const list = (options || []).slice().sort((x, y) => (mine(y) - mine(x)));
    if ($("lora-options")) $("lora-options").innerHTML = list.map((o) => '<option value="' + String(o).replace(/"/g, "&quot;") + '">').join("");
  }

  function render() {
    fillOptions();
    if (window.__renderAutoLoras) window.__renderAutoLoras();
    const box = $("lora-list");
    box.innerHTML = "";
    const list = load();
    if (!list.length) box.innerHTML = '<p class="pop-note">No LoRAs added. Search below to add one.</p>';
    list.forEach((l, i) => {
      const row = document.createElement("div");
      row.className = "lora-row";
      row.innerHTML = '<span class="lora-name"></span><button type="button" class="icon-btn sm" aria-label="Remove">✕</button>' +
        '<label class="lora-strength">Strength <input type="range" min="-1" max="2" step="0.05"><span class="lora-val"></span></label>';
      row.querySelector(".lora-name").textContent = short(l.name);
      row.querySelector(".lora-name").title = l.name;
      const r = row.querySelector("input"), v = row.querySelector(".lora-val");
      r.value = l.strength; v.textContent = Number(l.strength).toFixed(2);
      r.addEventListener("input", () => { v.textContent = Number(r.value).toFixed(2); });
      r.addEventListener("change", () => { const all = load(); all[i].strength = Number(r.value); save(all); });
      row.querySelector("button").addEventListener("click", () => { const all = load(); all.splice(i, 1); save(all); render(); });
      box.appendChild(row);
    });
    const a = window.__wfActive && window.__wfActive();
    $("lora-sub").textContent = "For " + (a ? a.card.name : "MiniMax H3 (built-in)") + ". Saved per engine.";
    $("lora-note").textContent = a && a.card.aio_mode ? "MiniMax H3 AIO has 3 spare LoRA slots; the realism LoRA stays in slot 1." : "";
  }

  async function addFromSearch() {
    const want = $("lora-search").value.trim();
    if (!want) return;
    const opts = await ensureOptions();
    const hit = opts.find((o) => o === want) || opts.find((o) => o.toLowerCase().includes(want.toLowerCase()));
    if (!hit) { toast("No LoRA matches “" + want + "”"); return; }
    const list = load();
    if (list.some((l) => l.name === hit)) { toast("Already added"); return; }
    if (list.length >= 6) { toast("6 LoRAs at most"); return; }
    list.push({ name: hit, strength: 1.0 });
    save(list);
    $("lora-search").value = "";
    render();
  }

  $("lora-pill").addEventListener("click", async (e) => {
    await ensureOptions();
    render();
    showPop("lora-pop", e.currentTarget);
  });
  $("lora-add-btn").addEventListener("click", addFromSearch);
  $("lora-search").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addFromSearch(); } });
  window.__addLora = (name, strength) => {
    const list = load();
    if (list.some((l) => l.name === name)) { toast("Already added"); return; }
    if (list.length >= 6) { toast("6 LoRAs at most"); return; }
    list.push({ name, strength: typeof strength === "number" ? strength : 1.0 });
    save(list);
    render();
  };
  $("lora-search").addEventListener("change", () => { if (options && options.indexOf($("lora-search").value) >= 0) addFromSearch(); });
  if (window.__swipeClose) window.__swipeClose($("lora-pop"), () => hidePops(), isPhoneUi);

  /* Resolution advice from this PC's specs and the engine's tested limits. */
  function resNote(active) {
    const c = active && active.card;
    const hw = window.__hw;
    const pc = hw && hw.vram_gb ? "Your PC (" + (hw.gpu || "GPU") + ", " + hw.vram_gb + " GB): " : "On this PC: ";
    if (c && c.limits && Object.keys(c.limits).length) {
      $("vres-note").textContent = pc + Object.keys(c.limits).map((r) => r + " up to " + c.limits[r] + " s").join(" · ") + ". Lower resolution is faster.";
    } else if (hw && hw.vram_gb && hw.vram_gb < 12) {
      $("vres-note").textContent = pc + "480p is the safe choice; try 720p for short clips.";
    } else {
      $("vres-note").textContent = "Higher resolution takes longer and needs more memory.";
    }
  }

  const baseEngine = window.onEngineChanged;
  window.onEngineChanged = function (active) {
    if (typeof baseEngine === "function") baseEngine.apply(this, arguments);
    syncPill();
    resNote(active);
  };

  /* Errors that come with fixes (missing nodes, missing model files...). */
  const baseAction = window.showActionError;
  window.showActionError = function (data) {
    if (data.code !== "fix") return baseAction(data);
    showError(data.error);
    const ul = $("dock-fixes");
    ul.innerHTML = "";
    (data.fixes || []).forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f.text;
      if (f.pack) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip-btn primary"; b.textContent = "Install";
        b.addEventListener("click", async () => {
          if (!window.confirm("Install this node pack into ComfyUI? Node packs run code on this PC.")) return;
          b.disabled = true; b.textContent = "Installing…";
          try {
            const r = await api("/api/nodes/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ packs: [f.pack] }) });
            b.textContent = (r.queued || []).length ? "Queued. Restart ComfyUI after" : "Couldn't install";
          } catch (e) { b.textContent = "Couldn't install"; }
        });
        li.appendChild(b);
      } else if (f.restart) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip-btn"; b.textContent = "Restart ComfyUI";
        b.addEventListener("click", async () => {
          if (!window.confirm("Restart ComfyUI now? Anything it's generating will stop.")) return;
          try { await api("/api/comfy/restart", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) {}
          toast("ComfyUI is restarting…", 4000);
        });
        li.appendChild(b);
      } else if (f.settings) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip-btn"; b.textContent = "Open Settings";
        b.addEventListener("click", () => { hideAlert(); showPop("settings-pop", $("settings-gear")); });
        li.appendChild(b);
      }
      ul.appendChild(li);
    });
    ul.classList.toggle("hidden", !ul.children.length);
    syncDockHeight();
  };
  const baseHide = hideAlert;
  hideAlert = function () { $("dock-fixes").classList.add("hidden"); return baseHide.apply(this, arguments); };

  syncPill();
})();
/* ===== end LoRAs ===== */

/* ===== Pills follow the active engine's roles v20261111o =====
   Every pill has a role. The engine in use declares which roles it supports (built-in MiniMax H3 image: all of
   its tools; an uploaded or built-in workflow: only what its card maps). Any other pill is removed, so nothing
   is left over from a different kind of workflow. */
(function () {
  const PILLS = {
    "res-picker": "size_image", "vsize-pill": "size_video", "vlen-pill": "length", "seed-pill": "seed",
    "lora-pill": "loras", "camera-open": "camera", "quality-pill": "enhance", "kind-pill": "edit_kind",
    "roles-pill": "roles", "mark-pill": "mark", "inpaint-pill": "inpaint", "help-pill": "help",
  };
  function roles() {
    const a = window.__wfActive && window.__wfActive();
    const video = state.media === "video";
    if (!a) {
      if (video || state.engine === "none") return new Set(["help"]);
      /* built-in MiniMax H3 image */
      const r = ["size_image", "seed", "loras", "camera", "help", "enhance"];   /* Enhance: new pictures and edits */
      if (state.mode === "edit") r.push("edit_kind", "roles", "inpaint", "mark");
      return new Set(r);
    }
    const c = a.card || {}, s = c.slots || {};
    const r = new Set(["help"]);
    const hasSize = !!((s.width || []).length || s.res_label || (s.megapixels || []).length || (s.size_combo || []).length || (s.aspect_combo || []).length || c.size_rule);
    if (hasSize) r.add(c.media === "video" ? "size_video" : "size_image");
    if (c.media === "video" && ((s.length || []).length || (s.seconds || []).length)) r.add("length");
    if ((s.seed || []).length) r.add("seed");
    if (c.supports_loras !== false) r.add("loras");
    if ((s.prompt || []).length) r.add("camera");
    // Mark (outline areas to refer to) goes with Inpaint: the same area tools feed both
    if (c.media === "image" && (c.picture_min || 0) >= 1 && c.inpaint !== false) { r.add("inpaint"); r.add("mark"); }
    if ((c.media === "video" || c.media === "image") && c.enhance !== false && (s.prompt || []).length) r.add("enhance");
    if (c.media === "image" && (c.picture_max || 0) >= 1 && c.assist !== false) {
      r.add("edit_kind");
      if ((c.picture_max || 0) >= 2) r.add("roles");
    }
    return r;
  }
  function syncRoles() {
    const r = roles();
    Object.keys(PILLS).forEach((id) => {
      const el = $(id);
      if (el) el.classList.toggle("role-off", !r.has(PILLS[id]));
    });
    document.body.classList.toggle("wf-can-inpaint", !!(window.__wfActive && window.__wfActive()) && r.has("inpaint"));
    // workflow engines that have these tools show them (the wf-active rule hides built-in-only pills otherwise)
    Object.keys(PILLS).forEach((id) => { const el = $(id); if (el) el.classList.toggle("wf-on", r.has(PILLS[id])); });
    if (window.syncHudFades) window.syncHudFades();
  }
  window.syncPillRoles = syncRoles;
  const baseEngine = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof baseEngine === "function") baseEngine.apply(this, arguments);
    syncRoles();
  };
  const baseSetMode = setMode;
  setMode = function () { const x = baseSetMode.apply(this, arguments); syncRoles(); return x; };
  syncRoles();
})();
/* ===== end pill roles ===== */

/* ===== Spec-aware length, upscale, image MP cap v20261111p ===== */
(function () {
  const PRESETS = [5, 10];
  function card() { const a = window.__wfActive && window.__wfActive(); return a && a.card; }
  function maxFor(c, res) {
    const lim = (c && c.limits) || {};
    return lim[res] || Math.max(0, ...Object.values(lim)) || 10;
  }
  function note() {
    const c = card();
    let p = $("vlen-note");
    if (!p) { p = document.createElement("p"); p.id = "vlen-note"; p.className = "vlen-note"; $("vlen-pop").appendChild(p); }
    if (!c || c.media !== "video") { p.textContent = ""; return; }
    const lim = c.limits || {};
    const basis = c.limits_basis === "tested" ? "tested on this PC" : "estimated for this PC";
    const cap = lim[state.video.res];
    let t = Object.keys(lim).map((r) => r + ": up to " + lim[r] + " s").join(" · ") + " (" + basis + ").";
    if (c.clip_seconds) t += " Made of " + c.clip_seconds + " s clips so each one fits in memory.";
    if (cap && state.video.len > cap) t += " At " + state.video.res + " this clip will be " + cap + " s.";
    p.textContent = t;
  }
  function setLen(v) {
    const c = card();
    const top = Math.max(...Object.values((c && c.limits) || { x: 10 }));
    v = Math.max(1, Math.min(Math.round(Number(v) || 5), top || 120));
    state.video.len = v;
    LS.set("cs.v.len", String(v));
    document.querySelectorAll("#vlen-seg button").forEach((b) => b.classList.toggle("is-on", Number(b.dataset.v) === v));
    $("vlen-custom").value = PRESETS.indexOf(v) < 0 ? v : "";
    if (window.syncVideoPills) window.syncVideoPills();
    note();
  }
  function buildLength() {
    const seg = $("vlen-seg");
    seg.innerHTML = "";
    PRESETS.forEach((v) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.v = v;
      b.textContent = v + "s";
      b.addEventListener("click", () => { setLen(v); hidePops(); });
      seg.appendChild(b);
    });
    const c = card();
    const top = Math.max(...Object.values((c && c.limits) || { x: 10 }));
    $("vlen-custom").max = top || 120;
    $("vlen-custom").placeholder = "up to " + (top || 120);
    setLen(state.video.len || 5);
  }
  $("vlen-custom-set").addEventListener("click", () => { setLen($("vlen-custom").value); hidePops(); });
  $("vlen-custom").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); setLen($("vlen-custom").value); hidePops(); } });

  function resNote() {
    const c = card();
    const hw = window.__hw || {};
    const el = $("vres-note");
    if (!el || !c || c.media !== "video") return;
    const basis = c.limits_basis === "tested" ? "tested" : "estimated";
    el.textContent = "Your PC (" + (hw.gpu || "GPU") + ", " + (hw.vram_gb || "?") + " GB): " +
      Object.keys(c.limits || {}).map((r) => r + " up to " + c.limits[r] + " s").join(" · ") + " (" + basis + "). Bigger cards unlock bigger sizes.";
  }

  const baseEngine = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof baseEngine === "function") baseEngine.apply(this, arguments);
    buildLength();
    resNote();
  };
  /* the resolution buttons are rebuilt by the engine; keep the note in step when they change */
  $("vres-seg").addEventListener("click", () => setTimeout(() => { note(); resNote(); }, 0));

  /* ---------- upscale a finished video ---------- */
  let upItem = null;
  async function openUpscale(it, anchor) {
    if (!it || it.media !== "video") return;
    upItem = it;
    const box = $("upscale-list");
    box.innerHTML = '<p class="pop-note">Checking what this PC can run…</p>';
    showPop("upscale-pop", anchor);
    let d;
    try { d = await api("/api/upscale/options?id=" + encodeURIComponent(it.id)); } catch (e) { box.innerHTML = '<p class="pop-note">Couldn\'t check upscalers.</p>'; return; }
    box.innerHTML = "";
    $("upscale-sub").textContent = "Only what your PC (" + ((d.hardware || {}).gpu || "GPU") + ", " + ((d.hardware || {}).vram_gb || "?") + " GB) can run is shown.";
    if (!(d.methods || []).length) { box.innerHTML = '<p class="pop-note">No upscaler on this PC can go higher than this video.</p>'; return; }
    d.methods.forEach((m) => {
      const card = document.createElement("div");
      card.className = "up-method";
      card.innerHTML = '<span class="up-speed ' + m.speed + '">' + m.speed + '</span><strong></strong><span></span><div class="up-targets"></div>';
      card.querySelector("strong").textContent = m.label;
      card.querySelectorAll("span")[1].textContent = m.note || "";
      m.targets.forEach((t) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip-btn primary";
        b.textContent = "To " + t;
        b.addEventListener("click", async () => {
          hidePops();
          try {
            const r = await api("/api/upscale", { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: upItem.id, method: m.id, target: t }) });
            closeViewer();
            setView("create");
            enqueueJob(r.job);
            toast("Upscaling to " + t + "…");
          } catch (e) { showError(e.message); }
        });
        card.querySelector(".up-targets").appendChild(b);
      });
      box.appendChild(card);
    });
    placePop($("upscale-pop"), anchor);
  }
  $("result-upscale").addEventListener("click", (e) => openUpscale(state.reuseItem && Object.assign({ media: "video" }, state.reuseItem), e.currentTarget));
  $("viewer-upscale").addEventListener("click", (e) => openUpscale(state.library[state.viewerIndex], e.currentTarget));
  const baseShow = showResult;
  showResult = function (job) {
    const r = baseShow.apply(this, arguments);
    $("result-upscale").classList.toggle("hidden", !(job && job.media === "video"));
    if (state.reuseItem && job) state.reuseItem.media = job.media;
    return r;
  };
  const baseOpenV = openViewer;
  openViewer = function () {
    const r = baseOpenV.apply(this, arguments);
    const it = state.library[state.viewerIndex];
    $("viewer-upscale").classList.toggle("hidden", !(it && it.media === "video"));
    return r;
  };

  /* ---------- image megapixels capped to this PC ---------- */
  (async function capImageMp() {
    let hw;
    try { hw = await api("/api/hardware"); } catch (e) { return; }
    window.__hw = Object.assign(window.__hw || {}, hw);
    // the per-engine ceiling (megapixel ceiling block) also knows this PC's limit: let it redraw the dial
    if (typeof window.__applyMpCap === "function") window.__applyMpCap();
  })();
})();
/* ===== end spec-aware ===== */

/* ===== Reuse all settings -> the engine it was made with v20261111q =====
   Reuse prompt / Reuse input images keep you on the engine you're using now.
   Reuse all settings switches to the exact engine (built-in MiniMax H3 image, or the workflow) that made it. */
(function () {
  const TASK_MEDIA = { t2i: "image", edit: "image", t2v: "video", i2v: "video", r2v: "video", long: "video" };
  function engineOf(it) {
    if (!it) return null;
    if (it.workflow_id) {
      const w = (window.__wfList ? window.__wfList() : []).find((x) => x.id === it.workflow_id);
      return { id: it.workflow_id, task: (w && w.task) || it.task || (it.mode === "edit" ? "edit" : "t2i"), name: (w && w.name) || it.workflow_name || "its workflow" };
    }
    return { id: "builtin", task: it.mode === "edit" ? "edit" : "t2i", name: "MiniMax H3 (built-in)" };
  }
  const baseReuseAll = reuseAll;
  async function settle() {
    for (let i = 0; i < 3 && window.__enginePending; i++) { try { await window.__enginePending; } catch (e) {} }
  }
  function goEngine(e) {
    LS.set("cs.wf." + e.task, e.id);
    if (TASK_MEDIA[e.task] === "video") { if (state.media !== "video") setMedia("video"); setVideoMode(e.task); }
    else { if (state.media !== "image") setMedia("image"); setMode(e.task === "edit" ? "edit" : "t2i"); }
    if (typeof window.applyEngine === "function") window.applyEngine();
  }
  // Switch engine first, let it load its defaults, then lay the saved settings on top, then make sure
  // the engine is still the right one (the base function flips image modes) and re-apply the numbers.
  reuseAll = async function (it) {
    const e = engineOf(it);
    if (!e) return baseReuseAll.apply(this, arguments);
    goEngine(e);
    await settle();
    const r = await baseReuseAll.apply(this, arguments);
    if (TASK_MEDIA[e.task] === "video" || state.engine !== e.id) { goEngine(e); await settle(); }
    if (it.prompt_user || it.prompt) $("prompt").value = promptText(it, "original");
    if (it.steps && $("steps")) $("steps").value = it.steps;
    if (it.cfg != null && $("cfg")) $("cfg").value = it.cfg;
    if (it.seed != null && $("seed")) { $("random-seed").checked = false; $("seed").disabled = false; $("seed").value = it.seed; }
    if (state.engine !== e.id) toast("The engine that made this (" + e.name + ") isn't installed any more. Settings loaded on the current one.");
    else toast("Settings loaded on " + e.name);
    return r;
  };
  const baseRender = renderReuse;
  renderReuse = function (boxId, actionsId, it) {
    const r = baseRender.apply(this, arguments);
    const e = engineOf(it);
    const box = $(actionsId);
    if (box && e) {
      [...box.querySelectorAll("button")].forEach((b) => {
        if (/Reuse all settings/.test(b.textContent) && !b.querySelector(".reuse-sub")) {
          const sub = document.createElement("small");
          sub.className = "reuse-sub";
          sub.textContent = "Switches to " + e.name;
          b.querySelectorAll("span")[1].appendChild(sub);
        }
        if (/Reuse (original |enhanced )?prompt|Reuse input images/.test(b.textContent) && !b.querySelector(".reuse-sub")) {
          const sub = document.createElement("small");
          sub.className = "reuse-sub";
          sub.textContent = "Keeps your current engine";
          b.querySelectorAll("span")[1].appendChild(sub);
        }
      });
    }
    return r;
  };
})();
/* ===== end reuse engine ===== */

/* ===== Size from the prompt v20261111t =====
   Type the size into the prompt and the Size pill follows it (any engine, built-in or workflow):
     "16:9", "9:16 aspect ratio", "square format", "vertical format", "widescreen"  -> aspect
     "2 MP", "4mp", "16 megapixels"                                                  -> megapixels
     "fixed resolution" / "original size" / "keep the input resolution"              -> Picture 1's own size
     "fixed resolution 2 MP"                                                         -> Picture 1's shape at 2 MP
   The phrases are taken out of the prompt before it is sent, so the model never sees them.
   Videos take the aspect only (their size comes from the resolution pill). */
(function () {
  const WORDS = { square: "1:1", widescreen: "16:9", horizontal: "16:9", vertical: "9:16", portrait: "3:4",
                  landscape: "4:3", panoramic: "21:9", ultrawide: "21:9" };
  const key = (w) => String(w || "").toLowerCase().replace(/[\s-]/g, "");
  const LEAD = "(?:(?:in|at|with|use|using|make it|keep|set to|to)\\s+)?(?:(?:a|an|the)\\s+)?";
  const RX = {
    fixed: new RegExp("\\b" + LEAD + "(?:fixed|original|native|same|source|input|picture 1(?:'s)?)\\s+(?:image\\s+|input\\s+)?(?:resolution|res|size|dimensions)\\b", "i"),
    mp: new RegExp("\\b" + LEAD + "(\\d{1,2}(?:\\.\\d+)?)\\s*(?:mp|mpx|megapixels?|mega[\\s-]?pixels?)\\b", "i"),
    ratio: new RegExp("\\b" + LEAD + "(?:aspect(?:\\s+ratio)?(?:\\s+of)?\\s*:?\\s*)?(\\d{1,2})\\s*[:\\u2236]\\s*(\\d{1,2})(?:\\s+(?:aspect(?:\\s+ratio)?|ratio|format|frame))?\\b", "i"),
    ratioX: new RegExp("\\b" + LEAD + "(?:aspect(?:\\s+ratio)?(?:\\s+of)?\\s*(\\d{1,2})\\s*[x\\u00d7/]\\s*(\\d{1,2})|(\\d{1,2})\\s*[x\\u00d7/]\\s*(\\d{1,2})\\s+(?:aspect(?:\\s+ratio)?|ratio))\\b", "i"),
    word: new RegExp("\\b" + LEAD + "(square|wide[\\s-]?screen|horizontal|vertical|portrait|landscape|panoramic|ultra[\\s-]?wide)\\s+(?:format|orientation|aspect(?:\\s+ratio)?|ratio|frame|crop|shape)\\b", "i"),
    bare: /\b(widescreen|ultra[\s-]?wide)\b/i,
  };

  function nearestAspect(w, h, list) {
    let best = null, d = 1e9;
    list.forEach((id) => {
      const [a, b] = id.split(":").map(Number);
      const e = Math.abs(Math.log((w / h) / (a / b)));
      if (e < d) { d = e; best = id; }
    });
    return d < 0.08 ? best : null;   // within ~8%; times like 4:30 match nothing
  }

  function pool() {
    if (state.media !== "video") return ASPECTS.map((a) => a.id);
    if (window.__videoAspects && window.__videoAspects.length) return window.__videoAspects;
    return ["16:9", "9:16", "1:1", "4:3", "3:4"];
  }

  function parse(text) {
    const out = { cut: [] };
    let t = String(text || "");
    const take = (re, fn) => {
      const m = t.match(re);
      if (m && fn(m) !== false) { out.cut.push(m[0]); t = t.replace(m[0], " "); }
    };
    const ids = pool();
    take(RX.fixed, () => { out.fixed = true; });
    take(RX.mp, (m) => { const n = Number(m[1]); if (!(n > 0)) return false; out.mp = n; });
    take(RX.ratio, (m) => { const a = nearestAspect(Number(m[1]), Number(m[2]), ids); if (!a) return false; out.aspect = a; });
    if (!out.aspect) take(RX.ratioX, (m) => {
      const w = Number(m[1] || m[3]), h = Number(m[2] || m[4]);
      const a = w && h && nearestAspect(w, h, ids);
      if (!a) return false; out.aspect = a;
    });
    if (!out.aspect) take(RX.word, (m) => { const a = WORDS[key(m[1])]; if (!a || ids.indexOf(a) < 0) return false; out.aspect = a; });
    if (!out.aspect) take(RX.bare, (m) => { const a = WORDS[key(m[1])]; if (!a || ids.indexOf(a) < 0) return false; out.aspect = a; });
    return out;
  }

  function tidy(t) {
    return t.replace(/\(\s*\)/g, " ").replace(/\s{2,}/g, " ").replace(/\s+([,.;:!?])/g, "$1")
      .replace(/([,;])\s*(?=[,;.])/g, "").replace(/^[\s,;.]+/, "").replace(/[\s,;]+$/, "").trim()
      .replace(/^(?:and|then|also|but|so)\s+/i, "").replace(/\s+(?:and|then|with|in|at)$/i, "").trim();
  }

  function clean(text) {
    const p = parse(text);
    if (!p.cut.length) return text;
    let t = String(text);
    p.cut.forEach((c) => { t = t.replace(c, " "); });
    return tidy(t) || text;
  }
  window.__sizeClean = clean;
  window.__sizeParse = parse;

  function engineCap() {
    const a = window.__wfActive && window.__wfActive();
    const hw = (window.__hw && window.__hw.image_mp_max) || 16;
    return a ? Math.min(a.card.mp_max || 4, hw) : Math.min(20, hw);
  }

  let lastKey = "";
  function apply(fromSend) {
    const p = parse($("prompt").value);
    if (!p.cut.length) { lastKey = ""; return; }
    const k = JSON.stringify([state.media, p.fixed, p.mp, p.aspect, !!state.images[0]]);
    if (k === lastKey && !fromSend) return;
    lastKey = k;
    if (state.media === "video") {
      if (p.aspect && state.video.aspect !== p.aspect) {
        state.video.aspect = p.aspect;
        LS.set("cs.v.aspect", p.aspect);
        if (window.syncVideoPills) window.syncVideoPills();
        toast("Aspect from your prompt: " + p.aspect);
      }
      return;
    }
    const cap = engineCap();
    const mp = p.mp != null ? Math.max(1, Math.min(cap, Math.round(p.mp * 2) / 2)) : null;
    const notes = [];
    if (p.mp != null && mp < p.mp) notes.push(p.mp + " MP is more than this model draws well, so " + mp + " MP (upscale after for more)");
    let res, said;
    if (p.fixed && state.images[0]) {
      res = mp != null ? "picture1:" + mp : "picture1:original";
      said = mp != null ? "Picture 1's shape at " + mp + " MP" : "Picture 1's own size";
    } else {
      if (p.fixed) notes.push("add Picture 1 to use its resolution");
      if (p.aspect == null && mp == null) { if (notes.length && !fromSend) toast(notes.join("; ")); return; }
      const asp = p.aspect || (state.quality === "custom" ? (state.customAspect || state.aspect) : state.aspect) || "1:1";
      const useMp = mp != null ? mp : Math.min(cap, Number(currentMp()) || 4);
      res = "custom:" + asp + ":" + useMp;
      said = asp + " · " + useMp + " MP";
    }
    applyResolution(res);
    if (mp != null && typeof setMp === "function") setMp(mp);   // applyResolution keeps an older MP in some modes
    if (!fromSend || notes.length) toast("Size from your prompt: " + said + (notes.length ? ". " + notes.join("; ") + "." : ""));
  }
  window.__sizeApply = apply;

  let timer = 0;
  $("prompt").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => apply(false), 450); });
  // Right before sending: settle the size from the prompt (capture phase runs before the send handlers).
  $("generate").addEventListener("click", () => { clearTimeout(timer); apply(true); }, true);
  // ...and take the size words out of what the model receives (built-in and workflow sends both POST here).
  const baseFetch = window.fetch;
  window.fetch = function (url, opts) {
    try {
      const u = String(url);
      if ((u === "/api/generate" || u === "/api/assist") && opts && typeof opts.body === "string") {
        const b = JSON.parse(opts.body);
        if (typeof b.prompt === "string") {
          const c = clean(b.prompt);
          if (c !== b.prompt) { b.prompt = c; opts = Object.assign({}, opts, { body: JSON.stringify(b) }); }
        }
      }
    } catch (e) {}
    return baseFetch.call(this, url, opts);
  };
})();
/* ===== end size from prompt ===== */

/* ===== Studio stage v20261111v =====
   1. Progress strip: a see-through bar above the pills with a long progress line, what the job is doing
      (from the ComfyUI node that is running), the steps once, and changing status words.
   2. Big live preview: while a job runs, a stage opens between the top bar and the composer. The preview is
      drawn at the shape of the picture being made; the composer gets smaller but still works.
   3. Edit threads: the stage lists every picture made from the same source picture (edits of edits too),
      newest first, with search and a fold button. Recent shows a thread as one card with a count. */
(function () {
  const WORDS = {
    queued: ["Waiting its turn", "Holding a spot in line", "Next up soon"],
    waiting: ["Waiting for ComfyUI", "Knocking on ComfyUI's door"],
    enhance: ["Polishing your prompt", "Finding the right words", "Sharpening the idea"],
    load: ["Waking the model", "Loading weights", "Warming up the GPU", "Unpacking the brain"],
    prepare: ["Studying your pictures", "Measuring the frame", "Prepping references"],
    read: ["Reading your prompt", "Parsing the vibe", "Understanding the scene"],
    paint: ["Sculpting light", "Weaving pixels", "Denoising the dream", "Chasing fine detail", "Mixing colours",
            "Settling the grain", "Shaping forms", "Balancing shadows", "Refining edges", "Finding the focus"],
    paintVideo: ["Rolling camera", "Animating frames", "Choreographing motion", "Keeping faces steady", "Timing the action",
                 "Blending frames"],
    decode: ["Developing the film", "Unfolding pixels", "Bringing it into focus"],
    upscale: ["Adding resolution", "Recovering detail"],
    save: ["Framing the result", "Saving your picture"],
    work: ["Working on it", "Crunching numbers"],
  };
  const PHASE_NAME = { queued: "Queued", waiting: "Waiting", enhance: "Enhancing prompt", load: "Loading model",
                       prepare: "Preparing pictures", read: "Reading prompt", paint: "Generating", decode: "Decoding",
                       upscale: "Upscaling", save: "Saving", work: "Working" };
  const gs = { open: false, key: "", sel: "", live: "", items: [], all: [], byId: {}, fold: false, q: "", started: {} };
  const ON = () => LS.get("cs.bigPreview", "1") !== "0";

  /* ---------------- threads ---------------- */
  function tkey(it, depth) {
    if (!it) return "";
    if ((depth || 0) > 25) return "id:" + it.id;
    const p = it.parent_id && gs.byId[it.parent_id];
    if (p) return tkey(p, (depth || 0) + 1);
    const src = (it.source_images || [])[0];
    if (it.mode === "edit" && src) return "in:" + src;
    return "id:" + it.id;
  }
  function jobKey(job) {
    if (job.parent_id && gs.byId[job.parent_id]) return tkey(gs.byId[job.parent_id]);
    if (job.source0) return "in:" + job.source0;
    return "id:" + job.id;
  }
  // One shared copy of the library list: several parts of the page ask at once (feed, stage, viewer), and on a
  // phone over the network each extra download is noticeable. Calls within a second share one request.
  let allP = null, allT = 0;
  function loadAll(force) {
    if (!force && allP && Date.now() - allT < 1000) return allP;
    allT = Date.now();
    allP = api("/api/gallery?mode=all").then((d) => { gs.all = d.items || []; }).catch(() => { gs.all = gs.all || []; })
      .then(() => { gs.byId = {}; gs.all.forEach((it) => { gs.byId[it.id] = it; }); return gs.all; });
    return allP;
  }
  function threadItems(key) { return gs.all.filter((it) => tkey(it) === key); }
  window.__threadKey = (it) => tkey(it);

  /* ---------------- progress strip ---------------- */
  function phaseOf(job) {
    if (job.status === "queued") return "queued";
    if (job.status === "waiting_comfy") return "waiting";
    if (/enhanc/i.test(job.message || "") && !job.phase) return "enhance";
    if (job.phase) return job.phase;
    return (job.progress_max || 0) ? "paint" : "load";
  }
  function word(job, phase) {
    let list = WORDS[phase] || WORDS.work;
    if (phase === "paint" && job.media === "video") list = WORDS.paintVideo;
    const t0 = gs.started[job.id] || (gs.started[job.id] = Date.now());
    const seed = [...String(job.id)].reduce((a, c) => a + c.charCodeAt(0), 0);
    return list[(seed + Math.floor((Date.now() - t0) / 2600)) % list.length];
  }
  function ensureStrip() {
    let el = $("gen-strip");
    if (!el) {
      el = document.createElement("div");
      el.id = "gen-strip";
      el.className = "gen-strip hidden";
      el.setAttribute("aria-live", "polite");
      const hud = $("hud");
      hud.parentNode.insertBefore(el, hud);
    }
    return el;
  }
  function renderStrip() {
    const box = ensureStrip();
    const active = (state.activeJobs || []).filter((j) => j && ["queued", "waiting_comfy", "running"].includes(j.status));
    box.classList.toggle("hidden", !active.length);
    // only the job that's generating gets a row; the rest wait behind "N queued" (opens the Queue panel)
    const main = active.find((j) => j.status === "running") || active.find((j) => j.status === "waiting_comfy") || active[0];
    const shown = main ? [main] : [];
    const keep = new Set(shown.map((j) => j.id));
    [...box.children].forEach((c) => { if (c.dataset.id && !keep.has(c.dataset.id)) c.remove(); });
    const waiting = active.length - shown.length;
    let more = box.querySelector(".gsr-more");
    if (waiting > 0) {
      if (!more) {
        more = document.createElement("button");
        more.type = "button";
        more.className = "gsr-more";
        more.setAttribute("aria-haspopup", "dialog");
        more.addEventListener("click", (e) => { renderQueuePop(); showPop("queue-pop", e.currentTarget); });
      }
      box.appendChild(more);   // always last, under the main row
      more.textContent = waiting + " queued ▾";
      more.title = "See and cancel the " + waiting + " job" + (waiting === 1 ? "" : "s") + " waiting";
    } else if (more) {
      more.remove();
    }
    if ($("queue-pop") && !$("queue-pop").hidden) renderQueuePop();
    shown.forEach((job) => {
      let row = [...box.children].find((c) => c.dataset.id === job.id);
      if (!row) {
        row = document.createElement("div");
        row.className = "gsr";
        row.dataset.id = job.id;
        row.innerHTML = '<button type="button" class="gsr-main" aria-label="Show the big preview">' +
          '<span class="gsr-word"></span><span class="gsr-phase"></span><span class="gsr-steps"></span><span class="gsr-pct"></span></button>' +
          '<button type="button" class="gsr-x" aria-label="Cancel this job">✕</button><span class="gsr-line"><i></i></span>';
        row.querySelector(".gsr-main").addEventListener("click", () => openForJob(job.id, true));
        row.querySelector(".gsr-x").addEventListener("click", () => cancelRun(job.id));
        box.appendChild(row);
      }
      const ph = phaseOf(job);
      const max = job.progress_max || 0, v = job.progress || 0;
      const pct = max ? Math.min(100, Math.round((100 * v) / max)) : 0;
      row.querySelector(".gsr-word").textContent = word(job, ph) + "…";
      row.querySelector(".gsr-phase").textContent = PHASE_NAME[ph] || "";
      row.querySelector(".gsr-steps").textContent = max ? ("Step " + v + " / " + max) : "";
      row.querySelector(".gsr-pct").textContent = max ? pct + "%" : "";
      const line = row.querySelector(".gsr-line");
      line.classList.toggle("indet", !max);
      line.querySelector("i").style.width = (max ? pct : 30) + "%";
      row.classList.toggle("is-run", job.status === "running");
    });
    syncDockHeight();
  }

  /* ---------------- stage ---------------- */
  function ensureStage() {
    let el = $("gen-stage");
    if (el) return el;
    el = document.createElement("section");
    el.id = "gen-stage";
    el.className = "gs hidden";
    el.setAttribute("aria-label", "Preview and edits");
    el.innerHTML =
      '<aside class="gs-rail">' +
      '  <div class="gs-rail-head"><button type="button" class="gs-fold" aria-label="Fold the edits list"><span class="gs-count"></span></button>' +
      '    <input type="search" class="gs-search" placeholder="Search edits" aria-label="Search edits"></div>' +
      '  <div class="gs-list" role="list"></div>' +
      '</aside>' +
      '<div class="gs-main">' +
      '  <div class="gs-top"><p class="gs-cap"></p><button type="button" class="icon-btn sm gs-close" aria-label="Hide the preview">✕</button></div>' +
      '  <div class="gs-canvas"><div class="gs-frame"><img class="gs-img" alt=""><video class="gs-vid hidden" controls playsinline loop></video>' +
      '    <div class="gs-ph"><span class="gs-ph-word"></span></div><div class="gs-live-tag hidden">Live</div></div></div>' +
      '  <div class="gs-actions">' +
      '    <button type="button" class="chip-btn danger gs-cancel hidden">Cancel</button>' +
      '    <button type="button" class="chip-btn primary gs-edit hidden">Edit this</button>' +
      '    <button type="button" class="chip-btn gs-inpaint hidden">Inpaint</button>' +
      '    <button type="button" class="chip-btn gs-details hidden">Details</button>' +
      '    <a class="chip-btn gs-dl hidden" download>Save</a>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(el);
    // caption, preview and buttons move as one block, centred in the space (no gaps between them)
    { const cv = el.querySelector(".gs-canvas"), fr = el.querySelector(".gs-frame");
      cv.insertBefore(el.querySelector(".gs-top"), fr); cv.appendChild(el.querySelector(".gs-actions")); }
    el.querySelector(".gs-close").addEventListener("click", closeStage);
    el.querySelector(".gs-fold").addEventListener("click", () => { gs.fold = !gs.fold; LS.set("cs.gsFold", gs.fold ? "1" : "0"); render(); });
    el.querySelector(".gs-search").addEventListener("input", (e) => { gs.q = e.target.value.trim().toLowerCase(); renderList(); });
    el.querySelector(".gs-cancel").addEventListener("click", () => gs.live && cancelRun(gs.live));
    async function useOriginal() {
      const o = originOf(); if (!o) return false;
      await reuseInputs(o.from);          // the stored copy of the picture this thread started from
      if (state.images.length > 1) { state.images = state.images.slice(0, 1); renderSlots(); renderAliasPreview(); }
      return true;
    }
    el.querySelector(".gs-edit").addEventListener("click", async () => {
      if (String(gs.sel).startsWith("orig:")) { if (await useOriginal()) { openStage(gs.key, gs.sel); $("prompt").focus(); } return; }
      const it = gs.byId[gs.sel]; if (!it) return;
      await reuseOutput(it, "picture1");
      openStage(gs.key, gs.sel);   // stay in this thread; the next edit lands here
      $("prompt").focus();
    });
    el.querySelector(".gs-inpaint").addEventListener("click", async () => {
      if (String(gs.sel).startsWith("orig:")) {
        if (await useOriginal()) { openStage(gs.key, gs.sel); setTimeout(() => { const b = $("inpaint-pill"); if (b) b.click(); }, 200); }
        return;
      }
      const it = gs.byId[gs.sel]; if (!it) return;
      await reuseOutput(it, "picture1");
      openStage(gs.key, gs.sel);
      setTimeout(() => { const b = $("inpaint-pill"); if (b) b.click(); }, 200);
    });
    el.querySelector(".gs-details").addEventListener("click", () => {
      const list = visibleItems();
      const i = list.findIndex((x) => x.id === gs.sel);
      if (i < 0) return;
      state.library = list;
      openViewer(i);
    });
    window.addEventListener("resize", () => gs.open && fit());
    el.querySelector(".gs-img").addEventListener("click", (e) => { if (e.currentTarget.src) openResultFs(e.currentTarget.src); });
    window.__gsFit = () => gs.open && fit();
    gs.fold = LS.get("cs.gsFold", "0") === "1";
    return el;
  }

  function visibleItems() {
    const q = gs.q;
    return gs.items.filter((it) => !q || ((it.prompt_user || it.prompt || "") + " " + (it.workflow_name || "")).toLowerCase().indexOf(q) >= 0);
  }

  function liveJob() { return (state.activeJobs || []).find((j) => j.id === gs.live && ["queued", "waiting_comfy", "running"].includes(j.status)); }

  function originOf() {
    // the picture this thread started from (an upload): shown last in the list, selectable like the others
    const oldest = gs.items[gs.items.length - 1];
    if (!gs.key.startsWith("in:") || !oldest) return null;
    const src = (oldest.input_snapshots && oldest.input_snapshots[0] && oldest.input_snapshots[0].url) || ("/api/source?id=" + encodeURIComponent(oldest.id) + "&slot=1");
    return { id: "orig:" + gs.key, src, from: oldest };
  }

  // Rebuilt only when the list itself changes. It used to be rebuilt on every progress poll (twice a second while
  // a job runs), so a tap that straddled a rebuild landed on a removed tile and did nothing.
  function renderList() {
    const el = ensureStage();
    const list = el.querySelector(".gs-list");
    const job = liveJob();
    const live = job && jobKey(job) === gs.key ? job : null;
    const items = visibleItems();
    const orig = !gs.q ? originOf() : null;
    const sig = [gs.key, live ? live.id : "", items.map((x) => x.id).join(","), orig ? orig.src : ""].join("|");
    if (list._sig !== sig) {
      list._sig = sig;
      list.innerHTML = "";
      const add = (id, src, label, cls, extra) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "gs-tile" + (cls ? " " + cls : "");
        b.dataset.id = id;
        b.setAttribute("role", "listitem");
        b.title = label || "";
        b.innerHTML = (src ? '<img alt="" loading="lazy">' : '<span class="gs-spin"></span>') + (extra || "");
        if (src) b.querySelector("img").src = src;
        b.addEventListener("click", () => { gs.sel = id; render(); });
        list.appendChild(b);
      };
      if (live) add(live.id, "", "Generating now", "is-live");
      items.forEach((it) => add(it.id, it.thumb, it.prompt_user || it.prompt || ""));
      if (orig) add(orig.id, orig.src, "The original picture", "is-origin", '<span class="gs-orig">Original</span>');
    }
    // cheap updates: which tile is selected, and the live tile's picture
    [...list.children].forEach((b) => b.classList.toggle("is-sel", b.dataset.id === gs.sel));
    if (live && live.has_preview) {
      const t = list.querySelector('.gs-tile.is-live');
      const now = Date.now();
      if (t && (!t._t || now - t._t > 1500)) {
        t._t = now;
        const url = "/api/live?id=" + encodeURIComponent(live.id) + "&t=" + now;
        const pre = new Image();
        pre.onload = () => { let im = t.querySelector("img"); if (!im) { t.innerHTML = '<img alt="">'; im = t.querySelector("img"); } im.src = url; };
        pre.src = url;
      }
    }
    const n = gs.items.length;
    el.querySelector(".gs-count").textContent = n ? (n === 1 ? "1 picture" : n + " pictures") : "New";
  }

  function fit() {
    const el = $("gen-stage");
    if (!el || el.classList.contains("hidden")) return;
    const canvas = el.querySelector(".gs-canvas"), frame = el.querySelector(".gs-frame");
    const top = el.querySelector(".gs-top"), acts = el.querySelector(".gs-actions");
    const gap = 10;
    const W = canvas.clientWidth;
    const H = canvas.clientHeight - (top ? top.offsetHeight : 0) - (acts ? acts.offsetHeight : 0) - 2 * gap;   // room left for the picture
    const ar = Number(frame.dataset.ar) || 1;
    let w = W, h = W / ar;
    if (h > H) { h = H; w = H * ar; }
    frame.style.width = Math.max(40, Math.floor(w)) + "px";
    frame.style.height = Math.max(40, Math.floor(h)) + "px";
  }

  function render() {
    const el = ensureStage();
    el.classList.toggle("is-fold", gs.fold);
    // the list only shows when there are two or more things to switch between (a single tile just repeats the big picture)
    const lj = liveJob();
    const tiles = gs.items.length + (originOf() && !gs.q ? 1 : 0) + (lj && jobKey(lj) === gs.key ? 1 : 0);
    el.classList.toggle("no-rail", tiles < 2);
    el.classList.toggle("has-search", gs.items.length >= 6);      // search only helps on longer threads
    renderList();
    const job = liveJob();
    const showLive = job && gs.sel === job.id;
    const orig = !showLive && String(gs.sel).startsWith("orig:") ? originOf() : null;
    const it = showLive || orig ? null : gs.byId[gs.sel];
    const frame = el.querySelector(".gs-frame"), img = el.querySelector(".gs-img"), vid = el.querySelector(".gs-vid");
    const ph = el.querySelector(".gs-ph");
    let ar = 1;
    if (showLive) {
      if (job.width && job.height) ar = job.width / job.height;
      else if (state.images[0] && state.images[0].width) ar = state.images[0].width / state.images[0].height;
      else if (job.media === "video") ar = 16 / 9;
      el.querySelector(".gs-cap").textContent = job.prompt_user || job.prompt || "";
      vid.classList.add("hidden"); vid.removeAttribute("src");
      if (job.has_preview) {
        const now = Date.now();
        if (!frame._t || now - frame._t > 650) {
          frame._t = now;
          const url = "/api/live?id=" + encodeURIComponent(job.id) + "&t=" + now;
          const pre = new Image();
          pre.onload = () => { if (gs.sel === job.id) { img.src = url; img.classList.remove("hidden"); ph.classList.add("hidden"); } };
          pre.src = url;
        }
      } else if (!img.src || frame._for !== job.id) {
        img.classList.add("hidden"); ph.classList.remove("hidden");
      }
      frame._for = job.id;
      frame._live = true;          // what's shown is a live frame: the finished picture must replace it
      el.querySelector(".gs-ph-word").textContent = word(job, phaseOf(job)) + "…";
      el.querySelector(".gs-live-tag").classList.toggle("hidden", !job.has_preview);
    } else if (orig) {
      el.querySelector(".gs-cap").textContent = "The original picture";
      ph.classList.add("hidden");
      el.querySelector(".gs-live-tag").classList.add("hidden");
      vid.classList.add("hidden"); vid.pause && vid.pause();
      img.classList.remove("hidden");
      if (frame._for !== orig.id) {
        frame._for = orig.id;
        img.onload = () => { if (frame._for === orig.id && img.naturalWidth) { frame.dataset.ar = img.naturalWidth / img.naturalHeight; fit(); } };
        img.src = orig.src;
      }
      ar = Number(frame.dataset.ar) || 1;
    } else if (it) {
      ar = (it.width && it.height) ? it.width / it.height : 1;
      el.querySelector(".gs-cap").textContent = it.prompt_user || it.prompt || "";
      ph.classList.add("hidden");
      el.querySelector(".gs-live-tag").classList.add("hidden");
      if (it.media === "video") {
        img.classList.add("hidden"); vid.classList.remove("hidden");
        if (vid.dataset.id !== it.id) { vid.dataset.id = it.id; vid.src = it.url; }
      } else {
        vid.classList.add("hidden"); vid.pause && vid.pause();
        img.classList.remove("hidden");
        if (frame._for !== it.id || frame._live) {
          frame._live = false;
          img.src = (it.url || it.thumb) + ((it.url || "").includes("?") ? "&" : "?") + "f=" + Date.now();
          frame._for = it.id;
        }
      }
    }
    frame.dataset.ar = ar;
    fit();
    el.querySelector(".gs-cancel").classList.toggle("hidden", !showLive);
    ["gs-edit", "gs-dl"].forEach((c) => el.querySelector("." + c).classList.toggle("hidden", !(it || orig)));
    el.querySelector(".gs-details").classList.toggle("hidden", !it);
    el.querySelector(".gs-inpaint").classList.toggle("hidden", !((it && it.media !== "video") || orig));
    const dl = el.querySelector(".gs-dl");
    if (it) { dl.href = it.download || ("/api/download?id=" + encodeURIComponent(it.id)); }
    else if (orig) { dl.href = orig.src; }
  }

  function setTop() {
    const tb = document.querySelector(".topbar");
    if (tb) document.documentElement.style.setProperty("--top-h", Math.ceil(tb.getBoundingClientRect().bottom) + "px");
  }

  async function openStage(key, sel) {
    ensureStage();
    if (!gs.all.length) await loadAll();
    gs.key = key;
    gs.items = threadItems(key).sort((a, b) => (b.t || 0) - (a.t || 0));
    gs.sel = sel || (gs.items[0] && gs.items[0].id) || gs.sel;
    gs.open = true;
    setTop();
    $("gen-stage").classList.remove("hidden");
    document.body.classList.add("gs-open");
    syncDockHeight();
    render();
    requestAnimationFrame(fit);
  }
  function closeStage() {
    gs.open = false;
    const el = $("gen-stage");
    if (el) el.classList.add("hidden");
    document.body.classList.remove("gs-open");
    syncDockHeight();
  }
  window.__openThread = (it) => { loadAll().then(() => openStage(tkey(gs.byId[it.id] || it), it.id)); };

  async function openForJob(id, force) {
    const job = (state.activeJobs || []).find((j) => j.id === id);
    if (!job) return;
    if (!force && !ON()) return;
    if (!gs.all.length) await loadAll();
    gs.live = id;
    openStage(jobKey(job), id);
  }

  /* ---------------- hooks ---------------- */
  // the strip replaces the old job cards
  renderJobQueue = function () {
    renderStrip();
    if (gs.open) {
      const job = liveJob();
      if (job || gs.sel === gs.live) render();
    }
  };
  const baseEnqueue = enqueueJob;
  enqueueJob = function (job) {
    const r = baseEnqueue.apply(this, arguments);
    gs.started[job.id] = Date.now();
    if (state.view === "create") {
      // the job does not know its thread yet: work it out from Picture 1 (a library picture, or an upload)
      const p1 = state.images && state.images[0];
      const j = (state.activeJobs || []).find((x) => x.id === job.id);
      if (j && p1 && !j.source0) j.source0 = job.mode === "edit" || job.media === "video" ? p1.name : null;
      openForJob(job.id, false);
    }
    return r;
  };
  const baseShow = showResult;
  showResult = function (job) {
    const r = baseShow.apply(this, arguments);
    if (gs.open && job && (gs.live === job.id || gs.sel === job.id)) {
      // the finished picture can take a moment to reach the library (inpaints are blended first): keep asking
      let tries = 0;
      const swap = () => loadAll(true).then(() => {
        const it = gs.byId[job.id];
        if (!it) { if (++tries < 8) setTimeout(swap, 700); return; }
        gs.live = "";
        openStage(tkey(it), job.id);
      });
      swap();
    } else {
      loadAll(true);
    }
    return r;
  };

  // Recent: one card per thread (newest picture), with a count; tap a thread to open it on the stage
  const baseRecent = refreshRecent;
  refreshRecent = async function () {
    const r = await baseRecent.apply(this, arguments);
    const row = $("recent-row");
    const items = state.recentItems || [];
    if (!row || !items.length) return r;
    await loadAll();
    const seen = {}, groups = [];
    items.forEach((it) => {
      const k = tkey(gs.byId[it.id] || it);
      if (seen[k]) { seen[k].n++; return; }
      seen[k] = { it, n: 1, k };
      groups.push(seen[k]);
    });
    groups.forEach((g) => { g.n = threadItems(g.k).length || g.n; });
    const shown = groups.map((g) => g.it);
    row.innerHTML = "";
    groups.forEach((g, i) => {
      const it = g.it;
      const b = document.createElement("button");
      b.type = "button";
      b.title = it.prompt_user || it.prompt || "";
      const thumb = document.createElement("span");
      thumb.className = "thumb";
      const img = document.createElement("img");
      img.loading = "lazy"; img.decoding = "async";
      img.alt = it.prompt_user || it.prompt || "Image";
      if (it.width && it.height) { img.width = it.width; img.height = it.height; }
      img.src = it.thumb;
      thumb.appendChild(img);
      if (g.n > 1) {
        const badge = document.createElement("span");
        badge.className = "stack-badge";
        badge.textContent = g.n + " edits";
        thumb.appendChild(badge);
        thumb.classList.add("is-stack");
      }
      b.appendChild(thumb);
      b.addEventListener("click", () => {
        if (g.n > 1) openStage(g.k, it.id);
        else { state.library = shown; openViewer(i); }
      });
      row.appendChild(b);
    });
    if (typeof decorateVideos === "function") try { decorateVideos(row, shown); } catch (e) {}
    return r;
  };

  // viewer: "All edits" opens the thread this picture belongs to
  const vEdit = $("viewer-edit");
  if (vEdit && !$("viewer-thread")) {
    const b = document.createElement("button");
    b.type = "button"; b.id = "viewer-thread"; b.className = "chip-btn hidden"; b.textContent = "All edits";
    vEdit.parentNode.insertBefore(b, vEdit.nextSibling);
    b.addEventListener("click", () => {
      const it = state.library[state.viewerIndex];
      if (!it) return;
      closeViewer();
      setView("create");
      window.__openThread(it);
    });
  }
  const baseOpenV = openViewer;
  openViewer = function () {
    const r = baseOpenV.apply(this, arguments);
    const it = state.library[state.viewerIndex];
    const b = $("viewer-thread");
    if (b && it) {
      const run = () => { const n = threadItems(tkey(gs.byId[it.id] || it)).length; b.classList.toggle("hidden", n < 2); b.textContent = "All edits (" + n + ")"; };
      if (gs.all.length) run(); else loadAll().then(run);
    }
    return r;
  };

  // leaving Create hides the stage; Escape hides it too
  const baseSetView = setView;
  setView = function (v) {
    const r = baseSetView.apply(this, arguments);
    if (v !== "create" && gs.open) closeStage();
    return r;
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && gs.open && !document.querySelector(".pop:not(.hidden), .sheet:not(.hidden), .viewer:not(.hidden)")) closeStage(); });
  setInterval(() => { if (gs.open && liveJob()) render(); }, 1300);
  ensureStrip();
})();
/* ===== end studio stage ===== */

/* ===== Stage fills the window; composer collapses v20261111w =====
   While the stage is open the composer shrinks to one slim line (pills stay, smaller). Tap or focus it and it
   opens to full size, floating over the bottom of the stage; tap the stage (or send) and it slims down again.
   The stage itself never resizes when the composer opens, so the preview doesn't jump. */
(function () {
  const body = document.body;
  const dockInner = document.querySelector(".dock-inner");
  const card = $("prompt-card");

  function measure() {
    // only measure the slim dock: the stage leaves exactly this much room at the bottom
    if (!body.classList.contains("gs-open") || body.classList.contains("gs-expand") || !dockInner) return;
    const h = Math.ceil(dockInner.getBoundingClientRect().height || 0) + 22;
    if (h === measure.last) return;
    measure.last = h;
    document.documentElement.style.setProperty("--gs-dock", h + "px");
    if (window.__gsFit) requestAnimationFrame(window.__gsFit);   // the stage refits its frame (no resize event: that loops)
  }
  const baseSync = syncDockHeight;
  syncDockHeight = function () {
    const r = baseSync.apply(this, arguments);
    measure();
    return r;
  };

  function expand() {
    if (!body.classList.contains("gs-open") || body.classList.contains("gs-expand")) return;
    body.classList.add("gs-expand");
    baseSync();
  }
  function collapse() {
    if (!body.classList.contains("gs-expand")) return;
    body.classList.remove("gs-expand");
    if (document.activeElement && card.contains(document.activeElement)) document.activeElement.blur();
    requestAnimationFrame(() => { baseSync(); measure(); });
  }
  window.__composerCollapse = collapse;

  // open: focusing the prompt, or tapping the slim composer anywhere that isn't a button
  $("prompt").addEventListener("focus", expand);
  card.addEventListener("pointerdown", (e) => {
    if (!body.classList.contains("gs-open") || body.classList.contains("gs-expand")) return;
    if (e.target.closest("button, a, input[type=file]")) return;
    expand();
  });
  // close: tapping the stage, or sending
  document.addEventListener("pointerdown", (e) => {
    if (!body.classList.contains("gs-expand")) return;
    const st = $("gen-stage");
    if (st && st.contains(e.target)) collapse();
  });
  $("generate").addEventListener("click", () => setTimeout(collapse, 60));

  // keep the slim measurement right when the stage opens/closes
  new MutationObserver(() => {
    // only touch the class when it is there: classList.remove() always rewrites the attribute, which would
    // wake this observer again forever
    if (!body.classList.contains("gs-open") && body.classList.contains("gs-expand")) body.classList.remove("gs-expand");
    requestAnimationFrame(measure);
  }).observe(body, { attributes: true, attributeFilter: ["class"] });
})();
/* ===== end stage fill ===== */


/* ===== Start ComfyUI from the app v20261111zi =====
   When ComfyUI is offline, a small Start button sits beside the status chip. It starts ComfyUI wherever it is
   installed (the server finds it), then the chip shows "Starting ComfyUI…" until it is connected. */
(function () {
  const chip = $("status");
  if (!chip) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.id = "comfy-start";
  btn.className = "comfy-start hidden";
  btn.setAttribute("aria-label", "Start ComfyUI");
  btn.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6c0 .5.5.8.9.5l7.2-4.8a.6.6 0 0 0 0-1L5.9 2.7c-.4-.3-.9 0-.9.5z"/></svg><span>Start</span>';
  chip.parentNode.insertBefore(btn, chip);
  // Stop: shown while ComfyUI is running (there may be no console window to close)
  const stop = document.createElement("button");
  stop.type = "button";
  stop.id = "comfy-stop";
  stop.className = "comfy-start comfy-stop hidden";
  stop.setAttribute("aria-label", "Stop ComfyUI");
  stop.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="1.5"/></svg><span>Stop</span>';
  chip.parentNode.insertBefore(stop, chip);
  stop.addEventListener("click", async () => {
    if (!window.confirm("Stop ComfyUI? Anything it's generating will stop.")) return;
    stop.disabled = true;
    const post = (force) => api("/api/comfy/stop", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }) });
    try {
      let res;
      try { res = await post(false); }
      catch (e) {
        if (!/generating/i.test(e.message || "") || !window.confirm("ComfyUI is still generating. Stop it anyway?")) throw e;
        res = await post(true);
      }
      toast((res && res.message) || "ComfyUI stopped.");
    } catch (e) { toast(e.message || "Couldn't stop ComfyUI"); }
    stop.disabled = false;
    setTimeout(refreshStatus, 1500);
  });
  let startingUntil = 0;
  const base = refreshStatus;
  function sync() {
    const st = chip.dataset.state;
    const down = (st === "down" || st === "starting") && chip.textContent !== "App unreachable";
    const starting = Date.now() < startingUntil;
    if (down && starting && st !== "starting") { chip.dataset.state = "starting"; chip.textContent = "Starting ComfyUI…"; }
    if (!down) startingUntil = 0;
    btn.classList.toggle("hidden", !down || starting);
    stop.classList.toggle("hidden", !(st === "up" || st === "busy"));
  }
  refreshStatus = async function () {
    const r = await base.apply(this, arguments);
    sync();
    return r;
  };
  setInterval(sync, 2000);   // the app's own 8 s status check may call the original function: keep the button in step
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await api("/api/comfy/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      startingUntil = Date.now() + 180000;
      toast((res && res.message) || "Starting ComfyUI…");
      refreshStatus();
      const t = setInterval(() => { refreshStatus(); if (!startingUntil || Date.now() > startingUntil) clearInterval(t); }, 3000);
    } catch (e) {
      toast(e.message || "Couldn't start ComfyUI");
    }
    btn.disabled = false;
  });
  refreshStatus();
})();
/* ===== end start ComfyUI ===== */


/* ===== Megapixel ceiling per engine v20261111zk =====
   The Size menu offers only what the current engine can draw, says so in one line, and the MiniMax AIO image
   editor (the video model, max "1080p (2.0 MP)") now gets the size you picked instead of its default. */
(function () {
  const labelMp = (txt) => { const m = String(txt || "").match(/([\d.]+)\s*MP/i); return m ? Number(m[1]) : null; };
  // Unknown hardware (ComfyUI was busy when the page loaded) must not shrink the menu: re-check until it's real.
  function hwKnown() { return !!(window.__hw && window.__hw.vram_gb && !window.__hw.error); }
  function hwCap() { return hwKnown() ? (window.__hw.image_mp_max || 20) : 20; }
  (async function recheck(n) {
    if (hwKnown() || n > 30) return;
    await new Promise((r) => setTimeout(r, n ? 10000 : 3000));
    try {
      const hw = await api("/api/hardware");
      if (hw && hw.vram_gb) { window.__hw = Object.assign(window.__hw || {}, hw); delete window.__hw.error; applyCap(); return; }
    } catch (e) {}
    recheck(n + 1);
  })(0);
  function engineCap() {
    const a = window.__wfActive && window.__wfActive();
    if (!a || state.media === "video") return { cap: Math.min(20, hwCap()), name: "MiniMax H3 (built-in)", builtin: true };
    const c = a.card || {}, rl = (c.slots || {}).res_label;
    let cap = c.mp_max || 4;
    if (rl && rl.map) {
      const mps = Object.values(rl.map).map(labelMp).filter((x) => x);
      if (mps.length) cap = Math.max(...mps);
    }
    return { cap: Math.min(cap, hwCap()), name: c.name || "This engine", builtin: false };
  }
  window.__engineMpCap = () => engineCap().cap;

  // The AIO node takes a size label (480p ... 1080p); pick the one closest to the megapixels you chose.
  window.__imgResLabel = function (card) {
    const rl = (card.slots || {}).res_label;
    if (!rl || !rl.map) return card.default_resolution;
    let want = Number(currentMp()) || 1;
    const p1 = state.images[0];
    if ((state.quality === "picture1" || state.quality === "keep") && state.p1Original !== false && p1 && p1.width) {
      want = (p1.width * p1.height) / 1e6;
    }
    let best = card.default_resolution, d = 1e9;
    const allowed = card.resolutions && card.resolutions.length ? card.resolutions : Object.keys(rl.map);   // what the server accepts
    Object.entries(rl.map).filter(([key]) => allowed.indexOf(key) >= 0).forEach(([key, label]) => {
      const mp = labelMp(label);
      if (mp != null && Math.abs(mp - want) < d) { d = Math.abs(mp - want); best = key; }
    });
    return best;
  };

  /* The dial follows the loaded engine: chips cover that engine's range, the slider stops at its ceiling, and a size
     above the ceiling is brought down (and shown) right away. Your own pick is remembered per engine, so going back
     to an engine that draws bigger restores it. */
  function chipsFor(cap) {
    if (cap <= 2) return [1, 1.5, 2];
    if (cap <= 4) return [1, 2, 3, 4];
    if (cap <= 8) return [2, 4, 6, 8];
    return [2, 4, 8, 12, 16].filter((v) => v <= cap);
  }
  const engineKey = () => "cs.mp.pick." + (state.media === "video" ? "video" : (state.engine || "builtin"));
  let lastChips = "", lastEngine = null, clamping = false;
  function buildChips(cap) {
    const box = $("p1-mp-chips");
    if (!box) return;
    const vals = chipsFor(cap), sig = vals.join(",");
    if (sig !== lastChips) {
      lastChips = sig;
      box.innerHTML = vals.map((v) => '<button type="button" data-p1mp="' + v + '">' + v + "</button>").join("");
    }
    const cur = Number(currentMp());
    const orig = (state.quality === "picture1" || state.quality === "keep") && state.mode === "edit" && state.p1Original !== false;
    box.querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", !orig && Number(b.dataset.p1mp) === cur));
  }
  // one listener for whatever chips are there (they are rebuilt per engine)
  if ($("p1-mp-chips") && !$("p1-mp-chips").dataset.delegated) {
    $("p1-mp-chips").dataset.delegated = "1";
    $("p1-mp-chips").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-p1mp]");
      if (!b) return;
      e.stopImmediatePropagation();
      setMp(b.dataset.p1mp);
    });
  }
  // remember what you pick, per engine
  if (typeof setMp === "function") {
    const baseSetMp = setMp;
    setMp = function (value) {
      const r = baseSetMp.apply(this, arguments);
      if (!clamping) { try { LS.set(engineKey(), String(state.mp)); } catch (e) {} }
      return r;
    };
    window.setMp = setMp;
  }

  function applyCap() {
    const { cap, name, builtin } = engineCap();
    const eng = engineKey();
    const engineChanged = eng !== lastEngine;
    // settled on this engine: what's selected now is your size for it (default included), so it comes back later
    if (!engineChanged && state.media !== "video" && Number(currentMp())) {
      try { LS.set(eng, String(currentMp())); } catch (e) {}
    }
    lastEngine = eng;
    if (state.media !== "video") {
      const cur = Number(currentMp()) || 0;
      const saved = Number(LS.get(eng, "")) || 0;
      const orig = (state.quality === "picture1" || state.quality === "keep") && state.p1Original !== false;
      let want = null;
      if (engineChanged && saved && Math.abs(saved - cur) > 0.01) want = Math.min(saved, cap);   // your pick for this engine
      else if (cur > cap + 0.01) want = cap;                                                     // more than it draws well
      if (want != null && !orig && Math.abs(want - cur) > 0.01) {
        clamping = true;
        try { setMp(want); } finally { clamping = false; }
        if (cur > cap + 0.01 && !saved) toast("Size set to " + cap + " MP, the most " + name + " draws well.");
      }
    }
    buildChips(cap);
    ["mp-slider", "mp-num", "p1-mp-slider", "p1-mp-num"].forEach((id) => { if ($(id)) $(id).max = cap; });
    if ($("p1-mp-slider")) {
      const v = Math.min(cap, Number($("p1-mp-slider").value) || cap);
      $("p1-mp-slider").value = v;
    }
    let note = $("mp-engine-note");
    if (!note) {
      note = document.createElement("p");
      note.id = "mp-engine-note";
      note.className = "mp-engine-note";
      const menu = $("res-menu");
      if (menu) menu.appendChild(note);
    }
    if (builtin) note.textContent = "Up to " + cap + " MP on this PC.";
    else if (cap < 4) note.textContent = name + " goes up to " + cap + " MP. For bigger pictures, use MiniMax H3 (built-in): up to " + Math.min(20, hwCap()) + " MP.";
    else note.textContent = name + " goes up to " + cap + " MP.";
    // the trigger shows what will really be made
    const want = Number(currentMp()) || 0;
    if (!builtin && want > cap && $("res-trigger-meta")) $("res-trigger-meta").textContent = cap + " MP (engine max)";
  }
  window.__applyMpCap = applyCap;
  const baseEng = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof baseEng === "function") baseEng.apply(this, arguments);
    setTimeout(applyCap, 0);
  };
  $("res-trigger").addEventListener("click", () => setTimeout(applyCap, 0));
  if (typeof scheduleOutputSize === "function") {
    const baseSched = scheduleOutputSize;
    scheduleOutputSize = function () { const r = baseSched.apply(this, arguments); setTimeout(applyCap, 120); return r; };
  }
  setTimeout(applyCap, 1500);
})();
/* ===== end megapixel ceiling ===== */


/* ===== Look & feel: engine pill, friendly model names, pill-row fades v20261111zy =====
   Labels only: every value the app sends stays exactly the same. */
(function () {
  /* ---------- engine pill: what is loaded, one tap to change it ---------- */
  function engineName() {
    const pick = $("wf-pick");
    const o = pick && pick.selectedOptions && pick.selectedOptions[0];
    let n = o ? o.textContent : "";
    n = n.replace(/\s*\(built-in\)\s*$/i, "").replace(/\s*·\s*(Text to image|Image edit|Text to video|Image to video|Inpaint.*)$/i, "");
    if (!n || /^MiniMax H3$/i.test(n)) n = (state.media === "video") ? (n || "Video engine") : "MiniMax H3";
    return n;
  }
  function syncEnginePill() {
    const l = $("engine-pill-label");
    if (!l) return;
    const n = engineName();
    l.textContent = n.length > 26 ? n.slice(0, 25) + "…" : n;
    $("engine-pill").title = "Engine: " + n + " (tap to change)";
    $("engine-pill").classList.toggle("is-wf", !!state.engine && state.engine !== "builtin");
  }
  const baseEng = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof baseEng === "function") baseEng.apply(this, arguments);
    setTimeout(() => { syncEnginePill(); prettify(); updateFades(); }, 60);
  };
  if ($("engine-pill")) {
    $("engine-pill").addEventListener("click", () => {
      if (typeof openSettings === "function") openSettings();
      setTimeout(() => {
        const g = document.querySelector("#settings-pop .engine-group");
        if (g) g.scrollIntoView({ block: "start" });
        const f = $("wf-family");
        if (f) { f.focus({ preventScroll: true }); g && g.classList.add("flash"); setTimeout(() => g && g.classList.remove("flash"), 900); }
      }, 30);
    });
  }
  ["wf-pick", "wf-family"].forEach((id) => { if ($(id)) $(id).addEventListener("change", () => setTimeout(syncEnginePill, 400)); });

  /* ---------- Auto / Manual settings ---------- */
  const TUNE_NOTES = {
    auto: "Auto: steps, CFG, sampler and scheduler follow the model you load (its tested settings). Unknown models use the workflow's own.",
    manual: "Manual: your own steps, CFG, sampler and scheduler are kept for each engine, and every other setting in the workflow is listed under All workflow settings. Changing the model won't touch them.",
  };
  function syncTune() {
    const m = tuneMode();
    document.querySelectorAll("#tune-seg > button").forEach((b) => {
      const on = b.dataset.tune === m;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    if ($("tune-note")) $("tune-note").textContent = TUNE_NOTES[m];
    const chip = $("tune-chip");
    if (chip) { chip.textContent = m === "manual" ? "Manual" : "Auto"; chip.classList.toggle("is-manual", m === "manual"); }
    const pill = $("engine-pill");
    if (pill) pill.setAttribute("aria-label", "Engine " + engineName() + ", settings " + m + ". Tap to change.");
  }
  function builtinAuto() {
    const c = state.cfg || {}, d = c.defaults || {};
    if ($("steps")) $("steps").value = c.default_steps || 6;
    if ($("cfg")) $("cfg").value = d.cfg ?? 1;
    const setSel = (id, v) => { const el = $(id); if (el && v && [...el.options].some((o) => o.value === v)) el.value = v; };
    setSel("sampler_name", d.sampler_name || "euler");
    setSel("scheduler", d.scheduler || "simple");
  }
  function setTune(m) {
    if (m === tuneMode()) return;
    const id = state.engine || "builtin";
    if (m === "manual") {
      // start Manual from what is on screen now, unless this engine already has your own values
      if (LS.get(tuneKey(id), null) == null) saveManual(id);
      LS.set("cs.tune.mode", "manual");
      restoreManual(id);
      if (window.__refillWfSettings && id !== "builtin") window.__refillWfSettings();
      toast("Manual: your settings stay as you set them");
    } else {
      LS.set("cs.tune.mode", "auto");
      if (id === "builtin" || id === "none") builtinAuto();
      else if (window.__refillWfSettings) window.__refillWfSettings();
      toast("Auto: settings follow the loaded model");
    }
    syncTune();
  }
  document.querySelectorAll("#tune-seg > button").forEach((b) => b.addEventListener("click", () => {
    setTune(b.dataset.tune);
    if (b.dataset.tune === "manual" && window.__restoreEditLora) window.__restoreEditLora();
    if (window.__renderKnobs) window.__renderKnobs();
    if (window.__syncKreaHint) window.__syncKreaHint();
  }));
  if ($("krea-keepface")) {
    $("krea-keepface").checked = LS.get("cs.krea.keepface", "0") === "1";
    $("krea-keepface").addEventListener("change", () => LS.set("cs.krea.keepface", $("krea-keepface").checked ? "1" : "0"));
  }
  // in Manual, every change you make is remembered for the engine you're on
  TUNE_KEYS.forEach((k) => {
    const el = $(k);
    if (el) el.addEventListener("change", () => { if (tuneMode() === "manual") saveManual(state.engine || "builtin"); });
  });
  const baseEng2 = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof baseEng2 === "function") baseEng2.apply(this, arguments);
    // built-in H3 in Manual: put your own values back (workflows do it in fillWfSettings)
    if (tuneMode() === "manual" && (!state.engine || state.engine === "builtin")) setTimeout(() => restoreManual("builtin"), 0);
    setTimeout(() => { syncTune(); if (window.__syncKreaHint) window.__syncKreaHint(); }, 80);
  };
  syncTune();

  /* ---------- friendly model names ---------- */
  const MODEL_RE = /\.(safetensors|gguf|ckpt|pt|pth|sft|bin)$/i;
  const SEP = /[\\/]/;   // Windows (\) and web (/) folder separators
  function nice(v) { return String(v).split(SEP).pop().replace(MODEL_RE, ""); }
  function prettify(root) {
    (root || document).querySelectorAll("#settings-pop select").forEach((sel) => {
      const opts = [...sel.options].filter((o) => MODEL_RE.test(o.value));
      if (!opts.length) return;
      const count = {};
      opts.forEach((o) => { const k = nice(o.value).toLowerCase(); count[k] = (count[k] || 0) + 1; });
      opts.forEach((o) => {
        const base = nice(o.value), parts = String(o.value).split(SEP);
        const folder = parts.length > 1 ? parts[parts.length - 2] : "";
        o.textContent = count[base.toLowerCase()] > 1 && folder ? base + "  ·  " + folder : base;
        o.title = o.value;
      });
      const cur = sel.selectedOptions && sel.selectedOptions[0];
      sel.title = cur ? cur.value : "";
    });
  }
  window.__prettifyModels = prettify;
  // selects are refilled when engines/models load: tidy them whenever the settings panel changes
  const pop = $("settings-pop");
  if (pop && window.MutationObserver) {
    let t = 0;
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => prettify(), 50); })
      .observe(pop, { childList: true, subtree: true });
    pop.addEventListener("change", (e) => { if (e.target.tagName === "SELECT") e.target.title = e.target.value; });
  }

  /* ---------- pill row: fade at an edge when there's more to scroll ---------- */
  const hud = $("hud"), sc = $("hud-scroller");
  function updateFades() {
    if (!hud || !sc) return;
    const max = sc.scrollWidth - sc.clientWidth;
    hud.classList.toggle("more-left", sc.scrollLeft > 4);
    hud.classList.toggle("more-right", max - sc.scrollLeft > 4);
  }
  if (sc) {
    sc.addEventListener("scroll", updateFades, { passive: true });
    window.addEventListener("resize", updateFades);
    if (window.ResizeObserver) new ResizeObserver(updateFades).observe(sc);
  }
  ["setMode", "setMedia", "setVideoMode"].forEach((name) => {
    const orig = window[name];
    if (typeof orig !== "function") return;
    window[name] = function () { const r = orig.apply(this, arguments); setTimeout(() => { updateFades(); syncEnginePill(); }, 80); return r; };
  });

  setTimeout(() => { syncEnginePill(); prettify(); updateFades(); }, 800);
  setTimeout(() => { syncEnginePill(); updateFades(); }, 3000);
})();
/* ===== end look & feel ===== */

/* ===== Video / audio inputs for workflows that take them v20261112l =====
   A workflow that loads a video (to edit, restyle, or drive motion) or an audio clip gets a small strip under the
   pictures: + Video / + Audio, from this PC's folders or from this device. Nothing shows for other engines. */
(function () {
  state.mediaIn = state.mediaIn || { videos: [], audios: [] };
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const WORD = { videos: "video", audios: "audio" };

  function need() {
    const a = window.__wfActive && window.__wfActive();
    const s = (a && a.card && a.card.slots) || {};
    return { videos: (s.videos || []).length, audios: (s.audios || []).length, card: a && a.card };
  }

  function render() {
    const box = $("media-in");
    if (!box) return;
    const n = need();
    const on = !!(n.videos || n.audios);
    box.classList.toggle("hidden", !on);
    document.body.classList.toggle("wf-media-in", on);
    if (!on) return;
    ["videos", "audios"].forEach((k) => { if (state.mediaIn[k].length > n[k]) state.mediaIn[k].length = n[k]; });
    const list = $("media-in-list");
    list.innerHTML = "";
    ["videos", "audios"].forEach((k) => state.mediaIn[k].forEach((it, i) => {
      const chip = document.createElement("div");
      chip.className = "media-chip " + WORD[k];
      chip.innerHTML = (k === "videos"
        ? '<img alt="" loading="lazy" src="/api/input?frame=1&name=' + encodeURIComponent(it.name) + '"><span class="media-play" aria-hidden="true">▶</span>'
        : '<span class="media-note" aria-hidden="true">♫</span>')
        + '<span class="media-name" title="' + esc(it.label || it.name) + '">' + esc(it.label || it.name.split("/").pop()) + "</span>"
        + '<button type="button" class="media-x" aria-label="Remove ' + WORD[k] + '">✕</button>';
      chip.querySelector(".media-x").addEventListener("click", () => { state.mediaIn[k].splice(i, 1); render(); });
      list.appendChild(chip);
    }));
    $("media-add-video").classList.toggle("hidden", state.mediaIn.videos.length >= n.videos);
    $("media-add-audio").classList.toggle("hidden", state.mediaIn.audios.length >= n.audios);
    const lead = $("media-in-lead");
    const want = [];
    if (n.videos > state.mediaIn.videos.length) want.push(n.videos === 1 ? "a video" : n.videos + " videos");
    if (n.audios > state.mediaIn.audios.length) want.push(n.audios === 1 ? "an audio clip" : n.audios + " audio clips");
    lead.textContent = want.length ? "This workflow needs " + want.join(" and ") + "." : "";
    lead.classList.toggle("hidden", !want.length);
  }
  window.__renderMediaIn = render;

  function add(rec) {
    if (!rec || !rec.name) return;
    const k = rec.kind === "audio" ? "audios" : "videos";
    if (state.mediaIn[k].length >= need()[k]) { toast("This workflow takes " + need()[k] + " " + WORD[k] + (need()[k] === 1 ? "" : "s") + "."); return; }
    state.mediaIn[k].push({ name: rec.name, label: rec.label || rec.name.split("/").pop(), kind: rec.kind });
    render();
  }

  /* ---- choose where from ---- */
  let wantKind = "video";
  function chooser(kind, anchor) {
    wantKind = kind;
    const pop = $("media-src-pop");
    pop.querySelector(".media-src-title").textContent = kind === "video" ? "Add a video" : "Add an audio clip";
    pop.classList.remove("hidden");
    pop.hidden = false;
    const r = anchor.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(window.innerWidth - pop.offsetWidth - 8, r.left)) + "px";
    pop.style.top = Math.max(8, r.top - pop.offsetHeight - 8) + "px";
  }
  function closeChooser() { const p = $("media-src-pop"); if (p) { p.classList.add("hidden"); p.hidden = true; } }
  document.addEventListener("click", (e) => {
    const p = $("media-src-pop");
    if (p && !p.hidden && !p.contains(e.target) && !e.target.closest("#media-add-video,#media-add-audio")) closeChooser();
  });
  if ($("media-add-video")) $("media-add-video").addEventListener("click", (e) => chooser("video", e.currentTarget));
  if ($("media-add-audio")) $("media-add-audio").addEventListener("click", (e) => chooser("audio", e.currentTarget));
  if ($("media-src-device")) $("media-src-device").addEventListener("click", () => {
    closeChooser();
    const f = $("media-file");
    f.accept = wantKind === "video" ? "video/*,.mp4,.mov,.webm,.mkv,.m4v" : "audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aac";
    f.value = "";
    f.click();
  });
  if ($("media-file")) $("media-file").addEventListener("change", async () => {
    const file = $("media-file").files && $("media-file").files[0];
    if (!file) return;
    toast("Adding " + (file.name || wantKind) + "…");
    try { add(await uploadFile(file)); } catch (err) { showError(err.message || "Couldn't add that file."); }
  });
  if ($("media-src-pc")) $("media-src-pc").addEventListener("click", () => { closeChooser(); openPc(wantKind); });

  /* ---- a small PC browser that lists only videos or only audio ---- */
  const pc = { kind: "video", root: "", path: "" };
  async function openPc(kind) {
    pc.kind = kind;
    const sheet = $("media-pc");
    sheet.classList.remove("hidden");
    sheet.hidden = false;
    $("media-pc-title").textContent = kind === "video" ? "Videos on this PC" : "Audio on this PC";
    try {
      const r = await api("/api/pc/roots");
      const roots = [].concat(r.roots || [], r.shortcuts || [], r.drives || []);
      const sel = $("media-pc-root");
      sel.innerHTML = '<option value="">Recent</option>' + roots.map((x) => '<option value="' + esc(x.id) + '">' + esc(x.name || x.label || x.id) + "</option>").join("");
      sel.value = pc.root && roots.some((x) => x.id === pc.root) ? pc.root : "";
      pc.root = sel.value;
    } catch (e) { /* keep Recent */ }
    load();
  }
  function closePc() { const s = $("media-pc"); s.classList.add("hidden"); s.hidden = true; }
  async function load() {
    const grid = $("media-pc-grid");
    grid.innerHTML = '<p class="muted">Loading…</p>';
    try {
      const d = pc.root
        ? await api("/api/pc/list?media=" + pc.kind + "&root=" + encodeURIComponent(pc.root) + "&path=" + encodeURIComponent(pc.path || "") + "&limit=200")
        : await api("/api/pc/recent?media=" + pc.kind + "&limit=120");
      const dirs = d.dirs || [];
      const files = d.files || d.items || [];
      grid.innerHTML = "";
      if (pc.root && pc.path) {
        const up = document.createElement("button");
        up.type = "button"; up.className = "media-pc-dir"; up.textContent = "↑ Up";
        up.addEventListener("click", () => { pc.path = pc.path.split("/").slice(0, -1).join("/"); load(); });
        grid.appendChild(up);
      }
      dirs.forEach((x) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "media-pc-dir"; b.textContent = "📁 " + x.name;
        b.addEventListener("click", () => { pc.path = x.path; load(); });
        grid.appendChild(b);
      });
      files.forEach((x) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "media-pc-file";
        const root = x.root || pc.root;
        b.innerHTML = (pc.kind === "video"
          ? '<img alt="" loading="lazy" src="/api/pc/thumb?root=' + encodeURIComponent(root) + "&path=" + encodeURIComponent(x.path) + '">'
          : '<span class="media-note" aria-hidden="true">♫</span>') + '<span class="media-name">' + esc(x.name) + "</span>";
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            const r = await api("/api/pc/pick", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: [{ root, path: x.path }] }) });
            add(r.item || (r.items || [])[0]);
            closePc();
          } catch (err) { showError(err.message || "Couldn't add that file."); b.disabled = false; }
        });
        grid.appendChild(b);
      });
      if (!dirs.length && !files.length) grid.innerHTML = '<p class="muted">No ' + (pc.kind === "video" ? "videos" : "audio files") + " here.</p>";
    } catch (err) {
      grid.innerHTML = '<p class="muted">' + esc(err.message || "Couldn't open that folder.") + "</p>";
    }
  }
  if ($("media-pc-root")) $("media-pc-root").addEventListener("change", () => { pc.root = $("media-pc-root").value; pc.path = ""; load(); });
  if ($("media-pc-close")) $("media-pc-close").addEventListener("click", closePc);

  // follow the engine
  const base = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof base === "function") base.apply(this, arguments);
    setTimeout(render, 90);
  };
  window.__mediaInBody = () => ({ videos: state.mediaIn.videos.map((x) => x.name), audios: state.mediaIn.audios.map((x) => x.name) });
  render();
})();

/* ===== Updates: important-fix banner + automatic fixes switch v20261112n =====
   The server checks GitHub in the background (once the app knows its GitHub page). A normal update shows on the
   Workflows page; an important (critical) fix also shows a banner. Installing keeps data/ (library, PIN, settings)
   and rolls back by itself if the new version doesn't start. */
(function () {
  const LATER = "cs.upd.later";
  async function refresh() {
    let st;
    try { st = await api("/api/update/state"); } catch (e) { return; }
    if ($("upd-auto")) {
      $("upd-auto").checked = !!st.auto_critical;
      $("upd-auto").disabled = !(st.repo || st.git);
      $("upd-auto").closest("label").title = (st.repo || st.git) ? "" : "Available once this copy knows its GitHub page.";
    }
    const show = !!(st.critical && st.behind) && LS.get(LATER, "") !== String(st.latest || "");
    const b = $("upd-banner");
    if (!b) return;
    b.classList.toggle("hidden", !show);
    if (show) b.querySelector(".upd-text").textContent = "Important fix available (" + (st.latest || "new version") + "). It installs in a few seconds and keeps your library.";
  }
  async function install() {
    const b = $("upd-banner");
    b.querySelector(".upd-text").textContent = "Installing the fix\u2026 the app restarts by itself.";
    try {
      const r = await api("/api/update/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.updated) { b.querySelector(".upd-text").textContent = r.message || "Nothing to install."; return; }
    } catch (e) { b.querySelector(".upd-text").textContent = e.message || "Couldn't install the fix."; return; }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try { await api("/api/status"); location.reload(); return; } catch (e) {}
    }
  }
  if ($("upd-install")) $("upd-install").addEventListener("click", install);
  if ($("upd-later")) $("upd-later").addEventListener("click", async () => {
    try { const st = await api("/api/update/state"); LS.set(LATER, String(st.latest || "")); } catch (e) {}
    $("upd-banner").classList.add("hidden");
  });
  if ($("upd-auto")) $("upd-auto").addEventListener("change", async () => {
    try {
      const r = await api("/api/update/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: $("upd-auto").checked }) });
      toast(r.auto_critical ? "Important fixes will install by themselves (only while idle)" : "Important fixes will wait for you");
    } catch (e) { showError(e.message || "Couldn't save that."); }
  });
  refresh();
  setInterval(refresh, 30 * 60 * 1000);
})();

/* ===== Node packs Comfy Studio added (Workflows page): list + Remove v20261112p ===== */
(function () {
  const page = $("view-workflows");
  if (!page) return;
  const det = document.createElement("details");
  det.className = "fold-block wf-nodepacks";
  det.innerHTML = '<summary><span class="fold-label">Node packs added by Comfy Studio</span></summary>'
    + '<div class="wf-nodepacks-body"><p class="fold-note">Packs installed here from a GitHub link or a zip. Remove moves a pack to ComfyUI\u2019s custom_nodes_quarantine folder (nothing is deleted); restart ComfyUI afterwards.</p>'
    + '<div class="wf-nodepacks-list"></div>'
    + '<div class="wf-pack-add-row"><input type="url" class="wf-gh-url" placeholder="https://github.com/owner/repo" aria-label="GitHub link of a node pack">'
    + '<button type="button" class="chip-btn wf-gh-go">Install</button><button type="button" class="chip-btn wf-zip-go">Add from zip</button>'
    + '<input type="file" class="wf-zip-file" accept=".zip,application/zip" hidden></div></div>';
  const howto = page.querySelector(".wf-howto");
  page.insertBefore(det, howto || null);
  async function load() {
    const list = det.querySelector(".wf-nodepacks-list");
    let d;
    try { d = await api("/api/nodes/custom"); } catch (e) { list.textContent = ""; return; }
    list.innerHTML = "";
    if (!(d.installed || []).length) { list.innerHTML = '<p class="muted">None yet.</p>'; return; }
    d.installed.forEach((it) => {
      const row = document.createElement("div");
      row.className = "wf-pack";
      row.innerHTML = "<div><strong></strong><small></small></div>";
      row.querySelector("strong").textContent = it.name;
      row.querySelector("small").textContent = (it.source || "") + (it.requirements_ok === false ? " \u00b7 some requirements didn't install" : "");
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip-btn danger"; b.textContent = "Remove";
      b.addEventListener("click", async () => {
        if (!window.confirm("Remove " + it.name + "? It's moved to custom_nodes_quarantine, so you can put it back. Restart ComfyUI afterwards.")) return;
        try {
          const r = await api("/api/nodes/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: it.name }) });
          toast("Moved to " + r.moved_to, 4000);
          load();
        } catch (e) { showError(e.message); }
      });
      row.appendChild(b);
      list.appendChild(row);
    });
  }
  det.addEventListener("toggle", () => { if (det.open) load(); });
  det.querySelector(".wf-gh-go").addEventListener("click", (e) => window.__installGithub && window.__installGithub(det.querySelector(".wf-gh-url").value.trim(), e.currentTarget));
  det.querySelector(".wf-zip-go").addEventListener("click", () => det.querySelector(".wf-zip-file").click());
  det.querySelector(".wf-zip-file").addEventListener("change", (e) => window.__installNodeZip && window.__installNodeZip(e.target.files && e.target.files[0], det.querySelector(".wf-zip-go")));
})();

/* ===== Prompt Enhancer instructions per engine v20261112q =====
   Each engine shows the built-in system prompt the enhancer uses for it (new pictures / videos, and edits). The
   user can change it for that engine; Back to default removes their version. The model's own style note is
   still sent with each prompt, and the app keeps one short output-format line so the result can always be read. */
(function () {
  const box = $("pe-sys-group");
  if (!box) return;
  let cur = { mode: "t2i", def: "", custom: "", engine: "builtin" };
  // built-in MiniMax H3 has its own instructions too ("builtin"); workflow engines use their id
  function engineId() { return !state.engine || state.engine === "none" ? "builtin" : state.engine; }
  function syncTabs(modes, media) {
    const tabs = $("pe-sys-mode");
    tabs.classList.toggle("hidden", (modes || []).length < 2);
    document.querySelectorAll("#pe-sys-mode button").forEach((b) => {
      b.classList.toggle("is-on", b.dataset.peSys === cur.mode);
      b.classList.toggle("hidden", (modes || []).indexOf(b.dataset.peSys) < 0);
      b.textContent = media === "video" ? (b.dataset.peSys === "edit" ? "With pictures" : "Text only")
        : (b.dataset.peSys === "edit" ? "Edit" : "New");
    });
  }
  async function load(mode) {
    const eng = engineId();
    cur.mode = mode || cur.mode || "t2i";
    const peMode = state.peMode === "quality" ? "quality" : "fast";
    let d;
    try { d = await api("/api/pe/system?engine=" + encodeURIComponent(eng) + "&mode=" + cur.mode + "&pe_mode=" + peMode); }
    catch (e) { $("pe-sys-status").textContent = e.message || "Couldn't load the instructions."; return; }
    // engines whose workflow has no prompt box (or turns Enhance off) have nothing to show here
    box.classList.toggle("hidden", !(d.modes || []).length);
    if (!(d.modes || []).length) return;
    cur = { mode: d.mode || cur.mode, def: d.default || "", custom: d.custom || "", engine: eng };
    syncTabs(d.modes, d.media);
    $("pe-sys-text").value = cur.custom || cur.def;
    $("pe-sys-badge").textContent = cur.custom ? "yours" : "";
    const what = d.media === "video" ? (cur.mode === "edit" ? "videos from your pictures" : "videos from text")
      : (cur.mode === "edit" ? "picture edits" : "new pictures");
    $("pe-sys-status").textContent = (cur.custom ? "Your own instructions for " : "Built-in instructions (" + (peMode === "quality" ? "Quality" : "Fast") + ") for ")
      + (d.engine_name || "this engine") + ", " + what + ". Edit the text and Save to use your own; the output format is kept for you.";
    $("pe-sys-note").textContent = d.model_note ? "Also sent with each prompt for this model: " + d.model_note : "";
    $("pe-sys-reset").disabled = !cur.custom;
  }
  async function save(reset) {
    const text = $("pe-sys-text").value;
    const body = { engine: cur.engine, mode: cur.mode };
    if (reset || text.trim() === cur.def.trim() || !text.trim()) body.reset = true; else body.text = text;
    try {
      await api("/api/pe/system", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      toast(body.reset ? "Back to the built-in instructions" : "Saved. The enhancer uses your instructions for this engine.");
      load(cur.mode);
    } catch (e) { showError(e.message || "Couldn't save that."); }
  }
  $("pe-sys-save").addEventListener("click", () => save(false));
  $("pe-sys-reset").addEventListener("click", () => save(true));
  document.querySelectorAll("#pe-sys-mode button").forEach((b) => b.addEventListener("click", () => load(b.dataset.peSys)));
  // refresh whenever Settings opens, and when the engine changes
  const pop = $("settings-pop");
  if (pop) new MutationObserver(() => { if (!pop.hidden && !pop.classList.contains("hidden")) load((state.mode === "edit" || (state.images || []).length) ? "edit" : "t2i"); })
    .observe(pop, { attributes: true, attributeFilter: ["hidden", "class"] });
  const base = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof base === "function") base.apply(this, arguments);
    const p = $("settings-pop");
    if (p && !p.hidden) setTimeout(() => load(), 120);
  };
  window.__peSysLoad = load;
})();

/* ===== Keep clothing switch v20261112r =====
   On by default. When an edit (or a new picture that describes clothes) isn't about clothing, the server adds a
   short "keep the clothing the same" instruction in the engine's own wording; prompts about clothing are left alone. */
(function () {
  const cb = $("keep-clothing");
  if (!cb) return;
  try { cb.checked = localStorage.getItem("cs.keepcloth") !== "0"; } catch (e) { cb.checked = true; }
  cb.addEventListener("change", () => {
    try { localStorage.setItem("cs.keepcloth", cb.checked ? "1" : "0"); } catch (e) {}
    toast(cb.checked ? "Clothing is kept unless your prompt is about clothing" : "Clothing guard off");
  });
})();

/* ===== Auto LoRAs + LoRA search with folders v20261112s =====
   Each LoRA's model family and trigger words come from the file itself (or its folder / name), so this works for
   anyone's LoRAs. With "Auto-pick" on, LoRAs made for the loaded engine's model are added when the prompt uses
   their trigger words or the words of their name (rules in static/lora_match.js). A LoRA whose model can't be told is never
   auto-picked; it can still be added by hand from the search. */
(function () {
  let INDEX = null, FAMMAP = {};
  const dismissed = new Set();
  function loraMax() { try { return parseInt(localStorage.getItem("cs.lora.max"), 10) || 3; } catch (e) { return 3; } }
  // crossing a LoRA out skips it for the next picture only: after you send, it comes back if the prompt still asks for it
  window.__loraSent = () => { dismissed.clear(); window.__syncLoraPill && window.__syncLoraPill(); };
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const short = (n) => String(n || "").split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|bin)$/i, "");
  const FAM_LABEL = { minimax_h3: "MiniMax H3", krea2: "Krea 2", qwen_image: "Qwen Image", flux2: "FLUX.2", flux1: "FLUX.1",
    moss: "MOSS", sdxl: "SDXL", sd15: "SD 1.5", ltx: "LTX", wan: "Wan", hunyuan: "Hunyuan", z_image: "Z-Image" };
  async function ensureIndex() {
    if (INDEX) return INDEX;
    try { const d = await api("/api/loras/index"); INDEX = d.items || []; FAMMAP = d.engine_family || {}; GENERIC = new Set(d.generic || []); LISTS = Object.assign({}, d); delete LISTS.items; delete LISTS.engine_family; delete LISTS.generic; } catch (e) { INDEX = []; }
    return INDEX;
  }
  function engineFam() {
    const a = window.__wfActive && window.__wfActive();
    if (!a) return state.media === "video" ? null : "minimax_h3";
    const c = a.card || {};
    return FAMMAP[c.family] || null;
  }
  const isOn = () => { try { return localStorage.getItem("cs.lora.auto") === "1"; } catch (e) { return false; } };
  let GENERIC = new Set();
let LISTS = {};   // the server's NSFW word lists (synonyms, sizes, undress words, sfw phrases) for LoraMatch
  // picks from the prompt (static/lora_match.js): whole phrases only, ordinary words never alone, "no X" respected,
  // one copy per LoRA, at most one style and one person, strength from the maker or the kind, lower when stacked
  function matches() {
    if (!isOn() || !INDEX || !window.LoraMatch) return [];
    const fam = engineFam();
    if (!fam) return [];
    const p = ($("prompt") && $("prompt").value || "");
    if (!p.trim()) return [];
    return window.LoraMatch.pick(p, INDEX, fam, Object.assign({}, LISTS, { dismissed, generic: GENERIC, max: loraMax() }));
  }
  window.__autoLoras = matches;

  function renderAuto() {
    const box = $("lora-auto-list");
    if (!box) return;
    const list = matches().filter((a) => !(window.__manualLoras ? window.__manualLoras() : []).includes(a.name));
    box.classList.toggle("hidden", !isOn());
    if (!isOn()) return;
    if (!engineFam()) { box.innerHTML = '<p class="pop-note">This engine\u2019s model family isn\u2019t known, so nothing is auto-picked.</p>'; return; }
    if (!list.length) { box.innerHTML = '<p class="pop-note">Nothing in your prompt matches a ' + esc(FAM_LABEL[engineFam()] || "") + " LoRA yet.</p>"; return; }
    box.innerHTML = '<p class="pop-label">Auto-picked from your prompt</p>';
    list.forEach((a) => {
      const row = document.createElement("div");
      row.className = "lora-row lora-auto";
      row.innerHTML = '<span class="lora-name"></span><span class="lora-why"></span>'
        + '<button type="button" class="chip-btn lora-keep">Keep</button><button type="button" class="icon-btn sm" aria-label="Don\u2019t use">\u2715</button>';
      row.querySelector(".lora-name").textContent = short(a.name);
      row.querySelector(".lora-name").title = a.name;
      row.querySelector(".lora-why").textContent = "\u201c" + a.why + "\u201d \u00b7 " + a.strength.toFixed(2)
        + (a.add_trigger ? " \u00b7 adds \u201c" + a.add_trigger + "\u201d" : "");
      row.querySelector(".lora-why").title = "Picked because your prompt says \u201c" + a.why + "\u201d. Strength "
        + a.strength.toFixed(2) + (a.add_trigger ? ". Its trigger word is added in front of the prompt sent to the model (your box isn\u2019t changed)." : ".");
      row.querySelector(".lora-keep").addEventListener("click", () => { window.__addLora && window.__addLora(a.name, a.strength); });
      row.querySelector(".icon-btn").addEventListener("click", () => { dismissed.add(a.group || a.name); renderAuto(); window.__syncLoraPill && window.__syncLoraPill(); });
      box.appendChild(row);
    });
  }
  window.__renderAutoLoras = renderAuto;

  // search: live results with folder and model
  function renderResults() {
    const box = $("lora-results");
    const q = ($("lora-search").value || "").trim().toLowerCase();
    if (!q || !INDEX) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    const fam = engineFam();
    const hits = INDEX.filter((it) => (it.name + " " + (it.triggers || []).join(" ")).toLowerCase().includes(q))
      .sort((a, b) => ((b.family === fam) - (a.family === fam)) || a.name.localeCompare(b.name)).slice(0, 40);
    box.classList.remove("hidden");
    if (!hits.length) { box.innerHTML = '<p class="pop-note">No LoRA matches \u201c' + esc(q) + "\u201d.</p>"; return; }
    box.innerHTML = "";
    hits.forEach((it) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lora-hit" + (fam && it.family && it.family !== fam ? " other" : "");
      b.setAttribute("role", "option");
      b.innerHTML = '<span class="lora-hit-name"></span><span class="lora-hit-meta"></span>';
      b.querySelector(".lora-hit-name").textContent = short(it.name);
      b.querySelector(".lora-hit-meta").textContent = [it.folder || "(top folder)", it.family ? FAM_LABEL[it.family] || it.family : "model unknown",
        (it.triggers || [])[0] ? "trigger: " + it.triggers[0] : ""].filter(Boolean).join(" \u00b7 ");
      b.title = it.name;
      b.addEventListener("click", () => { window.__addLora && window.__addLora(it.name); $("lora-search").value = ""; renderResults(); });
      box.appendChild(b);
    });
  }
  let t = 0;
  $("lora-search").addEventListener("input", async () => { await ensureIndex(); clearTimeout(t); t = setTimeout(renderResults, 120); });
  if ($("lora-max")) {
    $("lora-max").value = String(loraMax());
    $("lora-max").addEventListener("change", async () => {
      try { localStorage.setItem("cs.lora.max", $("lora-max").value); } catch (e) {}
      await ensureIndex(); renderAuto(); window.__syncLoraPill && window.__syncLoraPill();
    });
  }
  $("lora-auto").addEventListener("change", async () => {
    try { localStorage.setItem("cs.lora.auto", $("lora-auto").checked ? "1" : "0"); } catch (e) {}
    dismissed.clear();   // turning Auto-pick off and on again brings back the ones you crossed out
    await ensureIndex();
    renderAuto();
    window.__syncLoraPill && window.__syncLoraPill();
    toast($("lora-auto").checked ? "LoRAs are picked from your prompt (for this engine's model)" : "Auto LoRAs off");
  });
  $("lora-pill").addEventListener("click", async () => { $("lora-auto").checked = isOn(); await ensureIndex(); renderAuto(); });
  let pt = 0;
  $("prompt").addEventListener("input", () => {
    if (!isOn()) return;
    clearTimeout(pt);
    pt = setTimeout(async () => {
      await ensureIndex();
      if (!($("prompt").value || "").trim()) dismissed.clear();
      window.__syncLoraPill && window.__syncLoraPill();
      const pop = $("lora-pop");
      if (pop && !pop.hidden) renderAuto();
    }, 400);
  });
  const base = window.onEngineChanged;
  window.onEngineChanged = function () {
    if (typeof base === "function") base.apply(this, arguments);
    setTimeout(() => { window.__syncLoraPill && window.__syncLoraPill(); }, 150);
  };
  if (isOn()) ensureIndex().then(() => window.__syncLoraPill && window.__syncLoraPill());
})();

/* ===== Queue panel controls v20261112t ===== */
(function () {
  if (!$("queue-cancel-all")) return;
  $("queue-cancel-all").addEventListener("click", async () => {
    const waiting = (state.activeJobs || []).filter((j) => j && (j.status === "queued" || j.status === "waiting_comfy"));
    if (!waiting.length) { toast("Nothing waiting"); return; }
    if (!window.confirm("Cancel the " + waiting.length + " waiting job" + (waiting.length === 1 ? "" : "s") + "? The one generating now keeps going.")) return;
    for (const j of waiting) {
      try { await api("/api/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: j.id }) }); j.status = "cancelled"; } catch (e) {}
    }
    renderJobQueue();
    pollJobs();
    toast("Cancelled " + waiting.length);
  });
  if ($("queue-close")) $("queue-close").addEventListener("click", () => hidePops());
})();


/* ===== "This engine" guide in the ? pill v20261112w =====
   What the loaded engine is for, how to prompt it, and its practical tips. Built-in engines have written guides;
   an uploaded workflow gets one made from what the app read in it. */
(function () {
  const G = {
    builtin_t2i: { what: "MiniMax H3 Image: photoreal or graphic pictures from a description.",
      how: "Describe the whole picture in plain sentences: subject, what they're doing, place, light, camera. Short ideas work too: switch on Enhance and it writes the full description for you.",
      tips: ["6 steps, CFG 1 is the tuned setting; 8 steps adds a little detail.", "Up to 4 MP; use the size pill for the shape."] },
    builtin_edit: { what: "MiniMax H3 Image edit: changes a picture you add, or combines several (up to 16).",
      how: "Say the change and name pictures as <Picture 1>, <Picture 2> (Image 1, img1 and @Image1 work too). One change per run is most reliable.",
      tips: ["Inpaint (paint an area) limits the change to that area.", "Mark labels parts of the picture so the prompt can point at them.", "Keep clothing (Settings) stops edits from changing what people wear."] },
    krea2_t2i: { what: "Krea 2 Turbo: realistic pictures with strong aesthetics.",
      how: "Natural photographic language in 1-3 rich sentences: subject, setting, light quality, texture, lens and mood. No tag lists.",
      tips: ["8 steps, CFG 1, euler / simple.", "Raw model: 28 steps, CFG 3.5 for more detail."] },
    krea2_edit: { what: "Krea 2 Edit: edits a picture from one plain instruction; a second picture can be a person to place into it.",
      how: "Write the edit as one short command: “Turn her to a left-facing side profile, full body.” “Change her outfit to a red raincoat.”",
      tips: ["Auto picks Identity Edit (pose, angle, new place) or General Edit (outfits, removals, background, light) from your words.",
        "Size “Original” is best for pose changes; a picked size up to 2 MP also works.",
        "Keep face (Settings) adds a line that holds the face; full masks and helmets automatically hide the hair.",
        "Removals clean up best on the Raw model.",
        "Head swap: add the body picture first, then the head picture, and say “head swap” (needs the BFS Head Swap LoRA). “Body swap” puts the whole person from picture 2 in."] },
    krea2_paint: { what: "Krea 2 Paint: repaints only the area you paint with Inpaint; the rest stays pixel for pixel.",
      how: "Describe the finished picture, not the change: “a sunny garden lawn with a small wooden bench”.",
      tips: ["Paint a little past the edges of what should change.", "8 steps, CFG 1."] },
    krea2_style: { what: "Krea 2 style from pictures: a new picture in the look of 1-2 pictures you add.",
      how: "Describe the new subject and scene; the pictures supply the style.", tips: [] },
    klein9b_t2i: { what: "FLUX.2 Klein 9B: fast, detailed pictures.",
      how: "Concrete prose with the main subject first, then setting, light, materials, camera and style. 40-120 words. No keyword lists.", tips: [] },
    klein9b_edit: { what: "FLUX.2 Klein 9B edit: edits with one or more reference pictures.",
      how: "Describe the change and refer to the pictures as image 1, image 2.", tips: [] },
    moss_t2i: { what: "MOSS Image: reads long scene captions very closely.",
      how: "Write a full scene caption: every subject with position, clothing and pose, then the setting, light and camera.",
      tips: ["KroMOSS / Base: 50 steps, CFG 3, euler. SFT: 25 steps, CFG 2.5. With the 8-step DMD2 LoRA: 8 steps, CFG 1.", "1-2 MP is its range."] },
    qwen21_t2i: { what: "Qwen Image 2.1: great with long prompts and text in pictures.",
      how: "One long flowing paragraph from foreground to background. Put any text that must appear in double quotes.", tips: [] },
    qwen21_edit: { what: "Qwen Image 2.1 edit: edits with up to 4 pictures.",
      how: "Say the change and refer to Picture 1, Picture 2. Text to change goes in double quotes.", tips: [] },
    qwen_edit2509: { what: "Qwen Image Edit 2509: precise edits with up to 3 pictures.",
      how: "Say the change plainly and name the pictures (Picture 1...).", tips: ["Slower: 20 steps."] },
    h3_video: { what: "MiniMax H3 video: clips with sound from text, a start picture or references.",
      how: "Describe what happens over time: the action, how the camera moves, and the sound. For a start picture, describe the motion rather than how things look.",
      tips: ["Reference to video: name the pictures as <Picture 1>...", "Long video joins several clips seamlessly."] },
    ltx23: { what: "LTX 2.3 video: image to video, text to video, first + last frame and style transitions.",
      how: "One paragraph about the motion and camera; for first + last frame, describe how the scene gets from one to the other.", tips: [] },
  };
  function guideFor() {
    const a = window.__wfActive && window.__wfActive();
    if (!a) return Object.assign({ name: "MiniMax H3 (built-in)" }, state.mode === "edit" ? G.builtin_edit : G.builtin_t2i);
    const c = a.card || {}, id = c.id || "";
    let g = G[id] || (/^h3_/.test(id) ? G.h3_video : /^ltx23_/.test(id) ? G.ltx23 : null);
    if (!g && c.family === "MOSS") g = G.moss_t2i;
    if (g) return Object.assign({ name: c.name }, g);
    // an uploaded workflow: from what the app read in it
    const s = c.slots || {};
    const TASK = { t2i: "makes pictures from text", edit: "edits the pictures you add", t2v: "makes videos from text",
      i2v: "turns a picture into a video", r2v: "makes a video from reference pictures", v2v: "edits or drives a video you add" };
    const needs = [];
    if (c.picture_min) needs.push(c.picture_min + " picture" + (c.picture_min === 1 ? "" : "s"));
    if (c.video_min) needs.push(c.video_min + " video" + (c.video_min === 1 ? "" : "s"));
    if (c.audio_min) needs.push(c.audio_min + " audio clip" + (c.audio_min === 1 ? "" : "s"));
    const ctl = ["prompt", "negative", "width", "seed", "steps", "cfg", "length", "models"].filter((k) => (s[k] || []).length)
      .map((k) => ({ prompt: "prompt", negative: "negative prompt", width: "size", seed: "seed", steps: "steps", cfg: "CFG", length: "length", models: "models" })[k]);
    return { name: c.name, what: (c.family || "This workflow") + " (uploaded): " + (TASK[c.task] || "runs your workflow") + ".",
      how: (needs.length ? "Add " + needs.join(" and ") + " with +, then write" : "Write") + " the prompt the way this model's own guide suggests.",
      tips: ["The app controls: " + (ctl.join(", ") || "the prompt") + ".", "Settings → Manual lists every other value in the workflow.",
        c.sampling_note || "Steps and CFG start at the workflow's own values."] };
  }
  const base = renderHelp;
  renderHelp = function () {
    base.apply(this, arguments);
    const box = $("help-engine");
    if (!box) return;
    const g = guideFor();
    const esc = (t) => String(t || "").replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[ch]);
    box.innerHTML = '<p class="pop-label">This engine · ' + esc(g.name) + "</p>"
      + "<p class=\"help-what\">" + esc(g.what) + "</p>"
      + "<p class=\"help-how\"><b>How to prompt:</b> " + esc(g.how) + "</p>"
      + ((g.tips || []).length ? "<ul class=\"help-list\">" + g.tips.map((t) => "<li>" + esc(t) + "</li>").join("") + "</ul>" : "")
      + '<p class="pop-label help-general">General tips</p>';
  };
})();


/* ===== Auto understanding: your additions v20261112x ===== */
(function () {
  if (!$("und-group")) return;
  let scope = "engine", data = { all: {}, engine: {} };
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const eng = () => (!state.engine || state.engine === "none" ? "builtin" : state.engine);
  function cur() { return (scope === "all" ? data.all : data.engine) || {}; }
  function addRow(from, to) {
    const row = document.createElement("div");
    row.className = "und-row";
    row.innerHTML = '<input type="text" class="und-from" placeholder="word" aria-label="Word" value="' + esc(from) + '">'
      + '<span class="und-arrow" aria-hidden="true">\u2192</span>'
      + '<input type="text" class="und-to" placeholder="what it means" aria-label="What it means" value="' + esc(to) + '">'
      + '<button type="button" class="icon-btn sm" aria-label="Remove">\u2715</button>';
    row.querySelector("button").addEventListener("click", () => row.remove());
    $("und-words").appendChild(row);
  }
  function render() {
    document.querySelectorAll("#und-scope button").forEach((b) => b.classList.toggle("is-on", b.dataset.und === scope));
    const c = cur();
    $("und-words").innerHTML = "";
    (c.words || []).forEach((w) => addRow(w.from, w.to));
    if (!(c.words || []).length) addRow("", "");
    $("und-extra").value = c.extra || "";
    const n = ((data.all.words || []).length + (data.all.extra ? 1 : 0)) + ((data.engine.words || []).length + (data.engine.extra ? 1 : 0));
    $("und-badge").textContent = n ? "yours" : "";
    $("und-revert").disabled = !((c.words || []).length || c.extra);
  }
  async function load() {
    try { data = await api("/api/understanding?engine=" + encodeURIComponent(eng())); } catch (e) { data = { all: {}, engine: {} }; }
    render();
  }
  async function send(body) {
    try {
      await api("/api/understanding", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ engine: eng(), scope }, body)) });
      await load();
      return true;
    } catch (e) { showError(e.message || "Couldn't save that."); return false; }
  }
  $("und-add-word").addEventListener("click", () => addRow("", ""));
  $("und-save").addEventListener("click", async () => {
    const words = [...document.querySelectorAll("#und-words .und-row")].map((r) => ({
      from: r.querySelector(".und-from").value.trim(), to: r.querySelector(".und-to").value.trim() })).filter((w) => w.from && w.to);
    if (await send({ words, extra: $("und-extra").value })) toast("Saved for " + (scope === "all" ? "all engines" : "this engine"));
  });
  $("und-revert").addEventListener("click", async () => {
    if (!window.confirm("Remove your additions for " + (scope === "all" ? "all engines" : "this engine") + "? The built-in understanding stays as shipped.")) return;
    if (await send({ reset: true })) toast("Back to the original understanding");
  });
  document.querySelectorAll("#und-scope button").forEach((b) => b.addEventListener("click", () => { scope = b.dataset.und; render(); }));
  const pop = $("settings-pop");
  if (pop) new MutationObserver(() => { if (!pop.hidden && !pop.classList.contains("hidden")) load(); })
    .observe(pop, { attributes: true, attributeFilter: ["hidden", "class"] });
})();

/* ===== Node packs the built-in engines need (Library page) v20261112za ===== */
(function () {
  const list = $("wf-list");
  if (!list) return;
  const box = document.createElement("div");
  box.id = "np-banner";
  box.className = "np-banner hidden";
  list.parentNode.insertBefore(box, list);
  let timer = 0;
  async function refresh() {
    let d;
    try { d = await api("/api/nodes/required"); } catch (e) { return; }
    const miss = (d.packs || []).filter((p) => !p.installed);
    const waiting = (d.packs || []).filter((p) => p.installed && !p.loaded);
    if (!miss.length && !(d.comfy_up && waiting.length)) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    const esc = (t) => String(t || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
    if (!miss.length) {
      box.innerHTML = '<p class="np-title">Node packs installed</p><p class="pop-note">Restart ComfyUI so it loads: '
        + waiting.map((p) => esc(p.folder)).join(", ") + ".</p>";
      return;
    }
    box.innerHTML = '<p class="np-title">' + miss.length + " node pack" + (miss.length === 1 ? "" : "s") + " missing</p>"
      + '<p class="pop-note">Some engines need these ComfyUI node packs. Comfy Studio installs the exact versions it was tested with.</p>'
      + '<ul class="np-list">' + miss.map((p) => "<li><b>" + esc(p.folder) + "</b> · " + esc(p.for) + "</li>").join("") + "</ul>"
      + '<div class="np-actions"><button type="button" class="chip-btn primary" id="np-install">Install ' + (miss.length === 1 ? "it" : "all " + miss.length) + '</button>'
      + '<span class="np-status" id="np-status"></span></div>';
    $("np-install").addEventListener("click", install);
  }
  async function install() {
    const b = $("np-install");
    if (b) b.disabled = true;
    try {
      const r = await api("/api/nodes/required", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      poll(r.job);
    } catch (e) { showError(e.message || "Couldn't start the install."); if (b) b.disabled = false; }
  }
  async function poll(id) {
    clearTimeout(timer);
    let j;
    try { j = await api("/api/nodes/job?id=" + encodeURIComponent(id)); } catch (e) { return; }
    if ($("np-status")) $("np-status").textContent = j.message || "Installing…";
    if (j.state === "running") { timer = setTimeout(() => poll(id), 1500); return; }
    toast(j.message || "Done");
    refresh();
  }
  new MutationObserver(() => { if (document.body.classList.contains("view-workflows")) refresh(); })
    .observe(document.body, { attributes: true, attributeFilter: ["class"] });
  window.__refreshNodePacks = refresh;
})();


/* ===== Remember the tools between sessions + Restore defaults v20261112za =====
   Saved: mode, picture size / shape, camera, Enhance (on/off + Fast/Quality), Auto understanding, reference mode,
   seed (random or the number), the built-in engine's model picks. Engine choice, video length / resolution / shape,
   Auto/Manual, Manual values, LoRAs and workflow model picks were already saved by their own parts of the app.
   Never saved: the prompt, the negative prompt, pictures, Reuse settings. */
(function () {
  const INPUTS = ["pe", "auto-assist", "random-seed", "seed", "unet_name", "clip_name", "vae_name", "pe_clip_name"];
  let ready = false, last = "";
  function snapshot() {
    const s = {}, inp = {};
    REMEMBER_STATE.forEach((k) => { s[k] = state[k]; });
    INPUTS.forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (el.type === "checkbox") inp[id] = el.checked;
      else if (el.tagName === "SELECT" && !el.options.length) return;     // list not filled yet: keep what was saved
      else inp[id] = el.value;
    });
    return { v: 1, state: s, inputs: inp };
  }
  function save() {
    if (!ready) return;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem("cs.remember") || "{}") || {}; } catch (e) {}
    const snap = snapshot();
    snap.inputs = Object.assign({}, saved.inputs || {}, snap.inputs);
    const t = JSON.stringify(snap);
    if (t === last) return;
    last = t;
    try { localStorage.setItem("cs.remember", t); } catch (e) {}
  }
  window.__restoreRemembered = function (keepSize) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("cs.remember") || "null"); } catch (e) {}
    if (keepSize && saved && saved.state) Object.assign(state, keepSize);   // setMode may have reset the size
    if (state.mode === "edit") {          // an edit always starts at Picture 1's own size
      state.quality = "picture1";
      state.p1Original = true;
    }
    const inp = (saved && saved.inputs) || {};
    Object.keys(inp).forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (el.type === "checkbox") {
        if (el.checked !== !!inp[id]) { el.checked = !!inp[id]; el.dispatchEvent(new Event("change", { bubbles: true })); }
      } else if (el.tagName === "SELECT") {
        if ([...el.options].some((o) => o.value === inp[id]) && el.value !== inp[id]) {
          el.value = inp[id]; el.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } else if (inp[id] !== "" && el.value !== String(inp[id])) {
        el.value = inp[id]; el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    try { if (typeof renderAspects === "function") renderAspects(); if (typeof renderCustomMp === "function") renderCustomMp();
      if (typeof renderResMenu === "function") renderResMenu(); if (typeof scheduleOutputSize === "function") scheduleOutputSize();
      if (typeof syncPills === "function") syncPills(); } catch (e) {}
    ready = true;
    save();
  };
  setInterval(save, 2000);
  document.addEventListener("change", () => setTimeout(save, 50), true);
  document.addEventListener("click", () => setTimeout(save, 50), true);
  window.addEventListener("pagehide", save);
  document.addEventListener("visibilitychange", () => { if (document.hidden) save(); });

  // Settings -> Restore defaults: every tool back to how the app ships (your pictures, library, words and
  // Enhancer instructions are kept; those have their own Revert buttons)
  const btn = $("tools-reset");
  if (btn) btn.addEventListener("click", () => {
    const go = () => {
      ready = false;
      try {
        Object.keys(localStorage).filter((k) => k.indexOf("cs.") === 0 || k === "ipSplit").forEach((k) => localStorage.removeItem(k));
      } catch (e) {}
      location.reload();
    };
    if (typeof askConfirm === "function") askConfirm({
      title: "Restore every tool to its default?",
      body: "Engine choices, sizes, Enhance, Auto/Manual values, LoRAs, workflow settings and model picks go back to how the app ships, for all workflows. Your pictures, videos and library stay.",
      yes: "Restore defaults", onYes: go,
    });
    else if (window.confirm("Restore every tool to its default for all workflows?")) go();
  });
})();


/* ===== Enhance: token dial (Auto or a fixed limit) v20261112zc =====
   Same rules as pe.py: the limit covers Quality's thinking and its answer; below 4096 (new picture) or 512 (edit)
   Quality writes directly instead of thinking first, so it can't run out of room. */
(function () {
  const dial = $("pe-tokens");
  if (!dial) return;
  const STEPS = [256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192];
  const THINK_MIN = { t2i: 4096, edit: 512 };
  function autoTokens() {
    const words = String(($("prompt") && $("prompt").value) || "").trim().split(/\s+/).filter(Boolean).length;
    const edit = state.mode === "edit", n = (state.images || []).length;
    if (state.peMode !== "quality") return (words > 40 || (edit && n > 2)) ? 512 : 384;
    if (edit) return n > 2 ? 768 : 512;
    return 4096;
  }
  function show() {
    const i = Math.max(0, STEPS.indexOf(Number(state.peTokens)) + 1);
    dial.value = String(i);
    const n = i ? STEPS[i - 1] : autoTokens();
    const thinks = state.peMode === "quality" && n >= THINK_MIN[state.mode === "edit" ? "edit" : "t2i"];
    $("pe-tok-val").textContent = (i ? n + " tokens" : "Auto \u00b7 " + n) + (state.peMode === "quality" ? (thinks ? " \u00b7 thinks first" : " \u00b7 writes directly") : "");
  }
  dial.addEventListener("input", () => {
    const i = Number(dial.value);
    state.peTokens = i ? STEPS[i - 1] : null;
    show();
  });
  document.querySelectorAll("#pe-modes button").forEach((b) => b.addEventListener("click", () => setTimeout(show, 0)));
  if ($("prompt")) $("prompt").addEventListener("input", () => { if (!state.peTokens) show(); });
  const pop = $("quality-pop") || $("pe-modes").closest(".pop");
  if (pop) new MutationObserver(show).observe(pop, { attributes: true, attributeFilter: ["hidden", "class"] });
  window.__showPeTokens = show;
  show();
})();


/* ===== Keep image quality switch v20261112zh =====
   On by default. Edits get one line (in the engine's own wording) that keeps the original picture's quality, texture,
   colours and light; the server leaves out what the prompt itself asks to change. */
(function () {
  const cb = $("keep-quality");
  if (!cb) return;
  try { cb.checked = localStorage.getItem("cs.keepquality") !== "0"; } catch (e) { cb.checked = true; }
  cb.addEventListener("change", () => {
    try { localStorage.setItem("cs.keepquality", cb.checked ? "1" : "0"); } catch (e) {}
    toast(cb.checked ? "Edits keep the original image quality" : "Image quality guard off");
  });
})();


/* ===== Auto decides the prompt helpers v20261112zj =====
   Auto: Keep clothing / Keep image quality / Keep face are chosen for each instruction by the server (a removal
   gets nothing extra, a face edit doesn't lock the face, everything else gets them). Manual: the ticks apply.
   In Auto the ticks are shown greyed with a note, so it's clear they aren't what decides. */
(function () {
  const groups = ["keep-cloth-group", "keep-quality-group", "krea-keepface-group"];
  function sync() {
    const auto = tuneMode() === "auto";
    groups.forEach((id) => {
      const g = $(id);
      if (!g) return;
      g.querySelectorAll("input[type=checkbox]").forEach((cb) => { cb.disabled = auto; });
      g.classList.toggle("auto-decides", auto);
      let n = g.querySelector(".auto-note");
      if (!n) {
        n = document.createElement("p");
        n.className = "pop-note auto-note";
        n.textContent = "Auto: chosen for each instruction (a removal gets nothing extra; a face edit doesn't lock the face). Switch to Manual to set it yourself.";
        const label = g.querySelector(".check-row");
        if (label && label.nextSibling) g.insertBefore(n, label.nextSibling); else g.appendChild(n);
      }
      n.classList.toggle("hidden", !auto);
    });
  }
  document.querySelectorAll("#tune-seg button").forEach((b) => b.addEventListener("click", () => setTimeout(sync, 0)));
  const pop = $("settings-pop");
  if (pop) new MutationObserver(() => { if (!pop.hidden && !pop.classList.contains("hidden")) sync(); })
    .observe(pop, { attributes: true, attributeFilter: ["hidden", "class"] });
  window.__syncAutoGuards = sync;
  sync();
})();


/* ===== Clear the prompt v20261112zl =====
   A small "Clear" chip in the bottom bar, after Create / Edit and well away from Send, shown only while there is
   text. Clearing keeps an Undo (in the same spot) for 6 s, so a mis-tap costs nothing. */
(function () {
  const box = $("prompt"), btn = $("prompt-clear"), undo = $("prompt-undo");
  if (!box || !btn || !undo) return;
  let saved = "", timer = 0;
  function sync() {
    // Clear shows while there is text; Undo takes its place for a few seconds after clearing
    btn.classList.toggle("hidden", !box.value.length || !undo.classList.contains("hidden"));
  }
  function fire() { box.dispatchEvent(new Event("input", { bubbles: true })); }
  btn.addEventListener("mousedown", (e) => e.preventDefault());       // keep the keyboard open on phones
  btn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!box.value) return;
    saved = box.value;
    box.value = "";
    fire(); sync();
    box.focus();
    undo.classList.remove("hidden");
    clearTimeout(timer);
    timer = setTimeout(() => { undo.classList.add("hidden"); saved = ""; sync(); }, 6000);
  });
  undo.addEventListener("mousedown", (e) => e.preventDefault());
  undo.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!saved) return;
    box.value = saved;
    saved = "";
    clearTimeout(timer);
    undo.classList.add("hidden");
    fire(); sync();
    box.focus();
    try { box.setSelectionRange(box.value.length, box.value.length); } catch (err) {}
  });
  box.addEventListener("input", () => { sync(); if (box.value && !undo.classList.contains("hidden") && saved && box.value !== saved) { /* typing again: keep Undo until it times out */ } });
  // text set by the app itself (Reuse, Edit this, Restore) doesn't fire "input": check now and then
  setInterval(sync, 800);
  // Where it sits: on a PC, in the bottom bar after Create / Edit (far from Send). On a phone the bar has no room
  // (it would push Send off the screen), so it moves to the start of the pill row above the prompt box.
  const bar = btn.parentNode, grow = bar.querySelector(".grow"), hud = $("hud-scroller");
  const narrow = window.matchMedia("(max-width: 480px)");
  function place() {
    if (narrow.matches && hud) {
      if (btn.parentNode !== hud) { hud.insertBefore(undo, hud.firstChild); hud.insertBefore(btn, hud.firstChild); }
    } else if (btn.parentNode !== bar) {
      bar.insertBefore(btn, grow); bar.insertBefore(undo, grow);
    }
    btn.classList.toggle("in-hud", btn.parentNode === hud);
    undo.classList.toggle("in-hud", undo.parentNode === hud);
  }
  if (narrow.addEventListener) narrow.addEventListener("change", place); else if (narrow.addListener) narrow.addListener(place);
  place();
  sync();
})();

/* ===== Edits start at Picture 1's own size v20261112zn =====
   Switching to Edit resets the size to Match Picture 1 > Original (a new-picture size like 16:9 12 MP would make the
   model redraw the whole photo). You can still pick another size for the edit afterwards. */
(function () {
  const base = setMode;
  setMode = function (mode) {
    const was = state.mode;
    const out = base.apply(this, arguments);
    if (mode === "edit" && was !== "edit") {
      state.quality = "picture1";
      state.p1Original = true;
      try { if (typeof renderResMenu === "function") renderResMenu(); if (typeof scheduleOutputSize === "function") scheduleOutputSize();
            if (typeof syncPills === "function") syncPills(); } catch (e) {}
    }
    return out;
  };
})();


/* ===== Warn when an edit would be much bigger than Picture 1 v20261112zo =====
   An edit made at several times Picture 1's pixels makes the model invent most of the picture, so even "remove the
   cup" comes back as a redrawn image. Shown under the prompt box (and in the size panel) with a one-tap fix. */
(function () {
  const note = $("size-warn");
  if (!note) return;
  const txt = note.querySelector(".size-warn-text"), fix = note.querySelector("button");
  window.__sizeWarn = function (info, img) {
    let show = false;
    if (state.mode === "edit" && img && img.width && img.height && info && info.width && info.height) {
      const ratio = (info.width * info.height) / (img.width * img.height);
      if (ratio >= 3) {
        show = true;
        const inMp = Math.max(0.1, Math.round(img.width * img.height / 1e5) / 10);
        txt.textContent = "Picture 1 is " + inMp + " MP and this edit would be " + (Math.round(info.width * info.height / 1e5) / 10)
          + " MP (" + Math.round(ratio) + "\u00d7 the pixels), so the model redraws most of it. For removals and small changes use Original, then Upscale.";
        const out = $("p1-output");
        if (out && out.textContent.indexOf("\u00d7 Picture 1") < 0) out.textContent += " \u2014 " + Math.round(ratio) + "\u00d7 Picture 1's pixels.";
      }
    }
    note.classList.toggle("hidden", !show);
  };
  fix.addEventListener("click", () => {
    if (typeof setP1Original === "function") setP1Original(true);
    else { state.p1Original = true; if (typeof scheduleOutputSize === "function") scheduleOutputSize(); }
    if (state.quality !== "picture1") state.quality = "picture1";
    if (typeof scheduleOutputSize === "function") scheduleOutputSize();
    if (typeof syncPills === "function") syncPills();
    note.classList.add("hidden");
    toast("Size: Match Picture 1 \u203a Original");
  });
  // leaving Edit hides it
  const base = setMode;
  setMode = function (mode) { const r = base.apply(this, arguments); if (mode !== "edit") note.classList.add("hidden"); return r; };
})();
