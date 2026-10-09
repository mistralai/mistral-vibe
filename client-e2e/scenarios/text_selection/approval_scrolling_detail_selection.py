"""A drag across a scrolling approval detail never selects its scrollbar."""

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
expected_clipboard = "echo line-08\necho line-09"

_COMMAND = {"command": "\n".join(f"echo line-{i:02}" for i in range(1, 41))}

# Drag across two detail rows through the scrollbar column (1-based SGR coordinates).
_DRAG = "\x1b[<0;3;21M\x1b[<32;119;22M\x1b[<0;119;22m"

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
