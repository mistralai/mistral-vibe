"""Keep a tiny config browser visible and modal."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

capture_startup = False
screen_contains = {"rust": ("Enlarge terminal",)}
timeline: Timeline = ["/config\r", resize(7, 19)]
