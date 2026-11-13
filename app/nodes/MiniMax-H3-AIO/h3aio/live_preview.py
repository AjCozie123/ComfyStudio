"""H3 Live Preview - a large, resizable view of the sampling preview while the MiniMax H3 AIO node runs.

Its `enabled` switch is the on/off for the whole preview: the AIO reads it from the queued prompt. Off = the
AIO does not attach the preview decoder at all (no per-step decode, less VRAM). The frames are sent by the
KJNodes preview override (madebyollin's taeh3, one frame per step); js/h3_live_preview.js draws them.
"""

from comfy_api.latest import io


class MiniMaxH3LivePreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3AIOLivePreview",
            display_name="H3 Live Preview",
            category="MiniMax H3 AIO",
            description="Watch the MiniMax H3 AIO clip form while it samples. Switch off to save VRAM.",
            is_output_node=True,
            inputs=[io.Boolean.Input("enabled", default=True,
                                     tooltip="On = the AIO decodes one small preview frame per step and shows it here. "
                                             "Off = no preview decoding at all (saves VRAM and a little time).")],
            outputs=[],
        )

    @classmethod
    def execute(cls, enabled=True) -> io.NodeOutput:
        return io.NodeOutput()


def preview_requested(prompt):
    """True when the queued prompt has an H3 Live Preview node that is switched on."""
    nodes = [n for n in (prompt or {}).values() if isinstance(n, dict) and n.get("class_type") == "MiniMaxH3AIOLivePreview"]
    return any(n.get("inputs", {}).get("enabled", True) for n in nodes)
