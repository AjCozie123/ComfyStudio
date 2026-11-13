"""The MiniMax H3 AIO run. Every mode of Aitrepreneur's MINIMAX H3 ULTRA V3 workflow, behind one dropdown.

Speed plan (8 GB VRAM / 32 GB RAM):
  * turbo LoRA, 8 euler steps on the beta schedule (his settings; turbo's sweet spot is 6-8 steps);
  * Spectrum forecasts part of those steps instead of running the transformer (no extra passes);
  * Fast modes draft at ~1/4 of the output pixels, finish the draft (audio included), then the
    learned 3D latent upscaler enlarges the video latent and 2-4 short steps at full size add detail;
  * every prompt of a run is encoded in one text-encoder session, released before the video model.

Long modes and Extend Video join clips seamlessly: each clip's last 39 frames of video+audio latent are
copied into the next clip as a protected prefix (no decode / re-encode), and the final file is streamed
clip by clip with the 39-frame seams blended, so RAM stays bounded however many clips there are.
"""

import logging
import math
import time

import torch

import comfy.model_management as mm
import comfy.utils
import folder_paths
import node_helpers
from comfy_api.latest import InputImpl, Types

from . import engine, models

log = logging.getLogger("MiniMax-H3-AIO")

FPS = 24
CONTEXT_FRAMES = 39      # a valid H3 video run that also lands exactly on the 40 Hz audio grid
AUDIO_FEATHER = 8        # 0.2 s half-cosine release of the protected audio prefix
MAX_EXTENSIONS = 6       # the seam streamer's limit

# mode -> (kind, fast, long). Names follow Aitrepreneur's ULTRA V3 workflow sections.
MODES = {
    "Text to Video": ("t2v", False, False),
    "Image to Video / First-Last Frame": ("i2v", False, False),
    "References to Video": ("r2v", False, False),
    "Fast Text to Video": ("t2v", True, False),
    "Fast Image to Video": ("i2v", True, False),
    "Fast References to Video": ("r2v", True, False),
    "Long Text to Video": ("t2v", False, True),
    "Long Image to Video": ("i2v", False, True),
    "Extend Video": ("extend", False, True),
    "Image Editing": ("edit", False, False),
    "Inpainting": ("inpaint", False, False),
}
RESOLUTIONS = {"480p (0.4 MP)": 0.4, "768 canvas (0.56 MP)": 0.5625, "576p (0.6 MP)": 0.6, "720p (0.9 MP)": 0.9, "768p (1.0 MP)": 1.0,
               "832p (1.2 MP)": 1.2, "1080p (2.0 MP)": 2.0}
ASPECTS = {"16:9 landscape": 16 / 9, "9:16 portrait": 9 / 16, "1:1 square": 1.0, "4:3": 4 / 3, "3:4": 3 / 4,
           "Match input": None}
REFINE = {"Light (2 steps)": "0.6316, 0.3158, 0.0",
          "Balanced (3 steps)": "0.9035, 0.6316, 0.3158, 0.0",
          "Strong (4 steps)": "0.9035, 0.8000, 0.6316, 0.3158, 0.0"}
REFERENCE_DETAIL = {"Standard": None, "High": 1.0, "Max": 1.6}   # reference megapixels, None = video size

I2V_ANCHOR = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
FLF_ANCHOR = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
              "0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the {end:.2f}-second mark "
              "of the target video.\n\n")
CONTINUE = ("Continue directly from the final moment of the previous clip with no cut, reset or re-establishment. "
            "The protected opening frames are authoritative for pose, motion, camera position and movement, "
            "lighting, environment, clothing, voice and ambience; carry them forward naturally.\n\n")


def frames_for(seconds):
    n = max(5, round(seconds * FPS))
    return n + (5 - n % 17) % 17


def size_for(megapixels, ratio):
    px = megapixels * 1024 * 1024
    w = math.sqrt(px * ratio)
    return max(32, round(w / 32) * 32), max(32, round(w / ratio / 32) * 32)


def draft_size(w, h):
    return max(256, round(w / 2 / 32) * 32), max(256, round(h / 2 / 32) * 32)


