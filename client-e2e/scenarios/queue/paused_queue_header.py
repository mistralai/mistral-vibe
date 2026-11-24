"""After Esc, the held queue's header lists its keys and the interrupt row offers /retry."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    queued_turn,
    turn_interrupted,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

screen_contains = (
    "Or /retry to resume.",
    "Enter to send · ↑ to edit · Ctrl+C to cancel last queued message",
)

held = [queued_turn(2, "Now summarize it in one sentence.")]
on_request = {
    "turn/interrupt": [
        discard_entry("partial", cause="interrupt"),
        turn_interrupted(),
        turn_queue_updated(held, paused=True),
    ]
}

timeline: Timeline = [
    "Write a 40-line poem about queues, one line at a time.\r",
    turn_started(),
    user_msg("Write a 40-line poem about queues, one line at a time."),
    assistant_msg("Queues hum", entry_id="partial", generation_status="in_progress"),
    {"release": 4},
    "Now summarize it in one sentence.\r",
    "\x1b",
    take_snapshot(),
]
