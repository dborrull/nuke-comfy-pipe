# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Nuke side. Every entry point receives the ComfyBridge node from its button
(nuke.thisNode()) and never asks Python for any other Node object: internal
nodes are reached through nuke.knob("Group.Node.knob") paths and expressions."""

import json
import os
import re
import sys
import tempfile

try:
    import nuke
except ImportError:  # lets the module be imported (and tested) outside Nuke
    nuke = None

from .client import ComfyClient, ComfyError, is_cloud
from .help import QUICK_HELP, REPO_URL
from . import models as _models
from . import sequence as _seq
from .templates import bake_template, node_template
from .workflows import (CB_SAVE_EXR, IN_VIEWER, LIB_NONE, LUT, library_kind, MAX_SLOTS, OUT_AUTO, OUT_DATA,
                        SAVE_TYPES, SEND_DATA, _CS_LINEAR, _CS_SRGB, adapt_for_cloud,
                        apply_server_options, build_prompt, cloud_unsupported,
                        cast_param, find_node, load_workflow, match_layer, next_version_dir,
                        named_colorspace, ocio_names, output_images, param_specs, parse_viewer_process,
                        pick_colorspace, resolve_model_files, scan_library, slot_specs,
                        wait_for, WORKFLOW_DIR)


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
    if name == "lib_type":
        _refresh_library(node)
        return
    if name not in ("backend", "showPanel"):
        return
    cloud = _server(node) == "cloud" and "backend" in node.knobs()
    for k in ("server", "launch"):
        try:
            if k in node.knobs():
                node[k].setEnabled(not cloud)
        except Exception as e:
            _log("could not grey out %s: %s" % (k, e))
    if name == "backend" and node["workflow"].value():
        node["wf_loaded"].setValue("")
        try:
            load_workflow_into(node, force=False)
        except Exception as e:
            _log("could not reload the workflow: %s" % e)


def _lib_type(node):
    try:
        return node["lib_type"].value() if "lib_type" in node.knobs() else "all"
    except Exception:
        return "all"


def _refresh_library(node, keep=None):
    """Fill the library pulldown with the workflows of the chosen type. The
    current choice stays in the list even if it is of another type."""
    shown = list(scan_library(show=_lib_type(node)))
    keep = keep if keep is not None else node["library"].value()
    if keep and keep != LIB_NONE and keep not in shown:
        shown.append(keep)
    try:
        node["library"].setValues([LIB_NONE] + shown)
        node["library"].setValue(keep if keep in shown else LIB_NONE)
    except Exception as e:
        _log("could not refresh the library list: %s" % e)


def _auto_mode(node, meta):
    """Video workflows run as a sequence, image ones frame by frame. Workflows of
    both kinds (depth, upscale) follow the type filter (video -> sequence,
    image -> frames) and keep the user's choice with 'all'."""
    if "mode" not in node.knobs():
        return
    kind = library_kind(meta)
    if kind == "both":
        kind = _lib_type(node)
    if kind == "video":
        node["mode"].setValue(_seq.MODE_SEQUENCE)
    elif kind == "image":
        node["mode"].setValue(_seq.MODE_FRAMES)


def create_node():
    first, last = _start_range()
    _paste(node_template(list(scan_library()), os.environ.get("COMFY_BRIDGE_URL", "127.0.0.1:8188"),
                         first, last))


def _start_range():
    """Range for a new node: the selected node (it becomes the plate input), else
    the project. Fixed numbers, so a longer project does not send more frames."""
    try:
        sel = nuke.selectedNode()
        return int(sel.firstFrame()), int(sel.lastFrame())
    except Exception:
        pass
    root = nuke.root()
    return int(root["first_frame"].value()), int(root["last_frame"].value())


def use_input_range(node):
    """Button 'use input range': first / last = the range of the plate input."""
    inp = node.input(0)
    if inp is None:
        nuke.message("ComfyBridge: nothing is connected to the plate input.")
        return
    node["first"].setValue(int(inp.firstFrame()))
    node["last"].setValue(int(inp.lastFrame()))


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


