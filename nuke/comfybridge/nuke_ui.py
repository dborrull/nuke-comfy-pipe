# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Nuke side. Every entry point receives the ComfyBridge node from its button
(nuke.thisNode()) and never asks Python for any other Node object: internal
nodes are reached through nuke.knob("Group.Node.knob") paths and expressions."""

import json
import os
import re
import tempfile

try:
    import nuke
except ImportError:  # lets the module be imported (and tested) outside Nuke
    nuke = None

from .client import ComfyClient, ComfyError
from .help import QUICK_HELP, REPO_URL
from . import models as _models
from .templates import bake_template, node_template
from .workflows import (CB_SAVE_EXR, IN_VIEWER, LIB_NONE, LUT, MAX_SLOTS, OUT_AUTO, OUT_DATA,
                        SAVE_TYPES, SEND_DATA, _CS_LINEAR, _CS_SRGB, adapt_for_cloud,
                        apply_server_options, build_prompt, cloud_unsupported,
                        cast_param, find_node, load_workflow, match_layer, next_version_dir,
                        ocio_names, output_images, param_specs, parse_viewer_process,
                        pick_colorspace, resolve_model_files, scan_library, slot_specs,
                        wait_for)


def _log(msg):
    print("[ComfyBridge] %s" % msg)


def show_help(node=None):
    nuke.message(QUICK_HELP)


def open_repo(node=None):
    import webbrowser
    try:
        webbrowser.open(REPO_URL)
    except Exception as e:
        nuke.message("ComfyBridge: open this page in your browser:\n%s\n(%s)" % (REPO_URL, e))


def _paste(text, beside=None, positions=None):
    """Paste .nk text. With beside=<node>, paste in the graph that contains
    that node: button callbacks of a Group run *inside* the group, so without
    this the nodes would land inside ComfyBridge. Costs one Node object.
    text may also be a callable returning (text, positions); it is called inside
    the target graph so it can check the node names already there.
    positions: {node name: (x, y)} applied by name (no Node objects)."""
    fd, path = tempfile.mkstemp(suffix=".nk", prefix="comfybridge_")
    os.close(fd)

    def _do():
        content, pos = text() if callable(text) else (text, positions)
        with open(path, "w") as f:
            f.write(content)
        nuke.nodePaste(path)
        for name, (x, y) in (pos or {}).items():
            try:
                nuke.knob(name + ".xpos", str(int(x)))
                nuke.knob(name + ".ypos", str(int(y)))
            except Exception as e:
                _log("could not place %s: %s" % (name, e))

    try:
        if beside is not None:
            full = beside.fullName()
            ctx = nuke.toNode(full.rsplit(".", 1)[0]) if "." in full else nuke.root()
            with ctx:
                _do()
        else:
            _do()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _unique_base(base):
    if not nuke.exists(base + "_Read"):
        return base
    i = 2
    while nuke.exists("%s_%d_Read" % (base, i)):
        i += 1
    return "%s_%d" % (base, i)


def _k(node, internal, knob, value=None):
    path = "%s.%s.%s" % (node.fullName(), internal, knob)
    return nuke.knob(path) if value is None else nuke.knob(path, str(value))


def _working_space():
    for k in ("workingSpaceLUT", "colorManagement_workingSpace"):
        try:
            v = nuke.knob("root." + k)
            if v:
                return v
        except Exception:
            pass  # expected: not every Nuke setup has it
    return "scene_linear"


def _viewer_process():
    """Viewer transform of the active Viewer (costs one Node object in NC)."""
    name = None
    try:
        av = nuke.activeViewer()
        if av is not None:
            name = av.node().name()
    except Exception:
        name = None
    for n in ([name] if name else []) + ["Viewer1"]:
        try:
            return parse_viewer_process(nuke.knob(n + ".viewerProcess"))
        except Exception:
            continue
    return None


BACKEND_CLOUD = 1   # index of "Comfy Cloud" in the backend pulldown


def _server(node):
    """Address for ComfyClient: 'cloud' when the backend pulldown says Comfy Cloud,
    else the server knob. Older nodes without the pulldown use the server knob."""
    try:
        if "backend" in node.knobs() and int(node["backend"].getValue()) == BACKEND_CLOUD:
            return "cloud"
    except Exception as e:
        _log("could not read the backend: %s" % e)
    return node["server"].value()


def knob_changed(node, knob):
    """Group knobChanged: grey out the address on Cloud and refresh the workflow
    (model lists differ between local ComfyUI and Comfy Cloud)."""
    try:
        name = knob.name()
    except Exception:
        return
    if name not in ("backend", "showPanel"):
        return
    cloud = _server(node) == "cloud" and "backend" in node.knobs()
    try:
        node["server"].setEnabled(not cloud)
    except Exception as e:
        _log("could not grey out the server: %s" % e)
    if name == "backend" and node["workflow"].value():
        node["wf_loaded"].setValue("")
        try:
            load_workflow_into(node, force=False)
        except Exception as e:
            _log("could not reload the workflow: %s" % e)


def create_node():
    _paste(node_template(list(scan_library()), os.environ.get("COMFY_BRIDGE_URL", "127.0.0.1:8188")))


def test_connection(node):
    client = ComfyClient(_server(node))
    if client.cloud:
        try:
            u = client.user()
        except ComfyError as e:
            nuke.message(str(e))
            return
        who = u.get("email") or u.get("name") or u.get("id") or "?"
        extra = ""
        for k in ("subscription", "plan", "tier"):
            if u.get(k):
                v = u[k]
                extra += "\n%s: %s" % (k, v.get("name") or v.get("tier") or v) if isinstance(v, dict) \
                    else "\n%s: %s" % (k, v)
        nuke.message("Connected to Comfy Cloud\naccount: %s%s" % (who, extra))
        return
    try:
        s = client.system_stats()
        dev = s.get("devices", [{}])[0]
        vram = dev.get("vram_total", 0) / (1024.0 ** 3)
        nuke.message("Connected to ComfyUI %s\n%s\nVRAM: %.1f GB" % (
            s.get("system", {}).get("comfyui_version", ""), dev.get("name", "?"), vram))
    except ComfyError as e:
        nuke.message(str(e))


def load_workflow_into(node, force=True):
    """Read the chosen workflow, show its description and build its parameter knobs."""
    lib = scan_library()
    choice = node["library"].value()
    if choice in lib:  # the library wins; pick "(choose a workflow)" to use a custom file
        node["workflow"].setValue(lib[choice])
    path = node["workflow"].evaluate()
    if not path:
        if force:
            nuke.message("Choose a workflow from the library or a .json file.")
        return
    if not force and path == node["wf_loaded"].value():
        return
    try:
        wf, meta = load_workflow(path)
    except ComfyError as e:
        nuke.message("ComfyBridge:\n" + str(e))
        return
    desc = meta.get("description", "")
    out = meta.get("output")
    if out in ("data", "color"):
        desc += "<br><small>output: %s</small>" % out
    node["wf_desc"].setValue("<i>%s</i>" % desc if desc else " ")
    # each workflow sets it: upscalers / outpaint keep their bigger size
    node["match_format"].setValue(bool(meta.get("match_format", True)))
    specs = param_specs(wf, meta)
    client = ComfyClient(_server(node), timeout=5)
    _update_models_info(node, wf, meta, client)
    missing = apply_server_options(wf, specs, client.object_info)
    _rebuild_params(node, specs)
    if missing and "models_info" in node.knobs():
        _log("not on the ComfyUI server: " + ", ".join(missing))
    elif missing:
        nuke.message("ComfyBridge: not found on the ComfyUI server (choose another one in "
                     "Parameters, or install it):\n\n" + "\n".join(missing))
    try:
        _configure_slots(node, slot_specs(wf, meta), meta)
    except ComfyError as e:
        nuke.message("ComfyBridge:\n" + str(e))
        return
    node["wf_loaded"].setValue(path)
    # refresh the library list in case new workflows were added
    try:
        node["library"].setValues([LIB_NONE] + list(lib))
        if choice in lib:
            node["library"].setValue(choice)
    except Exception as e:
        _log("could not refresh the library list: %s" % e)


# ---- models: check and download -------------------------------------------
_SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "comfy_bridge_settings.json")


_USER_SETTINGS = os.path.join(os.path.expanduser("~"), ".nuke", "comfy_bridge_settings.json")


def _settings():
    for p in (_SETTINGS, _USER_SETTINGS):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def _save_setting(key, value):
    """Save next to the install; if that folder is read-only (a shared studio
    install), save in the user's ~/.nuke instead."""
    s = _settings()
    s[key] = value
    for p in (_SETTINGS, _USER_SETTINGS):
        try:
            d = os.path.dirname(p)
            if not os.path.isdir(d):
                os.makedirs(d)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2)
            return
        except OSError as e:
            _log("could not save settings to %s: %s" % (p, e))


