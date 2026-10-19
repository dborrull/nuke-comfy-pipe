"""HTTP client for the ComfyUI API (no Nuke dependency).

Two backends with the same interface:
- local / LAN ComfyUI:   server = "127.0.0.1:8188" (or any host:port)
- Comfy Cloud:          server = "cloud"  (or https://cloud.comfy.org)

Comfy Cloud needs a paid plan and an API key. The key is read, in order, from
the COMFY_API_KEY / COMFY_CLOUD_API_KEY environment variables, the file named by
COMFY_BRIDGE_KEY_FILE, or Documents/ComfyBridge/comfy_cloud_api_key.txt. It is
never stored in the Nuke script.
"""

import json
import os
import uuid
from urllib import request as _req
from urllib import parse as _parse
from urllib.error import HTTPError, URLError

UPLOAD_SUBFOLDER = "comfy_bridge"
CLOUD_URL = "https://cloud.comfy.org"
CLOUD_KEY_FILE = os.path.join("ComfyBridge", "comfy_cloud_api_key.txt")

# Comfy Cloud job states (status and jobs endpoints use slightly different words)
_CLOUD_OK = ("completed", "success")
_CLOUD_FAILED = ("error", "failed", "non_retryable_error", "lost", "cancelled", "canceled")

_OBJECT_INFO_CACHE = {}   # full /object_info per server (Cloud has no per-node route)


class ComfyError(Exception):
    pass


def is_cloud(server):
    s = (server or "").strip().lower().rstrip("/")
    return s in ("cloud", "comfy cloud", "comfycloud") or "cloud.comfy.org" in s


def cloud_key_paths():
    """Places where the Comfy Cloud API key file is looked for."""
    paths = []
    if os.environ.get("COMFY_BRIDGE_KEY_FILE"):
        paths.append(os.environ["COMFY_BRIDGE_KEY_FILE"])
    roots = [os.path.join(os.path.expanduser("~"), "Documents")]
    up = os.environ.get("USERPROFILE")
    if up:
        roots += [os.path.join(up, "Documents"), os.path.join(up, "OneDrive", "Documents")]
    for r in roots:
        p = os.path.join(r, CLOUD_KEY_FILE)
        if p not in paths:
            paths.append(p)
    return paths