# ---- launch a local ComfyUI ------------------------------------------------
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]")


def _is_local(server):
    host = re.sub(r"^https?://", "", (server or "").strip()).split("/")[0]
    m = re.match(r"^\[([^\]]*)\](?::\d+)?$", host)   # IPv6 with a port: [::1]:8188
    if m:
        host = m.group(1)
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.lower() in _LOCAL_HOSTS


def _comfy_answers(server, timeout=2):
    try:
        ComfyClient(server, timeout=timeout).system_stats()
        return True
    except Exception:
        return False


def _launcher():
    """Path of the script that starts ComfyUI (run_nvidia_gpu.bat in the portable
    build). Env COMFY_BRIDGE_LAUNCHER, else the settings, else asked once."""
    p = os.environ.get("COMFY_BRIDGE_LAUNCHER") or _settings().get("comfyui_launcher") or ""
    if p and os.path.isfile(p):
        return p
    default = p or ("C:/ComfyUI/run_nvidia_gpu.bat" if os.name == "nt"
                    else os.path.expanduser("~/ComfyUI/main.py"))
    p = nuke.getFilename("Script that starts your ComfyUI (portable: run_nvidia_gpu.bat; "
                         "Linux / macOS: a .sh script or ComfyUI's main.py)",
                         "*.bat *.cmd *.sh *.py", default)
    if not p:
        return None
    p = p.strip().strip('"').replace("\\", "/")
    if not os.path.isfile(p):
        nuke.message("ComfyBridge: file not found:\n" + p)
        return None
    _save_setting("comfyui_launcher", p)
    return p


def _clean_env():
    """Environment for a process started from Nuke, without Nuke's Python, Qt,
    OCIO and library paths (inherited, they break torch: learned in GeoProjection)."""
    nuke_dir = os.path.dirname(os.path.abspath(sys.executable)) if sys.executable else ""
    env = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku.startswith(("NUKE", "FOUNDRY", "PYTHON", "QT_", "OCIO")):
            continue
        if ku in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH") and nuke_dir:
            v = os.pathsep.join(d for d in v.split(os.pathsep)
                                if d and not os.path.abspath(d).lower().startswith(nuke_dir.lower()))
        env[k] = v
    return env