def _models_state(node, wf=None, meta=None, client=None):
    if wf is None:
        wf, meta = load_workflow(node["workflow"].evaluate())
    client = client or ComfyClient(_server(node), timeout=5)
    req = _models.required_models(wf, meta)
    return req, _models.check_models(req, client.list_models)


def _update_models_info(node, wf, meta, client):
    if "models_info" not in node.knobs():
        return
    if client.cloud:
        node["models_info"].setValue(_cloud_info(wf, client))
        return
    try:
        req, (present, missing, unknown) = _models_state(node, wf, meta, client)
    except Exception as e:
        node["models_info"].setValue("<small>could not check (%s)</small>" % e)
        return
    if not req:
        text = "<small>none needed (or downloaded by the workflow itself)</small>"
    elif missing:
        text = "<font color=orange><b>%d missing</b></font> <small>of %d: %s</small>" % (
            len(missing), len(req), ", ".join(m["file"] for m in missing))
    elif unknown:
        text = "<small>%d needed; this ComfyUI cannot list its models</small>" % len(req)
    else:
        text = "<font color=lightgreen>all %d found</font>" % len(req)
    node["models_info"].setValue(text)


def _cloud_info(wf, client):
    """Models line on Comfy Cloud: models live there; flag nodes it does not have."""
    bad = cloud_unsupported(wf)
    if bad:
        return "<font color=orange><b>not for Cloud</b></font> <small>(%s): use local</small>" % ", ".join(bad)
    try:
        missing = client.missing_nodes(n["class_type"] for n in wf.values()
                                       if n.get("class_type") != CB_SAVE_EXR)
    except ComfyError:
        missing = []
    if missing:
        return "<font color=orange><b>Cloud lacks</b></font> <small>%s</small>" % ", ".join(missing)
    return "<font color=lightgreen>Comfy Cloud</font> <small>(models on the server; check the Parameters lists)</small>"


