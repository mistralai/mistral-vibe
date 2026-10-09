"""Typed values are written on Enter, and arrows move between inputs."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

handshake = proxy_handshake()
request_methods = METHODS
screen_contains = {
    "rust": ("Proxy settings saved. Restart the CLI for changes to take effect.",)
}
screen_excludes = {"rust": ("Proxy Configuration",)}
timeline: Timeline = [
    "/proxy-setup\r",
    "http://proxy.example.com:8080",
    "\x1b[B",
    "https://proxy.example.com:8443",
    "\x1b[A",
    "\x1b[A",
    "/etc/ssl/certs",
    "\r",
]
