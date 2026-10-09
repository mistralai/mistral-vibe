"""Editing a saved value rewrites it; clearing one unsets it."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
screen_contains = {
    "rust": ("Proxy settings saved. Restart the CLI for changes to take effect.",)
}
timeline: Timeline = [
    "/proxy-setup\r",
    "\x15",
    "http://new-proxy:9090",
    "\x1b[B",
    "\x15",
    "\r",
]
