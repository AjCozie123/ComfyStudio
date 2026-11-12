# Comfy Studio

A simple, clean desktop front end for **ComfyUI** that runs on your own PC. Pick an engine (MiniMax H3, Krea 2,
FLUX.2 Klein, MOSS, Qwen Image, LTX, or any workflow you upload), write a prompt, and go.

## What you need
- Windows 10/11 with an NVIDIA GPU (8 GB VRAM or more is best)
- [ComfyUI](https://www.comfy.org/download) (portable or desktop), running
- Python 3.10+ ([python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH")

## Install
1. Download the latest release zip and unzip it anywhere (for example `C:\ComfyStudio`).
2. Start ComfyUI.
3. Double-click **Install Comfy Studio.bat**. It checks Python and ComfyUI, adds a desktop shortcut and starts the app.

Comfy Studio finds ComfyUI's folders by itself (saved in `data\settings.json`).

## Models and engines
Open **Workflows**. Every engine shows whether it fits your PC (from your GPU, VRAM and RAM) and which files it
still needs. **Download** fetches the missing files from their official Hugging Face pages straight into the right
ComfyUI folder, after checking free space. Nothing downloads unless you press it.

Upload your own ComfyUI workflow with **+ Upload workflow**: the app reads it (pictures, video or audio inputs,
prompt, size, seed, steps, models) and gives it the same simple controls. In **Settings → Manual** every value in
a workflow can be changed.

## Updates
**Workflows → Check for updates** installs the newest release; your library and settings are kept, and the
app restores the previous version by itself if an update fails to start. Important fixes show a banner; you can
let them install automatically (only while nothing is generating).

Comfy Studio runs on this PC only (http://127.0.0.1:8198); nothing on it is reachable from other devices.
