# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Sequence mode: a frame range as ONE ComfyUI job (or a few chunks).

No Nuke dependency. Only ComfyUI core nodes are added to the workflow, because
Comfy Cloud has no folder loaders: N x LoadImage -> Batch Images (max 50 inputs
per node; bigger ranges use groups of 50 joined by one more Batch Images).

Workflow metadata ("_comfy_bridge"):
    "sequence":    "batch"    frames are independent: the range can be split in chunks
                   "temporal" the model looks across frames: whole range in one job
                   "no"       single-image workflow: sequence mode is blocked (default)
    "frame_ratio": output frames per input frame (default 1; RTX frame gen x2 -> 2)
    "constraints": {"frames_rule": "8n+1", "size_multiple": 32, "max_frames": 121}
    "output_scale": size of the result / size sent (for the crop after a size pad)
"""

import struct

from .client import ComfyError

SEQ_BATCH, SEQ_TEMPORAL, SEQ_NO = "batch", "temporal", "no"
SEQ_KINDS = (SEQ_BATCH, SEQ_TEMPORAL, SEQ_NO)
MODE_FRAMES, MODE_SEQUENCE = "frames", "sequence"
BATCH_MAX = 50                     # inputs of one BatchImagesNode / BatchMasksNode
_IMG = ("BatchImagesNode", "images.image%d", 0)
_MSK = ("BatchMasksNode", "masks.mask%d", 1)


def sequence_kind(meta):
    k = str((meta or {}).get("sequence", SEQ_NO)).lower()
    return k if k in SEQ_KINDS else SEQ_NO


def kind_label(kind):
    return {SEQ_BATCH: "sequence: batch (can be split in chunks)",
            SEQ_TEMPORAL: "sequence: temporal (whole range in one job)",
            SEQ_NO: "sequence: no (single image only)"}[kind]


def frame_ratio(meta):
    try:
        r = float((meta or {}).get("frame_ratio", 1) or 1)
    except (TypeError, ValueError):
        r = 1.0
    return r if r > 0 else 1.0


def constraints(meta):
    c = (meta or {}).get("constraints") or {}
    return c if isinstance(c, dict) else {}


def check_mode(kind, n_frames, meta):
    """Raise a clear error when the workflow cannot run as a sequence."""
    if kind == SEQ_NO:
        raise ComfyError("This workflow works on one image at a time "
                         "(edit / inpaint / relight / render to photo...).\n"
                         "Sequence mode is not available for it: set 'mode' to frames.")
    mx = constraints(meta).get("max_frames")
    if mx and n_frames > int(mx) and kind == SEQ_TEMPORAL:
        raise ComfyError("This workflow takes at most %d frames in one job (range: %d).\n"
                         "Shorten the range." % (int(mx), n_frames))


def make_chunks(frames, size, kind):
    """List of frame lists. size <= 0 (or a temporal workflow) -> whole range."""
    frames = list(frames)
    if kind != SEQ_BATCH or not size or size <= 0 or size >= len(frames):
        return [frames]
    size = int(size)
    return [frames[i:i + size] for i in range(0, len(frames), size)]


def pad_count(n, rule):
    """Extra frames needed so n follows the rule ('8n+1' -> 1, 9, 17, 25...)."""
    if not rule:
        return 0
    rule = str(rule).replace(" ", "").lower()
    if rule == "8n+1":
        return (-(n - 1)) % 8
    raise ComfyError("Unknown frames_rule '%s' in the workflow metadata." % rule)


def pad_names(names, rule):
    """Repeat the last frame until the rule is met. Returns (names, added)."""
    add = pad_count(len(names), rule)
    return list(names) + [names[-1]] * add, add


def expected_outputs(n_real, ratio):
    """Output frames that belong to n_real input frames (2N-1 for x2 frame gen)."""
    if ratio == 1:
        return n_real
    return int(round((n_real - 1) * ratio)) + 1


def _links_to(wf, nid):
    """[(node, input, output index)] of every input linked to node nid."""
    out = []
    for k, n in wf.items():
        for inp, v in (n.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) == str(nid):
                out.append((k, inp, int(v[1])))
    return out


def _batch_tree(p, prefix, loaders, spec, title):
    """Add Batch nodes joining output spec[2] of the loaders. Returns the final node id."""
    cls, key, out_idx = spec
    groups = [loaders[i:i + BATCH_MAX] for i in range(0, len(loaders), BATCH_MAX)]
    if len(groups) > BATCH_MAX:
        raise ComfyError("Too many frames for one job (%d). Use a chunk size of %d or less."
                         % (len(loaders), BATCH_MAX * BATCH_MAX))
    ids = []
    for g, group in enumerate(groups):
        bid = "%s_b%02d" % (prefix, g)
        p[bid] = {"class_type": cls, "_meta": {"title": "%s batch %d" % (title, g)},
                  "inputs": {key % i: [lid, out_idx] for i, lid in enumerate(group)}}
        ids.append(bid)
    if len(ids) == 1:
        return ids[0]
    top = "%s_ball" % prefix
    p[top] = {"class_type": cls, "_meta": {"title": "%s batch (all)" % title},
              "inputs": {key % i: [bid, 0] for i, bid in enumerate(ids)}}
    return top


def _expand_one(p, nid, files, title):
    if nid not in p:
        raise ComfyError("Node '%s' does not exist in the workflow." % nid)
    if p[nid].get("class_type") != "LoadImage":
        raise ComfyError("Sequence mode needs a LoadImage for the %s (node %s is a %s)."
                         % (title, nid, p[nid].get("class_type")))
    links = _links_to(p, nid)
    del p[nid]
    prefix = "cbseq_%s" % nid
    loaders = []
    for i, f in enumerate(files):
        lid = "%s_%04d" % (prefix, i)
        p[lid] = {"class_type": "LoadImage", "_meta": {"title": "%s %d" % (title, i)},
                  "inputs": {"image": f}}
        loaders.append(lid)
    tops = {}
    for _k, _inp, idx in links:
        if idx not in tops:
            spec = _MSK if idx == 1 else _IMG
            tops[idx] = _batch_tree(p, prefix + ("m" if idx == 1 else ""), loaders, spec, title)
    for k, inp, idx in links:
        p[k]["inputs"][inp] = [tops[idx], 0]
    return tops.get(0) or tops.get(1)


def expand_to_sequence(wf, plate_node_id, files, mask_node_id=None, mask_files=None):
    """Copy of wf where the plate LoadImage (and the mask one) become N LoadImage
    + Batch Images (IMAGE links) / Batch Masks (MASK links), in frame order.
    The aux / extra inputs are not expanded. Returns the new prompt."""
    import json
    if not files:
        raise ComfyError("No frames to send.")
    p = json.loads(json.dumps(wf))
    _expand_one(p, str(plate_node_id), files, "plate")
    if mask_node_id and mask_files:
        if len(mask_files) != len(files):
            raise ComfyError("The mask has %d frames and the plate %d." % (len(mask_files), len(files)))
        _expand_one(p, str(mask_node_id), mask_files, "mask")
    return p


def apply_size_multiple(p, batch_id, save_id, w, h, multiple, out_scale=1.0):
    """Pad the batch right/bottom to a multiple of `multiple` and crop the result back
    before the save node. Returns (pad_w, pad_h)."""
    m = int(multiple or 0)
    if m <= 1:
        return 0, 0
    pw, ph = (-w) % m, (-h) % m
    if not pw and not ph:
        return 0, 0
    links = [(k, inp) for k, inp, idx in _links_to(p, batch_id) if idx == 0]
    p["cbseq_pad"] = {"class_type": "ImagePadForOutpaint", "_meta": {"title": "pad to %d" % m},
                      "inputs": {"image": [batch_id, 0], "left": 0, "top": 0,
                                 "right": pw, "bottom": ph, "feathering": 0}}
    for k, inp in links:
        p[k]["inputs"][inp] = ["cbseq_pad", 0]
    src = p[save_id]["inputs"]["images"]
    p["cbseq_crop"] = {"class_type": "ImageCrop", "_meta": {"title": "undo pad"},
                       "inputs": {"image": src, "x": 0, "y": 0,
                                  "width": int(round(w * out_scale)),
                                  "height": int(round(h * out_scale))}}
    p[save_id]["inputs"]["images"] = ["cbseq_crop", 0]
    return pw, ph


def png_size(path):
    """(width, height) of a PNG, read from its header."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ComfyError("Not a PNG: %s" % path)
    return struct.unpack(">II", head[16:24])


def output_frames(chunk, n_imgs, ratio, first_out):
    """Frame numbers for the images a chunk returned.
    ratio 1: the chunk's own (original) numbers; else continuous from first_out."""
    if ratio == 1:
        if n_imgs < len(chunk):
            raise ComfyError("Frames %d-%d: ComfyUI returned %d images for %d frames."
                             % (chunk[0], chunk[-1], n_imgs, len(chunk)))
        return list(chunk)
    n = expected_outputs(len(chunk), ratio)
    if n_imgs < n:
        raise ComfyError("ComfyUI returned %d images, %d expected (frame ratio %g)."
                         % (n_imgs, n, ratio))
    return list(range(first_out, first_out + n))
