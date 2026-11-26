"""A queued prompt promoted after a turn ends runs with its own loading line."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Ends on the promoted turn's loading frame; queue/queued_turn_promoted covers its reply.
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
]
