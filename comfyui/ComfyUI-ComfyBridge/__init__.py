# -*- coding: utf-8 -*-
# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""
ComfyUI-ComfyBridge
===================
"Save EXR (ComfyBridge)" node: saves images as float OpenEXR without going
through 8 bits. Self-contained EXR writer (scanline, uncompressed): only needs
numpy, which ships with ComfyUI.

"Load EXR" node: loads a float EXR sent by ComfyBridge (depth, AOVs, linear
passes) and turns it into an IMAGE: linear -> sRGB, raw data, or depth
normalised for depth ControlNets (Blender distance or Nuke 1/z).

"Mark Mask" node: tints the masked area so instruction-edit models know where to act.
"Crop To Mask" / "Stitch" nodes: work on the masked region at the model's
preferred resolution and paste the result back into the full frame.

Made for ComfyBridge / nuke-comfy-pipe, but works on its own.
https://github.com/dborrull/nuke-comfy-pipe
"""

import os
import struct

import numpy as np

import folder_paths

__version__ = "0.8.2"


# Minimal OpenEXR writer
_HALF, _FLOAT = 1, 2


def _attr(name, type_name, data):
    return (name.encode() + b"\0" + type_name.encode() + b"\0"
            + struct.pack("<i", len(data)) + data)


def write_exr(path, img, half=True):
    """img: float array (H, W, C) with C = 1, 3 or 4 (RGB(A) order)."""
    img = np.asarray(img, dtype=np.float32)
    if img.ndim == 2:
        img = img[:, :, None]
    h, w, c = img.shape
    names = {1: ["Y"], 3: ["R", "G", "B"], 4: ["R", "G", "B", "A"]}[c]
    # EXR requires channels in alphabetical order
    order = sorted(range(c), key=lambda i: names[i])
    ptype = _HALF if half else _FLOAT
    dtype = np.dtype("<f2") if half else np.dtype("<f4")

    chlist = b""
    for i in order:
        chlist += names[i].encode() + b"\0" + struct.pack("<iB3xii", ptype, 0, 1, 1)
    chlist += b"\0"

    header = b"\x76\x2f\x31\x01" + struct.pack("<i", 2)
    header += _attr("channels", "chlist", chlist)
    header += _attr("compression", "compression", b"\x00")
    header += _attr("dataWindow", "box2i", struct.pack("<iiii", 0, 0, w - 1, h - 1))
    header += _attr("displayWindow", "box2i", struct.pack("<iiii", 0, 0, w - 1, h - 1))
    header += _attr("lineOrder", "lineOrder", b"\x00")
    header += _attr("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    header += _attr("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0))
    header += _attr("screenWindowWidth", "float", struct.pack("<f", 1.0))
    header += b"\0"

    # (H, C, W): each scanline = whole channel 1, then whole channel 2...
    planes = np.ascontiguousarray(img[:, :, order].transpose(0, 2, 1)).astype(dtype)
    line_bytes = c * w * dtype.itemsize
    block = 8 + line_bytes
    start = len(header) + 8 * h
    offsets = np.arange(h, dtype="<u8") * block + start

    with open(path, "wb") as f:
        f.write(header)
        f.write(offsets.tobytes())
        size = struct.pack("<i", line_bytes)
        for y in range(h):
            f.write(struct.pack("<i", y))
            f.write(size)
            f.write(planes[y].tobytes())


def srgb_to_linear(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


# Minimal OpenEXR reader: single-part scanline files, uncompressed / ZIPS / ZIP
# (what Nuke's Write node produces by default). No dependencies but numpy.
_LINES_PER_BLOCK = {0: 1, 2: 1, 3: 16}          # NONE, ZIPS, ZIP
_PIX_DTYPE = {0: np.dtype("<u4"), 1: np.dtype("<f2"), 2: np.dtype("<f4")}


def _read_cstr(buf, pos):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("latin-1"), end + 1


def _parse_header(buf):
    if buf[:4] != b"\x76\x2f\x31\x01":
        raise ValueError("Not an OpenEXR file.")
    version = struct.unpack_from("<i", buf, 4)[0]
    if version & 0x200:
        raise ValueError("Tiled EXR is not supported (write scanline EXR from Nuke).")
    if version & 0x1000:
        raise ValueError("Multi-part EXR is not supported.")
    pos, attrs = 8, {}
    while buf[pos:pos + 1] != b"\0":
        name, pos = _read_cstr(buf, pos)
        typ, pos = _read_cstr(buf, pos)
        size = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
        attrs[name] = (typ, buf[pos:pos + size])
        pos += size
    return attrs, pos + 1


def _parse_chlist(data):
    chans, pos = [], 0
    while data[pos:pos + 1] != b"\0":
        name, pos = _read_cstr(data, pos)
        ptype, _plin, xs, ys = struct.unpack_from("<iB3xii", data, pos)
        pos += 16
        if xs != 1 or ys != 1:
            raise ValueError("Subsampled channels are not supported (%s)." % name)
        chans.append((name, ptype))
    return chans


def _unzip_block(data):
    """OpenEXR ZIP: zlib, then undo the byte predictor and the even/odd split."""
    import zlib
    t = np.frombuffer(zlib.decompress(data), dtype=np.uint8).astype(np.int64)
    if len(t) > 1:
        t[1:] -= 128
        t = np.cumsum(t) & 0xFF
    t = t.astype(np.uint8)
    n = len(t)
    half = (n + 1) // 2
    out = np.empty(n, dtype=np.uint8)
    out[0::2] = t[:half]
    out[1::2] = t[half:]
    return out.tobytes()


def read_exr(path):
    """Return ({channel name: float32 array (H, W)}, (width, height)) laid out on
    the display window (data outside it is cropped, missing data is zero)."""
    with open(path, "rb") as f:
        buf = f.read()
    attrs, pos = _parse_header(buf)
    chans = _parse_chlist(attrs["channels"][1])
    comp = attrs["compression"][1][0]
    if comp not in _LINES_PER_BLOCK:
        raise ValueError("EXR compression %d is not supported. In the Nuke Write use "
                         "'none' or 'Zip (1/16 scanlines)'." % comp)
    dx0, dy0, dx1, dy1 = struct.unpack("<iiii", attrs["dataWindow"][1])
    vx0, vy0, vx1, vy1 = struct.unpack("<iiii", attrs["displayWindow"][1])
    dw, dh = dx1 - dx0 + 1, dy1 - dy0 + 1
    lpb = _LINES_PER_BLOCK[comp]
    nblocks = (dh + lpb - 1) // lpb
    offsets = np.frombuffer(buf, dtype="<u8", count=nblocks, offset=pos)
    sizes = [_PIX_DTYPE[p].itemsize for _, p in chans]
    data = {name: np.zeros((dh, dw), np.float32) for name, _ in chans}
    for off in offsets:
        off = int(off)
        y, size = struct.unpack_from("<ii", buf, off)
        raw = buf[off + 8: off + 8 + size]
        lines = min(lpb, dy1 - y + 1)
        expected = lines * dw * sum(sizes)
        if comp != 0 and size < expected:   # stored raw when compression did not help
            raw = _unzip_block(raw)
        p = 0
        for ly in range(lines):
            row = y - dy0 + ly
            for (name, ptype), sz in zip(chans, sizes):
                data[name][row] = np.frombuffer(raw, _PIX_DTYPE[ptype], dw, p).astype(np.float32)
                p += dw * sz
    W, H = vx1 - vx0 + 1, vy1 - vy0 + 1
    out = {}
    x0, y0 = max(dx0, vx0), max(dy0, vy0)
    x1, y1 = min(dx1, vx1), min(dy1, vy1)
    for name, arr in data.items():
        full = np.zeros((H, W), np.float32)
        if x1 >= x0 and y1 >= y0:
            full[y0 - vy0:y1 - vy0 + 1, x0 - vx0:x1 - vx0 + 1] = \
                arr[y0 - dy0:y1 - dy0 + 1, x0 - dx0:x1 - dx0 + 1]
        out[name] = full
    return out, (W, H)


_SUFFIX = {"r": 0, "red": 0, "g": 1, "green": 1, "b": 2, "blue": 2, "a": 3, "alpha": 3}


def pick_channels(chans):
    """EXR channel names -> (rgb names or None, alpha name or None, first channel).
    Works with 'R,G,B,A', 'layer.R', 'layer.red', 'Z', 'depth.Z'..."""
    rgba = [None] * 4
    others = []
    for name in sorted(chans):
        suf = name.rsplit(".", 1)[-1].lower()
        if suf in _SUFFIX and rgba[_SUFFIX[suf]] is None:
            rgba[_SUFFIX[suf]] = name
        else:
            others.append(name)
    rgb = rgba[:3] if all(rgba[:3]) else None
    first = rgba[0] or (others[0] if others else rgba[3])
    return rgb, rgba[3], first


DEPTH_MODES = ["depth: distance -> 1/z (disparity)", "depth: distance (normalize + invert)",
               "depth: Nuke 1/z (normalize)"]
EXR_MODES = ["color (linear -> sRGB)", "data (as is)", "data (clamp 0-1)"] + DEPTH_MODES


def depth_to_image(z, mode, low_pct=0.5, high_pct=99.5):
    """z: (H, W) depth -> (H, W) in 0..1, near = white, background = black.
    distance modes: Blender / most renderers (Z = distance, sky = huge or inf).
    Nuke mode     : ScanlineRender depth (already 1/z, sky = 0)."""
    z = np.asarray(z, np.float32)
    finite = np.isfinite(z)
    if mode == DEPTH_MODES[2]:
        valid = finite & (z > 0)
        v = z
    else:
        # background: inf / NaN, half max (65504) or absurdly far for the scene
        valid = finite & (z > 0) & (z < 6.0e4)
        if valid.any():
            valid &= z < np.median(z[valid]) * 1.0e3
        v = 1.0 / np.maximum(z, 1e-6) if mode == DEPTH_MODES[0] else z
    if not valid.any():
        return np.zeros_like(z)
    lo, hi = np.percentile(v[valid], [low_pct, high_pct])
    n = np.clip((v - lo) / max(float(hi - lo), 1e-8), 0.0, 1.0)
    if mode == DEPTH_MODES[1]:
        n = 1.0 - n
    n = np.where(valid, n, 0.0)
    return n.astype(np.float32)


def exr_to_image(chans, mode="data (as is)", channels="auto"):
    """chans: dict from read_exr -> (H, W, 3) float32 image and (H, W) alpha."""
    rgb, a, first = pick_channels(chans)
    if first is None:
        raise ValueError("The EXR has no channels.")
    any_ch = chans[first]
    alpha = chans[a] if a else np.ones_like(any_ch)
    mono = channels == "first channel (gray)" or (channels == "auto" and rgb is None) \
        or mode in DEPTH_MODES
    if mono:
        c = chans[first]
        if mode in DEPTH_MODES:
            c = depth_to_image(c, mode)
        img = np.stack([c, c, c], axis=-1)
    else:
        if rgb is None:
            raise ValueError("No R/G/B channels in the EXR (channels: %s)." % ", ".join(sorted(chans)))
        img = np.stack([chans[n] for n in rgb], axis=-1)
    if mode == "color (linear -> sRGB)":
        img = linear_to_srgb(img)
    if mode != "data (as is)":
        img = np.clip(img, 0.0, 1.0)
    img = np.nan_to_num(img, nan=0.0, posinf=0.0, neginf=0.0)
    return img.astype(np.float32), alpha.astype(np.float32)


# ComfyUI node
class ComfyBridgeSaveEXR(object):
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "comfy_bridge"}),
                "transfer": (["data (as is)", "sRGB -> linear"],),
                "precision": (["half", "float"],),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "ComfyBridge"

    def save(self, images, filename_prefix="comfy_bridge",
             transfer="data (as is)", precision="half"):
        h, w = int(images.shape[1]), int(images.shape[2])
        full_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir, w, h)
        results = []
        for img in images:
            arr = img.cpu().numpy().astype(np.float32)
            if transfer == "sRGB -> linear":
                arr = arr.copy()
                arr[..., :3] = srgb_to_linear(arr[..., :3])  # alpha stays as is
            name = "%s_%05d_.exr" % (filename, counter)
            write_exr(os.path.join(full_folder, name), arr, half=(precision == "half"))
            results.append({"filename": name, "subfolder": subfolder, "type": "output"})
            counter += 1
        # custom "exr" key: the web UI does not try to preview it
        return {"ui": {"exr": results}}


# Crop to mask / stitch back (pure maths kept separate so it is testable)
def mask_bbox(mask, threshold=0.01):
    """mask: array (H, W) or (B, H, W). Union bbox (x0, y0, x1, y1), x1/y1 exclusive."""
    m = np.asarray(mask)
    if m.ndim == 3:
        m = m.max(axis=0)
    ys, xs = np.nonzero(m > threshold)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def padded_box(bbox, width, height, padding, min_size=64):
    """Grow the bbox by padding, keep it at least min_size and inside the frame."""
    if bbox is None:
        return 0, 0, width, height
    x0, y0, x1, y1 = bbox
    x0, y0, x1, y1 = x0 - padding, y0 - padding, x1 + padding, y1 + padding
    for lo, hi, size in ((0, 2, width), (1, 3, height)):
        box = [x0, y0, x1, y1]
        if box[hi] - box[lo] < min_size:
            c = (box[hi] + box[lo]) // 2
            box[lo], box[hi] = c - min_size // 2, c + min_size - min_size // 2
        if box[lo] < 0:
            box[hi] -= box[lo]
            box[lo] = 0
        if box[hi] > size:
            box[lo] -= box[hi] - size
            box[hi] = size
        box[lo] = max(0, box[lo])
        x0, y0, x1, y1 = box
    return int(x0), int(y0), int(x1), int(y1)


def work_size(cw, ch, target, multiple, max_scale=4.0):
    """Size the crop is processed at: long edge = target (0 = keep), never enlarged
    more than max_scale (tiny regions blown up too much lose the scene scale),
    sides rounded to multiples of 'multiple'."""
    multiple = max(1, int(multiple))
    if target and target > 0:
        scale = float(target) / max(cw, ch)
        if max_scale and max_scale > 0:
            scale = min(scale, float(max_scale))
        tw, th = cw * scale, ch * scale
    else:
        tw, th = cw, ch
    return (max(multiple, int(round(tw / multiple)) * multiple),
            max(multiple, int(round(th / multiple)) * multiple))


def color_match(gen, ref, ref_mask, strength=1.0, eps=1e-4):
    """Match per-channel mean / std of gen to ref, measured only where ref_mask
    is true (the untouched context around the mask). gen, ref: (H, W, 3);
    ref_mask: (H, W) bool. Returns the corrected gen (numpy float32)."""
    gen = np.asarray(gen, dtype=np.float32)
    ref = np.asarray(ref, dtype=np.float32)
    sel = np.asarray(ref_mask, dtype=bool)
    if sel.sum() < 64 or strength <= 0:
        return gen
    out = gen.copy()
    for c in range(3):
        g, r = gen[..., c][sel], ref[..., c][sel]
        gs, rs = g.std(), r.std()
        scale = rs / gs if gs > eps else 1.0
        scale = float(np.clip(scale, 0.25, 4.0))   # never go wild on flat areas
        out[..., c] = (gen[..., c] - g.mean()) * scale + r.mean()
    out = np.clip(out, 0.0, None)
    return gen + (out - gen) * float(strength)


def _resize(images, w, h):
    """images: torch (B, H, W, C) -> (B, h, w, C)."""
    import torch
    import torch.nn.functional as F
    if images.shape[1] == h and images.shape[2] == w:
        return images
    x = images.movedim(-1, 1)
    x = F.interpolate(x, size=(h, w), mode="bicubic", align_corners=False, antialias=True)
    return torch.clamp(x.movedim(1, -1), 0.0, 1.0)


class ComfyBridgeCropToMask(object):
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "mask": ("MASK",),
            "padding": ("INT", {"default": 64, "min": 0, "max": 4096,
                                "tooltip": "Context around the mask, in plate pixels."}),
            "target_size": ("INT", {"default": 1024, "min": 0, "max": 8192,
                                    "tooltip": "Long edge the crop is processed at (0 = native)."}),
            "multiple": ("INT", {"default": 16, "min": 1, "max": 128}),
            "max_scale": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 16.0, "step": 0.5,
                                    "tooltip": "Never enlarge the crop more than this (0 = no limit)."}),
        }}

    RETURN_TYPES = ("IMAGE", "MASK", "CB_CROP")
    RETURN_NAMES = ("image", "mask", "crop_info")
    FUNCTION = "crop"
    CATEGORY = "ComfyBridge"

    def crop(self, image, mask, padding=64, target_size=1024, multiple=16, max_scale=4.0):
        _, H, W, _ = image.shape
        m = mask
        if m.dim() == 2:
            m = m.unsqueeze(0)
        if m.shape[1] != H or m.shape[2] != W:  # mask at another size: bring it to the plate
            m = _resize(m.unsqueeze(-1), W, H)[..., 0]
        x0, y0, x1, y1 = padded_box(mask_bbox(m.cpu().numpy()), W, H, padding)
        tw, th = work_size(x1 - x0, y1 - y0, target_size, multiple, max_scale)
        img = _resize(image[:, y0:y1, x0:x1, :], tw, th)
        msk = _resize(m[:, y0:y1, x0:x1].unsqueeze(-1), tw, th)[..., 0]
        info = {"box": (x0, y0, x1, y1), "size": (W, H),
                "mask": m[:, y0:y1, x0:x1].detach().cpu().numpy()}
        return (img, msk, info)


class ComfyBridgeStitch(object):
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "original": ("IMAGE",),
            "crop": ("IMAGE",),
            "crop_info": ("CB_CROP",),
        }, "optional": {
            "match_color": (["off", "on"], {"default": "off",
                            "tooltip": "Match level/colour of the generated crop to the untouched plate around the mask."}),
            "match_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            "matte": ("MASK", {"tooltip": "Optional matte of the crop (e.g. SAM3 of the generated object). "
                                          "Written to the alpha channel, zero outside the crop."}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "stitch"
    CATEGORY = "ComfyBridge"

    @staticmethod
    def _paste(dst, src, box):
        x0, y0, x1, y1 = box
        if src.shape[0] == 1 and dst.shape[0] > 1:
            dst[:, y0:y1, x0:x1, ...] = src[0]
        else:
            b = min(dst.shape[0], src.shape[0])
            dst[:b, y0:y1, x0:x1, ...] = src[:b]

    def stitch(self, original, crop, crop_info, matte=None, match_color="off", match_strength=1.0):
        import torch
        x0, y0, x1, y1 = box = crop_info["box"]
        patch = _resize(crop[..., :3], x1 - x0, y1 - y0).to(original.device, original.dtype)
        if match_color == "on" and crop_info.get("mask") is not None:
            ref = original[:, y0:y1, x0:x1, :3].detach().cpu().numpy()
            gen = patch.detach().cpu().numpy()
            cm = crop_info["mask"]
            fixed = []
            for i in range(gen.shape[0]):
                mi = cm[min(i, cm.shape[0] - 1)] < 0.02      # context: outside the mask
                ri = ref[min(i, ref.shape[0] - 1)]
                fixed.append(color_match(gen[i], ri, mi, match_strength))
            patch = torch.from_numpy(np.stack(fixed)).to(original.device, original.dtype)
        out = original[..., :3].clone()
        self._paste(out, patch, box)
        if matte is None:
            return (out,)
        m = matte if matte.dim() == 3 else matte.unsqueeze(0)
        m = _resize(m.unsqueeze(-1).to(original.device, original.dtype), x1 - x0, y1 - y0)
        alpha = torch.zeros(out.shape[:3] + (1,), device=out.device, dtype=out.dtype)
        self._paste(alpha, m, box)
        return (torch.cat([out, alpha], dim=-1),)


class ComfyBridgeMarkMask(object):
    """Tint the masked area (red by default) so instruction-edit models that
    cannot take a mask (Qwen-Image-Edit...) know *where* to act:
    'add a bicycle in the red area'."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "mask": ("MASK",),
            "strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05}),
            "red": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}),
            "green": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0}),
            "blue": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "mark"
    CATEGORY = "ComfyBridge"

    def mark(self, image, mask, strength=0.5, red=1.0, green=0.0, blue=0.0):
        import torch
        m = mask if mask.dim() == 3 else mask.unsqueeze(0)
        if m.shape[1] != image.shape[1] or m.shape[2] != image.shape[2]:
            m = _resize(m.unsqueeze(-1), image.shape[2], image.shape[1])[..., 0]
        m = (m > 0.5).float().unsqueeze(-1).to(image.device, image.dtype) * float(strength)
        color = torch.tensor([red, green, blue], device=image.device, dtype=image.dtype)
        rgb = image[..., :3] * (1.0 - m) + color * m
        return (rgb,)


