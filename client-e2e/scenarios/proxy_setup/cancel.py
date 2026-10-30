"""Esc discards the drafts without writing."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

handshake = proxy_handshake()
request_methods = METHODS
screen_excludes = (
    "Proxy setup cancelled.",
    "Proxy settings saved.",
    "Proxy Configuration",
)
timeline: Timeline = ["/proxy-setup\r", "http://should-not-save:8080", "\x1b"]
