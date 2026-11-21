"""Find the face in a picture and write either a head mask or a head-and-hair crop.

Run with ComfyUI's own Python (it has ultralytics + torch); the app calls it as a separate process on the CPU, so it
never competes with ComfyUI for the graphics card:
    python face_box.py head <picture> <out_mask.png> <face_model.pt>
        white = the head (face box grown up for the hair, sideways for the ears/hair, a little down for the chin),
        soft-edged; black = everything else. The mask is the size of the picture.
    python face_box.py crop <picture> <out.jpg> <face_model.pt>
        the head and hair cut out of the picture (square-ish, the face in the middle): a face reference without the
        rest of that photo (its background, shoulders, framing).
Prints one JSON line: {"ok": true, "box": [x0, y0, x1, y1], "faces": n} or {"ok": false, "why": "..."}.
Model: models/ultralytics/bbox/face_yolov8m.pt (any YOLO face bbox model works).
"""
import json
import sys


def pick(boxes, confs, W, H):
    """The face the edit is about: big, confident, near the middle."""
    best, score = None, -1.0
    for b, c in zip(boxes, confs):
        x0, y0, x1, y1 = b
        area = (x1 - x0) * (y1 - y0) / float(W * H)
        cx = (x0 + x1) / 2.0 / W
        s = area * c * (1.0 - min(0.7, abs(cx - 0.5)))
        if s > score:
            best, score = b, s
    return best


def main():
    if len(sys.argv) < 5:
        print(json.dumps({"ok": False, "why": "usage"}))
        return
    mode, src, out, model = sys.argv[1:5]
    try:
        import numpy as np
        from PIL import Image, ImageOps, ImageDraw, ImageFilter
        from ultralytics import YOLO
    except Exception as e:
        print(json.dumps({"ok": False, "why": "missing package: %s" % e}))
        return
    with Image.open(src) as im:
        pic = ImageOps.exif_transpose(im).convert("RGB")
    W, H = pic.size
    # big pictures are searched at a working size (faster on the CPU), boxes scaled back
    k = min(1.0, 1600.0 / max(W, H))
    small = pic.resize((max(1, int(W * k)), max(1, int(H * k)))) if k < 1 else pic
    r = YOLO(model).predict(np.asarray(small)[:, :, ::-1], device="cpu", conf=0.3, verbose=False)[0]
    if r.boxes is None or not len(r.boxes):
        print(json.dumps({"ok": False, "why": "no face found"}))
        return
    boxes = [[v / k for v in b] for b in r.boxes.xyxy.tolist()]
    x0, y0, x1, y1 = pick(boxes, r.boxes.conf.tolist(), W, H)
    fw, fh = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2.0
    if mode == "head":
        # head and hair: wider than the face, well above it (hair, updo), a little below the chin
        hx0, hx1 = cx - fw * 1.0, cx + fw * 1.0
        hy0, hy1 = y0 - fh * 0.85, y1 + fh * 0.22
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).ellipse([max(0, hx0), max(0, hy0), min(W, hx1), min(H, hy1)], fill=255)
        # the whole neck, down to the collarbones: the join then sits where the body's own skin and shadows hide it,
        # not across the middle of the neck
        ny1 = min(H, y1 + fh * 0.6)
        ImageDraw.Draw(m).rounded_rectangle([max(0, cx - fw * 0.6), max(0, y1 - fh * 0.2), min(W, cx + fw * 0.6), ny1],
                                            radius=int(fw * 0.3), fill=255)
        hy1 = max(hy1, ny1)
        m = m.filter(ImageFilter.GaussianBlur(max(2, fw * 0.04)))
        m = m.point(lambda v: 255 if v > 110 else 0)          # the app feathers the blend itself
        m.save(out)
        res_box = [int(max(0, hx0)), int(max(0, hy0)), int(min(W, hx1)), int(min(H, hy1))]
    elif mode == "crop":
        side = max(fw * 2.3, fh * 2.0)
        cy = (y0 + y1) / 2.0 - fh * 0.2                          # a bit up: the hair matters more than the neck
        bx0, by0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        bx1, by1 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        pic.crop((bx0, by0, bx1, by1)).save(out, quality=95)
        res_box = [bx0, by0, bx1, by1]
        x0, y0, x1, y1 = x0 - bx0, y0 - by0, x1 - bx0, y1 - by0     # the face inside the crop
    else:
        print(json.dumps({"ok": False, "why": "unknown mode"}))
        return
    print(json.dumps({"ok": True, "box": res_box, "face": [int(x0), int(y0), int(x1), int(y1)], "faces": len(boxes)}))


if __name__ == "__main__":
    main()
