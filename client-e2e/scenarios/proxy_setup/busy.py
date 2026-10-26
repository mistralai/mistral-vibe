"""Proxy settings open and save during a running turn."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.proxy_setup import METHODS, handshake as proxy_handshake
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
handshake = proxy_handshake()
request_methods = METHODS
screen_contains = ("Proxy settings saved. Restart the CLI for changes to take effect.",)
screen_excludes = ("Slash commands cannot be queued", "Proxy Configuration")
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/proxy-setup",
    "\r",
    "http://proxy.example.com:8080",
    "\r",
]
