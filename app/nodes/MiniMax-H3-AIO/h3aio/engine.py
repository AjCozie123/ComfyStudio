"""Call ComfyUI node implementations directly from Python.

Nothing here reimplements a node. Every step looks the real class up in ComfyUI's live
registry (nodes.NODE_CLASS_MAPPINGS) and calls it, so MiniMaxH3ReferenceToVideo,
SamplerCustomAdvanced, the Spectrum patch, the latent upscaler and the masked-context nodes
run exactly the code the graph would run, and follow their packs when those update.

V1 nodes have INPUT_TYPES / FUNCTION and need an instance; V3 nodes subclass
comfy_api io.ComfyNode and have a classmethod execute() returning io.NodeOutput.
"""

import nodes


class NodeCallError(RuntimeError):
    pass


def get_class(node_type):
    cls = nodes.NODE_CLASS_MAPPINGS.get(node_type)
    if cls is None:
        raise NodeCallError(f"node type {node_type!r} is not installed or failed to import - "
                            f"run install.bat, or check the ComfyUI console for that pack's import error")
    return cls


def has(node_type):
    return node_type in nodes.NODE_CLASS_MAPPINGS


def _run(node_type, kwargs):
    cls = get_class(node_type)
    if hasattr(cls, "define_schema") and hasattr(cls, "execute"):
        # V3 nodes read cls.hidden; PREPARE_CLASS_CLONE(None) builds the clone the executor uses.
        target = cls.PREPARE_CLASS_CLONE(None) if hasattr(cls, "PREPARE_CLASS_CLONE") else cls
        result = target.execute(**kwargs)
        ui = getattr(result, "ui", None)
        out = getattr(result, "result", result)
        return (out if isinstance(out, tuple) else (out,)), ui
    fn = getattr(cls(), cls.FUNCTION)
    out = fn(**kwargs)
    ui = None
    if isinstance(out, dict):
        ui = out.get("ui")
        out = out.get("result", ())
    return (out if isinstance(out, tuple) else (out,)), ui


def call(node_type, **kwargs):
    """Run a node and return its outputs as a tuple."""
    return _run(node_type, kwargs)[0]


def call1(node_type, **kwargs):
    return _run(node_type, kwargs)[0][0]


def call_ui(node_type, **kwargs):
    """Run an output node and return (outputs, ui dict)."""
    return _run(node_type, kwargs)
