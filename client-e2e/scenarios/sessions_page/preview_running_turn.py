"""The preview of the attached session shows a running turn's steps and folds the settled turn before it, like the conversation."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, bash, in_turn, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

_RUNNING = [
    "next\r",
    *(
        in_turn(event, "turn-2")
        for event in (
            turn_started(),
            user_msg("next"),
            assistant_msg("Checking the other tests."),
            bash("pytest", "12 passed"),
            assistant_msg("All green so far."),
        )
    ),
]

timeline: Timeline = [*worked_turn(), *_RUNNING, "/sessions", "\r"]

screen_contains = (
    "Sessions",
    "Worked for 1m 12s",
    "Checking the other tests.",
    "Ran pytest",
    "All green so far.",
)
screen_excludes = ("Let me look at the test first.",)
