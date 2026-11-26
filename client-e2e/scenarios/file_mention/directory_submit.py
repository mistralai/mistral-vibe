"""Enter on the typed directory row closes the popup; the next Enter submits it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

screen_contains = ("Directory received.",)

timeline: Timeline = [
    "@data",
    "\t",
    "\r",
    "\r",
    turn_started(),
    user_msg("@data/"),
    assistant_msg("Directory received."),
    turn_completed(),
]
