"""`/retry` with no interrupted response says so instead of doing nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = ("⚠ No interrupted response to continue.",)

timeline: Timeline = ["/retry\r"]
