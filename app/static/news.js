/* ===== News page + "New" pop-ups v20261124 =====
   News tab (between Guide and Workflows): What's new (static/news.json: where to find it, how to try it, Auto vs
   Manual), New workflows (with their set-up state on this PC), Coming soon, and All updates (the app's GitHub
   releases and commits, via /api/news/remote: read-only, nothing is installed from here).
   Anything new you haven't seen gets a dot on the tab and one small pop-up with a "Try it" button that takes you to
   the right place (the Swap sheet, the Workflows card, the pills ...). Seen items: localStorage cs.news.seen. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const page = $("view-news");
  if (!page || typeof setView !== "function") return;
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const seenGet = () => { try { return JSON.parse(localStorage.getItem("cs.news.seen") || "[]"); } catch (e) { return []; } };
  const seenAdd = (ids) => { try { const s = new Set(seenGet()); ids.forEach((i) => s.add(i)); localStorage.setItem("cs.news.seen", JSON.stringify([...s])); } catch (e) {} };
  let NEWS = null, remote = null, tab = "releases";

  async function loadNews() {
    if (NEWS) return NEWS;
    try { NEWS = await (await fetch("/static/news.json?v=" + Date.now(), { cache: "no-store" })).json(); }
    catch (e) { NEWS = { items: [], soon: [] }; }
    return NEWS;
  }
  function unseen() { const s = new Set(seenGet()); return ((NEWS && NEWS.items) || []).filter((i) => !s.has(i.id)); }
  function syncDot() { const d = $("news-dot"); if (d) d.classList.toggle("hidden", !unseen().length); }

  /* ---------- the page ---------- */
  const base = setView;
  setView = function (view) {
    const r = base.apply(this, arguments);
    const on = view === "news";
    page.classList.toggle("hidden", !on);
    document.body.classList.toggle("view-news", on);
    if (on) {
      document.body.classList.remove("view-create");
      $("view-create").classList.add("hidden");
      document.querySelectorAll(".views button").forEach((b) => b.classList.toggle("is-on", b.dataset.view === "news"));
      window.scrollTo(0, 0);
      render();
      closePop();
    }
    return r;
  };
  page.querySelectorAll(".gd-jump a").forEach((a) => a.addEventListener("click", (e) => {
    const t = document.querySelector(a.getAttribute("href"));
    if (!t) return;
    e.preventDefault();
    t.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  const KIND = { workflow: "New workflow", feature: "New", fix: "Fixed" };
  function card(it) {
    const how = (it.how || []).map((s) => "<li>" + esc(s) + "</li>").join("");
    return '<article class="nw-card" id="nw-' + esc(it.id) + '">'
      + '<div class="nw-top"><span class="nw-kind nw-' + esc(it.kind) + '">' + esc(KIND[it.kind] || "New") + "</span>"
      + '<span class="nw-meta">' + esc(it.version ? "v" + it.version : "") + (it.date ? " · " + esc(it.date) : "") + "</span></div>"
      + "<h3>" + esc(it.title) + "</h3><p>" + esc(it.summary) + "</p>"
      + (it.where ? '<p class="nw-where"><b>Where:</b> ' + esc(it.where) + "</p>" : "")
      + (how ? '<div class="nw-how"><b>How to try it</b><ol>' + how + "</ol></div>" : "")
      + ((it.auto || it.manual) ? '<div class="nw-modes">'
        + (it.auto ? '<div><span class="nw-mode">Auto</span><p>' + esc(it.auto) + "</p></div>" : "")
        + (it.manual ? '<div><span class="nw-mode nw-manual">Manual</span><p>' + esc(it.manual) + "</p></div>" : "") + "</div>" : "")
      + (it.setup ? '<p class="nw-setup">' + esc(it.setup) + "</p>" : "")
      + (it.try ? '<div class="nw-actions"><button type="button" class="chip-btn primary" data-try="' + esc(it.try) + '" data-id="'
        + esc(it.id) + '">' + esc(it.try_label || "Try it") + "</button></div>" : "")
      + "</article>";
  }

  async function render() {
    await loadNews();
    const ver = $("news-version");
    $("news-list").innerHTML = (NEWS.items || []).map(card).join("") || '<p class="muted">Nothing new yet.</p>';
    $("news-soon").innerHTML = (NEWS.soon || []).map((s) => '<div class="nw-soon"><b>' + esc(s.title) + "</b><p>" + esc(s.text) + "</p></div>").join("");
    bindTry(page);
    seenAdd((NEWS.items || []).map((i) => i.id));
    syncDot();
    renderWorkflows();
    loadRemote().then((d) => { if (ver && d && d.version) ver.textContent = "You have version " + d.version + "."; });
  }

  async function renderWorkflows() {
    const box = $("news-wf");
    const wfs = (NEWS.items || []).filter((i) => i.workflow);
    if (!wfs.length) { box.innerHTML = '<p class="muted">No new workflows right now.</p>'; return; }
    let cards = [];
    try { cards = (await (await fetch("/api/workflows")).json()).items || []; } catch (e) {}
    box.innerHTML = wfs.map((i) => {
      const c = cards.find((x) => x.id === i.workflow) || {};
      const ready = c.id && !(c.missing_custom || []).length && c.fit !== "no";
      const state = !c.id ? "" : (ready ? '<span class="nw-ok">Ready on this PC</span>' : '<span class="nw-warn">Needs Set up</span>');
      return '<button type="button" class="nw-wf" data-try="workflows:' + esc(i.workflow) + '"><span class="nw-wf-name">'
        + esc(c.name || i.title) + "</span>" + state + '<small>' + esc(i.summary) + "</small></button>";
    }).join("");
    bindTry(box);
  }

  async function loadRemote() {
    const box = $("news-remote");
    if (!remote) {
      try { remote = await (await fetch("/api/news/remote")).json(); } catch (e) { remote = { error: "Couldn't load the update list." }; }
    }
    paintRemote();
    return remote;
  }
  function md(text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    let html = "", list = false;
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    lines.forEach((l) => {
      const li = l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
      if (li) { if (!list) { html += "<ul>"; list = true; } html += "<li>" + inline(li[1]) + "</li>"; return; }
      if (list) { html += "</ul>"; list = false; }
      if (/^#{1,4}\s/.test(l)) html += "<h4>" + inline(l.replace(/^#+\s*/, "")) + "</h4>";
      else if (l.trim()) html += "<p>" + inline(l) + "</p>";
    });
    return html + (list ? "</ul>" : "");
  }
  function paintRemote() {
    const box = $("news-remote");
    if (!box || !remote) return;
    document.querySelectorAll("#news-tabs button").forEach((b) => b.classList.toggle("is-on", b.dataset.tab === tab));
    const err = remote.error ? '<p class="muted">' + esc(remote.error) + "</p>" : "";
    if (tab === "releases") {
      box.innerHTML = err + (remote.releases || []).map((r, i) =>
        '<details class="nw-rel"' + (i === 0 ? " open" : "") + "><summary><b>" + esc(r.name) + "</b><span>" + esc(r.date) + "</span></summary>"
        + '<div class="nw-rel-body">' + md(r.body) + "</div></details>").join("");
    } else {
      box.innerHTML = err + '<ul class="nw-commits">' + (remote.commits || []).map((c) =>
        "<li><span>" + esc(c.date) + "</span>" + esc(c.msg) + "</li>").join("") + "</ul>";
    }
  }
  document.querySelectorAll("#news-tabs button").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; paintRemote(); }));

  /* ---------- "Try it": take the user to the right place ---------- */
  function go(action) {
    const a = String(action || "");
    if (a === "swap") {
      setView("create");
      if (typeof setMode === "function") setMode("edit");
      setTimeout(() => {
        if (window.openSwapSheet) window.openSwapSheet();
        const n = (typeof state !== "undefined" && state.images) ? state.images.length : 0;
        if (n < 2 && typeof toast === "function") toast(n ? "Now add the new person with + (Picture 2)" : "Add your photo with +, then the new person", 3500);
      }, 250);
    } else if (a === "downloads") {
      if (window.openDownloads) window.openDownloads();
    } else if (a === "enhance") {
      setView("create");
      setTimeout(() => { const p = $("quality-pill"); if (p) p.click(); }, 250);
    } else if (a === "create" || a === "edit") {
      setView("create");
      if (a === "edit" && typeof setMode === "function") setMode("edit");
      const hud = $("hud");
      if (hud) { hud.classList.remove("nw-flash"); void hud.offsetWidth; hud.classList.add("nw-flash"); }
    } else if (a.startsWith("workflows")) {
      setView("workflows");
      const id = a.split(":")[1];
      if (id) setTimeout(() => flashWorkflow(id), 700);
    } else if (a.startsWith("guide:")) {
      setView("guide");
      setTimeout(() => { const t = $(a.slice(6)); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); }, 200);
    }
  }
  function flashWorkflow(id, tries) {
    const it = ((NEWS && NEWS.items) || []).find((i) => i.workflow === id);
    const name = (it && it.title) || "";
    const h = [...document.querySelectorAll("#view-workflows h3, #view-workflows h2, #view-workflows .wf-name")]
      .find((x) => /character swap/i.test(x.textContent) && name && /swap/i.test(name) || (name && x.textContent.indexOf(name) >= 0));
    const c = h && (h.closest("article, .wf-card, li") || h);
    if (!c) { if ((tries || 0) < 6) setTimeout(() => flashWorkflow(id, (tries || 0) + 1), 400); return; }
    c.scrollIntoView({ behavior: "smooth", block: "center" });
    c.classList.remove("nw-flash"); void c.offsetWidth; c.classList.add("nw-flash");
  }
  function bindTry(root) {
    root.querySelectorAll("[data-try]").forEach((b) => {
      if (b._nw) return;
      b._nw = true;
      b.addEventListener("click", () => { if (b.dataset.id) seenAdd([b.dataset.id]); closePop(); go(b.dataset.try); });
    });
  }

  /* ---------- the "New" pop-up (one at a time, newest unseen) ---------- */
  let pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } }
  function showPop() {
    const it = unseen()[0];
    if (!it || pop || document.body.classList.contains("view-news")) return;
    if (document.querySelector(".tour-root:not(.hidden)")) { setTimeout(showPop, 8000); return; }   // never over the tour
    pop = document.createElement("div");
    pop.className = "nw-pop";
    pop.setAttribute("role", "status");
    pop.innerHTML = '<button type="button" class="icon-btn sm nw-pop-x" aria-label="Close">✕</button>'
      + '<span class="nw-kind nw-' + esc(it.kind) + '">' + esc(KIND[it.kind] || "New") + "</span>"
      + "<b>" + esc(it.title) + "</b><p>" + esc(it.summary) + "</p>"
      + '<div class="nw-actions">' + (it.try ? '<button type="button" class="chip-btn primary" data-try="' + esc(it.try) + '" data-id="' + esc(it.id) + '">' + esc(it.try_label || "Try it") + "</button>" : "")
      + '<button type="button" class="chip-btn" data-more="1">How it works</button></div>';
    document.body.appendChild(pop);
    pop.querySelector(".nw-pop-x").addEventListener("click", () => { seenAdd([it.id]); closePop(); syncDot(); setTimeout(showPop, 1500); });
    pop.querySelector("[data-more]").addEventListener("click", () => {
      closePop(); setView("news");
      setTimeout(() => { const t = $("nw-" + it.id); if (t) t.scrollIntoView({ behavior: "smooth", block: "center" }); }, 250);
    });
    bindTry(pop);
  }

  loadNews().then(() => { syncDot(); setTimeout(showPop, 4000); });
})();
