"""A destructive confirm takes only `y`: a second Enter after a row delete or a delete review sends nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    ESCAPE,
    FLAKY,
    REQUEST_METHODS,
    SANTA,
    flagged,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(
        archived_ids=(SANTA, FLAKY), suggested=[], delete_suggested=[SANTA, FLAKY]
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# The delete review: Enter asks, Enter again does nothing; Esc backs out. Then a row delete: `d`, Enter.
timeline: Timeline = [
    "/storage",
    "\r",
    "\r",
    "\r",
    "\r",
    take_snapshot(),
    ESCAPE,
    ESCAPE,
    DOWN,
    "d",
    "\r",
]

screen_contains = ("y confirm  n/Esc cancel",)
screen_excludes = ("Deleted ", "y/Enter confirm")
