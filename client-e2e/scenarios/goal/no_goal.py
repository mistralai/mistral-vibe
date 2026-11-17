"""`/goal` without a goal shows how to set one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["/goal\r", "/goal EDIT\r", take_snapshot()]

screen_contains = ("No goal set. Use /goal <objective>.",)
