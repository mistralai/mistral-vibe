"""An unfocused approval owns the title until focus acknowledges it."""

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
capture_steps = {1, 2, 3, 4, 5, 6}

# An inert Right key requests a settled frame after each focus event.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    tool_approval_added("bash", "shell", {"command": "echo hello"}),
    tool_approval("bash", "shell", {"command": "echo hello"}),
    {"release": 3},
    "\x1b[O\x1b[C",
    {"release": 2},
    "\x1b[I\x1b[C",
    "\x1b[O\x1b[C",
    "1",
]

expected_titles = (
    "Vibe",
    ">> Vibe",
    ">> Vibe",
    "Vibe - Action Required",
    "? Vibe",
    "? Vibe",
    ">> Vibe",
)
