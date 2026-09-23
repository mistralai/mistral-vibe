"""Resize an open editor without losing layers, draft, or save target."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = AUTO_COMPACT_HANDSHAKE

capture_startup = False
screen_contains = {"rust": ("240000", "220000", "200000", "180000", "project config")}
timeline: Timeline = [
    "/config\r",
    "\r",
    resize(24, 80),
    resize(24, 60),
    "\t",
    resize(12, 40),
    resize(40, 120),
]
