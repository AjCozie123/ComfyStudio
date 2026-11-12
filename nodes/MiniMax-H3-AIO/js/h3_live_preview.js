// H3 Live Preview: paints the sampling preview large while the MiniMax H3 AIO node runs.
// Frames come from the KJNodes preview override the AIO attaches (`kj_preview_override`: base64 JPEG + step/total)
// and, as a fallback, ComfyUI's own `b_preview` stream. The `enabled` switch turns the whole preview off.

const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;

const NODE = "MiniMaxH3AIOLivePreview";

function themed(el) {
  const L = window.LiteGraph || {};
  el.style.setProperty("--lp-bg", L.WIDGET_BGCOLOR || "#222");
  el.style.setProperty("--lp-line", L.WIDGET_OUTLINE_COLOR || "#666");
  el.style.setProperty("--lp-dim", L.WIDGET_SECONDARY_TEXT_COLOR || "#999");
  el.style.setProperty("--lp-text", L.WIDGET_TEXT_COLOR || "#ddd");
}

app.registerExtension({
  name: "MiniMaxH3AIO.LivePreview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      const wrap = document.createElement("div");
      themed(wrap);
      wrap.style.cssText += ";position:relative;width:100%;height:100%;box-sizing:border-box;display:flex;" +
        "align-items:center;justify-content:center;background:var(--lp-bg);border:1px solid var(--lp-line);" +
        "border-radius:8px;overflow:hidden";
      const img = document.createElement("img");
      img.style.cssText = "max-width:100%;max-height:100%;object-fit:contain;display:none";
      const hint = document.createElement("div");
      hint.style.cssText = "color:var(--lp-dim);font:12px Arial,sans-serif;text-align:center;padding:12px";
      const bar = document.createElement("div");
      bar.style.cssText = "position:absolute;left:0;bottom:0;height:3px;width:0;background:var(--lp-text);opacity:.7;transition:width .12s";
      const tag = document.createElement("div");
      tag.style.cssText = "position:absolute;left:8px;top:6px;color:var(--lp-dim);font:11px Arial,sans-serif;" +
        "background:rgba(0,0,0,.45);padding:1px 7px;border-radius:9px;display:none";
      wrap.append(img, hint, bar, tag);

      const dom = this.addDOMWidget("live_preview", "live_preview", wrap, {});
      dom.serialize = false;
      dom.computeSize = (width) => [width, Math.max(200, this.size[1] - 70)];
      this.size = [560, 380];
      this.resizable = true;

      const enabledWidget = () => this.widgets?.find((w) => w.name === "enabled");
      const isOn = () => enabledWidget()?.value !== false;
      const idle = () => {
        img.style.display = "none";
        tag.style.display = "none";
        hint.style.display = "";
        hint.textContent = isOn()
          ? "The clip appears here while it samples."
          : "Live preview is off - no preview decoding, less VRAM. Switch 'enabled' on to watch.";
      };
      idle();
      const ew = enabledWidget();
      if (ew) {
        const cb = ew.callback;
        ew.callback = function () { const res = cb?.apply(this, arguments); idle(); return res; };
      }

      let url = null;
      const show = (src, label) => {
        if (!isOn()) return;
        img.src = src;
        img.style.display = "";
        hint.style.display = "none";
        tag.textContent = label;
        tag.style.display = "";
      };
      this._lpKJ = (e) => {
        const d = e.detail || {};
        if (d.image && (d.mime || "image/jpeg").startsWith("image/")) {
          show(`data:${d.mime || "image/jpeg"};base64,${d.image}`, d.total ? `step ${d.step}/${d.total}` : "live");
        }
        if (d.total) {
          bar.style.width = Math.min(100, Math.round(((d.step || 0) / d.total) * 100)) + "%";
        }
      };
      this._lpFrame = (e) => {
        const blob = e.detail?.blob ?? e.detail;
        if (!(blob instanceof Blob)) return;
        if (url) URL.revokeObjectURL(url);
        url = URL.createObjectURL(blob);
        show(url, "live");
      };
      this._lpProgress = (e) => {
        const { value = 0, max = 0 } = e.detail || {};
        const p = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
        bar.style.width = p + "%";
        if (p >= 100) setTimeout(() => { bar.style.width = "0"; }, 400);
      };
      api.addEventListener("kj_preview_override", this._lpKJ);
      api.addEventListener("b_preview", this._lpFrame);
      api.addEventListener("b_preview_with_metadata", this._lpFrame);
      api.addEventListener("progress", this._lpProgress);
      Object.defineProperty(this, "imgs", { get() { return undefined; }, set() {}, configurable: true });
      return r;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      api.removeEventListener("kj_preview_override", this._lpKJ);
      api.removeEventListener("b_preview", this._lpFrame);
      api.removeEventListener("b_preview_with_metadata", this._lpFrame);
      api.removeEventListener("progress", this._lpProgress);
      return onRemoved?.apply(this, arguments);
    };
  },
});
