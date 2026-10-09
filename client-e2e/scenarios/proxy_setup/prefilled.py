"""Saved values prefill their inputs; the focused one starts fully selected."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
screen_contains = {"rust": ("http://old-proxy:8080", "https://old-proxy:8443")}
timeline: Timeline = ["/proxy-setup\r"]
