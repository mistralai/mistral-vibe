"""A box selection dies with the answered approval instead of moving to the next one."""

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
capture_steps = {1, 2, 3}
clipboard_clients = {"rust"}
expected_clipboard = "Permission for the bash tool"
screen_contains = {"rust": ("echo second",)}

_FIRST = {"command": "echo first"}
_SECOND = {"command": "echo second"}

# Drag across the first approval's title (1-based SGR coordinates), then approve it once.
_DRAG = "\x1b[<0;3;30M\x1b[<32;60;30M\x1b[<0;60;30m"

timeline: Timeline = [
    "run the commands\r",
    turn_started(),
    user_msg("run the commands"),
    tool_approval_added("bash", "shell", _FIRST, callback_id="callback-first"),
    tool_approval(
        "bash", "shell", _FIRST, callback_id="callback-first", request_id=9001
    ),
    tool_approval_added("bash", "shell", _SECOND, callback_id="callback-second"),
    tool_approval(
        "bash", "shell", _SECOND, callback_id="callback-second", request_id=9002
    ),
    {"release": 7},
    _DRAG,
    "1",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
