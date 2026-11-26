"""The `/` completion closes as soon as anything, even a space, follows the command."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

screen_contains = ("status a",)
screen_excludes = ("Display agent statistics",)
timeline: Timeline = ["/status", " ", "a"]
