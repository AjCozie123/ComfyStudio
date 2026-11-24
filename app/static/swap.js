/* Character swap (MiniMax H3 Image + BFS swap LoRA) v20261124
   The Swap pill + sheet: choose who in Picture 1 is replaced by Picture 2 (3, 4), what happens to their body, the
   size, and (Manual) every setting of the workflow. Works with the normal Generate button:
     Auto   - the app swaps when your words ask for it ("swap her with picture 2", "put pic 2 in her place" ...)
     Always - every edit with 2+ pictures is a swap
     Off    - never (your words are sent to the engine you picked)
   Server: /api/swap/status, /api/swap/people, /api/swap/check; char_swap.py. */
(function () {
  "use strict";
  const CARD = "h3_char_swap";
  const COLORS = [
    { key: "1", name: "red", hex: "#e5484d", label: "Person 1" },
    { key: "2", name: "blue", hex: "#3e8ef7", label: "Person 2" },
    { key: "3", name: "green", hex: "#30a46c", label: "Person 3" },
  ];
  const S = {
    status: null,            // /api/swap/status
    people: null,            // /api/swap/people result for picKey
    picKey: "",              // Picture 1 name + size the people were found for
    picks: {},               // {"1": "a1", ...}  (only when YOU picked; empty = the app chooses)
    picksMp: null,
    color: "1",
    busy: false,
    wordsSwap: false,        // the prompt asks for a swap
    masks: {},               // id -> {img, canvas}
  };
  const el = (id) => document.getElementById(id);
  const ls = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

  function routeMode() { const m = ls("cs.swap.mode", "auto"); return m === "on" || m === "off" ? m : "auto"; }
  function settings() {
    const d = (S.status && S.status.defaults) || { body: "auto", mp: 2, guide: "hatching", lora: 1, steps: 6, margin: 0,
      feather: 0.01, clean: 0.5, hatch_width: 1, hatch_spacing: 1, threshold: 0.4 };
    return Object.assign({}, d, { body: "auto" }, ls("cs.swap.settings", {}));
  }
  function setSetting(k, v) { const s = ls("cs.swap.settings", {}); s[k] = v; lsSet("cs.swap.settings", s); }
  function isManual() { return typeof tuneMode === "function" && tuneMode() === "manual"; }
  function activeCardId() { const a = window.__wfActive && window.__wfActive(); return a && a.card ? a.card.id : ""; }
  function pics() { return (typeof state !== "undefined" && state.images) || []; }
  function editMode() { return typeof state !== "undefined" && state.mode === "edit"; }
  function hasPicks() { const k = pics()[0] ? pics()[0].name : ""; return !!k && S.picKey.startsWith(k + "|") && Object.keys(S.picks).length > 0; }
  function swapWillRun() {
    if (!editMode() || pics().length < 2) return false;
    if (activeCardId() === CARD) return true;
    const m = routeMode();
    return m === "on" || (m === "auto" && (S.wordsSwap || hasPicks()));
  }
  window.__swapOn = () => swapWillRun() && (activeCardId() === CARD || routeMode() === "on");

  /* ---------------- what goes to the server with Generate ---------------- */
  window.__swapAttach = function (body) {
    if (!editMode()) return;
    const m = routeMode();
    if (m === "off" && activeCardId() !== CARD) { body.swap_route = false; return; }
    const s = settings();
    const key = pics()[0] ? pics()[0].name : "";
    const mine = S.picKey.startsWith(key + "|") ? S.picks : {};
    body.swap_mp = Number(s.mp) || 2;
    body.swap = {
      // you chose who to swap in the sheet: that is a swap, whatever the words say
      on: m === "on" || activeCardId() === CARD || (pics().length >= 2 && Object.keys(mine).length > 0),
      manual: isManual(),
      picks: Object.assign({}, mine),
      picks_mp: Object.keys(mine).length ? S.picksMp : null,
      people: S.people && Object.keys(mine).length ? S.people.people.map((p) => ({ id: p.id, box: p.box })) : [],
      settings: Object.assign({}, s, { body: s.body === "auto" ? "" : s.body }),
      own_prompt: isManual() && !!ls("cs.swap.ownPrompt", false),
      prompt: isManual() ? (ls("cs.swap.ownText", "") || "") : "",
    };
  };

  /* ---------------- the pill ---------------- */
  function pillText() {
    const n = pics().length;
    if (!editMode()) return "";
    if (activeCardId() === CARD) return n < 2 ? "Add Picture 2" : picksLabel();
    const m = routeMode();
    if (m === "off") return "Off";
    if (n < 2) return m === "on" ? "Add Picture 2" : "Auto";
    if (m === "on" || S.wordsSwap || hasPicks()) return picksLabel();
    return "Auto";
  }
  function picksLabel() {
    const k = Object.keys(S.picks).length;
    const key = pics()[0] ? pics()[0].name : "";
    if (k && S.picKey.startsWith(key + "|")) return k === 1 ? "1 person" : k + " people";
    return "On · app picks";
  }
  function renderPill() {
    const p = el("swap-pill");
    if (!p) return;
    const show = editMode() && (pics().length >= 2 || activeCardId() === CARD || S.wordsSwap);
    p.classList.toggle("hidden", !show);
    el("swap-pill-label").textContent = pillText();
    p.classList.toggle("is-on", swapWillRun());
    renderHint();
  }

  function renderHint() {
    const h = el("swap-hint");
    if (!h) return;
    const n = pics().length;
    let txt = "", btn = "";
    if (editMode() && S.wordsSwap && routeMode() !== "off" && n < 2) {
      txt = "Sounds like a character swap. Add the new person as Picture 2 (tap +), then press Generate.";
    } else if (swapWillRun()) {
      const k = Object.keys(S.picks).length && S.picKey.startsWith((pics()[0] || {}).name + "|");
      txt = "Character swap: " + (n >= 3 ? "Pictures 2" + (n >= 4 ? "–4" : " and 3") + " replace people" : "Picture 2 replaces a person")
        + " in Picture 1. " + (k ? "You chose who." : "The app picks who (biggest person, or the one your words point to).");
      btn = "Choose who";
    }
    h.classList.toggle("hidden", !txt);
    el("swap-hint-text").textContent = txt;
    const b = el("swap-hint-btn");
    b.classList.toggle("hidden", !btn);
    b.textContent = btn;
  }

  let checkT = 0, lastChecked = "";
  function checkWords() {
    clearTimeout(checkT);
    checkT = setTimeout(async () => {
      const t = (el("prompt") || {}).value || "";
      if (t === lastChecked) return;
      lastChecked = t;
      if (!t.trim()) { S.wordsSwap = false; renderPill(); return; }
      try {
        const r = await fetch("/api/swap/check?prompt=" + encodeURIComponent(t.slice(0, 600)) + "&n=" + pics().length);
        const d = await r.json();
        S.wordsSwap = !!d.swap;
        if (d.body === "keep" && settings().body === "auto") S.autoBody = "keep"; else S.autoBody = "";
      } catch (e) { S.wordsSwap = false; }
      renderPill();
    }, 350);
  }

  /* ---------------- the sheet ---------------- */
  async function loadStatus() {
    try { S.status = await (await fetch("/api/swap/status")).json(); } catch (e) { S.status = null; }
    return S.status;
  }

  async function openSheet() {
    if (typeof hidePops === "function") hidePops();
    el("swap-sheet").classList.remove("hidden");
    renderSheet();
    await loadStatus();
    renderSheet();
    if (pics()[0] && (!S.status || S.status.ready)) findPeople(false);
  }
  function closeSheet() { el("swap-sheet").classList.add("hidden"); renderPill(); }
  window.closeSwapSheet = closeSheet;
  window.openSwapSheet = openSheet;

  function renderSheet() {
    const st = S.status;
    // setup banner
    const setup = el("swap-setup");
    if (st && !st.ready) {
      setup.classList.remove("hidden");
      el("swap-setup-text").textContent = st.comfy === false
        ? "ComfyUI isn't running. Press Generate once (the app starts it) or start ComfyUI, then open this again."
        : "One-time setup needed: " + (st.missing || []).join(", ") + ". Press Set up: the app downloads and installs everything for you.";
      el("swap-setup-go").classList.toggle("hidden", st.comfy === false);
    } else setup.classList.add("hidden");
    // route mode
    document.querySelectorAll("#swap-route button").forEach((b) => b.classList.toggle("is-on", b.dataset.route === routeMode()));
    el("swap-route-note").textContent = {
      auto: "Swaps only when your words ask for it, e.g. “swap her with picture 2” or “put pic 2 in her place”.",
      on: "Every edit with two or more pictures is a character swap. You don't need to type anything.",
      off: "Never swaps. Your words go to the engine you picked, as usual.",
    }[routeMode()];
    if (activeCardId() === CARD) el("swap-route-note").textContent = "Character swap is your engine, so every Generate is a swap.";
    renderColors();
    renderStage();
    // body
    const s = settings();
    const body = s.body === "auto" ? (S.autoBody || "new") : s.body;
    document.querySelectorAll("#swap-body .opt-card").forEach((c) => c.classList.toggle("is-on", c.dataset.body === body));
    el("swap-body-note").textContent = s.body === "auto"
      ? (S.autoBody === "keep" ? "Chosen from your words (“keep her figure”). Tap one to decide yourself." : "Auto: the new person's body, unless your words say to keep the figure.")
      : "Your choice. Tap the selected one again to go back to Auto.";
    // size
    document.querySelectorAll("#swap-size button").forEach((b) => b.classList.toggle("is-on", Number(b.dataset.mp) === Number(s.mp)));
    // manual
    const man = isManual();
    el("swap-manual").classList.toggle("hidden", !man);
    el("swap-auto-note").classList.toggle("hidden", man);
    if (man) renderManual();
  }

  function renderColors() {
    const box = el("swap-colors");
    box.innerHTML = "";
    let more = 0;
    COLORS.forEach((c, i) => {
      const ref = pics()[i + 1];
      if (!ref && i > 0) { more++; return; }       // unused people: one short line below instead of empty rows
      const b = document.createElement("button");
      b.type = "button";
      b.className = "swap-color" + (S.color === c.key ? " is-on" : "") + (ref ? "" : " is-off");
      b.style.setProperty("--c", c.hex);
      const thumb = ref ? '<img alt="" src="' + (ref.thumb || ref.url || "") + '">' : '<span class="swap-color-empty">+</span>';
      const who = S.picks[c.key] ? "chosen" : "not chosen yet";
      b.innerHTML = '<span class="swap-dot"></span><span class="swap-color-txt"><strong>' + c.label + "</strong><small>"
        + (ref ? "becomes Picture " + (i + 2) + " · " + who : "add Picture " + (i + 2) + " to use") + "</small></span>" + thumb;
      b.disabled = !ref;
      b.setAttribute("aria-pressed", S.color === c.key ? "true" : "false");
      b.addEventListener("click", () => { S.color = c.key; renderColors(); drawOverlay(); stageNote(); });
      box.appendChild(b);
    });
    if (more) {
      const n = document.createElement("p");
      n.className = "swap-note swap-more";
      const first = pics().length + 1;
      n.textContent = "Swapping more people? Add Picture " + first + (first < 4 ? " (and " + (first + 1) + ")" : "")
        + " with +: each extra picture replaces one more person.";
      box.appendChild(n);
    }
  }

  function stageNote(extra) {
    const c = COLORS.find((x) => x.key === S.color) || COLORS[0];
    const n = Object.keys(S.picks).length;
    let t;
    if (S.busy) t = "Finding the people in Picture 1… (a few seconds; the first time can take longer)";
    else if (!pics()[0]) t = "Add Picture 1 first (the photo to change).";
    else if (!S.people) t = "Press “Find people” to see who can be swapped.";
    else if (!S.people.people.length) t = "No person found in this picture.";
    else t = "Tap the person who should become Picture " + (Number(c.key) + 1) + " (" + c.label + ", " + c.name + "). "
      + (n ? "Tap a coloured person again to unpick them." : "Nobody picked: the app chooses for you.");
    el("swap-stage-note").textContent = extra || t;
  }

  function renderStage() {
    const p1 = pics()[0];
    const img = el("swap-img");
    if (!p1) { img.removeAttribute("src"); el("swap-stage").classList.add("is-empty"); stageNote(); return; }
    el("swap-stage").classList.remove("is-empty");
    const src = p1.url || p1.thumb || "";
    if (img.getAttribute("src") !== src) { img.src = src; img.onload = drawOverlay; }
    drawOverlay();
    stageNote();
  }

  async function findPeople(force) {
    const p1 = pics()[0];
    if (!p1 || S.busy) return;
    const s = settings();
    const key = p1.name + "|" + Number(s.mp);
    if (!force && S.people && S.picKey === key) { drawOverlay(); stageNote(); return; }
    if (!S.picKey.startsWith(p1.name + "|")) S.picks = {};
    S.busy = true; stageNote(); el("swap-find").disabled = true;
    try {
      const r = await fetch("/api/swap/people", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: p1.name, mp: Number(s.mp) || 2, refs: Math.max(1, pics().length - 1),
          prompt: (el("prompt") || {}).value || "", settings: isManual() ? s : null }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Couldn't look for people.");
      S.people = d; S.picKey = key; S.masks = {};
      S.autoPicks = d.auto || {};
      await loadMasks();
    } catch (e) {
      S.people = null;
      stageNote(e.message);
      S.busy = false; el("swap-find").disabled = false;
      return;
    }
    S.busy = false; el("swap-find").disabled = false;
    drawOverlay(); stageNote(); renderColors(); renderPill();
  }

  function loadMasks() {
    const list = (S.people && S.people.people) || [];
    return Promise.all(list.map((p) => new Promise((res) => {
      if (!p.mask) return res();
      const im = new Image();
      im.onload = () => {
        const c = document.createElement("canvas");
        c.width = im.naturalWidth; c.height = im.naturalHeight;
        const x = c.getContext("2d");
        x.drawImage(im, 0, 0);
        S.masks[p.id] = { canvas: c, data: x.getImageData(0, 0, c.width, c.height).data };
        res();
      };
      im.onerror = () => res();
      im.src = p.mask;
    })));
  }

  function colorOf(id) { const k = Object.keys(S.picks).find((x) => S.picks[x] === id); return k ? COLORS[Number(k) - 1] : null; }
  function autoColorOf(id) {
    if (Object.keys(S.picks).length) return null;
    const k = Object.keys(S.autoPicks || {}).find((x) => S.autoPicks[x] === id);
    return k ? COLORS[Number(k) - 1] : null;
  }

  function drawOverlay() {
    const cv = el("swap-canvas"), img = el("swap-img");
    if (!cv || !img || !img.naturalWidth) return;
    const w = img.clientWidth, h = img.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.width = w + "px"; cv.style.height = h + "px";
    const x = cv.getContext("2d");
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.clearRect(0, 0, w, h);
    const list = (S.people && S.people.people) || [];
    list.forEach((p) => {
      const chosen = colorOf(p.id), auto = autoColorOf(p.id);
      if (!p.likely && !chosen) return;                 // specks the finder half-believed are hidden
      const col = chosen ? chosen.hex : (auto ? auto.hex : "#ffffff");
      const m = S.masks[p.id];
      if (m) {
        const t = document.createElement("canvas");
        t.width = m.canvas.width; t.height = m.canvas.height;
        const tx = t.getContext("2d");
        tx.drawImage(m.canvas, 0, 0);
        tx.globalCompositeOperation = "source-in";
        tx.fillStyle = col;
        tx.fillRect(0, 0, t.width, t.height);
        // the mask is white-on-black: keep only the white part
        const id = tx.getImageData(0, 0, t.width, t.height);
        for (let i = 0; i < id.data.length; i += 4) id.data[i + 3] = m.data[i] > 127 ? 255 : 0;
        tx.putImageData(id, 0, 0);
        x.globalAlpha = chosen ? 0.5 : (auto ? 0.28 : 0.16);
        x.drawImage(t, 0, 0, w, h);
        x.globalAlpha = 1;
      }
      const [bx, by, bw, bh] = p.box;
      x.lineWidth = chosen ? 3 : 1.5;
      x.setLineDash(chosen ? [] : [6, 5]);
      x.strokeStyle = col;
      x.strokeRect(bx * w, by * h, bw * w, bh * h);
      x.setLineDash([]);
      const tag = chosen ? chosen.label + " → Picture " + (Number(Object.keys(S.picks).find((k) => S.picks[k] === p.id)) + 1)
        : (auto ? "App's pick" : "Tap to pick");
      x.font = "600 13px system-ui, sans-serif";
      const tw = x.measureText(tag).width + 12;
      const ty = Math.max(0, by * h - 22);
      x.fillStyle = chosen ? col : "rgba(0,0,0,.65)";
      x.fillRect(bx * w, ty, tw, 20);
      x.fillStyle = "#fff";
      x.fillText(tag, bx * w + 6, ty + 14);
    });
  }

  function personAt(fx, fy) {
    const list = ((S.people && S.people.people) || []).filter((p) => p.likely || colorOf(p.id));
    const hits = list.filter((p) => {
      const m = S.masks[p.id];
      if (m) {
        const px = Math.min(m.canvas.width - 1, Math.floor(fx * m.canvas.width));
        const py = Math.min(m.canvas.height - 1, Math.floor(fy * m.canvas.height));
        if (m.data[(py * m.canvas.width + px) * 4] > 127) return true;
      }
      return false;
    });
    if (hits.length) return hits.sort((a, b) => a.area - b.area)[0];
    const inBox = list.filter((p) => fx >= p.box[0] && fx <= p.box[0] + p.box[2] && fy >= p.box[1] && fy <= p.box[1] + p.box[3]);
    return inBox.sort((a, b) => a.area - b.area)[0] || null;
  }

  function onStageClick(e) {
    if (!S.people) { findPeople(true); return; }
    const r = el("swap-img").getBoundingClientRect();
    const p = personAt((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
    if (!p) { stageNote("That's not a person the app found. Tap inside a person's outline."); return; }
    const had = Object.keys(S.picks).find((k) => S.picks[k] === p.id);
    if (had) {
      delete S.picks[had];
      toastSafe(COLORS[Number(had) - 1].label + " unpicked");
    } else {
      if (!pics()[Number(S.color)]) { stageNote("Add Picture " + (Number(S.color) + 1) + " first: that's who this person becomes."); return; }
      S.picks[S.color] = p.id;
      S.picksMp = Number(settings().mp);
      toastSafe(COLORS[Number(S.color) - 1].label + " picked: becomes Picture " + (Number(S.color) + 1));
      // move on to the next colour that has a picture and no person yet
      const next = COLORS.find((c, i) => !S.picks[c.key] && pics()[i + 1]);
      if (next) S.color = next.key;
    }
    renderColors(); drawOverlay(); stageNote(); renderPill();
  }
  function toastSafe(m) { if (typeof toast === "function") toast(m); }

  function renderManual() {
    const s = settings();
    const guides = (S.status && S.status.guides) || { hatching: "Lines (recommended)", shape: "Body shape (3D surface)", outline: "Outline only", overlay: "See-through tint" };
    const sel = el("swap-guide");
    if (!sel.options.length) Object.keys(guides).forEach((k) => { const o = document.createElement("option"); o.value = k; o.textContent = guides[k]; sel.appendChild(o); });
    sel.value = s.guide;
    el("swap-lines").classList.toggle("hidden", s.guide !== "hatching");
    document.querySelectorAll("#swap-manual input[data-k]").forEach((inp) => {
      inp.value = s[inp.dataset.k];
      const out = el("swap-v-" + inp.dataset.k);
      if (out) out.textContent = fmt(inp.dataset.k, s[inp.dataset.k]);
    });
    el("swap-own").checked = !!ls("cs.swap.ownPrompt", false);
    el("swap-own-text").value = ls("cs.swap.ownText", "");
    el("swap-own-text").classList.toggle("hidden", !el("swap-own").checked);
  }
  function fmt(k, v) {
    v = Number(v);
    if (k === "margin" || k === "feather") return Math.round(v * 100) + "%";
    if (k === "steps") return String(Math.round(v));
    return v.toFixed(2).replace(/0$/, "");
  }

  function bind() {
    const pill = el("swap-pill");
    if (!pill) return;
    pill.addEventListener("click", openSheet);
    el("swap-hint-btn").addEventListener("click", openSheet);
    el("swap-close").addEventListener("click", closeSheet);
    el("swap-done").addEventListener("click", closeSheet);
    el("swap-backdrop").addEventListener("click", closeSheet);
    el("swap-find").addEventListener("click", () => findPeople(true));
    el("swap-clear").addEventListener("click", () => { S.picks = {}; S.color = "1"; renderColors(); drawOverlay(); stageNote(); renderPill(); toastSafe("The app will choose who to swap"); });
    el("swap-stage").addEventListener("click", onStageClick);
    el("swap-setup-go").addEventListener("click", () => {
      closeSheet();
      if (typeof window.__openWorkflowsFor === "function") window.__openWorkflowsFor(CARD);
      else { const b = document.querySelector('.views button[data-view="workflows"]'); if (b) b.click(); }
      toastSafe("Find “MiniMax H3 · Character swap” and press Set up");
    });
    document.querySelectorAll("#swap-route button").forEach((b) => b.addEventListener("click", () => {
      lsSet("cs.swap.mode", b.dataset.route); renderSheet(); renderPill();
    }));
    document.querySelectorAll("#swap-body .opt-card").forEach((c) => c.addEventListener("click", () => {
      const s = settings();
      setSetting("body", s.body === c.dataset.body ? "auto" : c.dataset.body);
      renderSheet();
    }));
    document.querySelectorAll("#swap-size button").forEach((b) => b.addEventListener("click", () => {
      setSetting("mp", Number(b.dataset.mp)); renderSheet();
      if (S.people) findPeople(true);       // people are found at the size the job uses
    }));
    el("swap-guide").addEventListener("change", (e) => { setSetting("guide", e.target.value); renderManual(); });
    document.querySelectorAll("#swap-manual input[data-k]").forEach((inp) => inp.addEventListener("input", () => {
      setSetting(inp.dataset.k, Number(inp.value));
      const out = el("swap-v-" + inp.dataset.k);
      if (out) out.textContent = fmt(inp.dataset.k, inp.value);
    }));
    el("swap-reset").addEventListener("click", () => { lsSet("cs.swap.settings", {}); renderSheet(); toastSafe("Swap settings back to the defaults"); });
    el("swap-own").addEventListener("change", (e) => { lsSet("cs.swap.ownPrompt", e.target.checked); renderManual(); });
    el("swap-own-text").addEventListener("input", (e) => lsSet("cs.swap.ownText", e.target.value));
    window.addEventListener("resize", () => { if (!el("swap-sheet").classList.contains("hidden")) drawOverlay(); });
    const pr = el("prompt");
    if (pr) pr.addEventListener("input", checkWords);
    const slots = el("slots");
    if (slots && window.MutationObserver) new MutationObserver(() => {
      const p1 = pics()[0];
      if (!p1 || !S.picKey.startsWith(p1.name + "|")) { S.people = null; S.picks = {}; S.picKey = ""; }
      lastChecked = ""; checkWords(); renderPill();
    }).observe(slots, { childList: true, subtree: true });
    document.addEventListener("click", (e) => {       // engine / mode changes elsewhere: keep the pill right
      if (e.target.closest && e.target.closest(".hud, .seg, .mode-switch, .wf-sheet, #engine-pill")) setTimeout(renderPill, 120);
    });
    // a "setup needed" answer from Generate: offer the setup right there
    const base = window.showActionError;
    if (typeof base === "function") window.showActionError = function (data) {
      base(data);
      if (data && data.code === "setup" && data.workflow_id === CARD) {
        const btn = el("dock-alert-action");
        if (btn) {
          btn.textContent = "Set up Character swap";
          btn.classList.remove("hidden");
          btn.onclick = () => el("swap-setup-go").click();
        }
      }
    };
    renderPill();
    checkWords();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
})();
