"""MiniMax H3 AIO: every section of Aitrepreneur's MINIMAX H3 ULTRA V3 workflow in one node, picked from
one dropdown. Everything set once, or used by only some modes, lives in a small task node wired into it:

  MiniMax H3 Media Loader  - pictures / videos / audio            (references)
  H3 AIO Models            - model files                          (models)
  H3 AIO LoRAs             - extra LoRAs                          (loras)
  H3 AIO Speed & Quality   - steps, turbo, Spectrum, upscale...   (settings)
  H3 AIO Seamless Planner  - one story -> per-clip prompts        (long_video)   or
  H3 AIO Long Video        - one prompt box per clip              (long_video)
  H3 AIO Seamless Settings - carry-over / seam fade / blend       (into either of the two above)
  H3 Live Preview          - watch it sample
Any side node left unconnected falls back to sensible defaults."""

import folder_paths
from comfy_api.latest import io
from comfy_execution.graph_utils import ExecutionBlocker

from . import pipeline, planner
from .live_preview import preview_requested

NEGATIVE_DEFAULT = ("blurry, low quality, distorted face, warped hands, extra fingers, morphing, flicker, jitter, "
                    "watermark, subtitles, text overlay, cartoon, plastic skin, oversaturated, voice-over, narration, "
                    "muffled audio")

MODELS_T = io.Custom("H3_AIO_MODELS")
LORAS_T = io.Custom("H3_AIO_LORAS")
LONG_T = io.Custom("H3_AIO_LONG")
SPEED_T = io.Custom("H3_AIO_SETTINGS")
SEAM_T = io.Custom("H3_AIO_SEAMLESS")
MAX_LORAS = 4
CATEGORY = "MiniMax H3 AIO"

# carry-over options: label -> frames (valid H3 video runs that also land exactly on the 40 Hz audio grid)
CARRY = {"1.6 s (39 frames) - default": 39, "3.75 s (90 frames) - steadier, slower": 90,
         "5.9 s (141 frames) - steadiest, slowest": 141}
FADE = {"off (hard audio join)": 0, "0.1 s": 4, "0.2 s - default": 8, "0.5 s": 20}
BLEND = {"full carry-over - default": 1.0, "half": 0.5, "off (hard picture join)": 0.0}
CLIP_SECONDS = {"5 s - fastest, most control": 5.0, "7 s": 7.0, "10 s": 10.0, "15 s - longest H3 clip": 15.0}


def _files(kind):
    try:
        return folder_paths.get_filename_list(kind)
    except KeyError:
        return []


def _pick(options, *needles):
    for needle in needles:
        for o in options:
            if needle in o.lower():
                return o
    return options[0] if options else None


# ---------------- defaults used when a side node is not connected ----------------

MODEL_SLOTS = {
    "model_text_image": ("diffusion_models", ("fl2va_pruned_w4a8", "fl2va_pruned_int8", "fl2va"),
                         "H3 first/last-frame model: Text and Image modes."),
    "model_references": ("diffusion_models", ("ref2va_pruned_w4a8", "ref2va_pruned_int8", "ref2va"),
                         "H3 Ref2VA model: References, Extend Video, Image Editing, Inpainting."),
    "text_encoder": ("text_encoders", ("qwen3vl_32b_minimax_h3_nvfp4_awq", "qwen3vl_32b_minimax_h3"),
                     "Qwen3-VL 32B H3 text encoder."),
    "video_vae": ("vae", ("minimax_h3_video_vae_int8", "minimax_h3_video_vae"), "H3 video VAE (int8 saves 2.2 GB RAM)."),
    "audio_vae": ("vae", ("minimax_h3_audio_vae",), "H3 audio VAE."),
    "image_vae": ("vae", ("minimax_h3_t1_image_vae",), "Image Editing: H3 T1 image VAE."),
    "speed_lora_name": ("loras", ("turbo_v4_step600_ema_pruned", "turbo_v4", "turbo_8step", "turbo"),
                        "Turbo LoRA used when Speed & Quality's turbo switch is on. v4 step600 EMA is the strongest."),
}

SPEED_DEFAULTS = {"steps": 8, "speed_lora": True, "speed_lora_strength": 1.0, "spectrum": True,
                  "refine_strength": "Balanced (3 steps)", "reference_detail": "High", "live_preview": True,
                  "auto_prompt_format": True}
SEAM_DEFAULTS = {"context_frames": 39, "audio_feather": 8, "seam_blend": 39}


def default_models():
    out = {name: _pick(_files(kind), *needles) or "None" for name, (kind, needles, _) in MODEL_SLOTS.items()}
    out["keep_text_encoder_loaded"] = False
    return out


# ---------------- side nodes ----------------

