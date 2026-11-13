# MiniMax H3 AIO

Every MiniMax H3 video workflow in one ComfyUI node, tuned for 8–16 GB GPUs and 32 GB RAM.

Pick the mode from one dropdown (names follow Aitrepreneur's MINIMAX H3 ULTRA V3 workflow). All media comes
from the **MiniMax H3 Media Loader** (Picture 1-9, Video 1-3 with sound, Audio 1-3). Outputs: video -> Save Video,
image -> Save Image (Image Editing), info -> Preview Any.

| mode | load | model |
|---|---|---|
| Text to Video | - | fl2va |
| Image to Video / First-Last Frame | Picture 1 (+ Picture 2 = last frame) | fl2va |
| References to Video | Pictures / Videos / Audios | ref2va |
| Fast Text / Image / References to Video | as above; draft at 1/4 size + learned latent upscale + short refine | as above |
| Long Text / Image to Video | first clip + 1-6 seamless extensions | fl2va |
| Extend Video | Video 1 (+ Pictures as identity refs) | ref2va |
| Image Editing | Picture 1 = photo to edit (+ Pictures as refs), T1 image VAE | ref2va |
| Inpainting | Video 1; SAM3 tracks `inpaint_target`, the crop is regenerated and pasted back | ref2va |

Seamless extensions copy the previous clip's last 39 frames of video + audio latent (not decoded and
re-encoded), and the final file is streamed clip by clip with the 39-frame seams blended.

## On/off switches

| switch | default | what it costs / saves |
|---|---|---|
| speed_lora | on | Turbo v4 LoRA: 8 steps instead of ~20 |
| spectrum | on | forecasts part of the steps instead of running the model (training-free, slight differences vs off) |
| use_negative | off | negative prompt via CFG; each draft step then runs twice |
| live_preview | on | madebyollin's temporal taeh3 preview on the node (KJNodes) |
| auto_prompt_format | on | adds H3's official Image-to-Video anchor line and a no-cut line to extensions |

## Memory order

The 32B text encoder (~15 GB) and the video model (~12.5 GB) do not fit in 32 GB RAM together, so all
prompts of a run are encoded in one session and the text encoder is released before the video model is
used. Encoded prompts are kept, so changing only seed / steps / speed settings never reloads it.
Start ComfyUI **without** `--cache-lru` (it never evicts, and H3 slows from ~60 s/it to ~450 s/it).

## Prompts

Write in time order. Text/Image: `integrated_multimodal_description: [Shot 1] ...`, `overall_soundscape: ...`,
`non_diegetic_music: ...`. References: `subject_definitions / summary / retention_analysis /
detailed_description / overall_soundscape / non_diegetic_music`. Dialogue: `(S1) says: <d>[English] words</d>`.
Real recorded sound: describe the room acoustics and mic, and say "not a voice-over".

## Install

Run `install.bat` (packs pinned to tested commits, models from their original Hugging Face pages,
files you already have are skipped even if renamed). Uses: ComfyUI-Spectrum-MiniMax-H3 (xmarre),
ComfyUI-H3-Motion-Context-MultiRef-V3 (Aitrepreneur / seitanism), Comfyui_Minimax_h3_latent_Upscaler-Plus
(xmarre, LBH-123-AI's model), ComfyUI-VideoHelperSuite, ComfyUI-KJNodes (preview only).
