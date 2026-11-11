"""A saved /thinking pick made while a turn runs is parked by the server, not failed or reloaded."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

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
    take_snapshot(),
    {"release": 5},
    take_snapshot(),
    "/thinking",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    {"release": 2},
    take_snapshot(),
    "\r",
    take_snapshot(),
]

screen_contains = ("Saved; it applies when the current turn ends.",)
screen_excludes = ("Failed to apply", "Applied:")
