"""A paste lands in the focused input, keeping only its first line."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

handshake = proxy_handshake()
request_methods = METHODS
screen_contains = {"rust": ("localhost,127.0.0.1",)}
screen_excludes = {"rust": ("second line",)}
timeline: Timeline = [
    "/proxy-setup\r",
    "\x1b[A",
    "\x1b[A",
    "\x1b[A",
    "\x1b[200~localhost,127.0.0.1\nsecond line\x1b[201~",
]
