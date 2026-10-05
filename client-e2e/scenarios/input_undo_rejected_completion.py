"""Undo restores the partial slash command after its completed submission is rejected."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
request_methods = {"telemetry/record"}
expected_clipboard = "conf"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/conf",
    "\r",
    "\x1b[122;9u",
    "\x1b[18~\x1b[99;9u",
]
