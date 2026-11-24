"""/retry runs while the interrupt holds the queue, without resuming it from the client."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    queued_turn,
    turn_completed,
    turn_interrupted,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

held = [queued_turn(2, "second")]
on_request = {
    "turn/interrupt": [
        discard_entry("partial", cause="interrupt"),
        turn_interrupted(),
        turn_queue_updated(held, paused=True),
    ]
}

screen_contains = ("Here is the full answer.", "second")
screen_excludes = ("Slash commands cannot be queued", "Let me che")

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Let me che", entry_id="partial", generation_status="in_progress"),
    {"release": 4},
    "second\r",
    "\x1b",
    "/retry\r",
    turn_started(),
    assistant_msg("Here is the full answer."),
    turn_completed(),
    {"release": 4},
]
