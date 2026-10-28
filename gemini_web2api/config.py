"""Configuration management."""
import json
import os

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": False,
    "allow_unauthenticated_remote": False,
    "max_request_bytes": 10485760,
    "max_upload_bytes": 10485760,
    "max_websocket_message_bytes": 1048576,
    "max_image_fetch_bytes": 10485760,
    "max_requests_per_minute": 60,
    "cors_origins": ["http://localhost", "http://127.0.0.1"],
}

CONFIG = dict(DEFAULT_CONFIG)


def load_config(path: str = None):
    """Load config from JSON file."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("configuration file must contain a JSON object")
        CONFIG.update(loaded)
    # Environment variables override sensitive/deployment-critical settings.
    if os.environ.get("GEMINI_WEB2API_API_KEYS") is not None:
        CONFIG["api_keys"] = [key.strip() for key in os.environ["GEMINI_WEB2API_API_KEYS"].split(",") if key.strip()]
    if os.environ.get("GEMINI_WEB2API_HOST"):
        CONFIG["host"] = os.environ["GEMINI_WEB2API_HOST"]
    if os.environ.get("GEMINI_WEB2API_PORT"):
        CONFIG["port"] = int(os.environ["GEMINI_WEB2API_PORT"])
    return CONFIG


def find_config():
    """Search for config file in standard locations."""
    for path in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
        if os.path.exists(path):
            return path
    return None
