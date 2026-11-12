"""Library metadata and safe delete/import for Comfy Studio outputs only."""
from __future__ import annotations

import os, shutil, time, urllib.parse


def app_out_root(output, app_out):
    return os.path.realpath(os.path.join(output, app_out))


def is_app_output(path, output, app_out):
    try:
        root = app_out_root(output, app_out)
        real = os.path.realpath(path)
        return os.path.commonpath([os.path.normcase(real), os.path.normcase(root)]) == os.path.normcase(root)
    except (ValueError, OSError):
        return False


def snapshot_edit_source(job, output, app_out, input_root):
    """Copy Picture 1 into the app output _sources folder so before/after survives later."""
    snaps = snapshot_all_inputs(job, output, app_out, input_root)
    if not snaps:
        return None, None
    first = snaps[0]
    return first.get("subfolder"), first.get("filename")


def snapshot_all_inputs(job, output, app_out, input_root):
    """Copy every input image into the app output _sources folder so Reuse still works later."""
    if job.get("mode") != "edit":
        return []
    srcs = job.get("source_images") or []
    if not srcs:
        return []
    sub = os.path.join(app_out, "_sources")
    dest_dir = os.path.join(output, sub)
    os.makedirs(dest_dir, exist_ok=True)
    snaps = []
    root = os.path.abspath(input_root)
    for i, raw in enumerate(srcs, 1):
        rel = str(raw).replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            continue
        src = os.path.abspath(os.path.join(input_root, rel.replace("/", os.sep)))
        if not (src.startswith(root + os.sep) and os.path.isfile(src)):
            continue
        ext = os.path.splitext(src)[1] or ".png"
        name = "%s_p%d%s" % (job["id"], i, ext.lower())
        dest = os.path.join(dest_dir, name)
        try:
            shutil.copy2(src, dest)
        except OSError:
            continue
        if not is_app_output(dest, output, app_out):
            continue
        snaps.append({
            "slot": i,
            "subfolder": sub.replace("\\", "/"),
            "filename": name,
            "source_name": rel,
        })
    return snaps



def library_record(job, output, app_out, input_root):
    img = (job.get("images") or [{}])[0]
    snaps = snapshot_all_inputs(job, output, app_out, input_root)
    src_sub = snaps[0]["subfolder"] if snaps else None
    src_name = snaps[0]["filename"] if snaps else None
    w, h = job.get("width"), job.get("height")
    if job.get("keep_input"):
        resolution = "keep"
    elif job.get("match_input"):
        resolution = "match"
    else:
        resolution = ("%dx%d" % (w, h)) if w and h else ""
    return {
        "id": job["id"],
        "t": time.time(),
        "mode": job.get("mode"),
        "prompt": job.get("prompt"),
        "seed": job.get("seed"),
        "steps": job.get("steps"),
        "width": w,
        "height": h,
        "resolution": resolution,
        "match_input": bool(job.get("match_input")),
        "keep_input": bool(job.get("keep_input")),
        "pe": bool(job.get("pe")),
        "pe_mode": job.get("pe_mode"),
        "prompt_user": job.get("prompt_user") or job.get("prompt"),
        "enhanced_prompt": job.get("enhanced_prompt") or "",
        "pe_fallback": bool(job.get("pe_fallback")),

        "cfg": job.get("cfg"),
        "sampler_name": job.get("sampler_name"),
        "scheduler": job.get("scheduler"),
        "filename": img.get("filename"),
        "subfolder": img.get("subfolder") or "",
        "source_images": job.get("source_images") or [],
        "source_subfolder": src_sub,
        "source_filename": src_name,
        "input_snapshots": snaps,
        "resolution_in": job.get("resolution_in") or resolution,
        "sampler_name": job.get("sampler_name"),
        "scheduler": job.get("scheduler"),
        "unet_name": (job.get("models") or {}).get("unet_name") or job.get("unet_name"),
        "clip_name": (job.get("models") or {}).get("clip_name") or job.get("clip_name"),
        "vae_name": (job.get("models") or {}).get("vae_name") or job.get("vae_name"),
        "pe_clip_name": job.get("pe_clip_used") or job.get("pe_clip_name"),
        "fav": False,
        "auto_assist": bool(job.get("auto_assist")),
        "roles": job.get("roles") or {},
        "camera": job.get("camera") or {},
        "ref_mode": bool(job.get("ref_mode")),
        "edit_kind": job.get("edit_kind") or ("ref" if job.get("ref_mode") else job.get("mode")),
        "prompt_sent": job.get("prompt_sent") or job.get("prompt") or "",
        "assist_preview": job.get("assist_preview") or "",
    }


def decorate(it):
    rec = dict(it)
    gid = urllib.parse.quote(it["id"])
    rec["url"] = "/api/image?id=" + gid
    rec["thumb"] = "/api/thumb?id=" + gid
    rec["download"] = "/api/download?id=" + gid
    snaps = list(it.get("input_snapshots") or [])
    if not snaps and it.get("source_filename"):
        snaps = [{"slot": 1, "subfolder": it.get("source_subfolder"), "filename": it.get("source_filename")}]
    rec["input_snapshots"] = [
        dict(s, url="/api/source?id=" + gid + "&slot=" + str(s.get("slot") or i + 1))
        for i, s in enumerate(snaps)
    ]
    if it.get("source_filename") or snaps:
        rec["before_url"] = "/api/source?id=" + gid + "&slot=1"
        rec["has_before"] = True
    else:
        rec["has_before"] = False
    rec["fav"] = bool(it.get("fav"))
    return rec


def copy_output_to_input(full, input_dir, input_sub, save_upload):
    """Read an app-owned output and save it as a normal input upload (for 'Edit this image')."""
    data = open(full, "rb").read()
    return save_upload(data, os.path.basename(full))