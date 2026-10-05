"""Ctrl+C denies a pending tool approval before it interrupts the turn, as Esc does."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False

_PROMPT = "clean the build"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    tool_approval_added("bash", "shell", {"command": "rm -rf build"}),
    tool_approval("bash", "shell", {"command": "rm -rf build"}),
    {"release": 5},
    "\x03",
]

# The approval closes with a deny; the turn keeps running.
screen_contains = {"rust": ("Esc/Ctrl+C to interrupt",)}
screen_excludes = {"rust": ("↑↓/jk navigate", "Interrupted")}
