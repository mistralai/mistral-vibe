"""`/goal` opens the detail panel over the input; Esc closes it."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import ESCAPE, started_with

_OBJECTIVE = (
    "Make the parity suite pass on every platform, then file the follow-ups "
    "for whatever still diverges between the Python and the Rust clients."
)

handshake = started_with(goal(objective=_OBJECTIVE))

timeline: Timeline = ["/goal\r", ESCAPE, take_snapshot()]

screen_excludes = ("Tokens",)
