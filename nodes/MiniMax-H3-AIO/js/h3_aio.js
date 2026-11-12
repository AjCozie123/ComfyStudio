// MiniMax H3 AIO layout: section headers, a "what to load" line under the mode, friendly labels, only the
// settings the chosen mode / switches use, and an (i) "How to use" pop-up on every H3 AIO node.

const { app } = window.comfyAPI.app;

// mode -> [kind, fast, long]; mirrors h3aio/pipeline.py MODES
const MODES = {
  "Text to Video": ["t2v", false, false],
  "Image to Video / First-Last Frame": ["i2v", false, false],
  "References to Video": ["r2v", false, false],
  "Fast Text to Video": ["t2v", true, false],
  "Fast Image to Video": ["i2v", true, false],
  "Fast References to Video": ["r2v", true, false],
  "Long Text to Video": ["t2v", false, true],
  "Long Image to Video": ["i2v", false, true],
  "Extend Video": ["extend", false, true],
  "Image Editing": ["edit", false, false],
  "Inpainting": ["inpaint", false, false],
};

const LOAD = {
  t2v: "Prompt only. Nothing to load.",
  i2v: "Media Loader: Picture 1 = first frame, Picture 2 = optional last frame.",
  i2v_long: "Media Loader: Picture 1 = first frame.",
  r2v: "Media Loader: Pictures / Videos / Audios -> <Picture 1>, <Video 1>, <Audio 1> in the prompt.",
  extend: "Media Loader: Video 1 = the video to continue (+ Pictures keep faces the same).",
  edit: "Media Loader: Picture 1 = the photo to edit (+ more Pictures as references). Result -> image output.",
  inpaint: "Media Loader: Video 1. 'replace' = what SAM3 finds; prompt + Pictures = what it becomes.",
};

// ---------------- pop-up guides ----------------

const FORMAT_TEXT = `<pre>integrated_multimodal_description: [Shot 1] Live-action, handheld phone video, a sunlit kitchen. A young woman with curly dark hair (S1) smiles at the camera and says: &lt;d&gt;[English] Morning, everyone!&lt;/d&gt; The camera pushes in with small amplitude at slow speed.
[Shot 2] At 00:03.500, the camera cuts to a close-up of the kettle.

overall_soundscape: Small kitchen room tone, the kettle starting to hiss. Not a voice-over.

non_diegetic_music: N/A</pre>`;
const FORMAT_REF = `<pre>subject_definitions:
&lt;Subject 1&gt; is the woman in &lt;Picture 1&gt;, with long auburn hair and a green raincoat.

summary:
[reference generation] &lt;Subject 1&gt; walks through a harbour town in the rain.

retention_analysis:
&lt;Subject 1&gt; (appears in [Shot 1]): fully_preserved - face, hair and raincoat kept.

detailed_description:
The target video is live-action with soft overcast light.
[Shot 1] &lt;Subject 1&gt; (S1) walks along the harbour wall ...

overall_soundscape: Rain on stone, gulls.

non_diegetic_music: N/A</pre>`;
const RULES = `<ul>
<li>Write in time order. <b>[Shot 1]</b> has no time; later cuts are <b>[Shot 2] At 00:03.500, the camera cuts to ...</b></li>
<li>Always say what the camera does (push in, pan, static shot...) - H3 drifts when told nothing.</li>
<li>Speakers get a fixed ID: <b>The woman (S1) says: &lt;d&gt;[English] words&lt;/d&gt;</b>. Only the spoken words go inside &lt;d&gt;.</li>
<li>Real recorded sound: describe the room and mic in overall_soundscape and add "Not a voice-over".</li>
</ul>`;

