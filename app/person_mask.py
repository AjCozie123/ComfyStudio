"""Outline the person an edit is about, so everything else in the picture can be locked.

Run with ComfyUI's own Python (it has ultralytics + torch); the app calls it as a separate process, on the CPU, so it
never competes with ComfyUI for the graphics card:
    python person_mask.py <picture> <out_mask.png> <model.pt> [her|his|their]
Finds every person (a YOLO person-segmentation model, e.g. models/ultralytics/segm/person_yolov8m-seg.pt), picks the one
the edit is about (the biggest, most central one), and writes a black/white mask the size of the picture:
  white = that person, grown a little (sideways more than up/down, since a turned head / cap brim / arm sticks out
          further to the side), so the turned body fits inside it
  black = everything else, including every other person (they are cut out of the white area so they never change)
Prints one JSON line: {"ok": true, "people": n, "box": [x0, y0, x1, y1]} or {"ok": false, "why": "..."}.
"""
import json
import sys


def main():
    if len(sys.argv) < 4:
        print(json.dumps({"ok": False, "why": "usage"}))
        return
    src, out, model = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        import numpy as np
        from PIL import Image, ImageOps, ImageFilter
        from ultralytics import YOLO
    except Exception as e:
        print(json.dumps({"ok": False, "why": "missing package: %s" % e}))
        return
    with Image.open(src) as im:
        pic = ImageOps.exif_transpose(im).convert("RGB")
    W, H = pic.size
    r = YOLO(model).predict(np.asarray(pic)[:, :, ::-1], device="cpu", conf=0.35, classes=[0], verbose=False)[0]
    if r.masks is None or not len(r.boxes):
        print(json.dumps({"ok": False, "why": "no person found"}))
        return
    from PIL import ImageDraw
    masks = []
    for i, (b, c) in enumerate(zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist())):
        # the outline in the picture's own pixels (masks.data is in the model's padded working size, not the picture's)
        m = Image.new("L", (W, H), 0)
        for poly in ([r.masks.xy[i]] if len(r.masks.xy[i]) >= 3 else []):
            ImageDraw.Draw(m).polygon([(float(x), float(y)) for x, y in poly], fill=255)
        x0, y0, x1, y1 = b
        area = (x1 - x0) * (y1 - y0) / float(W * H)
        cx = (x0 + x1) / 2.0 / W
        # the one the edit is about: big and near the middle, a person cut by the picture's edge counts less
        edge = 0.6 if (x0 < W * 0.01 or x1 > W * 0.99) else 1.0
        score = area * (1.0 - min(0.8, abs(cx - 0.5) * 1.2)) * c * edge
        masks.append((score, [int(v) for v in b], m))
    masks.sort(key=lambda t: -t[0])
    _s, box, target = masks[0]
    bw, bh = box[2] - box[0], box[3] - box[1]
    t = np.asarray(target) > 100

    def grow(a, kx, ky):
        """Grow a True/False mask by kx pixels sideways and ky up/down (running sums: fast at any size)."""
        if kx > 0:
            cs = np.cumsum(np.pad(a.astype(np.int32), ((0, 0), (kx + 1, kx))), axis=1)
            a = (cs[:, 2 * kx + 1:] - cs[:, :-(2 * kx + 1)]) > 0
        if ky > 0:
            cs = np.cumsum(np.pad(a.astype(np.int32), ((ky + 1, ky), (0, 0))), axis=0)
            a = (cs[2 * ky + 1:, :] - cs[:-(2 * ky + 1), :]) > 0
        return a
    # A generous BOX around the person, not their outline: when someone turns, the old body has to be fully replaced
    # (an outline-shaped area left pieces of the old arm / hair at its border) and the seam has to fall on plain
    # background (an outline seam ran along the new body = a cut-out halo). 25% of their width each side, 6% of their
    # height above, down to the picture's edge if they are close to it.
    x0, y0, x1, y1 = box
    px, py = int(bw * 0.25), int(bh * 0.06)
    bx0, by0, bx1 = max(0, x0 - px), max(0, y0 - py), min(W, x1 + px)
    by1 = H if y1 + py >= H - bh * 0.05 else min(H, y1 + py)
    a = np.zeros((H, W), dtype=bool)
    a[by0:by1, bx0:bx1] = True
    a |= grow(t, max(2, int(bw * 0.12)), max(2, int(bh * 0.04)))
    # other people never change: cut them out (slightly grown) where they aren't the person themselves
    k = max(2, int(max(W, H) * 0.003))
    for _s2, _b2, m2 in masks[1:]:
        o = grow(np.asarray(m2) > 100, k, k)
        a &= ~(o & ~t)
    Image.fromarray((a * 255).astype("uint8")).save(out)
    print(json.dumps({"ok": True, "people": len(masks), "box": box}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # never take the edit down: the app just doesn't lock
        print(json.dumps({"ok": False, "why": "%s: %s" % (type(e).__name__, e)}))
