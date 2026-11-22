/* ===== Guided tour v20261122a =====
   Coach-mark tour (spotlight + small HUD card). Asked once per install, and once more for everyone whenever
   TOUR_VERSION goes up (the server keeps the last version seen in data/settings.json). Steps that point at a
   control that isn't on screen are skipped. The pill row scrolls by itself so hidden pills come into view.
   Restart any time: Guide page -> "Take the tour", or window.startTour(). */
(function () {
  "use strict";
  const TOUR_VERSION = 1;
  const TEST_PROMPT = "a red apple on a wooden kitchen table, soft window light, photo";
  const $ = (id) => document.getElementById(id);
  const q = (s) => document.querySelector(s);

  // ---------- seen flag (server, with a local copy if the server can't be reached) ----------
  async function seenVersion() {
    try {
      const r = await fetch("/api/tour", { cache: "no-store" });
      if (r.ok) return (await r.json()).seen || 0;
    } catch (e) {}
    try { return Number(localStorage.getItem("cs.tourSeen") || 0); } catch (e) { return 0; }
  }
  function markSeen() {
    try { localStorage.setItem("cs.tourSeen", String(TOUR_VERSION)); } catch (e) {}
    fetch("/api/tour", { method: "POST", headers: { "Content-Type": "application/json" },
                         body: JSON.stringify({ seen: TOUR_VERSION }) }).catch(() => {});
  }

  // ---------- overlay ----------
  let ui = null;
  function build() {
    if (ui) return ui;
    const root = document.createElement("div");
    root.className = "tour-root";
    root.innerHTML =
      '<div class="tour-block"></div><div class="tour-spot"></div>' +
      '<div class="tour-card" role="dialog" aria-live="polite">' +
      '<div class="tour-step"></div><h3 class="tour-title"></h3><p class="tour-text"></p>' +
      '<div class="tour-btns"></div></div>';
    document.body.appendChild(root);
    ui = { root, spot: root.querySelector(".tour-spot"), card: root.querySelector(".tour-card"),
           step: root.querySelector(".tour-step"), title: root.querySelector(".tour-title"),
           text: root.querySelector(".tour-text"), btns: root.querySelector(".tour-btns"), target: null };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("keydown", (e) => {
      if (!ui || ui.root.classList.contains("hidden")) return;
      if (e.key === "Escape") end();
      else if (e.key === "ArrowRight" && ui.next) ui.next();
      else if (e.key === "ArrowLeft" && ui.back) ui.back();
    });
    return ui;
  }

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && !el.closest(".hidden");
  }

  function place() {
    if (!ui || !ui.root.classList.contains("on")) return;
    const el = ui.target;
    const vw = window.innerWidth, vh = window.innerHeight, pad = 6, gut = 12;
    const card = ui.card;
    card.style.maxWidth = Math.min(340, vw - 2 * gut) + "px";
    if (!el || !visible(el)) {
      ui.spot.style.cssText = "left:50%;top:50%;width:0;height:0";
      card.style.left = Math.max(gut, (vw - card.offsetWidth) / 2) + "px";
      card.style.top = Math.max(gut, (vh - card.offsetHeight) / 2) + "px";
      return;
    }
    const r = el.getBoundingClientRect();
    ui.spot.style.cssText = "left:" + (r.left - pad) + "px;top:" + (r.top - pad) + "px;width:" + (r.width + 2 * pad) +
                            "px;height:" + (r.height + 2 * pad) + "px";
    const cw = card.offsetWidth, ch = card.offsetHeight;
    let top = r.bottom + pad + 10;
    if (top + ch > vh - gut) top = r.top - pad - 10 - ch;          // no room below: above it
    if (top < gut) top = Math.min(vh - ch - gut, Math.max(gut, r.bottom + pad + 10));
    let left = r.left + r.width / 2 - cw / 2;
    left = Math.max(gut, Math.min(vw - cw - gut, left));
    card.style.left = left + "px";
    card.style.top = top + "px";
  }

  function show(opts) {
    build();
    ui.root.classList.remove("hidden");
    ui.root.classList.add("on");
    document.body.classList.add("tour-on");
    ui.target = opts.el || null;
    ui.root.classList.toggle("no-target", !opts.el);
    ui.step.textContent = opts.step || "";
    ui.title.textContent = opts.title || "";
    ui.text.textContent = opts.text || "";
    ui.btns.innerHTML = "";
    ui.next = ui.back = null;
    (opts.buttons || []).forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tour-btn" + (b.primary ? " primary" : "") + (b.quiet ? " quiet" : "");
      btn.textContent = b.label;
      btn.disabled = !!b.disabled;
      btn.addEventListener("click", b.go);
      ui.btns.appendChild(btn);
      if (b.role === "next") ui.next = b.go;
      if (b.role === "back") ui.back = b.go;
    });
    if (opts.el) {
      // the pill row scrolls sideways: bring the pill into view first, then place the card
      try { opts.el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" }); } catch (e) {}
    }
    place();
    setTimeout(place, 220);
    setTimeout(place, 480);
    setTimeout(place, 900);                    // a long sideways scroll of the pill row ends later
    const first = ui.btns.querySelector(".primary");
    if (first) first.focus({ preventScroll: true });
  }

  function end() {
    if (!ui) return;
    ui.root.classList.remove("on");
    ui.root.classList.add("hidden");
    document.body.classList.remove("tour-on");
    ui.target = null;
    markSeen();
    setMedia("image");
    setMode("t2i");
  }

  // ---------- app helpers ----------
  function setMedia(m) {
    const b = q('#media-seg button[data-media="' + m + '"]');
    if (b && !b.classList.contains("is-on")) b.click();
  }
  function setMode(m) {
    const b = q('#mode-seg button[data-mode="' + m + '"], .mode-seg button[data-mode="' + m + '"]');
    if (b && !b.classList.contains("is-on")) b.click();
  }
  function goView(v) {
    const b = q('.topnav button[data-view="' + v + '"]');
    if (b && !b.classList.contains("is-on")) b.click();
  }

  // ---------- step lists ----------
  const S = (sel, title, text) => ({ sel, title, text });
  const BASICS = [
    S("#prompt-card", "The chat box", "Describe what you want here, in plain words. It slims down while a picture is being made; tap it to open it again."),
    S("#generate", "Send", "Sends your prompt. If ComfyUI isn't running yet, Comfy Studio starts it for you and waits until it's ready."),
    S("#add-images", "Add pictures", "Add photos from this device, from your PC or from the Library. They become Picture 1, 2, ... for edits."),
    S("#media-seg", "Image or video", "Switch between making pictures and making videos."),
    S("#mode-seg, .mode-seg", "Create or Edit", "Create makes something new from words. Edit changes a picture you added."),
    S("#engine-pill", "Engine", "Which model makes it. Auto picks the best one for what you asked."),
    S("#res-picker", "Size", "Shape (square, portrait, wide) and how many megapixels."),
    S("#quality-pill", "Enhance", "Lets an AI rewrite your prompt with more detail before it's made."),
    S("#camera-open", "Camera", "Camera angle, shot size, lens and depth of field."),
    S("#lora-pill", "LoRA", "Add a style or character add-on. None is fine for most pictures."),
    S("#seed-pill", "Seed", "Random gives a new picture each time. Lock it to repeat or fine-tune one."),
    S("#help-pill", "Quick help", "Tips for the controls you can see right now."),
    S("#settings-gear", "Settings", "Less common settings: steps, sampler, models, start options."),
  ];
  const EDIT = [
    S("#kind-pill", "Edit mode", "Edit changes Picture 1; Reference uses your pictures as a guide for something new."),
    S("#roles-pill", "Roles", "Tell it what each picture is for (person, outfit, background ...). Auto works out most of it."),
    S("#inpaint-pill", "Paint an area", "Paint over the part to change; everything else stays as it was."),
    S("#mark-pill", "Mark an area", "Drag a box to say where something should go."),
    S("#add-images", "Add Picture 1", "To edit, add a picture here first (your test picture is in the Library), then write the change, e.g. “make it evening light”."),
  ];
  const VIDEO = [
    S("#vsize-pill", "Video size", "Shape and resolution of the video. 720p is a good start on this PC."),
    S("#vlen-pill", "Length", "How many seconds. Longer takes much more time."),
    S("#engine-pill", "Video engine", "The video model. Videos take a few minutes on an 8 GB card."),
  ];
  const REST = [
    S('.topnav button[data-view="library"]', "Library", "Everything you've made, with search and favourites."),
    S('.topnav button[data-view="guide"]', "Guide", "How every pill works, with examples. The tour can be restarted from there."),
    S('.topnav button[data-view="workflows"]', "Workflows", "Add your own ComfyUI workflows and use them from the same chat box."),
    S("#status", "ComfyUI light", "Green means ComfyUI is ready. If it's stopped, just press send: it starts by itself."),
    S("#gen-pill", "Console", "Progress and the technical log, if you ever need help."),
  ];

  function resolve(list) {
    return list.map((s) => Object.assign({}, s, { el: [...document.querySelectorAll(s.sel)].find(visible) || null }))
               .filter((s) => s.el);
  }

  function runSteps(list, label, done) {
    let i = 0, steps = resolve(list);
    function go() {
      if (!steps.length) return done();
      const s = steps[i];
      show({
        el: s.el, step: label + " · " + (i + 1) + " / " + steps.length, title: s.title, text: s.text,
        buttons: [
          { label: "Skip tour", quiet: true, go: end },
          i > 0 ? { label: "Back", role: "back", go: () => { i--; go(); } } : null,
          { label: i === steps.length - 1 ? "Continue" : "Next", primary: true, role: "next",
            go: () => { if (i < steps.length - 1) { i++; go(); } else done(); } },
        ].filter(Boolean),
      });
    }
    go();
  }

  // ---------- the test runs ----------
  function sendPrompt(text) {
    const p = $("prompt");
    p.value = text;
    p.dispatchEvent(new Event("input", { bubbles: true }));
    $("generate").click();
  }

  async function waitForJob(t0, onTick) {
    for (let k = 0; k < 900; k++) {
      await new Promise((r) => setTimeout(r, 1500));
      let list = [];
      try { list = await (await fetch("/api/jobs", { cache: "no-store" })).json(); } catch (e) { continue; }
      const mine = (list || []).filter((j) => (j.t_start || j.t || 0) >= t0 - 2);
      const j = mine[0];
      if (!j) continue;
      if (onTick) onTick(j);
      if (["success", "error", "cancelled", "done"].includes(j.status)) return j;
    }
    return null;
  }

  function testImage(next) {
    show({
      step: "Test run", title: "Make a test picture?",
      text: "Comfy Studio will type a simple prompt and press send for you, so you can see a whole run. It takes about half a minute (longer if ComfyUI has to start first).",
      buttons: [
        { label: "Skip test", quiet: true, go: next },
        { label: "Make a test picture", primary: true, go: async () => {
          setMedia("image"); setMode("t2i");
          const t0 = Date.now() / 1000;
          sendPrompt(TEST_PROMPT);
          show({ el: $("prompt-card"), step: "Test run", title: "Working on it…",
                 text: "Your test prompt was sent. Progress shows above the chat box; you can keep using the app.",
                 buttons: [{ label: "Skip waiting", quiet: true, go: next }] });
          const j = await waitForJob(t0, (jj) => {
            if (ui && ui.title.textContent === "Working on it…") ui.text.textContent = "Status: " + (jj.message || jj.status);
          });
          if (!ui || !ui.root.classList.contains("on")) return;
          const ok = j && ["success", "done"].includes(j.status);
          show({ el: ok ? ($("gen-stage") || null) : null, step: "Test run",
                 title: ok ? "Done!" : "That didn't finish",
                 text: ok ? "Your picture is saved in the Library. Close it with ✕ to go back to the feed."
                          : ((j && j.error) || "No result yet. You can check the Console later."),
                 buttons: [{ label: "Continue", primary: true, role: "next", go: next }] });
        } },
      ],
    });
  }

  function offerEdit(next) {
    show({
      step: "Edit (optional)", title: "Want a look at editing?",
      text: "Edit changes a picture you already have: a new background, other light, another outfit.",
      buttons: [
        { label: "Skip", quiet: true, go: next },
        { label: "Show me Edit", primary: true, go: () => { setMode("edit"); setTimeout(() => runSteps(EDIT, "Edit", () => { setMode("t2i"); next(); }), 250); } },
      ],
    });
  }

  function offerVideo(next) {
    show({
      step: "Almost done", title: "Video test, or finish the tour?",
      text: "A short test video takes a few minutes on this PC. Or skip it and see the last few buttons.",
      buttons: [
        { label: "Finish the tour", primary: true, go: next },
        { label: "Show me Video", go: () => {
          setMedia("video");
          setTimeout(() => runSteps(VIDEO, "Video", () => {
            show({ step: "Video", title: "Make a short test video?",
                   text: "It uses the same chat box. Expect a few minutes; the Console shows progress.",
                   buttons: [
                     { label: "Skip", quiet: true, go: () => { setMedia("image"); next(); } },
                     { label: "Make a test video", primary: true, go: () => {
                       sendPrompt("a red apple slowly rotating on a wooden kitchen table, soft window light");
                       setTimeout(() => { setMedia("image"); next(); }, 600);
                     } },
                   ] });
          }), 250);
        } },
      ],
    });
  }

  function finish() {
    runSteps(REST, "More", () => {
      show({ step: "", title: "You're all set",
             text: "That's everything. Restart this tour any time from the Guide page.",
             buttons: [{ label: "Done", primary: true, go: end }] });
    });
  }

  function start() {
    goView("create");
    if (window.__composerCollapse) { try { window.__composerCollapse(); } catch (e) {} }
    setMedia("image"); setMode("t2i");
    setTimeout(() => runSteps(BASICS, "Tour", () => testImage(() => offerEdit(() => offerVideo(finish)))), 200);
  }

  function ask() {
    show({
      step: "Welcome", title: "Would you like a quick tour?",
      text: "A one-minute walk through the buttons, with an optional test picture at the end.",
      buttons: [
        { label: "Skip", quiet: true, go: end },
        { label: "Yes, show me", primary: true, go: start },
      ],
    });
  }

  window.startTour = start;

  // a "Take the tour" button at the top of the Guide page
  function addGuideButton() {
    const g = $("view-guide");
    if (!g || g.querySelector(".tour-again")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip-btn tour-again";
    b.textContent = "Take the tour";
    b.addEventListener("click", start);
    g.insertBefore(b, g.firstChild);
  }

  async function init() {
    addGuideButton();
    if (/[?&]tour=1/.test(location.search)) return setTimeout(ask, 800);
    const seen = await seenVersion();
    if (seen < TOUR_VERSION) setTimeout(ask, 1200);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
