"""A short terminal scrolls the inputs: focus follows, the wheel scrolls, typing snaps back."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline, resize

handshake = proxy_handshake()
request_methods = METHODS
screen_contains = {"rust": ("Proxy Configuration", "SSL_CERT_DIR", "/etc/ssl")}
timeline: Timeline = [
    "/proxy-setup\r",
    resize(14, 80),
    "\x1b[A",
    "\x1b[<64;20;8M",
    "\x1b[<64;20;8M",
    "/etc/ssl",
    resize(40, 120),
]
