# ComfyStudio-Krea2Edit-V2

Comfy Studio's built-in **Krea 2 Edit** engine needs the Krea 2 in-context edit nodes (v2, with reference placement).
`krea2edit.py` is a verbatim copy of **comfyui-krea2edit** by lbouaraba
(https://github.com/lbouaraba/comfyui-krea2edit), redistributed under its Apache License 2.0 (full text in `LICENSE`).
All credit for the implementation goes to its author.

The nodes are registered as `Krea2EditModelPatchV2` and `Krea2EditGroundedEncodeV2`, so they never replace an
installed older comfyui-krea2edit (which uses the plain names). If these V2 names are already registered by another
pack, that copy is kept and this one does nothing.