def _models_dir():
    d = os.environ.get("COMFY_BRIDGE_MODELS") or _settings().get("models_dir") or ""
    if d and os.path.isdir(d):
        return d
    d = nuke.getInput("ComfyUI models folder (the one that contains diffusion_models, vae, loras...):",
                      d or ("C:/ComfyUI/ComfyUI/models" if os.name == "nt"
                            else os.path.expanduser("~/ComfyUI/models")))
    if not d:
        return None
    d = d.strip().strip('"').replace("\\", "/")
    if not os.path.isdir(d):
        nuke.message("ComfyBridge: folder not found:\n" + d)
        return None
    _save_setting("models_dir", d)
    return d


def download_models(node):
    if ComfyClient(_server(node)).cloud:
        nuke.message("ComfyBridge: on Comfy Cloud the models are on the server.\n"
                     "If a model list in Parameters shows a name in front that is not on Cloud, "
                     "pick one from the list.")
        return
    try:
        wf, meta = load_workflow(node["workflow"].evaluate())
        req, (present, missing, unknown) = _models_state(node, wf, meta)
    except ComfyError as e:
        nuke.message("ComfyBridge:\n" + str(e))
        return
    if not missing:
        nuke.message("ComfyBridge: all the models of this workflow are already in ComfyUI.")
        return
    server = node["server"].value()
    if not any(h in server for h in ("127.0.0.1", "localhost")):
        if not nuke.ask("ComfyUI runs on another machine (%s).\nModels are downloaded to a folder "
                        "you choose; it must be that machine's models folder (e.g. a shared drive). "
                        "Continue?" % server):
            return
    manual = [m for m in missing if not m.get("url")]
    auto = [m for m in missing if m.get("url")]
    lines = ["%s  ->  %s" % (m["file"], m["folder"]) for m in auto]
    if manual:
        lines.append("")
        lines.append("Manual download (no direct link):")
        lines += ["%s  ->  %s\n    %s" % (m["file"], m["folder"], m.get("page") or "search the file name")
                  for m in manual]
    if not auto:
        nuke.message("ComfyBridge:\n" + "\n".join(lines))
        return
    lic = sorted(set(m.get("licence", "") for m in auto if m.get("licence")))
    msg = "Download %d model(s)?\n\n%s" % (len(auto), "\n".join(lines))
    if lic:
        msg += "\n\nLicences: %s. Check them before paid work." % ", ".join(lic)
    if not nuke.ask(msg):
        return
    root = _models_dir()
    if not root:
        return
    task = nuke.ProgressTask("ComfyBridge - downloading models")
    done_files, failed = [], []
    try:
        for i, m in enumerate(auto):
            dest = os.path.join(root, m["folder"], m["file"]).replace("\\", "/")
            label = "%s (%d/%d)" % (m["file"], i + 1, len(auto))

            def progress(done, total, label=label):
                gb = done / 1e9
                if total:
                    task.setProgress(int(100.0 * done / total))
                    task.setMessage("%s  %.2f / %.2f GB" % (label, gb, total / 1e9))
                else:
                    task.setMessage("%s  %.2f GB" % (label, gb))

            task.setMessage(label)
            try:
                _models.download(m["url"], dest, progress, task.isCancelled)
                done_files.append(m["file"])
            except _models.DownloadCancelled:
                failed.append("%s: cancelled (resumes next time)" % m["file"])
                break
            except Exception as e:
                failed.append("%s: %s" % (m["file"], e))
    finally:
        task = None  # closes the progress bar
    report = []
    if done_files:
        report.append("Downloaded:\n" + "\n".join(done_files))
    if failed:
        report.append("Not downloaded:\n" + "\n".join(failed))
    if manual:
        report.append("Still to download by hand:\n" + "\n".join(m["file"] for m in manual))
    nuke.message("ComfyBridge:\n\n" + "\n\n".join(report))
    load_workflow_into(node)   # refresh dropdowns and the models line


