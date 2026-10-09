"""Selecting transcript text while a tool approval is open highlights and copies it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
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
clipboard_contains = ("Bright violet machines",)

_SENTENCE = "Bright violet machines hum quietly in the northern hall."
_COMMAND = {"command": "echo hello"}

_DRAG = "\x1b[<0;3;15M\x1b[<32;119;15M\x1b[<0;119;15m"

timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    assistant_msg(_SENTENCE),
    tool_approval_added("bash", "shell", _COMMAND),
    tool_approval("bash", "shell", _COMMAND),
    {"release": 6},
    _DRAG,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