def _resize(image, w, h, crop):
    s = image[..., :3].movedim(-1, 1)
    return comfy.utils.common_upscale(s, w, h, "lanczos", crop).movedim(1, -1)


def _to_area(image, megapixels):
    h, w = image.shape[1], image.shape[2]
    s = math.sqrt(megapixels * 1024 * 1024 / (w * h))
    return _resize(image[:1], max(32, round(w * s / 32) * 32), max(32, round(h * s / 32) * 32), "disabled")


def extension_prompts(texts, count, fallback):
    """One prompt per extra clip; an empty box repeats the clip before's prompt (the main prompt for clip 1)."""
    out, last = [], fallback
    for i in range(count):
        text = texts[i].strip() if i < len(texts) else ""
        last = text or last
        out.append(last)
    return out


def _media(references):
    """The MiniMax H3 Media Loader bundle: pictures, videos (+ their soundtracks), audios."""
    b = references or {}
    pictures = [p for p in (b.get("pictures") or []) if p is not None]
    videos = list(b.get("videos") or [])
    video_audios = list(b.get("video_audios") or [])
    pairs = [(v, video_audios[i] if i < len(video_audios) else None) for i, v in enumerate(videos) if v is not None]
    audios = [a for a in (b.get("audios") or []) if a is not None]
    return pictures, pairs, audios


def _silence(frames):
    return {"waveform": torch.zeros(1, 2, int(frames / FPS * 44100)), "sample_rate": 44100}


