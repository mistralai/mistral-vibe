"""On a narrow terminal the held queue's header drops its extra keys and keeps Enter and Up."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    queued_turn,
    turn_interrupted,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, resize

on_request = {
    "turn/interrupt": [
        turn_interrupted(),
        turn_queue_updated([queued_turn(2, "second")], paused=True),
    ]
}

screen_contains = ("» Queued — Enter to send · ↑ to edit",)
screen_excludes = ("cancel last queued",)

timeline: Timeline = [
    resize(30, 60),
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "\x1b",
]