const MODE_GUIDE = {
  t2v: `<p>Prompt only. Uses the first/last-frame (fl2va) model.</p><h4>Prompt format</h4>${FORMAT_TEXT}${RULES}`,
  i2v: `<p>Load <b>Picture 1</b> (first frame) and optionally <b>Picture 2</b> (last frame) in the Media Loader. The official
    anchor line is added for you (auto prompt format). Describe what moves next: first-frame anchor -> action -> result.</p>
    <h4>Prompt format</h4>${FORMAT_TEXT}${RULES}`,
  r2v: `<p>Load people, places, objects (Pictures), motion or camera references (Videos) and voices (Audios). Refer to them as
    &lt;Picture 1&gt;, &lt;Video 1&gt;, &lt;Audio 1&gt; and define reusable things as &lt;Subject N&gt;. Uses the Ref2VA model.</p>
    <h4>Prompt format (six sections)</h4>${FORMAT_REF}${RULES}`,
  extend: `<p>Load the video to continue as <b>Video 1</b>. Its last 1.6 s are carried into the new clip in latent space, so it
    continues with no cut. Add Pictures to keep faces identical. Use the <b>Seamless Planner</b> for several clips.</p>${RULES}`,
  edit: `<p>Load the photo as <b>Picture 1</b> (more Pictures = extra references). The result goes to the <b>image</b>
    output. Up to 1080p.</p><pre>subject_definitions:
&lt;Picture 1&gt; is the base image being edited.
&lt;Subject 1&gt; is the woman from &lt;Picture 1&gt;.

summary:
[reference generation] The target image is an edited version of &lt;Picture 1&gt;.

retention_analysis:
&lt;Subject 1&gt;: fully_preserved - identity, face and hairstyle kept.

detailed_description:
Change only the jacket to red leather. Keep everything else identical.

overall_soundscape: Silence

non_diegetic_music: N/A</pre>`,
  inpaint: `<p>Load the video as <b>Video 1</b>. <b>replace</b> = what SAM3 should find (head, face, shirt, person...). The prompt
    and Pictures say what it becomes; everything else and the original sound are kept. Use 480p on 8 GB.</p>
    <pre>&lt;Subject 1&gt; is the character represented in &lt;Picture 1&gt;. The video is a close up face of &lt;Subject 1&gt;.</pre>`,
};

const GUIDES = {
  MiniMaxH3AIO: (node) => {
    const [kind, fast, long] = MODES[value(node, "mode")] || MODES["Text to Video"];
    return `<h3>${value(node, "mode")}</h3>${MODE_GUIDE[kind]}
      ${fast ? "<p><b>Fast:</b> drafts at a quarter of the pixels, then the learned latent upscaler + a short refine at full size. Best at 720p-832p.</p>" : ""}
      ${long ? "<p><b>Long:</b> connect the <b>H3 AIO Seamless Planner</b> (one story, split for you) or <b>H3 AIO Long Video</b> (one box per clip). With the planner the AIO prompt box is ignored.</p>" : ""}
      <h4>Side nodes</h4><ul><li><b>Speed & Quality</b>: steps, turbo, Spectrum, upscale detail, live preview.</li>
      <li><b>Models</b> / <b>LoRAs</b>: files, set once.</li><li><b>Live Preview</b>: watch it sample.</li></ul>
      <h4>Memory</h4><p>Prompts are encoded once and the 15 GB text encoder is released before the video model loads.
      Changing only seed / steps / speed never reloads it. If RAM is short the node frees memory first and tells you in <b>info</b>.
      Start ComfyUI without --cache-lru.</p>`;
  },
  MiniMaxH3AIOPlanner: () => `<h3>Seamless Planner</h3>
    <p>Write the whole video as one story. It is cut into clips of <b>seconds_per_clip</b> until <b>total_seconds</b>
    (max 7 clips), and every clip gets its own prompt in H3's official format. Read them in the <b>plan</b> output.</p>
    <h4>How the story is split</h4><ul>
    <li>Times you write: <b>[Shot 2] At 00:12.000, the camera cuts to ...</b>, <b>5-10s:</b>, <b>[0s-5s]</b>, <b>00:15</b> - each beat lands in the clip whose time it falls in.</li>
    <li>Or breaks: a line of <b>---</b>, or <b>Clip 2:</b> / <b>Scene 2:</b>.</li>
    <li>Or nothing: sentences are spread evenly (dialogue is never cut).</li></ul>
    <h4>What each clip gets</h4><ul>
    <li>The style at the start of [Shot 1]; the same overall_soundscape / music / subject definitions.</li>
    <li>Clip 2+: "continues with no cut from the carried-over frames", what that moment shows, and the same speakers (S1...).</li>
    <li>A lead-in to the next clip's first beat. Dialogue is never repeated.</li>
    <li>Cuts keep [Shot N] At times, rewritten relative to the clip.</li></ul>
    <p>Use with a <b>Long</b> mode or <b>Extend Video</b>. Plug <b>Seamless Settings</b> in to change how clips join.</p>${FORMAT_TEXT}`,
  MiniMaxH3AIOSeamless: () => `<h3>Seamless Settings</h3><ul>
    <li><b>carry_over</b>: how much of each clip's end is copied into the next clip's start (protected, in latent space -
    no decode/re-encode). 1.6 s is the tested default. 3.75 s / 5.9 s hold motion and voice steadier across the join but each
    extra clip renders that much more.</li>
    <li><b>audio_fade</b>: soft release of the carried sound into the new sound (0.2 s default).</li>
    <li><b>picture_blend</b>: cross-fade over the carried frames when the file is written. Full hides tiny differences.</li></ul>
    <p>All lengths land on H3's video and 40 Hz audio grids, so sound and picture stay in sync.</p>`,
  MiniMaxH3AIOLongVideo: () => `<h3>Long Video (manual)</h3><p>Set <b>extra_clips</b> and write what happens in each one. An
    empty box repeats the clip before. The AIO prompt is clip 1. For one big story split automatically, use the
    <b>Seamless Planner</b> instead.</p>${RULES}`,
  MiniMaxH3AIOSpeed: () => `<h3>Speed & Quality</h3><ul>
    <li><b>steps</b> 8 with turbo (its sweet spot is 6-8). Turbo off: 20+.</li>
    <li><b>spectrum</b>: forecasts some steps instead of running the model - faster, tiny differences.</li>
    <li><b>refine_strength</b> (Fast modes): Light keeps the draft, Strong adds the most detail.</li>
    <li><b>reference_detail</b>: bigger reference pictures = better likeness, a little slower.</li>
    <li><b>Live preview</b> is switched on/off on the <b>H3 Live Preview</b> node itself (off = no preview decoding, less VRAM).</li>
    <li><b>auto_prompt_format</b>: adds H3's official anchor / no-cut lines.</li></ul>`,
  MiniMaxH3AIOModels: () => `<h3>Models</h3><p>Picked automatically (smallest builds first). Text and Image modes use fl2va;
    References, Extend, Editing and Inpainting use ref2va. The int8 video VAE saves 2.2 GB of RAM. Keep the text encoder in
    RAM only with 64 GB+.</p>`,
  MiniMaxH3AIOLoras: () => `<h3>LoRAs</h3><p>Up to four extra LoRAs (style, realism, characters). The turbo LoRA is a switch in
    Speed & Quality. Put trigger words at the start of the prompt.</p>`,
};

