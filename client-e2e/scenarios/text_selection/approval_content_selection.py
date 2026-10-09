"""Tool approval box text is selectable and a drag auto-copies it."""

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
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2}
clipboard_clients = {"rust"}
expected_clipboard = "Permission for the bash tool\necho hello"

_COMMAND = {"command": "echo hello"}

# Drag from the title row into the command row (1-based SGR coordinates).
_DRAG = "\x1b[<0;3;30M\x1b[<32;119;31M\x1b[<0;119;31m"

timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    tool_approval_added("bash", "shell", _COMMAND),
    tool_approval("bash", "shell", _COMMAND),
    {"release": 5},
    _DRAG,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
