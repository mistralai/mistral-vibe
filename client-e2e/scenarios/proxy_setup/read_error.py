"""A failed settings read reports the error and keeps the input box."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS
from e2e.app_server.scenario import Timeline

handshake = {
    "config/proxy/read": {
        "error": {"code": "internal_error", "message": "Permission denied"}
    }
}
request_methods = METHODS
screen_contains = {"rust": ("Failed to read proxy settings: Permission denied",)}
screen_excludes = {"rust": ("Proxy setup opened...", "Proxy Configuration")}
timeline: Timeline = ["/proxy-setup\r"]
