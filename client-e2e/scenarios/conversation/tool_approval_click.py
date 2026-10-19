"""One click on a tool approval option answers it, like Enter."""

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
capture_steps = {1, 2}

# Press and release on the "3. Always allow" row, in 1-based terminal coordinates.
_CLICK_ALWAYS_ALLOW = "\x1b[<0;10;35M\x1b[<0;10;35m"
_ARGS = {"command": "printf 'approved\\n'"}

timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    tool_approval_added("bash", "shell", _ARGS, callback_id="callback-click"),
    tool_approval("bash", "shell", _ARGS, callback_id="callback-click"),
    {"release": 5},
    _CLICK_ALWAYS_ALLOW,
]

settle_per_key = True