// ---------------- layout ----------------

const SECTIONS = [["MODE", "mode"], ["PROMPT", "prompt"], ["VIDEO", "resolution"], ["INPAINT", "inpaint_target"]];

const LABELS = {
  use_negative: "negative prompt", negative_prompt: "negative", cfg: "negative strength (cfg)",
  duration_seconds: "duration (seconds)", aspect_ratio: "aspect", inpaint_target: "replace",
  speed_lora: "turbo LoRA", speed_lora_strength: "turbo strength", spectrum: "Spectrum speed-up",
  refine_strength: "upscale detail", reference_detail: "reference detail", live_preview: "live preview",
  auto_prompt_format: "auto prompt format", extra_clips: "extra clips", seconds_per_clip: "seconds per clip",
  total_seconds: "total seconds", prompt_format: "prompt format", carry_over: "carry-over", audio_fade: "audio fade",
  picture_blend: "picture blend", keep_text_encoder_loaded: "keep text encoder in RAM",
  model_text_image: "text / image model", model_references: "reference model", text_encoder: "text encoder",
  video_vae: "video VAE", audio_vae: "audio VAE", image_vae: "image VAE", speed_lora_name: "turbo LoRA",
};

const widget = (node, name) => node.widgets?.find((w) => w.name === name);
const value = (node, name) => widget(node, name)?.value;

function setShown(w, shown) {
  if (!w || shown === !w._h3Hidden) return;
  if (!shown) {
    w._h3Size = w.computeSize;
    w.computeSize = () => [0, -4];
  } else if (w._h3Size !== undefined) {
    w.computeSize = w._h3Size;
  } else {
    delete w.computeSize;
  }
  w._h3Hidden = !shown;
  w.hidden = !shown;
  if (w.element) w.element.style.display = shown ? "" : "none";
}

