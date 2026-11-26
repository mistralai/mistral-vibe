"""Keep every line of a long approval inspectable by scrolling."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}

_COMMAND = "\n".join("x" * 105 + "-WIDTH-TAIL" for _line in range(1, 3_001))
_WHEEL_DOWN = "\x1b[<65;60;20M"

timeline: Timeline = [
    "run the long command\r",
    turn_started(),
    user_msg("run the long command"),
    tool_approval_added("bash", "shell", {"command": _COMMAND}),
    tool_approval("bash", "shell", {"command": _COMMAND}),
    {"release": 5},
    _WHEEL_DOWN * 8,
    take_snapshot(),
]
