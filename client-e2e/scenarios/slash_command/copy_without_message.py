"""`/copy` before any agent reply says there is nothing to copy."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = ("No agent message available to copy",)

timeline: Timeline = ["/copy\r"]
