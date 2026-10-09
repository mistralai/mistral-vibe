"""An approval taking the input box collapses the composer selection to its caret."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False

_COMMAND = {"command": "echo hello"}
_SHIFT_LEFT = "\x1b[1;2D"

# The approval is held back until "text" is selected, then released over it.
timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    tool_approval_added("bash", "shell", _COMMAND),
    tool_approval("bash", "shell", _COMMAND),
    {"release": 3},
    "draft text",
    _SHIFT_LEFT * 4,
    {"release": 2},
    "1",
    "X",
]
capture_steps = {3, 4, 5, 6}

# A surviving selection would be replaced by the typed key: "draft X".
screen_contains = {"rust": ("draft Xtext",)}