def _start_process(path):
    """Start the launcher outside Nuke, with a clean environment and the script's
    folder as working directory (as with a double click). Windows: in its own
    console window (explorer.exe silently ignores a .bat on Windows 11).
    Elsewhere: a new session. Returns the Popen, to notice when it ends."""
    import subprocess
    folder = os.path.dirname(path)
    low = path.lower()
    if low.endswith(".py"):
        cmd = ["python" if os.name == "nt" else "python3", path]
    elif low.endswith((".bat", ".cmd")):
        cmd = ["cmd.exe", "/c", os.path.normpath(path)]
    elif low.endswith(".sh"):
        cmd = ["bash", path]
    else:
        cmd = [path]
    if os.name == "nt":
        return subprocess.Popen(cmd, cwd=folder, env=_clean_env(),
                                creationflags=subprocess.CREATE_NEW_CONSOLE)
    return subprocess.Popen(cmd, cwd=folder, env=_clean_env(), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def launch_comfyui(node, wait=180):
    """Button 'start ComfyUI': start the local ComfyUI if it does not answer yet,
    then wait until it does (cancellable)."""
    import time
    server = _server(node)
    if server == "cloud":
        nuke.message("ComfyBridge: 'run on' is Comfy Cloud, nothing to start.")
        return
    if _comfy_answers(server):
        nuke.message("ComfyBridge: ComfyUI is already running at %s." % server)
        return
    if not _is_local(server):
        nuke.message("ComfyBridge: %s is another machine. Only a ComfyUI on this computer "
                     "(127.0.0.1 / localhost) can be started from here." % server)
        return
    path = _launcher()
    if not path:
        return
    try:
        proc = _start_process(path)
    except Exception as e:
        nuke.message("ComfyBridge: could not start ComfyUI:\n%s\n%s" % (path, e))
        return
    _log("starting ComfyUI: %s" % path)
    task = nuke.ProgressTask("Starting ComfyUI")
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            if task.isCancelled():
                _log("stopped waiting for ComfyUI (it keeps starting in its own window)")
                return
            el = time.time() - t0
            task.setMessage("waiting for %s ... %d s" % (server, el))
            task.setProgress(min(99, int(100 * el / wait)))
            if _comfy_answers(server):
                del task
                task = None
                _log("ComfyUI answered after %d s" % el)
                nuke.message("ComfyBridge: ComfyUI is running (%d s)." % el)
                return
            if proc is not None and proc.poll() is not None:
                del task
                task = None
                nuke.message("ComfyBridge: the ComfyUI launcher ended (code %s) before ComfyUI "
                             "answered:\n%s" % (proc.returncode, path))
                return
            time.sleep(1.0)
    finally:
        if task is not None:
            del task
    nuke.message("ComfyBridge: ComfyUI did not answer at %s after %d s.\n"
                 "Check its console window (first start, model loading or an error)." % (server, wait))


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
    if meta.get("work_size") and "work_size" in node.knobs():
        # heavy video models: send at the size they are made for (still editable)
        node["work_size"].setValue(int(meta["work_size"]))
    _auto_mode(node, meta)
    _update_seq_info(node, meta)
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
    # refresh the library list (new workflows, type filter)
    _refresh_library(node, keep=choice if choice in lib else LIB_NONE)


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


def _seq_mode(node):
    """'frames' or 'sequence' (nodes made before v0.9 have no mode knob: frames)."""
    if "mode" not in node.knobs():
        return _seq.MODE_FRAMES
    return _seq.MODE_SEQUENCE if node["mode"].value() == _seq.MODE_SEQUENCE else _seq.MODE_FRAMES


def _update_seq_info(node, meta):
    if "seq_info" not in node.knobs():
        return
    kind = _seq.sequence_kind(meta)
    colour = {"batch": "lightgreen", "temporal": "lightgreen", "no": "gray"}[kind]
    extra = ""
    if _seq.frame_ratio(meta) != 1:
        extra = ", %g frames out per frame in" % _seq.frame_ratio(meta)
    node["seq_info"].setValue("<small><font color=%s>%s%s</font></small>"
                              % (colour, _seq.kind_label(kind), extra))
    try:
        node["chunk_size"].setEnabled(kind == _seq.SEQ_BATCH)
    except Exception as e:
        _log("could not grey out chunk size: %s" % e)


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


def _workflow_ref(path):
    """How a workflow is named in the run info: its file name when it is in the library
    (no folders of this computer in the file), else the full path, which 'reload
    parameters' needs to find a workflow kept elsewhere."""
    path = path.replace("\\", "/")
    try:
        in_lib = (os.path.normcase(os.path.dirname(os.path.abspath(path)))
                  == os.path.normcase(os.path.abspath(WORKFLOW_DIR)))
    except Exception:
        in_lib = False
    return os.path.basename(path) if in_lib else path


def _run_info(node, path, meta, params, frames):
    """What ComfyBridge stores in the result's metadata (exr/comfybridge in Nuke) and
    sends to the server: enough to trace a render back to its script, node, workflow
    and settings, without the folders of this computer or the server address."""
    import time
    from . import __version__
    try:
        script = nuke.root().name()
    except Exception:
        script = ""
    if script == "Root":   # what Nuke returns for a script that was never saved
        script = ""
    server = _server(node)
    info = {"version": __version__, "node": node.fullName(),
            "script": os.path.basename(script.replace("\\", "/")),
            "workflow": _workflow_ref(path), "workflow_name": meta.get("name") or "",
            "server": "cloud" if is_cloud(server) else "local",
            "frames": [frames[0], frames[-1]] if frames else [],
            "params": dict(("%s.%s" % k, v) for k, v in sorted(params.items())),
            "knobs": _node_settings(node),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return json.loads(json.dumps(info, default=str))   # only JSON values go to the server


# runs longer than this ask first (a long range may be a forgotten project range)
BIG_RUN = 50


# ComfyBridge knobs that shape a render (not paths, server or display options):
# stored with each result and set back by 'reload parameters'
RESTORE_KNOBS = (("first", "last", "mode", "chunk_size", "in_color", "send_exposure", "out_type",
                  "work_size", "match_format", "mask_source", "mask_channels", "mask_grow",
                  "mask_soften", "comp_over_plate", "comp_feather")
                 + tuple("x%d_%s" % (n, k) for n in range(1, MAX_SLOTS + 1)
                         for k in ("from", "layer", "send")))


def _node_settings(node):
    out = {}
    for name in RESTORE_KNOBS:
        if name in node.knobs():
            try:
                out[name] = node[name].value()
            except Exception as e:
                _log("could not read %s: %s" % (name, e))
    return out


def _save_prompt_json(node, out_dir, prompt):
    """Optional ('save prompt .json'): the prompt as sent, next to the result.
    Pure API format, so it opens when dropped on ComfyUI."""
    if "save_json" not in node.knobs() or not node["save_json"].value():
        return
    path = os.path.join(out_dir, node.name() + ".json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prompt, f, indent=1)
    except OSError as e:
        _log("could not save %s: %s" % (path, e))


# ---- reload parameters from a result ---------------------------------------
def exr_string_attrs(path):
    """String attributes of an EXR header (first part), without reading pixels."""
    import struct
    out = {}
    with open(path, "rb") as f:
        if f.read(4) != b"\x76\x2f\x31\x01":
            raise ComfyError("Not an EXR file:\n" + path)
        f.read(4)   # version and flags

        def cstr():
            b = bytearray()
            while True:
                c = f.read(1)
                if not c:
                    raise ComfyError("Truncated EXR header:\n" + path)
                if c == b"\0":
                    return b.decode("utf-8", "replace")
                b += c

        while True:
            name = cstr()
            if not name:
                return out
            type_name = cstr()
            size = struct.unpack("<i", f.read(4))[0]
            data = f.read(size)
            if type_name == "string":
                out[name] = data.decode("utf-8", "replace")


def _result_info(path=None, read=None):
    """The 'comfybridge' run info of a result: from the file header, else from
    the Read's metadata (e.g. a Nuke Write that kept exr/*). None if absent."""
    text = None
    if path and path.lower().endswith(".exr") and os.path.isfile(path):
        text = exr_string_attrs(path).get("comfybridge")
    if not text and read is not None:
        try:
            text = read.metadata("exr/comfybridge")
        except Exception:
            text = None
    if not text:
        return None
    try:
        info = json.loads(text)
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def _pick_result(node):
    """(path, Read node or None): a selected Read, else a file browser that starts
    at this node's last result."""
    for r in nuke.selectedNodes("Read"):
        if r is not node:
            try:
                return _read_first_file(r), r
            except Exception:
                continue
    start = node["cb_out_file"].value() if "cb_out_file" in node.knobs() else ""
    start = os.path.dirname(start) + "/" if start else ""
    path = nuke.getFilename("EXR made by ComfyBridge", "*.exr", start)
    if not path:
        return None, None
    return _first_frame_file(path), None


def _frame_file(pattern, frame):
    """'name.####.exr' / 'name.%04d.exr' / 'name.%d.exr' at one frame."""
    pattern = re.sub(r"%0(\d+)d", lambda g: "%0*d" % (int(g.group(1)), frame), pattern)
    pattern = re.sub(r"%d", lambda g: "%d" % frame, pattern)
    return re.sub(r"#+", lambda g: "%0*d" % (len(g.group(0)), frame), pattern)


def _first_frame_file(path):
    """A browser pick can be a sequence ('name.####.exr 1001-1010'): its first file."""
    if os.path.isfile(path):
        return path
    m = re.match(r"^(.*?)\s+(-?\d+)(?:-(-?\d+))?$", path)
    if not m:
        return path
    return _frame_file(m.group(1), int(m.group(2)))


def _read_first_file(read):
    """The file of a Read at its first frame (the current frame can be outside its range,
    and then evaluate() names a file that does not exist)."""
    pattern = read["file"].value()
    try:
        first = int(read["first"].value())
    except (TypeError, ValueError):
        first = None
    if (first is not None and isinstance(pattern, str) and "[" not in pattern
            and re.search(r"#|%0?\d*d", pattern)):
        return _frame_file(pattern, first)
    return read["file"].evaluate()   # a single file, or a path made with Tcl


def _set_knob(k, value):
    if isinstance(value, bool):
        k.setValue(value)
    elif isinstance(value, (int, float)):
        k.setValue(value)
    else:
        k.setValue(str(value))


def _apply_workflow(node, info):
    """Point the node at the workflow of the result: its library entry when it is
    there, else the file itself. Returns the path, or raises."""
    path = (info.get("workflow") or "").replace("\\", "/")
    lib = scan_library()
    name = next((n for n, p in lib.items() if os.path.normcase(p) == os.path.normcase(path)), None)
    if name is None and path and "/" not in path:   # a library workflow is stored by file name
        name = next((n for n, p in lib.items()
                     if os.path.normcase(os.path.basename(p)) == os.path.normcase(path)), None)
    if name is None and info.get("workflow_name") in lib:
        name = info["workflow_name"]   # the file moved but the library still has it
    if name is not None:
        _refresh_library(node, keep=name)
        node["workflow"].setValue(lib[name])
        return lib[name]
    if path and os.path.isfile(path):
        _refresh_library(node, keep=LIB_NONE)
        node["workflow"].setValue(path)
        return path
    raise ComfyError("The workflow of this result is not here any more:\n%s" % (path or "?"))


def reload_parameters(node):
    """Button 'reload parameters': set this node up as it was for a result
    (workflow, ComfyBridge settings and workflow parameters) from the EXR metadata."""
    try:
        _reload_parameters(node)
    except ComfyError as e:
        nuke.message("ComfyBridge:\n" + str(e))


def _reload_parameters(node):
    path, read = _pick_result(node)
    if not path and read is None:
        return
    info = _result_info(path, read)
    if not info:
        raise ComfyError("No ComfyBridge metadata in:\n%s\n\nIt was made before this version, "
                         "or a Write dropped it (Write 'metadata' must keep exr/*)."
                         % path)
    if not nuke.ask("Reload the parameters of this result into %s?\n\n%s\nworkflow: %s\n"
                    "from node %s, %s\n\nIts current values are replaced."
                    % (node.name(), path, info.get("workflow_name") or info.get("workflow"),
                       info.get("node", "?"), info.get("time", "?"))):
        return
    wf_path = _apply_workflow(node, info)
    node["wf_loaded"].setValue("")
    load_workflow_into(node, force=True)
    if node["wf_loaded"].value() != wf_path:
        raise ComfyError("Could not load the workflow:\n" + wf_path)

    skipped = []
    for name, value in sorted((info.get("knobs") or {}).items()):
        if name not in RESTORE_KNOBS or name not in node.knobs():
            skipped.append(name)
            continue
        try:
            _set_knob(node[name], value)
        except Exception as e:
            skipped.append("%s (%s)" % (name, e))
    params = info.get("params") or {}
    try:
        specs = json.loads(_raw(node["param_map"]) or "[]")
    except ValueError:
        specs = []
    used = set()
    for s in specs:
        key = "%s.%s" % (s["node"], s["input"])
        if key in params and s["knob"] in node.knobs():
            try:
                _set_knob(node[s["knob"]], params[key])
                used.add(key)
                used.update("%s.%s" % tuple(a) for a in s.get("also") or [])
            except Exception as e:
                skipped.append("%s (%s)" % (s["knob"], e))
    lost = sorted(set(params) - used)
    _log("reloaded %d parameters from %s" % (len(used), path))
    msg = "ComfyBridge: %s is set up as for\n%s\n(%d parameters)." % (node.name(), path, len(used))
    if lost or skipped:
        msg += ("\n\nNot restored (the workflow or the node changed since):\n  "
                + "\n  ".join(lost + skipped))
    nuke.message(msg)


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
    if len(frames) > BIG_RUN and not nuke.ask(
            "ComfyBridge: send %d frames (%d-%d) to %s?" % (
                len(frames), first, last, "Comfy Cloud" if client.cloud else "ComfyUI")):
        return
    seq_mode = _seq_mode(node) == _seq.MODE_SEQUENCE
    kind = _seq.sequence_kind(meta)
    if seq_mode:
        _seq.check_mode(kind, len(frames), meta)   # before any folder or render

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

    out_first, out_last = first, last
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

        if seq_mode:
            out_pattern, out_first, out_last = _run_sequence(
                node, client, task, wf, meta, kind, frames, load_id, save_id, mask_id,
                slot_runs, params, is_data, dv is not None, in_pattern, mask_pattern, out_dir)
            frames = []   # skips the per-frame loop below

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
            info = _run_info(node, path, meta, params, [f])
            if not jobs:
                _save_prompt_json(node, out_dir, prompt)
            jobs.append((f, client.queue(prompt, info)))

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
    if out_pattern is None:
        raise ComfyError("Nothing came back from ComfyUI.")

    # output colour
    is_exr = out_pattern.lower().endswith(".exr")
    out_cs = None
    named = meta.get("out_colorspace") if not is_data else None
    if named:
        # already scene-linear (e.g. ACEScg from LTX SDR->HDR): tag the Read,
        # never undo the viewer transform on it
        out_cs = named_colorspace(named, available)
        node["cb_out_ocio"].setValue(False)
        node["cb_out_raw"].setValue(out_cs is None)
        if out_cs:
            _k(node, "CB_Read", "colorspace", out_cs)
        else:
            nuke.message("ComfyBridge: the result is scene-linear %s, but the OCIO config of this "
                         "script has no such colour space. It is read raw: convert it yourself "
                         "(e.g. OCIOColorSpace %s -> your working space)." % (named, named))
    elif is_data:
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
        "raw": bool(is_data or (ocio_mode and not named) or (named and not out_cs)), "colorspace": out_cs,
        "ocio": [dv[0], dv[1], work_cs] if (ocio_mode and not is_data and not named) else None}))
    node["cb_first"].setValue(out_first)
    node["cb_last"].setValue(out_last)
    node["cb_out_file"].setValue(out_pattern)
    # Write the path straight into the internal Read: changing its file knob
    # (unlike an expression) makes Nuke reload it and refresh the node output.
    for knob, value in (("first", out_first), ("last", out_last), ("origfirst", out_first),
                        ("origlast", out_last), ("file", out_pattern)):
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