def _has_slots(node):
    return "x1_node" in node.knobs()


def _configure_slots(node, slots, meta):
    """Show the input slots the workflow uses and set their defaults
    (stream, layer, mono, colour/data). Internal knobs are set by path."""
    if "mask_channels" in node.knobs():
        node["mask_channels"].setValue(1 if meta.get("mask_channels") == "rgba" else 0)
    if not _has_slots(node):
        if slots:
            nuke.message("ComfyBridge: this workflow needs extra inputs, and this node was "
                         "created with an older version. Create a new ComfyBridge.")
        return
    by_n = {sl["n"]: sl for sl in slots}
    layers = _plate_layers(node) if any(sl["from"] == 0 for sl in slots) else []
    for n in range(1, MAX_SLOTS + 1):
        sl = by_n.get(n)
        for k in ("x%d_div", "x%d_from", "x%d_layer", "x%d_send"):
            try:
                node[k % n].setVisible(sl is not None)
            except Exception as e:
                _log("could not show/hide %s: %s" % (k % n, e))
        if sl is None:
            node["x%d_node" % n].setValue("")
            node["x%d_title" % n].setValue("<i>not used by this workflow</i>")
            continue
        node["x%d_node" % n].setValue(sl["node"])
        kind = "float EXR" if sl["exr"] else "PNG"
        node["x%d_title" % n].setValue("<b>%s</b>  <small>-> node %s, %s</small>"
                                       % (sl["label"], sl["node"], kind))
        node["x%d_from" % n].setValue(sl["from"])
        node["x%d_send" % n].setValue(1 if sl["data"] else 0)
        try:
            node["x%d_send" % n].setEnabled(not sl["exr"])   # EXR is always raw data
        except Exception as e:
            _log("could not lock the send knob of input %d: %s" % (n, e))
        sh = "CB_X%dShuffle" % n
        try:
            layer = match_layer(sl["layer"], layers) if sl["from"] == 0 else sl["layer"]
            _k(node, sh, "in", layer)
        except Exception as e:
            _log("layer %r not found for input %d: %s" % (sl["layer"], n, e))
        for ch, src in (("red", "red"), ("green", "red" if sl["mono"] else "green"),
                        ("blue", "red" if sl["mono"] else "blue"), ("alpha", "alpha")):
            try:
                _k(node, sh, ch, src)
            except Exception as e:
                _log("could not set %s.%s: %s" % (sh, ch, e))
    node["inputs_info"].setValue(
        "<i>Extra images for this workflow. Take them from a layer of the plate, "
        "the aux input or the mask input.</i>" if slots else
        "<i>This workflow uses no extra inputs.</i>")


