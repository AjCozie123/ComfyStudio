# Comfy Studio

A simple, clean desktop front end for **ComfyUI** that runs on your own PC. Pick an engine (MiniMax H3, Krea 2,
FLUX.2 Klein, MOSS, Qwen Image, LTX, or any workflow you upload), write a prompt, and go.

## What you need
- Windows 10/11 with an NVIDIA GPU (8 GB VRAM or more is best; MiniMax H3 video also needs about 32 GB of RAM)
- [ComfyUI](https://www.comfy.org/download) (portable or desktop), **updated to the latest version**, running.
  Krea 2 Edit and LTX 2.3 use nodes that only recent ComfyUI versions have.
- Python 3.10+ ([python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH")
- Optional: [Git for Windows](https://git-scm.com/download/win). Without it, node packs download as zips.

## Install
1. Download the latest release zip and unzip it anywhere (for example `C:\ComfyStudio`).
2. Start ComfyUI.
3. Double-click **Install Comfy Studio.bat**. It checks Python and ComfyUI, adds a desktop shortcut and starts the app.

Comfy Studio finds ComfyUI's folders by itself (saved in `data\settings.json`).

## Node packs, models and engines
Open **Workflows**.

1. **Node packs.** If any ComfyUI node pack an engine needs is missing, a banner lists it. **Install all** adds
   each one at the exact version Comfy Studio was tested with, plus its Python requirements (never torch or
   numpy). Then restart ComfyUI.
2. **Models.** Every engine shows whether it fits your PC (from your GPU, VRAM and RAM) and which files it still
   needs. **Download** fetches them from their Hugging Face pages straight into the right ComfyUI folder, after
   checking free space. Nothing downloads unless you press it.

| Engine | Node packs | Models from |
|---|---|---|
| MiniMax H3 (video, image edit) | MiniMax H3 AIO (included) and the packs it uses | Comfy-Org, lightx2v |
| Krea 2 (image, edit, style) | built into ComfyUI | Comfy-Org, conradlocke (Edit LoRAs) |
| Krea 2 Paint | krea2-anypaint | yijunwang2 |
| FLUX.2 Klein 9B | built into ComfyUI | Comfy-Org, black-forest-labs |
| MOSS, Qwen Image 2.1 | built into ComfyUI | Comfy-Org |
| Qwen Image Edit 2509 | ComfyUI-GGUF | QuantStack |
| LTX 2.3 video | ComfyUI-KJNodes | Lightricks, Comfy-Org, Kijai, valiantcat |

Node pack credits: MiniMax H3 AIO uses ComfyUI-Fantastic-MiniMaxH3-PromptBuilder (Adudeguyman),
ComfyUI-Spectrum-MiniMax-H3 and Comfyui_Minimax_h3_latent_Upscaler-Plus (xmarre),
ComfyUI-H3-Motion-Context-MultiRef-V3 (Aitrepreneur), ComfyUI-VideoHelperSuite (Kosinkadink),
ComfyUI-KJNodes (kijai), MaskVidExperiments (drozbay) and ComfyUI-NKD-Basic-Tools (Nekodificador).

Upload your own ComfyUI workflow with **+ Upload workflow**: the app reads it (pictures, video or audio inputs,
prompt, size, seed, steps, models) and gives it the same simple controls. In **Settings → Manual** every value in
a workflow can be changed.

## Updates
**Workflows → Check for updates** installs the newest release; your library and settings are kept, and the
app restores the previous version by itself if an update fails to start. Important fixes show a banner; you can
let them install automatically (only while nothing is generating).

Comfy Studio runs on this PC only (http://127.0.0.1:8198); nothing on it is reachable from other devices.