def load_cloud_key():
    for var in ("COMFY_API_KEY", "COMFY_CLOUD_API_KEY"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    for p in cloud_key_paths():
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        return line
        except OSError:
            continue
    raise ComfyError("Comfy Cloud: API key not found.\nSave it (one line) in:\n  %s\n"
                     "or set the COMFY_API_KEY environment variable." % cloud_key_paths()[0])


class ComfyClient(object):
    def __init__(self, server="127.0.0.1:8188", timeout=30):
        server = (server or "127.0.0.1:8188").strip().rstrip("/")
        self.cloud = is_cloud(server)
        self.key = None              # Cloud: read lazily, loading a workflow does not need it
        if self.cloud:
            self.base, self.prefix = CLOUD_URL, "/api"
            self.timeout = max(timeout, 60)
            self.poll = 2.0          # be gentle with the cloud API
        else:
            if not server.startswith("http"):
                server = "http://" + server
            self.base, self.prefix = server, ""
            self.timeout = timeout
            self.poll = 0.5
        self.client_id = str(uuid.uuid4())

    @property
    def label(self):
        return "Comfy Cloud" if self.cloud else "ComfyUI at %s" % self.base

    # ---- low level ---------------------------------------------------------
    def _request(self, path, data=None, headers=None):
        req = _req.Request(self.base + self.prefix + path, data=data, headers=headers or {})
        if self.cloud:
            if self.key is None:
                self.key = load_cloud_key()
            # "unredirected": urllib does not copy it when it follows a redirect,
            # so the key never reaches the signed storage URL given by /api/view.
            req.add_unredirected_header("X-API-Key", self.key)
        return req

    def _open(self, req, timeout=None):
        try:
            return _req.urlopen(req, timeout=timeout or self.timeout)
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError(_http_message(self, e.code, req.full_url, body))
        except URLError as e:
            raise ComfyError("Cannot reach %s\n(%s)" % (self.label, e.reason))

    def _get_json(self, path, timeout=None):
        with self._open(self._request(path), timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post_json(self, path, payload):
        req = self._request(path, json.dumps(payload).encode("utf-8"),
                            {"Content-Type": "application/json"})
        with self._open(req) as r:
            raw = r.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    # ---- API -----------------------------------------------------------------
    def system_stats(self):
        return self._get_json("/system_stats")

    def user(self):
        """Comfy Cloud account info (also checks the API key)."""
        return self._get_json("/user")

    def check(self):
        """Fail early with a clear message if the server (or the Cloud key) is not usable."""
        return self.user() if self.cloud else self.system_stats()

    def upload_image(self, path, name=None, subfolder=UPLOAD_SUBFOLDER):
        name = name or os.path.basename(path)
        ctype = "image/x-exr" if name.lower().endswith(".exr") else "image/png"
        with open(path, "rb") as f:
            file_data = f.read()
        if self.cloud and len(file_data) > 50 * 1024 * 1024:
            raise ComfyError("%s is %.0f MB; Comfy Cloud accepts files up to 50 MB."
                             % (name, len(file_data) / 1048576.0))
        boundary = "----ComfyBridge" + uuid.uuid4().hex
        parts = []
        for key, val in (("overwrite", "true"), ("type", "input"), ("subfolder", subfolder)):
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, key, val)).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
                      "Content-Type: %s\r\n\r\n" % (boundary, name, ctype)).encode("utf-8"))
        parts.append(file_data)
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        req = self._request("/upload/image", b"".join(parts),
                            {"Content-Type": "multipart/form-data; boundary=" + boundary})
        with self._open(req, max(self.timeout, 120)) as r:
            info = json.loads(r.read().decode("utf-8"))
        # Cloud stores inputs flat and content-addressed: always use the name it returns
        return (info.get("subfolder") + "/" + info["name"]) if info.get("subfolder") else info["name"]

    def object_info(self, class_type):
        if not self.cloud:
            info = self._get_json("/object_info/" + _parse.quote(class_type))
            return info.get(class_type, {})
        if self.base not in _OBJECT_INFO_CACHE:
            _OBJECT_INFO_CACHE[self.base] = self._get_json("/object_info", max(self.timeout, 120))
        return _OBJECT_INFO_CACHE[self.base].get(class_type, {})

    def missing_nodes(self, class_types):
        """Node classes of a workflow the server does not have ([] if it cannot tell)."""
        missing = []
        for c in sorted(set(class_types)):
            try:
                info = self.object_info(c)
            except ComfyError:
                return []
            known = (c in _OBJECT_INFO_CACHE.get(self.base, {})) if self.cloud else bool(info)
            if not known:
                missing.append(c)
        return missing

    def list_models(self, folder):
        """File names the server sees in a models folder, or None if it cannot list them."""
        try:
            data = self._get_json(("/experiment/models/" if self.cloud else "/models/")
                                  + _parse.quote(folder))
        except ComfyError:
            return None
        if isinstance(data, dict):
            data = data.get("models") or data.get("files") or data.get("items") or []
        out = []
        for m in data or []:
            if isinstance(m, dict):
                m = m.get("name") or m.get("filename") or m.get("path")
            if m:
                out.append(str(m))
        return out

    def queue(self, workflow):
        res = self._post_json("/prompt", {"prompt": workflow, "client_id": self.client_id})
        if res.get("node_errors"):
            raise ComfyError("Workflow errors:\n" + json.dumps(res["node_errors"], indent=1))
        if res.get("error"):
            raise ComfyError("Workflow rejected:\n" + _pretty_error(json.dumps(res)))
        return res["prompt_id"]

    def history(self, prompt_id):
        """Local: the /history entry, or None while it runs.
        Cloud: the same shape ({"status": {...}, "outputs": {...}}) built from the
        job endpoints, or None while the job is still queued / running."""
        if not self.cloud:
            return self._get_json("/history/" + prompt_id).get(prompt_id)
        st = self._get_json("/job/%s/status" % prompt_id)
        state = str(st.get("status", "")).lower()
        if state in _CLOUD_FAILED:
            msg = st.get("error_message") or ""
            if not msg:
                try:
                    msg = _job_error(self._get_json("/jobs/" + prompt_id))
                except ComfyError:
                    msg = ""
            return {"status": {"status_str": "error", "completed": False,
                               "messages": [["execution_error",
                                             {"exception_message": msg or "job %s" % state}]]},
                    "outputs": {}}
        if state not in _CLOUD_OK:
            return None
        outputs = {}
        try:
            outputs = self._get_json("/jobs/" + prompt_id).get("outputs") or {}
        except ComfyError:
            outputs = {}
        if not outputs:   # older route, kept as a fallback
            try:
                h = self._get_json("/history_v2/" + prompt_id)
                outputs = (h.get(prompt_id) or h).get("outputs") or {}
            except ComfyError:
                outputs = {}
        return {"status": {"status_str": "success", "completed": True}, "outputs": outputs}

    def download(self, img, dest):
        q = _parse.urlencode({"filename": img["filename"],
                              "subfolder": img.get("subfolder", ""),
                              "type": img.get("type", "output")})
        # On Cloud /view answers 302 -> signed URL; urllib follows it without the key.
        tmp = dest + ".part"
        with self._open(self._request("/view?" + q), max(self.timeout, 300)) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        if os.path.exists(dest):
            os.remove(dest)
        os.rename(tmp, dest)
        return dest

    def interrupt(self, prompt_ids=None):
        """Stop the work. On Cloud also removes the given jobs from the queue."""
        try:
            if self.cloud and prompt_ids:
                self._post_json("/queue", {"delete": list(prompt_ids)})
            self._post_json("/interrupt", {})
        except ComfyError:
            pass


def _job_error(job):
    err = job.get("execution_error") or {}
    if isinstance(err, dict):
        return err.get("exception_message") or err.get("message") or json.dumps(err)[:1500]
    return str(err)


def _http_message(client, code, url, body):
    if client.cloud:
        if code == 401:
            return ("Comfy Cloud refused the API key (401).\nCheck the key file:\n  %s"
                    % cloud_key_paths()[0])
        if code == 402:
            return "Comfy Cloud: not enough credits (402)."
        if code == 429:
            return ("Comfy Cloud: subscription inactive or too many requests (429).\n"
                    "The API needs a paid plan.")
    return "HTTP %s on %s\n%s" % (code, url.split("?")[0], _pretty_error(body))


def _pretty_error(body):
    try:
        d = json.loads(body)
        err = d.get("error", {})
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if d.get("node_errors"):
            msg += "\n" + json.dumps(d["node_errors"], indent=1)
        return msg or body
    except Exception:
        return body[:2000]