function css() {
  if (document.getElementById("h3aio-css")) return;
  const s = document.createElement("style");
  s.id = "h3aio-css";
  s.textContent = `
.h3-head{display:flex;align-items:center;gap:8px;height:22px;padding-top:6px;font:bold 11px Arial,sans-serif;
  letter-spacing:.1em;color:var(--h3-text);opacity:.85;user-select:none}
.h3-head:after{content:"";flex:1;border-top:1px solid var(--h3-line)}
.h3-hint{font:11px Arial,sans-serif;line-height:14px;color:var(--h3-dim);padding:3px 6px;border-radius:6px;
  background:rgba(127,127,127,.12);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.h3-info{display:flex;align-items:center;gap:6px;height:22px;padding:0 8px;border-radius:11px;cursor:pointer;
  font:12px Arial,sans-serif;color:var(--h3-text);background:rgba(127,127,127,.16);user-select:none;width:max-content}
.h3-info:hover{background:rgba(127,127,127,.3)}
.h3-modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:100000;display:flex;align-items:center;justify-content:center}
.h3-modal{width:min(760px,92vw);max-height:84vh;overflow:auto;background:#1e1e1e;color:#ddd;border:1px solid #555;
  border-radius:12px;padding:18px 22px;font:13px/1.55 Arial,sans-serif;position:relative}
.h3-modal h3{margin:0 0 8px;font-size:17px;color:#fff}.h3-modal h4{margin:14px 0 4px;font-size:13px;color:#fff}
.h3-modal pre{white-space:pre-wrap;background:#111;border:1px solid #333;border-radius:8px;padding:8px 10px;font:12px/1.45 Consolas,monospace;color:#cfe}
.h3-modal ul{margin:4px 0;padding-left:18px}.h3-modal li{margin:3px 0}
.h3-x{position:absolute;right:12px;top:8px;cursor:pointer;font-size:20px;color:#aaa}`;
  document.head.append(s);
}

function themed(el) {
  const L = window.LiteGraph || {};
  el.style.setProperty("--h3-line", L.WIDGET_OUTLINE_COLOR || "#666");
  el.style.setProperty("--h3-dim", L.WIDGET_SECONDARY_TEXT_COLOR || "#999");
  el.style.setProperty("--h3-text", L.WIDGET_TEXT_COLOR || "#ddd");
}

function addDom(node, name, el, height, before) {
  themed(el);
  const dom = node.addDOMWidget(name, "h3-ui", el, { serialize: false });
  dom.serialize = false;
  dom.computeSize = (width) => [width, height];
  node.widgets.splice(node.widgets.indexOf(dom), 1);
  node.widgets.splice(before ? node.widgets.indexOf(before) : 0, 0, dom);
  return dom;
}

function showGuide(html) {
  const bg = document.createElement("div");
  bg.className = "h3-modal-bg";
  const box = document.createElement("div");
  box.className = "h3-modal";
  box.innerHTML = `<span class="h3-x" title="close">×</span>${html}`;
  bg.append(box);
  const close = () => bg.remove();
  bg.onclick = (e) => { if (e.target === bg) close(); };
  box.querySelector(".h3-x").onclick = close;
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });
  document.body.append(bg);
}

function addInfo(node) {
  const el = document.createElement("div");
  el.className = "h3-info";
  el.textContent = "ⓘ  How to use";
  el.onclick = () => showGuide(GUIDES[node.type](node));
  addDom(node, "h3_info", el, 26, node.widgets?.[0]);
}

function tall(node, name, height) {
  const w = widget(node, name);
  if (!w || w._h3Tall) return;
  w._h3Tall = true;
  const base = w.computeSize;
  w.computeSize = (width) => [width, Math.max(height, base ? base.call(w, width)[1] : 0)];
}

function fit(node) {
  for (const w of node.widgets || []) if (LABELS[w.name]) w.label = LABELS[w.name];
  node.setSize([Math.max(node.size[0], 380), node.computeSize()[1]]);
  node.setDirtyCanvas(true, true);
}

function hook(node, names, apply) {
  for (const name of names) {
    const w = widget(node, name);
    if (!w || w._h3Hooked) continue;
    w._h3Hooked = true;
    const cb = w.callback;
    w.callback = function () {
      const r = cb?.apply(this, arguments);
      apply(node);
      return r;
    };
  }
}

// ---------------- per node ----------------

