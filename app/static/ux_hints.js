/* ===== Plain-language hints v20261124 =====
   Small guidance layer on top of app.js (nothing here changes what is generated):
   - every pill and icon button says what it does when you point at it (title / screen-reader label)
   - the pill row shows ‹ › arrows when more pills are hidden off the side (Seed and ? used to be invisible)
   - the prompt box suggests what to type for what you're doing (new picture, edit, several pictures, video) */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);

  const TIPS = {
    "engine-pill": "Engine: which AI model makes your picture or video. Tap to switch. Auto = the app picks the settings.",
    "res-trigger": "Shape and size of the picture. Bigger = sharper but slower.",
    "vsize-pill": "Shape and quality (resolution) of the video.",
    "vlen-pill": "How long the video is.",
    "kind-pill": "Edit Picture 1 (keep it, change parts) or make a new scene from your pictures.",
    "quality-pill": "Enhance: lets the app rewrite your prompt with more detail before sending. Off = your words exactly.",
    "camera-open": "Camera: angle, shot and lens (close-up, wide, from above ...). Any = the model decides.",
    "roles-pill": "Roles: what each picture is for (the person, the outfit, the background ...). Auto works it out.",
    "inpaint-pill": "Inpaint: paint over the part to change. Everything else stays exactly the same.",
    "swap-pill": "Character swap: put the person from Picture 2 in place of someone in Picture 1.",
    "lora-pill": "LoRA: add-on styles or characters for the model. None = the model on its own.",
    "seed-pill": "Seed: Random gives a new result each time. Fix a number to repeat a result.",
    "help-pill": "Help: what each pill does and tips for your current engine.",
    "add-images": "Add pictures: from your Library, your PC or your phone. The first one is Picture 1 (the one edited).",
    "settings-gear": "Settings: engine, Auto or Manual, steps and the finer controls.",
    "generate": "Generate (Enter). You can send more while one is running; they wait in a queue.",
    "prompt-clear": "Empty the prompt box (Undo brings it back).",
    "status": "ComfyUI connection: green = ready. If it's red, press Start.",
  };

  function applyTips() {
    Object.keys(TIPS).forEach((id) => {
      const b = el(id);
      if (!b) return;
      if (!b.title || b.dataset.tipSet) { b.title = TIPS[id]; b.dataset.tipSet = "1"; }
      if (!b.getAttribute("aria-description")) b.setAttribute("aria-description", TIPS[id]);
    });
    document.querySelectorAll('.mode[data-mode="t2i"]').forEach((b) => { if (!b.title) b.title = "Create: make something new from your words."; });
    document.querySelectorAll('.mode[data-mode="edit"]').forEach((b) => { if (!b.title) b.title = "Edit: change the picture you added (Picture 1)."; });
    const nav = { create: "Create: make and edit pictures and videos", library: "Library: everything you've made",
                  guide: "Guide: how everything works, step by step", workflows: "Workflows: the AI engines, and setting them up" };
    document.querySelectorAll(".views button[data-view]").forEach((b) => { if (nav[b.dataset.view]) b.title = nav[b.dataset.view]; });
  }

  /* ---- pill row: show ‹ › when pills are hidden off the side ---- */
  function arrows() {
    const hud = el("hud"), sc = el("hud-scroller");
    if (!hud || !sc || el("hud-more-r")) return;
    const mk = (id, dir, label) => {
      const b = document.createElement("button");
      b.type = "button"; b.id = id; b.className = "hud-more hud-more-" + dir + " hidden";
      b.setAttribute("aria-label", label); b.title = label;
      b.textContent = dir === "l" ? "‹" : "›";
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        const from = sc.scrollLeft, to = Math.max(0, Math.min(sc.scrollWidth - sc.clientWidth,
          from + (dir === "l" ? -1 : 1) * Math.max(160, sc.clientWidth * 0.7)));
        const t0 = performance.now();
        const step = (t) => {
          const k = Math.min(1, (t - t0) / 220);
          sc.scrollLeft = from + (to - from) * (1 - Math.pow(1 - k, 3));
          if (k < 1) requestAnimationFrame(step); else sc.dispatchEvent(new Event("scroll"));
        };
        requestAnimationFrame(step);
      });
      hud.appendChild(b);
      return b;
    };
    const l = mk("hud-more-l", "l", "Show the pills on the left");
    const r = mk("hud-more-r", "r", "More pills");
    const sync = () => {
      const max = sc.scrollWidth - sc.clientWidth;
      l.classList.toggle("hidden", sc.scrollLeft < 4);
      r.classList.toggle("hidden", max - sc.scrollLeft < 4);
      hud.classList.toggle("hud-has-more", max > 4);
    };
    sc.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    if (window.ResizeObserver) new ResizeObserver(sync).observe(sc);
    if (window.MutationObserver) new MutationObserver(() => setTimeout(sync, 30)).observe(sc, { attributes: true, subtree: true, attributeFilter: ["class", "hidden", "style"] });
    sync();
  }

  /* ---- the prompt box suggests what to type ---- */
  const EX = {
    t2i: ["a woman reading in a sunny café, 35mm photo", "a red vintage car on a coastal road at sunset", "a cosy cabin in the snow at night, warm windows"],
    edit1: ["make her jacket red", "change the background to a beach", "remove the person on the left"],
    edit2: ["swap her with picture 2", "put the dress from picture 2 on her", "she has the face and hair of picture 2"],
  };
  let exI = 0;
  function pick(list) { exI = (exI + 1) % list.length; return list[exI]; }
  function smartPlaceholder() {
    const ta = el("prompt");
    if (!ta || typeof state === "undefined") return false;
    if (state.inpaint) return false;                         // the painter sets its own text
    if (state.media === "video") return false;               // video tasks set their own text
    const n = (state.images || []).length;
    if (state.mode !== "edit") ta.placeholder = "Describe a picture, e.g. “" + pick(EX.t2i) + "”";
    else if (state.refMode) ta.placeholder = "Describe a new scene using your pictures · type @ to name one";
    else if (n >= 2) ta.placeholder = "Say what to do with your pictures, e.g. “" + pick(EX.edit2) + "”";
    else if (n === 1) ta.placeholder = "Say what to change in Picture 1, e.g. “" + pick(EX.edit1) + "”";
    else ta.placeholder = "Add a picture with + first, then say what to change";
    return true;
  }
  function hookPlaceholder() {
    if (typeof window.syncPlaceholder === "function" && !window.syncPlaceholder._ux) {
      const base = window.syncPlaceholder;
      const wrapped = function () { const r = base.apply(this, arguments); smartPlaceholder(); return r; };
      wrapped._ux = true;
      window.syncPlaceholder = wrapped;
    }
    const slots = el("slots");
    if (slots && window.MutationObserver) new MutationObserver(() => smartPlaceholder()).observe(slots, { childList: true });
    document.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest(".mode, .media-seg button, #kind-pop, #inpaint-chip-clear")) setTimeout(smartPlaceholder, 60);
    });
    smartPlaceholder();
  }

  function bind() {
    applyTips();
    arrows();
    hookPlaceholder();
    setTimeout(applyTips, 1500);           // pills added later (engine change, swap.js)
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
})();
