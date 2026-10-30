# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Workflows, metadata and colour helpers (no Nuke dependency)."""

import json
import os
import re
import time

from .client import ComfyError
from .help import default_param_tip

META_KEY = "_comfy_bridge"
SAVE_TYPES = ("ComfyBridgeSaveEXR", "SaveImageAdvanced", "SaveImage", "PreviewImage")
_OUT_KEYS = ("exr", "images")
# workflows live next to comfy_bridge.py (one level above this package)
WORKFLOW_DIR = os.environ.get(
    "COMFY_BRIDGE_WORKFLOWS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "comfy_bridge_workflows"))

IN_VIEWER = "viewer transform"
IN_SRGB = "sRGB"
OUT_AUTO = "auto (from workflow)"
OUT_DATA = "data (depth / matte / normals)"
OUT_COLOR = "color (image)"
LIB_NONE = "(choose a workflow)"
LIB_TYPES = ("all", "image", "video")
EXR_LOADER = "ComfyBridgeLoadEXR"
SLOT_TYPES = ("LoadImage", EXR_LOADER)
MAX_SLOTS = 3
SLOT_FROM = ("plate", "aux", "mask")
SEND_COLOR = "color (like plate)"
SEND_DATA = "data (raw)"
LUT = "__lut__"


def load_workflow(path):
    """Return (api_workflow_without_metadata, meta)."""
    if not path or not os.path.isfile(path):
        raise ComfyError("Workflow not found:\n%s" % path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "nodes" in data and "links" in data:
        raise ComfyError("This JSON is in UI format, not API format.\n"
                         "In ComfyUI use Workflow > Export (API).")
    meta = data.get(META_KEY) or {}
    wf = {k: v for k, v in data.items()
          if not str(k).startswith("_") and isinstance(v, dict) and "class_type" in v}
    if not wf:
        raise ComfyError("The workflow has no nodes.")
    return wf, meta


def _clean_label(text):
    # names go into a .nk pulldown: keep them free of quotes / braces / brackets
    return re.sub(r'[{}\[\]"\\]', "", text).strip() or "workflow"


def library_kind(meta):
    """'video' (whole shot only), 'image' (one image at a time) or 'both' (a still
    or a shot: depth, upscale...). "media" in the metadata wins; otherwise from
    "sequence": temporal -> video, batch -> both, no -> image."""
    media = str((meta or {}).get("media", "")).lower()
    if media in ("image", "video", "both"):
        return media
    seq = str((meta or {}).get("sequence", "no")).lower()
    return {"temporal": "video", "batch": "both"}.get(seq, "image")


def scan_library(folder=WORKFLOW_DIR, show="all"):
    """{display name: path} for the workflows in the library folder.
    show: 'all', 'image' or 'video' ('both' workflows appear in image and video)."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(folder, fn)
        try:
            _, meta = load_workflow(path)
        except Exception:
            continue
        if show in ("image", "video") and library_kind(meta) not in (show, "both"):
            continue
        name = _clean_label(meta.get("name") or os.path.splitext(fn)[0])
        if name in out:
            name = "%s (%s)" % (name, fn)
        out[name] = path.replace("\\", "/")
    return out


def find_node(wf, wanted_id, class_types, exclude=()):
    if wanted_id:
        if wanted_id not in wf:
            raise ComfyError("Node '%s' does not exist in the workflow." % wanted_id)
        return wanted_id
    for nid in sorted(wf, key=lambda k: int(k) if str(k).isdigit() else 0):
        if nid in exclude:
            continue
        if wf[nid].get("class_type") in class_types:
            return nid
    raise ComfyError("No %s node found in the workflow." % " / ".join(class_types))


# Help for parameters that the workflow metadata does not describe.
# Looked up by label first (prompt / negative share the same input name), then
# by (node class, input), then by input name alone.


def param_specs(wf, meta):
    specs = []
    for i, p in enumerate(meta.get("params", [])):
        nid, inp = str(p.get("node", "")), p.get("input", "")
        if nid not in wf or inp not in wf[nid].get("inputs", {}):
            continue
        cur = wf[nid]["inputs"][inp]
        if isinstance(cur, list):  # a link, not a value
            continue
        typ = p.get("type") or ("bool" if isinstance(cur, bool)
                                else "int" if isinstance(cur, int)
                                else "float" if isinstance(cur, float) else "string")
        # "also": more [node, input] pairs that get the same value
        also = [[str(a[0]), a[1]] for a in (p.get("also") or [])
                if str(a[0]) in wf and a[1] in wf[str(a[0])].get("inputs", {})]
        specs.append({"knob": "p_%d" % i, "label": p.get("label", inp), "node": nid,
                      "input": inp, "type": typ,
                      "default": fit_int(p.get("default", cur)) if typ == "int" else p.get("default", cur),
                      "min": p.get("min"), "max": p.get("max"),
                      "options": p.get("options"), "also": also,
                      "tooltip": p.get("tooltip") or default_param_tip(wf[nid].get("class_type"), inp, p.get("label", inp))})
    if meta.get("sampler_params", True):
        specs += sampler_specs(wf, specs)
    return specs


# KSampler inputs that are always offered, even if the workflow metadata
# does not list them: (input, label, type, min, max)
SAMPLER_TYPES = ("KSampler", "KSamplerAdvanced")
SAMPLER_INPUTS = (("seed", "seed", "int", None, None),
                  ("noise_seed", "seed", "int", None, None),
                  ("steps", "steps", "int", 1, 100),
                  ("cfg", "cfg", "float", 1.0, 10.0),
                  ("sampler_name", "sampler", "string", None, None),
                  ("scheduler", "scheduler", "string", None, None),
                  ("denoise", "denoise", "float", 0.0, 1.0))


def sampler_specs(wf, declared):
    """Specs for the KSampler inputs not already exposed by the metadata.
    Inputs fed by a link are skipped. With several samplers the label
    gets the node id."""
    taken = {(s["node"], s["input"]) for s in declared}
    for s in declared:
        taken.update((a[0], a[1]) for a in s.get("also") or [])
    samplers = sorted((k for k, v in wf.items()
                       if isinstance(v, dict) and v.get("class_type") in SAMPLER_TYPES), key=str)
    out = []
    for nid in samplers:
        node = wf[nid]
        for inp, label, typ, lo, hi in SAMPLER_INPUTS:
            cur = node.get("inputs", {}).get(inp)
            if cur is None or isinstance(cur, list) or (nid, inp) in taken:
                continue
            if len(samplers) > 1:
                label = "%s (%s)" % (label, nid)
            default = float(cur) if typ == "float" else int(cur) if typ == "int" else cur
            if typ == "int":
                default = fit_int(default)
            out.append({"knob": "p_k%s_%s" % (re.sub(r"\W", "_", nid), inp), "label": label,
                        "node": nid, "input": inp, "type": typ, "default": default,
                        "min": lo, "max": hi, "options": None, "also": [],
                        "tooltip": default_param_tip(node.get("class_type"), inp, label)})
    return out


def combo_options(node_info, input_name):
    """List of choices of a combo input in /object_info, or None.
    Handles the old format ([[choices], {...}]) and the new one
    (["COMBO", {"options": [...]}])."""
    inputs = (node_info or {}).get("input", {})
    for section in ("required", "optional"):
        spec = inputs.get(section, {}).get(input_name)
        if not spec:
            continue
        first = spec[0]
        if isinstance(first, list):
            return [str(o) for o in first]
        if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            opts = spec[1].get("options")
            if isinstance(opts, list):
                return [str(o) for o in opts]
    return None


def _model_key(name):
    return os.path.basename(str(name).replace("\\", "/")).lower()


def resolve_choice(value, options):
    """Return (choice, found). Exact match first, then same file in another
    subfolder ('x.safetensors' <-> 'Qwen\\x.safetensors')."""
    if value in options:
        return value, True
    key = _model_key(value)
    for o in options:
        if _model_key(o) == key:
            return o, True
    return value, False


def apply_server_options(wf, specs, get_info):
    """Turn string params that are combos on the server into dropdowns.
    get_info(class_type) -> /object_info entry (may raise). Returns the list of
    values that were not found on the server."""
    cache, missing = {}, []
    for s in specs:
        if s.get("options") or s["type"] not in ("string",):
            continue
        cls = wf[s["node"]].get("class_type")
        if cls not in cache:
            try:
                cache[cls] = get_info(cls)
            except Exception:
                cache[cls] = None
        opts = combo_options(cache[cls], s["input"])
        if not opts:
            continue
        choice, found = resolve_choice(str(s["default"]), opts)
        if not found:
            missing.append("%s: %s" % (s["label"], choice))
            opts = [choice] + opts
        s["default"], s["options"] = choice, opts
    return missing


_MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft")


def resolve_model_files(wf, get_info):
    """Rewrite model file names to the spelling the server lists. Workflows saved on
    Windows say 'Qwen\\x.safetensors'; Linux lists 'Qwen/x.safetensors' and Comfy
    Cloud often just 'x.safetensors'. Matches by file name. Changes wf in place and
    returns the 'node.input: value' entries the server does not have."""
    cache, missing = {}, []
    for nid, node in wf.items():
        cls = node.get("class_type")
        for inp, val in list(node.get("inputs", {}).items()):
            if inp == "image" or not isinstance(val, str) or not val.lower().endswith(_MODEL_EXT):
                continue
            if cls not in cache:
                try:
                    cache[cls] = get_info(cls)
                except Exception:
                    cache[cls] = None
            opts = combo_options(cache[cls], inp)
            if not opts or val in opts:
                continue
            choice, found = resolve_choice(val, opts)
            if found:
                node["inputs"][inp] = choice
            else:
                missing.append("%s.%s: %s" % (nid, inp, val))
    return missing


INT_MAX = 2 ** 31 - 1


def fit_int(value):
    """Nuke Int knobs are 32-bit on Windows: big seeds from templates are folded in."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return value
    return v % INT_MAX if abs(v) > INT_MAX else v


def cast_param(spec, value):
    t = spec["type"]
    if t == "bool":
        return value in (True, 1, "1", "true", "True")
    if t == "int":
        return int(round(float(value)))
    if t == "float":
        return float(value)
    return str(value)


def slot_specs(wf, meta):
    """Extra inputs declared in the workflow metadata:
    "inputs": [{"node": "2", "label": "depth", "from": "plate|aux|mask",
                "layer": "depth", "mono": true, "send": "color|data"}]
    A LoadImage node gets an 8-bit PNG; a Load EXR (ComfyBridge) node a float EXR."""
    out = []
    for i, d in enumerate((meta.get("inputs") or [])[:MAX_SLOTS]):
        nid = str(d.get("node", ""))
        if nid not in wf:
            raise ComfyError("Input %d: node '%s' does not exist in the workflow." % (i + 1, nid))
        cls = wf[nid].get("class_type")
        if cls not in SLOT_TYPES:
            raise ComfyError("Input %d: node '%s' is a %s; it must be a LoadImage or a "
                             "Load EXR (ComfyBridge)." % (i + 1, nid, cls))
        src = str(d.get("from", "plate")).lower()
        exr = cls == EXR_LOADER
        out.append({"n": i + 1, "node": nid, "label": _clean_label(d.get("label") or "input %d" % (i + 1)),
                    "from": SLOT_FROM.index(src) if src in SLOT_FROM else 0,
                    "layer": d.get("layer") or "rgba", "mono": bool(d.get("mono")),
                    "exr": exr, "data": exr or d.get("send") == "data",
                    "exposure": bool(d.get("match_exposure"))})
    return out


def build_prompt(wf, load_id, save_id, image_name, prefix, is_data, display_mode, params=None,
                 mask_id=None, mask_name=None, extra_images=None):
    """display_mode: True when the plate was sent through the viewer transform
    (the result is display-referred and Nuke undoes it)."""
    p = json.loads(json.dumps(wf))
    p[load_id]["inputs"]["image"] = image_name
    if mask_id and mask_name:
        p[mask_id]["inputs"]["image"] = mask_name
    for nid, name in (extra_images or {}).items():
        p[nid]["inputs"]["image"] = name
    for (nid, inp), val in (params or {}).items():
        p[nid]["inputs"][inp] = val
    save = p[save_id]
    if "filename_prefix" in save["inputs"]:
        save["inputs"]["filename_prefix"] = prefix
    if save.get("class_type") == "ComfyBridgeSaveEXR":
        if is_data:
            save["inputs"]["transfer"], save["inputs"]["precision"] = "data (as is)", "float"
        elif display_mode:
            save["inputs"]["transfer"], save["inputs"]["precision"] = "data (as is)", "half"
        else:
            save["inputs"]["transfer"], save["inputs"]["precision"] = "sRGB -> linear", "half"
    elif save.get("class_type") == "SaveImageAdvanced" and save["inputs"].get("format") == "exr":
        # core EXR saver (local and Cloud). Data and viewer-transform plates go out as is;
        # colour keeps the workflow's choice (sRGB, HDR for TrueHDR / HLG, linear...).
        if is_data:
            save["inputs"]["format.input_color_space"] = "linear"
            save["inputs"]["format.bit_depth"] = "32-bit float"
        elif display_mode:
            save["inputs"]["format.input_color_space"] = "linear"
    return p


# ComfyBridge nodes exist only on a ComfyUI with our custom node installed.
# On Comfy Cloud the EXR saver is swapped for the core Save Image (Advanced),
# which writes float EXR too; the others have no Cloud equivalent yet.
CB_SAVE_EXR = "ComfyBridgeSaveEXR"
_CLOUD_EXR_SPACE = {"data (as is)": "linear", "sRGB -> linear": "sRGB"}


def cloud_unsupported(wf):
    """ComfyBridge nodes of the workflow that cannot run on Comfy Cloud."""
    return sorted(set(n["class_type"] for n in wf.values()
                      if n.get("class_type", "").startswith("ComfyBridge")
                      and n["class_type"] != CB_SAVE_EXR))


def adapt_for_cloud(prompt):
    """Return the prompt with ComfyBridge Save EXR replaced by SaveImageAdvanced (EXR)."""
    bad = cloud_unsupported(prompt)
    if bad:
        raise ComfyError("This workflow uses ComfyBridge nodes that are not on Comfy Cloud:\n  "
                         + "\n  ".join(bad) + "\nRun it on your local ComfyUI instead.")
    p = json.loads(json.dumps(prompt))
    for node in p.values():
        if node.get("class_type") != CB_SAVE_EXR:
            continue
        inp = node.get("inputs", {})
        node["class_type"] = "SaveImageAdvanced"
        node["inputs"] = {
            "images": inp.get("images"),
            "filename_prefix": inp.get("filename_prefix", "comfy_bridge/result"),
            "format": "exr",
            "format.bit_depth": "32-bit float" if inp.get("precision") == "float" else "16-bit float",
            "format.input_color_space": _CLOUD_EXR_SPACE.get(inp.get("transfer"), "linear"),
        }
    return p


def output_images(hist, save_id):
    outs = hist.get("outputs", {})
    for key in _OUT_KEYS:
        if save_id in outs and outs[save_id].get(key):
            return outs[save_id][key]
    for key in _OUT_KEYS:
        for o in outs.values():
            if o.get(key):
                return o[key]
    return []


def wait_for(client, prompt_id, is_cancelled=lambda: False, poll=None, timeout=3600, pending=()):
    """pending: every job id of this run, so a cancel also clears them from the Cloud queue."""
    poll = poll or getattr(client, "poll", 0.5)
    t0 = time.time()
    while True:
        if is_cancelled():
            client.interrupt(list(pending) or [prompt_id])
            raise ComfyError("Cancelled by the user.")
        h = client.history(prompt_id)
        if h:
            st = h.get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                detail = msgs[0][1].get("exception_message", "") if msgs else ""
                raise ComfyError("ComfyUI failed while running the workflow.\n" + detail)
            if st.get("completed", True):
                return h
        if time.time() - t0 > timeout:
            raise ComfyError("Timed out.")
        time.sleep(poll)


def next_version_dir(base):
    n = 0
    if os.path.isdir(base):
        for d in os.listdir(base):
            m = re.match(r"^v(\d{3,})$", d)
            if m and os.path.isdir(os.path.join(base, d)):
                n = max(n, int(m.group(1)))
    return os.path.join(base, "v%03d" % (n + 1)).replace("\\", "/")


def parse_viewer_process(text):
    """'ACES 1.0 - SDR Video (sRGB - Display)' -> ('sRGB - Display', 'ACES 1.0 - SDR Video')
    'sRGB' (Nuke colour management)        -> (LUT, 'sRGB')
    '' / 'None'                              -> None"""
    text = (text or "").strip()
    m = re.match(r"^(.+?)\s*\((.+)\)$", text)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    if text and text.lower() not in ("none", "raw"):
        return LUT, text
    return None


def ocio_names():
    """Colour space names of the active OCIO config, or None if unavailable."""
    try:
        import PyOpenColorIO as OCIO
        cfg = OCIO.GetCurrentConfig()
        return [cs.getName() for cs in cfg.getColorSpaces()]
    except Exception:
        return None


def pick_colorspace(candidates, available=None):
    if available:
        low = {a.lower(): a for a in available}
        for c in candidates:
            if c.lower() in low:
                return low[c.lower()]
    return candidates[-1] if candidates else None


_CS_SRGB = ["sRGB - Texture", "srgb_tx", "sRGB Encoded Rec.709 (sRGB)", "Output - sRGB", "sRGB"]
_CS_LINEAR = ["Linear Rec.709 (sRGB)", "lin_rec709", "Utility - Linear - sRGB", "linear"]
# scene-linear results in other primaries ("out_colorspace" in the metadata)
_CS_NAMED = {"acescg": ["ACEScg", "ACES - ACEScg", "acescg", "lin_ap1", "Linear AP1"],
             "linear_rec709": _CS_LINEAR}


def named_colorspace(name, available):
    """OCIO colour space for a metadata name ('acescg'), or None when the active
    config does not have it (None also when the config cannot be listed)."""
    cands = _CS_NAMED.get(str(name).lower(), [name])
    if not available:
        return None
    low = {a.lower(): a for a in available}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def match_layer(wanted, layers):
    """Layer to use: the wanted one if it exists, else the first layer whose name
    contains it ('depth' -> 'ViewLayer_Depth', 'normal' -> 'ViewLayer_Normal')."""
    if not layers or wanted in layers:
        return wanted
    w = wanted.lower()
    for l in layers:                       # whole word: ViewLayer_Depth, depth.Z...
        if w in re.split(r"[_.\s-]+", l.lower()):
            return l
    if len(w) >= 4:                        # longer names: substring
        for l in layers:
            if w in l.lower():
                return l
    return wanted
