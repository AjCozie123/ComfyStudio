"""MiniMax H3 AIO - every MiniMax H3 video workflow in one node, tuned for 8-16 GB GPUs."""

import logging

import folder_paths
from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

from . import downloads
from .h3aio.live_preview import MiniMaxH3LivePreview
from .h3aio.node import (MiniMaxH3AIO, MiniMaxH3AIOLongVideo, MiniMaxH3AIOLoras, MiniMaxH3AIOModels,
                         MiniMaxH3AIOPlanner, MiniMaxH3AIOSeamless, MiniMaxH3AIOSpeed)

log = logging.getLogger("MiniMax-H3-AIO")

if "latent_upscale_models" not in folder_paths.folder_names_and_paths:
    import os
    folder_paths.add_model_folder_path("latent_upscale_models", os.path.join(folder_paths.models_dir, "latent_upscale_models"))

try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/h3_aio/models")
    async def _models(request):
        todo = downloads.missing(folder_paths.models_dir)
        return web.json_response({"missing": [{k: e[k] for k in ("file", "folder", "size_gb", "for")} for e in todo],
                                  **downloads.state})

    @PromptServer.instance.routes.post("/h3_aio/download")
    async def _download(request):
        return web.json_response({"started": downloads.start(folder_paths.models_dir)})
except Exception as e:
    log.warning("[H3 AIO] model routes not registered: %s", e)


class H3AIOExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MiniMaxH3AIO, MiniMaxH3AIOSpeed, MiniMaxH3AIOPlanner, MiniMaxH3AIOSeamless, MiniMaxH3AIOLongVideo,
                MiniMaxH3AIOLoras, MiniMaxH3AIOModels, MiniMaxH3LivePreview]


async def comfy_entrypoint() -> H3AIOExtension:
    return H3AIOExtension()


WEB_DIRECTORY = "./js"
