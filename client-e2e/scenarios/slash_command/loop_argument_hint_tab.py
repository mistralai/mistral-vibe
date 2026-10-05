"""Tab-completing `/loop` leaves a trailing space, so the argument hint shows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = {"rust": ("loop [schedule] [prompt]",)}
screen_excludes = {"rust": ("Schedule a recurring prompt",)}
timeline: Timeline = ["/loo", "\t"]
