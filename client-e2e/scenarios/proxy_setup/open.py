"""`/proxy-setup` opens one input per supported variable, placeholders shown."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

handshake = proxy_handshake()
request_methods = METHODS
screen_contains = {
    "rust": (
        "Proxy setup opened...",
        "Proxy Configuration",
        "Path to directory containing SSL certificates",
        "Enter save & exit",
    )
}
timeline: Timeline = ["/proxy-setup\r"]
