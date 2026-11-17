"""Enter on a session shows its history, worktree, branch and copy-on-write under the row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import DOWN, ESCAPE, REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Down twice skips the suggestion and the largest session to the copy-on-write one.
timeline: Timeline = [
    "/storage",
    "\r",
    DOWN,
    DOWN,
    take_snapshot(),
    "\r",
    take_snapshot(),
    ESCAPE,
    take_snapshot(),
]

screen_contains = ("Enter details",)
