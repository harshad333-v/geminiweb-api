"""Capability metadata for the unified API surface."""
import os
from .image_provider import configured as image_provider_configured
CAPABILITIES = {
    "chat.completions": {"available": True, "endpoint": "/v1/chat/completions"},
    "responses": {"available": True, "endpoint": "/v1/responses"},
    "models.list": {"available": True, "endpoint": "/v1/models"},
    "documents.extract": {"available": True, "endpoint": "/v1/extractions"},
    "files.upload": {"available": True, "endpoint": "/v1/files"},
    "sessions.persist": {"available": True, "endpoint": "/v1/sessions"},
    "usage.inspect": {"available": True, "endpoint": "/v1/usage"},
    "images.generations": {"available": image_provider_configured(), "endpoint": "/v1/images/generations",
                           "reason": None if image_provider_configured() else "Set GEMINI_WEB2API_IMAGE_BASE_URL for an external provider, or set GEMINI_WEB2API_IMAGE_BACKEND=gemini-web and configure a valid Gemini Web cookie."},
    "images.edits": {"available": image_provider_configured(), "endpoint": "/v1/images/edits",
                     "reason": None if image_provider_configured() else "Set GEMINI_WEB2API_IMAGE_BASE_URL for an external provider, or set GEMINI_WEB2API_IMAGE_BACKEND=gemini-web and configure a valid Gemini Web cookie."},
    "realtime.websocket": {"available": True, "endpoint": "/v1/ws"},
}


def public_capabilities():
    """Return a copy safe for API clients."""
    return {name: dict(value) for name, value in CAPABILITIES.items()}