def _plate_layers(node):
    """Layers reaching the node (the group passes the plate through when it shows
    no result). Uses the node we already have: no extra Node objects."""
    try:
        return sorted(set(c.split(".")[0] for c in node.channels()))
    except Exception:
        return []


def _rebuild_params(node, specs):
    for k in list(node.knobs().values()):
        if k.name().startswith("p_") or k.name() == "params_tab":
            node.removeKnob(k)
    if not specs:
        node["param_map"].setValue("[]")
        return
    node.addKnob(nuke.Tab_Knob("params_tab", "Parameters"))
    for s in specs:
        name, label, t = s["knob"], s["label"], s["type"]
        if s.get("options"):
            k = nuke.Enumeration_Knob(name, label, [str(o) for o in s["options"]])
        elif t == "bool":
            k = nuke.Boolean_Knob(name, label)
        elif t == "float":
            k = nuke.Double_Knob(name, label)
            if s.get("min") is not None and s.get("max") is not None:
                k.setRange(float(s["min"]), float(s["max"]))
        elif t == "int":
            k = nuke.Int_Knob(name, label)
        elif t == "text":
            k = nuke.Multiline_Eval_String_Knob(name, label)
        else:
            k = nuke.String_Knob(name, label)
        tip = s.get("tooltip") or ""
        tip = (tip + "\n\n" if tip else "") + "ComfyUI node %s, input '%s'." % (s["node"], s["input"])
        try:
            k.setTooltip(tip)
        except Exception as e:
            _log("no tooltip for %s: %s" % (name, e))
        node.addKnob(k)
        try:
            k.setValue(s["default"] if t in ("float", "int", "bool") else str(s["default"]))
        except Exception as e:
            _log("could not set the default of %s: %s" % (name, e))
        k.setFlag(nuke.STARTLINE)
    node["param_map"].setValue(json.dumps(
        [{k: s.get(k) for k in ("knob", "node", "input", "type", "also")} for s in specs]))


def _raw(knob):
    """Text of a string knob as typed. value() runs Tcl on [brackets], which
    breaks JSON and prompts that contain brackets."""
    try:
        return knob.getText()
    except Exception:
        return knob.value()


def _param_values(node):
    vals = {}
    try:
        specs = json.loads(_raw(node["param_map"]) or "[]")
    except ValueError:
        _log("param_map is not readable: parameters not sent")
        specs = []
    for s in specs:
        if s["knob"] in node.knobs():
            k = node[s["knob"]]
            v = cast_param(s, _raw(k) if s["type"] in ("text", "string") else k.value())
            for nid, inp in [(s["node"], s["input"])] + [tuple(a) for a in (s.get("also") or [])]:
                vals[(nid, inp)] = v
    return vals


def _send_colour(node, display, write, ocio_knob, raw_knob, dv, work_cs, available, data=False):
    """Colour of an image sent as PNG: viewer transform (OCIO display/view or
    Nuke LUT), sRGB, or raw for data."""
    ocio_mode = dv is not None and dv[0] != LUT and not data
    node[ocio_knob].setValue(ocio_mode)
    node[raw_knob].setValue(ocio_mode or data)
    if data:
        return
    if ocio_mode:
        _k(node, display, "colorspace", work_cs)
        _k(node, display, "display", dv[0])
        _k(node, display, "view", dv[1])
    elif dv is not None:
        _k(node, write, "colorspace", dv[1])
    else:
        _k(node, write, "colorspace", pick_colorspace(_CS_SRGB, available))


