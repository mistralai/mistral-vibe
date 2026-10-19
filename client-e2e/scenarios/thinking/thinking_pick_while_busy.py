"""A saved /thinking pick made while a turn runs is parked by the server, not failed or reloaded."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_startup = False
request_methods = {"config/reload"}
handshake = {"config/model/write": {"status": "pending"}}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Done."),
    turn_completed(),
    # A turn the user did not start (a scheduled /loop prompt) begins while the picker is open.
    turn_started(),
    {"release": 5},
    "/thinking",
    "\r",
    "\x1b[B",
    {"release": 2},
    "\r",
]

screen_contains = ("Saved; it applies when the current turn ends.",)
screen_excludes = ("Failed to apply", "Configuration reloaded")
