"""Space unchecks a row and lowers the estimate, `s` sorts by date, `a` selects all then none."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import DOWN, REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# The review opens largest first: Down to the second row, Space unchecks it, then sort by date.
timeline: Timeline = [
    "/storage",
    "\r",
    "\r",
    take_snapshot(),
    DOWN,
    " ",
    take_snapshot(),
    "s",
    take_snapshot(),
    "a",
    take_snapshot(),
    "a",
    take_snapshot(),
]

screen_contains = ("Archive 0 sessions →", "[ ]", "s sort: date")