class MiniMaxH3AIOModels(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        inputs = []
        for name, (kind, needles, tip) in MODEL_SLOTS.items():
            options = _files(kind) or ["None"]
            inputs.append(io.Combo.Input(name, options=options, default=_pick(options, *needles) or options[0], tooltip=tip))
        inputs.append(io.Boolean.Input("keep_text_encoder_loaded", default=False,
                                       tooltip="Keep the 15 GB text encoder in RAM between runs. Only with 64 GB+ RAM."))
        return io.Schema(node_id="MiniMaxH3AIOModels", display_name="H3 AIO Models", category=CATEGORY,
                         description="Which model files the MiniMax H3 AIO uses. Not connected = the best ones found.",
                         inputs=inputs, outputs=[MODELS_T.Output(display_name="models")])

    @classmethod
    def execute(cls, **models) -> io.NodeOutput:
        return io.NodeOutput(models)


class MiniMaxH3AIOLoras(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        options = ["None"] + _files("loras")
        inputs = []
        for i in range(1, MAX_LORAS + 1):
            inputs.append(io.Combo.Input(f"lora_{i}", options=options,
                                         default=_pick(options, "h3-realism-people") if i == 1 else "None"))
            inputs.append(io.Float.Input(f"strength_{i}", default=0.8, min=-2.0, max=2.0, step=0.05))
        return io.Schema(node_id="MiniMaxH3AIOLoras", display_name="H3 AIO LoRAs", category=CATEGORY,
                         description="Extra LoRAs (the turbo LoRA is a switch in Speed & Quality).",
                         inputs=inputs, outputs=[LORAS_T.Output(display_name="loras")])

    @classmethod
    def execute(cls, **kw) -> io.NodeOutput:
        return io.NodeOutput([(kw[f"lora_{i}"], kw[f"strength_{i}"]) for i in range(1, MAX_LORAS + 1)
                              if kw[f"lora_{i}"] != "None" and kw[f"strength_{i}"] != 0])


class MiniMaxH3AIOSpeed(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AIOSpeed", display_name="H3 AIO Speed & Quality", category=CATEGORY,
            description="How fast vs how detailed. Not connected = turbo 8 steps + Spectrum + live preview.",
            inputs=[
                io.Int.Input("steps", default=8, min=1, max=50,
                             tooltip="8 with the turbo LoRA (its quality sweet spot is 6-8). Without it use 20+."),
                io.Boolean.Input("speed_lora", default=True,
                                 tooltip="Turbo LoRA (file chosen in H3 AIO Models). Off = base model; raise steps to ~20."),
                io.Float.Input("speed_lora_strength", default=1.0, min=0.0, max=2.0, step=0.05),
                io.Boolean.Input("spectrum", default=True,
                                 tooltip="Forecasts some steps instead of running the model. Training-free, slight "
                                         "differences vs off. Needs 6+ steps."),
                io.Combo.Input("refine_strength", options=list(pipeline.REFINE), default="Balanced (3 steps)",
                               tooltip="Fast modes: the full-size detail pass after the latent upscale."),
                io.Combo.Input("reference_detail", options=list(pipeline.REFERENCE_DETAIL), default="High",
                               tooltip="Size reference pictures are given to H3. High/Max = better likeness, a little slower."),
                io.Boolean.Input("auto_prompt_format", default=True,
                                 tooltip="Adds H3's official Image-to-Video anchor line and the no-cut line for extra "
                                         "clips. Off = prompts are sent exactly as typed."),
                io.Float.Input("shift_video", default=12.0, min=1.0, max=30.0, step=0.5, optional=True,
                               tooltip="Sigma shift for the picture. 12 = Aitrepreneur's / the turbo LoRAs'; H3 Eros Max beta6 wants 10."),
                io.Float.Input("shift_audio", default=3.0, min=0.5, max=10.0, step=0.5, optional=True,
                               tooltip="Sigma shift for the sound (3 for every H3 build seen so far)."),
            ],
            outputs=[SPEED_T.Output(display_name="settings")])

    @classmethod
    def execute(cls, **kw) -> io.NodeOutput:
        return io.NodeOutput(kw)


class MiniMaxH3AIOSeamless(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AIOSeamless", display_name="H3 AIO Seamless Settings", category=CATEGORY,
            description="How clips join. Wire into the Seamless Planner or Long Video node.",
            inputs=[
                io.Combo.Input("carry_over", options=list(CARRY), default="1.6 s (39 frames) - default",
                               tooltip="How much of each clip's end is copied into the next one's start (in latent "
                                       "space, protected from change). Longer = steadier motion/voice across the join, "
                                       "but every extra clip costs that much more to render."),
                io.Combo.Input("audio_fade", options=list(FADE), default="0.2 s - default",
                               tooltip="Soft release of the carried-over sound into the new clip's sound."),
                io.Combo.Input("picture_blend", options=list(BLEND), default="full carry-over - default",
                               tooltip="Cross-fade of the pictures over the carried-over frames when the clips are "
                                       "joined. Full hides any tiny difference at the join."),
            ],
            outputs=[SEAM_T.Output(display_name="seamless")])

    @classmethod
    def execute(cls, carry_over, audio_fade, picture_blend) -> io.NodeOutput:
        frames = CARRY[carry_over]
        return io.NodeOutput({"context_frames": frames, "audio_feather": FADE[audio_fade],
                              "seam_blend": int(round(frames * BLEND[picture_blend]))})


class MiniMaxH3AIOPlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AIOPlanner", display_name="H3 AIO Seamless Planner", category=CATEGORY,
            description="Write the whole video as one story; it is split into seamless clips, each with its own "
                        "correctly formatted H3 prompt. For Long modes and Extend Video.",
            inputs=[
                io.String.Input("story", multiline=True, default="",
                                tooltip="The whole video in time order. Mark times (00:05, 5-10s:, [Shot 2] At 00:12) "
                                        "or breaks (---, Clip 2:) where you want them; without marks it is split "
                                        "evenly. H3 sections (subject_definitions, overall_soundscape, ...) are kept "
                                        "for every clip."),
                io.Float.Input("total_seconds", default=20.0, min=2.0, max=105.0, step=1.0,
                               tooltip="Length of the finished video (Extend Video: seconds added to your video)."),
                io.Combo.Input("seconds_per_clip", options=list(CLIP_SECONDS), default="5 s - fastest, most control",
                               tooltip="How the total is cut into clips (max 7 clips). Shorter clips follow the story "
                                       "more closely; 15 s is the longest H3 was trained on."),
                io.Combo.Input("prompt_format", options=["auto", "text / image modes", "reference modes"], default="auto",
                               tooltip="auto: six-section reference format when the story uses subject_definitions / "
                                       "detailed_description, else the three-field text format."),
                SEAM_T.Input("seamless", optional=True, tooltip="H3 AIO Seamless Settings. Not connected = defaults."),
            ],
            outputs=[LONG_T.Output(display_name="long_video"), io.String.Output(display_name="plan")])

    @classmethod
    def execute(cls, story, total_seconds, seconds_per_clip, prompt_format, seamless=None) -> io.NodeOutput:
        seam = seamless or dict(SEAM_DEFAULTS)
        if not story.strip():
            # nothing planned: the AIO uses its own prompt (and errors in a Long mode only if that is empty too)
            return io.NodeOutput(dict(seam, extensions=2, extension_seconds=5.0, prompts=[]),
                                 "No story written - the AIO uses its own prompt.")
        clip = CLIP_SECONDS[seconds_per_clip]
        count, extras = planner.clip_layout(total_seconds, clip, pipeline.MAX_EXTENSIONS + 1)
        ref = prompt_format == "reference modes" or (
            prompt_format == "auto" and any(k in story for k in ("subject_definitions", "detailed_description")))
        prompts = planner.plan(story, clip, count, seam["context_frames"] / pipeline.FPS, reference_mode=ref)
        long_video = dict(seam, planned=True, first_prompt=prompts[0], first_seconds=min(clip, total_seconds),
                          extensions=count - 1, extension_seconds=extras, prompts=prompts[1:])
        plan_text = "\n\n".join(
            f"===== clip {i + 1} ({min(clip, total_seconds) if i == 0 else extras[i - 1]:g} s new) =====\n{p}"
            for i, p in enumerate(prompts))
        head = (f"{count} clip(s) of up to {clip:g} s = {total_seconds:g} s, carry-over "
                f"{seam['context_frames']} frames. Use a Long mode (or Extend Video).\n\n")
        return io.NodeOutput(long_video, head + plan_text)


class MiniMaxH3AIOLongVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        inputs = [
            io.Int.Input("extra_clips", default=2, min=1, max=pipeline.MAX_EXTENSIONS,
                         tooltip="Clips added after the first one (Extend Video: after your video)."),
            io.Float.Input("seconds_per_clip", default=5.0, min=1.0, max=13.0, step=0.1,
                           tooltip="New seconds each extra clip adds."),
        ]
        for i in range(1, pipeline.MAX_EXTENSIONS + 1):
            inputs.append(io.String.Input(f"clip_{i}_prompt", multiline=True, default="",
                                          tooltip=f"What happens in extra clip {i}. Empty = the clip before's prompt."))
        inputs.append(SEAM_T.Input("seamless", optional=True, tooltip="H3 AIO Seamless Settings. Not connected = defaults."))
        return io.Schema(node_id="MiniMaxH3AIOLongVideo", display_name="H3 AIO Long Video", category=CATEGORY,
                         description="Write each extra clip's prompt yourself (or use the Seamless Planner).",
                         inputs=inputs, outputs=[LONG_T.Output(display_name="long_video")])

    @classmethod
    def execute(cls, extra_clips, seconds_per_clip, seamless=None, **prompts) -> io.NodeOutput:
        texts = [prompts[f"clip_{i}_prompt"] for i in range(1, extra_clips + 1)]
        return io.NodeOutput(dict(seamless or SEAM_DEFAULTS, extensions=extra_clips,
                                  extension_seconds=seconds_per_clip, prompts=texts))


# ---------------- the AIO node ----------------

class MiniMaxH3AIO(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AIO",
            display_name="MiniMax H3 AIO",
            category=CATEGORY,
            description="Every section of the MiniMax H3 ULTRA workflow in one node, tuned for 8-16 GB GPUs.",
            is_output_node=True,
            hidden=[io.Hidden.prompt],
            inputs=[
                io.Combo.Input("mode", options=list(pipeline.MODES), default="Text to Video",
                               tooltip="Aitrepreneur ULTRA V3 sections. Click the (i) button for what each mode needs."),
                io.String.Input("prompt", multiline=True, default="",
                                tooltip="Describe the clip in time order (see the (i) guide). Long modes: leave empty "
                                        "and use the Seamless Planner's story instead."),
                io.Boolean.Input("use_negative", default=False,
                                 tooltip="On = the negative prompt is used (CFG). Each step then runs twice."),
                io.String.Input("negative_prompt", multiline=True, default=NEGATIVE_DEFAULT,
                                tooltip="What to steer away from."),
                io.Float.Input("cfg", default=1.5, min=1.0, max=8.0, step=0.1,
                               tooltip="Negative-prompt strength. With the turbo LoRA keep 1.5-2.5."),
                io.Combo.Input("resolution", options=list(pipeline.RESOLUTIONS), default="720p (0.9 MP)",
                               tooltip="Final size. Fast modes draft at a quarter of it. 8 GB tips: Long / Inpainting "
                                       "480p-576p, Fast 720p-832p, Image Editing up to 1080p."),
                io.Combo.Input("aspect_ratio", options=list(pipeline.ASPECTS), default="16:9 landscape",
                               tooltip="Match input = shape of Picture 1."),
                io.Float.Input("duration_seconds", default=5.0, min=0.2, max=15.0, step=0.1,
                               tooltip="Length of the clip (Extend Video: of the first added clip). Snaps up to H3's "
                                       "frame grid: 5 s = 124 frames, 10 s = 243, 15 s = 362 (max)."),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff, control_after_generate=True),
                io.String.Input("inpaint_target", default="head",
                                tooltip="What SAM3 finds and replaces in Video 1 (head, face, shirt, person, dog...)."),
                io.Custom("H3_REFS").Input("references", optional=True,
                                           tooltip="MiniMax H3 Media Loader: Pictures 1-9, Videos 1-3 (with sound), Audios 1-3."),
                SPEED_T.Input("settings", optional=True, tooltip="H3 AIO Speed & Quality."),
                LONG_T.Input("long_video", optional=True,
                             tooltip="H3 AIO Seamless Planner or Long Video: extra clips for Long modes / Extend Video."),
                LORAS_T.Input("loras", optional=True, tooltip="H3 AIO LoRAs."),
                MODELS_T.Input("models", optional=True, tooltip="H3 AIO Models. Not connected = the best files found."),
            ],
            outputs=[io.Video.Output(display_name="video", tooltip="Video modes -> Save Video."),
                     io.Image.Output(display_name="image", tooltip="Image Editing -> Save Image."),
                     io.String.Output(display_name="info")],
        )

    @classmethod
    def execute(cls, references=None, settings=None, long_video=None, loras=None, models=None,
                **s) -> io.NodeOutput:
        s.update(SPEED_DEFAULTS)
        s.update(settings or {})
        s.update(models or default_models())
        s["loras"] = loras or []
        # the H3 Live Preview node's on/off switch decides whether the preview decoder is attached at all
        s["live_preview"] = preview_requested(cls.hidden.prompt if cls.hidden else None)
        lv = dict(SEAM_DEFAULTS, extensions=2, extension_seconds=5.0, prompts=[])
        lv.update(long_video or {})
        s.update(lv)
        s["extension_prompts"] = lv["prompts"]
        video, image, info = pipeline.Run(s, references).run()
        # The output this mode doesn't make is blocked, so its Save node just skips instead of erroring.
        return io.NodeOutput(video if video is not None else ExecutionBlocker(None),
                             image if image is not None else ExecutionBlocker(None), info)
