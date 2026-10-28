"""Capability metadata for the unified API surface."""
import os
CAPABILITIES = {
    "chat.completions": {"available": True, "endpoint": "/v1/chat/completions"},
    "responses": {"available": True, "endpoint": "/v1/responses"},
    "models.list": {"available": True, "endpoint": "/v1/models"},
    "documents.extract": {"available": True, "endpoint": "/v1/extractions"},
    "files.upload": {"available": True, "endpoint": "/v1/files"},
    "sessions.persist": {"available": True, "endpoint": "/v1/sessions"},
    "usage.inspect": {"available": True, "endpoint": "/v1/usage"},
    "images.generations": {"available": bool(os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip()), "endpoint": "/v1/images/generations",
                           "reason": None if os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip() else "Set GEMINI_WEB2API_IMAGE_BASE_URL to enable image generation."},
    "images.edits": {"available": bool(os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip()), "endpoint": "/v1/images/edits",
                     "reason": None if os.environ.get("GEMINI_WEB2API_IMAGE_BASE_URL", "").strip() else "Set GEMINI_WEB2API_IMAGE_BASE_URL to enable image editing."},
    "realtime.websocket": {"available": True, "endpoint": "/v1/ws"},
}


def public_capabilities():
    """Return a copy safe for API clients."""
    return {name: dict(value) for name, value in CAPABILITIES.items()}