def _default_out_dir(node):
    try:
        script = nuke.scriptName()  # raises on an unsaved script
    except Exception:
        script = ""
    base = os.path.dirname(script) if script else tempfile.gettempdir()
    return os.path.join(base, "comfy_bridge", node.name()).replace("\\", "/")


def run(node):
    try:
        _run(node)
    except ComfyError as e:
        node["cb_status"].setValue("error")
        nuke.message("ComfyBridge:\n" + str(e))


def _run(node):
    from . import __version__
    _log("run v%s on %s" % (__version__, _server(node)))
    client = ComfyClient(_server(node))
    client.check()

    load_workflow_into(node, force=False)
    path = node["workflow"].evaluate()
    if not path:
        raise ComfyError("Choose a workflow from the library or a .json file.")
    wf, meta = load_workflow(path)
    if client.cloud and cloud_unsupported(wf):
        adapt_for_cloud(wf)   # raises with the list of nodes Cloud does not have
    for m in resolve_model_files(wf, client.object_info):
        _log("model not found on the server: " + m)
    slots = slot_specs(wf, meta)
    if slots and not _has_slots(node):
        raise ComfyError("This workflow needs extra inputs and this node was created with "
                         "an older version. Create a new ComfyBridge.")
    mask_id = str(meta.get("mask_node") or "")
    taken = set([mask_id] + [sl["node"] for sl in slots])
    load_id = find_node(wf, (node["load_id"].value() or str(meta.get("plate_node") or "")).strip(),
                        ("LoadImage",), exclude=taken)
    save_id = find_node(wf, (node["save_id"].value() or "").strip(), SAVE_TYPES)
    params = _param_values(node)
    for (nid, inp), val in sorted(params.items()):  # what is really sent (Script Editor)
        _log("param %s.%s = %s" % (nid, inp, (str(val)[:80] + "...") if len(str(val)) > 80 else val))
    if mask_id and mask_id not in wf:
        raise ComfyError("The workflow metadata names mask node '%s', which does not exist." % mask_id)

    if meta.get("send_exposure") is False and "send_exposure" in node.knobs() \
            and float(node["send_exposure"].value()) != 0.0:
        # generative workflows relight / repaint: they do not keep the exposure sent
        node["send_exposure"].setValue(0.0)
        _log("send exposure set to 0: this workflow does not keep the exposure it gets")
    out_type = node["out_type"].value()
    is_data = (meta.get("output", "data") == "data") if out_type == OUT_AUTO else out_type == OUT_DATA

    dv = None
    if node["in_color"].value() == IN_VIEWER:
        dv = _viewer_process()
        if dv is None:
            raise ComfyError("Cannot read the Viewer transform.\n"
                             "Open a Viewer or set 'input color' to sRGB.")
    ocio_mode = dv is not None and dv[0] != LUT
    lut_mode = dv is not None and dv[0] == LUT

    first, last = int(node["first"].value()), int(node["last"].value())
    if last < first:
        raise ComfyError("Invalid frame range.")
    frames = list(range(first, last + 1))

    out_dir = node["out_dir"].evaluate() or _default_out_dir(node)
    if node["versioning"].value():
        out_dir = next_version_dir(out_dir)
    in_dir = os.path.join(out_dir, "_in")
    for d in (out_dir, in_dir):
        if not os.path.isdir(d):
            os.makedirs(d)
    name = node.name()
    in_pattern = os.path.join(in_dir, name + "_in.%04d.png").replace("\\", "/")
    mask_pattern = os.path.join(in_dir, name + "_mask.%04d.png").replace("\\", "/")

    # input colour (internal knobs are set by path)
    available = ocio_names()
    work_cs = _working_space()
    _send_colour(node, "CB_InDisplay", "CB_Write", "cb_in_ocio", "cb_in_raw",
                 dv, work_cs, available)
    node["cb_in_file"].setValue(in_pattern)
    slot_runs = []   # (write node, file pattern, workflow node, upload suffix)
    for sl in slots:
        n = sl["n"]
        if node["x%d_node" % n].value() != sl["node"]:
            raise ComfyError("The Inputs tab is out of date: press 'load / reload'.")
        data = sl["exr"] or node["x%d_send" % n].value() == SEND_DATA
        ext = ".exr" if sl["exr"] else ".png"
        pattern = os.path.join(in_dir, "%s_x%d.%%04d%s" % (name, n, ext)).replace("\\", "/")
        node["cb_x%d_file" % n].setValue(pattern)
        if "cb_x%d_exp" % n in node.knobs():
            node["cb_x%d_exp" % n].setValue(sl["exposure"] and not data)
        if sl["exr"]:
            node["cb_x%d_ocio" % n].setValue(False)
            node["cb_x%d_raw" % n].setValue(True)
        else:
            _send_colour(node, "CB_X%dDisplay" % n, "CB_X%dWritePNG" % n,
                         "cb_x%d_ocio" % n, "cb_x%d_raw" % n, dv, work_cs, available, data)
        slot_runs.append(("CB_X%dWrite%s" % (n, "EXR" if sl["exr"] else "PNG"),
                          pattern, sl["node"], "x%d%s" % (n, ext)))
    node["cb_mask_file"].setValue(mask_pattern)
    node["cb_mask_used"].setValue(bool(mask_id))
    if "cb_has_matte" in node.knobs():
        node["cb_has_matte"].setValue(bool(meta.get("matte")))
    elif meta.get("matte"):
        nuke.message("ComfyBridge: this node was created with an older version and cannot "
                     "output the matte as alpha. Create a new ComfyBridge to get it.\n"
                     "(The matte is still in the alpha of the Read next to the node.)")

    task = nuke.ProgressTask("ComfyBridge")
    out_pattern = None
    try:
        task.setMessage("Rendering input...")
        writes = ["CB_Write"] + (["CB_WriteMask"] if mask_id else []) + [r[0] for r in slot_runs]
        for w in writes:
            try:
                nuke.execute("%s.%s" % (node.fullName(), w), first, last, 1)
            except TypeError:  # older API without name lookup
                with node:
                    nuke.execute(nuke.toNode(w), first, last, 1)

        jobs = []
        for i, f in enumerate(frames):
            if task.isCancelled():
                client.interrupt([j[1] for j in jobs])
                raise ComfyError("Cancelled by the user.")
            task.setMessage("Uploading frame %d" % f)
            task.setProgress(int(20.0 * i / len(frames)))
            img_name = client.upload_image(in_pattern.replace("%04d", "%04d" % f),
                                           "%s_%04d.png" % (name, f))
            mask_name = None
            if mask_id:
                mask_name = client.upload_image(mask_pattern.replace("%04d", "%04d" % f),
                                                "%s_%04d_mask.png" % (name, f))
            extra = {}
            for _w, pattern, nid, suffix in slot_runs:
                extra[nid] = client.upload_image(pattern.replace("%04d", "%04d" % f),
                                                 "%s_%04d_%s" % (name, f, suffix))
            prompt = build_prompt(wf, load_id, save_id, img_name,
                                  "comfy_bridge/%s_%04d" % (name, f),
                                  is_data, dv is not None, params, mask_id, mask_name, extra)
            if client.cloud:
                prompt = adapt_for_cloud(prompt)
            jobs.append((f, client.queue(prompt)))

        for i, (f, pid) in enumerate(jobs):
            task.setMessage("%s working on frame %d  (%d/%d)" % (
                "Comfy Cloud" if client.cloud else "ComfyUI", f, i + 1, len(jobs)))
            task.setProgress(20 + int(80.0 * i / len(jobs)))
            hist = wait_for(client, pid, task.isCancelled, pending=[j[1] for j in jobs[i:]])
            imgs = output_images(hist, save_id)
            if not imgs:
                raise ComfyError("Frame %d returned no image." % f)
            if out_pattern is None:
                ext = os.path.splitext(imgs[0]["filename"])[1].lower() or ".png"
                out_pattern = os.path.join(out_dir, name + ".%04d" + ext).replace("\\", "/")
            client.download(imgs[0], out_pattern.replace("%04d", "%04d" % f))
        task.setProgress(100)
    finally:
        del task

    # output colour
    is_exr = out_pattern.lower().endswith(".exr")
    out_cs = None
    if is_data:
        node["cb_out_raw"].setValue(True)
        node["cb_out_ocio"].setValue(False)
    elif ocio_mode:
        node["cb_out_raw"].setValue(True)
        node["cb_out_ocio"].setValue(True)
        _k(node, "CB_OutDisplay", "colorspace", work_cs)
        _k(node, "CB_OutDisplay", "display", dv[0])
        _k(node, "CB_OutDisplay", "view", dv[1])
    else:
        node["cb_out_raw"].setValue(False)
        node["cb_out_ocio"].setValue(False)
        if lut_mode:
            out_cs = dv[1]
        elif is_exr:
            out_cs = pick_colorspace(_CS_LINEAR, available)
        else:
            out_cs = pick_colorspace(_CS_SRGB, available)
        _k(node, "CB_Read", "colorspace", out_cs)

    exp_used = 0.0
    if "send_exposure" in node.knobs() and not is_data:
        exp_used = float(node["send_exposure"].value())
    if "cb_exp_used" in node.knobs():
        node["cb_exp_used"].setValue(exp_used)
    node["cs_state"].setValue(json.dumps({"exposure": exp_used,
        "raw": bool(is_data or ocio_mode), "colorspace": out_cs,
        "ocio": [dv[0], dv[1], work_cs] if (ocio_mode and not is_data) else None}))
    node["cb_first"].setValue(first)
    node["cb_last"].setValue(last)
    node["cb_out_file"].setValue(out_pattern)
    # Write the path straight into the internal Read: changing its file knob
    # (unlike an expression) makes Nuke reload it and refresh the node output.
    for knob, value in (("first", first), ("last", last), ("origfirst", first),
                        ("origlast", last), ("file", out_pattern)):
        _k(node, "CB_Read", knob, value)
    node["cb_has_result"].setValue(True)
    version = os.path.basename(out_dir)
    node["cb_status"].setValue(version)
    node["res_info"].setValue("<small>%s  -  %s</small>" % (version, os.path.basename(out_pattern)))
    if "auto_read" in node.knobs() and node["auto_read"].value():
        bake_read(node)
    if mask_id or not ("auto_read" in node.knobs() and node["auto_read"].value()):
        # masked work: the node itself outputs the result comped over the plate
        node["show_result"].setValue(True)
    try:
        nuke.updateUI()
    except Exception:
        pass  # expected: not every Nuke setup has it