function applyAio(node) {
  const [kind, fast, long] = MODES[value(node, "mode")] || MODES["Text to Video"];
  const neg = !!value(node, "use_negative");
  const show = {
    negative_prompt: neg, cfg: neg,
    duration_seconds: !["edit", "inpaint"].includes(kind),
    aspect_ratio: ["t2v", "i2v", "r2v"].includes(kind),
    inpaint_target: kind === "inpaint",
  };
  for (const w of node.widgets || []) if (w.name in show) setShown(w, show[w.name]);
  const names = (node.widgets || []).map((w) => w.name);
  for (const [, first] of SECTIONS) {
    const head = widget(node, "h3_head_" + first);
    if (!head) continue;
    const start = names.indexOf(first);
    const next = SECTIONS.map((s) => names.indexOf("h3_head_" + s[1])).filter((i) => i > start).sort((a, b) => a - b)[0];
    setShown(head, node.widgets.slice(start, next).filter((w) => !w.name.startsWith("h3_")).some((w) => !w._h3Hidden));
  }
  const p = widget(node, "prompt");
  if (p) p.label = long ? "prompt (clip 1 - ignored when the Seamless Planner is connected)" : "prompt";
  const d = widget(node, "duration_seconds");
  if (d) d.label = kind === "extend" ? "first added clip (seconds)" : "duration (seconds)";
  const hint = widget(node, "h3_hint");
  if (hint) {
    hint.element.textContent = LOAD[kind === "i2v" && long ? "i2v_long" : kind]
      + (fast ? " Fast: drafts small, then a learned upscale." : "")
      + (long ? " Extra clips: Seamless Planner / Long Video." : "");
  }
  fit(node);
}

function buildAio(node) {
  for (const [title, first] of SECTIONS) {
    const anchor = widget(node, first);
    if (!anchor) continue;
    const el = document.createElement("div");
    el.className = "h3-head";
    el.textContent = title;
    addDom(node, "h3_head_" + first, el, 26, anchor);
  }
  const hint = document.createElement("div");
  hint.className = "h3-hint";
  addDom(node, "h3_hint", hint, 36, node.widgets[node.widgets.indexOf(widget(node, "mode")) + 1]);
  tall(node, "prompt", 150);
  hook(node, ["mode", "use_negative"], applyAio);
  // Sampling previews go to the separate H3 Live Preview node, so this node keeps its size.
  Object.defineProperty(node, "imgs", { get() { return undefined; }, set() {}, configurable: true });
}

function applySpeed(node) {
  setShown(widget(node, "speed_lora_strength"), !!value(node, "speed_lora"));
  fit(node);
}

function applyLong(node) {
  const n = Number(value(node, "extra_clips")) || 1;
  for (const w of node.widgets || []) {
    const m = /^clip_(\d+)_prompt$/.exec(w.name);
    if (m) setShown(w, Number(m[1]) <= n);
  }
  fit(node);
}

function buildLong(node) {
  for (const w of node.widgets || []) {
    const m = /^clip_(\d+)_prompt$/.exec(w.name);
    if (m && w.inputEl) w.inputEl.placeholder = `Extra clip ${m[1]}: what happens next (empty = same as the clip before)`;
  }
  hook(node, ["extra_clips"], applyLong);
}

function buildPlanner(node) {
  tall(node, "story", 260);
  const w = widget(node, "story");
  if (w?.inputEl) {
    w.inputEl.placeholder = "The whole video, in time order. Optional marks: [Shot 2] At 00:12.000, ... | 5-10s: | --- " +
      "| overall_soundscape: ... | non_diegetic_music: ...";
  }
}

const NODES = {
  MiniMaxH3AIO: [buildAio, applyAio],
  MiniMaxH3AIOSpeed: [(n) => hook(n, ["speed_lora"], applySpeed), applySpeed],
  MiniMaxH3AIOLongVideo: [buildLong, applyLong],
  MiniMaxH3AIOPlanner: [buildPlanner, fit],
  MiniMaxH3AIOSeamless: [() => {}, fit],
  MiniMaxH3AIOModels: [() => {}, fit],
  MiniMaxH3AIOLoras: [() => {}, fit],
};

function build(node) {
  if (node._h3Built) return;
  node._h3Built = true;
  css();
  const [extra] = NODES[node.type];
  extra(node);
  addInfo(node);
}

app.registerExtension({
  name: "MiniMaxH3AIO.Layout",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const entry = NODES[nodeData.name];
    if (!entry) return;
    const [, apply] = entry;
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      build(this);
      apply(this);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      build(this);
      apply(this);
      return r;
    };
  },
});
