# -*- coding: utf-8 -*-
# ComfyBridge (nuke-comfybridge) - by David Borrull
# https://github.com/dborrull/nuke-comfybridge
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Server routes to list, read and save ComfyBridge library workflows from ComfyUI
(used by "Export to ComfyBridge"). Only .json files inside the library folder.

    GET  /comfybridge/schema            meta_schema.json
    POST /comfybridge/check             body {"workflow": {...}} -> {"warnings", "cloud"}
    GET  /comfybridge/library           {"folder", "exists", "workflows": [...]}
    GET  /comfybridge/library/{file}    the workflow (API JSON + _comfy_bridge)
    POST /comfybridge/library/{file}    body {"workflow": {...}, "overwrite": false}
                                        -> {"file", "path", "warnings", "cloud", "backup"}
    POST /comfybridge/enabled/{file}    body {"enabled": true|false}: show / hide it in Nuke
                                        (listed in _disabled.json; the workflow is not touched)

Library folder: COMFY_BRIDGE_WORKFLOWS, else ~/.nuke/comfy_bridge_workflows (where
ComfyBridge installs it). The functions work without ComfyUI (tests)."""

import json
import os
import re
import shutil

try:
    from .meta_check import check_meta, load_schema
except ImportError:   # loaded as a plain module (tests)
    from meta_check import check_meta, load_schema

META_KEY = "_comfy_bridge"
SAVE_EXR = "ComfyBridgeSaveEXR"
DISABLED_FILE = "_disabled.json"   # same name as in nuke/comfybridge/workflows.py
MAX_BYTES = 8 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,120}\.json$")


def library_dir():
    return os.environ.get("COMFY_BRIDGE_WORKFLOWS") or os.path.join(
        os.path.expanduser("~"), ".nuke", "comfy_bridge_workflows")


def safe_path(folder, name):
    """Full path of a library file, or ValueError (no folders, only .json)."""
    if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
        raise ValueError("Invalid file name '%s': letters, digits, space, _ - . ( ) and .json at the end."
                         % name)
    root = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root:
        raise ValueError("Invalid file name '%s'." % name)
    return path


def split(data):
    """(nodes, meta) of an API workflow, like Nuke reads it. ValueError otherwise."""
    if not isinstance(data, dict):
        raise ValueError("The workflow must be a JSON object.")
    if "nodes" in data and "links" in data:
        raise ValueError("This is the UI format; ComfyBridge needs the API format.")
    nodes = {k: v for k, v in data.items()
             if not str(k).startswith("_") and isinstance(v, dict) and "class_type" in v}
    if not nodes:
        raise ValueError("The workflow has no nodes.")
    meta = data.get(META_KEY) or {}
    return nodes, meta


def cloud_unsupported(nodes):
    """ComfyBridge nodes that Comfy Cloud does not have (Save EXR is swapped by Nuke)."""
    return sorted(set(n["class_type"] for n in nodes.values()
                      if str(n.get("class_type", "")).startswith("ComfyBridge")
                      and n["class_type"] != SAVE_EXR))


def problems(nodes, meta):
    """Metadata problems plus what makes the workflow unusable from Nuke."""
    out = check_meta(nodes, meta)
    saves = load_schema()["keys"]["save_node"]["classes"]
    if not any(n.get("class_type") in saves for n in nodes.values()):
        out.append("no output node: add a %s" % " / ".join(saves))
    if not meta.get("plate_node") and not any(n.get("class_type") == "LoadImage" for n in nodes.values()):
        out.append("no LoadImage for the plate")
    if "output" not in meta:
        out.append("'output' is not set: Nuke treats the result as data (depth, matte)")
    return out


def disabled(folder=None):
    """File names switched off in Nuke's library pulldown (same file Nuke reads)."""
    try:
        with open(os.path.join(folder or library_dir(), DISABLED_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(str(n) for n in data.get("disabled", []))
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def set_enabled(name, enabled, folder=None):
    """Switch a library workflow on or off in Nuke. The file itself is not touched."""
    folder = folder or library_dir()
    path = safe_path(folder, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    off = disabled(folder)
    if enabled:
        off.discard(name)
    else:
        off.add(name)
    text = json.dumps({"_about": "Library workflows hidden in Nuke (set from ComfyUI > Export to ComfyBridge).",
                       "disabled": sorted(off, key=str.lower)}, indent=1) + "\n"
    tmp = os.path.join(folder, DISABLED_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, os.path.join(folder, DISABLED_FILE))
    return {"file": name, "enabled": bool(enabled)}


def list_workflows(folder=None):
    folder = folder or library_dir()
    out = []
    if not os.path.isdir(folder):
        return out
    off = disabled(folder)
    for fn in sorted(os.listdir(folder), key=str.lower):
        if not fn.lower().endswith(".json") or fn.startswith("_"):
            continue
        try:
            with open(os.path.join(folder, fn), "r", encoding="utf-8") as f:
                _nodes, meta = split(json.load(f))
        except (OSError, ValueError):
            continue
        out.append({"file": fn, "name": meta.get("name") or os.path.splitext(fn)[0],
                    "output": meta.get("output"), "sequence": meta.get("sequence", "no"),
                    "has_meta": bool(meta), "enabled": fn not in off})
    return out


def read_workflow(name, folder=None):
    path = safe_path(folder or library_dir(), name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_workflow(name, data, overwrite=False, folder=None):
    """Write the workflow (API JSON with _comfy_bridge). The previous version of a
    file that is overwritten is kept as <file>.bak. FileExistsError if it exists
    and overwrite is False; ValueError for a bad name or workflow."""
    folder = folder or library_dir()
    path = safe_path(folder, name)
    nodes, meta = split(data)
    if not isinstance(meta, dict) or not meta:
        raise ValueError("The workflow has no %s block." % META_KEY)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("The workflow is too big (more than %d MB)." % (MAX_BYTES // (1024 * 1024)))
    backup = None
    if os.path.exists(path):
        if not overwrite:
            raise FileExistsError(name)
        backup = path + ".bak"
        shutil.copyfile(path, backup)
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return {"file": name, "path": path.replace("\\", "/"), "warnings": problems(nodes, meta),
            "cloud": cloud_unsupported(nodes), "backup": backup and os.path.basename(backup)}


def register():
    """Add the routes to the ComfyUI server."""
    from aiohttp import web
    from server import PromptServer
    routes = PromptServer.instance.routes

    def _err(status, msg):
        return web.json_response({"error": msg}, status=status)

    @routes.get("/comfybridge/schema")
    async def _schema(request):
        return web.json_response(load_schema())

    @routes.get("/comfybridge/library")
    async def _list(request):
        folder = library_dir()
        return web.json_response({"folder": folder.replace("\\", "/"), "exists": os.path.isdir(folder),
                                  "workflows": list_workflows(folder)})

    @routes.post("/comfybridge/check")
    async def _check_route(request):
        try:
            body = await request.json()
            nodes, meta = split((body or {}).get("workflow"))
        except ValueError as e:
            return _err(400, str(e))
        except Exception:
            return _err(400, "The request body is not JSON.")
        return web.json_response({"warnings": problems(nodes, meta), "cloud": cloud_unsupported(nodes)})

    @routes.get("/comfybridge/library/{name}")
    async def _read(request):
        try:
            return web.json_response(read_workflow(request.match_info["name"]))
        except ValueError as e:
            return _err(400, str(e))
        except FileNotFoundError:
            return _err(404, "Not in the library: %s" % request.match_info["name"])
        except (OSError, json.JSONDecodeError) as e:
            return _err(500, "Could not read it: %s" % e)

    @routes.post("/comfybridge/enabled/{name}")
    async def _enabled(request):
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            return _err(400, "The request body is not JSON.")
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return _err(400, "The request body must be {\"enabled\": true | false}.")
        try:
            return web.json_response(set_enabled(name, body["enabled"]))
        except ValueError as e:
            return _err(400, str(e))
        except FileNotFoundError:
            return _err(404, "Not in the library: %s" % name)
        except OSError as e:
            return _err(500, "Could not write it: %s" % e)

    @routes.post("/comfybridge/library/{name}")
    async def _save(request):
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            return _err(400, "The request body is not JSON.")
        if not isinstance(body, dict):
            return _err(400, "The request body must be {\"workflow\": {...}}.")
        try:
            return web.json_response(save_workflow(name, body.get("workflow"), bool(body.get("overwrite"))))
        except FileExistsError:
            return _err(409, "%s already exists in the library." % name)
        except ValueError as e:
            return _err(400, str(e))
        except OSError as e:
            return _err(500, "Could not write it: %s" % e)
