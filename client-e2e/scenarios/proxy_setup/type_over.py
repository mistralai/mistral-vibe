"""Typing into a focused saved value replaces its selected text."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
screen_contains = {"rust": ("Proxy settings saved.",)}
timeline: Timeline = ["/proxy-setup\r", "http://typed:3128", "\r"]