# Load EXR (depth / AOVs / linear passes sent by ComfyBridge)
def _input_path(name):
    if hasattr(folder_paths, "get_annotated_filepath"):
        try:
            return folder_paths.get_annotated_filepath(name)
        except Exception:
            pass
    return os.path.join(folder_paths.get_input_directory(), name)


class ComfyBridgeLoadEXR(object):
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("STRING", {"default": "",
                                 "tooltip": "EXR in the ComfyUI input folder (ComfyBridge fills this in)."}),
            "mode": (EXR_MODES, {"default": EXR_MODES[0],
                                 "tooltip": "How to turn the float data into a 0-1 image. Depth modes "
                                            "give near = white, like Depth Anything / depth ControlNets."}),
            "channels": (["auto", "rgb", "first channel (gray)"], {"default": "auto"}),
        }}

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "alpha")
    FUNCTION = "load"
    CATEGORY = "ComfyBridge"

    def load(self, image, mode=EXR_MODES[0], channels="auto"):
        import torch
        chans, _ = read_exr(_input_path(image))
        img, alpha = exr_to_image(chans, mode, channels)
        return (torch.from_numpy(img)[None], torch.from_numpy(alpha)[None])

    @classmethod
    def IS_CHANGED(cls, image, mode=None, channels=None):
        import hashlib
        path = _input_path(image)
        if not os.path.isfile(path):
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    @classmethod
    def VALIDATE_INPUTS(cls, image, mode=None, channels=None):
        if not image or not os.path.isfile(_input_path(image)):
            return "EXR not found in the input folder: %s" % image
        return True


NODE_CLASS_MAPPINGS = {
    "ComfyBridgeMarkMask": ComfyBridgeMarkMask,
    "ComfyBridgeLoadEXR": ComfyBridgeLoadEXR,
    "ComfyBridgeSaveEXR": ComfyBridgeSaveEXR,
    "ComfyBridgeCropToMask": ComfyBridgeCropToMask,
    "ComfyBridgeStitch": ComfyBridgeStitch,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComfyBridgeMarkMask": "Mark Mask (ComfyBridge)",
    "ComfyBridgeLoadEXR": "Load EXR (ComfyBridge)",
    "ComfyBridgeSaveEXR": "Save EXR (ComfyBridge)",
    "ComfyBridgeCropToMask": "Crop To Mask (ComfyBridge)",
    "ComfyBridgeStitch": "Stitch (ComfyBridge)",
}
