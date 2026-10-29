"""Small security helpers shared by HTTP endpoints."""
import base64
import binascii
import os
import re
import secrets
from pathlib import Path


def safe_filename(name):
    """Return a display filename without path components or control characters."""
    name = os.path.basename(str(name or "upload.bin")).replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return (name or "upload.bin")[:180]


def decode_file_content(payload):
    """Decode either UTF-8 content or strict base64 content from a JSON request."""
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    if "content_base64" in payload:
        try:
            return base64.b64decode(payload["content_base64"], validate=True)
        except (ValueError, binascii.Error, TypeError) as exc:
            raise ValueError("content_base64 must be valid base64") from exc
    if "content" in payload and isinstance(payload["content"], str):
        return payload["content"].encode("utf-8")
    raise ValueError("provide either content or content_base64")


def save_upload(root, data, filename):
    """Persist a bounded upload using an unguessable server-generated identifier."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    file_id = "file_" + secrets.token_urlsafe(18)
    path = root / (file_id + ".bin")
    with path.open("xb") as handle:
        handle.write(data)
    return {"id": file_id, "filename": safe_filename(filename), "bytes": len(data), "path": str(path)}
