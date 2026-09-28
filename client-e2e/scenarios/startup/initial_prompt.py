"""A positional prompt is sent to the model at startup."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The prompt is sent on its own once ready; the typed "x" proves the composer started empty.
client_args = ("say hi",)
timeline: Timeline = [
    "x",
    turn_started(),
    user_msg("say hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
]