def bake_read(node):
    """Paste a standalone Read (plus inverse display if needed) with the current result."""
    path = node["cb_out_file"].value()
    if not path or not node["cb_has_result"].value():
        nuke.message("ComfyBridge: there is no result yet. Press 'Send to ComfyUI' first.")
        return
    try:
        st = json.loads(_raw(node["cs_state"]) or "{}")
    except ValueError:
        st = {}
    fmt = None
    if node["match_format"].value():
        try:
            fmt = (int(node.width()), int(node.height()))
        except Exception:
            fmt = None
    try:
        m = re.search(r"(\d+)", node["cb_status"].value() or "")
        step = (int(m.group(1)) - 1) % 8 if m else 0   # v001, v002... side by side
        xpos, ypos = int(node.xpos()) + 150 + 120 * step, int(node.ypos())
    except Exception:
        xpos = ypos = None
    status = node["cb_status"].value() or ""
    base = re.sub(r"\W", "_", "%s_%s" % (node.name(), status or "result"))
    first, last = int(node["cb_first"].value()), int(node["cb_last"].value())

    def build():  # runs inside the target graph: unique names there
        return bake_template(path, first, last, st.get("raw", True), st.get("colorspace"),
                             st.get("ocio"), _unique_base(base), xpos, ypos, status, fmt,
                             st.get("exposure", 0.0))

    _paste(build, beside=node)


def install_menu():
    # comfybridge.png sits next to comfy_bridge.py (in the plugin path)
    m = nuke.menu("Nodes").addMenu("ComfyUI", icon="comfybridge.png")
    m.addCommand("ComfyBridge", "import comfy_bridge; comfy_bridge.create_node()",
                 icon="comfybridge.png")
