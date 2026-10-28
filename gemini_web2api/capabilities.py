"""Capability metadata for the unified API surface."""
CAPABILITIES = {
    "chat.completions": {"available": True, "endpoint": "/v1/chat/completions"},
    "responses": {"available": True, "endpoint": "/v1/responses"},
    "models.list": {"available": True, "endpoint": "/v1/models"},
    "documents.extract": {"available": True, "endpoint": "/v1/extractions"},
    "files.upload": {"available": True, "endpoint": "/v1/files"},
    "images.generations": {"available": False, "endpoint": "/v1/images/generations",
                           "reason": "No verified image-generation provider is configured."},
    "images.edits": {"available": False, "endpoint": "/v1/images/edits",
                     "reason": "No verified image-editing provider is configured."},
    "realtime.websocket": {"available": True, "endpoint": "/v1/ws"},
}


def public_capabilities():
    """Return a copy safe for API clients."""
    return {name: dict(value) for name, value in CAPABILITIES.items()}
