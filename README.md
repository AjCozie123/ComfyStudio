# Comfy Studio

A simple, clean desktop front end for **ComfyUI** that runs on your own PC. Pick an engine (MiniMax H3, Krea 2,
FLUX.2 Klein, MOSS, Qwen Image, LTX, or any workflow you upload), write a prompt, and go.

## Get started

### Method 1: Download (easiest)
1. At the top of this page click the green **Code** button, then **Download ZIP**.
2. Unzip it anywhere, for example `C:\ComfyStudio`.
3. Double-click **Install Comfy Studio.bat**. It sets everything up and puts a **Comfy Studio** shortcut on your
   desktop, then opens the app.

### Method 2: With Git (easy updates)
1. Click the green **Code** button, stay on the **Local** tab, choose **HTTPS** and copy the link.
2. Open **Command Prompt** in the folder where you want it (for example `C:\`) and run:
   ```
   git clone https://github.com/AjCozie123/ComfyStudio.git
   ```
3. Open the new **ComfyStudio** folder and double-click **Install Comfy Studio.bat**.

To update later, open Command Prompt in that folder and run `git pull` (or just use the app's own Update button).

### Then, inside the app
Open **Workflows**. Every engine shows what it still needs, and the app downloads it for you with one click: the
models from their official pages and any ComfyUI node packs. You never have to hunt for files.

**You only ever need two files:** **Install Comfy Studio.bat** (once) and **Start Comfy Studio.bat** (or the desktop
shortcut). Everything else lives in the `app` folder, and you can ignore it.

## What you need
- Windows 10/11 with an NVIDIA GPU (8 GB VRAM or more is best; MiniMax H3 video also needs about 32 GB of RAM)
- [ComfyUI](https://www.comfy.org/download) (portable or desktop), **updated to the latest version**, running.
  Krea 2 Edit and LTX 2.3 use nodes that only recent ComfyUI versions have.
- Python 3.10+ ([python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH")
- Optional: [Git for Windows](https://git-scm.com/download/win). Without it, node packs download as zips.

Comfy Studio finds ComfyUI's folders by itself once ComfyUI is running (saved in `app\data\settings.json`).

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

**Optional: swap LoRAs.** Head, face and body swaps work on every edit engine from the prompt alone ("head swap",
"put the woman from Picture 2 into Picture 1"; Picture 1 is the photo kept). For stronger swaps, put these LoRAs from
[BFS Best Face Swap](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) in `ComfyUI/models/loras`; the app uses them
by itself when it sees a swap:

| Engine | Head / face swap | Body swap |
|---|---|---|
| Krea 2 Edit | `bfs_head_swap_v1.1_krea2` | `bfs_body_swap_v1_krea2` (+ the head swap) |
| FLUX.2 Klein 9B | `bfs_head_v1_flux-klein_9b_step3500_rank128` | - |
| Qwen Image Edit 2509 | `bfs_head_v3_qwen_image_edit_2509` | - |
| Qwen Image 2.1 | `bfs_head_v1.1_qwen_2.1` | `bfs_body_swap_v1.0_qwen_2.1` |

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
