"""Clicking an input focuses it and puts the caret at the clicked column."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
screen_contains = {"rust": ("httpsX://old-proxy:8443", "http://old-proxy:8080")}
timeline: Timeline = ["/proxy-setup\r", "\x1b[<0;10;29M\x1b[<0;10;29m", "X"]
