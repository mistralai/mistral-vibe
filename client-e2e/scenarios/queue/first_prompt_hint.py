"""The first prompt shows turn-interrupt controls, never queued-message controls."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = {
    "python": ("to steer", "cancel last queued message"),
    "rust": ("Esc/Ctrl+C to interrupt",),
}
screen_excludes = {
    "python": ("Esc/Ctrl+C to interrupt",),
    "rust": ("to steer", "cancel last queued message"),
}
skip_terminal_parity = "Python reports an accepted prompt as queued until turn/started"

timeline: Timeline = ["first\r"]
