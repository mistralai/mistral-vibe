"""A rejected write closes the app and reports the server's reason."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

MESSAGE = "HTTP_PROXY must start with http:// or https:// (got 'proxy:8080')"
handshake = proxy_handshake()
handshake["config/proxy/write"] = {
    "error": {"code": "invalid_params", "message": MESSAGE}
}
request_methods = METHODS
screen_contains = {"rust": (f"Failed to apply: proxy settings — {MESSAGE}",)}
screen_excludes = {"rust": ("Proxy settings saved.", "Proxy Configuration")}
timeline: Timeline = ["/proxy-setup\r", "proxy:8080", "\r"]
