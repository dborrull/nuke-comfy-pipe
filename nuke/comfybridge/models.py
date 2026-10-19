"""Which model files a workflow needs, whether ComfyUI has them, and downloads
(no Nuke dependency)."""

import json
import os
import re
import time
from urllib import request as _req
from urllib.error import HTTPError, URLError

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_catalog.json")

# loader input -> ComfyUI models subfolder (used when the catalog does not know the file)
MODEL_INPUTS = {
    "unet_name": "diffusion_models", "ckpt_name": "checkpoints", "lora_name": "loras",
    "control_net_name": "controlnet", "clip_name": "text_encoders", "clip_name1": "text_encoders",
    "clip_name2": "text_encoders", "vae_name": "vae",
}
_MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")


def basename(name):
    return os.path.basename(str(name).replace("\\", "/"))


def load_catalog(path=_CATALOG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def required_models(wf, meta=None, catalog=None):
    """[{file, folder, url, page, licence}] for every model file the workflow loads.
    The workflow metadata may add or override entries with "models": [...]."""
    catalog = load_catalog() if catalog is None else catalog
    found = {}
    for node in wf.values():
        for inp, folder in MODEL_INPUTS.items():
            val = node.get("inputs", {}).get(inp)
            if not isinstance(val, str) or not val.lower().endswith(_MODEL_EXT):
                continue
            name = basename(val)
            entry = dict(catalog.get(name, {}))
            entry.setdefault("folder", folder)
            entry["file"] = name
            found[name] = entry
    for m in (meta or {}).get("models", []) or []:
        name = basename(m.get("file", ""))
        if name:
            found[name] = dict(found.get(name, {}), **m, file=name)
    return list(found.values())


def check_models(required, list_folder):
    """Split into (present, missing, unknown). list_folder(folder) -> list of
    file names known to ComfyUI (may include subfolders), or None if it cannot tell."""
    cache, present, missing, unknown = {}, [], [], []
    for m in required:
        folder = m["folder"]
        if folder not in cache:
            try:
                names = list_folder(folder)
            except Exception:
                names = None
            cache[folder] = None if names is None else set(basename(n).lower() for n in names)
        have = cache[folder]
        if have is None:
            unknown.append(m)
        elif m["file"].lower() in have:
            present.append(m)
        else:
            missing.append(m)
    return present, missing, unknown


def direct_url(url):
    """Hugging Face page links (/blob/) -> download links (/resolve/)."""
    return re.sub(r"^(https://huggingface\.co/.+?)/blob/", r"\1/resolve/", url or "")


class DownloadCancelled(Exception):
    pass


def download(url, dest, progress=None, cancelled=lambda: False, chunk=8 << 20, timeout=60):
    """Download url to dest, resuming a previous .part file. progress(done, total)."""
    url = direct_url(url)
    part = dest + ".part"
    d = os.path.dirname(dest)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    done = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = {"User-Agent": "ComfyBridge"}
    if done:
        headers["Range"] = "bytes=%d-" % done
    try:
        resp = _req.urlopen(_req.Request(url, headers=headers), timeout=timeout)
    except HTTPError as e:
        if e.code == 416 and done:          # already complete
            os.replace(part, dest)
            return dest
        raise
    with resp:
        if done and resp.status != 206:     # server ignored the range: start again
            done = 0
        length = resp.headers.get("Content-Length")
        total = done + int(length) if length else None
        with open(part, "ab" if done else "wb") as f:
            last = 0.0
            while True:
                if cancelled():
                    raise DownloadCancelled(dest)
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress and time.time() - last > 0.3:
                    progress(done, total)
                    last = time.time()
    if total is not None and done < total:
        raise URLError("incomplete download (%d of %d bytes); press download again to resume" % (done, total))
    os.replace(part, dest)
    return dest
