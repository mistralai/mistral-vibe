"""A narrow /config wraps its footer above the box border instead of cutting Esc close off."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot

timeline: Timeline = [resize(30, 44), "/config\r", take_snapshot()]

screen_contains = ("Ctrl+R reset", "Esc close")
