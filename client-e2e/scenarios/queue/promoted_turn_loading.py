"""A queued prompt promoted after a turn ends runs with its own loading line."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
# Steps 3 and 5 match queue/queued_turn_promoted; only the promoted turn's loading frame is new.
capture_steps = {4}
screen_contains = ("> say ho", "Generating")
screen_excludes = ("Queued", "Ho. Ready when you are.")

# Each turn releases five events: start, the harness queue drain, the user echo, the reply, completion.
timeline: Timeline = [
    "say hi\r",
    turn_started(),
    user_msg("say hi"),
    assistant_msg("Hi. What are we working on?"),
    turn_completed(),
    {"release": 3},
    "say ho\r",
    {"release": 2},
    turn_started(),
    user_msg("say ho"),
    assistant_msg("Ho. Ready when you are."),
    turn_completed(),
    {"release": 3},
    {"release": 2},
]
