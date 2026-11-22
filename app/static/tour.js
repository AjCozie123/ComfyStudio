/* ===== Guided tour v20261122d =====
   Coach-mark tour (spotlight + small HUD card). Asked once per install, and once more for everyone whenever
   TOUR_VERSION goes up (the server keeps the last version seen in data/settings.json).
   - Every step checks what is really on screen for the current engine: a control the engine doesn't have (Inpaint,
     LoRA, Camera, video length ...) is skipped, and the tour says so in one line where it matters.
   - Each step has a short text and a "More" with real uses, so the tour stays quick but nothing is hidden.
   - Deep dives open the real panels (LoRA picker, Inpaint painter) and close them again.
   - Anything the tour changes (mode, media, pictures it added) is put back at the end.
   Restart any time: Guide page -> "Take the tour", window.startTour(), or ?tour=1. */
(function () {
  "use strict";
  const TOUR_VERSION = 2;
  const TEST_PROMPT = "a red apple on a wooden kitchen table, soft window light, photo";
  const $ = (id) => document.getElementById(id);
  const q = (s) => document.querySelector(s);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- seen flag ----------
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
    root.className = "tour-root hidden";
    root.innerHTML =
      '<div class="tour-block"></div><div class="tour-spot"></div>' +
      '<div class="tour-card" role="dialog" aria-live="polite">' +
      '<div class="tour-step"></div><h3 class="tour-title"></h3><p class="tour-text"></p>' +
      '<p class="tour-more hidden"></p><div class="tour-btns"></div></div>';
    document.body.appendChild(root);
    ui = { root, spot: root.querySelector(".tour-spot"), card: root.querySelector(".tour-card"),
           step: root.querySelector(".tour-step"), title: root.querySelector(".tour-title"),
           text: root.querySelector(".tour-text"), more: root.querySelector(".tour-more"),
           btns: root.querySelector(".tour-btns"), target: null };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("keydown", (e) => {
      if (!running()) return;
      if (e.key === "Escape") { e.stopPropagation(); end(); }
      else if (e.key === "ArrowRight" && ui.next) ui.next();
      else if (e.key === "ArrowLeft" && ui.back) ui.back();
    }, true);
    return ui;
  }
  const running = () => ui && ui.root.classList.contains("on");

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && !el.closest(".hidden,[hidden]");
  }
  const find = (sel) => [...document.querySelectorAll(sel)].find(visible) || null;

  function place() {
    if (!running()) return;
    const el = ui.target;
    const vw = window.innerWidth, vh = window.innerHeight, pad = 6, gut = 12;
    const card = ui.card;
    card.style.maxWidth = Math.min(360, vw - 2 * gut) + "px";
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
    if (top + ch > vh - gut) top = r.top - pad - 10 - ch;
    if (top < gut) {                       // a big target (a panel): put the card inside it, at the bottom
      top = Math.min(vh - ch - gut, Math.max(gut, r.bottom - ch - 16));
    }
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
    ui.root.classList.toggle("pass", !!opts.pass);     // pass: clicks reach the panel under the spotlight
    ui.step.textContent = opts.step || "";
    ui.title.textContent = opts.title || "";
    ui.text.textContent = opts.text || "";
    ui.more.textContent = opts.more || "";
    ui.more.classList.add("hidden");
    ui.btns.innerHTML = "";
    ui.next = ui.back = null;
    const btns = (opts.buttons || []).slice();
    if (opts.more) btns.splice(btns.findIndex((b) => b.quiet) + 1, 0, { label: "More", go: (e) => {
      const hid = ui.more.classList.toggle("hidden");
      e.currentTarget.textContent = hid ? "More" : "Less";
      place();
    } });
    btns.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tour-btn" + (b.primary ? " primary" : "") + (b.quiet ? " quiet" : "");
      btn.textContent = b.label;
      btn.addEventListener("click", b.go);
      ui.btns.appendChild(btn);
      if (b.role === "next") ui.next = b.go;
      if (b.role === "back") ui.back = b.go;
    });
    if (opts.el) {
      try { opts.el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" }); } catch (e) {}
    }
    place();
    [220, 480, 900].forEach((t) => setTimeout(place, t));
    const first = ui.btns.querySelector(".primary");
    if (first) first.focus({ preventScroll: true });
  }

  // ---------- app helpers ----------
  const saved = {};
  function snapshot() {
    try {
      saved.media = (q("#media-seg button.is-on") || {}).dataset ? q("#media-seg button.is-on").dataset.media : "image";
      saved.mode = (q('.mode-seg button.is-on, #mode-seg button.is-on') || {}).dataset ? q('.mode-seg button.is-on, #mode-seg button.is-on').dataset.mode : "t2i";
      saved.images = (window.state && Array.isArray(state.images)) ? state.images.slice() : null;
      saved.prompt = $("prompt") ? $("prompt").value : "";
    } catch (e) {}
  }
  function restore() {
    closePanels();
    setMedia(saved.media || "image");
    setMode(saved.mode || "t2i");
    try {
      if (saved.images && window.state && state.images.length !== saved.images.length) {
        state.images = saved.images.slice();
        if (typeof renderSlots === "function") renderSlots();
      }
    } catch (e) {}
    try {
      const p = $("prompt");
      if (p && p.value === TEST_PROMPT) { p.value = saved.prompt || ""; p.dispatchEvent(new Event("input", { bubbles: true })); }
    } catch (e) {}
  }
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
  function closePanels() {
    const ip = $("inpaint-sheet");
    if (ip && !ip.classList.contains("hidden") && $("ip-close")) $("ip-close").click();
    const lp = $("lora-pop");
    if (lp && !lp.classList.contains("hidden") && $("lora-pill")) $("lora-pill").click();
  }
  function engineName() {
    const p = $("engine-pill");
    return p ? (p.textContent || "").replace(/\bAuto\b/, "").trim() : "";
  }

  function end() {
    if (!ui) return;
    ui.root.classList.remove("on", "pass");
    ui.root.classList.add("hidden");
    document.body.classList.remove("tour-on");
    ui.target = null;
    markSeen();
    restore();
  }

  // ---------- step lists ----------
  // {sel, title, text, more}
  const S = (sel, title, text, more) => ({ sel, title, text, more });
  const BASICS = [
    S("#prompt-card", "The chat box", "Describe what you want in plain words, then press send.",
      "Write it like you'd brief a photographer: who or what, where, the light, the style. “a woman reading by a rainy window, warm lamp light, film photo” works better than one word. The box slims down while a picture is made; tap it to open it again."),
    S("#generate", "Send", "Makes it. If ComfyUI isn't running, Comfy Studio starts it for you first.",
      "You can keep sending while one is still being made: they queue up and run one after another. Enter also sends; Shift+Enter starts a new line."),
    S("#add-images", "Add pictures", "Add photos from this device, your PC or the Library.",
      "The first picture you add is Picture 1: that's the one an edit changes. More pictures (Picture 2, 3 ...) are references, like an outfit, a face or a background. In your prompt you can say “Picture 2” or “@2”."),
    S("#media-seg", "Image or video", "Switch between pictures and videos.",
      "Video uses the same chat box. With a picture added, the video starts from that picture."),
    S("#mode-seg, .mode-seg", "Create or Edit", "Create makes something new. Edit changes a picture you added.",
      "Edit keeps everything you don't mention. Say only what should change: “make it night time”, “put her in a green dress”."),
    S("#engine-pill", "Engine (Auto)", "The model that makes it. Auto picks one that fits what you asked.",
      "Auto looks at the job (new picture, edit, video), how many pictures you added and what your PC can run, and picks the engine that does that best. Auto understanding then reads your words (an outfit change, a removal, a new background ...) and adds the small notes the model needs so faces and clothes stay the same. Pick an engine yourself any time; Auto stays off until you choose it again."),
    S("#res-picker", "Size", "Shape and megapixels.",
      "Portrait for people, wide for landscapes and scenes. More megapixels = more detail but slower and more graphics memory. For edits, “Match Picture 1” keeps your photo's own size."),
    S("#quality-pill", "Enhance", "An AI rewrites your prompt with more detail first.",
      "Great for short prompts (“a castle at dusk”). Turn it off when you've written exactly what you want, or when a small word matters, so it isn't changed."),
    S("#camera-open", "Camera", "Angle, shot size, lens and depth of field.",
      ""),   // filled in at run time for the current engine
    S("#lora-pill", "LoRA", "Add-ons that teach the model a style, a character or a look.",
      "You'll see how to add one in a moment."),
    S("#seed-pill", "Seed", "Random gives a new picture every time.",
      "Lock the seed when you like a result and want to change only one thing (a word, the size, a LoRA strength): everything else stays the same, so you see exactly what your change did."),
    S("#help-pill", "Quick help", "Tips for the controls you can see right now."),
    S("#settings-gear", "Settings", "Less common settings.",
      "Steps, sampler, models, ComfyUI start options, Auto understanding on/off. The defaults are tuned for each engine; you rarely need to change them."),
  ];
  const VIDEO = [
    S("#vsize-pill", "Video size", "Shape and resolution of the video.", "720p is a good start on an 8 GB graphics card. Higher takes much longer."),
    S("#vlen-pill", "Length", "How many seconds.", "Longer videos take much more time and memory. Start short, then go longer once you like the look."),
    S("#engine-pill", "Video engine", "The video model.", "Text to video, picture to video and longer clips use different engines; Auto picks the right one."),
  ];
  const REST = [
    S('.topnav button[data-view="library"]', "Library", "Everything you've made.", "Search, favourites, reuse a prompt, or add a picture back as Picture 1 to keep editing it."),
    S('.topnav button[data-view="guide"]', "Guide", "How every pill works, with examples. Restart this tour from there."),
    S('.topnav button[data-view="workflows"]', "Workflows", "Add your own ComfyUI workflows.", "Upload a workflow file and it becomes an engine you can use from the same chat box."),
    S("#status", "ComfyUI light", "Green = ComfyUI is ready.", "If it's off, just press send: it starts by itself. Closing Comfy Studio also closes ComfyUI, so games and other apps get your graphics card back."),
    S("#gen-pill", "Console", "Progress and the technical log.", "Useful if something goes wrong and you want to see why."),
  ];

  function cameraMore() {
    const n = engineName().toLowerCase();
    let how = "In Edit, Camera only lists the options this engine really does (each one was tested), so not every engine shows the same list.";
    if (/krea/.test(n)) how = "Krea 2 can't re-shoot a photo from another angle, so Camera is hidden in Edit on Krea 2. Use MiniMax H3, FLUX.2 Klein or Qwen Image for new angles.";
    return "For new pictures it sets the shot (low angle, close-up, wide ...). In Edit it re-shoots your photo from a new viewpoint: side, back, from above, closer. " + how;
  }

  function resolve(list) {
    return list.map((s) => Object.assign({}, s, { el: find(s.sel),
                                                  more: s.sel === "#camera-open" ? cameraMore() : s.more }))
               .filter((s) => s.el);
  }

  function runSteps(list, label, done, opts) {
    opts = opts || {};
    let i = 0;
    const steps = resolve(list);
    function go() {
      if (!running() && i > 0) return;
      if (!steps.length) return done();
      const s = steps[i];
      show({
        el: s.el, step: label + " · " + (i + 1) + " / " + steps.length, title: s.title, text: s.text, more: s.more,
        pass: opts.pass,
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

  // ---------- LoRA deep dive ----------
  function loraDive(next) {
    const pill = find("#lora-pill");
    if (!pill) {
      return show({ step: "LoRA", title: "LoRAs", text: "This engine doesn't take LoRAs, so there's nothing to add here. Engines that do show a LoRA pill.",
                    buttons: [{ label: "Continue", primary: true, role: "next", go: next }] });
    }
    show({ el: pill, step: "LoRA", title: "Adding a LoRA",
           text: "A LoRA is a small add-on for one model family. Want to see the picker?",
           buttons: [{ label: "Skip", quiet: true, go: next },
                     { label: "Open it", primary: true, role: "next", go: async () => {
                       pill.click();
                       await wait(450);
                       const pop = find("#lora-pop");
                       const steps = [
                         { el: pop, title: "The LoRA picker",
                           text: "LoRAs made for the current engine are listed first; others are greyed out because they don't work with it.",
                           more: "Tap one to add it. The slider sets its strength: start around 0.7-1.0; lower it if the picture looks overcooked or faces change. Trigger words the LoRA needs are added to your prompt for you. One LoRA at a time gives the cleanest results; two can fight each other." },
                       ];
                       const sr = pop && pop.querySelector('input[type="search"], input[type="text"]');
                       if (sr && visible(sr)) steps.push({ el: sr, title: "Search", text: "Type part of a name or a word like “skin” or “anime” to find one fast." });
                       let k = 0;
                       const step = () => {
                         const s = steps[k];
                         show({ el: s.el, pass: true, step: "LoRA · " + (k + 1) + " / " + steps.length, title: s.title,
                                text: s.text, more: s.more,
                                buttons: [{ label: "Skip tour", quiet: true, go: end },
                                          { label: k === steps.length - 1 ? "Close picker" : "Next", primary: true, role: "next",
                                            go: () => { if (k < steps.length - 1) { k++; step(); } else { closePanels(); next(); } } }] });
                       };
                       if (pop) step(); else next();
                     } }] });
  }

  // ---------- test run ----------
  function sendPrompt(text) {
    const p = $("prompt");
    p.value = text;
    p.dispatchEvent(new Event("input", { bubbles: true }));
    $("generate").click();
  }

  async function waitForJob(t0, onTick) {
    for (let k = 0; k < 900; k++) {
      await wait(1500);
      if (!running()) return null;
      let list = [];
      try { list = await (await fetch("/api/jobs", { cache: "no-store" })).json(); } catch (e) { continue; }
      const j = (list || []).filter((x) => (x.t_start || x.t || 0) >= t0 - 2)[0];
      if (!j) continue;
      if (onTick) onTick(j);
      if (["success", "error", "cancelled", "done"].includes(j.status)) return j;
    }
    return null;
  }

  function testImage(next) {
    show({
      step: "Test run", title: "Make a test picture?",
      text: "Comfy Studio types a simple prompt and presses send for you, so you see a whole run (about half a minute; longer if ComfyUI has to start).",
      buttons: [
        { label: "Skip test", quiet: true, go: () => next(null) },
        { label: "Make a test picture", primary: true, go: async () => {
          setMedia("image"); setMode("t2i");
          const t0 = Date.now() / 1000;
          sendPrompt(TEST_PROMPT);
          show({ el: $("prompt-card"), step: "Test run", title: "Working on it…",
                 text: "Progress shows above the chat box. You can keep using the app meanwhile.",
                 buttons: [{ label: "Skip waiting", quiet: true, go: () => next(null) }] });
          const j = await waitForJob(t0, (jj) => {
            if (running() && ui.title.textContent === "Working on it…") ui.text.textContent = "Status: " + (jj.message || jj.status);
          });
          if (!running()) return;
          const ok = j && ["success", "done"].includes(j.status);
          if (!ok) {
            return show({ step: "Test run", title: "That didn't finish",
                          text: ((j && j.error) || "No result yet.") + " The tour carries on; the Console shows details.",
                          buttons: [{ label: "Continue", primary: true, role: "next", go: () => next(null) }] });
          }
          await wait(600);
          show({ el: find("#gen-stage .gs-canvas") || find("#gen-stage"), step: "Test run", title: "Done!",
                 text: "It's saved in the Library. Under the picture: Edit this, Inpaint and Details.",
                 more: "Details shows the exact prompt that was sent, the engine and the settings, so you can reuse them.",
                 buttons: [{ label: "Continue", primary: true, role: "next", go: () => next(j) }] });
        } },
      ],
    });
  }

  // ---------- Edit + Inpaint deep dive ----------
  function editDive(job, next) {
    const editBtn = find("#gen-stage .gs-edit");
    show({
      step: "Edit", title: job && editBtn ? "Try editing your picture?" : "Want a look at editing?",
      text: job && editBtn ? "“Edit this” puts your test picture in as Picture 1 so you can change it." :
            "Edit changes a picture you add: new light, another outfit, a different background.",
      buttons: [
        { label: "Skip", quiet: true, go: next },
        { label: "Show me Edit", primary: true, go: async () => {
          if (job && editBtn) editBtn.click(); else setMode("edit");
          await wait(700);
          if (window.__composerCollapse) { try { window.__composerCollapse(); } catch (e) {} }
          const EDIT = [
            S("#images, .slots", "Picture 1", "Your picture is now Picture 1: the one that gets changed.",
              "Add more pictures as references (Picture 2, 3 ...). Example: Picture 1 is you, Picture 2 is a jacket: “put the jacket from Picture 2 on her”."),
            S("#kind-pill", "Edit or Reference", "Edit changes Picture 1. Reference makes a new scene using your pictures as a guide.",
              "Edit: “make it sunset” keeps everything else. Reference: “the woman from Picture 1 walking on a beach” builds a new picture with the same person. Auto understanding (inside this pill) adds notes so faces and outfits stay the same; leave it on."),
            S("#roles-pill", "Roles", "What each picture is for: person, outfit, background, style ...",
              "Auto works most of this out from your words. Set it yourself when a reference is used the wrong way (for example the background picture's person ends up in the result)."),
            S("#camera-open", "Camera in Edit", "Re-shoot your photo from another angle.", cameraMore()),
            S("#inpaint-pill", "Inpaint", "Paint only the part to change; the rest stays exactly as it was.",
              "Best for small fixes: a hand, an object, a face expression, one piece of clothing."),
            S("#mark-pill", "Mark an area", "Drag a box to say where something should go.", "Example: mark the table, then “put a vase of tulips here”."),
          ];
          runSteps(EDIT, "Edit", () => inpaintDive(next), {});
        } },
      ],
    });
  }

  function inpaintDive(next) {
    const pill = find("#inpaint-pill");
    if (!pill) {
      return show({ step: "Inpaint", title: "Inpaint isn't on this engine",
                    text: "The current engine can't paint just one area, so its pill is hidden. Engines that can (for example MiniMax H3 or Krea 2 Paint) show an Inpaint pill in Edit.",
                    buttons: [{ label: "Continue", primary: true, role: "next", go: next }] });
    }
    show({ el: pill, step: "Inpaint", title: "Open the painter?", text: "See the painting tools on your picture. Nothing is changed unless you press send.",
           buttons: [{ label: "Skip", quiet: true, go: next },
                     { label: "Open it", primary: true, role: "next", go: async () => {
                       pill.click();
                       await wait(600);
                       if (!find("#inpaint-sheet")) return next();
                       const IP = [
                         S("#ip-stage", "Paint over it", "Paint the area to change, a little bigger than the thing itself.",
                           "Covering a bit of the surroundings lets the new part blend in. Then close this and write what it should become: “a red scarf”, “open hand”."),
                         S("#ip-modes", "Brush and eraser", "Switch between painting and erasing."),
                         S("#ip-size", "Brush size", "Bigger for large areas, smaller for edges."),
                         S("#ip-split", "Separate areas", "Paint several areas that each get their own instruction.",
                           "Example: area 1 “sunglasses”, area 2 “a coffee cup in her hand”, in one go."),
                         S("#ip-undo", "Undo / redo", "Step back if you painted too much."),
                         S("#ip-done", "Done", "Keeps the painted area for your next send. Cancel leaves the picture untouched."),
                       ];
                       runSteps(IP, "Inpaint", () => { closePanels(); next(); }, { pass: true });
                     } }] });
  }

  // ---------- video / finish ----------
  function offerVideo(next) {
    setMode("t2i");
    show({
      step: "Almost done", title: "Video, or finish the tour?",
      text: "See the video controls (a test video takes a few minutes on an 8 GB card), or skip to the last few buttons.",
      buttons: [
        { label: "Finish the tour", primary: true, go: next },
        { label: "Show me Video", go: () => {
          setMedia("video");
          setTimeout(() => runSteps(VIDEO, "Video", () => {
            show({ step: "Video", title: "Make a short test video?",
                   text: "Same chat box. It takes a few minutes; the Console shows progress and you can close the tour meanwhile.",
                   buttons: [
                     { label: "Skip", quiet: true, go: () => { setMedia("image"); next(); } },
                     { label: "Make a test video", primary: true, go: () => {
                       sendPrompt("a red apple slowly rotating on a wooden kitchen table, soft window light");
                       setTimeout(() => { setMedia("image"); next(); }, 600);
                     } },
                   ] });
          }), 300);
        } },
      ],
    });
  }

  function finish() {
    runSteps(REST, "More", () => {
      show({ step: "", title: "You're all set",
             text: "Restart this tour any time from the Guide page. Tip: a locked seed plus small prompt changes is the fastest way to learn what each word does.",
             buttons: [{ label: "Done", primary: true, go: end }] });
    });
  }

  function start() {
    snapshot();
    goView("create");
    closePanels();
    if (window.__composerCollapse) { try { window.__composerCollapse(); } catch (e) {} }
    setMedia("image"); setMode("t2i");
    setTimeout(() => runSteps(BASICS, "Tour", () => loraDive(() => testImage((job) => editDive(job, () => offerVideo(finish)))) ), 250);
  }

  function ask() {
    show({
      step: "Welcome", title: "Would you like a quick tour?",
      text: "A short walk through the buttons, how LoRAs and Inpaint work, and an optional test picture. Tap More on any step for examples.",
      buttons: [
        { label: "Skip", quiet: true, go: end },
        { label: "Yes, show me", primary: true, go: start },
      ],
    });
  }

  window.startTour = () => { build(); start(); };

  function addGuideButton() {
    const g = $("view-guide");
    if (!g || g.querySelector(".tour-again")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip-btn tour-again";
    b.textContent = "Take the tour";
    b.addEventListener("click", () => { goView("create"); setTimeout(window.startTour, 200); });
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
