"""Ctrl+C copies the focused selection instead of arming the quit confirmation."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
env = {"SSH_TTY": "/dev/test"}
expected_clipboard = "http://old-proxy:8080"
screen_contains = {"rust": ("Proxy Configuration",)}
screen_excludes = {"rust": ("again to quit",)}
timeline: Timeline = ["/proxy-setup\r", "\x03"]
