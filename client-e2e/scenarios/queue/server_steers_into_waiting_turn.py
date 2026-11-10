"""A prompt queued while the agent waits joins the turn when the server steers it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    model_wait_completed,
    model_wait_started,
    queued_turn,
    turn_completed,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

# The server steers on its own: no `session/turn/queue/steer` request is sent.
_STEERED = user_msg("what about the docs?")
_STEERED["params"]["entry"]["source"] = "turn_steer"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {"session/start": {"state": {"session": {"harness": "unified"}}}}

settle_per_key = True

# Waiting, typing, the prompt queued until the server steers it in, steered in,
# the next turn.
timeline: Timeline = [
    "start two subagents\r",
    turn_started(),
    user_msg("start two subagents"),
    model_wait_started(),
    {"release": 4},
    take_snapshot(),
    # The waiting line stays while the user types.
    "what about the docs?",
    take_snapshot(),
    "\r",
    turn_queue_updated([queued_turn(2, "what about the docs?")]),
    turn_queue_updated(),
    _STEERED,
    model_wait_completed(),
    assistant_msg("The docs folder holds the user guides."),
    turn_completed(),
    {"release": 1},
    take_snapshot(),
    {"release": 4},
    take_snapshot(),
    {"release": 1},
    # The steered prompt left the queue: the next prompt starts its own turn.
    "thanks\r",
    turn_started(),
    user_msg("thanks"),
    assistant_msg("You're welcome."),
    turn_completed(),
    {"release": 5},
    take_snapshot(),
]
