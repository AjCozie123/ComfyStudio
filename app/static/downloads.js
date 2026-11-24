/* ===== Downloads panel + Enhance model check v20261124 =====
   - A "Downloads" chip in the top bar appears while anything downloads (or after it finished / failed). It opens a
     panel that shows, for every file: what it is (Main model, Text encoder, Swap LoRA ...), which workflow it's for,
     its size, live progress with speed and time left, and what finished or failed (with Try again).
   - The Enhance pop-up says whether this PC has the Fast enhancer model and offers it (5.2 GB): without it, Fast
     uses the big Quality model, which can be very slow.
   Server: /api/install/progress (downloads), /api/setup/status (Set up steps), /api/install/plan?id=enhancer,
   /api/install/start {id, files}. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const gb = (v) => v == null ? "" : (v >= 1 ? v.toFixed(1) + " GB" : Math.round(v * 1000) + " MB");
  let st = null, setup = null, last = { file: "", got: 0, t: 0, speed: 0 }, timer = 0, seenDone = 0;

  /* ---------- the top-bar chip ---------- */
  function chip() {
    let c = $("dl-chip");
    if (c) return c;
    const bar = document.querySelector(".topbar");
    const status = $("status");
    if (!bar) return null;
    c = document.createElement("button");
    c.type = "button"; c.id = "dl-chip"; c.className = "dl-chip hidden";
    c.title = "Downloads: what's being downloaded for your workflows";
    c.innerHTML = '<span class="dl-ico" aria-hidden="true">⬇</span><span class="dl-chip-text"></span>';
    c.addEventListener("click", openSheet);
    bar.insertBefore(c, status || null);
    return c;
  }

  /* ---------- the panel ---------- */
  function sheet() {
    let s = $("dl-sheet");
    if (s) return s;
    s = document.createElement("div");
    s.id = "dl-sheet"; s.className = "sheet hidden"; s.setAttribute("role", "dialog"); s.setAttribute("aria-modal", "true");
    s.innerHTML = '<div class="sheet-backdrop"></div><div class="sheet-card dl-card"><div class="sheet-top"><div class="sheet-head">'
      + '<button type="button" class="icon-btn sm dl-x" aria-label="Close">✕</button><h2>Downloads</h2>'
      + '<button type="button" class="send-text dl-cancel hidden">Stop all</button></div>'
      + '<p class="sheet-lead">Everything Comfy Studio downloads for your workflows, from the official model pages, into your '
      + 'ComfyUI folders. You can keep using the app; big files take a while.</p></div><div class="dl-body"></div></div>';
    document.body.appendChild(s);
    s.querySelector(".sheet-backdrop").addEventListener("click", closeSheet);
    s.querySelector(".dl-x").addEventListener("click", closeSheet);
    s.querySelector(".dl-cancel").addEventListener("click", async () => {
      if (!window.confirm("Stop all downloads? What's already downloaded is kept, and pressing Set up or Download again carries on from there.")) return;
      try { await fetch("/api/install/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) {}
      poll();
    });
    return s;
  }
  function openSheet() { sheet().classList.remove("hidden"); render(); poll(); }
  function closeSheet() { const s = $("dl-sheet"); if (s) s.classList.add("hidden"); }
  window.openDownloads = openSheet;

  function row(it, state, extra) {
    const what = it.what ? '<b>' + esc(it.what) + "</b>" : "<b>Model file</b>";
    const forw = it.for ? '<span class="dl-for">for ' + esc(it.for) + "</span>" : "";
    return '<li class="dl-row is-' + state + '"><div class="dl-line">' + what + forw + "</div>"
      + '<div class="dl-file">' + esc(it.file) + (it.size_gb ? " · " + gb(it.size_gb) : "") + "</div>" + (extra || "") + "</li>";
  }

  function render() {
    const s = $("dl-sheet");
    if (!s || s.classList.contains("hidden") || !st) return;
    const body = s.querySelector(".dl-body");
    const a = st.active;
    const q = st.queue || [], done = (st.done || []).slice().reverse(), errs = (st.errors || []).slice().reverse();
    let html = "";
    if (setup && setup.state === "running") {
      const step = (setup.steps || []).find((x) => x.state === "running") || {};
      html += '<div class="dl-setup">Setting up <b>' + esc(setup.name) + "</b>: " + esc(step.label || "working") + (step.detail ? " · " + esc(step.detail) : "") + "</div>";
    }
    if (a) {
      const pct = a.total ? Math.floor(100 * (a.got || 0) / a.total) : null;
      const left = a.total && last.speed > 0 ? (a.total - a.got) / last.speed : null;
      const eta = left == null ? "" : left > 3600 ? Math.round(left / 360) / 10 + " h left" : left > 90 ? Math.round(left / 60) + " min left" : Math.round(left) + " s left";
      const sp = last.speed > 0 ? (last.speed / 1e6).toFixed(1) + " MB/s" : "starting…";
      html += '<h3 class="dl-h">Downloading now</h3><ul class="dl-list">' + row(Object.assign({}, a, { size_gb: a.size_gb || (a.total ? a.total / 1e9 : null) }), "active",
        '<div class="dl-bar"><i style="width:' + (pct || 2) + '%"></i></div><div class="dl-meta">' + (pct != null ? pct + "%" : "") + " · "
        + gb((a.got || 0) / 1e9) + (a.total ? " of " + gb(a.total / 1e9) : "") + " · " + sp + (eta ? " · " + eta : "") + "</div>") + "</ul>";
    }
    if (q.length) {
      const tot = q.reduce((n, x) => n + (x.size_gb || 0), 0);
      html += '<h3 class="dl-h">Waiting (' + q.length + (tot ? ", about " + gb(tot) : "") + ')</h3><ul class="dl-list">' + q.map((x) => row(x, "waiting")).join("") + "</ul>";
    }
    if (errs.length) {
      html += '<h3 class="dl-h">Didn\'t finish</h3><ul class="dl-list">' + errs.slice(0, 8).map((x) => row(x, "error",
        '<p class="dl-err">' + esc(x.error) + "</p>" + (x.for_id ? '<button type="button" class="chip-btn dl-retry" data-id="' + esc(x.for_id) + '" data-file="' + esc(x.file) + '">Try again</button>' : ""))).join("") + "</ul>";
    }
    if (done.length) {
      html += '<h3 class="dl-h">Finished</h3><ul class="dl-list">' + done.slice(0, 12).map((x) => row(x, "done",
        '<div class="dl-meta">✓ ' + (x.note === "already there" ? "Already on this PC" : "Downloaded") + "</div>")).join("") + "</ul>";
    }
    if (!html) html = '<p class="dl-empty">Nothing is downloading. When you press <b>Set up</b> on a workflow (Workflows page) or download the Enhance model, each file shows here: what it is, which workflow needs it, its size and progress.</p>';
    body.innerHTML = html;
    s.querySelector(".dl-cancel").classList.toggle("hidden", !(a || q.length));
    body.querySelectorAll(".dl-retry").forEach((b) => b.addEventListener("click", () => startFiles(b.dataset.id, [b.dataset.file])));
  }

  function paintChip() {
    const c = chip();
    if (!c || !st) return;
    const a = st.active, q = st.queue || [];
    const errs = (st.errors || []).filter((e) => Date.now() / 1000 - (e.t || 0) < 3600);
    const recentDone = (st.done || []).filter((d) => Date.now() / 1000 - (d.t || 0) < 600);
    let text = "", cls = "";
    if (a) {
      const pct = a.total ? Math.floor(100 * (a.got || 0) / a.total) : null;
      text = (pct != null ? pct + "%" : "…") + (q.length && window.innerWidth > 480 ? " · " + (q.length + 1) + " files" : "");
      cls = "is-busy";
    } else if (setup && setup.state === "running") { text = "Setting up"; cls = "is-busy"; }
    else if (errs.length) { text = "Check"; cls = "is-error"; }
    else if (recentDone.length) { text = "Done"; cls = "is-done"; }
    c.classList.toggle("hidden", !text);
    c.className = "dl-chip " + cls + (text ? "" : " hidden");
    c.querySelector(".dl-chip-text").textContent = text;
    c.setAttribute("aria-label", "Downloads: " + (text || "none"));
    if (recentDone.length > seenDone && seenDone && !a && typeof toast === "function") toast("Download finished: " + (recentDone[recentDone.length - 1].what || recentDone[recentDone.length - 1].file), 3500);
    seenDone = recentDone.length;
  }

  async function poll() {
    clearTimeout(timer);
    try { st = await (await fetch("/api/install/progress", { cache: "no-store" })).json(); } catch (e) { st = null; }
    try { setup = await (await fetch("/api/setup/status", { cache: "no-store" })).json(); } catch (e) { setup = null; }
    if (st && st.active) {
      const a = st.active, now = Date.now() / 1000;
      if (last.file === a.file && now > last.t && a.got >= last.got) {
        const inst = (a.got - last.got) / (now - last.t);
        last.speed = last.speed ? last.speed * 0.7 + inst * 0.3 : inst;
      } else last.speed = 0;
      last.file = a.file; last.got = a.got || 0; last.t = now;
    }
    paintChip();
    render();
    const busy = st && (st.active || (st.queue || []).length);
    const open = $("dl-sheet") && !$("dl-sheet").classList.contains("hidden");
    timer = setTimeout(poll, busy || open || (setup && setup.state === "running") ? 1500 : 12000);
  }
  window.__pollDownloads = poll;

  async function startFiles(id, files) {
    try {
      const r = await fetch("/api/install/start", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: id, files: files }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Couldn't start the download.");
      if (typeof toast === "function") toast("Downloading: see Downloads (top right)", 3000);
      poll();
      return true;
    } catch (e) { if (typeof showError === "function") showError(e.message); return false; }
  }

  /* ---------- Enhance: is the Fast model here? ---------- */
  let peInfo = null;
  async function enhancerInfo(force) {
    if (peInfo && !force) return peInfo;
    try { peInfo = ((await (await fetch("/api/install/plan?id=enhancer")).json()).items || [])[0] || null; } catch (e) { peInfo = null; }
    return peInfo;
  }
  async function paintEnhance() {
    const pop = $("quality-pop");
    if (!pop) return;
    let box = $("pe-models");
    if (!box) {
      box = document.createElement("div");
      box.id = "pe-models"; box.className = "pe-models";
      pop.appendChild(box);
    }
    const it = await enhancerInfo(true);
    if (!it) { box.innerHTML = ""; return; }
    const fast = it.files.find((f) => f.key === "fast"), q = it.files.find((f) => f.key === "quality");
    const busy = st && [st.active].concat(st.queue || []).some((x) => x && /qwen3vl_4b|qwen3\.5_9b/.test(x.file || ""));
    const line = (f, name, why) => '<div class="pe-model ' + (f.present ? "is-ok" : "is-miss") + '"><span>' + (f.present ? "✓" : "○") + "</span><div><b>" + name
      + "</b><small>" + (f.present ? "On this PC" + (f.using ? " (" + esc(String(f.using).split(/[\\/]/).pop()) + ")" : "") : why) + "</small></div>"
      + (!f.present && f.url ? '<button type="button" class="chip-btn" data-pe-dl="' + esc(f.file) + '">' + (busy ? "Downloading…" : "Download " + gb(f.size_gb)) + "</button>" : "") + "</div>";
    box.innerHTML = '<p class="pop-label">Enhancer models</p>'
      + line(fast, "Fast", "Not on this PC: Fast then uses the big Quality model, which is slow on some cards.")
      + line(q, "Quality", "Not on this PC: needed for Quality (and for Fast without its own model).");
    box.querySelectorAll("[data-pe-dl]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "Starting…";
      if (await startFiles("enhancer", [b.dataset.peDl])) openSheet();
      setTimeout(() => paintEnhance(), 1500);
    }));
  }

  function bind() {
    chip();
    poll();
    const pill = $("quality-pill");
    if (pill) pill.addEventListener("click", () => setTimeout(paintEnhance, 50));
    // the old one-line bar on Workflows opens the panel too
    document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("#inst-bar .inst-text")) openSheet(); });
    // a Set up started anywhere: show the panel's chip at once
    const ps = window.__pollSetup;
    if (typeof ps === "function") window.__pollSetup = function () { setTimeout(poll, 600); return ps.apply(this, arguments); };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
})();
