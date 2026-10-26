"""Voice settings open during a running turn and persist on Escape."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
handshake = {
    "config/read": {"config": {"voiceModeEnabled": False}},
    "runtime/read": {"runtime": {"config": {"voiceModeEnabled": False}}},
    "config/write": {
        "runtime": {"config": {"voiceModeEnabled": True}},
        "status": "pending",
    },
}
screen_contains = ("Voice mode enabled.",)
screen_excludes = ("Slash commands cannot be queued", "not implemented")
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/voice",
    "\r",
    " ",
    "\x1b",
]
