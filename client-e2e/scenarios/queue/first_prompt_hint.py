"""The first prompt shows turn-interrupt controls, never queued-message controls."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = ("Esc/Ctrl+C to interrupt",)
screen_excludes = ("to steer", "cancel last queued message")

timeline: Timeline = ["first\r"]
