"""Ctrl+C interrupts a running turn, as the loading hint says."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False

_PROMPT = "write the migration"

timeline: Timeline = [f"{_PROMPT}\r", turn_started(), user_msg(_PROMPT), "\x03"]

# The turn is interrupted instead of arming the quit confirmation.
screen_contains = {"rust": ("Interrupted",)}
screen_excludes = {"rust": ("Esc/Ctrl+C to interrupt", "again to quit")}
