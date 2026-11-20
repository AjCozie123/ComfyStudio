# Comfy Studio

A simple, clean desktop front end for **ComfyUI** that runs on your own PC. Pick an engine (MiniMax H3, Krea 2,
FLUX.2 Klein, MOSS, Qwen Image, LTX, or any workflow you upload), write a prompt, and go.

Step-by-step help for every part is in the **Help** folder (plain text files, open with a double-click):
**1 - Install**, **2 - Start and use**, **3 - ComfyUI**, **4 - Troubleshooting**.

## What you need
- **Windows 10 or 11, 64-bit.** An NVIDIA graphics card is best (8 GB of video memory or more; MiniMax H3 video
  also needs about 32 GB of RAM).
- **ComfyUI**, kept up to date: [ComfyUI Desktop](https://www.comfy.org/download) (easiest) or
  [ComfyUI Portable](https://github.com/comfyanonymous/ComfyUI/releases). You can install it before or after
  Comfy Studio. See **Help / 3 - ComfyUI.txt**.
- **Python 3.12** (64-bit). Don't have it? The installer offers to add it for you.
- Optional: [Git for Windows](https://git-scm.com/download/win), for easy updates and node packs.

## Install (once: about 5 minutes, plus model downloads)
1. **Download:** green **Code** button at the top of this page, then **Download ZIP**
   (or the zip from the latest **Release**).
2. **Extract it. Don't skip this.** Right-click the zip, choose **Extract All...**, and pick a simple place like
   `C:\ComfyStudio` (not Program Files). Running files from *inside* the zip doesn't work: only one file gets
   copied to a temporary folder and the installer can't find the rest.
3. **Open the extracted folder and double-click `Install Comfy Studio.bat`.**
   If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.

The installer then does everything by itself, step by step, and says what it did:
- checks the folder is complete and can save files;
- unblocks the downloaded files;
- finds Python, or offers to install Python 3.12;
- installs the Python parts Comfy Studio needs, and checks they work;
- checks Windows WebView2, which the app window uses (it offers to add it; without it the app opens in your
  browser);
- **finds your ComfyUI**, running or installed, and asks which one if you have several;
- adds **Comfy Studio** shortcuts to your Desktop and Start menu;
- starts Comfy Studio and confirms it really opened;
- **ComfyUI node packs:** lists the custom nodes the engines use that your ComfyUI doesn't have yet, and asks
  before adding them to ComfyUI's own `custom_nodes` folder (the exact versions Comfy Studio was tested with; their
  Python parts go into ComfyUI's own Python, and ComfyUI's GPU libraries are never touched). Restart ComfyUI after.
- **Model files:** lists the model families that fit your PC with their download sizes; type the numbers you want
  (or press Enter to skip). Files go into ComfyUI's model folders, files you already have are never downloaded
  twice, free space is checked first, and a stopped download carries on where it left off. ComfyUI must be
  running for this step. FLUX.2 Klein 9B needs a free Hugging Face account (its makers ask you to accept their
  licence); the installer explains how, or you can skip it.

Works with ComfyUI Desktop, Portable and manual installs. If anything goes wrong it **stops and says exactly what
to do**, and saves a report in `app\data\setup_report.txt`. Running the installer again is always safe: it only adds
what's still missing.

### With Git instead (easy updates)
```
git clone https://github.com/AjCozie123/ComfyStudio.git
```
Then open the new **ComfyStudio** folder and double-click **Install Comfy Studio.bat**. Update later with `git pull`,
or the app's own **Check for updates**.

## Start
1. Start **ComfyUI**.
2. Open **Comfy Studio**: the desktop shortcut, or double-click **Start Comfy Studio.bat**. It tells you what's
   wrong if the app can't start.
3. Open **Workflows**. Every engine shows what it still needs, and downloads it with one click: models from their
   official pages, and ComfyUI node packs.

**You only ever need two files:** **Install Comfy Studio.bat** (once) and **Start Comfy Studio.bat** (or the
shortcut). Everything else lives in the `app` folder.

## Quick fixes
| Problem | Fix |
|---|---|
| `Can not find script file ...\Temp\...` or files missing | You ran it from inside the zip: **Extract All...** first, then run the installer from the extracted folder. |
| "Windows protected your PC" | **More info**, then **Run anyway**. |
| Python not found | Let the installer add it (press **Y**), or install Python 3.12 from python.org and tick **Add python.exe to PATH**. |
| App doesn't open | Run **Start Comfy Studio.bat**: it shows the reason. Then see **Help / 4 - Troubleshooting.txt**. |
| "ComfyUI is offline" | Start ComfyUI and wait until it's ready. See **Help / 3 - ComfyUI.txt**. |
| New nodes missing after install | Restart ComfyUI completely (close it, start it again). |
| "needs a free Hugging Face account (401)" | Accept the model's licence on its Hugging Face page, make a Read token, press **Add Hugging Face token** on the Workflows page. |
| A model download stopped | Press **Download** again (or rerun the installer): it carries on where it stopped. |

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