def _run_sequence(node, client, task, wf, meta, kind, frames, load_id, save_id, mask_id,
                  slot_runs, params, is_data, display_mode, in_pattern, mask_pattern, out_dir):
    """Sequence mode: upload the rendered frames, one job per chunk (all queued at once),
    results mapped back to the original frame numbers. A failed chunk can be retried
    alone. Returns (out_pattern, out_first, out_last)."""
    name = node.name()
    ratio = _seq.frame_ratio(meta)
    cons = _seq.constraints(meta)
    size = int(node["chunk_size"].value()) if "chunk_size" in node.knobs() else 0
    chunks = _seq.make_chunks(frames, size, kind)
    if ratio != 1 and len(chunks) > 1:
        chunks = [list(frames)]   # retimed output must be continuous: one job
    _log("sequence: %d frames, %d job(s), kind %s, frame ratio %g"
         % (len(frames), len(chunks), kind, ratio))

    # 1. upload every frame once (plate, mask); extra inputs (aux) go once, from the first frame
    plate, mask = {}, {}
    for i, f in enumerate(frames):
        if task.isCancelled():
            raise ComfyError("Cancelled by the user.")
        task.setMessage("Uploading frame %d  (%d/%d)" % (f, i + 1, len(frames)))
        task.setProgress(int(20.0 * i / len(frames)))
        plate[f] = client.upload_image(in_pattern.replace("%04d", "%04d" % f),
                                       "%s_seq_%04d.png" % (name, f))
        if mask_id:
            mask[f] = client.upload_image(mask_pattern.replace("%04d", "%04d" % f),
                                          "%s_seq_%04d_mask.png" % (name, f))
    extra = {}
    for _w, pattern, nid, suffix in slot_runs:
        extra[nid] = client.upload_image(pattern.replace("%04d", "%04d" % frames[0]),
                                         "%s_seq_%04d_%s" % (name, frames[0], suffix))
    w, h = _seq.png_size(in_pattern.replace("%04d", "%04d" % frames[0]))

    # 2. one prompt per chunk
    def make_prompt(ci, chunk):
        names, _added = _seq.pad_names([plate[f] for f in chunk], cons.get("frames_rule"))
        mnames = None
        if mask_id:
            mnames, _ = _seq.pad_names([mask[f] for f in chunk], cons.get("frames_rule"))
        p = build_prompt(wf, load_id, save_id, names[0],
                         "comfy_bridge/%s_seq_c%02d" % (name, ci), is_data, display_mode,
                         params, mask_id, mnames[0] if mnames else None, extra)
        p = _seq.expand_to_sequence(p, load_id, names, mask_id, mnames)
        batch_id = "cbseq_%s_b00" % load_id if len(names) <= _seq.BATCH_MAX else "cbseq_%s_ball" % load_id
        if batch_id in p:
            _seq.apply_size_multiple(p, batch_id, save_id, w, h, cons.get("size_multiple"),
                                     float(meta.get("output_scale", 1) or 1))
        return adapt_for_cloud(p) if client.cloud else p

    prompts = [make_prompt(ci, c) for ci, c in enumerate(chunks)]
    wf_path = node["workflow"].evaluate() or ""
    infos = [_run_info(node, wf_path, meta, params, c) for c in chunks]
    _save_prompt_json(node, out_dir, prompts[0])
    jobs = [client.queue(p, i) for p, i in zip(prompts, infos)]

    # 3. wait in order, download, map to the original numbering
    out_pattern = None
    first_out = frames[0]
    written = []
    for ci, chunk in enumerate(chunks):
        label = "frames %d-%d" % (chunk[0], chunk[-1])
        while True:
            task.setMessage("%s working on %s  (job %d/%d)" % (
                "Comfy Cloud" if client.cloud else "ComfyUI", label, ci + 1, len(chunks)))
            task.setProgress(20 + int(70.0 * ci / len(chunks)))
            try:
                hist = wait_for(client, jobs[ci], task.isCancelled, pending=jobs[ci:])
                imgs = output_images(hist, save_id)
                nums = _seq.output_frames(chunk, len(imgs), ratio, first_out)
                break
            except ComfyError as e:
                if "Cancelled" in str(e):
                    raise
                if not nuke.ask("ComfyBridge: job %d/%d (%s) failed:\n\n%s\n\nRetry this chunk?"
                                % (ci + 1, len(chunks), label, str(e)[:1500])):
                    raise ComfyError("Stopped at %s. Frames written so far: %s"
                                     % (label, ("%d-%d" % (written[0], written[-1])) if written else "none"))
                jobs[ci] = client.queue(prompts[ci], infos[ci])
        for j, (num, img) in enumerate(zip(nums, imgs)):
            if out_pattern is None:
                ext = os.path.splitext(img["filename"])[1].lower() or ".png"
                out_pattern = os.path.join(out_dir, name + ".%04d" + ext).replace("\\", "/")
            task.setMessage("Downloading frame %d  (%s)" % (num, label))
            task.setProgress(20 + int(70.0 * ci / len(chunks) + 10.0 * j / max(1, len(nums))))
            client.download(img, out_pattern.replace("%04d", "%04d" % num))
            written.append(num)
        first_out = (nums[-1] + 1) if nums else first_out
    if not written:
        raise ComfyError("The sequence returned no images.")
    if ratio != 1:
        nuke.message("ComfyBridge: this workflow changes the frame count (x%g).\n"
                     "%d frames in -> %d out, written as %d-%d: the Read is retimed "
                     "against the plate." % (ratio, len(frames), len(written), written[0], written[-1]))
    return out_pattern, min(written), max(written)


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