class Run:
    def __init__(self, s, references):
        self.s = s
        self.mode = s["mode"]
        self.kind, self.fast, self.long = MODES[self.mode]
        self.pictures, self.videos, self.audios = _media(references)
        self.notes = []
        self.t0 = time.time()
        self._check_inputs()

        # Text and Image modes use the first/last-frame model; References, Extend, Editing and
        # Inpainting the ref2va model (the official templates' split). Extensions keep clip 1's model.
        self.keyframe_model = self.kind in ("t2v", "i2v")
        self.unet = s["model_text_image"] if self.keyframe_model else s["model_references"]

        source = self.videos[0][0] if self.kind in ("extend", "inpaint") else (self.pictures[0] if self.pictures else None)
        ratio = ASPECTS[s["aspect_ratio"]]
        if ratio is None or self.kind in ("extend", "inpaint", "edit"):
            ratio = source.shape[2] / source.shape[1] if source is not None else (ratio or 16 / 9)
        self.w, self.h = size_for(RESOLUTIONS[s["resolution"]], ratio)
        self.two_stage = self.fast and RESOLUTIONS[s["resolution"]] >= 0.6
        if self.fast and not self.two_stage:
            self.notes.append("480p is already the draft size - ran single-stage")
        self.dw, self.dh = draft_size(self.w, self.h) if self.two_stage else (self.w, self.h)

        # seamless settings (H3 AIO Seamless Settings, via the Long Video / Seamless Planner node)
        self.context = int(s.get("context_frames", CONTEXT_FRAMES))
        self.feather = int(s.get("audio_feather", AUDIO_FEATHER))
        self.blend = min(int(s.get("seam_blend", self.context)), self.context)
        first_seconds = (s.get("planned") and s.get("first_seconds")) or s["duration_seconds"]
        self.frames = frames_for(first_seconds if self.long else s["duration_seconds"])
        self.ext_count = 0
        if self.long:
            self.ext_count = max(0 if s.get("planned") else 1,
                                 min(s["extensions"], MAX_EXTENSIONS - (1 if self.kind == "extend" else 0)))
        secs = s["extension_seconds"] if isinstance(s["extension_seconds"], (list, tuple)) else [s["extension_seconds"]]
        secs = [secs[min(i, len(secs) - 1)] for i in range(self.ext_count)]
        # Extend Video: every generated clip is an extension of the uploaded video (first one = first_seconds)
        new = ([first_seconds] + secs) if self.kind == "extend" else secs
        self.clip_frames = [frames_for(x + self.context / FPS) for x in new]
        self.negative_on = s["use_negative"] and s["cfg"] > 1.0 and s["negative_prompt"].strip() != ""
        if s["use_negative"] and not self.negative_on:
            self.notes.append("negative prompt ignored: it needs cfg above 1.0 and some text")
        self.cfg = s["cfg"] if self.negative_on else 1.0

    def _check_inputs(self):
        need = {"i2v": "a start picture (Picture 1; Picture 2 = optional last frame)",
                "edit": "the picture to edit as Picture 1",
                "extend": "the video to continue as Video 1 (its sound is used too)",
                "inpaint": "the video to change as Video 1"}
        if self.kind in ("i2v", "edit") and not self.pictures or self.kind in ("extend", "inpaint") and not self.videos:
            raise ValueError(f"{self.mode} needs {need[self.kind]} in the MiniMax H3 Media Loader.")
        if self.kind == "r2v" and not (self.pictures or self.videos or self.audios):
            raise ValueError("References to Video needs at least one picture, video or audio in the MiniMax H3 Media Loader.")

    def note(self, msg):
        log.info("[H3 AIO] %s", msg)

    # ---------------- prompts ----------------

    def _prompt(self, text, extension=False, planned_continuation=False):
        # planner prompts for clip 2+ already carry their continuation; Extend Video's first clip does not
        if not self.s["auto_prompt_format"] or planned_continuation:
            return text
        if extension:
            # The guides want the fields first; the no-cut instruction belongs inside the opening shot.
            for anchor in ("[Shot 1]", "integrated_multimodal_description:", "detailed_description:"):
                i = text.find(anchor)
                if i >= 0:
                    i += len(anchor)
                    return text[:i] + " " + CONTINUE.strip() + " " + text[i:].lstrip()
            return CONTINUE + text
        if self.kind == "i2v" and "Picture 1" not in text:
            if len(self.pictures) > 1 and not self.long:
                return FLF_ANCHOR.format(end=(self.frames - 1) / FPS) + text
            return I2V_ANCHOR + text
        return text

    def _refs(self):
        detail = REFERENCE_DETAIL[self.s["reference_detail"]]
        mp = detail if detail is not None else self.w * self.h / (1024 * 1024)
        if self.kind == "edit":
            # Picture 1 is the photo being edited, at the output size; the rest are extra references.
            return [self.edit_base] + [_to_area(p, mp) for p in self.pictures[1:9]]
        return [_to_area(p, mp) for p in self.pictures[:9]]

    def _first_last(self):
        if self.kind != "i2v":
            return None, None
        last = self.pictures[1] if len(self.pictures) > 1 and not self.long else None
        return self.pictures[0], last

    def _cond_jobs(self, prompts):
        """(key, build(clip)) for every conditioning this run needs: prompts first, negatives last."""
        s = self.s
        use_refs = self.kind in ("r2v", "extend", "edit", "inpaint")
        refs = self._refs() if use_refs else []
        ref_videos = self.videos[:3] if self.kind == "r2v" else []
        ref_audios = self.audios[:3] if use_refs else []
        first, last = self._first_last()
        vae_name = s["image_vae"] if self.kind == "edit" else s["video_vae"]
        length = 5 if self.kind == "edit" else self.frames

        def r2v(text):
            def build(clip):
                kw = dict(clip=clip, prompt=text, width=self.w, height=self.h, length=length, ref_image_size="max",
                          vae=models.vae(vae_name), audio_vae=models.vae(s["audio_vae"]))
                if refs:
                    kw["ref_images"] = {f"ref_image_{i}": r for i, r in enumerate(refs)}
                if ref_videos:
                    kw["ref_videos"] = {f"ref_video_{i}": v for i, (v, _) in enumerate(ref_videos)}
                    kw["ref_video_audios"] = {f"ref_video_audio_{i}": a for i, (_, a) in enumerate(ref_videos) if a is not None}
                if ref_audios:
                    kw["ref_audios"] = {f"ref_audio_{i}": a for i, a in enumerate(ref_audios)}
                return engine.call("MiniMaxH3ReferenceToVideo", **kw)[0]
            key = models.tensor_digest("r2v", s["text_encoder"], vae_name, text, *refs,
                                       *[v for v, _ in ref_videos], *[a for _, a in ref_videos], *ref_audios)
            return key, build

        def fl2v(text, frames):
            def build(clip):
                kw = dict(clip=clip, vae=models.vae(s["video_vae"]), prompt=text, width=self.dw, height=self.dh,
                          length=self.frames)
                if frames:
                    kw["first_frame"] = first
                    if last is not None:
                        kw["last_frame"] = last
                return engine.call("MiniMaxH3ImageToVideo", **kw)[0]
            key = models.tensor_digest("fl2v", s["text_encoder"], s["video_vae"], text,
                                       first if frames else None, last if frames else None,
                                       (self.dw, self.dh, self.frames) if frames else None)
            return key, build

        jobs = []
        has_frames = first is not None
        for i, text in enumerate(prompts):
            jobs.append(fl2v(text, i == 0 and has_frames) if self.keyframe_model else r2v(text))
        if self.negative_on:
            if self.keyframe_model:
                jobs.append(fl2v(s["negative_prompt"], has_frames))
                if has_frames and len(prompts) > 1:
                    jobs.append(fl2v(s["negative_prompt"], False))
            else:
                jobs.append(r2v(s["negative_prompt"]))
        return jobs

    def encode_all(self, prompts):
        jobs = self._cond_jobs(prompts)
        todo = [(k, b) for k, b in jobs if models.cached_conditioning(k) is None]
        if todo:
            self.note(f"encoding {len(todo)} prompt(s)")
            clip = models.text_encoder(self.s["text_encoder"], self.s["keep_text_encoder_loaded"])
            for key, build in todo:
                mm.throw_exception_if_processing_interrupted()
                models.store_conditioning(key, build(clip))
            clip = None
            models.release_text_encoder(self.s["keep_text_encoder_loaded"])
        else:
            self.note("prompts unchanged - text encoder not needed")
        conds = [models.cached_conditioning(k) for k, _ in jobs]
        positives = conds[:len(prompts)]
        neg_main = neg_ext = None
        if self.negative_on:
            neg_main = conds[len(prompts)]
            neg_ext = conds[len(prompts) + 1] if len(conds) > len(prompts) + 1 else neg_main
        return positives, neg_main, neg_ext

    def keyframes_at(self, cond, w, h):
        """Image-to-Video keyframes re-encoded at the stage-2 size (same resize rules as the core node)."""
        first, last = self._first_last()
        if first is None or cond is None:
            return cond
        vae = models.vae(self.s["video_vae"])
        kfs = [{"resolved_frame_index": 0, "latent": vae.encode(_resize(first[:1], w, h, "disabled"))}]
        if last is not None:
            kfs.append({"resolved_frame_index": self.frames - 1, "latent": vae.encode(_resize(last[:1], w, h, "center"))})
        return node_helpers.conditioning_set_values(cond, {"minimax_keyframes": kfs})

    # ---------------- model ----------------

    def build_model(self):
        s = self.s
        models.ram_guard(models.file_size("diffusion_models", self.unet), "the video model", self.notes)
        model = models.video_model(self.unet)
        if s["speed_lora"] and s["speed_lora_name"] != "None" and s["speed_lora_strength"] != 0:
            strength = s["speed_lora_strength"]
            if self.kind == "inpaint" and strength > 0.7:
                strength = 0.7   # Aitrepreneur's inpainting setting; full turbo over-smooths the regenerated region
                self.notes.append("inpainting uses turbo strength 0.7")
            model = engine.call1("LoraLoaderModelOnly", model=model, lora_name=s["speed_lora_name"],
                                 strength_model=strength)
        for name, strength in s["loras"]:
            model = engine.call1("LoraLoaderModelOnly", model=model, lora_name=name, strength_model=strength)
        # Aitrepreneur's settings: shift 12 / 3, and 12 / 1.5 for inpainting.
        model = engine.call1("MiniMaxH3SigmaShift", model=model, shift_video=float(s.get("shift_video", 12.0)),
                             shift_audio=1.5 if self.kind == "inpaint" else float(s.get("shift_audio", 3.0)))
        if s["live_preview"]:
            model = self._preview(model)
        refine_model = model
        model = self._sparse(model)
        if s["spectrum"] and s["steps"] >= 6:
            if engine.has("SpectrumApplyMiniMaxH3"):
                model = self._spectrum(model)
            else:
                self.notes.append("Spectrum not installed - ran without it")
        elif s["spectrum"]:
            self.notes.append("Spectrum needs 6+ steps - skipped")
        return model, refine_model

    def _opts(self):
        """Speed options the PC Launcher writes per job (C:/PCLauncher/h3_speed_opts.json); missing file = all off."""
        try:
            import json as _j
            with open(r"C:\PCLauncher\h3_speed_opts.json", encoding="utf-8") as f:
                return _j.load(f)
        except Exception:
            return {}

    def _sparse(self, model):
        o = self._opts().get("sparse") or "off"
        if o == "off" or not engine.has("BlockSparseAttention"):
            return model
        sel = {"selection": "sol-attn", "tau": float(self._opts().get("tau", 1.3))} if o == "sol" else               {"selection": "vsa" if o == "vsa" else "sla", "keep_percent": float(self._opts().get("keep", 10.0))}
        self.notes.append(f"sparse attention: {o}")
        return engine.call1("BlockSparseAttention", model=model, selection=sel, start_percent=float(self._opts().get("start", 0.3)),
                            end_percent=0.9, dense_blocks="", min_tokens=12288, extra_tokens=256,
                            sink_conditioning="exact_kv_and_rows", verbose=False)

    def _spectrum(self, model):
        """Spectrum keeps H3's final-block hidden state (tokens x 5376, 2 bytes) of every actual step in system RAM
        for its offline replay. At 1056x608 x 7 s that is ~1.4 GB a step, ~7 GB a clip, which pushed a 32 GB PC into
        the pagefile (measured 2026-10-06: clip 467 s instead of ~200 s). Size it to this PC's RAM."""
        frames = max([self.frames] + list(getattr(self, "clip_frames", [])))
        latent_t = 2 if frames <= 5 else ((frames - 5) // 17) * 5 + 2
        anchor = (self.dw // 16) * (self.dh // 16) * latent_t * 5376 * 2
        budget = 0.20 * models.psutil.virtual_memory().total
        actual = max(2, math.ceil(self.s["steps"] * 0.6) + 1)
        kw = dict(model=model, enabled=True, blend_weight=0.5, degree=1, ridge_lambda=0.1, window_size=2.0,
                  flex_window=0.75, warmup_steps=1, tail_actual_steps=1, max_history=8, debug=False)
        if anchor * actual <= budget:
            return engine.call1("SpectrumApplyMiniMaxH3", **kw)
        if anchor * 3 <= budget:
            self.notes.append(f"Spectrum single-pass (replay would hold {anchor * actual / models.GB:.1f} GB of RAM)")
            kw.update(max_history=3, offline_smoothing_replay=False)
            return engine.call1("SpectrumApplyMiniMaxH3", **kw)
        self.notes.append(f"Spectrum off: it would need {anchor * 3 / models.GB:.1f} GB of RAM at this size")
        return model

    def _preview(self, model):
        if not engine.has("ModelPreviewOverrideKJ"):
            self.notes.append("live preview needs ComfyUI-KJNodes")
            return model
        tiny = next((o for o in folder_paths.get_filename_list("vae_approx") if "taeh3" in o), None)
        if tiny is None:
            self.notes.append("live preview needs models/vae_approx/taeh3.safetensors")
            return model
        return engine.call1("ModelPreviewOverrideKJ", model=model, max_resolution=512, jpeg_quality=70,
                            suppress_default_preview=True, preview_frames=1, preview_fps=12, tiny_vae=tiny)

    # ---------------- sampling ----------------

    def _guider(self, model, positive, negative):
        if negative is not None:
            return engine.call1("CFGGuider", model=model, positive=positive, negative=negative, cfg=self.cfg)
        return engine.call1("BasicGuider", model=model, conditioning=positive)

    def sample(self, model, positive, negative, latent, seed):
        noise = engine.call1("RandomNoise", noise_seed=seed)
        sampler = engine.call1("KSamplerSelect", sampler_name="euler")
        sigmas = engine.call1("BasicScheduler", model=model, scheduler="beta", steps=self.s["steps"], denoise=1.0)
        return engine.call("SamplerCustomAdvanced", noise=noise, guider=self._guider(model, positive, negative),
                           sampler=sampler, sigmas=sigmas, latent_image=latent)[0]

    def refine(self, model, positive, negative, latent, seed):
        cls = engine.get_class("MinimaxH3LatentUpscaler3DRefineHandoff")
        names = cls.INPUT_TYPES()["required"]["model_name"][0]
        name = next((n for n in names if "3d" in n.lower()), None)
        if name is None:
            raise ValueError("No H3 latent upscaler in models/latent_upscale_models (minimax_h3_latent_upscaler_3d_fp16.safetensors).")
        clean = {k: v for k, v in latent.items() if k != "noise_mask"}
        kw = dict(latent=clean, noise=engine.call1("RandomNoise", noise_seed=seed + 7919),
                  sampler=engine.call1("KSamplerSelect", sampler_name="euler"),
                  sigmas=engine.call1("ManualSigmas", sigmas=REFINE[self.s["refine_strength"]]),
                  model_name=name, mode="target dimensions", scale=2.0, width=self.w, height=self.h, megapixels=1.0,
                  align=32, keep_proportion=False, lock_audio=True, cfg=self.cfg, device="cuda", precision="fp16",
                  offload_after_upscale=mm.get_total_memory(mm.get_torch_device()) < 12 * models.GB,
                  model=model, positive=positive)
        if negative is not None:
            kw["negative"] = negative
        return engine.call1("MinimaxH3LatentUpscaler3DRefineHandoff", **kw)

    def empty(self, frames):
        return engine.call1("EmptyMiniMaxH3LatentAV", width=self.dw, height=self.dh, length=frames)

    # ---------------- run ----------------

    def run(self):
        """Returns (video or None, image or None, info)."""
        if self.kind == "edit":
            return self.run_edit()
        if self.kind == "inpaint":
            return self.run_inpaint()
        return self.run_video()

    def _finish(self, info_head):
        models.after_run(self.notes)
        total = time.time() - self.t0
        info = f"{self.mode} | {info_head} | {total:.0f} s"
        if self.notes:
            info += "\nnotes: " + "; ".join(self.notes)
        self.note(info.replace("\n", " | "))
        return info

    def run_video(self):
        s = self.s
        ext_prompts = extension_prompts(s["extension_prompts"], self.ext_count, s["prompt"])
        first_is_extension = self.kind == "extend"
        planned = self.long and s.get("planned")
        main = s.get("first_prompt") if planned else s["prompt"]
        if not (main or "").strip():
            raise ValueError("The prompt is empty - write one in the AIO node or connect the H3 AIO Seamless Planner.")
        prompts = [self._prompt(main, extension=first_is_extension)] + \
                  [self._prompt(p, extension=True, planned_continuation=bool(planned)) for p in ext_prompts]
        positives, neg_main, neg_ext = self.encode_all(prompts)
        mm.throw_exception_if_processing_interrupted()

        stage1_model, refine_model = self.build_model()
        vae = models.vae(s["video_vae"])
        avae = models.vae(s["audio_vae"])
        self.note(f"{self.mode}: {self.w}x{self.h}" + (f" (drafted at {self.dw}x{self.dh})" if self.two_stage else "")
                  + f", {self.frames} frames, {self.ext_count} extension(s), {s['steps']} steps")

        drafts, finals = [], []
        for i, positive in enumerate(positives):
            mm.throw_exception_if_processing_interrupted()
            first_clip = i == 0
            seed = s["seed"] + i * 1000
            negative = neg_main if first_clip else neg_ext
            if first_clip and not first_is_extension:
                latent = self.empty(self.frames)
            elif first_clip:
                source, source_audio = self.videos[0]
                latent = engine.call("MiniMaxH3StartMaskedContext", latent=self.empty(self.clip_frames[0]), vae=vae,
                                     audio_vae=avae, start_mode="existing_video", context_length=self.context,
                                     audio_feather_ticks=self.feather, source_fps=float(FPS), crop="center",
                                     source_frames=source,
                                     source_audio=source_audio if source_audio is not None else _silence(len(source)))[0]
            else:
                frames = self.clip_frames[i if first_is_extension else i - 1]
                latent = engine.call("MiniMaxH3GeneratedAVMaskedContext", latent=self.empty(frames),
                                     source_latent=drafts[-1], context_length=self.context,
                                     audio_feather_ticks=self.feather)[0]
            t = time.time()
            draft = self.sample(stage1_model, positive, negative, latent, seed)
            drafts.append(draft)
            self.note(f"clip {i + 1}/{len(positives)} sampled in {time.time() - t:.0f} s")
            if self.two_stage:
                t = time.time()
                pos2 = self.keyframes_at(positive, self.w, self.h) if first_clip else positive
                neg2 = self.keyframes_at(negative, self.w, self.h) if first_clip else negative
                finals.append(self.refine(refine_model, pos2, neg2, draft, seed))
                self.note(f"clip {i + 1} upscaled + refined in {time.time() - t:.0f} s")
            else:
                finals.append(draft)

        mm.throw_exception_if_processing_interrupted()
        video = self.assemble(finals, vae, avae)
        size = f"{self.w}x{self.h}" + (f" from {self.dw}x{self.dh}" if self.two_stage else "")
        return video, None, self._finish(f"{size} | {len(finals)} clip(s)")

    def assemble(self, finals, vae, avae):
        if len(finals) == 1 and self.kind != "extend":
            if self._opts().get("tiled_vae"):
                images = engine.call1("VAEDecodeTiled", samples=finals[0], vae=vae, tile_size=512, overlap=64,
                                      temporal_size=64, temporal_overlap=8)
            else:
                images = engine.call1("VAEDecode", samples=finals[0], vae=vae)
            audio = engine.call1("VAEDecodeAudio", samples=finals[0], vae=avae)
            return engine.call1("CreateVideo", images=images, fps=float(FPS), audio=audio)

        # Several clips: the streamer decodes one clip at a time, blends the 39-frame seams and writes a
        # temp file; the Save Video node after this one keeps it.
        kw = dict(video_vae=vae, audio_vae=avae, context_frames=self.context, video_overlap_frames=self.blend,
                  source_fps=float(FPS), crop="center", filename_prefix="H3_AIO_join", pix_fmt="yuv420p",
                  crf=17, save_metadata=False, trim_to_audio=True, save_output=False)
        if self.kind == "extend":
            source, source_audio = self.videos[0]
            kw.update(start_mode="existing_video", active_extensions=len(finals), source_frames=source,
                      source_audio=source_audio if source_audio is not None else _silence(len(source)))
            exts = finals
        else:
            kw.update(start_mode="generated", active_extensions=len(finals) - 1, starter_latent=finals[0])
            exts = finals[1:]
        for i, lat in enumerate(exts):
            kw[f"extension_{i + 1}"] = lat
        out = engine.call("MiniMaxH3StreamLiveExtensionAVToVHS", **kw)
        files = out[0][1] if out and isinstance(out[0], tuple) and len(out[0]) > 1 else []
        mp4 = [f for f in files if f.lower().endswith(".mp4")]
        if not mp4:
            raise RuntimeError("the clip joiner wrote no video file - see the console")
        return InputImpl.VideoFromFile(mp4[-1])

    def run_edit(self):
        s = self.s
        base = self.pictures[0]
        mp = RESOLUTIONS[s["resolution"]]
        w, h = size_for(mp, base.shape[2] / base.shape[1])
        self.w, self.h = w, h
        self.edit_base = _resize(base[:1], w, h, "center")
        self.dw, self.dh, self.frames, self.clip_frames = w, h, 1, []
        positives, neg_main, _ = self.encode_all([s["prompt"]])
        model, _ = self.build_model()
        latent = engine.call1("EmptyMiniMaxH3ImageLatentAV", width=w, height=h, batch_size=1)
        out = self.sample(model, positives[0], neg_main, latent, s["seed"])
        image = engine.call1("VAEDecode", samples=out, vae=models.vae(s["image_vae"]))
        return None, image, self._finish(f"{w}x{h} image")

    def run_inpaint(self):
        s = self.s
        source, source_audio = self.videos[0]
        if source_audio is None:
            source_audio = _silence(len(source))
            self.notes.append("Video 1 has no sound - used silence")
        source = _resize(source, self.w, self.h, "disabled")
        positives, neg_main, _ = self.encode_all([s["prompt"]])
        mm.throw_exception_if_processing_interrupted()

        # 1. find and track what to replace (SAM3 + the text in inpaint_target)
        sam_name = next((n for n in folder_paths.get_filename_list("checkpoints") if "sam3" in n.lower()), None)
        if sam_name is None:
            raise ValueError("Inpainting needs models/checkpoints/sam3.1_multiplex_fp16.safetensors (run install.bat).")
        sam_model, sam_clip, _ = engine.call("CheckpointLoaderSimple", ckpt_name=sam_name)[:3]
        target = engine.call1("CLIPTextEncode", clip=sam_clip, text=s["inpaint_target"])
        track = engine.call1("SAM3_VideoTrack", images=source, model=sam_model, detection_threshold=0.5,
                             max_objects=0, detect_interval=1, conditioning=target)
        masks = engine.call1("SAM3_TrackToMask", track_data=track, object_indices="")
        sam_model = sam_clip = target = track = None
        models.free_now()   # SAM3 is done; give its memory back before H3 loads
        if float(masks.max()) <= 0:
            raise ValueError(f"SAM3 found no '{s['inpaint_target']}' in the video - try another word (head, face, shirt, person...).")

        # 2. crop around it, build the masked AV latent (original sound kept)
        cropped, cropped_masks, bboxes, _ = engine.call(
            "MVEx_SubjectCrop", original_images=source, masks=masks,
            mode={"mode": "tracked", "crop_scale": 1.75, "padding": "firm", "prefer": "stillness",
                  "aspect_ratio": 0.0, "seamless_loop": False},
            divisible_by=32, upscale_megapixels=0.8, resize_method="auto")
        model, _ = self.build_model()
        vae = models.vae(s["video_vae"])
        avae = models.vae(s["audio_vae"])
        ops = engine.call("NKDMaskOps", mask=cropped_masks, invert=False, black_point=0.0, white_point=1.0,
                          despeckle=2, fill_holes=False, close_gaps=0, temporal_expand=1, temporal_smooth=0, expand=30,
                          blockify=0, blockify_threshold=0.05, feather=0, edge_low=0.0, edge_high=1.0, vae=vae,
                          model=model)
        paste_mask = engine.call1("NKDMaskOpsLean", mask=ops[0], fill_holes=False, expand=10, feather=20)
        latent = engine.call1("NKDAVLatent", images=cropped, audio=source_audio, video_vae=vae, audio_vae=avae,
                              audio_mode="keep", fps=float(FPS), ramp_ticks=0, ramp_out_ticks=0, ramp_shape="cosine",
                              latent_mask=ops[2])

        # 3. regenerate the masked region, paste it back
        negative = neg_main if neg_main is not None else engine.call1("ConditioningZeroOut", conditioning=positives[0])
        out = engine.call1("KSampler", model=model, seed=s["seed"], steps=s["steps"], cfg=self.cfg,
                           sampler_name="euler", scheduler="simple", positive=positives[0], negative=negative,
                           latent_image=latent, denoise=1.0)
        decoded = engine.call1("VAEDecode", samples=out, vae=vae)
        images = engine.call1("MVEx_SubjectUncrop", cropped_images=decoded, original_images=source, feather=16,
                              resize_method="auto", bboxes=bboxes, cropped_masks=paste_mask)
        video = engine.call1("CreateVideo", images=images, fps=float(FPS), audio=source_audio)
        return video, None, self._finish(f"{self.w}x{self.h}, crop {cropped.shape[2]}x{cropped.shape[1]}, {len(images)} frames")
