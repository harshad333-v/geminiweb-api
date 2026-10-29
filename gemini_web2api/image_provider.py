"""Optional OpenAI-compatible image provider adapter.

Configure GEMINI_WEB2API_IMAGE_BASE_URL and GEMINI_WEB2API_IMAGE_API_KEY.
No provider credentials are hard-coded or logged.
"""
import json
import os
import urllib.error
import urllib.request
import time

from .gemini import generate as gemini_generate, load_cookie
from .models import resolve_model
from .multimodal import detect_image_mime, upload_image


class ImageProviderError(RuntimeError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def configured():
    backend = os.environ.get("GEMINI_WEB2API_IMAGE_BACKEND", "").strip().lower()
    if os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip():
        return True
    if backend == "gemini-web":
        cookie, _ = load_cookie()
        return bool(cookie)
    return False


def _native_enabled():
    return os.environ.get("GEMINI_WEB2API_IMAGE_BACKEND", "").strip().lower() == "gemini-web"


def _nested(value, path, default=None):
    current = value
    for key in path:
        if isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        elif isinstance(current, dict) and str(key) in current:
            current = current[str(key)]
        else:
            return default
    return current if current is not None else default


def _field(container, index, default=None):
    if not isinstance(container, list):
        return default
    value = container[index] if index < len(container) else None
    if value not in (None, [], {}) and not isinstance(value, dict):
        return value
    bundle = container[-1] if container and isinstance(container[-1], dict) else {}
    return bundle.get(str(index + 1), default)


def _generated_image_urls(raw):
    """Extract generated-image URLs from Gemini Web's StreamGenerate response."""
    urls = []
    for line in raw.splitlines():
        if '"wrb.fr"' not in line:
            continue
        try:
            envelope = json.loads(line)
            inner_text = _nested(envelope, [0, 2])
            if not isinstance(inner_text, str):
                continue
            response = json.loads(inner_text)
            candidates = _nested(response, [4], [])
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                rich_content = _nested(candidate, [12], [])
                generated = _field(rich_content, 7, [])
                image_items = _nested(generated, [0], [])
                if not isinstance(image_items, list):
                    continue
                for item in image_items:
                    url = _nested(item, [0, 3, 3])
                    if isinstance(url, str) and url.startswith("https://") and url not in urls:
                        urls.append(url)
        except (ValueError, TypeError, IndexError):
            continue
    return urls


def _generate_with_gemini_web(payload, image_bytes=None, filename="image.png"):
    cookie, _ = load_cookie()
    if not cookie:
        raise ImageProviderError(
            "Gemini Web image backend requires a valid cookie configured for this server", 501
        )
    if payload.get("response_format") == "b64_json":
        raise ImageProviderError(
            "Gemini Web currently returns authenticated image URLs, not b64_json; omit response_format or use url", 400
        )
    model_name, model_id, think_mode, error, extra_fields = resolve_model(
        payload.get("model", "gemini-3.6-flash")
    )
    if error:
        raise ImageProviderError(error, 400)
    file_refs = None
    if image_bytes is not None:
        try:
            mime = detect_image_mime(image_bytes, "image/png")
            file_refs = [upload_image(image_bytes, filename, mime)]
        except Exception as exc:
            raise ImageProviderError("Gemini Web image upload failed: " + type(exc).__name__, 502) from exc
    prompt = payload["prompt"].strip()
    if image_bytes is not None:
        prompt = "Edit the attached image according to these instructions. Return an actual edited image, not only a text description. Instructions: " + prompt
    else:
        prompt = "Generate an image from this prompt. Return an actual generated image, not only a text description. Prompt: " + prompt
    try:
        raw = gemini_generate(prompt, model_id, think_mode, file_refs, extra_fields, return_raw=True)
    except Exception as exc:
        raise ImageProviderError("Gemini Web image generation failed: " + type(exc).__name__, 502) from exc
    urls = _generated_image_urls(raw)
    if not urls:
        raise ImageProviderError(
            "Gemini Web returned no generated-image URLs. Check that image generation is available for this account/model and try an explicit image-generation prompt.",
            502,
        )
    count = payload.get("n", 1)
    if not isinstance(count, int) or count < 1 or count > 4:
        raise ImageProviderError("n must be an integer between 1 and 4", 400)
    data = [{"url": url, "revised_prompt": payload["prompt"]} for url in urls[:count]]
    return {"created": int(time.time()), "data": data, "model": model_name}



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
    if _native_enabled():
        return _generate_with_gemini_web(payload)
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
    if _native_enabled():
        return _generate_with_gemini_web(payload, image_bytes=image_bytes, filename=filename)
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
