# ComfyBridge (nuke-comfybridge) - by David Borrull
# https://github.com/dborrull/nuke-comfybridge
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Check the _comfy_bridge block of a workflow against meta_schema.json (no Nuke
dependency). Problems are warnings: the workflow still loads."""

import json
import os

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meta_schema.json")
_schema = None


def load_schema(path=SCHEMA_PATH):
    global _schema
    if path != SCHEMA_PATH:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if _schema is None:
        with open(path, "r", encoding="utf-8") as f:
            _schema = json.load(f)
    return _schema


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check(where, value, spec, wf, out):
    t = spec.get("type", "any")
    if t == "any":
        return
    if t in ("string", "text"):
        if not isinstance(value, str):
            out.append("%s: should be text, is %r" % (where, value))
        return
    if t == "bool":
        if not isinstance(value, bool):
            out.append("%s: should be true or false, is %r" % (where, value))
        return
    if t in ("int", "number"):
        ok = (isinstance(value, int) and not isinstance(value, bool)) if t == "int" else _is_num(value)
        if not ok:
            out.append("%s: should be %s, is %r" % (where, "a whole number" if t == "int" else "a number", value))
        elif "min" in spec and value < spec["min"]:
            out.append("%s: %r is below the minimum %r" % (where, value, spec["min"]))
        return
    if t == "enum":
        if str(value).lower() not in spec["values"]:
            out.append("%s: %r is not one of %s" % (where, value, ", ".join(spec["values"])))
        return
    if t == "node":
        nid = str(value)
        if nid not in wf:
            out.append("%s: node '%s' does not exist in the workflow" % (where, nid))
        elif spec.get("classes") and wf[nid].get("class_type") not in spec["classes"]:
            out.append("%s: node '%s' is a %s, it should be a %s"
                       % (where, nid, wf[nid].get("class_type"), " / ".join(spec["classes"])))
        return
    if t == "pair":
        if not (isinstance(value, list) and len(value) == 2):
            out.append("%s: should be [node, input], is %r" % (where, value))
        elif str(value[0]) not in wf:
            out.append("%s: node '%s' does not exist in the workflow" % (where, value[0]))
        elif value[1] not in wf[str(value[0])].get("inputs", {}):
            out.append("%s: node '%s' has no input '%s'" % (where, value[0], value[1]))
        return
    if t == "list":
        if not isinstance(value, list):
            out.append("%s: should be a list, is %r" % (where, value))
            return
        if spec.get("max") and len(value) > spec["max"]:
            out.append("%s: %d entries, only the first %d are used" % (where, len(value), spec["max"]))
        for i, item in enumerate(value):
            _check("%s[%d]" % (where, i), item, spec.get("item", {}), wf, out)
        return
    if t == "object":
        if not isinstance(value, dict):
            out.append("%s: should be an object {...}, is %r" % (where, value))
            return
        _check_keys(where, value, spec.get("keys", {}), wf, out)


def _check_keys(where, obj, keys, wf, out):
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        if k not in keys:
            out.append("%s: unknown key '%s' (ignored)" % (where, k) if where else "unknown key '%s' (ignored)" % k)
            continue
        _check("%s.%s" % (where, k) if where else k, v, keys[k], wf, out)
    for k, spec in keys.items():
        if spec.get("required") and k not in obj:
            out.append("%s: '%s' is missing" % (where or "metadata", k))


def check_meta(wf, meta, schema=None):
    """List of problems of the metadata (empty when it is fine). wf: the API nodes."""
    schema = schema or load_schema()
    out = []
    if not meta:
        return out
    if not isinstance(meta, dict):
        return ["the _comfy_bridge block should be an object {...}"]
    _check_keys("", meta, schema["keys"], wf, out)
    # what the schema alone cannot say
    for i, p in enumerate(meta.get("params") or []):
        if not isinstance(p, dict):
            continue
        nid, inp = str(p.get("node", "")), p.get("input")
        if nid in wf and inp and inp not in wf[nid].get("inputs", {}):
            out.append("params[%d]: node '%s' (%s) has no input '%s'" % (i, nid, wf[nid].get("class_type"), inp))
        elif nid in wf and inp and isinstance(wf[nid]["inputs"][inp], list):
            out.append("params[%d]: %s.%s is fed by a link, it cannot be a knob" % (i, nid, inp))
    used = {}
    for key in ("plate_node", "mask_node"):
        if meta.get(key) is not None:
            used.setdefault(str(meta[key]), []).append(key)
    for i, d in enumerate(meta.get("inputs") or []):
        if isinstance(d, dict) and d.get("node") is not None:
            used.setdefault(str(d["node"]), []).append("inputs[%d]" % i)
    for nid, keys in sorted(used.items()):
        if len(keys) > 1:
            out.append("node '%s' is used by %s: each image goes to its own LoadImage" % (nid, " and ".join(keys)))
    return out
