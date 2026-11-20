"""The running turn shows its steps; the settled one before it is collapsed."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, bash, in_turn, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

# The running turn has its own id, as the server gives every turn.
_RUNNING = [
    "next\r",
    *(
        in_turn(event, "turn-2")
        for event in (
            turn_started(),
            user_msg("next"),
            assistant_msg("Checking the other tests."),
            bash("pytest", "12 passed"),
            assistant_msg("All green."),
        )
    ),
]

timeline: Timeline = [*worked_turn(), *_RUNNING, take_snapshot()]

screen_contains = ("Worked for 1m 12s", "Checking the other tests.", "All green.")
screen_excludes = ("Let me look at the test first.",)
