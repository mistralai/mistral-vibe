"""An accepted directory mention submits on the next Enter."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

capture_startup = False
capture_steps = {2}
settle_per_key = True
screen_contains = {"rust": ("Directory received.",)}

timeline: Timeline = [
    "@client-e2e/fixtures",
    "\t",
    "\r",
    turn_started(),
    user_msg("@client-e2e/fixtures/"),
    assistant_msg("Directory received."),
    turn_completed(),
]
