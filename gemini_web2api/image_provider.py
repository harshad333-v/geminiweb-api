"""Optional OpenAI-compatible image provider adapter.

Configure GEMINI_WEB2API_IMAGE_BASE_URL and GEMINI_WEB2API_IMAGE_API_KEY.
No provider credentials are hard-coded or logged.
"""
import json
import os
import urllib.error
import urllib.request


class ImageProviderError(RuntimeError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def configured():
    return bool(os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip())


def _base_url():
    value = os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip().rstrip("/")
    if not value:
        raise ImageProviderError("no image provider configured", 501)
    if not value.startswith(("https://", "http://")):
        raise ImageProviderError("image provider base URL must use HTTP or HTTPS", 500)
    return value


def _request(path, body, content_type="application/json"):
    base = _base_url()
    endpoint = path[3:] if base.endswith("/v1") and path.startswith("/v1/") else path
    url = base + endpoint
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    key = os.environ.get("GEMINI_WEB2API_IMAGE_API_KEY", "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = int(os.environ.get("GEMINI_WEB2API_IMAGE_TIMEOUT_SEC", "120"))
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(timeout, 300))) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ImageProviderError("image provider response exceeds 8 MiB")
            return json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not echo upstream headers or credentials.
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise ImageProviderError("image provider returned HTTP " + str(exc.code) + ": " + detail,
                                 502) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ImageProviderError("image provider request failed: " + type(exc).__name__, 502) from exc


def generate_image(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
        raise ImageProviderError("prompt is required", 400)
    clean = {key: value for key, value in payload.items()
             if key in ("model", "prompt", "n", "size", "quality", "style", "response_format", "user")}
    return _request("/v1/images/generations", json.dumps(clean).encode("utf-8"))


def edit_image(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
        raise ImageProviderError("prompt is required", 400)
    import base64
    import binascii
    image = payload.get("image_base64")
    if not isinstance(image, str):
        raise ImageProviderError("image_base64 is required for JSON image-edit requests", 400)
    try:
        image_bytes = base64.b64decode(image, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageProviderError("image_base64 must be valid base64", 400) from exc
    if not image_bytes or len(image_bytes) > 10 * 1024 * 1024:
        raise ImageProviderError("image must be between 1 byte and 10 MiB", 400)
    filename = os.path.basename(str(payload.get("filename", "image.png"))).replace('"', "")
    boundary = "----geminiweb2api-image-edit-boundary"
    fields = {
        "prompt": payload["prompt"],
        "model": str(payload.get("model", "gpt-image-1")),
    }
    for key in ("n", "size", "quality", "response_format", "user"):
        if key in payload:
            fields[key] = str(payload[key])
    parts = []
    for key, value in fields.items():
        parts.extend([("--" + boundary + "\r\n").encode(),
                      ('Content-Disposition: form-data; name="' + key + '"\r\n\r\n').encode(),
                      str(value).encode("utf-8"), b"\r\n"])
    parts.extend([("--" + boundary + "\r\n").encode(),
                  ('Content-Disposition: form-data; name="image"; filename="' + filename + '"\r\n').encode(),
                  b"Content-Type: application/octet-stream\r\n\r\n", image_bytes, b"\r\n",
                  ("--" + boundary + "--\r\n").encode()])
    return _request("/v1/images/edits", b"".join(parts), "multipart/form-data; boundary=" + boundary)
