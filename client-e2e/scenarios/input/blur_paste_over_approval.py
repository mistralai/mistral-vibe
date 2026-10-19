"""A paste while a tool approval is open is dropped, not kept in the composer."""

from __future__ import annotations

from e2e.app_server.events import (
    paste,
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
screen_excludes = ("pasted during approval",)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    tool_approval_added("bash", "shell", {"command": "echo hello"}),
    tool_approval("bash", "shell", {"command": "echo hello"}),
    {"release": 3},
    {"release": 2},
    "\x1b[O" + paste("pasted during approval") + "\x1b[I",
    "1",
]
